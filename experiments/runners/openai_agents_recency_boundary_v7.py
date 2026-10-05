"""v7 applicability boundary: compress only turns older than the current one.

Why this version exists
-----------------------
v4, v5 and v6 each changed *how* the plugin reduces a payload, and each failed for a
different reason:

* v4 asked only "has this text been delivered verbatim already" and dropped the
  Django literal ``existing_annotations`` that lives inside one source-code tool
  output - plugin strict quality 0/3;
* v5 pinned every registered literal carrier whole - quality recovered to 3/3, but
  the plugin took three model calls where the baseline took two and cost
  **-79.79 %** paired complete-total tokens;
* v6 replaced whole-text pinning with line-range elision plus a recovery pointer -
  evidence safety held (0 literal losses, 0 restore failures, pointers complete),
  but the real model *re-issued the tools to recover the omitted lines*, so the
  plugin needed 4-6 calls against the baseline's 2-4 and cost **-176.36 %**.

The v5 cost decomposition (``.tooling/diagnose_v5_cost_regression.py``) already
said where the money goes: the extra model round is **+2,335.8 input tokens, 99.85 %
of the regression**, while the re-sent pinned bytes are **-136.7 tokens, i.e. the
plugin's second call was 137 tokens *cheaper* than the baseline's**.  A mechanism
that changes what the model can see can therefore only win if it does **not** add a
round.

v7 is the smallest policy that can be stated without inventing a seventh reduction
shape.  It keeps the **most recent N assistant turns verbatim** and only allows the
existing, already-audited reduction (v6's registered-span line-range elision, with
its literal-survival guard, pointer completeness check and whole-payload fallback)
on **older** turns:

    elidable(turn) := current_index - turn_index >= N

Consequences, and they are the point of this version:

* on a task the baseline finishes in ``K <= N`` model calls, **nothing is ever
  elidable**, so v7 sends exactly the baseline payload - the plugin cannot win, and
  cannot lose either;
* on a long conversation the older turns are the bulk of the payload, and compressing
  them does not remove anything the current turn is reasoning about, so the round
  count is not provoked;
* the boundary between those two regimes is what this version measures.

The policy is deliberately *only* a recency rule.  The reduction itself, the
registered literal registry, the evidence layer, the guards and the fallback counters
are v6's, unchanged - which is also what keeps the comparison across versions
meaningful.
"""

from __future__ import annotations

from typing import Any, Sequence

from context_pruner.adapters.openai_agents import _is_message_item, _item_type, _jsonable_item
from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import openai_agents_span_retention_v6 as v6
from experiments.runners.openai_agents_span_retention_v6 import (  # noqa: F401
    CONTEXT_LINES,
    NOTE_PREFIX,
    SpanRetentionFilter as V6SpanRetentionFilter,
    note_is_pointer,
    span_record_sha256,
)

#: Schema version of this policy's recorded counters.
MECHANISM_SCHEMA = "openai_agents_recency_boundary_v7"

#: Number of most-recent assistant turns whose tool groups are kept verbatim.
#:
#: Frozen at 1 in the protocol, for a stated reason: the boundary this version is
#: about is "the plugin can only ever compress history that is at least one turn
#: behind the newest one", and N = 1 is the *smallest* recency window that still
#: protects everything the current turn is reasoning about.  A larger N only makes
#: the plugin weaker, so N = 1 is the most favourable setting for the plugin and
#: therefore the honest one to state the boundary from: if the plugin cannot win at
#: N = 1 on a short task, no larger N helps.  (N = 2 would also make the offline
#: gate unable to exercise the elision path at all on this frozen payload sequence,
#: because the first call has no history and the second call's only candidate output
#: belongs to the newest turn.)
RECENT_TURNS_KEPT = 1

#: Fields this version adds to the v6 manifest block.
MANIFEST_POLICY = {
    "policy": "recency_boundary",
    "recent_turns_kept_verbatim": RECENT_TURNS_KEPT,
    "elidable_rule": "current_call_index - turn_index >= recent_turns_kept_verbatim",
    "reduction": "v6 registered-span line-range elision, unchanged",
    "guards": "v6 literal-survival guard, pointer completeness, whole-payload fallback",
}


