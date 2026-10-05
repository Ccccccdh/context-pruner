"""v6 pointer retention: line-range elision with registered-literal spans.

The problem v6 solves
---------------------
v5 recovered strict quality (3/3 against a 3/3 baseline) but cost **−79.79 %**
paired complete-total tokens.  Its own RESULTS.md names the mechanism-level cause:
the rule *must* re-send the whole 4,896-character constraint-carrying source output
on every call, because ``existing_annotations`` exists only inside that output and
v5's only way to protect a literal was to pin the entire text that carries it.
The offline diagnosis (``integrations/openai_agents/V5_COST_REGRESSION_DIAGNOSIS.md``)
decomposes that regression and shows where the cost actually is.

What v6 changes - exactly one mechanism change
----------------------------------------------
A constraint-carrying tool output is **no longer kept verbatim whole**.  It is kept
*line-range-wise*:

1. the lines that carry a registered literal stay **verbatim**, together with a
   frozen context window (:data:`CONTEXT_LINES`) around them - computed from the
   registry, not chosen per sample;
2. every omitted run of lines is replaced by a deterministic note that states the
   repository path, the **exact omitted line intervals**, the kept lines, the
   omitted character count and the single tool call that recovers the text;
3. the kept lines and the omitted intervals must **partition the registered range
   exactly** (:func:`openai_agents_literal_registry_v6.elision_completeness`), so
   "the omitted text is recoverable by re-issuing the tool" is a property of the
   artifact the model receives;
4. a non-carrier output whose tool has a registered source range is elided in
   full, as in v5;
5. everything v5 already guaranteed stays: the registered-literal survival guard,
   the protected-unit checks, the protected Responses tool-group boundary check,
   the newest-group pin, the byte ceiling, and the deterministic whole-payload
   fallback with its counters.

The second v6 change fixes the v5 ``_group_hashes`` false positive (the v5 paid
batch recorded one ``protected_tool_group_changed:baseline:...`` fallback per
sample whose payload was byte-identical before and after).  v6 keeps the group
baseline only from observations the mechanism did not rewrite and compares group
fingerprints on the **common prefix** of items, so a group that legitimately grows
by one call/output pair per turn is growth, not a change.  The frozen v5 sources
are not edited; the fix lives here.

Why this is expected to be cheaper than the baseline
----------------------------------------------------
On Django the v5 plugin's second model call carried 8,510 bytes while the paid
baseline's second call carried the same three source views unfiltered; v5 still
paid a third call.  v6 elides *inside* the large output and leaves the small one
in place, so the plugin's late payloads are far below the baseline's, and the
projection in the diagnosis is computed from the recorded byte trajectory rather
than assumed.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any, Mapping, Sequence

from context_pruner.adapters.openai_agents import (
    _item_type,
    _jsonable_item,
    _protect_responses_groups,
)

from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    MIN_COMPACTABLE_OUTPUT_CHARS,
    NOTE_PREFIX as V5_NOTE_PREFIX,
    SelectiveRetentionFilter,
    _canonical,
    _group_fingerprint,
    _sha,
    _text_sha,
)

#: Schema version of this mechanism's recorded counters.
MECHANISM_SCHEMA = "openai_agents_span_retention_v6"

#: Marker every v6 note starts with.  The evidence layer and the audit key on it.
NOTE_PREFIX = "[pointer-retention v6]"

#: Context lines kept around every literal-carrying line.  Mirrors
#: :data:`openai_agents_literal_registry_v6.CONTEXT_LINES` and is asserted equal by
#: the offline gate, so the frozen constant cannot drift between the two modules.
#: Zero is the honest minimum: the registered literal lines are what the frozen
#: quality contract depends on, and every other line is recoverable by pointer.
CONTEXT_LINES = registry.CONTEXT_LINES


class SpanRetentionFilter(SelectiveRetentionFilter):
    """v5 evidence-safe retention with line-range elision and a fixed baseline.

    Only three things differ from the frozen v5 class: which outputs may be elided
    and how (:meth:`_output_decision` / :meth:`_elide`), what counts as a legal
    replacement (:meth:`_structural_violation`), and the protected tool-group
    baseline (:meth:`_group_hashes` / :meth:`_group_change`).
    """

    #: v6 keeps the group baseline out of ``_group_hashes`` (see the method
    #: docstring), so the frozen v5 class keeps its own behaviour and its diagnosis
    #: test still reproduces the defect.
    _track_group_content_baseline = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        #: group_id -> canonical item fingerprints at first sight, compared on the
        #: common prefix and never written from a payload this mechanism rewrote.
        self._trusted_baseline: dict[str, list[str]] = {}
        #: group_id -> content digest at first sight (evidence only, never compared).
        self._trusted_content_baseline: dict[str, str] = {}
        #: Per-call span accounting for the evidence layer and the audit.
        self.elided_source_spans = 0
        self.elided_source_lines = 0
        self.kept_source_lines = 0
        self.kept_literal_lines = 0
        self.span_notes_with_omitted_intervals = 0
        self.last_call_elisions: list[dict[str, Any]] = []

    # -- protected tool groups (the v5 false-positive fix) -----------------

    @staticmethod
    def _fingerprints(group: Any) -> list[str]:
        """Canonical fingerprint of every item of one group, in payload order.

        The fingerprint covers the whole item - including a tool output's text - so a
        group whose content changed is detectable, and it is compared on the
        **common prefix** so a group that merely grew by one call/output pair per
        turn is growth rather than a change.  The mechanism never writes this
        baseline from an observation it rewrote: that write is the v5 defect.
        """
        return [_canonical(_jsonable_item(item)) for item in group.items]

    def _observe_group_baseline(self, items: Sequence[Any]) -> None:
        """Write the group baseline from an observation, first sight only.

        Only the *observed* (pre-filter) payload reaches this method, so a note this
        mechanism wrote can never become the baseline it is later judged against.
        The content digest stored alongside is evidence only; the comparison itself
        is the common-prefix fingerprint check in :meth:`_group_change`.
        """
        _, groups = _protect_responses_groups(items, self.token_counter)
        digests = self._group_hashes(items)
        for group in groups:
            group_id = str(group.group_id)
            self._trusted_baseline.setdefault(group_id, self._fingerprints(group))
            self._trusted_content_baseline.setdefault(group_id, digests.get(group_id, ""))

    def _group_hashes(self, items: Sequence[Any]) -> dict[str, str]:
        """Group digests for the evidence layer, without poisoning the baseline.

        The base class writes ``_group_hash_baseline`` with ``setdefault`` on
        *every* call, including the ``filter_after`` observation of a payload it
        just rewrote; that is the v5 defect.  v6 keeps the trusted baseline
        (:meth:`_observe_group_baseline`) instead and only records the per-call
        digest map that ``metrics_dict`` and the evidence layer report.
        """
        _, groups = _protect_responses_groups(items, self.token_counter)
        return {
            str(group.group_id): _sha(
                [_group_fingerprint(entry) for entry in group.items]
            )
            for group in groups
        }

    def _group_change(self, before: Sequence[Any], after: Sequence[Any]) -> str:
        """Compare like with like: the observed payload against the trusted baseline.

        A group whose item list grew is compared on its common prefix only, so growth
        is never reported as a change.  A group the observed payload no longer
        contains, or whose already-seen items moved or changed, still fails - and
        still falls the whole call back.
        """
        self._observe_group_baseline(before)
        _, groups_before = _protect_responses_groups(before, self.token_counter)
        before_by_id = {str(group.group_id): group for group in groups_before}
        mismatch = sorted(
            group_id for group_id in before_by_id if group_id not in self._trusted_baseline
        )
        for group_id, group in before_by_id.items():
            baseline = self._trusted_baseline.get(group_id, [])
            if self._fingerprints(group)[: len(baseline)] != baseline:
                mismatch.append(group_id)
        if mismatch:
            self.group_hash_mismatch_count += 1
            return "baseline:" + ",".join(sorted(set(mismatch))[:8])
        return ""

    # -- classification ----------------------------------------------------

    @staticmethod
    def _is_placeholder(value: Any) -> bool:
        """True when a tool output text is a note this mechanism wrote.

        v6 emits two shapes: a whole-output pointer note (``NOTE_PREFIX`` first,
        the v5 shape used for non-carrier outputs) and a span reduction where the
        source-view header is retained and the marker follows it on the same line.
        Both must be recognised here, otherwise ``pointer_coverage`` would only
        count the notes whose text the base class's prefix check happens to match.
        """
        if not isinstance(value, str) or not value:
            return False
        text = str(value).lstrip()
        if text.startswith(NOTE_PREFIX) or text.startswith(V5_NOTE_PREFIX):
            return True
        return NOTE_PREFIX in text.splitlines()[0]

    def classify_outputs(self, items: Sequence[Any]) -> dict[str, Any]:
        """v5 classification plus the registered span each output's tool declares."""
        classified = super().classify_outputs(items)
        for record in classified["outputs"]:
            tool = str(record.get("tool") or "")
            source = registry.span_for_tool(self.task, tool)
            record["registered_span"] = (
                [source.first_line, source.last_line, list(source.literal_lines)]
                if source is not None
                else None
            )
            record["registered_literal_line_count"] = (
                len(source.literal_lines) if source is not None else 0
            )
        return classified

    # -- elision -----------------------------------------------------------

    def _output_decision(
        self,
        item: Any,
        index: int,
        recent: set[int],
        names: Mapping[str, str],
    ) -> tuple[Any | None, str]:
        """Return ``(replacement_item_or_None, keep_reason)`` for one output.

        ``None`` means "keep this item unchanged"; the second element is the
        recorded reason.  Order of checks, and why:

        1. not a tool output / not text / not a mapping -> keep;
        2. part of the **most recent tool group** -> keep verbatim, so the evidence
           the current turn is reasoning about is never elided;
        3. too small to be worth a note -> keep;
        4. the exact text has not been delivered in this run yet -> keep, because
           the model must see it once in full;
        5. the tool has no **registered source range** -> keep: a note could not
           state a pointer the audit can verify;
        6. reduce by line range (:meth:`_elide`) -> replace, or keep when the
           replacement is not strictly smaller.
        """
        if not str(_item_type(item)).endswith("_output"):
            return None, "non_mapping_item"
        raw = _jsonable_item(item)
        if not isinstance(raw, Mapping):
            return None, "non_mapping_item"
        text = raw.get("output")
        if not isinstance(text, str):
            return None, "non_text_output"
        if index in recent:
            return None, "most_recent_tool_group"
        if len(text) < MIN_COMPACTABLE_OUTPUT_CHARS:
            return None, "below_min_compactable_size"
        if _text_sha(text) not in self._delivered_text_sha():
            return None, "not_yet_delivered"
        call_id = str(raw.get("call_id") or "")
        tool_name = names.get(call_id, "tool")
        source = registry.span_for_tool(self.task, tool_name)
        if source is None:
            return None, "not_a_registered_source_range"
        parsed = registry.base.parse_source_pointer(text)
        if parsed is None:
            return None, "no_recovery_pointer"
        if (parsed[0], parsed[1]) != (source.repo, source.path):
            return None, "not_a_registered_source_range"
        replacement, record = self._elide(
            item=raw,
            text=text,
            tool_name=tool_name,
            call_id=call_id,
            source=source,
            carrier_span=self._carried_literal_span(text),
        )
        if replacement is None:
            return None, str(record.get("reason") or "no_omittable_lines")
        if len(str(replacement.get("output"))) >= len(text):
            return None, "note_not_shorter"
        note_text = str(replacement.get("output") or "")
        record["call_id"] = call_id
        record["tool"] = tool_name
        record["output_chars"] = len(text)
        record["replacement_chars"] = len(note_text)
        record["output_sha256"] = _text_sha(text)
        record["span_sha256"] = span_record_sha256(record)
        record["pointer_completeness"] = not registry.elision_completeness(
            text, record["kept_lines"], record["omitted_intervals"]
        )
        record["note_has_omitted_intervals"] = bool(record["omitted_intervals"])
        record["note_states_pointer"] = registry.base.parse_pointer_claim(note_text) is not None
        self.last_call_elisions.append(record)
        # The v5 bookkeeping in ``_filter`` derives its recovery-pointer list from
        # ``registry.parse_pointer_claim(note_text)``, which a span note (whose marker
        # follows a retained header line) cannot satisfy.  v6 records the same
        # quantity here, from the note it actually emitted, so
        # ``pointer_coverage`` counts the span notes too.
        self._elided_pointers().append(
            {
                "call_id": call_id,
                "tool": tool_name,
                "output_sha256": record["output_sha256"],
                "source_pointer": [
                    source.repo,
                    source.path,
                    int(record["source_range"][0]),
                    int(record["source_range"][1]),
                ],
                "source_pointer_sha256": _sha(
                    [
                        source.repo,
                        source.path,
                        int(record["source_range"][0]),
                        int(record["source_range"][1]),
                    ]
                ),
                "pointer_matches_registered_source": bool(
                    registry.base.pointer_matches_registered_source(
                        self.task,
                        tool_name,
                        (
                            source.repo,
                            source.path,
                            int(record["source_range"][0]),
                            int(record["source_range"][1]),
                        ),
                    )
                ),
            }
        )
        self.elided_source_spans += 1
        self.elided_source_lines += int(record["omitted_line_count"])
        self.kept_source_lines += int(record["kept_line_count"])
        self.kept_literal_lines += int(record["kept_literal_line_count"])
        if record["omitted_intervals"]:
            self.span_notes_with_omitted_intervals += 1
        return replacement, ""

    def _carried_literal_span(self, text: str) -> tuple[int, int, list[int]] | None:
        """Registered literals the observed text carries, and the lines carrying them."""
        if not registry.base.labels_in_text(self.task, text):
            return None
        return registry.literal_lines_in_text(self.task, text)

    def _elide(
        self,
        *,
        item: Mapping[str, Any],
        text: str,
        tool_name: str,
        call_id: str,
        source: registry.RegisteredSpan,
        carrier_span: tuple[int, int, list[int]] | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Build the reduced item and the record describing the omission.

        A **carrier** (the observed text carries a registered literal) keeps its
        literal-carrying lines plus :data:`CONTEXT_LINES` context lines and omits
        the rest.  A **non-carrier** keeps the header only and omits the whole
        numbered body - the v5 behaviour, already fully recoverable by pointer.
        """
        lines = str(text).splitlines()
        header, body = lines[0], lines[1:]
        numbered: list[tuple[int, str]] = []
        for line in body:
            parsed = registry.parse_numbered_line(line)
            if parsed is None:
                return None, _rejected(tool_name, source, "unparseable_source_view")
            numbered.append(parsed)
        if not numbered:
            return None, _rejected(tool_name, source, "empty_source_view")
        first, last = numbered[0][0], numbered[-1][0]
        if [number for number, _ in numbered] != list(range(first, last + 1)):
            return None, _rejected(tool_name, source, "non_contiguous_source_view")
        if carrier_span is not None:
            literal_lines = sorted(set(int(value) for value in carrier_span[2]))
            keep: set[int] = set()
            for number in literal_lines:
                keep.update(range(number - CONTEXT_LINES, number + CONTEXT_LINES + 1))
            keep = {number for number in keep if first <= number <= last}
        else:
            literal_lines = []
            keep = set()
        omitted = [number for number, _ in numbered if number not in keep]
        if not omitted:
            return None, _rejected(tool_name, source, "every_line_carries_a_literal")
        omitted_intervals = _intervals(omitted)
        kept_lines = [number for number, _ in numbered if number in keep]
        omitted_char_count = sum(
            len(value) + 1 for number, value in numbered if number not in keep
        )
        note_text = self._span_note(
            tool_name=tool_name,
            call_id=call_id,
            repo=source.repo,
            path=source.path,
            first=first,
            last=last,
            omitted_intervals=omitted_intervals,
            kept_lines=kept_lines,
            omitted_char_count=omitted_char_count,
            original_chars=len(text),
            carrier=carrier_span is not None,
        )
        body_lines = [
            line for line in body if (registry.parse_numbered_line(line) or (0, ""))[0] in keep
        ]
        replacement = dict(item)
        # The header is *retained* (it states the path and range the note points
        # at) and the note is appended to it on the same line, so the elided
        # output is a v6 pointer note the structural check can recognise while the
        # path/range header stays readable.  The kept lines follow verbatim, one
        # per line.  No item is added, removed or re-ordered, so the Responses tool
        # group boundary is untouched.
        replacement["output"] = "\n".join([f"{header} {note_text}", *body_lines])
        record = {
            "tool": tool_name,
            "call_id": call_id,
            "source_range": [first, last],
            "registered_source_range": [source.first_line, source.last_line],
            "omitted_intervals": [list(interval) for interval in omitted_intervals],
            "kept_lines": kept_lines,
            "kept_literal_lines": literal_lines,
            "omitted_line_count": len(omitted),
            "kept_line_count": len(kept_lines),
            "kept_literal_line_count": len(literal_lines),
            "omitted_char_count": omitted_char_count,
            "original_chars": len(text),
            "carrier": carrier_span is not None,
            "recovery_pointer_sha256": _sha([source.repo, source.path, first, last]),
            "pointer_matches_registered_source": (
                registry.base.pointer_matches_registered_source(
                    self.task, tool_name, (source.repo, source.path, first, last)
                )
            ),
        }
        return replacement, record

    def _span_note(
        self,
        *,
        tool_name: str,
        call_id: str,
        repo: str,
        path: str,
        first: int,
        last: int,
        omitted_intervals: Sequence[Sequence[int]],
        kept_lines: Sequence[int],
        omitted_char_count: int,
        original_chars: int,
        carrier: bool,
    ) -> str:
        """The deterministic note appended to the retained header line.

        It is a *pointer*, not a summary: path, exact omitted intervals, kept line
        numbers, omitted character count and the single tool call that recovers the
        text.  Nothing is paraphrased, so the artifact cannot lose meaning silently.
        """
        omitted = ", ".join(f"lines {start}-{end}" for start, end in omitted_intervals)
        kept = _compress_lines([int(value) for value in kept_lines])
        role = (
            "Kept verbatim: the lines carrying the registered constraint literals."
            if carrier
            else "No registered constraint literal occurs in this range."
        )
        return (
            f"{NOTE_PREFIX} {tool_name} call_id={call_id}: {repo}/{path} lines {first}-{last} "
            f"({original_chars} characters) was already returned in full earlier in this "
            f"conversation. Kept verbatim: {kept}. Omitted: {omitted} ({omitted_char_count} "
            f"characters). {role} Re-issue {tool_name} to recover the omitted lines."
        )

    def _elided_source_view(self, text: Any) -> bool:
        """True when an output text is a v6 pointer note over a retained header."""
        return self._is_placeholder(text) and isinstance(text, str) and NOTE_PREFIX in text

    # -- structural legality ----------------------------------------------

    def _structural_violation(self, before: Sequence[Any], after: Sequence[Any]) -> str:
        """v5's structural check, extended to accept a span reduction.

        v5 accepted only a whole-text note as a legal replacement, because that was
        its only reduction.  v6 replaces an output with *header + note + retained
        lines*, so the legal-replacement test is restated here: the replacement must
        be a v6 pointer note (or, defensively, a v5 note), it must be strictly
        shorter, and it must keep the same ``call_id`` and item count.  Every other
        structural rule from v5 is unchanged.
        """
        first = self._item_fingerprints(before)
        second = self._item_fingerprints(after)
        if len(first) != len(second):
            return "item_count"
        for index, (left, right) in enumerate(zip(first, second)):
            if left != right:
                return f"item_identity:{index}"
        protected = set(self.protected_unit_sha256)
        if self._unit_multiset(before, protected) != self._unit_multiset(after, protected):
            return "non_protected_units_changed"
        before_outputs = self._output_map(before)
        after_outputs = self._output_map(after)
        if sorted(before_outputs) != sorted(after_outputs):
            return "tool_output_call_ids_changed"
        for call_id, text in before_outputs.items():
            replaced = after_outputs[call_id]
            if replaced == text:
                continue
            if not self._is_placeholder(replaced):
                return f"unexpected_output_change:{call_id}"
            if len(replaced) >= len(text):
                return f"unexpected_output_change:{call_id}"
        return ""

    # -- counters ----------------------------------------------------------

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics.update(
            {
                "selective_retention_schema": MECHANISM_SCHEMA,
                "selective_retention_registry_schema": registry.REGISTRY_SCHEMA,
                "selective_retention_registry_fingerprint": registry.base_registry_fingerprint(
                    self.task
                ),
                "pointer_retention_registry_fingerprint": registry.registry_fingerprint(
                    self.task
                ),
                "pointer_retention_registry_problems": list(registry.all_problems([self.task])),
                "pointer_retention_context_lines": CONTEXT_LINES,
                "pointer_retention_elided_sources": self.elided_source_spans,
                "pointer_retention_elided_lines": self.elided_source_lines,
                "pointer_retention_kept_lines": self.kept_source_lines,
                "pointer_retention_kept_literal_lines": self.kept_literal_lines,
                "pointer_retention_span_notes_with_omitted_intervals": (
                    self.span_notes_with_omitted_intervals
                ),
                "selective_retention_protected_group_count": len(self._trusted_baseline),
                "selective_retention_group_hash_sha256": _sha(self._trusted_content_baseline),
                "pointer_retention_group_baseline_groups": len(self._trusted_baseline),
            }
        )
        return metrics


def _rejected(tool_name: str, source: registry.RegisteredSpan, reason: str) -> dict[str, Any]:
    """The record kept when an output may not be elided, with the reason."""
    return {
        "tool": tool_name,
        "source_range": [source.first_line, source.last_line],
        "omitted_intervals": [],
        "kept_lines": [],
        "kept_literal_lines": [],
        "omitted_line_count": 0,
        "kept_line_count": 0,
        "kept_literal_line_count": 0,
        "reason": reason,
    }


def _intervals(numbers: Sequence[int]) -> list[tuple[int, int]]:
    """Collapse a sorted line-number run into closed intervals."""
    ordered = sorted(int(value) for value in numbers)
    out: list[tuple[int, int]] = []
    for number in ordered:
        if out and number == out[-1][1] + 1:
            out[-1] = (out[-1][0], number)
            continue
        out.append((number, number))
    return out


def _compress_lines(numbers: Sequence[int]) -> str:
    if not numbers:
        return "none"
    parts = []
    for start, end in _intervals(numbers):
        parts.append(str(start) if start == end else f"{start}-{end}")
    return ", ".join(parts)


def note_is_pointer(note: Any) -> bool:
    """True when a tool output text is a v6 pointer note (either shape)."""
    return SpanRetentionFilter._is_placeholder(note) and isinstance(note, str) and NOTE_PREFIX in note


def span_record_sha256(record: Mapping[str, Any]) -> str:
    """Digest of one elision record (the audit recomputes it from the evidence)."""
    return hashlib.sha256(
        _canonical(
            {
                "source_range": list(record.get("source_range") or []),
                "omitted_intervals": [
                    list(value) for value in record.get("omitted_intervals") or []
                ],
                "kept_lines": list(record.get("kept_lines") or []),
                "kept_literal_lines": list(record.get("kept_literal_lines") or []),
            }
        ).encode("utf-8")
    ).hexdigest()


def output_is_pointer_note(text: Any) -> bool:
    return note_is_pointer(text)


def count_note_prefixes(items: Sequence[Any]) -> int:
    """How many tool outputs in one payload are v6 pointer notes."""
    total = 0
    for item in items:
        raw = _jsonable_item(item)
        if isinstance(raw, Mapping) and note_is_pointer(raw.get("output")):
            total += 1
    return total


__all__ = [
    "CONTEXT_LINES",
    "MECHANISM_SCHEMA",
    "NOTE_PREFIX",
    "SpanRetentionFilter",
    "count_note_prefixes",
    "note_is_pointer",
    "output_is_pointer_note",
    "registry",
    "span_record_sha256",
]
