"""Content-free evidence for OpenAI Agents input-filter experiments.

This module hashes local inputs but never serializes message text, tool output,
instructions, or credentials. It does not modify the object returned by the
wrapped SDK input filter. A future runner must separately call observe_model_input
at the model boundary so the exact submitted input is independently recorded.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from typing import Any, Sequence

from agents.models.interface import Model

from context_pruner.adapters.openai_agents import (
    _is_message_item,
    _item_type,
    _jsonable_item,
    _protect_responses_groups,
)
from context_pruner.types import estimate_tokens


# Public task labels only. Values are local search strings; only booleans are
# persisted. The Django set directly targets the missing v1 reference rule.
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


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def snapshot(task: str, items: Sequence[Any], instructions: str | None, stage: str, index: int) -> dict[str, Any]:
    """Return only digests, sizes, and public-constraint presence flags."""
    if task not in CONSTRAINTS:
        raise ValueError(f"unknown public task: {task}")
    serialized_items = [_jsonable_item(item) for item in items]
    payload = {"items": serialized_items, "instructions": instructions or ""}
    searchable = _canonical(payload).lower()
    _, groups = _protect_responses_groups(items, estimate_tokens)
    unprotected_searchable = _canonical([_jsonable_item(item) for item in items if _is_message_item(item)]).lower()
    protected_searchable = _canonical([_jsonable_item(item) for group in groups for item in group.items]).lower()
    protected = []
    for group in groups:
        content = [_jsonable_item(item) for item in group.items]
        outputs = [_jsonable_item(item) for item in group.items if _item_type(item).endswith("_output")]
        protected.append({
            "group_id": group.group_id,
            "group_sha256": _sha(content),
            "item_count": len(group.items),
            "tool_output_count": len(outputs),
            "tool_output_sha256": [_sha(output) for output in outputs],
        })
    return {
        "schema": "openai_input_evidence_v2",
        "task": task,
        "stage": stage,
        "index": index,
        "input_sha256": _sha(payload),
        "item_count": len(items),
        "input_bytes": len(_canonical(payload).encode("utf-8")),
        "constraint_present": {
            label: any(phrase.lower() in searchable for phrase in phrases)
            for label, phrases in CONSTRAINTS[task].items()
        },
        "constraint_present_in_unprotected_messages": {
            label: any(phrase.lower() in unprotected_searchable for phrase in phrases)
            for label, phrases in CONSTRAINTS[task].items()
        },
        "constraint_present_in_protected_groups": {
            label: any(phrase.lower() in protected_searchable for phrase in phrases)
            for label, phrases in CONSTRAINTS[task].items()
        },
        "protected_groups": protected,
    }


class InputEvidenceRecorder:
    """In-memory evidence; only hashed metadata is written to disk."""

    def __init__(self, task: str) -> None:
        if task not in CONSTRAINTS:
            raise ValueError(f"unknown public task: {task}")
        self.task = task
        self.records: list[dict[str, Any]] = []
        self.filter_calls = 0
        self.model_calls = 0

    def observe_filter_before(self, before: Any) -> int:
        index = self.filter_calls
        self.records.append(snapshot(self.task, before.input or [], before.instructions, "filter_before", index))
        self.filter_calls += 1
        return index

    def observe_filter_after(self, after: Any, index: int) -> None:
        self.records.append(snapshot(self.task, after.input or [], after.instructions, "filter_after", index))

    def observe_model_input(self, items: Sequence[Any], instructions: str | None) -> None:
        """Call immediately before the provider request; no input is returned or changed."""
        self.records.append(snapshot(self.task, items, instructions, "model_input", self.model_calls))
        self.model_calls += 1

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(_canonical(record) for record in self.records) + "\n", encoding="utf-8")


class EvidenceCaptureFilter:
    """Observe one SDK filter without modifying its returned ModelInputData."""

    def __init__(self, inner: Any, recorder: InputEvidenceRecorder) -> None:
        self.inner = inner
        self.recorder = recorder

    async def __call__(self, data: Any) -> Any:
        index = self.recorder.observe_filter_before(data.model_data)
        result = self.inner(data)
        if inspect.isawaitable(result):
            result = await result
        self.recorder.observe_filter_after(result, index)
        return result

    def metrics_dict(self) -> dict[str, Any]:
        return self.inner.metrics_dict() if hasattr(self.inner, "metrics_dict") else {}


class EvidenceModelProxy(Model):
    """Observe the exact SDK model input, then delegate the request unchanged."""

    def __init__(self, inner: Any, recorder: InputEvidenceRecorder) -> None:
        self.inner = inner
        self.recorder = recorder

    def __getattr__(self, name: str) -> Any:
        # Preserve the runner's existing usage, response and retry ledgers.
        return getattr(self.inner, name)

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ):
        items = list(input) if isinstance(input, list) else [{"role": "user", "content": str(input)}]
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
