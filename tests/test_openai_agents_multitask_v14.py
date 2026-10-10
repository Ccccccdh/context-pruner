"""Zero-API gates for the v14 multi-task chain.

Pins the claims the round rests on: the registration verifies, every task's replay gate is
qualified with its projection and safe set, the admission record applies the frozen rules,
the pilot audit is complete with the acceptance verdict recomputed (and false), the plugin
rows really carry the persisted mechanism counters, and both batches' manifests were written
in one pass with the frozen keys.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from experiments.audits import audit_openai_agents_multitask_v14 as audit_v14
from experiments.runners import openai_agents_multitask_registry_v14 as registry
from experiments.runners import openai_agents_multitask_replay_gate_v14 as gate

REPO = registry.REPO
RUNS = REPO / "runs/stage5-openai-agents-api"
FREEZE = REPO / "integrations/openai_agents/V14_MULTITASK_FREEZE_20261005.json"
ADMISSION = REPO / "integrations/openai_agents/V14_ADMISSION_20261005.json"
PILOT = RUNS / "openai-repo-diagnostic-v14-multitask-confirm-01"
ADMITTED = ("requests_binary_payload", "flask_empty_blueprint_name", "xarray_copy_dtype")


class RegistrationV14(unittest.TestCase):
    def test_every_task_registration_verifies(self):
        problems = registry.all_problems()
        self.assertEqual([], [f"{k}:{v}" for k, v in problems.items() if v])
        self.assertEqual(4, len(registry.task_ids()))
        for task_id in registry.task_ids():
            entry = registry.task(task_id)
            self.assertTrue(entry["head_matches_base_commit"], task_id)
            self.assertEqual(6, entry["read_calls"], task_id)
            self.assertEqual(7, entry["expected_model_calls"], task_id)

    def test_the_frozen_task_file_hash_matches_the_freeze(self):
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        amended = json.loads(
            (REPO / "integrations/openai_agents/V14_MULTITASK_FREEZE_AMENDMENT_20261005.json").read_text(
                encoding="utf-8"
            )
        )
        recorded = amended["amendments"][0]["new_sha256"]
        self.assertEqual(recorded, hashlib.sha256(registry.TASKS_FILE.read_bytes()).hexdigest())
        self.assertTrue(amended["freeze_sha256_unchanged"])
        self.assertEqual(
            freeze["held_out_basis"]["tasks_file_sha256"], amended["amendments"][0]["old_sha256"]
        )


class ReplayGateV14(unittest.TestCase):
    def test_every_gate_is_qualified_with_projection_and_safe_set(self):
        for task_id in registry.task_ids():
            report = gate.write(task_id)
            self.assertEqual([], report["problems"], task_id)
            self.assertTrue(report["invariant_gate"]["all_invariants_hold"], task_id)
            self.assertTrue(report["negative_control"]["caught"], task_id)
            self.assertGreaterEqual(report["projection"]["byte_saving_rate"], 0.03, task_id)
            self.assertGreater(report["safe_candidates"]["guard_filtered_safe_candidates"], 0, task_id)

    def test_the_gate_never_assumes_a_replacement_at_every_boundary(self):
        report = gate.analyse("requests_binary_payload")
        counts = report["safe_candidates"]["per_boundary_replaced_counts"]
        self.assertEqual(0, counts[0])
        self.assertEqual(3, counts[-1])
        self.assertEqual(7, len(report["boundaries"]))


class AdmissionV14(unittest.TestCase):
    def test_the_admission_record_applies_the_frozen_rules(self):
        record = json.loads(ADMISSION.read_text(encoding="utf-8"))
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(freeze["admission_rules"]["items"], record["rules_applied"])
        self.assertEqual(
            freeze["admission_rules"]["projection_floor"], record["projection_floor"]
        )
        self.assertEqual(list(ADMITTED), record["admitted_task_ids"])
        self.assertEqual(["requests_bad_host_unicode"], list(record["rejected"]))
        self.assertIn(
            "baseline_contract_attainable", record["rejected"]["requests_bad_host_unicode"]
        )
        for task_id, decision in record["decisions"].items():
            self.assertLessEqual(decision["requests"], 12, task_id)
            self.assertTrue(
                decision["manifest_written_in_one_pass"]
                and decision["no_manifest_amendment_tool_used"],
                task_id,
            )
            self.assertEqual(False, decision["manifest_keys_written_in_one_pass"]["citable_as_saving"])
            self.assertEqual(
                False,
                decision["manifest_keys_written_in_one_pass"]["citable_as_quality_equivalence"],
            )


class PilotV14(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = audit_v14.audit(PILOT, FREEZE, "pilot")

    def test_the_pilot_audit_is_complete_and_within_the_caps(self):
        self.assertEqual([], self.report["errors"])
        self.assertTrue(self.report["complete"])
        self.assertEqual(27, self.report["rows"])
        self.assertLessEqual(self.report["api_request_attempts"], 300)
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        limit = int(freeze["stage_pilot"]["max_summary_calls_per_sample"]) * self.report["rows"]
        self.assertLessEqual(self.report["summary_calls"], limit)

    def test_the_acceptance_line_was_not_met_and_that_is_reported(self):
        acceptance = self.report["acceptance"]
        self.assertFalse(acceptance["track_A_all_tasks"])
        self.assertTrue(acceptance["pooled_saving_at_least_3_percent"])
        self.assertFalse(acceptance["met"])
        self.assertEqual(1, self.report["per_task"]["xarray_copy_dtype"]["pruner_v1_track_A_pass"])
        self.assertEqual(3, self.report["per_task"]["xarray_copy_dtype"]["track_A_baseline_pass"])

    def test_cost_is_positive_across_tasks_with_the_spread_reported(self):
        pooled = self.report["pooled"]["pruner_v1"]
        self.assertEqual(9, pooled["paired_n"])
        self.assertEqual(9, pooled["positive_pairs"])
        self.assertGreaterEqual(pooled["mean_saving"], 0.03)
        self.assertTrue(self.report["dispersion"]["all_tasks_positive"])
        self.assertGreater(self.report["dispersion"]["spread"], 0.0)
        self.assertEqual(3, len(self.report["dispersion"]["per_task_means"]))

    def test_the_plugin_rows_carry_the_persisted_counters(self):
        rows = audit_v14.read_jsonl(PILOT / "samples.jsonl")
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertEqual(9, len(plugin))
        for row in plugin:
            for name in (
                "exact_duplicate_replacements",
                "exact_duplicate_saved_bytes",
                "trigger_gate_triggered_calls",
                "narrow_guard_protected_unit_count",
            ):
                self.assertIn(name, row, f"{row['scenario']}:{name}")
            self.assertGreater(int(row["exact_duplicate_replacements"]), 0, row["scenario"])
        manifest = json.loads((PILOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["manifest_written_in_one_pass"])
        self.assertNotIn("manifest_amendments", manifest)
        self.assertFalse(manifest["citable_as_saving"])
        self.assertFalse(manifest["citable_as_quality_equivalence"])
        self.assertEqual(
            hashlib.sha256(FREEZE.read_bytes()).hexdigest(), manifest["freeze_sha256"]
        )

    def test_the_two_failures_are_the_length_condition_not_lost_facts(self):
        failures = [
            key for key, entry in self.report["quality"].items() if not entry["track_A"]["pass"]
        ]
        self.assertEqual(2, len(failures))
        for key in failures:
            entry = self.report["quality"][key]
            failing = [name for name, ok in entry["track_A"]["conditions"].items() if not ok]
            self.assertEqual(["within_max_chars"], failing, key)
            self.assertTrue(all(entry["track_A"]["required_terms"].values()), key)
            self.assertGreater(entry["answer_chars"], 160)


if __name__ == "__main__":
    unittest.main()
