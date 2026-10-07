"""CrewAI v16 run-controller guard: the first role cannot end its loop early.

Why the controller and not the view
----------------------------------
r15's per-boundary diagnosis (``integrations/crewai/R15_SPLIT_DIAGNOSIS_AND_BOUND_ERRATUM_20261005.md``)
showed ``mechanism_withheld_required_content = false`` for all six failing units: at the
call where the role stopped, the tool list, the JSON schemas, the tool-choice line, the
executed tools' verbatim evidence and the ``HANDOFF`` contract were all in view, and no
pin, protected round or whole-prefix fallback had taken anything away.  The role simply
emitted its final answer after three of the four frozen tools.

So v16 changes **only the controller**: a deterministic guard around the first role's
model call that refuses an early final answer until the frozen tool table has been
completed in order.  The compressed view is built by the *same* frozen r13 middleware,
from the *same* prepared messages, with no added content -- asserted byte-for-byte in
``tests/test_crewai_handoff_v16.py``.

What the guard counts
---------------------
``completed_tool_rounds`` counts the host's own ``Action: <name>`` markers in the raw
message list, in order, and stops at the first missing or out-of-order tool.  The guard
therefore cannot be fooled by prose: a message claiming "I already called the remaining
tool" contains no action marker and does not advance the count, and a repeated call of
the same tool does not advance it either (the expected tool at that position is a
different name).  Only a real tool call by the host advances it.

What the guard does on a violation
----------------------------------
It refuses the early final answer and re-serves the request with one appended control
message that lists the frozen tools, the completed prefix and the required next tool,
and it counts ``rejections``.  This is the only text the guard ever adds, and it exists
only on calls where the role tried to stop early -- so a run with no violation is
byte-identical to r15's mechanism.

Failure semantics (must never be booked as saving)
--------------------------------------------------
If the role keeps trying to finish early, the rejections consume further agent requests
until the global request cap or the agent's own iteration limit is reached.  A role that
never produces a ``HANDOFF`` is recorded as a **failed unit** by the runner
(``guard_exhausted: true``, empty first-role output), which the audit counts as a failure
and which disqualifies the unit from any saving claim.  The extra requests are charged to
the arm and to the global ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping, Sequence

from experiments.runners import run_crewai_experiment as base

#: Same marker shape the host uses when it parses its own ReAct actions.
_ACTION_LINE = re.compile(r"(?im)^\s*Action\s*:\s*([^\r\n]+)")
_FINAL_ANSWER = re.compile(r"(?im)^\s*Final Answer\s*:\s*")
#: How many times one role may be pushed back before the unit is declared exhausted.
DEFAULT_MAX_REJECTIONS = 6
GUARD_PREFIX = "Host control: the evidence loop is not finished."


def _message_text(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def _message_role(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("role", "") or message.get("type", "") or "")
    return str(getattr(message, "role", "") or getattr(message, "type", "") or "")


def is_final_answer(response: str) -> bool:
    """Whether the model's response ends the role's loop."""
    return bool(_FINAL_ANSWER.search(str(response or "")))


def final_answer_body(response: str) -> str:
    match = _FINAL_ANSWER.search(str(response or ""))
    if match is None:
        return ""
    return str(response)[match.end():].strip()


def completed_tool_rounds(messages: Sequence[Any], tool_names: Sequence[str]) -> int:
    """How many frozen tools were executed **in order**, counted from real markers.

    Only lines produced by the host's own ReAct scaffolding count.  The count stops at
    the first tool that is missing or out of order, so a model's prose claim cannot
    advance it and a repeated call of an earlier tool cannot either.
    """
    expected = [str(name) for name in tool_names]
    called: list[str] = []
    for message in messages:
        if _message_role(message) == "system":
            continue
        for match in _ACTION_LINE.finditer(_message_text(message)):
            name = match.group(1).strip().strip("` ")
            if name:
                called.append(name)
    completed = 0
    for index, name in enumerate(expected):
        if index >= len(called) or called[index] != name:
            break
        completed = index + 1
    return completed


def guard_message(tool_names: Sequence[str], completed: int) -> str:
    """The single control message appended when an early final answer is refused."""
    expected = [str(name) for name in tool_names]
    done = expected[:completed]
    remaining = expected[completed:]
    return (
        f"{GUARD_PREFIX}\n"
        f"Frozen evidence order: {', '.join(expected)}.\n"
        f"Completed in order: {', '.join(done) if done else 'none'}.\n"
        f"Still required: {', '.join(remaining)}.\n"
        f"Call {remaining[0]} now. Do not end the loop before every required evidence "
        "source has been called."
    )


#: Guard state lives outside the pydantic LLM model: CrewAI's ``BaseLLM`` is a pydantic
#: ``BaseModel``, which would copy a plain instance attribute and leave the runner
#: reading a stale copy of the counters.  The registry is keyed by ``id(llm)`` and the
#: entry is removed when the runner closes the sample.
_GUARD_STATES: dict[int, GuardState] = {}


def register_guard_state(llm: Any, state: GuardState) -> GuardState:
    _GUARD_STATES[id(llm)] = state
    return state


def guard_state_for(llm: Any) -> GuardState | None:
    return _GUARD_STATES.get(id(llm))


def release_guard_state(llm: Any) -> None:
    _GUARD_STATES.pop(id(llm), None)


@dataclass
class GuardState:
    """Per-role guard bookkeeping, recorded into the sample ledger."""

    tool_names: tuple[str, ...] = ()
    enabled: bool = False
    max_rejections: int = DEFAULT_MAX_REJECTIONS
    rejections: int = 0
    reached_required_rounds: bool = False
    exhausted: bool = False
    last_completed: int = 0
    rejection_records: list[dict[str, Any]] = field(default_factory=list)

    def as_record(self) -> dict[str, Any]:
        return {
            "guard_enabled": self.enabled,
            "guard_tool_names": list(self.tool_names),
            "guard_required_rounds": len(self.tool_names),
            "guard_rejections": self.rejections,
            "guard_reached_required_rounds": self.reached_required_rounds,
            "guard_exhausted": self.exhausted,
            "guard_last_completed_rounds": self.last_completed,
            "guard_rejection_records": list(self.rejection_records),
        }


class _GuardMixin:
    """The guard logic, shared by the live provider and the mock provider.

    ``guard_state`` is read from the module registry (:func:`guard_state_for`) rather
    than from an instance attribute, because CrewAI's ``BaseLLM`` is a pydantic model:
    pydantic would copy a plain attribute and the runner would then read a stale copy
    of the counters.
    """

    def _state(self) -> GuardState | None:
        return guard_state_for(self)

    def _guarded_call(self, messages, call_once, **kwargs) -> str:
        attempt_messages = list(messages)
        while True:
            response = call_once(attempt_messages, **kwargs)
            state = self._state()
            if state is None or not state.enabled or not state.tool_names:
                return response
            if not is_final_answer(response):
                return response
            completed = completed_tool_rounds(attempt_messages, state.tool_names)
            state.last_completed = completed
            if completed >= len(state.tool_names):
                state.reached_required_rounds = True
                return response
            if state.rejections >= state.max_rejections:
                state.exhausted = True
                state.rejection_records.append(
                    {
                        "reason": "rejection_cap_reached",
                        "completed_rounds": completed,
                        "required_rounds": len(state.tool_names),
                        "rejected_body": final_answer_body(response)[:200],
                    }
                )
                return response
            state.rejections += 1
            state.rejection_records.append(
                {
                    "reason": "early_final_answer",
                    "completed_rounds": completed,
                    "required_rounds": len(state.tool_names),
                    "rejected_body": final_answer_body(response)[:200],
                }
            )
            attempt_messages = [
                *attempt_messages,
                {"role": "user", "content": guard_message(state.tool_names, completed)},
            ]


class GuardedCrewAILLM(_GuardMixin, base.OpenAICompatCrewAILLM):
    """The r15 live model client plus the controller guard on the first role.

    The guard state is registered in the module registry by the runner (see
    :func:`register_guard_state`), which keeps the counters out of pydantic's copies.
    """

    #: No per-instance attributes are touched: the counters live in ``_GUARD_STATES``.
    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ) -> str:
        return self._guarded_call(
            messages,
            lambda attempt, **kwargs: super(GuardedCrewAILLM, self).call(attempt, **kwargs),
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
            response_model=response_model,
        )


class GuardedReplayCrewAILLM(_GuardMixin, base.ReplayCrewAILLM):
    """The deterministic mock provider plus the same guard, for the zero-API gate.

    The scripted responses are a fixed sequence, so a refused early final answer simply
    consumes the next scripted response -- which is exactly how the negative controls
    ("the model insists on stopping early") are exercised without any API request.
    """

    def call(
        self,
        messages,
        tools=None,
        callbacks=None,
        available_functions=None,
        from_task=None,
        from_agent=None,
        response_model=None,
    ) -> str:
        return self._guarded_call(
            messages,
            lambda attempt, **kwargs: super(GuardedReplayCrewAILLM, self).call(
                attempt, **kwargs
            ),
            tools=tools,
            callbacks=callbacks,
            available_functions=available_functions,
            from_task=from_task,
            from_agent=from_agent,
            response_model=response_model,
        )


def create_guard_state(tool_names: Sequence[str], *, enabled: bool,
                       max_rejections: int = DEFAULT_MAX_REJECTIONS) -> GuardState:
    return GuardState(
        tool_names=tuple(str(name) for name in tool_names),
        enabled=bool(enabled),
        max_rejections=int(max_rejections),
    )


__all__ = [
    "DEFAULT_MAX_REJECTIONS",
    "guard_state_for",
    "register_guard_state",
    "release_guard_state",
    "GUARD_PREFIX",
    "GuardedCrewAILLM",
    "GuardedReplayCrewAILLM",
    "GuardState",
    "completed_tool_rounds",
    "create_guard_state",
    "final_answer_body",
    "guard_message",
    "is_final_answer",
]
