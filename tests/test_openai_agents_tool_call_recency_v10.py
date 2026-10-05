"""Zero-API gate for v10: replay on the **recorded** real payloads, not a self-built stub.

Three things are established without any provider request:

* **the fixture is the recorded payload structure**: rebuilt boundaries must match the paid
  v9 batch's recorded item counts, message counts, tool-output counts and contiguity at
  every boundary - if a future change reintroduces a stub-shaped payload, this fails;
* **the hypothesis**: with recency counted in tool calls the window is non-empty on the
  recorded structure, older calls are reduced, the newest call stays verbatim, pointer and
  interval checks hold, no registered literal regresses, and every guard counter stays 0;
* **the pre-registered decision**: a positive replay projection is required before any paid
  request; the recorded projection is written to
  ``integrations/openai_agents/V10_REPLAY_PROJECTION.json``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from experiments.runners import openai_agents_long_baseline_boundary_v10 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners import openai_agents_replay_projection_v10 as projection
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners import run_openai_agents_repo_diagnostic_v10 as v10
from experiments.runners import run_openai_agents_repo_diagnostic_v9 as v9
from experiments.runners.openai_agents_span_retention_v6 import SpanRetentionFilter
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
)

ROOT = Path(__file__).resolve().parents[1]
PAID_BATCH_ID = "openai-repo-diagnostic-v10-tool-call-recency-boundary"
PROJECTION_PATH = ROOT / "integrations/openai_agents/V10_REPLAY_PROJECTION.json"


class ToolCallRecencyV10Gate(unittest.TestCase):
    # -- the fixture is the recorded structure -----------------------------

    def test_fixture_reproduces_the_recorded_payload_structure(self):
        report = replay.fixture_report()
        self.assertEqual([], report["mismatches"], report["mismatches"])
        self.assertTrue(report["ok"])
        self.assertEqual(report["recorded_boundaries"], report["fixture_boundaries"])
        self.assertTrue(report["tool_items_contiguous"])
        self.assertEqual([0, 1, 1, 1, 1, 1, 1], report["recorded_turn_count_by_boundary"])
        self.assertEqual([[] for _ in range(7)], report["recorded_elidable_indices"])
        for check in report["checks"]:
            self.assertTrue(check["ok"], check)
            self.assertLessEqual(check["observed"]["tool_runs"], 1)

    def test_fixture_is_built_from_public_deterministic_inputs(self):
        """No message is inserted between tool items - the property the stub got wrong."""
        boundaries = replay.realistic_boundaries(0)
        self.assertEqual(7, len(boundaries))
        for previous, current in zip(boundaries, boundaries[1:]):
            self.assertEqual(len(previous) + 2, len(current))
        # every boundary carries the same three messages and no more
        for payload in boundaries:
            self.assertEqual(3, replay.message_item_count(payload))
        self.assertEqual(1, replay.contiguous_tool_runs(boundaries[-1]))
        self.assertEqual(6, replay.tool_output_count(boundaries[-1]))

    # -- the hypothesis ----------------------------------------------------

    def test_tool_call_window_is_non_empty_where_the_group_window_was_empty(self):
        boundaries = replay.realistic_boundaries(0)
        last = boundaries[-1]
        self.assertEqual(6, policy.tool_call_count(last))
        self.assertEqual({13, 14}, policy.protected_call_indices(last))
        self.assertEqual(
            {3, 4, 5, 6, 7, 8, 9, 10, 11, 12}, policy.elidable_call_indices(last)
        )
        # the contiguous-group window is empty on the same payload (the v9 finding)
        from experiments.runners import openai_agents_recency_boundary_v7 as recency

        self.assertEqual([], sorted(recency.elidable_indices(last)))

    def test_replay_reduces_with_every_guard_at_zero(self):
        report = projection.run_replay(0)
        self.assertTrue(report["fixture"]["ok"])
        self.assertTrue(report["elidable_indices_non_empty"])
        self.assertGreater(report["total_elided_sources"], 0)
        self.assertGreater(report["total_elided_lines"], 0)
        self.assertEqual(0, report["task_anchor_restore_failures"])
        self.assertEqual(0, report["budget_fallbacks"])
        self.assertEqual({}, report["fallback_reasons"])
        self.assertEqual([], report["literal_regressions"])
        self.assertGreater(report["byte_saving_rate"], 0)
        self.assertLess(report["plugin_payload_bytes"], report["baseline_payload_bytes"])

    def test_newest_tool_call_is_never_touched(self):
        case = v8.build_case(policy.LONG_TASK_ID, 0)
        boundaries = replay.realistic_boundaries(0)
        retention = policy.ToolCallRecencyFilter(
            task=policy.LONG_TASK_ID, task_statement=case.task_statement
        )
        retention.observe(boundaries[0])
        retention.observe_delivered(boundaries[0])
        sent = boundaries[0]
        for payload in boundaries[1:]:
            sent = payload
            retention.observe(sent)
            retention.observe_delivered(sent)
            filtered = retention.filter_items(sent)
            after = sent if filtered is None else filtered
            protected = policy.protected_call_indices(sent)
            for index in protected:
                before_item = json.dumps(sent[index], sort_keys=True, default=str)
                after_item = json.dumps(after[index], sort_keys=True, default=str)
                self.assertEqual(before_item, after_item, f"protected item {index} changed")
            retention.observe(after)
            retention.observe_delivered(after)

    def test_span_records_partition_and_keep_literal_lines(self):
        report = projection.run_replay(0)
        self.assertGreater(report["total_elided_sources"], 0)
        case = v8.build_case(policy.LONG_TASK_ID, 0)
        boundaries = replay.realistic_boundaries(0)
        retention = policy.ToolCallRecencyFilter(
            task=policy.LONG_TASK_ID, task_statement=case.task_statement
        )
        retention.observe(boundaries[0])
        retention.observe_delivered(boundaries[0])
        all_records: list[dict] = []
        for payload in boundaries[1:]:
            retention.observe(payload)
            retention.observe_delivered(payload)
            filtered = retention.filter_items(payload)
            all_records.extend(retention.last_call_elisions)
            after = payload if filtered is None else filtered
            retention.observe(after)
            retention.observe_delivered(after)
        self.assertGreater(len(all_records), 0)
        for entry in all_records:
            self.assertTrue(entry["pointer_completeness"])
            self.assertTrue(entry["pointer_matches_registered_source"])
            self.assertTrue(entry["note_states_pointer"])
            first, last = entry["source_range"]
            expected = list(range(int(first), int(last) + 1))
            kept = sorted(int(value) for value in entry["kept_lines"])
            omitted: list[int] = []
            for interval in entry["omitted_intervals"]:
                omitted.extend(range(int(interval[0]), int(interval[1]) + 1))
            self.assertEqual(expected, sorted(kept + omitted))
            registered = set(
                long_registry.span_for_tool(policy.LONG_TASK_ID, entry["tool"]).literal_lines
            )
            self.assertFalse(registered & set(omitted))
            self.assertEqual(registered & set(kept), set(entry["kept_literal_lines"]))

    # -- only the recency unit changed -------------------------------------

    def test_only_the_recency_unit_changed(self):
        inherited = policy.inherited_method_objects()
        self.assertIs(SpanRetentionFilter._elide, inherited["_elide"])
        self.assertIs(SpanRetentionFilter._structural_violation, inherited["_structural_violation"])
        self.assertIs(SelectiveRetentionFilter._carrier_loss, inherited["_carrier_loss"])
        self.assertIs(SpanRetentionFilter._group_change, inherited["_group_change"])
        # v10 overrides the window installation and the counters, nothing that reduces.
        self.assertNotIn("_elide", policy.ToolCallRecencyFilter.__dict__)
        self.assertNotIn("_structural_violation", policy.ToolCallRecencyFilter.__dict__)
        self.assertNotIn("_carrier_loss", policy.ToolCallRecencyFilter.__dict__)
        self.assertIs(v10.build_retentive_filter.__module__, v10.__name__)
        self.assertIsNot(v10.build_retentive_filter, v9.build_retentive_filter)

    # -- the pre-registered paid decision ----------------------------------

    def test_projection_artifact_records_the_pre_registered_verdict(self):
        if not PROJECTION_PATH.is_file():
            self.skipTest("replay projection artifact not written yet")
        artifact = json.loads(PROJECTION_PATH.read_text(encoding="utf-8"))
        self.assertEqual("openai_agents_v10_replay_v1", artifact["schema"])
        self.assertTrue(artifact["fixture"]["ok"])
        self.assertIn(artifact["verdict"], {"positive", "empty"})
        if artifact["verdict"] == "positive":
            self.assertTrue(artifact["elidable_indices_non_empty"])
            self.assertGreater(artifact["byte_saving_rate"], 0)
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if paid_dir.is_dir() and artifact["verdict"] != "positive":
            self.fail("a paid batch exists although the replay projection was not positive")

    def test_paid_batch_when_present_is_an_honest_v10_batch(self):
        paid_dir = ROOT / "runs/stage5-openai-agents-api" / PAID_BATCH_ID
        if not paid_dir.is_dir():
            self.skipTest("no paid v10 batch")
        manifest = json.loads((paid_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("tool_call_recency_v10", manifest["mechanism"]["name"])
        self.assertEqual(policy.LONG_TASK_ID, manifest["scenarios"][0])
        self.assertFalse(bool(manifest.get("offline_gate")))


if __name__ == "__main__":
    unittest.main()
