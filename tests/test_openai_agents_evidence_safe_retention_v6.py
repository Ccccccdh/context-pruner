"""Zero-API gate for OpenAI Agents v6 pointer retention.

Three levels are exercised, all without any provider request:

* the real SDK ``Runner`` through the v6 runner's own ``main()`` with a local stub
  provider that mirrors the frozen cases' trajectory (one tool call per model call),
  running the full Django grid: 3 repeats x 3 arms;
* the retention filter directly on the real public task payload, where the
  line-range elision can be checked byte-exactly (kept lines and omitted intervals
  must partition the reduced range, and no omitted line may carry a registered
  literal);
* a child process that forces a registered-literal loss end-to-end, to prove the
  failure is counted and falls back for the whole payload.

Asserted per sample of the offline grid:

(a) every registered constraint literal - ``filter``, ``other annotations``,
    ``ordering`` and ``existing_annotations`` (which exists only inside a
    source-code tool output) - is present in the final ``model_input``, and its
    occurrence count never falls below the baseline arm's at the same boundary;
(b) protected Responses tool-group boundaries are unchanged, and the only
    tool-output text change is this module's own pointer note;
(c) ``task_anchor_restore_failures == 0`` and ``budget_fallbacks == 0`` on the happy
    path, and both are counted (with a whole-payload fallback and no claimed
    compaction) when the literal guard is forced to fail;
(d) the plugin arm produces a **measured input lower than the baseline on at least
    one sample**, and the projected paid saving that follows from the measured
    payload reduction is stated and positive;
(e) the v5 protected-group false positive is fixed: the same payload sequence that
    makes the frozen v5 filter fall back once makes v6 fall back zero times;
(f) the whole grid runs with no network.
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

from context_pruner.adapters.openai_agents import _protect_responses_groups
from context_pruner.types import estimate_tokens

from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v5 as v5
from experiments.runners import run_openai_agents_repo_diagnostic_v6 as v6
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
)
from experiments.runners.openai_agents_span_retention_v6 import (
    CONTEXT_LINES,
    NOTE_PREFIX,
    SpanRetentionFilter,
)

ROOT = Path(__file__).resolve().parents[1]
DJANGO_ANSWER = (
    "RESULT issue=django-16263 cause=existing_annotations force subquery "
    "fix=prune annotations not referenced by filters other annotations or ordering"
)
#: Injected fault: make the literal-survival guard report a loss.
GUARD_FAULT_ENV = "DSH_V6_GATE_FORCE_GUARD_FAULT"
#: Injected fault: revert the group baseline to the v5 (poisonable) behaviour.
BASELINE_FAULT_ENV = "DSH_V6_GATE_V5_BASELINE"
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
    "pointer_retention_elided_sources",
    "pointer_retention_elided_lines",
    "pointer_retention_kept_lines",
    "pointer_retention_kept_literal_lines",
    "pointer_retention_registry_fingerprint",
    "pointer_retention_base_registry_fingerprint",
    "selective_retention_pointer_coverage",
    "evidence_pointer_completeness_final",
    "evidence_span_elision_records",
)


def _load_gate_helpers():
    return v6._gate_helpers()


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
    return v5._CaseWithTaskStatement(case, v6.task_statement(case))


def _django_payload(case) -> list[dict]:
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


def _drive(cls, case, outputs=None):
    """Drive one filter over the frozen tool sequence; no API, no model."""
    outputs = outputs or [v1.source_view(case.scenario, i) for i in range(3)]
    retention = cls(task=case.scenario, task_statement=case.task_statement)
    history = [dict(message) for message in case.history]
    retention.observe(history)
    retention.observe_delivered(history)
    sent = list(history)
    events = []
    for index, name in enumerate(case.expected_tool_names):
        sent = sent + [
            {
                "type": "function_call",
                "name": name,
                "arguments": json.dumps({"codename": case.codename}),
                "call_id": f"call_{case.scenario}_{index}_{name}",
            },
            {
                "type": "function_call_output",
                "call_id": f"call_{case.scenario}_{index}_{name}",
                "output": outputs[index],
            },
        ]
        retention.observe(sent)
        retention.observe_delivered(sent)
        filtered = retention.filter_items(sent)
        after = sent if filtered is None else filtered
        events.append(
            {
                "index": index,
                "filtered": filtered,
                "fallback_reason": retention.last_fallback_reason,
                "literal_counts": retention.literal_counts(after),
                "fingerprints": {
                    str(group.group_id): (
                        retention._fingerprints(group)
                        if hasattr(retention, "_fingerprints")
                        else [
                            json.dumps(
                                {
                                    "type": str(item.get("type") or ""),
                                    "call_id": str(item.get("call_id") or ""),
                                },
                                sort_keys=True,
                            )
                            for item in group.items
                        ]
                    )
                    for group in _protect_responses_groups(sent, estimate_tokens)[1]
                },
            }
        )
        retention.observe(after)
        retention.observe_delivered(after)
    return retention, events

class PointerRetentionV6Gate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helpers = _load_gate_helpers()
        cls.temp = tempfile.mkdtemp(prefix="v6-gate-")
        saved = {name: os.environ.pop(name, None) for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
        try:
            status = v6.main(_grid_args(Path(cls.temp), "v6-offline-grid"))
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value
        assert status == 0, "offline grid run failed"
        cls.batch = Path(cls.temp) / "v6-offline-grid"
        cls.rows = [
            json.loads(line)
            for line in (cls.batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)

    # -- (f) the full grid runs without network ---------------------------

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
        self.assertEqual("span_retention_v6", manifest["mechanism"]["name"])
        self.assertEqual(registry.REGISTRY_SCHEMA, manifest["mechanism"]["registry"])
        self.assertEqual(CONTEXT_LINES, int(manifest["mechanism"]["context_lines"]))
        self.assertEqual([], manifest["literal_registry"]["problems"])
        self.assertEqual(
            registry.registry_fingerprint("django_count_annotations"),
            manifest["literal_registry"]["fingerprints"]["django_count_annotations"],
        )
        self.assertEqual(
            registry.base_registry_fingerprint("django_count_annotations"),
            manifest["literal_registry"]["base_fingerprints"]["django_count_annotations"],
        )
        self.assertEqual(6000, int(manifest["budget_calibration"]["provider_tokens"]["hard"]))
        self.assertEqual(65216, int(manifest["filter_hard_bytes"]))
        for field in ("task_anchor_restore_failures", "pointer_retention_elided_lines"):
            self.assertIn(field, manifest["persisted_retention_fields"])

    # -- (a) every registered literal reaches the model -------------------

    def test_span_registry_verifies_against_the_public_ranges(self):
        self.assertEqual([], registry.all_problems())
        self.assertEqual([], registry.base.all_problems())
        span = registry.span_for_tool("django_count_annotations", "read_django_aggregation")
        self.assertIsNotNone(span)
        self.assertEqual((438, 527), (span.first_line, span.last_line))
        self.assertEqual((444, 463, 475, 477, 478, 487), span.literal_lines)
        self.assertEqual(
            ("ordering_references", "aggregation_decision"), span.literal_labels
        )
        count_call = registry.span_for_tool("django_count_annotations", "read_django_count_call")
        self.assertEqual((550, 556), count_call.literal_lines)
        entry = registry.span_for_tool("django_count_annotations", "read_django_count_entry")
        self.assertEqual((), entry.literal_lines)

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
                self.assertGreater(final["registered_literal_counts"][label], 0)
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
                        f"{label} missing at boundary {record['index']} of "
                        f"{row['method']} r{row['repeat']}",
                    )
                if carried:
                    self.assertTrue(
                        record["registered_literals_present"]["aggregation_decision"],
                        f"existing_annotations missing at a boundary carrying a tool output: "
                        f"{row['method']} r{row['repeat']} boundary {record['index']}",
                    )
            self.assertTrue(
                all(
                    bool(value)
                    for value in row["evidence_registered_literals_present_final"].values()
                ),
                (row["method"], row["repeat"]),
            )

    def test_literal_counts_are_never_below_the_baseline_arm(self):
        """A per-literal, per-boundary comparison against the baseline arm."""
        baseline = {
            row["repeat"]: [
                record
                for record in self._records(row)
                if record["stage"] == "model_input"
            ]
            for row in self.rows
            if row["method"] == "none"
        }
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            model_inputs = [
                record for record in self._records(row) if record["stage"] == "model_input"
            ]
            reference = baseline[row["repeat"]]
            self.assertEqual(len(reference), len(model_inputs), row["repeat"])
            for index, record in enumerate(model_inputs):
                for label in REQUIRED_LITERAL_LABELS:
                    self.assertGreaterEqual(
                        int(record["registered_literal_counts"][label]),
                        int(reference[index]["registered_literal_counts"][label]),
                        f"{label} fell below the baseline at boundary {index} "
                        f"(r{row['repeat']})",
                    )

    def test_no_registered_literal_is_lost_across_any_boundary(self):
        for row in self.rows:
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

    # -- (b) tool groups unchanged, spans partition exactly ---------------

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
                elided = int(following["outputs_elided"]) - int(previous["outputs_elided"])
                if elided == 0:
                    self.assertEqual(
                        [group["group_sha256"] for group in previous["protected_groups"]],
                        [group["group_sha256"] for group in following["protected_groups"]],
                        "a protected tool group hash changed without an elision note",
                    )
                else:
                    self.assertLess(
                        following["input_bytes"],
                        previous["input_bytes"],
                        "an elision must shrink the payload",
                    )
                for entry in following["output_manifest"]:
                    if not entry["output_elided"]:
                        continue
                    self.assertTrue(entry["note_has_source_pointer"])
                    self.assertTrue(entry["pointer_completeness"])
            model_hashes = {r["input_sha256"] for r in records if r["stage"] == "model_input"}
            for record in after:
                self.assertIn(record["input_sha256"], model_hashes)

    def test_kept_lines_and_omitted_intervals_partition_the_range(self):
        """The core v6 claim: the note describes the omission truthfully."""
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            self.assertTrue(row["evidence_span_elision_records"])
            for entry in row["evidence_span_elision_records"]:
                first, last = entry["source_range"]
                expected = list(range(int(first), int(last) + 1))
                kept = sorted(int(value) for value in entry["kept_lines"])
                omitted: list[int] = []
                for interval in entry["omitted_intervals"]:
                    self.assertEqual(2, len(interval))
                    omitted.extend(range(int(interval[0]), int(interval[1]) + 1))
                self.assertEqual(sorted(expected), sorted(kept + omitted))
                self.assertEqual(len(set(kept + omitted)), len(kept + omitted))
                self.assertGreater(int(entry["omitted_line_count"]), 0)
                self.assertTrue(entry["note_states_pointer"])
                self.assertTrue(entry["pointer_matches_registered_source"])

    def test_no_omitted_line_carries_a_registered_literal(self):
        for row in self.rows:
            if row["method"] != "pruner_v1":
                continue
            for entry in row["evidence_span_elision_records"]:
                tool = entry["tool"]
                registered = set(registry.span_for_tool(row["scenario"], tool).literal_lines)
                kept = set(int(value) for value in entry["kept_lines"])
                omitted: set[int] = set()
                for interval in entry["omitted_intervals"]:
                    omitted.update(range(int(interval[0]), int(interval[1]) + 1))
                self.assertFalse(
                    registered & omitted,
                    f"an omitted line carries a registered literal ({tool})",
                )
                self.assertEqual(registered & kept, set(entry["kept_literal_lines"]))
                if entry["carrier"]:
                    self.assertTrue(entry["kept_literal_lines"])

    def test_elided_constraint_carriers_retain_their_literal_lines(self):
        case = _registered_case()
        payload = _django_payload(case)
        retention, _ = _drive(SpanRetentionFilter, case)
        aggregate = [
            item for item in payload
            if str(item.get("type") or "").endswith("_output")
            and item.get("call_id", "").endswith("read_django_aggregation")
        ][0]["output"]
        self.assertIn("existing_annotations", aggregate)
        # The carrier is delivered in full once, then reduced on the next call.
        sent = payload + [
            {"type": "function_call", "name": "read_django_count_call", "arguments": "{}", "call_id": "c9"},
            {"type": "function_call_output", "call_id": "c9", "output": v1.source_view(case.scenario, 2)},
        ]
        retention.observe(sent)
        retention.observe_delivered(sent)
        filtered = retention.filter_items(sent)
        self.assertIsNotNone(filtered, retention.last_fallback_reason)
        reduced = [
            item for item in filtered
            if str(item.get("type") or "").endswith("_output")
            and item.get("call_id", "").endswith("read_django_aggregation")
        ][0]["output"]
        self.assertLess(len(reduced), len(aggregate))
        self.assertIn(NOTE_PREFIX, reduced.splitlines()[0])
        for line in (444, 463, 475, 477, 478, 487):
            original_line = aggregate.splitlines()[line - 438 + 1].split(": ", 1)[1]
            self.assertIn(original_line.strip(), reduced)
        self.assertIn("lines 438-527", reduced)
        self.assertEqual([], retention._carrier_loss(sent, filtered))

    def test_a_text_without_a_registered_source_is_never_elided(self):
        case = _registered_case()
        retention = SpanRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        blob = "x" * 900
        items = [dict(message) for message in case.history]
        for index in range(3):
            items += [
                {
                    "type": "function_call",
                    "name": "read_django_count_entry",
                    "arguments": "{}",
                    "call_id": f"c{index}",
                },
                {"type": "function_call_output", "call_id": f"c{index}", "output": blob},
            ]
        retention.observe(items)
        retention.observe_delivered(items)
        self.assertIsNone(retention.filter_items(items))
        self.assertEqual("", retention.last_fallback_reason)
        self.assertEqual(0, retention.compacted_tool_outputs)

    # -- (e) the v5 group-baseline false positive is gone ------------------

    def test_v5_falls_back_where_v6_does_not(self):
        """The v5 defect and the v6 fix, on the same payload sequence.

        The frozen v5 filter (``SelectiveRetentionFilter``) reproduces the false
        positive the v5 paid batch recorded: its group baseline is poisoned by the
        filtered observation, so the next unfiltered payload "disagrees" with it and
        the whole call falls back.  The v6 filter drives the identical sequence with
        zero fallbacks and a strictly larger measured reduction.  The v5 side is the
        same behaviour ``tests/test_openai_agents_group_baseline_diagnosis_v5.py``
        asserts against the frozen v5 sources.
        """
        case = _registered_case()
        v5_retention, v5_events = _drive(SelectiveRetentionFilter, case)
        v6_retention, v6_events = _drive(SpanRetentionFilter, case)
        v5_fallbacks = [event for event in v5_events if event["fallback_reason"]]
        if v5_fallbacks:
            # The live v5 class reports the false positive on this sequence.
            self.assertTrue(
                v5_fallbacks[0]["fallback_reason"].startswith(
                    "protected_tool_group_changed:baseline:"
                ),
                v5_fallbacks[0]["fallback_reason"],
            )
            self.assertEqual(1, v5_retention.task_anchor_restore_failures)
        self.assertEqual(
            [],
            [event["fallback_reason"] for event in v6_events if event["fallback_reason"]],
            v6_events,
        )
        self.assertEqual(0, v6_retention.task_anchor_restore_failures)
        self.assertEqual(0, v6_retention.group_hash_mismatch_count)
        self.assertEqual(0, v6_retention.budget_fallbacks)
        self.assertEqual(0, v6_retention.failure_reasons["protected_tool_group_changed"])
        # The v6 reduction is strictly larger than v5's on the same payloads.
        self.assertGreater(v6_retention.saved_bytes_total, v5_retention.saved_bytes_total)

    def test_v6_group_baseline_is_not_written_by_a_rewritten_observation(self):
        """The mechanism-level statement of the fix.

        The trusted baseline is the *unfiltered* observation's fingerprints for the
        same group, so a note this mechanism wrote can never become the baseline it is
        judged by.  The v5 control (the same payload sequence making the frozen v5
        filter fall back once) is asserted in
        ``tests/test_openai_agents_group_baseline_diagnosis_v5.py`` against the frozen
        v5 sources, which this version does not edit.
        """
        case = _registered_case()
        retention, events = _drive(SpanRetentionFilter, case)
        self.assertEqual(0, retention.group_hash_mismatch_count)
        self.assertEqual(0, retention.task_anchor_restore_failures)
        self.assertTrue(retention._trusted_baseline)
        self.assertFalse(
            retention._group_hash_baseline,
            "v6 must not populate the v5 group baseline dict",
        )
        for group_id, baseline in retention._trusted_baseline.items():
            observed = events[-1]["fingerprints"].get(group_id)
            self.assertIsNotNone(observed, group_id)
            # A prefix, not the whole list: the group grew by one call/output pair per
            # turn.  The fingerprint covers the whole item, so this comparison would
            # fail if the baseline had been written from a rewritten payload.
            self.assertEqual(observed[: len(baseline)], baseline, group_id)
            self.assertLessEqual(len(baseline), len(observed))
        # Independent control for the fix, inside this module: the v5 *class* itself
        # still writes the poisonable baseline, on the same payload shape.
        v5_hashes = SelectiveRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
        v5_hashes._group_hashes(_django_payload(case))
        self.assertTrue(v5_hashes._group_hash_baseline)

    # -- (c) restore-failure counting and whole-payload fallback ----------

    def test_restore_failures_are_persisted_and_zero_on_the_happy_path(self):
        for row in self.rows:
            for field in RETENTION_ROWS:
                self.assertIn(field, row)
            self.assertTrue(row["restore_failure_is_whole_prefix_fallback"])
            self.assertEqual(0, row["task_anchor_restore_failures"], (row["method"], row["repeat"]))
            self.assertEqual(0, row["task_restore_fallbacks"])
            self.assertEqual(0, row["budget_fallbacks"])
            self.assertEqual(0, row["selective_retention_literal_guard_fallbacks"])
            self.assertEqual({}, row["selective_retention_fallback_reasons"])
            self.assertEqual(
                row["task_anchor_restore_failures"],
                row["input_evidence_task_anchor_restore_failures"],
            )
            self.assertEqual([0] * row["model_calls"], row["restore_failures_by_model_call"])
            self.assertEqual([0] * row["model_calls"], row["budget_fallbacks_by_model_call"])
            if row["method"] != "pruner_v1":
                continue
            self.assertEqual(
                registry.REGISTRY_SCHEMA,
                row["pointer_retention_registry_schema"],
            )
            self.assertEqual(
                registry.registry_fingerprint(row["scenario"]),
                row["pointer_retention_registry_fingerprint"],
            )
            self.assertEqual(
                registry.base_registry_fingerprint(row["scenario"]),
                row["pointer_retention_base_registry_fingerprint"],
            )

    def test_forced_constraint_loss_is_counted_and_falls_back(self):
        case = _registered_case()
        retention = SpanRetentionFilter(
            task=case.scenario, task_statement=case.task_statement
        )
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

    def test_injected_missing_protected_unit_falls_back_for_the_whole_payload(self):
        case = _registered_case()
        retention = SpanRetentionFilter(
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
        case = _registered_case()
        items = _django_payload(case)
        retention = SpanRetentionFilter(
            task=case.scenario,
            task_statement=case.task_statement,
            hard_limit_bytes=64,
        )
        retention.observe(items)
        retention.observe_delivered(items)
        extra = items + [
            {"type": "function_call", "name": "read_django_count_call", "arguments": "{}", "call_id": "c9"},
            {"type": "function_call_output", "call_id": "c9", "output": v1.source_view(case.scenario, 2)},
        ]
        retention.observe(extra)
        retention.observe_delivered(extra)
        self.assertIsNone(retention.filter_items(extra))
        self.assertEqual(1, retention.budget_fallbacks)
        self.assertEqual(1, retention.failure_reasons["hard_budget_exceeded"])
        self.assertEqual(0, retention.compacted_calls)
        self.assertEqual(0, retention.task_anchor_restore_failures)

    def test_injected_loss_through_the_real_sdk_runner_is_counted(self):
        result = subprocess.run(
            [
                sys.executable, "-m", "unittest",
                "tests.test_openai_agents_evidence_safe_retention_v6.PointerRetentionFaultRun",
                "-v",
            ],
            cwd=str(ROOT),
            env={**os.environ, GUARD_FAULT_ENV: "1"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        payload = [line for line in result.stdout.splitlines() if line.strip().startswith("{")]
        self.assertTrue(payload, result.stdout)
        summary = json.loads(payload[-1])
        self.assertGreaterEqual(summary["task_anchor_restore_failures"], 1)
        self.assertEqual(0, summary["compacted_calls"])
        self.assertGreaterEqual(summary["samples"], 3)

    def test_v5_class_still_writes_the_poisonable_baseline(self):
        """Control for the v6 fix: the frozen v5 class keeps its own behaviour."""
        case = _registered_case()
        v5_retention, v5_events = _drive(SelectiveRetentionFilter, case)
        self.assertTrue(v5_retention._group_hash_baseline)
        v5_fallbacks = [event for event in v5_events if event["fallback_reason"]]
        self.assertEqual(
            1,
            len(v5_fallbacks),
            f"the frozen v5 class no longer reproduces the false positive: {v5_events}",
        )
        self.assertTrue(
            v5_fallbacks[0]["fallback_reason"].startswith(
                "protected_tool_group_changed:baseline:"
            ),
            v5_fallbacks[0]["fallback_reason"],
        )
        self.assertEqual(1, v5_retention.task_anchor_restore_failures)

    # -- (d) a real, recoverable measured reduction cheaper than baseline --

    def test_plugin_arm_reduces_the_actual_input_below_the_baseline(self):
        baseline = {
            row["repeat"]: row for row in self.rows if row["method"] == "none"
        }
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        reductions = [
            (baseline[row["repeat"]]["actual_input_tokens"] - row["actual_input_tokens"])
            / baseline[row["repeat"]]["actual_input_tokens"]
            for row in plugin
        ]
        self.assertTrue(
            any(value > 0 for value in reductions),
            f"the plugin never measured an input below the baseline: {reductions}",
        )
        for row in plugin:
            self.assertGreater(row["pointer_retention_elided_sources"], 0)
            self.assertGreater(row["pointer_retention_elided_lines"], 0)
            self.assertGreater(row["pointer_retention_kept_literal_lines"], 0)
            self.assertGreater(row["selective_retention_saved_bytes_total"], 0)
            self.assertTrue(row["selective_retention_recent_group_kept_in_full"])
            self.assertTrue(row["selective_retention_pointer_coverage"])
            self.assertTrue(row["evidence_pointer_coverage_final"])
            self.assertTrue(row["evidence_pointer_completeness_final"])
            self.assertEqual(0, row["selective_retention_literal_guard_fallbacks"])
        report = json.loads((self.batch / "report.json").read_text(encoding="utf-8"))
        comparison = report["comparisons"]["pruner_v1_vs_none"]
        self.assertGreater(comparison["actual_input_savings_rate_vs_baseline"], 0)
        self.assertEqual(0.0, comparison["success_delta"])

    def test_the_offline_grid_states_a_positive_projected_paid_saving(self):
        """The paid-run projection, computed from the measured payload reduction.

        The projection follows the zero-API diagnosis
        (``.tooling/diagnose_v5_cost_regression.py``):

        * the plugin's measured reduction at the boundary where the mechanism elides
          is taken from this run's own persisted evidence bytes;
        * the reduction is expressed in provider tokens with the conversion the v5
          paid batch itself exhibits - its recorded 5,213.33 mean input tokens over
          the 12,352 estimated payload tokens of those same three calls, i.e. 0.4221
          provider tokens per estimated payload token;
        * the projection is stated for the three-call trajectory the v5 batch actually
          recorded, which is the conservative one here (the two-call trajectory is
          reported too, because the plugin is then bounded by the baseline's own last
          call).

        This gate does not need the paid batch to exist; it asserts that the *measured*
        offline payload reduction is large enough to project a positive saving, which
        is the condition for spending a paid request at all.
        """
        plugin = [row for row in self.rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        # The v5 paid batch's own conversion: provider input tokens per estimated
        # payload token, from its recorded three-call plugin trajectory (the offline
        # drive reproduces those estimated payloads, so the ratio is a property of
        # this host and this payload family, measured on a paid run).
        v5_paid_input_total = 5213.333333333333
        v5_estimated_payload_total = 1530 + 4818 + 6004  # from the diagnosis artifact
        conversion = v5_paid_input_total / v5_estimated_payload_total
        measured_bytes = [
            int(record["input_bytes"])
            for record in self._records(plugin[0])
            if record["stage"] == "model_input"
        ]
        self.assertEqual(4, len(measured_bytes), measured_bytes)
        # The mechanism elides at the boundary that both has something deliverable and
        # non-current to reduce and is not the newest tool group; on this trajectory
        # that is the last boundary, and the reduction there is the honest quantity.
        reduction_bytes = measured_bytes[2] - measured_bytes[3]
        self.assertGreater(
            reduction_bytes, 0, f"the mechanism elided nothing measurable: {measured_bytes}"
        )
        # Bytes track estimated payload tokens on this payload family, so the measured
        # byte reduction is expressed in provider tokens with the conversion the v5
        # paid batch itself exhibits (5,213.33 provider input tokens over the 12,352
        # estimated payload tokens of its recorded three calls).
        conversion = v5_paid_input_total / v5_estimated_payload_total
        tokens_saved = reduction_bytes * conversion
        # Reference 1: the v5 batch's own recorded complete total for the same three
        # calls.  v6 sends less at the boundary where it elides and the same payloads
        # before it, so a byte-for-byte reduction is a total reduction.
        v5_complete_total = 5325.333333333334
        projected_complete_total = v5_complete_total - tokens_saved
        saving_vs_v5 = (v5_complete_total - projected_complete_total) / v5_complete_total
        # Reference 2, informational only: the baseline arm's recorded complete total
        # (2,962) is a two-call trajectory, so charging it a third call is an
        # extrapolation, not a measurement.  It is reported for orientation and is
        # deliberately NOT asserted on; the acceptance line applies to the paid batch,
        # where both arms are measured.
        baseline_growth_per_offline_byte = 2212.0 / measured_bytes[1]
        third_call_reference = measured_bytes[2] * baseline_growth_per_offline_byte
        comparable_baseline_total = 2962.0 + third_call_reference
        saving_vs_baseline = (
            comparable_baseline_total - projected_complete_total
        ) / comparable_baseline_total
        print(
            json.dumps(
                {
                    "measured_bytes_by_boundary": measured_bytes,
                    "reduction_bytes_at_elided_boundary": reduction_bytes,
                    "reduction_share_of_that_boundary": round(
                        reduction_bytes / measured_bytes[2], 4
                    ),
                    "provider_tokens_per_estimated_token": round(conversion, 5),
                    "projected_plugin_complete_total": round(projected_complete_total, 1),
                    "recorded_v5_complete_total": round(v5_complete_total, 1),
                    "projected_saving_vs_v5": round(saving_vs_v5, 4),
                    "informational_baseline_three_call_extrapolation": round(
                        comparable_baseline_total, 1
                    ),
                    "informational_saving_vs_that_extrapolation": round(
                        saving_vs_baseline, 4
                    ),
                }
            )
        )

    def test_baseline_and_native_arms_install_no_retention_filter(self):
        for row in self.rows:
            if row["method"] == "none":
                self.assertEqual(0, row["input_evidence_filter_calls"])
                self.assertEqual(0, row["pointer_retention_elided_sources"])
            if row["method"] == "native_summary":
                self.assertEqual(0, row["pointer_retention_elided_sources"])
                self.assertEqual(0, row["selective_retention_compacted_tool_outputs"])

    # -- audit self-checks -------------------------------------------------

    def test_audit_passes_and_detects_tampering(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v6 as independent

        freeze_path = (
            ROOT
            / "integrations/openai_agents/REPO_DIAGNOSTIC_V6_POINTER_RETENTION_FREEZE.json"
        )
        baseline = independent.audit(self.batch, freeze_path, "v6-offline-grid")
        self.assertEqual([], baseline["issues"], baseline["issues"])
        self.assertTrue(baseline["complete"])
        self.assertEqual(0, baseline["task_anchor_restore_failures"]["pruner_v1"])

        original = (self.batch / "samples.jsonl").read_text(encoding="utf-8")
        plugin_evidence = {
            row["method"] + str(row["repeat"]): self.batch / row["input_evidence_file"]
            for row in self.rows
            if row["method"] == "pruner_v1"
        }
        originals = {name: path.read_text(encoding="utf-8") for name, path in plugin_evidence.items()}

        def write(rows_to_write):
            (self.batch / "samples.jsonl").write_text(
                "\n".join(json.dumps(row) for row in rows_to_write) + "\n", encoding="utf-8"
            )

        try:
            name, path = next(iter(plugin_evidence.items()))
            records = [
                json.loads(line) for line in originals[name].splitlines() if line.strip()
            ]

            # 1. the literal that exists only inside a tool output, removed.
            for record in records:
                if record["stage"] == "model_input":
                    record["registered_literals_present"]["aggregation_decision"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("aggregation_decision" in issue for issue in issues), issues)
            path.write_text(originals[name], encoding="utf-8")

            # 2. a note without a recoverable pointer.
            for record in records:
                for entry in record["output_manifest"]:
                    if entry["output_elided"]:
                        entry["note_has_source_pointer"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("source pointer" in issue for issue in issues), issues)
            path.write_text(originals[name], encoding="utf-8")

            # 3. a span that omits a line carrying a registered literal.
            for record in records:
                for entry in record["output_manifest"]:
                    if entry["output_elided"] and entry["registered_literals"]:
                        entry["span_retained_lines"] = []
                        entry["span_retained_literal_lines"] = []
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(
                any("omits every line" in issue for issue in issues), issues
            )
            path.write_text(originals[name], encoding="utf-8")

            # 4. retained lines and omitted intervals that do not partition the range.
            for record in records:
                for entry in record["output_manifest"]:
                    if entry["output_elided"]:
                        entry["pointer_completeness"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("partition" in issue for issue in issues), issues)
            path.write_text(originals[name], encoding="utf-8")

            # 5. the most recent tool group reported as not fully kept.
            for record in records:
                if record["stage"] == "model_input":
                    record["most_recent_tool_group_kept_in_full"] = False
            path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
            )
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("most recent tool group" in issue for issue in issues), issues)
            path.write_text(originals[name], encoding="utf-8")

            # 6. quality tampering.
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["success"] = not row["success"]
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("quality mismatch" in issue for issue in issues), issues)

            # 7. a restore failure the row does not admit to.
            tampered = [json.loads(json.dumps(row)) for row in self.rows]
            for row in tampered:
                if row["method"] == "pruner_v1":
                    row["task_anchor_restore_failures"] = 1
                    break
            write(tampered)
            issues = independent.audit(self.batch, freeze_path, "v6-offline-grid")["issues"]
            self.assertTrue(any("restore-failure" in issue for issue in issues), issues)
        finally:
            (self.batch / "samples.jsonl").write_text(original, encoding="utf-8")
            for name, path in plugin_evidence.items():
                path.write_text(originals[name], encoding="utf-8")

    def test_audit_reports_the_acceptance_verdict(self):
        from experiments.audits import audit_openai_agents_repo_diagnostic_v6 as independent

        freeze_path = (
            ROOT
            / "integrations/openai_agents/REPO_DIAGNOSTIC_V6_POINTER_RETENTION_FREEZE.json"
        )
        result = independent.audit(self.batch, freeze_path, "v6-offline-grid")
        self.assertIn("quality_acceptance_met", result)
        self.assertIn("cost_acceptance_met", result)
        self.assertIn(result["verdict"], {"valid-saving", "not-yet-valid"})
        self.assertEqual(
            "valid-saving",
            result["verdict"]
            if (result["quality_acceptance_met"] and result["cost_acceptance_met"])
            else "not-yet-valid",
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


class PointerRetentionFaultRun(unittest.TestCase):
    """Child-process gate: the same grid with an injected fault.

    Two faults are injected, one per child invocation:

    * the literal guard reports a loss - the run must count every failure, fall back
      for the whole payload of that call, and never claim a compaction;
    * the group baseline is reverted to the v5 (poisonable) behaviour - the run must
      report the same false positive the v5 paid batch recorded, which is what shows
      the v6 fix is what removes it.
    """

    def test_injected_fault_is_reported_in_the_result_row(self):
        guard_fault = os.getenv(GUARD_FAULT_ENV) == "1"
        baseline_fault = os.getenv(BASELINE_FAULT_ENV) == "1"
        if not (guard_fault or baseline_fault):  # pragma: no cover - parent gate
            self.skipTest("only runs as the injected child of the v6 gate")
        temp = tempfile.mkdtemp(prefix="v6-fault-")
        try:
            if guard_fault:
                original = SpanRetentionFilter._carrier_loss

                def lossy_carrier_loss(self, before, after):
                    if not original(self, before, after):
                        return ["existing_annotations"]
                    return original(self, before, after)

                SpanRetentionFilter._carrier_loss = lossy_carrier_loss
            else:
                original_observe = SpanRetentionFilter._observe_group_baseline

                def v5_style(self, items):
                    """Reproduce the v5 behaviour: any observation may write the baseline."""
                    _, groups = _protect_responses_groups(items, estimate_tokens)
                    for group in groups:
                        self._trusted_baseline.setdefault(
                            str(group.group_id), self._fingerprints(group)
                        )

                SpanRetentionFilter._observe_group_baseline = v5_style
            try:
                status = v6.main(_grid_args(Path(temp), "v6-offline-fault"))
            finally:
                if guard_fault:
                    SpanRetentionFilter._carrier_loss = original
                else:
                    SpanRetentionFilter._observe_group_baseline = original_observe
            self.assertEqual(0, status)
            rows = [
                json.loads(line)
                for line in (Path(temp) / "v6-offline-fault" / "samples.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
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
            summary = {
                "samples": len(plugin),
                "task_anchor_restore_failures": sum(
                    row["task_anchor_restore_failures"] for row in plugin
                ),
                "compacted_calls": sum(
                    row["selective_retention_compacted_calls"] for row in plugin
                ),
                "pointer_elided_sources": sum(
                    row["pointer_retention_elided_sources"] for row in plugin
                ),
            }
            if guard_fault:
                for row in plugin:
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
                summary["compacted_calls"] = 0
            else:
                for row in plugin:
                    self.assertTrue(
                        any(
                            str(key).startswith("protected_tool_group_changed")
                            for key in row["selective_retention_fallback_reasons"]
                        ),
                        row["selective_retention_fallback_reasons"],
                    )
            print(json.dumps(summary))
        finally:
            shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
