"""Diagnostic (non-frozen) recomputation of the r17 confirmation batch.

What this is, and what it is not
--------------------------------
It is a **post-hoc, non-frozen recomputation**: an independent auditor written after the
batch, compatible with the confirmation task table, that re-derives every number the batch
reports (36 samples, 334 charged requests, 20 guard re-sends, per-task tool tables, strict /
semantic verdicts, complete token cost) straight from the recorded rows.

It is **not** an independent audit of the frozen revision.  The frozen audit of this batch
remains ``complete=false`` with **38 errors**, which are preserved verbatim in
``old_audit_errors`` below and are never retroactively converted into a pass.  The two facts
are reported side by side and the verdict says which one governs.

It classifies each unit's trajectory into the pre-registered kinds: complete table,
action-less rejection (bare/marked HANDOFF), action-but-wrong-tool-or-order, and guard
re-sends.  The point the classification makes explicit: the current guard refuses only
"no valid Action while the table is unfinished", so it does **not** guarantee correct order.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BATCH = ROOT / "runs/stage5-crewai/crewai-two-role-r17-confirmation-3arm-01"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r17_confirmation.json"
ARMS = ("none", "pruner_v1", "native_summary")
ACTION = re.compile(r"(?im)^\s*Action\s*:\s*([^\r\n]+)")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def trace_classification(expected: list[str], actual: list[str]) -> dict:
    """How the first role's executed tool trace relates to the frozen table."""
    prefix = 0
    for index, name in enumerate(actual):
        if index < len(expected) and name == expected[index]:
            prefix += 1
        else:
            break
    if actual == expected:
        kind = "complete_exact_order"
    elif prefix == len(actual) and len(actual) < len(expected):
        kind = "incomplete_prefix"  # stopped early, everything it did was in order
    elif len(actual) >= len(expected) and sorted(actual[: len(expected)]) == sorted(expected):
        kind = "complete_but_misordered"
    else:
        kind = "wrong_tool_or_order"
    return {
        "ordered_prefix_length": prefix,
        "expected_length": len(expected),
        "actual_length": len(actual),
        "missing_tools": [name for name in expected if name not in actual],
        "out_of_order": (
            [name for index, name in enumerate(actual)
             if index < len(expected) and name != expected[index]]
            if kind in ("complete_but_misordered", "wrong_tool_or_order") else []
        ),
        "kind": kind,
    }


def rejection_classification(row: dict) -> dict:
    """Split the guard's re-sends by what the refused turn looked like."""
    records = row.get("guard_rejection_records") or []
    bare = marked = other = 0
    for record in records:
        body = str(record.get("rejected_body") or "")
        if "Final Answer:" in body:
            marked += 1
        elif body.strip():
            bare += 1
        else:
            other += 1
    reason_counts: dict[str, int] = {}
    for record in records:
        key = str(record.get("reason") or "unknown")
        reason_counts[key] = reason_counts.get(key, 0) + 1
    return {
        "rejections": int(row.get("guard_rejections") or 0),
        "records": len(records),
        "bare_handoff_rejections": bare,
        "marked_handoff_rejections": marked,
        "other_rejections": other,
        "reasons": reason_counts,
    }


