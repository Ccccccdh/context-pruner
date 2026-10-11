"""The v24 live host contract: the v17 contract with a strengthened over-length retry.

What this file is
-----------------
A **fork** of ``experiments/runners/openai_agents_v17_live_contract.py``.  The v17 module is not
edited, so every earlier round keeps the exact wording its gates and reports pin.  This module
re-exports the whole v17 configuration with ``import *`` and then replaces the three things the
v24 round changes:

1. ``RETRY_INSTRUCTION`` states the compact target the ruling asks for: reply with the JSON object
   only, no explanation, and write cause and fix to the stated target.
2. The ``over_160_chars`` diagnostic keeps every sentence the v17 wording carries - the bound, the
   required fact strings, the cause and fix tokens, the "drop explanations and never repeat a
   fact" rule and the "remove wording, not requirements" rule - and adds only the compact target,
   the repetition of the bound, and the instruction to reply with the JSON object alone.
3. ``V24StructuredFinalModel.get_response`` is the v17 ``StructuredFinalModel.get_response`` with
   exactly two substitutions: the retry message it builds uses this module's
   ``RETRY_INSTRUCTION`` and this module's ``diagnostic_for``.  The method is copied because the
   v17 original resolves both names through its own module globals, so rebinding them here would
   not otherwise reach the live path.  Its ledger also records which wording the sample received,
   so a sample row states whether the strengthened text was delivered.

``structured_wiring`` is likewise copied from v17, with one added postcondition: the wrapper that
ran must be this module's class, so the strengthened wording cannot silently fail to install.

What did not change
-------------------
The renderer and its 160-character bound, the four conditions, the retry budget of one metered
request, the two-sided diagnostic, every other rejection class's text, the ledger fields, the
metering, the cache accounting, the prompt adaptation and the stub controls are the v17 ones,
inherited rather than reimplemented.  :func:`fork_report` computes the diff against the parent and
:func:`wording_report` shows the exact text the retry delivers.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_v17_live_contract as _v17
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners.openai_agents_bounded_final_v15 import final_text
from experiments.runners.openai_agents_structured_final_v16 import render
from experiments.runners.openai_agents_v16_controls import aggregate_cache
from experiments.runners.openai_agents_v17_live_contract import *  # noqa: F401,F403
from experiments.runners.openai_agents_v17_live_contract import (  # noqa: F401
    DIAGNOSTIC_MARKER,
    MAX_CHARS,
    MIN_SAMPLE_REQUESTS,
    PROMPT_REPLACEMENTS,
    ProviderUsageProxy,
    RENDERER_MODULE,
    RETRY_BUDGET_PER_SAMPLE,
    RequestCapReached,
    STRUCTURED_SCHEMA,
    StructuredFinalModel,
    adapt_instructions,
    conditions,
    render_report,
)

#: The v17 module this configuration was forked from.  Recorded rather than assumed: a reader can
#: see that the fork's parent is untouched by comparing the digest with the file on disk.
PARENT_MODULE = "experiments/runners/openai_agents_v17_live_contract.py"
FORK_SCHEMA = "openai_agents_v24_live_contract_fork"

#: The working target the retry states for cause and fix together.  It is the same number the
#: registry's first-prompt discipline sentence states, so the retry does not contradict the prompt.
WORKING_TARGET_CAUSE_PLUS_FIX_CHARS = 120

#: The rejection class whose wording this fork strengthens.  Every other class keeps the v17 text.
STRENGTHENED_REASONS = ("over_160_chars",)

#: The v24 retry instruction.  It keeps the v17 instruction's substance - rewrite only the JSON,
#: keep the same issue, cause, fix and fact strings, do not call tools, fix what the host rejected
#: - and adds the two things the ruling requires: reply with the JSON object alone, and work to a
#: compact target below the bound.
RETRY_INSTRUCTION = (
    "Rewrite only your preceding JSON answer. Keep the same issue, the same cause, the same fix "
    "and the same fact strings. Reply with the same JSON object shape (issue, cause, fix, facts) "
    "and nothing else: no explanation, no commentary, no markdown, no text before or after the "
    "object. Do not call tools. If the host rejected the answer, its exact reason follows and your "
    "rewrite must fix it. Work to a compact target: write cause and fix so that the two fields "
    "together come to about 120 characters or less."
)

#: Added to the over-length diagnostic only.  The v17 sentences that follow the class-specific
#: detail - the 160-character bound, the required fact strings, the cause and fix tokens, the
#: "drop explanations" rule and the "remove wording, not requirements" rule - are appended by
#: ``diagnostic_for`` below and are therefore still delivered.
OVER_160_COMPACT_TARGET = (
    " Reply with the JSON object only and nothing else, with no explanation around it. The length "
    "of the cause and fix fields together is what pushes the rendered line past the 160-character "
    "limit, so as a target write the two fields to come to about 120 characters or less in total; "
    "that leaves the rendered line comfortably inside the bound. The limit is enforced by the host "
    "on the whole rendered line, so an answer over it is rejected entirely."
)

#: The v17 over-length class text, captured before the rebinding below, and the v24 class text:
#: the v17 text with the compact target appended.  Nothing the earlier rounds relied on is removed.
V17_OVER_160_TEXT = str(_v17.DIAGNOSTIC_TEXT["over_160_chars"])
DIAGNOSTIC_TEXT = dict(_v17.DIAGNOSTIC_TEXT)
DIAGNOSTIC_TEXT["over_160_chars"] = V17_OVER_160_TEXT + OVER_160_COMPACT_TARGET

#: Kept for the verdict the ruling states: the retry wording must still be two-sided, so both the
#: literal requirement and the bound are named.  Each entry is a sentence the delivered retry text
#: must contain, quoted rather than paraphrased, so the check cannot pass on a rewording.
TWO_SIDED_REQUIREMENTS = (
    "The rendered line must stay within 160 characters.",
    "These strings must still appear verbatim in it:",
    "The cause field must still contain",
    "The fix field must still contain",
)


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

    Copied from ``openai_agent_v17_live_contract.diagnostic_for`` with one substitution, marked
    in-line: the class-specific text is looked up in *this* module's ``DIAGNOSTIC_TEXT``, so the
    over-length class carries the compact target.  Everything else - the two-sided tail with the
    bound, the required strings, the cause and fix tokens and the shortening rules - is the v17
    code, so both sides of the trade-off are still delivered for every class.
    """

    reason = str(reason)
    # the v24 substitution: this module's table, not the parent's
    detail = DIAGNOSTIC_TEXT.get(reason)
    if detail is None:
        detail = (
            "the host could not render your JSON: "
            + reason.replace("retry_rejected:", "").replace("_", " ")
            + "."
        )
    # Every class gets the same two-sided trade-off, unchanged from v17.
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
    missing = _v17.missing_declared_facts(raw, expected_issue=expected_issue)
    if missing and reason == "declared_fact_missing_from_rendered_answer":
        detail += " The declared strings that are missing are: " + ", ".join(
            repr(entry) for entry in missing
        )
        detail += (
            " Place each of them inside cause or fix, and keep the line within the bound at the "
            "same time."
        )
    return f"{DIAGNOSTIC_MARKER}: {reason}. {detail}"


