"""Zero-API tests for the v16 structured live runner, its registry and its renderer path.

Nothing in this module contacts a provider.  The plan and stub modes of
``run_openai_agents_v16_acquisition`` are exercised end to end, and the paid path is checked
only for its refusals - with the provider client and provider model replaced by objects that
raise if they are ever used.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from experiments.runners import openai_agents_multitask_chain_v14 as chain
from experiments.runners import openai_agents_v16_live_contract as live
from experiments.runners import openai_agents_v16_registry as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_v16_acquisition as runner
from experiments.runners.openai_agents_structured_final_v16 import MAX_CHARS, render


class RegistryTests(unittest.TestCase):
    def test_three_tasks_verify_against_the_frozen_sources(self) -> None:
        self.assertEqual(
            (
                "django_sqlite_version_floor",
                "django_orderedset_reversed",
                "django_field_hash_immutability",
            ),
            registry.task_ids(),
        )
        self.assertEqual({}, {k: v for k, v in registry.all_problems().items() if v})

    def test_views_and_contracts_are_the_registered_ones(self) -> None:
        for task_id in registry.task_ids():
            entry = registry.task(task_id)
            self.assertEqual(3, len(entry["views"]))
            self.assertEqual(3, len(entry["tool_names"]))
            self.assertEqual(6, entry["read_calls"])
            self.assertEqual(7, entry["expected_model_calls"])
            self.assertEqual(MAX_CHARS, int(entry["contract"]["max_answer_chars"]))
            self.assertEqual(entry["contract"]["frozen_regex"], entry["answer_pattern"])
            self.assertEqual(
                tuple(entry["contract"]["required_facts"]), tuple(entry["required_terms"])
            )
            for index, view in enumerate(entry["views"]):
                text = registry.source_view(task_id, index)
                self.assertEqual(
                    view["view_content_sha256"],
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )

    def test_derived_fields_are_declared(self) -> None:
        for task_id in registry.task_ids():
            derived = list(registry.task(task_id)["derived_fields"])
            self.assertIn("protocol_steps", derived)
            self.assertIn("tool_docs", derived)
        self.assertEqual(
            "runner_supplied_in_registered_view_order",
            registry.task("django_field_hash_immutability")["tool_names_source"],
        )
        self.assertEqual(
            "registered_artifact",
            registry.task("django_sqlite_version_floor")["tool_names_source"],
        )

    def test_problem_statement_hash_reconciliation_is_recorded(self) -> None:
        reconciled = registry.task("django_field_hash_immutability")[
            "problem_statement_hash_reconciliation"
        ]
        self.assertIsNotNone(reconciled)
        self.assertEqual(
            reconciled["public_table_statement_sha256"], reconciled["candidate_pool_value"]
        )


class RendererPathTests(unittest.TestCase):
    def test_self_test_is_green(self) -> None:
        result = live.self_test("django-13821")
        self.assertTrue(result["ok"], result["checks"])
        self.assertEqual(0, result["api_calls"])
        self.assertEqual(160, MAX_CHARS)

    def test_stage1_named_controls_still_land_on_their_reasons(self) -> None:
        over_long = json.dumps(
            {"issue": "django-13821", "cause": "c" * 200, "fix": "f", "facts": ["f"]}
        )
        self.assertEqual("over_160_chars", render(over_long, expected_issue="django-13821").reason)
        missing = json.dumps(
            {
                "issue": "django-13821",
                "cause": "c",
                "fix": "c",
                "facts": ["c", "3.12.0"],
            }
        )
        verdict = render(missing, expected_issue="django-13821")
        self.assertFalse(verdict.accepted)
        self.assertEqual("declared_fact_missing_from_rendered_answer", verdict.reason)

    def test_over_long_instance_renders_nothing(self) -> None:
        answer_type = live.structured_answer_type("django-13821")
        long_answer = answer_type(
            issue="django-13821", cause="c" * 200, fix="c", facts=["c"]
        )
        self.assertEqual("", str(long_answer))


class PlanModeTests(unittest.TestCase):
    def test_plan_writes_api_calls_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "plan.json"
            status = runner.main(["--plan", "--artifact", str(artifact)])
            self.assertEqual(0, status)
            payload = json.loads(artifact.read_text(encoding="utf-8"))
        self.assertEqual(0, payload["api_calls"])
        self.assertFalse(payload["paid_batch_started"])
        self.assertEqual(runner.FREEZE_SHA256, payload["freeze"]["sha256"])
        self.assertTrue(payload["renderer_self_test"]["ok"])
        plan = payload["planned_requests"]
        self.assertEqual(3, plan["samples"])
        self.assertEqual(21, plan["minimum_planned_requests"])
        self.assertLessEqual(plan["worst_case_logical_requests_with_one_retry_each"], 30)
        self.assertTrue(plan["worst_case_fits_the_cap"])
        self.assertEqual(
            list(live.PER_SAMPLE_FIELDS), payload["sdk_wiring"]["per_sample_report_fields"]
        )

    def test_plan_refuses_to_overwrite_its_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "plan.json"
            artifact.write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                runner.main(["--plan", "--artifact", str(artifact)])


class StubModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        artifact = Path(cls._tmp.name) / "stub.json"
        cls._status = runner.main(["--stub", "--artifact", str(artifact)])
        cls._payload = json.loads(artifact.read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_stub_is_zero_api_and_all_controls_pass(self) -> None:
        self.assertEqual(0, self._status)
        self.assertEqual(0, self._payload["api_calls"])
        self.assertTrue(self._payload["controls_ok"])
        self.assertFalse(self._payload["paid_batch_started"])
        self.assertFalse(self._payload["api_key_used"])
        self.assertEqual(13, len(self._payload["controls"]))

    def test_the_retry_carries_the_host_rejection_reason(self) -> None:
        by_name = {entry["control"]: entry for entry in self._payload["controls"]}
        for name, entry in by_name.items():
            observed = entry["observed"]
            if observed["repair_calls"] == 0:
                self.assertFalse(observed["diagnostic_carried"], name)
                self.assertEqual(0, observed["diagnostic_chars"], name)
            else:
                self.assertTrue(observed["diagnostic_carried"], name)
                self.assertGreater(observed["diagnostic_chars"], 0, name)
        used = by_name["diagnostic_used_by_model"]["observed"]
        self.assertEqual("accepted_retry", used["reason"])
        self.assertTrue(used["render_accepted"])
        self.assertTrue(used["frozen_regex"])
        self.assertEqual(1, used["repair_calls"])
        ignored = by_name["diagnostic_ignored_still_fails"]["observed"]
        self.assertEqual(
            "retry_rejected:declared_fact_missing_from_rendered_answer", ignored["reason"]
        )
        self.assertEqual("", ignored["final_output"])

    def test_diagnostic_text_names_the_failing_rule(self) -> None:
        text = live.diagnostic_for(
            "declared_fact_missing_from_rendered_answer",
            raw=json.dumps(
                {
                    "issue": "django-13821",
                    "cause": "check_sqlite_version floor",
                    "fix": "require 3.9.0",
                    "facts": ["check_sqlite_version", "3.12.0"],
                }
            ),
            expected_issue="django-13821",
            required_facts=["check_sqlite_version", "3.8.3", "3.9.0"],
            cause_token="check_sqlite_version",
            fix_token="3.9.0",
        )
        self.assertIn(live.DIAGNOSTIC_MARKER, text)
        self.assertIn("declared_fact_missing_from_rendered_answer", text)
        self.assertIn("3.12.0", text)
        self.assertIn("facts", text)
        self.assertIn("160", text)
        for fact in ("check_sqlite_version", "3.8.3", "3.9.0"):
            self.assertIn(fact, text)
        self.assertIn("Place each of them inside cause or fix", text)

    def test_every_rejection_class_states_the_bound_and_the_literals(self) -> None:
        facts = ["__hash__", "__eq__", "creation_counter"]
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
            text = live.diagnostic_for(
                reason,
                required_facts=facts,
                cause_token="__hash__",
                fix_token="creation_counter",
            )
            self.assertIn("160", text, reason)
            for fact in facts:
                self.assertIn(fact, text, reason)
            self.assertIn("creation_counter", text, reason)
            self.assertIn("restate", text, reason)

    def test_six_validator_negatives_fail_closed_with_their_reasons(self) -> None:
        by_name = {entry["control"]: entry for entry in self._payload["controls"]}
        expected = {
            "invalid_json": "retry_rejected:invalid_json_or_duplicate_key",
            "missing_field": "retry_rejected:missing_or_extra_field",
            "extra_field": "retry_rejected:missing_or_extra_field",
            "duplicate_key": "retry_rejected:invalid_json_or_duplicate_key",
            "wrong_type": "retry_rejected:invalid_fact_list",
            "artificially_over_long": "retry_rejected:over_160_chars",
            "missing_literal": "retry_rejected:declared_fact_missing_from_rendered_answer",
        }
        for name, reason in expected.items():
            observed = by_name[name]["observed"]
            self.assertFalse(observed["render_accepted"], name)
            self.assertEqual(reason, observed["reason"], name)
            self.assertEqual(1, observed["repair_calls"], name)
            self.assertEqual("", observed["final_output"], name)
            self.assertFalse(observed["within_160"], name)

    def test_controls_land_on_the_frozen_verdicts(self) -> None:
        by_name = {entry["control"]: entry for entry in self._payload["controls"]}
        over_long = by_name["artificially_over_long"]["observed"]
        self.assertGreater(over_long["rendered_chars"], MAX_CHARS)
        self.assertTrue(over_long["had_candidate"])
        self.assertGreater(over_long["candidate_chars"], MAX_CHARS)
        tampered = by_name["one_character_tampered_output"]["observed"]
        self.assertTrue(tampered["render_accepted"])
        self.assertEqual("accepted_first", tampered["reason"])
        self.assertFalse(tampered["frozen_regex"])
        self.assertEqual(0, tampered["repair_calls"])
        self.assertTrue(tampered["substituted"])
        positive = by_name["well_formed_positive"]["observed"]
        self.assertTrue(positive["render_accepted"])
        self.assertTrue(positive["frozen_regex"])
        self.assertTrue(positive["within_160"])
        self.assertTrue(positive["single_line_prefix"])
        self.assertTrue(positive["substituted"])
        self.assertEqual(
            "RESULT issue=django-14089 cause=OrderedSet lacks __reversed__ "
            "fix=add __reversed__ to OrderedSet",
            positive["final_output"],
        )

    def test_every_control_runs_the_reads_answer_and_one_retry_only(self) -> None:
        for entry in self._payload["controls"]:
            observed = entry["observed"]
            self.assertEqual(
                registry.EXPECTED_MODEL_CALLS + observed["repair_calls"],
                observed["model_calls"],
            )
            self.assertEqual(observed["model_calls"], observed["provider_calls"])
            self.assertEqual(observed["model_calls"], observed["api_request_attempts"])
            self.assertLessEqual(observed["repair_calls"], 1)

    def test_no_control_truncated_or_dropped_a_field(self) -> None:
        for entry in self._payload["controls"]:
            self.assertTrue(entry["checks"]["never_truncated"])
            self.assertTrue(entry["checks"]["no_field_dropped"])

    def test_per_boundary_evidence_is_recorded_for_every_call(self) -> None:
        for entry in self._payload["controls"]:
            boundaries = entry["observed"]["boundaries"]
            self.assertEqual(entry["observed"]["model_calls"], boundaries["records"])
            self.assertTrue(boundaries["input_sha256_present"])
            self.assertTrue(boundaries["item_count_present"])
            self.assertTrue(boundaries["message_items_present"])
            self.assertTrue(boundaries["output_manifest_present"])
            self.assertIn("call_id", boundaries["output_manifest_entry_fields"])
            self.assertIn("output_sha256", boundaries["output_manifest_entry_fields"])
            self.assertIn("output_chars", boundaries["output_manifest_entry_fields"])

    def test_per_sample_report_fields_are_the_frozen_ones(self) -> None:
        for entry in self._payload["controls"]:
            observed = entry["observed"]
            for name in live.PER_SAMPLE_FIELDS:
                self.assertIn(name, observed, name)
            self.assertEqual(4, len(live.CONDITION_NAMES))

    def test_every_control_ran_with_the_adapted_frozen_prompt(self) -> None:
        for entry in self._payload["controls"]:
            self.assertTrue(entry["checks"]["frozen_prompt_adapted"], entry["control"])
            applied = entry["observed"]["prompt_adaptations"]["applied"]
            unapplied = entry["observed"]["prompt_adaptations"]["unapplied"]
            self.assertEqual(len(live.PROMPT_REPLACEMENTS), len(applied), entry["control"])
            self.assertEqual([], unapplied, entry["control"])


class PaidModeRefusalTests(unittest.TestCase):
    def test_resume_and_retry_failed_are_refused(self) -> None:
        for flag in ("--resume", "--retry-failed"):
            with self.assertRaises(SystemExit):
                runner.main([flag, "--confirm-send-public-source"])

    def test_confirmation_flag_is_required(self) -> None:
        with self.assertRaises(SystemExit):
            runner.main([])

    def test_paid_path_stops_without_a_credential_and_never_builds_a_provider(self) -> None:
        original_client = base.AsyncOpenAI
        original_model = base.OpenAIChatCompletionsModel

        def forbidden(**kwargs):
            raise AssertionError("the provider client must not be built")

        base.AsyncOpenAI = forbidden
        base.OpenAIChatCompletionsModel = runner._NoProviderModel
        try:
            with mock.patch.dict(
                os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""}
            ):
                with self.assertRaises(SystemExit) as raised:
                    runner.main(
                        ["--confirm-send-public-source", "--out", "runs/v16-unused-test-out"]
                    )
        finally:
            base.AsyncOpenAI = original_client
            base.OpenAIChatCompletionsModel = original_model
        self.assertIn("is not set", str(raised.exception))
        self.assertFalse(Path("runs/v16-unused-test-out").exists())


class PaidPathRehearsalTests(unittest.TestCase):
    """The paid code path with the provider replaced by the local model: still zero API."""

    def _proposal(self, task_id: str) -> str:
        entry = registry.task(task_id)
        contract = entry["contract"]
        facts = [str(fact) for fact in contract["required_facts"]]
        cause = "cause: " + " ".join(facts[:2]) + " " + entry["cause_token"]
        fix = "fix: " + " ".join(facts[2:]) + " " + entry["fix_token"]
        return json.dumps(
            {
                "issue": str(contract["issue_value"]),
                "cause": cause.strip(),
                "fix": fix.strip(),
                "facts": facts,
            }
        )

    def test_paid_path_runs_end_to_end_against_the_local_model(self) -> None:
        def factory(shared: dict):
            def build(inner, request_budget, **kwargs):
                key = shared.get("key")
                task_id = str(key[0]) if key else registry.task_ids()[0]
                repeat = int(key[1]) if key else 0
                return live.ScriptedStubModel(
                    runner.build_case(task_id, repeat),
                    request_budget=request_budget,
                    final_text_value=self._proposal(task_id),
                )

            return build

        original_client = base.AsyncOpenAI
        original_model = base.OpenAIChatCompletionsModel
        original_chain_registry = chain.registry
        base.AsyncOpenAI = lambda **kwargs: runner._NoNetworkClient()
        base.OpenAIChatCompletionsModel = runner._NoProviderModel
        chain.registry = registry
        runner._placeholder_key_installed()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                status = runner.run_paid(
                    [
                        "--confirm-send-public-source",
                        "--out",
                        tmp,
                    ],
                    inner_factory=factory,
                )
                root = Path(tmp) / runner.EXPERIMENT_ID
                manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
                rows = [
                    json.loads(line)
                    for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
        finally:
            base.AsyncOpenAI = original_client
            base.OpenAIChatCompletionsModel = original_model
            chain.registry = original_chain_registry
        self.assertEqual(0, status)
        self.assertEqual(3, len(rows))
        self.assertEqual(21, manifest["api_calls"])
        self.assertTrue(manifest["v16_manifest_finalized"])
        self.assertFalse(manifest["citable_as_saving"])
        self.assertEqual(3, manifest["batch_report"]["render_accepted_count"])
        self.assertEqual(0, manifest["batch_report"]["post_render_over_160_count"])
        self.assertTrue(manifest["batch_report"]["attainable_on_this_batch"])
        self.assertEqual(0, manifest["batch_report"]["truncations"])
        self.assertEqual(0, manifest["batch_report"]["fields_dropped"])
        for entry in manifest["batch_report"]["per_sample"]:
            for name in live.PER_SAMPLE_FIELDS:
                self.assertIn(name, entry, name)
            self.assertTrue(entry["render_accepted"], entry)
            self.assertTrue(entry["success"], entry)
            self.assertEqual(7, entry["requests"])
        self.assertEqual(
            set(registry.task_ids()),
            {str(row["scenario"]) for row in rows},
        )
        for row in rows:
            self.assertEqual(7, int(row["model_calls"]))
            self.assertEqual(0, int(row["render_repair_calls"]))
            self.assertEqual(0, int(row["api_retry_count"]))


class RawCacheMeteringTests(unittest.TestCase):
    """The vendor cache split is read where it still exists: the raw request response."""

    class _ExtrasOnly:
        model_extra = {
            "prompt_cache_hit_tokens": 40,
            "prompt_cache_miss_tokens": 60,
        }
        prompt_tokens = 100
        completion_tokens = 5
        total_tokens = 105

        def model_dump(self) -> dict:
            return {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                **self.model_extra,
            }

    class _Response:
        def __init__(self, usage) -> None:
            self.usage = usage

    def test_vendor_cache_fields_are_read_from_model_extra(self) -> None:
        record = live.raw_usage_of(self._Response(self._ExtrasOnly()))
        self.assertEqual(40, record["prompt_cache_hit_tokens"])
        self.assertEqual(60, record["prompt_cache_miss_tokens"])
        self.assertTrue(record["cache_fields_present"])
        self.assertFalse(record["cache_usage_unknown"])
        self.assertEqual(100, record["prompt_tokens"])
        self.assertIn("prompt_cache_hit_tokens", record["usage_keys"])

    def test_sdk_usage_type_drops_the_vendor_fields_so_they_stay_unknown(self) -> None:
        from agents import Usage

        record = live.raw_usage_of(
            self._Response(
                Usage(requests=1, input_tokens=100, output_tokens=5, total_tokens=105)
            )
        )
        self.assertIsNone(record["prompt_cache_hit_tokens"])
        self.assertIsNone(record["prompt_cache_miss_tokens"])
        self.assertFalse(record["cache_fields_present"])
        self.assertTrue(record["cache_usage_unknown"])

    def test_proxy_records_one_entry_per_raw_request(self) -> None:
        import asyncio

        sink: list = []

        class _Completions:
            def __init__(self) -> None:
                self.calls = 0

            async def create(self, *args, **kwargs):
                self.calls += 1
                return RawCacheMeteringTests._Response(
                    RawCacheMeteringTests._ExtrasOnly()
                )

        class _Client:
            class _Chat:
                completions = _Completions()

            chat = _Chat()

        proxy = live.ProviderUsageProxy(_Client(), sink)

        async def run() -> None:
            await proxy.chat.completions.create()
            await proxy.chat.completions.create()

        asyncio.run(run())
        self.assertEqual(2, len(sink))
        self.assertEqual([0, 1], [entry["raw_call_index"] for entry in sink])
        self.assertTrue(all(entry["cache_fields_present"] for entry in sink))
        self.assertIsInstance(sink[0]["off_peak"], bool)


class ReportSemanticsTests(unittest.TestCase):
    """No-answer and rendered-over-length are separate counts (attempt 1 conflated them)."""

    def _row(self, task: str, **overrides):
        row = {
            "scenario": task,
            "repeat": 0,
            "method": "none",
            "api_request_attempts": 8,
            "final_output": "",
            "render_reason": "retry_rejected:over_160_chars",
            "render_had_candidate": True,
            "render_rendered_chars": 208,
            "render_repair_calls": 1,
            "render_truncated": False,
            "render_fields_dropped": False,
            "render_report": {
                "source_chars": 240,
                "rendered_chars": 208,
                "render_accepted": False,
                "facts_complete": False,
                "frozen_regex": False,
                "single_line_prefix": False,
                "within_160": False,
            },
            "success": False,
            "error_type": "",
        }
        row.update(overrides)
        return row

    def test_provider_error_and_over_length_are_counted_separately(self) -> None:
        tasks = list(registry.task_ids())
        error_row = self._row(
            tasks[0],
            api_request_attempts=1,
            render_reason="no_final_answer",
            render_had_candidate=False,
            render_rendered_chars=0,
            render_repair_calls=0,
            error_type="BadRequestError",
        )
        accepted_row = self._row(
            tasks[2],
            final_output="RESULT issue=x cause=y fix=z",
            render_reason="accepted_first",
            render_rendered_chars=30,
            render_repair_calls=0,
            render_report={
                "source_chars": 90,
                "rendered_chars": 30,
                "render_accepted": True,
                "facts_complete": True,
                "frozen_regex": True,
                "single_line_prefix": True,
                "within_160": True,
            },
            success=True,
        )
        report = runner.manifest_report([error_row, self._row(tasks[1]), accepted_row])
        self.assertEqual(3, report["samples"])
        self.assertEqual(2, report["samples_with_a_candidate"])
        self.assertEqual(1, report["samples_without_a_candidate"])
        self.assertEqual(
            {"provider_error:BadRequestError": 1}, report["samples_without_a_candidate_reasons"]
        )
        self.assertEqual(1, report["post_render_over_160_count"])
        self.assertEqual(0.5, report["post_render_over_160_share_over_candidates"])
        self.assertEqual(0.3333, report["post_render_over_160_share_over_all_samples"])
        self.assertEqual(1, report["render_accepted_count"])
        self.assertEqual(0.5, report["observed_retry_rate_over_candidates"])

    def test_a_batch_without_candidates_reports_no_length_measurement(self) -> None:
        rows = [
            self._row(
                task,
                render_reason="no_final_answer",
                render_had_candidate=False,
                render_rendered_chars=0,
                render_repair_calls=0,
                error_type="BadRequestError",
            )
            for task in registry.task_ids()
        ]
        report = runner.manifest_report(rows)
        self.assertEqual(0, report["samples_with_a_candidate"])
        self.assertEqual(3, report["samples_without_a_candidate"])
        self.assertIsNone(report["post_render_over_160_share_over_candidates"])
        self.assertEqual(0, report["post_render_over_160_count"])
        self.assertIsNone(report["observed_retry_rate_over_candidates"])
        self.assertFalse(report["attainable_on_this_batch"])


class ManifestFinalisationTests(unittest.TestCase):
    def _root(self, directory: str) -> Path:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        (root / "manifest.json").write_text(
            json.dumps({"experiment_id": "unit-test"}), encoding="utf-8"
        )
        row = {
            "scenario": registry.task_ids()[0],
            "repeat": 0,
            "method": "none",
            "api_request_attempts": 7,
            "final_output": "RESULT issue=x cause=y fix=z",
            "render_report": {
                "source_chars": 90,
                "rendered_chars": 30,
                "render_accepted": True,
                "facts_complete": True,
                "frozen_regex": True,
                "single_line_prefix": True,
                "within_160": True,
            },
            "render_repair_calls": 0,
            "render_truncated": False,
            "render_fields_dropped": False,
            "provider_calls": [],
            "success": True,
        }
        (root / "samples.jsonl").write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return root

    def test_finalisation_writes_once_and_refuses_a_second_pass(self) -> None:
        freeze = {
            "path": "integrations/openai_agents/V16_ACQUISITION_FREEZE_20261006.json",
            "sha256": runner.FREEZE_SHA256,
            "matrix": {"tasks": list(registry.task_ids()), "arms": ["none"], "repeats": 1},
            "renderer_sha256": "0" * 64,
            "source_of_record": {},
        }
        amendment = {
            "path": "integrations/openai_agents/V16_ACQUISITION_FREEZE_AMENDMENT_20261006.json",
            "sha256": "0" * 64,
            "pinned_files": {},
        }
        sources = {"files": {}, "all_match": True, "count": 0}
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root(tmp)
            manifest = runner.finalise_manifest(
                root,
                freeze=freeze,
                amendment=amendment,
                sources=sources,
                status=0,
                stopped_early=False,
                stop_reason="",
            )
            self.assertTrue(manifest["v16_manifest_finalized"])
            self.assertEqual(7, manifest["api_calls"])
            self.assertFalse(manifest["citable_as_saving"])
            self.assertFalse(manifest["citable_as_quality_equivalence"])
            self.assertEqual(amendment, manifest["freeze_amendment"])
            self.assertTrue(manifest["source_of_record_verified"]["all_match"])
            report = manifest["batch_report"]["per_sample"][0]
            for name in live.PER_SAMPLE_FIELDS:
                self.assertIn(name, report, name)
            self.assertEqual(
                list(live.PER_SAMPLE_FIELDS), manifest["criteria"]["per_sample_report"]
            )
            self.assertTrue(manifest["request_budget"]["worst_case_fits_the_cap"])
            with self.assertRaises(SystemExit):
                runner.finalise_manifest(
                    root,
                    freeze=freeze,
                    amendment=amendment,
                    sources=sources,
                    status=0,
                    stopped_early=False,
                    stop_reason="",
                )


class FreezePinningTests(unittest.TestCase):
    def test_the_freeze_records_a_hash_for_every_source_of_record_file(self) -> None:
        freeze = json.loads(runner.FREEZE.read_text(encoding="utf-8"))
        recorded = freeze["source_of_record"]
        self.assertEqual(4, len(recorded))
        for relative, digest in recorded.items():
            self.assertEqual(64, len(str(digest)), relative)
            self.assertEqual(
                str(digest),
                hashlib.sha256((runner.REPO / relative).read_bytes()).hexdigest(),
                relative,
            )

    def test_the_amendment_pins_the_implementation_and_verifies(self) -> None:
        amendment = runner.verify_amendment()
        self.assertEqual(runner.FREEZE_SHA256, amendment["freeze_sha256"])
        self.assertTrue(amendment["freeze_unchanged"])
        self.assertGreaterEqual(amendment["pinned_count"], 5)
        self.assertGreaterEqual(amendment["amendment_entries"], 3)
        self.assertIn(
            "tests/test_openai_agents_v16_live_runner_zero_api.py", amendment["archived_files"]
        )
        for relative, entry in amendment["pinned_files"].items():
            self.assertTrue(entry["matches"], relative)
            self.assertEqual(entry["recorded_sha256"], entry["current_sha256"], relative)
            self.assertTrue(entry["reason"], relative)
            self.assertIn(entry["matched_entry"], range(len(entry["recorded_digests"])))
        self.assertEqual(len(amendment["pinned_files"]), amendment["pinned_count"])
        self.assertGreaterEqual(len(amendment["archived_files"]), 1)
        replacement = amendment["runtime_prompt_replacement"]
        self.assertIsNotNone(replacement)
        self.assertEqual(
            "experiments/runners/run_openai_agents_api_experiment.py", replacement["file"]
        )
        self.assertEqual(
            hashlib.sha256(
                (runner.REPO / replacement["file"]).read_bytes()
            ).hexdigest(),
            replacement["file_sha256"],
        )
        self.assertEqual(0, replacement["file_edited"] if "file_edited" in replacement else 0)
        self.assertTrue(replacement["canary"])
        self.assertEqual(0, amendment["files_changed_after_freeze"])

    def test_source_of_record_evidence_matches_on_disk(self) -> None:
        freeze = runner.verify_freeze()
        evidence = runner.source_of_record_evidence(freeze)
        self.assertEqual(4, evidence["count"])
        self.assertTrue(evidence["all_match"])

    def test_a_drifted_pinned_file_stops_the_batch(self) -> None:
        record = json.loads(runner.AMENDMENT.read_text(encoding="utf-8"))
        target = "experiments/runners/openai_agents_v16_live_contract.py"
        for amendment in record["amendments"]:
            for entry in amendment["additional_files_edited_after_freeze"]:
                if entry["path"] == target:
                    entry["new_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as tmp:
            drifted = Path(tmp) / "amendment.json"
            drifted.write_text(json.dumps(record), encoding="utf-8")
            original = runner.AMENDMENT
            runner.AMENDMENT = drifted
            try:
                with self.assertRaises(SystemExit):
                    runner.verify_amendment()
            finally:
                runner.AMENDMENT = original


class CaseShapeTests(unittest.TestCase):
    def test_case_matches_the_frozen_matrix(self) -> None:
        for task_id in registry.task_ids():
            case = runner.build_case(task_id, 0)
            entry = registry.task(task_id)
            self.assertEqual(task_id, case.scenario)
            self.assertEqual(entry["instance_id"], case.codename)
            self.assertEqual(7, case.expected_model_calls)
            self.assertEqual(tuple(entry["tool_names"]) * 2, tuple(case.expected_tool_names))
            self.assertEqual(3, len(case.tools))
            self.assertEqual(tuple(entry["required_terms"]), tuple(case.expected_terms))
            self.assertEqual(entry["answer_pattern"], case.answer_pattern)
            self.assertTrue(case.allow_repeat_tools)
            self.assertEqual(3, len(case.history))

    def test_planned_requests_fit_the_frozen_cap(self) -> None:
        plan = runner.planned_requests()
        self.assertEqual(3, plan["samples"])
        self.assertEqual(24, plan["worst_case_logical_requests_with_one_retry_each"])
        self.assertTrue(plan["worst_case_fits_the_cap"])
        self.assertEqual(30, plan["max_api_requests"])


if __name__ == "__main__":
    unittest.main()
