"""Paired long-context validation across OpenAI-compatible model providers.

Only the synthetic task data loaded from ``--tasks`` is sent to a provider.
Evaluation terms stay local and are never added to model messages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from context_pruner import (
    ContextBudget,
    ContextPluginConfig,
    ContextPrunerMiddleware,
    __version__ as context_pruner_version,
)
from context_pruner.types import estimate_tokens


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TASKS = PROJECT_ROOT / "tasks" / "stage5_provider" / "long_natural_tasks.json"
METHODS = ("none", "pruner_v1")
PROTOCOL_VERSION = "provider-long-natural-v1"
SYNTHETIC_DISCLOSURE = (
    "Only synthetic conversations, identifiers, policies, and tool results are sent. "
    "No workspace file, source code, API key, or evaluation answer list is sent."
)


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    base_url: str
    key_env: str
    deepseek_thinking_disabled: bool = False
    thinking_policy: str = "provider_default"


PROVIDERS = {
    "deepseek": ProviderConfig(
        "deepseek",
        "deepseek-v4-flash",
        "https://api.deepseek.com",
        "DEEPSEEK_API_KEY",
        True,
        "disabled",
    ),
    "zhipu": ProviderConfig(
        "zhipu",
        "GLM-5.3-Flash",
        "https://open.bigmodel.cn/api/paas/v4",
        "ZHIPU_API_KEY",
        False,
        "provider_forced",
    ),
}


class RequestBudget:
    def __init__(self, limit: int, used: int = 0) -> None:
        self.limit = int(limit)
        self.used = int(used)
        if self.limit <= 0 or self.used < 0 or self.used > self.limit:
            raise ValueError("invalid API request budget")

    def consume(self) -> None:
        if self.used >= self.limit:
            raise RuntimeError(f"API request cap reached ({self.used}/{self.limit})")
        self.used += 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="跨模型长自然任务配对实验")
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="deepseek")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--task-ids", default="")
    parser.add_argument("--methods", default="none,pruner_v1")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--soft-limit", type=int, default=1400)
    parser.add_argument("--hard-limit", type=int, default=1800)
    parser.add_argument("--target", type=int, default=1000)
    parser.add_argument("--reserved-tokens", type=int, default=180)
    parser.add_argument("--max-output-tokens", type=int, default=512)
    parser.add_argument("--max-api-retries", type=int, default=2)
    parser.add_argument("--retry-base-delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--max-api-requests", type=int, default=72)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-provider-long"))
    parser.add_argument("--experiment-id", default="provider-long-natural-6x1")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def resolve_provider(args: argparse.Namespace) -> ProviderConfig:
    default = PROVIDERS[args.provider]
    return ProviderConfig(
        name=default.name,
        model=args.model or default.model,
        base_url=(args.base_url or default.base_url).rstrip("/"),
        key_env=args.api_key_env or default.key_env,
        deepseek_thinking_disabled=default.deepseek_thinking_disabled,
        thinking_policy=default.thinking_policy,
    )


def load_tasks(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("task file must contain a non-empty JSON list")
    required = {"task_id", "domain", "system", "request", "final_prompt", "tool_results", "expected_terms"}
    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in payload:
        task = dict(raw)
        missing = required - task.keys()
        if missing:
            raise ValueError(f"task is missing fields: {sorted(missing)}")
        task_id = str(task["task_id"])
        if task_id in seen:
            raise ValueError(f"duplicate task_id: {task_id}")
        if len(task["tool_results"]) != 2 or not task["expected_terms"]:
            raise ValueError(f"task {task_id} must contain two tool results and answer terms")
        seen.add(task_id)
        tasks.append(task)
    return tasks


def build_initial_history(task: Mapping[str, Any]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = [{"role": "system", "content": str(task["system"])}]
    # These realistic but explicitly obsolete discussions exercise long-history pruning.
    for index in range(12):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"[OBSOLETE; superseded by the current request] Historical planning note "
                        f"{index + 1}: review an unrelated draft for the {task['domain']} queue."
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        f"Obsolete draft {index + 1}: tentative owner=legacy-{index % 4}, "
                        f"window={8 + index:02d}:00 UTC, status=PENDING. Await verified tools."
                    ),
                },
            ]
        )
    messages.append({"role": "user", "content": str(task["request"])})
    return messages


def build_model_messages(task: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build provider input without ever consulting expected_terms."""
    messages = build_initial_history(task)
    for result in task["tool_results"]:
        messages.append(
            {
                "role": "user",
                "content": f"VERIFIED TOOL RESULT [{result['name']}]: {result['content']}",
            }
        )
    messages.append({"role": "user", "content": str(task["final_prompt"])})
    return messages


