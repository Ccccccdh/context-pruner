"""Read-only CrewAI candidate gate from recorded, paid host payloads.

This does not replay a model, rescore old answers, or change an old batch.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[2]
BATCHES = {
    "r9": "crewai-two-role-r9-multitask-01",
    "r10": "crewai-two-role-r10-fresh-multitask-01",
    "r13": "crewai-two-role-r13-toolrounds-and-contract-01",
}


def rows(batch: str) -> list[dict]:
    path = ROOT / "runs" / "stage5-crewai" / BATCHES[batch] / "results.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def audit_state(batch: str) -> dict:
    path = ROOT / "runs" / "stage5-crewai" / BATCHES[batch] / "audit.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    return {
        "complete": audit.get("complete"),
        "freeze_checked": audit.get("freeze_checked"),
        "errors": len(audit.get("errors") or []),
    }


def summarize(batch: str) -> dict:
    batch_dir = ROOT / "runs" / "stage5-crewai" / BATCHES[batch]
    data = rows(batch)
    by_key = {(r["task_id"], int(r["repeat"]), r["method"]): r for r in data}
    if len(by_key) != len(data):
        raise ValueError(f"duplicate task/repeat/arm in {batch}")
    tasks = sorted({r["task_id"] for r in data})
    out = {
        "source_sha256": {
            name: hashlib.sha256((batch_dir / name).read_bytes()).hexdigest()
            for name in ("results.jsonl", "audit.json", "manifest.json")
        },
        "audit": audit_state(batch), "row_count": len(data), "tasks": {},
    }
    for task in tasks:
        repeats = sorted({int(r["repeat"]) for r in data if r["task_id"] == task})
        pairs = []
        for repeat in repeats:
            none = by_key[(task, repeat, "none")]
            plugin = by_key[(task, repeat, "pruner_v1")]
            n_total = int(none["all_arm_total_tokens"])
            p_total = int(plugin["all_arm_total_tokens"])
            n_calls = len(none.get("agent_attempt_records") or [])
            p_calls = len(plugin.get("agent_attempt_records") or [])
            n_trace = list((none.get("role_tool_traces") or [[]])[0])
            p_trace = list((plugin.get("role_tool_traces") or [[]])[0])
            same_work = n_calls == p_calls and n_trace == p_trace
            pairs.append({
                "repeat": repeat,
                "saving_percent": round(100 * (n_total - p_total) / n_total, 4),
                "same_work": same_work,
                "none_calls": n_calls,
                "plugin_calls": p_calls,
                "none_trace": n_trace,
                "plugin_trace": p_trace,
                "none_strict": bool(none.get("strict_success")),
                "plugin_strict": bool(plugin.get("strict_success")),
                "none_semantic": bool(none.get("semantic_success")),
                "plugin_semantic": bool(plugin.get("semantic_success")),
                "none_first_pass": bool(none.get("first_pass_complete")),
                "plugin_first_pass": bool(plugin.get("first_pass_complete")),
                "plugin_compression_events": int(plugin.get("compression_events") or 0),
            })
        same = [p for p in pairs if p["same_work"]]
        out["tasks"][task] = {
            "pairs": pairs,
            "all_mean_percent": round(mean(p["saving_percent"] for p in pairs), 4),
            "same_work_count": len(same),
            "same_work_mean_percent": round(mean(p["saving_percent"] for p in same), 4)
            if same else None,
            "strict_none": sum(p["none_strict"] for p in pairs),
            "strict_plugin": sum(p["plugin_strict"] for p in pairs),
            "semantic_none": sum(p["none_semantic"] for p in pairs),
            "semantic_plugin": sum(p["plugin_semantic"] for p in pairs),
            "first_pass_none": sum(p["none_first_pass"] for p in pairs),
            "first_pass_plugin": sum(p["plugin_first_pass"] for p in pairs),
        }
    return out


def main() -> None:
    result = {
        "method": "Existing results only; same-work means equal agent call counts and first-role tool traces",
        "batches": {batch: summarize(batch) for batch in BATCHES},
        "new_task_real_payload_available": False,
        "paid_candidate_gate": "fail_missing_new_task_real_payload",
    }
    target = ROOT / "integrations" / "crewai" / "R15_CANDIDATE_ZERO_API_GATE_20261005.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
