"""Independent r6 audit: freeze hashes, every sample, ledgers, both quality gates.

This module deliberately imports **only** the judge (for the frozen rules) and
never the r5 runner, so a bug or a tuned threshold in the runner cannot make the
audit pass.  It re-derives, from the raw ``results.jsonl``:

  * both quality gates per sample (strict and semantic) from the recorded final
    answer, and compares them with the recorded booleans;
  * the handoff canonicalisation and the recovery decision;
  * the tool evidence of both roles against the frozen task file;
  * the complete-total-token sum, including every auxiliary (summary) request;
  * the per-sample request accounting against the global ledger total and cap.

Run:

    python -m experiments.audits.audit_crewai_handoff_v5 <batch> --freeze <freeze.json>
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from experiments.runners import crewai_semantic_equivalence_v6 as judge


ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "native_summary", "pruner_v1")
SAFETY_FIELDS = (
    "crewai_group_restore_failure_count",
    "crewai_unmatched_call_count",
    "crewai_active_pending_view_count",
)
MANIFEST_FIELDS = {
    "protocol", "runner_sha256", "task_sha256", "judge_sha256", "tasks", "methods",
    "repeats", "model", "mode", "base_url", "budget", "limits", "max_api_requests",
    "quality_gates", "failure_policy",
}
FROZEN_PATHS = {
    "experiments/runners/run_crewai_handoff_v6.py",
    "experiments/runners/crewai_semantic_equivalence_v6.py",
    "experiments/runners/run_crewai_experiment.py",
    "experiments/runners/run_crewai_handoff_v4.py",
    "tasks/stage5_autogen/natural_tasks_r6.json",
    "integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R6_MULTITASK_01.md",
    "experiments/audits/audit_crewai_handoff_v6.py",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(directory: Path, freeze_path: Path | None = None,
          dry_run_mock: bool = False) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tasks = {task["task_id"]: task for task in judge.load_tasks(judge.TASK_FILE)}
    errors: list[str] = []
    if freeze_path is not None:
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        if not dry_run_mock and freeze.get("batch") != directory.name:
            errors.append("freeze batch mismatch")
        sources = freeze.get("source_sha256", {})
        if set(sources) != FROZEN_PATHS:
            errors.append("freeze source path set mismatch")
        for name in sorted(FROZEN_PATHS):
            path = ROOT / name
            if not path.is_file() or _sha256(path) != sources.get(name):
                errors.append(f"frozen source mismatch: {name}")
        expected = freeze.get("manifest", {})
        if set(expected) != MANIFEST_FIELDS or set(manifest) != MANIFEST_FIELDS:
            errors.append("manifest field set mismatch")
        for field in sorted(MANIFEST_FIELDS):
            if dry_run_mock and field == "mode":
                if expected.get("mode") != "api" or manifest.get("mode") != "mock":
                    errors.append("mock mode mismatch")
            elif expected.get(field) != manifest.get(field):
                errors.append(f"manifest mismatch: {field}")
        if freeze.get("samples") != (
            len(manifest["tasks"]) * int(manifest["repeats"]) * len(manifest["methods"])
        ):
            errors.append("freeze sample count mismatch")
    if manifest.get("protocol") != "crewai-two-role-r6-handoff-semantic-quality":
        errors.append("protocol mismatch")
    if _sha256(judge.TASK_FILE) != manifest.get("task_sha256"):
        errors.append("task hash mismatch")
    if _sha256(Path(judge.__file__)) != manifest.get("judge_sha256"):
        errors.append("judge hash mismatch")
    if manifest.get("quality_gates") != ["strict", "semantic"]:
        errors.append("quality gate declaration mismatch")

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

    counters = {
        method: {
            "n": 0, "strict_success": 0, "semantic_success": 0,
            "first_pass_facts": 0, "recovered": 0, "recovery_failed": 0,
            "complete_total_tokens": 0, "compression_events": 0,
            "failed_or_limited": 0, "summary_attempts": 0,
        }
        for method in manifest["methods"]
    }
    total_requests = 0
    for row in rows:
        task_id = str(row["task_id"])
        repeat = int(row["repeat"])
        method = str(row["method"])
        key = f"{task_id}/{repeat}/{method}"
        if task_id not in tasks:
            errors.append(f"{key}: unknown task")
            continue
        task = tasks[task_id]
        outputs = list(row["role_outputs"])
        if len(outputs) != 2:
            # A sample that never produced both role outputs is still counted, so
            # the arm totals and the global ledger cannot silently shrink.
            errors.append(f"{key}: wrong number of role outputs")
            counter = counters.get(method)
            if counter is not None:
                counter["n"] += 1
                counter["failed_or_limited"] += 1
                counter["complete_total_tokens"] += int(row.get("all_arm_total_tokens", 0))
                counter["compression_events"] += int(row.get("compression_events", 0))
                counter["summary_attempts"] += int(row.get("summary_attempts", 0))
                counter["recovered"] += int(row.get("recovery_invocations", 0))
            total_requests += int(row.get("api_request_attempts", 0))
            continue
        first = row.get("handoff_first_pass") or {}
        raw_outputs = list(row.get("role_raw_outputs") or [])
        recorded_raw = raw_outputs[0] if raw_outputs else outputs[0]
        if str(outputs[0]).strip() != str(recorded_raw).strip():
            errors.append(f"{key}: first-pass raw output mismatch")
        handle_rule = judge.handle_rule_for([task], task_id)
        rejudged_handoff = judge.judge_handoff(handle_rule, recorded_raw)
        if first.get("semantic", {}).get("canonical") != rejudged_handoff.semantic["canonical"]:
            errors.append(f"{key}: handoff canonical mismatch")
        if first.get("semantic", {}).get("missing_facts") != rejudged_handoff.semantic["missing_facts"]:
            errors.append(f"{key}: handoff fact mismatch")
        recoveries = int(row["recovery_invocations"])
        if recoveries not in (0, 1):
            errors.append(f"{key}: recovery cap exceeded")
        if bool(recoveries) != bool(rejudged_handoff.semantic["needs_recovery"]):
            errors.append(f"{key}: recovery trigger mismatch")
        if recoveries:
            recovery = row.get("handoff_recovery") or {}
            recovery_rule = judge.judge_handoff(
                handle_rule, str(row.get("handoff_recovery_raw") or "")
            )
            expected_handoff = recovery_rule.semantic["canonical"] or ""
            if not recovery_rule.semantic_pass:
                counters[method]["recovery_failed"] += 1
            if bool(recovery.get("semantic_pass")) != recovery_rule.semantic_pass:
                errors.append(f"{key}: recovery verdict mismatch")
            if recovery.get("semantic", {}).get("canonical") != expected_handoff:
                errors.append(f"{key}: recovery canonical mismatch")
            try:
                observed = json.loads(row.get("recovery_observation", ""))
            except (TypeError, ValueError):
                observed = None
            if not isinstance(observed, dict):
                errors.append(f"{key}: recovery lacks the recorded JSON observation")
        else:
            if (row.get("handoff_recovery") is not None
                    or row.get("handoff_recovery_raw")
                    or row.get("recovery_observation")):
                errors.append(f"{key}: unmetered recovery data")
            expected_handoff = rejudged_handoff.semantic["canonical"] or ""
        if row.get("handoff_for_decider") != expected_handoff:
            errors.append(f"{key}: decider handoff mismatch")

        expected_traces = (
            [str(tool["name"]) for tool in task["tools"]],
            [str(task["tools"][-1]["name"])],
        )
        traces = [list(trace) for trace in row["role_tool_traces"]]
        tool_ok = traces == [list(expected_traces[0]), list(expected_traces[1])]
        if not tool_ok:
            errors.append(f"{key}: tool evidence mismatch")
        if bool(row["tool_evidence_consistent"]) != tool_ok:
            errors.append(f"{key}: tool evidence flag mismatch")

        metrics = [*row["role_metrics"], row.get("recovery_metrics") or {}]
        role_safe = all(
            int(metric.get(field, 0)) == 0 for metric in metrics for field in SAFETY_FIELDS
        )
        if bool(row["role_safe"]) != role_safe:
            errors.append(f"{key}: role safety mismatch")

        answer_rule = judge.answer_rule_for([task], task_id)
        verdict = judge.judge_answer(answer_rule, outputs[1])
        recorded = row.get("answer_verdict") or {}
        if bool(recorded.get("strict_pass")) != verdict.strict_pass:
            errors.append(f"{key}: strict verdict mismatch")
        if bool(recorded.get("semantic_pass")) != verdict.semantic_pass:
            errors.append(f"{key}: semantic verdict mismatch")
        strict_valid = bool(not row["error"] and tool_ok and role_safe
                            and expected_handoff and verdict.strict_pass)
        semantic_valid = bool(not row["error"] and tool_ok and role_safe
                              and expected_handoff and verdict.semantic_pass)
        if bool(row.get("strict_success")) != strict_valid:
            errors.append(f"{key}: strict success mismatch")
        if bool(row.get("semantic_success")) != semantic_valid:
            errors.append(f"{key}: semantic success mismatch")
        if bool(row.get("success")) != strict_valid:
            errors.append(f"{key}: legacy success flag mismatch")

        tokens = sum(int(row[field]) for field in (
            "agent_input_tokens", "agent_output_tokens",
            "summary_input_tokens", "summary_output_tokens",
        ))
        if int(row["all_arm_total_tokens"]) != tokens:
            errors.append(f"{key}: token sum mismatch")
        if int(row["summary_attempts"]) != (
            int(row["summary_recorded_calls"]) + int(row["summary_failures"])
        ):
            errors.append(f"{key}: summary accounting mismatch")
        if method != "native_summary" and int(row["summary_attempts"]):
            errors.append(f"{key}: summary in another method")
        requests = int(row["api_request_attempts"])
        if manifest["mode"] == "api" and requests != (
            len(row["agent_attempt_records"]) + int(row["summary_attempts"])
        ):
            errors.append(f"{key}: request accounting mismatch")
        if manifest["mode"] == "mock" and requests:
            errors.append(f"{key}: mock made a modelled request")
        if int(row["recovery_api_attempts"]) > requests:
            errors.append(f"{key}: recovery request accounting mismatch")
        total_requests += requests

        counter = counters[method]
        counter["n"] += 1
        counter["strict_success"] += int(strict_valid)
        counter["semantic_success"] += int(semantic_valid)
        counter["first_pass_facts"] += int(not rejudged_handoff.semantic["missing_facts"])
        counter["recovered"] += recoveries
        counter["complete_total_tokens"] += int(row["all_arm_total_tokens"])
        counter["compression_events"] += int(row["compression_events"])
        counter["summary_attempts"] += int(row["summary_attempts"])
        counter["failed_or_limited"] += int(not (strict_valid and semantic_valid))

    if total_requests > int(manifest["max_api_requests"]):
        errors.append("global request cap exceeded")
    if manifest["mode"] == "api" and total_requests < 2 * len(expected_keys):
        errors.append("fewer requests than the minimum two per sample")

    paired: dict[str, dict[str, float | int | None]] = {}
    for task_id in manifest["tasks"]:
        pairs = []
        for repeat in range(int(manifest["repeats"])):
            values = {
                str(row["method"]): int(row["all_arm_total_tokens"])
                for row in rows
                if row["task_id"] == task_id and int(row["repeat"]) == repeat
            }
            if set(values) != set(METHODS):
                continue
            pairs.append(
                round((values["none"] - values["pruner_v1"]) / values["none"] * 100, 4)
            )
        paired[task_id] = {
            "pruner_paired_mean_saving_percent": round(sum(pairs) / len(pairs), 4)
            if pairs else None,
            "pruner_paired_positive": sum(1 for value in pairs if value > 0),
            "paired_n": len(pairs),
            "per_repeat": pairs,
        }
    means = [
        entry["pruner_paired_mean_saving_percent"]
        for entry in paired.values()
        if entry["pruner_paired_mean_saving_percent"] is not None
    ]
    complete = len(rows) == len(expected_keys) and set(keys) == expected_keys
    return {
        "complete": complete,
        "freeze_checked": freeze_path is not None,
        "dry_run_mock": dry_run_mock,
        "rows": len(rows),
        "expected_rows": len(expected_keys),
        "request_attempts": total_requests,
        "quality": counters,
        "complete_total_tokens_by_method": {
            method: counters[method]["complete_total_tokens"] for method in METHODS
        },
        "paired": paired,
        "plugin_paired_mean_saving_percent": (
            round(sum(means) / len(means), 4) if means else None
        ),
        "plugin_inter_task_dispersion_percentage_points": (
            round(max(means) - min(means), 4) if len(means) > 1 else None
        ),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--dry-run-mock", action="store_true")
    args = parser.parse_args()
    result = audit(args.directory, args.freeze, dry_run_mock=args.dry_run_mock)
    (args.directory / "audit.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2))
    return 0 if result["complete"] and not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
