"""Tests for the offline Context-Pruner command-line contract."""

import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from context_pruner.cli import main


class ContextPrunerCliTest(unittest.TestCase):
    def test_adapters_command_emits_machine_readable_capability_catalog(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["adapters", "--json", "--include-planned"])

        data = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual(9, len(data))
        implemented = {item["adapter_id"] for item in data if item["implemented"]}
        self.assertIn("generic_loop", implemented)
        self.assertIn("langgraph", implemented)
        self.assertIn("openai_agents", implemented)
        self.assertIn("autogen_agentchat", implemented)
        self.assertIn("autogen_team", implemented)
        self.assertIn("explicit_history", implemented)
        self.assertIn("http_sidecar", implemented)
        self.assertIn("microsoft_agent_framework", implemented)
        self.assertIn("crewai", implemented)

    def test_assess_command_reports_explicit_only_for_exported_history(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["assess", "--exported-history", "--json"])

        data = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("explicit", data["integration_level"])
        self.assertFalse(data["automatic_context_control"])

    def test_compress_writes_output_and_resumable_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"
            output_path = root / "output.json"
            state_path = root / "state.json"
            messages = [{"role": "user", "content": "Remember Aurora-17."}]
            for index in range(8):
                messages.append(
                    {
                        "role": "assistant",
                        "content": f"analysis {index} " + "background " * 30,
                    }
                )
            input_path.write_text(
                json.dumps({"messages": messages, "task_state": "Aurora-17"}),
                encoding="utf-8",
            )

            code = main(
                [
                    "compress",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                    "--state-out",
                    str(state_path),
                    "--soft",
                    "220",
                    "--hard",
                    "320",
                    "--target",
                    "180",
                    "--reserved-tokens",
                    "40",
                ]
            )

            output = json.loads(output_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(0, code)
            self.assertIn("messages", output)
            self.assertIn("metrics", output)
            self.assertEqual(1, state["schema_version"])
            self.assertEqual(40, output["metrics"]["reserved_tokens_total"])

    def test_serve_builds_local_sidecar_config_without_starting_in_test(self):
        with patch.dict(
            "os.environ", {"TEST_CONTEXT_PRUNER_TOKEN": "local-secret"}
        ), patch("context_pruner.sidecar.serve_sidecar") as serve:
            code = main(
                [
                    "serve",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "0",
                    "--auth-token-env",
                    "TEST_CONTEXT_PRUNER_TOKEN",
                    "--soft",
                    "500",
                    "--hard",
                    "700",
                    "--target",
                    "400",
                ]
            )

        self.assertEqual(0, code)
        config = serve.call_args.args[0]
        self.assertEqual("local-secret", config.auth_token)
        self.assertEqual(700, config.default_plugin.budget.hard_limit_tokens)


if __name__ == "__main__":
    unittest.main()