def _mock_answer(task: Mapping[str, Any]) -> str:
    evidence = " ".join(str(item["content"]) for item in task["tool_results"])
    domain = str(task["domain"])
    if domain == "incident_response":
        return "INCIDENT id=INC-930 action=ROLLBACK window=01:40 UTC"
    if domain == "release_governance":
        return "RELEASE name=Nova-8 status=BLOCKED owner=secops"
    if domain == "migration_planning":
        return "MIGRATION customer=Orion region=eu-west window=04:15 UTC"
    if domain == "customer_support":
        return "SUPPORT case=CS-440 priority=P1 target=30 minutes status=escalated"
    if domain == "compliance":
        return "TRANSFER dataset=Ledger-X decision=APPROVE destination=sandbox-eu encryption=AES-256"
    if domain == "capacity_management":
        return "CAPACITY cluster=C-19 route=BURST pool=B-7"
    return evidence


def _normalize_messages(messages: Sequence[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, Mapping):
            normalized.append(
                {"role": str(message.get("role", "user")), "content": str(message.get("content", ""))}
            )
        else:
            normalized.append({"role": "user", "content": str(message)})
    return normalized


def _api_request(
    client: Any,
    provider: ProviderConfig,
    messages: Sequence[Mapping[str, str]],
    *,
    max_output_tokens: int,
) -> tuple[str, int, int, int, str]:
    kwargs: dict[str, Any] = {
        "model": provider.model,
        "messages": list(messages),
        "temperature": 0,
        "max_tokens": max_output_tokens,
    }
    if provider.deepseek_thinking_disabled:
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content or ""
    if not content.strip():
        raise RuntimeError("provider returned an empty response")
    usage = response.usage
    completion_details = getattr(usage, "completion_tokens_details", None)
    reasoning_tokens = int(
        getattr(completion_details, "reasoning_tokens", 0) or 0
    )
    return (
        content.strip(),
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
        reasoning_tokens,
        str(getattr(response.choices[0], "finish_reason", "") or ""),
    )


def _is_retryable(error: Exception) -> bool:
    status = getattr(error, "status_code", None)
    return type(error).__name__ in {
        "APIConnectionError", "APITimeoutError", "RateLimitError", "InternalServerError",
        "ConnectError", "ConnectTimeout", "ReadTimeout",
    } or status in {408, 409, 425, 429} or (isinstance(status, int) and 500 <= status < 600)


def run_case(
    task: Mapping[str, Any],
    *,
    repeat: int,
    method: str,
    mode: str,
    provider: ProviderConfig,
    client: Any,
    request_budget: RequestBudget,
    budget: ContextBudget,
    reserved_tokens: int,
    max_output_tokens: int,
    max_api_retries: int,
    retry_base_delay: float,
) -> dict[str, Any]:
    history = build_initial_history(task)
    middleware = ContextPrunerMiddleware(
        ContextPluginConfig(enabled=method != "none", method=method, budget=budget),
        task_state=str(task["request"]),
    )
    # First boundary represents the planning turn before tools are invoked.
    middleware.before_model(history, reserved_tokens=reserved_tokens)
    for result in task["tool_results"]:
        tool_message = {
            "role": "user",
            "content": f"VERIFIED TOOL RESULT [{result['name']}]: {result['content']}",
        }
        history.append(tool_message)
        middleware.after_tool(tool_message)
    history.append({"role": "user", "content": str(task["final_prompt"])})
    hook = middleware.before_model(history, reserved_tokens=reserved_tokens)
    served = _normalize_messages(hook.messages)
    started = time.perf_counter()
    attempts = 0
    error_type = ""
    error_message = ""
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0
    finish_reason = "mock"
    output = ""
    try:
        if mode == "mock":
            output = _mock_answer(task)
            prompt_tokens = sum(estimate_tokens(message["content"]) for message in served)
            completion_tokens = estimate_tokens(output)
        else:
            for attempt in range(max_api_retries + 1):
                request_budget.consume()
                attempts += 1
                try:
                    (
                        output,
                        prompt_tokens,
                        completion_tokens,
                        reasoning_tokens,
                        finish_reason,
                    ) = _api_request(client, provider, served, max_output_tokens=max_output_tokens)
                    break
                except Exception as error:
                    if attempt >= max_api_retries or not _is_retryable(error):
                        raise
                    time.sleep(max(0.0, retry_base_delay) * (2**attempt))
        middleware.after_model({"role": "assistant", "content": output})
    except Exception as error:  # Persist failures so --resume --retry-failed can recover.
        error_type = type(error).__name__
        error_message = str(error)[:500]
        middleware.on_error(error, query=str(task["request"]), recover=True)
    expected = [str(term) for term in task["expected_terms"]]
    missing = [term for term in expected if term.casefold() not in output.casefold()]
    success = not error_type and not missing
    metrics = middleware.finalize({"success": success})
    return {
        "task_id": str(task["task_id"]),
        "domain": str(task["domain"]),
        "repeat": repeat,
        "method": method,
        "provider": provider.name if mode == "api" else "mock",
        "model": provider.model if mode == "api" else "deterministic-mock",
        "success": success,
        "missing_terms": missing,
        "output": output,
        "error_type": error_type,
        "error_message": error_message,
        "latency_seconds": time.perf_counter() - started,
        "api_request_attempts": attempts,
        "provider_prompt_tokens": prompt_tokens,
        "provider_completion_tokens": completion_tokens,
        "provider_reasoning_tokens": reasoning_tokens,
        "finish_reason": finish_reason,
        "history_message_count": len(history),
        "served_message_count": len(served),
        "before_model_calls": int(metrics["before_model_calls"]),
        "full_context_tokens": int(metrics["last_full_context_tokens"]),
        "served_context_tokens": int(metrics["last_served_context_tokens"]),
        "gross_input_savings_rate": float(metrics["gross_input_savings_rate"]),
        "budget_violation_count": int(metrics["model_input_budget_violation_count"]),
        "checkpoint_count": int(metrics["checkpoint_count"]),
        "finalized": True,
    }


def _bootstrap_ci(values: Sequence[float], rounds: int = 2000) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return float(values[0]), float(values[0])
    rng = random.Random(42)
    means = sorted(
        statistics.mean(rng.choice(values) for _ in values) for _ in range(rounds)
    )
    return means[int(rounds * 0.025)], means[min(rounds - 1, int(rounds * 0.975))]


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for method in METHODS:
        selected = [row for row in rows if row["method"] == method]
        if not selected:
            continue
        methods[method] = {
            "n": len(selected),
            "success_rate": statistics.mean(bool(row["success"]) for row in selected),
            "provider_prompt_tokens_mean": statistics.mean(row["provider_prompt_tokens"] for row in selected),
            "provider_completion_tokens_mean": statistics.mean(row["provider_completion_tokens"] for row in selected),
            "provider_reasoning_tokens_mean": statistics.mean(row.get("provider_reasoning_tokens", 0) for row in selected),
            "served_context_tokens_mean": statistics.mean(row["served_context_tokens"] for row in selected),
            "latency_seconds_mean": statistics.mean(row["latency_seconds"] for row in selected),
            "budget_violation_count": sum(row["budget_violation_count"] for row in selected),
        }
    indexed = {(row["task_id"], row["repeat"], row["method"]): row for row in rows}
    savings: list[float] = []
    total_savings: list[float] = []
    completion_changes: list[float] = []
    reasoning_deltas: list[float] = []
    success_deltas: list[float] = []
    for task_id, repeat, method in indexed:
        if method != "none":
            continue
        baseline = indexed[(task_id, repeat, "none")]
        pruned = indexed.get((task_id, repeat, "pruner_v1"))
        if not pruned:
            continue
        before = int(baseline["provider_prompt_tokens"])
        savings.append((before - int(pruned["provider_prompt_tokens"])) / before if before else 0.0)
        baseline_completion = int(baseline["provider_completion_tokens"])
        pruned_completion = int(pruned["provider_completion_tokens"])
        baseline_total = before + baseline_completion
        pruned_total = int(pruned["provider_prompt_tokens"]) + pruned_completion
        total_savings.append(
            (baseline_total - pruned_total) / baseline_total if baseline_total else 0.0
        )
        completion_changes.append(
            (pruned_completion - baseline_completion) / baseline_completion
            if baseline_completion
            else 0.0
        )
        reasoning_deltas.append(
            float(pruned.get("provider_reasoning_tokens", 0))
            - float(baseline.get("provider_reasoning_tokens", 0))
        )
        success_deltas.append(float(bool(pruned["success"])) - float(bool(baseline["success"])))
    low, high = _bootstrap_ci(savings)
    total_low, total_high = _bootstrap_ci(total_savings)
    return {
        "rows": len(rows),
        "all_success": bool(rows) and all(bool(row["success"]) for row in rows),
        "all_finalized": bool(rows) and all(bool(row["finalized"]) for row in rows),
        "methods": methods,
        "paired": {
            "n": len(savings),
            "provider_input_savings_rate_mean": statistics.mean(savings) if savings else 0.0,
            "provider_input_savings_ci_low": low,
            "provider_input_savings_ci_high": high,
            "provider_total_token_savings_rate_mean": statistics.mean(total_savings) if total_savings else 0.0,
            "provider_total_token_savings_ci_low": total_low,
            "provider_total_token_savings_ci_high": total_high,
            "provider_completion_token_change_rate_mean": statistics.mean(completion_changes) if completion_changes else 0.0,
            "provider_reasoning_tokens_delta_mean": statistics.mean(reasoning_deltas) if reasoning_deltas else 0.0,
            "success_delta_mean": statistics.mean(success_deltas) if success_deltas else 0.0,
            "positive_savings_pairs": sum(value > 0 for value in savings),
        },
    }


def _result_key(row: Mapping[str, Any]) -> tuple[str, int, str]:
    return str(row["task_id"]), int(row["repeat"]), str(row["method"])


def _latest_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in rows:
        latest[_result_key(row)] = dict(row)
    return list(latest.values())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _manifest(
    args: argparse.Namespace,
    provider: ProviderConfig,
    tasks: Sequence[Mapping[str, Any]],
    methods: Sequence[str],
    task_path: Path,
) -> dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "created_at": datetime.now().astimezone().isoformat(),
        "context_pruner_version": context_pruner_version,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "task_sha256": hashlib.sha256(task_path.read_bytes()).hexdigest(),
        "mode": args.mode,
        "provider": provider.name if args.mode == "api" else "mock",
        "model": provider.model if args.mode == "api" else "deterministic-mock",
        "base_url": provider.base_url if args.mode == "api" else None,
        "thinking_policy": provider.thinking_policy if args.mode == "api" else "none",
        "key_environment_variable": provider.key_env if args.mode == "api" else None,
        "tasks": [str(task["task_id"]) for task in tasks],
        "methods": list(methods),
        "repeats": args.repeats,
        "budget": {"soft": args.soft_limit, "hard": args.hard_limit, "target": args.target, "reserved": args.reserved_tokens},
        "max_output_tokens": args.max_output_tokens,
        "max_api_retries": args.max_api_retries,
        "max_api_requests": args.max_api_requests,
        "synthetic_data_only": True,
        "evaluation_answer_terms_disclosed": False,
    }


