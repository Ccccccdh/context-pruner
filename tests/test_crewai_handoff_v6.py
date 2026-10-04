"""Zero-API gates for the CrewAI r6 runner, judge wiring and independent audit."""

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

from experiments.audits import audit_crewai_handoff_v6 as independent
from experiments.runners import crewai_semantic_equivalence_v6 as judge
from experiments.runners import run_crewai_handoff_v6 as r5


ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "runs/stage5-crewai/crewai-two-role-r6-multitask-mock-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R6_MULTITASK_01.json"
MOCK_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R6_MULTITASK_MOCK_01.json"


def _args(**overrides) -> SimpleNamespace:
    values = dict(
        model="mock", base_url="", max_output_tokens=512, fixed_reserved_tokens=300,
        max_summary_tokens=1024, max_summary_calls=4, provider_soft=1200,
        provider_hard=3000, provider_target=900, soft_limit=0, hard_limit=0, target=0,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


class CrewAIRunnerV5MockGridTest(unittest.TestCase):
    """The frozen zero-API grid: 27 executions, both gates, one metered remedy."""

    def test_mock_grid_has_27_samples_with_both_gates(self) -> None:
        rows = [
            json.loads(line)
            for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(27, len(rows))
        keys = {(row["task_id"], row["repeat"], row["method"]) for row in rows}
        self.assertEqual(27, len(keys))
        self.assertTrue(all(row["strict_success"] for row in rows))
        self.assertTrue(all(row["semantic_success"] for row in rows))
        self.assertTrue(all(row["api_request_attempts"] == 0 for row in rows))
        self.assertTrue(all(row["summary_attempts"] == 0 for row in rows))

    def test_mock_grid_records_the_injected_missing_fact_remedy(self) -> None:
        rows = [
            json.loads(line)
            for line in (MOCK / "results.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        remedies = [row for row in rows if row["recovery_invocations"]]
        self.assertEqual(
            [("queue_backlog_replay", 2, "pruner_v1")],
            [(row["task_id"], row["repeat"], row["method"]) for row in remedies],
        )
        row = remedies[0]
        self.assertEqual("HANDOFF", row["role_outputs"][0])
        self.assertFalse(row["handoff_first_pass"]["semantic"]["missing_facts"] == [])
        self.assertTrue(row["handoff_recovery"]["semantic"]["pass"])
        self.assertTrue(row["strict_success"])

    def test_missing_fact_remedy_is_metered_and_reaches_the_same_answer(self) -> None:
        task = next(task for task in r5.load_tasks() if task["task_id"] == "queue_backlog_replay")
        budget = r5.base.resolve_budget(_args())[0]
        rows = {}
        for method in ("none", "pruner_v1", "native_summary"):
            with contextlib.redirect_stdout(io.StringIO()):
                rows[method] = r5.run_case(
                    task, method, 2, "mock", None, r5.base.RequestBudget(200),
                    _args(), budget, force_missing_handoff=True,
                )
        answers = set()
        for method, row in rows.items():
            with self.subTest(method=method):
                self.assertEqual(1, row["recovery_invocations"])
                self.assertEqual(0, row["recovery_api_attempts"])
                self.assertIn("12400", row["handoff_for_decider"])
                self.assertTrue(row["strict_success"] and row["semantic_success"])
                answers.add(row["role_outputs"][1])
        self.assertEqual(1, len(answers))


class CrewAIAuditV5Test(unittest.TestCase):
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

    def test_audit_rejects_source_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            batch = tmp / "batch"
            batch.mkdir()
            for name in ("manifest.json", "results.jsonl"):
                shutil.copy2(MOCK / name, batch / name)
            frozen = json.loads(MOCK_FREEZE.read_text(encoding="utf-8"))
            source = "experiments/runners/crewai_semantic_equivalence_v6.py"
            frozen["source_sha256"][source] = "0" * 64
            wrong = tmp / "wrong-freeze.json"
            wrong.write_text(json.dumps(frozen), encoding="utf-8")
            errors = independent.audit(batch, wrong, dry_run_mock=True)["errors"]
            self.assertIn(f"frozen source mismatch: {source}", errors)

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


class CrewAIRunnerV5FreezeTest(unittest.TestCase):
    """The paid freeze must describe the paid batch and hash every source."""

    def test_paid_freeze_matches_sources_and_manifest(self) -> None:
        frozen = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("crewai-two-role-r6-multitask-01", frozen["batch"])
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


if __name__ == "__main__":
    unittest.main()
