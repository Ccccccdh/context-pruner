"""v11: the elision-ratio boundary - protect the newest M tool calls, elide older ones.

The question this version answers
---------------------------------
v10 measured +27.68 % paired complete-total saving with plugin strict quality **0/3** by
protecting only the newest tool call (``M = 1``).  This version makes ``M`` the **single
variable** and asks whether a smaller elision keeps quality while still saving tokens:

    elidable(call) := the number of newer tool calls in the payload >= M

Everything else is frozen: the v9 narrowed-guard classification, the v6 span reduction,
the literal-survival guard, the pointer checks, the structural check, the whole-payload
fallback, the thresholds and the budgets.  Only ``M`` changes between arms of the
ablation, and the offline replay records each ``M`` before any paid request.

The cross-evidence that motivates protecting more context: on this task the host-native
summary arm also reduces input substantially and also loses quality (v10 batch: native
1/3), which says the task is sensitive to what the model can see.  So the ablation is
designed to **protect quality first** and take the largest ``M`` whose projection is still
positive.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, Sequence

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_baseline_boundary_v10 as v10
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners.openai_agents_span_retention_v6 import (
    CONTEXT_LINES,
    NOTE_PREFIX,
    _sha,
    span_record_sha256,
)

#: Schema version of this version's recorded counters.
MECHANISM_SCHEMA = "openai_agents_elision_ratio_boundary_v11"

#: The pre-registered elision ladder.  ``M = 6`` is the most conservative setting on this
#: task's recorded structure (six tool calls exist, so nothing is elidable at all).
ELISION_LADDER: tuple[int, ...] = (6, 4, 2)

#: Environment variable that selects ``M`` for one process.  It exists so the ablation can
#: be driven without editing this file between arms; the value is recorded in the manifest,
#: the protocol and every row, and the gate asserts the ladder is the frozen one.
ELISION_M_ENV = "DSH_V11_RECENT_CALLS_KEPT"

#: Default ``M`` when the environment does not select one (the most conservative setting).
DEFAULT_ELISION_M = ELISION_LADDER[0]

LONG_TASK_ID = v10.LONG_TASK_ID
SHORT_TASK_ID = v10.SHORT_TASK_ID
TASKS = v10.TASKS
SOURCE_REGISTRATION_TASK = v10.SOURCE_REGISTRATION_TASK


def current_elision_m() -> int:
    """The ``M`` this process runs with (the frozen ladder only)."""
    raw = os.getenv(ELISION_M_ENV, "")
    if not raw:
        return DEFAULT_ELISION_M
    try:
        value = int(str(raw).strip())
    except ValueError as error:  # pragma: no cover - guarded by the runner
        raise ValueError(f"{ELISION_M_ENV} must be an integer, got {raw!r}") from error
    if value not in ELISION_LADDER:
        raise ValueError(
            f"{ELISION_M_ENV}={value} is not on the pre-registered ladder {ELISION_LADDER}"
        )
    return value


MANIFEST_POLICY = {
    **v10.MANIFEST_POLICY,
    "policy": "elision_ratio_boundary",
    "module": "openai_agents_long_baseline_boundary_v11",
    "single_variable": "recent_tool_calls_kept (M)",
    "elision_ladder": list(ELISION_LADDER),
    "elidable_rule": (
        "a tool call is elidable when the number of newer tool calls in the payload is "
        ">= M"
    ),
    "unchanged": (
        "v10 recency unit (tool call), v9 narrowed-guard classification, v6 span "
        "reduction, literal-survival guard, pointer checks, structural check, "
        "whole-payload fallback, thresholds, budgets"
    ),
    "quality_first": (
        "the native-summary arm also reduces input and also loses quality on this task, so "
        "the ablation takes the largest M whose replay projection is still positive"
    ),
}


class ElisionRatioFilter(v10.ToolCallRecencyFilter):
    """v10's filter with the ladder value ``M`` supplied by the frozen environment knob.

    The window computation, the recency unit, the reduction, the guards, the classification
    and the fallback semantics are v10's (which are v9's/v6's) and asserted by identity in
    the gate; the only difference is the value of ``M`` and the schema label.
    """

    def __init__(self, *args: Any, recent_calls_kept: int | None = None, **kwargs: Any) -> None:
        super().__init__(
            *args,
            recent_calls_kept=current_elision_m() if recent_calls_kept is None
            else int(recent_calls_kept),
            **kwargs,
        )

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "elision_ratio_recent_calls_kept_m": int(self.recent_calls_kept),
                "elision_ratio_ladder": list(ELISION_LADDER),
                "elision_ratio_elidable_calls": int(
                    getattr(self, "tool_call_elidable_calls", 0)
                ),
                "elision_ratio_protected_calls": int(
                    getattr(self, "tool_call_protected_calls", 0)
                ),
                "elision_ratio_elided_sources": self.elided_source_spans,
            }
        )
        return metrics


def call_groups(items: Sequence[Any]) -> list[list[int]]:
    return v10.call_groups(items)


def protected_call_indices(
    items: Sequence[Any], keep: int = DEFAULT_ELISION_M
) -> set[int]:
    return v10.protected_call_indices(items, keep)


def elidable_call_indices(
    items: Sequence[Any], keep: int = DEFAULT_ELISION_M
) -> set[int]:
    return v10.elidable_call_indices(items, keep)


def tool_call_count(items: Sequence[Any]) -> int:
    return v10.tool_call_count(items)


#: Re-exported for the runner, the replay evaluator and the gate.
boundary_proposition = v10.boundary_proposition
long_task_boundary = v10.long_task_boundary
inherited_method_objects = v10.inherited_method_objects
protectable_units = v10.protectable_units
protectable_unit_sha256 = v10.protectable_unit_sha256


def policy_dict() -> dict[str, Any]:
    return {
        **MANIFEST_POLICY,
        "registry_schema": long_registry.REGISTRY_SCHEMA,
        "base_registry_schema": base_registry.REGISTRY_SCHEMA,
        "context_lines": CONTEXT_LINES,
        "note_prefix": NOTE_PREFIX,
        "schema": MECHANISM_SCHEMA,
        "investigation_steps": list(long_registry.INVESTIGATION_STEPS),
    }


__all__ = [
    "CONTEXT_LINES",
    "DEFAULT_ELISION_M",
    "ELISION_LADDER",
    "ELISION_M_ENV",
    "ElisionRatioFilter",
    "LONG_TASK_ID",
    "MANIFEST_POLICY",
    "MECHANISM_SCHEMA",
    "NOTE_PREFIX",
    "SHORT_TASK_ID",
    "SOURCE_REGISTRATION_TASK",
    "TASKS",
    "boundary_proposition",
    "call_groups",
    "current_elision_m",
    "elidable_call_indices",
    "inherited_method_objects",
    "long_registry",
    "long_task_boundary",
    "policy_dict",
    "protectable_unit_sha256",
    "protectable_units",
    "protected_call_indices",
    "span_record_sha256",
    "tool_call_count",
    "_sha",
]
