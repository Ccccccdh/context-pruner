"""Validate a completed AutoGen team run against a preregistered protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def evaluate_summary(
    summary: Mapping[str, Any], protocol: Mapping[str, Any]
) -> list[dict[str, Any]]:
    gates = protocol["acceptance_gates"]
    paired = summary["paired"]
    methods = summary["methods"]
    checks: list[dict[str, Any]] = []

    def add(name: str, actual: float, operator: str, threshold: float) -> None:
        passed = actual <= threshold if operator == "<=" else actual >= threshold
        checks.append(
            {
                "name": name,
                "actual": actual,
                "operator": operator,
                "threshold": threshold,
                "passed": passed,
            }
        )

    add("paired_n", float(paired["n"]), ">=", float(protocol["expected_pairs"]))
    add(
        "incomplete_pairs",
        float(paired["incomplete_n"]),
        "<=",
        float(gates["incomplete_pairs_max"]),
    )
    for method in protocol["methods"]:
        values = methods[method]
        prefix = str(method)
        add(
            f"{prefix}.success_rate",
            float(values["success_rate"]),
            ">=",
            float(gates["success_rate_min"]),
        )
        for metric, gate_key in (
            ("model_input_privacy_rate", "model_input_privacy_rate_min"),
            ("handoff_preserved_rate", "handoff_preserved_rate_min"),
            ("handoff_target_only_rate", "handoff_target_only_rate_min"),
            ("state_roundtrip_ok_rate", "state_roundtrip_rate_min"),
            ("output_contract_correct_rate", "output_contract_rate_min"),
        ):
            add(
                f"{prefix}.{metric}",
                float(values[metric]),
                ">=",
                float(gates[gate_key]),
            )
    add(
        "input_savings_ci_low",
        float(paired["input_savings_ci_low"]),
        ">=",
        float(gates["input_savings_ci_low_min"]),
    )
    add(
        "total_token_savings_ci_low",
        float(paired["total_token_savings_ci_low"]),
        ">=",
        float(gates["total_token_savings_ci_low_min"]),
    )
    add(
        "positive_savings_rate",
        float(paired["positive_savings_rate"]),
        ">=",
        float(gates["positive_savings_rate_min"]),
    )
    add(
        "absolute_output_token_change",
        abs(float(paired["output_token_change_rate_mean"])),
        "<=",
        float(gates["output_token_change_abs_max"]),
    )
    add(
        "pruner_v1.hard_budget_violations",
        float(methods["pruner_v1"]["hard_budget_violations"]),
        "<=",
        float(gates["plugin_hard_budget_violations_max"]),
    )
    return checks


def validate_run(run_dir: Path, protocol_path: Path) -> dict[str, Any]:
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads(
        (run_dir / "run_manifest.json").read_text(encoding="utf-8")
    )
    request_usage = json.loads(
        (run_dir / "request_usage.json").read_text(encoding="utf-8")
    )
    expected_protocol_hash = hashlib.sha256(protocol_path.read_bytes()).hexdigest()
    manifest_protocol = manifest.get("protocol") or {}
    integrity_checks = [
        {
            "name": "protocol_id",
            "passed": manifest_protocol.get("protocol_id")
            == protocol.get("protocol_id"),
        },
        {
            "name": "protocol_sha256",
            "passed": manifest_protocol.get("sha256") == expected_protocol_hash,
        },
        {
            "name": "request_cap",
            "passed": int(request_usage["api_requests_used"])
            <= int(protocol["maximum_api_requests"]),
        },
        {
            "name": "thinking_mode",
            "passed": summary.get("thinking_mode") == protocol["thinking_mode"],
        },
        {
            "name": "answers_not_disclosed",
            "passed": summary.get("evaluation_answer_terms_disclosed") is False,
        },
    ]
    if "context_pruner_version" in protocol:
        integrity_checks.append(
            {
                "name": "context_pruner_version",
                "passed": manifest.get("version")
                == protocol.get("context_pruner_version")
                == manifest_protocol.get("context_pruner_version"),
            }
        )
    checks = integrity_checks + evaluate_summary(summary, protocol)
    rows = _load_jsonl(run_dir / "results.jsonl")
    attempt_prompt_tokens = 0
    attempt_completion_tokens = 0
    for row in rows:
        attempt_groups = row.get("api_attempt_records") or {}
        if not isinstance(attempt_groups, Mapping):
            continue
        for records in attempt_groups.values():
            for record in records or []:
                attempt_prompt_tokens += int(record.get("prompt_tokens", 0))
                attempt_completion_tokens += int(record.get("completion_tokens", 0))
    pricing = protocol["pricing_snapshot"]
    peak_cost = (
        attempt_prompt_tokens
        * float(pricing["input_cache_miss_per_million_peak"])
        + attempt_completion_tokens * float(pricing["output_per_million_peak"])
    ) / 1_000_000
    return {
        "protocol_id": protocol["protocol_id"],
        "all_passed": all(bool(check["passed"]) for check in checks),
        "checks": checks,
        "request_usage": request_usage,
        "observed_attempt_tokens": {
            "prompt": attempt_prompt_tokens,
            "completion": attempt_completion_tokens,
        },
        "peak_cost_upper_bound_cny": peak_cost,
        "latency_policy": protocol["latency_policy"],
    }


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _report(result: Mapping[str, Any]) -> str:
    lines = [
        "# AutoGen 团队确认实验预注册验收",
        "",
        f"- 协议：`{result['protocol_id']}`",
        f"- 总体通过：{'是' if result['all_passed'] else '否'}",
        f"- API 请求：{result['request_usage']['api_requests_used']}/"
        f"{result['request_usage']['maximum_api_requests']}",
        f"- 高峰全缓存未命中成本上界：¥{result['peak_cost_upper_bound_cny']:.4f}",
        "",
        "## 冻结门槛",
        "",
    ]
    for check in result["checks"]:
        status = "PASS" if check["passed"] else "FAIL"
        if "actual" in check:
            lines.append(
                f"- {status} `{check['name']}`：{check['actual']:.4f} "
                f"{check['operator']} {check['threshold']:.4f}"
            )
        else:
            lines.append(f"- {status} `{check['name']}`")
    lines.extend(["", f"延迟口径：{result['latency_policy']}"])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument(
        "--protocol",
        default="tasks/stage5_autogen_team/confirmatory_v107_protocol.json",
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    result = validate_run(run_dir, Path(args.protocol))
    (run_dir / "confirmatory_gate.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "confirmatory_gate.md").write_text(
        _report(result), encoding="utf-8"
    )
    print(
        f"confirmatory gates: {'PASS' if result['all_passed'] else 'FAIL'}; "
        f"report: {run_dir / 'confirmatory_gate.md'}"
    )


if __name__ == "__main__":
    main()