#: Item types the plugin may ever reduce.  Membership is decided by type, not by
#: "not a message": the SDK emits assistant messages as ``response.output_message``
#: objects in some payloads, and those are neither tool items nor the plain dicts
#: ``_is_message_item`` recognises.
_TOOL_ITEM_SUFFIXES = ("_call", "_output")


def is_tool_item(item: Any) -> bool:
    """True when an item is a Responses tool item (a call or an output)."""
    item_type = str(_item_type(item))
    return item_type.endswith(_TOOL_ITEM_SUFFIXES)


def turn_indices(items: Sequence[Any]) -> dict[int, int]:
    """Map every item index to the index of the assistant turn it belongs to.

    A *turn* is one run of **tool items**, which is exactly what one assistant step
    produces: a step that issues two tool calls in parallel (as the frozen v5/v6
    batches recorded for Django) is one turn holding both call/output pairs, while a
    step that issues one call is one turn holding one pair.  Anything that is not a
    tool item separates two turns; message items map to -1 and are never candidates,
    because the mechanism only ever reduces registered source-view tool outputs.
    """
    mapping: dict[int, int] = {}
    turn = -1
    previous_was_tool = False
    for index, item in enumerate(items):
        if not is_tool_item(item):
            mapping[index] = -1
            previous_was_tool = False
            continue
        if not previous_was_tool:
            turn += 1
        mapping[index] = turn
        previous_was_tool = True
    return mapping


def protected_turn_indices(items: Sequence[Any], keep: int = RECENT_TURNS_KEPT) -> set[int]:
    """Indices of the tool items that belong to the most recent ``keep`` turns."""
    mapping = turn_indices(items)
    turns = sorted({value for value in mapping.values() if value >= 0})
    if not turns:
        return set()
    newest = turns[-1]
    threshold = newest - int(keep) + 1
    return {index for index, value in mapping.items() if value >= threshold}


def elidable_indices(items: Sequence[Any], keep: int = RECENT_TURNS_KEPT) -> set[int]:
    """Indices of tool items old enough to be reduced under this policy."""
    mapping = turn_indices(items)
    turns = sorted({value for value in mapping.values() if value >= 0})
    if not turns:
        return set()
    newest = turns[-1]
    threshold = newest - int(keep)
    return {index for index, value in mapping.items() if 0 <= value <= threshold}


