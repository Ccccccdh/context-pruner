"""Independent audit of the r17 three-arm pilot (zero API).

This auditor is a separate module: it does **not** import the r17 runner, so it cannot
inherit a bug from it.  It reads the recorded batch, the freeze and the amendment ledger,
and re-derives:

  1. **the three acceptance conditions, exactly as the frozen protocol words them.**  The
     protocol writes condition 3 at **batch level** ("same work: the majority of pairs
     positive and the batch mean positive"), and only conditions 1 and 2 per task.  Both
     granularities are recorded: batch verdict per the pre-registration, per-task verdicts
     as an additional observation.  A stricter per-task reading of condition 3 is *not*
     the pre-registration and must not be presented as the batch verdict.
  2. the freeze/amendment admission: freeze self-hash fixed point, every pin resolved by
     the amendment-aware rule (fail-closed).
  3. the request ledger: agent attempts plus summary attempts plus failures, reconciled
     against the recorded per-row attempts and the cap.
  4. the per-call token totals and the paired token arithmetic.
  5. the guard ledger per arm, with the r17 stop condition (a guard firing on a
     non-plugin arm) as a hard error.
  6. pin / protected-round / fallback / summary presence and pairing.

Output: ``complete`` / ``errors`` / ``verdict`` plus the condition tables.  ``errors``
non-empty means the batch's numbers must not be quoted as a saving.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audits import crewai_amendment_hashes as pins  # noqa: E402

ARMS = ("none", "pruner_v1", "native_summary")
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01.json"
AMENDMENT = ROOT / (
    "integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01_FREEZE_AMENDMENT_20261005.json"
)
BOUND = ROOT / "integrations/crewai/R17_REAL_PAYLOAD_BOUND_20261005.json"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r17.json"
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _authority(freeze: dict, amendment_path: Path) -> dict[str, str]:
    authority = {
        name: value for name, value in (freeze.get("source_sha256") or {}).items()
        if value
    }
    if amendment_path.is_file():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        authority.update(amendment.get("pinned_sources_after_amendment") or {})
        for entry in amendment.get("amendments") or []:
            for item in entry.get("files") or []:
                authority[str(item["path"]).replace("\\", "/")] = str(
                    item["new_sha256"]
                )
    return authority


def audit(directory: Path | str, freeze_path: Path | str | None = None) -> dict:
    directory = Path(directory)
    freeze_path = Path(freeze_path) if freeze_path else FREEZE
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = _rows(directory)
    tasks = {
        str(task["task_id"]): task
        for task in json.loads(TASK_FILE.read_text(encoding="utf-8"))
    }
    errors: list[str] = []
    warnings: list[str] = []

    # ---- freeze admission -------------------------------------------------------
    clone = json.loads(json.dumps(freeze))
    clone["freeze_sha256"] = ""
    clone["manifest"]["freeze_sha256"] = ""
    if (
        hashlib.sha256(
            json.dumps(clone, ensure_ascii=False, indent=2).encode("utf-8")
        ).hexdigest()
        != freeze["freeze_sha256"]
    ):
        errors.append("freeze self-hash is not a fixed point")
    amendment_path = freeze_path.with_name(
        freeze_path.stem + "_FREEZE_AMENDMENT_20261005.json"
    )
    authority = _authority(freeze, amendment_path)
    for relative, expected in authority.items():
        if not _SHA.match(str(expected)):
            errors.append(f"unpinnable authority entry: {relative}")
            continue
        if _sha256(ROOT / relative) != expected:
            errors.append(f"unregistered pin drift: {relative}")
    if manifest.get("freeze_path") != str(freeze_path.relative_to(ROOT)).replace("\\", "/"):
        errors.append("manifest freeze_path mismatch")
    if manifest.get("freeze_sha256") != _sha256(freeze_path):
        errors.append("manifest freeze file hash mismatch")
    if manifest.get("freeze_declared_self_sha256") != freeze["freeze_sha256"]:
        errors.append("manifest freeze declared self-hash mismatch")
    for field, relative in (
        ("runner_sha256", "experiments/runners/run_crewai_handoff_v17.py"),
        ("guard_sha256", "experiments/runners/crewai_loop_guard_v17.py"),
        ("judge_sha256", "experiments/runners/crewai_semantic_equivalence_v8.py"),
        ("task_sha256", "tasks/stage5_autogen/natural_tasks_r17.json"),
    ):
        if field in manifest and _sha256(ROOT / relative) != manifest[field]:
            errors.append(f"manifest {field} does not match the recorded source")

    # ---- batch shape ------------------------------------------------------------
    frozen_tasks = list(freeze["manifest"]["tasks"])
    if [str(row["task_id"]) for row in rows][:0] and False:
        pass
    if sorted({str(row["task_id"]) for row in rows}) != sorted(frozen_tasks):
        errors.append("task set mismatch against the freeze")
    if int(manifest.get("repeats", -1)) != int(freeze["manifest"]["repeats"]):
        errors.append("repeat count mismatch against the freeze")
    if int(manifest.get("max_api_requests", -1)) != int(
        freeze["manifest"]["max_api_requests"]
    ):
        errors.append("request cap mismatch against the freeze")
    if manifest.get("guard_installed_arms") != freeze["manifest"]["guard_arms"] and (
        manifest.get("guard_installed_arms") != list(ARMS)
    ):
        errors.append("guard_installed_arms disagrees with the freeze")
    units_per_arm = int(freeze["units_per_arm"])
    by_arm = {arm: [r for r in rows if str(r["method"]) == arm] for arm in ARMS}
    for arm in ARMS:
        if len(by_arm[arm]) != units_per_arm:
            errors.append(f"{arm}: {len(by_arm[arm])} units, expected {units_per_arm}")
    for row in rows:
        if str(row["task_id"]) not in tasks:
            errors.append(f"unknown task: {row['task_id']}")

    # ---- request ledger ---------------------------------------------------------
    # ``api_request_attempts`` is the runner's charge for EVERY request the sample
    # consumed (agent calls, guard re-sends and auxiliary summary calls), so it is the
    # single source of truth for the ledger; the auxiliary breakdown is only a
    # cross-check, never an addend (adding it again would double-count summaries).
    ledger = {}
    for arm in ARMS:
        charged = sum(int(r["api_request_attempts"]) for r in by_arm[arm])
        agent = sum(int(r.get("agent_request_attempts", len(r["first_role_trace"]) + 2))
                    for r in by_arm[arm])
        auxiliary = sum(int(r.get("auxiliary_request_attempts", 0)) for r in by_arm[arm])
        summary = sum(int(r.get("summary_attempts", 0)) for r in by_arm[arm])
        resends = sum(int(r["guard_rejections"]) for r in by_arm[arm])
        failures = sum(1 for r in by_arm[arm] if r["error"])
        ledger[arm] = {
            "charged_requests": charged,
            "agent_attempts": agent,
            "auxiliary_attempts": auxiliary,
            "summary_attempts_recorded": summary,
            "guard_resend_requests": resends,
            "failed_units": failures,
        }
        for row in by_arm[arm]:
            recorded_row = int(row["api_request_attempts"])
            breakdown = int(
                row.get("agent_request_attempts", len(row["first_role_trace"]) + 2)
            ) + int(row.get("auxiliary_request_attempts", 0))
            if breakdown != recorded_row:
                errors.append(
                    f"{arm}/{row['task_id']}/{row['repeat']}: charged requests "
                    f"{recorded_row} != agent+auxiliary {breakdown}"
                )
        if auxiliary and summary and summary != auxiliary:
            warnings.append(
                f"{arm}: auxiliary attempts {auxiliary} != recorded summary attempts "
                f"{summary}"
            )
    charged = sum(entry["charged_requests"] for entry in ledger.values())
    recorded = sum(int(r["api_request_attempts"]) for r in rows)
    cap = int(manifest.get("max_api_requests", 0))
    if charged != recorded:
        errors.append(
            f"request ledger mismatch: charged {charged} != recorded {recorded}"
        )
    if charged > cap:
        errors.append(
            f"request ledger exceeds the declared cap: {charged} > {cap} "
            "(every request is charged, including summary calls)"
        )

    # ---- per-call tokens --------------------------------------------------------
    token_totals = {}
    for arm in ARMS:
        per_row = [
            {
                "task_id": str(r["task_id"]),
                "repeat": int(r["repeat"]),
                "complete_total": int(r["all_arm_total_tokens"]),
                "agent_input": int(r["agent_input_tokens"]),
                "agent_output": int(r["agent_output_tokens"]),
                "summary_input": int(r.get("summary_input_tokens", 0)),
                "summary_output": int(r.get("summary_output_tokens", 0)),
                "first_role_calls": len(r["first_role_trace"]),
                "charged_requests": int(r["api_request_attempts"]),
            }
            for r in by_arm[arm]
        ]
        token_totals[arm] = {
            "rows": len(per_row),
            "sum_complete_total": sum(e["complete_total"] for e in per_row),
            "sum_agent_input": sum(e["agent_input"] for e in per_row),
            "sum_agent_output": sum(e["agent_output"] for e in per_row),
            "sum_summary_input": sum(e["summary_input"] for e in per_row),
            "sum_summary_output": sum(e["summary_output"] for e in per_row),
            "detail": per_row,
        }
        # The arm's complete-total token figure must equal every token the sample paid for:
        # the agent loop's input+output plus the auxiliary summariser's input+output.
        for entry in per_row:
            expected = (
                entry["agent_input"] + entry["agent_output"]
                + entry["summary_input"] + entry["summary_output"]
            )
            if entry["complete_total"] != expected:
                errors.append(
                    f"{arm}/{entry['task_id']}/{entry['repeat']}: complete total "
                    f"{entry['complete_total']} != agent+summary {expected}"
                )

    # ---- guard ledger -----------------------------------------------------------
    guard = {}
    for arm in ARMS:
        selected = by_arm[arm]
        # The reasons are re-derived from the rejection records themselves (the row-level
        # convenience counters are not written by this runner), so the sum is authoritative.
        reasons = [
            str(record.get("reason"))
            for r in selected
            for record in (r.get("guard_rejection_records") or [])
        ]
        guard[arm] = {
            "units": len(selected),
            "guard_enabled": sum(1 for r in selected if r["guard_enabled"]),
            "rejections": sum(int(r["guard_rejections"]) for r in selected),
            "resends": sum(int(r["guard_rejections"]) for r in selected),
            "actionless_rejections": sum(
                1 for reason in reasons if reason == "reject_actionless"
            ),
            "final_answer_rejections": sum(
                1 for reason in reasons if reason == "reject_final_answer"
            ),
            "rejection_records": len(reasons),
            "exhausted": sum(1 for r in selected if r["guard_exhausted"]),
            "units_that_triggered": sum(
                1 for r in selected if int(r["guard_rejections"]) > 0
            ),
        }
        if len(reasons) != guard[arm]["rejections"]:
            errors.append(
                f"{arm}: {guard[arm]['rejections']} rejections recorded but "
                f"{len(reasons)} rejection records"
            )
    installed = list(manifest.get("guard_installed_arms") or [])
    for arm in ARMS:
        if arm in installed and guard[arm]["guard_enabled"] != units_per_arm:
            errors.append(
                f"{arm}: guard installed but enabled on only "
                f"{guard[arm]['guard_enabled']} units"
            )
    for arm in ARMS:
        if arm == "pruner_v1":
            continue
        if guard[arm]["rejections"] or guard[arm]["exhausted"]:
            errors.append(
                f"{arm}: guard fired on a non-plugin arm (r17 stop condition)"
            )
    if guard["pruner_v1"]["exhausted"]:
        errors.append("guard exhausted in the plugin arm")

    # ---- quality (conditions 1 and 2, per task and batch) -----------------------
    quality = {}
    for arm in ARMS:
        selected = by_arm[arm]
        quality[arm] = {
            "tool_sequence_consistent": sum(
                1 for r in selected if r["tool_sequence_consistent"]
            ),
            "strict_success": sum(1 for r in selected if r["strict_success"]),
            "semantic_success": sum(1 for r in selected if r["semantic_success"]),
            "first_pass_complete": sum(
                1 for r in selected if r["first_pass_complete"]
            ),
            "errors": sum(1 for r in selected if r["error"]),
        }
    per_task_quality = {}
    for task_id in sorted(tasks):
        per_task_quality[task_id] = {}
        for arm in ARMS:
            selected = [r for r in by_arm[arm] if str(r["task_id"]) == task_id]
            per_task_quality[task_id][arm] = {
                "tool_sequence_consistent": sum(
                    1 for r in selected if r["tool_sequence_consistent"]
                ),
                "strict_success": sum(1 for r in selected if r["strict_success"]),
                "semantic_success": sum(1 for r in selected if r["semantic_success"]),
                "first_pass_complete": sum(
                    1 for r in selected if r["first_pass_complete"]
                ),
                "units": len(selected),
            }

    # ---- pairing and cost -------------------------------------------------------
    baseline = {(str(r["task_id"]), int(r["repeat"])): r for r in by_arm["none"]}
    paired = []
    for row in by_arm["pruner_v1"]:
        key = (str(row["task_id"]), int(row["repeat"]))
        base = baseline.get(key)
        if base is None:
            warnings.append(f"unpairable plugin unit: {key}")
            continue
        same_work = (
            int(base["api_request_attempts"]) == int(row["api_request_attempts"])
            and list(base["first_role_trace"]) == list(row["first_role_trace"])
        )
        base_tokens = int(base["all_arm_total_tokens"])
        plugin_tokens = int(row["all_arm_total_tokens"])
        paired.append(
            {
                "task_id": key[0],
                "repeat": key[1],
                "same_work": same_work,
                "baseline_tokens": base_tokens,
                "plugin_tokens": plugin_tokens,
                "saving_percent": round(
                    100.0 * (base_tokens - plugin_tokens) / max(1, base_tokens), 3
                ),
                "baseline_trace_len": len(base["first_role_trace"]),
                "plugin_trace_len": len(row["first_role_trace"]),
                "requests_equal": int(base["api_request_attempts"])
                == int(row["api_request_attempts"]),
            }
        )
    same = [entry for entry in paired if entry["same_work"]]
    per_task_cost = {}
    for entry in paired:
        per_task_cost.setdefault(str(entry["task_id"]), []).append(entry)
    per_task_cost_summary = {
        task_id: {
            "paired_units": len(entries),
            "same_work_units": sum(1 for e in entries if e["same_work"]),
            "mean_saving_percent": round(
                statistics.fmean(e["saving_percent"] for e in entries), 3
            ),
            "mean_saving_same_work_percent": (
                round(
                    statistics.fmean(
                        e["saving_percent"] for e in entries if e["same_work"]
                    ),
                    3,
                )
                if any(e["same_work"] for e in entries) else None
            ),
        }
        for task_id, entries in sorted(per_task_cost.items())
    }
    all_pairs_mean = (
        round(statistics.fmean(e["saving_percent"] for e in paired), 3)
        if paired else None
    )
    same_work_mean = (
        round(statistics.fmean(e["saving_percent"] for e in same), 3) if same else None
    )
    same_work_positive = sum(1 for e in same if e["saving_percent"] > 0)
    dispersion = (
        round(
            max(v["mean_saving_percent"] for v in per_task_cost_summary.values())
            - min(v["mean_saving_percent"] for v in per_task_cost_summary.values()),
            3,
        )
        if per_task_cost_summary else None
    )

    # ---- protection / presence --------------------------------------------------
    protection = {}
    for arm in ("pruner_v1", "native_summary"):
        selected = by_arm[arm]
        protection[arm] = {
            "pinned_characters_total": sum(
                int(r.get("pinned_characters_total", 0)) for r in selected
            ),
            "protected_rounds_total": sum(
                int(r.get("recency_protected_rounds_total", 0)) for r in selected
            ),
            "whole_prefix_fallbacks": sum(
                int(r.get("required_fact_whole_prefix_fallbacks", 0))
                for r in selected
            ),
            "summary_attempts": sum(
                int(r.get("summary_attempts", 0)) for r in selected
            ),
            "summary_input_tokens": sum(
                int(r.get("summary_input_tokens", 0)) for r in selected
            ),
            "summary_output_tokens": sum(
                int(r.get("summary_output_tokens", 0)) for r in selected
            ),
        }
    restoration = sum(
        int((r.get("role_metrics") or [{}])[0].get(
            "crewai_group_restore_failure_count", 0
        ) or 0)
        for r in rows
    )
    if restoration:
        errors.append(f"{restoration} tool-group restore failures recorded")
    # presence: the plugin arm must actually compress, the baseline must not
    if quality["pruner_v1"]["tool_sequence_consistent"] == 0:
        errors.append("plugin arm produced no complete tool sequence")

    # ---- conditions, exactly as frozen -----------------------------------------
    condition_1_per_task = {
        task_id: {
            "plugin_tool_sequence_consistent": per_task_quality[task_id]["pruner_v1"][
                "tool_sequence_consistent"
            ],
            "baseline_tool_sequence_consistent": per_task_quality[task_id]["none"][
                "tool_sequence_consistent"
            ],
            "plugin_first_pass_complete": per_task_quality[task_id]["pruner_v1"][
                "first_pass_complete"
            ],
            "baseline_first_pass_complete": per_task_quality[task_id]["none"][
                "first_pass_complete"
            ],
            "not_below_baseline": (
                per_task_quality[task_id]["pruner_v1"]["tool_sequence_consistent"]
                >= per_task_quality[task_id]["none"]["tool_sequence_consistent"]
                and per_task_quality[task_id]["pruner_v1"]["first_pass_complete"]
                >= per_task_quality[task_id]["none"]["first_pass_complete"]
            ),
        }
        for task_id in sorted(tasks)
    }
    condition_1 = {
        "batch": {
            "plugin_tool_sequence_consistent": quality["pruner_v1"][
                "tool_sequence_consistent"
            ],
            "units": units_per_arm,
            "guard_exhausted": guard["pruner_v1"]["exhausted"],
            "non_plugin_guard_activity": guard["none"]["rejections"]
            + guard["native_summary"]["rejections"],
        },
        "per_task": condition_1_per_task,
    }
    condition_1["satisfied"] = (
        condition_1["batch"]["plugin_tool_sequence_consistent"] == units_per_arm
        and all(entry["not_below_baseline"] for entry in condition_1_per_task.values())
        and condition_1["batch"]["guard_exhausted"] == 0
        and condition_1["batch"]["non_plugin_guard_activity"] == 0
    )
    condition_2_per_task = {
        task_id: {
            "strict_not_below_baseline": (
                per_task_quality[task_id]["pruner_v1"]["strict_success"]
                >= per_task_quality[task_id]["none"]["strict_success"]
            ),
            "semantic_not_below_baseline": (
                per_task_quality[task_id]["pruner_v1"]["semantic_success"]
                >= per_task_quality[task_id]["none"]["semantic_success"]
            ),
            "plugin_strict": per_task_quality[task_id]["pruner_v1"]["strict_success"],
            "baseline_strict": per_task_quality[task_id]["none"]["strict_success"],
            "plugin_semantic": per_task_quality[task_id]["pruner_v1"][
                "semantic_success"
            ],
            "baseline_semantic": per_task_quality[task_id]["none"]["semantic_success"],
        }
        for task_id in sorted(tasks)
    }
    condition_2 = {
        "batch": {
            arm: {
                "strict_success": quality[arm]["strict_success"],
                "semantic_success": quality[arm]["semantic_success"],
            }
            for arm in ARMS
        },
        "per_task": condition_2_per_task,
    }
    condition_2["satisfied"] = (
        all(
            entry["strict_not_below_baseline"] and entry["semantic_not_below_baseline"]
            for entry in condition_2_per_task.values()
        )
        and quality["pruner_v1"]["strict_success"] >= quality["none"]["strict_success"]
        and quality["pruner_v1"]["semantic_success"]
        >= quality["none"]["semantic_success"]
    )
    # Condition 3 is the protocol's BATCH-level wording.
    majority_positive = bool(same) and same_work_positive * 2 > len(same)
    condition_3 = {
        "frozen_wording": "same work: the majority of pairs positive AND the batch mean "
                          "positive; fewer tool calls never counts as saving",
        "batch": {
            "same_work_units": len(same),
            "paired_units": len(paired),
            "same_work_positive": same_work_positive,
            "majority_positive": majority_positive,
            "same_work_mean_percent": same_work_mean,
            "all_pairs_mean_percent": all_pairs_mean,
        },
        "satisfied": bool(
            majority_positive and same_work_mean is not None and same_work_mean > 0
            and all_pairs_mean is not None and all_pairs_mean > 0
        ),
        "per_task_additional_observation": per_task_cost_summary,
        "per_task_note": (
            "per-task cost is an ADDITIONAL observation, not the frozen criterion; a task "
            "with no same-work unit cannot satisfy a per-task reading, and that stricter "
            "reading must not be presented as the batch verdict"
        ),
    }

    # ---- bound realisation ------------------------------------------------------
    bound = json.loads(BOUND.read_text(encoding="utf-8")) if BOUND.is_file() else {}
    bound_reference = (bound.get("batch_totals") or {}).get(
        "safe_candidate_reference_percent"
    )
    realisation = (
        round(100.0 * same_work_mean / bound_reference, 2)
        if same_work_mean is not None and bound_reference else None
    )

    verdict = "unspecified"
    if errors:
        verdict = (
            "cannot be quoted as a saving: the independent audit reports errors"
        )
    elif condition_1["satisfied"] and condition_2["satisfied"] and condition_3[
        "satisfied"
    ]:
        verdict = (
            "valid saving candidate under this host configuration (development "
            "evidence: the tasks were built while the guard was designed)"
        )
    else:
        verdict = "not_passed_quality_gate"
    return {
        "batch": directory.name,
        "freeze": str(freeze_path.relative_to(ROOT)).replace("\\", "/"),
        "freeze_file_sha256": _sha256(freeze_path),
        "complete": not errors,
        "errors": errors,
        "warnings": warnings,
        "units_per_arm": units_per_arm,
        "requests": {
            "recorded": recorded,
            "charged": charged,
            "cap": cap,
            "ledger": ledger,
        },
        "tokens": token_totals,
        "guard": guard,
        "quality": quality,
        "quality_per_task": per_task_quality,
        "protection": protection,
        "paired": {
            "units": len(paired),
            "same_work_units": len(same),
            "same_work_positive": same_work_positive,
            "same_work_mean_percent": same_work_mean,
            "all_pairs_mean_percent": all_pairs_mean,
            "dispersion_percentage_points": dispersion,
            "detail": paired,
            "per_task": per_task_cost_summary,
        },
        "conditions": {
            "condition_1_behaviour": condition_1,
            "condition_2_quality": condition_2,
            "condition_3_cost": condition_3,
        },
        "bound": {
            "safe_candidate_reference_percent": bound_reference,
            "realisation_vs_same_work_percent": realisation,
            "note": "the bound is analytic; >100% means the analytic bound is conservative",
        },
        "verdict": verdict,
    }


def main() -> int:
    directory = (
        Path(sys.argv[1]) if len(sys.argv) > 1
        else ROOT / "runs/stage5-crewai/crewai-two-role-r17-guarded-3arm-01"
    )
    result = audit(directory, FREEZE)
    target = Path(directory) / "R17_INDEPENDENT_AUDIT.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"wrote {target.relative_to(ROOT)}"]
    lines.append(f"complete={result['complete']} errors={len(result['errors'])}")
    for error in result["errors"]:
        lines.append(f"  ERR {error}")
    for warning in result["warnings"]:
        lines.append(f"  WARN {warning}")
    lines.append(f"requests={json.dumps(result['requests'], ensure_ascii=False)}")
    lines.append(f"guard={json.dumps(result['guard'], ensure_ascii=False)}")
    lines.append(f"quality={json.dumps(result['quality'], ensure_ascii=False)}")
    lines.append(
        "conditions: 1=%s 2=%s 3=%s"
        % (
            result["conditions"]["condition_1_behaviour"]["satisfied"],
            result["conditions"]["condition_2_quality"]["satisfied"],
            result["conditions"]["condition_3_cost"]["satisfied"],
        )
    )
    lines.append(
        "cost batch="
        + json.dumps(result["conditions"]["condition_3_cost"]["batch"], ensure_ascii=False)
    )
    lines.append(
        "cost per-task="
        + json.dumps(
            result["conditions"]["condition_3_cost"]["per_task_additional_observation"],
            ensure_ascii=False,
        )
    )
    lines.append(f"paired={json.dumps({k: v for k, v in result['paired'].items() if k not in ('detail',)}, ensure_ascii=False)}")
    lines.append(f"protection={json.dumps(result['protection'], ensure_ascii=False)}")
    lines.append(f"bound={json.dumps(result['bound'], ensure_ascii=False)}")
    lines.append(f"verdict={result['verdict']}")
    text = "\n".join(lines)
    (ROOT / ".tooling/tmp/r17_audit.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
