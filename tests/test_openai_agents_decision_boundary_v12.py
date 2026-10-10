"""Zero-API gate for the v12 decision-boundary analysis.

Pins the two things the analysis claims, so neither can silently rot:

* **the comparison is built from the v12 batch's own recorded payload** - the rebuilt
  payloads must reproduce the recorded structure, the recorded per-output SHA256/chars of
  the baseline arm, and the plugin arm's recomputed pointer text (which is fully determined
  by the newest full copy's call id and the source digest);
* **the strict-format failure is model-side wording** - the plugin answer contains every
  required term, matches the frozen regex end to end, and fails only the disclosed
  160-character bound.

The negative control at the end is what makes the presence check meaningful: replacing a
*unique* evidence item with a pointer must be reported as a lost fact.
"""

from __future__ import annotations

import hashlib
import unittest

from experiments.runners import openai_agents_decision_boundary_v12 as analysis
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import deduplicate


class DecisionBoundaryV12(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = analysis.analyse()

    # -- the comparison is built on recorded real payloads -------------------

    def test_every_boundary_reproduces_the_recorded_payload(self):
        self.assertEqual([], self.report["problems"], self.report["problems"])
        self.assertTrue(self.report["all_checks_ok"])
        self.assertEqual(7, len(self.report["boundaries"]))
        for row in self.report["boundaries"]:
            self.assertTrue(row["structure_matches_record"], row)
            self.assertTrue(row["baseline_outputs_match_record"], row)
            self.assertTrue(row["plugin_outputs_match_record"], row)
            self.assertTrue(row["pairing_ok"], row)
            self.assertTrue(row["message_units_verbatim_both_sides"], row)
        # the recorded batch produced exactly the same structure the fixture rebuilds
        first = self.report["boundaries"][0]
        last = self.report["boundaries"][-1]
        self.assertEqual((0, 0), (first["tool_calls"], first["tool_outputs"]))
        self.assertEqual((6, 6), (last["tool_calls"], last["tool_outputs"]))
        self.assertEqual(3, last["message_units"])

    def test_byte_deltas_match_the_record_and_the_v12_projection(self):
        deltas = [row["recorded_byte_delta"] for row in self.report["boundaries"]]
        self.assertEqual([0, 0, 0, 0, 772, 5613, 6267], deltas)
        self.assertEqual(12652, self.report["recorded_total_byte_delta"])
        # the rebuilt payloads reproduce the frozen projection byte for byte
        self.assertEqual(
            self.report["projected_byte_delta_v12_gate"], self.report["rebuilt_total_byte_delta"]
        )

    def test_only_exact_duplicates_are_replaced(self):
        expected = {0: [], 1: [], 2: [], 3: [], 4: [0], 5: [0, 1], 6: [0, 1, 2]}
        for row in self.report["boundaries"]:
            self.assertEqual(expected[row["boundary"]], row["replaced_output_positions"], row)

    def test_the_pointer_text_is_recomputable_from_the_record(self):
        arms = analysis.recorded_arms()
        payload = replay.realistic_boundaries(0)[4]
        plugin_manifest = arms["pruner_v1"][4]["output_manifest"]
        baseline_manifest = arms["none"][4]["output_manifest"]
        # the plugin's replaced item is exactly the frozen pointer text, naming the plugin
        # arm's own newest copy and the source digest both arms share
        pointer = analysis.pointer_text(
            str(plugin_manifest[3]["call_id"]), str(baseline_manifest[3]["output_sha256"])
        )
        self.assertEqual(157, len(pointer))
        self.assertEqual(
            str(plugin_manifest[0]["output_sha256"]),
            hashlib.sha256(pointer.encode()).hexdigest(),
        )
        # and the newest copy it points at is still present, byte-identical to the baseline
        self.assertEqual(
            str(baseline_manifest[3]["output_sha256"]),
            str(plugin_manifest[3]["output_sha256"]),
        )
        # boundary 4 carries 3 message units and 4 call/output pairs, in one contiguous run
        self.assertEqual(11, len(payload))
        self.assertEqual(3, len([item for item in payload if item.get("type") is None]))
        self.assertEqual(4, len([item for item in payload if item.get("type") == "function_call"]))

    # -- the mechanical verdict ---------------------------------------------

    def test_no_visible_fact_loses_presence_on_any_boundary(self):
        for row in self.report["boundaries"]:
            self.assertEqual([], row["lost_presence"], row)
            terms = {
                name: value
                for name, value in row["component_occurrences"].items()
                if name.startswith("term:")
            }
            for name, value in terms.items():
                if value["none"] > 0:
                    self.assertGreaterEqual(value["plugin"], 1, (row["boundary"], name))
        # the counts do drop at the boundaries that replaced a duplicate copy: reported,
        # not hidden
        self.assertEqual(6, self.report["boundaries"][-1]["component_occurrences"]["term:existing_annotations"]["none"])
        self.assertEqual(3, self.report["boundaries"][-1]["component_occurrences"]["term:existing_annotations"]["plugin"])

    def test_plugin_fails_only_the_length_bound_and_keeps_every_fact(self):
        plugin = self.report["quality"]["pruner_v1"]
        self.assertTrue(plugin["required_terms_present"])
        self.assertTrue(plugin["frozen_regex_fullmatch"])
        self.assertTrue(plugin["is_at_instruction_contract"])
        self.assertEqual(
            {"starts_with_RESULT": True, "single_line": True, "at_most_160_chars": False},
            plugin["format_conditions"],
        )
        self.assertGreater(plugin["answer_chars"], analysis.MAX_ANSWER_CHARS)
        self.assertFalse(plugin["recorded_success"])
        self.assertEqual("model-side-wording", self.report["verdict"]["call"])

    def test_the_reimplemented_rule_reproduces_every_recorded_flag(self):
        for method in analysis.METHODS:
            self.assertTrue(
                self.report["quality"][method]["reproduces_recorded_flags"], method
            )
        self.assertTrue(self.report["quality"]["none"]["recorded_success"])
        self.assertTrue(self.report["quality"]["native_summary"]["recorded_success"])
        self.assertFalse(self.report["quality"]["pruner_v1"]["recorded_success"])

    def test_the_format_constraint_is_not_a_model_input_item(self):
        """The rule lives in the agent instructions, which no item filter can touch."""
        payloads = replay.realistic_boundaries(0)
        for payload in payloads:
            text = analysis.visible_text(payload)
            self.assertNotIn("cause=", text)
            self.assertNotIn("at most 160 characters", text)
        self.assertIn("cause=<decision>", analysis.INSTRUCTION_CONTRACT)

    # -- the invariant gate (prescreen section 2, item 4) -------------------

    def test_every_boundary_keeps_the_task_constraints_and_the_current_evidence(self):
        for row in self.report["boundaries"]:
            inv = row["invariants"]
            self.assertTrue(inv["task_constraints"]["identical"], row["boundary"])
            self.assertEqual([], inv["task_constraints"]["labels_lost"], row["boundary"])
            self.assertEqual(
                [], inv["current_evidence"]["sources_without_full_copy_plugin"], row["boundary"]
            )
            self.assertGreaterEqual(
                inv["current_evidence"]["full_copies_plugin"],
                inv["current_evidence"]["distinct_sources"],
                row["boundary"],
            )
            # registered literal counts may drop when an older duplicate becomes a pointer,
            # but presence must never drop to zero (checked per label, per boundary)
            for label, count in inv["task_constraints"]["counts_none"].items():
                if count > 0:
                    self.assertGreaterEqual(
                        inv["task_constraints"]["counts_plugin"].get(label, 0), 1, label
                    )

    def test_the_tool_group_hash_is_identical_on_both_sides(self):
        for row in self.report["boundaries"]:
            group = row["invariants"]["tool_group"]
            self.assertTrue(group["identical"], row["boundary"])
            self.assertEqual(group["hash_none"], group["hash_plugin"], row["boundary"])
            # the hash is id-free: no arm-local call id may enter the structure
            for kind, label in group["structure_none"]:
                self.assertIn(kind, {"message", "function_call", "function_call_output"})
                self.assertNotIn("call_00", label)
        self.assertEqual([], self.report["invariant_gate"]["tool_group_hash_changes"])

    def test_restore_and_whole_payload_fallbacks_are_fully_accounted(self):
        for row in self.report["boundaries"]:
            restore = row["invariants"]["restore_and_fallback"]["plugin"]
            self.assertEqual("", restore["fallback_reason"], row["boundary"])
            self.assertEqual(0, restore["task_anchor_restore_failures"], row["boundary"])
            self.assertEqual(0, restore["task_restore_fallbacks"], row["boundary"])
            self.assertEqual(0, restore["budget_fallbacks"], row["boundary"])
            self.assertTrue(restore["pointer_completeness"], row["boundary"])
            self.assertTrue(restore["pointer_coverage"], row["boundary"])
            self.assertEqual([], row["invariants"]["unaccounted_fallback"], row["boundary"])
        gate = self.report["invariant_gate"]
        self.assertTrue(gate["all_invariants_hold"])
        self.assertEqual([], gate["lost_constraints"])
        self.assertEqual([], gate["lost_evidence"])
        self.assertEqual([], gate["unaccounted_fallbacks"])
        self.assertEqual([], gate["broken_source_pointers"])
        ledger = gate["recorded_restore_ledger"]
        self.assertEqual([0] * 7, ledger["restore_failures_by_model_call"])
        self.assertEqual({}, ledger["fallback_reasons"])
        self.assertEqual(0, ledger["unmatched_call_count"])
        self.assertTrue(ledger["pairing_integrity"])
        self.assertTrue(ledger["structure_safe"])
        self.assertTrue(ledger["constraint_preserved"])

    def test_source_location_resolves_for_every_pointer(self):
        for row in self.report["boundaries"]:
            self.assertTrue(row["invariants"]["source_location"]["pointer_resolves"], row["boundary"])
        self.assertEqual([], self.report["invariant_gate"]["broken_source_pointers"])

    def test_a_lost_full_copy_fails_the_invariant_gate_not_the_quality_verdict(self):
        """Negative control: losing current evidence must stop the gate mechanically."""
        payload = replay.realistic_boundaries(0)[6]
        reduced, _ = deduplicate(payload)
        outputs = [
            position
            for position, item in enumerate(payload)
            if item.get("type") == "function_call_output"
        ]
        damaged = [dict(item) for item in reduced]
        # overwrite the only surviving full copy of the aggregation view
        damaged[outputs[4]]["output"] = analysis.pointer_text("call_elsewhere", "0" * 64)
        distinct = analysis.unique_output_digests(payload)
        surviving = set(analysis.full_copy_digests(damaged))
        self.assertTrue(distinct - surviving)
        # the tool-group hash is unaffected: this loss is an evidence loss, not a grouping loss
        self.assertEqual(analysis.tool_group_hash(payload), analysis.tool_group_hash(damaged))

    def test_the_safe_candidate_count_is_reported_next_to_the_quality_outcome(self):
        safe = self.report["safe_candidate_accounting"]
        self.assertEqual(3, safe["guard_filtered_safe_candidates"])
        self.assertEqual(3, safe["potential_old_units_upper_bound"])
        self.assertEqual(6, safe["replacement_events_across_boundaries"])
        self.assertEqual(14.63, safe["paired_complete_total_saving_percent"])
        self.assertEqual("0/1 vs baseline 1/1", safe["strict_quality_outcome"])
        self.assertIn("counterexample", safe["interpretation"])
        self.assertGreater(len(safe["guard_checks_applied"]), 3)

    # -- negative control ---------------------------------------------------

    def test_replacing_a_unique_evidence_item_is_reported_as_a_lost_fact(self):
        """The fail-closed half: dropping unique evidence must not pass silently.

        Dropping a *unique* copy is exactly what the real mechanism never does, so this is
        the control that keeps the presence check from being a tautology.  Verified at
        boundary 6: every ``subquery`` occurrence lives in the aggregation view, and the
        newest full copy of that view is the only one that survives when the older
        duplicate has already become a pointer.
        """
        payload = replay.realistic_boundaries(0)[6]
        reduced, _ = deduplicate(payload)
        # the real reduction keeps presence everywhere ...
        self.assertEqual([], analysis.lost_presence(payload, reduced))
        outputs = [
            position
            for position, item in enumerate(payload)
            if item.get("type") == "function_call_output"
        ]
        # ... because every unique view keeps one full copy: after the real reduction the
        # newest aggregation copy (item 12) is the only place these two facts are visible.
        unique_newest_aggregation = outputs[4]
        self.assertEqual("function_call_output", reduced[unique_newest_aggregation]["type"])
        self.assertIn("GROUP BY", reduced[unique_newest_aggregation]["output"])
        damaged = [dict(item) for item in reduced]
        damaged[unique_newest_aggregation]["output"] = analysis.pointer_text(
            "call_elsewhere", "0" * 64
        )
        lost = analysis.lost_presence(payload, damaged)
        self.assertEqual(["term:existing_annotations", "term:subquery"], lost)
        # a reduction that keeps only pointers loses the same facts and nothing more:
        # `referenced` survives because the issue statement itself carries that word.
        all_pointers = [dict(item) for item in reduced]
        for position in outputs:
            all_pointers[position]["output"] = analysis.pointer_text(
                "call_elsewhere", "0" * 64
            )
        self.assertEqual(
            ["term:existing_annotations", "term:subquery"],
            analysis.lost_presence(payload, all_pointers),
        )
        self.assertEqual(
            1,
            analysis.visible_counts(all_pointers)["term:referenced"],
        )

    def test_a_wrong_pointer_target_digest_is_not_accepted(self):
        """The recorded pointer names a digest that really is in the payload."""
        arms = analysis.recorded_arms()
        baseline_manifest = arms["none"][6]["output_manifest"]
        plugin_manifest = arms["pruner_v1"][6]["output_manifest"]
        digests = {str(record["output_sha256"]) for record in baseline_manifest}
        for position in (0, 1, 2):
            pointer = analysis.pointer_text(
                str(plugin_manifest[3 + position]["call_id"]),
                str(baseline_manifest[3 + position]["output_sha256"]),
            )
            self.assertIn(str(baseline_manifest[3 + position]["output_sha256"]), digests)
            self.assertEqual(
                str(plugin_manifest[position]["output_sha256"]),
                hashlib.sha256(pointer.encode()).hexdigest(),
            )
        # a pointer built from the wrong call id does not reproduce the record
        wrong = analysis.pointer_text(
            str(plugin_manifest[0]["call_id"]), str(baseline_manifest[3]["output_sha256"])
        )
        self.assertNotEqual(
            str(plugin_manifest[0]["output_sha256"]),
            hashlib.sha256(wrong.encode()).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
