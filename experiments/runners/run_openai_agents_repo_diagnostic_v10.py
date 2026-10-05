"""v10 entrypoint: the long-baseline grid with recency measured in tool calls.

The mechanics, the case builder, the task input, the registry, the thresholds and the
budgets are v9's/v8's.  This module changes two things and nothing else:

* the plugin filter is :class:`openai_agents_long_baseline_boundary_v10.ToolCallRecencyFilter`,
  whose protection window is counted in **tool calls** instead of contiguous tool groups;
* the evidence recorder is the v10 one.

Batch id: ``openai-repo-diagnostic-v10-tool-call-recency-boundary``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v10 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v9 as v9

HOST = v9.HOST
DISCLOSURE = v9.DISCLOSURE
BYTES_PER_ESTIMATED_TOKEN = v9.BYTES_PER_ESTIMATED_TOKEN
build_case = v9.build_case
protocol_message = v9.protocol_message
registered_statement = v9.registered_statement
task_statement = v9.task_statement
_CaseWithTaskStatement = v9._CaseWithTaskStatement
REGISTERED_CASES = v9.REGISTERED_CASES
LONG_TASK_ID = policy.LONG_TASK_ID
SHORT_TASK_ID = policy.SHORT_TASK_ID
TASKS = policy.TASKS

MANIFEST_EXTRA = {
    **v9.MANIFEST_EXTRA,
    "kind": "openai_agents_runner_real_api_paired_tool_call_recency",
    "mechanism": {
        "arm": "pruner_v1",
        "name": "tool_call_recency_v10",
        "schema": policy.MECHANISM_SCHEMA,
        "registry": long_registry.REGISTRY_SCHEMA,
        "base_registry": policy.base_registry.REGISTRY_SCHEMA,
        **policy.policy_dict(),
        "purpose": (
            "test the one direction v9 left un-falsified: measure the recency window in "
            "tool calls instead of contiguous tool groups, on the real recorded payload "
            "structure rather than on a self-built stub"
        ),
    },
}

PERSISTED_RETENTION_FIELDS = tuple(
    dict.fromkeys(
        (
            *v9.PERSISTED_RETENTION_FIELDS,
            "tool_call_recency_unit",
            "tool_call_recency_recent_calls_kept",
            "tool_call_recency_tool_calls",
            "tool_call_recency_elidable_calls",
            "tool_call_recency_protected_indices",
            "tool_call_recency_elidable_indices",
        )
    )
)

#: The registered statement, needed by the v9 audit for its narrowed-protected-set check.
REGISTERED_STATEMENT = registered_statement()


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the tool-call-recency filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = policy.ToolCallRecencyFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    from experiments.runners.trigger_gate import BudgetTriggeredFilter

    if isinstance(context_filter, BudgetTriggeredFilter):
        context_filter.inner = retention
        return v9.v8._GateAware(context_filter, retention)
    return retention


#: The frozen v9/v8 helper captured at import time, so this module's replacement can call
#: the original after it has been installed into the v9 module.
_V9_RETENTION_COUNTERS = v9._retention_counters


def _retention_counters(recorder: Any, context_filter: Any) -> dict[str, Any]:
    """v9's counters plus the tool-call recency columns, taken from the evidence records.

    The recency window is computed on the payload handed to each model call, so those values
    live in the recorder's per-boundary records (the gate's metrics dict does not carry them);
    the last model-input record is the boundary a reader cares about.
    """
    counters = _V9_RETENTION_COUNTERS(recorder, context_filter)
    model = [
        record for record in recorder.records if record.get("stage") == "model_input"
    ]
    final = model[-1] if model else {}
    counters.update(
        {
            "tool_call_recency_unit": str(final.get("tool_call_recency_unit", "tool call")),
            "tool_call_recency_recent_calls_kept": int(
                final.get(
                    "tool_call_recency_recent_calls_kept",
                    policy.RECENT_TOOL_CALLS_KEPT,
                )
            ),
            "tool_call_recency_tool_calls": int(
                final.get("tool_call_recency_tool_calls", 0)
            ),
            "tool_call_recency_elidable_calls": int(
                final.get("tool_call_recency_elidable_calls", 0)
            ),
            "tool_call_recency_protected_indices": list(
                final.get("tool_call_recency_protected_indices", [])
            ),
            "tool_call_recency_elidable_indices": list(
                final.get("tool_call_recency_elidable_indices", [])
            ),
        }
    )
    return counters


def main(argv=None) -> int:
    import sys

    from experiments.runners.openai_agents_evidence_v10 import ToolCallRecencyRecorder

    args = list(sys.argv[1:] if argv is None else argv)
    original = {
        "build_retentive_filter": v9.build_retentive_filter,
        "retention_counters": v9._retention_counters,
        "manifest_extra": v9.MANIFEST_EXTRA,
        "persisted_fields": v9.PERSISTED_RETENTION_FIELDS,
        "recorder_factory": v9.RECORDER_FACTORY,
    }
    v9.build_retentive_filter = build_retentive_filter
    v9._retention_counters = _retention_counters
    v9.MANIFEST_EXTRA = MANIFEST_EXTRA
    v9.PERSISTED_RETENTION_FIELDS = list(PERSISTED_RETENTION_FIELDS)
    v9.RECORDER_FACTORY = ToolCallRecencyRecorder
    try:
        return v9.main(args)
    finally:
        v9.build_retentive_filter = original["build_retentive_filter"]
        v9._retention_counters = original["retention_counters"]
        v9.MANIFEST_EXTRA = original["manifest_extra"]
        v9.PERSISTED_RETENTION_FIELDS = original["persisted_fields"]
        v9.RECORDER_FACTORY = original["recorder_factory"]


def replay_projection() -> dict[str, Any]:
    from experiments.runners.openai_agents_replay_projection_v10 import run_replay

    return run_replay(0)


def boundary_for_batch(batch: Path) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in (batch / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    baseline = [row for row in rows if row.get("method") == "none"]
    if not baseline:
        raise SystemExit("no baseline rows in this batch")
    return policy.long_task_boundary(
        max(int(row.get("model_calls", 0)) for row in baseline),
        max(int(row.get("actual_input_tokens", 0)) for row in baseline),
    )


if __name__ == "__main__":
    raise SystemExit(main())
