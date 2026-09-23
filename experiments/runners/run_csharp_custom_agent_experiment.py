"""Build and run a real dependency-free C# Agent against the HTTP Sidecar."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import __version__ as context_pruner_version
from context_pruner.sidecar import ContextSidecarService, SidecarServerConfig, create_sidecar_server
from experiments.runners.run_n8n_sidecar_api_experiment import summarize_api_rows
from experiments.runners.run_n8n_sidecar_experiment import PROJECT_ROOT


CSHARP_ROOT = PROJECT_ROOT / "integrations" / "csharp"
CLIENT_PROJECT = CSHARP_ROOT / "ContextPruner.Sidecar" / "ContextPruner.Sidecar.csproj"
CLIENT_SOURCE = CSHARP_ROOT / "ContextPruner.Sidecar" / "ContextPrunerSidecarClient.cs"
EXAMPLE_PROJECT = CSHARP_ROOT / "ContextPruner.Example" / "ContextPruner.Example.csproj"
EXAMPLE_SOURCE = CSHARP_ROOT / "ContextPruner.Example" / "Program.cs"
SOURCES = (CLIENT_PROJECT, CLIENT_SOURCE, EXAMPLE_PROJECT, EXAMPLE_SOURCE)
TASK_COUNT = 3
METHOD_COUNT = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用真实 C# 自研 Agent 验证 Sidecar 客户端")
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--dotnet", type=Path)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-output-tokens", type=int, default=160)
    parser.add_argument("--max-api-retries", type=int, default=2)
    parser.add_argument("--max-api-requests", type=int, default=18)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-csharp-custom-agent"))
    parser.add_argument("--experiment-id", default="csharp-custom-agent-mock-3x1")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _dotnet_tool(requested: Path | None) -> Path:
    if requested:
        candidate = requested.resolve()
        if candidate.is_file():
            return candidate
        raise RuntimeError(f"dotnet executable does not exist: {candidate}")
    executable = shutil.which("dotnet.exe") or shutil.which("dotnet")
    if not executable:
        raise RuntimeError("未找到 .NET 8+ SDK；请安装 SDK 或使用 --dotnet 指定路径")
    return Path(executable)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _publish(dotnet: Path, output: Path) -> Path:
    command = [
        str(dotnet), "publish", str(EXAMPLE_PROJECT),
        "-c", "Release", "--nologo", "-o", str(output),
        "-p:RestoreIgnoreFailedSources=true",
    ]
    completed = subprocess.run(
        command, cwd=PROJECT_ROOT, text=True, encoding="utf-8", errors="replace",
        capture_output=True, check=False,
    )
    if completed.returncode:
        details = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        raise RuntimeError(f"C# 客户端编译失败（exit={completed.returncode}）\n{details}")
    assembly = output / "ContextPruner.Example.dll"
    if not assembly.is_file():
        raise RuntimeError(f"C# 编译未生成入口程序集：{assembly}")
    return assembly


def _run_dotnet(command: Sequence[str], env: Mapping[str, str], timeout: float) -> dict[str, Any]:
    completed = subprocess.run(
        list(command), cwd=PROJECT_ROOT, env=dict(env), text=True, encoding="utf-8",
        errors="replace", capture_output=True, timeout=timeout, check=False,
    )
    if completed.returncode:
        details = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        raise RuntimeError(f"C# Agent 执行失败（exit={completed.returncode}）\n{details}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("C# Agent 未返回合法 JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise RuntimeError("C# Agent 返回结构无效")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats <= 0 or args.max_api_retries < 0:
        raise SystemExit("repeats must be positive and retries non-negative")
    if any(marker in args.base_url for marker in "[]()"):
        raise SystemExit("base-url must be a plain URL, not a Markdown link")
    if any(not source.is_file() for source in SOURCES):
        raise SystemExit("C# client source is incomplete")
    dotnet = _dotnet_tool(args.dotnet)
    cases = TASK_COUNT * METHOD_COUNT * args.repeats
    maximum_requests = cases * (args.max_api_retries + 1) if args.mode == "api" else 0
    if maximum_requests > args.max_api_requests:
        raise SystemExit(
            f"worst-case API requests {maximum_requests} exceed --max-api-requests={args.max_api_requests}"
        )
    plan = {
        "host": "custom-csharp-agent",
        "runtime": "dotnet",
        "mode": args.mode,
        "tasks": TASK_COUNT,
        "methods": ["none", "pruner_v1"],
        "repeats": args.repeats,
        "planned_model_calls": cases if args.mode == "api" else 0,
        "maximum_model_api_requests_with_retries": maximum_requests,
        "model": args.model if args.mode == "api" else None,
        "synthetic_data_only": True,
        "dotnet": dotnet.name,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未读取密钥、未编译 C#、未启动 Sidecar、未发送请求。")
        return 0
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    api_key = ""
    if args.mode == "api":
        api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        if not api_key:
            raise SystemExit("DEEPSEEK_API_KEY or OPENAI_API_KEY is not set")
    output = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    if output.exists():
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output}")

    with tempfile.TemporaryDirectory(prefix="context-pruner-csharp-") as temporary:
        publish_dir = Path(temporary) / "publish"
        assembly = _publish(dotnet, publish_dir)
        token = os.urandom(24).hex()
        server_config = SidecarServerConfig(host="127.0.0.1", port=0, auth_token=token)
        service = ContextSidecarService(server_config)
        server = create_sidecar_server(server_config, service=service)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address[:2]
        try:
            env = os.environ.copy()
            env.update({
                "CONTEXT_PRUNER_BASE_URL": f"http://{host}:{port}",
                "CONTEXT_PRUNER_AUTH_TOKEN": token,
                "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                "DOTNET_NOLOGO": "1",
            })
            if args.mode == "api":
                env["MODEL_API_KEY"] = api_key
            command = [
                str(dotnet), str(assembly),
                "--mode", args.mode,
                "--repeats", str(args.repeats),
                "--model", args.model,
                "--model-base-url", args.base_url.rstrip("/"),
                "--max-output-tokens", str(args.max_output_tokens),
                "--max-api-retries", str(args.max_api_retries),
            ]
            payload = _run_dotnet(command, env, args.timeout)
            rows = payload["rows"]
            lifecycle_probe = payload.get("lifecycle_probe")
            if not isinstance(lifecycle_probe, Mapping) or not lifecycle_probe.get("success"):
                raise RuntimeError(f"C# 客户端生命周期探针失败：{lifecycle_probe}")
            if len(rows) != cases or service.session_count != cases:
                raise RuntimeError(
                    f"C# Agent 结果不完整：预期 {cases}，返回 {len(rows)}，Sidecar 会话 {service.session_count}"
                )
            summary = summarize_api_rows(rows)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    output.mkdir(parents=True)
    report = {
        "protocol_version": "csharp-custom-agent-sidecar-v1",
        "context_pruner_version": context_pruner_version,
        "third_party_host": "custom-csharp-agent",
        "dotnet_executable": dotnet.name,
        "source_sha256": {str(source.relative_to(CSHARP_ROOT)): _sha256(source) for source in SOURCES},
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
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"C# 自研 Agent 报告已生成：{output}")
    return 0 if summary["all_success"] and summary["all_finalized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
