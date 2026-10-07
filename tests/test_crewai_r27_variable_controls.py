"""Offline controls for the prospective variable-source audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from experiments.audits.audit_crewai_r27_variable_dev import row_errors, trace_from_capture

ROOT = Path(__file__).resolve().parents[1]


class R27Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "runs/stage5-crewai/crewai-r26-coverage-guard-confirm-01/results.jsonl"
        cls.rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        tasks = json.loads((ROOT / "tasks/stage5_autogen/natural_tasks_r26_confirmation.json")
                           .read_text(encoding="utf-8"))
        cls.task = next(t for t in tasks if t["task_id"] == "certificate_rotation_release")

    def test_real_captured_coverage_and_old_fixed_K_is_rejected(self):
        row = next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                   and r["method"] == "pruner_v1")
        self.assertEqual(trace_from_capture(row, "Evidence investigator"), row["first_role_trace"])
        self.assertTrue(any("task-scaled K differs" in e for e in
                            row_errors(row, self.task, mode="api")))

    def test_missing_source_cannot_be_supplied_by_runner_field(self):
        row = copy.deepcopy(next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                                 and r["method"] == "pruner_v1"))
        first = next(c for c in row["full_attempt_capture"]
                     if c.get("host_executed_tool") == "read_chain_probe"
                     and "Evidence investigator" in c["full_input_messages"][0]["content"])
        first["host_executed_tool"] = None
        errors = row_errors(row, self.task, mode="api")
        self.assertTrue(any("trace differs from capture" in e for e in errors))
        self.assertTrue(any("required source missing" in e for e in errors))

    def test_missing_native_summary_capture_is_rejected(self):
        row = copy.deepcopy(next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                                 and r["method"] == "native_summary"))
        self.assertTrue(row["native_summary_capture"])
        row["native_summary_capture"] = []
        self.assertTrue(any("per-call capture incomplete" in e for e in
                            row_errors(row, self.task, mode="api")))


if __name__ == "__main__":
    unittest.main()
