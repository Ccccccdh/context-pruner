"""CrewAI v11 middleware: keep the tool sequence and the contract after compression.

Why v11 exists (the r10 finding)
--------------------------------
r10 (``runs/stage5-crewai/crewai-two-role-r10-fresh-multitask-01``) kept the v9
pinning promises -- first-pass fact completeness 16/16, zero remedy calls, zero
whole-prefix fallbacks -- but the plugin arm's quality fell to 6/16 against a
16/16 baseline.  The recorded cause was **not** lost facts:

  * in 6 of 16 plugin samples the first role's tool sequence was broken
    (``window_gate``: only ``check_window_lock`` of the three frozen tools;
    ``credential_rotation`` repeats 2/3: the frozen order was permuted);
  * in 4 of 16 plugin samples the decider echoed the contract *template* into its
    answer (``decision=<RETRY>``, ``decision=PROCEED``).  The same template echo
    appeared 3 times in the ``native_summary`` arm and 0 times uncompressed, so it
    is a contract-presentation failure, not a judge defect.

Both are **presentation** failures: the frozen tool list ("call every available
tool exactly once, in order") and the decision contract are carried by messages
that the compressed view may no longer show.

What v11 adds (exactly two deterministic guarantees, on top of v9)
-----------------------------------------------------------------
``VerbatimToolSequenceMiddleware`` subclasses the frozen r9 middleware and keeps
its three tiers unchanged (verbatim evidence pins, contract pins, counted
whole-prefix fallback).  It adds two more, decided only from the frozen task
record and the pre-compression history -- never from model behaviour:

  * **A. tool-sequence directive.**  The tool calls that have already completed in
    this context are counted from the ReAct action markers in the *raw* history
    (deterministic text parsing, no model cooperation required).  Whenever the
    frozen tool list still has un-called tools, one directive message is appended
    that restates the frozen tool names in their frozen order, lists the tools
    already called, and names the next tool.  The directive stays until every
    frozen tool has been observed, so a compressed view cannot end the first
    role's tool loop early.  Its effect is verified after the fact against the
    frozen trace: the plugin arm must show the same tool sequence as the
    uncompressed arm (r10 baseline 16/16).
  * **B. decision-contract directive.**  For the deciding role, when the served
    view no longer contains the frozen ``RESULT`` contract in full, the contract
    sentence is appended verbatim (template included) together with one template
    warning.  The template itself must stay visible, because hiding it is what
    makes the model autocomplete it; the warning states that the template is an
    illustration and that the value after ``decision=`` must be the frozen
    decision word.  The wording is frozen here, so it cannot be tuned per sample.

Nothing in ``context_pruner``, in the frozen v8/v9/v10 modules or in any stored
result is modified: this is one subclass that overrides ``before_model``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from experiments.runners import crewai_pinned_evidence_v9 as v9

MAX_TOOL_DIRECTIVE_MESSAGES = 1
MAX_CONTRACT_DIRECTIVE_MESSAGES = 1
#: CrewAI's text-ReAct trace: the assistant message that also carries the
#: observation.  Only the action label is read; nothing is inferred from prose.
_ACTION_LINE = re.compile(r"^\s*Action\s*:\s*([A-Za-z0-9_.\-]+)\s*$", re.MULTILINE)
_TOOL_DIRECTIVE_PREFIX = "Required tool sequence (host directive):"
_CONTRACT_DIRECTIVE_PREFIX = "Output contract reminder (host directive):"


def observed_tool_calls(messages: Sequence[Any]) -> list[str]:
    """Tool names whose ReAct action is recorded in these messages, in order.

    The parse is textual and deterministic: a line whose whole content is
    ``Action: <name>`` that is followed by an ``Action Input:`` line in the same
    message.  Both markers are produced by the host's own ReAct scaffolding, so
    the count cannot be inflated by a role mentioning a tool name in prose.
    """
    found: list[str] = []
    for message in messages:
        text = v9._text_of(message)
        if "Action Input:" not in text:
            continue
        for name in _ACTION_LINE.findall(text):
            found.append(name)
    return found


class VerbatimToolSequenceMiddleware(v9.PinnedEvidenceMiddleware):
    """v9 pinning plus the tool-sequence and decision-contract directives."""

    def __init__(
        self,
        config: Any | None = None,
        *,
        rule: v9.PinnedRule | None = None,
        budget: Any | None = None,
        task_state: str = "",
        token_counter: Any | None = None,
        tool_names: Sequence[str] = (),
        role_prompt: str = "",
        decision_placeholder: str = "",
        enable_tool_sequence: bool = False,
        enable_decision_contract: bool = False,
    ) -> None:
        super().__init__(
            config, rule=rule, budget=budget, task_state=task_state,
            token_counter=token_counter,
        )
        self.tool_names = tuple(str(name) for name in tool_names)
        self.role_prompt = str(role_prompt or "")
        self.decision_placeholder = str(
            decision_placeholder or (rule and rule.task_id) or ""
        )
        self.enable_tool_sequence = bool(enable_tool_sequence)
        self.enable_decision_contract = bool(enable_decision_contract)
        self.tool_directive_events = 0
        self.tool_directive_messages = 0
        self.contract_directive_events = 0
        self.contract_directive_messages = 0
        self.observed_tool_sequence: list[str] = []

    # -- directives ------------------------------------------------------
    def tool_directive(self, observed: Sequence[str]) -> list[dict[str, str]]:
        """The frozen tool order plus what is still missing, as one message.

        Returns nothing once every frozen tool has been observed, so the
        directive cannot keep the role in the tool loop after the evidence is
        complete.
        """
        if not self.enable_tool_sequence or not self.tool_names:
            return []
        done = list(observed)
        remaining = [name for name in self.tool_names if name not in done]
        if not remaining:
            return []
        lines = [
            _TOOL_DIRECTIVE_PREFIX,
            "Frozen tool order for this task: " + ", ".join(self.tool_names) + ".",
            "Already observed in this conversation: " + (", ".join(done) if done else "none") + ".",
            "Still required before the final handoff: " + ", ".join(remaining) + ".",
            f"Call {remaining[0]} now and record its current observation; do not stop "
            "the tool loop while any tool above is still un-called.",
        ]
        return [{"role": "user", "content": "\n".join(lines)}]

    def contract_directive(self, served: Sequence[Any]) -> list[dict[str, str]]:
        """The frozen output contract, appended when the view lost it.

        The gate is deliberately *stricter* than "the prompt text is somewhere in
        the view":  the view must still spell out the labelled decision field
        **and** still show the contract's own angle-bracket template.  A view that
        shows only one of the two is exactly the shape that produced the r10
        placeholder echo, so it gets the reminder.

        The template is reproduced verbatim from the frozen task prompt because
        hiding the angle-bracket template is what makes the model autocomplete
        it: it must appear as an illustration, next to an explicit instruction to
        replace it with the current decision word.
        """
        if not self.enable_decision_contract or not self.role_prompt:
            return []
        anchor = f"RESULT task={self.rule.task_id}" if self.rule is not None else ""
        template = f"decision=<{self.decision_placeholder}>"
        served_text = "\n".join(v9._text_of(message) for message in served)
        if anchor and anchor in served_text and template in served_text:
            return []
        return [
            {
                "role": "user",
                "content": (
                    _CONTRACT_DIRECTIVE_PREFIX + "\n" + self.role_prompt + "\n"
                    "The line above is the frozen contract; the value inside angle "
                    "brackets is a template illustration. Never output angle brackets: "
                    "write the decision word and the tool values exactly as the current "
                    "observations recorded them."
                ),
            }
        ]

    # -- hook ------------------------------------------------------------
    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        reserved_tokens: int = 0,
    ) -> Any:
        raw = list(messages)
        result = super().before_model(
            messages, task_state=task_state, reserved_tokens=reserved_tokens
        )
        if self.rule is None:
            return result
        served = list(result.messages)
        # Deterministic, from the raw pre-compression history only.
        observed = observed_tool_calls(raw)
        self.observed_tool_sequence = observed
        tool_pins = self.tool_directive(observed)
        contract_pins = self.contract_directive(served)
        additions: list[dict[str, str]] = []
        if tool_pins:
            additions.extend(tool_pins[:MAX_TOOL_DIRECTIVE_MESSAGES])
        if contract_pins:
            additions.extend(contract_pins[:MAX_CONTRACT_DIRECTIVE_MESSAGES])
        if additions:
            served = [*served, *additions]
            if tool_pins:
                self.tool_directive_events += 1
                self.tool_directive_messages += len(tool_pins[:MAX_TOOL_DIRECTIVE_MESSAGES])
            if contract_pins:
                self.contract_directive_events += 1
                self.contract_directive_messages += len(
                    contract_pins[:MAX_CONTRACT_DIRECTIVE_MESSAGES]
                )
        # The added text is host-authored and carries no frozen fact literal, so
        # it can never satisfy a required literal on its own; there is therefore
        # nothing to re-check against the pin rule here.  The v9 budget guard and
        # whole-prefix fallback have already run above.
        result.messages = served
        return result

    def metrics_dict(self) -> dict[str, Any]:
        data = super().metrics_dict()
        data.update(
            {
                "tool_directive_events": self.tool_directive_events,
                "tool_directive_messages": self.tool_directive_messages,
                "contract_directive_events": self.contract_directive_events,
                "contract_directive_messages": self.contract_directive_messages,
                "observed_tool_sequence": list(self.observed_tool_sequence),
            }
        )
        return data


def create_tool_sequence_adapter(
    config: Any,
    *,
    rule: v9.PinnedRule,
    budget: Any,
    task_state: str = "",
    fixed_reserved_tokens: int = 0,
    agent_roles: Sequence[str] | None = None,
    tool_names: Sequence[str] = (),
    role_prompt: str = "",
    decision_placeholder: str = "",
    enable_tool_sequence: bool = False,
    enable_decision_contract: bool = False,
):
    """A ``TriggeredCrewAIContextAdapter`` running the v11 middleware."""
    from experiments.runners import run_crewai_experiment as base

    adapter = base.TriggeredCrewAIContextAdapter(
        config,
        task_state=task_state,
        fixed_reserved_tokens=fixed_reserved_tokens,
        agent_roles=list(agent_roles or ()),
        trigger_soft_limit_tokens=budget.soft_limit_tokens,
    )
    adapter.middleware = VerbatimToolSequenceMiddleware(
        config,
        rule=rule,
        budget=budget,
        task_state=task_state,
        tool_names=tool_names,
        role_prompt=role_prompt,
        decision_placeholder=decision_placeholder,
        enable_tool_sequence=enable_tool_sequence,
        enable_decision_contract=enable_decision_contract,
    )
    return adapter


def build_tool_sequence_middleware(
    config: Any,
    *,
    rule: v9.PinnedRule,
    budget: Any,
    task_state: str = "",
    tool_names: Sequence[str] = (),
    role_prompt: str = "",
    decision_placeholder: str = "",
    enable_tool_sequence: bool = False,
    enable_decision_contract: bool = False,
) -> VerbatimToolSequenceMiddleware:
    return VerbatimToolSequenceMiddleware(
        config,
        rule=rule,
        budget=budget,
        task_state=task_state,
        tool_names=tool_names,
        role_prompt=role_prompt,
        decision_placeholder=decision_placeholder,
        enable_tool_sequence=enable_tool_sequence,
        enable_decision_contract=enable_decision_contract,
    )


__all__ = [
    "MAX_CONTRACT_DIRECTIVE_MESSAGES",
    "MAX_TOOL_DIRECTIVE_MESSAGES",
    "VerbatimToolSequenceMiddleware",
    "build_tool_sequence_middleware",
    "create_tool_sequence_adapter",
    "observed_tool_calls",
]
