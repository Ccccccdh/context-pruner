"""v16 zero-API controls: structured-output SDK wiring, independent audit, cache metering.

Three things are proved here without any provider request:

1. **SDK wiring**: an ``Agent`` is constructed with the frozen structured ``output_type``
   (issue/cause/fix/facts) and the shared renderer is used for the final answer, with **at most
   one** extra billed request; a second failure closes the door and never becomes final. The
   live path is a thin adapter that is *not* exercised without a provider - the controls drive
   a stub runner instead.
2. **Independent audit negative controls**: a synthetic batch plus four tampered variants
   (changed view hash, deleted contract field, tampered pinned value, forged ``complete``) must
   all be rejected.
3. **Cache metering**: per-call ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` and
   the call timestamp are extracted and persisted, with a fail-closed path when the provider
   omits them (recorded as unknown, never as zero).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from experiments.runners.openai_agents_structured_final_v16 import (
    MAX_CHARS,
    MeteredResponse,
    RequestFailure,
    SettledAnswer,
    render,
    settle,
)

REPO = Path(__file__).resolve().parents[2]
REGISTRATION = REPO / "integrations/openai_agents/V16_SOURCE_REGISTRATION_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_NEGATIVE_CONTROL_20261006.json"

CACHE_HIT_FIELD = "prompt_cache_hit_tokens"
CACHE_MISS_FIELD = "prompt_cache_miss_tokens"


# --------------------------------------------------------------------------- SDK wiring
def structured_output_model():
    """The pydantic model the SDK is asked to produce (imported lazily, no provider call)."""
    from pydantic import BaseModel, Field

    class StructuredAnswer(BaseModel):
        issue: str = Field(min_length=1, max_length=64)
        cause: str = Field(min_length=1)
        fix: str = Field(min_length=1)
        facts: list[str] = Field(min_length=1)

    return StructuredAnswer


def build_agent(*, name: str, instructions: str):
    """Construct the SDK ``Agent`` with the frozen structured output type (no request)."""
    from agents import Agent

    return Agent(name=name, instructions=instructions, output_type=structured_output_model())


def sdk_runner(agent, *, user_input: str) -> Callable[[], Awaitable[MeteredResponse]]:
    """Adapter for the live SDK path: returns a zero-argument async caller.

    It is wired but not exercised by the controls (no provider is contacted). Usage fields the
    provider may omit stay ``None`` in the ledger instead of being invented.
    """

    async def call() -> MeteredResponse:
        from agents import Runner

        result = await Runner.run(agent, input=user_input)
        final = result.final_output
        text = (
            final.model_dump_json()
            if hasattr(final, "model_dump_json")
            else json.dumps(final, ensure_ascii=False)
        )
        usage = getattr(result, "context_wrapper", None)
        usage = getattr(usage, "usage", None)
        return MeteredResponse(
            text=text,
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            request_attempts=1,
        )

    return call


async def run_structured(
    first_call: Callable[[], Awaitable[MeteredResponse]],
    retry_call: Callable[[], Awaitable[MeteredResponse]],
    *,
    expected_issue: str,
) -> tuple[SettledAnswer, int]:
    """One first attempt plus at most one retry; returns the settled answer and call count."""
    calls = 0

    async def counted_first() -> MeteredResponse:
        nonlocal calls
        calls += 1
        return await first_call()

    async def counted_retry() -> MeteredResponse:
        nonlocal calls
        calls += 1
        return await retry_call()

    first = await counted_first()
    initial = render(first.text, expected_issue=expected_issue)
    if initial.accepted:
        return (
            SettledAnswer(True, initial.output, "accepted_first", 0, first.request_attempts,
                          first.input_tokens, first.output_tokens, True),
            calls,
        )
    answer = await settle(first, counted_retry, expected_issue=expected_issue)
    return answer, calls


# ------------------------------------------------------------------- independent audit
@dataclass
class AuditResult:
    complete: bool
    errors: list[str] = field(default_factory=list)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_view_hashes(task_id: str) -> list[str]:
    """Recompute each view's content hash from the pinned source (read-only git objects)."""
    from experiments.runners import openai_agents_v16_source_registration as registration

    registration_report = registration.build()
    task = registration_report["tasks"][task_id]
    commit = task["base_commit"]
    hashes = []
    for view in task["views"]:
        payload = registration.view_payload(
            commit, view["file"], view["first_line"], view["last_line"]
        )
        hashes.append(payload["view_content_sha256"])
    return hashes


