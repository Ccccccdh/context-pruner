"""Content-free v4 evidence for the selective-retention arm.

Differences from the frozen v2 evidence layer (which stays untouched):

* the schema is ``openai_selective_retention_v4``;
* every record carries the retention counters, including
  ``task_anchor_restore_failures`` - the field v3 computed in memory but never
  persisted, which is exactly why a fallback-to-full-prefix event could not be
  ruled out after that batch;
* protected group hashes are recorded for the *entire* input at every boundary,
  so a reader can check that the tool groups did not move, and additionally that
  the *text* of a tool output changed only into this module's compaction note;
* no message text, tool output, instruction or credential is ever written: only
  SHA256 digests, byte counts, booleans and counters.
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

from experiments.runners.openai_agents_selective_retention_v4 import (
    CONSTRAINTS, CONSTRAINT_SCHEMA, PLACEHOLDER_PREFIX,
)

SCHEMA = "openai_selective_retention_v4"
STAGES = ("filter_before", "filter_after", "model_input")
GROUP_HASH_LIMIT = 512


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text_sha(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def constraint_presence(task: str, searchable: str) -> dict[str, bool]:
    lowered = searchable.lower()
    return {
        label: any(str(phrase).lower() in lowered for phrase in phrases)
        for label, phrases in CONSTRAINTS.get(task, {}).items()
    }


class SelectiveRetentionEvidenceRecorder:
    """In-memory evidence; only digests, sizes, booleans and counters are saved."""

    def __init__(self, task: str, retention: Any | None = None) -> None:
        if task not in CONSTRAINTS:
            raise ValueError(f"unknown public task: {task}")
        self.task = str(task)
        #: The live filter, consulted for counters that only exist on it.
        self.retention = retention
        self.records: list[dict[str, Any]] = []
        self.filter_calls = 0
        self.model_calls = 0
        self.last_input_sha256 = ""
        self.last_compacted_tool_outputs = 0
        #: Task-unit restore failures as of each model call, in call order.
        self.restore_failures_by_model_call: list[int] = []
        self.restore_fallbacks_by_model_call: list[int] = []

    # -- boundaries -------------------------------------------------------

    def observe_filter_before(self, items: Sequence[Any], instructions: str | None) -> int:
        index = self.filter_calls
        self.records.append(
            self._snapshot(items, instructions, "filter_before", index)
        )
        self.last_compacted_tool_outputs = int(
            getattr(self.retention, "compacted_tool_outputs", 0)
        )
        self.filter_calls += 1
        return index

    def observe_filter_after(
        self, items: Sequence[Any], instructions: str | None, index: int
    ) -> None:
        delta = int(getattr(self.retention, "compacted_tool_outputs", 0)) - self.last_compacted_tool_outputs
        record = self._snapshot(items, instructions, "filter_after", index)
        record["compacted_tool_outputs_this_call"] = delta
        record["restore_fallbacks_total"] = int(
            getattr(self.retention, "task_restore_fallbacks", 0)
        )
        record["fallback_reason"] = str(getattr(self.retention, "last_fallback_reason", ""))
        self.records.append(record)

    def observe_model_input(self, items: Sequence[Any], instructions: str | None) -> None:
        record = self._snapshot(items, instructions, "model_input", self.model_calls)
        failures = int(getattr(self.retention, "task_anchor_restore_failures", 0))
        fallbacks = int(getattr(self.retention, "task_restore_fallbacks", 0))
        record["task_anchor_restore_failures"] = failures
        record["task_restore_fallbacks"] = fallbacks
        record["fallback_reason"] = str(getattr(self.retention, "last_fallback_reason", ""))
        self.records.append(record)
        self.last_input_sha256 = str(record["input_sha256"])
        self.restore_failures_by_model_call.append(failures)
        self.restore_fallbacks_by_model_call.append(fallbacks)
        self.model_calls += 1

    # -- persistence ------------------------------------------------------

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(_canonical(record) for record in self.records) + "\n", encoding="utf-8")

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
        record: dict[str, Any] = {
            "schema": SCHEMA,
            "constraint_schema": CONSTRAINT_SCHEMA,
            "task": self.task,
            "stage": stage,
            "index": index,
            "input_sha256": _sha(payload),
            "item_count": len(items),
            "input_bytes": len(encoded.encode("utf-8")),
            "constraint_present": constraint_presence(self.task, searchable),
            "constraint_present_in_unprotected_messages": constraint_presence(self.task, unprotected),
            "constraint_present_in_protected_groups": constraint_presence(self.task, protected_searchable),
            "protected_groups": self._groups(groups),
            "compaction_note_count": sum(
                1
                for item in serialized
                if isinstance(item, dict)
                and isinstance(item.get("output"), str)
                and str(item["output"]).startswith(PLACEHOLDER_PREFIX)
            ),
            "output_text_sha256": [
                _text_sha(str(item.get("output")))
                for item in serialized
                if isinstance(item, dict) and isinstance(item.get("output"), str)
            ][:GROUP_HASH_LIMIT],
        }
        return record

    @staticmethod
    def _groups(groups: Sequence[Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for group in list(groups)[:GROUP_HASH_LIMIT]:
            content = [_jsonable_item(item) for item in group.items]
            outputs = [_jsonable_item(item) for item in group.items if _item_type(item).endswith("_output")]
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
