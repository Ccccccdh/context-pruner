"""The v24 length-compliance round's own invariants, in a form anyone can re-run.

These tests are the zero-API claims of
``experiments/runners/run_openai_agents_v24_length_compliance_k1.py`` and
``experiments/runners/run_openai_agents_v24_recalibration.py`` written down where a test runner can
reach them: that the two forks are forks and their parents are untouched, that the length
discipline text is in the first prompt of every arm identically, that the over-length retry is
strengthened while keeping both sides of the v17 trade-off, that the 160-character bound is still a
strict-track condition, that K is still 1 and K=2 is still refused, and that the batch discloses the
void batch it follows.  Nothing here spends a request.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v16_live_contract as live16  # noqa: E402
from experiments.runners import openai_agents_v17_live_contract as live17  # noqa: E402
from experiments.runners import openai_agents_v18_registry as registry18  # noqa: E402
from experiments.runners import openai_agents_v22_window as window  # noqa: E402
from experiments.runners import openai_agents_v24_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v24_registry as registry  # noqa: E402
from experiments.runners import (  # noqa: E402
    run_openai_agents_v24_length_compliance_k1 as runner,
)
from experiments.runners import (  # noqa: E402
    run_openai_agents_v24_recalibration as recalibration,
)

REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
FREEZE = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_CONFIRMATION_FREEZE_20261010.json"
)
V24_REGISTRATION = ROOT / "integrations/openai_agents/V24_SOURCE_REGISTRATION_20261010.json"
LEDGER = (
    ROOT
    / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_MODULE_REVISIONS_20261010.json"
)
DIAGNOSIS = ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_DIAGNOSIS_20261010.json"
STOPPED_BATCH = (
    ROOT
    / "runs/stage5-openai-agents-api/openai-repo-diagnostic-v23-v18-sealed-window-k1-01"
)

#: The module-level bindings ``configure()`` installs.  A test that configures this round must put
#: every one of them back afterwards: the frozen modules are process-global, and leaving them
#: pointed at this round's task set makes the other rounds' own tests fail for an unrelated reason.
PATCHED_RUNNER17 = (
    "registry",
    "live",
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
        "v14": __import__(
            "experiments.runners.openai_agents_multitask_chain_v14", fromlist=["registry"]
        ).registry,
        "v19": v19.registry,
        "protocol_steps": runner.registry.protocol_steps,
    }
    with runner.sealed_v24_registry_installed():
        runner.select_window(1)
        runner.configure()
        try:
            yield
        finally:
            for name, value in saved["runner17"].items():
                setattr(runner.runner17, name, value)
            for name, value in saved["chain"].items():
                setattr(runner.chain, name, value)
            v19.registry = saved["v19"]
            runner.registry.protocol_steps = saved["protocol_steps"]


class ParentUntouchedTests(unittest.TestCase):
    """Every fork's parent must still be on disk and still carry its own behaviour."""

    def test_the_v18_registry_is_not_edited_and_still_has_no_discipline_text(self) -> None:
        payload = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        self.assertEqual(payload["registered_task_count"], 3)
        with runner.sealed_v24_registry_installed():
            registry18.SEALED_REGISTRATION = REGISTRATION
            registry18._CACHE.clear()
            registry18.set_active("sealed")
            registry18.READ_ROUNDS = 3
            registry18.READS_PER_ROUND = 1
            registry18.READ_CALLS = 3
            registry18.EXPECTED_MODEL_CALLS = 4
            try:
                for task_id in registry18.task_ids():
                    prompt = str(registry18.task(task_id)["contract_prompt"])
                    self.assertNotIn("Length discipline", prompt, task_id)
                    self.assertNotIn("rejects the ENTIRE answer", prompt, task_id)
            finally:
                registry18.set_active("development")
                registry18._CACHE.clear()

    def test_the_v17_live_contract_is_not_edited(self) -> None:
        self.assertNotIn("compact", live17.RETRY_INSTRUCTION)
        self.assertNotIn("120 characters", live17.DIAGNOSTIC_TEXT["over_160_chars"])
        self.assertEqual(
            live17.RETRY_INSTRUCTION,
            "Rewrite only your preceding JSON answer. Keep the same issue, the same cause, the same "
            "fix and the same fact strings. Reply with the same JSON object shape (issue, cause, "
            "fix, facts) and nothing else. Do not call tools. If the host rejected the answer, its "
            "exact reason follows and your rewrite must fix it.",
        )

    def test_both_forks_report_their_parents_and_their_diffs(self) -> None:
        registry_block = registry.fork_block()
        live_block = live.fork_report()
        self.assertTrue(registry_block["parent_is_not_edited"])
        self.assertEqual(
            registry_block["parent_sha256"],
            runner.sha256_of(ROOT / registry_block["parent_module"]),
        )
        self.assertTrue(live_block["parent_is_not_edited"])
        self.assertEqual(
            live_block["parent_sha256"],
            runner.sha256_of(ROOT / live_block["parent_module"]),
        )
        for name, value in live_block["deliberately_identical"].items():
            self.assertTrue(value, name)
        for name, value in live_block["deliberately_different"].items():
            if isinstance(value, bool):
                self.assertTrue(value, name)
        difference = live_block["get_response_token_difference"]
        self.assertTrue(
            difference["verified_one_identifier_apart"],
            difference,
        )
        self.assertEqual(difference["parent_tokens"], difference["own_tokens"])


