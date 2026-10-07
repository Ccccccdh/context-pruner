"""Independent audit of the r20 order-free three-arm pilot, with the criteria wired in (zero API).

This is the audit the pre-registration names as the one that decides a pass.  It recomputes
every criterion itself from the recorded rows and cross-checks any value recorded elsewhere;
it never imports a runner and never trusts the batch's own self-description.

What it decides
---------------
* **quality**: the frozen r8 judge's strict and semantic success per task must not fall below
  the baseline arm, AND ``CRIT_R20_COVERAGE`` must hold on every unit.  Coverage is the only
  criterion allowed onto the quality side.
* **cost**: paired **complete provider tokens** must be positive on the majority of units and
  positive in the batch mean.  Every repeat, remedy and guard request is charged in full.
* report only: repeat counts, call counts, tool traces, set equality and the same-work subset.

What it refuses
---------------
A batch with any guard activity on any arm, a row whose recorded token ledger does not match
its own per-attempt capture, an unpinned or drifted source, or a recorded criteria value that
disagrees with the recomputation.  When ``errors`` is non-empty, the numbers are not an
effective saving and must not be quoted as one.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audits import criteria_crewai_r20_orderfree as criteria  # noqa: E402

FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json"
DEFAULT_BATCH = ROOT / "runs/stage5-crewai/crewai-r20-orderfree-3arm-01"
DEFAULT_OUT = ROOT / "integrations/crewai/R20_ORDERFREE_3ARM_AUDIT_20261006.json"
BASELINE = "none"
PLUGIN = "pruner_v1"
SUMMARY = "native_summary"
PRICE = {"hit": 0.003, "miss": 0.15, "output": 0.45}
OFF_PEAK_MULTIPLIER = 0.5

#: Frozen guard control texts, duplicated as literals: importing the guards would make this
#: audit depend on the code it is supposed to police.
CONTROL_TEXTS = (
    "Host control: the evidence loop is not finished.",
    "Host control: the frozen evidence order is mandatory.",
)

ERRORS_RULE = (
    "errors non-empty: these numbers are NOT an effective saving and must not be quoted as "
    "one; a report-only item is never a pass and a pass never depends on a report-only item"
)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path) -> str:
    path = Path(path)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def load_rows(directory: Path) -> list[dict]:
    text = (Path(directory) / "results.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def attempts_of(row: Mapping[str, Any]) -> list[dict]:
    return [entry for entry in (row.get("full_attempt_capture") or []) if isinstance(entry, dict)]


def summary_attempts_of(row: Mapping[str, Any]) -> list[dict]:
    return [entry for entry in (row.get("native_summary_capture") or []) if isinstance(entry, dict)]


def complete_provider_tokens(row: Mapping[str, Any]) -> dict:
    """The acceptance line: every provider token this unit spent, nothing netted out."""
    agent = attempts_of(row)
    summaries = summary_attempts_of(row)
    agent_input = sum(int(a.get("input_tokens", 0) or 0) for a in agent)
    agent_output = sum(int(a.get("output_tokens", 0) or 0) for a in agent)
    summary_input = sum(int(a.get("input_tokens", 0) or 0) for a in summaries)
    summary_output = sum(int(a.get("output_tokens", 0) or 0) for a in summaries)
    return {
        "agent_requests": len(agent),
        "summary_requests": len(summaries),
        "agent_input_tokens": agent_input,
        "agent_output_tokens": agent_output,
        "summary_input_tokens": summary_input,
        "summary_output_tokens": summary_output,
        "input_tokens": agent_input + summary_input,
        "output_tokens": agent_output + summary_output,
        "complete_provider_tokens": agent_input + agent_output + summary_input + summary_output,
    }


def cache_split(row: Mapping[str, Any]) -> dict:
    entries = attempts_of(row) + summary_attempts_of(row)
    hit = sum(int(e.get("prompt_cache_hit_tokens", 0) or 0) for e in entries)
    miss = sum(int(e.get("prompt_cache_miss_tokens", 0) or 0) for e in entries)
    absent = sum(1 for e in entries if e.get("cache_tokens_source") in (None, "", "absent"))
    return {
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "attempts_without_split": absent,
        "attempts": len(entries),
        "hit_share_percent": (
            round(100.0 * hit / (hit + miss), 2) if (hit + miss) else None
        ),
    }


def usd(row: Mapping[str, Any]) -> dict:
    entries = attempts_of(row) + summary_attempts_of(row)
    peak = 0.0
    billed = 0.0
    off_peak_attempts = 0
    timestamped = 0
    for entry in entries:
        hit = int(entry.get("prompt_cache_hit_tokens", 0) or 0)
        miss = int(entry.get("prompt_cache_miss_tokens", 0) or 0)
        output = int(entry.get("output_tokens", 0) or 0)
        price = (
            hit / 1e6 * PRICE["hit"] + miss / 1e6 * PRICE["miss"] + output / 1e6 * PRICE["output"]
        )
        peak += price
        tier = entry.get("off_peak_window")
        if tier is not None:
            timestamped += 1
        if tier is True:
            off_peak_attempts += 1
            billed += price * OFF_PEAK_MULTIPLIER
        else:
            billed += price
    return {
        "peak_equivalent_usd": round(peak, 6),
        "billed_usd": round(billed, 6),
        "attempts": len(entries),
        "attempts_timestamped": timestamped,
        "attempts_off_peak": off_peak_attempts,
    }


def guard_scan(rows: Sequence[Mapping[str, Any]]) -> dict:
    occurrences = {text: 0 for text in CONTROL_TEXTS}
    for row in rows:
        for entry in attempts_of(row):
            for message in entry.get("full_input_messages") or []:
                if not isinstance(message, Mapping):
                    continue
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                for text in CONTROL_TEXTS:
                    if text in content:
                        occurrences[text] += 1
    return {
        "rows": len(rows),
        "rows_with_guard_enabled": sum(1 for r in rows if r.get("guard_enabled")),
        "rows_with_guard_rejections": sum(
            1 for r in rows if int(r.get("guard_rejections", 0) or 0) != 0
        ),
        "rows_with_guard_exhausted": sum(1 for r in rows if r.get("guard_exhausted")),
        "rows_with_rejection_records": sum(1 for r in rows if r.get("guard_rejection_records")),
        "guard_rejections_total": sum(int(r.get("guard_rejections", 0) or 0) for r in rows),
        "control_text_occurrences": occurrences,
        "control_text_occurrences_total": sum(occurrences.values()),
        "zero_trigger": sum(occurrences.values()) == 0 and not any(
            r.get("guard_enabled") or r.get("guard_exhausted")
            or int(r.get("guard_rejections", 0) or 0) != 0
            for r in rows
        ),
    }


def paired_comparison(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Pair each controller unit with its own baseline unit, by task and repeat."""
    index: dict[tuple[str, int, str], Mapping[str, Any]] = {
        (str(r.get("task_id")), int(r.get("repeat", 0) or 0), str(r.get("method"))): r
        for r in rows
    }
    tasks = [str(t["task_id"]) for t in json.loads(TASK_FILE.read_text(encoding="utf-8"))]
    repeats = sorted({int(r.get("repeat", 0) or 0) for r in rows})
    units: list[dict] = []
    for arm in (PLUGIN, SUMMARY):
        for task_id in tasks:
            for repeat in repeats:
                baseline = index.get((task_id, repeat, BASELINE))
                treatment = index.get((task_id, repeat, arm))
                if baseline is None or treatment is None:
                    continue
                base_tokens = complete_provider_tokens(baseline)["complete_provider_tokens"]
                arm_tokens = complete_provider_tokens(treatment)["complete_provider_tokens"]
                units.append(
                    {
                        "arm": arm,
                        "task_id": task_id,
                        "repeat": repeat,
                        "baseline_tokens": base_tokens,
                        "arm_tokens": arm_tokens,
                        "delta_tokens": base_tokens - arm_tokens,
                        "percent_of_baseline": (
                            round(100.0 * (base_tokens - arm_tokens) / base_tokens, 3)
                            if base_tokens
                            else None
                        ),
                        "baseline_strict": bool(baseline.get("strict_success")),
                        "baseline_semantic": bool(baseline.get("semantic_success")),
                        "arm_strict": bool(treatment.get("strict_success")),
                        "arm_semantic": bool(treatment.get("semantic_success")),
                    }
                )

    def summarise(arm: str) -> dict:
        subset = [u for u in units if u["arm"] == arm]
        deltas = [u["delta_tokens"] for u in subset]
        positive = sum(1 for d in deltas if d > 0)
        mean = sum(deltas) / len(deltas) if deltas else None
        if deltas and len(deltas) > 1 and mean is not None:
            variance = sum((d - mean) ** 2 for d in deltas) / (len(deltas) - 1)
            stdev = math.sqrt(variance)
            stderr = stdev / math.sqrt(len(deltas))
            ci = [round(mean - 1.96 * stderr, 1), round(mean + 1.96 * stderr, 1)]
        else:
            stdev = stderr = None
            ci = [None, None]
        baseline_total = sum(u["baseline_tokens"] for u in subset)
        return {
            "arm": arm,
            "units": len(subset),
            "positive_units": positive,
            "positive_majority": bool(subset) and positive * 2 > len(subset),
            "mean_delta_tokens": round(mean, 1) if mean is not None else None,
            "mean_delta_percent": (
                round(100.0 * sum(deltas) / baseline_total, 3) if baseline_total else None
            ),
            "stdev_delta_tokens": round(stdev, 1) if stdev is not None else None,
            "ci95_delta_tokens": ci,
            "baseline_tokens_total": baseline_total,
            "arm_tokens_total": sum(u["arm_tokens"] for u in subset),
            "quality_not_below_baseline": all(
                (u["arm_strict"] or not u["baseline_strict"])
                and (u["arm_semantic"] or not u["baseline_semantic"])
                for u in subset
            ),
        }

    return {
        "pairing": "each (task, repeat) pair: controller unit versus its own baseline unit",
        "units": units,
        "arm_summaries": {arm: summarise(arm) for arm in (PLUGIN, SUMMARY)},
    }


