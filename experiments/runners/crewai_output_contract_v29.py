"""Deterministic, all-arm CrewAI output canonicalization with evidence checks.

This never inserts an unmentioned fact: a unit alias is expanded only when the
model wrote the exact value and an actually executed task tool returned it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import crewai_coverage_guard_v24 as coverage


def _observed_value(task: dict[str, Any], observed_tools: Sequence[str],
                    field: str, value: int) -> bool:
    seen = set(str(name) for name in observed_tools)
    return any(tool["name"] in seen and tool["result"].get(field) == value
               for tool in task["tools"])


def normalize(response: str, task: dict[str, Any], *, stage: int,
              observed_tools: Sequence[str]) -> tuple[str, list[dict[str, str]]]:
    text = str(response)
    edits: list[dict[str, str]] = []
    if stage == 0:
        # Only the final-answer marker is changed. Actions, observations and
        # ordinary mentions of HANDOFF inside evidence remain byte-identical.
        pattern = re.compile(r"(?i)(\bFinal Answer:\s*|^)HANDOFF\s*[:：]\s*")
        changed, count = pattern.subn(lambda match: match.group(1) + "HANDOFF ", text,
                                      count=1)
        if count:
            edits.append({"kind": "handoff_marker", "from": "HANDOFF:",
                          "to": "HANDOFF "})
            text = changed
    elif stage == 1:
        for fact in task.get("answer_facts") or []:
            match = re.fullmatch(r"([a-z][a-z0-9_]*)_seconds\s+(\d+)", str(fact))
            if not match:
                continue
            field = match.group(1) + "_seconds"
            value = int(match.group(2))
            if not _observed_value(task, observed_tools, field, value):
                continue
            alias = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(match.group(1))}\s*=\s*{value}\s*s\b",
                               re.IGNORECASE)
            replacement = f"{field}={value}"
            changed, count = alias.subn(replacement, text, count=1)
            if count:
                edits.append({"kind": "verified_seconds_alias", "from": alias.pattern,
                              "to": replacement})
                text = changed
    return text, edits


def normalize_role_outputs(row: dict[str, Any], task: dict[str, Any]) -> tuple[list[str], list[dict]]:
    outputs = list(row.get("role_raw_outputs") or [])
    traces = list(row.get("role_tool_traces") or [])
    served, edits = [], []
    for stage, output in enumerate(outputs[:2]):
        observed = [name for trace in traces[:stage + 1] for name in trace]
        value, changes = normalize(output, task, stage=stage, observed_tools=observed)
        served.append(value)
        edits.extend({"stage": stage, **change} for change in changes)
    return served, edits


def final_answer(response: str) -> str:
    match = re.search(r"(?is)\bFinal Answer:\s*(.*)$", response)
    return match.group(1).strip() if match else response.strip()


@dataclass
class OutputContractState:
    task: dict[str, Any]
    stage: int
    observed_tools: list[str]
    prior_tools: tuple[str, ...] = ()
    max_retries: int = 1
    retry_count: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)

    def process(self, messages: Sequence[Any], call_once: Callable[[Sequence[Any]], str]) -> str:
        attempt = list(messages)
        while True:
            raw = call_once(attempt)
            served, edits = normalize(raw, self.task, stage=self.stage,
                                      observed_tools=[*self.prior_tools, *self.observed_tools])
            missing: list[str] = []
            if self.stage == 1 and "RESULT task=" in final_answer(served):
                verdict = judge.judge_answer(judge.answer_rule(self.task), final_answer(served))
                missing = list(verdict.strict["missing_facts"])
            retry = bool(missing and self.retry_count < self.max_retries)
            self.records.append({"stage": self.stage, "raw": raw, "served": served,
                                 "edits": edits, "missing_facts": missing, "retry": retry})
            if not retry:
                return served
            self.retry_count += 1
            fields = [item.split()[0] for item in missing]
            control = (
                "Host output check: your RESULT omitted required field name(s) "
                + ", ".join(fields)
                + ". Review the already observed tool evidence and return one RESULT line "
                  "with those exact field names and their observed values. Do not invent values."
            )
            attempt = [*attempt, {"role": "assistant", "content": served},
                       {"role": "user", "content": control}]


_STATES: dict[int, OutputContractState] = {}


def register(llm: Any, state: OutputContractState) -> None:
    _STATES[id(llm)] = state


def release(llm: Any) -> None:
    _STATES.pop(id(llm), None)


def get_state(llm: Any) -> OutputContractState | None:
    return _STATES.get(id(llm))


class _Mixin:
    def _contracted_call(self, messages: Sequence[Any], call_once: Callable[[Sequence[Any]], str]) -> str:
        state = _STATES.get(id(self))
        return state.process(messages, call_once) if state is not None else call_once(messages)


class ContractedCapturedCrewAILLM(_Mixin, coverage.GuardedCapturedCrewAILLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._contracted_call(messages, lambda attempt: super(
            ContractedCapturedCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))


class ContractedCapturedNativeSummaryCrewAILLM(
    _Mixin, coverage.GuardedCapturedNativeSummaryCrewAILLM
):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._contracted_call(messages, lambda attempt: super(
            ContractedCapturedNativeSummaryCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))


class ContractedReplayCrewAILLM(_Mixin, coverage.GuardedReplayCrewAILLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._contracted_call(messages, lambda attempt: super(
            ContractedReplayCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))
