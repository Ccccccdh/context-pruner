"""Command-line interface for offline context compression and state resume."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .middleware import ContextPrunerMiddleware
from .plugin import ContextPluginConfig
from .types import ContextBudget
from .adapters import AgentSurface, assess_agent_surface, list_adapter_descriptors


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="context-pruner")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compress = subparsers.add_parser(
        "compress",
        help="compress a JSON message history without calling an external model",
    )
    compress.add_argument("--input", required=True, help="input JSON file")
    compress.add_argument("--output", required=True, help="output JSON file")
    compress.add_argument("--state-in", help="optional middleware state JSON")
    compress.add_argument("--state-out", help="optional resumed state output JSON")
    compress.add_argument("--task-state", default="")
    compress.add_argument("--method", choices=("none", "pruner_v0", "pruner_v1"), default="pruner_v1")
    compress.add_argument("--soft", type=int, default=6000)
    compress.add_argument("--hard", type=int, default=8000)
    compress.add_argument("--target", type=int, default=5000)
    compress.add_argument("--reserved-tokens", type=int, default=0)
    adapters = subparsers.add_parser(
        "adapters",
        help="list implemented Agent categories, integration levels, and capabilities",
    )
    adapters.add_argument("--json", action="store_true", dest="as_json")
    adapters.add_argument("--include-planned", action="store_true")
    assess = subparsers.add_parser(
        "assess",
        help="assess the safest integration level for an Agent surface",
    )
    assess.add_argument("--full-history", action="store_true")
    assess.add_argument("--pre-model-hook", action="store_true")
    assess.add_argument("--tool-events", action="store_true")
    assess.add_argument("--state-persistence", action="store_true")
    assess.add_argument("--configurable-model-endpoint", action="store_true")
    assess.add_argument("--exported-history", action="store_true")
    assess.add_argument("--multi-agent", action="store_true")
    assess.add_argument("--server-managed-history", action="store_true")
    assess.add_argument("--json", action="store_true", dest="as_json")
    serve = subparsers.add_parser(
        "serve",
        help="run the language-neutral local JSON/HTTP lifecycle sidecar",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--max-request-bytes", type=int, default=1_048_576)
    serve.add_argument("--max-sessions", type=int, default=128)
    serve.add_argument("--auth-token-env", default="CONTEXT_PRUNER_AUTH_TOKEN")
    serve.add_argument("--method", choices=("none", "pruner_v0", "pruner_v1"), default="pruner_v1")
    serve.add_argument("--soft", type=int, default=6000)
    serve.add_argument("--hard", type=int, default=8000)
    serve.add_argument("--target", type=int, default=5000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "adapters":
        descriptors = list_adapter_descriptors(include_planned=args.include_planned)
        data = [descriptor.to_dict() for descriptor in descriptors]
        if args.as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            for item in data:
                status = "implemented" if item["implemented"] else "planned"
                frameworks = ", ".join(item["frameworks"])
                print(
                    f"{item['adapter_id']}: category={item['category']} "
                    f"level={item['integration_level']} status={status} "
                    f"validation={item['validation_level']} frameworks={frameworks}"
                )
        return 0
    if args.command == "assess":
        assessment = assess_agent_surface(
            AgentSurface(
                full_history_visible=args.full_history,
                pre_model_hook=args.pre_model_hook,
                tool_events_visible=args.tool_events,
                state_persistence=args.state_persistence,
                configurable_model_endpoint=args.configurable_model_endpoint,
                exported_history=args.exported_history,
                multi_agent=args.multi_agent,
                server_managed_history=args.server_managed_history,
            )
        )
        data = assessment.to_dict()
        if args.as_json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(
                f"level={data['integration_level']} "
                f"automatic={str(data['automatic_context_control']).lower()}"
            )
            for reason in data["reasons"]:
                print(f"reason: {reason}")
            for constraint in data["safety_constraints"]:
                print(f"constraint: {constraint}")
        return 0
    if args.command == "serve":
        from .sidecar import SidecarServerConfig, serve_sidecar

        token = os.getenv(args.auth_token_env, "")
        serve_sidecar(
            SidecarServerConfig(
                host=args.host,
                port=args.port,
                auth_token=token,
                max_request_bytes=args.max_request_bytes,
                max_sessions=args.max_sessions,
                default_plugin=ContextPluginConfig(
                    method=args.method,
                    budget=ContextBudget(args.soft, args.hard, args.target),
                ),
            )
        )
        return 0
    if args.command != "compress":  # pragma: no cover - argparse enforces this
        return 2

    payload = _read_json(Path(args.input))
    messages, embedded_task = _extract_messages(payload)
    state = _read_json(Path(args.state_in)) if args.state_in else None
    config = ContextPluginConfig(
        method=args.method,
        budget=ContextBudget(args.soft, args.hard, args.target),
    )
    middleware = ContextPrunerMiddleware.from_state(
        state,
        config=config,
        task_state=args.task_state or embedded_task,
    )
    result = middleware.before_model(
        messages,
        task_state=args.task_state or embedded_task,
        reserved_tokens=args.reserved_tokens,
    )
    output = {
        "messages": result.messages,
        "metrics": result.metrics,
        "lifecycle_state": result.lifecycle_state,
    }
    _write_json(Path(args.output), output)
    if args.state_out:
        _write_json(Path(args.state_out), result.lifecycle_state)
    return 0


def _extract_messages(payload: Any) -> tuple[list[Any], str]:
    if isinstance(payload, list):
        return payload, ""
    if isinstance(payload, dict) and isinstance(payload.get("messages"), list):
        return list(payload["messages"]), str(payload.get("task_state") or "")
    raise ValueError("input JSON must be a message list or an object with a messages list")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
