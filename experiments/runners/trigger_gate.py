"""Symmetric trigger rule shared by the new-host three-arm experiments.

The asymmetry this removes
--------------------------
In the first version of the OpenAI Agents / CrewAI experiments the plugin arm
compressed the payload on **every** model call (its metrics count renders, not
reductions), while the ``native_summary`` arm only summarised once the payload
crossed the soft budget. Comparing those two arms therefore compared two
different trigger conditions as well as two different compression mechanisms.

With this wrapper every arm obeys one rule:

    act only when the estimated payload exceeds the soft budget,
    otherwise pass the input through unchanged.

The property is directly testable and is asserted by the zero-API gate: below the
threshold the wrapped arm must produce a payload byte-identical to ``none``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Mapping, Sequence

from context_pruner.types import estimate_tokens

try:  # the filter protocol only exists when the SDK is installed
    from agents.run import ModelInputData
except ImportError:  # pragma: no cover - optional dependency path
    ModelInputData = None  # type: ignore[assignment]


class BudgetTriggeredFilter:
    """Wrap an SDK model-input filter and gate it on the shared soft budget.

    ``inner`` may be the Context-Pruner adapter or the native summariser; both
    expose ``filter_items`` (returning ``(items, payload)`` in whichever shape
    they define) and both are called through ``__call__`` by the SDK.
    """

    def __init__(
        self,
        inner: Any,
        *,
        soft_limit_tokens: int,
        token_counter: Callable[[str], int] | None = None,
    ) -> None:
        self.inner = inner
        self.soft_limit_tokens = max(1, int(soft_limit_tokens))
        self.token_counter = token_counter or estimate_tokens
        self.calls = 0
        self.triggered_calls = 0
        self.passthrough_calls = 0
        self.last_estimated_tokens = 0

    # -- SDK hook ---------------------------------------------------------

    async def __call__(self, data: Any) -> Any:
        model_data = data.model_data
        items = list(model_data.input or [])
        if not self.should_trigger(items):
            return ModelInputData(input=items, instructions=model_data.instructions)
        # The wrapped arm may be the native summariser (async) or the Context-Pruner
        # adapter (sync); awaiting a non-awaitable result is the bug this guards.
        result = self.inner(data)
        if inspect.isawaitable(result):
            result = await result
        return result

    def should_trigger(self, items: Sequence[Any]) -> bool:
        self.calls += 1
        self.last_estimated_tokens = self.estimated_tokens(items)
        if self.last_estimated_tokens <= self.soft_limit_tokens:
            self.passthrough_calls += 1
            return False
        self.triggered_calls += 1
        return True

    def estimated_tokens(self, items: Sequence[Any]) -> int:
        total = 0
        for item in items:
            text = (
                str(item)
                if not isinstance(item, Mapping)
                else repr(dict(item))
            )
            total += self.token_counter(text)
        return total

    def metrics_dict(self) -> dict[str, Any]:
        metrics = dict(self.inner.metrics_dict()) if hasattr(self.inner, "metrics_dict") else {}
        metrics.update(
            {
                "trigger_gate_calls": self.calls,
                "trigger_gate_triggered_calls": self.triggered_calls,
                "trigger_gate_passthrough_calls": self.passthrough_calls,
                "trigger_gate_soft_limit_tokens": self.soft_limit_tokens,
                "trigger_gate_last_estimated_tokens": self.last_estimated_tokens,
            }
        )
        return metrics


def should_trigger_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    soft_limit_tokens: int,
    token_counter: Callable[[str], int] | None = None,
) -> tuple[bool, int]:
    """Same rule for a flat chat-message list (the CrewAI host's shape).

    Returns ``(triggered, estimated_tokens)`` so callers can record the number.
    """
    counter = token_counter or estimate_tokens
    total = sum(counter(repr(dict(message))) for message in messages)
    return total > max(1, int(soft_limit_tokens)), total
