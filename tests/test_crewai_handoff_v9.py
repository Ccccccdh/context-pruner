"""Zero-API gates for the CrewAI r9 runner: pinned evidence, first-pass gate, audit.

Three things must hold before the paid r9 batch is allowed to run:

  1. the frozen 27-execution mock grid passes with the new first-class
     first-pass gate, and the plugin's first-pass completeness no longer depends
     on the metered remedy call;
  2. the pin actually changes the model view in the *shape* the frozen r8 batch
     showed to be lossy: with enough history for the trigger gate to fire, the
     contract-carrying task statement survives compression and every required
     literal is in the view;
  3. the deterministic whole-prefix fallback fires and is counted when a
     required literal cannot be pinned, and the independent audit detects frozen
     tampering.
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

from experiments.audits import audit_crewai_handoff_v9 as independent
from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v9 as r9


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r9-multitask-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R9_MULTITASK_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R9_MULTITASK_MOCK_01.json"
PAID_BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r9-multitask-01"


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        model="mock", base_url="", max_output_tokens=512, fixed_reserved_tokens=300,
        max_summary_tokens=1024, max_summary_calls=4, provider_soft=1200,
        provider_hard=3000, provider_target=900, soft_limit=0, hard_limit=0, target=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _mock_rows():
    return [
        json.loads(line)
        for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class CrewAIPinnedEvidenceMechanismTest(unittest.TestCase):
    """The mechanism itself: pins the contract and the required literals."""

    def _task(self, task_id: str) -> dict:
        return next(task for task in r9.load_tasks() if task["task_id"] == task_id)

    def test_pin_rule_is_the_frozen_handoff_contract(self) -> None:
        for task in r9.load_tasks():
            with self.subTest(task=task["task_id"]):
                rule = pinned.pinned_rule(task)
                self.assertEqual([str(item) for item in task["handle_facts"]],
                                 list(rule.required_literals))
                self.assertEqual(len(task["tools"]), len(rule.evidence_fragments))
                for tool, fragment in zip(task["tools"], rule.evidence_fragments):
                    self.assertEqual(
                        json.dumps(tool["result"], ensure_ascii=False, sort_keys=True),
                        fragment,
                    )

    def test_evidence_pin_uses_the_exact_tool_bytes(self) -> None:
        task = self._task("schema_migration")
        rule = pinned.pinned_rule(task)
        middleware = pinned.PinnedEvidenceMiddleware(rule=rule)
        pins = middleware._evidence_pins(["failed 0"])
        self.assertTrue(pins)
        self.assertIn("failed", pins[0]["content"])
        self.assertIn('"failed": 0', pins[0]["content"])

    def test_pin_survives_a_compressed_history_that_dropped_the_contract(self) -> None:
        """The r8 lossy shape: long history, task statement compressed away."""
        task = self._task("region_failover")
        rule = pinned.pinned_rule(task)
        budget, _ = base.resolve_budget(_args())
        history: list[dict[str, str]] = []
        for index in range(20):
            history.extend(
                [
                    {
                        "role": "user",
                        "content": (
                            f"第 {index + 1} 次历史交接涉及跨区容量预留、旧集群退役记录、演练复盘和"
                            "已取消的切换窗口。当时团队还讨论了容量、排期、负责人和备选方案，这些内容"
                            "仅用于理解背景，不能替代最新运行数据。"
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
        statement = (
            f"调查阶段：{task['title']}。按顺序对每个可用工具各调用一次，记录工具返回的当前事实。"
            "最终只输出一行以 HANDOFF 开头的交接内容，包含固定标识和工具关键数值；不要给出最终 decision。"
        )
        history.append({"role": "user", "content": statement})
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
        baseline_text = "\n".join(str(message.get("content", "")) for message in baseline_view)
        self.assertLess(len(baseline_view), len(history))  # compression really ran
        self.assertNotIn("HANDOFF", baseline_text)  # the frozen r8 failure shape

        middleware = pinned.PinnedEvidenceMiddleware(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=rule,
            budget=budget,
        )
        result = middleware.before_model(
            [dict(message) for message in history], reserved_tokens=300
        )
        served_text = "\n".join(str(message.get("content", "")) for message in result.messages)
        self.assertIn("HANDOFF", served_text)
        for literal in rule.required_literals:
            self.assertTrue(judge._literal_present(judge._fold(served_text), literal), literal)
        self.assertGreaterEqual(middleware.pinned_events, 1)
        self.assertEqual(0, middleware.required_fact_whole_prefix_fallbacks)

    def test_whole_prefix_fallback_is_deterministic_and_counted(self) -> None:
        """A literal that exists nowhere reachable still yields a counted fallback."""
        task = self._task("schema_migration")
        rule = pinned.pinned_rule(task)
        budget, _ = base.resolve_budget(_args())
        unreachable = pinned.PinnedRule(
            task_id=rule.task_id,
            required_literals=(*rule.required_literals, "unreachable-literal-7"),
            marker=rule.marker,
            evidence_fragments=rule.evidence_fragments,
        )
        middleware = pinned.PinnedEvidenceMiddleware(rule=unreachable, budget=budget)
        messages = [dict(message) for message in r9.fixed_history(task, 0)]
        result = middleware.before_model(messages, reserved_tokens=300)
        self.assertEqual(1, middleware.required_fact_whole_prefix_fallbacks)
        self.assertEqual(
            {"required_fact_missing": 1}, middleware.fallback_reasons
        )
        self.assertEqual("whole_prefix_fallback", middleware.served_mode)
        # The fallback serves the uncompressed history verbatim: the served view
        # must be exactly the raw message list, in order, with nothing dropped.
        self.assertEqual(
            [{"role": message.get("role"), "content": message.get("content")}
             for message in messages],
            [{"role": message.get("role"), "content": message.get("content")}
             for message in result.messages],
        )
        self.assertLess(middleware.pinned_events, 1 + len(messages))
        served_text = "\n".join(
            str(message.get("content", "")) for message in result.messages
        )
        # ``schema-42`` is in the task statement and therefore in the fallback
        # prefix; ``failed 0`` only ever came from the pinned tool evidence, so a
        # fallback that cannot pin it serves history without it.  That is the
        # honest, counted outcome: the mechanism reports the gap instead of
        # inventing a fact.
        self.assertTrue(judge._literal_present(judge._fold(served_text), "schema-42"))
        self.assertFalse(judge._literal_present(judge._fold(served_text), "failed 0"))
        metrics = middleware.metrics_dict()
        self.assertEqual(1, metrics["required_fact_whole_prefix_fallbacks"])
        self.assertEqual(1, metrics["required_facts_missing_after_pin_total"])

    def test_create_pinned_adapter_installs_the_middleware(self) -> None:
        task = self._task("queue_backlog_replay")
        budget, _ = base.resolve_budget(_args())
        adapter = pinned.create_pinned_adapter(
            base.ContextPluginConfig(enabled=True, method="pruner_v1", budget=budget),
            rule=pinned.pinned_rule(task),
            budget=budget,
            task_state=str(task["history_constraint"]),
            fixed_reserved_tokens=300,
            agent_roles=[r9.FIRST_ROLE],
        )
        self.assertIsInstance(adapter.middleware, pinned.PinnedEvidenceMiddleware)
        self.assertEqual(
            list(pinned.pinned_rule(task).required_literals),
            list(adapter.middleware.rule.required_literals),
        )


class CrewAIRunnerV9MockGridTest(unittest.TestCase):
    """The frozen zero-API grid: 27 executions, three gates, one metered remedy."""

    def test_mock_grid_has_27_samples_with_three_gates(self) -> None:
        rows = _mock_rows()
        self.assertEqual(27, len(rows))
        keys = {(row["task_id"], row["repeat"], row["method"]) for row in rows}
        self.assertEqual(27, len(keys))
        self.assertTrue(all(row["strict_success"] for row in rows))
        self.assertTrue(all(row["semantic_success"] for row in rows))
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))
        self.assertTrue(all(row["summary_attempts"] == 0 for row in rows))
        for row in rows:
            self.assertIn("first_pass_complete", row)

    def test_plugin_first_pass_completeness_does_not_need_the_remedy(self) -> None:
        rows = _mock_rows()
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        remedies = [row for row in plugin if row["recovery_invocations"]]
        self.assertEqual(
            [("queue_backlog_replay", 2, "pruner_v1")],
            [(row["task_id"], row["repeat"], row["method"]) for row in remedies],
        )
        self.assertEqual(8, sum(row["first_pass_complete"] for row in plugin))
        for row in plugin:
            if row["recovery_invocations"]:
                continue
            self.assertTrue(row["first_pass_complete"], (row["task_id"], row["repeat"]))

    def test_mock_grid_pins_evidence_and_the_task_contract(self) -> None:
        plugin = [row for row in _mock_rows() if row["method"] == "pruner_v1"]
        self.assertTrue(all(row["pinned_events"] > 0 for row in plugin))
        self.assertTrue(all(row["required_fact_whole_prefix_fallbacks"] == 0 for row in plugin))
        tiers = {}
        for row in plugin:
            for tier, count in row["pin_tier_counts"].items():
                tiers[tier] = tiers.get(tier, 0) + count
        self.assertEqual(36, tiers["tier1_evidence_pinned"])
        self.assertEqual(18, tiers["tier2_task_contract"])
        for row in _mock_rows():
            if row["method"] == "pruner_v1":
                continue
            self.assertEqual(0, row["pinned_events"])
            self.assertEqual(0, row["required_fact_whole_prefix_fallbacks"])

    def test_missing_fact_remedy_is_metered_and_reaches_the_same_answer(self) -> None:
        task = next(task for task in r9.load_tasks()
                    if task["task_id"] == "queue_backlog_replay")
        budget = base.resolve_budget(_args())[0]
        rows = {}
        for method in ("none", "pruner_v1", "native_summary"):
            with contextlib.redirect_stdout(io.StringIO()):
                rows[method] = r9.run_case(
                    task, method, 2, "mock", None, base.RequestBudget(200),
                    _args(), budget, force_missing_handoff=True,
                )
        answers = set()
        for method, row in rows.items():
            with self.subTest(method=method):
                self.assertEqual(1, row["recovery_invocations"])
                self.assertEqual(0, row["recovery_api_attempts"])
                self.assertFalse(row["first_pass_complete"])
                self.assertIn("12400", row["handoff_for_decider"])
                self.assertTrue(row["strict_success"] and row["semantic_success"])
                answers.add(row["role_outputs"][1])
        self.assertEqual(1, len(answers))

    def test_repeated_grid_runs_are_record_identical(self) -> None:
        """Two in-process grids must differ only in wall-clock latency."""
        task = next(task for task in r9.load_tasks()
                    if task["task_id"] == "region_failover")
        budget = base.resolve_budget(_args())[0]

        def run() -> dict:
            with contextlib.redirect_stdout(io.StringIO()):
                row = r9.run_case(task, "pruner_v1", 0, "mock", None,
                                  base.RequestBudget(200), _args(), budget)
            for record in row["agent_attempt_records"]:
                record["latency_seconds"] = 0.0
            return row

        first, second = run(), run()
        self.assertEqual(first, second)


class CrewAIAuditV9Test(unittest.TestCase):
    """The independent audit checks the grid and detects frozen tampering."""

    def test_mock_grid_passes_the_frozen_audit(self) -> None:
        result = independent.audit(MOCK, MOCK_FREEZE, dry_run_mock=True)
        self.assertTrue(result["complete"], result["errors"])
        self.assertTrue(result["freeze_checked"])
        self.assertEqual([], result["errors"])
        self.assertEqual(27, result["rows"])
        self.assertEqual(0, result["request_attempts"])
        self.assertEqual(
            {"strict": 27, "semantic": 27},
            {
                "strict": sum(
                    entry["strict_success"] for entry in result["quality"].values()
                ),
                "semantic": sum(
                    entry["semantic_success"] for entry in result["quality"].values()
                ),
            },
        )
        self.assertEqual(9, result["quality"]["none"]["first_pass_facts"])
        self.assertEqual(8, result["quality"]["pruner_v1"]["first_pass_facts"])
        self.assertEqual(1, result["quality"]["pruner_v1"]["recovered"])
        self.assertEqual(8, result["first_pass_gate"]["plugin_first_pass_facts"])
        self.assertEqual(
            {
                "queue_backlog_replay": ["PIPE-3391", "us-west-2", "12400"],
                "region_failover": ["apollo-cache-9", "ap-south-1", "45%"],
                "schema_migration": ["schema-42", "failed 0"],
            },
            result["pin_rules"],
        )

    def test_audit_rejects_source_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            source = "experiments/runners/crewai_pinned_evidence_v9.py"
            frozen["source_sha256"][source] = "0" * 64
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(f"frozen source mismatch: {source}", errors)

    def test_audit_rejects_first_pass_gate_tampering(self) -> None:
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

    def test_audit_rejects_pin_rule_tampering(self) -> None:
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
            row = next(entry for entry in rows if entry["method"] == "pruner_v1")
            row["role_metrics"][0]["pin_rule"]["required_literals"] = ["nothing"]
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{row['task_id']}/{row['repeat']}/{row['method']}: pin rule mismatch",
                errors,
            )

    def test_audit_rejects_pin_activity_outside_the_plugin_arm(self) -> None:
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
            row["pinned_events"] = 3
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(entry) for entry in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{row['task_id']}/{row['repeat']}/{row['method']}: "
                "pin activity outside the plugin arm",
                errors,
            )

    def test_audit_rejects_manifest_and_row_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
            manifest["max_api_requests"] += 1
            (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIn(
                "manifest mismatch: max_api_requests",
                independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"],
            )
            manifest["max_api_requests"] -= 1
            (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            rows = [
                json.loads(line)
                for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            rows[0]["answer_verdict"]["semantic_pass"] = not rows[0]["answer_verdict"]["semantic_pass"]
            rows[0]["semantic_success"] = not rows[0]["semantic_success"]
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{rows[0]['task_id']}/{rows[0]['repeat']}/{rows[0]['method']}: "
                "semantic verdict mismatch",
                errors,
            )
            self.assertIn(
                f"{rows[0]['task_id']}/{rows[0]['repeat']}/{rows[0]['method']}: "
                "semantic success mismatch",
                errors,
            )

    def test_audit_rejects_tool_evidence_tampering(self) -> None:
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
            rows[0]["role_tool_traces"][0] = []
            (batch / "results.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
            )
            errors = independent.audit(batch, MOCK_FREEZE, dry_run_mock=True)["errors"]
            self.assertIn(
                f"{rows[0]['task_id']}/{rows[0]['repeat']}/{rows[0]['method']}: "
                "tool evidence mismatch",
                errors,
            )


class CrewAIRunnerV9FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r9-multitask-01", frozen["batch"])
        self.assertEqual(27, frozen["samples"])
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
        self.assertEqual(
            ["queue_backlog_replay", "region_failover", "schema_migration"],
            manifest["tasks"],
        )
        self.assertEqual(
            hashlib.sha256(judge.TASK_FILE.read_bytes()).hexdigest(),
            manifest["task_sha256"],
        )
        self.assertEqual(independent.FIRST_PASS_GATE, frozen["first_pass_gate"])

    def test_paid_batch_directory_does_not_exist_before_the_run(self) -> None:
        # The freeze is only meaningful while the paid directory is still absent;
        # once the run has happened this assertion is replaced by the audit.
        if PAID_BATCH.exists():
            self.assertTrue((PAID_BATCH / "results.jsonl").is_file())
        else:
            self.assertFalse(PAID_BATCH.exists())


if __name__ == "__main__":
    unittest.main()
