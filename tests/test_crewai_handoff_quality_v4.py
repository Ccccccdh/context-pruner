"""Zero-API acceptance gate for the prospective CrewAI r4 handoff contract."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from experiments.runners.crewai_handoff_quality_v4 import assess_handoff


ROOT = Path(__file__).resolve().parents[1]
R3 = ROOT / "runs/stage5-crewai/crewai-two-role-fixed-r3-dev-01/results.jsonl"


class CrewAIHandoffQualityV4Test(unittest.TestCase):
    def test_format_variants_preserve_facts(self) -> None:
        for value, first_pass in (
            ("HANDOFF: Atlas-2.4 passed=184 failed=0", False),
            ("HANDOFF：Atlas-2.4 passed=184 failed=0", False),
            ("HANDOFF | Atlas-2.4 passed=184 failed=0", True),
            ("Atlas-2.4 passed=184 failed=0", False),
        ):
            with self.subTest(value=value):
                quality = assess_handoff("release_readiness", value)
                self.assertTrue(quality.facts_present)
                self.assertEqual(first_pass, quality.first_pass_valid)
                self.assertEqual("HANDOFF Atlas-2.4 passed=184 failed=0", quality.canonical)
                self.assertFalse(quality.needs_recovery)

    def test_missing_fact_cannot_be_canonicalized(self) -> None:
        quality = assess_handoff("incident_triage", "HANDOFF")
        self.assertFalse(quality.facts_present)
        self.assertIsNone(quality.canonical)
        self.assertTrue(quality.needs_recovery)

    def test_multiline_needs_recovery(self) -> None:
        quality = assess_handoff("customer_migration", "HANDOFF eu-west\n02:00 UTC")
        self.assertTrue(quality.facts_present)
        self.assertIsNone(quality.canonical)
        self.assertTrue(quality.needs_recovery)

    def test_r3_replay_is_read_only_and_detects_substantive_failure(self) -> None:
        before = R3.read_bytes()
        rows = [json.loads(line) for line in before.decode("utf-8").splitlines() if line]
        self.assertEqual(27, len(rows))
        fact_failures = []
        format_only = []
        for row in rows:
            assessment = assess_handoff(row["task_id"], row["role_outputs"][0])
            key = (row["task_id"], row["repeat"], row["method"])
            if not assessment.facts_present:
                fact_failures.append(key)
                self.assertIsNone(assessment.canonical)
            elif not assessment.first_pass_valid:
                format_only.append(key)
                self.assertIsNotNone(assessment.canonical)
        self.assertEqual([("incident_triage", 2, "pruner_v1")], fact_failures)
        self.assertGreater(len(format_only), 0)
        self.assertEqual(before, R3.read_bytes())


if __name__ == "__main__":
    unittest.main()
