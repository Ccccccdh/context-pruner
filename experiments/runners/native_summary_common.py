"""Shared transport pieces for the host-native ``native_summary`` arms.

The OpenAI Agents and CrewAI experiments each own a different *splitting* rule -
Responses items are grouped so a ``function_call`` never separates from its
``function_call_output``, while CrewAI carries a flat role/content message list -
but both must make the summary request, account for its cost and degrade on
failure in exactly the same way. This module holds that shared part so the two
arms cannot drift apart in their accounting.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any, Callable, Mapping, Sequence

#: Prefix that marks an injected summary so reports and operators can spot it.
SUMMARY_PREFIX = "[conversation summary]"

SUMMARY_INSTRUCTIONS = (
    "You compress an agent conversation so it fits a smaller context window. "
    "Rewrite the conversation prefix below as terse operational notes. Preserve every "
    "codename, identifier, file path, numeric value, tool name, tool argument and tool "
    "result value verbatim; these are the only facts the agent needs. Drop all prose, "
    "repetition, greetings and speculation. Keep the notes in chronological order. "
    "Do not invent facts that are absent from the conversation. Reply with the notes "
    "only: no preamble, no markdown headings, no code fences."
)


class SummaryCallLedger:
    """Records auxiliary summary requests so the report can charge them back."""

    def __init__(self) -> None:
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_seconds = 0.0
        self.last_error = ""

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def record(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        latency: float,
    ) -> None:
        self.calls += 1
        self.input_tokens += int(input_tokens)
        self.output_tokens += int(output_tokens)
        self.latency_seconds += float(latency)

    def record_failure(self, error: BaseException | str) -> None:
        self.failures += 1
        self.last_error = (
            f"{type(error).__name__}: {error}"[:300]
            if isinstance(error, BaseException)
            else str(error)[:300]
        )

    def metrics_dict(self) -> dict[str, Any]:
        return {
            "native_summary_calls": self.calls,
            "native_summary_failures": self.failures,
            "native_summary_input_tokens": self.input_tokens,
            "native_summary_output_tokens": self.output_tokens,
            "native_summary_latency_seconds": self.latency_seconds,
            "native_summary_last_error": self.last_error,
        }


class SummaryRequestError(RuntimeError):
    """Raised when the auxiliary summary request cannot be completed."""


# ---------------------------------------------------------------------------
# Transport
#
# The auxiliary summariser talks to the *async* chat-completions surface, but
# the two hosts call it from different places:
#
#   * the OpenAI Agents SDK awaits an async input filter inside its own loop, so
#     there the request is simply awaited on the caller's loop;
#   * CrewAI calls the provider synchronously (possibly from a worker thread),
#     so there the request runs on one *persistent* event loop owned by a
#     background thread.
#
# A client must be created on the same loop that will await it: an `AsyncOpenAI`
# built on one loop and awaited on another fails with "Event loop is closed".
# Both paths therefore accept a client *factory* and create the client on the
# loop that uses it.
# ---------------------------------------------------------------------------


class LoopRunner:
    """Owns one persistent event loop in a background thread.

    Using a single long-lived loop (instead of a fresh loop per request) is what
    makes the async connection pool usable at all: the pool's connections are
    bound to the loop that created them, so a second loop cannot reuse them.
    """

    def __init__(self, name: str = "native-summary-loop") -> None:
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._thread.start()
        self._ready.wait(timeout=10.0)

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._ready.set()
        loop.run_forever()

    def run(self, coro) -> Any:
        if self._loop is None or not self._thread.is_alive():
            raise SummaryRequestError("summary event loop is not running")
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout=5.0)


async def summarize_block(
    client: Any,
    *,
    model: str,
    body: str,
    ledger: SummaryCallLedger,
    max_tokens: int,
    temperature: float,
    timeout: float,
    estimated_input_tokens: int = 0,
    extra_body: Mapping[str, Any] | None = None,
) -> str | None:
    """Summarise ``body`` on the caller's loop, recording cost.

    Returns ``None`` on any failure: a failed summary must degrade the arm to the
    uncompressed baseline, never raise into the host's agent loop.

    ``extra_body`` carries the provider-specific switches the host's own agent
    loop already uses - for this provider, disabling the reasoning channel.
    Without it a reasoning model can spend the whole ``max_tokens`` budget on
    hidden reasoning and return an empty message, which silently disables the arm
    while still paying for the request.
    """
    started = time.perf_counter()
    request: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SUMMARY_INSTRUCTIONS},
            {"role": "user", "content": body},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra_body:
        request["extra_body"] = dict(extra_body)
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(**request),
            timeout=timeout,
        )
    except Exception as error:  # provider failure must degrade, never raise
        ledger.record_failure(error)
        return None
    latency = time.perf_counter() - started
    prompt_tokens, completion_tokens = usage_tokens(response)
    ledger.record(
        input_tokens=prompt_tokens or int(estimated_input_tokens),
        output_tokens=completion_tokens,
        latency=latency,
    )
    text = response_text(response)
    if not text:
        ledger.record_failure("empty summary response")
        return None
    return f"{SUMMARY_PREFIX}\n{text.strip()}"


def usage_tokens(response: Any) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0
    prompt = getattr(usage, "prompt_tokens", None)
    if prompt is None:
        prompt = getattr(usage, "input_tokens", 0)
    completion = getattr(usage, "completion_tokens", None)
    if completion is None:
        completion = getattr(usage, "output_tokens", 0)
    return int(prompt or 0), int(completion or 0)


def response_text(response: Any) -> str:
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None)
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:  # some providers return content parts
        text = getattr(part, "text", None)
        if text is None and isinstance(part, Mapping):
            text = part.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def serialized_tokens(
    items: Sequence[Any],
    token_counter,
    *,
    serializer,
) -> int:
    return sum(token_counter(serializer(item)) for item in items)
