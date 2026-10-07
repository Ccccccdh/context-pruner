"""Negative controls from the actual r23 defect, without a provider call."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from experiments.audits.audit_crewai_r24_coverage_pilot import (
    quality_and_cost, row_ledger_errors,
)

ROOT = Path(__file__).resolve().parents[1]


class R24AuditControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = (ROOT / "runs/stage5-crewai/crewai-r23-orderfree-3arm-cumulative-01"
                / "results.jsonl")
        cls.rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        task_file = ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json"
        cls.task = next(task for task in json.loads(task_file.read_text(encoding="utf-8"))
                        if task["task_id"] == "ledger_lock_attestation")

    def test_positive_cost_cannot_override_failed_quality(self):
        rows = [r for r in self.rows if r["task_id"] == "ledger_lock_attestation"]
        outcome = quality_and_cost(rows, self.task)
        self.assertTrue(outcome["cost_pass"])
        self.assertFalse(outcome["quality_pass"])
        self.assertEqual(outcome["coverage"]["pruner_v1"], 0)

    def test_native_summary_missing_agent_capture_is_rejected(self):
        row = next(r for r in self.rows if r["method"] == "native_summary")
        errors = row_ledger_errors(row)
        self.assertTrue(any("per-call capture incomplete" in error for error in errors))
        self.assertTrue(any("complete token ledger differs" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
