"""Tests for the Microsoft Agent Framework paired experiment runner."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_framework import AgentResponse, Message

from experiments.runners.run_microsoft_agent_framework_experiment import (
    _final_response_text,
    async_main,
    build_parser,
)


class MicrosoftAgentFrameworkExperimentTest(unittest.IsolatedAsyncioTestCase):
    def arguments(self, root: Path, *extra: str):
        return build_parser().parse_args(
            [
                "--task-ids",
                "incident_triage",
                "--repeats",
                "1",
                "--out",
                str(root),
                "--experiment-id",
                "test-run",
                *extra,
            ]
        )

    async def test_plan_sends_no_request_and_creates_no_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = io.StringIO()
            with redirect_stdout(output):
                await async_main(self.arguments(root, "--mode", "api", "--plan"))

            self.assertIn("No API request was sent", output.getvalue())
            self.assertFalse((root / "test-run").exists())

    async def test_final_response_uses_last_message_not_concatenated_text(self) -> None:
        response = AgentResponse(
            messages=[
                Message("assistant", ["I'll verify first."]),
                Message("assistant", ["RESULT task=demo decision=GO"]),
            ]
        )

        self.assertEqual(
            "RESULT task=demo decision=GO",
            _final_response_text(response),
        )

    async def test_mock_runs_real_framework_tool_loop_and_paired_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(io.StringIO()):
                await async_main(self.arguments(root, "--mode", "mock"))

            output = root / "test-run"
            rows = [
                json.loads(line)
                for line in (output / "results.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
            manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(2, len(rows))
            self.assertTrue(all(row["success"] for row in rows))
            self.assertTrue(all(row["tool_correct"] for row in rows))
            self.assertTrue(all(row["structure_safe"] for row in rows))
            self.assertTrue(all(row["session_state_roundtrip"] for row in rows))
            pruner_row = next(row for row in rows if row["method"] == "pruner_v1")
            self.assertEqual(0, pruner_row["hard_budget_violation_count"])
            self.assertGreater(pruner_row["estimated_peak_model_input_tokens"], 0)
            self.assertGreater(pruner_row["estimated_peak_reserved_tokens"], 0)
            self.assertEqual(1, summary["paired"]["n"])
            self.assertGreater(summary["paired"]["input_savings_rate_mean"], 0)
            self.assertIn("total_token_savings_rate_mean", summary["paired"])
            self.assertIn("latency_change_rate_mean", summary["paired"])
            self.assertFalse(manifest["evaluation_answer_terms_disclosed"])
            self.assertEqual("1.19.0", manifest["agent_framework_core_version"])

    async def test_resume_skips_complete_rows_without_duplicate_append(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with redirect_stdout(io.StringIO()):
                await async_main(self.arguments(root, "--mode", "mock"))
            results = root / "test-run" / "results.jsonl"
            before = results.read_text(encoding="utf-8")

            with redirect_stdout(io.StringIO()):
                await async_main(
                    self.arguments(root, "--mode", "mock", "--resume")
                )

            self.assertEqual(before, results.read_text(encoding="utf-8"))

    async def test_rejects_markdown_base_url_before_api_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "plain URL"):
                await async_main(
                    self.arguments(
                        Path(directory),
                        "--mode",
                        "api",
                        "--base-url",
                        "[https://api.deepseek.com](https://api.deepseek.com)",
                    )
                )


if __name__ == "__main__":
    unittest.main()
