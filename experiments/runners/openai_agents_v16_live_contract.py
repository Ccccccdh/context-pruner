"""The v16 live host configuration: structured answer, deterministic renderer, one retry.

The all-arm contract is fixed by ``V16_ACQUISITION_FREEZE_20261006.json``:

* the agent is built with ``output_type=StructuredAnswer`` (JSON ``issue``/``cause``/``fix``/
  ``facts``), identically in every arm;
* the final answer the host records is produced by ``render()`` in
  ``openai_agents_structured_final_v16.py`` - it never truncates, never paraphrases and never
  consults an answer key, and it fails closed (empty answer) on malformed JSON, duplicate or
  unexpected keys, unsafe fields, a declared fact missing from the rendered line, or more than
  160 characters;
* at most **one** additional provider request may be spent per sample, and that retry is fully
  metered; a retry that is still rejected never becomes the final answer;
* every provider call is recorded per call with its billed sizes, its cache fields
  (``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``, recorded as unknown rather than
  zero when the provider omits them), its UTC timestamp and whether it fell in the off-peak
  window.

Nothing here edits a frozen module: the provider model, the agent factory and the sample runner
of ``run_openai_agents_api_experiment`` are patched at runtime and restored afterwards.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from agents import Agent, ModelResponse, Usage
from agents.models.interface import Model
from openai.types.responses import (
    ResponseFunctionToolCall,
    ResponseOutputMessage,
    ResponseOutputText,
)

from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners.openai_agents_bounded_final_v15 import final_text
from experiments.runners.openai_agents_structured_final_v16 import MAX_CHARS, render
from experiments.runners.openai_agents_v16_controls import (
    aggregate_cache,
    extract_cache_usage,
)

STRUCTURED_SCHEMA = "all_arm_structured_json_v16"
RENDERER_MODULE = "experiments/runners/openai_agents_structured_final_v16.py"
RENDERER_RELATIVE = RENDERER_MODULE
RETRY_BUDGET_PER_SAMPLE = 1
#: The four conditions the frozen criteria report per sample, plus the render verdict.
CONDITION_NAMES = (
    "facts_complete",
    "frozen_regex",
    "single_line_prefix",
    "within_160",
)
PER_SAMPLE_FIELDS = (
    "source_chars",
    "rendered_chars",
    "render_accepted",
    "facts_complete",
    "frozen_regex",
    "single_line_prefix",
    "within_160",
)
#: A sample needs six reads plus one answer; below this the batch must stop instead of failing.
MIN_SAMPLE_REQUESTS = 7
STRUCTURED_INSTRUCTION = (
    "Your final message must be that JSON object alone, with no text before or after it: the "
    "host renders issue, cause and fix into the single RESULT line, so never write 'RESULT' "
    "yourself and never add commentary."
)
#: The frozen runner's own prompt describes the *old* final-answer contract (one RESULT line
#: written by the model). Under the v16 host configuration the model writes JSON and the host
#: renders the line, so those two sentences are replaced for every arm, identically. The
#: replacement is recorded in the artifacts, and a frozen prompt that no longer contains the
#: replaced text is reported instead of being silently left contradictory.
PROMPT_REPLACEMENTS = (
    (
        "The final response must be exactly one line, begin with RESULT, contain at most 160 "
        "characters, and follow this contract: ",
        "Your final message must be exactly one JSON object and nothing else, following this "
        "contract: ",
    ),
    (
        "Replace angle-bracket fields with tool values.",
        "Fill every field with the values the tools returned.",
    ),
)


def adapt_instructions(text: str) -> tuple[str, list[str], list[str]]:
    """Replace the frozen one-line-RESULT sentences with the structured contract, identically."""

    adapted = str(text)
    applied: list[str] = []
    unapplied: list[str] = []
    for old, new in PROMPT_REPLACEMENTS:
        if old in adapted:
            adapted = adapted.replace(old, new)
            applied.append(old)
        else:
            unapplied.append(old)
    return adapted, applied, unapplied
RETRY_INSTRUCTION = (
    "Rewrite only your preceding JSON answer. Keep the same issue, the same cause, the same fix "
    "and the same fact strings. Reply with the same JSON object shape (issue, cause, fix, facts) "
    "and nothing else. Do not call tools. If the host rejected the answer, its exact reason "
    "follows and your rewrite must fix it."
)
#: The retry is only useful if it says what the host rejected. The diagnostic is appended to the
#: retry message verbatim, chosen by rejection reason, and recorded in the ledger and the
#: per-sample evidence. It carries no new information about the task, only about the failure.
DIAGNOSTIC_MARKER = "the host rejected your previous JSON"
DIAGNOSTIC_TEXT = {
    "declared_fact_missing_from_rendered_answer": (
        "every string you list in \"facts\" must appear verbatim in the rendered line, which is "
        "built from \"issue\", \"cause\" and \"fix\" only, and a fact you listed does not appear "
        "there. Either put that exact string into \"cause\" or \"fix\", or remove it from "
        "\"facts\"."
    ),
    "over_160_chars": (
        "the rendered line 'RESULT issue=... cause=... fix=...' exceeded 160 characters. Shorten "
        "\"cause\" and \"fix\" while keeping every fact string and every required technical name."
    ),
    "invalid_json_or_duplicate_key": (
        "your previous message was not a single JSON object, or it repeated a key. Reply with "
        "exactly one JSON object holding the keys issue, cause, fix and facts, each key once."
    ),
    "missing_or_extra_field": (
        "your JSON object must contain exactly the keys issue, cause, fix and facts: no key "
        "missing and no extra key."
    ),
    "invalid_fact_list": (
        "\"facts\" must be a non-empty list of single-line strings, each of which appears "
        "verbatim in the rendered line."
    ),
    "invalid_cause_or_fix": (
        "\"cause\" and \"fix\" must be single-line non-empty strings without leading or trailing "
        "spaces."
    ),
    "embedded_field_delimiter": (
        "\"cause\" and \"fix\" must not contain the text ' cause=' or ' fix=', because the host "
        "uses those markers to build the rendered line."
    ),
    "issue_mismatch": "\"issue\" must be exactly the issue identifier the contract states.",
    "invalid_issue": "\"issue\" must be a lowercase identifier such as django-13821.",
}


def diagnostic_for(
    reason: str,
    *,
    raw: str = "",
    expected_issue: str = "",
    required_facts: tuple[str, ...] | list[str] = (),
    cause_token: str = "",
    fix_token: str = "",
) -> str:
    """The exact sentence the retry receives, chosen by the host's rejection reason.

    For an over-length rejection the sentence states both halves of the trade-off: the
    160-character bound and the exact strings that must still appear verbatim. The pilot showed
    four over-length rejections that the previous, general wording did not repair, so the
    wording now names the bound and the literals instead of asking for a shorter rewrite.
    """

    reason = str(reason)
    detail = DIAGNOSTIC_TEXT.get(reason)
    if detail is None:
        detail = (
            "the host could not render your JSON: "
            + reason.replace("retry_rejected:", "").replace("_", " ")
            + "."
        )
    # Every class gets the same two-sided trade-off. The pilot showed why: told only that a
    # declared fact was missing, the model added text and pushed the rendered line past 160, so
    # a one-sided diagnostic repairs one condition while breaking another.
    literals = [str(fact) for fact in required_facts]
    detail += " The rendered line must stay within 160 characters."
    if literals:
        detail += (
            " These strings must still appear verbatim in it: "
            + ", ".join(repr(entry) for entry in literals)
            + "."
        )
    for label, token in (("cause", cause_token), ("fix", fix_token)):
        if token:
            detail += f" The {label} field must still contain {token!r}."
    detail += (
        " Keep the answer as short as the required strings allow: drop explanations, "
        "restatements and repeated names, and never state the same fact twice."
    )
    if reason == "over_160_chars":
        detail += (
            " Remove wording, not requirements: a shorter line that loses a required string is "
            "rejected as well."
        )
    missing = missing_declared_facts(raw, expected_issue=expected_issue)
    if missing and reason == "declared_fact_missing_from_rendered_answer":
        detail += " The declared strings that are missing are: " + ", ".join(
            repr(entry) for entry in missing
        )
        detail += (
            " Place each of them inside cause or fix, and keep the line within the bound at the "
            "same time."
        )
    return f"{DIAGNOSTIC_MARKER}: {reason}. {detail}"


def missing_declared_facts(raw: str, *, expected_issue: str = "") -> list[str]:
    """Declared facts that the candidate line built from the same JSON would not contain."""

    try:
        proposal = json.loads(raw)
    except Exception:
        return []
    if not isinstance(proposal, dict):
        return []
    cause, fix, facts = proposal.get("cause"), proposal.get("fix"), proposal.get("facts")
    if not all(isinstance(value, str) for value in (cause, fix)) or not isinstance(facts, list):
        return []
    candidate = f"RESULT issue={proposal.get('issue', expected_issue)} cause={cause} fix={fix}"
    return [str(fact) for fact in facts if isinstance(fact, str) and fact not in candidate]


class RequestCapReached(RuntimeError):
    """Raised before a sample that cannot fit inside the remaining frozen request budget."""


# ----------------------------------------------------------------------------- conditions
def conditions(answer: str, issue: str, pattern: str, facts: tuple[str, ...] | list[str]) -> dict[str, bool]:
    """The four frozen conditions, computed independently of the renderer's own verdict."""

    text = str(answer)
    return {
        "facts_complete": all(str(fact) in text for fact in facts),
        "frozen_regex": bool(re.fullmatch(str(pattern), text, flags=re.IGNORECASE)) if text else False,
        "single_line_prefix": bool(text) and text.startswith("RESULT ") and "\n" not in text,
        "within_160": bool(text) and len(text) <= MAX_CHARS,
    }


