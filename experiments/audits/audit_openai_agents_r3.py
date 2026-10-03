"""Independent, offline audit of the frozen OpenAI Agents v2 three-repeat pilot.

Reads raw JSONL and the runner report; does not import the runner or call APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FROZEN = {
    "experiments/runners/run_openai_agents_api_experiment.py": "cbb47df340603beaf6f44d5fb7cc8345041293f82e15ec196f551d82a7f69bfc",
    "experiments/runners/token_policy.py": "e1d3c0de34c1ba55a3400a24ca71ea514363b7c9cdcf0ca4e8449382a58474cb",
    "experiments/runners/trigger_gate.py": "931fd869733029f2f47604f6d99fdaef5da542840c6cb8c806360870cb3c5b23",
    "experiments/runners/openai_agents_native_summary.py": "2b05db2560ab80d8eca9aaee5828209f9439d0682d200cf8e6a7f01ab5f7fccd",
}
EXPECTED_TOOLS = {
    "single_tool": ["lookup_project"],
    "parallel_tools": ["read_status", "read_owner"],
    "multi_tool_chain": ["lookup_project", "check_policy"],
}
METHODS = ("none", "pruner_v1", "native_summary")


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)


def audit(batch: Path) -> dict:
    issues: list[str] = []
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (batch / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    if manifest.get("experiment_id") != batch.name or manifest.get("repeats") != 3:
        issues.append("manifest identity/repeats mismatch")
    if manifest.get("methods") != list(METHODS) or set(manifest.get("scenarios", [])) != set(EXPECTED_TOOLS):
        issues.append("manifest methods/scenarios mismatch")
    if manifest.get("maximum_api_requests") != 240 or manifest.get("trigger_policy") != "symmetric_budget":
        issues.append("manifest cap/trigger mismatch")
    source_hashes = {}
    for path, expected in FROZEN.items():
        actual = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        source_hashes[path] = actual
        if actual != expected:
            issues.append(f"source hash changed: {path}")

    keyed = {(r.get("scenario"), r.get("repeat"), r.get("method")): r for r in rows}
    expected_keys = {(s, repeat, method) for s in EXPECTED_TOOLS for repeat in range(3) for method in METHODS}
    if len(rows) != 27 or set(keyed) != expected_keys:
        issues.append(f"incomplete or duplicate samples: rows={len(rows)} keys={len(keyed)}")
    attempts = 0
    per_method = {}
    for method in METHODS:
        arm_rows = [r for r in rows if r.get("method") == method]
        per_method[method] = {"samples": len(arm_rows), "success": sum(bool(r.get("success")) for r in arm_rows),
                              "structure_safe": sum(bool(r.get("structure_safe")) for r in arm_rows),
                              "summary_calls": sum(int(r.get("summary_calls", 0)) for r in arm_rows),
                              "compression_events": sum(int(r.get("context_compression_events", 0)) for r in arm_rows)}
        reported = next((m for m in report.get("methods", []) if m.get("method") == method), None)
        if reported is None or reported.get("n") != len(arm_rows):
            issues.append(f"report arm count mismatch: {method}")
        elif not close(float(reported.get("success_rate", -1)), per_method[method]["success"] / max(1, len(arm_rows))):
            issues.append(f"report success rate mismatch: {method}")
    for r in rows:
        key = (r.get("scenario"), r.get("repeat"), r.get("method"))
        if r.get("scenario") not in EXPECTED_TOOLS:
            issues.append(f"unknown scenario: {key}")
            continue
        names = r.get("tool_names", [])
        wanted = EXPECTED_TOOLS[r["scenario"]]
        if Counter(names) != Counter(wanted):
            issues.append(f"tool contract mismatch: {key}")
        if r["scenario"] == "multi_tool_chain" and names != wanted:
            issues.append(f"tool order mismatch: {key}")
        codename = f"Aurora-API-{int(r['repeat']):02d}"
        final = str(r.get("final_output", ""))
        terms = [codename, "active"]
        if r["scenario"] == "parallel_tools":
            terms.append("team-blue")
        if r["scenario"] == "multi_tool_chain":
            terms.append("compliant")
        answer_ok = final.startswith("RESULT ") and "\n" not in final and len(final) <= 160 and all(t in final for t in terms)
        quality_flags = ("answer_correct", "final_format_correct", "tool_correct", "tool_order_correct",
                         "parallel_correct", "constraint_preserved", "pairing_integrity", "structure_safe")
        recomputed_success = answer_ok and all(bool(r.get(k)) for k in quality_flags) and not r.get("error_type")
        if recomputed_success != bool(r.get("success")):
            issues.append(f"success mismatch: {key}")
        if sum(r.get("actual_input_tokens_by_call", [])) != r.get("actual_input_tokens") or sum(r.get("actual_output_tokens_by_call", [])) != r.get("actual_output_tokens"):
            issues.append(f"usage arrays mismatch: {key}")
        expected_total = sum(int(r.get(k, 0)) for k in ("actual_input_tokens", "actual_output_tokens", "summary_input_tokens", "summary_output_tokens"))
        if expected_total != r.get("all_arm_total_tokens"):
            issues.append(f"all-arm total mismatch: {key}")
        count = int(r.get("api_request_attempts", 0))
        attempts += count
        if count > 34:
            issues.append(f"per-sample request bound exceeded: {key}: {count}")
        if r.get("method") != "native_summary" and int(r.get("summary_calls", 0)):
            issues.append(f"unexpected summary: {key}")
        if int(r.get("approximate_input_tokens", 0)) + int(r.get("actual_output_tokens", 0)) + int(r.get("summary_input_tokens", 0)) + int(r.get("summary_output_tokens", 0)) > 312000:
            issues.append(f"planning token warning: {key}")
    if attempts > 240:
        issues.append(f"global request bound exceeded: {attempts}")
    if report.get("request_budget", {}).get("recorded_attempts_all_rows") != attempts:
        issues.append("request ledger mismatch")

    pairs = {}
    for arm in METHODS[1:]:
        vals_input, vals_total, wins, quality = [], [], 0, 0
        by_scenario = {}
        for scenario in EXPECTED_TOOLS:
            segment = []
            for repeat in range(3):
                baseline = keyed.get((scenario, repeat, "none"))
                candidate = keyed.get((scenario, repeat, arm))
                if baseline is None or candidate is None:
                    continue
                a = (baseline["actual_input_tokens"] - candidate["actual_input_tokens"]) / baseline["actual_input_tokens"]
                b = (baseline["all_arm_total_tokens"] - candidate["all_arm_total_tokens"]) / baseline["all_arm_total_tokens"]
                vals_input.append(a)
                vals_total.append(b)
                segment.append(b)
                wins += b > 0
                quality += bool(candidate["success"]) == bool(baseline["success"])
            by_scenario[scenario] = {"n": len(segment), "all_arm_mean": sum(segment) / len(segment) if segment else None}
        mean_input = sum(vals_input) / len(vals_input) if vals_input else None
        mean_total = sum(vals_total) / len(vals_total) if vals_total else None
        pairs[arm] = {"n": len(vals_input), "input_mean": mean_input, "all_arm_mean": mean_total,
                      "all_arm_positive_pairs": wins, "paired_success_agreement": quality, "by_scenario": by_scenario}
        reported = report.get("comparisons", {}).get(f"{arm}_vs_none", {})
        if reported.get("paired_n") != len(vals_input) or not close(float(reported.get("actual_input_savings_rate_vs_baseline", -999)), mean_input or 0):
            issues.append(f"report input comparison mismatch: {arm}")
        if not close(float(reported.get("all_arm_total_token_savings_rate_vs_baseline", -999)), mean_total or 0):
            issues.append(f"report all-arm comparison mismatch: {arm}")
    return {"complete": not issues, "issues": issues, "raw_rows": len(rows), "unique_samples": len(keyed),
            "source_hashes": source_hashes, "api_request_attempts": attempts, "methods": per_method, "paired": pairs}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    args = parser.parse_args()
    result = audit(args.batch)
    (args.batch / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["complete"] else 1)