class FirstPromptTests(unittest.TestCase):
    """The length discipline must be in the first prompt, identically for every arm."""

    def test_the_discipline_text_is_appended_to_the_contract_prompt_of_every_task(self) -> None:
        with runner.sealed_v24_registry_installed():
            for task_id in registry.task_ids():
                entry = registry.task(task_id)
                discipline = entry["length_discipline"]
                self.assertIn(discipline["text"], entry["contract_prompt"], task_id)
                self.assertTrue(
                    entry["contract_prompt"].rstrip().endswith(discipline["text"].rstrip()),
                    task_id,
                )
                self.assertEqual(registry.verify(task_id), [], task_id)

    def test_the_discipline_states_the_bound_the_rejection_and_a_target(self) -> None:
        text = registry.LENGTH_DISCIPLINE_SENTENCE
        self.assertIn("160", text)
        self.assertIn("rejects the ENTIRE answer", text)
        self.assertIn(str(registry.WORKING_TARGET_CAUSE_PLUS_FIX_CHARS), text)
        self.assertIn("must still appear verbatim", text)
        self.assertIn("does not count as the required string", text)

    def test_the_discipline_arithmetic_is_measured_not_assumed(self) -> None:
        with runner.sealed_v24_registry_installed():
            for task_id in registry.task_ids():
                entry = registry.task(task_id)
                issue = str(entry["contract"]["issue_value"])
                measured = len(
                    registry.RENDERED_LINE_TEMPLATE.format(issue=issue, cause="", fix="")
                )
                block = entry["length_discipline"]["arithmetic"]
                self.assertEqual(block["fixed_characters_of_the_rendered_line"], measured, task_id)
                self.assertEqual(
                    block["characters_left_for_cause_and_fix"], 160 - measured, task_id
                )
                self.assertGreaterEqual(
                    block["characters_left_for_cause_and_fix"],
                    registry.WORKING_TARGET_CAUSE_PLUS_FIX_CHARS,
                    task_id,
                )

    def test_every_arm_gets_one_prompt_with_the_sentence_verbatim(self) -> None:
        with configured_for_this_round():
            check = runner._first_prompt_discipline_check()
        self.assertTrue(check["ok"], check["per_arm"])
        self.assertTrue(check["the_three_arms_get_one_prompt_digest"])
        for arm, entry in check["per_arm"].items():
            self.assertTrue(entry["discipline_text_present"], arm)
            self.assertTrue(entry["states_the_bound_is_a_rejection"], arm)
            self.assertTrue(entry["gives_a_working_target"], arm)
            self.assertTrue(entry["keeps_the_two_sided_literal_rule"], arm)

    def test_the_first_prompt_discipline_check_can_fail(self) -> None:
        with configured_for_this_round():
            case = runner.runner17.build_case(registry.task_ids()[0], 0)
            discipline = str(registry.task(registry.task_ids()[0])["length_discipline"]["text"])
            stripped = runner._assemble_first_prompt(
                str(case.final_contract).replace(discipline, "")
            )
        self.assertNotIn(registry.LENGTH_DISCIPLINE_SENTENCE, stripped)


