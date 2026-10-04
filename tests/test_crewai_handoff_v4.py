"""Zero-API r4 runner and independent auditor regression tests."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


_STORAGE = Path(tempfile.gettempdir()) / "context-pruner-crewai-r4-tests"
_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LOCALAPPDATA", str(_STORAGE))
os.environ.setdefault("CREWAI_STORAGE_DIR", str(_STORAGE))

from experiments.audits import audit_crewai_handoff_v4 as independent
from experiments.runners import run_crewai_handoff_v4 as pilot


class CrewAIHandoffV4Test(unittest.TestCase):
    def _row(self) -> dict:
        task = next(task for task in pilot.base.load_tasks(pilot.TASK_FILE)
                    if task["task_id"] == "incident_triage")
        args = SimpleNamespace(
            model="mock", base_url="", max_output_tokens=512,
            fixed_reserved_tokens=300, max_summary_tokens=1024,
            max_summary_calls=4,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            return pilot.run_case(
                task, "pruner_v1", 2, "mock", None,
                pilot.base.RequestBudget(24), args,
                pilot.ContextBudget(3084, 7712, 2313),
            )

    def test_missing_fact_uses_one_metered_recovery_without_second_tool_call(self) -> None:
        row = self._row()
        self.assertEqual("HANDOFF", row["role_outputs"][0])
        self.assertFalse(row["handoff_first_pass"]["facts_present"])
        self.assertEqual(1, row["recovery_invocations"])
        self.assertEqual(0, row["recovery_api_attempts"])
        self.assertTrue(row["handoff_recovery"]["facts_present"])
        self.assertIn("degraded 3.8", row["handoff_for_decider"])
        self.assertEqual([["get_service_health"], ["get_recent_deployment"]],
                         row["role_tool_traces"])
        self.assertTrue(row["success"])

    def test_independent_audit_detects_raw_handoff_tampering(self) -> None:
        row = self._row()
        with tempfile.TemporaryDirectory() as location:
            directory = Path(location)
            manifest = {
                "protocol": "crewai-two-role-r4-handoff-quality",
                "tasks": ["incident_triage"], "methods": ["pruner_v1"],
                "repeats": 3, "mode": "mock", "max_api_requests": 24,
                "task_sha256": hashlib.sha256(independent.TASK_FILE.read_bytes()).hexdigest(),
            }
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (directory / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            audit = independent.audit(directory)
            self.assertEqual([], audit["errors"])
            self.assertFalse(audit["complete"])
            row["handoff_first_pass"]["facts_present"] = True
            (directory / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            self.assertTrue(any("first-pass quality mismatch" in error
                                for error in independent.audit(directory)["errors"]))

    def test_freeze_checks_endpoint_and_all_source_hashes(self) -> None:
        row = self._row()
        with tempfile.TemporaryDirectory() as location:
            directory = Path(location)
            source_hashes = {
                path: hashlib.sha256((independent.ROOT / path).read_bytes()).hexdigest()
                for path in independent.FROZEN_PATHS
            }
            manifest = {
                "protocol": "crewai-two-role-r4-handoff-quality",
                "tasks": ["incident_triage"], "methods": ["pruner_v1"],
                "repeats": 3, "model": "deepseek-v4-flash", "mode": "mock",
                "base_url": "https://api.deepseek.com", "budget": {"provider_tokens": {"soft": 1200}},
                "limits": {"max_output_tokens": 512}, "max_api_requests": 60,
                "task_sha256": source_hashes["tasks/stage5_autogen/natural_tasks.json"],
                "runner_sha256": source_hashes["experiments/runners/run_crewai_handoff_v4.py"],
            }
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (directory / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
            frozen_manifest = {**manifest, "mode": "api"}
            freeze = {"batch": "future-paid-pilot", "samples": 3,
                      "manifest": frozen_manifest, "source_sha256": source_hashes}
            freeze_path = directory / "freeze.json"
            freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
            clean = independent.audit(directory, freeze_path, dry_run_mock=True)
            self.assertTrue(clean["freeze_checked"])
            self.assertEqual([], clean["errors"])
            self.assertFalse(clean["complete"])
            manifest["base_url"] = "https://wrong.example"
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            changed = independent.audit(directory, freeze_path, dry_run_mock=True)
            self.assertIn("freeze manifest mismatch: base_url", changed["errors"])
            manifest["base_url"] = frozen_manifest["base_url"]
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            freeze["source_sha256"]["experiments/audits/audit_crewai_handoff_v4.py"] = "0" * 64
            freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
            changed_source = independent.audit(directory, freeze_path, dry_run_mock=True)
            self.assertIn("freeze source changed: experiments/audits/audit_crewai_handoff_v4.py",
                          changed_source["errors"])


if __name__ == "__main__":
    unittest.main()
