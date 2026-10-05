"""Zero-API gate for v11: the elision-ratio ladder on the recorded real payloads.

Establishes, without any provider request:

* **the fixture is the recorded payload structure** (v10's replay fixture): rebuilt boundaries
  must match the paid v9 batch's recorded item/message/output counts and contiguity;
* **the ladder is the frozen one** and it is the single variable: ``M`` from the environment
  must be on the ladder, the protection window must follow ``M`` monotonically, and the
  reduction, guards, classification and fallback semantics must still be the frozen objects;
* **the three projections** are recorded, with ``M = 6`` projecting exactly zero and at least
  one larger ``M`` projecting positive, and the selection equal to the largest positive ``M``;
* **the paid-run rule**: a paid batch may exist only when a positive ``M`` was selected, and
  its manifest must record the freeze file's own SHA256.
"""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path

from experiments.runners import openai_agents_long_baseline_boundary_v11 as policy
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners import openai_agents_elision_ladder_projection_v11 as ladder
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners import run_openai_agents_repo_diagnostic_v9 as v9
from experiments.runners import run_openai_agents_repo_diagnostic_v10 as v10
from experiments.runners import run_openai_agents_repo_diagnostic_v11 as v11
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
)
from experiments.runners.openai_agents_span_retention_v6 import SpanRetentionFilter

ROOT = Path(__file__).resolve().parents[1]
PAID_BATCH_ID = "openai-repo-diagnostic-v11-elision-ratio-boundary"
FREEZE_PATH = (
    ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V11_ELISION_RATIO_FREEZE.json"
)
LADDER_PROJECTION = (
    ROOT / "integrations/openai_agents/V11_ELISION_LADDER_PROJECTION.json"
)


