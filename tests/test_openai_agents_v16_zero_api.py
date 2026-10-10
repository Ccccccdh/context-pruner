"""Zero-API gates for the v16 registration, controls and cost accounting.

Pins: the registration verifies with two tasks and one recorded rejection, the structured-output
controls never exceed two provider calls (one billed retry), the independent audit rejects all
four tampered records, cache fields are fail-closed, and the USD cost arithmetic matches the
frozen prices. Nothing here contacts a provider or touches a frozen artifact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import unittest
from pathlib import Path

from experiments.runners import openai_agents_v16_controls as controls
from experiments.runners import openai_agents_v16_cost_table as cost
from experiments.runners import openai_agents_v16_source_registration as registration

REPO = Path(__file__).resolve().parents[1]
REGISTRATION_JSON = REPO / "integrations/openai_agents/V16_SOURCE_REGISTRATION_20261006.json"
CONTROLS_JSON = REPO / "integrations/openai_agents/V16_NEGATIVE_CONTROL_20261006.json"
COST_JSON = REPO / "integrations/openai_agents/V16_CACHE_METERING_AND_COST_20261006.json"


class RegistrationV16(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = registration.build()

    def test_two_tasks_register_and_one_candidate_is_rejected_not_substituted(self):
        self.assertTrue(self.report["verified"], self.report["problems"])
        self.assertEqual(2, self.report["registered_task_count"])
        rejected = self.report["rejected_candidates"]
        self.assertEqual(["django__django-13410"], [entry["instance_id"] for entry in rejected])
        self.assertEqual("rejected_not_substituted", rejected[0]["decision"])
        self.assertEqual(0, rejected[0]["occurrence_counts"]["lockf"])
        self.assertEqual(2, rejected[0]["occurrence_counts"]["flock"])
        self.assertEqual(
            "580a4341cb0b4cbfc215a70afc004875a7e815f4", rejected[0]["base_commit"]
        )
        self.assertEqual(
            "c46b00b90576cca644e9dae1870b3a944ae1e4fa",
            rejected[0]["checked_files"][0]["git_blob_id"],
        )
        self.assertTrue(rejected[0]["checked_files"][0]["content_sha256"])
        self.assertTrue(rejected[0]["commands"])
        self.assertEqual(
            "NO_FRESH_CANDIDATE_UNDER_THE_SAME_RULE", self.report["selection_block"]["status"]
        )
        self.assertEqual(
            0, self.report["selection_block"]["measured"]["eligible_after_exclusions"]
        )

    def test_every_view_is_pinned_by_blob_hash_and_content_hash(self):
        for task_id, task in self.report["tasks"].items():
            for view in task["views"]:
                self.assertRegex(view["blob_sha256_git"], r"^[0-9a-f]{40}$", task_id)
                self.assertRegex(view["view_content_sha256"], r"^[0-9a-f]{64}$", task_id)
                self.assertEqual(view["commit_path"], f"{task['base_commit']}:{view['file']}")
            self.assertTrue(task["problem_statement_sha256_matches_preselection"], task_id)

    def test_the_defect_is_present_or_absent_as_registered(self):
        floor = self.report["tasks"]["django_sqlite_version_floor"]["defect_evidence"]
        ordered = self.report["tasks"]["django_orderedset_reversed"]["defect_evidence"]
        self.assertEqual(("present", True), (floor["observed"], floor["ok"]))
        self.assertEqual(("absent", True), (ordered["observed"], ordered["ok"]))


class ControlsV16(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = asyncio.run(controls.run_controls())

    def test_no_control_exceeds_one_billed_retry(self):
        self.assertTrue(self.report["structured_output_all_ok"])
        self.assertEqual(2, self.report["max_provider_calls_observed"])
        for entry in self.report["structured_output_controls"]:
            self.assertLessEqual(entry["provider_calls"], 2, entry["control"])

    def test_over_long_twice_is_closed_on_the_length_rule(self):
        entry = next(
            item
            for item in self.report["structured_output_controls"]
            if item["control"] == "over_long_twice_closed"
        )
        self.assertFalse(entry["accepted"])
        self.assertIn("over_160_chars", entry["reason"])
        self.assertEqual(2, entry["provider_calls"])

    def test_the_audit_rejects_every_tampered_record(self):
        self.assertTrue(self.report["audit_all_ok"])
        outcomes = {entry["control"]: entry for entry in self.report["audit_controls"]}
        self.assertTrue(outcomes["clean_record"]["complete"])
        for name in ("changed_view_hash", "deleted_contract_field", "tampered_pinned_value", "forged_complete"):
            self.assertFalse(outcomes[name]["complete"], name)

    def test_cache_fields_are_fail_closed(self):
        cache = self.report["cache_controls"]
        self.assertTrue(cache["ok"])
        self.assertIsNone(cache["without_fields"]["prompt_cache_hit_tokens"])
        self.assertTrue(cache["without_fields"]["cache_usage_unknown"])
        self.assertEqual(1, cache["aggregate"]["calls_without_cache_fields"])

    def test_the_three_arm_run_was_not_started(self):
        self.assertFalse(self.report["three_arm_started"])
        self.assertIn("not started", self.report["three_arm_gate"])


class CostV16(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = cost.build()

    def test_prices_and_ratios_are_the_official_ones(self):
        prices = self.report["prices_usd_per_million_tokens"]
        self.assertEqual(0.15, prices["input_cache_miss"])
        self.assertEqual(0.003, prices["input_cache_hit"])
        self.assertEqual(0.60, prices["output"])
        self.assertEqual(50.0, self.report["price_ratios"]["cache_hit_vs_miss"])
        self.assertEqual(200.0, self.report["price_ratios"]["output_vs_cache_hit"])

    def test_the_cost_arithmetic_is_exact_on_the_frozen_prices(self):
        tokens_in, tokens_out = 1_000_000, 1_000_000
        computed = cost.arm_costs(tokens_in, tokens_out)
        self.assertEqual(0.15, computed["input_all_miss_usd"])
        self.assertEqual(0.003, computed["input_all_hit_usd"])
        self.assertEqual(0.6, computed["output_usd"])
        self.assertEqual(0.75, computed["total_all_miss_usd"])
        self.assertEqual(0.075, computed["input_all_miss_off_peak_usd"])

    def test_v14_is_priced_and_the_output_delta_is_reported_with_the_over_length_pairs(self):
        v14 = self.report["v14"]["all_arms"]
        self.assertGreater(v14["input_tokens"], 0)
        self.assertGreater(v14["total_all_miss_usd"], v14["total_hypothetical_all_hit_usd"])
        delta = self.report["v14_output_delta_plugin_vs_baseline"]
        self.assertEqual(2, len(delta["over_length_pairs"]))
        for pair in delta["over_length_pairs"]:
            self.assertGreater(pair["answer_chars"], 160)

    def test_v15_matches_its_recorded_result(self):
        self.assertEqual(3, len(self.report["v15"]))
        recorded = {
            "django_field_error_messages_copy": (7, 11753),
            "django_method_decorator_partial": (7, 13577),
            "django_textchoices_string_value": (8, 15580),
        }
        for task, (requests, tokens) in recorded.items():
            batch = next(
                item for item in self.report["v15"] if f"-{task}-acquisition-02" in item["batch"]
            )
            self.assertEqual(requests, sum(arm["requests"] for arm in batch["arms"].values()))
            self.assertEqual(
                tokens, batch["all_arms"]["input_tokens"] + batch["all_arms"]["output_tokens"]
            )


class FrozenArtifactsUntouched(unittest.TestCase):
    def test_the_v15_freeze_and_results_still_exist_with_their_recorded_hashes(self):
        admission = json.loads(
            (
                REPO / "integrations/openai_agents/V15_ACQUISITION_02_ADMISSION_20261006.json"
            ).read_text(encoding="utf-8")
        )
        freeze = REPO / "integrations/openai_agents/V15_ACQUISITION_FREEZE_02_20261006.json"
        self.assertTrue(freeze.is_file())
        self.assertEqual(
            admission["freeze_sha256"], hashlib.sha256(freeze.read_bytes()).hexdigest()
        )
        self.assertFalse(admission["pilot_gate_met"])
        self.assertEqual("STOP_BEFORE_THREE_ARM_PILOT", admission["decision"])
        self.assertTrue(
            (REPO / "integrations/openai_agents/V15_ACQUISITION_02_RESULT_20261006.md").is_file()
        )

    def test_this_round_wrote_only_new_v16_artifacts(self):
        for path in (REGISTRATION_JSON, CONTROLS_JSON, COST_JSON):
            self.assertTrue(path.is_file(), path.name)
            text = path.read_text(encoding="utf-8")
            self.assertEqual(0, json.loads(text).get("paid_requests", 0), path.name)


if __name__ == "__main__":
    unittest.main()
