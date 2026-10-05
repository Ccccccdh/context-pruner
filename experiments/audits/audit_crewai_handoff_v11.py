"""Independent r11 audit: hard tool-sequence and first-pass gates, same judges.

This module imports **only** the frozen r9 mechanism module (pin declaration), the
frozen r8 judge rules (through the r11 routing module) and the frozen r10 task
file, and never the r11 runner, so a bug or a tuned threshold in the runner cannot
make the audit pass.  It re-derives, from the raw ``results.jsonl``:

  * both answer gates per sample (strict and semantic) and the frozen
    **first-pass fact completeness** gate from the recorded raw first-role output;
  * the **hard r11 gate**: ``tool_sequence_consistent``, recomputed from the
    recorded first-role tool trace against the frozen task's tool list, plus
    ``tool_sequence_matches_baseline`` recomputed against the uncompressed arm's
    recorded trace for the same (task, repeat);
  * the handoff canonicalisation, the remedy decision and the remedy cap;
  * the pinned-evidence declaration, the whole-prefix fallback accounting, and the
    two v11 directives: they may only appear in the plugin arm;
  * the complete-total-token sum, including every auxiliary (summary) request;
  * the per-sample request accounting against the global ledger total and cap;
  * the frozen task set, so a shrunken grid cannot pass as the planned one.

Acceptance (protocol §3): plugin ``tool_sequence_consistent`` not worse than the
baseline, plugin strict and semantic quality not worse than the baseline, and a
positive paired complete-total-token saving.  If that does not hold, the returned
``verdict`` says ``not_yet_valid`` and ``verdict_reason`` names the failure.

Run:

    & .\\.venv-crewai\\Scripts\\python.exe -m experiments.audits.audit_crewai_handoff_v11 <batch> --freeze <freeze.json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from experiments.runners import crewai_pinned_evidence_v9 as pinned
from experiments.runners import crewai_semantic_equivalence_v8 as frozen_rules
from experiments.runners import crewai_semantic_equivalence_v11 as judge


ROOT = Path(__file__).resolve().parents[2]
METHODS = ("none", "native_summary", "pruner_v1")
SAFETY_FIELDS = (
    "crewai_group_restore_failure_count",
    "crewai_unmatched_call_count",
    "crewai_active_pending_view_count",
)
MANIFEST_FIELDS = {
    "protocol", "runner_sha256", "task_sha256", "judge_sha256",
    "judge_routing_sha256", "mechanism_sha256", "pinned_evidence_sha256",
    "tasks", "methods", "repeats", "model", "mode", "base_url", "budget",
    "limits", "max_api_requests", "quality_gates", "failure_policy",
}
FROZEN_PATHS = {
    "experiments/runners/run_crewai_handoff_v11.py",
    "experiments/runners/crewai_tool_sequence_v11.py",
    "experiments/runners/crewai_pinned_evidence_v9.py",
    "experiments/runners/crewai_semantic_equivalence_v11.py",
    "experiments/runners/crewai_semantic_equivalence_v8.py",
    "experiments/runners/run_crewai_experiment.py",
    "experiments/runners/run_crewai_handoff_v4.py",
    "tasks/stage5_autogen/natural_tasks_r10.json",
    "integrations/crewai/PILOT_PROTOCOL_TWO_ROLE_R11_TOOLSEQ_MULTITASK_01.md",
    "experiments/audits/audit_crewai_handoff_v11.py",
}
PROTOCOL = judge.PROTOCOL
QUALITY_GATES = ["strict", "semantic", "first_pass_facts", "tool_sequence_consistent"]
#: The frozen r10 task set, reused on purpose so the mechanism is the only
#: variable between r10 and r11.
FRESH_TASKS = ("credential_rotation", "shard_split", "batch_replay", "window_gate")
FRESH_REPEATS = 4
DIRECTIVE_FIELDS = (
    "tool_directive_events",
    "tool_directive_messages",
    "contract_directive_events",
    "contract_directive_messages",
)
FIRST_PASS_GATE = {
    "name": "first_pass_facts",
    "definition": (
        "the first-role HANDOFF line emitted before any remedy call carries "
        "every literal of the frozen handle_facts list of its task under the "
        "frozen semantic judge, is single-line and within the 450-character "
        "contract"
    ),
    "reported_with": ["strict", "semantic", "tool_sequence_consistent"],
    "saving_requires": (
        "tool-sequence consistency, strict and semantic quality non-inferior to "
        "the uncompressed arm, first-pass completeness recorded, and complete "
        "total tokens positive"
    ),
}
TOOL_SEQUENCE_GATE = {
    "name": "tool_sequence_consistent",
    "definition": (
        "the first role's recorded tool trace equals the frozen task's tool list "
        "for that role, in the frozen order, once per tool; "
        "tool_sequence_matches_baseline additionally requires equality with the "
        "uncompressed arm's recorded trace for the same task and repeat"
    ),
    "reported_with": ["strict", "semantic", "first_pass_facts"],
    "acceptance": (
        "plugin tool-sequence consistency and strict/semantic quality must not be "
        "worse than the uncompressed arm, and complete total tokens must be positive"
    ),
}
MECHANISM_GUARANTEES = {
    "frozen_tool_order": "experiments/runners/crewai_tool_sequence_v11.py",
    "tool_directive": (
        "the frozen tool list in its frozen order is restated whenever the raw "
        "history shows an un-called frozen tool, so a compressed view cannot end "
        "the first role's tool loop early"
    ),
    "decision_contract_directive": (
        "the frozen RESULT contract is restated verbatim when the served view lost "
        "it, together with an explicit instruction never to output angle brackets"
    ),
    "unchanged": [
        "experiments/runners/crewai_pinned_evidence_v9.py",
        "experiments/runners/crewai_semantic_equivalence_v8.py",
        "tasks/stage5_autogen/natural_tasks_r10.json",
    ],
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
        if freeze.get("first_pass_gate") != FIRST_PASS_GATE:
            errors.append("first-pass gate declaration mismatch")
        if freeze.get("tool_sequence_gate") != TOOL_SEQUENCE_GATE:
            errors.append("tool-sequence gate declaration mismatch")
        if freeze.get("mechanism_guarantees") != MECHANISM_GUARANTEES:
            errors.append("mechanism guarantee declaration mismatch")
    if manifest.get("protocol") != PROTOCOL:
        errors.append("protocol mismatch")
    if _sha256(judge.TASK_FILE) != manifest.get("task_sha256"):
        errors.append("task hash mismatch")
    if _sha256(Path(frozen_rules.__file__)) != manifest.get("judge_sha256"):
        errors.append("judge hash mismatch")
    if _sha256(Path(judge.__file__)) != manifest.get("judge_routing_sha256"):
        errors.append("judge routing hash mismatch")
    if _sha256(Path(pinned.__file__)) != manifest.get("pinned_evidence_sha256"):
        errors.append("pinned evidence hash mismatch")
    if manifest.get("quality_gates") != QUALITY_GATES:
        errors.append("quality gate declaration mismatch")
    if list(manifest["tasks"]) != list(FRESH_TASKS):
        errors.append("frozen task set mismatch")
    if int(manifest["repeats"]) != FRESH_REPEATS:
        errors.append("frozen repeat count mismatch")
    if judge.TASK_FILE.name != "natural_tasks_r10.json":
        errors.append("judge does not route to the frozen r10 task file")
    if set(tasks) != set(FRESH_TASKS):
        errors.append("task file does not match the frozen task set")

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

    baseline_traces = {
        (str(row["task_id"]), int(row["repeat"])): [str(name) for name in (row.get("first_role_trace") or [])]
        for row in rows if row["method"] == "none"
    }
    counters = {
        method: {
            "n": 0, "strict_success": 0, "semantic_success": 0,
            "first_pass_facts": 0, "first_pass_missing_slots": 0,
            "tool_sequence_consistent": 0, "tool_sequence_matches_baseline": 0,
            "decider_tool_sequence_consistent": 0, "recovered": 0,
            "recovery_failed": 0, "complete_total_tokens": 0,
            "compression_events": 0, "failed_or_limited": 0,
            "summary_attempts": 0, "pinned_events": 0, "pinned_messages": 0,
            "pinned_characters": 0, "whole_prefix_fallbacks": 0,
            "tool_directive_events": 0, "tool_directive_messages": 0,
            "contract_directive_events": 0, "contract_directive_messages": 0,
        }
        for method in manifest["methods"]
    }
    total_requests = 0
    pin_rules: dict[str, list[str]] = {}
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
        counter = counters.get(method)
        if len(outputs) != 2:
            errors.append(f"{key}: wrong number of role outputs")
            if counter is not None:
                counter["n"] += 1
                counter["failed_or_limited"] += 1
                counter["complete_total_tokens"] += int(row.get("all_arm_total_tokens", 0))
                counter["compression_events"] += int(row.get("compression_events", 0))
                counter["summary_attempts"] += int(row.get("summary_attempts", 0))
                counter["recovered"] += int(row.get("recovery_invocations", 0))
                counter["whole_prefix_fallbacks"] += int(
                    row.get("required_fact_whole_prefix_fallbacks", 0)
                )
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

        # ---- frozen first-pass fact-completeness gate -------------------
        first_pass_ok = bool(
            not rejudged_handoff.semantic["missing_facts"]
            and rejudged_handoff.semantic["one_line"]
        )
        if bool(row.get("first_pass_complete")) != first_pass_ok:
            errors.append(f"{key}: first-pass completeness mismatch")
        if list(row.get("first_pass_missing_facts") or []) != list(
            rejudged_handoff.semantic["missing_facts"]
        ):
            errors.append(f"{key}: first-pass missing fact mismatch")

        # ---- hard r11 tool-sequence gate --------------------------------
        expected_first_trace = [str(tool["name"]) for tool in task["tools"]]
        recorded_first_trace = [str(name) for name in (row.get("first_role_trace") or [])]
        trace_ok = recorded_first_trace == expected_first_trace
        if list(row.get("expected_first_role_trace") or []) != expected_first_trace:
            errors.append(f"{key}: frozen tool list mismatch")
        if bool(row.get("tool_sequence_consistent")) != trace_ok:
            errors.append(f"{key}: tool-sequence flag mismatch")
        reference = baseline_traces.get((task_id, repeat))
        paired_ok = reference is not None and recorded_first_trace == reference
        if bool(row.get("tool_sequence_matches_baseline")) != paired_ok:
            errors.append(f"{key}: paired baseline flag mismatch")
        if row.get("paired_baseline_tool_trace") != reference:
            errors.append(f"{key}: paired baseline trace mismatch")
        if not trace_ok and method == "pruner_v1":
            errors.append(f"{key}: plugin tool sequence inconsistent with the frozen task")

        # ---- pinned-evidence declaration and v11 directives --------------
        rule_record = ((row.get("role_metrics") or [{}])[0] or {}).get("pin_rule") or {}
        expected_literals = [str(item) for item in task["handle_facts"]]
        if list(rule_record.get("required_literals") or []) != expected_literals:
            errors.append(f"{key}: pin rule mismatch")
        pin_rules[task_id] = list(rule_record.get("required_literals") or [])
        fallbacks = int(row.get("required_fact_whole_prefix_fallbacks", 0))
        reasons = dict(row.get("pin_fallback_reasons") or {})
        if fallbacks != sum(int(value) for value in reasons.values()):
            errors.append(f"{key}: pin fallback accounting mismatch")
        if fallbacks and not reasons:
            errors.append(f"{key}: pin fallback without a reason")
        if method != "pruner_v1":
            if fallbacks or int(row.get("pinned_events", 0)):
                errors.append(f"{key}: pin activity outside the plugin arm")
            if any(int(row.get(field, 0)) for field in DIRECTIVE_FIELDS):
                errors.append(f"{key}: v11 directive outside the plugin arm")

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
            expected_first_trace,
            [str(task["tools"][-1]["name"])],
        )
        traces = [list(trace) for trace in row["role_tool_traces"]]
        tool_ok = traces == [list(expected_traces[0]), list(expected_traces[1])]
        if not tool_ok:
            errors.append(f"{key}: tool evidence mismatch")
        if bool(row["tool_evidence_consistent"]) != tool_ok:
            errors.append(f"{key}: tool evidence flag mismatch")
        decider_ok = traces[1] == list(expected_traces[1])
        if bool(row.get("decider_tool_sequence_consistent")) != decider_ok:
            errors.append(f"{key}: decider tool-sequence flag mismatch")

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

        counter["n"] += 1
        counter["strict_success"] += int(strict_valid)
        counter["semantic_success"] += int(semantic_valid)
        counter["first_pass_facts"] += int(first_pass_ok)
        counter["first_pass_missing_slots"] += len(rejudged_handoff.semantic["missing_facts"])
        counter["tool_sequence_consistent"] += int(trace_ok)
        counter["tool_sequence_matches_baseline"] += int(paired_ok)
        counter["decider_tool_sequence_consistent"] += int(decider_ok)
        counter["recovered"] += recoveries
        counter["complete_total_tokens"] += int(row["all_arm_total_tokens"])
        counter["compression_events"] += int(row["compression_events"])
        counter["summary_attempts"] += int(row["summary_attempts"])
        counter["pinned_events"] += int(row.get("pinned_events", 0))
        counter["pinned_messages"] += int(row.get("pinned_message_total", 0))
        counter["pinned_characters"] += int(row.get("pinned_characters_total", 0))
        counter["whole_prefix_fallbacks"] += fallbacks
        for field in DIRECTIVE_FIELDS:
            counter[field] += int(row.get(field, 0))
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
    plugin = counters.get("pruner_v1", {})
    baseline = counters.get("none", {})
    tool_sequence_non_inferior = bool(
        plugin.get("tool_sequence_consistent", 0) >= baseline.get("tool_sequence_consistent", 0)
    )
    quality_non_inferior = bool(
        plugin.get("strict_success", 0) >= baseline.get("strict_success", 0)
        and plugin.get("semantic_success", 0) >= baseline.get("semantic_success", 0)
    )
    first_pass_non_inferior = bool(
        plugin.get("first_pass_facts", 0) >= baseline.get("first_pass_facts", 0)
    )
    paired_mean = round(sum(means) / len(means), 4) if means else None
    positive_saving = bool(paired_mean is not None and paired_mean > 0)
    reasons: list[str] = []
    if not complete:
        reasons.append("grid incomplete: fewer rows than the frozen 48")
    if not tool_sequence_non_inferior:
        reasons.append(
            "plugin tool-sequence consistency below the uncompressed arm "
            f"({plugin.get('tool_sequence_consistent', 0)} vs "
            f"{baseline.get('tool_sequence_consistent', 0)})"
        )
    if not quality_non_inferior:
        reasons.append(
            f"plugin quality below the uncompressed arm (strict "
            f"{plugin.get('strict_success', 0)} vs {baseline.get('strict_success', 0)}, "
            f"semantic {plugin.get('semantic_success', 0)} vs "
            f"{baseline.get('semantic_success', 0)})"
        )
    if not positive_saving:
        reasons.append(f"paired complete-total saving not positive ({paired_mean})")
    if errors:
        reasons.append(f"audit errors present ({len(errors)})")
    verdict = "valid_equivalent_saving" if not reasons else "not_yet_valid"
    if reasons:
        reasons.append("本批数字不得当作有效节省")
    return {
        "complete": complete,
        "freeze_checked": freeze_path is not None,
        "dry_run_mock": dry_run_mock,
        "rows": len(rows),
        "expected_rows": len(expected_keys),
        "request_attempts": total_requests,
        "quality": counters,
        "pin_rules": pin_rules,
        "tool_sequence_gate": {
            "name": "tool_sequence_consistent",
            "definition": TOOL_SEQUENCE_GATE["definition"],
            "plugin_tool_sequence_consistent": plugin.get("tool_sequence_consistent", 0),
            "baseline_tool_sequence_consistent": baseline.get("tool_sequence_consistent", 0),
            "plugin_matches_baseline": plugin.get("tool_sequence_matches_baseline", 0),
            "baseline_matches_baseline": baseline.get("tool_sequence_matches_baseline", 0),
            "plugin_decider_tool_sequence_consistent": plugin.get(
                "decider_tool_sequence_consistent", 0
            ),
            "tool_directive_events": plugin.get("tool_directive_events", 0),
            "contract_directive_events": plugin.get("contract_directive_events", 0),
        },
        "first_pass_gate": {
            "name": "first_pass_facts",
            "definition": FIRST_PASS_GATE["definition"],
            "plugin_first_pass_facts": plugin.get("first_pass_facts", 0),
            "baseline_first_pass_facts": baseline.get("first_pass_facts", 0),
            "plugin_remedy_calls": plugin.get("recovered", 0),
            "baseline_remedy_calls": baseline.get("recovered", 0),
            "plugin_whole_prefix_fallbacks": plugin.get("whole_prefix_fallbacks", 0),
        },
        "tool_sequence_non_inferior": tool_sequence_non_inferior,
        "quality_non_inferior": quality_non_inferior,
        "first_pass_non_inferior": first_pass_non_inferior,
        "complete_total_tokens_by_method": {
            method: counters[method]["complete_total_tokens"] for method in METHODS
        },
        "paired": paired,
        "plugin_paired_mean_saving_percent": paired_mean,
        "plugin_inter_task_dispersion_percentage_points": (
            round(max(means) - min(means), 4) if len(means) > 1 else None
        ),
        "verdict": verdict,
        "verdict_reason": reasons,
        "errors": errors,
        "errors_meaning": (
            "non-empty errors means the batch cannot be cited as an effective "
            "saving; 本批数字不得当作有效节省"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--dry-run-mock", action="store_true")
    args = parser.parse_args()
    result = audit(args.directory, args.freeze, dry_run_mock=args.dry_run_mock)
    (args.directory / "audit.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["complete"] and not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
