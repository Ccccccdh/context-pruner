"""v10: recency measured in **tool-call ordinals** instead of contiguous tool groups.

The hypothesis under test
-------------------------
v7-v9 kept the newest N *contiguous tool groups* verbatim.  On the real SDK payload every
tool item forms one contiguous run (recorded as ``turn_count = 1`` at every boundary of the
paid v9 batch), so that rule protects the whole accumulated history and the plugin can
never compress anything: v7 measured 0.0000 % and v9 measured −0.000004 %.

The only direction v9 left un-falsified is to change the *unit* of recency: protect the
newest N **tool calls** (a call together with the output that answers it) and allow the
older calls to be reduced.  That is what this module implements - as a **hypothesis to be
tested on the recorded real payloads**, not as a claimed improvement.

Everything else is inherited unchanged from v9/v6: the registered-span line-range elision,
the literal-survival guard, the pointer completeness check, the structural check, the
message-unit classification of v9, the whole-payload fallback, the thresholds and the
budgets.  The offline gate asserts that by object identity.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from context_pruner.adapters.openai_agents import _item_type, _jsonable_item

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_baseline_boundary_v9 as v9
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners.openai_agents_span_retention_v6 import (
    CONTEXT_LINES,
    NOTE_PREFIX,
    _sha,
    span_record_sha256,
)

#: Schema version of this version's recorded counters.
MECHANISM_SCHEMA = "openai_agents_tool_call_recency_v10"

#: Task ids this version can run.
LONG_TASK_ID = v9.LONG_TASK_ID
SHORT_TASK_ID = v9.SHORT_TASK_ID
TASKS = v9.TASKS
SOURCE_REGISTRATION_TASK = v9.SOURCE_REGISTRATION_TASK

#: How many newest **tool calls** stay verbatim.  Frozen at 1: the current call's evidence
#: is what the newest turn reasons about, and anything older has already been delivered.
RECENT_TOOL_CALLS_KEPT = 1

MANIFEST_POLICY = {
    **v9.MANIFEST_POLICY,
    "policy": "tool_call_recency",
    "module": "openai_agents_long_baseline_boundary_v10",
    "recency_unit": "tool call (a call together with the output that answers it)",
    "recent_tool_calls_kept_verbatim": RECENT_TOOL_CALLS_KEPT,
    "elidable_rule": (
        "a tool call is elidable when the number of newer tool calls in the payload is "
        ">= recent_tool_calls_kept_verbatim"
    ),
    "hypothesis": (
        "on the real recorded payloads the contiguous-group rule protected everything "
        "(turn_count = 1, elidable_indices empty); measuring recency in tool calls should "
        "leave the older calls elidable - this is measured by replay, not assumed"
    ),
    "unchanged": (
        "span reduction, literal-survival guard, pointer checks, structural check, "
        "message-unit classification, whole-payload fallback, thresholds, budgets"
    ),
}


def call_groups(items: Sequence[Any]) -> list[list[int]]:
    """Group payload indices by tool call, in order of first appearance.

    A group is one tool call together with the output that answers it (they share a
    ``call_id``).  An output whose call is not in the payload forms its own group, so no
    item is ever left unaccounted for.
    """
    groups: list[list[int]] = []
    index_of: dict[str, int] = {}
    for index, item in enumerate(items):
        item_type = str(_item_type(item))
        if not item_type.endswith(("_call", "_output")):
            continue
        raw = _jsonable_item(item)
        call_id = str(raw.get("call_id") or "") if isinstance(raw, Mapping) else ""
        if not call_id:
            groups.append([index])
            continue
        position = index_of.get(call_id)
        if position is None:
            index_of[call_id] = len(groups)
            groups.append([index])
        else:
            groups[position].append(index)
    return groups


def protected_call_indices(
    items: Sequence[Any], keep: int = RECENT_TOOL_CALLS_KEPT
) -> set[int]:
    """Indices of the tool items belonging to the newest ``keep`` tool calls."""
    groups = call_groups(items)
    if not groups:
        return set()
    newest = len(groups)
    threshold = max(0, newest - int(keep))
    protected: set[int] = set()
    for position in range(threshold, newest):
        protected.update(groups[position])
    return protected


def elidable_call_indices(
    items: Sequence[Any], keep: int = RECENT_TOOL_CALLS_KEPT
) -> set[int]:
    """Indices of the tool items belonging to calls older than the newest ``keep``."""
    groups = call_groups(items)
    if not groups:
        return set()
    newest = len(groups)
    threshold = max(0, newest - int(keep))
    elidable: set[int] = set()
    for position in range(0, threshold):
        elidable.update(groups[position])
    return elidable


def tool_call_count(items: Sequence[Any]) -> int:
    return len(call_groups(items))


class ToolCallRecencyFilter(v9.NarrowGuardBoundaryFilter):
    """v9's narrowed-guard filter with recency measured in tool calls.

    One behavioural override: :meth:`_filter` installs the tool-call window before the
    frozen v6 filter body runs.  The reduction, the guards, the classification and the
    fallback semantics are inherited from v9/v6 and asserted by identity in the gate.

    ``recent_calls_kept`` defaults to this version's frozen constant; a later version (v11)
    passes another value for its elision ladder, which is the single variable it varies.
    """

    def __init__(
        self, *args: Any, recent_calls_kept: int | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.recent_calls_kept = (
            RECENT_TOOL_CALLS_KEPT if recent_calls_kept is None else int(recent_calls_kept)
        )

    def _filter(self, items: Sequence[Any], recent: set[int]):
        """Set the tool-call window, then run the frozen v6 filter body.

        ``_filter`` is where ``_output_decision`` reads ``last_elidable_indices``, and the
        inherited ``filter_items`` chain recomputes that attribute in contiguous groups just
        before calling here - so this is the one place where the tool-call-ordinal window can
        be installed without being overwritten.  Everything the frozen body does (message-unit
        classification, span reduction, guards, structural check) is untouched.
        """
        window = elidable_call_indices(items, self.recent_calls_kept)
        protected = protected_call_indices(items, self.recent_calls_kept)
        self.last_elidable_indices = window
        self.last_protected_turn_items = len(protected)
        groups = call_groups(items)
        self.tool_call_count_value = len(groups)
        self.tool_call_elidable_calls = sum(
            1 for group in groups if group and group[0] in window
        )
        self.tool_call_protected_calls = len(groups) - self.tool_call_elidable_calls
        return v9.v8.SpanRetentionFilter._filter(self, items, recent)

    def filter_items(self, items: Sequence[Any]):
        """Run the inherited pipeline, then restore the tool-call-ordinal window.

        The inherited chain recomputes the window in contiguous groups for its own evidence;
        this version's window is restored afterwards so the counters and the recorded
        decisions describe the rule that was actually applied.
        """
        result = super(v9.NarrowGuardBoundaryFilter, self).filter_items(items)
        self.last_elidable_indices = elidable_call_indices(items, self.recent_calls_kept)
        self.last_protected_turn_items = len(
            protected_call_indices(items, self.recent_calls_kept)
        )
        return result

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "tool_call_recency_recent_calls_kept": int(self.recent_calls_kept),
                "tool_call_recency_tool_calls": int(
                    getattr(self, "tool_call_count_value", 0)
                ),
                "tool_call_recency_protected_calls": int(
                    getattr(self, "tool_call_protected_calls", 0)
                ),
                "tool_call_recency_elidable_calls": int(
                    getattr(self, "tool_call_elidable_calls", 0)
                ),
                "tool_call_recency_elidable_indices": sorted(
                    self.last_elidable_indices
                ),
                "tool_call_recency_elided_sources": self.elided_source_spans,
            }
        )
        return metrics


def call_index_position(items: Sequence[Any], index: int) -> int:
    """Position of the call group that contains ``index`` (0-based, oldest first)."""
    for position, group in enumerate(call_groups(items)):
        if index in group:
            return position
    return -1


#: Re-exported so the runner, the replay evaluator and the gate import one module.
boundary_proposition = v9.boundary_proposition
long_task_boundary = v9.long_task_boundary
inherited_method_objects = v9.inherited_method_objects
protectable_units = v9.protectable_units
protectable_unit_sha256 = v9.protectable_unit_sha256


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
    "LONG_TASK_ID",
    "MANIFEST_POLICY",
    "MECHANISM_SCHEMA",
    "NOTE_PREFIX",
    "RECENT_TOOL_CALLS_KEPT",
    "SHORT_TASK_ID",
    "SOURCE_REGISTRATION_TASK",
    "TASKS",
    "ToolCallRecencyFilter",
    "boundary_proposition",
    "call_groups",
    "call_index_position",
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
