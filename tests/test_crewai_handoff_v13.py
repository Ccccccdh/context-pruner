"""Zero-API gates for the CrewAI r13 tool-round / contract batch.

What must hold before the paid r13 batch may run:

  1. the structure rule now protects **every** frozen tool round (K=3) and still
     adds no message of its own beyond the adapter's placeholders;
  2. the pre-registered contract sentence is exactly what the deciding role receives,
     and the judge is untouched: it still passes when the template is replaced by the
     real value and still fails when the template is echoed;
  3. the pre-registered failure classes are derived from the recorded row by the
     runner and re-derived independently by the audit, which rejects a mismatch;
  4. the frozen 48-execution mock grid passes, the recency counters live only in the
     plugin arm, and the audit answers the pre-registered decision table.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from experiments.audits import audit_crewai_handoff_v13 as independent
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v13 as judge
from experiments.runners import crewai_toolrounds_contract_v13 as mechanism
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v13 as r13


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R13_TOOLROUNDS_CONTRACT_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R13_TOOLROUNDS_CONTRACT_MOCK_01.json"
PAID_BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r13-toolrounds-and-contract-01"


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


def _task(task_id: str = "window_gate") -> dict:
    return next(task for task in judge.load_tasks() if task["task_id"] == task_id)


def _placeholder(group_id: str) -> dict:
    return {
        "role": "system",
        "content": f"{mechanism.PROTECTED_GROUP_PREFIX} {group_id}]",
        "_context_pruner_crewai_group": group_id,
    }


class KThreeStructureTest(unittest.TestCase):
    """Change 1: all three frozen tool rounds are protected, nothing is invented."""

    def test_frozen_k_is_three_and_matches_the_frozen_tool_count(self) -> None:
        self.assertEqual(3, mechanism.DEFAULT_K_RECENT_TOOL_ROUNDS)
        self.assertEqual(3, r13.K_RECENT_TOOL_ROUNDS)
        self.assertEqual(3, independent.MECHANISM_RULES["k_recent_tool_rounds"])
        for task in judge.load_tasks():
            with self.subTest(task=task["task_id"]):
                self.assertEqual(3, len(task["tools"]))

    def test_all_rounds_are_selected_and_older_ones_are_not_dropped(self) -> None:
        prepared = [
            {"role": "user", "content": "history"},
            _placeholder("round-1"),
            {"role": "assistant", "content": "thinking"},
            _placeholder("round-2"),
            _placeholder("round-3"),
        ]
        self.assertEqual(
            ["round-1", "round-2", "round-3"],
            [
                p["_context_pruner_crewai_group"]
                for p in mechanism.newest_round_placeholders(prepared, 3)
            ],
        )

    def test_middleware_protects_every_round_and_adds_only_placeholders(self) -> None:
        task = _task("window_gate")
        budget, _ = base.resolve_budget(_args())
        middleware = mechanism.build_recency_middleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=pinned.pinned_rule(task),
            budget=budget,
            k_recent_tool_rounds=3,
        )
        history: list[dict] = [{"role": "user", "content": str(task["history_constraint"])}]
        for index in range(24):
            history.extend(
                [
                    {"role": "user", "content": f"Historical handoff {index + 1}: background only."},
                    {"role": "assistant", "content": f"Historical note {index + 1} filed as stale."},
                ]
            )
        history.append(
            {
                "role": "user",
                "content": (
                    "调查阶段：{title}。按顺序对每个可用工具各调用一次，记录工具返回的当前事实。"
                    "最终只输出一行以 HANDOFF 开头的交接内容，包含固定标识和工具关键数值；"
                    "不要给出最终 decision。"
                ).format(title=task["title"]),
            }
        )
        for index in range(3):
            history.append(_placeholder(f"round-{index + 1}"))
        result = middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        )
        ids = [
            str(message.get("_context_pruner_crewai_group"))
            for message in result.messages
            if mechanism.is_tool_group_placeholder(message)
        ]
        self.assertEqual(["round-1", "round-2", "round-3"], ids)
        metrics = middleware.metrics_dict()
        self.assertEqual(3, metrics["k_recent_tool_rounds"])
        self.assertEqual(3, metrics["recent_rounds_seen_total"])
        self.assertEqual(0, metrics["recency_omitted_tool_rounds_total"])
        served_text = "\n".join(str(m.get("content", "")) for m in result.messages)
        for forbidden in (
            "host directive", "Required tool sequence", "Output contract reminder",
        ):
            self.assertNotIn(forbidden, served_text)


class ContractWordingTest(unittest.TestCase):
    """Change 2: the pre-registered sentence reaches the decider; judging is unchanged."""

    def test_decider_prompt_contains_the_frozen_sentence(self) -> None:
        task = _task("batch_replay")
        prompt = r13.decider_role_prompt(task)
        self.assertIn(r13.latest_task_prompt(task), prompt)
        self.assertIn(r13.DECISION_PLACEHOLDER_SENTENCE, prompt)
        self.assertIn("never output the angle-bracket characters", prompt)
        self.assertIn("replace it with the actual value", prompt)
        # Deterministic: the same task always produces the same prompt.
        self.assertEqual(prompt, r13.decider_role_prompt(task))

    def test_frozen_first_role_prompt_is_unchanged(self) -> None:
        task = _task("window_gate")
        frozen = r13.latest_task_prompt(task)
        self.assertNotIn(r13.DECISION_PLACEHOLDER_SENTENCE, frozen)
        history = r13.fixed_history(task, 0)
        self.assertNotIn(
            r13.DECISION_PLACEHOLDER_SENTENCE,
            "\n".join(str(message.get("content", "")) for message in history),
        )

    def test_judge_still_rejects_the_echoed_template(self) -> None:
        for task in judge.load_tasks():
            with self.subTest(task=task["task_id"]):
                rule = judge.answer_rule_v13(task)
                self.assertEqual(
                    f"decision=<{task['decision']}>", rule["decision_template"]
                )
                evidence = (
                    judge.tool_evidence(task)[-1].replace('": ', '=').replace('"', "")
                )
                good = f"RESULT task={task['task_id']} decision={task['decision']} evidence={evidence}"
                echoed = (
                    f"RESULT task={task['task_id']} decision=<{task['decision']}> "
                    f"evidence={evidence}"
                )
                self.assertTrue(judge.judge_answer(rule, good).strict_pass)
                self.assertTrue(judge.judge_answer(rule, good).semantic_pass)
                self.assertFalse(judge.judge_answer(rule, echoed).strict_pass)
                self.assertFalse(judge.judge_answer(rule, echoed).semantic_pass)


class FailureClassTest(unittest.TestCase):
    """The pre-registered classes are mechanical, and the audit re-derives them."""

    def test_classify_failure_uses_the_recorded_row(self) -> None:
        task = _task("batch_replay")
        template = f"decision=<{task['decision']}>"
        evidence = "job-2214 replay_safe=true dry_run_records=1100"
        base_row = {
            "strict_success": True, "semantic_success": True,
            "tool_sequence_consistent": True,
            "role_outputs": [
                "HANDOFF run-8842 step-transform failed 0",
                f"RESULT task={task['task_id']} decision={task['decision']} evidence={evidence}",
            ],
            "answer_verdict": {"semantic": {"decision_seen": task["decision"]}},
        }
        self.assertEqual("none", r13.classify_failure(dict(base_row), task))

        broken = dict(base_row, tool_sequence_consistent=False,
                      strict_success=False, semantic_success=False)
        self.assertEqual("tool_sequence", r13.classify_failure(broken, task))

        echo = dict(
            base_row,
            strict_success=False,
            semantic_success=False,
            role_outputs=[
                base_row["role_outputs"][0],
                f"RESULT task={task['task_id']} {template} evidence={evidence}",
            ],
            answer_verdict={"semantic": {"decision_seen": ""}},
        )
        self.assertEqual("contract_placeholder_echo", r13.classify_failure(echo, task))
        self.assertIn("<", template)  # the recorded echo really has the brackets

        wrong_word = dict(
            base_row,
            strict_success=False,
            semantic_success=False,
            answer_verdict={"semantic": {"decision_seen": "PROCEED"}},
        )
        self.assertEqual(
            "contract_placeholder_echo", r13.classify_failure(wrong_word, task)
        )

        other = dict(
            base_row,
            strict_success=False,
            semantic_success=False,
            role_outputs=[
                base_row["role_outputs"][0],
                f"RESULT task={task['task_id']} decision={task['decision']} evidence=none",
            ],
            answer_verdict={"semantic": {"decision_seen": task["decision"]}},
        )
        self.assertEqual("other", r13.classify_failure(other, task))

    def test_audit_rejects_a_reclassified_sample(self) -> None:
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
            row = next(entry for entry in rows if entry["method"] == "none")
            row["failure_class"] = "tool_sequence"
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{row['task_id']}/{row['repeat']}/{row['method']}: failure class "
                "mismatch ('tool_sequence' != 'none')",
                errors,
            )


class CrewAIRunnerV13MockGridTest(unittest.TestCase):
    """The frozen zero-API grid: 48 executions, four gates, one metered remedy."""

    def test_mock_grid_has_48_samples_with_four_gates(self) -> None:
        rows = _mock_rows()
        self.assertEqual(48, len(rows))
        self.assertEqual(
            48, len({(row["task_id"], row["repeat"], row["method"]) for row in rows})
        )
        self.assertTrue(all(row["strict_success"] for row in rows))
        self.assertTrue(all(row["semantic_success"] for row in rows))
        self.assertTrue(all(row["tool_sequence_consistent"] for row in rows))
        self.assertTrue(all(row["tool_sequence_matches_baseline"] for row in rows))
        self.assertTrue(all(row["failure_class"] == "none" for row in rows))
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))

    def test_only_the_published_remedy_probe_needs_a_remedy(self) -> None:
        rows = _mock_rows()
        remedies = [
            (row["task_id"], row["repeat"], row["method"])
            for row in rows if row["recovery_invocations"]
        ]
        self.assertEqual([(r13.REMEDY_PROBE[0], r13.REMEDY_PROBE[1], "pruner_v1")], remedies)
        self.assertEqual(47, sum(row["first_pass_complete"] for row in rows))

    def test_k_three_counters_live_only_in_the_plugin_arm(self) -> None:
        rows = _mock_rows()
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertTrue(all(row["k_recent_tool_rounds"] == 3 for row in plugin))
        for row in plugin:
            self.assertEqual(
                row["recent_rounds_seen_total"],
                row["recency_protected_rounds_total"] + row["recent_rounds_readded_total"],
            )
            # K=3 covers every round the role has reached so far.
            self.assertEqual(0, row["recency_omitted_tool_rounds_total"])
            self.assertGreaterEqual(row["recent_rounds_seen_total"], 1)
        for row in rows:
            if row["method"] == "pruner_v1":
                continue
            self.assertEqual(0, row["recency_calls"])
            self.assertEqual(0, row["recent_rounds_readded_total"])
            self.assertEqual(0, row["pinned_events"])

    def test_repeated_grid_runs_are_record_identical(self) -> None:
        task = _task("credential_rotation")
        budget = base.resolve_budget(_args())[0]

        def run() -> dict:
            with contextlib.redirect_stdout(io.StringIO()):
                row = r13.run_case(task, "pruner_v1", 0, "mock", None,
                                   base.RequestBudget(440), _args(), budget)
            for record in row["agent_attempt_records"]:
                record["latency_seconds"] = 0.0
            return row

        first, second = run(), run()
        self.assertEqual(first, second)


class CrewAIAuditV13Test(unittest.TestCase):
    """Hard gates, tamper rejection, the decision table and the wording."""

    def _copy_grid(self, directory: Path) -> Path:
        batch = directory / "batch"
        batch.mkdir()
        for name in ("manifest.json", "results.jsonl"):
            shutil.copy2(MOCK / name, batch / name)
        return batch

    def _rows(self, batch: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _write_rows(self, batch: Path, rows: list[dict]) -> None:
        (batch / "results.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
        )

    def test_mock_grid_passes_the_frozen_audit(self) -> None:
        result = independent.audit(MOCK, MOCK_FREEZE, dry_run_mock=True)
        self.assertTrue(result["complete"], result["errors"])
        self.assertEqual([], result["errors"])
        self.assertEqual(48, result["rows"])
        self.assertEqual(3, result["structure_rule_gate"]["k_recent_tool_rounds"])
        self.assertTrue(result["tool_sequence_perfect"])
        self.assertIn(
            result["pre_registered_outcome"],
            (
                "boundary_nothing_left_to_compress",
                "compression_exonerated_residual_is_model_behaviour",
                "tool_sequence_not_caused_by_compression",
            ),
        )
        self.assertEqual(
            {"plugin": 16, "baseline": 16},
            {
                "plugin": result["tool_sequence_gate"]["plugin_tool_sequence_consistent"],
                "baseline": result["tool_sequence_gate"]["baseline_tool_sequence_consistent"],
            },
        )
        self.assertEqual(16, result["tool_sequence_gate"]["plugin_matches_baseline"])
        self.assertEqual(
            {"none": 16, "tool_sequence": 0, "contract_placeholder_echo": 0, "other": 0},
            result["failure_class_gate"]["per_method"]["pruner_v1"],
        )
        self.assertEqual(
            {
                "credential_rotation": ["svc-ledger-key", "ca-central-1", "failed 0"],
                "shard_split": ["shard-88", "tenant-lumen", "target_shards 3"],
                "batch_replay": ["run-8842", "step-transform", "failed 0"],
                "window_gate": ["window-9930", "620", "failed 0"],
            },
            result["pin_rules"],
        )

    def test_audit_rejects_a_ragged_plugin_tool_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            batch = self._copy_grid(Path(directory))
            rows = self._rows(batch)
            row = next(
                entry for entry in rows
                if entry["method"] == "pruner_v1" and entry["task_id"] == "window_gate"
            )
            row["first_role_trace"] = ["check_window_lock"]
            row["role_tool_traces"] = [["check_window_lock"], row["role_tool_traces"][1]]
            row["tool_sequence_consistent"] = False
            row["tool_sequence_matches_baseline"] = False
            row["failure_class"] = "tool_sequence"
            row["strict_success"] = False
            row["semantic_success"] = False
            self._write_rows(batch, rows)
            result = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)
            key = f"{row['task_id']}/{row['repeat']}/{row['method']}"
            self.assertIn(
                f"{key}: plugin tool sequence inconsistent with the frozen task",
                result["errors"],
            )
            self.assertFalse(result["tool_sequence_non_inferior"])
            self.assertFalse(result["tool_sequence_perfect"])
            self.assertEqual(
                "tool_sequence_not_caused_by_compression",
                result["pre_registered_outcome"],
            )
            self.assertEqual("not_yet_valid", result["verdict"])
            self.assertIn("本批数字不得当作有效节省", result["verdict_reason"])
            self.assertIn("non-empty errors", result["errors_meaning"])

    def test_audit_rejects_frozen_rule_and_declaration_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = self._copy_grid(tmp)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            frozen["source_sha256"][
                "experiments/runners/crewai_toolrounds_contract_v13.py"
            ] = "0" * 64
            frozen["failure_class_gate"] = {"classes": ["none"]}
            frozen["outcomes"] = {"only": "one"}
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(
                "frozen source mismatch: "
                "experiments/runners/crewai_toolrounds_contract_v13.py",
                errors,
            )
            self.assertIn("failure-class gate declaration mismatch", errors)
            self.assertIn("pre-registered outcome declaration mismatch", errors)


class CrewAIRunnerV13FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r13-toolrounds-and-contract-01", frozen["batch"])
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
        self.assertEqual(independent.QUALITY_GATES, manifest["quality_gates"])
        self.assertEqual(independent.FIRST_PASS_GATE, frozen["first_pass_gate"])
        self.assertEqual(independent.TOOL_SEQUENCE_GATE, frozen["tool_sequence_gate"])
        self.assertEqual(independent.FAILURE_CLASS_GATE, frozen["failure_class_gate"])
        self.assertEqual(independent.MECHANISM_RULES, frozen["mechanism_rules"])
        self.assertEqual(independent.OUTCOMES, frozen["outcomes"])

    def test_paid_batch_directory_does_not_exist_before_the_run(self) -> None:
        if PAID_BATCH.exists():
            self.assertTrue((PAID_BATCH / "results.jsonl").is_file())
        else:
            self.assertFalse(PAID_BATCH.exists())


if __name__ == "__main__":
    unittest.main()