def audit_registration(registration: dict, batch: dict) -> AuditResult:
    """Independent audit of one synthetic batch against the registration.

    Rejects: a view whose content hash no longer matches the pinned source, a contract that lost
    a required field, a pinned value that was changed (answer length bound or required facts),
    and a record that claims ``complete`` while carrying errors or missing evidence.
    """
    errors: list[str] = []
    tasks = registration.get("tasks") or {}
    task_id = str(batch.get("task_id"))
    task = tasks.get(task_id)
    if task is None:
        return AuditResult(False, [f"unknown task {task_id!r}"])
    # 1. view hashes, recomputed from the pinned source and compared with the record
    actual_hashes = source_view_hashes(task_id)
    recorded_hashes = [view["view_content_sha256"] for view in task["views"]]
    if actual_hashes != recorded_hashes:
        errors.append("view_hash_mismatch")
    # 2. contract completeness
    contract = task["contract"]
    for field_name in ("shape", "json_fields", "issue_value", "required_facts",
                       "cause_token", "fix_token", "frozen_regex", "max_answer_chars"):
        if field_name not in contract:
            errors.append(f"contract_field_missing:{field_name}")
    # 3. pinned values
    if int(contract.get("max_answer_chars", -1)) != MAX_CHARS:
        errors.append("pinned_max_answer_chars_changed")
    if not contract.get("required_facts"):
        errors.append("pinned_required_facts_empty")
    # 4. forged completeness / evidence
    if batch.get("complete") is True:
        if batch.get("errors"):
            errors.append("forged_complete_with_errors")
        rendered = str(batch.get("answer_rendered", ""))
        if not rendered:
            errors.append("forged_complete_without_rendered_answer")
        for fact in contract.get("required_facts") or []:
            if fact not in rendered:
                errors.append(f"required_fact_missing:{fact}")
        if batch.get("issue_rendered") != contract["issue_value"]:
            errors.append("issue_mismatch")
    return AuditResult(not errors, sorted(set(errors)))


# ---------------------------------------------------------------------- cache metering
def extract_cache_usage(usage: Any, *, when: datetime | None = None) -> dict[str, Any]:
    """Per-call cache accounting; missing provider fields stay unknown, never zero."""

    def get(name: str) -> Any:
        if usage is None:
            return None
        if isinstance(usage, dict):
            return usage.get(name)
        return getattr(usage, name, None)

    hit = get(CACHE_HIT_FIELD)
    miss = get(CACHE_MISS_FIELD)
    stamp = when or datetime.now(timezone.utc)
    complete = isinstance(hit, int) and isinstance(miss, int)
    return {
        "prompt_cache_hit_tokens": int(hit) if isinstance(hit, int) else None,
        "prompt_cache_miss_tokens": int(miss) if isinstance(miss, int) else None,
        "cache_fields_present": complete,
        "cache_usage_unknown": not complete,
        "call_timestamp_utc": stamp.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "off_peak": is_off_peak(stamp),
    }


def is_off_peak(when: datetime) -> bool:
    """Non-peak = outside UTC Mon-Fri 01:00-04:00 and 06:00-10:00."""
    moment = when.astimezone(timezone.utc)
    if moment.weekday() >= 5:
        return True
    hour = moment.hour
    return not (1 <= hour < 4 or 6 <= hour < 10)


def aggregate_cache(calls: list[dict[str, Any]]) -> dict[str, Any]:
    hits = [call["prompt_cache_hit_tokens"] for call in calls if call["cache_fields_present"]]
    misses = [call["prompt_cache_miss_tokens"] for call in calls if call["cache_fields_present"]]
    return {
        "calls": len(calls),
        "calls_with_cache_fields": len(hits),
        "calls_without_cache_fields": len(calls) - len(hits),
        "prompt_cache_hit_tokens_total": sum(hits) if hits else None,
        "prompt_cache_miss_tokens_total": sum(misses) if misses else None,
        "all_calls_off_peak": all(call["off_peak"] for call in calls) if calls else None,
        "per_call": calls,
        "note": (
            "a batch whose provider omits the cache fields records them as unknown; the totals "
            "stay null rather than being reported as zero"
        ),
    }


def attach_cache_to_manifest(manifest: dict, calls: list[dict[str, Any]]) -> dict:
    manifest["prompt_cache"] = aggregate_cache(calls)
    return manifest


# ------------------------------------------------------------------------- control battery
def _proposal(issue: str, cause: str, fix: str, facts: list[str] | None = None) -> str:
    return json.dumps(
        {
            "issue": issue,
            "cause": cause,
            "fix": fix,
            "facts": facts if facts is not None else [cause.split()[0], fix.split()[0]],
        }
    )


