"""Offline experiment regression tests for the Stage-5 adapter validation."""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_openai_agents_mock_experiment import main


class Stage5OpenAIAgentsMockTest(unittest.TestCase):
    def test_paired_mock_preserves_quality_and_reports_positive_savings(self):
        with tempfile.TemporaryDirectory() as directory:
            code = main(
                [
                    "--repeats",
                    "3",
                    "--out",
                    directory,
                    "--experiment-id",
                    "test-run",
                ]
            )
            root = Path(directory) / "test-run"
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            samples = (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(0, code)
            self.assertEqual(18, len(samples))
            for method in report["methods"]:
                self.assertEqual(1.0, method["success_rate"])
                self.assertEqual(1.0, method["protected_integrity_rate"])
                self.assertEqual(1.0, method["pairing_integrity_rate"])
                self.assertEqual(0, method["restore_failure_count"])
            self.assertEqual(9, report["paired"]["paired_n"])
            self.assertGreater(
                report["paired"]["input_savings_rate_vs_baseline"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
