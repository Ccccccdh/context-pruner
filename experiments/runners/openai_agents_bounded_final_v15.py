"""Shared, fail-closed final-answer retry for prospective strict-track runs.

Wrap the existing metered RecordingRetryModel in every arm. The wrapper never
rewrites an answer locally. A single provider retry is requested only after a
final text violates the public one-line/160-character format. Both model
responses remain in the wrapped recorder's input, response and token ledgers.
The retry is not a new tool turn: tools are disabled and the original answer
is returned if the retry fails validation. Independent scoring still decides
whether facts, regex, tool sequence and task quality were preserved.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from agents.models.interface import Model


def _field(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def final_text(response: Any) -> str | None:
    """Return text only for a single final message with no tool call."""
    output = _field(response, "output", [])
    if not isinstance(output, list) or len(output) != 1:
        return None
    message = output[0]
    if _field(message, "type") != "message":
        return None
    content = _field(message, "content", [])
    if not isinstance(content, list) or len(content) != 1:
        return None
    part = content[0]
    if _field(part, "type") not in ("output_text", "text"):
        return None
    answer = _field(part, "text")
    return answer if isinstance(answer, str) else None


def format_ok(answer: str) -> bool:
    return (
        answer.startswith("RESULT ")
        and "\n" not in answer
        and len(answer) <= 160
        and bool(re.fullmatch(r"RESULT issue=\S+ cause=\S.* fix=\S.*", answer))
    )


def _hash(value: Any) -> str:
    serial = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(exclude_none=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _usage(response: Any) -> dict[str, int]:
    usage = _field(response, "usage")
    return {
        "input_tokens": int(_field(usage, "input_tokens", 0) or 0),
        "output_tokens": int(_field(usage, "output_tokens", 0) or 0),
    }


class BoundedFinalModel(Model):
    """One identical format rule and retry budget for baseline, plugin and native."""

    def __init__(self, inner: Model) -> None:
        self.inner = inner
        self.repair_ledger: list[dict[str, Any]] = []
        self.repair_evidence: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def get_response(
        self, system_instructions, input, model_settings, tools, output_schema,
        handoffs, tracing, *, previous_response_id, conversation_id, prompt,
    ):
        kwargs = dict(
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        first = await self.inner.get_response(
            system_instructions, input, model_settings, tools, output_schema,
            handoffs, tracing, **kwargs,
        )
        answer = final_text(first)
        if answer is None or format_ok(answer):
            return first

        # The repair sees exactly the same filtered context plus the original
        # final answer. It cannot call tools. Its paid usage is recorded by the
        # inner model even when validation rejects its output.
        items = list(input) if isinstance(input, list) else [{"role": "user", "content": str(input)}]
        repair_input = items + [
            {"role": "assistant", "content": answer},
            {"role": "user", "content": (
                "Rewrite only your preceding RESULT answer as one line of at most 160 "
                "characters. Keep the same issue, cause and fix facts, all technical names, "
                "and the exact 'RESULT issue=... cause=... fix=...' structure. Do not call tools "
                "or add commentary. If you cannot preserve the facts, repeat the original."
            )},
        ]
        normalized_input = _jsonable(repair_input)
        entry = {
            "trigger_answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "trigger_chars": len(answer),
            "original_usage": _usage(first),
            "repair_input_sha256": _hash(normalized_input),
            "repair_input_item_count": len(repair_input),
            "repair_tool_count": 0,
            "attempted": True,
        }
        try:
            second = await self.inner.get_response(
                system_instructions, repair_input, model_settings, [], output_schema,
                handoffs, tracing, **kwargs,
            )
            revised = final_text(second)
            entry["repair_usage"] = _usage(second)
            entry["repair_answer_sha256"] = (
                hashlib.sha256(revised.encode("utf-8")).hexdigest() if revised is not None else None
            )
            entry["repair_chars"] = len(revised) if revised is not None else None
            entry["accepted_format"] = revised is not None and format_ok(revised)
            self.repair_ledger.append(entry)
            self.repair_evidence.append({
                "input_items": normalized_input,
                "input_sha256": entry["repair_input_sha256"],
                "original_answer": answer,
                "repair_answer": revised,
            })
            return second if entry["accepted_format"] else first
        except Exception as error:
            entry["accepted_format"] = False
            entry["error_type"] = type(error).__name__
            self.repair_ledger.append(entry)
            self.repair_evidence.append({
                "input_items": normalized_input,
                "input_sha256": entry["repair_input_sha256"],
                "original_answer": answer,
                "repair_answer": None,
            })
            return first

    def stream_response(self, *args, **kwargs):
        return self.inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request):
        return self.inner.get_retry_advice(request)
