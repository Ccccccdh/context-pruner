"""Prospective order-free coverage guard for CrewAI's first role.

The task asks for every named evidence source at least once. Tool order and
repetition are unrestricted. Only host-observed Action/Observation turns count;
a HANDOFF claiming a missing source was read cannot satisfy coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from experiments.runners import crewai_capture_v20 as capture
from experiments.runners import run_crewai_experiment as base
from experiments.runners.crewai_loop_guard_v18 import (
    GuardExhausted, candidate_actions, host_executed_actions,
)

CONTROL_PREFIX = "Host control: required evidence sources are still missing."
MAX_REJECTIONS = 6


def classify(messages: Sequence[Any], response: str,
             required_tools: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    required = tuple(str(name) for name in required_tools)
    if not required or len(required) != len(set(required)):
        raise ValueError("the required source names must be nonempty and unique")
    executed = host_executed_actions(messages)
    if any(name not in required for name in executed):
        return "invalid_executed_history", required
    missing = tuple(name for name in required if name not in executed)
    if not missing:
        return "complete", ()
    actions = candidate_actions(response)
    if not actions:
        return "reject_actionless", missing
    if len(actions) != 1 or actions[0] not in required:
        return "reject_unknown_or_ambiguous_action", missing
    # Repeats and any ordering are allowed by the frozen order-free contract.
    return "advance", missing


@dataclass
class CoverageGuardState:
    required_tools: tuple[str, ...]
    enabled: bool = True
    max_rejections: int = MAX_REJECTIONS
    rejections: int = 0
    exhausted: bool = False
    reached_required_rounds: bool = False
    rejection_records: list[dict[str, Any]] = field(default_factory=list)
    turn_records: list[dict[str, Any]] = field(default_factory=list)

    def apply(self, messages: Sequence[Any], response: str) -> tuple[bool, str]:
        if not self.enabled:
            return True, ""
        verdict, missing = classify(messages, response, self.required_tools)
        self.turn_records.append({"verdict": verdict, "missing": list(missing)})
        if verdict == "invalid_executed_history":
            self.exhausted = True
            raise GuardExhausted("host history contains an unregistered tool")
        if verdict in ("complete", "advance"):
            if verdict == "complete":
                self.reached_required_rounds = True
            return True, ""
        self.rejection_records.append({"reason": verdict, "missing": list(missing),
                                       "cap_reached": self.rejections >= self.max_rejections})
        if self.rejections >= self.max_rejections:
            self.exhausted = True
            raise GuardExhausted(f"{verdict}; max_rejections={self.max_rejections}")
        self.rejections += 1
        control = (f"{CONTROL_PREFIX}\n"
                   f"Still missing: {', '.join(missing)}.\n"
                   f"Call {missing[0]} now with one exact Action line. "
                   "Do not hand off until each source has been called at least once.")
        return False, control

    def as_record(self) -> dict[str, Any]:
        return {
            "guard_enabled": self.enabled,
            "guard_tool_names": list(self.required_tools),
            "guard_required_rounds": len(self.required_tools),
            "guard_rejections": self.rejections,
            "guard_reached_required_rounds": self.reached_required_rounds,
            "guard_exhausted": self.exhausted,
            "guard_rejection_records": list(self.rejection_records),
            "guard_turn_records": list(self.turn_records),
            "guard_rule": "order_free_required_source_coverage",
        }


_STATES: dict[int, CoverageGuardState] = {}


def register(llm: Any, state: CoverageGuardState) -> None:
    _STATES[id(llm)] = state


def release(llm: Any) -> None:
    _STATES.pop(id(llm), None)


class _Mixin:
    def _guarded_call(self, messages: Sequence[Any], call_once: Callable[[Sequence[Any]], str]) -> str:
        state = _STATES.get(id(self))
        if state is None or not state.enabled:
            return call_once(messages)
        attempt = list(messages)
        while True:
            response = call_once(attempt)  # every rejection is a real, metered call
            accepted, control = state.apply(attempt, response)
            if accepted:
                return response
            attempt = [*attempt, {"role": "user", "content": control}]


class GuardedCapturedCrewAILLM(_Mixin, capture.CapturedOpenAICompatCrewAILLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(messages, lambda attempt: super(
            GuardedCapturedCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))


class GuardedCapturedNativeSummaryCrewAILLM(_Mixin, capture.CapturedNativeSummaryCrewAILLM):
    # v20 native-summary metered the summary transport but inherited the plain
    # agent call; its per-agent capture stayed empty. Install the same v19
    # per-attempt capture path explicitly for this new, prospective version.
    bind_executed_tools = capture.CapturedOpenAICompatCrewAILLM.bind_executed_tools

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(messages, lambda attempt: (
            capture.CapturedOpenAICompatCrewAILLM.call)(self,
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))


class GuardedReplayCrewAILLM(_Mixin, base.ReplayCrewAILLM):
    """Run the same guard through CrewAI's real tool parser with no API calls."""

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(messages, lambda attempt: super(
            GuardedReplayCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model))
