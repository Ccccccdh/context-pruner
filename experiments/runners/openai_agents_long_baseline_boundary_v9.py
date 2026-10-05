"""v9: narrow the guard's protected set to what carries a registered literal.

Why this is a classification fix, not a relaxation
--------------------------------------------------
v8 built the long baseline (K = 7 model calls offline) and the plugin entered the
elision path (15 sources, 632 lines elided) - and then every call fell back with
``missing_protected_unit``.  The cause is a contradiction between two frozen rules:

* the guard protects **every** task-statement text unit, and the request line
  ("Diagnose why count() retains an unused annotation. ...") is one of them;
* the reduction drops a **message unit that was already delivered verbatim** in this run,
  and that same request line also occurs inside the issue statement, so it is dropped
  from the rebuilt message.

The dropped unit is not a constraint, so the fallback was a classification mismatch, not
a real loss.  The same guard already reported five ``protected_item_dropped`` fallbacks
per plugin sample on the **v7 short baseline** - harmless there only because nothing was
elidable anyway, which is why v7 still reported a payload identical to the baseline.

v9 changes exactly one thing: :class:`NarrowGuardBoundaryFilter` replaces the protected
set with the units that actually carry a **registered literal**, plus the registered
literal units themselves.  Everything else - the span elision, the literal-survival
guard (occurrence counts, per carrier tool), the pointer checks, the structural check,
the dedup rule, the whole-payload fallback, the thresholds and the budgets - is inherited
from the frozen v6/v7/v8 code and asserted by identity in the offline gate.

The two assertions this change must satisfy (and the gate proves them):

* **positive protection** - a message unit that carries a registered literal is never
  dropped, even when the dedup rule matches it: every registered literal is present at
  every model boundary with an occurrence count **not below the baseline arm's**;
* **negative control** - inject a unit that carries a registered literal *and* repeats
  inside the request message; it must **not** be dropped, and the guard must still refuse
  when that literal is about to be lost.  This shows the change fixed the classification
  contradiction instead of switching task-statement protection off wholesale.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_baseline_boundary_v8 as v8
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_recency_boundary_v7 as recency
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    MIN_UNIT_CHARS,
    SelectiveRetentionFilter,
    _split_message_content,
    _text_sha,
    normalise_unit,
)
from experiments.runners.openai_agents_span_retention_v6 import (
    CONTEXT_LINES,
    NOTE_PREFIX,
    SpanRetentionFilter,
    _sha,
    span_record_sha256,
)

#: Schema version of this version's recorded counters.
MECHANISM_SCHEMA = "openai_agents_narrow_guard_boundary_v9"

RECENT_TURNS_KEPT = v8.RECENT_TURNS_KEPT
LONG_TASK_ID = v8.LONG_TASK_ID
SHORT_TASK_ID = v8.SHORT_TASK_ID
TASKS = v8.TASKS
SOURCE_REGISTRATION_TASK = v8.SOURCE_REGISTRATION_TASK

MANIFEST_POLICY = {
    **v8.MANIFEST_POLICY,
    "policy": "narrow_guard_boundary",
    "module": "openai_agents_long_baseline_boundary_v9",
    "guard_change": (
        "the protected message-unit set is narrowed to the units that carry a registered "
        "literal (plus the registered literal units); the frozen rule that drops an "
        "already-delivered message unit is left in place, so the two rules no longer "
        "contradict each other"
    ),
    "unchanged": (
        "span reduction, literal-survival guard, pointer checks, structural check, dedup "
        "rule, whole-payload fallback, thresholds, budgets"
    ),
    "prior_observation": (
        "the same guard reported 5 protected_item_dropped fallbacks per plugin sample on "
        "the v7 short baseline; harmless there because no turn was elidable, recorded now "
        "because it is the same contradiction"
    ),
}


def protectable_units(task: str, statement: str) -> list[str]:
    """Task-statement units that carry a **registered literal** for ``task``.

    This is the narrowed protected set.  A unit qualifies only when one of the literals
    the frozen contracts depend on actually occurs in it; a unit such as the request line
    ("Diagnose why count() retains an unused annotation. ...") does not qualify and is
    therefore subject to the frozen dedup rule like any other repeated text.
    """
    units: list[str] = []
    for line in str(statement).splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) < MIN_UNIT_CHARS:
            continue
        if long_registry.labels_in_text(task, stripped):
            units.append(stripped)
    return units


def protectable_unit_sha256(task: str, statement: str) -> list[str]:
    return sorted({_text_sha(normalise_unit(unit)) for unit in protectable_units(task, statement)})


class NarrowGuardBoundaryFilter(v8.LongBaselineBoundaryFilter):
    """v8's filter with the protected message-unit set narrowed to literal carriers.

    One override (:meth:`__init__`) plus one recorded counter.  The reduction, the
    literal-survival guard, the pointer checks, the structural check, the recency gate,
    the dedup rule and the whole-payload fallback are the frozen ones, and the offline gate
    asserts that by identity.
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
        super().__init__(
            task=task,
            task_statement=task_statement,
            constraint_phrases=constraint_phrases,
            token_counter=token_counter,
            hard_limit_bytes=hard_limit_bytes,
        )
        stripped_statement = "\n".join(
            line
            for line in str(task_statement).splitlines()
            if not str(line).strip().startswith("STEP ")
        ).strip()
        #: The narrowed protected set: only units carrying a registered literal.
        self.protectable_units = protectable_units(self.long_task, stripped_statement)
        self.protected_unit_sha256 = protectable_unit_sha256(
            self.long_task, stripped_statement
        )
        #: Units the narrowing deliberately leaves unprotected (recorded, not stored as
        #: text: only their digests and counts).
        self.unprotected_statement_unit_sha256 = sorted(
            set(self.task_unit_sha256) - set(self.protected_unit_sha256)
        )
        self.guard_narrowing_schema = "narrow_to_registered_literal_carriers_v9"

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "narrow_guard_schema": self.guard_narrowing_schema,
                "narrow_guard_protected_unit_count": len(self.protected_unit_sha256),
                "narrow_guard_unprotected_statement_unit_count": len(
                    self.unprotected_statement_unit_sha256
                ),
                "narrow_guard_protectable_unit_count": len(self.protectable_units),
                "narrow_guard_full_statement_unit_count": len(self.task_unit_sha256),
            }
        )
        return metrics