class RecencyBoundaryFilter(V6SpanRetentionFilter):
    """v6's span retention, restricted to turns older than the newest N.

    Only two things are added to the frozen v6 class: the recency gate in
    :meth:`_output_decision` (an output in a protected turn is kept, with the reason
    recorded) and the per-sample counters the boundary report needs.  The reduction,
    the registry, the guards and the fallback accounting are unchanged, so a v7
    sample and a v6 sample differ in exactly one variable.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: Indices the recency policy made eligible on the last call.
        self.last_elidable_indices: set[int] = set()
        #: Tool items the recency policy protected on the last call.
        self.last_protected_turn_items = 0
        #: Outputs kept *because* of the recency policy, counted per call.
        self.recency_protected_outputs = 0
        self.recency_protected_calls = 0

    # -- policy ------------------------------------------------------------

    def _output_decision(self, item: Any, index: int, recent: set[int], names: Any):
        """Keep evidence belonging to the newest N turns, whatever else says.

        The check runs after the newest-group check (which is v6's) and before every
        reduction rule, so a protected turn can never be reduced by accident.  The
        reason is recorded so the evidence layer can show *why* a compressible-looking
        output was kept.
        """
        if index not in self.last_elidable_indices:
            if str(_item_type(item)).endswith("_output"):
                self.recency_protected_outputs += 1
            return None, "within_recent_turns_kept_verbatim"
        return super()._output_decision(item, index, recent, names)

    def filter_items(self, items: Sequence[Any]):
        """Recompute the recency window, then delegate to the v6 mechanism."""
        self.last_elidable_indices = elidable_indices(items)
        self.last_protected_turn_items = len(protected_turn_indices(items))
        before = self.recency_protected_outputs
        outcome = super().filter_items(items)
        if self.recency_protected_outputs > before:
            self.recency_protected_calls += 1
        return outcome

    # -- counters ----------------------------------------------------------

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "recency_boundary_recent_turns_kept_verbatim": RECENT_TURNS_KEPT,
                "recency_boundary_turn_count": len(
                    {value for value in self.last_elidable_indices} or {0}
                ),
                "recency_boundary_protected_turn_items": self.last_protected_turn_items,
                "recency_boundary_elidable_indices": sorted(self.last_elidable_indices),
                "recency_boundary_protected_outputs": self.recency_protected_outputs,
                "recency_boundary_protected_calls": self.recency_protected_calls,
                "recency_boundary_elided_sources": self.elided_source_spans,
            }
        )
        return metrics


def policy_dict() -> dict[str, Any]:
    """The frozen policy statement, for the manifest, protocol and audit."""
    return {
        **MANIFEST_POLICY,
        "registry_schema": registry.REGISTRY_SCHEMA,
        "base_registry_schema": registry.base.REGISTRY_SCHEMA,
        "context_lines": CONTEXT_LINES,
        "note_prefix": NOTE_PREFIX,
        "schema": MECHANISM_SCHEMA,
    }


def boundary_proposition(
    baseline_model_calls: int, baseline_input_tokens: int
) -> dict[str, Any]:
    """The falsifiable boundary statement for one measured baseline.

    ``plugin_wins_possible`` is False when the baseline never has an elidable turn,
    i.e. when it finishes within the frozen recency window; the proposition is that a
    content-changing mechanism can only win above that regime, and the number chain
    that makes the short regime necessarily negative is returned with it.
    """
    elidable_turns = max(0, int(baseline_model_calls) - 1 - RECENT_TURNS_KEPT)
    return {
        "frozen_recent_turns_kept_verbatim": RECENT_TURNS_KEPT,
        "baseline_model_calls": int(baseline_model_calls),
        "elidable_turns_available_to_the_plugin": elidable_turns,
        "plugin_can_elide_anything": elidable_turns > 0,
        "plugin_wins_possible": elidable_turns > 0,
        "baseline_input_tokens": int(baseline_input_tokens),
        "short_regime_arithmetic": {
            "v5_plugin_extra_round_input_tokens": 2335.8,
            "v5_extra_round_share_of_input_regression": 0.9985,
            "v5_pinned_text_effect_input_tokens": -136.7,
            "baseline_complete_total_tokens": 2962,
            "reading": (
                "one extra round costs 2,335.8 input tokens - 99.85 % of the v5 "
                "regression and more than the whole 2,962-token baseline complete "
                "total; re-sent pinned bytes are worth -136.7 tokens, i.e. they help. "
                "So on a baseline this short a mechanism that changes visible text can "
                "only be negative, and a mechanism that changes nothing can only be "
                "zero."
            ),
        },
    }


def turn_count(items: Sequence[Any]) -> int:
    return len({value for value in turn_indices(items).values() if value >= 0})


def message_item_count(items: Sequence[Any]) -> int:
    return sum(1 for item in items if _is_message_item(item))


def output_item_count(items: Sequence[Any]) -> int:
    return sum(1 for item in items if str(_item_type(item)).endswith("_output"))


def tool_item_count(items: Sequence[Any]) -> int:
    return sum(1 for item in items if is_tool_item(item))


def jsonable(item: Any) -> Any:
    return _jsonable_item(item)


__all__ = [
    "CONTEXT_LINES",
    "MANIFEST_POLICY",
    "MECHANISM_SCHEMA",
    "NOTE_PREFIX",
    "RECENT_TURNS_KEPT",
    "RecencyBoundaryFilter",
    "boundary_proposition",
    "elidable_indices",
    "is_tool_item",
    "message_item_count",
    "note_is_pointer",
    "output_item_count",
    "policy_dict",
    "protected_turn_indices",
    "registry",
    "span_record_sha256",
    "tool_item_count",
    "turn_count",
    "turn_indices",
]