class RetryWordingTests(unittest.TestCase):
    """The over-length retry is strengthened, and keeps every sentence the v17 wording had."""

    def test_the_retry_instruction_is_strengthened_on_both_points(self) -> None:
        text = live.RETRY_INSTRUCTION
        self.assertNotEqual(text, live17.RETRY_INSTRUCTION)
        self.assertIn("and nothing else", text)
        self.assertIn("no explanation, no commentary", text)
        self.assertIn(str(live.WORKING_TARGET_CAUSE_PLUS_FIX_CHARS), text)
        self.assertIn("Keep the same issue, the same cause, the same fix", text)

    def test_the_over_160_diagnostic_keeps_the_v17_text_whole(self) -> None:
        self.assertIn(live.V17_OVER_160_TEXT, live.DIAGNOSTIC_TEXT["over_160_chars"])
        self.assertNotEqual(
            live.DIAGNOSTIC_TEXT["over_160_chars"],
            live17.DIAGNOSTIC_TEXT["over_160_chars"],
        )

    def test_every_other_rejection_class_is_byte_identical_to_the_parent(self) -> None:
        wording = live.wording_report()
        self.assertTrue(wording["every_class_but_over_160_is_byte_identical_to_v17"])

    def test_the_over_length_retry_still_states_both_sides(self) -> None:
        wording = live.wording_report(
            required_facts=("template_changed", "template_dir", "file_path"),
            cause_token="template_changed",
            fix_token="template_dir",
            issue="django-15572",
        )
        two_sided = wording["two_sided_requirements"]
        self.assertTrue(two_sided["the_over_160_retry_states_all_of_them"], two_sided["detail"])
        self.assertEqual(
            two_sided["detail"]["facts_named"],
            ["template_changed", "template_dir", "file_path"],
        )
        self.assertTrue(two_sided["detail"]["bound_named"])
        self.assertTrue(two_sided["detail"]["cause_token_named"])
        self.assertTrue(two_sided["detail"]["fix_token_named"])
        self.assertTrue(two_sided["detail"]["explanation_ban_named"])
        self.assertTrue(two_sided["detail"]["drop_explanations_rule_kept"])
        self.assertTrue(two_sided["detail"]["remove_wording_not_requirements_kept"])

    def test_the_retry_wording_is_the_same_for_every_arm(self) -> None:
        """Neither wording is a function of the arm: both are module constants."""

        wording = live.wording_report()
        self.assertEqual(wording["strengthened_reasons"], ["over_160_chars"])
        # The arms reach the wording only through the wrapper's own method, which reads the module
        # constant; there is no per-arm branch anywhere in the module.
        source = (ROOT / "experiments/runners/openai_agents_v24_live_contract.py").read_text(
            encoding="utf-8"
        )
        for arm in ("none", "native_summary", "pruner_v1"):
            self.assertNotIn(f'"{arm}"', source)

    def test_the_retry_budget_is_still_one_and_the_bound_is_still_160(self) -> None:
        self.assertEqual(live.RETRY_BUDGET_PER_SAMPLE, 1)
        self.assertEqual(live.MAX_CHARS, 160)
        self.assertEqual(live.MAX_CHARS, live17.MAX_CHARS)
        self.assertEqual(
            live.wording_report()["renderer_module"],
            "experiments/runners/openai_agents_structured_final_v16.py",
        )

    def test_the_unstrengthened_classes_are_still_the_v16_wording(self) -> None:
        fixed = {
            "raw": "",
            "expected_issue": "self-test",
            "required_facts": ["a"],
            "cause_token": "a",
            "fix_token": "a",
        }
        for reason in live17.DIAGNOSTIC_TEXT:
            if reason == "over_160_chars":
                continue
            self.assertEqual(
                live16.diagnostic_for(reason, **fixed),
                live.diagnostic_for(reason, **fixed),
                reason,
            )