#: Re-exported for the runner, evidence layer, audit and gate.
elidable_indices = v8.elidable_indices
protected_turn_indices = v8.protected_turn_indices
turn_indices = v8.turn_indices
turn_count = v8.turn_count
is_tool_item = v8.is_tool_item
message_item_count = v8.message_item_count
output_item_count = v8.output_item_count
jsonable = v8.jsonable
boundary_proposition = v8.boundary_proposition
long_task_boundary = v8.long_task_boundary
inherited_method_objects = v8.inherited_method_objects


def carrier_literal_counts(items: Any, literals: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Occurrence count of every registered literal in one payload (case-insensitive)."""
    from experiments.runners.openai_agents_evidence_safe_retention_v5 import _canonical

    text = _canonical(list(items)).lower()
    return {
        label: sum(text.count(str(phrase).lower()) for phrase in phrases)
        for label, phrases in literals.items()
    }


def message_units(items: Any) -> list[str]:
    """Every message unit in a payload (the text the dedup rule operates on)."""
    units: list[str] = []
    for item in items:
        units.extend(_split_message_content(item))
    return units


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


def evidence_record(**kwargs: Any) -> dict[str, Any]:
    """The evidence record shape this version writes (v8's, relabelled)."""
    record = dict(kwargs)
    record["schema"] = "openai_narrow_guard_boundary_v9"
    record["mechanism_schema"] = MECHANISM_SCHEMA
    return record


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
    "NarrowGuardBoundaryFilter",
    "boundary_proposition",
    "carrier_literal_counts",
    "elidable_indices",
    "evidence_record",
    "inherited_method_objects",
    "is_tool_item",
    "jsonable",
    "long_registry",
    "long_task_boundary",
    "message_item_count",
    "message_units",
    "output_item_count",
    "policy_dict",
    "protectable_unit_sha256",
    "protectable_units",
    "protected_turn_indices",
    "recency",
    "SelectiveRetentionFilter",
    "SpanRetentionFilter",
    "_sha",
    "span_record_sha256",
    "turn_count",
    "turn_indices",
    "v8",
]
