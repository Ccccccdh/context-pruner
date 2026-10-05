"""Content-free v7 evidence: v6's records plus the recency-boundary policy state.

Reuses the v6 evidence layer (``openai_agents_evidence_v6.PointerRetentionRecorder``)
and restricts it in two ways:

* the schema is ``openai_recency_boundary_v7``, so a v7 file can never be mistaken for
  a v6 file;
* every record additionally carries what the recency policy decided on that call: how
  many turn items it protected, which indices were elidable, and how many outputs were
  kept *because* they were inside a protected turn.  That is what makes "the plugin
  could not compress anything here" a checkable property of the record rather than a
  claim about intent.

Nothing else changes: no message text, tool output, instruction, path or credential is
written, and only digests, byte and line counts, booleans, counters and the interval
boundaries the mechanism published in its own notes are persisted.
"""

from __future__ import annotations

from typing import Any, Sequence

from experiments.runners import openai_agents_recency_boundary_v7 as policy
from experiments.runners import openai_agents_span_retention_v6 as mechanism
from experiments.runners.openai_agents_evidence_v6 import (
    PointerRetentionModelProxy,
    PointerRetentionRecorder,
)

SCHEMA = "openai_recency_boundary_v7"
STAGES = ("filter_before", "filter_after", "model_input")


class RecencyBoundaryRecorder(PointerRetentionRecorder):
    """v6's recorder with the recency policy recorded per boundary."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        super().__init__(task, retention)
        self.last_elidable_indices: list[int] = []
        self.last_protected_turn_items = 0
        self.last_recency_protected_outputs = 0

    # -- boundaries -------------------------------------------------------

    def observe_filter_before(self, items: Sequence[Any], instructions: str | None) -> int:
        index = super().observe_filter_before(items, instructions)
        self.last_elidable_indices = sorted(policy.elidable_indices(items))
        self.last_protected_turn_items = len(policy.protected_turn_indices(items))
        self.last_recency_protected_outputs = int(
            getattr(self.retention, "recency_protected_outputs", 0)
        )
        return index

    def observe_filter_after(
        self, items: Sequence[Any], instructions: str | None, index: int
    ) -> None:
        super().observe_filter_after(items, instructions, index)
        record = self.records[-1]
        record["recency_protected_outputs_this_call"] = int(
            getattr(self.retention, "recency_protected_outputs", 0)
        ) - self.last_recency_protected_outputs
        record["recency_elidable_indices_this_call"] = list(self.last_elidable_indices)
        record["recency_protected_turn_items_this_call"] = self.last_protected_turn_items

    def observe_model_input(self, items: Sequence[Any], instructions: str | None) -> None:
        super().observe_model_input(items, instructions)
        record = self.records[-1]
        record["recency_elidable_indices"] = sorted(policy.elidable_indices(items))
        record["recency_elided_sources_total"] = int(
            getattr(self.retention, "elided_source_spans", 0)
        )

    # -- internals --------------------------------------------------------

    def _snapshot(
        self,
        items: Sequence[Any],
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        record["schema"] = SCHEMA
        record.update(
            {
                "recency_policy": dict(policy.MANIFEST_POLICY),
                "recency_turn_count": policy.turn_count(items),
                "recency_protected_turn_items": len(policy.protected_turn_indices(items)),
                "recency_elidable_indices": sorted(policy.elidable_indices(items)),
                "recency_elidable_turn_items": len(policy.elidable_indices(items)),
                "recency_protected_outputs_total": int(
                    getattr(self.retention, "recency_protected_outputs", 0)
                ),
                "recency_protected_calls_total": int(
                    getattr(self.retention, "recency_protected_calls", 0)
                ),
                "recency_elided_sources_total": int(
                    getattr(self.retention, "elided_source_spans", 0)
                ),
                "recency_message_items": policy.message_item_count(items),
                "recency_output_items": policy.output_item_count(items),
            }
        )
        return record


def mechanism_identity() -> dict[str, Any]:
    """The mechanism block written into the v7 manifest."""
    return {
        "schema": SCHEMA,
        "mechanism_schema": policy.MECHANISM_SCHEMA,
        "v6_mechanism_schema": mechanism.MECHANISM_SCHEMA,
        **policy.policy_dict(),
    }


__all__ = [
    "PointerRetentionModelProxy",
    "RecencyBoundaryRecorder",
    "SCHEMA",
    "STAGES",
    "mechanism_identity",
]