def render_report(
    *,
    source_chars: int,
    output: str,
    issue: str,
    pattern: str,
    facts: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    """The per-sample report the freeze fixes, in the freeze's own field names."""

    accepted = str(output)
    verdict = conditions(accepted, issue, pattern, facts)
    return {
        "source_chars": int(source_chars),
        "rendered_chars": len(accepted),
        "render_accepted": bool(accepted),
        **verdict,
    }


def self_test(expected_issue: str = "self-test") -> dict[str, Any]:
    """Zero-API proof that the renderer and the structured type fail closed as frozen."""

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    check("max_chars_is_160", MAX_CHARS == 160, MAX_CHARS)
    over_long = json.dumps(
        {"issue": expected_issue, "cause": "c" * 200, "fix": "f", "facts": ["f"]}
    )
    verdict = render(over_long, expected_issue=expected_issue)
    check(
        "over_long_input_rejected_empty_output",
        (not verdict.accepted) and verdict.output == "" and verdict.reason == "over_160_chars",
        {"reason": verdict.reason, "chars": verdict.chars},
    )
    cases = {
        "duplicate_key": (
            '{"issue": "%s", "issue": "%s", "cause": "c", "fix": "c", "facts": ["c"]}'
            % (expected_issue, expected_issue),
            "invalid_json_or_duplicate_key",
        ),
        "extra_field": (
            json.dumps(
                {
                    "issue": expected_issue,
                    "cause": "c",
                    "fix": "c",
                    "facts": ["c"],
                    "note": "extra",
                }
            ),
            "missing_or_extra_field",
        ),
        "unsafe_field": (
            json.dumps(
                {"issue": expected_issue, "cause": "c\nd", "fix": "c", "facts": ["c"]}
            ),
            "invalid_cause_or_fix",
        ),
        "embedded_delimiter": (
            json.dumps(
                {
                    "issue": expected_issue,
                    "cause": "c fix=d",
                    "fix": "c",
                    "facts": ["c"],
                }
            ),
            "embedded_field_delimiter",
        ),
        "declared_fact_missing": (
            json.dumps(
                {
                    "issue": expected_issue,
                    "cause": "c",
                    "fix": "c",
                    "facts": ["c", "absent-fact"],
                }
            ),
            "declared_fact_missing_from_rendered_answer",
        ),
        "issue_mismatch": (
            json.dumps(
                {"issue": "other-issue", "cause": "c", "fix": "c", "facts": ["c"]}
            ),
            "issue_mismatch",
        ),
        "valid": (
            json.dumps(
                {"issue": expected_issue, "cause": "c", "fix": "c", "facts": ["c"]}
            ),
            "accepted",
        ),
    }
    for name, (raw, expected) in cases.items():
        result = render(raw, expected_issue=expected_issue)
        ok = result.reason == expected and (result.accepted == (expected == "accepted"))
        check(f"raw_{name}", ok, {"reason": result.reason, "expected": expected})
    answer_type = structured_answer_type(expected_issue)
    good = answer_type(issue=expected_issue, cause="c", fix="c", facts=["c"])
    check(
        "final_answer_path_is_render",
        str(good) == f"RESULT issue={expected_issue} cause=c fix=c",
        str(good),
    )
    bad = answer_type(issue=expected_issue, cause="c" * 200, fix="c", facts=["c"])
    check("over_long_instance_renders_empty", str(bad) == "", repr(str(bad)))
    return {
        "checks": checks,
        "ok": all(entry["ok"] for entry in checks),
        "max_answer_chars": MAX_CHARS,
        "renderer_module": RENDERER_MODULE,
        "api_calls": 0,
    }


# ------------------------------------------------------------------------ structured type
def structured_answer_type(expected_issue: str):
    """The pydantic model the SDK is asked to produce, whose text is the rendered answer."""

    from pydantic import BaseModel, Field

    class StructuredAnswer(BaseModel):
        issue: str = Field(min_length=1, max_length=64)
        cause: str = Field(min_length=1)
        fix: str = Field(min_length=1)
        facts: list[str] = Field(min_length=1)

        def __str__(self) -> str:  # the final-answer path is the frozen renderer
            return render(self.model_dump_json(), expected_issue=expected_issue).output

    return StructuredAnswer


# --------------------------------------------------------------------------- metered model
def _sha(value: Any) -> str:
    serial = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def _attempts(inner: Any) -> int | None:
    budget = getattr(inner, "request_budget", None)
    used = getattr(budget, "used", None)
    return int(used) if isinstance(used, int) else None


def _usage_of(response: Any) -> Any:
    return getattr(response, "usage", None) if not isinstance(response, dict) else response.get("usage")


def cache_usage_of(usage: Any, *, when: datetime | None = None) -> dict[str, Any]:
    """Per-call cache accounting; a field the provider omits stays unknown, never zero.

    Three lookup paths are tried because the SDK may keep the vendor's extra usage fields as
    attributes, in ``model_extra``, or only in a full dump.
    """

    record = extract_cache_usage(usage, when=when)
    if not record["cache_fields_present"]:
        extra = getattr(usage, "model_extra", None) or {}
        dumped: dict[str, Any] = {}
        dump = getattr(usage, "model_dump", None)
        if callable(dump):
            try:
                dumped = dict(dump())
            except Exception:  # pragma: no cover - defensive
                dumped = {}
        for name in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            if record[name] is None:
                found = extra.get(name, dumped.get(name))
                if isinstance(found, int):
                    record[name] = int(found)
        record["cache_fields_present"] = isinstance(
            record["prompt_cache_hit_tokens"], int
        ) and isinstance(record["prompt_cache_miss_tokens"], int)
        record["cache_usage_unknown"] = not record["cache_fields_present"]
    record["usage_keys"] = sorted(
        list(getattr(usage, "model_extra", None) or {})
        or (
            sorted(getattr(usage, "model_dump", lambda: {})())
            if callable(getattr(usage, "model_dump", None))
            else []
        )
    )
    return record


def raw_usage_of(response: Any, *, when: datetime | None = None) -> dict[str, Any]:
    """The vendor usage of one *raw* chat-completion request.

    The SDK's own ``Usage`` type keeps only the standard counters and drops the vendor's cache
    split, so the per-call cache fields are read here, where the provider's response still
    carries them.  A field the provider omits stays unknown, never zero.
    """

    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    stamp = when or datetime.now(timezone.utc)
    record = cache_usage_of(usage, when=stamp)
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
        record[name] = int(value) if isinstance(value, int) else None
    record["request_timestamp_utc"] = stamp.astimezone(timezone.utc).isoformat(timespec="seconds")
    return record


class _CompletionsProxy:
    """Records the raw provider usage of each chat-completion request, then delegates."""

    def __init__(self, inner: Any, sink: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._sink = sink

    async def create(self, *args: Any, **kwargs: Any):
        response = await self._inner.create(*args, **kwargs)
        entry = raw_usage_of(response)
        entry["raw_call_index"] = len(self._sink)
        self._sink.append(entry)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _ChatProxy:
    def __init__(self, inner: Any, sink: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._sink = sink

    @property
    def completions(self) -> _CompletionsProxy:
        return _CompletionsProxy(self._inner.completions, self._sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class ProviderUsageProxy:
    """A provider client that records per-request usage without changing any behaviour."""

    def __init__(self, client: Any, sink: list[dict[str, Any]]) -> None:
        self._client = client
        self._sink = sink

    @property
    def chat(self) -> _ChatProxy:
        return _ChatProxy(self._client.chat, self._sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _render_into_response(response: Any, text: str) -> bool:
    """Put the host-rendered line in place of the model's JSON, so scoring sees the answer.

    Every character of ``text`` comes from the model's own declared fields, put in the frozen
    order by ``render()``; this is the contract's rendering step, not a locally authored answer.
    If the response is not a single message the substitution is refused and the raw proposal is
    kept, so the sample fails closed instead of being scored on something the host invented.
    """

    output = getattr(response, "output", None)
    if not isinstance(output, list) or len(output) != 1:
        return False
    message = output[0]
    content = getattr(message, "content", None)
    if not isinstance(content, list) or len(content) != 1:
        return False
    part = content[0]
    if getattr(part, "type", None) not in ("output_text", "text"):
        return False
    try:
        part.text = str(text)
    except Exception:  # pragma: no cover - defensive: an immutable provider type
        return False
    return True


def _render_into_response_flag() -> str:
    return "render_substituted_into_final_answer"


class StructuredFinalModel(Model):
    """Every arm's model: raw proposal -> frozen render -> at most one metered retry."""

    def __init__(
        self,
        inner: Any,
        *,
        expected_issue: str,
        task_id: str = "",
        required_facts: tuple[str, ...] | list[str] = (),
        cause_token: str = "",
        fix_token: str = "",
    ) -> None:
        self.inner = inner
        self.expected_issue = str(expected_issue)
        self.task_id = str(task_id)
        self.required_facts = tuple(str(fact) for fact in required_facts)
        self.cause_token = str(cause_token)
        self.fix_token = str(fix_token)
        self.calls: list[dict[str, Any]] = []
        self.ledger: list[dict[str, Any]] = []
        self.evidence: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def _record_call(
        self,
        response: Any,
        *,
        phase: str,
        started: datetime,
        attempts_before: int | None,
        raw: str | None,
        verdict: Any,
    ) -> None:
        usage = _usage_of(response)
        attempts_after = _attempts(self.inner)
        entry = {
            "call_index": len(self.calls),
            "phase": phase,
            "task": self.task_id,
            "call_timestamp_utc": started.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0)
            if usage is not None
            else 0,
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0)
            if usage is not None
            else 0,
            "request_attempts": (
                None
                if attempts_before is None or attempts_after is None
                else max(0, attempts_after - attempts_before)
            ),
            "raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw is not None else None,
            "raw_chars": len(raw) if raw is not None else None,
            "render_accepted": bool(verdict.accepted) if verdict is not None else None,
            "render_reason": verdict.reason if verdict is not None else None,
            "is_final_turn": raw is not None,
        }
        entry.update(cache_usage_of(usage, when=started))
        self.calls.append(entry)

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ):
        kwargs = dict(
            previous_response_id=previous_response_id,
            conversation_id=conversation_id,
            prompt=prompt,
        )
        started = datetime.now(timezone.utc)
        before = _attempts(self.inner)
        first = await self.inner.get_response(
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            **kwargs,
        )
        raw = final_text(first)
        first_verdict = render(raw, expected_issue=self.expected_issue) if raw is not None else None
        self._record_call(
            first,
            phase="answer" if raw is not None else "tool_turn",
            started=started,
            attempts_before=before,
            raw=raw,
            verdict=first_verdict,
        )
        if raw is None:
            return first
        entry: dict[str, Any] = {
            "task": self.task_id,
            "expected_issue": self.expected_issue,
            "first_raw_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "first_raw_chars": len(raw),
            "first_render_reason": first_verdict.reason,
            "first_render_accepted": first_verdict.accepted,
            "first_candidate_chars": int(first_verdict.chars),
            "repair_calls": 0,
        }
        if first_verdict.accepted:
            entry.update(self._settle(raw, first_verdict.output, "accepted_first"))
            entry[_render_into_response_flag()] = _render_into_response(first, first_verdict.output)
            self._ledger_append(entry)
            return first

        # One retry, metered like any other request, with tools withheld. The retry sees the
        # filtered context, the original proposal and the host's exact rejection reason.
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
        diagnostic = diagnostic_for(
            first_verdict.reason,
            raw=raw,
            expected_issue=self.expected_issue,
            required_facts=self.required_facts,
            cause_token=self.cause_token,
            fix_token=self.fix_token,
        )
        entry["diagnostic"] = diagnostic
        entry["diagnostic_sha256"] = hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
        entry["diagnostic_carried_in_retry_input"] = True
        repair_input = items + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"{RETRY_INSTRUCTION}\n{diagnostic}"},
        ]
        entry["repair_input_sha256"] = _sha(
            [item if isinstance(item, (str, int, float, bool, type(None))) else str(item) for item in repair_input]
        )
        entry["repair_input_item_count"] = len(repair_input)
        entry["repair_tool_count"] = 0
        started_retry = datetime.now(timezone.utc)
        before_retry = _attempts(self.inner)
        try:
            second = await self.inner.get_response(
                system_instructions,
                repair_input,
                model_settings,
                [],
                output_schema,
                handoffs,
                tracing,
                **kwargs,
            )
        except Exception as error:  # a failed retry is recorded and the first proposal is kept
            entry["repair_error_type"] = type(error).__name__
            entry["repair_calls"] = 1
            entry["accepted"] = False
            entry["output"] = ""
            entry["reason"] = f"retry_error:{type(error).__name__}"
            entry["accounting_complete"] = False
            entry[_render_into_response_flag()] = _render_into_response(first, "")
            self._ledger_append(entry)
            return first
        revised = final_text(second)
        revised_verdict = (
            render(revised, expected_issue=self.expected_issue) if revised is not None else None
        )
        self._record_call(
            second,
            phase="retry",
            started=started_retry,
            attempts_before=before_retry,
            raw=revised,
            verdict=revised_verdict,
        )
        entry["repair_calls"] = 1
        entry["repair_raw_sha256"] = (
            hashlib.sha256(revised.encode("utf-8")).hexdigest() if revised is not None else None
        )
        entry["repair_raw_chars"] = len(revised) if revised is not None else None
        entry["repair_render_reason"] = revised_verdict.reason if revised_verdict else None
        entry["repair_candidate_chars"] = (
            int(revised_verdict.chars) if revised_verdict is not None else None
        )
        entry["repair_candidate_delta_chars"] = (
            int(revised_verdict.chars) - int(entry.get("first_candidate_chars", 0))
            if revised_verdict is not None
            else None
        )
        if revised_verdict is not None and revised_verdict.accepted:
            entry.update(self._settle(revised, revised_verdict.output, "accepted_retry"))
            entry[_render_into_response_flag()] = _render_into_response(second, revised_verdict.output)
            self._ledger_append(entry)
            return second
        entry.update(
            self._settle(
                raw,
                "",
                "retry_rejected:"
                + (revised_verdict.reason if revised_verdict else "retry_returned_no_answer"),
            )
        )
        # Fail closed: a rejected proposal never becomes the answer, so the sample is scored on
        # an empty answer rather than on its own malformed text.
        entry[_render_into_response_flag()] = _render_into_response(first, "")
        self._ledger_append(entry)
        return first

    def _settle(self, raw: str, output: str, reason: str) -> dict[str, Any]:
        verdict = render(raw, expected_issue=self.expected_issue)
        payload = {
            "accepted": bool(output),
            "output": str(output),
            "reason": str(reason),
            "source_chars": len(str(raw)),
            "rendered_chars": len(str(output)) if output else int(verdict.chars),
            "truncated": False,
            "fields_dropped": False,
            "accounting_complete": True,
        }
        if output:
            declared = []
            try:
                declared = list(json.loads(raw).get("facts") or [])
            except Exception:  # pragma: no cover - defensive
                declared = []
            payload["declared_facts_in_output"] = all(fact in output for fact in declared)
            payload["literals_dropped"] = [
                fact for fact in declared if fact not in output
            ]
        return payload

    def _ledger_append(self, entry: dict[str, Any]) -> None:
        self.ledger.append(entry)
        self.evidence.append(
            {
                "task": self.task_id,
                "reason": entry.get("reason"),
                "accepted": entry.get("accepted"),
                "output": entry.get("output"),
                "first_raw_chars": entry.get("first_raw_chars"),
                "repair_raw_chars": entry.get("repair_raw_chars"),
            }
        )

    def stream_response(self, *args, **kwargs):
        return self.inner.stream_response(*args, **kwargs)

    def get_retry_advice(self, request):
        return self.inner.get_retry_advice(request)


