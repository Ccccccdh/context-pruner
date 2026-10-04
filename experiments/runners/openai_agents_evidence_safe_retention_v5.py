"""v5 evidence-safe retention for the OpenAI Agents SDK input filter.

What changed from v4 (and why)
-----------------------------
v4 asked one question about a tool output: *"has this text already been
delivered verbatim to the model?"*.  If yes, it replaced the text with a short
note.  On the Django task the literal ``existing_annotations`` exists only inside
one such output (``read_django_aggregation``), so the plugin arm deleted a frozen
reference condition from the model boundary, produced a real input reduction and
scored 0/3.  The measured saving was therefore not a saving: information had been
moved out of the model's view.

v5 keeps the reduction source but makes compression eligibility consult the
**registered constraint set** first:

1. **Registered literal units are pinned.**  Before the run, every task-statement
   text unit that carries a registered literal (``filter operations``,
   ``other annotations``, ``ordering`` for Django) is registered by SHA256 of its
   normalised text and can never be dropped by the duplicate rule.
2. **A tool output that carries a registered literal is pinned verbatim.**  It is
   never replaced by an annotation, on any call, no matter how many times its
   text has already been sent.  On Django that is ``read_django_aggregation``
   (``existing_annotations``, ``ordering``) and ``read_django_count_call``
   (``filter``).  The check is driven by
   :mod:`experiments.runners.openai_agents_literal_registry_v5`, so it is a
   property of the registry rather than of "what the model happens to have seen".
3. **Deterministic annotations carry recovery pointers.**  An elided output that
   is a registered, line-numbered source view is replaced by a note that states
   its repository path and the exact line range, so the omitted text is
   recoverable by re-issuing a tool call.  A note is never emitted for a text the
   registry cannot point at; such a text is simply kept.
4. **The most recent tool group is kept in full.**  Every tool item from the first
   tool item after the last message item onwards is device-pinned, so the
   evidence the current turn is reasoning about is never elided.
5. **A literal-survival guard runs on every call.**  Each registered literal
   must occur at least as many times in the filtered payload as in the observed
   input.  Any net loss is a violation regardless of which rule caused it.
6. **Fallback is deterministic and counted.**  A missing protected unit, a lost
   literal, a changed protected tool group, a structural mismatch or a filtered
   payload above the hard budget all cause the *whole payload* of that model call
   to be sent unfiltered, counted in ``task_anchor_restore_failures`` (hard
   violations) or ``budget_fallbacks``, and never partially restored.

Reduction still happens, which is what v3 lost: an output that is **not** a
constraint carrier, is **not** in the most recent tool group, is already present
verbatim in the observed input and has a parseable source pointer is replaced by
a note that states its path and line range.  On Django that is
``read_django_count_entry`` and, on the later call, ``read_django_count_call``.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from typing import Any, Callable, Mapping, Sequence

from context_pruner.adapters.openai_agents import (
    _is_message_item,
    _item_type,
    _jsonable_item,
    _protect_responses_groups,
)
from context_pruner.types import estimate_tokens

from experiments.runners import openai_agents_literal_registry_v5 as registry

#: Schema version of this mechanism's recorded counters.
MECHANISM_SCHEMA = "openai_agents_evidence_safe_retention_v5"

#: A unit shorter than this carries too little text to reason about; it is never
#: dropped and never registered as a protected unit.
MIN_UNIT_CHARS = 20
#: Tool output text below this size is not worth a compaction note.
MIN_COMPACTABLE_OUTPUT_CHARS = 400
#: Default byte ceiling for one filtered payload.  The runner derives this from
#: the arm's hard token budget; the constant keeps the mechanism usable on its
#: own in the offline gate.
DEFAULT_HARD_LIMIT_BYTES = 24_000
NOTE_PREFIX = "[evidence-safe-retention v5]"
GROUP_HASH_LIMIT = 512
#: Labels under which a fallback is recorded.
HARD_FALLBACK_REASONS = (
    "protected_item_dropped",
    "missing_protected_unit",
    "missing_registered_literal_unit",
    "registered_literal_lost",
    "protected_tool_group_changed",
)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def normalise_unit(text: str) -> str:
    """Normalise one unit for protected-text comparison (never for content)."""
    return " ".join(str(text).split())


def split_units(text: str) -> list[str]:
    """Split text into the units this filter reasons about.

    A unit is one non-empty stripped line.  Blank lines and indentation carry no
    information on this host and are not units; multi-line code blocks stay
    line-by-line so a dropped line can never delete a different sentence.
    """
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def _split_message_content(item: Any) -> list[str]:
    if not isinstance(item, Mapping):
        return []
    content = item.get("content")
    if not isinstance(content, str):
        return []
    return split_units(content)


def _rebuild_message(item: Any, content: str) -> Any:
    rebuilt: Any = dict(item) if isinstance(item, Mapping) else item
    if isinstance(rebuilt, dict):
        rebuilt["content"] = content
    return rebuilt


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(NOTE_PREFIX)


def tool_name_by_call_id(items: Sequence[Any]) -> dict[str, str]:
    """``call_id -> tool name`` for every ``*_call`` item in one payload."""
    mapping: dict[str, str] = {}
    for item in items:
        raw = _jsonable_item(item)
        if isinstance(raw, Mapping) and str(_item_type(item)).endswith("_call"):
            call_id = str(raw.get("call_id") or "")
            if call_id:
                mapping[call_id] = str(raw.get("name") or "tool")
    return mapping


def recent_tool_group_indices(items: Sequence[Any]) -> set[int]:
    """Indices of the most recent tool group: the newest call/output pair.

    Tool items follow the conversation messages.  A *group* is one
    ``function_call`` together with the ``function_call_output`` that answers it,
    in payload order.  v5 keeps the **newest** group in full, so the evidence the
    current turn is reasoning about is never elided; earlier groups stay
    eligible, because their text was delivered verbatim in the turn that produced
    it - and, if a group's text carries a registered literal, it is pinned anyway
    by the registry rule.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    for index, item in enumerate(items):
        if _is_message_item(item):
            if current:
                groups.append(current)
                current = []
            continue
        current.append(index)
        if str(_item_type(item)).endswith("_output"):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return set(groups[-1]) if groups else set()


