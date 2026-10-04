"""v4 selective retention for the OpenAI Agents SDK input filter.

Mechanism
---------
v3 pinned the first two user messages verbatim.  That repaired the literal loss
measured in v2 (the Django issue statement reached the model again) but it also
removed the *only* thing the v2 plugin arm had been compressing, so the plugin
arm produced no input saving at all.

v4 keeps the information invariant of v3 and looks for reduction somewhere that
cannot damage the task statement:

  * **Protected task units.**  The registered task statement text (the public
    issue statement and the fixed request/contract lines) is split into units.
    Every unit carries a SHA256 of its normalised text.  After filtering, *all*
    of them must still be present in the payload that goes to the provider,
    otherwise the filter returns the untouched input and counts a restore
    failure.  A restore failure is therefore a whole-payload fallback for that
    model call: no partially damaged task statement is ever sent.
  * **Constraint units.**  Units containing a registered literal reference
    condition (``filter``, ``other annotations``, ``ordering``,
    ``existing_annotations`` for the Django scenario) are protected the same
    way.  The reported count separates task-statement units from
    constraint-bearing units, so a reader can tell how much protection was
    actually registered.
  * **Protected Responses tool groups.**  ``function_call`` /
    ``function_call_output`` items are never removed, never reordered and never
    merged; the group hashes computed by
    ``context_pruner.adapters.openai_agents._protect_responses_groups`` must be
    identical before and after every filtered call.
  * **Reduction 1 - exact-duplicate units.**  A message unit whose normalised
    text is already present elsewhere in the same payload, or was already sent
    verbatim in an earlier model call of this run, is dropped.  Only the later
    copy is dropped, so one canonical occurrence always survives.
  * **Reduction 2 - already-served tool output text.**  A ``*_output`` item whose
    text was already sent **verbatim in an earlier model call of the same run**
    is replaced by a short deterministic note that keeps the item, its
    ``call_id`` and therefore the call/output pairing intact.  The model keeps
    the content it was shown when the tool ran; the host does not pay to
    re-transmit the same public source text on every later turn.

Everything is deterministic: no model, no randomness, and no text is rewritten
or summarised.  Text is either kept verbatim or replaced by a note that states
exactly what was replaced.

Boundaries (stated in the protocol as well)
-------------------------------------------
* Only the ``pruner_v1`` arm installs this filter.  ``none`` states the literal
  no-filter baseline and ``native_summary`` stays the host-native summariser.
* A compaction note is emitted only when it is strictly shorter than the text it
  replaces, and the whole call is discarded (fallback) when the resulting
  payload is not strictly smaller than the observed input.
* A restore failure means the **whole payload** for that model call is sent
  unfiltered (``task_restore_fallbacks``); it is never a partial restore.
* Nothing here evaluates the model or grades quality; that stays with the frozen
  answer contract and the independent audit.
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

#: Schema version for the constraint registry below.  A new schema means the
#: recorded public constraint literals changed and results are not comparable.
CONSTRAINT_SCHEMA = "openai_agents_task_constraints_v4"

#: Literal reference conditions per public task.  These are public strings from
#: the task statement itself; they are used only as local search terms, and only
#: booleans plus SHA256 digests are ever persisted.
CONSTRAINTS: dict[str, dict[str, tuple[str, ...]]] = {
    "pytest_mro": {
        "own_mark_rule": ("__dict__",),
        "mro_rule": ("MRO",),
    },
    "pylint_regex_csv": {
        "split_location": ("_splitstrip",),
        "quantifier_boundary": ("quantifier", "{1,3}"),
    },
    "django_count_annotations": {
        "filter_references": ("filter",),
        "other_annotation_references": ("other annotations",),
        "ordering_references": ("ordering",),
        "aggregation_decision": ("existing_annotations",),
    },
}

#: A unit shorter than this carries too little text to reason about; it is never
#: dropped and never registered as a protected unit.
MIN_UNIT_CHARS = 20
#: Tool output text below this size is not worth a compaction note.
MIN_COMPACTABLE_OUTPUT_CHARS = 400
PLACEHOLDER_PREFIX = "[selective-retention v4]"
GROUP_HASH_LIMIT = 512


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


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


def _contains_phrase(text: str, phrases: Sequence[str]) -> bool:
    lowered = str(text).lower()
    return any(str(phrase).lower() in lowered for phrase in phrases)


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
    return isinstance(value, str) and value.startswith(PLACEHOLDER_PREFIX)


def _group_fingerprint(item: Any) -> dict[str, Any]:
    """Fields of a Responses item that a protected tool group must keep intact.

    The output *text* is excluded on purpose: it is the one field the mechanism
    may replace with a note, and only for a call that was already served in full.
    The evidence layer records the text's own SHA256 separately.
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
    """Filter installed inside the shared trigger gate for the plugin arm."""

    def __init__(
        self,
        *,
        task: str,
        task_statement: str,
        constraint_phrases: Mapping[str, Sequence[str]] | None = None,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.task = str(task)
        self.constraint_phrases = {
            str(label): tuple(str(phrase) for phrase in phrases)
            for label, phrases in dict(
                constraint_phrases
                if constraint_phrases is not None
                else CONSTRAINTS.get(self.task, {})
            ).items()
        }
        self.token_counter = token_counter or estimate_tokens
        #: Every unit of the registered task statement must survive filtering.
        self.task_units = [
            unit for unit in split_units(task_statement) if len(unit) >= MIN_UNIT_CHARS
        ]
        #: Units of the task statement that additionally carry a registered literal.
        self.constraint_units = [
            unit
            for unit in self.task_units
            if any(_contains_phrase(unit, phrases) for phrases in self.constraint_phrases.values())
        ]
        self.task_unit_sha256 = [_text_sha(normalise_unit(unit)) for unit in self.task_units]
        self.constraint_unit_sha256 = [
            _text_sha(normalise_unit(unit)) for unit in self.constraint_units
        ]
        self.protected_unit_sha256 = sorted(
            set(self.task_unit_sha256) | set(self.constraint_unit_sha256)
        )
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
        #: Times a protected tool group's group hash changed for an unfiltered call.
        self.group_hash_mismatch_count = 0
        self.last_fallback_reason = ""
        self.failure_reasons: Counter[str] = Counter()
        #: Text already sent verbatim in an earlier model call of this run.
        self._verbatim_text: set[str] = set()
        self._compacted_text_sha256: list[str] = []
        #: SHA256 of every compaction note this run has emitted.
        self._compacted_text_set: set[str] = set()
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
        passes through untouched; that memory is what makes "already served
        verbatim" a checkable property instead of an assumption.
        """
        self._remember_verbatim(items)

    def filter_items(self, items: Sequence[Any]) -> list[Any] | None:
        """Return the filtered item list, or ``None`` when the call falls back.

        ``None`` means "send the observed input unchanged"; every fallback is
        counted and reasoned about.  The filter never returns a payload whose
        protected units are missing or whose tool groups changed.
        """
        self.calls += 1
        self.last_saved_bytes = 0
        self.last_fallback_reason = ""
        before_bytes = len(_canonical([_jsonable_item(item) for item in items]).encode("utf-8"))
        filtered, dropped_duplicates, compacted = self._filter(items)
        if dropped_duplicates == 0 and compacted == 0:
            # Nothing provably redundant on this call: send the payload unchanged
            # and let the trigger gate's own measurement stay the honest one.
            self._remember_verbatim(items)
            return None
        # Note the notes this call would emit *before* comparing item identity, so
        # a compaction is not mistaken for a dropped item.
        self._remember_compactions(filtered)
        structural = self._structural_violation(items, filtered)
        if structural:
            return self._fallback(items, f"protected_item_dropped:{structural}", hard=True)
        if self._missing_protected_units(filtered):
            return self._fallback(items, "missing_protected_unit", hard=True)
        group_change = self._group_change(items, filtered)
        if group_change:
            # A group boundary moved without any item being dropped.  The tool
            # groups stay protected, so this is recorded and the call still falls
            # back deterministically rather than sending a re-partitioned payload.
            return self._fallback(items, f"protected_tool_group_changed:{group_change}", hard=True)
        after_bytes = len(_canonical([_jsonable_item(item) for item in filtered]).encode("utf-8"))
        if after_bytes >= before_bytes:
            self.budget_fallbacks += 1
            self.failure_reasons["no_measured_reduction"] += 1
            self.last_fallback_reason = "no_measured_reduction"
            self._remember_verbatim(items)
            return None
        self.compacted_calls += 1
        self.duplicate_units_dropped += dropped_duplicates
        self.compacted_tool_outputs += compacted
        self.last_saved_bytes = before_bytes - after_bytes
        self.saved_bytes_total += self.last_saved_bytes
        self._remember_verbatim(items)
        return filtered

    def metrics_dict(self) -> dict[str, Any]:
        return {
            "selective_retention_schema": CONSTRAINT_SCHEMA,
            "selective_retention_filter_calls": self.calls,
            "selective_retention_compacted_calls": self.compacted_calls,
            "selective_retention_duplicate_units_dropped": self.duplicate_units_dropped,
            "selective_retention_compacted_tool_outputs": self.compacted_tool_outputs,
            "selective_retention_saved_bytes_total": self.saved_bytes_total,
            "selective_retention_last_saved_bytes": self.last_saved_bytes,
            "selective_retention_task_unit_count": len(self.task_units),
            "selective_retention_constraint_unit_count": len(self.constraint_units),
            "selective_retention_protected_unit_count": len(self.protected_unit_sha256),
            "selective_retention_protected_group_count": len(self._group_hash_baseline),
            "selective_retention_group_hash_sha256": _sha(self._group_hash_baseline),
            "selective_retention_compacted_text_sha256": list(self._compacted_text_sha256),
            "task_anchor_restore_failures": self.task_anchor_restore_failures,
            "task_restore_fallbacks": self.task_restore_fallbacks,
            "budget_fallbacks": self.budget_fallbacks,
            "last_fallback_reason": self.last_fallback_reason,
            "fallback_reasons": dict(self.failure_reasons),
        }

    # -- internals --------------------------------------------------------

    def _fallback(self, items: Sequence[Any], reason: str, *, hard: bool) -> None:
        if hard:
            self.task_anchor_restore_failures += 1
            self.task_restore_fallbacks += 1
        self.failure_reasons[reason] += 1
        self.last_fallback_reason = reason
        self._remember_verbatim(items)
        return None

    def _filter(self, items: Sequence[Any]) -> tuple[list[Any], int, int]:
        """Return ``(filtered_items, dropped_duplicates, compacted_outputs)``."""
        protected = set(self.protected_unit_sha256)
        occurrences: Counter[str] = Counter()
        for item in items:
            for unit in _split_message_content(item):
                if len(unit) >= MIN_UNIT_CHARS:
                    occurrences[_text_sha(normalise_unit(unit))] += 1
        tool_name_by_call_id: dict[str, str] = {}
        for item in items:
            raw = _jsonable_item(item)
            if isinstance(raw, Mapping) and str(_item_type(item)).endswith("_call"):
                call_id = str(raw.get("call_id") or "")
                if call_id:
                    tool_name_by_call_id[call_id] = str(raw.get("name") or "tool")
        dropped_duplicates = 0
        compacted = 0
        emitted: set[str] = set()
        filtered: list[Any] = []
        for item in items:
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
                    if digest in emitted or digest in self._verbatim_text:
                        # A later exact copy, or text already shown verbatim in an
                        # earlier call of this run: the canonical copy survives.
                        dropped_duplicates += 1
                        continue
                    if occurrences[digest] > 1:
                        emitted.add(digest)
                    kept.append(unit)
                filtered.append(_rebuild_message(item, "\n".join(kept)))
                continue
            note = self._compact_output(item, tool_name_by_call_id)
            if note is None:
                filtered.append(item)
            else:
                filtered.append(note)
                compacted += 1
        return filtered, dropped_duplicates, compacted

    def _compact_output(self, item: Any, tool_name_by_call_id: Mapping[str, str]) -> Any | None:
        if not str(_item_type(item)).endswith("_output"):
            return None
        raw = _jsonable_item(item)
        if not isinstance(raw, Mapping):
            return None
        text = raw.get("output")
        if not isinstance(text, str) or len(text) < MIN_COMPACTABLE_OUTPUT_CHARS:
            return None
        if normalise_unit(text) not in self._verbatim_text:
            # The model has not been shown this text yet in this run; it must be
            # delivered in full at least once.
            return None
        call_id = str(raw.get("call_id") or "")
        tool_name = tool_name_by_call_id.get(call_id, "tool")
        note = (
            f"{PLACEHOLDER_PREFIX} {tool_name} call_id={call_id} was returned in full earlier in "
            f"this conversation ({len(text)} characters, {len(split_units(text))} lines). "
            "It is not re-transmitted; use the copy already in the conversation."
        )
        if len(note) >= len(text):
            return None
        compacted_item = dict(raw)
        compacted_item["output"] = note
        return compacted_item

    def _group_hashes(self, items: Sequence[Any]) -> dict[str, str]:
        """Group hashes for the evidence layer and the independent audit.

        A group hash covers only the fields this filter must never rewrite
        (``type``, ``call_id``, ``name``, ``arguments``, ``role``); the output
        *text* of an already-served tool result is the one field the mechanism is
        allowed to replace with a note.
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
        """Positional identity of every item: type, role and call identity.

        Output *text* is deliberately excluded: replacing an already-served
        output with this module's note is the one sanctioned text change, and it
        is checked separately by :meth:`_structural_violation`.
        """
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
        """Return a non-empty label when the two item lists are not equivalent.

        The lists are equivalent when they have the same length, the same
        positional item identity, the same protected message-unit multiset, the
        same tool-output call ids, and every output text that changed is a
        well-formed, strictly shorter compaction note for that same call id.
        """
        first = self._item_fingerprints(before)
        second = self._item_fingerprints(after)
        if len(first) != len(second):
            return "item_count"
        for index, (left, right) in enumerate(zip(first, second)):
            if left != right:
                return f"item_identity:{index}"
        protected = set(self.protected_unit_sha256)
        before_units = self._unit_multiset(before, protected)
        after_units = self._unit_multiset(after, protected)
        if before_units != after_units:
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
        """Return a non-empty label when a protected tool group changed.

        The baseline hashes are also compared against the run's first observation
        of each group, so a group whose identity is rewritten *between* calls is
        still detected even though both lists would look consistent on their own.
        """
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
        present: set[str] = set()
        for item in filtered:
            for unit in _split_message_content(item):
                present.add(_text_sha(normalise_unit(unit)))
            raw = _jsonable_item(item)
            if isinstance(raw, Mapping):
                output = raw.get("output")
                if isinstance(output, str):
                    present.add(_text_sha(normalise_unit(output)))
        return [digest for digest in self.protected_unit_sha256 if digest not in present]

    def _remember_verbatim(self, items: Sequence[Any]) -> None:
        for item in items:
            raw = _jsonable_item(item)
            if not isinstance(raw, Mapping):
                continue
            content = raw.get("content")
            if isinstance(content, str):
                self._verbatim_text.add(normalise_unit(content))
            output = raw.get("output")
            if isinstance(output, str):
                self._verbatim_text.add(normalise_unit(output))

    def _remember_compactions(self, filtered: Sequence[Any]) -> None:
        for item in filtered:
            raw = _jsonable_item(item)
            if not isinstance(raw, Mapping):
                continue
            output = raw.get("output")
            if _is_placeholder(output):
                digest = _text_sha(normalise_unit(output))
                self._compacted_text_sha256.append(digest)
                self._compacted_text_set.add(digest)


def constraint_presence(task: str, texts: Sequence[str]) -> dict[str, bool]:
    """Boolean literal-constraint presence for one payload (no text persisted)."""
    searchable = "\n".join(str(text) for text in texts).lower()
    return {
        label: any(str(phrase).lower() in searchable for phrase in phrases)
        for label, phrases in CONSTRAINTS.get(task, {}).items()
    }


class GateAwareSelectiveRetention:
    """Install selective retention *around* the shared trigger gate.

    The gate is what decides whether an arm acts at all (identical rule for
    ``pruner_v1`` and ``native_summary``), but that also means it hides the calls
    it passes through.  Selective retention needs those calls: "this tool output
    was already sent verbatim" is only knowable if every model input is observed.
    This wrapper therefore sits outside the gate, observes and remembers each
    exact model input, and delegates the actual filtering decision to the gate.
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
