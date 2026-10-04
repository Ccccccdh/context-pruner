"""Independent, zero-model audit for the repository diagnostic development batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FREEZE = ROOT / "integrations/openai_agents/REPO_DIAGNOSTIC_V1_FREEZE.json"
METHODS = ("none", "pruner_v1", "native_summary")
CONTRACTS = {
    "pytest_mro": (
        ("read_pytest_mark_decorator", "read_pytest_mark_storage", "read_pytest_mark_context"),
        r"RESULT issue=pytest-10356 cause=.*getattr.* fix=.*__dict__.*MRO.*",
        ("getattr", "__dict__", "MRO"),
    ),
    "pylint_regex_csv": (
        ("read_pylint_option", "read_pylint_transformer", "read_pylint_csv_helper"),
        r"RESULT issue=pylint-8898 cause=.*_splitstrip.* fix=.*(?:quantifier|brace).*",
        ("_splitstrip", "quantifier"),
    ),
    "django_count_annotations": (
        ("read_django_count_entry", "read_django_aggregation", "read_django_count_call"),
        r"RESULT issue=django-16263 cause=.*existing_annotations.*subquery.* fix=.*referenced.*",
        ("existing_annotations", "subquery", "referenced"),
    ),
}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit(batch: Path, freeze_path: Path = DEFAULT_FREEZE) -> dict:
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    manifest = json.loads((batch / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((batch / "report.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (batch / "samples.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    issues: list[str] = []
    if batch.name != freeze["batch"] or manifest.get("experiment_id") != freeze["batch"]:
        issues.append("batch ID mismatch")
    for relative, wanted in {**freeze["source_sha256"], **freeze["public_input_sha256"]}.items():
        if _digest(ROOT / relative) != wanted:
            issues.append(f"hash mismatch: {relative}")
    for field, wanted in (("scenarios", freeze["scenarios"]), ("methods", list(METHODS)), ("repeats", freeze["repeats"]),
                          ("maximum_api_requests", freeze["max_api_requests"]), ("max_output_tokens", freeze["max_output_tokens"])):
        if manifest.get(field) != wanted:
            issues.append(f"manifest mismatch: {field}")
    if manifest.get("model") != freeze["model"] or manifest.get("base_url") != freeze["base_url"]:
        issues.append("model/endpoint mismatch")
    if manifest.get("trigger_policy") != "symmetric_budget":
        issues.append("trigger policy mismatch")
    if manifest.get("budget_calibration", {}).get("provider_tokens") != freeze["provider_budget_tokens"]:
        issues.append("provider budget mismatch")
    keys = [(r.get("scenario"), r.get("repeat"), r.get("method")) for r in rows]
    expected = [(s, i, arm) for s in freeze["scenarios"] for i in range(freeze["repeats"]) for arm in METHODS]
    if Counter(keys) != Counter(expected):
        issues.append("sample grid mismatch")
    attempts = 0
    for row in rows:
        key = (row.get("scenario"), row.get("repeat"), row.get("method"))
        if key[0] not in CONTRACTS:
            issues.append(f"unknown task: {key}")
            continue
        tool_names, pattern, terms = CONTRACTS[key[0]]
        final = str(row.get("final_output", ""))
        answer_ok = bool(re.fullmatch(pattern, final, flags=re.IGNORECASE)) and all(t.lower() in final.lower() for t in terms)
        format_ok = final.startswith("RESULT ") and "\n" not in final and len(final) <= 160
        observed = tuple(row.get("tool_names", []))
        position = 0
        for name in observed:
            if position < len(tool_names) and name == tool_names[position]:
                position += 1
        tools_ok = set(observed) == set(tool_names) and position == len(tool_names)
        safe = all(bool(row.get(flag)) for flag in ("constraint_preserved", "pairing_integrity", "structure_safe"))
        success = answer_ok and format_ok and tools_ok and safe and not row.get("error_type")
        if bool(row.get("success")) != success:
            issues.append(f"quality mismatch: {key}")
        inputs = sum(int(v) for v in row.get("actual_input_tokens_by_call", []))
        outputs = sum(int(v) for v in row.get("actual_output_tokens_by_call", []))
        if inputs != row.get("actual_input_tokens") or outputs != row.get("actual_output_tokens"):
            issues.append(f"model usage mismatch: {key}")
        total = inputs + outputs + int(row.get("summary_input_tokens", 0)) + int(row.get("summary_output_tokens", 0))
        if total != row.get("all_arm_total_tokens"):
            issues.append(f"all-call usage mismatch: {key}")
        calls = int(row.get("api_request_attempts", 0))
        attempts += calls
        if calls < int(row.get("model_calls", 0)) + int(row.get("summary_calls", 0)):
            issues.append(f"request ledger mismatch: {key}")
        if key[2] != "native_summary" and row.get("summary_calls", 0):
            issues.append(f"unexpected summary calls: {key}")
    if attempts > freeze["max_api_requests"] or attempts != report.get("request_budget", {}).get("recorded_attempts_all_rows"):
        issues.append("global request ledger mismatch")
    keyed = {(r.get("scenario"), r.get("repeat"), r.get("method")): r for r in rows}
    savings = {}
    for arm in METHODS[1:]:
        values = []
        for task in freeze["scenarios"]:
            for repeat in range(freeze["repeats"]):
                baseline = keyed.get((task, repeat, "none"))
                candidate = keyed.get((task, repeat, arm))
                if baseline and candidate and baseline.get("all_arm_total_tokens"):
                    values.append((baseline["all_arm_total_tokens"] - candidate["all_arm_total_tokens"]) / baseline["all_arm_total_tokens"])
        savings[arm] = {"n": len(values), "mean": sum(values) / len(values) if values else None, "positive": sum(v > 0 for v in values)}
        recorded = report.get("comparisons", {}).get(f"{arm}_vs_none", {})
        if recorded.get("paired_n") != len(values) or (values and not math.isclose(
            recorded.get("all_arm_total_token_savings_rate_vs_baseline", float("nan")), savings[arm]["mean"], rel_tol=1e-9, abs_tol=1e-9
        )):
            issues.append(f"paired savings mismatch: {arm}")
    return {"complete": not issues, "issues": issues, "samples": len(rows), "attempts": attempts,
            "success": {arm: sum(bool(r.get("success")) for r in rows if r.get("method") == arm) for arm in METHODS},
            "paired_savings": savings}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--freeze", type=Path, default=DEFAULT_FREEZE)
    args = parser.parse_args()
    result = audit(args.batch, args.freeze)
    (args.batch / "audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
