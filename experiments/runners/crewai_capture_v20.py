"""r20 capture: the r19 per-attempt capture plus a metered native-summary client.

Why a new file
--------------
``experiments/runners/crewai_capture_v19.py`` is pinned by the r19 and r20 acquisition freezes,
and a pinned source is never edited.  The r20 three-arm batch needs exactly one thing v19 does
not have: the ``native_summary`` arm makes its summary request through its *own* async client,
so without a meter on that client the arm would have an unmetered side channel -- the summary
request would be charged to the request budget but its cache split, price tier and timestamp
would be invisible.  That is the same hole the r17 accounting rules call out, and this file
closes it without touching the pinned capture.

Everything the agent loop itself records is inherited verbatim from v19.
"""

from __future__ import annotations

from typing import Any

from experiments.runners import crewai_capture_v19 as v19
from experiments.runners import run_crewai_experiment as base

#: The per-attempt capture class is the pinned v19 one, unchanged and re-exported: this module
#: adds a meter, it does not reimplement the capture the frozen acquisition path validated.
CapturedOpenAICompatCrewAILLM = v19.CapturedOpenAICompatCrewAILLM

REUSED_FROM_V19 = {
    "source_file": "experiments/runners/crewai_capture_v19.py",
    "source_sha256_at_build": "03419f730c9930f425ee53a6ab77874b8bd72af90f5dd22dba48ec19674e29b9",
    "frozen_by": [
        "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_ACQUISITION_01.json",
    ],
    "reused_changes": [
        "CapturedOpenAICompatCrewAILLM is subclassed, not edited",
        "a per-request summary meter is added for the native_summary arm's own client",
        "everything else (per-attempt capture, cache metering, off-peak stamps) is inherited",
    ],
}


class CapturedNativeSummaryCrewAILLM(base.NativeSummaryCrewAILLM):
    """The r20 native-summary arm: v19-shaped capture on the agent loop, a meter on the summary.

    ``NativeSummaryCrewAILLM`` does not keep the factory it was given, so this subclass keeps
    it, rebuilds the summary transport through :class:`SummaryClientMeter`, and records one
    entry per summary request.  The agent loop inherits ``call`` from the v19 capture, so the
    per-attempt capture, the cache split and the price-tier stamps are the pinned ones.
    """

    def __init__(self, *, executed_tool_trace: list[str],
                 frozen_tool_names: Any, summary_records: list[dict[str, Any]] | None = None,
                 **kwargs: Any) -> None:
        # Every custom attribute is set AFTER the parent's __init__: it resets the instance
        # __dict__, so anything assigned before it disappears, and the failure surfaces later
        # as a missing attribute on the first real call rather than here.
        super().__init__(**kwargs)
        _set(self, "summary_records",
             summary_records if summary_records is not None else [])
        _set(self, "capture", [])
        _set(self, "executed_tool_trace", executed_tool_trace)
        _set(self, "frozen_tool_names", tuple(str(name) for name in frozen_tool_names))
        _set(self, "_bound_tool_count", 0)
        # The parent builds the summary transport during its own ``__init__`` and keeps no
        # reference to the factory, so the transport it built is wrapped **here**: replacing
        # the factory afterwards would be undone, because ``_compact`` rebuilds the client on
        # every call.  Wrapping the built client is what actually survives.
        self.attach_summary_meter()

    def attach_summary_meter(self) -> None:
        """Put the meter between the summary path and the provider.

        Idempotent and safe to call again: ``_compact`` closes and re-creates the client on
        each summary attempt, so the meter is re-installed on every call as well.
        """
        client = getattr(self, "summary_client", None)
        if client is None or isinstance(client, SummaryClientMeter):
            return
        _set(self, "summary_client", SummaryClientMeter(client, self.summary_records))

    def _compact(self, *args: Any, **kwargs: Any) -> Any:
        # Re-install the meter before delegating: if the parent rebuilds its client from the
        # factory during this call, the rebuilt client is wrapped by the time it is used.
        self.attach_summary_meter()
        return super()._compact(*args, **kwargs)


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class _MeteredCompletions:
    def __init__(self, parent: "SummaryClientMeter") -> None:
        self._parent = parent

    async def create(self, **request: Any) -> Any:
        meter = self._parent
        slot = len(meter.records) + 1
        started = _utc_now()
        entry: dict[str, Any] = {
            "kind": "native_summary_request",
            "request_slot": slot,
            "utc_started": started,
            "utc_finished": None,
            "off_peak_window": None,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "cache_tokens_source": "",
            "input_tokens": 0,
            "output_tokens": 0,
            "status": "pending",
            "error_type": "",
        }
        meter.records.append(entry)
        try:
            response = await meter.client.chat.completions.create(**request)
        except Exception as error:
            entry.update({"status": "error", "error_type": type(error).__name__,
                          "utc_finished": _utc_now()})
            raise
        usage = getattr(response, "usage", None)
        input_tokens = 0
        output_tokens = 0
        if usage is not None:
            input_tokens = int(getattr(usage, "prompt_tokens", 0)
                               or getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0)
                                or getattr(usage, "output_tokens", 0) or 0)
        cache = v19._cache_tokens(usage, input_tokens)
        entry.update({
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "prompt_cache_hit_tokens": cache["hit"],
            "prompt_cache_miss_tokens": cache["miss"],
            "cache_tokens_source": cache["source"],
            "utc_finished": _utc_now(),
            "off_peak_window": v19.off_peak(started),
            "status": "success",
        })
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parent.client.chat.completions, name)


class _MeteredChat:
    def __init__(self, parent: "SummaryClientMeter") -> None:
        self._parent = parent
        self.completions = _MeteredCompletions(parent)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parent.__dict__["client"].chat, name)


class SummaryClientMeter:
    """Delegating proxy: it meters, it does not replace, the summary transport.

    Only ``chat.completions.create`` is intercepted; every other attribute -- and ``close``
    in particular -- is forwarded to the real client, so the proxy cannot silently change how
    the summary path talks to the provider.
    """

    def __init__(self, client: Any, records: list[dict[str, Any]]) -> None:
        self.client = client
        self.records = records
        self.chat = _MeteredChat(self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["client"], name)

    async def close(self) -> Any:
        closer = getattr(self.client, "close", None)
        if closer is None:
            return None
        result = closer()
        return await result if hasattr(result, "__await__") else result


def _utc_now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _set(obj: Any, name: str, value: Any) -> None:
    """Set an attribute on either a plain object or a pydantic model.

    ``crewai``'s ``BaseLLM`` subclasses ``pydantic.BaseModel``, which rejects normal
    attribute assignment for names it does not declare.  Going through ``object.__setattr__``
    is what makes the meter and the capture list reachable on such an instance; without it the
    assignment appears to succeed only on a plain stub and raises on the real class.
    """
    try:
        setattr(obj, name, value)
    except Exception:
        object.__setattr__(obj, name, value)


__all__ = [
    "CapturedNativeSummaryCrewAILLM",
    "CapturedOpenAICompatCrewAILLM",
    "REUSED_FROM_V19",
    "SummaryClientMeter",
]
