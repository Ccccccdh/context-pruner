"""Content-free v11 evidence: v10's records plus the elision-ratio ladder step.

Reuses the v10 recorder and adds the one variable this version varies: ``M``, the number of
newest tool calls kept verbatim.  Counts and digests only, never text.
"""

from __future__ import annotations

from typing import Any, Sequence

from experiments.runners import openai_agents_long_baseline_boundary_v11 as policy
from experiments.runners.openai_agents_evidence_v10 import ToolCallRecencyRecorder

SCHEMA = "openai_elision_ratio_boundary_v11"


class ElisionRatioRecorder(ToolCallRecencyRecorder):
    """v10's recorder with the ladder value and the elision ratio recorded per boundary."""

    def _snapshot(
        self,
        items: Sequence[Any],
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        record["schema"] = SCHEMA
        groups = policy.call_groups(items)
        elidable = policy.elidable_call_indices(items, policy.current_elision_m())
        protected = policy.protected_call_indices(items, policy.current_elision_m())
        elidable_calls = sum(1 for group in groups if group and group[0] in elidable)
        record.update(
            {
                "elision_ratio_recent_calls_kept_m": int(policy.current_elision_m()),
                "elision_ratio_ladder": list(policy.ELISION_LADDER),
                "elision_ratio_tool_calls": len(groups),
                "elision_ratio_elidable_calls": elidable_calls,
                "elision_ratio_protected_calls": len(groups) - elidable_calls,
                "elision_ratio_elidable_fraction": (
                    elidable_calls / len(groups) if groups else 0.0
                ),
                "elision_ratio_elidable_indices": sorted(elidable),
                "elision_ratio_protected_indices": sorted(protected),
            }
        )
        return record


def mechanism_identity() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "mechanism_schema": policy.MECHANISM_SCHEMA,
        "v10_mechanism_schema": policy.v10.MECHANISM_SCHEMA,
        **policy.policy_dict(),
    }


__all__ = ["ElisionRatioRecorder", "SCHEMA", "mechanism_identity"]
