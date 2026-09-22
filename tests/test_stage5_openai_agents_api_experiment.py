"""Offline checks for the consent-gated real API Runner experiment."""

import contextlib
import asyncio
import importlib.util
import io
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(
    importlib.util.find_spec("agents") is not None,
    "openai-agents optional dependency is not installed",
)
class Stage5OpenAIAgentsApiExperimentTest(unittest.TestCase):
    def test_plan_is_offline_and_reports_request_cap(self):
        from experiments.runners.run_openai_agents_api_experiment import main

        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), contextlib.redirect_stdout(output):
            self.assertEqual(0, main(["--plan"]))
        text = output.getvalue()
        self.assertIn("minimum_planned_model_requests=14", text)
        self.assertIn("hard_request_cap=24", text)
        self.assertIn("No API request was sent", text)

    def test_explicit_confirmation_is_required_before_key_lookup(self):
        from experiments.runners.run_openai_agents_api_experiment import main

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(SystemExit, "confirm-send-synthetic-data"):
                main([])

    def test_markdown_base_url_is_rejected(self):
        from experiments.runners.run_openai_agents_api_experiment import main

        with self.assertRaisesRegex(SystemExit, "plain http"):
            main(["--plan", "--base-url", "[https://api.deepseek.com](x)"])

    def test_cases_define_required_tool_protocol(self):
        from experiments.runners.run_openai_agents_api_experiment import SCENARIOS, build_case

        cases = {scenario: build_case(scenario, 0) for scenario in SCENARIOS}
        self.assertEqual(("lookup_project",), cases["single_tool"].expected_tool_names)
        self.assertTrue(cases["single_tool"].final_contract.startswith("RESULT "))
        self.assertTrue(cases["parallel_tools"].require_parallel_first_batch)
        self.assertEqual(
            ("lookup_project", "check_policy"),
            cases["multi_tool_chain"].expected_tool_names,
        )

    def test_retry_is_counted_against_global_request_budget(self):
        from experiments.runners.run_openai_agents_api_experiment import RecordingRetryModel, RequestBudget

        class APIConnectionError(Exception):
            pass

        class FlakyModel:
            def __init__(self):
                self.attempts = 0

            async def get_response(self, *args, **kwargs):
                self.attempts += 1
                if self.attempts == 1:
                    raise APIConnectionError("temporary")
                return SimpleNamespace(output=[], usage=SimpleNamespace())

        budget = RequestBudget(2)
        model = RecordingRetryModel(
            FlakyModel(), budget, max_retries=1, retry_base_delay=0
        )

        async def invoke():
            return await model.get_response(
                "instructions",
                [{"role": "user", "content": "synthetic"}],
                None,
                [],
                None,
                [],
                None,
                previous_response_id=None,
                conversation_id=None,
                prompt=None,
            )

        response = asyncio.run(invoke())
        self.assertEqual([], response.output)
        self.assertEqual(2, budget.used)
        self.assertEqual(1, model.retry_count)
        self.assertEqual(1, len(model.inputs))

    def test_report_uses_latest_retry_and_actual_provider_tokens(self):
        from experiments.runners.run_openai_agents_api_experiment import build_report

        def row(method, tokens, success):
            return {
                "scenario": "single_tool",
                "repeat": 0,
                "method": method,
                "success": success,
                "answer_correct": success,
                "final_format_correct": success,
                "final_output_chars": 50,
                "tool_correct": success,
                "structure_safe": True,
                "actual_input_tokens": tokens,
                "actual_output_tokens": 10,
                "actual_peak_input_tokens": tokens,
                "model_calls": 2,
                "tool_calls": 1,
                "latency_seconds": 1.0,
                "estimated_cost": 0.0,
                "api_retry_count": 0,
                "restore_failure_count": 0,
                "unmatched_call_count": 0,
                "resync_count": 0,
            }

        report = build_report(
            [
                row("none", 100, True),
                row("pruner_v1", 90, False),
                row("pruner_v1", 60, True),
            ]
        )
        self.assertEqual(1, report["paired"]["paired_n"])
        self.assertAlmostEqual(
            0.4, report["paired"]["actual_input_savings_rate_vs_baseline"]
        )
        self.assertAlmostEqual(
            (110 - 70) / 110,
            report["paired"]["actual_total_token_savings_rate_vs_baseline"],
        )
        self.assertEqual(1.0, report["methods"][1]["success_rate"])


if __name__ == "__main__":
    unittest.main()
