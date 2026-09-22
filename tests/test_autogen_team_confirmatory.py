"""Tests for preregistered AutoGen team confirmation gates."""

import json
import unittest
from pathlib import Path

from experiments.audits.validate_autogen_team_confirmatory import evaluate_summary


PROTOCOL = Path("tasks/stage5_autogen_team/confirmatory_v107_protocol.json")


class AutoGenTeamConfirmatoryTest(unittest.TestCase):
    def test_protocol_freezes_hash_sample_size_and_non_thinking_policy(self):
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))

        self.assertTrue(protocol["preregistered"])
        self.assertEqual(15, protocol["expected_pairs"])
        self.assertEqual(72, protocol["maximum_api_requests"])
        self.assertEqual("disabled", protocol["thinking_mode"])

    def test_gate_evaluation_rejects_incomplete_pairs(self):
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        method = {
            "success_rate": 1.0,
            "model_input_privacy_rate": 1.0,
            "handoff_preserved_rate": 1.0,
            "handoff_target_only_rate": 1.0,
            "state_roundtrip_ok_rate": 1.0,
            "output_contract_correct_rate": 1.0,
            "hard_budget_violations": 0,
        }
        summary = {
            "methods": {"none": dict(method), "pruner_v1": dict(method)},
            "paired": {
                "n": 14,
                "incomplete_n": 1,
                "input_savings_ci_low": 0.6,
                "total_token_savings_ci_low": 0.6,
                "positive_savings_rate": 1.0,
                "output_token_change_rate_mean": 0.0,
            },
        }

        checks = evaluate_summary(summary, protocol)
        failed = {check["name"] for check in checks if not check["passed"]}

        self.assertIn("paired_n", failed)
        self.assertIn("incomplete_pairs", failed)


if __name__ == "__main__":
    unittest.main()