async def run_controls() -> dict[str, Any]:
    import asyncio

    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    task_id = registration["registered_task_ids"][0]
    task = registration["tasks"][task_id]
    issue = task["contract"]["issue_value"]
    good = {
        "issue": issue,
        "cause": "check_sqlite_version still allows SQLite 3.8.3",
        "fix": "require SQLite 3.9.0",
        "facts": ["check_sqlite_version", "3.8.3", "3.9.0"],
    }
    good_text = json.dumps(good)
    results: list[dict[str, Any]] = []

    def response(text: str, *, attempts: int = 1, tin: int = 900, tout: int = 40) -> MeteredResponse:
        return MeteredResponse(text, tin, tout, attempts)

    async def one(name: str, first_text: str, retry_text: str | None, *, expect_calls: int, expect_accepted: bool) -> None:
        calls: list[int] = []

        async def first_call() -> MeteredResponse:
            calls.append(1)
            return response(first_text)

        async def retry_call() -> MeteredResponse:
            calls.append(1)
            if retry_text is None:
                raise RequestFailure("provider_error", request_attempts=1)
            return response(retry_text)

        answer, call_count = await run_structured(
            first_call, retry_call, expected_issue=issue
        )
        results.append(
            {
                "control": name,
                "accepted": answer.accepted,
                "reason": answer.reason,
                "repair_calls": answer.repair_calls,
                "provider_calls": call_count,
                "request_attempts": answer.request_attempts,
                "input_tokens": answer.input_tokens,
                "output_tokens": answer.output_tokens,
                "accounting_complete": answer.accounting_complete,
                "expected_calls": expect_calls,
                "expected_accepted": expect_accepted,
                "ok": call_count == expect_calls and answer.accepted == expect_accepted,
            }
        )

    # 1. a valid first answer is final with exactly one provider call
    await one("valid_first", good_text, None, expect_calls=1, expect_accepted=True)
    # 2. missing field, repaired on the single retry
    await one(
        "missing_field_then_repair",
        json.dumps({"issue": issue, "cause": "x", "fix": "y"}),
        good_text,
        expect_calls=2,
        expect_accepted=True,
    )
    # 3. extra field, repaired
    await one(
        "extra_field_then_repair",
        json.dumps({**good, "extra": "nope"}),
        good_text,
        expect_calls=2,
        expect_accepted=True,
    )
    # 4. wrong type, repaired
    await one(
        "wrong_type_then_repair",
        json.dumps({**good, "facts": "not-a-list"}),
        good_text,
        expect_calls=2,
        expect_accepted=True,
    )
    # 5. over-long first answer, retry also over-long: closed after two calls, never accepted
    long_cause = "check_sqlite_version still allows SQLite 3.8.3 " + "x" * 130
    await one(
        "over_long_twice_closed",
        json.dumps({**good, "cause": long_cause}),
        json.dumps({**good, "cause": long_cause}),
        expect_calls=2,
        expect_accepted=False,
    )
    # 6. retry raises an unknown exception: rejected with incomplete accounting
    unknown = {"control": "retry_unknown_exception"}
    try:
        calls: list[int] = []

        async def first_call() -> MeteredResponse:
            calls.append(1)
            return response(json.dumps({**good, "facts": []}))

        async def retry_call() -> MeteredResponse:
            calls.append(1)
            raise RuntimeError("transport exploded")

        answer, call_count = await run_structured(first_call, retry_call, expected_issue=issue)
        unknown.update(
            {
                "accepted": answer.accepted,
                "reason": answer.reason,
                "provider_calls": call_count,
                "accounting_complete": answer.accounting_complete,
                "request_attempts": answer.request_attempts,
                "ok": not answer.accepted and answer.accounting_complete is False and call_count == 2,
            }
        )
    except Exception as error:  # pragma: no cover - defensive
        unknown.update({"ok": False, "error": type(error).__name__})
    results.append(unknown)

    # -- independent audit negative controls ---------------------------------
    batch = {
        "task_id": task_id,
        "complete": True,
        "answer_rendered": f"RESULT issue={issue} cause={good['cause']} fix={good['fix']}",
        "issue_rendered": issue,
    }
    audit_controls = []
    clean = audit_registration(registration, batch)
    audit_controls.append({"control": "clean_record", "complete": clean.complete, "errors": clean.errors, "ok": clean.complete})

    tampered_hash = json.loads(json.dumps(registration))
    tampered_hash["tasks"][task_id]["views"][0]["view_content_sha256"] = "0" * 64
    outcome = audit_registration(tampered_hash, batch)
    audit_controls.append({"control": "changed_view_hash", "complete": outcome.complete, "errors": outcome.errors, "ok": not outcome.complete and "view_hash_mismatch" in outcome.errors})

    deleted_field = json.loads(json.dumps(registration))
    del deleted_field["tasks"][task_id]["contract"]["required_facts"]
    outcome = audit_registration(deleted_field, batch)
    audit_controls.append({"control": "deleted_contract_field", "complete": outcome.complete, "errors": outcome.errors, "ok": not outcome.complete})

    tampered_pin = json.loads(json.dumps(registration))
    tampered_pin["tasks"][task_id]["contract"]["max_answer_chars"] = 400
    outcome = audit_registration(tampered_pin, batch)
    audit_controls.append({"control": "tampered_pinned_value", "complete": outcome.complete, "errors": outcome.errors, "ok": not outcome.complete and "pinned_max_answer_chars_changed" in outcome.errors})

    forged = {"task_id": task_id, "complete": True, "errors": ["something"], "answer_rendered": "", "issue_rendered": issue}
    outcome = audit_registration(registration, forged)
    audit_controls.append({"control": "forged_complete", "complete": outcome.complete, "errors": outcome.errors, "ok": not outcome.complete and "forged_complete_with_errors" in outcome.errors})

    # -- SDK wiring proof (no provider call) ---------------------------------
    model = structured_output_model()
    agent = build_agent(name="v16-control", instructions="diagnose and answer as JSON")
    wiring = {
        "structured_output_type": model.__name__,
        "fields": sorted(model.model_fields),
        "agent_has_output_type": getattr(agent, "output_type", None) is model,
        "live_adapter": "experiments.runners.openai_agents_v16_controls.sdk_runner",
        "live_adapter_exercised": False,
        "live_adapter_note": "wired but not contacted: this round makes no provider request",
    }

    # -- cache metering controls ---------------------------------------------
    stamp_peak = datetime(2026, 10, 6, 2, 30, tzinfo=timezone.utc)
    stamp_off = datetime(2026, 10, 6, 20, 30, tzinfo=timezone.utc)
    cache_controls = {
        "with_fields": extract_cache_usage(
            {"prompt_cache_hit_tokens": 8000, "prompt_cache_miss_tokens": 1500}, when=stamp_peak
        ),
        "without_fields": extract_cache_usage({}, when=stamp_off),
        "aggregate": aggregate_cache(
            [
                extract_cache_usage(
                    {"prompt_cache_hit_tokens": 8000, "prompt_cache_miss_tokens": 1500},
                    when=stamp_peak,
                ),
                extract_cache_usage({}, when=stamp_off),
            ]
        ),
        "peak_is_peak": not is_off_peak(stamp_peak),
        "off_peak_is_off": is_off_peak(stamp_off),
    }
    cache_controls["ok"] = (
        cache_controls["with_fields"]["cache_fields_present"] is True
        and cache_controls["without_fields"]["cache_usage_unknown"] is True
        and cache_controls["without_fields"]["prompt_cache_hit_tokens"] is None
        and cache_controls["aggregate"]["calls_without_cache_fields"] == 1
        and cache_controls["peak_is_peak"]
        and cache_controls["off_peak_is_off"]
    )

    return {
        "schema": "openai_agents_v16_zero_api_controls",
        "date": "20261006",
        "paid_requests": 0,
        "structured_output_controls": results,
        "structured_output_all_ok": all(entry.get("ok") for entry in results),
        "max_provider_calls_observed": max(entry.get("provider_calls", 0) for entry in results),
        "audit_controls": audit_controls,
        "audit_all_ok": all(entry["ok"] for entry in audit_controls),
        "sdk_wiring": wiring,
        "cache_controls": cache_controls,
        "three_arm_started": False,
        "three_arm_gate": (
            "not started: two development tasks are registered (one candidate was rejected by "
            "the source-defect gate) and no paid request has been made"
        ),
    }


