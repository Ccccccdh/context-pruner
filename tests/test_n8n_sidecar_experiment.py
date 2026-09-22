"""Tests for the real n8n Sidecar workflow runner helpers."""

import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from experiments.runners.run_n8n_sidecar_experiment import (
    DEFAULT_WORKFLOW,
    VALIDATION_NODE,
    _render_workflow,
    _launcher,
    extract_validation_rows,
    summarize_rows,
)


class N8nSidecarExperimentTest(unittest.TestCase):
    def test_committed_workflow_has_real_n8n_http_boundary(self):
        workflow = json.loads(DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
        node_types = {node["type"] for node in workflow["nodes"]}
        self.assertIn("n8n-nodes-base.manualTrigger", node_types)
        self.assertIn("n8n-nodes-base.code", node_types)
        self.assertIn("n8n-nodes-base.httpRequest", node_types)
        self.assertIn("__SIDECAR_URL__", DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
        self.assertIn("__SIDECAR_TOKEN__", DEFAULT_WORKFLOW.read_text(encoding="utf-8"))

        http_nodes = [
            node for node in workflow["nodes"]
            if node["type"] == "n8n-nodes-base.httpRequest"
        ]
        self.assertEqual(2, len(http_nodes))
        for node in http_nodes:
            parameters = node["parameters"]
            self.assertEqual("json", parameters["contentType"])
            self.assertEqual("json", parameters["specifyBody"])
            self.assertIn("jsonBody", parameters)
            self.assertNotIn("body", parameters)

    def test_render_replaces_only_runtime_sidecar_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "workflow.json"
            digest = _render_workflow(
                DEFAULT_WORKFLOW,
                target,
                base_url="http://127.0.0.1:54321",
                token="local-test-token",
                collector_url="http://127.0.0.1:54322",
                collector_token="collector-test-token",
            )
            rendered = target.read_text(encoding="utf-8")
            self.assertEqual(64, len(digest))
            self.assertNotIn("__SIDECAR_", rendered)
            self.assertIn("http://127.0.0.1:54321", rendered)
            self.assertIn("Bearer local-test-token", rendered)
            self.assertIn("http://127.0.0.1:54322", rendered)
            self.assertIn("Bearer collector-test-token", rendered)

    def test_fixed_launcher_requires_both_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            node = Path(temporary) / "node.exe"
            script = Path(temporary) / "n8n"
            node.touch()
            script.touch()
            args = Namespace(
                node_executable=node,
                n8n_cli_script=script,
                n8n_version="2.6.4",
            )
            self.assertEqual([str(node.resolve()), str(script.resolve())], _launcher(args))
            args.n8n_cli_script = None
            with self.assertRaisesRegex(RuntimeError, "必须同时提供"):
                _launcher(args)

    def test_extract_and_summarize_paired_results(self):
        rows = []
        for task_id in ("incident", "release", "migration"):
            rows.extend(
                [
                    {
                        "task_id": task_id,
                        "method": "none",
                        "success": True,
                        "full_context_tokens": 1000,
                        "served_context_tokens": 1000,
                        "model_input_budget_violation_count": 0,
                    },
                    {
                        "task_id": task_id,
                        "method": "pruner_v1",
                        "success": True,
                        "full_context_tokens": 1000,
                        "served_context_tokens": 600,
                        "model_input_budget_violation_count": 0,
                    },
                ]
            )
        execution = {
            "data": {
                "resultData": {
                    "runData": {
                        VALIDATION_NODE: [
                            {"data": {"main": [[{"json": row} for row in rows]]}}
                        ]
                    }
                }
            }
        }

        extracted = extract_validation_rows(execution)
        summary = summarize_rows(extracted)

        self.assertEqual(6, summary["case_count"])
        self.assertEqual(3, summary["paired_n"])
        self.assertEqual(3, summary["paired_token_n"])
        self.assertTrue(summary["all_success"])
        self.assertEqual(0, summary["transport_error_count"])
        self.assertAlmostEqual(0.4, summary["paired_input_savings_rate_mean"])
        self.assertEqual(1.0, summary["paired_input_savings_win_rate"])


if __name__ == "__main__":
    unittest.main()
