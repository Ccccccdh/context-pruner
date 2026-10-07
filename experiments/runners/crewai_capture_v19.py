"""Prospective full per-attempt capture for a CrewAI acquisition run.

Each provider attempt records the full synthetic input, raw and normalized output,
provider usage, request slot, prospective v18 guard decision, and the tool the host
actually executes next. The latter is bound from the host tool trace after kickoff.
No network call occurs unless a caller constructs this class with a real client and
invokes ``call``; the r19 readiness gate must pass before that is allowed.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from experiments.runners import crewai_loop_guard_v18 as guard
from experiments.runners import run_crewai_experiment as base

#: Off-peak is five times cheaper.  The provider's peak windows are 01:00-04:00 and
#: 06:00-10:00 UTC on weekdays, so "off peak" means: a weekend day, or a weekday outside
#: those two windows.  Recorded per attempt so the price tier can be checked later.
PEAK_UTC_WINDOWS = ((1, 4), (6, 10))


def off_peak(utc_stamp: str) -> bool:
    """Whether a UTC timestamp falls outside the provider's weekday peak windows."""
    try:
        parsed = time.strptime(utc_stamp, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return False
    if parsed.tm_wday >= 5:  # Saturday or Sunday
        return True
    hour = parsed.tm_hour
    return not any(start <= hour < end for start, end in PEAK_UTC_WINDOWS)


def _cache_tokens(usage: Any, input_tokens: int) -> dict[str, Any]:
    """The provider's cached/uncached input split, from whichever shape it reports it.

    DeepSeek returns ``prompt_cache_hit_tokens`` and ``prompt_cache_miss_tokens`` at the top
    level of ``usage``; some OpenAI-compatible variants nest them under
    ``prompt_tokens_details``.  Both are read, the top-level pair first.  When neither is
    present the split is reported as unknown rather than silently booked as a full miss, so
    a missing split cannot be mistaken for a full-price call.
    """
    hit = miss = None
    for attribute in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
        value = getattr(usage, attribute, None)
        if value is not None:
            if attribute.endswith("hit_tokens"):
                hit = int(value)
            else:
                miss = int(value)
    source = "usage_top_level" if hit is not None or miss is not None else ""
    if hit is None and miss is None:
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            details_hit = getattr(details, "cached_tokens", None)
            if details_hit is not None:
                hit = int(details_hit)
                source = "usage_prompt_tokens_details"
    if hit is None and miss is None:
        return {"hit": 0, "miss": 0, "source": "absent"}
    if hit is None:
        hit = max(0, input_tokens - int(miss))
    if miss is None:
        miss = max(0, input_tokens - int(hit))
    return {"hit": int(hit), "miss": int(miss), "source": source or "usage_top_level"}


class CapturedOpenAICompatCrewAILLM(base.OpenAICompatCrewAILLM):
    def __init__(self, *, executed_tool_trace: list[str],
                 frozen_tool_names: Sequence[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.capture: list[dict[str, Any]] = []
        self.executed_tool_trace = executed_tool_trace
        self.frozen_tool_names = tuple(str(x) for x in frozen_tool_names)
        self._bound_tool_count = 0

    def bind_executed_tools(self) -> None:
        """Bind each new host tool execution to its preceding unbound Action attempt."""
        while self._bound_tool_count < len(self.executed_tool_trace):
            name = self.executed_tool_trace[self._bound_tool_count]
            candidates = [record for record in self.capture
                          if record["host_executed_tool"] is None
                          and record["response_action_candidate"] == name
                          and record["status"] == "success"]
            if not candidates:
                raise RuntimeError(f"host executed {name!r} without matching captured Action")
            record = candidates[-1]
            record["host_parsed_action"] = name
            record["host_executed_tool"] = name
            record["executed_tool_index"] = self._bound_tool_count
            self._bound_tool_count += 1

    def call(self, messages: Sequence[Any], tools=None, callbacks=None,
             available_functions=None, from_task=None, from_agent=None,
             response_model=None) -> str:
        self.bind_executed_tools()
        normalized = self._record_input(messages)
        self.request_budget.consume()
        slot = self.request_budget.used
        capture = {
            "request_slot": slot,
            "full_input_messages": normalized,
            "raw_model_output": None,
            "normalized_model_output": None,
            "response_action_candidate": None,
            "host_parsed_action": None,
            "host_executed_tool": None,
            "executed_tool_index": None,
            "guard_installed": False,
            "guard_allowance": "not_installed_acquisition",
            "v18_offline_verdict": None,
            "input_tokens": 0,
            "output_tokens": 0,
            # Cache metering: the provider bills cached input at 1/50 of the miss price, so
            # the two components are recorded separately for every attempt, together with
            # the attempt's UTC time so off-peak pricing can be checked afterwards.
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0,
            "cache_tokens_source": "",
            "off_peak_window": None,
            "utc_started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "utc_finished": None,
            "status": "pending",
            "error_type": "",
        }
        self.capture.append(capture)
        started = time.perf_counter()
        try:
            request: dict[str, Any] = {
                "model": self.model, "messages": normalized,
                "temperature": 0, "max_tokens": self.max_output_tokens,
            }
            if self.thinking_mode == "disabled":
                request["extra_body"] = {"thinking": {"type": "disabled"}}
            completion = self.client.chat.completions.create(**request)
            choice = completion.choices[0]
            raw = str(choice.message.content or "").strip()
            response, sanitized = base._normalize_react_response(raw)
            usage = completion.usage
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            cache = _cache_tokens(usage, input_tokens)
            capture.update({
                "raw_model_output": raw,
                "normalized_model_output": response,
                "response_action_candidate": (guard.candidate_actions(response) or [None])[0],
                "v18_offline_verdict": guard.classify(messages, response, self.frozen_tool_names)[0],
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "prompt_cache_hit_tokens": cache["hit"],
                "prompt_cache_miss_tokens": cache["miss"],
                "cache_tokens_source": cache["source"],
                "utc_finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "off_peak_window": off_peak(capture["utc_started"]),
                "status": "success" if response else "empty",
            })
            record = {
                "status": capture["status"], "latency_seconds": time.perf_counter() - started,
                "error_type": "" if response else "EmptyModelResponseError",
                "error_message": "" if response else "empty model response",
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "prompt_cache_hit_tokens": cache["hit"],
                "prompt_cache_miss_tokens": cache["miss"],
                "cache_tokens_source": cache["source"],
                "utc_started": capture["utc_started"],
                "utc_finished": capture["utc_finished"],
                "off_peak_window": capture["off_peak_window"],
                "finish_reason": str(getattr(choice, "finish_reason", "") or ""),
                "reasoning_tokens": 0, "reasoning_characters": 0,
                "raw_response_characters": len(raw),
                "normalized_response_characters": len(response),
                "react_response_sanitized": sanitized,
            }
            self.responses.append(response)
            self.response_records.append(record)
            self.attempt_records.append(record)
            if not response:
                raise base.EmptyModelResponseError("empty model response")
            return response
        except Exception as error:
            if capture["status"] == "pending":
                capture.update({"status": "error", "error_type": type(error).__name__})
                self.attempt_records.append({
                    "status": "error", "latency_seconds": time.perf_counter() - started,
                    "error_type": type(error).__name__, "error_message": str(error)[:300],
                    "input_tokens": 0, "output_tokens": 0, "finish_reason": "",
                    "reasoning_tokens": 0, "reasoning_characters": 0,
                    "raw_response_characters": 0, "normalized_response_characters": 0,
                    "react_response_sanitized": False,
                })
            raise