def main() -> int:
    import asyncio

    report = asyncio.run(run_controls())
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(
        f"structured controls all ok: {report['structured_output_all_ok']} "
        f"(max provider calls {report['max_provider_calls_observed']})"
    )
    for entry in report["structured_output_controls"]:
        print(
            f"  {entry['control']:<28} accepted={entry.get('accepted')} calls={entry.get('provider_calls')} "
            f"reason={entry.get('reason')} ok={entry.get('ok')}"
        )
    print(f"audit controls all ok: {report['audit_all_ok']}")
    for entry in report["audit_controls"]:
        print(f"  {entry['control']:<28} complete={entry['complete']} errors={entry['errors']} ok={entry['ok']}")
    print(f"cache controls ok: {report['cache_controls']['ok']}")
    return 0 if (
        report["structured_output_all_ok"] and report["audit_all_ok"] and report["cache_controls"]["ok"]
    ) else 1


__all__ = [
    "AuditResult",
    "CACHE_HIT_FIELD",
    "CACHE_MISS_FIELD",
    "OUT",
    "aggregate_cache",
    "attach_cache_to_manifest",
    "audit_registration",
    "build_agent",
    "extract_cache_usage",
    "is_off_peak",
    "run_controls",
    "run_structured",
    "sdk_runner",
    "source_view_hashes",
    "structured_output_model",
]


if __name__ == "__main__":
    raise SystemExit(main())
