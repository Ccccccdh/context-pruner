"""The v23 K=1 round's own invariants, in a form anyone can re-run.

These tests are the zero-API claims of
``experiments/runners/run_openai_agents_v23_v18_sealed_window_k1.py`` written down where a test
runner can reach them: the window that is pinned, the arithmetic that produces the cap, the
registration the tasks come from, the literal invariant on the real payloads, the single-occurrence
paths, and the rule that a note can never become a carrier.  Nothing here spends a request.

The gate artifact remains the pass evidence for the batch; this file is what makes the same claims
checkable without reading the gate.
"""

from __future__ import annotations

import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_multitask_chain_v14 as v14  # noqa: E402
from experiments.runners import openai_agents_v22_window as window  # noqa: E402
from experiments.runners import (  # noqa: E402
    run_openai_agents_v23_v18_sealed_window_k1 as runner,
)

REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
FREEZE = ROOT / "integrations/openai_agents/V18_SEALED_CONFIRMATION_FREEZE_20261010.json"

#: The module-level bindings ``configure()`` installs.  A test that configures this round must put
#: every one of them back afterwards: the frozen modules are process-global, and leaving them
#: pointed at this round's task set makes the *other* rounds' own tests fail for a reason that has
#: nothing to do with them.
PATCHED_RUNNER17 = (
    "registry",
    "build_case",
    "MAX_TURNS",
    "PILOT_METHODS",
    "PILOT_REPEATS",
    "PILOT_MAX_API_REQUESTS",
    "PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE",
    "PILOT_EXPERIMENT_ID",
)
PATCHED_CHAIN = (
    "registry",
    "CONFIG_MODULE",
    "FILTER_CLASS",
    "WINDOW_LINES",
    "MECHANISM_SCHEMA",
    "EVIDENCE_SCHEMA",
    "MECHANISM_NAME",
    "COMPOSITION_SCHEMA",
)


@contextmanager
def configured_for_this_round():
    """Configure this round for one block and restore every frozen binding afterwards."""

    from experiments.runners import openai_agents_v19_shape_dose as v19

    saved = {
        "runner17": {name: getattr(runner.runner17, name) for name in PATCHED_RUNNER17},
        "chain": {name: getattr(runner.chain, name) for name in PATCHED_CHAIN},
        "v14": v14.registry,
        "v19": v19.registry,
        "protocol_steps": runner.registry.protocol_steps,
    }
    with runner.sealed_v18_registry_installed():
        runner.select_window(1)
        runner.configure()
        try:
            yield
        finally:
            for name, value in saved["runner17"].items():
                setattr(runner.runner17, name, value)
            for name, value in saved["chain"].items():
                setattr(runner.chain, name, value)
            v14.registry = saved["v14"]
            v19.registry = saved["v19"]
            runner.registry.protocol_steps = saved["protocol_steps"]


class WindowTests(unittest.TestCase):
    def test_the_batch_is_k1_and_every_other_window_is_refused(self) -> None:
        self.assertEqual(runner.K, 1)
        with self.assertRaises(ValueError):
            window.configuration(2)
        with self.assertRaises(ModuleNotFoundError):
            __import__("experiments.runners.openai_agents_v22_window_k2")
        with configured_for_this_round():
            with self.assertRaises(SystemExit):
                runner.select_window(2)

    def test_the_cap_is_a_consequence_of_the_registered_shape(self) -> None:
        math_ = runner.arithmetic()
        self.assertEqual((runner.READ_CALLS, runner.MAX_TURNS), (3, 5))
        self.assertEqual(math_["worst_case_requests_per_sample"], 6)
        self.assertEqual(math_["worst_case_model_calls"], 270)
        self.assertEqual(math_["summary_calls_worst_case"], 30)
        self.assertEqual(math_["worst_case_requests"], 300)
        self.assertEqual(math_["cap"], 390)
        self.assertTrue(math_["at_least_1_3"])
        self.assertEqual(math_["margin"], 1.3)

    def test_every_configuration_module_carries_its_own_window(self) -> None:
        self.assertEqual(window.configuration(1)["k"], 1)
        self.assertEqual(window.configuration(3)["k"], 3)
        self.assertEqual(window.configuration(0)["k"], 0)
        self.assertNotEqual(
            window.configuration(1)["mechanism_schema"], window.configuration(3)["mechanism_schema"]
        )


