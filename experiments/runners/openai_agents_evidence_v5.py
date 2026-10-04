"""Content-free v5 evidence for the evidence-safe retention arm.

Differences from the frozen v4 evidence layer (``openai_agents_evidence_v4.py``,
which stays untouched):

* the schema is ``openai_evidence_safe_retention_v5`` and every record carries the
  registry fingerprint it was produced under, so a reader can tell which
  registered literal set the eligibility rule consulted;
* each output item in the payload is described individually - ``call_id``, tool
  name, text SHA256, byte and line counts, the registered literals it carries,
  whether it is pinned and why, and whether a recovery pointer was parseable.
  That is what makes the v5 eligibility rule testable **from the evidence layer
  alone**: "a constraint carrier is never replaced by a note" and "the most
  recent tool group is kept in full" become assertions over these records;
* ``registered_literal_counts`` records how many times each registered literal
  occurs at the boundary, and ``registered_literals_present`` the booleans, so a
  net loss of an occurrence is visible per model call;
* no message text, tool output, instruction, path or credential is ever written:
  only SHA256 digests, byte and line counts, booleans and counters.  Recovery
  pointers are stored as digests and flag counts, never as text.
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

from experiments.runners import openai_agents_literal_registry_v5 as registry

SCHEMA = "openai_evidence_safe_retention_v5"
STAGES = ("filter_before", "filter_after", "model_input")
GROUP_HASH_LIMIT = 512
NOTE_PREFIX = "[evidence-safe-retention v5]"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


class EvidenceSafeRetentionRecorder:
    """In-memory evidence; only digests, sizes, booleans and counters are saved."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        if task not in registry.CONSTRAINTS:
            raise ValueError(f"unknown public task: {task}")
        self.task = str(task)
        #: The live filter, consulted for counters and for the eligibility view.
        self.retention = retention
        self.registry_fingerprint = registry.registry_fingerprint(self.task)
        self.records: list[dict[str, Any]] = []
        self.filter_calls = 0
        self.model_calls = 0
        self.last_input_sha256 = ""
        self.last_compacted_tool_outputs = 0
        #: Task/constraint restore failures as of each model call, in call order.
        self.restore_failures_by_model_call: list[int] = []
        self.restore_fallbacks_by_model_call: list[int] = []
        self.budget_fallbacks_by_model_call: list[int] = []

    # -- boundaries -------------------------------------------------------

    def observe_filter_before(self, items: Sequence[Any], instructions: str | None) -> int:
        index = self.filter_calls
        record = self._snapshot(items, instructions, "filter_before", index)
        self.records.append(record)
        self.last_compacted_tool_outputs = int(
            getattr(self.retention, "compacted_tool_outputs", 0)
        )
        self.last_budget_fallbacks = int(getattr(self.retention, "budget_fallbacks", 0))
        self.filter_calls += 1
        return index

    def observe_filter_after(
        self, items: Sequence[Any], instructions: str | None, index: int
    ) -> None:
        delta = int(getattr(self.retention, "compacted_tool_outputs", 0)) - self.last_compacted_tool_outputs
        record = self._snapshot(items, instructions, "filter_after", index)
        record["compacted_tool_outputs_this_call"] = delta
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
            1
            for entry in record["output_manifest"]
            if entry.get("output_replaced_by_note")
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
        note_records = [entry for entry in manifest if entry["output_replaced_by_note"]]
        pointer_records = [
            entry
            for entry in note_records
            if entry["note_has_source_pointer"]
        ]
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "registry_schema": registry.REGISTRY_SCHEMA,
            "registry_fingerprint": self.registry_fingerprint,
            "task": self.task,
            "stage": stage,
            "index": index,
            "input_sha256": _sha(payload),
            "item_count": len(items),
            "input_bytes": len(encoded.encode("utf-8")),
            "registered_literals_present": registry.constraint_presence(
                self.task, searchable
            ),
            "registered_literal_counts": self._literal_counts(searchable),
            "registered_literals_in_unprotected_messages": registry.constraint_presence(
                self.task, unprotected
            ),
            "registered_literals_in_protected_groups": registry.constraint_presence(
                self.task, protected_searchable
            ),
            "protected_groups": self._groups(groups),
            "output_manifest": manifest,
            "pinned_output_count": sum(1 for entry in manifest if entry["pinned"]),
            "constraint_carrying_output_count": sum(
                1 for entry in manifest if entry["registered_literals"]
            ),
            "outputs_replaced_by_note": len(note_records),
            "notes_with_source_pointer": len(pointer_records),
            "note_pointer_coverage": (
                len(pointer_records) == len(note_records) if note_records else True
            ),
            "compaction_note_count": sum(
                1
                for item in serialized
                if isinstance(item, dict)
                and isinstance(item.get("output"), str)
                and str(item["output"]).startswith(NOTE_PREFIX)
            ),
            "recent_tool_group_items": self._recent_group_size(items),
            "most_recent_tool_group_kept_in_full": self._recent_group_intact(items),
        }
        return record

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        return {
            label: sum(
                searchable.count(str(phrase).lower()) for phrase in phrases
            )
            for label, phrases in registry.literals_for(self.task).items()
        }

    def _output_manifest(self, items: Sequence[Any]) -> list[dict[str, Any]]:
        retention = self.retention
        #: Pointer the live filter recorded for each elided output on this call.
        #: The mechanism parses the pointer out of the note it actually wrote, so
        #: this is a record of the emitted artifact, not a claim about intent.
        emitted: dict[str, dict[str, Any]] = {}
        decision = getattr(retention, "last_call_decision", {}) if retention else {}
        for entry in decision.get("elided_output_pointers", []) or []:
            emitted[str(entry.get("call_id") or "")] = entry
        if retention is not None and hasattr(retention, "classify_outputs"):
            base = retention.classify_outputs(items)
            entries = []
            for entry in base["outputs"]:
                note_text = None
                for item in items:
                    raw = _jsonable_item(item)
                    if (
                        isinstance(raw, dict)
                        and str(raw.get("call_id") or "") == entry["call_id"]
                        and str(_item_type(item)).endswith("_output")
                    ):
                        note_text = raw.get("output")
                        break
                replaced = isinstance(note_text, str) and note_text.startswith(NOTE_PREFIX)
                pointer_record = emitted.get(entry["call_id"], {})
                pointer_digest = str(pointer_record.get("source_pointer_sha256") or "")
                pointer_checked = bool(
                    pointer_record.get("pointer_matches_registered_source")
                )
                entries.append(
                    {
                        "call_id": entry["call_id"],
                        "tool": entry["tool"],
                        "output_sha256": entry["output_sha256"],
                        "output_chars": entry["output_chars"],
                        "output_lines": entry["output_lines"],
                        "registered_literals": list(entry["registered_literals"]),
                        "pinned": bool(entry["pinned"]) and not replaced,
                        "pinned_reasons": list(entry["pinned_reasons"]),
                        "recovery_pointer_sha256": (
                            _sha(entry["recovery_pointer"])
                            if entry["recovery_pointer"]
                            else pointer_digest
                        ),
                        "registered_source_pointer": bool(
                            entry["registered_source_pointer"]
                        ),
                        "output_replaced_by_note": replaced,
                        # A note is only accepted as recoverable when the note the
                        # model actually receives parsed back into exactly the
                        # path and line range the registry records for that tool.
                        "note_has_source_pointer": bool(replaced and pointer_checked),
                    }
                )
            return entries
        # No live filter (``none`` / ``native_summary`` arms): describe the
        # outputs without eligibility, so the column stays comparable.
        entries = []
        for index, item in enumerate(items):
            if not str(_item_type(item)).endswith("_output"):
                continue
            raw = _jsonable_item(item)
            if not isinstance(raw, dict):
                continue
            text = raw.get("output")
            call_id = str(raw.get("call_id") or raw.get("id") or f"#{index}")
            labels = registry.labels_in_text(self.task, text if isinstance(text, str) else "")
            replaced = isinstance(text, str) and text.startswith(NOTE_PREFIX)
            entries.append(
                {
                    "call_id": call_id,
                    "tool": "",
                    "output_sha256": _text_sha(text if isinstance(text, str) else ""),
                    "output_chars": len(text) if isinstance(text, str) else 0,
                    "output_lines": len(text.splitlines()) if isinstance(text, str) else 0,
                    "registered_literals": list(labels),
                    "pinned": False,
                    "pinned_reasons": [],
                    "recovery_pointer_sha256": "",
                    "registered_source_pointer": False,
                    "output_replaced_by_note": replaced,
                    "note_has_source_pointer": False,
                }
            )
        return entries

    @staticmethod
    def _recent_group_size(items: Sequence[Any]) -> int:
        return len(EvidenceSafeRetentionRecorder._recent_group_indices(items))

    def _recent_group_intact(self, items: Sequence[Any]) -> bool:
        """True when no item of the newest tool group was replaced by this module's note.

        A payload with no tool items at all has nothing to keep, so it counts as
        intact: the invariant is "the newest group is never elided", not "a group
        must exist".
        """
        indices = self._recent_group_indices(items)
        if not indices:
            return True
        for index in indices:
            raw = _jsonable_item(items[index])
            if isinstance(raw, dict):
                text = raw.get("output")
                if isinstance(text, str) and text.startswith(NOTE_PREFIX):
                    return False
        return True

    @staticmethod
    def _recent_group_indices(items: Sequence[Any]) -> set[int]:
        """Indices of the most recent tool group: the newest call/output pair.

        Restated here rather than imported, so the evidence layer's view of "most
        recent tool group" is an *independent* reading of the payload.  If the
        mechanism and this reading ever disagree, the audit's
        ``most_recent_tool_group_kept_in_full`` check fails instead of quietly
        agreeing with the mechanism it is supposed to verify.
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

    @staticmethod
    def _groups(groups: Sequence[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in list(groups)[:GROUP_HASH_LIMIT]:
            content = [_jsonable_item(item) for item in group.items]
            outputs = [
                _jsonable_item(item) for item in group.items if _item_type(item).endswith("_output")
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


class EvidenceSafeModelProxy(Model):
    """Observe the exact SDK model input, then delegate the request unchanged."""

    def __init__(self, inner: Any, recorder: EvidenceSafeRetentionRecorder) -> None:
        self.inner = inner
        self.recorder = recorder

    def __getattr__(self, name: str) -> Any:
        # Preserve the runner's usage, response and retry ledgers.
        return getattr(self.inner, name)

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ):
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
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
