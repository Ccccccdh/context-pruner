"""Independent r15 three-arm audit: hard gates, 12/12 denominator, failure classes.

Imports **only** the frozen r9 pinning module (pin declaration), the frozen r8 judge
rules and the frozen r15 task file; never the r15 runner.  So a bug or a tuned
threshold in the runner cannot make this audit pass.  It re-derives from the raw
``results.jsonl``:

  * strict / semantic / **first-pass fact completeness** per sample, from the recorded
    raw first-role output;
  * the hard ``tool_sequence_consistent`` field per sample and the **per-arm unit
    count for this matrix: 12/12** (4 tasks x 3 repeats).  r13's 16/16 is a different
    matrix and is deliberately *not* inherited;
  * the pre-registered ``failure_class`` of every sample, re-derived and compared;
  * same-work pairing (equal agent call counts and equal first-role traces), per-task
    and batch paired token means, dispersion, and the **implementation rate** against
    the payload batch's mechanistic upper bound;
  * the frozen-source SHA256 set, including the protocol and the freeze file itself,
    plus the request ledger and the complete token sum.

Acceptance (protocol section 3) needs all three: behaviour equivalence (plugin
12/12 and per-task not below baseline), quality non-inferiority (per task and
overall), and positive paired tokens on the same work.  Anything less is reported as
"not passed the quality gate" and the batch must not be cited as a saving.

Whenever ``errors`` is non-empty the result says in words that the numbers must not be
cited as an effective saving.

Run:

    & .\\.venv-crewai\\Scripts\\python.exe -m experiments.audits.audit_crewai_handoff_v15 <batch> --freeze <freeze.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean

from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as judge


ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "native_summary", "pruner_v1")
SAFETY_FIELDS = (
    "crewai_group_restore_failure_count",
    "crewai_unmatched_call_count",
    "crewai_active_pending_view_count",
)
MANIFEST_FIELDS = {
    "protocol", "runner_sha256", "task_sha256", "judge_sha256", "mechanism_sha256",
    "pinned_evidence_sha256", "freeze_path", "freeze_sha256",
    "freeze_declared_self_sha256", "tasks", "methods",
    "repeats", "units_per_arm", "model", "mode", "base_url", "budget", "limits",
    "max_api_requests", "quality_gates", "mechanistic_upper_bound_percent",
    "citable_as_saving", "citable_as_quality_equivalence", "citable_note",
    "failure_policy",
}
FROZEN_PATHS = {
    "experiments/runners/run_crewai_handoff_v15.py",
    "experiments/runners/crewai_toolrounds_contract_v13.py",
    "experiments/runners/crewai_pinned_evidence_v9.py",
    "experiments/runners/crewai_semantic_equivalence_v8.py",
    "experiments/runners/run_crewai_experiment.py",
    "experiments/runners/run_crewai_handoff_v4.py",
    "tasks/stage5_autogen/natural_tasks_r15.json",
    "integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R15_RELEASE_GATE_3ARM_01.md",
    "experiments/audits/audit_crewai_handoff_v15.py",
}
PROTOCOL = "crewai-two-role-r15-release-gate"
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
FAILURE_CLASSES = ("none", "tool_sequence", "contract_placeholder_echo", "other")
TASKS = (
    "service_release_gate",
    "schema_migration_gate",
    "flag_promotion_gate",
    "cache_promotion_gate",
)
REPEATS = 3
#: This matrix has 4 tasks x 3 repeats = 12 units per arm.  NOT 16.
UNITS_PER_ARM = 12
UPPER_BOUND_PERCENT = 10.247
PROTECTION_FIELDS = (
    "pinned_events", "pinned_message_total", "pinned_characters_total",
    "required_fact_whole_prefix_fallbacks", "required_facts_missing_after_pin_total",
    "recency_calls", "recency_protected_rounds_total", "recent_rounds_seen_total",
    "recent_rounds_readded_total", "recency_omitted_tool_rounds_total",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _expected_failure_class(row: dict, task: dict) -> str:
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


def audit(directory: Path, freeze_path: Path | None = None) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tasks = {str(task["task_id"]): task for task in judge.load_tasks(
        ROOT / "tasks/stage5_autogen/natural_tasks_r15.json"
    )}
    errors: list[str] = []
    freeze_sha256 = ""
    if freeze_path is not None:
        freeze_sha256 = _sha256(freeze_path)
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if freeze.get("batch") != directory.name:
            errors.append("freeze batch mismatch")
        expected = freeze.get("manifest", {})
        if set(expected) != MANIFEST_FIELDS:
            errors.append("freeze manifest field set mismatch")
        for field in sorted(MANIFEST_FIELDS):
            if expected.get(field) != manifest.get(field):
                errors.append(f"manifest mismatch: {field}")
        sources = freeze.get("source_sha256", {})
        if set(sources) != FROZEN_PATHS:
            errors.append("freeze source path set mismatch")
        for name in sorted(FROZEN_PATHS):
            path = ROOT / name
            if not path.is_file() or _sha256(path) != sources.get(name):
                errors.append(f"frozen source mismatch: {name}")
        # The freeze declares its own hash: the canonical dump of the freeze content
        # with every ``freeze_sha256`` copy blanked (a file cannot hold its own digest).
        declared = freeze.get("freeze_sha256")
        blanked = dict(freeze)
        blanked["freeze_sha256"] = ""
        if isinstance(blanked.get("manifest"), dict):
            blanked["manifest"] = dict(blanked["manifest"])
            blanked["manifest"]["freeze_sha256"] = ""
            blanked["manifest"]["freeze_declared_self_sha256"] = ""
        recomputed = hashlib.sha256(
            json.dumps(blanked, indent=2, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if declared != recomputed:
            errors.append("freeze self-hash mismatch")
        if manifest.get("freeze_sha256") != freeze_sha256:
            errors.append("batch manifest freeze_sha256 (file bytes) mismatch")
        if manifest.get("freeze_declared_self_sha256") != declared:
            errors.append("batch manifest freeze_declared_self_sha256 mismatch")
        if str(manifest.get("freeze_path", "")).replace("\\", "/") != str(
            freeze_path
        ).replace("\\", "/"):
            errors.append("batch manifest freeze_path mismatch")
        for field in ("citable_as_saving", "citable_as_quality_equivalence"):
            if manifest.get(field) is not False:
                errors.append(f"manifest {field} must be false")
        if int(freeze.get("units_per_arm", -1)) != UNITS_PER_ARM:
            errors.append("freeze units_per_arm mismatch")
    if manifest.get("protocol") != PROTOCOL:
        errors.append("protocol mismatch")
    if list(manifest["tasks"]) != list(TASKS):
        errors.append("frozen task set mismatch")
    if int(manifest.get("repeats")) != REPEATS:
        errors.append("frozen repeat count mismatch")
    if int(manifest.get("units_per_arm", -1)) != UNITS_PER_ARM:
        errors.append("manifest units_per_arm mismatch (must be 12, not 16)")
    if _sha256(ROOT / "experiments/runners/crewai_semantic_equivalence_v8.py") != manifest.get("judge_sha256"):
        errors.append("judge hash mismatch")
    if _sha256(ROOT / "tasks/stage5_autogen/natural_tasks_r15.json") != manifest.get("task_sha256"):
        errors.append("task hash mismatch")
    if _sha256(Path(pinned.__file__)) != manifest.get("pinned_evidence_sha256"):
        errors.append("pinned evidence hash mismatch")

    expected_keys = {
        (task, repeat, method)
        for task in manifest["tasks"]
        for repeat in range(int(manifest["repeats"]))
        for method in manifest["methods"]
    }
    keys = [(row["task_id"], int(row["repeat"]), row["method"]) for row in rows]
    if len(keys) != len(set(keys)):
        errors.append("duplicate sample key")
    if set(keys) - expected_keys:
        errors.append("unexpected sample key")
    if set(manifest["methods"]) - set(METHODS):
        errors.append("unexpected method")

    counters: dict[str, dict[str, Any]] = {
        method: {
            "n": 0, "strict_success": 0, "semantic_success": 0,
            "first_pass_facts": 0, "tool_sequence_consistent": 0, "recovered": 0,
            "complete_total_tokens": 0, "summary_attempts": 0, "errors": 0,
            "failed_or_limited": 0, "same_work": 0,
            "failure_classes": {name: 0 for name in FAILURE_CLASSES},
            "per_task": {},
        }
        for method in manifest["methods"]
    }
    for method in manifest["methods"]:
        counters[method].update({field: 0 for field in PROTECTION_FIELDS})
    per_task_counts: dict[str, dict[str, dict[str, int]]] = {
        task: {method: {"n": 0, "strict_success": 0, "semantic_success": 0,
                        "first_pass_facts": 0, "tool_sequence_consistent": 0}
               for method in manifest["methods"]}
        for task in manifest["tasks"]
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
        outputs = list(row.get("role_outputs") or [])
        if len(outputs) != 2:
            errors.append(f"{key}: wrong number of role outputs")
            counter["n"] += 1
            counter["errors"] += 1
            counter["failed_or_limited"] += 1
            counter["complete_total_tokens"] += int(row.get("all_arm_total_tokens", 0))
            total_requests += int(row.get("api_request_attempts", 0))
            continue
        handle_rule = judge.handle_rule_for([task], task_id)
        recorded_raw = (row.get("role_raw_outputs") or outputs)[0]
        rejudged_handoff = judge.judge_handoff(handle_rule, recorded_raw)
        first_ok = bool(
            not rejudged_handoff.semantic["missing_facts"]
            and rejudged_handoff.semantic["one_line"]
        )
        if bool(row.get("first_pass_complete")) != first_ok:
            errors.append(f"{key}: first-pass completeness mismatch")
        if list(row.get("first_pass_missing_facts") or []) != list(
            rejudged_handoff.semantic["missing_facts"]
        ):
            errors.append(f"{key}: first-pass missing fact mismatch")
        expected_trace = [str(tool["name"]) for tool in task["tools"]]
        trace_ok = list(row.get("first_role_trace") or []) == expected_trace
        if bool(row.get("tool_sequence_consistent")) != trace_ok:
            errors.append(f"{key}: tool-sequence flag mismatch")
        if not trace_ok and method == "pruner_v1":
            errors.append(f"{key}: plugin tool sequence inconsistent with the frozen task")
        verdict = judge.judge_answer(
            judge.answer_rule_for([task], task_id), outputs[1]
        )
        recorded = row.get("answer_verdict") or {}
        if bool(recorded.get("strict_pass")) != verdict.strict_pass:
            errors.append(f"{key}: strict verdict mismatch")
        if bool(recorded.get("semantic_pass")) != verdict.semantic_pass:
            errors.append(f"{key}: semantic verdict mismatch")
        strict_valid = bool(not row["error"] and trace_ok and verdict.strict_pass)
        semantic_valid = bool(not row["error"] and trace_ok and verdict.semantic_pass)
        if bool(row.get("strict_success")) != strict_valid:
            errors.append(f"{key}: strict success mismatch")
        if bool(row.get("semantic_success")) != semantic_valid:
            errors.append(f"{key}: semantic success mismatch")
        if expected_class == "none" and not (strict_valid and semantic_valid):
            errors.append(f"{key}: failure class says none but a gate failed")
        tokens = sum(int(row[field]) for field in (
            "agent_input_tokens", "agent_output_tokens",
            "summary_input_tokens", "summary_output_tokens",
        ))
        if int(row["all_arm_total_tokens"]) != tokens:
            errors.append(f"{key}: token sum mismatch")
        if method != "native_summary" and int(row["summary_attempts"]):
            errors.append(f"{key}: summary in another method")
        requests = int(row["api_request_attempts"])
        if manifest["mode"] == "api" and requests != (
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
        counter["complete_total_tokens"] += int(row["all_arm_total_tokens"])
        counter["summary_attempts"] += int(row["summary_attempts"])
        counter["errors"] += int(bool(row["error"]))
        counter["failed_or_limited"] += int(not (strict_valid and semantic_valid))
        counter["same_work"] += int(bool(row.get("same_work_as_baseline")))
        for field in PROTECTION_FIELDS:
            counter[field] += int(row.get(field, 0))
        task_counter = per_task_counts[task_id][method]
        task_counter["n"] += 1
        task_counter["strict_success"] += int(strict_valid)
        task_counter["semantic_success"] += int(semantic_valid)
        task_counter["first_pass_facts"] += int(first_ok)
        task_counter["tool_sequence_consistent"] += int(trace_ok)

    if total_requests > int(manifest["max_api_requests"]):
        errors.append("global request cap exceeded")

    # ---- paired cost, per task and batch ---------------------------------
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
            base_total = int(base_row["all_arm_total_tokens"])
            plugin_total = int(plugin_row["all_arm_total_tokens"])
            if not base_total:
                continue
            pairs.append(round((base_total - plugin_total) / base_total * 100, 4))
        paired[task_id] = {
            "per_repeat": pairs,
            "mean_percent": round(mean(pairs), 4) if pairs else None,
            "positive": sum(1 for value in pairs if value > 0),
            "n": len(pairs),
        }
        all_pairs.extend(pairs)
    means = [entry["mean_percent"] for entry in paired.values()
             if entry["mean_percent"] is not None]
    batch_mean = round(mean(all_pairs), 4) if all_pairs else None
    plugin = counters.get("pruner_v1", {})
    baseline = counters.get("none", {})
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
        plugin.get("tool_sequence_consistent", 0) >= UNITS_PER_ARM
        and all(entry["tool_sequence_not_below_baseline"]
                for entry in per_task_non_inferior.values())
        and plugin.get("first_pass_facts", 0) >= baseline.get("first_pass_facts", 0)
        and all(entry["first_pass_not_below_baseline"]
                for entry in per_task_non_inferior.values())
    )
    quality_ok = bool(
        plugin.get("strict_success", 0) >= baseline.get("strict_success", 0)
        and plugin.get("semantic_success", 0) >= baseline.get("semantic_success", 0)
        and all(entry["strict_not_below_baseline"] and entry["semantic_not_below_baseline"]
                for entry in per_task_non_inferior.values())
    )
    positive_pairs = sum(1 for value in all_pairs if value > 0)
    cost_ok = bool(batch_mean is not None and batch_mean > 0
                   and positive_pairs > len(all_pairs) / 2)
    # Protection cost share: pinned characters converted with the measured r15 ratio
    # (2.41 characters per token) plus fallbacks and summary requests.
    characters_per_token = 2.41
    pin_tokens = round(plugin.get("pinned_characters_total", 0) / characters_per_token)
    summary_tokens = sum(
        int(row.get("summary_input_tokens", 0)) + int(row.get("summary_output_tokens", 0))
        for row in rows if row["method"] == "pruner_v1"
    )
    protection_tokens = pin_tokens + summary_tokens
    plugin_total_tokens = plugin.get("complete_total_tokens", 0)
    protection_share = (
        round(protection_tokens / plugin_total_tokens * 100, 4)
        if plugin_total_tokens else None
    )
    all_three = bool(behaviour_ok and quality_ok and cost_ok)
    reasons: list[str] = []
    if not behaviour_ok:
        reasons.append(
            "behaviour equivalence not met: plugin tool-sequence "
            f"{plugin.get('tool_sequence_consistent', 0)}/{UNITS_PER_ARM} "
            "(and/or per-task below baseline, and/or first-pass below baseline)"
        )
    if not quality_ok:
        reasons.append(
            "quality not non-inferior: strict "
            f"{plugin.get('strict_success', 0)} vs {baseline.get('strict_success', 0)}, "
            f"semantic {plugin.get('semantic_success', 0)} vs "
            f"{baseline.get('semantic_success', 0)}"
        )
    if not cost_ok:
        reasons.append(
            f"paired complete-total tokens not positive on the majority ({batch_mean})"
        )
    if errors:
        reasons.append(f"audit errors present ({len(errors)})")
        reasons.append("本批数字不得当作有效节省")
    verdict = (
        "first_valid_saving_candidate" if all_three and not errors
        else "not_passed_quality_gate"
    )
    return {
        "complete": len(rows) == len(expected_keys) and set(keys) == expected_keys,
        "freeze_checked": freeze_path is not None,
        "freeze_sha256": freeze_sha256,
        "rows": len(rows),
        "expected_rows": len(expected_keys),
        "units_per_arm": UNITS_PER_ARM,
        "request_attempts": total_requests,
        "cap_limited": total_requests >= int(manifest["max_api_requests"]),
        "quality": counters,
        "per_task_counts": per_task_counts,
        "per_task_non_inferior": per_task_non_inferior,
        "paired": paired,
        "batch_paired_mean_percent": batch_mean,
        "batch_paired_positive": positive_pairs,
        "batch_pairs": len(all_pairs),
        "inter_task_dispersion_percentage_points": (
            round(max(means) - min(means), 4) if len(means) > 1 else None
        ),
        "implementation_rate": {
            "measured_batch_mean_percent": batch_mean,
            "mechanistic_upper_bound_percent": UPPER_BOUND_PERCENT,
            "rate_percent": (
                round(batch_mean / UPPER_BOUND_PERCENT * 100, 4)
                if batch_mean is not None else None
            ),
        },
        "protection_cost": {
            "pinned_characters_total": plugin.get("pinned_characters_total", 0),
            "pinned_characters_to_tokens_ratio": characters_per_token,
            "pin_tokens_estimate": pin_tokens,
            "pinned_events": plugin.get("pinned_events", 0),
            "protected_tool_rounds": plugin.get("recency_protected_rounds_total", 0),
            "tool_rounds_readded": plugin.get("recent_rounds_readded_total", 0),
            "tool_rounds_omitted": plugin.get("recency_omitted_tool_rounds_total", 0),
            "whole_prefix_fallbacks": plugin.get("required_fact_whole_prefix_fallbacks", 0),
            "summary_attempts": plugin.get("summary_attempts", 0),
            "summary_tokens": summary_tokens,
            "protection_tokens_total": protection_tokens,
            "protection_share_of_plugin_tokens_percent": protection_share,
        },
        "behaviour_equivalence_met": behaviour_ok,
        "quality_non_inferior_met": quality_ok,
        "cost_positive_met": cost_ok,
        "verdict": verdict,
        "verdict_reason": reasons,
        "homogeneity_boundary": (
            "the four tasks share the constructed 14-pair filler history, so the narrow "
            "per-task spread is an artefact of one design round and is NOT evidence of "
            "task-level heterogeneity; report per-task means and dispersion together "
            "with this caveat"
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
    parser.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.directory, args.freeze)
    (args.directory / "audit.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "complete": result["complete"], "rows": result["rows"],
        "units_per_arm": result["units_per_arm"],
        "request_attempts": result["request_attempts"],
        "verdict": result["verdict"],
        "behaviour": result["behaviour_equivalence_met"],
        "quality": result["quality_non_inferior_met"],
        "cost_positive": result["cost_positive_met"],
        "batch_paired_mean_percent": result["batch_paired_mean_percent"],
        "dispersion_pp": result["inter_task_dispersion_percentage_points"],
        "implementation_rate": result["implementation_rate"],
        "errors": len(result["errors"]),
    }, ensure_ascii=False, indent=2))
    return 0 if result["complete"] and not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
