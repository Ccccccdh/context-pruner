"""Independent r16 audit: hard gates plus the controller-guard ledger.

Imports only the frozen r9 pinning module, the frozen r8 judge rules and the frozen r16
task file; never the r16 runner.  On top of the r15 checks (hard
``tool_sequence_consistent`` and ``first_pass_facts``, re-derived ``failure_class``,
per-task non-inferiority, same-work pairing, request ledger) it audits the guard:

  * every plugin unit must have the guard enabled with the frozen tool count;
  * a unit whose sequence is complete must have reached the required rounds (or never
    have needed the guard at all -- a complete run triggers no rejection);
  * every rejection record must be internally consistent (completion count below the
    required count, and a refusal reason);
  * a ``guard_exhausted`` unit must be recorded as a failure and can never be counted as
    a saving; exhaustion also means the extra requests were charged to the arm.

``errors`` non-empty always means the batch cannot be cited as an effective saving.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any

from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as judge


ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "native_summary", "pruner_v1")
SAFETY_FIELDS = (
    "crewai_group_restore_failure_count",
    "crewai_unmatched_call_count",
    "crewai_active_pending_view_count",
)
FAILURE_CLASSES = ("none", "tool_sequence", "contract_placeholder_echo",
                   "guard_exhausted", "other")
TASKS = (
    "artifact_publish_gate",
    "quota_scale_gate",
    "traffic_shift_gate",
    "failover_switch_gate",
)
REPEATS = 3
UNITS_PER_ARM = 12
REQUIRED_ROUNDS = 4
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r16.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_failure_class(row: dict, task: dict) -> str:
    if bool(row.get("guard_exhausted")):
        return "guard_exhausted"
    if not bool(row.get("tool_sequence_consistent")):
        return "tool_sequence"
    if bool(row.get("strict_success")) and bool(row.get("semantic_success")):
        return "none"
    outputs = list(row.get("role_outputs") or [])
    answer = str(outputs[-1]) if outputs else ""
    verdict = (row.get("answer_verdict") or {}).get("semantic") or {}
    seen = judge.canonical_decision(str(verdict.get("decision_seen") or ""))
    expected = judge.canonical_decision(str(task["decision"]))
    if "<" in answer or ">" in answer or seen != expected:
        return "contract_placeholder_echo"
    return "other"


def audit(directory: Path | str, freeze_path: Path | str | None = None) -> dict:
    # Accept plain paths so a caller cannot silently audit nothing by passing a string.
    directory = Path(directory)
    freeze_path = Path(freeze_path) if freeze_path is not None else None
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tasks = {str(task["task_id"]): task for task in judge.load_tasks(TASK_FILE)}
    errors: list[str] = []
    if freeze_path is not None:
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("batch") != directory.name:
            errors.append("freeze batch mismatch")
        sources = freeze.get("source_sha256", {})
        for name, expected in sources.items():
            path = ROOT / name
            if not path.is_file():
                errors.append(f"frozen source missing: {name}")
            elif expected and _sha256(path) != expected:
                # An empty expected value means the freeze cannot contain its own digest;
                # the two manifest fields below carry that value instead.
                errors.append(f"frozen source mismatch: {name}")
        # The freeze self-hash must be a fixed point of its own blanked canonical dump,
        # and the batch manifest must carry the freeze file's byte hash plus that value.
        clone = json.loads(json.dumps(freeze))
        clone["freeze_sha256"] = ""
        if isinstance(clone.get("manifest"), dict):
            clone["manifest"]["freeze_sha256"] = ""
        canonical = json.dumps(clone, ensure_ascii=False, indent=2).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != freeze.get("freeze_sha256"):
            errors.append("freeze self-hash is not a fixed point")
        if manifest.get("freeze_path") != str(freeze_path).replace("\\", "/"):
            errors.append("manifest freeze_path mismatch")
        if manifest.get("freeze_sha256") and manifest["freeze_sha256"] != _sha256(
            freeze_path
        ):
            errors.append("manifest freeze file hash mismatch")
        if manifest.get("freeze_declared_self_sha256") != str(
            freeze.get("freeze_sha256") or ""
        ):
            errors.append("manifest freeze declared self-hash mismatch")
        # Fields the freeze and the batch manifest must agree on, by name.
        for field in ("protocol", "tasks", "repeats", "units_per_arm", "max_api_requests"):
            if field in freeze.get("manifest", {}):
                if freeze["manifest"][field] != manifest.get(field):
                    errors.append(f"manifest mismatch: {field}")
        source_fields = {
            "runner_sha256": "experiments/runners/run_crewai_handoff_v16.py",
            "guard_sha256": "experiments/runners/crewai_loop_guard_v16.py",
            "mechanism_sha256": "experiments/runners/crewai_toolrounds_contract_v13.py",
            "pinned_evidence_sha256": "experiments/runners/crewai_pinned_evidence_v9.py",
            "judge_sha256": "experiments/runners/crewai_semantic_equivalence_v8.py",
            "task_sha256": "tasks/stage5_autogen/natural_tasks_r16.json",
        }
        for field, relative in source_fields.items():
            expected = freeze.get("manifest", {}).get(field)
            if expected and manifest.get(field) != expected:
                errors.append(f"manifest mismatch: {field} vs {relative}")
    if list(manifest.get("tasks", [])) != list(TASKS):
        errors.append("frozen task set mismatch")
    if int(manifest.get("repeats", -1)) != REPEATS:
        errors.append("frozen repeat count mismatch")
    if int(manifest.get("units_per_arm", -1)) != UNITS_PER_ARM:
        errors.append("units_per_arm mismatch")
    if _sha256(Path(pinned.__file__)) != manifest.get("pinned_evidence_sha256"):
        errors.append("pinned evidence hash mismatch")

    expected_keys = {
        (task, repeat, method)
        for task in manifest["tasks"]
        for repeat in range(int(manifest["repeats"]))
        for method in manifest["methods"]
    }
    keys = [(row["task_id"], int(row["repeat"]), row["method"]) for row in rows]
    if set(keys) - expected_keys:
        errors.append("unexpected sample key")
    counters: dict[str, dict[str, Any]] = {
        method: {
            "n": 0, "strict_success": 0, "semantic_success": 0, "first_pass_facts": 0,
            "tool_sequence_consistent": 0, "same_work": 0, "recovered": 0,
            "summary_attempts": 0, "complete_total_tokens": 0, "errors": 0,
            "failure_classes": {name: 0 for name in FAILURE_CLASSES},
        }
        for method in manifest["methods"]
    }
    per_task_counts: dict[str, dict[str, dict[str, int]]] = {
        task: {method: {"n": 0, "strict_success": 0, "semantic_success": 0,
                        "first_pass_facts": 0, "tool_sequence_consistent": 0}
               for method in manifest["methods"]}
        for task in manifest["tasks"]
    }
    guard_gate = {
        "plugin_samples": 0,
        "guard_enabled_every_plugin_unit": True,
        "plugin_samples_with_exhausted_guard": 0,
        "plugin_guard_required_rounds_total": 0,
        "plugin_guard_rejections_total": 0,
        "plugin_guard_rejection_records": [],
        "non_plugin_guard_activity": 0,
        # r17 constraint: the guard is a host-side controller configuration.  The batch
        # manifest declares which arms carry it; when it is installed on every arm the
        # audit requires it to be **enabled** on every unit and to be **inert** on the
        # non-plugin arms (zero rejections there), so the plugin arm differs from the
        # baseline only by the compression mechanism.
        "guard_installed_arms": list(manifest.get("guard_installed_arms") or []),
        "guard_enabled_every_unit": True,
        "non_plugin_guard_enabled_units": 0,
        "non_plugin_guard_rejections": 0,
        "non_plugin_units": 0,
    }
    total_requests = 0
    for row in rows:
        task_id = str(row["task_id"])
        repeat = int(row["repeat"])
        method = str(row["method"])
        key = f"{task_id}/{repeat}/{method}"
        task = tasks.get(task_id)
        counter = counters[method]
        if task is None:
            errors.append(f"{key}: unknown task")
            continue
        expected_class = _expected_failure_class(row, task)
        recorded_class = str(row.get("failure_class") or "")
        if recorded_class != expected_class:
            errors.append(
                f"{key}: failure class mismatch ({recorded_class!r} != {expected_class!r})"
            )
        counter["failure_classes"][expected_class] += 1
        # ---- guard ledger ------------------------------------------------
        if method == "pruner_v1":
            guard_gate["plugin_samples"] += 1
            if not row.get("guard_enabled"):
                guard_gate["guard_enabled_every_plugin_unit"] = False
                errors.append(f"{key}: guard not enabled in a plugin unit")
                guard_gate["guard_enabled_every_unit"] = False
            if int(row.get("guard_required_rounds", 0)) != REQUIRED_ROUNDS:
                errors.append(f"{key}: guard required-rounds mismatch")
            guard_gate["plugin_guard_required_rounds_total"] += int(
                row.get("guard_required_rounds", 0)
            )
            guard_gate["plugin_guard_rejections_total"] += int(
                row.get("guard_rejections", 0)
            )
            if row.get("guard_exhausted"):
                guard_gate["plugin_samples_with_exhausted_guard"] += 1
                if row.get("strict_success") or row.get("semantic_success"):
                    errors.append(f"{key}: exhausted guard unit counted as success")
            for record in row.get("guard_rejection_records") or []:
                guard_gate["plugin_guard_rejection_records"].append(
                    {"key": key, **record}
                )
                if int(record.get("completed_rounds", 0)) >= REQUIRED_ROUNDS:
                    errors.append(f"{key}: rejection recorded although the table was complete")
            if bool(row.get("tool_sequence_consistent")) and int(
                row.get("guard_rejections", 0)
            ) == 0 and not row.get("guard_reached_required_rounds"):
                # A smooth run needs no rejection; that is consistent, not an error.
                pass
        else:
            guard_gate["non_plugin_units"] += 1
            installed_here = (
                bool(guard_gate["guard_installed_arms"])
                and method in guard_gate["guard_installed_arms"]
            )
            rejections = int(row.get("guard_rejections", 0) or 0)
            if row.get("guard_enabled"):
                guard_gate["non_plugin_guard_enabled_units"] += 1
            guard_gate["non_plugin_guard_rejections"] += rejections
            if installed_here:
                # The guard was installed on this arm on purpose.  It must be enabled and
                # must stay inert: a rejection (or an exhaustion) here is the r17 stop
                # condition, because it means the frozen tool-table constraint is not
                # one-way on this host configuration and the batch cannot be quoted as a
                # plugin-arm effect.
                if not row.get("guard_enabled"):
                    errors.append(f"{key}: guard installed but not enabled on this arm")
                if rejections or row.get("guard_exhausted"):
                    guard_gate["non_plugin_guard_activity"] += 1
                    errors.append(
                        f"{key}: guard fired on a non-plugin arm (r17 stop condition)"
                    )
            else:
                # The older arm-scoped layout: no guard activity may appear at all.
                if row.get("guard_enabled") or rejections:
                    guard_gate["non_plugin_guard_activity"] += 1
                    errors.append(f"{key}: guard activity outside the plugin arm")
        outputs = list(row.get("role_outputs") or [])
        if len(outputs) != 2:
            errors.append(f"{key}: wrong number of role outputs")
            counter["n"] += 1
            counter["errors"] += 1
            total_requests += int(row.get("api_request_attempts", 0))
            continue
        handle_rule = judge.handle_rule_for([task], task_id)
        recorded_raw = (row.get("role_raw_outputs") or outputs)[0]
        rejudged = judge.judge_handoff(handle_rule, recorded_raw)
        first_ok = bool(not rejudged.semantic["missing_facts"]
                        and rejudged.semantic["one_line"])
        if bool(row.get("first_pass_complete")) != first_ok:
            errors.append(f"{key}: first-pass completeness mismatch")
        expected_trace = [str(tool["name"]) for tool in task["tools"]]
        trace_ok = list(row.get("first_role_trace") or []) == expected_trace
        if bool(row.get("tool_sequence_consistent")) != trace_ok:
            errors.append(f"{key}: tool-sequence flag mismatch")
        if not trace_ok and method == "pruner_v1":
            errors.append(f"{key}: plugin tool sequence inconsistent with the frozen task")
        verdict = judge.judge_answer(judge.answer_rule_for([task], task_id), outputs[1])
        strict_valid = bool(not row["error"] and trace_ok and verdict.strict_pass)
        semantic_valid = bool(not row["error"] and trace_ok and verdict.semantic_pass)
        if bool(row.get("strict_success")) != strict_valid:
            errors.append(f"{key}: strict success mismatch")
        if bool(row.get("semantic_success")) != semantic_valid:
            errors.append(f"{key}: semantic success mismatch")
        tokens = sum(int(row[field]) for field in (
            "agent_input_tokens", "agent_output_tokens",
            "summary_input_tokens", "summary_output_tokens",
        ))
        if int(row["all_arm_total_tokens"]) != tokens:
            errors.append(f"{key}: token sum mismatch")
        requests = int(row["api_request_attempts"])
        if manifest.get("mode") == "api" and requests != (
            len(row["agent_attempt_records"]) + int(row["summary_attempts"])
        ):
            errors.append(f"{key}: request accounting mismatch")
        total_requests += requests
        counter["n"] += 1
        counter["strict_success"] += int(strict_valid)
        counter["semantic_success"] += int(semantic_valid)
        counter["first_pass_facts"] += int(first_ok)
        counter["tool_sequence_consistent"] += int(trace_ok)
        counter["recovered"] += int(row.get("recovery_invocations", 0))
        counter["summary_attempts"] += int(row.get("summary_attempts", 0))
        counter["complete_total_tokens"] += int(row["all_arm_total_tokens"])
        counter["errors"] += int(bool(row["error"]))
        counter["same_work"] += int(bool(row.get("same_work_as_baseline")))
        per_task_counts[task_id][method]["n"] += 1
        per_task_counts[task_id][method]["strict_success"] += int(strict_valid)
        per_task_counts[task_id][method]["semantic_success"] += int(semantic_valid)
        per_task_counts[task_id][method]["first_pass_facts"] += int(first_ok)
        per_task_counts[task_id][method]["tool_sequence_consistent"] += int(trace_ok)

    by_key = {(str(r["task_id"]), int(r["repeat"]), str(r["method"])): r for r in rows}
    paired: dict[str, Any] = {}
    all_pairs: list[float] = []
    for task_id in manifest["tasks"]:
        pairs = []
        for repeat in range(int(manifest["repeats"])):
            base_row = by_key.get((task_id, repeat, "none"))
            plugin_row = by_key.get((task_id, repeat, "pruner_v1"))
            if base_row is None or plugin_row is None:
                continue
            if len(base_row["role_outputs"]) != 2 or len(plugin_row["role_outputs"]) != 2:
                continue
            base_total = int(base_row["all_arm_total_tokens"])
            if not base_total:
                continue
            pairs.append(
                round((base_total - int(plugin_row["all_arm_total_tokens"])) / base_total * 100, 4)
            )
        paired[task_id] = {
            "per_repeat": pairs,
            "mean_percent": round(mean(pairs), 4) if pairs else None,
            "positive": sum(1 for value in pairs if value > 0),
            "n": len(pairs),
        }
        all_pairs.extend(pairs)
    plugin = counters["pruner_v1"]
    baseline = counters["none"]
    per_task_non_inferior = {
        task_id: {
            "tool_sequence_not_below_baseline": (
                per_task_counts[task_id]["pruner_v1"]["tool_sequence_consistent"]
                >= per_task_counts[task_id]["none"]["tool_sequence_consistent"]
            ),
            "strict_not_below_baseline": (
                per_task_counts[task_id]["pruner_v1"]["strict_success"]
                >= per_task_counts[task_id]["none"]["strict_success"]
            ),
            "semantic_not_below_baseline": (
                per_task_counts[task_id]["pruner_v1"]["semantic_success"]
                >= per_task_counts[task_id]["none"]["semantic_success"]
            ),
            "first_pass_not_below_baseline": (
                per_task_counts[task_id]["pruner_v1"]["first_pass_facts"]
                >= per_task_counts[task_id]["none"]["first_pass_facts"]
            ),
        }
        for task_id in manifest["tasks"]
    }
    behaviour_ok = bool(
        plugin["tool_sequence_consistent"] >= UNITS_PER_ARM
        and all(entry["tool_sequence_not_below_baseline"]
                for entry in per_task_non_inferior.values())
        and plugin["first_pass_facts"] >= baseline["first_pass_facts"]
    )
    quality_ok = bool(
        plugin["strict_success"] >= baseline["strict_success"]
        and plugin["semantic_success"] >= baseline["semantic_success"]
        and all(entry["strict_not_below_baseline"] and entry["semantic_not_below_baseline"]
                for entry in per_task_non_inferior.values())
    )
    batch_mean = round(mean(all_pairs), 4) if all_pairs else None
    cost_ok = bool(batch_mean is not None and batch_mean > 0
                   and sum(1 for v in all_pairs if v > 0) > len(all_pairs) / 2)
    all_three = bool(behaviour_ok and quality_ok and cost_ok and not errors)
    reasons: list[str] = []
    if not behaviour_ok:
        reasons.append("behaviour equivalence not met under the guard")
    if not quality_ok:
        reasons.append("quality not non-inferior")
    if not cost_ok:
        reasons.append("paired tokens not positive on the majority")
    if guard_gate["plugin_samples_with_exhausted_guard"]:
        reasons.append(
            f"guard exhausted in {guard_gate['plugin_samples_with_exhausted_guard']} units "
            "(counted as failures, never as saving)"
        )
    if errors:
        reasons.append(f"audit errors present ({len(errors)})")
        reasons.append("本批数字不得当作有效节省")
    return {
        "complete": len(rows) == len(expected_keys) and set(keys) == expected_keys,
        "freeze_checked": freeze_path is not None,
        "rows": len(rows),
        "expected_rows": len(expected_keys),
        "units_per_arm": UNITS_PER_ARM,
        "request_attempts": total_requests,
        "quality": counters,
        "per_task_counts": per_task_counts,
        "per_task_non_inferior": per_task_non_inferior,
        "paired": paired,
        "batch_paired_mean_percent": batch_mean,
        "inter_task_dispersion_percentage_points": (
            round(max(e["mean_percent"] for e in paired.values())
                  - min(e["mean_percent"] for e in paired.values()), 4)
            if len(paired) > 1 else None
        ),
        "guard_gate": guard_gate,
        "behaviour_equivalence_met": behaviour_ok,
        "quality_non_inferior_met": quality_ok,
        "cost_positive_met": cost_ok,
        "verdict": "first_valid_saving_candidate" if all_three
        else "not_passed_quality_gate",
        "verdict_reason": reasons,
        "homogeneity_boundary": (
            "the four tasks share the constructed 14-pair filler history; cross-task "
            "uniformity is an artefact of one design round and row-per-task means plus "
            "dispersion must be reported with this caveat"
        ),
        "errors": errors,
        "errors_meaning": (
            "non-empty errors means the batch cannot be cited as an effective saving; "
            "本批数字不得当作有效节省"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", type=Path, default=None)
    args = parser.parse_args()
    result = audit(args.directory, args.freeze)
    (args.directory / "audit.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "complete": result["complete"],
        "rows": result["rows"],
        "units_per_arm": result["units_per_arm"],
        "verdict": result["verdict"],
        "guard_gate": result["guard_gate"],
        "behaviour": result["behaviour_equivalence_met"],
        "quality": result["quality_non_inferior_met"],
        "cost_positive": result["cost_positive_met"],
        "errors": len(result["errors"]),
    }, ensure_ascii=False, indent=2))
    return 0 if result["complete"] and not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
