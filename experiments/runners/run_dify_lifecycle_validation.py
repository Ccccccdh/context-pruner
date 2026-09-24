"""Validate the imported Dify workflow against the Sidecar lifecycle API.

This runner calls a published Dify Workflow App; it never impersonates Dify.
It measures host boundary and served context, not model answer quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.runners.run_n8n_sidecar_experiment import PROJECT_ROOT


DSL = PROJECT_ROOT / "integrations" / "dify" / "context_pruner_lifecycle.yml"
TASKS = (
    ("incident", "Keep latest incident INC-901 and rollback_trigger=true."),
    ("release", "Keep latest release Vega-31 and security_gate=failed."),
    ("migration", "Keep latest migration Cygnus-4 and region=ap-south."),
)
METHODS = ("none", "pruner_v1")
WORKFLOW_CALLS_PER_CASE = 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="验证已发布 Dify Workflow 的 Sidecar 生命周期边界")
    parser.add_argument("--dify-base-url", default="https://api.dify.ai/v1")
    parser.add_argument("--api-key-env", default="DIFY_API_KEY")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-workflow-runs", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-dify-lifecycle"))
    parser.add_argument("--experiment-id", default="dify-lifecycle-3x1")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _messages(current: str) -> list[dict[str, str]]:
    history = [{"role": "system", "content": "Use only the latest verified record."}]
    for index in range(14):
        history.append({
            "role": "assistant",
            "content": (f"Obsolete Dify draft {index}: abandoned options and superseded records. ") * 3,
        })
        history.append({
            "role": "user",
            "content": (f"Archived Dify request {index}: compare an old choice that is no longer authorized. ") * 3,
        })
    history.append({"role": "user", "content": "CURRENT VERIFIED RECORD: " + current})
    return history


def _parse_workflow_response(raw: Mapping[str, Any]) -> dict[str, Any]:
    data = raw.get("data")
    if not isinstance(data, Mapping) or data.get("status") != "succeeded":
        raise RuntimeError(f"Dify workflow did not succeed: {data.get('status') if isinstance(data, Mapping) else 'missing data'}")
    outputs = data.get("outputs")
    if not isinstance(outputs, Mapping):
        raise RuntimeError("Dify workflow response is missing outputs")
    response_json = outputs.get("response_json")
    try:
        result = json.loads(response_json) if isinstance(response_json, str) else response_json
    except json.JSONDecodeError as error:
        raise RuntimeError("Dify output response_json is not valid JSON") from error
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise RuntimeError("Dify output is not a Sidecar lifecycle response")
    return result


def _invoke(
    endpoint: str,
    api_key: str,
    session_id: str,
    operation: str,
    payload: Mapping[str, Any],
    *,
    timeout: float,
) -> dict[str, Any]:
    request_body = {
        "inputs": {
            "session_id": session_id,
            "operation": operation,
            "payload_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
        "response_mode": "blocking",
        "user": "context-pruner-dify-validation",
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "ContextPrunerDifyValidation/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.load(response)
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            failure = json.loads(error.read(4096))
            if isinstance(failure, Mapping):
                code = failure.get("code")
                message = failure.get("message")
                parts = [value for value in (code, message) if isinstance(value, str)]
                detail = ": ".join(parts)[:300].replace(api_key, "[redacted]")
        except (ValueError, UnicodeDecodeError):
            pass
        if not detail:
            content_type = error.headers.get("Content-Type", "unknown").split(";", 1)[0]
            server = error.headers.get("Server", "unknown")
            ray = error.headers.get("CF-Ray", "")
            detail = f"non-JSON response; content-type={content_type}; server={server}"
            if ray:
                detail += f"; cf-ray={ray}"
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Dify workflow HTTP {error.code}{suffix}") from error
    if not isinstance(raw, Mapping):
        raise RuntimeError("Dify workflow returned a non-object response")
    return _parse_workflow_response(raw)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats <= 0 or args.max_workflow_runs <= 0 or args.timeout <= 0:
        raise SystemExit("repeats, max-workflow-runs and timeout must be positive")
    if any(marker in args.dify_base_url for marker in "[]()"):
        raise SystemExit("dify-base-url must be a plain URL, not a Markdown link")
    if not args.dify_base_url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
        raise SystemExit("Dify endpoint must use HTTPS unless it is loopback")
    planned_calls = len(TASKS) * len(METHODS) * args.repeats * WORKFLOW_CALLS_PER_CASE
    if planned_calls > args.max_workflow_runs:
        raise SystemExit(
            f"planned Dify workflow runs {planned_calls} exceed --max-workflow-runs={args.max_workflow_runs}"
        )
    plan = {
        "host": "dify-workflow",
        "mode": "sidecar-boundary",
        "tasks": len(TASKS),
        "methods": list(METHODS),
        "repeats": args.repeats,
        "planned_dify_workflow_runs": planned_calls,
        "planned_model_calls": 0,
        "synthetic_data_only": True,
        "dify_base_url": args.dify_base_url.rstrip("/"),
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未读取密钥、未请求 Dify、未创建报告。")
        return 0
    if not args.confirm_send_synthetic_data:
        raise SystemExit("Dify execution requires --confirm-send-synthetic-data")
    api_key = os.getenv(args.api_key_env) or ""
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")
    output = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    if output.exists():
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output}")
    endpoint = args.dify_base_url.rstrip("/") + "/workflows/run"
    rows: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        for task_id, current in TASKS:
            for method in METHODS:
                session_id = f"dify:{task_id}:r{repeat}:{method}"
                history = _messages(current)
                before = _invoke(endpoint, api_key, session_id, "before_model", {
                    "messages": history,
                    "task_state": current,
                    "method": method,
                    "budget": {"soft": 900, "hard": 1200, "target": 650},
                    "reserved_tokens": 180,
                }, timeout=args.timeout)
                rendered = json.dumps(before.get("messages"), ensure_ascii=False)
                preserved = all(term in rendered for term in current.split(" and "))
                _invoke(endpoint, api_key, session_id, "after_model", {
                    "output": {"role": "assistant", "content": current},
                }, timeout=args.timeout)
                final = _invoke(endpoint, api_key, session_id, "finalize", {
                    "metadata": {"host": "dify-workflow", "repeat": repeat},
                }, timeout=args.timeout)
                deleted = _invoke(endpoint, api_key, session_id, "delete_session", {}, timeout=args.timeout)
                metrics = final.get("metrics", {})
                finalized = final.get("lifecycle_state", {}).get("plugin", {}).get("finalized") is True
                rows.append({
                    "task_id": task_id,
                    "repeat": repeat,
                    "method": method,
                    "session_id": session_id,
                    "current_record_preserved": preserved,
                    "finalized": finalized,
                    "deleted": deleted.get("deleted") is True,
                    "served_context_tokens": metrics.get("last_served_context_tokens"),
                    "model_input_budget_violation_count": metrics.get("model_input_budget_violation_count"),
                })
    by_key = {(row["task_id"], row["repeat"], row["method"]): row for row in rows}
    savings = []
    for repeat in range(args.repeats):
        for task_id, _ in TASKS:
            base = by_key[(task_id, repeat, "none")]["served_context_tokens"]
            pruned = by_key[(task_id, repeat, "pruner_v1")]["served_context_tokens"]
            if not isinstance(base, (int, float)) or not isinstance(pruned, (int, float)) or base <= 0:
                raise RuntimeError("Dify run lacks valid context metrics")
            savings.append((base - pruned) / base)
    summary = {
        "case_count": len(rows),
        "paired_n": len(savings),
        "all_preserved": all(row["current_record_preserved"] for row in rows),
        "all_finalized": all(row["finalized"] for row in rows),
        "all_deleted": all(row["deleted"] for row in rows),
        "paired_served_context_savings_rate_mean": sum(savings) / len(savings),
        "paired_positive_savings_n": sum(value > 0 for value in savings),
    }
    output.mkdir(parents=True)
    report = {
        "protocol_version": "dify-sidecar-lifecycle-v1",
        "host": "dify-workflow",
        "dify_base_url": args.dify_base_url.rstrip("/"),
        "dsl_sha256": hashlib.sha256(DSL.read_bytes()).hexdigest(),
        "synthetic_data_only": True,
        "model_calls": 0,
        "planned_dify_workflow_runs": planned_calls,
        "summary": summary,
        "rows": rows,
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Dify 宿主边界报告已生成：{output}")
    return 0 if summary["all_preserved"] and summary["all_finalized"] and summary["all_deleted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
