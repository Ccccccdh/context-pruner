"""Tests for the n8n full Sidecar lifecycle workflow."""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_n8n_sidecar_lifecycle_experiment import (
    DEFAULT_WORKFLOW,
    _render_lifecycle_workflow,
    main,
)


class N8nSidecarLifecycleExperimentTest(unittest.TestCase):
    def test_workflow_covers_state_tool_error_and_finalize_boundaries(self):
        workflow = json.loads(DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
        names = {node["name"] for node in workflow["nodes"]}
        self.assertTrue(
            {
                "Before Model",
                "After Tool",
                "After Model",
                "Export State",
                "Restore State",
                "Recover On Error",
                "Finalize Restored Session",
                "Validate Lifecycle",
            }.issubset(names)
        )
        http_nodes = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.httpRequest"
        ]
        for node in http_nodes:
            parameters = node["parameters"]
            if parameters.get("sendBody"):
                self.assertEqual("json", parameters["contentType"])
                self.assertEqual("json", parameters["specifyBody"])

    def test_render_replaces_ephemeral_runtime_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "workflow.json"
            digest = _render_lifecycle_workflow(
                DEFAULT_WORKFLOW,
                target,
                sidecar_url="http://127.0.0.1:5101",
                sidecar_token="sidecar-secret",
                collector_url="http://127.0.0.1:5102",
                collector_token="collector-secret",
            )
            rendered = target.read_text(encoding="utf-8")
            self.assertEqual(64, len(digest))
            self.assertNotIn("__SIDECAR_", rendered)
            self.assertNotIn("__COLLECTOR_", rendered)
            self.assertIn("Bearer sidecar-secret", rendered)
            json.loads(rendered)

    def test_plan_has_no_model_api_requests(self):
        with tempfile.TemporaryDirectory() as temporary:
            node = Path(temporary) / "node.exe"
            script = Path(temporary) / "n8n"
            node.touch()
            script.touch()
            self.assertEqual(
                0,
                main(
                    [
                        "--node-executable",
                        str(node),
                        "--n8n-cli-script",
                        str(script),
                        "--plan",
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()
