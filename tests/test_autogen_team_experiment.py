"""Tests for the paired AutoGen team namespace experiment runner."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from experiments.runners.run_autogen_team_experiment import (
    _contains_terms,
    _valid_prefixed_line,
    async_main,
    build_parser,
    load_tasks,
)


TASKS = Path("tasks/stage5_autogen_team/team_tasks.json")


class AutoGenTeamExperimentTest(unittest.TestCase):
    def test_output_contract_accepts_space_or_colon_but_rejects_other_prefixes(self):
        self.assertTrue(_valid_prefixed_line("HANDOFF task=x", "HANDOFF"))
        self.assertTrue(_valid_prefixed_line("HANDOFF: task=x", "HANDOFF"))
        self.assertTrue(_valid_prefixed_line("RESULT task=x", "RESULT"))
        self.assertFalse(_valid_prefixed_line("HANDOFF-task=x", "HANDOFF"))
        self.assertFalse(_valid_prefixed_line("note\nRESULT task=x", "RESULT"))

    def test_term_matching_accepts_equivalent_units_and_pass_status(self):
        self.assertTrue(_contains_terms("retention=21", ["21-days"]))
        self.assertTrue(_contains_terms("delay=96 hours", ["96-hours"]))
        self.assertTrue(_contains_terms("replica_lag=12", ["12-seconds"]))
        self.assertTrue(_contains_terms("checksum=pass", ["passed"]))
        self.assertFalse(_contains_terms("retention=30", ["21-days"]))

    def test_task_set_defines_three_namespace_handoff_scenarios(self):
        tasks = load_tasks(TASKS)

        self.assertEqual(3, len(tasks))
        self.assertEqual(3, len({task["task_id"] for task in tasks}))
        for task in tasks:
            self.assertIn("PLAN-SECRET", task["planner_private_canary"])
            self.assertIn("EXEC-PRIVATE", task["executor_private_canary"])
            self.assertTrue(task["expected_handoff_terms"])
            self.assertTrue(task["expected_final_terms"])
            self.assertTrue(task["planner_output_schema"].startswith("HANDOFF "))
            self.assertTrue(task["executor_output_schema"].startswith("RESULT "))

    def test_api_plan_is_offline_and_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(
                [
                    "--mode",
                    "api",
                    "--plan",
                    "--repeats",
                    "1",
                    "--max-api-requests",
                    "18",
                    "--out",
                    directory,
                    "--experiment-id",
                    "plan-only",
                ]
            )

            code = asyncio.run(async_main(args))

            self.assertEqual(0, code)
            self.assertFalse((Path(directory) / "plan-only").exists())

    def test_api_mode_requires_confirmation_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(
                [
                    "--mode",
                    "api",
                    "--repeats",
                    "1",
                    "--max-api-requests",
                    "18",
                    "--out",
                    directory,
                    "--experiment-id",
                    "no-confirm",
                ]
            )

            with self.assertRaisesRegex(SystemExit, "explicit|confirm"):
                asyncio.run(async_main(args))

            self.assertFalse((Path(directory) / "no-confirm").exists())

    def test_confirmatory_protocol_matches_frozen_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            args = build_parser().parse_args(
                [
                    "--mode",
                    "api",
                    "--plan",
                    "--protocol",
                    "tasks/stage5_autogen_team/confirmatory_v107_protocol.json",
                    "--repeats",
                    "5",
                    "--model",
                    "deepseek-flash",
                    "--thinking-mode",
                    "disabled",
                    "--max-output-tokens",
                    "512",
                    "--max-visible-output-chars",
                    "180",
                    "--max-api-retries",
                    "2",
                    "--max-api-requests",
                    "72",
                    "--out",
                    directory,
                ]
            )

            self.assertEqual(0, asyncio.run(async_main(args)))

    def test_confirmatory_protocol_rejects_changed_repeat_count(self):
        args = build_parser().parse_args(
            [
                "--mode",
                "api",
                "--plan",
                "--protocol",
                "tasks/stage5_autogen_team/confirmatory_v107_protocol.json",
                "--repeats",
                "4",
                "--model",
                "deepseek-flash",
                "--thinking-mode",
                "disabled",
                "--max-api-retries",
                "2",
                "--max-api-requests",
                "72",
            ]
        )

        with self.assertRaisesRegex(SystemExit, "protocol mismatch"):
            asyncio.run(async_main(args))

    def test_mock_pair_preserves_namespaces_saves_tokens_and_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            argv = [
                "--mode",
                "mock",
                "--task-ids",
                "incident_handoff",
                "--repeats",
                "1",
                "--out",
                directory,
                "--experiment-id",
                "paired",
            ]
            args = build_parser().parse_args(argv)
            asyncio.run(async_main(args))
            root = Path(directory) / "paired"
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
            manifest = json.loads(
                (root / "run_manifest.json").read_text(encoding="utf-8")
            )
            raw_path = root / "results.jsonl"
            raw_before_audit = raw_path.read_bytes()
            rows_before = raw_before_audit.decode("utf-8").splitlines()
            result_rows = [json.loads(line) for line in rows_before]

            self.assertEqual(1, summary["paired"]["n"])
            self.assertEqual(1, summary["paired"]["matched_n"])
            self.assertEqual(0, summary["paired"]["incomplete_n"])
            self.assertFalse(summary["evaluation_answer_terms_disclosed"])
            self.assertEqual("default", summary["thinking_mode"])
            self.assertFalse(manifest["evaluation_answer_terms_disclosed"])
            self.assertEqual(
                "task_facts_plus_field_schema_no_expected_terms",
                manifest["prompt_protocol"],
            )
            self.assertGreater(summary["paired"]["input_savings_rate_mean"], 0)
            self.assertIn("planner_output_token_change_rate_mean", summary["paired"])
            self.assertIn("executor_latency_change_rate_mean", summary["paired"])
            self.assertEqual(1, summary["by_task"]["incident_handoff"]["paired_n"])
            for row in result_rows:
                self.assertTrue(row["output_contract_correct"])
                self.assertIn("provider_input_tokens", row["planner_usage"])
                self.assertIn("reasoning_tokens_estimate", row["executor_usage"])
                self.assertGreaterEqual(row["planner_latency_seconds"], 0)
                self.assertGreaterEqual(row["coordination_latency_seconds"], 0)
                self.assertGreaterEqual(row["executor_latency_seconds"], 0)
            for method in ("none", "pruner_v1"):
                self.assertEqual(1.0, summary["methods"][method]["success_rate"])
                self.assertEqual(1, summary["methods"][method]["completed_n"])
                self.assertEqual(
                    1.0,
                    summary["methods"][method]["model_input_privacy_rate"],
                )
                self.assertEqual(
                    1.0,
                    summary["methods"][method]["handoff_preserved_rate"],
                )

            from experiments.audits.audit_autogen_team_results import audit

            audited = audit(root, tasks_path=TASKS)
            self.assertTrue(all(row["success"] for row in audited))
            self.assertEqual(raw_before_audit, raw_path.read_bytes())

            resumed = build_parser().parse_args([*argv, "--resume"])
            asyncio.run(async_main(resumed))
            rows_after = (root / "results.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(rows_before, rows_after)


if __name__ == "__main__":
    unittest.main()
