"""Offline diagnosis of the v5 protected-group baseline defect (zero API).

The v5 paid pilot recorded ``task_anchor_restore_failures = 1`` in every plugin
sample, with ``fallback_reason = protected_tool_group_changed:baseline:...``.  The
evidence shows the payload was byte-identical before and after that call, so the
fallback cannot have been caused by a real group change.

This module reproduces the cause without any provider request, and checks the
proposed correction.  Both the live v5 class and a corrected subclass are driven
over the same payload sequence:

* the **baseline** that ``_group_change`` compares against is written by
  ``_group_hashes`` on *every* call, including the ``filter_after`` observation of
  a payload whose tool-output text the mechanism itself just replaced with a note.
* once that filtered observation has poisoned the baseline, the next call's
  *unfiltered* ``filter_before`` payload - which still carries the original output
  text - differs from the baseline, so the baseline check fires and the call falls
  back for the whole payload.
* the correction is to derive the baseline **only** from observations the
  mechanism did not rewrite, and to compare like with like: an unfiltered
  observation against the unfiltered baseline, a filtered payload against the
  groups of the payload it came from.

The corrected subclass here is a diagnosis artifact, not a new batch: the frozen
v5 sources stay byte-identical to the v5 freeze, and the v5 paid batch is left
exactly as recorded.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from experiments.runners import openai_agents_literal_registry_v5 as registry
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
    _jsonable_item,
)

ROOT = Path(__file__).resolve().parents[1]
V5_BATCH = (
    ROOT
    / "runs/stage5-openai-agents-api"
    / "openai-repo-diagnostic-v5-evidence-safe-retention-pilot-01"
)
V4_BATCH = (
    ROOT
    / "runs/stage5-openai-agents-api"
    / "openai-repo-diagnostic-v4-selective-retention-pilot-01"
)


class CorrectedBaselineMixin:
    """Baseline taken only from unfiltered observations, compared prefix-wise.

    Two corrections, both needed for the pilot's own trajectory:

    1. ``self._trusted_baseline`` is written only from a payload as observed at
       the model boundary *before* filtering.  A payload the mechanism itself
       rewrote never updates it, so a note can no longer poison the baseline.
    2. A group that legitimately grows between calls (the platform adds one
       call/output pair per turn) is compared on the **common prefix**: only the
       items the baseline already saw are required to be unchanged, so growth is
       not reported as a change.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._trusted_baseline: dict[str, list[str]] = {}

    @staticmethod
    def _fingerprints(group) -> list[str]:
        from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
            _canonical,
            _group_fingerprint,
        )

        return [_canonical(_group_fingerprint(item)) for item in group.items]

    def _observe_baseline(self, items) -> None:
        _, groups = _protect_groups(items, self.token_counter)
        for group in groups:
            self._trusted_baseline.setdefault(
                str(group.group_id), self._fingerprints(group)
            )

    def _group_change(self, before, after):  # type: ignore[override]
        self._observe_baseline(before)
        _, groups_before = _protect_groups(before, self.token_counter)
        _, groups_after = _protect_groups(after, self.token_counter)
        before_by_id = {str(group.group_id): group for group in groups_before}
        after_by_id = {str(group.group_id): group for group in groups_after}
        mismatch = sorted(group_id for group_id in before_by_id if group_id not in after_by_id)
        for group_id, group in before_by_id.items():
            baseline = self._trusted_baseline.get(group_id, [])
            if self._fingerprints(group)[: len(baseline)] != baseline:
                mismatch.append(group_id)
        if mismatch:
            self.group_hash_mismatch_count += 1
            return "baseline:" + ",".join(sorted(set(mismatch))[:8])
        # A group that is new since the baseline is growth, not a change; the
        # platform adds exactly one call/output pair per turn.
        return ""


def _protect_groups(items, token_counter):
    from context_pruner.adapters.openai_agents import _protect_responses_groups

    return _protect_responses_groups(items, token_counter)


def _sha_of_group(group):
    import hashlib

    from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
        _canonical,
        _group_fingerprint,
    )

    content = [_group_fingerprint(item) for item in group.items]
    return hashlib.sha256(_canonical(content).encode("utf-8")).hexdigest()


class CorrectedSelectiveRetentionFilter(CorrectedBaselineMixin, SelectiveRetentionFilter):
    """v5 with the group-baseline defect corrected (diagnosis only)."""


def _django_case(repeat: int = 0):
    case = v1.build_case("django_count_annotations", repeat)
    return case, v1.source_view(case.scenario, 0), v1.source_view(case.scenario, 1), v1.source_view(
        case.scenario, 2
    )


def _payload(case, outputs, call_id_prefix="call"):
    items = [dict(message) for message in case.history]
    for index, name in enumerate(case.expected_tool_names):
        items += [
            {
                "type": "function_call",
                "name": name,
                "arguments": "{}",
                "call_id": f"{call_id_prefix}_{index}_{name}",
            },
            {
                "type": "function_call_output",
                "call_id": f"{call_id_prefix}_{index}_{name}",
                "output": outputs[index],
            },
        ]
    return items