# -------------------------------------------------------------------------- SDK  stub
class ScriptedStubModel(Model):
    """Deterministic provider stand-in used by the zero-API wiring and control path.

    It produces the six registered read calls and then one scripted final message, using the
    real Responses item types, so the production path (SDK ``Runner``, the real
    ``call_model_input_filter``, the recorder, the renderer and the retry wrapper) runs
    unchanged with no network access.
    """

    def __init__(
        self,
        case: Any,
        *,
        request_budget: Any | None = None,
        final_text_value: str = "",
        final_text_after_diagnostic: str = "",
        uses_diagnostic: bool = True,
        input_tokens_per_call: int = 1800,
        output_tokens_per_call: int = 24,
        with_cache_fields: bool = True,
    ) -> None:
        self.case = case
        self.request_budget = request_budget
        self.final_text_value = str(final_text_value)
        self.final_text_after_diagnostic = str(final_text_after_diagnostic)
        self.uses_diagnostic = bool(uses_diagnostic)
        self.inputs: list[list[Any]] = []
        self.instructions: list[str] = []
        self.responses: list[ModelResponse] = []
        self.retry_count = 0
        self.diagnostic_seen = False
        self.diagnostics_seen: list[str] = []
        self.input_tokens_per_call = int(input_tokens_per_call)
        self.output_tokens_per_call = int(output_tokens_per_call)
        self.with_cache_fields = bool(with_cache_fields)

    async def get_response(
        self,
        system_instructions,
        input,
        model_settings,
        tools,
        output_schema,
        handoffs,
        tracing,
        *,
        previous_response_id,
        conversation_id,
        prompt,
    ) -> ModelResponse:
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
        serialized = json.dumps(
            [_jsonable(item) for item in items], ensure_ascii=False, default=str
        )
        if DIAGNOSTIC_MARKER in serialized:
            self.diagnostic_seen = True
            marker = serialized.find(DIAGNOSTIC_MARKER)
            self.diagnostics_seen.append(serialized[marker : marker + 400])
        if self.request_budget is not None:
            self.request_budget.consume()
        self.inputs.append(items)
        self.instructions.append(str(system_instructions or ""))
        call_index = len(self.inputs) - 1
        usage = self._usage(items, system_instructions)
        response = ModelResponse(
            output=self._output_for_call(call_index, tools),
            usage=usage,
            response_id=f"stub-{self.case.scenario}-{call_index}",
        )
        self.responses.append(response)
        return response

    def _usage(self, items: list[Any], system_instructions: Any) -> Any:
        payload = json.dumps(
            [_jsonable(item) for item in items], ensure_ascii=False, default=str
        )
        billed = max(1, len(payload) // 4) + max(
            1, len(str(system_instructions or "")) // 4
        )
        fields = dict(
            requests=1,
            input_tokens=billed,
            output_tokens=self.output_tokens_per_call,
            total_tokens=billed + self.output_tokens_per_call,
        )
        if self.with_cache_fields:
            try:
                return Usage(
                    **fields,
                    prompt_cache_hit_tokens=billed // 2,
                    prompt_cache_miss_tokens=billed - billed // 2,
                )
            except Exception:
                # The SDK's usage type rejects the vendor extras here; the cache fields must
                # then be recorded as unknown for the stub, never as zero.
                pass
        return Usage(**fields)

    def _output_for_call(self, call_index: int, tools: Any) -> list[Any]:
        expected = list(self.case.expected_tool_names)
        if call_index < len(expected):
            return [self._tool_call(expected[call_index], call_index)]
        return [self._final_message()]

    def _tool_call(self, name: str, index: int) -> ResponseFunctionToolCall:
        return ResponseFunctionToolCall(
            arguments=json.dumps({}),
            call_id=f"call_{self.case.scenario}_{index}_{name}",
            name=name,
            type="function_call",
        )

    def _final_message(self) -> ResponseOutputMessage:
        # A retry (tools withheld) returns the scripted repair text when the stand-in is told to
        # act on the host's diagnostic, and the same failing text when it is told to ignore it:
        # the positive control can only pass if the diagnostic actually reached the model.
        text = self.final_text_value
        if (
            self.diagnostic_seen
            and self.uses_diagnostic
            and self.final_text_after_diagnostic
        ):
            text = self.final_text_after_diagnostic
        return ResponseOutputMessage(
            id=f"msg_{self.case.scenario}",
            content=[ResponseOutputText(annotations=[], text=text, type="output_text")],
            role="assistant",
            status="completed",
            type="message",
        )

    def stream_response(self, *args, **kwargs):
        async def empty_stream():
            if False:  # pragma: no cover - generator shaped like the SDK's
                yield None

        return empty_stream()

    def get_retry_advice(self, request):
        return None


