"""CrewAI v12 middleware: keep the newest K tool rounds verbatim (no directives).

Why v12 exists
--------------
Two attempts bracket the problem.  r10 (v9/v10, unchanged mechanism on fresh
tasks) lost quality 6/16 against a 16/16 baseline because the compressed view
dropped the tool rounds the first role still needed: 6 samples ended the tool loop
early or permuted the frozen order.  r11 then tried the obvious repair -- append a
host **directive** naming the next required tool -- and the trigger condition
(self-maintaining: "some frozen tool is still un-called") made the role loop on the
same tool 6 times in 12/16 samples, so tool-sequence consistency fell from 10/16 to
0/16 and the paired saving inverted to -68.28%.  A directive that keeps asserting
itself is not a safe lever on this host.

r12 therefore removes the text entirely and fixes the **structure** instead: on top
of the frozen v9 tiers (verbatim evidence pins, task-contract pin, counted
whole-prefix fallback) it guarantees that the **newest K tool rounds survive
compression verbatim**.

How the structure is protected
------------------------------
The CrewAI adapter replaces every tool call/observation pair with a placeholder
message carrying ``_context_pruner_crewai_group`` and restores the real messages
byte-for-byte after the middleware returns, so a placeholder that survives the
compressed view is a verbatim tool round.  This middleware:

  1. reads the placeholder messages out of the **prepared** (pre-compression) list,
     in order, and takes the newest ``K`` of them;
  2. runs the frozen v9 middleware (v9 pinning, task-contract pin and budget guard
     all still run, and the whole-prefix fallback is still counted);
  3. re-appends **only those newest-K placeholders** whose group id the compressed
     view dropped.  Nothing is synthesised: each re-added placeholder is the exact
     dict the adapter built from a group it will restore, so the model gets the real
     tool messages back, not a description of them;
  4. older rounds stay compressible, so the mechanism still reduces the view.

The omitted rounds remain constrained by the frozen guarantees: v9's tier-1 pins
keep every ``handle_facts`` literal and its tool evidence in the view, and if a
required literal would still be missing the counted tier-3 whole-prefix fallback
fires.  Omissions are reported per call (``recency_omitted_tool_rounds``), so "what
was compressed" is auditable instead of implicit.

A count of the re-additions is recorded per call and per sample, and the effect is
verified against the frozen trace afterwards: the plugin arm's first-role tool
sequence must equal the uncompressed arm's.  The rule is decided only from the
frozen task record and the pre-compression message list -- never from model
behaviour -- so it cannot become self-maintaining the way a directive can.
"""

from __future__ import annotations

from typing import Any, Sequence

from experiments.runners import crewai_pinned_evidence_v9 as v9

#: Frozen protocol value: how many of the newest tool rounds are kept verbatim.
#: ``K = 1`` keeps the round the role is currently working on; a tool round that
#: is no longer the newest one may be folded away as long as the frozen fact pins
#: and the counted whole-prefix fallback still hold.
DEFAULT_K_RECENT_TOOL_ROUNDS = 1
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
    capped by what the prepared list actually contains: the middleware never
    invents a round the adapter did not protect.
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
        # The frozen v9 tiers run first, unchanged, on the prepared list.
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