class BoundStillStrictTests(unittest.TestCase):
    """The evidence bar must not have moved: the bound is still an acceptance condition."""

    def test_the_160_bound_is_still_a_strict_track_condition(self) -> None:
        self.assertEqual(runner.live.MAX_CHARS, 160)
        self.assertEqual(live16.MAX_CHARS, 160)
        self.assertEqual(
            live.conditions(
                "RESULT issue=x cause=" + "c" * 200 + " fix=f", "x", "RESULT issue=x cause=.* fix=.*", ["f"]
            )["within_160"],
            False,
        )

    def test_the_renderer_still_fails_closed_over_the_bound(self) -> None:
        over = json.dumps(
            {"issue": "django-15572", "cause": "c" * 200, "fix": "f", "facts": ["f"]}
        )
        verdict = live.self_test("django-15572")
        self.assertTrue(verdict["ok"], verdict["checks"])
        self.assertEqual(live.render(over, expected_issue="django-15572").reason, "over_160_chars")
        self.assertEqual(live.render(over, expected_issue="django-15572").output, "")

    def test_the_literals_and_the_frozen_regex_are_the_registered_ones(self) -> None:
        with runner.sealed_v24_registry_installed():
            for task_id in registry.task_ids():
                entry = registry.task(task_id)
                contract = entry["contract"]
                self.assertEqual(len(contract["required_facts"]), 3, task_id)
                self.assertEqual(contract["max_answer_chars"], 160, task_id)
                feasible = registry.minimal_feasible_line(task_id)
                self.assertLessEqual(feasible["chars"], 160, task_id)
                self.assertEqual(feasible["problems"], [], task_id)
                searchable = "\n".join(registry.view_texts(task_id)) + "\n" + registry.statement(
                    task_id
                )
                for token in (entry["cause_token"], entry["fix_token"]):
                    self.assertIn(token, searchable, task_id)
                for fact in contract["required_facts"]:
                    self.assertIn(fact, searchable, task_id)


class WindowTests(unittest.TestCase):
    def test_the_batch_is_still_k1_and_k2_is_still_refused(self) -> None:
        self.assertEqual(runner.K, 1)
        with self.assertRaises(ValueError):
            window.configuration(2)
        with self.assertRaises(ModuleNotFoundError):
            __import__("experiments.runners.openai_agents_v22_window_k2")
        with configured_for_this_round():
            with self.assertRaises(SystemExit):
                runner.select_window(2)

    def test_the_cap_and_the_shape_are_unchanged_from_the_v23_round(self) -> None:
        math_ = runner.arithmetic()
        self.assertEqual((runner.READ_CALLS, runner.MAX_TURNS), (3, 5))
        self.assertEqual(runner.REPEATS, 5)
        self.assertEqual(runner.SAMPLES, 45)
        self.assertEqual(math_["worst_case_requests"], 300)
        self.assertEqual(math_["cap"], 390)
        self.assertEqual(math_["margin"], 1.3)
        self.assertEqual(
            math_["derivation"],
            "READ_CALLS=3 => MAX_TURNS = 3+2 = 5 => worst case 6 per sample => 45x6 + 15x2 = 300 => 1.3x => 390",
        )

    def test_the_recalibration_cap_arithmetic_is_written_out(self) -> None:
        math_ = recalibration.arithmetic()
        self.assertEqual(recalibration.SAMPLES, 9)
        self.assertEqual(recalibration.REPEATS, 1)
        self.assertEqual(math_["worst_case_requests"], 60)
        self.assertEqual(math_["cap"], 80)
        self.assertTrue(math_["at_least_1_3"])


