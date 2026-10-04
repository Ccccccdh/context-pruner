"""Zero-API gate for OpenAI Agents v5 evidence-safe retention.

Three levels are exercised, all without any provider request:

* the real SDK ``Runner`` through the v5 runner's own ``main()`` with a local stub
  provider that mirrors the frozen cases' trajectory (one tool call per model
  call), running the full Django grid: 3 repeats x 3 arms;
* the retention filter directly on the real public task payload, where the v5
  eligibility rule can be checked byte-exactly;
* a child process that forces a registered-literal loss end-to-end, to prove the
  failure is counted and falls back for the whole payload instead of silently
  dropping a constraint-bearing unit.

Asserted per sample of the offline grid:

(a) every registered constraint literal - ``filter``, ``other annotations``,
    ``ordering`` and ``existing_annotations`` (which exists only inside a
    source-code tool output) - is present in the final ``model_input``;
(b) protected Responses tool-group boundaries are unchanged, and the only
    tool-output text change is this module's own note;
(c) ``task_anchor_restore_failures == 0`` on the happy path, and is counted (with
    a whole-payload fallback and no claimed compaction) when a constraint-bearing
    output is forced to be lost;
(d) the plugin arm produces a real measured input reduction, and every elided
    output's note carries a recoverable path/line pointer;
(e) the whole grid runs with no network.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from openai.types.responses import ResponseOutputMessage, ResponseOutputText

from experiments.runners import openai_agents_literal_registry_v5 as registry
from experiments.runners import run_openai_agents_repo_diagnostic_v4 as v4
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v5 as v5
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
    _text_sha,
    normalise_unit,
    recent_tool_group_indices,
    tool_name_by_call_id,
)

ROOT = Path(__file__).resolve().parents[1]
DJANGO_ANSWER = (
    "RESULT issue=django-16263 cause=existing_annotations force subquery "
    "fix=prune annotations not referenced by filters other annotations or ordering"
)
#: Injected fault: pretend the registry sees a literal in no output at all, which
#: is exactly the information the v4 eligibility rule was missing.
CARRIER_BLIND_ENV = "DSH_V5_GATE_BLIND_CARRIERS"
#: Injected fault: make the literal-survival guard report a loss.
GUARD_FAULT_ENV = "DSH_V5_GATE_FORCE_GUARD_FAULT"
REQUIRED_LITERAL_LABELS = (
    "filter_references",
    "other_annotation_references",
    "ordering_references",
    "aggregation_decision",
)
RETENTION_ROWS = (
    "task_anchor_restore_failures",
    "selective_retention_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "selective_retention_pinned_outputs",
    "selective_retention_elided_outputs",
    "selective_retention_notes_with_source_pointer",
    "selective_retention_recent_group_kept_in_full",
    "selective_retention_literal_guard_fallbacks",
)


def _load_gate_helpers():
    """The same memoised helper module the runner's offline gate instantiates."""
    return v5._gate_helpers()


def _sequential_contract_stub(helpers):
    """A stub whose trajectory matches the frozen cases: one tool call per turn."""

    class SequentialContractStub(helpers.StubApiModel):
        def _output_for_call(self, call_index: int):
            expected = list(self.case.expected_tool_names)
            if call_index < len(expected):
                return [self._tool_call(expected[call_index], 0)]
            return [self._final_message()]

        def _final_message(self) -> ResponseOutputMessage:
            return ResponseOutputMessage(
                id=f"msg_{self.case.scenario}",
                content=[
                    ResponseOutputText(annotations=[], text=DJANGO_ANSWER, type="output_text")
                ],
                role="assistant",
                status="completed",
                type="message",
            )

    return SequentialContractStub


def _grid_args(output: Path, experiment_id: str) -> list[str]:
    return [
        "--offline-gate", "--confirm-send-public-source",
        "--scenarios", "django_count_annotations",
        "--methods", "none,pruner_v1,native_summary",
        "--repeats", "3",
        "--provider-soft", "2000", "--provider-target", "1500", "--provider-hard", "6000",
        "--max-output-tokens", "1024", "--max-api-requests", "100",
        "--out", str(output), "--experiment-id", experiment_id,
    ]


def _registered_case(repeat: int = 0):
    case = v1.build_case("django_count_annotations", repeat)
    return v5._CaseWithTaskStatement(case, v5.task_statement(case))