def _jsonable(item: Any) -> Any:
    if isinstance(item, dict):
        return item
    model_dump = getattr(item, "model_dump", None)
    return model_dump(exclude_none=True) if callable(model_dump) else item


# ------------------------------------------------------------------------------ wiring
@contextmanager
def inner_model_wiring(factory: Callable[..., Any]):
    """Replace the innermost provider model (stub path only). Enter before evidence wiring."""

    original = base.RecordingRetryModel
    base.RecordingRetryModel = factory
    try:
        yield factory
    finally:
        base.RecordingRetryModel = original


@contextmanager
def structured_wiring(
    output_root: Path,
    *,
    contracts: dict[str, dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    check_request_cap: bool = False,
):
    """Install the v16 agent contract, the metered retry wrapper and the per-sample report.

    Entered *after* the evidence wiring so that the structured wrapper is the outermost model
    and therefore sees every provider call, including the retry.

    ``contracts`` maps a task id to its frozen ``issue`` value, ``pattern`` (the frozen regex)
    and ``facts`` (the required fact strings); an unknown task resolves to an empty issue, which
    the renderer rejects rather than accepts.
    """

    original_agent = base.Agent
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    original_filter = base._build_filter
    active: dict[str, Any] = state if state is not None else {}
    table = {str(key): dict(value) for key, value in (contracts or {}).items()}
    root = Path(output_root)

    def current_task() -> str:
        key = active.get("key")
        return str(key[0]) if key else ""

    def current_contract() -> dict[str, Any]:
        return table.get(current_task(), {})

    def agent_factory(*args: Any, **kwargs: Any):
        # Option A, registered in the freeze amendment: the agent carries no ``output_type``,
        # because on agents 0.22.3 that setting makes the chat-completions model send
        # ``response_format`` unconditionally and this deployment answers
        # "This response_format type is unavailable now" with HTTP 400 before any tool call.
        # The JSON contract in the prompt is unchanged; enforcement moves to the host, where
        # ``render()`` is the final-answer path and the only validator.
        kwargs.pop("output_type", None)
        adapted, applied, unapplied = adapt_instructions(str(kwargs.get("instructions") or ""))
        active.setdefault("prompt_adaptations", {"applied": [], "unapplied": []})
        for entry in applied:
            if entry not in active["prompt_adaptations"]["applied"]:
                active["prompt_adaptations"]["applied"].append(entry)
        for entry in unapplied:
            if entry not in active["prompt_adaptations"]["unapplied"]:
                active["prompt_adaptations"]["unapplied"].append(entry)
        kwargs["instructions"] = adapted + " " + STRUCTURED_INSTRUCTION
        return original_agent(*args, **kwargs)

    def model_factory(*args: Any, **kwargs: Any):
        contract = current_contract()
        wrapper = StructuredFinalModel(
            original_model(*args, **kwargs),
            expected_issue=str(contract.get("issue", "")),
            task_id=current_task(),
            required_facts=tuple(contract.get("facts", ()) or ()),
            cause_token=str(contract.get("cause_token", "")),
            fix_token=str(contract.get("fix_token", "")),
        )
        active["structured"] = wrapper
        return wrapper

    def build_filter(method: str, *, case: Any = None, request_budget: Any = None, **kwargs: Any):
        # The per-sample key is published before delegating, so the model factory (built inside
        # the frozen run_case, after this call) can resolve the sample's contract.
        if case is not None:
            active["key"] = (case.scenario, case.repeat, method)
        if check_request_cap and request_budget is not None:
            remaining = int(request_budget.limit) - int(request_budget.used)
            if remaining < MIN_SAMPLE_REQUESTS:
                raise RequestCapReached(
                    "the frozen request cap leaves fewer than the seven requests a sample "
                    f"needs ({request_budget.used}/{request_budget.limit} used); stopping"
                )
        return original_filter(
            method, case=case, request_budget=request_budget, **kwargs
        )

    async def run_case(case: Any, *, method: str, client: Any = None, **kwargs: Any):
        sink = active.setdefault("raw_usage", [])
        del sink[:]
        if client is not None:
            kwargs["client"] = ProviderUsageProxy(client, sink)
        row = await original_case(case, method=method, **kwargs)
        wrapper = active.get("structured")
        if wrapper is None:
            raise RuntimeError("the structured model wrapper was not installed for this sample")
        issue = str((table.get(str(case.scenario)) or {}).get("issue", ""))
        pattern = str((table.get(str(case.scenario)) or {}).get("pattern", ""))
        facts = tuple((table.get(str(case.scenario)) or {}).get("facts", ()) or ())
        entry = wrapper.ledger[-1] if wrapper.ledger else None
        # The four conditions and the render verdict are read from the host's own render ledger,
        # never from ``final_output``: a rejected sample's final_output is the model's raw JSON,
        # which must not be scored as if it were an answer.
        accepted_output = str(entry.get("output") or "") if entry else ""
        row["v16_raw_final_output"] = str(row.get("final_output") or "")
        row["v16_structured_schema"] = STRUCTURED_SCHEMA
        row["v16_renderer_module"] = RENDERER_MODULE
        row["v16_expected_issue"] = issue
        row["v16_host_enforces_schema"] = True
        row["v16_agent_output_type"] = None
        row["render_final_answer_matches_rendered_line"] = bool(accepted_output) and (
            str(row.get("final_output") or "") == accepted_output
        )
        row["prompt_adaptations"] = {
            "applied": list((active.get("prompt_adaptations") or {}).get("applied") or []),
            "unapplied": list((active.get("prompt_adaptations") or {}).get("unapplied") or []),
            "expected_applied": [old for old, _ in PROMPT_REPLACEMENTS],
        }
        row["render_ledger"] = list(wrapper.ledger)
        row["render_repair_calls"] = int(entry.get("repair_calls", 0)) if entry else 0
        row["render_diagnostic"] = str((entry or {}).get("diagnostic", ""))
        row["render_diagnostic_carried"] = bool(
            (entry or {}).get("diagnostic_carried_in_retry_input")
        )
        row["render_accepted"] = bool(entry.get("accepted")) if entry else False
        row["render_reason"] = str(entry.get("reason", "")) if entry else "no_final_answer"
        row["render_source_chars"] = int(entry.get("source_chars", 0)) if entry else 0
        row["render_rendered_chars"] = int(entry.get("rendered_chars", 0)) if entry else 0
        row["render_had_candidate"] = bool(entry) and (
            int(entry.get("source_chars", 0) or 0) > 0
        )
        row["render_substituted_into_final_answer"] = bool(
            entry.get(_render_into_response_flag()) if entry else False
        )
        row["render_truncated"] = False
        row["render_fields_dropped"] = bool(
            entry.get("literals_dropped") if entry else False
        )
        row["render_conditions"] = conditions(
            accepted_output, issue, str(pattern), tuple(facts or ())
        )
        report = render_report(
            source_chars=row["render_source_chars"],
            output=accepted_output,
            issue=issue,
            pattern=str(pattern),
            facts=tuple(facts or ()),
        )
        if not report["render_accepted"]:
            # The freeze reports the rejected candidate's own length (0 when the rejection is
            # structural), never the length of a truncated answer: nothing is ever truncated.
            report["rendered_chars"] = int(row["render_rendered_chars"])
        row["render_report"] = report
        calls = [dict(entry) for entry in wrapper.calls]
        raw = [dict(entry) for entry in sink]
        if raw and len(raw) == len(calls):
            # One raw request per logical model call: the vendor's own cache split is copied in.
            for call, entry in zip(calls, raw):
                for name in (
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                    "cache_fields_present",
                    "cache_usage_unknown",
                ):
                    call[name] = entry[name]
                call["cache_source"] = "raw_provider_usage_index_matched"
        else:
            for call in calls:
                call.setdefault("cache_source", "sdk_usage")
        row["raw_provider_usage"] = raw
        row["raw_usage_pairing"] = (
            "index_matched"
            if raw and len(raw) == len(calls)
            else f"count_mismatch:{len(raw)}_raw_vs_{len(calls)}_logical"
        )
        row["provider_calls"] = calls
        row["provider_cache"] = aggregate_cache(calls)
        row["provider_cache_unknown"] = all(
            entry.get("cache_usage_unknown") for entry in calls
        )
        row["all_arm_requests_recorded"] = int(row.get("api_request_attempts", 0) or 0)
        evidence_rel = f"render-evidence/{case.scenario}-{case.repeat}-{method}.jsonl"
        evidence_path = root / evidence_rel
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(
            "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in wrapper.evidence
            ),
            encoding="utf-8",
        )
        row["render_evidence_file"] = evidence_rel
        return row

    base.Agent = agent_factory
    base.RecordingRetryModel = model_factory
    base.run_case = run_case
    base._build_filter = build_filter
    try:
        yield active
    finally:
        base.Agent = original_agent
        base.RecordingRetryModel = original_model
        base.run_case = original_case
        base._build_filter = original_filter


__all__ = [
    "CONDITION_NAMES",
    "MIN_SAMPLE_REQUESTS",
    "PER_SAMPLE_FIELDS",
    "PROMPT_REPLACEMENTS",
    "RENDERER_MODULE",
    "RETRY_BUDGET_PER_SAMPLE",
    "RETRY_INSTRUCTION",
    "RequestCapReached",
    "STRUCTURED_INSTRUCTION",
    "STRUCTURED_SCHEMA",
    "ProviderUsageProxy",
    "ScriptedStubModel",
    "StructuredFinalModel",
    "adapt_instructions",
    "cache_usage_of",
    "conditions",
    "inner_model_wiring",
    "raw_usage_of",
    "render_report",
    "self_test",
    "structured_answer_type",
    "structured_wiring",
]
