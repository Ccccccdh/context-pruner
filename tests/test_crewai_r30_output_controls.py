"""Offline controls for r30 output-contract confirmation."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from experiments.audits.audit_crewai_r30_output_confirm import row_errors
from integrations.crewai.r28_task_contract_gate import inspect

ROOT = Path(__file__).resolve().parents[1]


class R30Controls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = ROOT / "runs/stage5-crewai/crewai-r29-output-contract-dev-01/results.jsonl"
        cls.rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
        tasks = json.loads((ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json")
                           .read_text(encoding="utf-8"))
        cls.task = next(t for t in tasks if t["task_id"] == "dns_cutover_wait")

    def test_new_task_contract_and_old_collision_control(self):
        self.assertTrue(inspect(ROOT / "tasks/stage5_autogen/natural_tasks_r30_fresh.json")["passed"])
        self.assertFalse(inspect(ROOT / "tasks/stage5_autogen/natural_tasks_r27_variable.json")["passed"])

    def test_real_r29_output_records_reconstruct(self):
        row = next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                   and r["method"] == "none" and r["output_contract_retries"] == 1)
        self.assertEqual(row_errors(row, self.task, mode="api"), [])

    def test_forged_served_output_is_rejected(self):
        row = copy.deepcopy(next(r for r in self.rows if r["task_id"] == self.task["task_id"]
                                 and r["method"] == "none" and r["output_contract_retries"] == 1))
        row["output_contract_records"][-1]["served"] = "RESULT task=dns_cutover_wait decision=WAIT evidence=fake"
        self.assertTrue(any("output-contract transform differs" in error for error in
                            row_errors(row, self.task, mode="api")))


if __name__ == "__main__":
    unittest.main()
