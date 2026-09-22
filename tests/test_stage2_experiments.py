from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_demo import (
    FaultInjectingLLM,
    KnowledgeBase,
    MockLLM,
    OpenAICompatClient,
    ReActAgent,
    load_tasks,
)
from agent_demo.agent import RunRecord, TurnRecord, _recovery_quality
from agent_demo.env import Task
from metrics.summary import build_report, discover, paired_comparisons, regrade_samples
from experiments.runners.run_experiment import (
    _parse_sample_keys,
    _parse_task_ids,
    _resolve_context_budget,
    build_parser,
    main as run_experiment,
)
from experiments.runners.run_suite import (
    ABLATION_METHODS,
    MAIN_METHODS,
    _resolve_methods,
    _suite_methods,
    main as run_suite,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Stage2TaskSetTest(unittest.TestCase):
    def test_stage2_task_set_has_three_balanced_scenarios(self):
        tasks = load_tasks(PROJECT_ROOT / "tasks" / "stage2")

        self.assertEqual(30, len(tasks))
        self.assertEqual(
            Counter({"long_context": 10, "tool_chain": 10, "recovery": 10}),
            Counter(task.scenario for task in tasks),
        )
        recovery_tasks = [task for task in tasks if task.scenario == "recovery"]
        self.assertTrue(all(task.recovery_targets for task in recovery_tasks))
        self.assertTrue(all(task.fault_injection for task in recovery_tasks))

    def test_multi_target_backup_questions_explicitly_request_rpo_and_rto(self):
        tasks = {
            task.task_id: task
            for task in load_tasks(PROJECT_ROOT / "tasks" / "stage2")
        }

        for task_id in ("tool_chain_005", "tool_chain_010"):
            self.assertIn("RPO", tasks[task_id].question)
            self.assertIn("RTO", tasks[task_id].question)


class FaultInjectionTest(unittest.TestCase):
    def test_parse_fault_is_deterministic_and_resettable(self):
        env = KnowledgeBase({"d1": "关键事实 A"})
        client = FaultInjectingLLM(
            MockLLM(env),
            {"type": "parse_error", "call": 2, "payload": "broken"},
        )
        messages = [{"role": "user", "content": "当前任务：查找 A"}]

        first = client.complete(messages)
        second = client.complete(messages)
        client.reset()
        reset_first = client.complete(messages)

        self.assertIn('"action"', first)
        self.assertEqual("broken", second)
        self.assertEqual(first, reset_first)

    def test_recovery_ablation_restores_injected_context_loss(self):
        docs = {
            "d1": "目标集群是 cluster-zeta。",
            "d2": "切换时间是 2026-10-09 02:00。",
            "d3": "干扰信息三。",
            "d4": "干扰信息四。",
            "d5": "干扰信息五。",
            "d6": "干扰信息六。",
        }
        task = Task(
            "recovery_test",
            "recovery",
            "找出目标集群和切换时间",
            docs=docs,
            golden_facts=["cluster-zeta", "2026-10-09 02:00"],
            recovery_targets=["cluster-zeta", "2026-10-09 02:00"],
            fault_injection={
                "type": "parse_error",
                "call": 7,
                "payload": "恢复 cluster-zeta 和 2026-10-09 02:00",
                "drop_terms": ["cluster-zeta", "2026-10-09 02:00"],
            },
        )

        control_env = KnowledgeBase(docs)
        control = ReActAgent(
            FaultInjectingLLM(MockLLM(control_env), task.fault_injection),
            control_env,
            method="ablation_c",
        ).run(task)
        treatment_env = KnowledgeBase(docs)
        treatment = ReActAgent(
            FaultInjectingLLM(MockLLM(treatment_env), task.fault_injection),
            treatment_env,
            method="ablation_d",
        ).run(task)

        self.assertFalse(control.success)
        self.assertTrue(treatment.success)
        self.assertEqual(1.0, treatment.recovery_recall)
        self.assertGreater(treatment.recovered_tokens, 0)


class RunMetricsTest(unittest.TestCase):
    def test_paired_report_keeps_primary_and_quality_conditioned_efficiency(self):
        records = [
            ("a", "none", False, 100),
            ("a", "pruner_v1", True, 500),
            ("b", "none", True, 1000),
            ("b", "pruner_v1", True, 500),
            ("c", "none", True, 1000),
            ("c", "pruner_v1", True, 600),
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for task_id, method, success, tokens in records:
                method_dir = root / method
                method_dir.mkdir(exist_ok=True)
                (method_dir / f"{task_id}.r00.summary.json").write_text(
                    json.dumps(
                        {
                            "task_id": task_id,
                            "scenario": "tool_chain",
                            "method": method,
                            "run_id": "r00",
                            "success": success,
                            "tokens_in_total": tokens,
                            "peak_context_tokens": tokens,
                        }
                    ),
                    encoding="utf-8",
                )

            row = paired_comparisons(discover(root), "none")[0]

        self.assertAlmostEqual((-4.0 + 0.5 + 0.4) / 3, row["net_input_savings_rate_vs_baseline"])
        self.assertAlmostEqual(0.4, row["net_input_savings_rate_median"])
        self.assertAlmostEqual(2 / 3, row["net_input_savings_win_rate"])
        self.assertEqual(2, row["both_success_paired_n"])
        self.assertAlmostEqual(0.45, row["both_success_net_input_savings_rate"])

    def test_net_savings_and_cost_include_compression_overhead(self):
        record = RunRecord(
            task=Task("t", "s", "q"),
            method="pruner_v1",
            success=True,
            final_answer="ok",
            compression_overhead_tokens=20,
            pricing={
                "input_per_million": 2.0,
                "output_per_million": 4.0,
                "compressor_per_million": 1.0,
            },
            turns=[
                TurnRecord(0, 80, 100, 10, "a", None, None, 0.1),
                TurnRecord(1, 70, 100, 10, "b", None, None, 0.2),
            ],
        )

        self.assertEqual(50, record.gross_input_tokens_saved)
        self.assertEqual(30, record.net_input_tokens_saved)
        self.assertAlmostEqual(0.15, record.net_input_savings_rate)
        self.assertAlmostEqual(0.0004, record.estimated_total_cost)

    def test_irrelevant_recovery_has_zero_amplification(self):
        precision, recall, amplification = _recovery_quality(
            [{"recovered_texts": ["重新执行 read d1"]}],
            ["cluster-zeta", "2026-10-09 02:00"],
        )

        self.assertEqual(0.0, precision)
        self.assertEqual(0.0, recall)
        self.assertEqual(0.0, amplification)

    def test_regrade_audits_modal_word_false_negative_without_mutating_raw_file(self):
        task = {
            "task_id": "grade_001",
            "scenario": "tool_chain",
            "question": "给出阈值",
            "docs": {"d1": "差异率为 0%"},
            "golden_facts": ["差异率为 0%"],
        }
        summary = {
            "task_id": "grade_001",
            "scenario": "tool_chain",
            "method": "pruner_v1",
            "run_id": "r00",
            "success": False,
            "partial_score": 0.0,
            "final_answer": "差异率必须为 0%。",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            summary_path = root / "grade_001.r00.summary.json"
            task_path.write_text(json.dumps([task], ensure_ascii=False), encoding="utf-8")
            summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

            samples = discover(root)
            regraded, audit = regrade_samples(samples, task_path)

            self.assertTrue(regraded[0].success)
            self.assertEqual(1.0, regraded[0].partial_score)
            self.assertEqual(1, len(audit))
            self.assertFalse(json.loads(summary_path.read_text(encoding="utf-8"))["success"])

    def test_regrade_normalizes_constraint_whitespace(self):
        task = {
            "task_id": "grade_constraint_001",
            "scenario": "tool_chain",
            "question": "给出恢复时间",
            "docs": {"d1": "恢复时间为 2 小时"},
            "golden_facts": ["2 小时"],
            "answer_constraints": {"required": ["2 小时"]},
        }
        summary = {
            "task_id": "grade_constraint_001",
            "scenario": "tool_chain",
            "method": "none",
            "run_id": "r00",
            "success": True,
            "partial_score": 1.0,
            "constraint_adherence": 0.0,
            "final_answer": "恢复时间为2小时。",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            summary_path = root / "grade_constraint_001.r00.summary.json"
            task_path.write_text(json.dumps([task], ensure_ascii=False), encoding="utf-8")
            summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

            regraded, audit = regrade_samples(discover(root), task_path)

            self.assertEqual(1.0, regraded[0].constraint_adherence)
            self.assertEqual(1, len(audit))
            self.assertEqual(0.0, audit[0]["original_constraint_adherence"])
            self.assertEqual(1.0, audit[0]["corrected_constraint_adherence"])

    def test_discover_backfills_post_sufficiency_calls_from_old_turn_logs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "legacy_001.r00.summary.json").write_text(
                json.dumps(
                    {
                        "task_id": "legacy_001",
                        "scenario": "tool_chain",
                        "method": "pruner_v1",
                        "run_id": "r00",
                        "success": True,
                    }
                ),
                encoding="utf-8",
            )
            turns = [
                {"step": 0, "kirr": 0.5, "action": {"name": "read"}, "tokens_in": 10},
                {"step": 1, "kirr": 1.0, "action": {"name": "read"}, "tokens_in": 20},
                {"step": 2, "kirr": 1.0, "action": None, "tokens_in": 30},
            ]
            (root / "legacy_001.r00.turns.jsonl").write_text(
                "\n".join(json.dumps(row) for row in turns), encoding="utf-8"
            )

            sample = discover(root)[0]

            self.assertEqual(1, sample.post_sufficiency_tool_call_count)


class ExperimentRunnerTest(unittest.TestCase):
    def test_single_mock_experiment_writes_manifest_events_and_reportable_summary(self):
        task = {
            "task_id": "tiny_001",
            "scenario": "long_context",
            "question": "读取资料并回答",
            "docs": {"d1": "事实 A", "d2": "事实 B"},
            "golden_facts": ["事实 A", "事实 B"],
            "expected_tools": ["search", "read"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            task_path.write_text(json.dumps([task], ensure_ascii=False), encoding="utf-8")

            result_dir = run_experiment(
                [
                    "--tasks", str(task_path),
                    "--method", "pruner_v1",
                    "--mock",
                    "--out", str(root / "runs"),
                    "--experiment-id", "test-exp",
                    "--context-soft-limit", "800",
                    "--context-hard-limit", "1000",
                    "--context-target", "700",
                    "--archive-db", str(root / "archive.sqlite3"),
                ]
            )

            manifest = json.loads((result_dir / "run_manifest.json").read_text(encoding="utf-8"))
            summary = json.loads((result_dir / "tiny_001.r00.summary.json").read_text(encoding="utf-8"))
            samples = discover(result_dir.parent)
            report = build_report(samples, baseline="pruner_v1")

            self.assertEqual("completed", manifest["status"])
            self.assertEqual("test-exp", summary["experiment_id"])
            self.assertEqual(42, summary["run_seed"])
            self.assertTrue(summary["success"])
            self.assertTrue((result_dir / "tiny_001.r00.events.jsonl").exists())
            self.assertTrue((root / "archive.sqlite3").exists())
            self.assertEqual(1000, manifest["context_budget"]["hard_limit_tokens"])
            self.assertEqual("sqlite", manifest["archive_backend"])
            self.assertEqual(1, report["sample_count"])
            self.assertIn("scenario_paired_to_baseline", report)

    def test_suite_presets_are_explicit(self):
        self.assertEqual(MAIN_METHODS, _suite_methods("main"))
        self.assertEqual(ABLATION_METHODS, _suite_methods("ablation"))
        self.assertEqual(MAIN_METHODS + ABLATION_METHODS, _suite_methods("all"))

    def test_real_pilot_can_select_methods_and_tasks(self):
        self.assertEqual(("none", "pruner_v1"), _resolve_methods("all", "none,pruner_v1"))
        self.assertEqual(
            ["long_context_001", "tool_chain_001", "recovery_001"],
            _parse_task_ids("long_context_001,tool_chain_001,recovery_001"),
        )
        self.assertEqual(
            [("tool_chain_001", 0), ("tool_chain_001", 2)],
            _parse_sample_keys("tool_chain_001:r00,tool_chain_001:r02"),
        )

    def test_context_budget_defaults_soft_and_target_from_hard_limit(self):
        args = build_parser().parse_args(["--context-hard-limit", "1000"])
        budget = _resolve_context_budget(args)

        self.assertEqual(800, budget.soft_limit_tokens)
        self.assertEqual(1000, budget.hard_limit_tokens)
        self.assertEqual(800, budget.target_tokens)

    def test_resume_skips_complete_sample_and_adds_missing_repeat(self):
        task = {
            "task_id": "resume_001",
            "scenario": "long_context",
            "question": "读取资料并回答",
            "docs": {"d1": "事实 A"},
            "golden_facts": ["事实 A"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            task_path.write_text(json.dumps([task], ensure_ascii=False), encoding="utf-8")
            base_args = [
                "--tasks", str(task_path),
                "--method", "pruner_v1",
                "--mock",
                "--out", str(root / "runs"),
                "--experiment-id", "resume-exp",
            ]
            run_experiment([*base_args, "--repeats", "1"])
            result_dir = run_experiment(
                [*base_args, "--repeats", "2", "--resume"]
            )

            manifest = json.loads(
                (result_dir / "run_manifest.json").read_text(encoding="utf-8")
            )
            rows = [
                json.loads(line)
                for line in (result_dir / "task_results.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]

            self.assertEqual("completed", manifest["status"])
            self.assertEqual(1, manifest["skipped_sample_count"])
            self.assertEqual(1, manifest["executed_sample_count"])
            self.assertEqual(["r00", "r01"], [row["run_id"] for row in rows])

    def test_pair_interleaved_suite_accumulates_atomic_samples(self):
        task = {
            "task_id": "interleave_001",
            "scenario": "tool_chain",
            "question": "读取资料并回答",
            "docs": {"d1": "事实 A", "d2": "事实 B"},
            "golden_facts": ["事实 A", "事实 B"],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            task_path.write_text(json.dumps([task], ensure_ascii=False), encoding="utf-8")
            result_dir = run_suite(
                [
                    "--suite", "main",
                    "--methods", "none,pruner_v1",
                    "--baseline", "none",
                    "--tasks", str(task_path),
                    "--repeats", "2",
                    "--execution-order", "pair-interleaved",
                    "--mock",
                    "--no-fault-injection",
                    "--out", str(root / "runs"),
                    "--experiment-id", "interleaved-exp",
                ]
            )

            suite_manifest = json.loads(
                (result_dir / "suite_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("completed", suite_manifest["status"])
            self.assertEqual("pair-interleaved", suite_manifest["execution_order"])
            self.assertEqual(4, suite_manifest["sample_count"])
            for method in ("none", "pruner_v1"):
                method_manifest = json.loads(
                    (result_dir / method / "run_manifest.json").read_text(encoding="utf-8")
                )
                rows = (result_dir / method / "task_results.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
                self.assertEqual("completed", method_manifest["status"])
                self.assertEqual(2, len(rows))

    def test_task_subset_keeps_full_task_set_seed_position(self):
        tasks = [
            {
                "task_id": f"seed_{index:03d}",
                "scenario": "long_context",
                "question": "读取资料并回答",
                "docs": {"d1": f"事实 {index}"},
                "golden_facts": [f"事实 {index}"],
            }
            for index in range(2)
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            task_path = root / "tasks.json"
            task_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8")
            result_dir = run_experiment(
                [
                    "--tasks", str(task_path),
                    "--task-ids", "seed_001",
                    "--method", "none",
                    "--mock",
                    "--out", str(root / "runs"),
                    "--experiment-id", "seed-exp",
                    "--seed", "42",
                ]
            )
            summary = json.loads(
                (result_dir / "seed_001.r00.summary.json").read_text(encoding="utf-8")
            )

            self.assertEqual(10042, summary["run_seed"])


class APIRetryTest(unittest.TestCase):
    def test_transient_connection_error_uses_exponential_backoff(self):
        class APIConnectionError(Exception):
            pass

        calls = 0

        def create(**kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise APIConnectionError("temporary")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
            )

        client = object.__new__(OpenAICompatClient)
        client.model = "fake"
        client.max_output_tokens = 128
        client.max_retries = 3
        client.retry_base_delay = 0.5
        client.retry_max_delay = 4.0
        client.retry_count = 0
        client.retry_events = []
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("agent_demo.llm.time.sleep") as sleep:
            reply = client.complete([{"role": "user", "content": "test"}])

        self.assertEqual("ok", reply)
        self.assertEqual(3, calls)
        self.assertEqual(2, client.retry_count)
        self.assertEqual([0.5, 1.0], [call.args[0] for call in sleep.call_args_list])

    def test_empty_model_content_is_retried_inside_same_agent_turn(self):
        calls = 0

        def create(**kwargs):
            nonlocal calls
            calls += 1
            content = "" if calls == 1 else '{"final_answer":"ok"}'
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

        client = object.__new__(OpenAICompatClient)
        client.model = "fake"
        client.max_output_tokens = 128
        client.max_retries = 2
        client.retry_base_delay = 0.1
        client.retry_max_delay = 1.0
        client.retry_count = 0
        client.retry_events = []
        client._client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with patch("agent_demo.llm.time.sleep"):
            reply = client.complete([{"role": "user", "content": "test"}])

        self.assertIn("final_answer", reply)
        self.assertEqual(2, calls)
        self.assertEqual(1, client.retry_count)


if __name__ == "__main__":
    unittest.main()
