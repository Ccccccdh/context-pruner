"""CrewAI v9 pinned-evidence middleware: required facts survive compression.

Why this module exists
----------------------
The frozen r8 batch (``runs/stage5-crewai/crewai-two-role-r8-multitask-01``)
recorded **first-pass fact completeness 3/9 for the plugin arm** while ``none``
and ``native_summary`` were 9/9.  ``.tooling/diagnose_crewai_r8.py`` recomputes
from the recorded rows that no required literal was ever dropped by the
compression pipeline: in all six lossy samples every required literal was still
present in the model view that produced the lossy handoff.  What the compressed
view *had* lost was the first-role task statement -- the message that tells the
role to end with one ``HANDOFF`` line.  In 5 of the 6 lossy samples the final
model view contained no ``HANDOFF`` contract at all, while all 21 complete
samples carried it.

What v9 changes (the only variable against r8)
----------------------------------------------
A middleware wrapper around the frozen ``ContextPrunerMiddleware`` that pins, in
three deterministic tiers, decided only from the frozen task record and the
pre-compression history -- never from model behaviour:

  tier 1  **verbatim tool evidence.** The frozen tool-result fragments that
          carry a required ``handle_facts`` literal are pinned verbatim as
          additional model-view messages.  They are the same bytes the tools
          returned (``json.dumps(..., sort_keys=True)`` of the frozen result),
          so the compressed view cannot paraphrase them.
  tier 2  **the task contract.** The first-role task statement (the message that
          carries the required literals together with the host's ReAct format
          markers and the task's own ``HANDOFF`` marker) is kept verbatim, so the
          output-format instruction is never compressed away.
  tier 3  **whole-prefix fallback.** If any required literal is still missing
          from the view after tier 1 and tier 2, the uncompressed
          pre-compression history is served instead, and the event is counted
          (``required_fact_whole_prefix_fallbacks`` with the reason attached).
          The fallback is deterministic and auditable.

Every tier is counted per model call, and the runner charges the pinned text to
the arm's own provider-token ledger because it sums the provider-reported input
of every call.

Design constraints
------------------
* Nothing in ``context_pruner`` or in the frozen r8 modules is modified: this is
  an override of one method of the public middleware class.
* The CrewAI adapter hands the middleware *placeholders* for protected tool
  groups (``[protected CrewAI tool group: ...]``) and restores the real messages
  afterwards on the same hook call.  The pin therefore does not guess what a
  placeholder hides: it (a) checks the literals that are already visible, (b)
  pins from the frozen task record, and (c) - because the restored group is
  re-checked by the audit -- the fallback only fires when a literal is missing
  from both the visible view and the frozen evidence.
* A pinned message is a plain ``{"role", "content"}`` dict without the internal
  ``_context_pruner_crewai_group`` key, so the adapter's group restore treats it
  as an ordinary message.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping, Sequence

from context_pruner import ContextBudget, ContextPluginConfig
from context_pruner.middleware import ContextPrunerMiddleware
from context_pruner.plugin import PluginHookResult
from context_pruner.types import estimate_tokens
from experiments.runners import crewai_semantic_equivalence_v8 as judge

#: The host's ReAct prompt template always mentions both markers; only the first
#: role's task statement couples them with the task's own frozen HANDOFF marker.
REACT_FORMAT_MARKERS = ("Thought:", "Final Answer:")

#: Bounds on the deterministic pin, so a pathological task cannot make the
#: pinned view unbounded.  Exceeding a bound triggers the counted tier-3
#: whole-prefix fallback instead of silently dropping a required literal.
MAX_PINNED_MESSAGES = 8
MAX_PINNED_CHARACTERS = 2400
PROTECTED_GROUP_PREFIX = "[protected CrewAI tool group:"


@dataclass(frozen=True)
class PinnedRule:
    """The frozen, per-task pin rule."""

    task_id: str
    required_literals: tuple[str, ...]
    marker: str
    evidence_fragments: tuple[str, ...]
    react_markers: tuple[str, ...] = REACT_FORMAT_MARKERS

    def as_record(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "required_literals": list(self.required_literals),
            "marker": self.marker,
            "react_markers": list(self.react_markers),
            "evidence_fragment_count": len(self.evidence_fragments),
        }


def _fold(text: str) -> str:
    return judge._fold(text)


def _carries(text: str, literal: str) -> bool:
    return judge._literal_present(_fold(text), literal)


def _tool_result_text(tool: Mapping[str, Any]) -> str:
    return json.dumps(tool["result"], ensure_ascii=False, sort_keys=True)


def evidence_fragments(task: Mapping[str, Any]) -> tuple[str, ...]:
    """Verbatim tool evidence, one entry per tool, in call order.

    These are exactly the bytes ``run_crewai_handoff_v8.build_tools`` returns, so
    a pinned fragment is byte-identical to the tool observation the role saw.
    """
    return tuple(_tool_result_text(tool) for tool in task["tools"])


def pinned_rule(task: Mapping[str, Any]) -> PinnedRule:
    """Pin rule for one frozen task record.

    The required literals are the task's own ``handle_facts`` -- the exact list
    the handoff judge and the independent audit read -- so the mechanism cannot
    be tuned per sample after the fact.
    """
    literals = [str(item) for item in task["handle_facts"]]
    if not literals:
        raise ValueError(f"task {task.get('task_id')} has no handle_facts to pin")
    return PinnedRule(
        task_id=str(task["task_id"]),
        required_literals=tuple(literals),
        marker=str(task.get("handoff_marker") or "HANDOFF"),
        evidence_fragments=evidence_fragments(task),
    )


def _text_of(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("content", "") or "")
    return str(getattr(message, "content", "") or "")


def _role_of(message: Any) -> str:
    if isinstance(message, Mapping):
        role = message.get("role") or message.get("type") or "user"
    else:
        role = getattr(message, "role", None) or getattr(message, "type", "user")
    return str(role).lower()


def _as_pinned(message: Any, role: str | None = None) -> dict[str, str]:
    """A plain text copy: no framework-internal keys are carried over."""
    return {"role": role or _role_of(message), "content": _text_of(message)}


def _messages_tokens(messages: Sequence[Any]) -> int:
    return sum(estimate_tokens(_text_of(message)) for message in messages)


def _is_protected_placeholder(message: Any) -> bool:
    return PROTECTED_GROUP_PREFIX in _text_of(message)


class PinnedEvidenceMiddleware(ContextPrunerMiddleware):
    """Compress, then pin back the required evidence and the task contract."""

    def __init__(
        self,
        config: ContextPluginConfig | None = None,
        *,
        rule: PinnedRule | None = None,
        budget: ContextBudget | None = None,
        task_state: str = "",
        token_counter: Any | None = None,
    ) -> None:
        super().__init__(config, task_state=task_state, token_counter=token_counter)
        self.rule = rule
        self.budget = budget
        self.pinned_events = 0
        self.pinned_message_total = 0
        self.pinned_characters_total = 0
        self.required_fact_whole_prefix_fallbacks = 0
        self.required_facts_missing_after_pin_total = 0
        self.tier_counts: dict[str, int] = {}
        self.fallback_reasons: dict[str, int] = {}
        self.served_mode = "compressed_plus_pinned"

    # -- pinning ---------------------------------------------------------
    def _evidence_pins(self, missing: Sequence[str]) -> list[dict[str, str]]:
        """Shortest frozen tool-evidence fragments covering the missing facts."""
        if not missing or self.rule is None:
            return []
        order = sorted(
            range(len(self.rule.evidence_fragments)),
            key=lambda index: (len(self.rule.evidence_fragments[index]), index),
        )
        chosen: list[int] = []
        covered: set[str] = set()
        for literal in missing:
            for index in order:
                if index in chosen:
                    continue
                if _carries(self.rule.evidence_fragments[index], literal):
                    chosen.append(index)
                    covered.add(literal)
                    break
        chosen.sort()
        budget = MAX_PINNED_CHARACTERS
        pins: list[dict[str, str]] = []
        for index in chosen:
            text = self.rule.evidence_fragments[index]
            if len(text) > budget and pins:
                break
            budget -= len(text)
            pins.append({"role": "user", "content": f"Current tool evidence: {text}"})
        return pins

    def _is_contract_message(self, message: Any) -> bool:
        """Whether one message is the role's output-format contract.

        The task statement is the only non-system message that carries the task's
        own frozen HANDOFF marker; the host's system prompt carries the ReAct
        syntax template but never that frozen marker, so this test cannot select
        the system prompt by accident.  No ``Thought:``/``Final Answer:``
        requirement is imposed because the host splits those markers into its own
        scaffolding messages.
        """
        if self.rule is None:
            return False
        if _role_of(message) == "system" or _is_protected_placeholder(message):
            return False
        return self.rule.marker in _text_of(message)

    def _contract_pin(self, raw: Sequence[Any], served: Sequence[Any]) -> list[dict[str, str]]:
        """The task statement, pinned when the served view no longer carries it."""
        if self.rule is None:
            return []
        if any(self._is_contract_message(message) for message in served):
            return []
        for message in reversed(list(raw)):
            if self._is_contract_message(message):
                return [_as_pinned(message, role="user")]
        return []

    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        reserved_tokens: int = 0,
    ) -> PluginHookResult:
        raw = list(messages)
        result = ContextPrunerMiddleware.before_model(
            self, messages, task_state=task_state, reserved_tokens=reserved_tokens
        )
        if self.rule is None:
            return result
        served = list(result.messages)
        served_text = "\n".join(_text_of(message) for message in served)
        invisible_text = "\n".join(
            _text_of(message) for message in raw if _is_protected_placeholder(message)
        )
        missing = [
            literal
            for literal in self.rule.required_literals
            if not _carries(served_text, literal) and not _carries(invisible_text, literal)
        ]
        tiers: list[str] = []
        contract = self._contract_pin(raw, served)
        if not missing:
            self.tier_counts["tier1_evidence_already_present"] = (
                self.tier_counts.get("tier1_evidence_already_present", 0) + 1
            )
            pins = contract
            if contract:
                tiers.append("tier2_task_contract")
        else:
            pins = self._evidence_pins(missing) + contract
            if len(pins) > MAX_PINNED_MESSAGES:
                pins = pins[:MAX_PINNED_MESSAGES]
            if pins:
                tiers.append("tier1_evidence_pinned")
            if contract:
                tiers.append("tier2_task_contract")
        if pins:
            served = [*served, *pins]
            self.pinned_events += 1
            self.pinned_message_total += len(pins)
            self.pinned_characters_total += sum(len(pin["content"]) for pin in pins)
        still_missing = [
            literal
            for literal in self.rule.required_literals
            if not _carries("\n".join(_text_of(item) for item in served), literal)
            and not _carries(invisible_text, literal)
        ]
        if still_missing:
            served = [_as_pinned(message) for message in raw]
            self.required_fact_whole_prefix_fallbacks += 1
            self.required_facts_missing_after_pin_total += len(still_missing)
            self.fallback_reasons["required_fact_missing"] = (
                self.fallback_reasons.get("required_fact_missing", 0) + 1
            )
            tiers.append("tier3_whole_prefix_fallback")
        elif self.budget is not None and _messages_tokens(served) > (
            self.budget.reserve(max(0, int(reserved_tokens))).hard_limit_tokens
        ):
            served = [_as_pinned(message) for message in raw]
            self.required_fact_whole_prefix_fallbacks += 1
            self.fallback_reasons["pin_exceeds_hard_budget"] = (
                self.fallback_reasons.get("pin_exceeds_hard_budget", 0) + 1
            )
            tiers.append("tier3_whole_prefix_fallback")
        for tier in tiers:
            self.tier_counts[tier] = self.tier_counts.get(tier, 0) + 1
        self.served_mode = (
            "whole_prefix_fallback"
            if "tier3_whole_prefix_fallback" in tiers
            else "compressed_plus_pinned"
        )
        if not tiers:
            self.tier_counts["tier0_nothing_needed"] = (
                self.tier_counts.get("tier0_nothing_needed", 0) + 1
            )
        result.messages = served
        return result

    def metrics_dict(self) -> dict[str, Any]:
        data = ContextPrunerMiddleware.metrics_dict(self)
        data.update(
            {
                "pinned_events": self.pinned_events,
                "pinned_message_total": self.pinned_message_total,
                "pinned_characters_total": self.pinned_characters_total,
                "required_fact_whole_prefix_fallbacks": (
                    self.required_fact_whole_prefix_fallbacks
                ),
                "required_facts_missing_after_pin_total": (
                    self.required_facts_missing_after_pin_total
                ),
                "pin_tier_counts": dict(self.tier_counts),
                "pin_fallback_reasons": dict(self.fallback_reasons),
                "pin_served_mode": self.served_mode,
                "pin_rule": self.rule.as_record() if self.rule else None,
            }
        )
        return data


def build_pinned_middleware(
    config: ContextPluginConfig,
    *,
    rule: PinnedRule,
    budget: ContextBudget,
    task_state: str = "",
) -> PinnedEvidenceMiddleware:
    return PinnedEvidenceMiddleware(
        config=config,
        task_state=task_state,
        rule=rule,
        budget=budget,
    )


def create_pinned_adapter(
    config: ContextPluginConfig,
    *,
    rule: PinnedRule,
    budget: ContextBudget,
    task_state: str = "",
    fixed_reserved_tokens: int = 0,
    agent_roles: Sequence[str] | None = None,
):
    """A ``TriggeredCrewAIContextAdapter`` whose middleware pins required facts."""
    from experiments.runners import run_crewai_experiment as base

    adapter = base.TriggeredCrewAIContextAdapter(
        config,
        task_state=task_state,
        fixed_reserved_tokens=fixed_reserved_tokens,
        agent_roles=list(agent_roles or ()),
        trigger_soft_limit_tokens=budget.soft_limit_tokens,
    )
    adapter.middleware = build_pinned_middleware(
        config, rule=rule, budget=budget, task_state=task_state
    )
    return adapter


__all__ = [
    "MAX_PINNED_CHARACTERS",
    "MAX_PINNED_MESSAGES",
    "PROTECTED_GROUP_PREFIX",
    "PinnedEvidenceMiddleware",
    "PinnedRule",
    "REACT_FORMAT_MARKERS",
    "build_pinned_middleware",
    "create_pinned_adapter",
    "evidence_fragments",
    "pinned_rule",
]
