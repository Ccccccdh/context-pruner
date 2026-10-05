"""CrewAI v13 middleware: keep **every** frozen tool round verbatim (no directives).

Two pre-registered changes from v12 (protocol §2)
-------------------------------------------------
r12 proved that protecting the newest tool round is safe and deterministic -- 44/44
protected, zero re-additions, and r11's self-maintaining directive loop gone (0
errors, 0 remedies) -- but the plugin still lost the tool sequence in 5/16 samples
because the rounds that carry the *order* are older than the newest one.  r13
therefore raises the protected window from ``K = 1`` to ``K = 3``, which is the
frozen number of tool rounds the first role's tasks have.  With ``K = 3`` nothing is
left compressible for the first role, and the audit reports that directly
(``still_compressible``).

The second change is wording, not judging: the runner sends the frozen r8 contract
sentence with one pre-registered sentence appended that names the observed failure
mode ("the angle-bracket placeholder must be replaced by the actual value from this
run's tool evidence, and the angle brackets themselves must never be output again").
The judges, their strictness and every stored answer are untouched.

No instruction message is added by this middleware: the only thing it appends is the
adapter's own protected-group placeholder, which the adapter turns back into the
real tool messages byte-for-byte.
"""

from __future__ import annotations

from typing import Any, Sequence

from experiments.runners import crewai_pinned_evidence_v9 as v9

#: Frozen protocol value for r13: the first role's tasks all have three frozen tools,
#: so K=3 protects every round the role needs.
DEFAULT_K_RECENT_TOOL_ROUNDS = 3
PROTECTED_GROUP_PREFIX = v9.PROTECTED_GROUP_PREFIX


def is_tool_group_placeholder(message: Any) -> bool:
    """Whether one prepared message is the adapter's protected tool-group slot."""
    return isinstance(message, dict) and bool(
        message.get("_context_pruner_crewai_group")
    )


def group_placeholders(messages: Sequence[Any]) -> list[dict[str, Any]]:
    """The protected tool-group placeholders of a prepared list, in order."""
    return [dict(message) for message in messages if is_tool_group_placeholder(message)]


def newest_round_placeholders(
    messages: Sequence[Any], k: int
) -> list[dict[str, Any]]:
    """The newest ``k`` distinct tool-round placeholders, oldest-first.

    Distinct by group id so a repeated placeholder cannot be counted twice, and
    capped by what the prepared list actually contains: the middleware never invents
    a round the adapter did not protect.
    """
    ordered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for placeholder in group_placeholders(messages):
        group_id = str(placeholder.get("_context_pruner_crewai_group"))
        if group_id in seen:
            continue
        seen.add(group_id)
        ordered.append(placeholder)
    count = max(0, int(k))
    if count == 0:
        return []
    return ordered[-count:]