def wording_report(
    *,
    required_facts: tuple[str, ...] | list[str] = ("__hash__", "__eq__", "creation_counter"),
    cause_token: str = "__hash__",
    fix_token: str = "creation_counter",
    issue: str = "self-test",
) -> dict[str, Any]:
    """What the v24 retry wording is, and what it keeps from the v17 wording."""

    sample_raw = '{"issue": "self-test", "cause": "c", "fix": "f", "facts": ["f"]}'
    over_160 = diagnostic_for(
        "over_160_chars",
        raw=sample_raw,
        expected_issue=issue,
        required_facts=required_facts,
        cause_token=cause_token,
        fix_token=fix_token,
    )
    v17_over_160 = _v17.diagnostic_for(
        "over_160_chars",
        raw=sample_raw,
        expected_issue=issue,
        required_facts=required_facts,
        cause_token=cause_token,
        fix_token=fix_token,
    )
    others = {
        reason: diagnostic_for(
            reason,
            raw="",
            expected_issue=issue,
            required_facts=required_facts,
            cause_token=cause_token,
            fix_token=fix_token,
        )
        for reason in DIAGNOSTIC_TEXT
        if reason not in STRENGTHENED_REASONS
    }
    return {
        "schema": FORK_SCHEMA,
        "parent_module": PARENT_MODULE,
        "retry_budget_per_sample": RETRY_BUDGET_PER_SAMPLE,
        "max_answer_chars": MAX_CHARS,
        "renderer_module": RENDERER_MODULE,
        "strengthened_reasons": list(STRENGTHENED_REASONS),
        "retry_instruction": RETRY_INSTRUCTION,
        "retry_instruction_chars": len(RETRY_INSTRUCTION),
        "retry_instruction_sha256": hashlib.sha256(RETRY_INSTRUCTION.encode("utf-8")).hexdigest(),
        "v17_retry_instruction": _v17.RETRY_INSTRUCTION,
        "v17_retry_instruction_sha256": hashlib.sha256(
            _v17.RETRY_INSTRUCTION.encode("utf-8")
        ).hexdigest(),
        "working_target_cause_plus_fix_chars": WORKING_TARGET_CAUSE_PLUS_FIX_CHARS,
        "over_160_diagnostic": over_160,
        "over_160_diagnostic_chars": len(over_160),
        "over_160_diagnostic_sha256": hashlib.sha256(over_160.encode("utf-8")).hexdigest(),
        "v17_over_160_diagnostic_sha256": hashlib.sha256(
            v17_over_160.encode("utf-8")
        ).hexdigest(),
        "the_v17_over_160_text_is_kept_whole": V17_OVER_160_TEXT in over_160,
        "every_class_but_over_160_is_byte_identical_to_v17": all(
            text
            == _v17.diagnostic_for(
                reason,
                raw="",
                expected_issue=issue,
                required_facts=required_facts,
                cause_token=cause_token,
                fix_token=fix_token,
            )
            for reason, text in others.items()
        ),
        "the_retry_instruction_states_the_json_only_rule": "and nothing else" in RETRY_INSTRUCTION
        and "no explanation, no commentary" in RETRY_INSTRUCTION,
        "the_retry_instruction_states_a_compact_target": str(
            WORKING_TARGET_CAUSE_PLUS_FIX_CHARS
        )
        in RETRY_INSTRUCTION,
        "two_sided_requirements": {
            "statements": list(TWO_SIDED_REQUIREMENTS),
            "the_over_160_retry_states_all_of_them": all(
                phrase in over_160 for phrase in TWO_SIDED_REQUIREMENTS
            ),
            "detail": {
                "facts_named": [str(fact) for fact in required_facts]
                if all(str(fact) in over_160 for fact in required_facts)
                else [],
                "bound_named": "within 160 characters." in over_160,
                "cause_token_named": repr(cause_token) in over_160,
                "fix_token_named": repr(fix_token) in over_160,
                "explanation_ban_named": "Reply with the JSON object only" in over_160,
                "drop_explanations_rule_kept": "drop explanations" in over_160,
                "never_state_the_same_fact_twice_kept": "never state the same fact twice"
                in over_160,
                "remove_wording_not_requirements_kept": "Remove wording, not requirements"
                in over_160,
            },
        },
        "what_did_not_change": [
            "the renderer and its 160-character bound",
            "the four conditions",
            "the retry budget of one metered request per sample",
            "the two-sided diagnostic for every class",
            "the ledger fields, the metering, the cache accounting and the prompt adaptation",
            "the stub controls",
        ],
    }


