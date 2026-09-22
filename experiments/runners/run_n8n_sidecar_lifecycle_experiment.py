"""Validate the full Sidecar lifecycle from a real imported n8n workflow."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from typing import Any, Sequence

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
    PROJECT_ROOT / "integrations" / "n8n" / "context_pruner_sidecar_lifecycle.json"
)
WORKFLOW_ID = "context-pruner-sidecar-lifecycle-v1"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="在真实 n8n 中验证 Sidecar 完整生命周期")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--n8n-version", default=DEFAULT_N8N_VERSION)
    parser.add_argument("--node-executable", type=Path)
    parser.add_argument("--n8n-cli-script", type=Path)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-n8n-sidecar"))
    parser.add_argument("--experiment-id", default="n8n-sidecar-full-lifecycle-v1")
    parser.add_argument("--plan", action="store_true")
    return parser


def _render_lifecycle_workflow(
    template: Path,
    target: Path,
    *,
    sidecar_url: str,
    sidecar_token: str,
    collector_url: str,
    collector_token: str,
) -> str:
    raw = template.read_text(encoding="utf-8")
    data = json.loads(raw)
    if data.get("id") != WORKFLOW_ID:
        raise RuntimeError(f"工作流 ID 必须为 {WORKFLOW_ID}")
    rendered = raw
    for placeholder, value in {
        "__SIDECAR_URL__": sidecar_url.rstrip("/"),
        "__SIDECAR_TOKEN__": sidecar_token,
        "__COLLECTOR_URL__": collector_url.rstrip("/"),
        "__COLLECTOR_TOKEN__": collector_token,
    }.items():
        rendered = rendered.replace(placeholder, value)
    if "__SIDECAR_" in rendered or "__COLLECTOR_" in rendered:
        raise RuntimeError("工作流仍包含未替换的运行时占位符")
    json.loads(rendered)
    target.write_text(rendered, encoding="utf-8")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


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
        "sidecar_sessions": 2,
        "model_api_requests": 0,
        "boundaries": [
            "before-model",
            "after-tool",
            "after-model",
            "state-export",
            "state-restore",
            "error-recovery",
            "finalize",
        ],
        "launcher": launcher,
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未启动 Sidecar 或 n8n，未发送模型 API 请求。")
        return 0

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
        with tempfile.TemporaryDirectory(prefix="context-pruner-n8n-lifecycle-") as temporary:
            runtime = Path(temporary)
            rendered_workflow = runtime / "workflow.json"
            workflow_hash = _render_lifecycle_workflow(
                workflow,
                rendered_workflow,
                sidecar_url=f"http://{host}:{port}",
                sidecar_token=sidecar_token,
                collector_url=f"http://{collector_host}:{collector_port}",
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
            if len(rows) != 1 or service.session_count != 2:
                raise RuntimeError(
                    f"生命周期结果不完整：回传 {len(rows)}，Sidecar 会话 {service.session_count}"
                )
            result: dict[str, Any] = rows[0]
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)
        collector_server.shutdown()
        collector_server.server_close()
        collector_thread.join(timeout=5)

    output_dir.mkdir(parents=True)
    report = {
        "protocol_version": "n8n-sidecar-full-lifecycle-v1",
        "context_pruner_version": context_pruner_version,
        "n8n_version": args.n8n_version,
        "workflow_sha256": workflow_hash,
        "third_party_host": "n8n",
        "model_api_requests": 0,
        "synthetic_data_only": True,
        "result": result,
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"n8n Sidecar 生命周期报告已生成：{output_dir}")
    return 0 if result.get("success") else 2


if __name__ == "__main__":
    raise SystemExit(main())