def criteria_blocks(rows: Sequence[Mapping[str, Any]]) -> dict:
    """Recompute all three criteria here; a recorded value is only cross-checked."""
    expected_by_task = {
        str(t["task_id"]): [str(tool["name"]) for tool in (t.get("tools") or [])]
        for t in json.loads(TASK_FILE.read_text(encoding="utf-8"))
    }
    units: list[dict] = []
    for row in rows:
        task_id = str(row.get("task_id"))
        executed = [str(x) for x in (row.get("first_role_trace") or [])]
        declared = expected_by_task.get(task_id, [])
        units.append(
            {
                "task_id": task_id,
                "repeat": int(row.get("repeat", 0) or 0),
                "arm": str(row.get("method")),
                "declared_expected_trace_matches_task_file": [
                    str(x) for x in (row.get("expected_first_role_trace") or [])
                ] == declared,
                criteria.CRIT_COVERAGE: criteria.coverage(declared, executed),
                criteria.CRIT_REPEAT: criteria.repeat_report(declared, executed),
            }
        )
    covered = sum(1 for u in units if u[criteria.CRIT_COVERAGE]["satisfied"])
    # Set equality per (task, repeat) is a report item: on an order-free contract, "the set of
    # sources read equals the frozen set" is exactly what coverage already says, and the
    # stronger "touched nothing extra" question belongs to the report, never to the gate.
    by_unit: dict[tuple[str, int], dict[str, list[str]]] = {}
    for row in rows:
        key = (str(row.get("task_id")), int(row.get("repeat", 0) or 0))
        executed = set(str(x) for x in (row.get("first_role_trace") or []))
        expected = set(expected_by_task.get(key[0], []))
        by_unit.setdefault(key, {})[str(row.get("method"))] = [
            "set_equal" if executed == expected else "set_differs"
        ]
    set_equal_units = 0
    same_work_units = 0
    same_work_pairs: list[dict] = []
    for key, arms in sorted(by_unit.items()):
        if arms.get(BASELINE) == ["set_equal"]:
            set_equal_units += 1
        index = {
            (str(r.get("task_id")), int(r.get("repeat", 0) or 0), str(r.get("method"))): r
            for r in rows
        }
        baseline_row = index.get((key[0], key[1], BASELINE))
        plugin_row = index.get((key[0], key[1], PLUGIN))
        if baseline_row is None or plugin_row is None:
            continue
        report = criteria.same_work_report(baseline_row, plugin_row)
        report["task_id"] = key[0]
        report["repeat"] = key[1]
        same_work_pairs.append(report)
        if report["same_work"]:
            same_work_units += 1
    return {
        "criterion_ids": list(criteria.CRITERION_IDS),
        "quality_criteria": list(criteria.QUALITY_CRITERIA),
        "gate_ids": list(criteria.QUALITY_CRITERIA),
        "report_only_ids": [
            cid for cid in criteria.CRITERION_IDS if cid not in criteria.QUALITY_CRITERIA
        ],
        criteria.CRIT_COVERAGE: {
            "criterion": criteria.CRIT_COVERAGE,
            "side": "quality",
            "gate": True,
            "computed_from": "first_role_trace of each recorded row, against the frozen task file",
            "units": len(units),
            "satisfied": covered,
            "satisfied_percent": round(100.0 * covered / len(units), 2) if units else None,
            "all_satisfied": bool(units) and covered == len(units),
            "by_arm": {
                arm: {
                    "units": sum(1 for u in units if u["arm"] == arm),
                    "satisfied": sum(
                        1 for u in units if u["arm"] == arm
                        and u[criteria.CRIT_COVERAGE]["satisfied"]
                    ),
                }
                for arm in (BASELINE, PLUGIN, SUMMARY)
            },
        },
        criteria.CRIT_REPEAT: {
            "criterion": criteria.CRIT_REPEAT,
            "side": "report_only",
            "gate": False,
            "repeat_calls_total": sum(u[criteria.CRIT_REPEAT]["repeat_calls"] for u in units),
            "call_count_total": sum(u[criteria.CRIT_REPEAT]["call_count"] for u in units),
            "expected_count_total": sum(u[criteria.CRIT_REPEAT]["expected_count"] for u in units),
            "per_arm": {
                arm: {
                    "call_count": sum(
                        u[criteria.CRIT_REPEAT]["call_count"] for u in units if u["arm"] == arm
                    ),
                    "repeat_calls": sum(
                        u[criteria.CRIT_REPEAT]["repeat_calls"] for u in units if u["arm"] == arm
                    ),
                }
                for arm in (BASELINE, PLUGIN, SUMMARY)
            },
        },
        criteria.CRIT_SAME_WORK: {
            "criterion": criteria.CRIT_SAME_WORK,
            "side": "report_only",
            "gate": False,
            "applicable": bool(same_work_pairs),
            "pairs": same_work_pairs,
            "same_work_units": same_work_units,
            "baseline_set_equal_units": set_equal_units,
            "resend_overrun_calls": sum(p["resend_overrun_calls"] for p in same_work_pairs),
        },
        "units": units,
    }