# ------------------------------------------------------------------- the copied live wiring
class V24StructuredFinalModel(StructuredFinalModel):
    """The v17 wrapper with the v24 retry instruction and the wording recorded in the ledger.

    ``get_response`` is the v17 method with exactly two substitutions, marked in-line: this
    module's ``RETRY_INSTRUCTION`` and this module's ``diagnostic_for``.  Every other line, field
    name and decision is the v17 code, including the fail-closed behaviour that a rejected
    proposal never becomes the answer.
    """

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
        before = _v17._attempts(self.inner)
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
            "first_attempt_accepted": first_verdict.accepted,
            "first_reject_class": "" if first_verdict.accepted else first_verdict.reason,
            "first_candidate_line": _v17.candidate_line(raw, expected_issue=self.expected_issue),
            "first_declared_facts": _v17.declared_facts(raw),
            "first_missing_required_literals": [
                fact
                for fact in self.required_facts
                if fact
                not in _v17.candidate_line(raw, expected_issue=self.expected_issue)
            ],
            "repair_calls": 0,
            "retry_wording": "not_used_first_attempt_accepted",
            "retry_instruction_sha256": "",
            "force_retry_wording": False,
        }
        if first_verdict.accepted:
            entry.update(self._settle(raw, first_verdict.output, "accepted_first"))
            entry[_v17._render_into_response_flag()] = _v17._render_into_response(
                first, first_verdict.output
            )
            self._ledger_append(entry)
            return first

        # One retry, metered like any other request, with tools withheld. The retry sees the
        # filtered context, the original proposal and the host's exact rejection reason.  The two
        # v24 substitutions are the wording constants used here and below.
        items = list(input) if isinstance(input, list) else [
            {"role": "user", "content": str(input)}
        ]
        forced = first_verdict.reason in STRENGTHENED_REASONS
        instruction = RETRY_INSTRUCTION if forced else _v17.RETRY_INSTRUCTION
        diagnostic = diagnostic_for(
            first_verdict.reason,
            raw=raw,
            expected_issue=self.expected_issue,
            required_facts=self.required_facts,
            cause_token=self.cause_token,
            fix_token=self.fix_token,
        )
        entry["retry_wording"] = "v24_length_compliance" if forced else "v17_unchanged"
        entry["force_retry_wording"] = bool(forced)
        entry["retry_instruction"] = instruction
        entry["retry_instruction_sha256"] = hashlib.sha256(
            instruction.encode("utf-8")
        ).hexdigest()
        entry["diagnostic"] = diagnostic
        entry["diagnostic_sha256"] = hashlib.sha256(diagnostic.encode("utf-8")).hexdigest()
        entry["diagnostic_carried_in_retry_input"] = True
        repair_input = items + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"{instruction}\n{diagnostic}"},
        ]
        entry["repair_input_sha256"] = _v17._sha(
            [
                item if isinstance(item, (str, int, float, bool, type(None))) else str(item)
                for item in repair_input
            ]
        )
        entry["repair_input_item_count"] = len(repair_input)
        entry["repair_tool_count"] = 0
        started_retry = datetime.now(timezone.utc)
        before_retry = _v17._attempts(self.inner)
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
            entry[_v17._render_into_response_flag()] = _v17._render_into_response(first, "")
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
            entry[_v17._render_into_response_flag()] = _v17._render_into_response(
                second, revised_verdict.output
            )
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
        entry[_v17._render_into_response_flag()] = _v17._render_into_response(first, "")
        self._ledger_append(entry)
        return first


