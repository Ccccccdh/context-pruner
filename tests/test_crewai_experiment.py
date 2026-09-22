"""Tests for the CrewAI natural-task paired experiment runner."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


_CREWAI_TEST_STORAGE = Path(tempfile.gettempdir()) / "context-pruner-crewai-tests"
_CREWAI_TEST_STORAGE.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("LOCALAPPDATA", str(_CREWAI_TEST_STORAGE))
os.environ.setdefault("CREWAI_STORAGE_DIR", str(_CREWAI_TEST_STORAGE))

CREWAI_INSTALLED = importlib.util.find_spec("crewai") is not None

if CREWAI_INSTALLED:
    from experiments.runners import run_crewai_experiment as runner


@unittest.skipUnless(CREWAI_INSTALLED, "CrewAI optional dependency is not installed")
class CrewAIExperimentTest(unittest.TestCase):
    @staticmethod
    def _capture_main(arguments: list[str]) -> str:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            runner.main(arguments)
        return output.getvalue()

    def test_api_plan_is_offline_and_does_not_create_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs"
            text = self._capture_main(
                [
                    "--mode",
                    "api",
                    "--task-ids",
                    "incident_triage",
                    "--out",
                    str(output),
                    "--experiment-id",
                    "plan-only",
                    "--plan",
                ]
            )

            self.assertIn("No API request was sent", text)
            self.assertFalse(output.exists())

    def test_api_mode_requires_confirmation_before_output_creation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs"
            with self.assertRaisesRegex(SystemExit, "confirm-send-synthetic-data"):
                self._capture_main(
                    [
                        "--mode",
                        "api",
                        "--task-ids",
                        "incident_triage",
                        "--out",
                        str(output),
                        "--experiment-id",
                        "no-confirmation",
                    ]
                )

            self.assertFalse(output.exists())

    def test_mock_pair_runs_real_agent_tool_loop_and_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "runs"
            arguments = [
                "--mode",
                "mock",
                "--task-ids",
                "incident_triage",
                "--methods",
                "none,pruner_v1",
                "--repeats",
                "1",
                "--out",
                str(output),
                "--experiment-id",
                "paired",
            ]
            self._capture_main(arguments)
            experiment = output / "paired"
            rows = [
                json.loads(line)
                for line in (experiment / "results.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            summary = json.loads(
                (experiment / "summary.json").read_text(encoding="utf-8")
            )

            self.assertEqual(2, len(rows))
            self.assertTrue(all(row["success"] for row in rows))
            self.assertTrue(all(row["model_calls"] == 3 for row in rows))
            self.assertTrue(all(row["tool_calls"] == 2 for row in rows))
            self.assertTrue(all(row["state_roundtrip"] for row in rows))
            self.assertTrue(all(row["structure_safe"] for row in rows))
            self.assertEqual(1, summary["paired"]["n"])
            self.assertGreater(summary["paired"]["input_savings_rate_mean"], 0)
            self.assertEqual(0, summary["methods"]["pruner_v1"]["hard_budget_violations"])

            resumed = self._capture_main([*arguments, "--resume"])
            rows_after = [
                line
                for line in (experiment / "results.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            self.assertEqual(2, len(rows_after))
            self.assertIn("skip incident_triage", resumed)

    def test_request_budget_rejects_overrun_and_invalid_resume_usage(self) -> None:
        budget = runner.RequestBudget(2)
        budget.consume()
        budget.consume()
        with self.assertRaisesRegex(RuntimeError, "global API request limit"):
            budget.consume()
        with self.assertRaisesRegex(ValueError, "existing API requests"):
            runner.RequestBudget(2, used=3)

    def test_react_normalizer_keeps_only_first_real_tool_action(self) -> None:
        raw = (
            "Thought: verify both facts\n"
            "Action: get_service_health\n"
            'Action Input: {"service":"payments-api"}\n'
            'Observation: {"status":"degraded"}\n'
            "Action: get_recent_deployment\n"
            'Action Input: {"service":"payments-api"}'
        )

        normalized, sanitized = runner._normalize_react_response(raw)

        self.assertTrue(sanitized)
        self.assertIn("Action: get_service_health", normalized)
        self.assertNotIn("Observation", normalized)
        self.assertNotIn("get_recent_deployment", normalized)
        self.assertEqual(1, normalized.count("Action:"))

    def test_react_normalizer_returns_one_line_final_answer(self) -> None:
        raw = (
            "Thought: complete\n"
            "Final Answer: RESULT task=incident_triage decision=ROLLBACK evidence=ok\n"
            "This trailing text must not escape the contract."
        )

        normalized, sanitized = runner._normalize_react_response(raw)

        self.assertTrue(sanitized)
        self.assertEqual(
            "Final Answer: RESULT task=incident_triage decision=ROLLBACK evidence=ok",
            normalized,
        )

    def test_api_llm_disables_thinking_and_records_sanitization(self) -> None:
        class Value:
            pass

        message = Value()
        message.content = (
            "Thought: verify\nAction: get_service_health\n"
            'Action Input: {"service":"payments-api"}\nObservation: invented'
        )
        message.reasoning_content = "private reasoning must not be returned"
        choice = Value()
        choice.message = message
        choice.finish_reason = "stop"
        details = Value()
        details.reasoning_tokens = 7
        usage = Value()
        usage.prompt_tokens = 11
        usage.completion_tokens = 19
        usage.completion_tokens_details = details
        completion = Value()
        completion.choices = [choice]
        completion.usage = usage

        class Completions:
            def __init__(self) -> None:
                self.request = None

            def create(self, **kwargs):
                self.request = kwargs
                return completion

        class Client:
            def __init__(self) -> None:
                self.chat = Value()
                self.chat.completions = Completions()

        client = Client()
        llm = runner.OpenAICompatCrewAILLM(
            client=client,
            model="deepseek-v4-flash",
            request_budget=runner.RequestBudget(1),
            max_output_tokens=512,
            thinking_mode="disabled",
        )

        response = llm.call([{"role": "user", "content": "test"}])

        self.assertEqual(
            {"thinking": {"type": "disabled"}},
            client.chat.completions.request["extra_body"],
        )
        self.assertNotIn("Observation", response)
        self.assertNotIn("private reasoning", response)
        self.assertEqual(7, llm.response_records[0]["reasoning_tokens"])
        self.assertTrue(llm.response_records[0]["react_response_sanitized"])


if __name__ == "__main__":
    unittest.main()
