from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_csharp_custom_agent_experiment import (
    CLIENT_PROJECT,
    CLIENT_SOURCE,
    EXAMPLE_SOURCE,
    _dotnet_tool,
    main,
)


class CSharpCustomAgentExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.dotnet = _dotnet_tool(None)
        except RuntimeError as error:
            raise unittest.SkipTest(str(error))

    def test_client_is_dependency_free_and_exposes_full_lifecycle(self) -> None:
        client = CLIENT_SOURCE.read_text(encoding="utf-8")
        project = CLIENT_PROJECT.read_text(encoding="utf-8")
        for marker in (
            "BeforeModelAsync(", "AfterModelAsync(", "AfterToolAsync(", "OnErrorAsync(",
            "FinalizeAsync(", "GetStateAsync(", "RestoreStateAsync(", "DeleteSessionAsync(",
        ):
            self.assertIn(marker, client)
        self.assertIn("HttpClient", client)
        self.assertIn("System.Text.Json", client)
        self.assertNotIn("PackageReference", project)
        self.assertNotIn("Newtonsoft", client + project)

    def test_model_request_does_not_serialize_expected_terms(self) -> None:
        source = EXAMPLE_SOURCE.read_text(encoding="utf-8")
        model_body = source.split("private static async Task<ModelResult> CallModelAsync", 1)[1].split(
            "private static async Task<JsonObject> RunLifecycleProbeAsync", 1
        )[0]
        self.assertIn('["messages"] = messages.DeepClone()', model_body)
        self.assertNotIn("Expected", model_body)

    def test_real_compiled_csharp_agent_completes_http_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = main([
                "--mode", "mock",
                "--dotnet", str(self.dotnet),
                "--repeats", "1",
                "--max-api-requests", "1",
                "--out", str(root),
                "--experiment-id", "csharp-test",
            ])
            self.assertEqual(0, code)
            report = json.loads(
                (root / "csharp-test" / "report.json").read_text(encoding="utf-8")
            )
            self.assertEqual(6, report["summary"]["case_count"])
            self.assertTrue(report["summary"]["all_success"])
            self.assertTrue(report["summary"]["all_finalized"])
            self.assertTrue(report["lifecycle_probe"]["success"])
            self.assertTrue(report["lifecycle_probe"]["tool_fact_preserved"])
            self.assertTrue(report["lifecycle_probe"]["sessions_deleted"])

    def test_api_request_cap_is_checked_before_key_lookup(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main([
                "--mode", "api",
                "--dotnet", str(self.dotnet),
                "--repeats", "2",
                "--max-api-retries", "2",
                "--max-api-requests", "1",
                "--confirm-send-synthetic-data",
            ])
        self.assertIn("worst-case API requests", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