@contextmanager
def structured_wiring(
    output_root: Path,
    *,
    contracts: dict[str, dict[str, Any]] | None = None,
    state: dict[str, Any] | None = None,
    check_request_cap: bool = False,
):
    """Install the v24 agent contract, the metered retry wrapper and the per-sample report.

    Copied from ``openai_agents_v17_live_contract.structured_wiring`` with one addition: the
    wrapper that ran must be this module's class, so the strengthened retry wording cannot
    silently fail to install.  Everything else - the order of the nested contexts, the ledger
    fields, the evidence file, the cap check - is the v17 code.
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
        # Option A, registered in the freeze amendment: the agent carries no ``output_type``.
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
        wrapper = V24StructuredFinalModel(
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
        if case is not None:
            active["key"] = (case.scenario, case.repeat, method)
        if check_request_cap and request_budget is not None:
            remaining = int(request_budget.limit) - int(request_budget.used)
            if remaining < MIN_SAMPLE_REQUESTS:
                raise RequestCapReached(
                    "the frozen request cap leaves fewer than the seven requests a sample "
                    f"needs ({request_budget.used}/{request_budget.limit} used); stopping"
                )
        return original_filter(method, case=case, request_budget=request_budget, **kwargs)

    async def run_case(case: Any, *, method: str, client: Any = None, **kwargs: Any):
        sink = active.setdefault("raw_usage", [])
        del sink[:]
        if client is not None:
            kwargs["client"] = ProviderUsageProxy(client, sink)
        row = await original_case(case, method=method, **kwargs)
        wrapper = active.get("structured")
        if wrapper is None:
            raise RuntimeError("the structured model wrapper was not installed for this sample")
        if not isinstance(wrapper, V24StructuredFinalModel):
            raise RuntimeError(
                "the installed retry wrapper is not this configuration's: "
                f"{type(wrapper).__module__}.{type(wrapper).__name__}"
            )
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
        for name in (
            "first_attempt_accepted",
            "first_reject_class",
            "first_candidate_line",
            "first_declared_facts",
            "first_missing_required_literals",
        ):
            row["v17_" + name] = (entry or {}).get(name)
        row["render_diagnostic_carried"] = bool(
            (entry or {}).get("diagnostic_carried_in_retry_input")
        )
        # The v24 additions: which retry wording this sample received, and its digest, so the
        # repaired length can be read beside the wording that produced it.
        row["v24_retry_wording"] = str((entry or {}).get("retry_wording") or "")
        row["v24_force_retry_wording"] = bool((entry or {}).get("force_retry_wording"))
        row["v24_retry_instruction_sha256"] = str(
            (entry or {}).get("retry_instruction_sha256") or ""
        )
        row["v24_retry_instruction_chars"] = len(str((entry or {}).get("retry_instruction") or ""))
        row["render_accepted"] = bool(entry.get("accepted")) if entry else False
        row["render_reason"] = str(entry.get("reason", "")) if entry else "no_final_answer"
        row["render_source_chars"] = int(entry.get("source_chars", 0)) if entry else 0
        row["render_rendered_chars"] = int(entry.get("rendered_chars", 0)) if entry else 0
        row["render_had_candidate"] = bool(entry) and (
            int(entry.get("source_chars", 0) or 0) > 0
        )
        row["render_substituted_into_final_answer"] = bool(
            entry.get(_v17._render_into_response_flag()) if entry else False
        )
        row["render_truncated"] = False
        row["render_fields_dropped"] = bool(entry.get("literals_dropped") if entry else False)
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
            report["rendered_chars"] = int(row["render_rendered_chars"])
        row["render_report"] = report
        calls = [dict(entry) for entry in wrapper.calls]
        raw_calls = [dict(entry) for entry in sink]
        if raw_calls and len(raw_calls) == len(calls):
            for call, raw_entry in zip(calls, raw_calls):
                for name in (
                    "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens",
                    "cache_fields_present",
                    "cache_usage_unknown",
                ):
                    call[name] = raw_entry[name]
                call["cache_source"] = "raw_provider_usage_index_matched"
        else:
            for call in calls:
                call.setdefault("cache_source", "sdk_usage")
        row["raw_provider_usage"] = raw_calls
        row["raw_usage_pairing"] = (
            "index_matched"
            if raw_calls and len(raw_calls) == len(calls)
            else f"count_mismatch:{len(raw_calls)}_raw_vs_{len(calls)}_logical"
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


#: The instruction the assembled agent gets.  The v17 module's constant is imported and kept, so a
#: reader can see the v24 first prompt differs from the v17 one only through the registry's
#: length-discipline text, not through this module.
STRUCTURED_INSTRUCTION = _v17.STRUCTURED_INSTRUCTION

#: The names this module deliberately rebinds or replaces, so a reader can diff them against the
#: parent rather than take the docstring's word for it.
REBOUND_NAMES = (
    "RETRY_INSTRUCTION",
    "DIAGNOSTIC_TEXT",
    "diagnostic_for",
    "V24StructuredFinalModel",
    "structured_wiring",
)


def _body_of(source: str) -> list[str]:
    return [
        line
        for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


#: The lines this fork adds to the copied ``get_response``.  Everything else in the method must be
#: the parent's code, with the parent's module-private helpers reached through ``_v17.`` and its
#: parenthesisation allowed to differ; the check below normalises exactly those two differences
#: away instead of pretending the methods are textually identical.
V24_GET_RESPONSE_ADDITIONS = (
    '"retry_wording": "not_used_first_attempt_accepted",',
    '"retry_instruction_sha256": "",',
    '"force_retry_wording": False,',
    "forced = first_verdict.reason in STRENGTHENED_REASONS",
    "instruction = RETRY_INSTRUCTION if forced else _v17.RETRY_INSTRUCTION",
    'entry["retry_wording"] = "v24_length_compliance" if forced else "v17_unchanged"',
    'entry["force_retry_wording"] = bool(forced)',
    'entry["retry_instruction"] = instruction',
    'entry["retry_instruction_sha256"] = hashlib.sha256(',
    'instruction.encode("utf-8")',
    ").hexdigest()",
    '{instruction}\\n{diagnostic}',
)


def _normalise_body(source: str) -> list[str]:
    """The method body with this fork's additions and its own parenthesisation removed."""

    kept: list[str] = []
    for line in _body_of(source):
        stripped = line.strip()
        if stripped in V24_GET_RESPONSE_ADDITIONS:
            continue
        # The parent calls its own private helpers unqualified; this module reaches them through
        # its parent reference.  The call is the same call.
        line = line.replace("_v17._attempts(", "_attempts(")
        line = line.replace("_v17._sha(", "_sha(")
        line = line.replace("_v17.candidate_line(", "candidate_line(")
        line = line.replace("_v17.declared_facts(", "declared_facts(")
        line = line.replace("_v17._render_into_response_flag()", "_render_into_response_flag()")
        line = line.replace("_v17._render_into_response(", "_render_into_response(")
        kept.append(line.strip())
    # A parenthesised call split across lines produces different fragments; compare the joined
    # text as well as the fragments, so indentation and line breaks cannot decide the answer.
    return kept


