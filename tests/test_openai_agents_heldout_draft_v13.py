"""Zero-API structural gate for the v13 held-out-task draft.

The draft is not a freeze and must never be mistaken for one: this gate pins that, and pins
the parts of the design that are easy to water down later (the two-track applicability
boundary, the replay gate's "no self-made stub" requirement, the failure lines).
"""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAFT = ROOT / "integrations/openai_agents/V13_HELDOUT_TASK_FREEZE_DRAFT_20261005.json"
PROTOCOL = ROOT / "integrations/openai_agents/V13_HELDOUT_TASK_PROTOCOL_DRAFT_20261005.md"


class HeldOutDraftV13(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.draft = json.loads(DRAFT.read_text(encoding="utf-8"))
        cls.text = PROTOCOL.read_text(encoding="utf-8")

    def test_the_draft_is_not_a_freeze_and_authorises_nothing(self):
        self.assertEqual("DRAFT_NOT_FROZEN", self.draft["status"])
        self.assertFalse(self.draft["frozen"])
        self.assertFalse(self.draft["may_run_paid_batch"])
        self.assertTrue(self.draft["amendment_required_for_any_change"])
        self.assertEqual(0, self.draft["paid_requests_this_round"])
        self.assertIn("DRAFT", DRAFT.name)
        # The approved chain has since been executed under its own one-shot freeze; the draft
        # never became that freeze and still authorises nothing by itself. The executed
        # artifacts are what the batches record, and they are asserted in
        # tests/test_openai_agents_requests_heldout_v13.py.
        executed = ROOT / "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
        self.assertTrue(executed.is_file())
        executed_freeze = json.loads(executed.read_text(encoding="utf-8"))
        self.assertEqual("FROZEN", executed_freeze["status"])
        self.assertIsNot(self.draft, executed_freeze)
        self.assertNotIn("batch", self.draft)
        self.assertNotEqual(DRAFT.name, executed.name)

    def test_every_executed_batch_records_the_freeze_it_ran_under(self):
        freeze = ROOT / "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"
        expected = hashlib.sha256(freeze.read_bytes()).hexdigest()
        batches = sorted(
            (ROOT / "runs/stage5-openai-agents-api").glob("openai-repo-diagnostic-v13-*")
        )
        self.assertTrue(batches)
        for batch in batches:
            manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(expected, manifest.get("freeze_sha256"), batch.name)
            for key, value in (
                ("citable_as_saving", False),
                ("citable_as_quality_equivalence", False),
            ):
                self.assertIn(key, manifest, batch.name)
                self.assertIs(value, manifest[key], batch.name)
            self.assertTrue(str(manifest.get("purpose") or "").strip(), batch.name)

    def test_two_tracks_with_explicit_applicability_boundary(self):
        tracks = self.draft["quality_tracks"]
        self.assertEqual("primary acceptance track", tracks["track_A_strict"]["role"])
        self.assertTrue(tracks["track_A_strict"]["single_line"])
        self.assertEqual(160, tracks["track_A_strict"]["max_characters"])
        semantic = tracks["track_B_semantic"]
        self.assertEqual(["cause=", "fix="], semantic["both_fields_required"])
        self.assertTrue(semantic["extra_explanation_inside_cause_allowed"])
        self.assertEqual(400, semantic["max_characters_cause_plus_fix"])
        boundary = semantic["applicability_boundary"]
        for phrase in ("missing required literal", "wrong number or identifier", "different decision label"):
            self.assertIn(phrase, boundary)
        self.assertIn("never as 'quality unchanged'", semantic["reporting_rule"])
        self.assertIn("diagnostic secondary track", semantic["role"])

    def test_replay_gate_requires_recorded_real_payloads(self):
        gate = self.draft["replay_gate"]
        self.assertTrue(gate["required_before_paid_stage_c"])
        stage_a = gate["stage_A_record_run"]
        self.assertEqual(["none"], stage_a["arms"])
        self.assertEqual(12, stage_a["max_api_requests"])
        self.assertIn("per_output_sha256", stage_a["required_recorded_fields"])
        self.assertIn("per_output_chars", stage_a["required_recorded_fields"])
        joined = " ".join(gate["conditions"])
        self.assertIn("no self-made stub", joined)
        self.assertIn("Exact duplicate output", joined)
        self.assertIn("negative control", joined)

    def test_matrix_budgets_and_failure_lines(self):
        stage_c = self.draft["stage_C_matrix"]
        self.assertEqual(["none", "pruner_v1", "native_summary"], stage_c["arms"])
        self.assertEqual(3, stage_c["repeats"])
        self.assertEqual(9, stage_c["samples"])
        self.assertEqual(66, stage_c["max_api_requests"])
        self.assertEqual(24, stage_c["requests_per_repeat"])
        self.assertEqual(65216, stage_c["filter_hard_bytes"])
        self.assertIn("track A plugin quality >= baseline", stage_c["acceptance_line"])
        self.assertIn(">= 3%", stage_c["acceptance_line"])
        for line in (
            "track A below baseline",
            "paired complete-total saving < 3%",
            "zero actual duplicate replacements",
            "a required literal loses presence",
            "request cap exceeded",
        ):
            self.assertIn(line, stage_c["failure_lines"])
        self.assertTrue(stage_c["all_attempts_and_failed_samples_counted"])
        self.assertTrue(stage_c["no_retry_to_chase_a_positive_number"])

    def test_the_new_task_is_held_out_from_every_host(self):
        basis = self.draft["held_out_basis"]
        self.assertTrue(basis["repo_unused_by_all_three_hosts"])
        self.assertTrue(basis["not_used_by_openai_agents_v1_to_v12"])
        self.assertEqual("psf/requests", self.draft["candidate_task"]["repo"])
        self.assertFalse(self.draft["candidate_task"]["selected"])
        # The claim is historical: before this round the repository had zero references to
        # this task. The executed chain is the only thing that now refers to it, and it is
        # confined to the v13 files plus its own batches.
        referring = sorted(
            path.name
            for path in (ROOT / "experiments/runners").glob("*requests*_v13.py")
        )
        self.assertTrue(referring)
        for path in (ROOT / "experiments/runners").glob("openai_agents_*_v13.py"):
            if "requests" in path.name:
                continue
            self.assertNotIn("psf/requests", path.read_text(encoding="utf-8"), path.name)

    def test_the_design_does_not_rest_on_candidate_counts(self):
        """The prescreen's counterexample must be written into the design, not just known."""
        alignment = self.draft["prescreen_alignment"]
        self.assertIn("does not predict an acceptable benefit", alignment["accepted_premise"])
        rows = {row["version"]: row for row in alignment["counterexamples_openai_host"]}
        self.assertEqual({"v9", "v10", "v11", "v12"}, set(rows))
        self.assertEqual([0, 0, 0], rows["v9"]["potential_old_units_upper_bound"])
        self.assertEqual("0/3 vs 3/3", rows["v10"]["plugin_over_baseline_strict"])
        self.assertEqual("2/3 vs 3/3", rows["v11"]["plugin_over_baseline_strict"])
        self.assertEqual(3, rows["v12"]["guard_filtered_safe_candidates"])
        self.assertEqual("clean at every boundary", rows["v12"]["invariant_gate"])
        rules = alignment["rules"]
        self.assertEqual("forbidden", rules["quality_prediction_from_candidates"])
        self.assertIn("never re-judge quality", rules["mechanism_side_channel"])
        self.assertIn("sequential gates", rules["payment_path"])
        self.assertGreaterEqual(len(alignment["guard_checks"]), 5)

    def test_the_invariant_gate_has_five_columns_and_a_stop_rule(self):
        gate = self.draft["prescreen_alignment"]["invariant_gate"]
        self.assertEqual(
            [
                "task constraints",
                "current evidence",
                "tool-group hash",
                "restore and whole-payload fallback accounting",
                "source location",
            ],
            gate["per_boundary_columns"],
        )
        self.assertIn("requires a mechanism fix", gate["stop_rule"])
        self.assertIn("never upgrades a quality verdict", gate["stop_rule"])
        self.assertTrue(Path(ROOT / gate["reusable_implementation"]).is_file())
        self.assertTrue(Path(ROOT / gate["worked_example"]).is_file())

    def test_the_acquisition_run_is_authorised_but_not_executed_with_evidence(self):
        acquisition = self.draft["payload_acquisition_run"]
        self.assertTrue(acquisition["authorised_this_round"])
        self.assertFalse(acquisition["executed"])
        shape = acquisition["bounded_shape_if_executed"]
        self.assertEqual(["none"], shape["arms"])
        self.assertEqual(12, shape["hard_max_api_requests"])
        self.assertTrue(shape["must_not_be_reported_as_saving_evidence"])
        self.assertEqual(3, len(acquisition["blocking_evidence"]))
        joined = " ".join(acquisition["blocking_evidence"])
        self.assertIn("raw.githubusercontent.com resolves to 0.0.0.0", joined)
        self.assertIn("git is forbidden", joined)
        self.assertIn("no public instance", joined)

    def test_the_protocol_carries_the_prescreen_rules(self):
        for phrase in (
            "不能**用\"可省略单位数量\"论证收益",
            "守卫后安全候选",
            "不变量门",
            "不得改判质量",
            "筛除",
            "禁止**把它写进任何收益预期",
        ):
            self.assertIn(phrase, self.text, phrase)
        self.assertIn("7.1 本轮为什么**没有**执行有界", self.text)

    def test_the_hard_list_is_complete_and_the_protocol_forbids_stage_c(self):
        required = self.draft["required_before_freeze"]
        self.assertGreaterEqual(len(required), 6)
        joined = " ".join(required)
        self.assertIn("Stage 0", joined)
        self.assertIn("Stage A", joined)
        self.assertIn("replay gate", joined)
        self.assertIn("record the freeze file's own SHA256", joined)
        self.assertIn("不得运行 Stage C", self.text)
        self.assertIn("本轮零付费", self.text)
        self.assertIn('写成"质量不降"', self.text)
        self.assertIn("语义轨通过", self.text)
        self.assertIn("不追溯", self.text)


if __name__ == "__main__":
    unittest.main()