def _resume_compatible(existing: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    ignored = {"created_at", "runner_sha256"}
    return {k: v for k, v in existing.items() if k not in ignored} == {
        k: v for k, v in current.items() if k not in ignored
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    methods = tuple(part.strip() for part in args.methods.split(",") if part.strip())
    if not methods or set(methods) - set(METHODS):
        raise SystemExit("--methods only supports none,pruner_v1")
    if args.repeats <= 0 or args.max_api_retries < 0:
        raise SystemExit("--repeats must be positive and --max-api-retries non-negative")
    if not (0 < args.target <= args.soft_limit <= args.hard_limit):
        raise SystemExit("budget must satisfy 0 < target <= soft <= hard")
    provider = resolve_provider(args)
    if any(marker in provider.base_url for marker in "[]()"):
        raise SystemExit("base-url must be a plain URL, not a Markdown link")
    task_path = args.tasks.resolve()
    tasks = load_tasks(task_path)
    selected = {item.strip() for item in args.task_ids.split(",") if item.strip()}
    if selected:
        unknown = selected - {str(task["task_id"]) for task in tasks}
        if unknown:
            raise SystemExit(f"unknown task ids: {sorted(unknown)}")
        tasks = [task for task in tasks if str(task["task_id"]) in selected]
    plan: list[tuple[Mapping[str, Any], int, str]] = []
    for repeat in range(args.repeats):
        for index, task in enumerate(tasks):
            order = list(methods)
            if (repeat * len(tasks) + index) % 2:
                order.reverse()
            plan.extend((task, repeat, method) for method in order)
    maximum = len(plan) * (args.max_api_retries + 1) if args.mode == "api" else 0
    print(SYNTHETIC_DISCLOSURE)
    print(json.dumps({
        "provider": provider.name, "model": provider.model, "tasks": len(tasks),
        "executions": len(plan), "planned_requests": len(plan) if args.mode == "api" else 0,
        "worst_case_requests": maximum,
    }, ensure_ascii=False))
    if maximum > args.max_api_requests:
        raise SystemExit(f"worst-case requests {maximum} exceed --max-api-requests={args.max_api_requests}")
    if args.plan:
        print("计划模式：未读取密钥、未创建输出目录、未发送 API 请求。")
        return 0
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    api_key = os.getenv(provider.key_env, "") if args.mode == "api" else ""
    if args.mode == "api" and not api_key:
        raise SystemExit(f"{provider.key_env} is not set in this process")

    output = (PROJECT_ROOT / args.out / args.experiment_id).resolve()
    results_path = output / "results.jsonl"
    manifest_path = output / "run_manifest.json"
    manifest = _manifest(args, provider, tasks, methods, task_path)
    if output.exists() and any(output.iterdir()) and not args.resume:
        raise SystemExit(f"experiment directory already exists: {output}")
    if args.resume and manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not _resume_compatible(existing, manifest):
            raise SystemExit("resume manifest does not match current arguments")
        manifest["created_at"] = existing["created_at"]
        manifest["resumed_at"] = datetime.now().astimezone().isoformat()
    _write_json(manifest_path, manifest)
    raw_rows = _load_jsonl(results_path) if args.resume else []
    latest = _latest_rows(raw_rows)
    completed = {
        _result_key(row) for row in latest if not args.retry_failed or bool(row.get("success"))
    }
    used = sum(int(row.get("api_request_attempts", 0)) for row in raw_rows)
    request_budget = RequestBudget(args.max_api_requests, used=used)
    client = None
    if args.mode == "api":
        try:
            from openai import OpenAI
        except ImportError as error:
            raise SystemExit("missing openai dependency: python -m pip install openai") from error
        client = OpenAI(api_key=api_key, base_url=provider.base_url, max_retries=0, timeout=args.timeout)
    budget = ContextBudget(args.soft_limit, args.hard_limit, args.target)
    try:
        for task, repeat, method in plan:
            key = (str(task["task_id"]), repeat, method)
            if key in completed:
                print(f"skip {key[0]} r{repeat:02d} {method}")
                continue
            row = run_case(
                task, repeat=repeat, method=method, mode=args.mode, provider=provider,
                client=client, request_budget=request_budget, budget=budget,
                reserved_tokens=args.reserved_tokens, max_output_tokens=args.max_output_tokens,
                max_api_retries=args.max_api_retries, retry_base_delay=args.retry_base_delay,
            )
            _append_jsonl(results_path, row)
            raw_rows.append(row)
            completed.add(key)
            print(
                f"{key[0]:32} r{repeat:02d} {method:10} success={str(row['success']):5} "
                f"prompt={row['provider_prompt_tokens']} requests={request_budget.used}/{request_budget.limit}"
            )
    finally:
        if client is not None:
            client.close()
    rows = sorted(_latest_rows(raw_rows), key=_result_key)
    summary = summarize(rows)
    _write_json(output / "report.json", {"manifest": manifest, "summary": summary, "rows": rows})
    _write_json(output / "request_usage.json", {
        "maximum_api_requests": args.max_api_requests,
        "api_requests_used": request_budget.used,
        "remaining": request_budget.limit - request_budget.used,
    })
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"跨模型长上下文报告已生成：{output}")
    return 0 if summary["all_success"] and summary["all_finalized"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
