"""Zero-API design for an all-arm structured final-answer renderer.

The model proposes JSON with issue/cause/fix and self-declared fact strings.
The host renders those strings verbatim into the frozen one-line RESULT shape.
It never truncates, paraphrases, or consults task answer keys. Ground-truth
facts and causal adequacy remain independent quality checks. An overlong or
malformed proposal fails closed; one optional provider retry is fully metered.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable


MAX_CHARS = 160
ISSUE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FIELDS = {"issue", "cause", "fix", "facts"}


@dataclass(frozen=True)
class RenderResult:
    accepted: bool
    output: str
    reason: str
    chars: int


@dataclass(frozen=True)
class MeteredResponse:
    text: str
    input_tokens: int
    output_tokens: int
    request_attempts: int


@dataclass(frozen=True)
class SettledAnswer:
    accepted: bool
    output: str
    reason: str
    repair_calls: int
    request_attempts: int | None
    input_tokens: int
    output_tokens: int
    accounting_complete: bool


class RequestFailure(Exception):
    def __init__(self, reason: str, *, request_attempts: int, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(reason)
        self.request_attempts = request_attempts
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _safe_field(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value.splitlines()) == 1
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def render(raw: str, *, expected_issue: str | None = None) -> RenderResult:
    try:
        proposal = json.loads(raw, object_pairs_hook=_unique_pairs)
    except (TypeError, ValueError, json.JSONDecodeError):
        return RenderResult(False, "", "invalid_json_or_duplicate_key", 0)
    if not isinstance(proposal, dict) or set(proposal) != FIELDS:
        return RenderResult(False, "", "missing_or_extra_field", 0)
    issue, cause, fix, facts = (proposal[name] for name in ("issue", "cause", "fix", "facts"))
    if not _safe_field(issue) or not ISSUE_RE.fullmatch(issue):
        return RenderResult(False, "", "invalid_issue", 0)
    if expected_issue is not None and issue != expected_issue:
        return RenderResult(False, "", "issue_mismatch", 0)
    if not _safe_field(cause) or not _safe_field(fix):
        return RenderResult(False, "", "invalid_cause_or_fix", 0)
    if " cause=" in cause or " fix=" in cause or " cause=" in fix or " fix=" in fix:
        return RenderResult(False, "", "embedded_field_delimiter", 0)
    if not isinstance(facts, list) or not facts or not all(_safe_field(fact) for fact in facts):
        return RenderResult(False, "", "invalid_fact_list", 0)
    output = f"RESULT issue={issue} cause={cause} fix={fix}"
    if not all(fact in output for fact in facts):
        return RenderResult(False, "", "declared_fact_missing_from_rendered_answer", len(output))
    if len(output) > MAX_CHARS:
        return RenderResult(False, "", "over_160_chars", len(output))
    return RenderResult(True, output, "accepted", len(output))


async def settle(
    first: MeteredResponse,
    retry: Callable[[], Awaitable[MeteredResponse]],
    *,
    expected_issue: str | None = None,
) -> SettledAnswer:
    """At most one extra metered request; invalid output never becomes final."""
    if first.request_attempts < 1 or min(first.input_tokens, first.output_tokens) < 0:
        raise ValueError("first request ledger invalid")
    initial = render(first.text, expected_issue=expected_issue)
    if initial.accepted:
        return SettledAnswer(True, initial.output, "accepted_first", 0, first.request_attempts, first.input_tokens, first.output_tokens, True)
    try:
        second = await retry()
    except RequestFailure as error:
        if error.request_attempts < 1 or min(error.input_tokens, error.output_tokens) < 0:
            return SettledAnswer(False, "", "retry_ledger_invalid", 1, None, first.input_tokens, first.output_tokens, False)
        return SettledAnswer(False, "", f"retry_error:{type(error).__name__}", 1, first.request_attempts + error.request_attempts, first.input_tokens + error.input_tokens, first.output_tokens + error.output_tokens, True)
    except Exception as error:
        # An unknown provider exception may hide transport attempts. Do not
        # report a falsely complete request count or admit the answer.
        return SettledAnswer(False, "", f"retry_accounting_unknown:{type(error).__name__}", 1, None, first.input_tokens, first.output_tokens, False)
    if second.request_attempts < 1 or min(second.input_tokens, second.output_tokens) < 0:
        return SettledAnswer(False, "", "retry_ledger_invalid", 1, None, first.input_tokens, first.output_tokens, False)
    revised = render(second.text, expected_issue=expected_issue)
    return SettledAnswer(
        revised.accepted,
        revised.output if revised.accepted else "",
        "accepted_retry" if revised.accepted else f"retry_rejected:{revised.reason}",
        1,
        first.request_attempts + second.request_attempts,
        first.input_tokens + second.input_tokens,
        first.output_tokens + second.output_tokens,
        True,
    )
