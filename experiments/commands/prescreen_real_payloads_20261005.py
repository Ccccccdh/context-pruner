"""Read-only retrospective screen over already recorded real host payloads.

The script never reruns a model, scores an old sample, or edits a frozen batch.
It writes only the new aggregate JSON chosen with --output.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[2]
OPENAI = ROOT / "runs/stage5-openai-agents-api"
CREWAI = ROOT / "runs/stage5-crewai"
OPENHANDS = ROOT / "runs/stage5-openhands"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def last_model_record(path: Path) -> dict:
    records = read_rows(path)
    matches = [item for item in records if item.get("stage") == "model_input"]
    if not matches:
        raise ValueError(f"no real model input evidence: {path}")
    return matches[-1]


def keyed_pairs(rows: list[dict], task_field: str) -> dict[tuple[str, int], dict[str, dict]]:
    pairs: dict[tuple[str, int], dict[str, dict]] = {}
    for row in rows:
        key = str(row[task_field]), int(row["repeat"])
        arms = pairs.setdefault(key, {})
        method = str(row["method"])
        if method in arms:
            raise ValueError(f"duplicate sample: {key} {method}")
        arms[method] = row
    if any(set(arms) != {"none", "native_summary", "pruner_v1"} for arms in pairs.values()):
        raise ValueError("incomplete three-arm grid")
    return pairs


def savings(base: dict, plugin: dict) -> float:
    denominator = int(base["all_arm_total_tokens"])
    if denominator <= 0:
        raise ValueError("baseline has no complete total token usage")
    return 100 * (denominator - int(plugin["all_arm_total_tokens"])) / denominator


def openai_cases() -> list[dict]:
    specs = (
        ("v9", "openai-repo-diagnostic-v9-long-baseline-boundary", "turn", 1),
        ("v10", "openai-repo-diagnostic-v10-tool-call-recency-boundary", "tool_call", 1),
        ("v11", "openai-repo-diagnostic-v11-elision-ratio-boundary", "tool_call", 4),
    )
    result = []
    for version, batch_id, unit, keep_recent in specs:
        batch = OPENAI / batch_id
        audit = read_json(batch / "audit.json")
        if audit.get("complete") is not True or audit.get("issues"):
            raise ValueError(f"OpenAI batch lacks clean independent audit: {batch_id}")
        pairs = keyed_pairs(read_rows(batch / "samples.jsonl"), "scenario")
        if len(pairs) != 3:
            raise ValueError(f"unexpected OpenAI pair count: {batch_id}")
        for (task, repeat), arms in sorted(pairs.items()):
            evidence = last_model_record(batch / "input-evidence" / f"{task}-{repeat}-none.jsonl")
            turn_count = int(evidence.get("recency_turn_count") or 0)
            tool_calls = evidence.get("tool_call_recency_tool_calls")
            tool_calls = int(tool_calls) if tool_calls is not None else None
            # This is an opportunity upper bound. Some older calls may carry
            # required facts and remain protected even if they are outside M.
            opportunity = (
                max(0, turn_count - keep_recent)
                if unit == "turn" else max(0, (tool_calls or 0) - keep_recent)
            )
            result.append({
                "version": version, "batch": batch_id, "task": task, "repeat": repeat,
                "evidence_kind": "recorded_baseline_final_model_input",
                "unit": unit, "recent_units_kept": keep_recent,
                "baseline_final_input_bytes": int(evidence["input_bytes"]),
                "baseline_turn_count": turn_count,
                "baseline_tool_calls": tool_calls,
                "potential_old_units_upper_bound": opportunity,
                "plugin_strict_success": bool(arms["pruner_v1"]["success"]),
                "baseline_strict_success": bool(arms["none"]["success"]),
                "paired_complete_total_saving_percent": savings(arms["none"], arms["pruner_v1"]),
                "plugin_compressed_calls": int(arms["pruner_v1"].get("compressed_calls", 0)),
            })
    return result


def first_call_history_chars(row: dict) -> dict[str, int]:
    traces = row.get("model_input_traces") or []
    if not traces:
        raise ValueError("CrewAI baseline has no real model input trace")
    totals = {"assistant": 0, "user": 0, "system": 0}
    for message in traces[0]["messages"]:
        role = str(message.get("role", ""))
        if role in totals:
            totals[role] += int(message.get("content_characters", 0))
    return totals


def crewai_cases() -> list[dict]:
    specs = (
        ("r4", "crewai-two-role-r4-multitask-dev-01"),
        ("r9", "crewai-two-role-r9-multitask-01"),
    )
    result = []
    for version, batch_id in specs:
        batch = CREWAI / batch_id
        audit = read_json(batch / "audit.json")
        if audit.get("complete") is not True or audit.get("errors"):
            raise ValueError(f"CrewAI batch lacks clean independent audit: {batch_id}")
        pairs = keyed_pairs(read_rows(batch / "results.jsonl"), "task_id")
        if len(pairs) != 9:
            raise ValueError(f"unexpected CrewAI pair count: {batch_id}")
        for (task, repeat), arms in sorted(pairs.items()):
            history = first_call_history_chars(arms["none"])
            quality_key = "strict_success" if version == "r9" else "success"
            result.append({
                "version": version, "batch": batch_id, "task": task, "repeat": repeat,
                "evidence_kind": "recorded_baseline_first_model_input",
                "baseline_initial_history_characters_by_role": history,
                "plugin_strict_success": bool(arms["pruner_v1"][quality_key]),
                "baseline_strict_success": bool(arms["none"][quality_key]),
                "paired_complete_total_saving_percent": savings(arms["none"], arms["pruner_v1"]),
                "plugin_compression_events": int(arms["pruner_v1"].get("compression_events", 0)),
                "plugin_first_pass_facts": bool(arms["pruner_v1"].get("first_pass_complete", False))
                if version == "r9" else None,
            })
    return result


def openhands_cases() -> list[dict]:
    result = []
    for version, batch_id in (
        ("v58", "django-multitask-v58-prefix-fork-01"),
        ("v61-01", "django-multitask-v61-01"),
    ):
        batch = OPENHANDS / batch_id
        comparison = read_json(batch / "comparison.json")
        prediction = comparison["prefix"]["trigger_prediction"]
        replay = prediction.get("condenser_replay_audit") or []
        audit_path = batch / "audit.json"
        audit = read_json(audit_path) if audit_path.is_file() else {}
        arms = comparison["branches"]
        result.append({
            "version": version, "batch": batch_id,
            "evidence_kind": "recorded_real_prefix_and_condenser_replay",
            "selected_task": comparison["selected_task"],
            "prefix_agent_requests": int(comparison["prefix"]["agent_requests"]),
            "measured_view_tokens": int(comparison["prefix"]["measured_view_tokens"]),
            "trigger_input_tokens": int(prediction["trigger_input_tokens"]),
            "trigger_margin_tokens": int(comparison["prefix"]["measured_view_tokens"])
            - int(prediction["trigger_input_tokens"]),
            "replay_omitted_units": int(replay[0].get("omitted_units", 0)) if replay else None,
            "replay_protected_tokens": int(replay[0].get("protected_tokens", 0)) if replay else None,
            "plugin_condensation_events": int(arms["pruner_v1"]["condensation_events"]),
            "plugin_post_fork_saving_percent": 100 * float(arms["pruner_v1"]["post_fork_savings_vs_none"]),
            "plugin_whole_run_saving_percent": 100 * float(arms["pruner_v1"]["whole_run_savings_vs_none"]),
            "quality_verdict_obtained": bool(comparison.get("quality_verdict_obtained", False)),
            "branch_host_verdicts_measured": bool(comparison.get("branch_host_verdicts_measured", False)),
            "plugin_host_passed": bool(arms["pruner_v1"].get("final_host_passed", False)),
            "baseline_host_passed": bool(arms["none"].get("final_host_passed", False)),
            "independent_audit_complete": bool(audit.get("complete", False)),
        })
    batch = OPENHANDS / "django-multitask-v61-02"
    selection = read_json(batch / "select.json")
    measured = [
        int(attempt["trigger_prediction"]["measured_view_tokens"])
        for attempt in selection["attempts"]
        if attempt.get("trigger_prediction") and attempt["trigger_prediction"].get("measured_view_tokens")
    ]
    thresholds = {
        int(item["trigger_prediction"]["trigger_input_tokens"])
        for item in selection["attempts"]
        if item.get("trigger_prediction")
        and item["trigger_prediction"].get("trigger_input_tokens") is not None
    }
    if not measured or len(thresholds) != 1 or selection.get("selected") is not False:
        raise ValueError("v61-02 no-trigger selection evidence changed")
    result.append({
        "version": "v61-02", "batch": "django-multitask-v61-02",
        "evidence_kind": "recorded_real_prefix_selection_without_branches",
        "max_measured_view_tokens": max(measured),
        "trigger_input_tokens": thresholds.pop(),
        "prefix_selected": False,
        "quality_verdict_obtained": False,
        "independent_audit_complete": False,
    })
    return result


def summarize(agents: list[dict], crew: list[dict], hands: list[dict]) -> dict:
    agent_versions = {}
    for version in ("v9", "v10", "v11"):
        rows = [row for row in agents if row["version"] == version]
        agent_versions[version] = {
            "pairs": len(rows),
            "potential_old_units_upper_bound": [row["potential_old_units_upper_bound"] for row in rows],
            "plugin_success": sum(row["plugin_strict_success"] for row in rows),
            "baseline_success": sum(row["baseline_strict_success"] for row in rows),
            "mean_paired_saving_percent": mean(row["paired_complete_total_saving_percent"] for row in rows),
        }
    crew_tasks = {}
    for version in ("r4", "r9"):
        for task in sorted({row["task"] for row in crew if row["version"] == version}):
            rows = [row for row in crew if row["version"] == version and row["task"] == task]
            crew_tasks[f"{version}:{task}"] = {
                "pairs": len(rows),
                "initial_assistant_chars_mean": mean(
                    row["baseline_initial_history_characters_by_role"]["assistant"] for row in rows
                ),
                "mean_paired_saving_percent": mean(row["paired_complete_total_saving_percent"] for row in rows),
                "plugin_success": sum(row["plugin_strict_success"] for row in rows),
                "baseline_success": sum(row["baseline_strict_success"] for row in rows),
            }
    return {
        "openai_by_version": agent_versions,
        "crewai_by_task": crew_tasks,
        "openhands": hands,
        "interpretation_limit": (
            "Retrospective necessary-condition checks only. Final baseline model inputs and "
            "post-run quality cannot be used as a validated prospective predictor; "
            "different versions and tasks are not pooled into a treatment effect."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "integrations/REAL_PAYLOAD_PRESCREEN_20261005.json")
    args = parser.parse_args()
    agents, crew, hands = openai_cases(), crewai_cases(), openhands_cases()
    result = {
        "schema": "real_payload_prescreen_20261005",
        "source": "recorded real model-input evidence, frozen sample rows and comparisons; zero API",
        "openai_pairs": agents,
        "crewai_pairs": crew,
        "openhands_cases": hands,
        "summary": summarize(agents, crew, hands),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.output}: {len(agents)} OpenAI, {len(crew)} CrewAI, {len(hands)} OpenHands observations; no API")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
