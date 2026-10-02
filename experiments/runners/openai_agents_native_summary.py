"""`native_summary` arm for the OpenAI Agents SDK Runner (host-native baseline).

The OpenAI Agents SDK exposes exactly one client-side hook that can reshape the
model input: ``RunConfig.call_model_input_filter``.  The SDK itself does not
provide conversation summarisation, so this module implements the closest
host-native equivalent of the baseline that the OpenHands three-arm protocol
uses (``LLMSummarizingCondenser``):

  * the filter is *always installed* but only acts when the estimated input
    token count exceeds the context budget's soft limit;
  * when it acts, the **older prefix** of the Responses item list is replaced by
    a single summary message produced by one auxiliary LLM call, using the same
    model family and the same synthetic-payload rules as the agent loop;
  * the **recent window** (the last user message plus enough trailing items to
    fill the retention target) is kept verbatim, and every Responses tool
    group is cut at a group boundary so a ``function_call`` is never separated
    from its ``function_call_output``;
  * nothing is archived, scored, ranked, or recovered - there is no plugin
    machinery in this arm.  The summariser only folds older items into prose.

Fairness properties that make this arm comparable with ``none`` and
``pruner_v1``:

  * it is subject to the *same* global API request budget object, so a summary
    call is charged exactly like an agent call;
  * its auxiliary token usage is recorded separately
    (``summary_input_tokens`` / ``summary_output_tokens``) and added to the
    three-arm total, so savings cannot be manufactured by moving work into an
    unmetered side channel;
  * the summary is only accepted when it is actually smaller than the items it
    replaces, and any provider error leaves the input untouched (a failed
    summary degrades to the ``none`` arm for that call, never to an error).

Cost accounting note: like the OpenHands ``native_summary`` arm, this
summariser does not itself call ``Context-Pruner``; it is the baseline that the
plugin has to beat, including its own summary-call cost.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any, Callable, Mapping, Sequence

from context_pruner.types import estimate_tokens

from experiments.runners.native_summary_common import (
    SUMMARY_PREFIX,
    SummaryCallLedger,
    serialized_tokens,
    summarize_block,
)

#: The summary must be a single user-role message so the SDK treats it as normal
#: conversation history, never as a tool definition or a Responses item.
SUMMARY_ROLE = "user"


class NativeSummaryInputFilter:
    """Fold an older prefix of Responses items into one summarising message.

    The filter is an **async** SDK input filter, which the SDK awaits inside its
    own event loop (``run_internal.turn_preparation`` awaits an awaitable filter
    result). That is what lets the summariser await the async chat-completions
    client on the *same* loop that owns the client's connection pool.

    The filter is deliberately stateless across calls: the SDK re-sends the full
    item list every turn, and the previous summary is already part of that list,
    so re-summarising naturally folds summary-into-summary instead of growing a
    parallel history.
    """

    def __init__(
        self,
        client_factory: Any,
        *,
        model_name: str,
        soft_limit_tokens: int,
        hard_limit_tokens: int,
        target_tokens: int,
        retention_tokens: int | None = None,
        pinned_units: int = 1,
        regrowth_ratio: float = 0.25,
        max_summary_tokens: int = 1024,
        max_summary_calls: int = 8,
        temperature: float = 0.0,
        timeout_seconds: float = 60.0,
        token_counter: Callable[[str], int] | None = None,
        request_budget: Any | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> None:
        #: Called once per summary request to build an async client on the loop
        #: that will await it. A client created on a different loop raises
        #: "Event loop is closed", so the factory - not a shared client - is what
        #: this filter stores.
        self.client_factory = client_factory
        #: Retained for the synchronous metric checks; never used for requests.
        self.client = client_factory
        self.model_name = str(model_name)
        self.soft_limit_tokens = max(1, int(soft_limit_tokens))
        self.hard_limit_tokens = max(self.soft_limit_tokens, int(hard_limit_tokens))
        self.target_tokens = max(1, int(target_tokens))
        # How many recent tokens must survive verbatim.  Defaults to the target
        # budget so the boundary matches the budget the plugin arm aims at.
        self.retention_tokens = max(
            1, int(retention_tokens if retention_tokens is not None else self.target_tokens)
        )
        #: Leading units (the task statement) that are always sent verbatim.
        self.pinned_units = max(0, int(pinned_units))
        self.max_summary_tokens = max(64, int(max_summary_tokens))
        self.max_summary_calls = max(0, int(max_summary_calls))
        self.temperature = float(temperature)
        self.timeout_seconds = float(timeout_seconds)
        self.token_counter = token_counter or estimate_tokens
        self.request_budget = request_budget
        #: Provider switches forwarded with the summary request (for this
        #: provider: reasoning disabled), mirroring the agent loop's own request.
        self.extra_body = dict(extra_body) if extra_body else None
        self.ledger = SummaryCallLedger()
        self.filter_calls = 0
        self.triggered_calls = 0
        self.summarised_calls = 0
        self.skipped_reason: str | None = None
        self.removed_key_misses = 0
        self.last_reduction_tokens = 0
        #: Payload size immediately after the last applied summary. A payload that
        #: has not grown since is already summarised, so re-summarising it would
        #: charge the arm for the same content on every turn. This mirrors how a
        #: real host summariser behaves and keeps the baseline from being
        #: penalised for a bookkeeping artefact rather than for its policy.
        self._last_compacted_tokens = 0
        #: Required growth before the arm summarises the same conversation again.
        self.regrowth_ratio = float(regrowth_ratio)
        self.growth_guard_skips = 0

    # -- SDK hook ---------------------------------------------------------

    async def __call__(self, data: Any) -> Any:
        """Return ``ModelInputData``; the SDK awaits this inside its own loop."""
        from agents.run import ModelInputData

        model_data = data.model_data
        items = list(model_data.input or [])
        instructions = model_data.instructions
        kept, summary = await self.filter_items(items)
        if summary is None:
            return ModelInputData(input=items, instructions=instructions)
        summary_message = {**summary, "role": SUMMARY_ROLE}
        return ModelInputData(
            input=[*kept[: self.pinned_units], summary_message, *kept[self.pinned_units :]],
            instructions=instructions,
        )

    # -- core -------------------------------------------------------------

    async def filter_items(
        self,
        items: Sequence[Any],
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """Return ``(retained_items, summary_message_or_None)``.

        ``summary_message`` is ``None`` whenever the input is left untouched.
        When a summary is produced, ``retained_items`` keeps the pinned leading
        units first and the recent window after them; the caller inserts the
        summary between the two slices.
        """
        self.filter_calls += 1
        self.skipped_reason = None
        self.last_reduction_tokens = 0
        total = self._input_tokens(items)
        if total <= self.soft_limit_tokens:
            self.skipped_reason = "below_soft_limit"
            return list(items), None
        self.triggered_calls += 1
        threshold = self._last_compacted_tokens * (1.0 + self.regrowth_ratio)
        if self._last_compacted_tokens and total <= threshold:
            self.skipped_reason = "no_growth_since_summary"
            self.growth_guard_skips += 1
            return list(items), None
        if self.ledger.calls >= self.max_summary_calls:
            self.skipped_reason = "summary_call_limit_reached"
            return list(items), None
        prefix, head, tail = _split_items(
            items,
            self.retention_tokens,
            self.token_counter,
            pinned_units=self.pinned_units,
        )
        if not head:
            self.skipped_reason = "no_summarisable_prefix"
            return list(items), None
        summary_text = await self._summarise([*prefix, *head])
        if summary_text is None:
            return list(items), None
        message = {"content": summary_text}
        retained = [*prefix, *tail]
        if not retained:
            self.skipped_reason = "no_retained_window"
            return list(items), None
        # Only accept a summary that actually shrinks the payload: a summary that
        # costs more than it removes is a regression, not a compression.
        proposed = [*prefix, message, *tail]
        if self._input_tokens(proposed) >= total:
            self.skipped_reason = "summary_not_smaller"
            return list(items), None
        self.summarised_calls += 1
        compacted = self._input_tokens(proposed)
        self.last_reduction_tokens = total - compacted
        self._last_compacted_tokens = compacted
        return retained, message

    def could_shrink(self, items: Sequence[Any]) -> bool:
        """Report whether the filter *would* attempt a summary (no API call)."""
        if self._input_tokens(items) <= self.soft_limit_tokens:
            return False
        _, head, _ = _split_items(
            items,
            self.retention_tokens,
            self.token_counter,
            pinned_units=self.pinned_units,
        )
        return bool(head)

    # -- internals --------------------------------------------------------

    def _input_tokens(self, items: Sequence[Any]) -> int:
        return serialized_tokens(items, self.token_counter, serializer=_jsonable_text)

    async def _summarise(self, head: Sequence[Any]) -> str | None:
        body = "\n".join(_jsonable_text(item) for item in head)
        calls_before = self.ledger.calls
        if self.request_budget is not None:
            try:
                self.request_budget.consume()
            except Exception as error:  # global cap reached: degrade to baseline
                self.ledger.record_failure(error)
                self.skipped_reason = "request_budget_exhausted"
                return None
        client = self.client_factory()
        try:
            summary = await summarize_block(
                client,
                model=self.model_name,
                body=body,
                ledger=self.ledger,
                max_tokens=self.max_summary_tokens,
                temperature=self.temperature,
                timeout=self.timeout_seconds,
                estimated_input_tokens=self.token_counter(body),
                extra_body=self.extra_body,
            )
        finally:
            await _aclose(client)
        if summary is None or self.ledger.calls == calls_before:
            self.skipped_reason = (
                self.skipped_reason or "summary_call_failed"
            )
            return None
        return summary

    def metrics_dict(self) -> dict[str, Any]:
        metrics = {
            "native_summary_filter_calls": self.filter_calls,
            "native_summary_triggered_calls": self.triggered_calls,
            "native_summary_applied_calls": self.summarised_calls,
            "native_summary_skipped_reason": self.skipped_reason,
            "native_summary_removed_key_misses": self.removed_key_misses,
            "native_summary_growth_guard_skips": self.growth_guard_skips,
            "native_summary_last_compacted_tokens": self._last_compacted_tokens,
            "native_summary_last_reduction_tokens": self.last_reduction_tokens,
            "native_summary_soft_limit_tokens": self.soft_limit_tokens,
            "native_summary_retention_tokens": self.retention_tokens,
        }
        metrics.update(self.ledger.metrics_dict())
        return metrics


async def _aclose(client: Any) -> None:
    """Close a per-request async client, ignoring provider-side close errors."""
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if hasattr(result, "__await__"):
            await result
    except Exception:
        return


def _split_items(
    items: Sequence[Any],
    retention_tokens: int,
    token_counter: Callable[[str], int],
    *,
    pinned_units: int = 1,
) -> tuple[list[Any], list[Any], list[Any]]:
    """Split ``items`` into ``(pinned, head, tail)``.

    ``tail`` keeps the most recent tool groups up to ``retention_tokens`` and is
    always cut at a group boundary.  ``pinned`` holds the leading ``pinned_units``
    units (by default the task statement) which are always sent verbatim, so the
    task identity can never be reduced to a summary.  ``head`` is what the
    summariser is allowed to fold into prose.  The pinned units are passed to the
    summariser as well, because the summary replaces them in the semantic view of
    the conversation even though a verbatim copy is kept in front of it.
    """
    units = _group_units(items)
    pinned = min(max(0, int(pinned_units)), max(0, len(units) - 1))
    if len(units) <= pinned + 1:
        return [], [], list(items)
    tail_start = len(units)
    tail_tokens = 0
    for index in range(len(units) - 1, pinned - 1, -1):
        size = sum(token_counter(_jsonable_text(item)) for item in units[index])
        if tail_tokens + size > retention_tokens and index < len(units) - 1:
            break
        tail_tokens += size
        tail_start = index
    tail_start = max(tail_start, pinned + 1)
    pinned_items = [item for unit in units[:pinned] for item in unit]
    head = [item for unit in units[pinned:tail_start] for item in unit]
    tail = [item for unit in units[tail_start:] for item in unit]
    return pinned_items, head, tail


def _group_units(items: Sequence[Any]) -> list[list[Any]]:
    """Group Responses items so call/output pairs are never split."""
    units: list[list[Any]] = []
    current: list[Any] = []
    for item in items:
        if _is_message_item(item) and _message_role(item) in {"user", "system", "developer"}:
            if current:
                units.append(current)
                current = []
            units.append([item])
            continue
        current.append(item)
    if current:
        units.append(current)
    return units


def _is_message_item(item: Any) -> bool:
    if isinstance(item, Mapping):
        item_type = str(item.get("type") or "").lower()
        if item_type and item_type not in {"message", "input_text"}:
            return False
        return "role" in item and "content" in item
    role = getattr(item, "role", None)
    content_present = hasattr(item, "content")
    item_type = str(getattr(item, "type", "") or "").lower()
    return bool(role) and content_present and item_type in {"", "message"}


def _message_role(item: Any) -> str:
    if isinstance(item, Mapping):
        return str(item.get("role") or "").lower()
    return str(getattr(item, "role", "") or "").lower()


def _jsonable_text(item: Any) -> str:
    if isinstance(item, Mapping):
        return json.dumps(dict(item), ensure_ascii=False, sort_keys=True, default=str)
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        return json.dumps(model_dump(exclude_none=True), ensure_ascii=False, default=str)
    return str(item)


def tool_call_counts(items: Sequence[Any]) -> Counter[str]:
    """Count ``function_call`` items by call id; used by offline invariance tests."""
    calls: Counter[str] = Counter()
    for item in items:
        item_type = str(
            item.get("type") if isinstance(item, Mapping) else getattr(item, "type", "")
        ).lower()
        if not item_type.endswith("_call"):
            continue
        call_id = str(
            item.get("call_id") if isinstance(item, Mapping) else getattr(item, "call_id", "")
        )
        if call_id:
            calls[call_id] += 1
    return calls
