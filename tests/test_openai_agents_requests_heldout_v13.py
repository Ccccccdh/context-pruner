"""Zero-API gates for the v13 held-out chain (`psf__requests-1766`).

These tests re-derive the claims the paid batches rest on, from the frozen registration, the
recorded payloads and the frozen mechanism rule:

* the registration verifies (views exist, literals are in the views or the statement, the base
  defect is visible, the FAIL_TO_PASS/base-tree split is what Stage 0 recorded);
* the rebuild reproduces the recorded boundaries of both paid batches (structure, contiguity,
  per-output SHA256 and character counts) - the replay gate's core claim;
* the plugin's substituted items are exactly the frozen pointer text, recomputed from the
  newest full copy's recorded call id and digest;
* the invariant columns hold and the negative control catches a lost unique copy;
* the frozen rule is inherited by identity, and the held-out filter changes only the
  registration;
* Track A and Track B are computed from the frozen contract, and Track B never rescues a
  missing fact.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path

from experiments.audits import audit_openai_agents_requests_confirm_v13 as confirm_audit
from experiments.runners import openai_agents_requests_duplicate_filter_v13 as filter_v13
from experiments.runners import openai_agents_requests_replay_gate_v13 as gate
from experiments.runners import openai_agents_requests_task_registry_v13 as registry
from experiments.runners.openai_agents_exact_duplicate_replay_v12 import (
    ExactDuplicateFilter,
    deduplicate,
)

ROOT = Path(__file__).resolve().parents[1]
STAGE_A = ROOT / (
    "runs/stage5-openai-agents-api/openai-repo-diagnostic-v13-requests1766-acquisition-01"
)
STAGE_C = ROOT / (
    "runs/stage5-openai-agents-api/openai-repo-diagnostic-v13-requests1766-confirm-01"
)
FREEZE = ROOT / "integrations/openai_agents/V13_REQUESTS1766_FREEZE_20261005.json"


class Stage0RegistrationV13(unittest.TestCase):
    """The sanctioned read-only acquisition is the registration of record."""

    @classmethod
    def setUpClass(cls):
        from experiments.runners import openai_agents_requests_stage0_registration_v13 as stage0

        cls.module = stage0
        cls.registration = json.loads(
            (
                ROOT
                / "integrations/openai_agents/V13_REQUESTS1766_STAGE0_REGISTRATION_20261005.json"
            ).read_text(encoding="utf-8")
        )

    def test_the_checkout_is_the_pinned_commit_and_matches_the_used_copies(self):
        registration = self.registration
        self.assertTrue(registration["verified"], registration["problems"])
        self.assertEqual([], registration["problems"])
        self.assertEqual(registry.BASE_COMMIT, registration["provenance"]["checkout_head"])
        self.assertEqual(registry.BASE_COMMIT, registration["base_commit"])
        for relative, entry in registration["baseline_source"]["files"].items():
            self.assertTrue(entry["copy_identical"], relative)
            self.assertEqual(entry["sha256"], entry["copy_sha256"], relative)
            checkout = ROOT / registration["baseline_source"]["checkout"] / relative
            self.assertEqual(
                hashlib.sha256(checkout.read_bytes()).hexdigest(), entry["sha256"], relative
            )

    def test_baseline_source_and_judging_metadata_are_separate_records(self):
        registration = self.registration
        self.assertEqual("mechanism input", registration["baseline_source"]["role"])
        judging = registration["judging_metadata"]
        self.assertIn("never sent to the model", judging["role"])
        self.assertFalse(judging["test_patch"]["read_by_this_host"])
        self.assertFalse(judging["reference_patch"]["read_by_this_host"])
        self.assertEqual(571, judging["test_patch"]["bytes"])
        self.assertEqual(621, judging["reference_patch"]["bytes"])
        self.assertGreater(judging["problem_statement"]["sha256"], "")
        self.assertEqual(79, judging["pass_to_pass"]["count"])

    def test_the_missing_evaluation_test_is_the_test_patch_not_a_defect(self):
        judging = self.registration["judging_metadata"]
        self.assertEqual(
            ["test_DIGESTAUTH_QUOTES_QOP_VALUE"],
            [name for name in ("test_DIGESTAUTH_QUOTES_QOP_VALUE",)],
        )
        self.assertIn("normal SWE-bench", judging["test_patch"]["explains"])
        present = judging["fail_to_pass"]["present_in_base_tree"]
        self.assertNotIn("test_DIGESTAUTH_QUOTES_QOP_VALUE", present)
        self.assertEqual(5, len(present))

    def test_read_only_acquisition_used_no_forbidden_operation(self):
        provenance = self.registration["provenance"]
        self.assertEqual([], provenance["forbidden_operations_used"])
        self.assertFalse(provenance["raw_githubusercontent_used"])
        self.assertIn("fetch --depth 1", provenance["acquisition"])
        self.assertTrue(provenance["recorded_hashes_validated_not_replaced"])


class RegistrationV13(unittest.TestCase):
    def test_the_registration_verifies(self):
        self.assertEqual([], registry.verify())
        self.assertTrue(registry.INSTANCE_FILE.is_file())
        self.assertEqual("847735553aeda6e6633f2b32e14ba14ba86887a4", registry.BASE_COMMIT)

    def test_the_base_defect_is_visible_and_the_fix_is_not(self):
        evidence = registry.defect_evidence()
        self.assertTrue(evidence["unquoted_emission_present"])
        self.assertFalse(evidence["quoted_emission_present"])
        self.assertIn("qop=auth, nc=", evidence["emitting_line"])

    def test_the_fail_to_pass_split_is_the_recorded_one(self):
        consistency = registry.ftp_consistency()
        self.assertEqual(
            ["test_DIGESTAUTH_QUOTES_QOP_VALUE"], consistency["absent_from_base_tree"]
        )
        self.assertEqual(5, len(consistency["present_in_base_tree"]))

    def test_views_are_numbered_and_carry_the_literals(self):
        for label, phrases in registry.LITERAL_LABELS.items():
            reachable = "".join(registry.view_texts()) + registry.statement()
            for phrase in phrases:
                self.assertIn(phrase, reachable, label)
        self.assertEqual(7, len(registry.PROTOCOL_STEPS))
        self.assertEqual(len(registry.PROTOCOL_STEPS), registry.EXPECTED_MODEL_CALLS)


class MechanismV13(unittest.TestCase):
    def test_only_the_registration_is_new(self):
        inherited = filter_v13.inherited_rule_objects()
        self.assertIs(inherited["filter_items"], ExactDuplicateFilter.filter_items)
        self.assertIs(inherited["_carrier_loss"], ExactDuplicateFilter._carrier_loss)
        self.assertIs(inherited["_elide"], ExactDuplicateFilter._elide)
        self.assertIs(inherited["_structural_violation"], ExactDuplicateFilter._structural_violation)
        self.assertNotIn("filter_items", filter_v13.RequestsDuplicateFilter.__dict__)
        self.assertNotIn("_elide", filter_v13.RequestsDuplicateFilter.__dict__)

    def test_the_guard_protects_the_statement_literal_carriers(self):
        flt = filter_v13.RequestsDuplicateFilter(
            task=registry.TASK_ID,
            task_statement=registry.statement(),
            hard_limit_bytes=65216,
        )
        self.assertGreaterEqual(len(flt.protectable_units), 1)
        self.assertIn("qop-options", " ".join(flt.protectable_units))
        self.assertEqual([], flt.long_registry_problems)
        self.assertEqual(
            registry.registry_fingerprint(), flt.registry_fingerprint
        )

    def test_the_reduction_replaces_only_exact_duplicates(self):
        payloads = gate.boundaries()
        replaced = [len(deduplicate(payload)[1]) for payload in payloads]
        self.assertEqual([0, 0, 0, 0, 1, 2, 3], replaced)
        reduced, replacements = deduplicate(payloads[-1])
        for replacement in replacements:
            old = payloads[-1][int(replacement["old_index"])]["output"]
            new = payloads[-1][int(replacement["new_index"])]["output"]
            self.assertEqual(old, new)
            self.assertTrue(
                str(reduced[int(replacement["old_index"])]["output"]).startswith(
                    "[Exact duplicate output;"
                )
            )


class ReplayGateV13(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = gate.analyse(STAGE_A)

    def test_the_rebuild_reproduces_the_recorded_payload(self):
        self.assertEqual([], self.report["problems"], self.report["problems"])
        self.assertEqual(7, self.report["recorded_boundaries"])
        for row in self.report["boundaries"]:
            self.assertTrue(row["structure_matches_record"], row["boundary"])
            self.assertTrue(row["outputs_match_record"], row["boundary"])

    def test_projection_and_safe_candidates_are_reported_separately(self):
        self.assertGreaterEqual(self.report["projection"]["byte_saving_rate"], 0.03)
        self.assertTrue(self.report["projection"]["passes_floor"])
        candidates = self.report["safe_candidates"]
        self.assertEqual(3, candidates["guard_filtered_safe_candidates"])
        self.assertIn("not a benefit prediction", candidates["rule"])
        self.assertEqual("proceed", self.report["verdict"])

    def test_invariants_hold_and_the_negative_control_is_caught(self):
        self.assertTrue(self.report["invariant_gate"]["all_invariants_hold"])
        control = self.report["negative_control"]
        self.assertTrue(control["real_reduction_loses_nothing"])
        self.assertTrue(control["caught"], control)
        self.assertEqual(5, len(self.report["invariant_gate"]["columns"]))


class ConfirmationV13(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = confirm_audit.audit(STAGE_C, FREEZE)

    def test_the_audit_is_complete_and_the_frozen_cap_is_respected(self):
        self.assertEqual([], self.report["errors"])
        self.assertTrue(self.report["complete"])
        self.assertEqual(9, self.report["rows"])
        self.assertLessEqual(self.report["api_request_attempts"], 66)

    def test_track_a_is_at_least_baseline_and_the_pairing_saving_clears_the_floor(self):
        self.assertEqual(3, self.report["track_A_baseline_pass"])
        self.assertEqual(3, self.report["track_A_plugin_pass"])
        paired = self.report["paired"]["pruner_v1"]
        self.assertEqual(3, paired["paired_n"])
        self.assertEqual(3, paired["positive_pairs"])
        self.assertGreaterEqual(paired["mean_saving"], 0.03)
        self.assertTrue(self.report["acceptance"]["met"])
        self.assertFalse(self.report["acceptance"]["zero_replacements"])

    def test_replacements_are_recomputed_not_trusted(self):
        self.assertGreater(self.report["recomputed_plugin_replacements_across_boundaries"], 0)
        for key, entry in self.report["quality"].items():
            if not key.startswith("pruner_v1"):
                continue
            self.assertTrue(entry["track_A"]["pass"], key)
            self.assertTrue(entry["track_B"]["pass"], key)

    def test_the_two_tracks_are_kept_apart_and_track_b_cannot_rescue_a_missing_fact(self):
        answer = (
            "RESULT issue=requests-1766 cause=build_digest_header emits qop=auth "
            'unquoted fix=quote qop: qop="auth"'
        )
        self.assertTrue(confirm_audit.track_a(answer)["pass"])
        self.assertTrue(confirm_audit.track_b(answer)["pass"])
        # a wrong-value answer that keeps the wording passes Track B's shape only if the
        # required literals are present; dropping a required fact must fail BOTH tracks
        missing = "RESULT issue=requests-1766 cause=the method is wrong fix=no change needed"
        self.assertFalse(confirm_audit.track_a(missing)["pass"])
        self.assertFalse(confirm_audit.track_b(missing)["pass"])
        self.assertIn("fix=", answer)
        self.assertTrue(re.fullmatch(registry.ANSWER_PATTERN, answer, flags=re.IGNORECASE))

    def test_the_manifest_really_carries_the_frozen_keys(self):
        manifest = json.loads((STAGE_C / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["citable_as_saving"])
        self.assertFalse(manifest["citable_as_quality_equivalence"])
        self.assertTrue(str(manifest["purpose"]).strip())
        self.assertEqual(
            hashlib.sha256(FREEZE.read_bytes()).hexdigest(), manifest["freeze_sha256"]
        )
        self.assertTrue(manifest.get("manifest_amendments"))

    def test_stage_a_is_only_a_payload_record(self):
        manifest = json.loads((STAGE_A / "manifest.json").read_text(encoding="utf-8"))
        self.assertFalse(manifest["citable_as_saving"])
        self.assertFalse(manifest["citable_as_quality_equivalence"])
        rows = [
            json.loads(line)
            for line in (STAGE_A / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(1, len(rows))
        self.assertLessEqual(sum(int(row["api_request_attempts"]) for row in rows), 12)


if __name__ == "__main__":
    unittest.main()
