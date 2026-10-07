"""Independent r27 audit; first-role coverage comes from provider captures."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from experiments.runners import crewai_semantic_equivalence_v8 as judge

ROOT = Path(__file__).resolve().parents[2]
BATCH = "crewai-r28-variable-source-confirm-01"
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R28_VARIABLE_CONFIRM_01.json"
DEFAULT = ROOT / "runs/stage5-crewai" / BATCH
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json"
ARMS = ("none", "pruner_v1", "native_summary")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def trace_from_capture(row: dict, role: str) -> list[str]:
    trace = []
    for call in row.get("full_attempt_capture") or []:
        messages = call.get("full_input_messages") or []
        system = str(messages[0].get("content", "")) if messages else ""
        if f"You are {role}." in system and call.get("host_executed_tool") is not None:
            trace.append(str(call["host_executed_tool"]))
    return trace


def row_errors(row: dict, task: dict, *, mode: str) -> list[str]:
    key = f"{row.get('task_id')}/{row.get('repeat')}/{row.get('method')}"
    errors = []
    agent = row.get("full_attempt_capture") or []
    summary = row.get("native_summary_capture") or []
    requests = int(row.get("api_request_attempts", -1))
    if row.get("guard_enabled") is not True or row.get("guard_installed") is not True:
        errors.append(f"{key}: all-arm guard absent")
    if row.get("guard_scope") != "all_arms_order_free_coverage":
        errors.append(f"{key}: guard scope differs")
    if mode == "mock":
        if requests != 0 or agent or summary:
            errors.append(f"{key}: mock has provider calls")
        trace = row.get("first_role_trace") or []
    else:
        if (len(agent), len(summary)) != (int(row.get("agent_request_attempts", -1)),
                                          int(row.get("auxiliary_request_attempts", -1))):
            errors.append(f"{key}: per-call capture incomplete")
        if len(agent) + len(summary) != requests:
            errors.append(f"{key}: request components differ")
        if sum(int(c.get("input_tokens", 0)) + int(c.get("output_tokens", 0))
               for c in [*agent, *summary]) != int(row.get("all_arm_total_tokens", -1)):
            errors.append(f"{key}: captured token total differs")
        if any(c.get("status") != "success" for c in [*agent, *summary]):
            errors.append(f"{key}: failed provider request")
        captured_slots = [int(c.get("request_slot", -1)) for c in [*agent, *summary]]
        if len(captured_slots) != len(set(captured_slots)):
            errors.append(f"{key}: duplicate captured slot")
        if any(c.get("host_executed_tool") is not None and
               (c["host_executed_tool"] != c.get("host_parsed_action") or
                c["host_executed_tool"] != c.get("response_action_candidate")) for c in agent):
            errors.append(f"{key}: host action not bound to response")
        trace = trace_from_capture(row, "Evidence investigator")
        if trace != (row.get("first_role_trace") or []):
            errors.append(f"{key}: first-role trace differs from capture")
        if trace_from_capture(row, "Operations decision maker") != [task["tools"][-1]["name"]]:
            errors.append(f"{key}: decision-role trace differs")
        if not row.get("capture_complete"):
            errors.append(f"{key}: capture marked incomplete")
    required = {tool["name"] for tool in task["tools"]}
    if not required.issubset(set(trace)) or not set(trace) <= required:
        errors.append(f"{key}: required source missing or unknown")
    if row.get("guard_exhausted"):
        errors.append(f"{key}: guard exhausted")
    if row.get("method") == "pruner_v1":
        first_metrics = (row.get("role_metrics") or [{}])[0]
        if int(first_metrics.get("k_recent_tool_rounds", -1)) != len(task["tools"]):
            errors.append(f"{key}: task-scaled K differs from source count")
        if int(first_metrics.get("crewai_group_restore_failure_count", -1)) != 0:
            errors.append(f"{key}: protected tool-group restoration failed")
    return errors


def outcomes(rows: list[dict], tasks: list[dict]) -> dict:
    by = {(r["task_id"], int(r["repeat"]), r["method"]): r for r in rows}
    per_task, all_deltas = {}, []
    quality_ok = cost_ok = True
    for task in tasks:
        task_id = task["task_id"]
        strict = {arm: 0 for arm in ARMS}
        semantic = {arm: 0 for arm in ARMS}
        coverage = {arm: 0 for arm in ARMS}
        required = {tool["name"] for tool in task["tools"]}
        deltas = []
        for repeat in range(3):
            for arm in ARMS:
                row = by[(task_id, repeat, arm)]
                coverage[arm] += required.issubset(set(row.get("first_role_trace") or []))
                final = (row.get("role_raw_outputs") or ["", ""])[-1]
                verdict = judge.judge_answer(judge.answer_rule(task), final)
                first = (row.get("role_raw_outputs") or [""])[0]
                handoff = judge.judge_handoff(judge.handle_rule(task), first)
                safe = bool(not row.get("error") and not row.get("guard_exhausted")
                            and row.get("role_safe") and row.get("handoff_for_decider")
                            and len(row.get("role_outputs") or []) == 2)
                strict[arm] += bool(safe and handoff.strict_pass and verdict.strict_pass)
                semantic[arm] += bool(safe and handoff.semantic_pass and verdict.semantic_pass)
            baseline = int(by[(task_id, repeat, "none")]["all_arm_total_tokens"])
            plugin = int(by[(task_id, repeat, "pruner_v1")]["all_arm_total_tokens"])
            deltas.append(baseline - plugin)
        task_quality = (all(count == 3 for count in coverage.values())
                        and strict["none"] >= 2 and strict["pruner_v1"] >= strict["none"]
                        and semantic["pruner_v1"] >= semantic["none"])
        task_cost = sum(x > 0 for x in deltas) >= 2 and sum(deltas) > 0
        quality_ok &= task_quality
        cost_ok &= task_cost
        all_deltas.extend(deltas)
        per_task[task_id] = {"coverage": coverage, "strict": strict, "semantic": semantic,
                             "paired_delta_tokens": deltas,
                             "quality_pass": task_quality, "cost_pass": task_cost}
    cost_ok &= sum(x > 0 for x in all_deltas) >= 5 and sum(all_deltas) > 0
    return {"per_task": per_task, "all_paired_delta_tokens": all_deltas,
            "positive_pairs": sum(x > 0 for x in all_deltas),
            "quality_pass": quality_ok, "cost_pass": cost_ok}


def audit(directory: Path = DEFAULT, freeze_path: Path = FREEZE) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines() if x]
    slots_path = directory / "request-slots.jsonl"
    slots = ([json.loads(x) for x in slots_path.read_text(encoding="utf-8").splitlines() if x]
             if slots_path.exists() else [])
    tasks = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    by_task = {task["task_id"]: task for task in tasks}
    errors = []
    if manifest.get("freeze_sha256") != sha(freeze_path):
        errors.append("manifest freeze hash differs")
    if manifest.get("batch") != freeze["batch"] or manifest.get("guard_installed_arms") != list(ARMS):
        errors.append("batch or all-arm guard manifest differs")
    for name, expected in freeze["source_sha256"].items():
        if sha(ROOT / name) != expected:
            errors.append(f"frozen source drift: {name}")
    keys = [(r.get("task_id"), r.get("repeat"), r.get("method")) for r in rows]
    expected = {(task_id, repeat, arm) for task_id in freeze["tasks"]
                for repeat in range(3) for arm in ARMS}
    if len(rows) != 18 or len(set(keys)) != 18 or set(keys) != expected:
        errors.append("18-cell balanced grid incomplete")
    for row in rows:
        if row.get("task_id") in by_task:
            errors.extend(row_errors(row, by_task[row["task_id"]], mode=manifest.get("mode", "")))
    if [s.get("slot") for s in slots] != list(range(1, len(slots) + 1)):
        errors.append("durable slots noncontiguous")
    if len(slots) != sum(int(r.get("api_request_attempts", 0)) for r in rows):
        errors.append("durable slots differ from rows")
    if len(slots) > int(freeze["max_api_requests"]):
        errors.append("request cap exceeded")
    result = outcomes(rows, tasks) if not errors else None
    return {"batch": freeze["batch"], "complete": not errors, "errors": errors,
            "rows": len(rows), "durable_request_slots": len(slots), "outcomes": result,
            "acceptance_met": bool(not errors and manifest.get("mode") == "api"
                                   and result["quality_pass"] and result["cost_pass"]),
            "can_be_quoted_as_saving": False,
            "reason": "variable-source synthetic CrewAI task family; natural-task and cross-host evidence remain"}


if __name__ == "__main__":
    result = audit()
    (DEFAULT / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"complete": result["complete"], "errors": result["errors"],
                      "acceptance_met": result["acceptance_met"]}, ensure_ascii=False))
    raise SystemExit(0 if result["complete"] else 1)
