"""Independent, zero-API audit of the CrewAI v2-R2FIX paid batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path


TASKS = {"incident_triage", "release_readiness", "customer_migration"}
ARMS = {"none", "pruner_v1", "native_summary"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(batch: Path, root: Path) -> dict:
    freeze = json.loads((root / "integrations/crewai/PRE_RUN_FREEZE_R2FIX.json").read_text(encoding="utf-8"))
    manifest = json.loads((batch / "run_manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((batch / "summary.json").read_text(encoding="utf-8"))
    usage = json.loads((batch / "request_usage.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (batch / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    checks: list[str] = []

    def require(condition: bool, label: str) -> None:
        if not condition:
            checks.append(label)

    require(manifest["mode"] == "api", "mode")
    require(manifest["repeats"] == 2, "repeats")
    require(set(manifest["tasks"]) == TASKS, "tasks")
    require(set(manifest["methods"]) == ARMS, "arms")
    require(manifest["maximum_api_requests"] == 96, "request_cap")
    require(manifest["max_summary_calls"] == 6, "summary_cap")
    require(batch.name == freeze["batch"], "frozen_batch_name")
    require(
        freeze["protocol_sha256"] == digest(root / "integrations/crewai/PILOT_PROTOCOL_V2_R2FIX.md"),
        "protocol_hash",
    )
    gate_snapshot = batch / "gate_snapshot.json"
    gate_source = gate_snapshot if gate_snapshot.exists() else root / ".tooling/gates/crewai-3arm-gate.json"
    require(freeze["gate_sha256"] == digest(gate_source), "gate_hash")
    runner_snapshot = batch / "runner_snapshot_v2_r2fix.py"
    runner_source = runner_snapshot if runner_snapshot.exists() else root / "experiments/runners/run_crewai_experiment.py"
    require(
        manifest["runner_sha256"]
        == digest(runner_source),
        "runner_hash",
    )
    require(manifest["task_sha256"] == digest(root / manifest["task_file"]), "task_hash")
    require(manifest["runner_sha256"] == freeze["runner_sha256"], "frozen_runner_hash")
    require(manifest["task_sha256"] == freeze["task_sha256"], "frozen_task_hash")
    keys = [(row["task_id"], row["repeat"], row["method"]) for row in rows]
    expected = {(task, repeat, arm) for task in TASKS for repeat in range(2) for arm in ARMS}
    require(len(rows) == 18 and len(set(keys)) == 18 and set(keys) == expected, "sample_grid")
    require(
        sum(int(row["api_request_attempts"]) for row in rows)
        == int(usage["api_requests_used"]),
        "request_accounting",
    )
    require(0 <= usage["api_requests_used"] <= usage["maximum_api_requests"] == 96, "request_limit")
    require(
        usage["remaining"] == 96 - usage["api_requests_used"], "remaining"
    )
    for row in rows:
        tag = f"{row['task_id']}/r{row['repeat']}/{row['method']}"
        require(
            row["all_arm_total_tokens"]
            == row["actual_input_tokens"]
            + row["actual_output_tokens"]
            + row["summary_input_tokens"]
            + row["summary_output_tokens"],
            f"token_identity:{tag}",
        )
        require(row["summary_attempts"] <= 6, f"summary_limit:{tag}")
        require(
            row["api_request_attempts"]
            == len(row["api_attempt_records"]) + row["summary_attempts"],
            f"request_exact_count:{tag}",
        )
        require(row["summary_calls"] <= row["summary_attempts"], f"summary_usage_count:{tag}")
        require(row["summary_failures"] <= row["summary_attempts"], f"summary_failure_count:{tag}")
        require(not row["summary_close_errors"], f"summary_close:{tag}")
        if row["method"] != "native_summary":
            require(row["summary_attempts"] == 0, f"unexpected_summary:{tag}")

    by_key = dict(zip(keys, rows))
    comparisons: dict[str, dict] = {}
    if set(keys) == expected:
        for arm in ("pruner_v1", "native_summary"):
            pairs = []
            for task in sorted(TASKS):
                for repeat in range(2):
                    base = by_key[task, repeat, "none"]
                    test = by_key[task, repeat, arm]
                    pairs.append(
                        {
                            "task": task,
                            "repeat": repeat,
                            "base_success": bool(base["success"]),
                            "arm_success": bool(test["success"]),
                            "input_savings": 1 - test["actual_input_tokens"] / base["actual_input_tokens"],
                            "total_savings": 1 - test["all_arm_total_tokens"] / base["all_arm_total_tokens"],
                            "peak_savings": 1 - test["actual_peak_input_tokens"] / base["actual_peak_input_tokens"],
                        }
                    )
            comparisons[arm] = {
                "n": len(pairs),
                "input_savings_mean": statistics.mean(x["input_savings"] for x in pairs),
                "total_savings_mean": statistics.mean(x["total_savings"] for x in pairs),
                "peak_savings_mean": statistics.mean(x["peak_savings"] for x in pairs),
                "positive_input_pairs": sum(x["input_savings"] > 0 for x in pairs),
                "positive_total_pairs": sum(x["total_savings"] > 0 for x in pairs),
                "pairs": pairs,
            }
            reported = summary["comparisons"][f"{arm}_vs_none"]
            require(
                abs(comparisons[arm]["input_savings_mean"] - reported["input_savings_rate_mean"]) < 1e-9,
                f"summary_input_match:{arm}",
            )
            require(
                abs(comparisons[arm]["total_savings_mean"] - reported["all_arm_total_token_savings_rate_mean"]) < 1e-9,
                f"summary_total_match:{arm}",
            )

    result = {
        "complete": not checks,
        "errors": checks,
        "sample_count": len(rows),
        "request_budget_slots_used": usage["api_requests_used"],
        "request_budget_slots_cap": usage["maximum_api_requests"],
        "success_by_arm": {
            arm: sum(bool(row["success"]) for row in rows if row["method"] == arm)
            for arm in sorted(ARMS)
        },
        "summary_calls": sum(row["summary_calls"] for row in rows),
        "summary_attempts": sum(row["summary_attempts"] for row in rows),
        "summary_failures": sum(row["summary_failures"] for row in rows),
        "summary_failure_details": [
            {"task": row["task_id"], "repeat": row["repeat"], "details": row["summary_failure_details"]}
            for row in rows if row["summary_failure_details"]
        ],
        "agent_attempts": sum(len(row["api_attempt_records"]) for row in rows),
        "auxiliary_budget_reservations": sum(
            row["api_request_attempts"] - len(row["api_attempt_records"])
            for row in rows
        ),
        "auxiliary_reservations_without_usage": sum(
            row["api_request_attempts"]
            - len(row["api_attempt_records"])
            - row["summary_calls"]
            for row in rows
        ),
        "comparisons": comparisons,
    }
    (batch / "audit.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    repository = Path(__file__).resolve().parents[2]
    result = audit(options.out, repository)
    print(json.dumps({k: v for k, v in result.items() if k != "comparisons"}, indent=2))
    raise SystemExit(0 if result["complete"] else 1)
