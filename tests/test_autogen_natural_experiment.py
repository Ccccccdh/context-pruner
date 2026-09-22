"""Tests for the AutoGen natural-task experiment runner."""

import asyncio
import importlib.util
import tempfile
import unittest
from pathlib import Path


AUTOGEN_INSTALLED = importlib.util.find_spec("autogen_agentchat") is not None


@unittest.skipUnless(AUTOGEN_INSTALLED, "AutoGen optional dependency is not installed")
class AutoGenNaturalExperimentTest(unittest.TestCase):
    def test_recording_client_retries_empty_api_response_within_global_budget(self):
        from autogen_core.models import ModelFamily, UserMessage
        from autogen_ext.models.replay import ReplayChatCompletionClient
        from experiments.runners.run_autogen_natural_experiment import RecordingClient, RequestBudget

        async def run_client():
            budget = RequestBudget(2)
            inner = ReplayChatCompletionClient(
                ["", "RESULT ok"],
                model_info={
                    "vision": False,
                    "function_calling": True,
                    "json_output": False,
                    "family": ModelFamily.UNKNOWN,
                    "structured_output": False,
                },
            )
            client = RecordingClient(
                inner,
                budget,
                api_mode=True,
                max_retries=1,
                retry_base_delay=0,
            )
            response = await client.create(
                [UserMessage(content="test", source="user")]
            )
            return budget, client, response

        budget, client, response = asyncio.run(run_client())

        self.assertEqual("RESULT ok", response.content)
        self.assertEqual(2, budget.used)
        self.assertEqual(1, client.retry_count)
        self.assertEqual(1, client.empty_response_count)
        self.assertEqual(1, len(client.responses))
        self.assertEqual(2, len(client.attempt_records))
        self.assertEqual("empty", client.attempt_records[0]["status"])
        self.assertEqual("success", client.attempt_records[1]["status"])
        self.assertIn("completion_tokens", client.attempt_records[0])

    def test_mock_pair_runs_real_agent_tool_loop_and_summarizes(self):
        from context_pruner import ContextBudget
        from experiments.runners.run_autogen_natural_experiment import (
            RequestBudget,
            load_tasks,
            run_case,
            summarize,
        )

        task = load_tasks(Path("tasks/stage5_autogen/natural_tasks.json"))[0]

        async def run_pair():
            rows = []
            for method in ("none", "pruner_v1"):
                rows.append(
                    await run_case(
                        task,
                        repeat=0,
                        method=method,
                        mode="mock",
                        request_budget=RequestBudget(1),
                        model_name="unused",
                        base_url="https://example.invalid",
                        api_key="",
                        budget=ContextBudget(1300, 2800, 1050),
                        fixed_reserved_tokens=300,
                        max_output_tokens=128,
                        max_api_retries=0,
                        retry_base_delay=0,
                    )
                )
            return rows

        rows = asyncio.run(run_pair())
        summary = summarize(rows, "mock")

        self.assertTrue(all(row["success"] for row in rows))
        self.assertEqual(2, rows[0]["model_calls"])
        self.assertEqual(2, rows[1]["tool_calls"])
        self.assertEqual(0, rows[1]["restore_failure_count"])
        self.assertEqual(0, rows[1]["hard_budget_violation_count"])
        self.assertEqual(1, summary["paired"]["n"])
        self.assertGreater(summary["paired"]["input_savings_rate_mean"], 0)

    def test_api_mode_requires_explicit_confirmation_before_output_creation(self):
        from experiments.runners.run_autogen_natural_experiment import async_main, build_parser

        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(
                [
                    "--mode",
                    "api",
                    "--out",
                    directory,
                    "--experiment-id",
                    "must-not-run",
                ]
            )
            with self.assertRaises(SystemExit) as caught:
                asyncio.run(async_main(args))

            self.assertIn("--confirm-send-synthetic-data", str(caught.exception))
            self.assertFalse((Path(directory) / "must-not-run").exists())


if __name__ == "__main__":
    unittest.main()
