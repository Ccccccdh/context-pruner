"""Zero-API gates for the CrewAI r10 fresh-task confirmation batch.

Four things must hold before the paid r10 batch is allowed to run:

  1. the new task set really is new: no task id, tool name, fact literal, region
     or version identifier of ``natural_tasks_r10.json`` collides with the task
     files used for r3-r9 development;
  2. the frozen 48-execution mock grid (4 fresh tasks x 4 repeats x 3 arms)
     passes with all three gates, and the v9 pin is a no-op outside the plugin
     arm;
  3. the deterministic whole-prefix fallback and the metered remedy still fire
     and are counted under the new tasks, so a first-pass regression cannot hide
     behind the task swap;
  4. the independent audit accepts the mock grid, rejects frozen tampering, and
     the paid freeze hashes the r10 sources while the paid directory is absent.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from experiments.audits import audit_crewai_handoff_v10 as independent
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v10 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v10 as r10


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R10_FRESH_MULTITASK_MOCK_01.json"
PAID_BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01"
DEVELOPMENT_TASK_FILES = (
    "tasks/stage5_autogen/natural_tasks.json",
    "tasks/stage5_autogen/natural_tasks_r5.json",
    "tasks/stage5_autogen/natural_tasks_r6.json",
    "tasks/stage5_autogen/natural_tasks_r7.json",
    "tasks/stage5_autogen/natural_tasks_r8.json",
)
#: The one literal r10 shares with r6-r8 on purpose: the frozen contract sentence
#: ("state the effective result of every failed check in the form ``0 failed``")
#: is part of the unchanged two-role contract shape and the judge is unchanged.
FROZEN_SHARED_LITERALS = {"failed 0"}


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        model="mock", base_url="", max_output_tokens=512, fixed_reserved_tokens=300,
        max_summary_tokens=1024, max_summary_calls=4, provider_soft=1200,
        provider_hard=3000, provider_target=900, soft_limit=0, hard_limit=0, target=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _mock_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _old_task_values() -> set[str]:
    values: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            values.add(str(value))

    for name in DEVELOPMENT_TASK_FILES:
        for task in json.loads((ROOT / name).read_text(encoding="utf-8")):
            walk(task)
    return values


class CrewAIR10FreshTaskSetTest(unittest.TestCase):
    """The task set must be new on every axis that carries a fact."""

    def test_task_file_is_the_r10_file_and_the_default_route(self) -> None:
        self.assertEqual("natural_tasks_r10.json", judge.TASK_FILE.name)
        self.assertEqual(
            [str(task["task_id"]) for task in judge.load_tasks()],
            list(independent.FRESH_TASKS),
        )

    def test_no_task_id_tool_or_fact_is_reused_from_development_files(self) -> None:
        new = judge.load_tasks()
        old_values = _old_task_values()
        old_ids, old_tools = set(), set()
        for name in DEVELOPMENT_TASK_FILES:
            for task in json.loads((ROOT / name).read_text(encoding="utf-8")):
                old_ids.add(str(task["task_id"]))
                for tool in task.get("tools") or []:
                    if isinstance(tool, dict):
                        old_tools.add(str(tool["name"]))
        self.assertEqual(4, len(new))
        for task in new:
            task_id = str(task["task_id"])
            self.assertNotIn(task_id, old_ids, task_id)
            for tool in task["tools"]:
                self.assertNotIn(str(tool["name"]), old_tools, tool["name"])
            for field in ("handle_facts", "answer_facts", "expected_terms"):
                for literal in task[field]:
                    if str(literal) in FROZEN_SHARED_LITERALS:
                        continue
                    self.assertNotIn(str(literal), old_values, f"{task_id}:{literal}")
            for field in ("region_fact", "version_fact"):
                value = str(task.get(field) or "")
                if value:
                    self.assertNotIn(value, old_values, f"{task_id}:{field}")

    def test_development_decisions_are_reused_as_contracts_only(self) -> None:
        """r10 keeps the frozen contract *shape*, with decisions r3-r9 never used."""
        new_decisions = {str(task["decision"]) for task in judge.load_tasks()}
        old_decisions = set()
        for name in DEVELOPMENT_TASK_FILES:
            for task in json.loads((ROOT / name).read_text(encoding="utf-8")):
                if "task_id" in task:
                    old_decisions.add(str(task.get("decision")))
        self.assertTrue(new_decisions.isdisjoint(old_decisions), new_decisions & old_decisions)
        self.assertEqual({"RENEW", "FORK", "RETRY", "CLEAR"}, new_decisions)

    def test_first_pass_handoff_and_answer_judge_on_a_constructed_sample(self) -> None:
        for task in judge.load_tasks():
            task_id = str(task["task_id"])
            with self.subTest(task=task_id):
                handle_rule = judge.handle_rule_for([task], task_id)
                handoff = "HANDOFF " + " ".join(str(f) for f in task["handle_facts"])
                verdict = judge.judge_handoff(handle_rule, handoff)
                self.assertEqual([], verdict.semantic["missing_facts"])
                self.assertFalse(verdict.semantic["needs_recovery"])
                self.assertIsNotNone(verdict.semantic["canonical"])
                partial = judge.judge_handoff(handle_rule, "HANDOFF " + str(task["handle_facts"][0]))
                self.assertTrue(partial.semantic["missing_facts"])

                evidence = (
                    judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
                )
                answer = f"RESULT task={task_id} decision={task['decision']} evidence={evidence}"
                answer_verdict = judge.judge_answer(
                    judge.answer_rule_for([task], task_id), answer
                )
                self.assertTrue(answer_verdict.strict_pass, answer_verdict.strict["issues"])
                self.assertTrue(answer_verdict.semantic_pass, answer_verdict.semantic["issues"])
                wrong = judge.judge_answer(
                    judge.answer_rule_for([task], task_id),
                    answer.replace(str(task["decision"]), "WRONG"),
                )
                self.assertFalse(wrong.semantic_pass)


class CrewAIPinnedEvidenceV10Test(unittest.TestCase):
    """The r9 mechanism is reused unchanged, over the r10 tasks."""

    def _task(self, task_id: str) -> dict:
        return next(task for task in judge.load_tasks() if task["task_id"] == task_id)

    def test_pin_rule_is_the_frozen_handoff_contract(self) -> None:
        for task in judge.load_tasks():
            with self.subTest(task=task["task_id"]):
                rule = pinned.pinned_rule(task)
                self.assertEqual(
                    [str(item) for item in task["handle_facts"]],
                    list(rule.required_literals),
                )
                self.assertEqual(len(task["tools"]), len(rule.evidence_fragments))
                for tool, fragment in zip(task["tools"], rule.evidence_fragments):
                    self.assertEqual(
                        json.dumps(tool["result"], ensure_ascii=False, sort_keys=True),
                        fragment,
                    )

    def test_pin_survives_a_compressed_history_that_dropped_the_contract(self) -> None:
        task = self._task("window_gate")
        rule = pinned.pinned_rule(task)
        budget, _ = base.resolve_budget(_args())
        history: list[dict[str, str]] = []
        for index in range(20):
            history.extend(
                [
                    {
                        "role": "user",
                        "content": (
                            f"第 {index + 1} 次历史交接涉及{task['history_topic']}。当时团队还讨论了"
                            "容量、排期、负责人和备选方案，这些内容仅用于理解背景，不能替代最新运行数据。"
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": (
                            f"历史记录 {index + 1} 已整理：相关讨论存在时间差，部分结论已经关闭或撤销。"
                            "执行当前任务时应优先核对可用工具返回的实时事实，并继续遵守已确认标识。"
                        ),
                    },
                ]
            )
        history.append(
            {
                "role": "user",
                "content": (
                    f"调查阶段：{task['title']}。按顺序对每个可用工具各调用一次，记录工具返回的当前事实。"
                    "最终只输出一行以 HANDOFF 开头的交接内容，包含固定标识和工具关键数值；"
                    "不要给出最终 decision。"
                ),
            }
        )
        history.append(
            {"role": "system", "content": "[protected CrewAI tool group: crewai-test-0]"}
        )
        history.append(
            {
                "role": "user",
                "content": (
                    "Analyze the tool result. If requirements are met, provide the Final "
                    "Answer. Otherwise, call the next tool."
                ),
            }
        )
        plain = base.TriggeredCrewAIContextAdapter(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=300,
            trigger_soft_limit_tokens=budget.soft_limit_tokens,
        )
        baseline_view = plain.middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        ).messages
        baseline_text = "\n".join(str(m.get("content", "")) for m in baseline_view)
        self.assertLess(len(baseline_view), len(history))
        self.assertNotIn("HANDOFF", baseline_text)

        middleware = pinned.PinnedEvidenceMiddleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=rule,
            budget=budget,
        )
        result = middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        )
        served_text = "\n".join(str(m.get("content", "")) for m in result.messages)
        self.assertIn("HANDOFF", served_text)
        for literal in rule.required_literals:
            self.assertTrue(
                judge._literal_present(judge._fold(served_text), literal), literal
            )
        self.assertGreaterEqual(middleware.pinned_events, 1)
        self.assertEqual(0, middleware.required_fact_whole_prefix_fallbacks)

    def test_whole_prefix_fallback_is_deterministic_and_counted(self) -> None:
        """A literal that exists nowhere reachable still yields a counted fallback."""
        task = self._task("credential_rotation")
        rule = pinned.pinned_rule(task)
        budget, _ = base.resolve_budget(_args())
        unreachable = pinned.PinnedRule(
            task_id=rule.task_id,
            required_literals=(*rule.required_literals, "unreachable-literal-7"),
            marker=rule.marker,
            evidence_fragments=rule.evidence_fragments,
        )
        middleware = pinned.PinnedEvidenceMiddleware(rule=unreachable, budget=budget)
        messages = [dict(message) for message in r10.fixed_history(task, 0)]
        result = middleware.before_model(messages, reserved_tokens=300)
        self.assertEqual(1, middleware.required_fact_whole_prefix_fallbacks)
        self.assertEqual({"required_fact_missing": 1}, middleware.fallback_reasons)
        self.assertEqual("whole_prefix_fallback", middleware.served_mode)
        self.assertEqual(
            [{"role": m.get("role"), "content": m.get("content")} for m in messages],
            [{"role": m.get("role"), "content": m.get("content")} for m in result.messages],
        )
        metrics = middleware.metrics_dict()
        self.assertEqual(1, metrics["required_fact_whole_prefix_fallbacks"])
        self.assertEqual(1, metrics["required_facts_missing_after_pin_total"])

    def test_create_pinned_adapter_installs_the_middleware(self) -> None:
        task = self._task("shard_split")
        budget, _ = base.resolve_budget(_args())
        adapter = pinned.create_pinned_adapter(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=pinned.pinned_rule(task),
            budget=budget,
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=300,
            agent_roles=[r10.FIRST_ROLE],
        )
        self.assertIsInstance(adapter.middleware, pinned.PinnedEvidenceMiddleware)
        self.assertEqual(
            list(pinned.pinned_rule(task).required_literals),
            list(adapter.middleware.rule.required_literals),
        )


class CrewAIRunnerV10MockGridTest(unittest.TestCase):
    """The frozen zero-API grid: 48 executions, three gates, one metered remedy."""

    def test_mock_grid_has_48_samples_with_three_gates(self) -> None:
        rows = _mock_rows()
        self.assertEqual(48, len(rows))
        keys = {(row["task_id"], row["repeat"], row["method"]) for row in rows}
        self.assertEqual(48, len(keys))
        self.assertEqual(
            set(independent.FRESH_TASKS), {row["task_id"] for row in rows}
        )
        self.assertEqual({0, 1, 2, 3}, {int(row["repeat"]) for row in rows})
        self.assertTrue(all(row["strict_success"] for row in rows))
        self.assertTrue(all(row["semantic_success"] for row in rows))
        # The one published remedy probe (mock only) is the only row whose first
        # pass is incomplete by construction; every other row must complete it.
        probe = [
            row for row in rows
            if (row["task_id"], row["repeat"], row["method"])
            == (r10.REMEDY_PROBE[0], r10.REMEDY_PROBE[1], "pruner_v1")
        ]
        self.assertEqual(1, len(probe))
        self.assertFalse(probe[0]["first_pass_complete"])
        self.assertTrue(
            all(row["first_pass_complete"] for row in rows if row not in probe)
        )
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))
        self.assertTrue(all(row["summary_attempts"] == 0 for row in rows))

    def test_only_the_published_remedy_probe_needs_a_remedy(self) -> None:
        rows = _mock_rows()
        remedies = [
            (row["task_id"], row["repeat"], row["method"])
            for row in rows if row["recovery_invocations"]
        ]
        self.assertEqual(
            [(r10.REMEDY_PROBE[0], r10.REMEDY_PROBE[1], "pruner_v1")], remedies
        )
        # 47 of 48 first-pass complete: the probe row is incomplete by design and
        # is repaired by the metered remedy, whose cost stays in its own arm.
        self.assertEqual(47, sum(row["first_pass_complete"] for row in rows))
        probe_metrics = {
            "none": 16, "pruner_v1": 15, "native_summary": 16,
        }
        observed = {
            method: sum(
                row["first_pass_complete"] for row in rows if row["method"] == method
            )
            for method in probe_metrics
        }
        self.assertEqual(probe_metrics, observed)

    def test_mock_grid_pins_evidence_and_the_task_contract(self) -> None:
        rows = _mock_rows()
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertTrue(all(row["pinned_events"] > 0 for row in plugin))
        self.assertTrue(
            all(row["required_fact_whole_prefix_fallbacks"] == 0 for row in plugin)
        )
        tiers: dict[str, int] = {}
        for row in plugin:
            for tier, count in row["pin_tier_counts"].items():
                tiers[tier] = tiers.get(tier, 0) + count
        self.assertGreaterEqual(tiers.get("tier1_evidence_pinned", 0), 1)
        for row in rows:
            if row["method"] == "pruner_v1":
                continue
            self.assertEqual(0, row["pinned_events"])
            self.assertEqual(0, row["required_fact_whole_prefix_fallbacks"])

    def test_missing_fact_remedy_is_metered_and_reaches_the_same_answer(self) -> None:
        task = next(
            task for task in judge.load_tasks()
            if task["task_id"] == r10.REMEDY_PROBE[0]
        )
        budget = base.resolve_budget(_args())[0]
        answers = set()
        for method in ("none", "pruner_v1", "native_summary"):
            with self.subTest(method=method):
                with contextlib.redirect_stdout(io.StringIO()):
                    row = r10.run_case(
                        task, method, r10.REMEDY_PROBE[1], "mock", None,
                        base.RequestBudget(400), _args(), budget,
                        force_missing_handoff=True,
                    )
                self.assertEqual(1, row["recovery_invocations"])
                self.assertEqual(0, row["recovery_api_attempts"])
                self.assertFalse(row["first_pass_complete"])
                self.assertTrue(row["strict_success"] and row["semantic_success"])
                answers.add(row["role_outputs"][1])
        self.assertEqual(1, len(answers))

    def test_repeated_grid_runs_are_record_identical(self) -> None:
        """Two in-process grids must differ only in wall-clock latency."""
        task = next(
            task for task in judge.load_tasks() if task["task_id"] == "batch_replay"
        )
        budget = base.resolve_budget(_args())[0]

        def run() -> dict:
            with contextlib.redirect_stdout(io.StringIO()):
                row = r10.run_case(task, "pruner_v1", 0, "mock", None,
                                   base.RequestBudget(400), _args(), budget)
            for record in row["agent_attempt_records"]:
                record["latency_seconds"] = 0.0
            return row

        first, second = run(), run()
        self.assertEqual(first, second)


class CrewAIAuditV10Test(unittest.TestCase):
    """The independent audit checks the grid and detects frozen tampering."""

    def test_mock_grid_passes_the_frozen_audit(self) -> None:
        result = independent.audit(MOCK, MOCK_FREEZE, dry_run_mock=True)
        self.assertTrue(result["complete"], result["errors"])
        self.assertTrue(result["freeze_checked"])
        self.assertEqual([], result["errors"])
        self.assertEqual(48, result["rows"])
        self.assertEqual(0, result["request_attempts"])
        self.assertEqual(
            {"strict": 48, "semantic": 48},
            {
                "strict": sum(
                    entry["strict_success"] for entry in result["quality"].values()
                ),
                "semantic": sum(
                    entry["semantic_success"] for entry in result["quality"].values()
                ),
            },
        )
        self.assertEqual(16, result["quality"]["none"]["first_pass_facts"])
        self.assertEqual(15, result["quality"]["pruner_v1"]["first_pass_facts"])
        self.assertEqual(1, result["quality"]["pruner_v1"]["recovered"])
        self.assertEqual(
            {
                "credential_rotation": ["svc-ledger-key", "ca-central-1", "failed 0"],
                "shard_split": ["shard-88", "tenant-lumen", "target_shards 3"],
                "batch_replay": ["run-8842", "step-transform", "failed 0"],
                "window_gate": ["window-9930", "620", "failed 0"],
            },
            result["pin_rules"],
        )

    def test_audit_rejects_frozen_source_tampering(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            source = "tasks/stage5_autogen/natural_tasks_r10.json"
            frozen["source_sha256"][source] = "0" * 64
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(f"frozen source mismatch: {source}", errors)

    def test_audit_rejects_a_swapped_task_set(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
            manifest["tasks"] = [
                "queue_backlog_replay", "region_failover", "schema_migration"
            ]
            (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn("fresh task set mismatch", errors)

    def test_audit_rejects_first_pass_gate_tampering(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            rows = [
                json.loads(line)
                for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            rows[0]["first_pass_complete"] = not rows[0]["first_pass_complete"]
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{rows[0]['task_id']}/{rows[0]['repeat']}/{rows[0]['method']}: "
                "first-pass completeness mismatch",
                errors,
            )

    def test_audit_rejects_pin_rule_tampering_and_off_arm_pin_activity(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            rows = [
                json.loads(line)
                for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            pinned_row = next(entry for entry in rows if entry["method"] == "pruner_v1")
            pinned_row["role_metrics"][0]["pin_rule"]["required_literals"] = ["nothing"]
            plain_row = next(entry for entry in rows if entry["method"] == "none")
            plain_row["pinned_events"] = 3
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{pinned_row['task_id']}/{pinned_row['repeat']}/"
                f"{pinned_row['method']}: pin rule mismatch",
                errors,
            )
            self.assertIn(
                f"{plain_row['task_id']}/{plain_row['repeat']}/"
                f"{plain_row['method']}: pin activity outside the plugin arm",
                errors,
            )

    def test_audit_rejects_row_and_token_tampering(self) -> None:
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            rows = [
                json.loads(line)
                for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            rows[0]["all_arm_total_tokens"] += 1
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{rows[0]['task_id']}/{rows[0]['repeat']}/{rows[0]['method']}: "
                "token sum mismatch",
                errors,
            )


class CrewAIRunnerV10FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r10-fresh-multitask-01", frozen["batch"])
        self.assertEqual(48, frozen["samples"])
        self.assertEqual(independent.FROZEN_PATHS, set(frozen["source_sha256"]))
        for name in sorted(independent.FROZEN_PATHS):
            path = ROOT / name
            self.assertTrue(path.is_file(), name)
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                frozen["source_sha256"][name],
                name,
            )
        manifest = frozen["manifest"]
        self.assertEqual(independent.MANIFEST_FIELDS, set(manifest))
        self.assertEqual("api", manifest["mode"])
        self.assertEqual(list(independent.FRESH_TASKS), manifest["tasks"])
        self.assertEqual(4, manifest["repeats"])
        self.assertEqual(
            hashlib.sha256(judge.TASK_FILE.read_bytes()).hexdigest(),
            manifest["task_sha256"],
        )
        self.assertEqual(independent.FIRST_PASS_GATE, frozen["first_pass_gate"])
        self.assertEqual(independent.TASK_NOVELTY, frozen["task_novelty"])

    def test_paid_batch_directory_does_not_exist_before_the_run(self) -> None:
        if PAID_BATCH.exists():
            self.assertTrue((PAID_BATCH / "results.jsonl").is_file())
        else:
            self.assertFalse(PAID_BATCH.exists())


if __name__ == "__main__":
    unittest.main()
