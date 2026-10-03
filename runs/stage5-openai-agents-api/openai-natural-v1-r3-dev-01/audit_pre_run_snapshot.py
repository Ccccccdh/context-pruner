"""Independent offline audit for fixed-input OpenAI Agents natural-v1 runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FREEZE = ROOT / "integrations/openai_agents/NATURAL_V1_FREEZE.json"
CONTRACTS = {
    "incident_triage": (("read_service_log", "read_deploy_record", "read_incident_runbook"), r"RESULT incident=INC-204 cause=.*PAYMENT_TOKEN.* action=.*roll.?back.*"),
    "invoice_reconcile": (("read_invoice", "read_payment_ledger", "read_billing_policy"), r"RESULT invoice=INV-17 outstanding_usd=120(?:\.00)? action=.*(?:request|collect|follow.?up).*"),
    "release_gate": (("read_ci_matrix", "read_release_policy", "read_fix_status"), r"RESULT release=REL-9 decision=hold reason=.*Windows.*"),
}
METHODS = ("none", "pruner_v1", "native_summary")


def audit(batch: Path, freeze_path: Path = FREEZE) -> dict:
    issues = []
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (batch / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    for path, digest in freeze["source_sha256"].items():
        actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        if actual != digest:
            issues.append(f"source changed: {path}")
    if manifest.get("experiment_id") != batch.name:
        issues.append("manifest experiment ID mismatch")
    if freeze.get("batch") and freeze["batch"] != batch.name:
        issues.append("freeze batch ID mismatch")
    if tuple(manifest.get("scenarios", [])) != tuple(freeze["scenarios"]):
        issues.append("scenario list mismatch")
    if tuple(manifest.get("methods", [])) != METHODS:
        issues.append("method list mismatch")
    if int(manifest.get("repeats", -1)) != freeze["repeats"]:
        issues.append("repeat count mismatch")
    if int(manifest.get("maximum_api_requests", -1)) != freeze["max_api_requests"]:
        issues.append("request cap mismatch")
    if manifest.get("trigger_policy") != "symmetric_budget":
        issues.append("trigger policy mismatch")
    if manifest.get("model") != "deepseek-v4-flash" or manifest.get("base_url") != "https://api.deepseek.com":
        issues.append("model/endpoint mismatch")
    if manifest.get("max_turns") != 6 or manifest.get("max_output_tokens") != 1024:
        issues.append("turn/output cap mismatch")
    if manifest.get("max_summary_calls") != 16 or manifest.get("max_summary_tokens") != 1024:
        issues.append("summary cap mismatch")
    if manifest.get("budget_calibration", {}).get("provider", {}) != {"soft": 1200, "hard": 3000, "target": 900}:
        issues.append("provider budget mismatch")
    keys = [(r.get("scenario"), r.get("repeat"), r.get("method")) for r in rows]
    expected = [(s, repeat, method) for s in freeze["scenarios"] for repeat in range(freeze["repeats"]) for method in METHODS]
    if Counter(keys) != Counter(expected):
        issues.append(f"sample keys mismatch: {len(rows)} rows")
    attempts = 0
    for row in rows:
        key = (row.get("scenario"), row.get("repeat"), row.get("method"))
        if key[0] not in CONTRACTS:
            issues.append(f"unknown task: {key}")
            continue
        tools, pattern = CONTRACTS[key[0]]
        final = str(row.get("final_output", ""))
        answer = bool(re.fullmatch(pattern, final, flags=re.IGNORECASE)) and "\n" not in final and len(final) <= 160
        observed = tuple(row.get("tool_names", []))
        wanted_at = 0
        for name in observed:
            if wanted_at < len(tools) and name == tools[wanted_at]:
                wanted_at += 1
        tool_ok = set(observed) == set(tools) and wanted_at == len(tools)
        safe = all(bool(row.get(flag)) for flag in ("constraint_preserved", "pairing_integrity", "structure_safe"))
        success = answer and tool_ok and safe and not row.get("error_type")
        if bool(row.get("success")) != success:
            issues.append(f"quality mismatch: {key}")
        if sum(row.get("actual_input_tokens_by_call", [])) != row.get("actual_input_tokens"):
            issues.append(f"input usage mismatch: {key}")
        if sum(row.get("actual_output_tokens_by_call", [])) != row.get("actual_output_tokens"):
            issues.append(f"output usage mismatch: {key}")
        total = sum(int(row.get(field, 0)) for field in ("actual_input_tokens", "actual_output_tokens", "summary_input_tokens", "summary_output_tokens"))
        if total != row.get("all_arm_total_tokens"):
            issues.append(f"all-call usage mismatch: {key}")
        attempts += int(row.get("api_request_attempts", 0))
        if int(row.get("api_request_attempts", 0)) < int(row.get("model_calls", 0)) + int(row.get("summary_calls", 0)):
            issues.append(f"attempt count smaller than completed calls: {key}")
        if row.get("method") != "native_summary" and row.get("summary_calls", 0):
            issues.append(f"unexpected summary calls: {key}")
    if attempts != report.get("request_budget", {}).get("recorded_attempts_all_rows"):
        issues.append("request attempt ledger mismatch")
    if attempts > freeze["max_api_requests"]:
        issues.append("request cap exceeded")
    keyed = {(r["scenario"], r["repeat"], r["method"]): r for r in rows}
    savings = {}
    for arm in METHODS[1:]:
        values = []
        for scenario in freeze["scenarios"]:
            for repeat in range(freeze["repeats"]):
                baseline = keyed.get((scenario, repeat, "none"))
                candidate = keyed.get((scenario, repeat, arm))
                if baseline and candidate and baseline["all_arm_total_tokens"]:
                    values.append((baseline["all_arm_total_tokens"] - candidate["all_arm_total_tokens"]) / baseline["all_arm_total_tokens"])
        savings[arm] = {"n": len(values), "mean": sum(values) / len(values) if values else None, "positive": sum(v > 0 for v in values)}
        reported = report.get("comparisons", {}).get(f"{arm}_vs_none", {})
        if reported.get("paired_n") != len(values):
            issues.append(f"paired count mismatch: {arm}")
        elif values and not math.isclose(reported.get("all_arm_total_token_savings_rate_vs_baseline", float("nan")), savings[arm]["mean"], rel_tol=1e-9, abs_tol=1e-9):
            issues.append(f"paired savings mismatch: {arm}")
    return {"complete": not issues, "issues": issues, "samples": len(rows), "attempts": attempts, "success": {arm: sum(bool(r.get("success")) for r in rows if r.get("method") == arm) for arm in METHODS}, "paired_savings": savings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--freeze", type=Path, default=FREEZE)
    args = parser.parse_args()
    result = audit(args.batch, args.freeze)
    (args.batch / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
