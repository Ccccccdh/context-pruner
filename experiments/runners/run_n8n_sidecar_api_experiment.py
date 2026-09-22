"""Run a real DeepSeek A/B experiment through an imported n8n workflow.

The API key is injected only into a temporary workflow copy.  The committed
workflow, report, and logs never contain the credential.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import __version__ as context_pruner_version
from context_pruner.sidecar import (
    ContextSidecarService,
    SidecarServerConfig,
    create_sidecar_server,
)
from experiments.runners.run_n8n_sidecar_experiment import (
    DEFAULT_N8N_VERSION,
    PROJECT_ROOT,
    _ResultCollector,
    _create_collector_server,
    _launcher,
    _run,
)


DEFAULT_WORKFLOW = (
    PROJECT_ROOT / "integrations" / "n8n" / "context_pruner_sidecar_deepseek_ab.json"
)
WORKFLOW_ID = "context-pruner-sidecar-deepseek-ab-v1"
TASK_COUNT = 3
METHODS = ("none", "pruner_v1")
MODEL_NODE_MAX_TRIES = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在真实 n8n + Sidecar 边界运行 DeepSeek 自然任务配对实验"
    )
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--n8n-version", default=DEFAULT_N8N_VERSION)
    parser.add_argument("--node-executable", type=Path)
    parser.add_argument("--n8n-cli-script", type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-api-requests", type=int, default=18)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-n8n-sidecar-api"))
    parser.add_argument("--experiment-id", default="deepseek-n8n-sidecar-3x1-smoke")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _render_api_workflow(
    template: Path,
    target: Path,
    *,
    sidecar_url: str,
    sidecar_token: str,
    collector_url: str,
    collector_token: str,
    model_base_url: str,
    model_api_key: str,
    model_name: str,
    repeats: int,
) -> str:
    raw = template.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("id") != WORKFLOW_ID:
        raise RuntimeError(f"工作流 ID 必须为 {WORKFLOW_ID}")
    replacements = {
        "__SIDECAR_URL__": sidecar_url.rstrip("/"),
        "__SIDECAR_TOKEN__": sidecar_token,
        "__COLLECTOR_URL__": collector_url.rstrip("/"),
        "__COLLECTOR_TOKEN__": collector_token,
        "__MODEL_BASE_URL__": model_base_url.rstrip("/"),
        "__MODEL_API_KEY__": model_api_key,
        "__MODEL_NAME__": model_name,
        "__REPEATS__": str(repeats),
    }
    rendered = raw
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    unresolved = [key for key in replacements if key in rendered]
    if unresolved:
        raise RuntimeError(f"工作流仍包含未替换占位符：{', '.join(unresolved)}")
    # Validate the final JSON before a credential-bearing temporary file is used.
    json.loads(rendered)
    target.write_text(rendered, encoding="utf-8")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def summarize_api_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    pairs: dict[tuple[str, int], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        method = str(row.get("method") or "")
        task_id = str(row.get("task_id") or "")
        repeat = int(row.get("repeat", -1))
        if method not in METHODS or not task_id or repeat < 0:
            raise RuntimeError("n8n 返回了未知任务、方法或重复编号")
        grouped[method].append(row)
        pairs[(task_id, repeat)][method] = row

    method_summary: dict[str, Any] = {}
    for method in METHODS:
        values = grouped.get(method, [])
        if not values:
            raise RuntimeError(f"n8n 没有返回方法 {method} 的结果")
        method_summary[method] = {
            "n": len(values),
            "success_rate": sum(bool(row.get("success")) for row in values) / len(values),
            "prompt_tokens_mean": sum(int(row.get("prompt_tokens", 0)) for row in values)
            / len(values),
            "completion_tokens_mean": sum(
                int(row.get("completion_tokens", 0)) for row in values
            )
            / len(values),
            "served_context_tokens_mean": sum(
                int(row.get("served_context_tokens", 0)) for row in values
            )
            / len(values),
            "budget_violation_count": sum(
                int(row.get("model_input_budget_violation_count", 0)) for row in values
            ),
        }

    prompt_savings: list[float] = []
    served_savings: list[float] = []
    success_deltas: list[int] = []
    for pair_id, methods in sorted(pairs.items()):
        if set(methods) != set(METHODS):
            raise RuntimeError(f"样本 {pair_id} 缺少配对方法")
        baseline = methods["none"]
        treated = methods["pruner_v1"]
        baseline_prompt = int(baseline.get("prompt_tokens", 0))
        treated_prompt = int(treated.get("prompt_tokens", 0))
        baseline_served = int(baseline.get("served_context_tokens", 0))
        treated_served = int(treated.get("served_context_tokens", 0))
        if baseline_prompt > 0 and treated_prompt > 0:
            prompt_savings.append((baseline_prompt - treated_prompt) / baseline_prompt)
        if baseline_served > 0 and treated_served > 0:
            served_savings.append((baseline_served - treated_served) / baseline_served)
        success_deltas.append(int(bool(treated.get("success"))) - int(bool(baseline.get("success"))))

    return {
        "case_count": len(rows),
        "paired_n": len(pairs),
        "all_success": all(bool(row.get("success")) for row in rows),
        "all_finalized": all(bool(row.get("finalized")) for row in rows),
        "transport_error_count": sum(bool(row.get("transport_error")) for row in rows),
        "method_summary": method_summary,
        "paired_prompt_token_savings_rate_mean": (
            sum(prompt_savings) / len(prompt_savings) if prompt_savings else None
        ),
        "paired_prompt_token_savings_win_rate": (
            sum(value > 0 for value in prompt_savings) / len(prompt_savings)
            if prompt_savings
            else None
        ),
        "paired_served_context_savings_rate_mean": (
            sum(served_savings) / len(served_savings) if served_savings else None
        ),
        "success_delta_mean": (
            sum(success_deltas) / len(success_deltas) if success_deltas else None
        ),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats <= 0:
        raise SystemExit("--repeats 必须大于 0")
    case_count = TASK_COUNT * len(METHODS) * args.repeats
    maximum_requests = case_count * MODEL_NODE_MAX_TRIES
    if maximum_requests > args.max_api_requests:
        raise SystemExit(
            f"最坏情况 API 请求数 {maximum_requests} 超过 --max-api-requests={args.max_api_requests}"
        )
    workflow = args.workflow.resolve()
    if not workflow.is_file():
        raise SystemExit(f"工作流不存在：{workflow}")
    launcher = _launcher(args)
    plan = {
        "host": "n8n",
        "n8n_version": args.n8n_version,
        "workflow": str(workflow),
        "model": args.model,
        "base_url": args.base_url.rstrip("/"),
        "synthetic_tasks": TASK_COUNT,
        "methods": list(METHODS),
        "repeats": args.repeats,
        "planned_model_calls": case_count,
        "maximum_model_api_requests_with_retries": maximum_requests,
        "launcher": launcher,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(
        "Only synthetic task histories and synthetic identifiers are sent; "
        "no workspace file is read or transmitted."
    )
    if args.plan:
        print("计划模式：未读取密钥、未启动 n8n、未发送模型 API 请求。")
        return 0
    if not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY or OPENAI_API_KEY is not set")

    output_dir = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    if output_dir.exists():
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output_dir}")

    sidecar_token = secrets.token_urlsafe(24)
    collector_token = secrets.token_urlsafe(24)
    server_config = SidecarServerConfig(host="127.0.0.1", port=0, auth_token=sidecar_token)
    service = ContextSidecarService(server_config)
    server = create_sidecar_server(server_config, service=service)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address[:2]

    collector = _ResultCollector(collector_token)
    collector_server = _create_collector_server(collector)
    collector_thread = threading.Thread(target=collector_server.serve_forever, daemon=True)
    collector_thread.start()
    collector_host, collector_port = collector_server.server_address[:2]

    try:
        with tempfile.TemporaryDirectory(prefix="context-pruner-n8n-api-") as temporary:
            runtime = Path(temporary)
            rendered_workflow = runtime / "workflow.json"
            workflow_hash = _render_api_workflow(
                workflow,
                rendered_workflow,
                sidecar_url=f"http://{host}:{port}",
                sidecar_token=sidecar_token,
                collector_url=f"http://{collector_host}:{collector_port}",
                collector_token=collector_token,
                model_base_url=args.base_url,
                model_api_key=api_key,
                model_name=args.model,
                repeats=args.repeats,
            )
            env = os.environ.copy()
            env.update(
                {
                    "N8N_USER_FOLDER": str(runtime / "user"),
                    "N8N_DIAGNOSTICS_ENABLED": "false",
                    "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
                    "N8N_TEMPLATES_ENABLED": "false",
                    "N8N_PERSONALIZATION_ENABLED": "false",
                    "N8N_LOG_LEVEL": "error",
                    "N8N_RUNNERS_ENABLED": "false",
                }
            )
            _run(
                [*launcher, "import:workflow", f"--input={rendered_workflow}"],
                env=env,
                timeout=args.timeout,
            )
            _run(
                [*launcher, "execute", f"--id={WORKFLOW_ID}", "--rawOutput"],
                env=env,
                timeout=args.timeout,
            )
            rows = collector.snapshot()
            if len(rows) != case_count or service.session_count != case_count:
                raise RuntimeError(
                    f"n8n 结果不完整：预期 {case_count}，回传 {len(rows)}，"
                    f"Sidecar 会话 {service.session_count}"
                )
            summary = summarize_api_rows(rows)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        collector_server.shutdown()
        collector_server.server_close()
        collector_thread.join(timeout=5)

    output_dir.mkdir(parents=True)
    report = {
        "protocol_version": "n8n-sidecar-deepseek-ab-v1",
        "context_pruner_version": context_pruner_version,
        "n8n_version": args.n8n_version,
        "workflow_sha256": workflow_hash,
        "third_party_host": "n8n",
        "model": args.model,
        "base_url": args.base_url.rstrip("/"),
        "synthetic_data_only": True,
        "planned_model_calls": case_count,
        "maximum_model_api_requests_with_retries": maximum_requests,
        "summary": summary,
        "rows": rows,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"n8n DeepSeek Sidecar 报告已生成：{output_dir}")
    return 0 if summary["all_success"] and summary["all_finalized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
