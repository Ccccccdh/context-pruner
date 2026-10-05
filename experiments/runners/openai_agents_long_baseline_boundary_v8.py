"""v8 long-baseline boundary: v7's recency rule on a longer baseline task.

The mechanism is **not** new.  v8 is v7's policy verbatim -

    elidable(turn) := newest_turn - turn >= RECENT_TURNS_KEPT      (frozen N = 1)

- with the same v6 reduction, the same guards, the same whole-payload fallback and the
same budgets.  What changes is the task *input*: the public Django issue keeps its frozen
text and gains one extra user message carrying a pre-registered read-only investigation
protocol (six separate, one-tool-per-turn reads before the answer), so the baseline runs
to K ~= 10-12 model calls and ``max(0, K - 1 - N)`` becomes positive.  That is the second
half of the applicability boundary v7 could only infer.

Why the task id is invisible to the mechanism
---------------------------------------------
The long task exposes **the same three registered source ranges and the same literal
set** as the frozen ``django_count_annotations`` task: the v5 literal registration and
the v6 span table are reused, not re-derived.  The mechanism therefore keeps resolving
``self.task`` through those frozen keys - so every registry lookup the audited v6 code
performs (span, pointer claim, literal labels, task units) behaves exactly as it did in
v5/v6/v7 - and the long task id lives in ``self.long_task``, used only for the v8
registration's own checks and for the counters.

Two deliberate amendments, both asserted by the offline gate:

* :meth:`LongBaselineBoundaryFilter._verbatim_units` switches off the frozen reduction's
  *message-unit repeat* rule.  That rule is not what the boundary is about, and on this
  task it makes the mechanism unusable for a measurement reason: the SDK history already
  repeats the issue header and the request line across two user messages, so the rule
  drops them from the rebuilt message on every call, the structural check then reports
  ``non_protected_units_changed`` and the whole call falls back.  On the short task that
  was harmless (nothing was elidable anyway - v7's rows carry five
  ``protected_item_dropped`` entries per plugin sample and still report an identical
  payload); on the long task it blocks *every* span elision.  With the rule off, message
  text is sent verbatim, which is strictly more conservative.
* :meth:`LongBaselineBoundaryFilter._output_decision` records, for an elidable output
  that was nevertheless kept, the reason it was kept, so the evidence and the audit can
  read it instead of a bare empty reason.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_recency_boundary_v7 as policy
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    SelectiveRetentionFilter,
)
from experiments.runners.openai_agents_span_retention_v6 import (
    CONTEXT_LINES,
    NOTE_PREFIX,
    SpanRetentionFilter,
    _sha,
    span_record_sha256,
)

#: Schema version of this version's recorded counters.
MECHANISM_SCHEMA = "openai_agents_long_baseline_boundary_v8"

#: The recency window.  Identical to v7's frozen value.
RECENT_TURNS_KEPT = policy.RECENT_TURNS_KEPT

#: Task ids this version can run.
LONG_TASK_ID = long_registry.TASK_ID
SHORT_TASK_ID = long_registry.SHORT_TASK_ID
TASKS = (LONG_TASK_ID, SHORT_TASK_ID)

#: The frozen task id whose registration supplies the source ranges and literal labels.
SOURCE_REGISTRATION_TASK = SHORT_TASK_ID

MANIFEST_POLICY = {
    "policy": "long_baseline_boundary",
    "recent_turns_kept_verbatim": RECENT_TURNS_KEPT,
    "elidable_rule": policy.MANIFEST_POLICY["elidable_rule"],
    "reduction": "v6 registered-span line-range elision, unchanged",
    "guards": "v6 literal-survival guard, pointer completeness, whole-payload fallback",
    "task_input_change": (
        "the long task keeps the frozen issue and request messages verbatim and appends "
        "one user message carrying a pre-registered read-only investigation protocol (six "
        "one-tool-per-turn reads); the mechanism, the thresholds and the budgets are v7's"
    ),
    "source_registration_task": SOURCE_REGISTRATION_TASK,
    "amendments": (
        "message-unit repeat rule disabled (message text is sent verbatim, strictly more "
        "conservative); kept-output reasons recorded for elidable candidates"
    ),
}


class LongBaselineBoundaryFilter(policy.RecencyBoundaryFilter):
    """v7's recency-restricted retention, on the v8 registration.

    ``self.task`` stays the frozen registration id so every v6 lookup resolves through the
    audited keys; ``self.long_task`` carries the v8 task id for the registration checks and
    the counters.
    """

    def __init__(
        self,
        *,
        task: str,
        task_statement: str,
        constraint_phrases: Mapping[str, Sequence[str]] | None = None,
        token_counter: Callable[[str], int] | None = None,
        hard_limit_bytes: int = 24_000,
    ) -> None:
        if str(task) not in TASKS:
            raise ValueError(f"unsupported v8 task id: {task!r}")
        registration_task = (
            SOURCE_REGISTRATION_TASK if str(task) == LONG_TASK_ID else str(task)
        )
        # The registered *units* are the frozen issue-statement units plus the frozen
        # request line; the investigation protocol travels in its own message and is
        # deliberately not registered.  Registering it would protect the seven STEP lines,
        # which the mechanism never rebuilds (they live in a frozen history message), so the
        # protection would be untestable - and those lines are not constraints anyway.
        registered_statement = "\n".join(
            line
            for line in str(task_statement).splitlines()
            if not str(line).strip().startswith("STEP ")
        ).strip()
        super().__init__(
            task=registration_task,
            task_statement=registered_statement,
            constraint_phrases=constraint_phrases,
            token_counter=token_counter,
            hard_limit_bytes=hard_limit_bytes,
        )
        #: The v8 task id this run is about (the mechanism itself resolves through
        #: ``self.task``, which is the frozen registration id).
        self.long_task = str(task)
        #: The frozen v5 constructor does not keep the statement; this version needs it.
        self.task_statement = str(task_statement)
        self.literal_units = long_registry.literal_units(self.long_task, self.task_statement)
        self.long_registry_fingerprint = long_registry.registry_fingerprint(self.long_task)
        self.long_registry_problems = long_registry.verify(self.long_task)
        self.long_base_registry_fingerprint = long_registry.base_fingerprint(self.long_task)
        #: Reasons an elidable candidate was nevertheless kept, per call.
        self.kept_output_reasons: list[dict[str, Any]] = []

    # -- the two amendments ------------------------------------------------

    def _verbatim_units(self) -> set[str]:
        """Switch off the frozen message-unit repeat rule (see the module docstring)."""
        return set()

    def _output_decision(self, item: Any, index: int, recent: set[int], names: Mapping[str, str]):
        """v7's recency gate, then v6's eligibility, with the keep reason recorded."""
        if index not in self.last_elidable_indices:
            from context_pruner.adapters.openai_agents import _item_type

            if str(_item_type(item)).endswith("_output"):
                self.recency_protected_outputs += 1
            return None, "within_recent_turns_kept_verbatim"
        outcome = SpanRetentionFilter._output_decision(self, item, index, recent, names)
        if outcome[0] is None:
            self.kept_output_reasons.append(
                {"index": int(index), "reason": str(outcome[1] or "kept_without_reason")}
            )
        return outcome

    def _filter(self, items: Sequence[Any], recent: set[int]):
        """v6's filter, with every message item restored to the text it arrived with.

        The frozen reduction rebuilds message content by dropping *repeated* units, and
        that rewrite is what the structural check objects to: a protected unit whose text
        also appears inside another message (the issue header, the request line) is dropped
        from the rebuild, the unit multiset changes, and the whole call falls back.  On the
        short task that never mattered (nothing was elidable); here it would block every
        span elision, so this version restores message items verbatim.  The reduction this
        version is about - the registered-span line-range elision of **tool outputs** - is
        untouched, and message text is now sent exactly as observed, which is strictly more
        conservative than the frozen behaviour.
        """
        from context_pruner.adapters.openai_agents import _is_message_item, _item_type

        filtered, dropped, compacted, decision = super()._filter(items, recent)
        restored: list[Any] = []
        for original, candidate in zip(items, filtered):
            if _is_message_item(original) or not str(_item_type(original)).endswith(
                ("_call", "_output")
            ):
                restored.append(original)
                continue
            restored.append(candidate)
        if len(filtered) != len(items):
            # A length change means something structural happened elsewhere; keep the
            # original outcome so the mechanism's own check can report it.
            return filtered, dropped, compacted, decision
        return restored, 0, compacted, decision

    # -- counters ----------------------------------------------------------

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "long_baseline_task_id": self.long_task,
                "long_baseline_registration_task": self.task,
                "long_baseline_recent_turns_kept_verbatim": RECENT_TURNS_KEPT,
                "long_baseline_registry_schema": long_registry.REGISTRY_SCHEMA,
                "long_baseline_registry_fingerprint": self.long_registry_fingerprint,
                "long_baseline_base_registry_fingerprint": (
                    self.long_base_registry_fingerprint
                ),
                "long_baseline_registry_problems": list(self.long_registry_problems),
                "long_baseline_literal_context_units": list(
                    long_registry.LITERAL_CONTEXT_UNITS.get(self.long_task, ())
                ),
                "long_baseline_literal_unit_count": len(self.literal_units),
                "long_baseline_source_registration_task": SOURCE_REGISTRATION_TASK,
                "long_baseline_elided_sources": self.elided_source_spans,
                "long_baseline_kept_output_reasons": list(self.kept_output_reasons),
                "long_baseline_elidable_turn_items": len(self.last_elidable_indices),
            }
        )
        return metrics


#: Re-exported so the runner, the evidence layer and the audit import one module.
elidable_indices = policy.elidable_indices
protected_turn_indices = policy.protected_turn_indices
turn_indices = policy.turn_indices
turn_count = policy.turn_count
is_tool_item = policy.is_tool_item
message_item_count = policy.message_item_count
output_item_count = policy.output_item_count
jsonable = policy.jsonable
boundary_proposition = policy.boundary_proposition


def inherited_method_objects() -> dict[str, Any]:
    """The frozen method objects every inherited behaviour must still be."""
    return {
        "_elide": SpanRetentionFilter._elide,
        "_structural_violation": SpanRetentionFilter._structural_violation,
        "_carrier_loss": SelectiveRetentionFilter._carrier_loss,
        "_group_change": SpanRetentionFilter._group_change,
        "_filter": SelectiveRetentionFilter._filter,
    }


def long_task_boundary(baseline_model_calls: int, baseline_input_tokens: int) -> dict[str, Any]:
    """The boundary statement for the long baseline, naming both measured sides."""
    proposition = boundary_proposition(baseline_model_calls, baseline_input_tokens)
    proposition.update(
        {
            "regime": "long" if proposition["plugin_can_elide_anything"] else "short",
            "short_side_measured_by": (
                "v7 batch openai-repo-diagnostic-v7-applicability-boundary "
                "(payload identical to baseline, paired saving 0.0000 %)"
            ),
            "long_side_measured_by": (
                "v8 batch openai-repo-diagnostic-v8-long-baseline-boundary (this batch)"
            ),
        }
    )
    return proposition


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
    "RECENT_TURNS_KEPT",
    "SHORT_TASK_ID",
    "SOURCE_REGISTRATION_TASK",
    "TASKS",
    "LongBaselineBoundaryFilter",
    "boundary_proposition",
    "elidable_indices",
    "inherited_method_objects",
    "is_tool_item",
    "jsonable",
    "long_registry",
    "long_task_boundary",
    "message_item_count",
    "output_item_count",
    "policy_dict",
    "protected_turn_indices",
    "span_record_sha256",
    "turn_count",
    "turn_indices",
    "_sha",
]
