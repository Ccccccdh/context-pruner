"""v21 literal-carrier-preserving line elision: compress content that is not redundant at all.

What this module is for
-----------------------
The v20 round characterised the existing plugin, on this host, as an *evidence preserving
redundancy remover*: it removes exactly the part of the context that is redundant and its own
literal guard refuses, correctly, to remove any content that uniquely carries a registered
required literal.  On a read shape where no view repeats it therefore measures 0.00 percent
(``integrations/openai_agents/V20_RESULT_AND_CHARACTERISATION_20261010.json``).

The v21 question is whether content that is **not** redundant can be compressed while every
registered literal still survives:
``integrations/openai_agents/V21_LITERAL_CARRIER_ELISION_PLAN_20261010.json``.

The mechanism, exactly as pre-registered
----------------------------------------
Installed in the **plugin arm only**, and it runs **after** the existing rules (v12 exact
duplicate replay, then the v5 lineage evidence-safe selective retention).  The two stages under
it are reached through the same two calls the v20 composite makes - ``super().filter_items`` and
``v5.SelectiveRetentionFilter.filter_items`` - so **no module below this one is edited** and the
frozen v20 files keep their pinned digests.  For each tool output that is not part of the most
recent tool group:

1. **protect** every line that contains a registered literal or registered constraint phrase
   (case-insensitive, exact substring), together with a fixed window of :data:`WINDOW_LINES`
   lines before and after each such line, clipped to the output;
2. **elide** every other line, replacing each maximal contiguous omitted run with exactly one
   note line of the form ``[elided n lines first-last]`` and nothing else;
3. the **newest tool group** is kept verbatim (the same recency policy the existing lineage
   uses, ``recent_tool_group_indices`` in v5).

The window is frozen at three: it is declared in the pre-registration and may not be changed
after any saving number is seen.  It is written as a module constant so that the tests can pin
it and so that a later change is a visible edit to a registered module rather than a tuning
parameter.

The invariant that makes this legitimate
----------------------------------------
For every registered literal and constraint phrase, the number of verbatim occurrences in the
elided payload must be **greater than or equal to** the number in the payload that entered this
stage - counted per occurrence, not per distinct string.  The counting is the v5 lineage's own
:meth:`SelectiveRetentionFilter._carrier_loss` over the canonical JSON of the two item lists, so
the new mechanism is held to exactly the bar that already vetoed the old one.  (On this chain
only that function's occurrence-count half is live: ``self.task`` is the frozen registration id
and ``register_task_data`` records an empty tuple in ``registry.SOURCES``, so the attribution
half inspects nothing - recorded in
``V20_RETENTION_ENGAGEMENT_DIAGNOSIS_20261010.json`` and unchanged here.)

Fail-closed
-----------
Three conditions each cause the **whole payload** of that call to be sent verbatim, and each is
counted in ``v21_literal_guard_fallbacks``:

* the invariant above fails for any registered literal or phrase;
* the elision does not strictly reduce the payload's bytes (so the note overhead has to be paid
  for by the lines it replaces);
* any unexpected exception in the stage.

Content is never removed on the strength of an argument this module cannot check.  A note line
can never become a carrier of a literal either: it is generated text of a fixed form, and the
guard is evaluated on the payload as it would be sent.

Published counters
------------------
``v21_outputs_considered``
    Tool outputs (after the rules above) this stage looked at.
``v21_outputs_elided``
    Outputs whose text was actually shortened.
``v21_lines_elided``
    Lines removed, summed over those outputs.
``v21_bytes_saved``
    Net canonical-JSON bytes saved on this payload: the stage's input bytes minus the bytes it
    would send.  The note overhead is therefore already deducted - only the net is published.
``v21_literal_guard_fallbacks``
    Calls on which the whole payload was kept verbatim.
``v21_note_bytes``
    Bytes the note lines cost, so the overhead is reportable next to the net.

Every pre-existing counter is kept: ``metrics_dict`` calls ``super().metrics_dict()`` and only
adds to it.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from experiments.runners import openai_agents_evidence_safe_retention_v5 as v5
from experiments.runners.openai_agents_multitask_chain_v14 import MultitaskDuplicateFilter
from experiments.runners.openai_agents_v20_retention import NonDuplicateRetentionFilter

MECHANISM_SCHEMA = "openai_agents_v21_literal_carrier_elision"
COMPOSITION_SCHEMA = "duplicate_then_selective_retention_then_line_elision_v21"
#: Text substituted into the mechanism name of the manifest block.
MECHANISM_NAME = "literal_carrier_preserving_line_elision_v21"

#: The frozen protection window: this many lines are kept before and after every line that
#: carries a registered literal or constraint phrase.  Pre-registered as three; see the module
#: docstring.  The tests assert this value, so changing it is a deliberate, visible edit.
WINDOW_LINES = 3

#: The exact shape of an omitted-interval note line.  Nothing else may be written into one.
NOTE_PREFIX = "[elided "
NOTE_FORM = "[elided {count} lines {first}-{last}]"

#: New counters this version publishes, in the order the reports read them.
V21_COUNTER_FIELDS = (
    "v21_outputs_considered",
    "v21_outputs_elided",
    "v21_lines_elided",
    "v21_bytes_saved",
    "v21_literal_guard_fallbacks",
    "v21_note_bytes",
)
#: Existing counters that must survive the composition unchanged.
PRESERVED_COUNTER_FIELDS = (
    "exact_duplicate_replacements",
    "exact_duplicate_saved_bytes",
    "exact_duplicate_rejected",
    "selective_retention_saved_bytes_total",
    "v20_retention_engaged_calls",
    "v20_retention_fallback_calls",
    "v20_units_elided",
    "v20_bytes_saved",
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


def note_line(count: int, first: int, last: int) -> str:
    """The one and only note form: ``[elided n lines first-last]``."""
    return NOTE_FORM.format(count=int(count), first=int(first), last=int(last))


def is_note_line(text: str) -> bool:
    """Whether a line is one of this mechanism's notes (used by the tests and the report)."""
    return str(text).startswith(NOTE_PREFIX)


