"""Content-free v6 evidence for the pointer-retention arm.

Differences from the frozen v5 evidence layer (``openai_agents_evidence_v5.py``,
which stays untouched):

* the schema is ``openai_pointer_retention_v6`` and, in addition to the v5
  per-output manifest, every record carries the **span decision** for each output
  that was reduced: the registered source range, the omitted line intervals, the
  retained line numbers, the retained literal line numbers, the omitted character
  count and digests of the emitted note.  That is what makes "the omitted text is
  recoverable by re-issuing the tool, and the registered literal lines were kept"
  a checkable statement over the evidence instead of a claim about intent;
* the registry fingerprint of the v6 span registry is recorded next to the frozen
  v5 registry fingerprint, so a reader can tell which span table the eligibility
  rule consulted and that the v5 slice is unchanged;
* no message text, tool output, instruction, path or credential is ever written:
  only SHA256 digests, byte and line counts, booleans, counters and the interval
  boundaries the mechanism itself published in the note the model received.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from context_pruner.adapters.openai_agents import (
    _is_message_item,
    _item_type,
    _jsonable_item,
    _protect_responses_groups,
)
from context_pruner.types import estimate_tokens

from agents.models.interface import Model

from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import openai_agents_span_retention_v6 as mechanism

SCHEMA = "openai_pointer_retention_v6"
STAGES = ("filter_before", "filter_after", "model_input")
GROUP_HASH_LIMIT = 512
NOTE_PREFIX = mechanism.NOTE_PREFIX


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class PointerRetentionRecorder:
    """In-memory evidence; only digests, sizes, booleans, counts and intervals."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        if task not in registry.base.CONSTRAINTS:
            raise ValueError(f"unknown public task: {task}")
        self.task = str(task)
        self.retention = retention
        self.base_registry_fingerprint = registry.base_registry_fingerprint(self.task)
        self.registry_fingerprint = registry.registry_fingerprint(self.task)
        self.records: list[dict[str, Any]] = []
        self.filter_calls = 0
        self.model_calls = 0
        self.last_input_sha256 = ""
        self.last_elided_sources = 0
        self.restore_failures_by_model_call: list[int] = []
        self.restore_fallbacks_by_model_call: list[int] = []
        self.budget_fallbacks_by_model_call: list[int] = []

    # -- boundaries -------------------------------------------------------

    def observe_filter_before(self, items: Sequence[Any], instructions: str | None) -> int:
        index = self.filter_calls
        self.records.append(self._snapshot(items, instructions, "filter_before", index))
        self.last_elided_sources = int(getattr(self.retention, "elided_source_spans", 0))
        self.filter_calls += 1
        return index

    def observe_filter_after(
        self, items: Sequence[Any], instructions: str | None, index: int
    ) -> None:
        record = self._snapshot(items, instructions, "filter_after", index)
        record["elided_sources_this_call"] = (
            int(getattr(self.retention, "elided_source_spans", 0)) - self.last_elided_sources
        )
        record["span_elisions_this_call"] = list(
            getattr(self.retention, "last_call_elisions", []) or []
        )
        record["restore_failures_total"] = int(
            getattr(self.retention, "task_anchor_restore_failures", 0)
        )
        record["restore_fallbacks_total"] = int(
            getattr(self.retention, "task_restore_fallbacks", 0)
        )
        record["budget_fallbacks_total"] = int(getattr(self.retention, "budget_fallbacks", 0))
        record["fallback_reason"] = str(getattr(self.retention, "last_fallback_reason", ""))
        self.records.append(record)

    def observe_model_input(self, items: Sequence[Any], instructions: str | None) -> None:
        record = self._snapshot(items, instructions, "model_input", self.model_calls)
        failures = int(getattr(self.retention, "task_anchor_restore_failures", 0))
        fallbacks = int(getattr(self.retention, "task_restore_fallbacks", 0))
        budget = int(getattr(self.retention, "budget_fallbacks", 0))
        record["task_anchor_restore_failures"] = failures
        record["task_restore_fallbacks"] = fallbacks
        record["budget_fallbacks"] = budget
        record["fallback_reason"] = str(getattr(self.retention, "last_fallback_reason", ""))
        record["elided_output_count"] = sum(
            1 for entry in record["output_manifest"] if entry.get("output_elided")
        )
        self.records.append(record)
        self.last_input_sha256 = str(record["input_sha256"])
        self.restore_failures_by_model_call.append(failures)
        self.restore_fallbacks_by_model_call.append(fallbacks)
        self.budget_fallbacks_by_model_call.append(budget)
        self.model_calls += 1

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(_canonical(record) for record in self.records) + "\n", encoding="utf-8"
        )

    # -- internals --------------------------------------------------------

    def _snapshot(
        self,
        items: Sequence[Any],
        instructions: str | None,
        stage: str,
        index: int,
    ) -> dict[str, Any]:
        serialized = [_jsonable_item(item) for item in items]
        payload = {"items": serialized, "instructions": instructions or ""}
        encoded = _canonical(payload)
        searchable = _canonical({"items": serialized}).lower()
        unprotected = _canonical(
            [_jsonable_item(item) for item in items if _is_message_item(item)]
        ).lower()
        _, groups = _protect_responses_groups(items, estimate_tokens)
        protected_searchable = _canonical(
            [_jsonable_item(item) for group in groups for item in group.items]
        ).lower()
        manifest = self._output_manifest(items)
        elided = [entry for entry in manifest if entry["output_elided"]]
        return {
            "schema": SCHEMA,
            "registry_schema": registry.REGISTRY_SCHEMA,
            "base_registry_schema": registry.base.REGISTRY_SCHEMA,
            "registry_fingerprint": self.registry_fingerprint,
            "base_registry_fingerprint": self.base_registry_fingerprint,
            "task": self.task,
            "stage": stage,
            "index": index,
            "input_sha256": _sha(payload),
            "item_count": len(items),
            "input_bytes": len(encoded.encode("utf-8")),
            "registered_literals_present": registry.base.constraint_presence(
                self.task, searchable
            ),
            "registered_literal_counts": self._literal_counts(searchable),
            "registered_literals_in_unprotected_messages": registry.base.constraint_presence(
                self.task, unprotected
            ),
            "registered_literals_in_protected_groups": registry.base.constraint_presence(
                self.task, protected_searchable
            ),
            "protected_groups": self._groups(groups),
            "output_manifest": manifest,
            "pinned_output_count": sum(1 for entry in manifest if entry["pinned"]),
            "constraint_carrying_output_count": sum(
                1 for entry in manifest if entry["registered_literals"]
            ),
            "outputs_elided": len(elided),
            "elided_source_count": sum(
                1 for entry in elided if entry["output_elided"]
            ),
            "span_reduced_constraint_carriers": sum(
                1
                for entry in elided
                if entry["registered_literals"] and entry["span_retained_lines"]
            ),
            "notes_with_omitted_intervals": sum(
                1 for entry in elided if entry["omitted_intervals"]
            ),
            "pointer_coverage": all(
                bool(entry.get("note_has_source_pointer")) for entry in elided
            )
            if elided
            else True,
            "pointer_completeness": all(
                bool(entry.get("pointer_completeness")) for entry in elided
            )
            if elided
            else True,
            "omitted_line_total": sum(int(entry.get("omitted_line_count", 0)) for entry in elided),
            "retained_line_total": sum(
                int(entry.get("retained_line_count", 0)) for entry in elided
            ),
            "retained_literal_line_total": sum(
                int(entry.get("retained_literal_line_count", 0)) for entry in elided
            ),
            "elision_note_count": sum(
                1
                for item in serialized
                if isinstance(item, dict)
                and isinstance(item.get("output"), str)
                and NOTE_PREFIX in str(item["output"]).splitlines()[0]
            ),
            "recent_tool_group_items": self._recent_group_size(items),
            "most_recent_tool_group_kept_in_full": self._recent_group_intact(items),
        }

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        return {
            label: sum(searchable.count(str(phrase).lower()) for phrase in phrases)
            for label, phrases in registry.base.literals_for(self.task).items()
        }

    def _output_manifest(self, items: Sequence[Any]) -> list[dict[str, Any]]:
        """Per-output eligibility, retention and span record for one payload."""
        retention = self.retention
        decision = getattr(retention, "last_call_decision", {}) if retention else {}
        elisions: dict[str, dict[str, Any]] = {}
        for entry in getattr(retention, "last_call_elisions", []) or []:
            elisions[str(entry.get("call_id") or "")] = entry
        for entry in decision.get("elided_output_pointers", []) or []:
            elisions.setdefault(str(entry.get("call_id") or ""), entry)

        base_records: list[dict[str, Any]] = []
        if retention is not None and hasattr(retention, "classify_outputs"):
            base_records = list(retention.classify_outputs(items)["outputs"])
        else:
            for index, item in enumerate(items):
                if not str(_item_type(item)).endswith("_output"):
                    continue
                raw = _jsonable_item(item)
                if not isinstance(raw, dict):
                    continue
                text = raw.get("output")
                call_id = str(raw.get("call_id") or raw.get("id") or f"#{index}")
                base_records.append(
                    {
                        "call_id": call_id,
                        "tool": "",
                        "output_sha256": _text_sha(text if isinstance(text, str) else ""),
                        "output_chars": len(text) if isinstance(text, str) else 0,
                        "output_lines": len(text.splitlines()) if isinstance(text, str) else 0,
                        "registered_literals": [],
                        "pinned": False,
                        "pinned_reasons": [],
                        "recovery_pointer": None,
                        "registered_source_pointer": None,
                        "registered_span": None,
                        "registered_literal_line_count": 0,
                    }
                )
        entries: list[dict[str, Any]] = []
        for record in base_records:
            call_id = str(record.get("call_id") or "")
            note_text = None
            for item in items:
                raw = _jsonable_item(item)
                if (
                    isinstance(raw, dict)
                    and str(raw.get("call_id") or "") == call_id
                    and str(_item_type(item)).endswith("_output")
                ):
                    note_text = raw.get("output")
                    break
            span = elisions.get(call_id, {})
            elided = bool(
                isinstance(note_text, str)
                and note_text
                and (NOTE_PREFIX in str(note_text).splitlines()[0])
            )
            entries.append(
                {
                    "call_id": call_id,
                    "tool": record.get("tool", ""),
                    "output_sha256": record.get("output_sha256", ""),
                    "output_chars": int(record.get("output_chars", 0) or 0),
                    "output_lines": int(record.get("output_lines", 0) or 0),
                    "registered_literals": list(record.get("registered_literals") or []),
                    "pinned": bool(record.get("pinned")) and not elided,
                    "pinned_reasons": list(record.get("pinned_reasons") or []),
                    "registered_source_pointer": bool(record.get("registered_source_pointer")),
                    "registered_span": record.get("registered_span"),
                    "output_elided": elided,
                    "span_retained_lines": list(span.get("kept_lines") or []),
                    "span_retained_literal_lines": list(span.get("kept_literal_lines") or []),
                    "omitted_intervals": [
                        list(value) for value in (span.get("omitted_intervals") or [])
                    ],
                    "omitted_line_count": int(span.get("omitted_line_count", 0) or 0),
                    "retained_line_count": int(span.get("kept_line_count", 0) or 0),
                    "retained_literal_line_count": int(
                        span.get("kept_literal_line_count", 0) or 0
                    ),
                    "omitted_char_count": int(span.get("omitted_char_count", 0) or 0),
                    "elided_range": list(span.get("source_range") or []),
                    "carrier": bool(span.get("carrier", False)),
                    "pointer_completeness": bool(span.get("pointer_completeness", False)),
                    "note_has_source_pointer": bool(
                        elided
                        and span.get("note_states_pointer", False)
                        and span.get("pointer_matches_registered_source", False)
                    ),
                    "note_sha256": _text_sha(note_text) if isinstance(note_text, str) else "",
                    "note_omitted_interval_sha256": (
                        _sha([list(v) for v in (span.get("omitted_intervals") or [])])
                        if elided
                        else ""
                    ),
                }
            )
        return entries

    @staticmethod
    def _recent_group_size(items: Sequence[Any]) -> int:
        return len(PointerRetentionRecorder._recent_group_indices(items))

    def _recent_group_intact(self, items: Sequence[Any]) -> bool:
        """True when no item of the newest tool group was replaced by a v6 note."""
        indices = self._recent_group_indices(items)
        if not indices:
            return True
        for index in indices:
            raw = _jsonable_item(items[index])
            if isinstance(raw, dict):
                text = raw.get("output")
                if isinstance(text, str) and text and NOTE_PREFIX in text.splitlines()[0]:
                    return False
        return True

    @staticmethod
    def _recent_group_indices(items: Sequence[Any]) -> set[int]:
        """Indices of the most recent tool group, read independently of the filter."""
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

    @staticmethod
    def _groups(groups: Sequence[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in list(groups)[:GROUP_HASH_LIMIT]:
            content = [_jsonable_item(item) for item in group.items]
            outputs = [
                _jsonable_item(item)
                for item in group.items
                if _item_type(item).endswith("_output")
            ]
            out.append(
                {
                    "group_id": str(group.group_id),
                    "group_sha256": _sha(content),
                    "item_count": len(group.items),
                    "tool_output_count": len(outputs),
                    "tool_output_sha256": [_sha(output) for output in outputs],
                }
            )
        return out


class PointerRetentionModelProxy(Model):
    """Observe the exact SDK model input, then delegate the request unchanged."""

    def __init__(self, inner: Any, recorder: PointerRetentionRecorder) -> None:
        self.inner = inner
        self.recorder = recorder

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ):
        items = (
            list(input)
            if isinstance(input, list)
            else [{"role": "user", "content": str(input)}]
        )
        self.recorder.observe_model_input(items, system_instructions)
        return await self.inner.get_response(
            system_instructions, input, model_settings, tools, output_schema,
            handoffs, tracing, previous_response_id=previous_response_id,
            conversation_id=conversation_id, prompt=prompt,
        )

    def stream_response(self, *args, **kwargs):
        return self.inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request):
        return self.inner.get_retry_advice(request)
