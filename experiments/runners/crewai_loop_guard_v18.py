"""Prospective CrewAI next-tool guard. Zero-API development; not frozen for a paid run.

Install on *every* arm. The returned model text is inspected before CrewAI executes a
tool. Only the next name in the frozen table may be released. The actual earlier
tool calls are read from host ReAct messages, never inferred from model prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Mapping, Sequence

from experiments.runners import run_crewai_experiment as base

ACTION = re.compile(r"(?im)^\s*Action\s*:\s*([^\r\n]+)")
DEFAULT_MAX_REJECTIONS = 6
CONTROL_PREFIX = "Host control: the frozen evidence order is mandatory."


class GuardExhausted(RuntimeError):
    """The role failed its bounded tool-order contract; never count as a saving."""


def _content(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def _role(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("role", "") or message.get("type", "") or "")
    return str(getattr(message, "role", "") or getattr(message, "type", "") or "")


def host_executed_actions(messages: Sequence[Any]) -> list[str]:
    """Read Actions in host conversation history; never count the pending response."""
    out: list[str] = []
    for message in messages:
        if _role(message) == "system":
            continue
        text = _content(message)
        if _role(message) != "assistant" or "Observation:" not in text:
            continue
        out.extend(m.group(1).strip().strip("` ") for m in ACTION.finditer(text))
    return out


def candidate_actions(response: str) -> list[str]:
    return [m.group(1).strip().strip("` ") for m in ACTION.finditer(str(response or ""))]


def classify(messages: Sequence[Any], response: str, tool_names: Sequence[str]) -> tuple[str, int, str]:
    """Return (verdict, ordered prefix length, next tool). Fail closed on bad history."""
    expected = [str(x) for x in tool_names]
    executed = host_executed_actions(messages)
    if executed != expected[: len(executed)]:
        return "invalid_executed_history", min(len(executed), len(expected)), ""
    completed = len(executed)
    if completed >= len(expected):
        return "complete", completed, ""
    next_tool = expected[completed]
    actions = candidate_actions(response)
    if not actions:
        return "reject_actionless", completed, next_tool
    if len(actions) != 1 or actions[0] != next_tool:
        return "reject_wrong_action_or_order", completed, next_tool
    return "advance", completed, next_tool


@dataclass
class GuardState:
    tool_names: tuple[str, ...]
    enabled: bool = True
    max_rejections: int = DEFAULT_MAX_REJECTIONS
    rejections: int = 0
    exhausted: bool = False
    reached_required_rounds: bool = False
    rejection_records: list[dict[str, Any]] = field(default_factory=list)
    turn_records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_rejections < 0:
            raise ValueError("max_rejections must be nonnegative")

    def apply(self, messages: Sequence[Any], response: str) -> tuple[bool, str]:
        if not self.enabled:
            raise ValueError("guard must be enabled on every arm")
        verdict, completed, next_tool = classify(messages, response, self.tool_names)
        if verdict == "invalid_executed_history":
            self.exhausted = True
            self.turn_records.append({"verdict": verdict, "completed": completed})
            raise GuardExhausted("executed tool history violates frozen order")
        if verdict in ("complete", "advance"):
            self.turn_records.append({"verdict": verdict, "completed": completed})
            if verdict == "complete":
                self.reached_required_rounds = True
            return True, ""
        self.rejection_records.append({"reason": verdict, "completed_rounds": completed,
                                       "required_rounds": len(self.tool_names),
                                       "next_tool": next_tool, "cap_reached": self.rejections >= self.max_rejections,
                                       "rejected_body": str(response or "")[:200]})
        if self.rejections >= self.max_rejections:
            self.exhausted = True
            raise GuardExhausted(f"{verdict}; max_rejections={self.max_rejections}")
        self.rejections += 1
        control = (
            f"{CONTROL_PREFIX}\n"
            f"Frozen order: {', '.join(self.tool_names)}.\n"
            f"Completed in order: {', '.join(self.tool_names[:completed]) or 'none'}.\n"
            f"Call {next_tool} now with one exact Action line. Do not hand off yet."
        )
        return False, control

    def as_record(self) -> dict[str, Any]:
        return {"guard_enabled": self.enabled, "guard_tool_names": list(self.tool_names),
                "guard_required_rounds": len(self.tool_names), "guard_rejections": self.rejections,
                "guard_reached_required_rounds": self.reached_required_rounds,
                "guard_exhausted": self.exhausted,
                "guard_rejection_records": [r for r in self.rejection_records if not r["cap_reached"]],
                "guard_cap_reached_record": next((r for r in self.rejection_records if r["cap_reached"]), None),
                "guard_turn_records": list(self.turn_records)}


_STATES: dict[int, GuardState] = {}


def register_guard_state(llm: Any, state: GuardState) -> None:
    _STATES[id(llm)] = state


def release_guard_state(llm: Any) -> None:
    _STATES.pop(id(llm), None)


def create_guard_state(tool_names: Sequence[str], *, enabled: bool = True,
                       max_rejections: int = DEFAULT_MAX_REJECTIONS) -> GuardState:
    return GuardState(tuple(str(x) for x in tool_names), enabled=enabled,
                      max_rejections=max_rejections)


class _GuardMixin:
    def _guarded_call(self, messages: Sequence[Any], call_once: Callable[[Sequence[Any]], str]) -> str:
        attempt = list(messages)
        state = _STATES.get(id(self))
        if state is None:
            raise ValueError("v18 guard state missing; all-arm installation required")
        if not state.enabled or not state.tool_names:
            return call_once(attempt)
        while True:
            response = call_once(attempt)
            accepted, control = state.apply(attempt, response)
            if accepted:
                return response
            attempt = [*attempt, {"role": "user", "content": control}]


class GuardedCrewAILLM(_GuardMixin, base.OpenAICompatCrewAILLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(
            messages,
            lambda attempt: super(GuardedCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model),
        )


class GuardedReplayCrewAILLM(_GuardMixin, base.ReplayCrewAILLM):
    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(
            messages,
            lambda attempt: super(GuardedReplayCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model),
        )


class GuardedNativeSummaryCrewAILLM(_GuardMixin, base.NativeSummaryCrewAILLM):
    """The summary arm receives the same pre-execution tool-order guard."""

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None, response_model=None) -> str:
        return self._guarded_call(
            messages,
            lambda attempt: super(GuardedNativeSummaryCrewAILLM, self).call(
                attempt, tools=tools, callbacks=callbacks,
                available_functions=available_functions, from_task=from_task,
                from_agent=from_agent, response_model=response_model),
        )