class LiteralCarrierElisionFilter(NonDuplicateRetentionFilter):
    """The v20 composition plus a line elision that keeps every registered literal.

    The constructor is inherited unchanged: the v5 lineage state this composite reads is the same
    set v20's ``self_check`` already proves.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.v21_outputs_considered = 0
        self.v21_outputs_elided = 0
        self.v21_lines_elided = 0
        self.v21_bytes_saved = 0
        self.v21_literal_guard_fallbacks = 0
        self.v21_note_bytes = 0
        #: Why the v20 composition stage did what it did on the most recent call, so the v20
        #: counters this version advances stay explicable from the record.
        self.v20_last_composition_reason = ""
        #: Per-call record of what this stage did, for the evidence layer and the gate.
        self.v21_last_call: dict[str, Any] = {}
        self.v21_calls: list[dict[str, Any]] = []
        self.v21_composition_schema = COMPOSITION_SCHEMA

    # -- entry method ------------------------------------------------------

    def filter_items(self, items: Sequence[Any]) -> list[Any] | None:
        """Dedupe, retain, then elide carrier-free lines - or send the payload verbatim.

        The first two stages are the v20 composite's, reached through the same two calls v20
        itself makes - ``MultitaskDuplicateFilter.filter_items`` for the frozen v12 duplicate
        rule, then ``v5.SelectiveRetentionFilter.filter_items`` for the evidence-safe retention -
        so this version adds a stage without editing the version under it.  The v12 stage is named
        explicitly rather than reached through ``super()``: ``super()`` would be the *v20* entry
        point, which already performs both stages, and running it here would put the retention
        stage on the call path twice.  The v20 counters are advanced here by exactly the rule v20
        advances them, so a v21 row reports both stages.
        """
        observed = list(items)
        try:
            deduped = MultitaskDuplicateFilter.filter_items(self, items)
        except Exception:  # fail closed: v12 could not decide, so nothing may be removed
            deduped = None
        if deduped is None:
            dedupe_base = observed
            sent_observed = True
        else:
            dedupe_base = list(deduped)
            sent_observed = False

        before = {
            "compacted_calls": int(self.compacted_calls),
            "duplicate_units_dropped": int(self.duplicate_units_dropped),
            "compacted_tool_outputs": int(self.compacted_tool_outputs),
            "saved_bytes_total": int(self.saved_bytes_total),
        }
        dedupe_bytes = _bytes(dedupe_base)
        try:
            retained = v5.SelectiveRetentionFilter.filter_items(self, dedupe_base)
        except Exception as error:  # fail closed: keep the deduplicated content verbatim
            retained = None
            self.v20_last_composition_reason = f"{type(error).__name__}: {error}"
        if retained is None:
            base = dedupe_base
            self.v20_retention_fallback_calls += 1
        else:
            base = list(retained)
            units = (
                int(self.compacted_calls)
                - before["compacted_calls"]
                + int(self.duplicate_units_dropped)
                - before["duplicate_units_dropped"]
                + int(self.compacted_tool_outputs)
                - before["compacted_tool_outputs"]
            )
            self.v20_retention_engaged_calls += 1
            self.v20_units_elided += int(units)
            self.v20_bytes_saved += dedupe_bytes - _bytes(base)

        base_bytes = _bytes(base)
        self.v20_last_composition_reason = (
            "v12 rejected the payload" if sent_observed else "v12 decided"
        )
        try:
            elided, plan, note_bytes = self._elide(base)
        except Exception as error:  # fail closed: keep the payload verbatim
            return self._v21_fallback(
                base,
                base_bytes,
                reason=f"exception:{type(error).__name__}: {error}",
                plan={},
                note_bytes=0,
            )

        self.v21_outputs_considered += int(plan.get("outputs_considered", 0))
        if not plan.get("outputs_elided"):
            # Nothing to remove on this call.  This is not a fallback: no invariant was at risk
            # and no content was touched, so the payload is sent exactly as the earlier stages
            # left it, and the counter records that this stage did nothing.  ``None`` is returned
            # only when the earlier stages themselves decided to send the observed input, so the
            # v12/v20 fallback semantics ("send the observed input unchanged") are preserved.
            self._record_call(
                base,
                base,
                base_bytes=base_bytes,
                after_bytes=base_bytes,
                plan=plan,
                note_bytes=0,
                status="no_elision",
                detail="",
            )
            return None if sent_observed else list(base)

        after_bytes = _bytes(elided)
        loss = self._carrier_loss(base, elided)
        if loss:
            return self._v21_fallback(
                base,
                base_bytes,
                reason="registered_literal_lost:" + ",".join(loss[:4]),
                plan=plan,
                note_bytes=note_bytes,
            )
        if after_bytes >= base_bytes:
            return self._v21_fallback(
                base,
                base_bytes,
                reason="no_measured_reduction",
                plan=plan,
                note_bytes=note_bytes,
            )

        self.v21_outputs_elided += int(plan["outputs_elided"])
        self.v21_lines_elided += int(plan["lines_elided"])
        self.v21_bytes_saved += base_bytes - after_bytes
        self.v21_note_bytes += int(note_bytes)
        self._record_call(
            base,
            elided,
            base_bytes=base_bytes,
            after_bytes=after_bytes,
            plan=plan,
            note_bytes=note_bytes,
            status="engaged",
            detail="",
        )
        return elided

    # -- the mechanism -----------------------------------------------------

    def _elide(self, base: Sequence[Any]) -> tuple[list[Any], dict[str, Any], int]:
        """Return ``(payload, plan, note_bytes)`` for one composition result.

        ``plan`` is content-free: counts, line indices and the note forms only.  The newest tool
        group is never touched, so the items it covers are copied through unchanged.
        """
        from context_pruner.adapters.openai_agents import _item_type, _jsonable_item

        recent = v5.recent_tool_group_indices(base)
        literals = [str(text).lower() for text in self.literal_texts]
        out: list[Any] = []
        plan: dict[str, Any] = {
            "outputs_considered": 0,
            "outputs_elided": 0,
            "lines_elided": 0,
            "notes": [],
            "protected_lines": 0,
            "recent_group_items_kept": len(recent),
            "window_lines": WINDOW_LINES,
            "changed": False,
        }
        note_bytes = 0
        for index, item in enumerate(base):
            raw = _jsonable_item(item)
            if (
                not str(_item_type(item)).endswith("_output")
                or not isinstance(raw, Mapping)
                or not isinstance(raw.get("output"), str)
            ):
                out.append(item)
                continue
            plan["outputs_considered"] += 1
            if index in recent:
                out.append(item)
                plan["notes"].append({"call_id": str(raw.get("call_id") or ""), "elided": False,
                                      "reason": "most_recent_tool_group"})
                continue
            text = str(raw["output"])
            new_text, record = self._elide_text(text, literals)
            if new_text is None:
                out.append(item)
                plan["notes"].append(
                    {"call_id": str(raw.get("call_id") or ""), "elided": False, "reason": "unchanged"}
                )
                continue
            rebuilt = dict(raw)
            rebuilt["output"] = new_text
            out.append(rebuilt)
            plan["changed"] = True
            plan["outputs_elided"] += 1
            plan["lines_elided"] += int(record["lines_elided"])
            plan["protected_lines"] += int(record["protected_lines"])
            plan["notes"].append(record)
            note_bytes += int(record["note_bytes"])
        return out, plan, note_bytes

    def _elide_text(self, text: str, literals: Sequence[str]) -> tuple[str | None, dict[str, Any]]:
        """Elide one output's text.

        ``(None, record)`` means "keep this output unchanged".  An output whose every line is
        inside the protected set produces no elision rather than an empty output, because
        deleting the whole carrier of an output is exactly the content this mechanism must never
        remove.
        """
        lines = str(text).splitlines()
        protected = self._protected_line_indices(lines, literals)
        record: dict[str, Any] = {
            "elided": True,
            "lines": len(lines),
            "protected_lines": len(protected),
            "lines_elided": 0,
            "notes": [],
            "note_bytes": 0,
        }
        if not protected or len(protected) >= len(lines):
            record["elided"] = False
            record["reason"] = (
                "no_protected_line" if not protected else "every_line_is_protected"
            )
            return None, record

        kept: list[str] = []
        position = 0
        total_lines = len(lines)
        while position < total_lines:
            if position in protected:
                kept.append(lines[position])
                position += 1
                continue
            start = position
            while position < total_lines and position not in protected:
                position += 1
            # A maximal contiguous omitted run of at least one line: exactly one note, nothing
            # else.  The loop can only leave ``start`` unadvanced by leaving it protected, which
            # the branch above handles, so the run length is never zero.
            note = note_line(position - start, start, position - 1)
            kept.append(note)
            record["lines_elided"] += position - start
            record["note_bytes"] += len(note.encode("utf-8"))
            record["notes"].append(
                {
                    "count": position - start,
                    "first": start,
                    "last": position - 1,
                    "text": note,
                    "bytes": len(note.encode("utf-8")),
                }
            )
        new_text = "\n".join(kept)
        if new_text == text:
            record["elided"] = False
            record["reason"] = "unchanged"
            return None, record
        return new_text, record

    def _protected_line_indices(
        self, lines: Sequence[str], literals: Sequence[str]
    ) -> set[int]:
        """Line indices to keep: literal carriers plus the frozen window around each one.

        The window is read from ``self.v21_window_lines`` when the instance carries one, and from
        the module constant otherwise.  This class holds no such attribute, so for it the window
        is :data:`WINDOW_LINES` and nothing changes; the v22 dose configurations are subclasses
        that bind the attribute, which is how one mechanism is run at K=3, K=1 and K=0 without
        editing this module or re-implementing the stage.
        """
        window = int(getattr(self, "v21_window_lines", WINDOW_LINES))
        carriers = [
            index
            for index, line in enumerate(lines)
            if any(literal in str(line).lower() for literal in literals)
        ]
        protected: set[int] = set()
        for index in carriers:
            first = max(0, index - window)
            last = min(len(lines) - 1, index + window)
            protected.update(range(first, last + 1))
        return protected

    # -- fail closed and bookkeeping ---------------------------------------

    def _v21_fallback(
        self,
        base: Sequence[Any],
        base_bytes: int,
        *,
        reason: str,
        plan: Mapping[str, Any],
        note_bytes: int,
    ) -> list[Any]:
        """Send the payload this stage received, verbatim, and count the refusal."""
        self.v21_literal_guard_fallbacks += 1
        self.v21_outputs_considered += int(plan.get("outputs_considered", 0))
        self._record_call(
            base,
            base,
            base_bytes=base_bytes,
            after_bytes=base_bytes,
            plan=dict(plan),
            note_bytes=int(note_bytes),
            status="fallback",
            detail=reason,
        )
        return list(base)

    def _record_call(
        self,
        base: Sequence[Any],
        result: Sequence[Any],
        *,
        base_bytes: int,
        after_bytes: int,
        plan: Mapping[str, Any],
        note_bytes: int,
        status: str,
        detail: str,
    ) -> None:
        entry = {
            "call": int(self.calls),
            "status": status,
            "fallback_reason": detail,
            "v20_composition_reason": str(self.v20_last_composition_reason),
            "base_items": len(base),
            "base_bytes": int(base_bytes),
            "after_bytes": int(after_bytes),
            "net_bytes_saved": int(base_bytes) - int(after_bytes),
            "note_bytes": int(note_bytes),
            "outputs_considered": int(plan.get("outputs_considered", 0)),
            "outputs_elided": int(plan.get("outputs_elided", 0)),
            "lines_elided": int(plan.get("lines_elided", 0)),
            "protected_lines": int(plan.get("protected_lines", 0)),
            "window_lines": WINDOW_LINES,
            "recent_group_items_kept": int(plan.get("recent_group_items_kept", 0)),
            "notes": list(plan.get("notes", [])),
            "aggregate_outputs_elided": self.v21_outputs_elided,
            "aggregate_lines_elided": self.v21_lines_elided,
            "aggregate_bytes_saved": self.v21_bytes_saved,
            "aggregate_note_bytes": self.v21_note_bytes,
            "aggregate_literal_guard_fallbacks": self.v21_literal_guard_fallbacks,
            # ``result`` length: how many items this call would send.  Accepted for symmetry
            # with v20's recorder and so a reader of the record does not have to infer it.
            "result_items": len(result),
        }
        self.v21_last_call = dict(entry)
        self.v21_calls.append(entry)

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "v21_mechanism_schema": MECHANISM_SCHEMA,
                "v21_composition_schema": COMPOSITION_SCHEMA,
                # The window that governed this instance, read the same way the stage reads it:
                # the instance attribute when a configuration bound one, the module constant
                # otherwise.  Reporting the module constant here made a K=1 or K=0 row claim
                # K=3 while its elision counts proved the narrower window had run.
                "v21_window_lines": int(getattr(self, "v21_window_lines", WINDOW_LINES)),
                "v21_default_window_lines": WINDOW_LINES,
                "v21_outputs_considered": self.v21_outputs_considered,
                "v21_outputs_elided": self.v21_outputs_elided,
                "v21_lines_elided": self.v21_lines_elided,
                "v21_bytes_saved": self.v21_bytes_saved,
                "v21_literal_guard_fallbacks": self.v21_literal_guard_fallbacks,
                "v21_note_bytes": self.v21_note_bytes,
                "v21_last_call": dict(self.v21_last_call),
            }
        )
        return metrics


__all__ = [
    "COMPOSITION_SCHEMA",
    "LiteralCarrierElisionFilter",
    "MECHANISM_NAME",
    "MECHANISM_SCHEMA",
    "NOTE_FORM",
    "NOTE_PREFIX",
    "PRESERVED_COUNTER_FIELDS",
    "V21_COUNTER_FIELDS",
    "WINDOW_LINES",
    "is_note_line",
    "note_line",
]
