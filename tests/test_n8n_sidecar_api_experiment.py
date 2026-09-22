"""Tests for the real n8n + Sidecar + DeepSeek experiment boundary."""

import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_n8n_sidecar_api_experiment import (
    DEFAULT_WORKFLOW,
    _render_api_workflow,
    main,
    summarize_api_rows,
)


class N8nSidecarApiExperimentTest(unittest.TestCase):
    def test_workflow_calls_sidecar_model_and_finalize_with_json_bodies(self):
        workflow = json.loads(DEFAULT_WORKFLOW.read_text(encoding="utf-8"))
        by_name = {node["name"]: node for node in workflow["nodes"]}
        self.assertIn("Context-Pruner Before Model", by_name)
        self.assertIn("DeepSeek Chat Completion", by_name)
        self.assertIn("Context-Pruner After Model", by_name)
        self.assertIn("Context-Pruner Finalize", by_name)
        model_body = by_name["DeepSeek Chat Completion"]["parameters"]["jsonBody"]
        self.assertIn("$json.messages", model_body)
        self.assertNotIn("expected_terms", model_body)
        for node in workflow["nodes"]:
            if node["type"] == "n8n-nodes-base.httpRequest" and node["parameters"].get(
                "sendBody"
            ):
                self.assertEqual("json", node["parameters"]["contentType"])
                self.assertEqual("json", node["parameters"]["specifyBody"])

    def test_render_uses_temporary_credentials_and_replaces_all_placeholders(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "workflow.json"
            digest = _render_api_workflow(
                DEFAULT_WORKFLOW,
                target,
                sidecar_url="http://127.0.0.1:5001",
                sidecar_token="sidecar-secret",
                collector_url="http://127.0.0.1:5002",
                collector_token="collector-secret",
                model_base_url="https://api.deepseek.com",
                model_api_key="model-secret",
                model_name="deepseek-v4-flash",
                repeats=3,
            )
            rendered = target.read_text(encoding="utf-8")
            self.assertEqual(64, len(digest))
            self.assertNotIn("__", rendered)
            self.assertIn("Bearer model-secret", rendered)
            self.assertIn("const repeats = Number('3')", rendered)
            json.loads(rendered)

    def test_plan_does_not_require_api_key_and_enforces_worst_case_cap(self):
        with tempfile.TemporaryDirectory() as temporary:
            node = Path(temporary) / "node.exe"
            script = Path(temporary) / "n8n"
            node.touch()
            script.touch()
            common = [
                "--node-executable",
                str(node),
                "--n8n-cli-script",
                str(script),
            ]
            self.assertEqual(0, main([*common, "--plan"]))
            with self.assertRaisesRegex(SystemExit, "最坏情况 API 请求数 54"):
                main([*common, "--repeats", "3", "--max-api-requests", "18", "--plan"])

    def test_summarize_uses_actual_model_prompt_tokens(self):
        rows = []
        for repeat in range(2):
            for task_id in ("incident_decision", "release_gate", "migration_plan"):
                rows.extend(
                    [
                        {
                            "task_id": task_id,
                            "repeat": repeat,
                            "method": "none",
                            "success": True,
                            "finalized": True,
                            "prompt_tokens": 1000,
                            "completion_tokens": 20,
                            "served_context_tokens": 900,
                            "model_input_budget_violation_count": 1,
                        },
                        {
                            "task_id": task_id,
                            "repeat": repeat,
                            "method": "pruner_v1",
                            "success": True,
                            "finalized": True,
                            "prompt_tokens": 500,
                            "completion_tokens": 20,
                            "served_context_tokens": 450,
                            "model_input_budget_violation_count": 0,
                        },
                    ]
                )
        summary = summarize_api_rows(rows)
        self.assertEqual(12, summary["case_count"])
        self.assertEqual(6, summary["paired_n"])
        self.assertTrue(summary["all_success"])
        self.assertTrue(summary["all_finalized"])
        self.assertAlmostEqual(0.5, summary["paired_prompt_token_savings_rate_mean"])
        self.assertEqual(1.0, summary["paired_prompt_token_savings_win_rate"])
        self.assertEqual(0.0, summary["success_delta_mean"])


if __name__ == "__main__":
    unittest.main()
