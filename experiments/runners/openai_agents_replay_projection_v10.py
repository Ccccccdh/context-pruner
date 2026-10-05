"""Zero-API replay of the tool-call-ordinal hypothesis on the **recorded** real payloads.

This evaluator never invents a payload structure.  It rebuilds each model boundary from the
deterministic public inputs the paid batch actually sent (see
:mod:`experiments.runners.openai_agents_real_payload_replay_v10`), asserts the rebuild
matches the recorded structure of the paid v9 batch, and then drives the v10 filter over
that sequence with the same call cadence the SDK used: observe the boundary, filter,
observe the filtered payload, go on.

Two pre-registered outcomes:

* **positive** - the replay shows non-empty elidable sets and a positive projection, in
  which case a paid 9-sample batch may be run;
* **empty** - the replay shows nothing elidable, in which case this host is closed out and
  no request is sent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_baseline_boundary_v10 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    _canonical,
    _jsonable_item,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "integrations/openai_agents/V10_REPLAY_PROJECTION.json"


def _payload_bytes(items: Sequence[Any]) -> int:
    return len(_canonical([_jsonable_item(item) for item in items]).encode("utf-8"))


def _literal_counts(items: Sequence[Any]) -> dict[str, int]:
    text = _canonical([_jsonable_item(item) for item in items]).lower()
    return {
        label: sum(text.count(str(phrase).lower()) for phrase in phrases)
        for label, phrases in long_registry.literals_for(policy.LONG_TASK_ID).items()
    }


def run_replay(repeat: int = 0) -> dict[str, Any]:
    """Drive the v10 filter over the recorded real payload sequence."""
    case = v8.build_case(policy.LONG_TASK_ID, repeat)
    boundaries = replay.realistic_boundaries(repeat)
    retention = policy.ToolCallRecencyFilter(
        task=policy.LONG_TASK_ID, task_statement=case.task_statement
    )
    history = boundaries[0]
    retention.observe(history)
    retention.observe_delivered(history)
    events: list[dict[str, Any]] = []
    baseline_payloads: list[list[dict]] = []
    sent = history
    for index in range(1, len(boundaries)):
        sent = boundaries[index]
        baseline_payloads.append(list(sent))
        retention.observe(sent)
        retention.observe_delivered(sent)
        before_bytes = _payload_bytes(sent)
        before_literals = _literal_counts(sent)
        filtered = retention.filter_items(sent)
        after = sent if filtered is None else filtered
        events.append(
            {
                "index": index,
                "item_count": len(sent),
                "tool_calls": policy.tool_call_count(sent),
                "elidable_calls": retention.tool_call_elidable_calls,
                "protected_calls": retention.tool_call_protected_calls,
                "elidable_indices": sorted(retention.last_elidable_indices),
                "before_bytes": before_bytes,
                "after_bytes": _payload_bytes(after),
                "elided_sources_total": retention.elided_source_spans,
                "fallback_reason": retention.last_fallback_reason,
                "task_anchor_restore_failures": retention.task_anchor_restore_failures,
                "budget_fallbacks": retention.budget_fallbacks,
                "literal_counts": _literal_counts(after),
                "literal_counts_before": before_literals,
                "pointer_coverage": bool(
                    retention.last_call_decision.get("pointer_coverage", True)
                ),
            }
        )
        retention.observe(after)
        retention.observe_delivered(after)
    baseline_bytes = sum(_payload_bytes(payload) for payload in baseline_payloads)
    plugin_bytes = sum(event["after_bytes"] for event in events)
    literal_regressions = [
        {"index": event["index"], "label": label}
        for event in events
        for label, count in event["literal_counts"].items()
        if count < event["literal_counts_before"].get(label, 0)
    ]
    return {
        "schema": "openai_agents_v10_replay_v1",
        "task": policy.LONG_TASK_ID,
        "repeat": repeat,
        "fixture": replay.fixture_report(repeat=repeat),
        "events": events,
        "elidable_indices_non_empty": any(
            event["elidable_indices"] for event in events
        ),
        "total_elided_sources": retention.elided_source_spans,
        "total_elided_lines": retention.elided_source_lines,
        "total_kept_literal_lines": retention.kept_literal_lines,
        "task_anchor_restore_failures": retention.task_anchor_restore_failures,
        "budget_fallbacks": retention.budget_fallbacks,
        "fallback_reasons": dict(retention.failure_reasons),
        "literal_regressions": literal_regressions,
        "baseline_payload_bytes": baseline_bytes,
        "plugin_payload_bytes": plugin_bytes,
        "byte_saving_rate": (
            (baseline_bytes - plugin_bytes) / baseline_bytes if baseline_bytes else 0.0
        ),
        "recorded_reference": {
            "paid_baseline_complete_total_tokens": 17187.0,
            "paid_plugin_complete_total_tokens": 17187.0,
            "paid_paired_saving": -0.000004,
            "note": (
                "the v9 paid batch measured an identical payload; the replay projects what "
                "this mechanism would send on the same recorded structure"
            ),
        },
    }


def main() -> int:
    report = run_replay(0)
    projection = {
        **report,
        "pre_registered_outcomes": {
            "positive": "elidable indices non-empty and projection positive -> paid batch allowed",
            "empty": "nothing elidable -> close out this host, no request",
        },
        "verdict": (
            "positive"
            if report["elidable_indices_non_empty"] and report["byte_saving_rate"] > 0
            else "empty"
        ),
    }
    OUT.write_text(json.dumps(projection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        json.dumps(
            {
                "fixture_ok": report["fixture"]["ok"],
                "fixture_mismatches": report["fixture"]["mismatches"][:3],
                "boundaries": len(report["events"]),
                "elidable_indices_non_empty": report["elidable_indices_non_empty"],
                "total_elided_sources": report["total_elided_sources"],
                "total_elided_lines": report["total_elided_lines"],
                "task_anchor_restore_failures": report["task_anchor_restore_failures"],
                "budget_fallbacks": report["budget_fallbacks"],
                "literal_regressions": report["literal_regressions"][:3],
                "baseline_payload_bytes": report["baseline_payload_bytes"],
                "plugin_payload_bytes": report["plugin_payload_bytes"],
                "byte_saving_rate": round(report["byte_saving_rate"], 4),
                "verdict": projection["verdict"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