class ElisionRatioV11Gate(unittest.TestCase):
    # -- the fixture is the recorded structure -----------------------------

    def test_fixture_reproduces_the_recorded_payload_structure(self):
        report = replay.fixture_report()
        self.assertEqual([], report["mismatches"], report["mismatches"])
        self.assertTrue(report["ok"])
        self.assertTrue(report["tool_items_contiguous"])
        self.assertEqual([0, 1, 1, 1, 1, 1, 1], report["recorded_turn_count_by_boundary"])
        for check in report["checks"]:
            self.assertTrue(check["ok"], check)

    # -- the ladder is the single variable ---------------------------------

    def test_the_ladder_is_frozen_and_the_environment_rejects_other_values(self):
        self.assertEqual((6, 4, 2), policy.ELISION_LADDER)
        self.assertEqual(6, policy.DEFAULT_ELISION_M)
        for value in policy.ELISION_LADDER:
            os.environ[policy.ELISION_M_ENV] = str(value)
            try:
                self.assertEqual(value, policy.current_elision_m())
            finally:
                os.environ.pop(policy.ELISION_M_ENV, None)
        os.environ[policy.ELISION_M_ENV] = "3"
        try:
            with self.assertRaises(ValueError):
                policy.current_elision_m()
        finally:
            os.environ.pop(policy.ELISION_M_ENV, None)

    def test_protection_window_follows_m_monotonically(self):
        boundaries = replay.realistic_boundaries(0)
        last = boundaries[-1]
        windows = {}
        for m in policy.ELISION_LADDER:
            windows[m] = policy.elidable_call_indices(last, m)
        self.assertEqual(set(), windows[6])
        self.assertEqual({3, 4, 5, 6}, windows[4])
        self.assertEqual({3, 4, 5, 6, 7, 8, 9, 10}, windows[2])
        self.assertTrue(windows[2] > windows[4] > windows[6])
        # protecting more calls always leaves a subset elidable
        for smaller, larger in ((2, 4), (4, 6)):
            self.assertTrue(windows[larger] < windows[smaller])

    def test_only_the_ladder_value_changed(self):
        inherited = policy.inherited_method_objects()
        self.assertIs(SpanRetentionFilter._elide, inherited["_elide"])
        self.assertIs(SpanRetentionFilter._structural_violation, inherited["_structural_violation"])
        self.assertIs(SelectiveRetentionFilter._carrier_loss, inherited["_carrier_loss"])
        self.assertIs(SpanRetentionFilter._group_change, inherited["_group_change"])
        # v11 inherits v10's window computation; it adds no reduction of its own.
        self.assertNotIn("_elide", policy.ElisionRatioFilter.__dict__)
        self.assertNotIn("_structural_violation", policy.ElisionRatioFilter.__dict__)
        self.assertNotIn("_filter", policy.ElisionRatioFilter.__dict__)
        self.assertIs(policy.ElisionRatioFilter._filter, policy.v10.ToolCallRecencyFilter._filter)
        self.assertTrue(issubclass(policy.ElisionRatioFilter, policy.v10.ToolCallRecencyFilter))
        self.assertIsNot(v11.build_retentive_filter, v10.build_retentive_filter)

    # -- the three projections ---------------------------------------------

    def test_ladder_projection_records_all_three_steps(self):
        self.assertTrue(LADDER_PROJECTION.is_file(), "run the ladder projection first")
        artifact = json.loads(LADDER_PROJECTION.read_text(encoding="utf-8"))
        self.assertEqual(list(policy.ELISION_LADDER), list(artifact["ladder"]))
        for m in policy.ELISION_LADDER:
            report = artifact["projections"][str(m)]
            self.assertTrue(report["fixture_ok"], m)
            self.assertEqual(0, report["task_anchor_restore_failures"], m)
            self.assertEqual(0, report["budget_fallbacks"], m)
            self.assertEqual([], report["literal_regressions"], m)
        self.assertEqual(0.0, artifact["projections"]["6"]["byte_saving_rate"])
        positives = [
            m
            for m in policy.ELISION_LADDER
            if artifact["projections"][str(m)]["byte_saving_rate"] > 0
        ]
        self.assertTrue(positives, "the ladder projects no positive step at all")
        self.assertEqual(max(positives), artifact["selected_M"])

    def test_ladder_projection_is_ordered_and_guarded(self):
        artifact = json.loads(LADDER_PROJECTION.read_text(encoding="utf-8"))
        rates = {
            m: artifact["projections"][str(m)]["byte_saving_rate"]
            for m in policy.ELISION_LADDER
        }
        # a tighter window (smaller M) never saves less
        self.assertLessEqual(rates[6], rates[4])
        self.assertLessEqual(rates[4], rates[2])
        selected = artifact["selected_M"]
        self.assertIn(selected, policy.ELISION_LADDER)
        self.assertGreater(rates[selected], 0)
        self.assertTrue(
            artifact["projections"][str(selected)]["elidable_indices_non_empty"]
        )

    # -- the paid-run rule -------------------------------------------------

    def test_paid_batch_only_after_a_positive_selection_and_records_the_freeze_hash(self):
        artifact = json.loads(LADDER_PROJECTION.read_text(encoding="utf-8"))
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if not paid_dir.is_dir():
            self.skipTest("no paid v11 batch")
        self.assertIsNotNone(artifact["selected_M"])
        manifest = json.loads((paid_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("elision_ratio_boundary_v11", manifest["mechanism"]["name"])
        # ``selected_M`` is this version's authoritative statement. The inherited
        # ``recent_tool_calls_kept_verbatim`` key is re-declared by the v10 policy block that
        # the v11 runner spreads afterwards, so it keeps v10's frozen constant (1) and is not
        # the v11 selection.
        self.assertEqual(
            int(artifact["selected_M"]),
            int(manifest["mechanism"]["selected_M"]),
        )
        self.assertEqual(
            list(policy.ELISION_LADDER), list(manifest["mechanism"]["elision_ladder"])
        )
        recorded = manifest.get("freeze_sha256")
        self.assertTrue(recorded, "the paid batch must record the freeze file's SHA256")
        if FREEZE_PATH.is_file():
            actual = hashlib.sha256(FREEZE_PATH.read_bytes()).hexdigest()
            self.assertEqual(actual, recorded, "the freeze file changed after the batch")

    def test_audit_passes_when_the_batch_exists(self):
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if not paid_dir.is_dir() or not FREEZE_PATH.is_file():
            self.skipTest("no paid v11 batch or no freeze")
        from experiments.audits import audit_openai_agents_repo_diagnostic_v11 as independent

        result = independent.audit(paid_dir, FREEZE_PATH, PAID_BATCH_ID)
        self.assertEqual([], result["issues"], result["issues"])
        self.assertTrue(result["complete"])
        self.assertIn(result["verdict"], {"valid-saving", "not-yet-valid"})

    def test_freeze_is_written_once(self):
        """A frozen file is never rewritten; the runner refuses to overwrite it."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_v11_freeze_builder",
            ROOT / ".tooling/build_v11_elision_ratio_freeze.py",
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        self.assertTrue(module.OUT.exists(), "the freeze must be built before the batch")
        source = (ROOT / ".tooling/build_v11_elision_ratio_freeze.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("already exists; a frozen file is never rewritten", source)


class AmendmentAwareFreezeHashes(unittest.TestCase):
    """A declared post-freeze change is accepted; an undeclared one never is.

    The four freezes of the v7-v11 family pin the applicability-boundary document and the v7
    and v9 audits, and every one of those freeze files is immutable. The v11 closing version of
    the document (and the audits that learned to consult the amendment file) are recorded in
    ``REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json``; these tests pin the two halves of
    that contract: exactly the recorded content passes, and nothing else does.
    """

    DOC = "integrations/openai_agents/PLUGIN_APPLICABILITY_BOUNDARY.md"
    AMENDMENT = (
        ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V11_FREEZE_AMENDMENT_20261004.json"
    )

    def _freeze(self, wanted: str) -> dict:
        return {"source_sha256": {self.DOC: wanted}, "public_input_sha256": {}}

    def test_the_recorded_content_of_every_pinned_artifact_is_accepted(self):
        from experiments.audits import amendment_hashes

        table = amendment_hashes.amended_digests()
        self.assertTrue(table, "the amendment file records no digest at all")
        for relative, digests in table.items():
            path = ROOT / relative
            if not path.is_file():
                continue
            current = hashlib.sha256(path.read_bytes()).hexdigest()
            if current in digests:
                self.assertTrue(
                    amendment_hashes.is_recorded_amendment(relative, current, table), relative
                )
        # the document and the two audits this round edited are all covered
        for relative in (
            self.DOC,
            "experiments/audits/audit_openai_agents_repo_diagnostic_v7.py",
            "experiments/audits/audit_openai_agents_repo_diagnostic_v9.py",
        ):
            current = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertTrue(
                amendment_hashes.is_recorded_amendment(relative, current, table), relative
            )

    def test_an_undeclared_digest_is_still_a_mismatch(self):
        from experiments.audits import amendment_hashes

        table = amendment_hashes.amended_digests()
        frozen = "5b50e4596af21331545beb5d535153832dd1a7aa08a60533783fd064131e7bc2"
        self.assertNotEqual(frozen, hashlib.sha256((ROOT / self.DOC).read_bytes()).hexdigest())
        self.assertFalse(amendment_hashes.is_recorded_amendment(self.DOC, frozen, table))
        self.assertFalse(amendment_hashes.is_recorded_amendment(self.DOC, "0" * 64, table))
        # a file the amendment says nothing about is never suppressed
        self.assertFalse(
            amendment_hashes.is_recorded_amendment("tests/test_openai_agents_runner.py", "0" * 64, table)
        )
        issues = amendment_hashes.freeze_hash_issues(self._freeze(frozen), lambda _path: "0" * 64)
        self.assertEqual([f"hash mismatch: {self.DOC}"], issues)

    def test_a_missing_amendment_suppresses_nothing(self):
        from experiments.audits import amendment_hashes

        missing = ROOT / ".tooling/tmp/definitely-not-an-amendment.json"
        self.assertFalse(missing.exists())
        self.assertEqual({}, amendment_hashes.amended_digests(missing))
        current = hashlib.sha256((ROOT / self.DOC).read_bytes()).hexdigest()
        frozen = "5b50e4596af21331545beb5d535153832dd1a7aa08a60533783fd064131e7bc2"
        issues = amendment_hashes.freeze_hash_issues(
            self._freeze(frozen), lambda _path: current, missing
        )
        self.assertEqual([f"hash mismatch: {self.DOC}"], issues)


if __name__ == "__main__":
    unittest.main()