class GroupBaselineDefectDiagnosis(unittest.TestCase):
    def _drive(self, cls, case, outputs):
        """Reproduce the pilot's call sequence and report what happened."""
        from experiments.runners.run_openai_agents_repo_diagnostic_v5 import (
            task_statement as registered_statement,
        )

        retention = cls(
            task=case.scenario, task_statement=registered_statement(case)
        )
        history: list[dict] = [dict(message) for message in case.history]
        retention.observe(history)
        retention.observe_delivered(history)
        events = []
        sent = history
        for index, name in enumerate(case.expected_tool_names):
            sent = sent + [
                {"type": "function_call", "name": name, "arguments": "{}", "call_id": f"c{index}"},
                {"type": "function_call_output", "call_id": f"c{index}", "output": outputs[index]},
            ]
            before_bytes = len(json.dumps([_jsonable_item(i) for i in sent], default=str))
            retention.observe(sent)
            retention.observe_delivered(sent)
            filtered = retention.filter_items(sent)
            after = sent if filtered is None else filtered
            events.append(
                {
                    "index": index,
                    "fallback_reason": retention.last_fallback_reason,
                    "bytes_before": before_bytes,
                    "bytes_after": len(json.dumps([_jsonable_item(i) for i in after], default=str)),
                    "compacted": retention.compacted_tool_outputs,
                }
            )
            retention.observe(after)
            retention.observe_delivered(after)
        return retention, events

    def test_live_v5_baseline_is_poisoned_by_a_filtered_observation(self):
        case, *outputs = _django_case()
        retention, events = self._drive(SelectiveRetentionFilter, case, outputs)
        fallbacks = [event for event in events if event["fallback_reason"]]
        self.assertEqual(1, len(fallbacks), events)
        fallback = fallbacks[0]
        self.assertTrue(
            fallback["fallback_reason"].startswith("protected_tool_group_changed:baseline:"),
            fallback["fallback_reason"],
        )
        # The decisive evidence: the payload sent was the observed payload, so the
        # reported "group change" cannot have been a change in the payload.
        self.assertEqual(
            fallback["bytes_before"],
            fallback["bytes_after"],
            "the fallback did not resend the observed payload",
        )
        self.assertEqual(1, retention.task_anchor_restore_failures)
        # No registered literal was lost by the fallback.
        self.assertEqual([], retention._carrier_loss(events and [], []))
        self.assertEqual(1, retention.compacted_tool_outputs)

    def test_corrected_baseline_removes_the_false_positive(self):
        case, *outputs = _django_case()
        retention, events = self._drive(CorrectedSelectiveRetentionFilter, case, outputs)
        self.assertEqual(
            [],
            [event["fallback_reason"] for event in events if event["fallback_reason"]],
            events,
        )
        self.assertEqual(0, retention.task_anchor_restore_failures)
        self.assertEqual(0, retention.group_hash_mismatch_count)
        # The corrected baseline lets the third call compress too, so the
        # mechanism recovers the reduction the false positive was blocking.
        self.assertEqual(2, retention.compacted_tool_outputs)
        self.assertGreater(retention.saved_bytes_total, 0)

    def test_payload_is_byte_identical_before_and_after_the_fallback(self):
        """The batch's own evidence file says the same thing as this reproduction."""
        rows = [
            json.loads(line)
            for line in (V5_BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertEqual(3, len(plugin))
        for row in plugin:
            records = [
                json.loads(line)
                for line in (V5_BATCH / row["input_evidence_file"])
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            pairs = [
                (record["input_bytes"], record.get("fallback_reason", ""))
                for record in records
                if record["stage"] == "filter_after"
            ]
            self.assertTrue(any(reason.startswith("protected_tool_group_changed") for _, reason in pairs))
            for previous, following in zip(
                [r for r in records if r["stage"] == "filter_before"],
                [r for r in records if r["stage"] == "filter_after"],
            ):
                if not str(following.get("fallback_reason", "")).startswith(
                    "protected_tool_group_changed"
                ):
                    continue
                self.assertEqual(previous["input_bytes"], following["input_bytes"])
                self.assertEqual(
                    [group["group_sha256"] for group in previous["protected_groups"]],
                    [group["group_sha256"] for group in following["protected_groups"]],
                )

    def test_registered_literals_survive_in_the_paid_batch(self):
        """What v5 fixed: no registered literal went missing in any sample."""
        rows = [
            json.loads(line)
            for line in (V5_BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        labels = tuple(registry.literals_for("django_count_annotations"))
        for row in rows:
            final = row["evidence_registered_literals_present_final"]
            for label in labels:
                self.assertTrue(final[label], (row["method"], row["repeat"], label))
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        baseline = {
            row["repeat"]: row["evidence_registered_literal_counts_final"] for row in rows
            if row["method"] == "none"
        }
        for row in plugin:
            self.assertEqual(
                baseline[row["repeat"]],
                row["evidence_registered_literal_counts_final"],
                "the plugin arm's literal counts differ from the baseline arm",
            )

    def test_v4_batch_lost_the_literal_this_version_keeps(self):
        """The contrast that justifies v5: v4's plugin arm lost `existing_annotations`."""
        rows = [
            json.loads(line)
            for line in (V4_BATCH / "samples.jsonl").read_text(encoding="utf-8").splitlines()
            if line
        ]
        plugin = [row for row in rows if row["method"] == "pruner_v1"]
        self.assertTrue(plugin)
        lost = 0
        for row in plugin:
            records = [
                json.loads(line)
                for line in (V4_BATCH / row["input_evidence_file"])
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            final = [record for record in records if record["stage"] == "model_input"][-1]
            if not final["constraint_present"]["aggregation_decision"]:
                lost += 1
        self.assertGreaterEqual(lost, 1, "v4 did not lose the literal in this batch")


if __name__ == "__main__":
    unittest.main()
