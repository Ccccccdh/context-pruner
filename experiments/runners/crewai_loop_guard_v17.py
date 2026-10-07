"""CrewAI v17 run-controller guard: an action-less turn never ends the evidence loop.

What changed from v16, and why
------------------------------
v16 rejected a turn only when it carried a ``Final Answer:`` marker.  The r16 three-arm
pilot showed that this model almost never writes that marker: **12/12** plugin-arm rows and
**6/6** r15 plugin-arm early stops ended in a bare ``HANDOFF …`` line, so the guard caught
only the 4 units that happened to write the marker and missed the other 7.  The direction
(run controller, not view) was never falsified; the trigger condition was simply wrong for
this model's dominant stopping form.

v17 therefore triggers on the **host's own parse result** instead of on model text:

    the frozen tool table is not finished  AND  this turn produced no valid ``Action``
    ⇒ refuse the turn, append one control message, and re-serve the request

``Final Answer:`` is no longer necessary; it remains a *sufficient* signal (a turn that
says ``Final Answer:`` and carries no action is refused by the action-less rule as well,
so the two conditions coincide for that case).  The one case the new rule adds is the bare
handoff -- and any other action-less turn.

Because the trigger is "the host executed no tool this turn", it cannot be produced or
suppressed by model prose: prose cannot create an action marker, and only a real tool call
advances the count.  ``completed_tool_rounds`` is unchanged and still counts the host's own
``^Action: <name>`` markers in order, stopping at the first missing or out-of-order tool.

Scope: unchanged, controller only
--------------------------------
The compressed view is still produced by the same frozen r13 middleware from the same
prepared messages; the guard never imports or calls the view layer, and the only text it
ever adds is the control message on the rejection path.  The same registry
(``_GUARD_STATES``, keyed by ``id(llm)``) keeps the counters out of pydantic's copies, and
the guard is enabled for the plugin arm only, so the arms stay comparable.

Failure semantics (unchanged, and still never a saving)
------------------------------------------------------
A role that never produces a ``HANDOFF`` -- because it keeps answering with an action-less
turn -- exhausts the cap and is recorded as a **failed unit** (``guard_exhausted: true``).
Every rejection re-send is a real agent request charged to the arm and to the global
ledger, and no exhausted unit may be counted as a saving.
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
#: The v17 rejection rule, in one place so the tooling and the tests cannot drift from it.
REJECTION_RULE = (
    "the frozen tool table is not finished and this turn produced no valid Action; "
    "a Final Answer marker is not required (it is sufficient, not necessary)"
)
#: Legacy name from v16, kept so older imports keep working.
RULE_NOTE = REJECTION_RULE


def _message_text(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def _message_role(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("role", "") or message.get("type", "") or "")
    return str(getattr(message, "role", "") or getattr(message, "type", "") or "")


def is_final_answer(response: str) -> bool:
    """Whether the model's response carries the explicit completion marker."""
    return bool(_FINAL_ANSWER.search(str(response or "")))


def action_name(response: str) -> str:
    """The tool name of the **first** host-parseable action, or ``""`` if there is none."""
    match = _ACTION_LINE.search(str(response or ""))
    if match is None:
        return ""
    return match.group(1).strip().strip("` ")


def produces_action(response: str) -> bool:
    """Whether this turn gives the host a tool to execute."""
    return bool(action_name(response))


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


def classify_turn(response: str, completed: int, required: int) -> str:
    """The v17 turn verdict, as one of four pre-registered names.

    ``complete_with_final_answer`` / ``complete_with_action`` -- the table is finished, so
    the turn is released whatever it looks like.
    ``reject_actionless``  -- the table is unfinished and the turn gives no tool: the v17
    trigger (this is where a bare ``HANDOFF …`` lands).
    ``reject_final_answer`` -- the table is unfinished and the turn carries the explicit
    marker **and** an action: released as an action, but named separately so the ledger
    shows when the marker appeared.
    """
    if completed >= required:
        return ("complete_with_final_answer" if is_final_answer(response)
                else "complete_with_action")
    if not produces_action(response):
        return "reject_actionless"
    if is_final_answer(response):
        return "reject_final_answer"
    return "advance"


def accepts_turn(response: str, completed: int, required: int) -> bool:
    """Whether the guard releases this turn instead of refusing it."""
    if completed >= required:
        return True
    return produces_action(response)


def guard_message(tool_names: Sequence[str], completed: int) -> str:
    """The single control message appended when an action-less turn is refused."""
    expected = [str(name) for name in tool_names]
    done = expected[:completed]
    remaining = expected[completed:]
    if not remaining:
        raise ValueError(
            "guard_message is only defined for an unfinished tool table; "
            f"completed={completed} required={len(expected)}"
        )
    return (
        f"{GUARD_PREFIX}\n"
        f"Frozen evidence order: {', '.join(expected)}.\n"
        f"Completed in order: {', '.join(done) if done else 'none'}.\n"
        f"Still required: {', '.join(remaining)}.\n"
        f"You did not call a tool in that turn, and a handoff is only valid after every "
        f"required evidence source has been called. Call {remaining[0]} now using the "
        "exact action format, and do not end the loop before every required evidence "
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
    actionless_rejections: int = 0
    final_answer_rejections: int = 0
    rejection_records: list[dict[str, Any]] = field(default_factory=list)
    turn_records: list[dict[str, Any]] = field(default_factory=list)

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
            "guard_actionless_rejections": self.actionless_rejections,
            "guard_final_answer_rejections": self.final_answer_rejections,
            "guard_rule": REJECTION_RULE,
            "guard_turn_records": list(self.turn_records),
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
            completed = completed_tool_rounds(attempt_messages, state.tool_names)
            state.last_completed = completed
            required = len(state.tool_names)
            if accepts_turn(response, completed, required):
                if completed >= required:
                    state.reached_required_rounds = True
                elif state.turn_records is not None:
                    state.turn_records.append(
                        {
                            "verdict": "advance",
                            "completed_rounds": completed,
                            "action": action_name(response),
                        }
                    )
                return response
            kind = classify_turn(response, completed, required)
            if kind == "reject_actionless":
                state.actionless_rejections += 1
            else:
                state.final_answer_rejections += 1
            if state.rejections >= state.max_rejections:
                state.exhausted = True
                state.rejection_records.append(
                    {
                        "reason": kind,
                        "cap_reached": True,
                        "completed_rounds": completed,
                        "required_rounds": required,
                        "rejected_body": str(response or "")[:200],
                    }
                )
                return response
            state.rejections += 1
            state.rejection_records.append(
                {
                    "reason": kind,
                    "cap_reached": False,
                    "completed_rounds": completed,
                    "required_rounds": required,
                    "rejected_body": str(response or "")[:200],
                }
            )
            attempt_messages = [
                *attempt_messages,
                {"role": "user", "content": guard_message(state.tool_names, completed)},
            ]


class GuardedCrewAILLM(_GuardMixin, base.OpenAICompatCrewAILLM):
    """The live model client plus the controller guard on the first role.

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

    The scripted responses are a fixed sequence, so a refused turn simply consumes the
    next scripted response -- which is exactly how the negative controls ("the model
    insists on stopping early") are exercised without any API request.
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
    "REJECTION_RULE",
    "RULE_NOTE",
    "GuardedCrewAILLM",
    "GuardedReplayCrewAILLM",
    "GuardState",
    "accepts_turn",
    "action_name",
    "classify_turn",
    "completed_tool_rounds",
    "create_guard_state",
    "final_answer_body",
    "guard_message",
    "is_final_answer",
    "produces_action",
]