def grid_check(rows: Sequence[Mapping[str, Any]], expected_ids: Sequence[str],
               methods: Sequence[str], repeats: int) -> dict:
    """The frozen plan rotates the arm order (``balanced_plan``), so the check is set-based.

    Requiring a fixed arm order would fail a run that followed the pre-registered plan: the
    plan deliberately cycles the order of the arms inside each (task, repeat) block so that no
    arm systematically runs first.  What must hold is that every frozen cell exists exactly
    once, that no cell outside the grid exists, and that each arm runs equally often.
    """
    recorded = [
        (str(r.get("task_id")), int(r.get("repeat", 0) or 0), str(r.get("method"))) for r in rows
    ]
    expected = {
        (tid, rep, method)
        for tid in expected_ids for rep in range(repeats) for method in methods
    }
    counts: dict[tuple[str, int], dict[str, int]] = {}
    for task_id, repeat, method in recorded:
        counts.setdefault((task_id, repeat), {})
        counts[(task_id, repeat)][method] = counts[(task_id, repeat)].get(method, 0) + 1
    per_arm = {method: sum(1 for cell in recorded if cell[2] == method) for method in methods}
    duplicates = len(recorded) - len(set(recorded))
    missing = sorted(expected - set(recorded))
    extra = sorted(set(recorded) - expected)
    unbalanced = sorted(
        key for key, found in counts.items()
        if any(found.get(method, 0) != 1 for method in methods)
        or len(found) != len(methods)
    )
    return {
        "rows": len(rows),
        "expected_rows": len(expected),
        "duplicates": duplicates,
        "missing_cells": len(missing),
        "extra_cells": len(extra),
        "unbalanced_blocks": len(unbalanced),
        "per_arm": per_arm,
        "arm_order_rotated": len({tuple(cell[2] for cell in recorded[index:index + len(methods)])
                                  for index in range(0, len(recorded), len(methods))}) > 1,
        "complete": (
            not duplicates and not missing and not extra and not unbalanced
            and len(recorded) == len(expected)
        ),
    }