def _django_payload(case) -> list[dict]:
    """The payload the SDK actually submits, in the frozen trajectory order."""
    items: list[dict] = [dict(message) for message in case.history]
    for index, name in enumerate(case.expected_tool_names):
        items.append(
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"codename": case.codename}),
                "call_id": f"call_{case.scenario}_{index}_{name}",
            }
        )
        items.append(
            {
                "type": "function_call_output",
                "call_id": f"call_{case.scenario}_{index}_{name}",
                "output": v1.source_view(case.scenario, index),
            }
        )
    return items


class EvidenceSafeRetentionV5Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_gate_helpers()
        cls.helpers.StubApiModel = _sequential_contract_stub(cls.helpers)
        cls.temp = tempfile.mkdtemp(prefix="v5-gate-")
        # Prove the offline grid needs no credential: remove both provider keys for
        # the duration of the run and restore them afterwards.
        saved = {name: os.environ.pop(name, None) for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
        try:
            status = v5.main(_grid_args(Path(cls.temp), "v5-offline-grid"))
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        assert status == 0, "offline grid run failed"
        cls.batch = Path(cls.temp) / "v5-offline-grid"
        cls.rows = [
            json.loads(line)
            for line in (cls.batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    # -- (e) the full grid runs without network ---------------------------

    def test_grid_runs_without_network(self):
        keys = [(row["scenario"], row["repeat"], row["method"]) for row in self.rows]
        expected = [
            ("django_count_annotations", repeat, method)
            for repeat in range(3)
            for method in ("none", "pruner_v1", "native_summary")
        ]
        self.assertEqual(sorted(expected), sorted(keys))
        self.assertEqual(9, len(self.rows))
        for row in self.rows:
            self.assertEqual("", row["error_type"], row["error_message"])
            self.assertTrue(row["success"], (row["method"], row["repeat"]))
        manifest = json.loads((self.batch / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("public_source_diagnostic", manifest["data_class"])
        self.assertNotEqual("synthetic_only", manifest["data_class"])
        self.assertIn("public swe-bench", manifest["disclosure"].lower())
        self.assertTrue(manifest["shared_runner_disclosure_amended"])
        self.assertEqual("evidence_safe_retention_v5", manifest["mechanism"]["name"])
        self.assertEqual(registry.REGISTRY_SCHEMA, manifest["mechanism"]["registry"])
        self.assertEqual([], manifest["literal_registry"]["problems"])
        self.assertEqual(
            registry.registry_fingerprint("django_count_annotations"),
            manifest["literal_registry"]["fingerprints"]["django_count_annotations"],
        )
        self.assertEqual(6000, int(manifest["budget_calibration"]["provider_tokens"]["hard"]))
        self.assertGreater(int(manifest["filter_hard_bytes"]), 0)
        for field in ("task_anchor_restore_failures", "selective_retention_literal_guard_fallbacks"):
            self.assertIn(field, manifest["persisted_retention_fields"])

    # -- (a) every registered literal reaches the model -------------------

    def test_literal_registry_verifies_against_the_public_ranges(self):
        self.assertEqual([], registry.all_problems())
        # The Django literal that exists only inside a tool output must be
        # registered as a constraint and attributed to its carrier.
        carriers = {
            source.tool: source.literal_labels
            for source in registry.SOURCES["django_count_annotations"]
            if source.literal_labels
        }
        self.assertIn("read_django_aggregation", carriers)
        self.assertIn("aggregation_decision", carriers["read_django_aggregation"])
        self.assertIn("ordering_references", carriers["read_django_aggregation"])
        self.assertIn("read_django_count_call", carriers)
        self.assertIn("filter_references", carriers["read_django_count_call"])
        self.assertNotIn("read_django_count_entry", carriers)

    def test_every_registered_literal_is_in_the_final_model_input(self):
        for row in self.rows:
            model_inputs = [r for r in self._records(row) if r["stage"] == "model_input"]
            self.assertTrue(model_inputs, row["method"])
            final = model_inputs[-1]
            for label in REQUIRED_LITERAL_LABELS:
                self.assertTrue(
                    final["registered_literals_present"][label],
                    f"{label} missing from the final model input of "
                    f"{row['method']} r{row['repeat']}",
                )
                self.assertTrue(
                    final["registered_literal_counts"][label] > 0,
                    f"{label} has no occurrence in the final model input",
                )
            # ``existing_annotations`` exists only inside a source-code tool
            # output, so it is required from the first boundary that carries one;
            # the three issue-statement literals are required at every boundary.
            statement_labels = (
                "filter_references",
                "other_annotation_references",
                "ordering_references",
            )
            for record in model_inputs:
                carried = any(
                    entry.get("registered_literals") for entry in record["output_manifest"]
                )
                for label in statement_labels:
                    self.assertTrue(
                        record["registered_literals_present"][label],
                        f"{label} missing at model boundary {record['index']} of "
                        f"{row['method']} r{row['repeat']}",
                    )
                if carried:
                    self.assertTrue(
                        record["registered_literals_present"]["aggregation_decision"],
                        f"existing_annotations missing at a boundary that carries a tool "
                        f"output: {row['method']} r{row['repeat']} boundary {record['index']}",
                    )
            self.assertEqual(
                all(
                    bool(value)
                    for value in row["evidence_registered_literals_present_final"].values()
                ),
                True,
                (row["method"], row["repeat"]),
            )

    def test_no_registered_literal_is_lost_across_any_boundary(self):
        for row in self.rows:
            for record in self._records(row):
                for label, count in record["registered_literal_counts"].items():
                    self.assertGreaterEqual(
                        int(count),
                        0,
                        f"negative literal count for {label}",
                    )
            boundaries = [
                r for r in self._records(row) if r["stage"] in ("filter_before", "filter_after")
            ]
            for previous, following in zip(boundaries[0::2], boundaries[1::2]):
                for label in REQUIRED_LITERAL_LABELS:
                    self.assertGreaterEqual(
                        int(following["registered_literal_counts"][label]),
                        int(previous["registered_literal_counts"][label]),
                        f"{label} lost across the filter boundary of {row['method']} "
                        f"r{row['repeat']}",
                    )

    # -- (b) tool groups unchanged, and only noted text may change --------

    def test_tool_group_boundaries_and_text_transitions(self):
        for row in self.rows:
            records = self._records(row)
            before = [r for r in records if r["stage"] == "filter_before"]
            after = [r for r in records if r["stage"] == "filter_after"]
            self.assertEqual(len(before), len(after))
            if row["method"] == "pruner_v1":
                self.assertTrue(before, "the plugin arm installed no filter")
            for previous, following in zip(before, after):
                self.assertEqual(
                    [group["group_id"] for group in previous["protected_groups"]],
                    [group["group_id"] for group in following["protected_groups"]],
                    "tool group boundaries moved",
                )
                self.assertEqual(
                    [group["tool_output_count"] for group in previous["protected_groups"]],
                    [group["tool_output_count"] for group in following["protected_groups"]],
                    "a protected tool call/output pair was split",
                )
                # ``group_sha256`` covers the group's full content, so it is
                # expected to change exactly when an output text is replaced by
                # this module's note; the identity fields are what must never
                # move, and they are asserted above.
                notes_added = (
                    following["compaction_note_count"] - previous["compaction_note_count"]
                )
                if notes_added == 0:
                    self.assertEqual(
                        [group["group_sha256"] for group in previous["protected_groups"]],
                        [group["group_sha256"] for group in following["protected_groups"]],
                        "a protected tool group hash changed without a note",
                    )
                    self.assertEqual(
                        [group["tool_output_sha256"] for group in previous["protected_groups"]],
                        [group["tool_output_sha256"] for group in following["protected_groups"]],
                        "tool output changed without an auditable compaction note",
                    )
                else:
                    self.assertGreater(notes_added, 0)
                    self.assertLess(
                        following["input_bytes"],
                        previous["input_bytes"],
                        "a compaction must shrink the payload",
                    )
                # Every replaced output must carry a recoverable pointer.
                for entry in following["output_manifest"]:
                    if entry["output_replaced_by_note"]:
                        self.assertTrue(entry["registered_source_pointer"])
                        self.assertTrue(entry["note_has_source_pointer"])
            model_hashes = {r["input_sha256"] for r in records if r["stage"] == "model_input"}
            for record in after:
                self.assertIn(record["input_sha256"], model_hashes)

    def test_protected_tool_group_hashes_are_unchanged_on_the_real_payload(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = _django_payload(case)
        before = retention._group_hashes(items)
        self.assertTrue(before)
        # The first thing the model ever sees is the statement alone.
        retention.observe([dict(message) for message in case.history])
        retention.observe_delivered([dict(message) for message in case.history])
        # Nothing may be elided on the first sight of the tool outputs.
        self.assertIsNone(retention.filter_items(items))
        self.assertEqual(before, retention._group_hashes(items))
        self.assertEqual(0, retention.group_hash_mismatch_count)
        self.assertEqual(sorted(before), sorted(retention._group_hash_baseline))

    def test_constraint_carriers_are_pinned_verbatim(self):
        """The v4 defect, at the unit level: a carrier is never noted away."""
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = _django_payload(case)
        # Deliver everything once so the v4 rule would consider it removable.
        retention.observe(items)
        retention.observe_delivered(items)
        classified = retention.classify_outputs(items)
        by_tool = {
            entry["tool"]: entry for entry in classified["outputs"] if entry["tool"]
        }
        self.assertEqual(
            ["ordering_references", "aggregation_decision"],
            list(by_tool["read_django_aggregation"]["registered_literals"]),
        )
        self.assertTrue(by_tool["read_django_aggregation"]["pinned"])
        self.assertEqual(
            "registered_literal", by_tool["read_django_aggregation"]["pinned_reasons"][0]
        )
        self.assertEqual(
            ["filter_references"],
            list(by_tool["read_django_count_call"]["registered_literals"]),
        )
        self.assertTrue(by_tool["read_django_count_call"]["pinned"])
        self.assertEqual([], list(by_tool["read_django_count_entry"]["registered_literals"]))
        self.assertFalse(by_tool["read_django_count_entry"]["pinned"])

    def test_the_most_recent_tool_group_is_kept_in_full(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = _django_payload(case)
        recent = recent_tool_group_indices(items)
        self.assertEqual({6, 7}, recent, "the newest call/output pair is the newest group")
        retention.observe(items)
        retention.observe_delivered(items)
        decision = retention.classify_outputs(items)
        newest = [
            entry
            for entry in decision["outputs"]
            if "most_recent_tool_group" in entry["pinned_reasons"]
        ]
        self.assertEqual(["read_django_count_call"], [entry["tool"] for entry in newest])

    def test_compaction_notes_carry_path_and_line_pointers(self):
        """A real reduction that is fully recoverable by re-reading the source."""
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = _django_payload(case)
        # First three turns: the model has seen each source view exactly once.
        delivered: list[dict] = [dict(message) for message in case.history]
        for index, name in enumerate(case.expected_tool_names):
            delivered = delivered + [
                {
                    "type": "function_call",
                    "name": name,
                    "arguments": "{}",
                    "call_id": f"call_django_count_annotations_{index}_{name}",
                },
                {
                    "type": "function_call_output",
                    "call_id": f"call_django_count_annotations_{index}_{name}",
                    "output": v1.source_view(case.scenario, index),
                },
            ]
            retention.observe(delivered)
            retention.observe_delivered(delivered)
        # One more tool group arrives, pushing the first output out of the newest
        # group, so it becomes eligible - and it is the one with no constraint.
        final = delivered + [
            {"type": "function_call", "name": "read_django_count_call", "arguments": "{}", "call_id": "c9"},
            {"type": "function_call_output", "call_id": "c9", "output": v1.source_view(case.scenario, 2)},
        ]
        retention.observe(final)
        retention.observe_delivered(final)
        filtered = retention.filter_items(final)
        self.assertIsNotNone(filtered, retention.last_fallback_reason)
        self.assertEqual(1, retention.compacted_tool_outputs)
        self.assertEqual(0, retention.task_anchor_restore_failures)
        notes = [
            item for item in filtered
            if isinstance(item, dict) and str(item.get("output", "")).startswith(
                "[evidence-safe-retention v5]"
            )
        ]
        self.assertEqual(1, len(notes))
        note = notes[0]["output"]
        self.assertIn("django-16263/django/db/models/query.py", note)
        self.assertIn("lines 610-632", note)
        self.assertIn("re-issue read_django_count_entry", note)
        self.assertEqual(
            "call_django_count_annotations_0_read_django_count_entry", notes[0]["call_id"]
        )
        # The constraint carriers survived verbatim.
        self.assertIn("existing_annotations", json.dumps(filtered))
        self.assertEqual([], retention._carrier_loss(final, filtered))
        self.assertEqual([], retention._missing_protected_units(filtered))
        self.assertEqual([], retention._missing_literal_units(filtered))

    def test_a_text_without_a_registered_pointer_is_never_elided(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        blob = "x" * 900
        items = [dict(message) for message in case.history]
        items += [
            {"type": "function_call", "name": "read_django_count_entry", "arguments": "{}", "call_id": "c1"},
            {"type": "function_call_output", "call_id": "c1", "output": blob},
            {"type": "function_call", "name": "read_django_count_entry", "arguments": "{}", "call_id": "c2"},
            {"type": "function_call_output", "call_id": "c2", "output": blob},
            {"type": "function_call", "name": "read_django_count_entry", "arguments": "{}", "call_id": "c3"},
            {"type": "function_call_output", "call_id": "c3", "output": blob},
        ]
        retention.observe(items)
        retention.observe_delivered(items)
        filtered = retention.filter_items(items)
        self.assertIsNone(filtered)
        self.assertEqual("", retention.last_fallback_reason)
        self.assertEqual(0, retention.compacted_tool_outputs)
        self.assertEqual(
            2, retention.last_call_decision["pinned_by_reason"].get("no_recovery_pointer", 0)
        )

    # -- (c) restore-failure counting and whole-payload fallback ----------

    def test_restore_failures_are_persisted_and_zero_on_the_happy_path(self):
        for row in self.rows:
            for field in RETENTION_ROWS:
                self.assertIn(field, row)
            self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
            self.assertEqual(0, row["task_anchor_restore_failures"], (row["method"], row["repeat"]))
            self.assertEqual(0, row["task_restore_fallbacks"])
            self.assertEqual(0, row["selective_retention_literal_guard_fallbacks"])
            self.assertEqual({}, row["selective_retention_fallback_reasons"])
            self.assertEqual(
                row["task_anchor_restore_failures"],
                row["input_evidence_task_anchor_restore_failures"],
            )
            self.assertEqual([0] * row["model_calls"], row["restore_failures_by_model_call"])
            self.assertEqual(
                [0] * row["model_calls"], row["budget_fallbacks_by_model_call"]
            )

    def test_forced_constraint_loss_is_counted_and_falls_back(self):
        """A carrier replaced by a note is caught, counted, and whole-prefix reverted.

        The payload is built so the v4 rule's own premise holds - the carrier's
        text was already delivered verbatim - and the only thing standing between
        the policy and a literal loss is the guard this version adds.
        """
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        aggregation = v1.source_view(case.scenario, 1)
        # A short note so the payload is still a net reduction and only the
        # literal guard stands between the policy and an information loss.
        note = "[note] read_django_aggregation already served"
        damaged = _django_payload(case)
        for item in damaged:
            if item.get("call_id", "").endswith("read_django_aggregation"):
                item["output"] = note
        self.assertNotIn("existing_annotations", json.dumps(damaged))
        retention.observe(damaged)
        retention.observe_delivered(damaged)
        self.assertIsNone(retention.filter_items(damaged))
        self.assertGreaterEqual(retention.task_anchor_restore_failures, 1)
        self.assertEqual(
            retention.task_anchor_restore_failures, retention.task_restore_fallbacks
        )
        self.assertEqual(0, retention.compacted_calls)
        self.assertTrue(
            str(retention.last_fallback_reason).startswith("registered_literal_lost:"),
            retention.last_fallback_reason,
        )
        self.assertIn("existing_annotations", retention.last_fallback_reason)
        self.assertIn("read_django_aggregation", retention.last_fallback_reason)

    def test_injected_missing_protected_unit_falls_back_for_the_whole_payload(self):
        case = _registered_case()
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        items = _django_payload(case)

        def damaged_filter(payload, recent):
            del payload, recent
            return [], 1, 1, {"call": 1}

        retention._filter = damaged_filter
        self.assertIsNone(retention.filter_items(items))
        self.assertEqual(1, retention.task_anchor_restore_failures)
        self.assertEqual(1, retention.task_restore_fallbacks)
        self.assertTrue(
            retention.last_fallback_reason.startswith("registered_literal_lost"),
            retention.last_fallback_reason,
        )
        self.assertEqual(0, retention.compacted_calls)

    def test_pinning_above_the_hard_budget_falls_back_and_counts(self):
        """If pinning cannot fit the budget, fall back - never drop a carrier."""
        case = _registered_case()
        items = _django_payload(case)
        retention = SelectiveRetentionFilter(
            task=case.scenario,
            task_statement=case.task_statement,
            hard_limit_bytes=64,
        )
        delivered: list[dict] = [dict(message) for message in case.history]
        for index, name in enumerate(case.expected_tool_names):
            delivered = delivered + [
                {"type": "function_call", "name": name, "arguments": "{}", "call_id": f"c{index}"},
                {
                    "type": "function_call_output",
                    "call_id": f"c{index}",
                    "output": v1.source_view(case.scenario, index),
                },
            ]
            retention.observe(delivered)
            retention.observe_delivered(delivered)
        final = delivered + [
            {"type": "function_call", "name": "read_django_count_call", "arguments": "{}", "call_id": "c9"},
            {"type": "function_call_output", "call_id": "c9", "output": v1.source_view(case.scenario, 2)},
        ]
        retention.observe(final)
        retention.observe_delivered(final)
        self.assertIsNone(retention.filter_items(final))
        self.assertEqual(1, retention.budget_fallbacks)
        self.assertEqual(1, retention.failure_reasons["hard_budget_exceeded"])
        self.assertEqual(0, retention.compacted_calls)
        # The budget fallback is not a constraint loss.
        self.assertEqual(0, retention.task_anchor_restore_failures)

    def test_injected_loss_through_the_real_sdk_runner_is_counted(self):
        result = subprocess.run(
            [sys.executable, "-m", "unittest",
             "tests.test_openai_agents_evidence_safe_retention_v5.EvidenceSafeFaultRun", "-v"],
            cwd=str(ROOT),
            env={**os.environ, GUARD_FAULT_ENV: "1"},
            capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
        self.assertTrue(payload, result.stdout)
        summary = json.loads(payload[-1])
        self.assertGreaterEqual(summary["task_anchor_restore_failures"], 1)
        self.assertEqual(0, summary["compacted_calls"])
        self.assertGreaterEqual(summary["samples"], 3)

    def test_audit_detects_the_v4_defect_and_quality_tampering(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v5 as independent

        freeze_path = (
            ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V5_EVIDENCE_SAFE_RETENTION_FREEZE.json"
        )
        baseline = independent.audit(self.batch, freeze_path, "v5-offline-grid")
        self.assertEqual([], baseline["issues"], baseline["issues"])
        self.assertTrue(baseline["complete"])
        self.assertEqual(0, baseline["task_anchor_restore_failures"]["pruner_v1"])

        original = (self.batch / "samples.jsonl").read_text(encoding="utf-8")
        plugin_evidence = {
            row["method"]: self.batch / row["input_evidence_file"]
            for row in self.rows
            if row["method"] == "pruner_v1"
        }
        originals = {name: p.read_text(encoding="utf-8") for name, p in plugin_evidence.items()}

        def write(rows_to_write):
            (self.batch / "samples.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows_to_write) + "\n", encoding="utf-8"
            )

        try:
            # 1. the literal that v4 lost, removed from the final model input.
            name, path = next(iter(plugin_evidence.items()))
            records = [
                json.loads(line) for line in originals[name].splitlines() if line.strip()
            ]
            for record in records:
                if record["stage"] == "model_input" and record["index"] == max(
                    r["index"] for r in records if r["stage"] == "model_input"
                ):
                    record["registered_literals_present"]["aggregation_decision"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(
                any("aggregation_decision" in issue for issue in issues), issues
            )
            path.write_text(originals[name], encoding="utf-8")

            # 2. a constraint-carrying output replaced by a note.
            for record in records:
                if record["stage"] == "model_input":
                    for entry in record["output_manifest"]:
                        if entry["registered_literals"]:
                            entry["output_replaced_by_note"] = True
                            entry["pinned"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(
                any("replaced by a note" in issue for issue in issues), issues
            )
            path.write_text(originals[name], encoding="utf-8")

            # 3. a compaction note without a recoverable pointer.
            for record in records:
                for entry in record["output_manifest"]:
                    if entry["output_replaced_by_note"]:
                        entry["note_has_source_pointer"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(any("source pointer" in issue for issue in issues), issues)
            path.write_text(originals[name], encoding="utf-8")

            # 4. the most recent tool group reported as not fully kept.
            for record in records:
                if record["stage"] == "model_input":
                    record["most_recent_tool_group_kept_in_full"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(
                any("most recent tool group" in issue for issue in issues), issues
            )
            path.write_text(originals[name], encoding="utf-8")

            # 5. quality tampering still fails the audit.
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["success"] = not row["success"]
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(any("quality mismatch" in issue for issue in issues), issues)

            # 6. a restore failure that the row does not admit to.
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["task_anchor_restore_failures"] = 1
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"]
            self.assertTrue(any("restore-failure" in issue for issue in issues), issues)
        finally:
            (self.batch / "samples.jsonl").write_text(original, encoding="utf-8")
            for name, path in plugin_evidence.items():
                path.write_text(originals[name], encoding="utf-8")

    # -- (d) a real, recoverable measured reduction -----------------------

    def test_plugin_arm_reduces_the_actual_input(self):
        baseline = {
            row["repeat"]: row["actual_input_tokens"]
            for row in self.rows if row["method"] == "none"
        }
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        reductions = [
            (baseline[row["repeat"]] - row["actual_input_tokens"]) / baseline[row["repeat"]]
            for row in plugin
        ]
        self.assertTrue(all(value > 0 for value in reductions), reductions)
        for row in plugin:
            self.assertGreater(row["selective_retention_compacted_tool_outputs"], 0)
            self.assertGreater(row["selective_retention_saved_bytes_total"], 0)
            self.assertGreater(row["selective_retention_task_unit_count"], 0)
            self.assertGreater(row["selective_retention_literal_unit_count"], 0)
            self.assertEqual(
                row["selective_retention_literal_text_count"],
                len(registry.literals_for("django_count_annotations")),
            )
            self.assertGreater(row["selective_retention_pinned_outputs"], 0)
            self.assertTrue(row["selective_retention_recent_group_kept_in_full"])
            self.assertTrue(row["selective_retention_pointer_coverage"])
            self.assertEqual(
                row["selective_retention_elided_outputs"],
                row["selective_retention_notes_with_source_pointer"],
                "an elided output had no source pointer",
            )
            self.assertEqual(0, row["selective_retention_literal_guard_fallbacks"])
        report = json.loads((self.batch / "report.json").read_text(encoding="utf-8"))
        comparison = report["comparisons"]["pruner_v1_vs_none"]
        self.assertGreater(comparison["actual_input_savings_rate_vs_baseline"], 0)
        self.assertEqual(0.0, comparison["success_delta"])

    def test_baseline_and_native_arms_install_no_retention_filter(self):
        for row in self.rows:
            if row["method"] == "none":
                self.assertEqual(0, row["input_evidence_filter_calls"])
                self.assertEqual(0, row["selective_retention_pinned_outputs"])
                self.assertEqual(0, row["selective_retention_elided_outputs"])
            if row["method"] == "native_summary":
                self.assertEqual(0, row["selective_retention_elided_outputs"])
                self.assertEqual(0, row["selective_retention_pinned_outputs"])

    def test_offline_gate_rejects_a_batch_that_loses_a_carrier(self):
        """Sanity: with carrier detection blinded, the audit finds the loss.

        This is the control for the two audit assertions above - it shows the
        audit is not trivially passing because nothing was ever elided.
        """
        from experiments.audits import audit_openai_agents_repo_diagnostic_v5 as independent

        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertTrue(plugin)
        self.assertTrue(
            any(row["selective_retention_elided_outputs"] > 0 for row in plugin),
            "the plugin arm never elided anything, so the audit's pointer rule is untested",
        )
        freeze_path = (
            ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V5_EVIDENCE_SAFE_RETENTION_FREEZE.json"
        )
        self.assertEqual(
            [],
            independent.audit(self.batch, freeze_path, "v5-offline-grid")["issues"],
        )

    # -- helpers ----------------------------------------------------------

    def _records(self, row: dict) -> list[dict]:
        relative = row.get("input_evidence_file")
        if not relative:
            return []
        path = self.batch / relative
        if not path.is_file():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]


class EvidenceSafeFaultRun(unittest.TestCase):
    """Child-process gate: the same grid with the literal guard forced to fail.

    The fault is the smallest possible one - the guard reports that a registered
    literal was lost - so the run must (i) count every failure, (ii) fall back for
    the whole payload of that call, and (iii) never claim a compaction.
    """

    def test_forced_guard_failure_is_reported_in_the_result_row(self):
        if os.getenv(GUARD_FAULT_ENV) != "1":  # pragma: no cover - parent gate
            self.skipTest("only runs as the injected child of the v5 gate")
        helpers = _load_gate_helpers()
        helpers.StubApiModel = _sequential_contract_stub(helpers)
        temp = tempfile.mkdtemp(prefix="v5-fault-")
        try:
            # Poison the guard: every call reports that a registered literal was
            # lost, which must force a whole-payload fallback that is counted.
            original = SelectiveRetentionFilter._carrier_loss

            def lossy_carrier_loss(self, before, after):
                if not original(self, before, after):
                    return ["existing_annotations"]
                return original(self, before, after)

            SelectiveRetentionFilter._carrier_loss = lossy_carrier_loss
            try:
                status = v5.main(_grid_args(Path(temp), "v5-offline-fault"))
            finally:
                SelectiveRetentionFilter._carrier_loss = original
            self.assertEqual(0, status)
            rows = [
                json.loads(line)
                for line in (Path(temp) / "v5-offline-fault" / "samples.jsonl")
                .read_text(encoding="utf-8").splitlines()
                if line
            ]
            plugin = [row for row in rows if row["method"] == "pruner_v1"]
            self.assertEqual(3, len(plugin))
            for row in plugin:
                self.assertGreaterEqual(row["task_anchor_restore_failures"], 1)
                self.assertEqual(
                    row["task_anchor_restore_failures"],
                    row["input_evidence_task_anchor_restore_failures"],
                )
                self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
                self.assertGreaterEqual(
                    row["selective_retention_literal_guard_fallbacks"], 1
                )
                self.assertEqual(0, row["selective_retention_compacted_tool_outputs"])
                self.assertEqual(0, row["selective_retention_saved_bytes_total"])
                reasons = row["selective_retention_fallback_reasons"]
                self.assertTrue(
                    any(key.startswith("registered_literal_lost") for key in reasons),
                    reasons,
                )
            summary = {
                "samples": len(plugin),
                "task_anchor_restore_failures": sum(
                    row["task_anchor_restore_failures"] for row in plugin
                ),
                "compacted_calls": sum(
                    row["selective_retention_compacted_calls"] for row in plugin
                ),
            }
            print(json.dumps(summary))
        finally:
            shutil.rmtree(temp, ignore_errors=True)


class LegacyV4DefectRegression(unittest.TestCase):
    """The v4 rule, restated here, still loses the Django literal.

    This is a regression control, not a re-scoring of the frozen v4 batch: it
    shows that the defect v5 fixes is real and reproducible from the same payload,
    so the v5 assertions above are not vacuous.
    """

    def test_v4_style_eligibility_would_drop_the_existing_annotations_literal(self):
        case = _registered_case()
        items = _django_payload(case)
        aggregation = v1.source_view(case.scenario, 1)
        call_id = "call_django_count_annotations_1_read_django_aggregation"
        note = (
            f"[selective-retention v4] read_django_aggregation call_id={call_id} was "
            f"returned in full earlier in this conversation ({len(aggregation)} characters, "
            f"{len(aggregation.splitlines())} lines). It is not re-transmitted; use the copy "
            "already in the conversation."
        )
        damaged = [
            {**item, "output": note} if item.get("call_id") == call_id else item
            for item in items
        ]
        self.assertNotIn("existing_annotations", json.dumps(damaged))
        self.assertIn("existing_annotations", json.dumps(items))
        # v5's registry knows that tool must be pinned; v4's rule did not.
        carriers = {
            source.tool
            for source in registry.SOURCES["django_count_annotations"]
            if source.literal_labels
        }
        self.assertIn("read_django_aggregation", carriers)
        # And the v5 filter refuses to make that replacement.
        retention = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        retention.observe(items)
        retention.observe_delivered(items)
        filtered = retention.filter_items(items)
        rendered = json.dumps(filtered if filtered is not None else items)
        self.assertIn("existing_annotations", rendered)


if __name__ == "__main__":
    unittest.main()