class DisclosureTests(unittest.TestCase):
    """The prior run of the same task set must be disclosed, and the set declared exhausted."""

    def test_the_freeze_discloses_the_prior_batch_verbatim(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        prior = freeze["prior_batch"]
        self.assertEqual(prior["batch"], runner.STOPPED_BATCH)
        self.assertEqual(prior["stopped_at"], "39 of 45 samples")
        self.assertIn("django-15572", prior["failure"])
        self.assertIn("fifteen", prior["failure"])
        self.assertEqual(prior["audit"], "fail-closed: complete=false, usable_as_saving=false, fifteen errors")
        self.assertFalse(prior["citable"])
        self.assertTrue(freeze["this_is_the_second_run_of_the_same_task_set"])
        self.assertTrue(freeze["task_set_is_exhausted_after_this_batch"])

    def test_the_v24_source_registration_repeats_the_disclosure(self) -> None:
        payload = json.loads(V24_REGISTRATION.read_text(encoding="utf-8"))
        disclosure = payload["prior_batch_disclosure"]
        self.assertEqual(disclosure["prior_outcome"], "stopped at 39 of 45 samples")
        self.assertTrue(disclosure["this_batch_is_the_second_run_of_the_same_task_set"])
        self.assertTrue(disclosure["prior_batch_is_not_citable"])
        self.assertIn("exhausted", disclosure["task_set_exhaustion"])
        self.assertTrue(payload["task_set_is_exhausted_after_this_batch"])

    def test_the_diagnosis_names_the_failure_class_and_its_limits(self) -> None:
        payload = json.loads(DIAGNOSIS.read_text(encoding="utf-8"))
        self.assertEqual(payload["conclusion"]["answer"], "a first-answer compliance failure")
        self.assertTrue(payload["conclusion"]["symmetry"]["all_three_arms_rejected_on_the_same_class"])
        self.assertFalse(payload["stopped_batch"]["raw_answer_text_available"])
        statuses = {
            entry["status"] for entry in payload["limits"]["fields_left_undetermined"]
        }
        self.assertEqual(statuses, {"undetermined"})
        self.assertFalse(payload["limits"]["citable"])

    def test_the_stopped_batch_is_still_on_disk_and_untouched(self) -> None:
        self.assertTrue((STOPPED_BATCH / "samples.jsonl").is_file())
        rows = [
            json.loads(line)
            for line in (STOPPED_BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), 39)
        failures = [
            row
            for row in rows
            if str(row.get("scenario")) == "autoreload_template_changed"
            and not bool(row.get("render_accepted"))
        ]
        self.assertEqual(len(failures), 15)


class FreezeAndManifestContractTests(unittest.TestCase):
    def test_the_freeze_keeps_both_citation_flags_false_and_pins_every_module(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(freeze["status"], "FROZEN")
        self.assertFalse(freeze["citable_as_saving"])
        self.assertFalse(freeze["citable_as_quality_equivalence"])
        self.assertEqual(freeze["api_requests_spent_when_written"], 0)
        self.assertEqual(freeze["matrix"]["samples"], 45)
        self.assertEqual(freeze["matrix"]["repeats"], 5)
        self.assertEqual(freeze["matrix"]["max_api_requests"], 390)
        recorded = freeze["host_configuration"]["modules"]
        self.assertEqual(sorted(recorded), sorted(runner.module_hashes()))
        for relative, digest in recorded.items():
            self.assertEqual(runner.sha256_of(ROOT / relative), digest, relative)

    def test_the_freeze_pins_the_two_forks_with_their_own_digests(self) -> None:
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        modules = freeze["host_configuration"]["modules"]
        for relative in (
            "experiments/runners/openai_agents_v24_registry.py",
            "experiments/runners/openai_agents_v24_live_contract.py",
            "experiments/runners/openai_agents_v18_registry.py",
            "experiments/runners/openai_agents_v17_live_contract.py",
        ):
            self.assertIn(relative, modules)
            self.assertEqual(modules[relative], runner.sha256_of(ROOT / relative), relative)

    def test_the_revision_ledger_pins_the_same_set_as_the_freeze(self) -> None:
        ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(ledger["modules"]), sorted(freeze["host_configuration"]["modules"])
        )

    def test_the_pre_registered_keys_are_declared_and_extended(self) -> None:
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
        extra = freeze["manifest_contract"]["extra_keys_this_round_adds"]
        for key in (
            "v24_source_registration",
            "v24_confirmation_freeze",
            "v24_length_compliance_configuration",
            "v24_prior_batch_disclosure",
            "v24_task_set_exhaustion",
        ):
            self.assertIn(key, extra)
        self.assertEqual(list(extra), list(runner.EXTRA_MANIFEST_KEYS))

    def test_the_v16_sealed_number_is_never_merged_with_this_batch(self) -> None:
        self.assertIn("never merged", runner.PRIMARY_CRITERION["never_merged_with"])
        self.assertIn("12.54", runner.PRIMARY_CRITERION["never_merged_with"])
        freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
        never_rejudge = str(freeze["never_rejudge"])
        for token in ("v12 to v15", "v16 sealed confirmation batch", "v22"):
            self.assertIn(token, never_rejudge)
        self.assertIn("stopped v23 batch", never_rejudge)

    def test_the_paid_path_installs_the_same_contexts_as_the_rehearsal(self) -> None:
        self.assertTrue(runner._run_paid_installs_both_contexts())

    def test_the_recalibration_is_never_citable(self) -> None:
        with recalibration.installed_for_recalibration():
            freeze = recalibration.freeze_payload()
        self.assertFalse(freeze["citable_as_saving"])
        self.assertFalse(freeze["citable_as_quality_equivalence"])
        self.assertEqual(freeze["matrix"]["samples"], 9)
        # The acceptance line is the v24 configuration's own, imported rather than restated, so a
        # nine-sample development batch cannot lower a bar the confirmation batch will use.
        self.assertEqual(freeze["acceptance_line"]["line"], runner.ACCEPTANCE_LINE)
        self.assertEqual(
            freeze["recalibration_decision_line"]["line"], recalibration.DECISION_LINE
        )
        self.assertIn("70 percent", recalibration.DECISION_LINE)
        self.assertEqual(len(freeze["task_set"]), 3)
        self.assertEqual(
            set(freeze["task_set"]),
            set(json.loads(REGISTRATION.read_text(encoding="utf-8"))["task_ids"]),
        )


class MechanismStillPinnedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._configured = configured_for_this_round()
        self._configured.__enter__()

    def tearDown(self) -> None:
        self._configured.__exit__(None, None, None)

    def test_the_literal_invariant_probe_still_holds_and_can_fail(self) -> None:
        self.assertTrue(runner._invariant_probe_rejects_a_lost_literal())
        specials = runner.literal_invariant_special_tests()
        self.assertTrue(specials["ok"], specials["detail"])

    def test_every_task_still_elides_something(self) -> None:
        assertion = runner.gate_assertion_e()
        self.assertTrue(assertion["ok"], assertion["tasks_failing"])

    def test_the_mechanism_module_is_still_the_frozen_one(self) -> None:
        from experiments.runners import openai_agents_v21_carrier_elision as mechanism

        self.assertEqual(mechanism.WINDOW_LINES, 3)
        self.assertEqual(int(getattr(runner.chain.FILTER_CLASS, "WINDOW_LINES", -1)), 1)


class RegistryForkHashTests(unittest.TestCase):
    def test_the_fork_registry_hash_matches_the_file(self) -> None:
        block = registry.fork_block()
        self.assertEqual(
            block["module_sha256"],
            hashlib.sha256(
                (ROOT / "experiments/runners/openai_agents_v24_registry.py").read_bytes()
            ).hexdigest(),
        )

    def test_the_fork_live_contract_hash_matches_the_file(self) -> None:
        block = live.fork_report()
        self.assertEqual(
            block["module_sha256"],
            hashlib.sha256(
                (ROOT / "experiments/runners/openai_agents_v24_live_contract.py").read_bytes()
            ).hexdigest(),
        )


class RecalibrationShapeTests(unittest.TestCase):
    """The recalibration is nine samples on exactly the three registered tasks, and nothing else."""

    def test_the_recalibration_batch_is_nine_samples_on_three_tasks(self) -> None:
        self.assertEqual(recalibration.SAMPLES, 9)
        self.assertEqual(recalibration.REPEATS, 1)
        self.assertEqual(recalibration.NATIVE_SAMPLES, 3)
        freeze = json.loads(recalibration.FREEZE.read_text(encoding="utf-8"))
        self.assertEqual(len(freeze["matrix"]["tasks"]), 3)
        self.assertEqual(
            set(freeze["matrix"]["tasks"]),
            set(json.loads(REGISTRATION.read_text(encoding="utf-8"))["task_ids"]),
        )
        self.assertEqual(freeze["host_configuration"]["retry"]["budget_per_sample"], 1)

    def test_the_recalibration_pins_the_confirmation_set_plus_its_driver(self) -> None:
        payload = json.loads(recalibration.REVISION_LEDGER.read_text(encoding="utf-8"))
        modules = payload["modules"]
        for relative, digest in runner.module_hashes().items():
            self.assertEqual(modules.get(relative), digest, relative)
        self.assertEqual(
            modules.get(recalibration.driver_module()), recalibration.driver_hash()
        )
        self.assertEqual(
            payload["modules_this_batch_adds"], [recalibration.driver_module()]
        )
        confirmation = json.loads(LEDGER.read_text(encoding="utf-8"))
        self.assertNotIn(recalibration.driver_module(), confirmation["modules"])
        self.assertEqual(confirmation["revision"], 4)

    def test_the_recalibration_task_set_holder_restores_the_registry(self) -> None:
        before = registry.task_ids()
        with recalibration.registered_tasks_installed():
            inside = registry.task_ids()
            self.assertEqual(len(inside), 3)
            self.assertEqual(set(inside), set(json.loads(REGISTRATION.read_text(encoding="utf-8"))["task_ids"]))
        self.assertEqual(registry.task_ids(), before)


if __name__ == "__main__":
    raise SystemExit(unittest.main())
