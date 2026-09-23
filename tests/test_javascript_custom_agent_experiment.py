"""Tests for the zero-dependency JavaScript custom Agent integration."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_javascript_custom_agent_experiment import (
    DEFAULT_CLIENT,
    DEFAULT_SCRIPT,
    main,
)


class JavaScriptCustomAgentExperimentTest(unittest.TestCase):
    def test_client_exposes_complete_lifecycle_without_dependencies(self):
        source = DEFAULT_CLIENT.read_text(encoding="utf-8")
        for method in (
            "beforeModel",
            "afterModel",
            "afterTool",
            "onError",
            "finalize",
            "getState",
            "restoreState",
            "deleteSession",
        ):
            self.assertIn(f"async {method}(", source)
        package = json.loads(
            (DEFAULT_CLIENT.parent / "package.json").read_text(encoding="utf-8")
        )
        self.assertEqual({}, package.get("dependencies", {}))
        self.assertEqual(">=18", package["engines"]["node"])

    def test_runner_model_request_does_not_include_expected_terms(self):
        source = DEFAULT_SCRIPT.read_text(encoding="utf-8")
        model_body = source.split("body: JSON.stringify({", 1)[1].split("}),", 1)[0]
        self.assertIn("messages", model_body)
        self.assertNotIn("expected", model_body)

    def test_plan_enforces_api_confirmation_budget_without_reading_key(self):
        self.assertEqual(0, main(["--mode", "api", "--plan"]))
        with self.assertRaisesRegex(SystemExit, "最坏情况 API 请求数 54"):
            main(
                [
                    "--mode",
                    "api",
                    "--repeats",
                    "3",
                    "--max-api-requests",
                    "18",
                    "--plan",
                ]
            )

    @unittest.skipUnless(shutil.which("node") or shutil.which("node.exe"), "Node.js is optional")
    def test_real_node_mock_agent_completes_paired_sidecar_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            code = main(
                [
                    "--mode",
                    "mock",
                    "--repeats",
                    "1",
                    "--out",
                    temporary,
                    "--experiment-id",
                    "mock-integration",
                ]
            )
            self.assertEqual(0, code)
            report = json.loads(
                (Path(temporary) / "mock-integration" / "report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(6, report["summary"]["case_count"])
            self.assertEqual(3, report["summary"]["paired_n"])
            self.assertTrue(report["summary"]["all_success"])
            self.assertTrue(report["summary"]["all_finalized"])
            self.assertTrue(report["lifecycle_probe"]["success"])
            self.assertTrue(report["lifecycle_probe"]["capabilities_ok"])
            self.assertTrue(report["lifecycle_probe"]["sessions_deleted"])


if __name__ == "__main__":
    unittest.main()
