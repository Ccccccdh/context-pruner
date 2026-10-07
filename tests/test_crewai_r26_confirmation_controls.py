"""Offline negative controls for r26 confirmation audit."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from experiments.audits.audit_crewai_r26_coverage_confirm import (
    outcomes, row_errors, trace_from_capture,
)

ROOT = Path(__file__).resolve().parents[1]


class R26Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = ROOT / "runs/stage5-crewai/crewai-r25-coverage-guard-dev-01/results.jsonl"
        cls.rows = [json.loads(line) for line in data.read_text(encoding="utf-8").splitlines()]
        tasks = json.loads((ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json")
                           .read_text(encoding="utf-8"))
        cls.task = next(t for t in tasks if t["task_id"] == "ledger_lock_attestation")

    def test_real_r25_capture_reconstructs_coverage(self):
        row = next(r for r in self.rows if r["method"] == "pruner_v1")
        self.assertEqual(trace_from_capture(row, "Evidence investigator"), row["first_role_trace"])
        self.assertEqual(row_errors(row, self.task, mode="api"), [])

    def test_forged_runner_trace_cannot_supply_missing_source(self):
        row = copy.deepcopy(next(r for r in self.rows if r["method"] == "pruner_v1"))
        first = next(c for c in row["full_attempt_capture"]
                     if c.get("host_executed_tool") == "run_attestation_check"
                     and "Evidence investigator" in c["full_input_messages"][0]["content"])
        first["host_executed_tool"] = None
        self.assertTrue(any("trace differs from capture" in error for error in
                            row_errors(row, self.task, mode="api")))
        self.assertTrue(any("required source missing" in error for error in
                            row_errors(row, self.task, mode="api")))

    def test_missing_native_summary_capture_is_rejected(self):
        row = copy.deepcopy(next(r for r in self.rows if r["method"] == "native_summary"))
        self.assertTrue(row["native_summary_capture"])
        row["native_summary_capture"] = []
        self.assertTrue(any("per-call capture incomplete" in error for error in
                            row_errors(row, self.task, mode="api")))

    def test_positive_cost_does_not_override_quality_failure(self):
        rows_path = ROOT / "runs/stage5-crewai/crewai-r23-orderfree-3arm-cumulative-01/results.jsonl"
        rows = [json.loads(x) for x in rows_path.read_text(encoding="utf-8").splitlines()
                if json.loads(x)["task_id"] == "ledger_lock_attestation"]
        result = outcomes(rows, [self.task])
        self.assertFalse(result["quality_pass"])


if __name__ == "__main__":
    unittest.main()
