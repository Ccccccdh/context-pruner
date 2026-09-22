"""Run the Context-Pruner Sidecar inside a real local n8n workflow.

The workflow is imported into an isolated temporary n8n user folder, executed
through the n8n Server CLI, and removed with the temporary folder afterwards.
No model API or user workspace content is sent by this experiment.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import __version__ as context_pruner_version
from context_pruner.sidecar import (
    ContextSidecarService,
    SidecarServerConfig,
    create_sidecar_server,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOW = PROJECT_ROOT / "integrations" / "n8n" / "context_pruner_sidecar_ab.json"
WORKFLOW_ID = "context-pruner-sidecar-ab-v1"
VALIDATION_NODE = "Validate Preserved Facts"
DEFAULT_N8N_VERSION = "2.6.4"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在真实 n8n CLI 中验证 Context-Pruner HTTP Sidecar A/B 工作流"
    )
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--n8n-version", default=DEFAULT_N8N_VERSION)
    parser.add_argument(
        "--node-executable",
        type=Path,
        help="可选：用于启动固定 n8n CLI 脚本的 Node.js 可执行文件",
    )
    parser.add_argument(
        "--n8n-cli-script",
        type=Path,
        help="可选：已安装 n8n 的 bin/n8n 脚本；必须与 --node-executable 同时使用",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-n8n-sidecar"))
    parser.add_argument("--experiment-id", default="n8n-sidecar-local-ab-v1")
    parser.add_argument("--plan", action="store_true")
    return parser


def _npx_command() -> str:
    command = shutil.which("npx.cmd") or shutil.which("npx")
    if not command:
        raise RuntimeError("未找到 npx；n8n 本地验证需要 Node.js 与 npm/npx")
    return command


def _launcher(args: argparse.Namespace) -> list[str]:
    node = args.node_executable
    script = args.n8n_cli_script
    if bool(node) != bool(script):
        raise RuntimeError("--node-executable 与 --n8n-cli-script 必须同时提供")
    if node and script:
        node = node.resolve()
        script = script.resolve()
        if not node.is_file():
            raise RuntimeError(f"Node.js 可执行文件不存在：{node}")
        if not script.is_file():
            raise RuntimeError(f"n8n CLI 脚本不存在：{script}")
        return [str(node), str(script)]
    return [_npx_command(), "--yes", f"n8n@{args.n8n_version}"]


def _run(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: float,
) -> subprocess.CompletedProcess[str]:
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
        raise RuntimeError(
            f"命令执行失败（exit={completed.returncode}）：{' '.join(command)}\n{details}"
        )
    return completed


def _json_output(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index, char in enumerate(text) if char == "{"]
        value = None
        for start in reversed(starts):
            try:
                candidate = json.loads(text[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                value = candidate
                break
        if value is None:
            raise RuntimeError("n8n --rawOutput 未返回可解析的 JSON")
    if not isinstance(value, dict):
        raise RuntimeError("n8n 执行输出必须是 JSON 对象")
    return value


def extract_validation_rows(execution: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        runs = execution["data"]["resultData"]["runData"][VALIDATION_NODE]
        items = runs[-1]["data"]["main"][0]
    except (KeyError, IndexError, TypeError) as error:
        raise RuntimeError(f"n8n 输出缺少最终节点 {VALIDATION_NODE!r}") from error
    rows: list[dict[str, Any]] = []
    for item in items:
        data = item.get("json") if isinstance(item, Mapping) else None
        if not isinstance(data, Mapping):
            raise RuntimeError("n8n 最终节点包含无效数据项")
        rows.append(dict(data))
    if not rows:
        raise RuntimeError("n8n 最终节点没有返回 A/B 样本")
    return rows


def summarize_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    pairs: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in rows:
        method = str(row.get("method") or "")
        task_id = str(row.get("task_id") or "")
        if method not in {"none", "pruner_v1"} or not task_id:
            raise RuntimeError("n8n 返回了未知任务或方法")
        grouped[method].append(row)
        pairs[task_id][method] = row

    method_summary: dict[str, Any] = {}
    for method, values in sorted(grouped.items()):
        method_summary[method] = {
            "n": len(values),
            "success_rate": sum(bool(row.get("success")) for row in values) / len(values),
            "full_context_tokens_mean": sum(
                int(row.get("full_context_tokens", 0)) for row in values
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

    paired_savings: list[float] = []
    for task_id, methods in sorted(pairs.items()):
        if set(methods) != {"none", "pruner_v1"}:
            raise RuntimeError(f"任务 {task_id} 缺少配对方法")
        baseline = int(methods["none"].get("served_context_tokens", 0))
        treated = int(methods["pruner_v1"].get("served_context_tokens", 0))
        if baseline > 0 and treated > 0:
            paired_savings.append((baseline - treated) / baseline)

    return {
        "case_count": len(rows),
        "paired_n": len(pairs),
        "paired_token_n": len(paired_savings),
        "all_success": all(bool(row.get("success")) for row in rows),
        "transport_error_count": sum(bool(row.get("transport_error")) for row in rows),
        "method_summary": method_summary,
        "paired_input_savings_rate_mean": (
            sum(paired_savings) / len(paired_savings) if paired_savings else None
        ),
        "paired_input_savings_win_rate": (
            sum(value > 0 for value in paired_savings) / len(paired_savings)
            if paired_savings
            else None
        ),
    }


def _render_workflow(
    template: Path,
    target: Path,
    *,
    base_url: str,
    token: str,
    collector_url: str,
    collector_token: str,
) -> str:
    raw = template.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("id") != WORKFLOW_ID:
        raise RuntimeError(f"工作流 ID 必须为 {WORKFLOW_ID}")
    rendered = raw.replace("__SIDECAR_URL__", base_url).replace(
        "__SIDECAR_TOKEN__", token
    )
    rendered = rendered.replace("__COLLECTOR_URL__", collector_url).replace(
        "__COLLECTOR_TOKEN__", collector_token
    )
    if "__SIDECAR_" in rendered or "__COLLECTOR_" in rendered:
        raise RuntimeError("工作流仍包含未替换的运行时占位符")
    target.write_text(rendered, encoding="utf-8")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class _ResultCollector:
    def __init__(self, token: str):
        self.token = token
        self.rows: list[dict[str, Any]] = []
        self.lock = threading.RLock()

    def append(self, row: Mapping[str, Any]) -> None:
        with self.lock:
            self.rows.append(dict(row))

    def snapshot(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(row) for row in self.rows]


def _create_collector_server(collector: _ResultCollector) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {collector.token}"
            if not hmac.compare_digest(supplied, expected):
                self._send(401, {"accepted": False})
                return
            if self.path != "/result":
                self._send(404, {"accepted": False})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("result must be an object")
                collector.append(payload)
            except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"accepted": False})
                return
            self._send(200, {"accepted": True})

        def _send(self, status: int, payload: Mapping[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    return server


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    workflow = args.workflow.resolve()
    if not workflow.is_file():
        raise SystemExit(f"工作流不存在：{workflow}")
    launcher = _launcher(args)
    plan = {
        "host": "n8n",
        "n8n_version": args.n8n_version,
        "workflow": str(workflow),
        "tasks": 3,
        "methods": ["none", "pruner_v1"],
        "executions": 6,
        "model_api_requests": 0,
        "launcher": launcher,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未启动 Sidecar，未执行 n8n，未发送模型 API 请求。")
        return 0

    token = secrets.token_urlsafe(24)
    collector_token = secrets.token_urlsafe(24)
    server_config = SidecarServerConfig(
        host="127.0.0.1", port=0, auth_token=token
    )
    service = ContextSidecarService(server_config)
    server = create_sidecar_server(server_config, service=service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    base_url = f"http://{host}:{port}"
    collector = _ResultCollector(collector_token)
    collector_server = _create_collector_server(collector)
    collector_thread = threading.Thread(
        target=collector_server.serve_forever, daemon=True
    )
    collector_thread.start()
    collector_host, collector_port = collector_server.server_address[:2]
    collector_url = f"http://{collector_host}:{collector_port}"

    try:
        with tempfile.TemporaryDirectory(prefix="context-pruner-n8n-") as temporary:
            runtime = Path(temporary)
            rendered_workflow = runtime / "workflow.json"
            workflow_hash = _render_workflow(
                workflow,
                rendered_workflow,
                base_url=base_url,
                token=token,
                collector_url=collector_url,
                collector_token=collector_token,
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
                    # CLI one-shot execution has no long-lived runner broker.  In
                    # n8n 2.x the external task runner can reject the temporary
                    # Code-node worker with 403, so this reproducibility runner
                    # deliberately uses n8n's supported in-process fallback.
                    "N8N_RUNNERS_ENABLED": "false",
                }
            )
            _run(
                [*launcher, "import:workflow", f"--input={rendered_workflow}"],
                env=env,
                timeout=args.timeout,
            )
            cli_serialization_error = False
            try:
                executed = _run(
                    [*launcher, "execute", f"--id={WORKFLOW_ID}", "--rawOutput"],
                    env=env,
                    timeout=args.timeout,
                )
            except RuntimeError as error:
                rows = collector.snapshot()
                if len(rows) != 6 or service.session_count != 6:
                    raise RuntimeError(
                        f"{error}\nSidecar 实际创建会话数：{service.session_count}；"
                        f"结果回传数：{len(rows)}"
                    ) from error
                cli_serialization_error = True
            else:
                collected = collector.snapshot()
                if len(collected) == 6:
                    rows = collected
                else:
                    execution = _json_output(executed.stdout)
                    rows = extract_validation_rows(execution)
            summary = summarize_rows(rows)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        collector_server.shutdown()
        collector_server.server_close()
        collector_thread.join(timeout=5)

    output_dir = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    if output_dir.exists():
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output_dir}")
    output_dir.mkdir(parents=True)
    report = {
        "protocol_version": "n8n-sidecar-local-ab-v1",
        "context_pruner_version": context_pruner_version,
        "n8n_version": args.n8n_version,
        "workflow_sha256": workflow_hash,
        "third_party_host": "n8n",
        "model_api_requests": 0,
        "synthetic_data_only": True,
        "n8n_cli_serialization_workaround": cli_serialization_error,
        "summary": summary,
        "rows": rows,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"n8n Sidecar 报告已生成：{output_dir}")
    return 0 if summary["all_success"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