def _group_fingerprint(item: Any) -> dict[str, Any]:
    """Fields of a Responses item that a protected tool group must keep intact.

    The output *text* is excluded on purpose: it is the one field the mechanism
    may replace with a note, and only for a text that is neither a constraint
    carrier nor part of the most recent tool group.  The evidence layer records
    each output text's own SHA256 separately.
    """
    raw = _jsonable_item(item)
    if not isinstance(raw, Mapping):
        return {"opaque": str(raw)}
    return {
        "type": str(raw.get("type") or ""),
        "call_id": str(raw.get("call_id") or ""),
        "name": str(raw.get("name") or ""),
        "arguments": str(raw.get("arguments") or ""),
        "role": str(raw.get("role") or ""),
        "id": str(raw.get("id") or ""),
    }


class SelectiveRetentionFilter:
    """Evidence-safe retention filter installed inside the shared trigger gate."""

    def __init__(
        self,
        *,
        task: str,
        task_statement: str,
        constraint_phrases: Mapping[str, Sequence[str]] | None = None,
        token_counter: Callable[[str], int] | None = None,
        hard_limit_bytes: int = DEFAULT_HARD_LIMIT_BYTES,
    ) -> None:
        self.task = str(task)
        self.token_counter = token_counter or estimate_tokens
        self.hard_limit_bytes = int(hard_limit_bytes)
        if constraint_phrases is None:
            self.constraint_phrases = {
                label: tuple(str(value) for value in spec["literals"])
                for label, spec in registry.CONSTRAINTS.get(self.task, {}).items()
            }
        else:
            self.constraint_phrases = {
                str(label): tuple(str(phrase) for phrase in phrases)
                for label, phrases in dict(constraint_phrases).items()
            }
        statements = split_units(task_statement)
        self.task_units = [unit for unit in statements if len(unit) >= MIN_UNIT_CHARS]
        self.task_unit_sha256 = [_text_sha(normalise_unit(unit)) for unit in self.task_units]
        #: Task-statement units carrying a registered literal (frozen by the
        #: registry, not by "what the model has seen").
        self.literal_units = [
            unit
            for unit in self.task_units
            if any(
                str(fragment).lower() in unit.lower()
                for fragments in self._unit_fragments().values()
                for fragment in fragments
            )
        ]
        self.literal_unit_sha256 = sorted(
            {_text_sha(normalise_unit(unit)) for unit in self.literal_units}
        )
        self.constraint_units = self.literal_units
        self.constraint_unit_sha256 = list(self.literal_unit_sha256)
        self.protected_unit_sha256 = sorted(
            set(self.task_unit_sha256) | set(self.literal_unit_sha256)
        )
        #: Every registered literal string; a payload must never lose an
        #: occurrence of one of these relative to the observed input.
        self.literal_texts = sorted(
            {phrase for phrases in self.constraint_phrases.values() for phrase in phrases},
            key=lambda value: (-len(value), value),
        )
        self.literal_text_count = len(self.literal_texts)
        self.registry_fingerprint = registry.registry_fingerprint(self.task)
        self.registry_problems = registry.verify(self.task)
        # -- run state -----------------------------------------------------
        self.calls = 0
        self.compacted_calls = 0
        self.duplicate_units_dropped = 0
        self.compacted_tool_outputs = 0
        self.saved_bytes_total = 0
        self.last_saved_bytes = 0
        self.task_anchor_restore_failures = 0
        self.task_restore_fallbacks = 0
        self.budget_fallbacks = 0
        self.constraint_guard_fallbacks = 0
        #: Most recent per-call decision, for the evidence layer and the audit.
        self.last_call_decision: dict[str, Any] = {}
        self.group_hash_mismatch_count = 0
        self.last_fallback_reason = ""
        self.failure_reasons: Counter[str] = Counter()
        #: Output texts (normalised) pinned verbatim on this call, by reason.
        self.pinned_output_shas: list[str] = []
        self.pinned_by_reason: Counter[str] = Counter()
        self.elided_output_shas: list[str] = []
        self.source_pointer_notes = 0
        self.recent_group_items_kept = 0
        #: group_id -> ordered group hash, kept stable across the whole run.
        self._group_hash_baseline: dict[str, str] = {}

    # -- SDK hook ---------------------------------------------------------

    def __call__(self, data: Any) -> Any:
        from agents.run import ModelInputData

        model_data = data.model_data
        items = list(model_data.input or [])
        instructions = model_data.instructions
        outcome = self.filter_items(items)
        if outcome is None:
            return model_data
        return ModelInputData(input=outcome, instructions=instructions)

    # -- core -------------------------------------------------------------

    def observe(self, items: Sequence[Any]) -> None:
        """Remember one exact model input without changing it.

        Called for every model input, including the calls the shared trigger gate
        passes through untouched, and for every fallback (the payload actually
        sent).  That memory is what makes "already delivered verbatim" a checkable
        property instead of an assumption - and it is deliberately *not*
        sufficient on its own: eligibility additionally consults the registry, so
        "the model has seen this" can never license removing a registered
        literal.
        """
        self.observe_delivered(items)

    def filter_items(self, items: Sequence[Any]) -> list[Any] | None:
        """Return the filtered item list, or ``None`` when the call falls back.

        ``None`` means "send the observed input unchanged"; every fallback is
        counted and reasoned about.  The filter never returns a payload whose
        protected units or registered literals are missing, whose tool groups
        changed, or which is larger than the observed input.
        """
        self.calls += 1
        self.last_saved_bytes = 0
        self.last_fallback_reason = ""
        self.pinned_output_shas = []
        self.elided_output_shas = []
        self._elided_pointer_list: list[dict[str, Any]] = []
        before_bytes = len(_canonical([_jsonable_item(item) for item in items]).encode("utf-8"))
        recent = recent_tool_group_indices(items)
        self.recent_group_items_kept = len(recent)
        filtered, dropped_duplicates, compacted, decision = self._filter(items, recent)
        self.last_call_decision = dict(decision)
        if dropped_duplicates == 0 and compacted == 0:
            # Nothing provably safe to remove on this call: send the payload
            # unchanged and let the trigger gate's own measurement stay honest.
            self.observe(items)
            return None
        # The strongest invariant is checked first: a registered literal must
        # never lose an occurrence, whatever caused it.  It is deliberately
        # evaluated before the structural check so a literal loss can never be
        # reported as a mere structural mismatch.
        loss = self._carrier_loss(items, filtered)
        if loss:
            self.constraint_guard_fallbacks += 1
            return self._fallback(items, f"registered_literal_lost:{','.join(loss[:4])}", hard=True)
        if self._missing_literal_units(filtered):
            return self._fallback(items, "missing_registered_literal_unit", hard=True)
        structural = self._structural_violation(items, filtered)
        if structural:
            return self._fallback(items, f"protected_item_dropped:{structural}", hard=True)
        if self._missing_protected_units(filtered):
            return self._fallback(items, "missing_protected_unit", hard=True)
        group_change = self._group_change(items, filtered)
        if group_change:
            return self._fallback(items, f"protected_tool_group_changed:{group_change}", hard=True)
        after_bytes = len(_canonical([_jsonable_item(item) for item in filtered]).encode("utf-8"))
        if after_bytes > self.hard_limit_bytes:
            # Pinning the constraint carriers can push the payload above the hard
            # budget.  The constraint-bearing unit is never dropped; the whole
            # payload for this call is sent unfiltered instead, and counted.
            self.budget_fallbacks += 1
            self.failure_reasons["hard_budget_exceeded"] += 1
            self.last_fallback_reason = "hard_budget_exceeded"
            self.last_call_decision["fallback"] = "hard_budget_exceeded"
            self.observe(items)
            return None
        if after_bytes >= before_bytes:
            self.budget_fallbacks += 1
            self.failure_reasons["no_measured_reduction"] += 1
            self.last_fallback_reason = "no_measured_reduction"
            self.last_call_decision["fallback"] = "no_measured_reduction"
            self.observe(items)
            return None
        self.compacted_calls += 1
        self.duplicate_units_dropped += dropped_duplicates
        self.compacted_tool_outputs += compacted
        self.last_saved_bytes = before_bytes - after_bytes
        self.saved_bytes_total += self.last_saved_bytes
        self.observe(items)
        return filtered

    def metrics_dict(self) -> dict[str, Any]:
        decisions = self.last_call_decision
        return {
            "selective_retention_schema": MECHANISM_SCHEMA,
            "selective_retention_registry_schema": registry.REGISTRY_SCHEMA,
            "selective_retention_registry_fingerprint": self.registry_fingerprint,
            "selective_retention_registry_problems": list(self.registry_problems),
            "selective_retention_filter_calls": self.calls,
            "selective_retention_compacted_calls": self.compacted_calls,
            "selective_retention_duplicate_units_dropped": self.duplicate_units_dropped,
            "selective_retention_compacted_tool_outputs": self.compacted_tool_outputs,
            "selective_retention_saved_bytes_total": self.saved_bytes_total,
            "selective_retention_last_saved_bytes": self.last_saved_bytes,
            "selective_retention_task_unit_count": len(self.task_units),
            "selective_retention_literal_unit_count": len(self.literal_units),
            "selective_retention_literal_text_count": self.literal_text_count,
            "selective_retention_constraint_unit_count": len(self.constraint_units),
            "selective_retention_protected_unit_count": len(self.protected_unit_sha256),
            "selective_retention_protected_group_count": len(self._group_hash_baseline),
            "selective_retention_group_hash_sha256": _sha(self._group_hash_baseline),
            "selective_retention_recent_group_items_kept": int(
                decisions.get("recent_group_items", self.recent_group_items_kept)
            ),
            "selective_retention_recent_group_kept_in_full": bool(
                decisions.get("recent_group_kept_in_full", False)
            ),
            "selective_retention_pinned_outputs": int(decisions.get("pinned_outputs", 0)),
            "selective_retention_pinned_by_reason": dict(
                decisions.get("pinned_by_reason", {})
            ),
            "selective_retention_elided_outputs": int(decisions.get("elided_outputs", 0)),
            "selective_retention_notes_with_source_pointer": int(
                decisions.get("notes_with_source_pointer", 0)
            ),
            "selective_retention_pointer_coverage": bool(
                decisions.get("pointer_coverage", False)
            ),
            "selective_retention_literal_guard_fallbacks": self.constraint_guard_fallbacks,
            "task_anchor_restore_failures": self.task_anchor_restore_failures,
            "task_restore_fallbacks": self.task_restore_fallbacks,
            "budget_fallbacks": self.budget_fallbacks,
            "last_fallback_reason": self.last_fallback_reason,
            "fallback_reasons": dict(self.failure_reasons),
        }

    # -- classification (also used by the evidence layer) ------------------

    def classify_outputs(self, items: Sequence[Any]) -> dict[str, Any]:
        """Describe every output item in one payload, content-free.

        Returns, in item order, a list of records with the item's ``call_id``,
        its text SHA256, its byte and line counts, the registered literals it
        carries, whether it is pinned, why, and whether a recovery pointer was
        parseable.  Only digests, counts and booleans leave this method, so the
        evidence file stays content-free while the eligibility decision stays
        verifiable from the evidence alone.
        """
        recent = recent_tool_group_indices(items)
        names = tool_name_by_call_id(items)
        records: list[dict[str, Any]] = []
        for index, item in enumerate(items):
            if not str(_item_type(item)).endswith("_output"):
                continue
            raw = _jsonable_item(item)
            text = raw.get("output") if isinstance(raw, Mapping) else None
            call_id = str(raw.get("call_id") or "") if isinstance(raw, Mapping) else ""
            labels = registry.labels_in_text(self.task, text or "")
            pointer = registry.parse_source_pointer(text or "")
            source = registry.source_for_tool(self.task, names.get(call_id, ""))
            reasons: list[str] = []
            if labels:
                reasons.append("registered_literal")
            if index in recent:
                reasons.append("most_recent_tool_group")
            if not isinstance(text, str):
                reasons.append("non_text_output")
            records.append(
                {
                    "call_id": call_id,
                    "tool": names.get(call_id, ""),
                    "output_sha256": _text_sha(text if isinstance(text, str) else ""),
                    "output_chars": len(text) if isinstance(text, str) else 0,
                    "output_lines": len(split_units(text)) if isinstance(text, str) else 0,
                    "registered_literals": list(labels),
                    "pinned": bool(reasons),
                    "pinned_reasons": reasons,
                    "recovery_pointer": list(pointer) if pointer else None,
                    "registered_source_pointer": (
                        [source.repo, source.path, source.first_line, source.last_line]
                        if source is not None
                        else None
                    ),
                }
            )
        return {
            "outputs": records,
            "recent_group_items": len(recent),
            "pinned_outputs": sum(1 for record in records if record["pinned"]),
            "pinned_by_reason": dict(
                Counter(reason for record in records for reason in record["pinned_reasons"])
            ),
        }

    # -- internals --------------------------------------------------------

    def _unit_fragments(self) -> dict[str, tuple[str, ...]]:
        return {
            label: tuple(str(value) for value in spec["units"])
            for label, spec in registry.CONSTRAINTS.get(self.task, {}).items()
        }

    def _fallback(self, items: Sequence[Any], reason: str, *, hard: bool) -> None:
        if hard:
            self.task_anchor_restore_failures += 1
            self.task_restore_fallbacks += 1
        self.failure_reasons[reason] += 1
        self.last_fallback_reason = reason
        self.last_call_decision["fallback"] = reason
        self.last_call_decision["fallback_is_whole_prefix"] = True
        self.observe(items)
        return None

    def _filter(
        self, items: Sequence[Any], recent: set[int]
    ) -> tuple[list[Any], int, int, dict[str, Any]]:
        """Return ``(filtered_items, dropped_duplicates, compacted, decision)``."""
        protected = set(self.protected_unit_sha256)
        occurrences: Counter[str] = Counter()
        for item in items:
            for unit in _split_message_content(item):
                if len(unit) >= MIN_UNIT_CHARS:
                    occurrences[_text_sha(normalise_unit(unit))] += 1
        names = tool_name_by_call_id(items)
        dropped_duplicates = 0
        compacted = 0
        emitted: set[str] = set()
        filtered: list[Any] = []
        pinned_by_reason: Counter[str] = Counter()
        elided: list[str] = []
        pointer_notes = 0
        for index, item in enumerate(items):
            if _is_message_item(item):
                lines = _split_message_content(item)
                if not lines:
                    filtered.append(item)
                    continue
                kept: list[str] = []
                for unit in lines:
                    digest = _text_sha(normalise_unit(unit))
                    if len(unit) < MIN_UNIT_CHARS or digest in protected:
                        kept.append(unit)
                        continue
                    if digest in emitted or digest in self._verbatim_units():
                        # A later exact copy, or a whole unit already delivered
                        # verbatim in an earlier call of this run.  Only the
                        # canonical copy survives, and only for registered
                        # non-constraint units.
                        dropped_duplicates += 1
                        continue
                    if occurrences[digest] > 1:
                        emitted.add(digest)
                    kept.append(unit)
                filtered.append(_rebuild_message(item, "\n".join(kept)))
                continue
            note, reason = self._output_decision(item, index, recent, names)
            if note is None:
                filtered.append(item)
                if reason:
                    pinned_by_reason[reason] += 1
                    self.pinned_output_shas.append(
                        _text_sha(str(_jsonable_item(item).get("output", "")))
                    )
                continue
            filtered.append(note)
            compacted += 1
            elided.append(_text_sha(str(_jsonable_item(item).get("output", ""))))
            self.elided_output_shas.append(elided[-1])
            if _is_placeholder(note.get("output")):
                pointer_notes += 1
                raw_note = _jsonable_item(note)
                note_text = str(raw_note.get("output") or "")
                tool_name = names.get(str(raw_note.get("call_id") or ""), "")
                claim = registry.parse_pointer_claim(note_text)
                self._elided_pointers().append(
                    {
                        "call_id": str(raw_note.get("call_id") or ""),
                        "tool": tool_name,
                        "output_sha256": elided[-1],
                        "source_pointer": list(claim) if claim else None,
                        "source_pointer_sha256": _sha(claim) if claim else "",
                        "pointer_matches_registered_source": registry.pointer_matches_registered_source(
                            self.task, tool_name, claim
                        ),
                    }
                )
        decision = {
            "call": self.calls,
            "recent_group_items": len(recent),
            "recent_group_kept_in_full": True,
            "pinned_outputs": sum(pinned_by_reason.values()),
            "pinned_by_reason": dict(pinned_by_reason),
            "elided_outputs": len(elided),
            "elided_output_sha256": list(elided),
            "elided_output_pointers": list(self._elided_pointers()),
            "notes_with_source_pointer": pointer_notes,
            "pointer_coverage": all(
                bool(entry.get("source_pointer_sha256")) for entry in self._elided_pointers()
            )
            and len(self._elided_pointers()) == len(elided),
            "registered_literal_count": len(self.literal_texts),
        }
        self.pinned_by_reason.update(pinned_by_reason)
        return filtered, dropped_duplicates, compacted, decision

    def _output_decision(
        self,
        item: Any,
        index: int,
        recent: set[int],
        names: Mapping[str, str],
    ) -> tuple[Any | None, str]:
        """Return ``(note_item_or_None, pin_reason)`` for one output item.

        ``None`` means "keep this item unchanged"; the second element is the
        reason it was pinned, empty when the item needed no protection.  The
        checks run in this order, and the order is the v5 fix:

        1. does the text carry a **registered literal constraint**?  then pin it
           verbatim, whatever the model has already been shown;
        2. is it part of the **most recent tool group**?  then pin it verbatim;
        3. is it too small to be worth a note?  then keep it;
        4. has this exact text **already been delivered** in this run?  otherwise
           it must be delivered in full at least once;
        5. can the text be **pointed at by path and line range**?  otherwise keep
           it, because an annotation could not make it recoverable;
        6. only then replace the text with a deterministic note.
        """
        if not str(_item_type(item)).endswith("_output"):
            return None, ""
        raw = _jsonable_item(item)
        if not isinstance(raw, Mapping):
            return None, "non_mapping_item"
        text = raw.get("output")
        if not isinstance(text, str):
            return None, "non_text_output"
        labels = registry.labels_in_text(self.task, text)
        if labels:
            return None, "registered_literal"
        if index in recent:
            return None, "most_recent_tool_group"
        if len(text) < MIN_COMPACTABLE_OUTPUT_CHARS:
            return None, "below_min_compactable_size"
        if _text_sha(text) not in self._delivered_text_sha():
            # The model has not been shown this exact text yet in this run; it
            # must be delivered in full at least once.
            return None, "not_yet_delivered"
        call_id = str(raw.get("call_id") or "")
        tool_name = names.get(call_id, "tool")
        pointer = registry.parse_source_pointer(text)
        if pointer is None:
            # No recoverable pointer: v5 does not elide it, because an
            # annotation without a pointer would omit text the model cannot
            # recover (the second half of the v4 defect).
            return None, "no_recovery_pointer"
        repo, path, first, last = pointer
        note_text = self._pointer_note(tool_name, call_id, repo, path, first, last, text)
        if len(note_text) >= len(text):
            return None, "note_not_shorter"
        compacted_item = dict(raw)
        compacted_item["output"] = note_text
        return compacted_item, ""

    @staticmethod
    def _pointer_note(
        tool_name: str,
        call_id: str,
        repo: str,
        path: str,
        first: int,
        last: int,
        text: str,
    ) -> str:
        return (
            f"{NOTE_PREFIX} {tool_name} call_id={call_id}: the text of {repo}/{path} "
            f"lines {first}-{last} ({len(text)} characters, {len(split_units(text))} lines) "
            "was already returned in full earlier in this conversation. It is not "
            f"re-transmitted; re-issue {tool_name} to recover it."
        )

    def _verbatim_units(self) -> set[str]:
        units = getattr(self, "_verbatim_unit_set", None)
        if units is None:
            units = set()
            self._verbatim_unit_set = units
        return units

    def _elided_pointers(self) -> list[dict[str, Any]]:
        """Per elided output, the call id and the pointer its note must state."""
        pointers = getattr(self, "_elided_pointer_list", None)
        if pointers is None:
            pointers = []
            self._elided_pointer_list = pointers
        return pointers

    def _delivered_text_sha(self) -> set[str]:
        """SHA256 of every tool-output text the model has been shown in this run.

        A run-level, monotonically growing set: the model may still recall a text
        that a later call elided, and a per-call view would let an elision erase
        the memory that justified it.
        """
        delivered = getattr(self, "_delivered_texts", None)
        if delivered is None:
            delivered = set()
            self._delivered_texts = delivered
        return delivered

    def observe_delivered(self, items: Sequence[Any]) -> None:
        """Record the output texts the model has actually been shown."""
        for item in items:
            raw = _jsonable_item(item)
            if not isinstance(raw, Mapping):
                continue
            output = raw.get("output")
            if isinstance(output, str):
                self._delivered_text_sha().add(_text_sha(output))
            content = raw.get("content")
            if isinstance(content, str):
                for unit in split_units(content):
                    if len(unit) >= MIN_UNIT_CHARS:
                        self._verbatim_units().add(_text_sha(normalise_unit(unit)))

    def _group_hashes(self, items: Sequence[Any]) -> dict[str, str]:
        """Group hashes for the evidence layer and the independent audit.

        A group hash covers only the fields this filter must never rewrite
        (``type``, ``call_id``, ``name``, ``arguments``, ``role``); the output
        *text* of a non-constraint, non-recent output is the one field the
        mechanism may replace with a recovery note.
        """
        _, groups = _protect_responses_groups(items, self.token_counter)
        hashes = {
            str(group.group_id): _sha([_group_fingerprint(entry) for entry in group.items])
            for group in groups[:GROUP_HASH_LIMIT]
        }
        for group_id, digest in hashes.items():
            self._group_hash_baseline.setdefault(group_id, digest)
        return hashes

    def _item_fingerprints(self, items: Sequence[Any]) -> list[str]:
        fingerprints: list[str] = []
        for item in items:
            raw = _jsonable_item(item)
            if isinstance(raw, Mapping):
                fingerprints.append(
                    _canonical(
                        {
                            "type": str(raw.get("type") or ""),
                            "role": str(raw.get("role") or ""),
                            "call_id": str(raw.get("call_id") or ""),
                            "name": str(raw.get("name") or ""),
                        }
                    )
                )
            else:  # pragma: no cover - non-mapping items are rare on this host
                fingerprints.append(_canonical({"opaque": str(raw)}))
        return fingerprints

    def _structural_violation(self, before: Sequence[Any], after: Sequence[Any]) -> str:
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
            if not _is_placeholder(replaced) or len(replaced) >= len(text):
                return f"unexpected_output_change:{call_id}"
        return ""

    @staticmethod
    def _unit_multiset(items: Sequence[Any], protected: set[str]) -> list[str]:
        units: list[str] = []
        for item in items:
            for unit in _split_message_content(item):
                if len(unit) >= MIN_UNIT_CHARS and _text_sha(normalise_unit(unit)) not in protected:
                    units.append(_text_sha(normalise_unit(unit)))
        return sorted(units)

    @staticmethod
    def _output_map(items: Sequence[Any]) -> dict[str, str]:
        outputs: dict[str, str] = {}
        for index, item in enumerate(items):
            raw = _jsonable_item(item)
            if not isinstance(raw, Mapping) or not str(_item_type(item)).endswith("_output"):
                continue
            call_id = str(raw.get("call_id") or raw.get("id") or f"#{index}")
            text = raw.get("output")
            outputs[call_id] = text if isinstance(text, str) else ""
        return outputs

    def _group_change(self, before: Sequence[Any], after: Sequence[Any]) -> str:
        first = self._group_hashes(before)
        second = self._group_hashes(after)
        mismatch = sorted(
            group_id
            for group_id, digest in first.items()
            if self._group_hash_baseline.get(group_id, digest) != digest
        )
        if mismatch:
            self.group_hash_mismatch_count += 1
            return "baseline:" + ",".join(mismatch[:8])
        if first == second:
            return ""
        changed = sorted(key for key in set(first) | set(second) if first.get(key) != second.get(key))
        return ",".join(changed[:8]) or "unknown"

    def _missing_protected_units(self, filtered: Sequence[Any]) -> list[str]:
        present = self._present_unit_digests(filtered)
        return [digest for digest in self.protected_unit_sha256 if digest not in present]

    def _missing_literal_units(self, filtered: Sequence[Any]) -> list[str]:
        present = self._present_unit_digests(filtered)
        return [digest for digest in self.literal_unit_sha256 if digest not in present]

    def _present_unit_digests(self, items: Sequence[Any]) -> set[str]:
        present: set[str] = set()
        for item in items:
            for unit in _split_message_content(item):
                present.add(_text_sha(normalise_unit(unit)))
            raw = _jsonable_item(item)
            if isinstance(raw, Mapping):
                output = raw.get("output")
                if isinstance(output, str):
                    present.add(_text_sha(normalise_unit(output)))
        return present

    def _carrier_loss(self, before: Sequence[Any], after: Sequence[Any]) -> list[str]:
        """Registered literals the filtered payload may not be missing.

        Two things are checked, and both matter:

        * **Attribution.**  For every registered constraint-carrier tool, if the
          *observed* input carries that tool's registered literals then the
          *filtered* payload must carry them too.  This is what stops a
          literal-carrying output from being replaced by an annotation that no
          longer states the literal - the exact v4 failure.
        * **Occurrence count.**  No registered literal may lose an occurrence.
          This stops subtler damage such as truncating an output or rewriting
          text in a way that silently drops one of several occurrences.
        """
        before_text = _canonical([_jsonable_item(item) for item in before]).lower()
        after_text = _canonical([_jsonable_item(item) for item in after]).lower()
        losses: list[str] = []
        for source in registry.SOURCES.get(self.task, ()):
            if not source.literal_labels:
                continue
            expected = [
                literal
                for label in source.literal_labels
                for literal in self.constraint_phrases.get(label, ())
            ]
            if not any(literal.lower() in before_text for literal in expected):
                continue
            for literal in expected:
                if literal.lower() not in after_text:
                    losses.append(f"{source.tool}:{literal}")
        for literal in self.literal_texts:
            if before_text.count(literal.lower()) > after_text.count(literal.lower()):
                if literal not in losses:
                    losses.append(literal)
        return losses

    def literal_counts(self, items: Sequence[Any]) -> dict[str, int]:
        """Occurrence count of every registered literal in one payload."""
        text = _canonical([_jsonable_item(item) for item in items]).lower()
        return {literal: text.count(literal.lower()) for literal in self.literal_texts}


