"""Zero-API tests for the v17 host configuration (first-attempt compliance fix).

They assert the four properties the configuration claims: the literal-placement rule is in the
first prompt, the prompt text is byte-identical for every arm, a proposal that declares a
required literal outside cause/fix is still rejected, and the retry diagnostic is byte-identical
to v16. Nothing here contacts a provider.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from experiments.runners import openai_agents_v16_live_contract as live16
from experiments.runners import openai_agents_v16_registry as registry16
from experiments.runners import openai_agents_v17_live_contract as live17
from experiments.runners import openai_agents_v17_registry as registry17
from experiments.runners import run_openai_agents_v17_acquisition as runner
from experiments.runners.openai_agents_structured_final_v16 import render

ROOT = Path(__file__).resolve().parents[1]
DIFF = ROOT / "integrations/openai_agents/V17_HOST_CONFIGURATION_DIFF_20261010.json"
FREEZE = ROOT / "integrations/openai_agents/V17_DEV_PILOT_FREEZE_20261010.json"
RULE = "Every one of those strings must also appear verbatim inside"


class RuleSentenceTests(unittest.TestCase):
    def test_rule_sentence_is_in_every_first_prompt(self) -> None:
        for task_id in registry17.task_ids():
            self.assertIn(RULE, registry17.task(task_id)["contract_prompt"], task_id)

    def test_only_the_prompt_sentence_differs_from_v16(self) -> None:
        diff = json.loads(DIFF.read_text(encoding="utf-8"))
        registry_change = next(
            entry for entry in diff["forks"] if entry["target"].endswith("v17_registry.py")
        )
        self.assertEqual(1, registry_change["change_count"])
        self.assertEqual("first_prompt_literal_placement_rule", registry_change["changes"][0]["label"])
        self.assertFalse(diff["criteria_changed"])
        self.assertEqual(0, int(diff["frozen_files_changed_after_freeze"]))

    def test_prompt_is_task_level_so_no_arm_can_differ(self) -> None:
        # the prompt depends on the task contract only, and the agent factory takes no arm
        source = (
            ROOT / "experiments/runners/openai_agents_v17_live_contract.py"
        ).read_text(encoding="utf-8")
        factory = source.split("def agent_factory", 1)[1].split("def model_factory", 1)[0]
        self.assertNotIn("method", factory)
        for task_id in registry17.task_ids():
            first = registry17.task(task_id)["contract_prompt"]
            second = registry17.task(task_id)["contract_prompt"]
            self.assertEqual(first, second)

    def test_v16_prompt_is_unchanged(self) -> None:
        for task_id in registry16.task_ids():
            self.assertNotIn(RULE, registry16.task(task_id)["contract_prompt"], task_id)


class DiagnosticUnchangedTests(unittest.TestCase):
    def test_every_rejection_class_matches_v16_byte_for_byte(self) -> None:
        fixed = {
            "raw": json.dumps(
                {
                    "issue": "django-13821",
                    "cause": "check_sqlite_version floor",
                    "fix": "require 3.9.0",
                    "facts": ["check_sqlite_version", "3.12.0"],
                }
            ),
            "expected_issue": "django-13821",
            "required_facts": ["check_sqlite_version", "3.8.3", "3.9.0"],
            "cause_token": "check_sqlite_version",
            "fix_token": "3.9.0",
        }
        for reason in (
            "over_160_chars",
            "declared_fact_missing_from_rendered_answer",
            "invalid_json_or_duplicate_key",
            "missing_or_extra_field",
            "invalid_fact_list",
            "invalid_cause_or_fix",
            "embedded_field_delimiter",
            "issue_mismatch",
            "invalid_issue",
        ):
            self.assertEqual(
                live16.diagnostic_for(reason, **fixed),
                live17.diagnostic_for(reason, **fixed),
                reason,
            )

    def test_conditions_match_v16_on_a_fixed_line(self) -> None:
        line = "RESULT issue=django-13821 cause=check_sqlite_version floor is 3.8.3 fix=require 3.9.0"
        pattern = registry17.task("django_sqlite_version_floor")["answer_pattern"]
        facts = ["check_sqlite_version", "3.8.3", "3.9.0"]
        self.assertEqual(
            live16.conditions(line, "django-13821", pattern, facts),
            live17.conditions(line, "django-13821", pattern, facts),
        )


class StrictTrackUnchangedTests(unittest.TestCase):
    def test_one_missing_required_literal_is_still_rejected(self) -> None:
        proposal = json.dumps(
            {
                "issue": "django-13821",
                "cause": "check_sqlite_version floor is 3.8.3",
                "fix": "require 3.9.0",
                "facts": ["check_sqlite_version", "3.8.3", "3.9.0", "__iter__"],
            }
        )
        verdict = render(proposal, expected_issue="django-13821")
        self.assertFalse(verdict.accepted)
        self.assertEqual("declared_fact_missing_from_rendered_answer", verdict.reason)
        self.assertEqual("", verdict.output)

    def test_declared_string_outside_the_rendered_line_is_still_rejected(self) -> None:
        proposal = json.dumps(
            {
                "issue": "django-13821",
                "cause": "check_sqlite_version floor is 3.8.3",
                "fix": "require 3.9.0",
                "facts": ["check_sqlite_version", "3.8.3", "3.9.0", "unregistered-string"],
            }
        )
        verdict = render(proposal, expected_issue="django-13821")
        self.assertFalse(verdict.accepted)

    def test_over_length_is_still_rejected(self) -> None:
        proposal = json.dumps(
            {
                "issue": "django-13821",
                "cause": ("check_sqlite_version " + "floor " * 40).strip(),
                "fix": "require 3.9.0",
                "facts": ["check_sqlite_version", "3.9.0"],
            }
        )
        verdict = render(proposal, expected_issue="django-13821")
        self.assertFalse(verdict.accepted)
        self.assertEqual("over_160_chars", verdict.reason)


class FreezeAndCapTests(unittest.TestCase):
    def test_frozen_matrix_and_cap_hold(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual("FROZEN", freeze["status"])
        self.assertEqual(9, freeze["matrix"]["samples"])
        self.assertEqual(1, freeze["matrix"]["repeats"])
        self.assertEqual(110, freeze["budget_arithmetic"]["max_api_requests"])
        self.assertEqual(
            ["none", "native_summary", "pruner_v1"], list(freeze["matrix"]["arms"])
        )
        self.assertEqual(9, runner.PILOT_REPEATS * 3 * len(runner.PILOT_METHODS))
        self.assertEqual(110, runner.PILOT_MAX_API_REQUESTS)
        self.assertEqual(
            sorted(freeze["matrix"]["arms"]), sorted(runner.PILOT_METHODS)
        )

    def test_cap_keeps_a_1_3x_margin_over_the_frozen_worst_case(self) -> None:
        arithmetic = runner.pilot_arithmetic(runner.pilot_draft_freeze())
        self.assertLessEqual(arithmetic["worst_case_requests"], 110)
        self.assertGreaterEqual(110 / arithmetic["worst_case_requests"], 1.3)
        self.assertEqual(9, arithmetic["samples"])

    def test_renderer_module_is_the_v16_one(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        expected = str(freeze["host_configuration_diff_against_v16"]["unchanged"][0])
        self.assertIn("openai_agents_structured_final_v16.py", expected)
        digest = hashlib.sha256(
            (ROOT / "experiments/runners/openai_agents_structured_final_v16.py").read_bytes()
        ).hexdigest()
        self.assertIn(digest, expected)
        self.assertIn("22fa96a53c5aff5e372c4ef88787165e9e1f13e671b7ed0bafe5db2e2051612a", expected)


class InstrumentationFieldTests(unittest.TestCase):
    def test_v17_contract_declares_the_recording_fields(self) -> None:
        source = (
            ROOT / "experiments/runners/openai_agents_v17_live_contract.py"
        ).read_text(encoding="utf-8")
        for field in (
            "first_attempt_accepted",
            "first_reject_class",
            "first_candidate_line",
            "first_declared_facts",
            "first_missing_required_literals",
        ):
            self.assertIn(f'"{field}"', source, field)

    def test_candidate_line_and_declared_facts_helpers(self) -> None:
        raw = json.dumps(
            {
                "issue": "django-13821",
                "cause": "check_sqlite_version floor is 3.8.3",
                "fix": "require 3.9.0",
                "facts": ["check_sqlite_version", "3.8.3", "3.9.0"],
            }
        )
        self.assertEqual(
            "RESULT issue=django-13821 cause=check_sqlite_version floor is 3.8.3 "
            "fix=require 3.9.0",
            live17.candidate_line(raw, expected_issue="django-13821"),
        )
        self.assertEqual(
            ["check_sqlite_version", "3.8.3", "3.9.0"], live17.declared_facts(raw)
        )
        self.assertEqual([], live17.declared_facts("not json"))
        self.assertEqual("", live17.candidate_line("not json"))


if __name__ == "__main__":
    unittest.main()
