"""Post-hoc, zero-API diagnosis of the failed r17 confirmation batch.

This intentionally does not import the frozen auditor or write into the old run.
Its output is diagnostic only and cannot repair the old 38 audit errors.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "runs/stage5-crewai/crewai-two-role-r17-confirmation-3arm-01"
TASKS = ROOT / "tasks/stage5_autogen/natural_tasks_r17_confirmation.json"
OUT = Path(__file__).with_name("R18_R17_CONFIRMATION_POSTHOC_DIAGNOSIS_20261006.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise AssertionError(message)


def main() -> None:
    manifest_path = RUN / "manifest.json"
    rows_path = RUN / "results.jsonl"
    old_audit_path = RUN / "R17_CONFIRMATION_INDEPENDENT_AUDIT.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in rows_path.read_text(encoding="utf-8").splitlines() if line]
    old_audit = json.loads(old_audit_path.read_text(encoding="utf-8"))
    task_defs = {task["task_id"]: task for task in json.loads(TASKS.read_text(encoding="utf-8"))}
    require(len(rows) == 36, "expected all 36 rows")
    require(len({(r["task_id"], r["repeat"], r["method"]) for r in rows}) == 36, "duplicate sample")
    require(set(r["task_id"] for r in rows) == set(task_defs), "task table mismatch")
    require(all(r["repeat"] in (0, 1, 2) for r in rows), "repeat mismatch")
    require(all(r["method"] in ("none", "pruner_v1", "native_summary") for r in rows), "arm mismatch")
    require(old_audit["complete"] is False and len(old_audit["errors"]) == 38, "old failure changed")

    arm = defaultdict(lambda: Counter())
    details = []
    index = {}
    for r in rows:
        key = (r["task_id"], r["repeat"], r["method"])
        index[key] = r
        expected = r["expected_first_role_trace"]
        actual = r["first_role_trace"]
        require(expected == [tool["name"] for tool in task_defs[r["task_id"]]["tools"]], f"expected table changed: {key}")
        attempt_records = r["agent_attempt_records"]
        require(len(attempt_records) == r["agent_request_attempts"], f"agent attempts: {key}")
        require(sum(a["input_tokens"] for a in attempt_records) == r["agent_input_tokens"], f"agent input: {key}")
        require(sum(a["output_tokens"] for a in attempt_records) == r["agent_output_tokens"], f"agent output: {key}")
        require(r["api_request_attempts"] == r["agent_request_attempts"] + r["auxiliary_request_attempts"], f"request ledger: {key}")
        require(r["summary_recorded_calls"] == r["summary_attempts"], f"summary calls: {key}")
        require(r["all_arm_total_tokens"] == sum(r[k] for k in ("agent_input_tokens", "agent_output_tokens", "summary_input_tokens", "summary_output_tokens")), f"complete cost: {key}")
        require(r["guard_rejections"] == len(r["guard_rejection_records"]), f"guard ledger: {key}")
        require(r["guard_enabled"] is True, f"guard not installed: {key}")
        prefix = 0
        for want, got in zip(expected, actual):
            if want != got:
                break
            prefix += 1
        bad_order = actual != expected
        rejects = r["guard_rejection_records"]
        bare = sum("HANDOFF" in x["rejected_body"] and "Final Answer:" not in x["rejected_body"] for x in rejects)
        marked = sum("HANDOFF" in x["rejected_body"] and "Final Answer:" in x["rejected_body"] for x in rejects)
        other = len(rejects) - bare - marked
        item = {
            "task_id": r["task_id"], "repeat": r["repeat"], "arm": r["method"],
            "expected": expected, "actual": actual, "ordered_prefix": prefix,
            "exact_tool_sequence": actual == expected,
            "trace_failure_kind": "wrong_action_or_order" if bad_order else "none",
            "strict": r["strict_success"], "semantic": r["semantic_success"],
            "first_pass_complete": r["first_pass_complete"], "failure_class": r["failure_class"],
            "requests": r["api_request_attempts"], "agent_requests": r["agent_request_attempts"],
            "summary_requests": r["summary_attempts"], "complete_tokens": r["all_arm_total_tokens"],
            "guard_rejections": r["guard_rejections"], "bare_handoff_rejections": bare,
            "marked_handoff_rejections": marked, "other_rejections": other,
            "guard_exhausted": r["guard_exhausted"],
        }
        details.append(item)
        a = arm[r["method"]]
        for k, value in (("rows",1),("requests",r["api_request_attempts"]),("agent_requests",r["agent_request_attempts"]),("summary_requests",r["summary_attempts"]),("tokens",r["all_arm_total_tokens"]),("agent_input",r["agent_input_tokens"]),("agent_output",r["agent_output_tokens"]),("summary_input",r["summary_input_tokens"]),("summary_output",r["summary_output_tokens"]),("strict",int(r["strict_success"])),("semantic",int(r["semantic_success"])),("first_pass",int(r["first_pass_complete"])),("exact_tool_sequence",int(actual == expected)),("guard_rejections",r["guard_rejections"]),("guard_triggered_units",int(bool(rejects))),("guard_exhausted",int(r["guard_exhausted"])),("bare_handoff_rejections",bare),("marked_handoff_rejections",marked),("other_rejections",other)):
            a[k] += value

    require(sum(x["requests"] for x in details) == 334, "request total changed")
    require(arm["pruner_v1"]["guard_rejections"] == 20, "rejections changed")
    require(arm["pruner_v1"]["guard_triggered_units"] == 7, "triggered units changed")
    require(arm["pruner_v1"]["exact_tool_sequence"] == 9, "tool sequence changed")
    require(arm["none"]["guard_rejections"] == arm["native_summary"]["guard_rejections"] == 0, "non-plugin rejection")
    require(arm["pruner_v1"]["strict"] == arm["pruner_v1"]["semantic"] == 7, "quality changed")
    require(arm["none"]["strict"] == arm["none"]["semantic"] == 11, "baseline quality changed")

    paired = []
    for task_id in task_defs:
        for repeat in (0, 1, 2):
            base = index[(task_id, repeat, "none")]
            plugin = index[(task_id, repeat, "pruner_v1")]
            b, p = base["all_arm_total_tokens"], plugin["all_arm_total_tokens"]
            paired.append({"task_id":task_id,"repeat":repeat,"base_tokens":b,"plugin_tokens":p,"saving_pct":100*(b-p)/b,"same_work":base["agent_request_attempts"]==plugin["agent_request_attempts"] and base["first_role_trace"]==plugin["first_role_trace"]})
    same = [x for x in paired if x["same_work"]]
    require(len(same) == 5 and all(x["saving_pct"] > 0 for x in same), "same-work result changed")
    per_task = {task: {"mean_saving_pct":mean(x["saving_pct"] for x in paired if x["task_id"]==task),"same_work_pairs":sum(x["same_work"] for x in paired if x["task_id"]==task)} for task in task_defs}
    require(round(mean(x["saving_pct"] for x in paired),3) == -9.735, "batch mean changed")

    # This is a classification replay, not a simulated model continuation. A new guard
    # can reject a bad action but the recorded data cannot show how the model responds.
    replay = []
    for d in details:
        if d["arm"] != "pruner_v1":
            continue
        if not d["exact_tool_sequence"]:
            decision = "reject_wrong_action_or_order"
        elif d["guard_rejections"]:
            decision = "existing_actionless_rejections_only"
        else:
            decision = "pass"
        replay.append({"task_id":d["task_id"],"repeat":d["repeat"],"decision":decision,"observed_extra_requests":d["guard_rejections"]})
    # Worst case under the existing six-rejection cap: every plugin unit consumes six
    # further agent calls at the largest *observed* charged per-call token count.
    plugin_rows = [r for r in rows if r["method"] == "pruner_v1"]
    max_observed_call = max(a["input_tokens"]+a["output_tokens"] for r in plugin_rows for a in r["agent_attempt_records"])
    worst_added = 12 * manifest["limits"]["guard_max_rejections"] * max_observed_call
    observed_base = arm["none"]["tokens"]
    observed_plugin = arm["pruner_v1"]["tokens"]
    out = {
        "status":"posthoc_nonfrozen_diagnosis_only",
        "old_audit":{"complete":old_audit["complete"],"error_count":len(old_audit["errors"]),"unchanged_sha256":sha256(old_audit_path),"cannot_retroactively_validate":True},
        "source_sha256":{"results":sha256(rows_path),"manifest":sha256(manifest_path),"task_table":sha256(TASKS)},
        "rows":details,"arm_totals":{k:dict(v) for k,v in arm.items()},
        "paired":paired,"per_task":per_task,
        "paired_batch_mean_pct":mean(x["saving_pct"] for x in paired),
        "same_work_mean_pct":mean(x["saving_pct"] for x in same),
        "task_mean_spread_pp":max(x["mean_saving_pct"] for x in per_task.values())-min(x["mean_saving_pct"] for x in per_task.values()),
        "candidate_guard_replay":{"definition":"reject an Action unless it is the next frozen tool; reject actionless handoff before all four tools; fail closed on cap", "plugin_classification":replay,"nonplugin_trace_rejections":sum(int(d["actual"]!=d["expected"] or d["guard_rejections"]>0) for d in details if d["arm"]!="pruner_v1"),"model_continuation_observed":False},
        "cost_stress":{"basis":"largest observed plugin agent call, including input and output; 6 additional rejections per plugin unit; this is an observed-envelope stress test, not a provider hard upper bound", "max_observed_call_tokens":max_observed_call,"max_extra_requests":72,"max_extra_tokens":worst_added,"baseline_observed_tokens":observed_base,"plugin_observed_tokens":observed_plugin,"plugin_stress_tokens":observed_plugin+worst_added,"stress_saving_pct":100*(observed_base-observed_plugin-worst_added)/observed_base,"request_cap":manifest["max_api_requests"],"observed_requests":334,"stress_requests":334+72},
        "paid_gate":{"passed":False,"reasons":["old frozen independent audit remains incomplete with 38 errors", "three plugin autoscale traces violate frozen order despite current guard", "full-pair cost is negative and worst-case resend stress exceeds the existing request cap", "no three new-task real-payload safe replay for a revised mechanism"]},
    }
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"arm_totals":out["arm_totals"],"paired_batch_mean_pct":out["paired_batch_mean_pct"],"cost_stress":out["cost_stress"],"paid_gate":out["paid_gate"]},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
