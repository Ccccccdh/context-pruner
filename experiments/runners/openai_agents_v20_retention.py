"""v20 non-duplicate retention: compose the v12 duplicate rule with v5 evidence-safe retention.

Why this module exists
----------------------
The v19 mechanism wiring audit measured the plugin arm's filter chain on the N3 shape and found
that the only rule in force is the frozen v12 exact duplicate rule:

* ``MultitaskDuplicateFilter`` (v14) inherits ``ExactDuplicateFilter`` (v12), and **v12 owns
  ``filter_items``**;
* that ownership shadows the two ancestor implementations of the same entry method,
  ``RecencyBoundaryFilter.filter_items`` (v7) and ``SelectiveRetentionFilter.filter_items`` (v5);
* so on a shape where no view is read twice the rule has nothing to remove and the measured
  paired saving is exactly 0.00 percent - not because the mechanism cannot compress unique
  content, but because no mechanism that could was ever reached
  (``integrations/openai_agents/V19_MECHANISM_WIRING_AUDIT_20261010.json``).

This module does **not** write a new mechanism.  It is wiring only: one composite filter whose
entry method runs the duplicate rule first and then explicitly calls the shadowed v5 ancestor
implementation on what is left.  No frozen module is edited; the v5 literal guard and its
whole-payload fallback are the ones already registered for that lineage.

Order of operations, and why it is that order
---------------------------------------------
1. **dedupe first** (the frozen v12 rule, through ``super().filter_items``): an older tool
   output whose text is byte-identical to a newer one is replaced by a pointer naming the newest
   call id and the source sha256.  Its return value ``None`` means "send the observed input
   unchanged", so the composite distinguishes that case explicitly and falls back to the
   *observed* item list.
2. **then retain** (the v5 lineage evidence-safe selective retention): the representative of a
   repeated message unit, or an eligible, recoverable, non-constraint tool output, is replaced
   by a deterministic note.  Because this runs on the deduplicated list, the literal guard's
   view is the deduplicated content, and any byte saving this stage measures can only come from
   content that did not repeat inside the payload.

Fail-closed discipline
----------------------
Any exception raised by the v5 stage, and any ``None`` returned by it, makes the composite
return ``base`` - the deduplicated, verbatim item list - and increments
``v20_retention_fallback_calls``.  Content is never dropped because a stage misbehaved: the
worst outcome of a failure here is that the v20-only saving is not measured.

Published counters
------------------
``v20_retention_engaged_calls``
    Calls on which the v5 stage returned a filtered payload.
``v20_retention_fallback_calls``
    Calls on which it raised or returned ``None`` (the payload was sent deduplicated-verbatim).
``v20_units_elided``
    Units the v5 stage removed on this call: dropped repeated message units plus compacted tool
    outputs, taken as the delta of the v5 counters across this call.
``v20_bytes_saved``
    Canonical-JSON bytes the v5 stage removed from the deduplicated payload, measured directly
    so that the number cannot be confused with the duplicate rule's own saving.

Every pre-existing counter is kept: ``metrics_dict`` calls ``super().metrics_dict()`` and only
adds to it, so ``exact_duplicate_replacements``, ``selective_retention_saved_bytes_total`` and
the ``trigger_gate_*`` family keep their names and their meanings.

Composition seam for later versions (v21)
----------------------------------------
The two stages live in :meth:`NonDuplicateRetentionFilter._compose_base`, which returns the
decision without advancing this version's counters; :meth:`filter_items` is the only method that
records them.  A later version therefore composes on top of this one by calling ``_compose_base``
and continuing from ``base``, and its rows never double-count a v20 counter.  This extraction is
behaviour preserving: ``filter_items`` returns and counts exactly what it returned and counted
before it.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from experiments.runners import openai_agents_evidence_safe_retention_v5 as v5
from experiments.runners.openai_agents_multitask_chain_v14 import MultitaskDuplicateFilter

MECHANISM_SCHEMA = "openai_agents_v20_nonduplicate_retention"
COMPOSITION_SCHEMA = "duplicate_then_selective_retention_v20"
#: Text substituted into the mechanism name of the manifest block.
MECHANISM_NAME = "nonduplicate_selective_retention_v20"

#: New counters this version publishes, in the order the reports read them.
V20_COUNTER_FIELDS = (
    "v20_retention_engaged_calls",
    "v20_retention_fallback_calls",
    "v20_units_elided",
    "v20_bytes_saved",
)
#: Existing counters that must survive the composition unchanged.
PRESERVED_COUNTER_FIELDS = (
    "exact_duplicate_replacements",
    "exact_duplicate_saved_bytes",
    "exact_duplicate_rejected",
    "selective_retention_saved_bytes_total",
)

#: Attributes the shadowed v5 entry point reads.  Checked in ``__init__`` by
#: :meth:`NonDuplicateRetentionFilter.self_check`, so a chain that stops building one of them
#: fails loudly at construction time instead of degrading silently at run time.
V5_REQUIRED_ATTRIBUTES = (
    # read by v5 _filter / _output_decision / classify_outputs / literal_counts
    "task",
    "task_units",
    "task_unit_sha256",
    "literal_units",
    "literal_unit_sha256",
    "constraint_units",
    "constraint_unit_sha256",
    "protected_unit_sha256",
    "constraint_phrases",
    "literal_texts",
    "literal_text_count",
    "token_counter",
    "hard_limit_bytes",
    # run state v5 _filter mutates
    "calls",
    "compacted_calls",
    "duplicate_units_dropped",
    "compacted_tool_outputs",
    "saved_bytes_total",
    "last_saved_bytes",
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "constraint_guard_fallbacks",
    "last_call_decision",
    "group_hash_mismatch_count",
    "last_fallback_reason",
    "failure_reasons",
    "pinned_output_shas",
    "pinned_by_reason",
    "elided_output_shas",
    "recent_group_items_kept",
    "_group_hash_baseline",
)

#: Callables the shadowed v5 entry point and its helpers resolve on ``self``.  Each is checked
#: for ownership: an attribute that exists but is not reachable as a callable is a wiring fault
#: just as much as a missing one.
V5_REQUIRED_CALLABLES = (
    "filter_items",
    "_filter",
    "_fallback",
    "_carrier_loss",
    "_group_change",
    "_missing_literal_units",
    "_missing_protected_units",
    "_structural_violation",
    "observe",
    "observe_delivered",
    "classify_outputs",
    "literal_counts",
    "metrics_dict",
)


def _bytes(items: Sequence[Any]) -> int:
    """Canonical-JSON byte size of one item list, the same measure v5 uses internally."""
    from context_pruner.adapters.openai_agents import _jsonable_item

    return len(
        json.dumps(
            [_jsonable_item(item) for item in items],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


class NonDuplicateRetentionFilter(MultitaskDuplicateFilter):
    """The v12 duplicate rule followed by the v5 lineage evidence-safe selective retention.

    The chain's own constructor state is untouched: every attribute the shadowed v5 entry point
    reads is built by that constructor (directly or by the classes it calls), and
    :meth:`self_check` proves it before the filter is ever used.  In particular ``self.task``
    stays the frozen registration id the v8 boundary class deliberately pins, while
    ``self.constraint_phrases`` carries this task's registered literals, which is what the v5
    occurrence-count half of the literal guard reads.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v20_retention_engaged_calls = 0
        self.v20_retention_fallback_calls = 0
        self.v20_units_elided = 0
        self.v20_bytes_saved = 0
        #: Per-call record of what the composite did, for the evidence layer and the gate.
        self.v20_last_call: dict[str, Any] = {}
        self.v20_calls: list[dict[str, Any]] = []
        self.v20_composition_schema = COMPOSITION_SCHEMA

    # -- zero-API self check ----------------------------------------------

    def self_check(self) -> dict[str, Any]:
        """Assert that the shadowed v5 entry point has everything it reads.

        Returns a small report and raises :class:`RuntimeError` naming every missing attribute
        or unreachable callable.  A silent degradation - the failure mode that produced the
        0.00 percent N3 baseline while looking like a capability result - is not permitted.
        """
        missing = [name for name in V5_REQUIRED_ATTRIBUTES if not hasattr(self, name)]
        unreachable = [
            name for name in V5_REQUIRED_CALLABLES if not callable(getattr(self, name, None))
        ]
        if missing or unreachable:
            raise RuntimeError(
                "v20 retention wiring is incomplete: "
                f"missing attributes {missing}; unreachable callables {unreachable}"
            )
        for name in ("task_units", "protected_unit_sha256", "literal_texts"):
            if not getattr(self, name):
                raise RuntimeError(
                    f"v20 retention wiring is empty: {name} is falsy, so the literal guard "
                    "would run without a registered protection set"
                )
        return {
            "schema": COMPOSITION_SCHEMA,
            "checked_attributes": len(V5_REQUIRED_ATTRIBUTES),
            "checked_callables": len(V5_REQUIRED_CALLABLES),
            "task_units": len(self.task_units),
            "protected_unit_sha256": len(self.protected_unit_sha256),
            "literal_texts": len(self.literal_texts),
            "task": str(getattr(self, "task", "")),
            "heldout_task": str(getattr(self, "heldout_task", "")),
            "ok": True,
        }

    # -- entry method ------------------------------------------------------

    def filter_items(self, items: Sequence[Any]) -> list[Any] | None:
        """Dedupe with the frozen v12 rule, then run the v5 selective retention on the rest."""
        outcome = self._compose_base(items)
        base = outcome["base"]
        if outcome["status"] != "engaged":
            self.v20_retention_fallback_calls += 1
        else:
            self.v20_retention_engaged_calls += 1
            self.v20_units_elided += int(outcome["units"])
            self.v20_bytes_saved += int(outcome["saved"])
        self._record_call(
            base,
            duplicates=int(outcome["duplicates"]),
            status=str(outcome["status"]),
            detail=str(outcome["detail"]),
            units=int(outcome["units"]),
            saved=int(outcome["saved"]),
        )
        return outcome["result"]

    def _compose_base(self, items: Sequence[Any]) -> dict[str, Any]:
        """The dedupe-then-retain composition, without touching this version's counters.

        Extracted so that a later version can compose *on top of* this one (v21 adds a line
        elision stage after both of these) without the v20 counters being incremented twice or
        being advanced by a call path that is not v20's.  :meth:`filter_items` above is the only
        caller that records; the returned mapping is otherwise the whole decision:

        ``base``
            the deduplicated (and, when the retention stage engaged, retained) item list - the
            payload this version would send.
        ``result``
            what :meth:`filter_items` returns: ``base`` for every fallback, the retained list
            when the retention stage engaged.
        ``status``
            ``"engaged"`` or the reason no retention happened (``"fallback"``, ``"exception"``,
            ``"no_dedupe"``).
        ``duplicates``, ``units``, ``saved``
            the numbers :meth:`_record_call` reports.
        ``dedupe_rejected``
            true when the v12 stage rejected the payload, so ``base`` is the *observed* list and
            the caller must return ``None`` ("send the observed input unchanged") rather than the
            list, to keep v12's own fallback semantics intact.
        """
        observed = list(items)
        deduped = super().filter_items(items)
        if deduped is None:
            base = observed
            duplicates = 0
            dedupe_rejected = True
        else:
            base = list(deduped)
            duplicates = int(getattr(self, "exact_duplicate_replacements", 0))
            dedupe_rejected = False
        base_bytes = _bytes(base)
        before = {
            "compacted_calls": int(self.compacted_calls),
            "duplicate_units_dropped": int(self.duplicate_units_dropped),
            "compacted_tool_outputs": int(self.compacted_tool_outputs),
            "saved_bytes_total": int(self.saved_bytes_total),
        }
        try:
            out = v5.SelectiveRetentionFilter.filter_items(self, base)
        except Exception as error:  # fail closed: keep the deduplicated content verbatim
            return {
                "base": base,
                "result": base,
                "status": "exception",
                "detail": f"{type(error).__name__}: {error}",
                "duplicates": duplicates,
                "units": 0,
                "saved": 0,
                "dedupe_rejected": dedupe_rejected,
            }
        if out is None:
            return {
                "base": base,
                "result": base,
                "status": "fallback",
                "detail": str(getattr(self, "last_fallback_reason", "")),
                "duplicates": duplicates,
                "units": 0,
                "saved": 0,
                "dedupe_rejected": dedupe_rejected,
            }
        filtered = list(out)
        units = (
            int(self.compacted_calls)
            - before["compacted_calls"]
            + int(self.duplicate_units_dropped)
            - before["duplicate_units_dropped"]
            + int(self.compacted_tool_outputs)
            - before["compacted_tool_outputs"]
        )
        saved = base_bytes - _bytes(filtered)
        return {
            "base": base,
            "result": filtered,
            "status": "engaged",
            "detail": "",
            "duplicates": duplicates,
            "units": int(units),
            "saved": int(saved),
            "dedupe_rejected": dedupe_rejected,
        }

    # -- internals ---------------------------------------------------------

    def _record_call(
        self,
        base: Sequence[Any],
        *,
        duplicates: int,
        status: str,
        detail: str,
        units: int,
        saved: int,
    ) -> None:
        entry = {
            "call": int(self.calls),
            "status": status,
            "duplicate_replacements_after_dedupe": int(duplicates),
            "deduped_items": len(base),
            "deduped_bytes": _bytes(base),
            "units_elided": int(units),
            "bytes_saved": int(saved),
            "fallback_reason": detail,
            "aggregate_units_elided": self.v20_units_elided,
            "aggregate_bytes_saved": self.v20_bytes_saved,
        }
        self.v20_last_call = dict(entry)
        self.v20_calls.append(entry)

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "v20_mechanism_schema": MECHANISM_SCHEMA,
                "v20_composition_schema": COMPOSITION_SCHEMA,
                "v20_retention_engaged_calls": self.v20_retention_engaged_calls,
                "v20_retention_fallback_calls": self.v20_retention_fallback_calls,
                "v20_units_elided": self.v20_units_elided,
                "v20_bytes_saved": self.v20_bytes_saved,
                "v20_last_call": dict(self.v20_last_call),
            }
        )
        return metrics


def self_check_fields() -> list[str]:
    """The attributes :meth:`NonDuplicateRetentionFilter.self_check` requires (for the tests)."""
    return list(V5_REQUIRED_ATTRIBUTES)


__all__ = [
    "COMPOSITION_SCHEMA",
    "MECHANISM_NAME",
    "MECHANISM_SCHEMA",
    "NonDuplicateRetentionFilter",
    "PRESERVED_COUNTER_FIELDS",
    "V20_COUNTER_FIELDS",
    "V5_REQUIRED_ATTRIBUTES",
    "V5_REQUIRED_CALLABLES",
    "self_check_fields",
]