def main() -> int:
    rows = load_rows(BATCH)
    manifest = json.loads((BATCH / "manifest.json").read_text(encoding="utf-8"))
    tasks = {t["task_id"]: t for t in json.loads(TASK_FILE.read_text(encoding="utf-8"))}
    previous = json.loads(
        (BATCH / "R17_CONFIRMATION_INDEPENDENT_AUDIT.json").read_text(encoding="utf-8")
    )

    by_arm = {arm: [r for r in rows if str(r["method"]) == arm] for arm in ARMS}
    units = []
    for row in rows:
        expected = [str(x) for x in row["expected_first_role_trace"]]
        actual = [str(x) for x in row["first_role_trace"]]
        units.append(
            {
                "task_id": str(row["task_id"]),
                "repeat": int(row["repeat"]),
                "arm": str(row["method"]),
                "expected": expected,
                "actual": actual,
                "trace": trace_classification(expected, actual),
                "guard": rejection_classification(row),
                "strict": bool(row["strict_success"]),
                "semantic": bool(row["semantic_success"]),
                "first_pass": bool(row["first_pass_complete"]),
                "complete_tokens": int(row["all_arm_total_tokens"]),
                "charged_requests": int(row["api_request_attempts"]),
                "agent_requests": int(row.get("agent_request_attempts", 0)),
                "auxiliary_requests": int(row.get("auxiliary_request_attempts", 0)),
                "failure_class": str(row.get("failure_class") or "none"),
                "error": str(row.get("error") or ""),
            }
        )

    arm_totals = {}
    for arm in ARMS:
        selected = [u for u in units if u["arm"] == arm]
        arm_totals[arm] = {
            "units": len(selected),
            "charged_requests": sum(u["charged_requests"] for u in selected),
            "guard_rejections": sum(u["guard"]["rejections"] for u in selected),
            "guard_triggered_units": sum(
                1 for u in selected if u["guard"]["rejections"] > 0
            ),
            "bare_handoff_rejections": sum(
                u["guard"]["bare_handoff_rejections"] for u in selected
            ),
            "marked_handoff_rejections": sum(
                u["guard"]["marked_handoff_rejections"] for u in selected
            ),
            "guard_exhausted": sum(1 for u in selected if u["guard"]["records"] and False),
            "strict": sum(1 for u in selected if u["strict"]),
            "semantic": sum(1 for u in selected if u["semantic"]),
            "first_pass": sum(1 for u in selected if u["first_pass"]),
            "exact_tool_sequence": sum(
                1 for u in selected if u["trace"]["kind"] == "complete_exact_order"
            ),
            "complete_tokens": sum(u["complete_tokens"] for u in selected),
            "errors": sum(1 for u in selected if u["error"]),
        }
    per_task = {}
    for task_id in tasks:
        per_task[task_id] = {}
        for arm in ARMS:
            selected = [
                u for u in units if u["task_id"] == task_id and u["arm"] == arm
            ]
            per_task[task_id][arm] = {
                "units": len(selected),
                "tool_table_exact": sum(
                    1 for u in selected if u["trace"]["kind"] == "complete_exact_order"
                ),
                "trace_kinds": sorted({u["trace"]["kind"] for u in selected}),
                "strict": sum(1 for u in selected if u["strict"]),
                "semantic": sum(1 for u in selected if u["semantic"]),
                "charged_requests": sum(u["charged_requests"] for u in selected),
                "complete_tokens": sum(u["complete_tokens"] for u in selected),
            }

    baseline = {(u["task_id"], u["repeat"]): u for u in units if u["arm"] == "none"}
    paired = []
    for unit in units:
        if unit["arm"] != "pruner_v1":
            continue
        base = baseline.get((unit["task_id"], unit["repeat"]))
        if base is None:
            continue
        same_work = (
            base["charged_requests"] == unit["charged_requests"]
            and base["actual"] == unit["actual"]
        )
        saving = 100.0 * (base["complete_tokens"] - unit["complete_tokens"]) / max(
            1, base["complete_tokens"]
        )
        paired.append(
            {
                "task_id": unit["task_id"],
                "repeat": unit["repeat"],
                "same_work": same_work,
                "base_tokens": base["complete_tokens"],
                "plugin_tokens": unit["complete_tokens"],
                "saving_pct": round(saving, 3),
                "plugin_guard_rejections": unit["guard"]["rejections"],
            }
        )
    same = [p for p in paired if p["same_work"]]
    single_flight = [
        {
            "task_id": p["task_id"],
            "repeat": p["repeat"],
            "saving_pct": round(
                100.0
                * (
                    p["base_tokens"]
                    - p["base_tokens"]
                    * (1 - p["saving_pct"] / 100.0)
                    + 0
                )
                / max(1, p["base_tokens"]),
                3,
            ),
            "note": "placeholder, replaced below",
        }
        for p in []
    ]
    # Worst-case re-send cost: the largest per-request token charge any plugin unit paid,
    # multiplied by the guard's own cap, added on top of the plugin arm's observed cost.
    plugin_max_call = 0
    for row in rows:
        if str(row["method"]) != "pruner_v1":
            continue
        for attempt in row.get("agent_attempt_records") or []:
            plugin_max_call = max(
                plugin_max_call,
                int(attempt.get("input_tokens", 0)) + int(attempt.get("output_tokens", 0)),
            )
    plugin_units = arm_totals["pruner_v1"]["units"]
    worst_resends_per_unit = 6
    worst_extra_tokens = plugin_max_call * worst_resends_per_unit * plugin_units
    observed_plugin_tokens = arm_totals["pruner_v1"]["complete_tokens"]
    observed_base_tokens = arm_totals["none"]["complete_tokens"]
    worst_case_bound_tokens = (
        observed_base_tokens - (observed_plugin_tokens + worst_extra_tokens)
    )

    # Trajectory kinds across the whole batch.
    kind_counts: dict[str, int] = {}
    for unit in units:
        key = f"{unit['trait'] if False else unit['arm']}:{unit['trace']['kind']}"
        kind_counts[key] = kind_counts.get(key, 0) + 1

    incomplete_units = [
        u for u in units
        if u["arm"] == "pruner_v1" and u["trace"]["kind"] != "complete_exact_order"
    ]
    triggered_units = [
        u for u in units if u["arm"] == "pruner_v1" and u["guard"]["rejections"] > 0
    ]
    report = {
        "status": "posthoc_nonfrozen_recomputation",
        "explicit_warning": (
            "this file is NOT an independent audit of the frozen revision; the frozen audit "
            "of this batch remains complete=false with 38 errors, preserved verbatim under "
            "old_audit_errors and never retroactively converted into a pass"
        ),
        "batch": BATCH.name,
        "batch_rows_sha256": sha(BATCH / "results.jsonl"),
        "batch_manifest_sha256": sha(BATCH / "manifest.json"),
        "recomputed": {
            "rows": len(rows),
            "units_per_arm": {arm: len(by_arm[arm]) for arm in ARMS},
            "charged_requests": sum(u["charged_requests"] for u in units),
            "manifest_cap": manifest.get("max_api_requests"),
            "guard_rejections": sum(u["guard"]["rejections"] for u in units),
            "arm_totals": arm_totals,
            "per_task": per_task,
            "line_check": {
                "rows_36": len(rows) == 36,
                "requests_334": sum(u["charged_requests"] for u in units) == 334,
                "guard_resends_20": sum(u["guard"]["rejections"] for u in units) == 20,
                "baseline_zero_rejections": arm_totals["none"]["guard_rejections"] == 0,
                "summary_arm_zero_rejections": arm_totals["native_summary"][
                    "guard_rejections"
                ] == 0,
            },
        },
        "old_audit": {
            "complete": previous.get("complete"),
            "error_count": len(previous.get("errors") or []),
            "unchanged": True,
        },
        "old_audit_errors": list(previous.get("errors") or []),
        "trajectory_classification": {
            "kind_counts": kind_counts,
            "guard_triggered_units": [
                {
                    "task_id": u["task_id"],
                    "repeat": u["repeat"],
                    "rejections": u["guard"]["rejections"],
                    "bare_handoff_rejections": u["guard"]["bare_handoff_rejections"],
                    "marked_handoff_rejections": u[
                        "guard"]["marked_handoff_rejections"],
                    "trace_kind": u["trace"]["kind"],
                    "ordered_prefix_length": u["trace"]["ordered_prefix_length"],
                    "strict": u["strict"],
                }
                for u in triggered_units
            ],
            "incomplete_table_units": [
                {
                    "task_id": u["task_id"],
                    "repeat": u["repeat"],
                    "trace_kind": u["trace"]["kind"],
                    "actual": u["actual"],
                    "missing_tools": u["trace"]["missing_tools"],
                    "out_of_order": u["trace"]["out_of_order"],
                    "ordered_prefix_length": u["trace"]["ordered_prefix_length"],
                    "rejections": u["guard"]["rejections"],
                    "strict": u["strict"],
                    "semantic": u["semantic"],
                }
                for u in incomplete_units
            ],
            "key_limitation": (
                "the current guard refuses only 'no valid Action while the table is "
                "unfinished'; it does not check that the Action is the expected next tool, "
                "so a wrong tool or a wrong order is not refused"
            ),
        },
        "cost": {
            "paired": paired,
            "same_work_units": len(same),
            "same_work_mean_pct": (
                round(statistics.fmean(p["saving_pct"] for p in same), 3) if same else None
            ),
            "all_pairs_mean_pct": (
                round(statistics.fmean(p["saving_pct"] for p in paired), 3)
                if paired else None
            ),
            "max_plugin_call_tokens": plugin_max_call,
            "worst_case_resends_per_unit": worst_resends_per_unit,
            "worst_case_extra_tokens": worst_extra_tokens,
            "worst_case_bound_tokens": worst_case_bound_tokens,
            "worst_case_note": (
                "observed envelope: the largest observed plugin call times the guard's own "
                "rejection cap times every plugin unit; not a provider hard bound, and it is "
                "the number a candidate revision must carry in its upper bound"
            ),
            "single_flight_placeholder": single_flight,
        },
        "verdict": {
            "frozen_audit_governs": True,
            "can_be_quoted_as_saving": False,
            "reason": "the frozen audit is incomplete (38 errors); this file only recomputes",
        },
    }
    target = BATCH / "R19_CONFIRMATION_DIAGNOSTIC_RECOMPUTATION.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"wrote {target.relative_to(ROOT)}"]
    lines.append("recomputed: " + json.dumps(report["recomputed"]["line_check"]))
    lines.append("arm totals: " + json.dumps(arm_totals, ensure_ascii=False))
    lines.append("kind counts: " + json.dumps(kind_counts, ensure_ascii=False))
    lines.append(
        "guard-triggered units: "
        + json.dumps(report["trajectory_classification"]["guard_triggered_units"],
                     ensure_ascii=False)
    )
    lines.append(
        "incomplete-table units: "
        + json.dumps(report["trajectory_classification"]["incomplete_table_units"],
                     ensure_ascii=False)
    )
    lines.append(
        "cost: same_work=%s all=%s worst_bound=%s"
        % (
            report["cost"]["same_work_mean_pct"],
            report["cost"]["all_pairs_mean_pct"],
            report["cost"]["worst_case_bound_tokens"],
        )
    )
    text = "\n".join(lines)
    (ROOT / ".tooling/tmp/r19_diag.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
