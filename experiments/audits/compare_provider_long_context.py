"""Audit comparable long-context reports without comparing tokenizer raw totals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "manifest" not in payload or "summary" not in payload:
        raise ValueError(f"invalid provider report: {path}")
    return payload


def compare_reports(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(reports) < 2:
        raise ValueError("at least two reports are required")
    first = reports[0]["manifest"]
    frozen_fields = ("protocol_version", "task_sha256", "tasks", "methods", "repeats", "budget", "max_output_tokens")
    mismatches: list[dict[str, Any]] = []
    for index, report in enumerate(reports[1:], start=1):
        manifest = report["manifest"]
        for field in frozen_fields:
            if manifest.get(field) != first.get(field):
                mismatches.append({"report_index": index, "field": field})
    providers: list[dict[str, Any]] = []
    for report in reports:
        manifest = report["manifest"]
        summary = report["summary"]
        paired = summary["paired"]
        providers.append(
            {
                "provider": manifest["provider"],
                "model": manifest["model"],
                "paired_n": paired["n"],
                "success_rate_none": summary["methods"]["none"]["success_rate"],
                "success_rate_pruner_v1": summary["methods"]["pruner_v1"]["success_rate"],
                "success_delta_mean": paired["success_delta_mean"],
                "within_provider_input_savings_rate_mean": paired["provider_input_savings_rate_mean"],
                "within_provider_input_savings_ci_low": paired["provider_input_savings_ci_low"],
                "within_provider_input_savings_ci_high": paired["provider_input_savings_ci_high"],
                "within_provider_total_token_savings_rate_mean": paired["provider_total_token_savings_rate_mean"],
                "within_provider_total_token_savings_ci_low": paired["provider_total_token_savings_ci_low"],
                "within_provider_total_token_savings_ci_high": paired["provider_total_token_savings_ci_high"],
                "completion_token_change_rate_mean": paired["provider_completion_token_change_rate_mean"],
                "reasoning_tokens_delta_mean": paired["provider_reasoning_tokens_delta_mean"],
                "positive_savings_pairs": paired["positive_savings_pairs"],
                "budget_violations_none": summary["methods"]["none"]["budget_violation_count"],
                "budget_violations_pruner_v1": summary["methods"]["pruner_v1"]["budget_violation_count"],
            }
        )
    return {
        "comparable": not mismatches,
        "mismatches": mismatches,
        "raw_token_totals_compared_across_providers": False,
        "reason": "Providers may use different tokenizers; compare within-provider paired rates and task quality.",
        "protocol_version": first["protocol_version"],
        "task_sha256": first["task_sha256"],
        "providers": providers,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="审计多个供应商的同协议长上下文报告")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--out", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = compare_reports([load_report(path) for path in args.reports])
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded, encoding="utf-8")
        print(f"跨供应商审计已生成：{args.out}")
    print(encoded, end="")
    return 0 if result["comparable"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
