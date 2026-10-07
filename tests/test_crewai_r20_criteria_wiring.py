"""Zero-API checks that the r20 criteria are wired into the batch audit and stay split right.

The point of these tests is that the wiring is load-bearing: if somebody promotes a
report-only criterion onto the quality side, or lets the audit trust a recorded criteria
value instead of recomputing it, or freezes a cap below its own worst case, a test fails.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audits import audit_crewai_r20_orderfree_batch as batch_audit  # noqa: E402
from experiments.audits import criteria_crewai_r20_orderfree as criteria  # noqa: E402

PILOT_DRAFT = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01_DRAFT.json"
#: The report artifact is a different file from the freeze draft on purpose: one is the
#: reviewer-facing summary, the other is the thing that would be frozen.
PILOT_REPORT = ROOT / "integrations/crewai/R20_ORDERFREE_PILOT_FREEZE_DRAFT_20261006.json"
PREREG = ROOT / "integrations/crewai/R20_ORDERFREE_PREREGISTRATION_DRAFT_20261006.md"
FORBIDDEN = "四判据达成"
PROHIBITION_MARKERS = ("禁用", "禁止", "不得")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _walk(node: object):
    """Yield every nested dict/list so a claim cannot hide inside a nested string."""
    yield node
    if isinstance(node, dict):
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)


class CriteriaSideTest(unittest.TestCase):
    """Coverage is the only gate; the other two ids are report items."""

    def test_quality_side_is_exactly_coverage(self) -> None:
        self.assertEqual((criteria.CRIT_COVERAGE,), criteria.QUALITY_CRITERIA)

    def test_report_ids_are_the_other_two(self) -> None:
        self.assertEqual(
            (criteria.CRIT_REPEAT, criteria.CRIT_SAME_WORK),
            tuple(cid for cid in criteria.CRITERION_IDS if cid not in criteria.QUALITY_CRITERIA),
        )

    def test_repeat_and_same_work_are_never_gates(self) -> None:
        repeat = criteria.repeat_report(["a", "b"], ["a", "a", "b"])
        same_work = criteria.same_work_report(
            {"api_request_attempts": 4, "first_role_trace": ["a", "b", "c", "d"]},
            {"api_request_attempts": 4, "first_role_trace": ["a", "b", "c", "d"]},
        )
        for block in (repeat, same_work):
            self.assertIs(block["gate"], False)

    def test_coverage_is_at_least_once_not_exactly_once(self) -> None:
        """A repeat must not fail coverage: that is the whole point of demoting order."""
        block = criteria.coverage(["a", "b", "c", "d"], ["d", "c", "d", "b", "a"])
        self.assertTrue(block["satisfied"])

    def test_coverage_fails_on_a_source_never_read(self) -> None:
        block = criteria.coverage(["a", "b", "c", "d"], ["a", "b", "a"])
        self.assertFalse(block["satisfied"])
        self.assertEqual(["c", "d"], block["missing_sources"])


class BatchAuditTest(unittest.TestCase):
    """The audit recomputes the criteria and refuses a promoted report item."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = batch_audit.audit(batch_audit.DEFAULT_BATCH, batch_audit.DEFAULT_FREEZE, None)

    def test_audit_is_clean_on_the_measured_batch(self) -> None:
        self.assertEqual([], self.report["errors"])
        self.assertTrue(self.report["complete"])

    def test_all_three_criteria_are_present(self) -> None:
        for cid in criteria.CRITERION_IDS:
            self.assertIn(cid, self.report["criteria"])

    def test_coverage_satisfied_on_every_unit(self) -> None:
        coverage = self.report["criteria"][criteria.CRIT_COVERAGE]
        self.assertEqual(4, coverage["units"])
        self.assertEqual(4, coverage["satisfied"])
        self.assertTrue(coverage["all_satisfied"])

    def test_quality_verdict_uses_coverage_only(self) -> None:
        decision = self.report["quality_decision"]
        self.assertEqual(["CRIT_R20_COVERAGE"], decision["quality_criteria"])
        self.assertEqual("pass", decision["verdict"])

    def test_report_only_block_lists_both_report_ids(self) -> None:
        self.assertEqual(
            ["CRIT_R20_REPEAT_REPORT", "CRIT_R20_SAME_WORK_REPORT"],
            self.report["report_only"]["ids"],
        )

    def test_same_work_is_not_applicable_on_a_single_arm_batch(self) -> None:
        """Not applicable is not a pass: one arm has nothing to pair."""
        self.assertFalse(self.report["report_only"][criteria.CRIT_SAME_WORK]["applicable"])

    def test_promoting_a_report_item_fails_the_audit(self) -> None:
        original = criteria.QUALITY_CRITERIA
        criteria.QUALITY_CRITERIA = (criteria.CRIT_COVERAGE, criteria.CRIT_REPEAT)
        try:
            report = batch_audit.audit(
                batch_audit.DEFAULT_BATCH, batch_audit.DEFAULT_FREEZE, None
            )
        finally:
            criteria.QUALITY_CRITERIA = original
        self.assertIn("the quality side is not exactly CRIT_R20_COVERAGE", report["errors"])
        self.assertFalse(report["can_be_quoted_as_saving"])

    def test_guard_scan_sees_no_control_text(self) -> None:
        scan = self.report["guard_configuration"]["scan"]
        self.assertEqual(28, scan["attempts"])
        self.assertTrue(scan["baseline_zero_trigger"])
        self.assertEqual(0, scan["control_text_occurrences_total"])

    def test_absent_charged_fields_are_not_reported_as_zero(self) -> None:
        """A quantity nobody recorded must not read as 'nothing was charged'."""
        block = self.report["bound"]
        for field in block["charged_fields_not_recorded"]:
            self.assertEqual("not_recorded", block["charged_in_this_batch"][field])

    def test_empty_batch_is_never_a_pass(self) -> None:
        """Coverage on zero rows is vacuously true; the audit must not report a pass."""
        import shutil
        import tempfile

        tmp = Path(tempfile.mkdtemp())
        try:
            shutil.copy(batch_audit.DEFAULT_BATCH / "manifest.json", tmp / "manifest.json")
            (tmp / "results.jsonl").write_text("", encoding="utf-8")
            report = batch_audit.audit(tmp, batch_audit.DEFAULT_FREEZE, None)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("no rows: an empty batch is never a pass", report["errors"])
        self.assertEqual("fail", report["quality_decision"]["verdict"])
        self.assertFalse(report["can_be_quoted_as_saving"])
        self.assertFalse(report["criteria"][criteria.CRIT_COVERAGE]["all_satisfied"])

    def test_errors_rule_is_stated(self) -> None:
        self.assertIn("must not be quoted as one", self.report["errors_rule"])

    def test_audit_does_not_import_a_runner(self) -> None:
        source = Path(batch_audit.__file__).read_text(encoding="utf-8")
        self.assertNotIn("import crewai_", source)
        self.assertNotIn("from experiments.runners", source)