class RegistrationTests(unittest.TestCase):
    def test_the_three_sealed_tasks_are_the_registered_ones(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        self.assertEqual(payload["registered_task_count"], 3)
        self.assertEqual(
            payload["task_ids"],
            [
                "autoreload_template_changed",
                "sitemap_callable_lastmod",
                "query_utils_unregister_lookup",
            ],
        )
        for task_id, entry in payload["tasks"].items():
            self.assertEqual(len(entry["views"]), 3, task_id)
            self.assertTrue(entry["views_pairwise_disjoint"], task_id)
            for view in entry["views"]:
                self.assertLessEqual(view["line_count"], 60, task_id)
            self.assertEqual(entry["contract"]["max_answer_chars"], 160, task_id)
            self.assertNotEqual(entry["problem_statement_sha256"], "", task_id)

    def test_the_statement_hash_is_recomputed_and_matches_the_candidate_pool(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        for task_id, entry in payload["tasks"].items():
            self.assertTrue(entry["problem_statement_sha256_matches_preselection"], task_id)
            self.assertEqual(
                entry["statement_hash_reconciliation"]["recomputed_from_the_public_table"],
                entry["problem_statement_sha256"],
            )

    def test_the_lazy_negative_control_holds_for_every_task(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        for task_id, entry in payload["tasks"].items():
            check = entry["literal_rule_verdicts"]["R7_lazy_answer_control"]
            self.assertTrue(check["holds"], task_id)
            self.assertFalse(check["satisfies_frozen_regex"], task_id)
            self.assertFalse(check["satisfies_literal_requirement"], task_id)

    def test_the_defect_view_round_is_disclosed_per_task(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        rounds = {
            task_id: entry["defect_view"]["round"] for task_id, entry in payload["tasks"].items()
        }
        self.assertEqual(rounds["autoreload_template_changed"], 3)
        self.assertEqual(rounds["sitemap_callable_lastmod"], 1)
        self.assertEqual(rounds["query_utils_unregister_lookup"], 1)
        for task_id, entry in payload["tasks"].items():
            self.assertEqual(
                entry["defect_view"]["is_the_newest_tool_group"], entry["defect_view"]["round"] == 3
            )
            self.assertIn("recency_consequence", entry["defect_view"])

    def test_the_frozen_records_this_registration_rests_on_are_pinned(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        for name, block in payload["source_artifacts"].items():
            if "sha256" not in block:
                continue
            path = ROOT / block["path"]
            self.assertTrue(path.is_file(), name)
            self.assertEqual(runner.sha256_of(path), block["sha256"], name)


class FreezeTests(unittest.TestCase):
    def test_the_freeze_exists_and_keeps_both_citation_flags_false(self) -> None:
        self.assertTrue(FREEZE.is_file(), "the confirmation freeze must be written before any request")
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(freeze["status"], "FROZEN")
        self.assertFalse(freeze["citable_as_saving"])
        self.assertFalse(freeze["citable_as_quality_equivalence"])
        self.assertEqual(freeze["api_requests_spent_when_written"], 0)
        self.assertTrue(freeze["written_before_any_paid_request"])

    def test_the_freeze_pins_every_module_and_every_source_of_record_file(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        recorded = freeze["host_configuration"]["modules"]
        live = runner.module_hashes()
        self.assertEqual(sorted(recorded), sorted(live))
        for relative, digest in recorded.items():
            self.assertEqual(runner.sha256_of(ROOT / relative), digest, relative)
        for relative, digest in freeze["source_of_record"].items():
            self.assertEqual(runner.sha256_of(ROOT / relative), digest, relative)

    def test_the_frozen_cap_and_matrix_are_the_implemented_ones(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(freeze["matrix"]["samples"], 45)
        self.assertEqual(freeze["matrix"]["repeats"], 5)
        self.assertEqual(freeze["matrix"]["max_api_requests"], 390)
        self.assertEqual(freeze["budget_arithmetic"]["worst_case_requests"], 300)
        self.assertEqual(freeze["acceptance_line"]["line"], runner.ACCEPTANCE_LINE)

    def test_the_forbidden_and_required_wording_are_the_pre_registered_ones(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(freeze["required_wording"], "under this host configuration")
        self.assertEqual(
            set(freeze["forbidden_wording"]),
            {
                "compression alone saved X percent",
                "the four criteria are met",
                "the retry tax is gone",
                "the plugin saves about 12.5 percent of context",
            },
        )


class MechanismTests(unittest.TestCase):
    def setUp(self) -> None:
        self._configured = configured_for_this_round()
        self._configured.__enter__()

    def tearDown(self) -> None:
        self._configured.__exit__(None, None, None)

    def test_every_task_elides_something_and_keeps_every_literal(self) -> None:
        assertion = runner.gate_assertion_e()
        self.assertTrue(assertion["ok"], assertion["tasks_failing"])
        for entry in assertion["detail"]:
            self.assertEqual(entry["window_lines"], 1)
            self.assertTrue(entry["ok"], entry["task"])

    def test_the_single_occurrence_paths_hold(self) -> None:
        specials = runner.literal_invariant_special_tests()
        self.assertTrue(specials["ok"], specials["detail"])
        self.assertGreaterEqual(len(specials["tasks_with_a_literal_that_occurs_exactly_once"]), 2)
        for task_id, detail in specials["detail"].items():
            self.assertGreaterEqual(
                detail["path_one_carrier_protected"]["occurrences_after_in_the_output"], 1, task_id
            )
            self.assertTrue(
                detail["path_two_guard_refuses_a_lost_occurrence"]["guard_sees_the_loss"], task_id
            )
            self.assertTrue(detail["newest_group_kept_verbatim"]["newest_kept_verbatim"], task_id)
            self.assertEqual(detail["real_payload_notes_carrying_a_literal"], [], task_id)

    def test_the_invariant_probe_can_fail(self) -> None:
        self.assertTrue(runner._invariant_probe_rejects_a_lost_literal())

    def test_the_trigger_gate_counters_are_persisted(self) -> None:
        for name in runner.V21_ROW_FIELDS:
            self.assertIn(name, runner.chain.PERSISTED_EXTRA_FIELDS)


class ManifestContractTests(unittest.TestCase):
    def test_the_pre_registered_key_list_is_copied_and_extended(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        copied = freeze["manifest_contract"]["required_keys"]
        self.assertEqual(list(copied), list(runner.MANIFEST_REQUIRED_KEYS))
        for key in (
            "mechanism",
            "v16_manifest_finalized",
            "tool_output_disclosure",
            "re_read_visibility",
            "module_hashes",
            "forbidden_wording",
        ):
            self.assertIn(key, copied)
        for key in runner.EXTRA_MANIFEST_KEYS:
            self.assertIn(key, freeze["manifest_contract"]["extra_keys_this_round_adds"])

    def test_the_paid_path_installs_the_same_contexts_as_the_rehearsal(self) -> None:
        self.assertTrue(runner._run_paid_installs_both_contexts())

    def test_the_v16_sealed_number_is_never_merged_with_this_batch(self) -> None:
        self.assertIn("never merged", runner.PRIMARY_CRITERION["never_merged_with"])
        self.assertIn("12.54", runner.PRIMARY_CRITERION["never_merged_with"])
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        never_rejudge = str(freeze["never_rejudge"])
        for token in ("v12 to v15", "v16 sealed confirmation batch", "v22"):
            self.assertIn(token, never_rejudge)
        self.assertEqual(
            freeze["primary_criterion"]["name"], "as-paired complete provider tokens"
        )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