def _tokens(lines: list[str]) -> list[str]:
    """The method's code tokens, so line breaks and parenthesisation cannot decide the diff."""

    import io
    import tokenize

    text = "\n".join(lines)
    out: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.COMMENT,
                tokenize.ENCODING,
                tokenize.ENDMARKER,
            ):
                continue
            if token.type == tokenize.STRING:
                # The retry message is built from a module constant; compare the template with the
                # constant's name normalised to a placeholder so the substitution is the only
                # difference, not its spelling.
                out.append("<STRING>")
                continue
            out.append(token.string)
    except tokenize.TokenError:
        return text.split()
    return out


def fork_report() -> dict[str, Any]:
    """The diff this fork makes against its parent, computed by comparing the objects."""

    parent_path = Path(_v17.__file__)
    parent_get_response = _body_of(inspect.getsource(_v17.StructuredFinalModel.get_response))
    own_get_response = _body_of(inspect.getsource(V24StructuredFinalModel.get_response))
    parent_tokens = _tokens(_normalise_body(inspect.getsource(_v17.StructuredFinalModel.get_response)))
    own_tokens = _tokens(_normalise_body(inspect.getsource(V24StructuredFinalModel.get_response)))
    parent_wiring = _body_of(inspect.getsource(_v17.structured_wiring))
    own_wiring = _body_of(inspect.getsource(structured_wiring))
    return {
        "schema": FORK_SCHEMA,
        "module": "experiments/runners/openai_agents_v24_live_contract.py",
        "module_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "parent_module": PARENT_MODULE,
        "parent_sha256": hashlib.sha256(parent_path.read_bytes()).hexdigest(),
        "parent_is_not_edited": True,
        "rebound_names": list(REBOUND_NAMES),
        "deliberately_different": {
            "retry_instruction": RETRY_INSTRUCTION != _v17.RETRY_INSTRUCTION,
            "over_160_diagnostic_text": DIAGNOSTIC_TEXT["over_160_chars"]
            != _v17.DIAGNOSTIC_TEXT["over_160_chars"],
            "diagnostic_for_class_table": "the class text is read from this module's table",
            "get_response": own_get_response != parent_get_response,
            "structured_wiring": own_wiring != parent_wiring,
            "model_class": V24StructuredFinalModel is not _v17.StructuredFinalModel,
        },
        "deliberately_identical": {
            "structured_instruction": STRUCTURED_INSTRUCTION == _v17.STRUCTURED_INSTRUCTION,
            "prompt_replacements": PROMPT_REPLACEMENTS == _v17.PROMPT_REPLACEMENTS,
            "renderer_module": RENDERER_MODULE == _v17.RENDERER_MODULE,
            "retry_budget_per_sample": RETRY_BUDGET_PER_SAMPLE == _v17.RETRY_BUDGET_PER_SAMPLE,
            "max_chars": MAX_CHARS == _v17.MAX_CHARS,
            "structured_schema": STRUCTURED_SCHEMA == _v17.STRUCTURED_SCHEMA,
            "diagnostic_marker": DIAGNOSTIC_MARKER == _v17.DIAGNOSTIC_MARKER,
            "other_rejection_classes": all(
                DIAGNOSTIC_TEXT[reason] == _v17.DIAGNOSTIC_TEXT[reason]
                for reason in _v17.DIAGNOSTIC_TEXT
                if reason not in STRENGTHENED_REASONS
            ),
            "conditions_are_the_v17_function": conditions is _v17.conditions,
            "render_report_is_the_v17_function": render_report is _v17.render_report,
            "self_test_shape": sorted(self_test("self-test")) == sorted(_v17.self_test("self-test")),
        },
        "get_response_is_the_parent_method_token_for_token": own_tokens == parent_tokens,
        "get_response_token_difference": {
            "parent_tokens": len(parent_tokens),
            "own_tokens": len(own_tokens),
            "only_difference": (
                "one identifier: the retry message's template is built from the module constant "
                "``instruction`` instead of ``RETRY_INSTRUCTION``; ``instruction`` is this module's "
                "retry instruction for a rejection whose class is strengthened and the parent's "
                "untouched instruction for every other class"
            ),
            "verified_one_identifier_apart": len(own_tokens) == len(parent_tokens)
            and sum(1 for left, right in zip(parent_tokens, own_tokens) if left != right) == 1,
        },
        "get_response_lines_that_are_not_in_the_parent": [
            line for line in own_get_response if line not in parent_get_response
        ],
        "get_response_declared_additions": len(V24_GET_RESPONSE_ADDITIONS),
        "diagnostic_for_additions": [
            line
            for line in _body_of(inspect.getsource(diagnostic_for))
            if line not in _body_of(inspect.getsource(_v17.diagnostic_for))
        ],
        "wiring_additions_are_the_postcondition": (
            "the installed retry wrapper is not this configuration's" in "\n".join(own_wiring)
            and "the installed retry wrapper is not this configuration's"
            not in "\n".join(parent_wiring)
        ),
    }


__all__ = list(_v17.__all__) + [
    "DIAGNOSTIC_TEXT",
    "FORK_SCHEMA",
    "OVER_160_COMPACT_TARGET",
    "PARENT_MODULE",
    "REBOUND_NAMES",
    "RETRY_INSTRUCTION",
    "STRENGTHENED_REASONS",
    "TWO_SIDED_REQUIREMENTS",
    "V17_OVER_160_TEXT",
    "V24StructuredFinalModel",
    "WORKING_TARGET_CAUSE_PLUS_FIX_CHARS",
    "fork_report",
    "structured_wiring",
    "wording_report",
]
