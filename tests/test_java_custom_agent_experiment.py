from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_java_custom_agent_experiment import (
    SOURCES,
    _java_tools,
    main,
)


class JavaCustomAgentExperimentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.javac, cls.java = _java_tools(None)
        except RuntimeError as error:
            raise unittest.SkipTest(str(error))

    def test_client_is_dependency_free_and_exposes_full_lifecycle(self) -> None:
        client = SOURCES[1].read_text(encoding="utf-8")
        codec = SOURCES[0].read_text(encoding="utf-8")
        combined = client + codec
        for marker in (
            "beforeModel(", "afterModel(", "afterTool(", "onError(",
            "finalizeSession(", "getState(", "restoreState(", "deleteSession(",
        ):
            self.assertIn(marker, client)
        self.assertIn("HttpClient", client)
        self.assertIn("parseObject", codec)
        self.assertNotIn("com.fasterxml", combined)
        self.assertNotIn("com.google.gson", combined)

    def test_model_request_does_not_serialize_expected_terms(self) -> None:
        source = SOURCES[2].read_text(encoding="utf-8")
        model_body = source.split("private static ModelResult callModel", 1)[1].split(
            "private static Map<String, Object> runLifecycleProbe", 1
        )[0]
        self.assertIn('"messages", messages', model_body)
        self.assertNotIn('"expected"', model_body)

    def test_real_compiled_java_agent_completes_http_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = main([
                "--mode", "mock",
                "--java-home", str(self.javac.parent.parent),
                "--repeats", "1",
                "--max-api-requests", "1",
                "--out", str(root),
                "--experiment-id", "java-test",
            ])
            self.assertEqual(0, code)
            report = json.loads(
                (root / "java-test" / "report.json").read_text(encoding="utf-8")
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
                "--java-home", str(self.javac.parent.parent),
                "--repeats", "2",
                "--max-api-retries", "2",
                "--max-api-requests", "1",
                "--confirm-send-synthetic-data",
            ])
        self.assertIn("worst-case API requests", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
