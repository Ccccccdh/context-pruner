"""Static-contract and captured-coverage controls for r28."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from experiments.audits.audit_crewai_r28_variable_confirm import row_errors, trace_from_capture
from integrations.crewai.r28_task_contract_gate import inspect

ROOT = Path(__file__).resolve().parents[1]


class R28Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "runs/stage5-crewai/crewai-r27-variable-source-dev-01/results.jsonl"
        cls.rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        tasks = json.loads((ROOT / "tasks/stage5_autogen/natural_tasks_r27_variable.json")
                           .read_text(encoding="utf-8"))
        cls.task = next(t for t in tasks if t["task_id"] == "cluster_failover_hold")

    def test_static_gate_rejects_r27_collision_and_accepts_fresh_tasks(self):
        old = inspect(ROOT / "tasks/stage5_autogen/natural_tasks_r27_variable.json")
        new = inspect(ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json")
        self.assertFalse(old["passed"])
        self.assertIn("shipment_customs_dispatch", old["errors"])
        self.assertTrue(new["passed"])

    def test_real_five_source_capture_reconstructs(self):
        row = next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                   and r["method"] == "pruner_v1" and r["strict_success"])
        self.assertEqual(trace_from_capture(row, "Evidence investigator"), row["first_role_trace"])
        self.assertEqual(row_errors(row, self.task, mode="api"), [])

    def test_forged_five_source_trace_is_rejected(self):
        row = copy.deepcopy(next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                                 and r["method"] == "pruner_v1"))
        first = next(c for c in row["full_attempt_capture"]
                     if c.get("host_executed_tool") == "read_traffic_capacity"
                     and "Evidence investigator" in c["full_input_messages"][0]["content"])
        first["host_executed_tool"] = None
        errors = row_errors(row, self.task, mode="api")
        self.assertTrue(any("trace differs from capture" in e for e in errors))
        self.assertTrue(any("required source missing" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
