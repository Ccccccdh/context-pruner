"""Zero-API replay of the v11 elision ladder on the **recorded** real payloads.

The fixture is v10's replay fixture: boundaries rebuilt from the deterministic public inputs
and validated against the paid v9 batch's recorded structure (item counts, message counts,
tool-output counts, contiguity).  This module drives the v11 filter over that sequence for
every ``M`` on the pre-registered ladder and records, per ``M``:

* the elidable indices at the last boundary and how many tool calls they cover;
* the elided source and line counts, with every guard counter;
* the payload bytes sent versus the baseline payload bytes, i.e. the byte-level projection;
* whether any registered literal lost an occurrence.

The three projections are frozen before any paid request; the paid batch then takes the
**largest** ``M`` whose projection is positive (the most conservative elision).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from experiments.runners import openai_agents_long_baseline_boundary_v11 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import openai_agents_real_payload_replay_v10 as replay
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    _canonical,
    _jsonable_item,
)

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "integrations/openai_agents/V11_ELISION_LADDER_PROJECTION.json"


def _bytes(items: Sequence[Any]) -> int:
    return len(_canonical([_jsonable_item(item) for item in items]).encode("utf-8"))


def _literal_counts(items: Sequence[Any]) -> dict[str, int]:
    text = _canonical([_jsonable_item(item) for item in items]).lower()
    return {
        label: sum(text.count(str(phrase).lower()) for phrase in phrases)
        for label, phrases in long_registry.literals_for(policy.LONG_TASK_ID).items()
    }


def run_ladder_step(m: int, repeat: int = 0) -> dict[str, Any]:
    """Drive the v11 filter with ``M = m`` over the recorded payload sequence."""
    if int(m) not in policy.ELISION_LADDER:
        raise ValueError(f"{m} is not on the ladder {policy.ELISION_LADDER}")
    case = v8.build_case(policy.LONG_TASK_ID, repeat)
    boundaries = replay.realistic_boundaries(repeat)
    retention = policy.ElisionRatioFilter(
        task=policy.LONG_TASK_ID,
        task_statement=case.task_statement,
        recent_calls_kept=int(m),
    )
    history = boundaries[0]
    retention.observe(history)
    retention.observe_delivered(history)
    events: list[dict[str, Any]] = []
    baseline_bytes = 0
    plugin_bytes = 0
    sent = history
    for payload in boundaries[1:]:
        sent = payload
        retention.observe(sent)
        retention.observe_delivered(sent)
        before = _bytes(sent)
        before_literals = _literal_counts(sent)
        filtered = retention.filter_items(sent)
        after = sent if filtered is None else filtered
        baseline_bytes += before
        plugin_bytes += _bytes(after)
        events.append(
            {
                "index": len(events) + 1,
                "item_count": len(sent),
                "tool_calls": policy.tool_call_count(sent),
                "elidable_indices": sorted(
                    policy.elidable_call_indices(sent, int(m))
                ),
                "elidable_calls": len(
                    {
                        position
                        for position, group in enumerate(policy.call_groups(sent))
                        if group and group[0] in policy.elidable_call_indices(sent, int(m))
                    }
                ),
                "protected_indices": sorted(
                    policy.protected_call_indices(sent, int(m))
                ),
                "before_bytes": before,
                "after_bytes": _bytes(after),
                "elided_sources_total": retention.elided_source_spans,
                "fallback_reason": retention.last_fallback_reason,
                "literal_regressions": [
                    label
                    for label, count in _literal_counts(after).items()
                    if count < before_literals.get(label, 0)
                ],
            }
        )
        retention.observe(after)
        retention.observe_delivered(after)
    saving = (baseline_bytes - plugin_bytes) / baseline_bytes if baseline_bytes else 0.0
    return {
        "M": int(m),
        "fixture_ok": replay.fixture_report(repeat=repeat)["ok"],
        "boundaries": len(events),
        "last_boundary_elidable_indices": events[-1]["elidable_indices"] if events else [],
        "last_boundary_elidable_calls": events[-1]["elidable_calls"] if events else 0,
        "elidable_indices_non_empty": any(event["elidable_indices"] for event in events),
        "total_elided_sources": retention.elided_source_spans,
        "total_elided_lines": retention.elided_source_lines,
        "total_kept_literal_lines": retention.kept_literal_lines,
        "task_anchor_restore_failures": retention.task_anchor_restore_failures,
        "budget_fallbacks": retention.budget_fallbacks,
        "fallback_reasons": dict(retention.failure_reasons),
        "literal_regressions": sorted(
            {label for event in events for label in event["literal_regressions"]}
        ),
        "baseline_payload_bytes": baseline_bytes,
        "plugin_payload_bytes": plugin_bytes,
        "byte_saving_rate": saving,
        "events": events,
    }


def main() -> int:
    ladder = {str(m): run_ladder_step(m) for m in policy.ELISION_LADDER}
    positive = [
        int(m)
        for m, report in ladder.items()
        if report["byte_saving_rate"] > 0 and report["elidable_indices_non_empty"]
    ]
    selected = max(positive) if positive else None
    payload = {
        "schema": "openai_agents_v11_elision_ladder_v1",
        "task": policy.LONG_TASK_ID,
        "ladder": list(policy.ELISION_LADDER),
        "quality_first": policy.MANIFEST_POLICY["quality_first"],
        "projections": ladder,
        "positive_M": sorted(positive),
        "selected_M": selected,
        "selection_rule": (
            "largest M whose replay projection is positive (most conservative elision); "
            "if none is positive, no paid request is sent"
        ),
        "paid_run_allowed": selected is not None,
        "fixture_note": (
            "measured on the recorded real payload structure via "
            "openai_agents_real_payload_replay_v10 (no self-built stub)"
        ),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for m in policy.ELISION_LADDER:
        report = ladder[str(m)]
        print(
            json.dumps(
                {
                    "M": m,
                    "elidable_indices": report["last_boundary_elidable_indices"],
                    "elidable_calls": report["last_boundary_elidable_calls"],
                    "elided_sources": report["total_elided_sources"],
                    "elided_lines": report["total_elided_lines"],
                    "byte_saving_rate": round(report["byte_saving_rate"], 4),
                    "restore_failures": report["task_anchor_restore_failures"],
                    "literal_regressions": report["literal_regressions"],
                },
                ensure_ascii=False,
            )
        )
    print(
        json.dumps(
            {"positive_M": sorted(positive), "selected_M": selected, "paid_run_allowed": selected is not None}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
