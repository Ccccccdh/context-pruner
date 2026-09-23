"""Run a real Node.js custom Agent against the lifecycle Sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import __version__ as context_pruner_version
from context_pruner.sidecar import (
    ContextSidecarService,
    SidecarServerConfig,
    create_sidecar_server,
)
from experiments.runners.run_n8n_sidecar_api_experiment import summarize_api_rows
from experiments.runners.run_n8n_sidecar_experiment import PROJECT_ROOT


DEFAULT_SCRIPT = (
    PROJECT_ROOT / "integrations" / "javascript" / "run-custom-agent-experiment.mjs"
)
DEFAULT_CLIENT = (
    PROJECT_ROOT / "integrations" / "javascript" / "context-pruner-sidecar-client.mjs"
)
TASK_COUNT = 3
METHOD_COUNT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用真实 Node.js 自研 Agent 验证 JavaScript Sidecar 客户端"
    )
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--node-executable", type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=160)
    parser.add_argument("--max-api-retries", type=int, default=2)
    parser.add_argument("--max-api-requests", type=int, default=18)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument(
        "--out", type=Path, default=Path("runs/stage5-javascript-custom-agent")
    )
    parser.add_argument("--experiment-id", default="javascript-custom-agent-mock-3x1")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _node_executable(requested: Path | None) -> str:
    if requested:
        resolved = requested.resolve()
        if not resolved.is_file():
            raise RuntimeError(f"Node.js 可执行文件不存在：{resolved}")
        return str(resolved)
    found = shutil.which("node.exe") or shutil.which("node")
    if not found:
        raise RuntimeError("未找到 Node.js；JavaScript 自研 Agent 验证需要 Node 18+")
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_node(
    command: Sequence[str], *, env: Mapping[str, str], timeout: float
) -> dict[str, Any]:
    completed = subprocess.run(
        list(command),
        cwd=PROJECT_ROOT,
        env=dict(env),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        details = "\n".join(
            part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
        )
        raise RuntimeError(f"Node.js Agent 执行失败（exit={completed.returncode}）\n{details}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Node.js Agent 未返回合法 JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise RuntimeError("Node.js Agent 返回结构无效")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats <= 0:
        raise SystemExit("--repeats 必须大于 0")
    if args.max_api_retries < 0:
        raise SystemExit("--max-api-retries 不能小于 0")
    script = args.script.resolve()
    if not script.is_file() or not DEFAULT_CLIENT.is_file():
        raise SystemExit("JavaScript Agent 脚本或 Sidecar 客户端不存在")
    node = _node_executable(args.node_executable)
    cases = TASK_COUNT * METHOD_COUNT * args.repeats
    maximum_requests = cases * (args.max_api_retries + 1) if args.mode == "api" else 0
    if maximum_requests > args.max_api_requests:
        raise SystemExit(
            f"最坏情况 API 请求数 {maximum_requests} 超过 --max-api-requests={args.max_api_requests}"
        )
    plan = {
        "host": "custom-javascript-agent",
        "runtime": "node",
        "mode": args.mode,
        "tasks": TASK_COUNT,
        "methods": ["none", "pruner_v1"],
        "repeats": args.repeats,
        "planned_model_calls": cases if args.mode == "api" else 0,
        "maximum_model_api_requests_with_retries": maximum_requests,
        "model": args.model if args.mode == "api" else None,
        "synthetic_data_only": True,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未读取密钥、未启动 Sidecar、未执行 Node.js Agent。")
        return 0
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    api_key = ""
    if args.mode == "api":
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        if not api_key:
            raise SystemExit("DEEPSEEK_API_KEY or OPENAI_API_KEY is not set")

    output_dir = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    if output_dir.exists():
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output_dir}")

    sidecar_token = secrets.token_urlsafe(24)
    server_config = SidecarServerConfig(host="127.0.0.1", port=0, auth_token=sidecar_token)
    service = ContextSidecarService(server_config)
    server = create_sidecar_server(server_config, service=service)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address[:2]
    try:
        env = os.environ.copy()
        env.update(
            {
                "CONTEXT_PRUNER_BASE_URL": f"http://{host}:{port}",
                "CONTEXT_PRUNER_AUTH_TOKEN": sidecar_token,
            }
        )
        if args.mode == "api":
            env["MODEL_API_KEY"] = api_key
        command = [
            node,
            str(script),
            "--mode",
            args.mode,
            "--repeats",
            str(args.repeats),
            "--model",
            args.model,
            "--model-base-url",
            args.base_url.rstrip("/"),
            "--max-output-tokens",
            str(args.max_output_tokens),
            "--max-api-retries",
            str(args.max_api_retries),
        ]
        payload = _run_node(command, env=env, timeout=args.timeout)
        rows = payload["rows"]
        lifecycle_probe = payload.get("lifecycle_probe")
        if not isinstance(lifecycle_probe, Mapping) or not lifecycle_probe.get("success"):
            raise RuntimeError(f"JavaScript 客户端生命周期探针失败：{lifecycle_probe}")
        if len(rows) != cases or service.session_count != cases:
            raise RuntimeError(
                f"JavaScript Agent 结果不完整：预期 {cases}，返回 {len(rows)}，"
                f"Sidecar 会话 {service.session_count}"
            )
        summary = summarize_api_rows(rows)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)

    output_dir.mkdir(parents=True)
    report = {
        "protocol_version": "javascript-custom-agent-sidecar-v1",
        "context_pruner_version": context_pruner_version,
        "third_party_host": "custom-javascript-agent",
        "node_executable": Path(node).name,
        "client_sha256": _sha256(DEFAULT_CLIENT),
        "runner_sha256": _sha256(script),
        "mode": args.mode,
        "model": args.model if args.mode == "api" else None,
        "base_url": args.base_url.rstrip("/") if args.mode == "api" else None,
        "synthetic_data_only": True,
        "planned_model_calls": cases if args.mode == "api" else 0,
        "maximum_model_api_requests_with_retries": maximum_requests,
        "lifecycle_probe": dict(lifecycle_probe),
        "summary": summary,
        "rows": rows,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"JavaScript 自研 Agent 报告已生成：{output_dir}")
    return 0 if summary["all_success"] and summary["all_finalized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
