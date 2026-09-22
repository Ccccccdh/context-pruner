"""Tests for the preregistered cross-domain AutoGen team task set."""

import hashlib
import json
import unittest
from pathlib import Path

from experiments.runners.run_autogen_team_experiment import load_tasks


TASKS = Path("tasks/stage5_autogen_team/external_validity_v108_tasks.json")
PROTOCOL = Path("tasks/stage5_autogen_team/external_validity_v108_protocol.json")
REPAIRED_PROTOCOL = Path(
    "tasks/stage5_autogen_team/external_validity_v109_protocol.json"
)
ROLE_FIX_PROTOCOL = Path(
    "tasks/stage5_autogen_team/external_validity_v110_protocol.json"
)


class AutoGenTeamExternalValidityTest(unittest.TestCase):
    def test_six_unique_domains_and_derived_decisions(self):
        tasks = load_tasks(TASKS)

        self.assertEqual(6, len(tasks))
        self.assertEqual(6, len({task["domain"] for task in tasks}))
        for task in tasks:
            decision = task["expected_final_terms"][3]
            self.assertNotIn(decision, task["planner_task"])
            self.assertNotIn("expected", task["planner_task"].lower())

    def test_protocol_freezes_task_hash_and_request_budget(self):
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        task_hash = hashlib.sha256(TASKS.read_bytes()).hexdigest()

        self.assertTrue(protocol["preregistered"])
        self.assertEqual(task_hash, protocol["task_file_sha256"])
        self.assertEqual(18, protocol["expected_pairs"])
        self.assertEqual(72, protocol["minimum_api_requests"])
        self.assertEqual(84, protocol["maximum_api_requests"])
        self.assertEqual("disabled", protocol["thinking_mode"])

    def test_repaired_protocol_keeps_same_tasks_and_declares_scope(self):
        original = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        repaired = json.loads(REPAIRED_PROTOCOL.read_text(encoding="utf-8"))

        self.assertEqual(original["task_file_sha256"], repaired["task_file_sha256"])
        self.assertEqual(original["task_ids"], repaired["task_ids"])
        self.assertEqual(original["expected_pairs"], repaired["expected_pairs"])
        self.assertEqual(original["protocol_id"], repaired["supersedes_protocol"])
        self.assertEqual(3, len(repaired["repair_scope"]))

    def test_role_fix_protocol_freezes_implementation_version(self):
        repaired = json.loads(REPAIRED_PROTOCOL.read_text(encoding="utf-8"))
        role_fix = json.loads(ROLE_FIX_PROTOCOL.read_text(encoding="utf-8"))

        self.assertEqual("0.10.3", role_fix["context_pruner_version"])
        self.assertEqual(repaired["protocol_id"], role_fix["supersedes_protocol"])
        self.assertEqual(repaired["task_file_sha256"], role_fix["task_file_sha256"])


if __name__ == "__main__":
    unittest.main()