def _text_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def constraint_presence(task: str, texts: Sequence[str]) -> dict[str, bool]:
    """Boolean literal-constraint presence for one payload (no text persisted)."""
    return registry.constraint_presence(task, "\n".join(str(text) for text in texts))


class GateAwareSelectiveRetention:
    """Install evidence-safe retention *around* the shared trigger gate.

    The gate decides whether an arm acts at all (identical rule for ``pruner_v1``
    and ``native_summary``), but that also means it hides the calls it passes
    through.  Retention needs those calls: "already delivered verbatim" is only
    knowable if every model input is observed.  This wrapper therefore sits
    outside the gate, observes and remembers each exact model input, and delegates
    the filtering decision to the gate.
    """

    def __init__(self, gate: Any, retention: SelectiveRetentionFilter) -> None:
        self.gate = gate
        self.retention = retention
        #: Optional boundary observers: ``(items, instructions) -> index`` before
        #: the gate runs and ``(items, instructions, index)`` after it returns.
        self.observer_before: Any = None
        self.observer_after: Any = None

    async def __call__(self, data: Any) -> Any:
        import inspect

        model_data = data.model_data
        items = list(model_data.input or [])
        instructions = model_data.instructions
        self.retention.observe(items)
        self.retention.observe_delivered(items)
        index = -1
        if self.observer_before is not None:
            index = self.observer_before(items, instructions)
        result = self.gate(data)
        if inspect.isawaitable(result):
            result = await result
        if self.observer_after is not None:
            outputs = list(getattr(result, "input", None) or items)
            self.observer_after(outputs, instructions, index)
        return result

    def metrics_dict(self) -> dict[str, Any]:
        metrics = dict(self.gate.metrics_dict()) if hasattr(self.gate, "metrics_dict") else {}
        metrics.update(self.retention.metrics_dict())
        return metrics
