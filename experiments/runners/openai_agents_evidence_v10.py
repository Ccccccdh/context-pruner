"""Content-free v10 evidence: v9's records plus the tool-call recency window.

Reuses the v9 recorder and adds what this version has to show: the recency unit is a
**tool call**, and every boundary records how many calls the payload carried, how many the
window left elidable, and which indices those were.  Counts and digests only, never text.

One compatibility hook: when ``DSH_V11_RECENT_CALLS_KEPT`` is set the v11 ablation is
driving the run, and the *v11* schema label is written instead of this one - so whichever
recorder class the runner ends up instantiating, the persisted schema matches the batch's
version.  The recency fields themselves are identical.
"""

from __future__ import annotations

import os
from typing import Any, Sequence

from experiments.runners import openai_agents_long_baseline_boundary_v10 as policy
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder

SCHEMA = "openai_tool_call_recency_v10"
#: Schema label written when the v11 elision-ratio ablation drives the run.
V11_SCHEMA = "openai_elision_ratio_boundary_v11"
STAGES = ("filter_before", "filter_after", "model_input")


def active_schema() -> str:
    """The schema label for this process (v11's when the ablation knob is set)."""
    if os.getenv("DSH_V11_RECENT_CALLS_KEPT", "").strip():
        return V11_SCHEMA
    return SCHEMA


class ToolCallRecencyRecorder(NarrowGuardRecorder):
    """v9's recorder with the tool-call recency window recorded per boundary."""

    def _snapshot(
        self,
        items: Sequence[Any],
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        record["schema"] = active_schema()
        record.update(
            {
                "tool_call_recency_unit": "tool call",
                "tool_call_recency_recent_calls_kept": policy.RECENT_TOOL_CALLS_KEPT,
                "tool_call_recency_tool_calls": policy.tool_call_count(items),
                "tool_call_recency_elidable_calls": len(
                    {
                        position
                        for position, group in enumerate(policy.call_groups(items))
                        if group and group[0] in policy.elidable_call_indices(items)
                    }
                ),
                "tool_call_recency_protected_indices": sorted(
                    policy.protected_call_indices(items)
                ),
                "tool_call_recency_elidable_indices": sorted(
                    policy.elidable_call_indices(items)
                ),
                "tool_call_recency_is_tool_call_unit": True,
            }
        )
        return record


def mechanism_identity() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mechanism_schema": policy.MECHANISM_SCHEMA,
        "v9_mechanism_schema": policy.v9.MECHANISM_SCHEMA,
        **policy.policy_dict(),
    }


__all__ = ["SCHEMA", "STAGES", "ToolCallRecencyRecorder", "mechanism_identity"]