def audit(directory: Path = DEFAULT_BATCH, freeze_path: Path = FREEZE,
          out_path: Path | None = DEFAULT_OUT) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    errors: list[str] = []
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    pins = dict(freeze.get("source_sha256") or {})
    amendment = freeze_path.with_name(freeze_path.stem + "_FREEZE_AMENDMENT_20261006.json")
    if amendment.is_file():
        pins.update(
            (json.loads(amendment.read_text(encoding="utf-8")).get(
                "pinned_sources_after_amendment") or {})
        )
    for relative, expected in sorted(pins.items()):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"pinned source missing: {relative}")
        elif sha(path) != expected:
            errors.append(f"source drift (unregistered digest): {relative}")

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = load_rows(directory)
    tasks = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    expected_ids = [str(t["task_id"]) for t in tasks]
    methods = [str(m) for m in (freeze.get("methods") or [])]
    repeats = int(freeze.get("repeats", 0) or 0)

    if not rows:
        errors.append("no rows: an empty batch is never a pass")
    if manifest.get("purpose") != "three_arm_pilot":
        errors.append("manifest purpose is not the frozen three-arm pilot")
    if manifest.get("citable_as_saving") is not False or (
        manifest.get("citable_as_quality_equivalence") is not False
    ):
        errors.append("pilot is incorrectly marked citable")
    if manifest.get("freeze_sha256") != sha(freeze_path):
        errors.append("manifest freeze binding mismatch")
    if list(manifest.get("guard_installed_arms") or []):
        errors.append("manifest lists installed guard arms")
    if int(manifest.get("max_api_requests", 0) or 0) != int(freeze.get("max_api_requests", -1)):
        errors.append("manifest request cap differs from the freeze")
    if int(manifest.get("max_api_requests", 0) or 0) > 390:
        errors.append("request cap exceeds the frozen 390")

    grid = grid_check(rows, expected_ids, methods, repeats)
    if not grid["complete"]:
        errors.append(
            "recorded grid is not the frozen grid: "
            f"{grid['rows']} rows, {grid['missing_cells']} missing, {grid['extra_cells']} extra, "
            f"{grid['duplicates']} duplicated, {grid['unbalanced_blocks']} unbalanced blocks"
        )

    for row in rows:
        where = f"{row.get('task_id')}/{row.get('repeat')}/{row.get('method')}"
        attempts = attempts_of(row)
        summaries = summary_attempts_of(row)
        if row.get("agent_attempt_records") is None and not attempts:
            errors.append(f"per-attempt capture missing: {where}")
        if attempts and sum(
            int(a.get("input_tokens", 0) or 0) for a in attempts
        ) != int(row.get("agent_input_tokens", 0) or 0):
            errors.append(f"agent input token ledger mismatch: {where}")
        if attempts and sum(
            int(a.get("output_tokens", 0) or 0) for a in attempts
        ) != int(row.get("agent_output_tokens", 0) or 0):
            errors.append(f"agent output token ledger mismatch: {where}")
        if row.get("error"):
            errors.append(f"unit failed: {where}: {str(row['error'])[:120]}")
        if row.get("guard_installed") is not False:
            errors.append(f"row is not marked guardless: {where}")

    guards = guard_scan(rows)
    if not guards["zero_trigger"]:
        errors.append("guard activity or guard control text found on a no-guard batch")

    blocks = criteria_blocks(rows)
    if list(criteria.QUALITY_CRITERIA) != [criteria.CRIT_COVERAGE]:
        errors.append("the quality side is not exactly CRIT_R20_COVERAGE")
    for cid in (criteria.CRIT_REPEAT, criteria.CRIT_SAME_WORK):
        if blocks[cid]["gate"] is not False:
            errors.append(f"{cid} was promoted to a gate")
    for unit in blocks["units"]:
        if not unit["declared_expected_trace_matches_task_file"]:
            errors.append(f"row expected trace disagrees with the task file: {unit['task_id']}")

    paired = paired_comparison(rows)
    plugin_summary = paired["arm_summaries"][PLUGIN]
    quality_ok = bool(blocks[criteria.CRIT_COVERAGE]["all_satisfied"]) and bool(
        plugin_summary["quality_not_below_baseline"]
    )
    cost_ok = bool(plugin_summary["positive_majority"]) and (
        plugin_summary["mean_delta_tokens"] is not None
        and plugin_summary["mean_delta_tokens"] > 0
    )
    tokens = {arm: {
        "complete_provider_tokens": sum(
            complete_provider_tokens(r)["complete_provider_tokens"]
            for r in rows if str(r.get("method")) == arm
        ),
        "requests": sum(
            complete_provider_tokens(r)["agent_requests"] + complete_provider_tokens(r)["summary_requests"]
            for r in rows if str(r.get("method")) == arm
        ),
        "cache": {
            "prompt_cache_hit_tokens": sum(
                cache_split(r)["prompt_cache_hit_tokens"] for r in rows
                if str(r.get("method")) == arm
            ),
            "prompt_cache_miss_tokens": sum(
                cache_split(r)["prompt_cache_miss_tokens"] for r in rows
                if str(r.get("method")) == arm
            ),
        },
        "usd": {
            "peak_equivalent_usd": round(sum(
                usd(r)["peak_equivalent_usd"] for r in rows if str(r.get("method")) == arm
            ), 6),
            "billed_usd": round(sum(
                usd(r)["billed_usd"] for r in rows if str(r.get("method")) == arm
            ), 6),
        },
    } for arm in methods}
    all_cache_entries = [
        entry for row in rows for entry in attempts_of(row) + summary_attempts_of(row)
    ]
    hit_total = sum(int(e.get("prompt_cache_hit_tokens", 0) or 0) for e in all_cache_entries)
    miss_total = sum(int(e.get("prompt_cache_miss_tokens", 0) or 0) for e in all_cache_entries)

    report = {
        "artifact": "R20_ORDERFREE_3ARM_AUDIT_20261006",
        "independent": True,
        "imports_a_runner": False,
        "batch": rel(directory),
        "freeze": rel(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "freeze_declared_self_sha256": freeze.get("freeze_sha256"),
        "amendment_present": amendment.is_file(),
        "pins_checked": sorted(pins),
        "grid": {**grid, "repeats": repeats, "methods": methods, "tasks": expected_ids},
        "criteria": blocks,
        "guard_configuration": {
            "order_guard": "not_installed",
            "repeat_guard": "not_installed",
            "actionless_rule": "not_installed",
            "enabled_guard_requests_suppressed": int(
                manifest.get("enabled_guard_requests_suppressed", 0) or 0
            ),
            "scan": guards,
        },
        "paired": paired,
        "tokens_and_cost": tokens,
        "cache": {
            "attempts": len(all_cache_entries),
            "prompt_cache_hit_tokens": hit_total,
            "prompt_cache_miss_tokens": miss_total,
            "hit_share_percent": (
                round(100.0 * hit_total / (hit_total + miss_total), 2)
                if (hit_total + miss_total) else None
            ),
            "attempts_without_split": sum(
                1 for e in all_cache_entries
                if e.get("cache_tokens_source") in (None, "", "absent")
            ),
            "attempts_timestamped": sum(
                1 for e in all_cache_entries if e.get("utc_started")
            ),
            "attempts_off_peak": sum(
                1 for e in all_cache_entries if e.get("off_peak_window") is True
            ),
            "rule": "cache changes dollars only, never token counts",
        },
        "quality_decision": {
            "gate": "frozen r8 judge strictly/semantically not below baseline AND "
                    "CRIT_R20_COVERAGE on every unit",
            "coverage_all_satisfied": blocks[criteria.CRIT_COVERAGE]["all_satisfied"],
            "judge_not_below_baseline": plugin_summary["quality_not_below_baseline"],
            "verdict": "pass" if quality_ok else "fail",
        },
        "cost_decision": {
            "gate": "paired complete provider tokens positive on the majority of units and "
                    "positive in the batch mean; repeats and remedies charged in full",
            "positive_units": plugin_summary["positive_units"],
            "units": plugin_summary["units"],
            "mean_delta_tokens": plugin_summary["mean_delta_tokens"],
            "mean_delta_percent": plugin_summary["mean_delta_percent"],
            "verdict": "pass" if cost_ok else "fail",
        },
        "report_only": {
            "ids": blocks["report_only_ids"],
            "note": "repeats, call counts, tool traces, set equality and same work are "
                    "experimental control quantities on this contract, never gates",
            criteria.CRIT_REPEAT: blocks[criteria.CRIT_REPEAT],
            criteria.CRIT_SAME_WORK: {
                key: value for key, value in blocks[criteria.CRIT_SAME_WORK].items()
                if key != "pairs"
            },
        },
        "disclosure": {
            "development_evidence_only": (
                "these tasks were built while the order-free contract was being designed, so "
                "the batch is development evidence for that contract, not confirmation"
            ),
            "confirmation_needs_a_new_task_set": (
                "confirmation requires a brand-new task set built after the contract is frozen"
            ),
            "must_not_be_written_as": [
                "the plugin works on its own", "四判据达成", "compression alone saved X%",
            ],
            "attribution": freeze.get("attribution"),
            "cache_changes_dollars_only": True,
        },
        "errors_rule": ERRORS_RULE,
        "can_be_quoted_as_saving": not errors and cost_ok,
        "errors": errors,
        "complete": not errors,
        "criteria_module_sha256": sha(ROOT / "experiments/audits/criteria_crewai_r20_orderfree.py"),
        "audit_module_sha256": sha(Path(__file__)),
        "zero_api": True,
    }
    if out_path is not None:
        Path(out_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = Path(argv[0]) if argv else DEFAULT_BATCH
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    report = audit(directory, FREEZE, out)
    print(f"wrote {out}")
    print("errors: " + json.dumps(report["errors"], ensure_ascii=False))
    print("grid: " + json.dumps(report["grid"], ensure_ascii=False))
    print("quality: " + json.dumps(report["quality_decision"], ensure_ascii=False))
    print("cost: " + json.dumps(report["cost_decision"], ensure_ascii=False))
    print("paired plugin: " + json.dumps(
        report["paired"]["arm_summaries"][PLUGIN], ensure_ascii=False))
    print("paired summary: " + json.dumps(
        report["paired"]["arm_summaries"][SUMMARY], ensure_ascii=False))
    print("coverage: " + json.dumps(
        report["criteria"][criteria.CRIT_COVERAGE]["by_arm"], ensure_ascii=False))
    print("guards: " + json.dumps(
        {k: v for k, v in report["guard_configuration"]["scan"].items()
         if k != "control_text_occurrences"}, ensure_ascii=False))
    print("cache: " + json.dumps(report["cache"], ensure_ascii=False))
    print("tokens/cost: " + json.dumps(report["tokens_and_cost"], ensure_ascii=False))
    print("can_be_quoted_as_saving: " + str(report["can_be_quoted_as_saving"]))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
