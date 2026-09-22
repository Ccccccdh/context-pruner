"""Regression test for the real-Runner paired offline experiment."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(
    importlib.util.find_spec("agents") is not None,
    "openai-agents optional dependency is not installed",
)
class Stage5RunnerExperimentTest(unittest.TestCase):
    def test_real_runner_pairing_covers_all_scenarios_and_resumes(self):
        from experiments.runners.run_openai_agents_runner_experiment import main

        with tempfile.TemporaryDirectory() as directory:
            args = [
                "--repeats",
                "1",
                "--out",
                directory,
                "--experiment-id",
                "runner-test",
            ]
            self.assertEqual(0, main(args))
            root = Path(directory) / "runner-test"
            report = json.loads((root / "report.json").read_text(encoding="utf-8"))
            samples = (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()

            self.assertEqual(8, len(samples))
            self.assertEqual(4, report["paired"]["paired_n"])
            self.assertGreater(report["paired"]["input_savings_rate_vs_baseline"], 0)
            for method in report["methods"]:
                self.assertEqual(1.0, method["success_rate"])
                self.assertEqual(1.0, method["structure_safety_rate"])
                self.assertEqual(0, method["restore_failure_count"])
                self.assertEqual(0, method["unmatched_call_count"])

            self.assertEqual(0, main(args + ["--resume"]))
            resumed = (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(8, len(resumed))


if __name__ == "__main__":
    unittest.main()