class RecencyToolRoundMiddleware(v9.PinnedEvidenceMiddleware):
    """v9 pinning plus verbatim protection of the newest K tool rounds."""

    def __init__(
        self,
        config: Any | None = None,
        *,
        rule: v9.PinnedRule | None = None,
        budget: Any | None = None,
        task_state: str = "",
        token_counter: Any | None = None,
        k_recent_tool_rounds: int = DEFAULT_K_RECENT_TOOL_ROUNDS,
    ) -> None:
        super().__init__(
            config, rule=rule, budget=budget, task_state=task_state,
            token_counter=token_counter,
        )
        self.k_recent_tool_rounds = max(0, int(k_recent_tool_rounds))
        self.recency_protected_rounds_total = 0
        self.recent_rounds_seen_total = 0
        self.recent_rounds_readded_total = 0
        self.recent_replacement_characters_total = 0
        self.recency_omitted_tool_rounds_total = 0
        self.recency_calls = 0
        self.recency_last_round_ids: list[str] = []

    # -- hook ------------------------------------------------------------
    def before_model(
        self,
        messages: Sequence[Any],
        *,
        task_state: str | None = None,
        reserved_tokens: int = 0,
    ) -> Any:
        prepared = list(messages)
        recent = newest_round_placeholders(prepared, self.k_recent_tool_rounds)
        total_rounds = len(group_placeholders(prepared))
        result = super().before_model(
            messages, task_state=task_state, reserved_tokens=reserved_tokens
        )
        served = list(result.messages)
        if recent:
            present = {
                str(message.get("_context_pruner_crewai_group"))
                for message in served
                if is_tool_group_placeholder(message)
            }
            missing = [
                placeholder for placeholder in recent
                if str(placeholder.get("_context_pruner_crewai_group")) not in present
            ]
            self.recency_calls += 1
            self.recent_rounds_seen_total += len(recent)
            self.recency_last_round_ids = [
                str(placeholder.get("_context_pruner_crewai_group")) for placeholder in recent
            ]
            if missing:
                served = [*served, *missing]
                self.recent_rounds_readded_total += len(missing)
                self.recent_replacement_characters_total += sum(
                    len(str(placeholder.get("content", ""))) for placeholder in missing
                )
            self.recency_protected_rounds_total += len(recent) - len(missing)
            self.recency_omitted_tool_rounds_total += max(0, total_rounds - len(recent))
        result.messages = served
        return result

    def metrics_dict(self) -> dict[str, Any]:
        data = super().metrics_dict()
        data.update(
            {
                "k_recent_tool_rounds": self.k_recent_tool_rounds,
                "recency_calls": self.recency_calls,
                "recency_protected_rounds_total": self.recency_protected_rounds_total,
                "recent_rounds_seen_total": self.recent_rounds_seen_total,
                "recent_rounds_readded_total": self.recent_rounds_readded_total,
                "recent_replacement_characters_total": (
                    self.recent_replacement_characters_total
                ),
                "recency_omitted_tool_rounds_total": (
                    self.recency_omitted_tool_rounds_total
                ),
                "recency_last_round_ids": list(self.recency_last_round_ids),
            }
        )
        return data


def build_recency_middleware(
    config: Any,
    *,
    rule: v9.PinnedRule,
    budget: Any,
    task_state: str = "",
    k_recent_tool_rounds: int = DEFAULT_K_RECENT_TOOL_ROUNDS,
) -> RecencyToolRoundMiddleware:
    return RecencyToolRoundMiddleware(
        config,
        rule=rule,
        budget=budget,
        task_state=task_state,
        k_recent_tool_rounds=k_recent_tool_rounds,
    )


def create_recency_adapter(
    config: Any,
    *,
    rule: v9.PinnedRule,
    budget: Any,
    task_state: str = "",
    fixed_reserved_tokens: int = 0,
    agent_roles: Sequence[str] | None = None,
    k_recent_tool_rounds: int = DEFAULT_K_RECENT_TOOL_ROUNDS,
):
    """A ``TriggeredCrewAIContextAdapter`` whose middleware keeps the newest rounds."""
    from experiments.runners import run_crewai_experiment as base

    adapter = base.TriggeredCrewAIContextAdapter(
        config,
        task_state=task_state,
        fixed_reserved_tokens=fixed_reserved_tokens,
        agent_roles=list(agent_roles or ()),
        trigger_soft_limit_tokens=budget.soft_limit_tokens,
    )
    adapter.middleware = build_recency_middleware(
        config,
        rule=rule,
        budget=budget,
        task_state=task_state,
        k_recent_tool_rounds=k_recent_tool_rounds,
    )
    return adapter


__all__ = [
    "DEFAULT_K_RECENT_TOOL_ROUNDS",
    "PROTECTED_GROUP_PREFIX",
    "RecencyToolRoundMiddleware",
    "build_recency_middleware",
    "create_recency_adapter",
    "group_placeholders",
    "is_tool_group_placeholder",
    "newest_round_placeholders",
]