class FrozenConfigurationTest(unittest.TestCase):
    """The draft's own numbers must be internally consistent before anyone funds it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.draft = json.loads(PILOT_DRAFT.read_text(encoding="utf-8"))
        cls.report = json.loads(PILOT_REPORT.read_text(encoding="utf-8"))

    def test_guard_configuration_installs_no_order_or_repeat_guard(self) -> None:
        config = self.draft["guard_configuration"]
        self.assertEqual("not_installed", config["order_guard"])
        self.assertEqual("not_installed", config["repeat_guard"])
        self.assertEqual("none", config["guard_mode"])

    def test_view_byte_identity_every_task(self) -> None:
        assertions = self.report["guard_configuration"]["assertions"]
        self.assertTrue(assertions["views_byte_identical_every_task"])
        for task_id, block in assertions["view_byte_identity"].items():
            self.assertTrue(block["byte_identical"], task_id)
            self.assertEqual(block["view_sha256_without_guard"], block["view_sha256_with_guard"])
            self.assertFalse(block["guard_control_text_in_view"], task_id)

    def test_baseline_arm_is_trigger_free(self) -> None:
        assertions = self.report["guard_configuration"]["assertions"]
        self.assertTrue(assertions["baseline_zero_trigger"])
        self.assertTrue(assertions["control_text_never_in_view"])

    def test_cap_covers_its_own_worst_case(self) -> None:
        cap = self.draft["request_cap"]
        self.assertGreaterEqual(cap["declared_cap"], cap["worst_case_total"])
        self.assertGreaterEqual(cap["declared_cap"], cap["realistic_total"])
        self.assertTrue(cap["cap_covers_worst_case"])

    def test_on_cap_rule_keeps_the_partial_directory_under_a_new_id(self) -> None:
        rule = self.draft["request_cap"]["on_cap"]
        self.assertIn("--resume", rule)
        self.assertIn("NEW id", rule)
        self.assertIn("partial", rule)

    def test_pilot_is_not_citable_and_not_run(self) -> None:
        required = self.draft["manifest_fields_required"]
        self.assertIs(required["citable_as_saving"], False)
        self.assertIs(required["citable_as_quality_equivalence"], False)
        self.assertEqual("three_arm_pilot", required["purpose"])
        self.assertIn("no paid pilot", self.draft["not_run"])

    def test_acceptance_line_is_complete_provider_tokens(self) -> None:
        cost = self.draft["acceptance"]["cost"]
        self.assertIn("complete provider tokens", cost)
        self.assertIn("cache changes dollars only", cost)

    def test_attribution_forbids_the_two_phrases(self) -> None:
        attribution = self.draft["attribution"]
        self.assertIn("本族不装顺序/重复守卫", attribution["required_sentence"])
        self.assertIn("压缩单独省了 X%", attribution["forbidden"])
        self.assertIn("四判据达成", attribution["forbidden"])

    def test_no_artifact_contains_the_forbidden_phrase(self) -> None:
        """The forbidden phrase must not be *asserted* anywhere in this round's artifacts.

        Two shapes are legal and are checked as such: any JSON artifact read as a *claim* is
        checked with the phrase parsed out (so the phrase cannot hide inside a nested string),
        and a document is allowed to name the phrase only on a line that forbids it -- that is
        how the prohibition itself gets recorded.  Everywhere else it is an error.
        """
        for path in sorted((ROOT / "integrations/crewai").glob("R20_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            forbidden_lists = [
                json.dumps(entry, ensure_ascii=False)
                for entry in _walk(data)
                if isinstance(entry, dict) and "forbidden" in entry
            ]
            stripped = json.dumps(data, ensure_ascii=False)
            for entry in forbidden_lists:
                stripped = stripped.replace(entry, "")
            self.assertNotIn(FORBIDDEN, stripped, path.name)
        # The JSON artifacts already checked above are skipped by line: their forbidden list is
        # pretty-printed across lines.
        for path in (PREREG,):
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if FORBIDDEN in line:
                    self.assertTrue(
                        any(marker in line for marker in PROHIBITION_MARKERS),
                        f"{path.name}:{line_number} asserts the forbidden phrase "
                        f"outside a prohibition line",
                    )

    def test_report_artifact_states_the_errors_rule(self) -> None:
        report = json.loads(PILOT_REPORT.read_text(encoding="utf-8"))
        self.assertIn("must not be quoted as a saving", report["criteria_wiring"]["errors_rule"])
        self.assertTrue(report["criteria_wiring"]["audit_imports_a_runner"] is False)

    def test_criteria_ids_are_named_in_the_draft(self) -> None:
        wiring = self.draft["criteria"]
        for cid in criteria.CRITERION_IDS:
            self.assertIn(cid, json.dumps(wiring, ensure_ascii=False))

    def test_draft_names_the_audit_that_decides_a_pass(self) -> None:
        wiring = json.dumps(self.draft["criteria"], ensure_ascii=False)
        self.assertIn("audit_crewai_r20_orderfree_batch", wiring)


class PinIntegrityTest(unittest.TestCase):
    """The criteria module is a pinned source; the batch audit must not have moved it."""

    def test_criteria_module_matches_its_pinned_digest(self) -> None:
        freeze = json.loads(
            (ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_ACQUISITION_01.json")
            .read_text(encoding="utf-8")
        )
        pinned = freeze["source_sha256"]["experiments/audits/criteria_crewai_r20_orderfree.py"]
        self.assertEqual(
            pinned, sha(ROOT / "experiments/audits/criteria_crewai_r20_orderfree.py")
        )

    def test_audit_reports_its_own_digest(self) -> None:
        report = batch_audit.audit(
            batch_audit.DEFAULT_BATCH, batch_audit.DEFAULT_FREEZE, None
        )
        self.assertEqual(
            sha(Path(batch_audit.__file__)), report["audit_module_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
