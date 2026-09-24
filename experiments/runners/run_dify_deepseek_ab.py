"""Paired Dify-hosted DeepSeek answer-quality validation for the Sidecar bridge.

Only synthetic messages are sent. The Dify workflow owns the single model call
and explicit Sidecar lifecycle for each case; the runner keeps answer keys local.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from experiments.runners.run_n8n_sidecar_experiment import PROJECT_ROOT


DSL = PROJECT_ROOT / "integrations" / "dify" / "context_pruner_deepseek_ab.yml"
TASKS = (
    (
        "incident",
        "Current verified record: incident_id=INC-901; rollback_trigger=true. "
        "Report the incident_id and rollback_trigger as two key=value fields.",
        (r"\bincident_id\s*=\s*INC-901\b", r"\brollback_trigger\s*=\s*true\b"),
    ),
    (
        "release",
        "Current verified record: release_id=VEGA-31; security_gate=failed. "
        "Report the release_id and security_gate as two key=value fields.",
        (r"\brelease_id\s*=\s*VEGA-31\b", r"\bsecurity_gate\s*=\s*failed\b"),
    ),
    (
        "migration",
        "Current verified record: migration_id=CYGNUS-4; target_region=ap-south. "
        "Report the migration_id and target_region as two key=value fields.",
        (r"\bmigration_id\s*=\s*CYGNUS-4\b", r"\btarget_region\s*=\s*ap-south\b"),
    ),
)
METHODS = ("none", "pruner_v1")
DIVERSE_TASKS = (
    (
        "verified_tool",
        "Answer the final request using the latest verified incident ledger result.",
        (
            ("assistant", "I queried the incident ledger for INC-517."),
            ("tool", "incident_ledger: incident_id=INC-517; record_status=verified; owner_team=delta; rollback_trigger=true."),
            ("user", "For verified incident INC-517, return owner_team and rollback_trigger as two key=value fields."),
        ),
        (r"\bowner_team\s*=\s*delta\b", r"\brollback_trigger\s*=\s*true\b"),
        ("owner_team=delta", "rollback_trigger=true"),
    ),
    (
        "corrected_tool",
        "Use the verified release snapshot, not a superseded snapshot.",
        (
            ("assistant", "I queried the release registry for ATLAS-8."),
            ("tool", "release_registry snapshot=1: release_id=ATLAS-8; record_status=superseded; security_gate=passed."),
            ("assistant", "The first snapshot was superseded; I queried again."),
            ("tool", "release_registry snapshot=2: release_id=ATLAS-8; record_status=verified; security_gate=failed."),
            ("user", "From the verified ATLAS-8 snapshot, return release_id and security_gate as two key=value fields."),
        ),
        (r"\brelease_id\s*=\s*ATLAS-8\b", r"\bsecurity_gate\s*=\s*failed\b"),
        ("record_status=verified", "security_gate=failed"),
    ),
    (
        "two_source_join",
        "Join the two verified tool results for the requested migration.",
        (
            ("assistant", "I queried asset registry and region policy for ORION-7."),
            ("tool", "asset_registry: migration_id=ORION-7; record_status=verified; owner_team=platform."),
            ("tool", "region_policy: migration_id=ORION-7; record_status=verified; target_region=ap-south."),
            ("user", "For ORION-7, return owner_team and target_region as two key=value fields."),
        ),
        (r"\bowner_team\s*=\s*platform\b", r"\btarget_region\s*=\s*ap-south\b"),
        ("owner_team=platform", "target_region=ap-south"),
    ),
    (
        "arithmetic",
        "Compute the requested total from the verified batch counters.",
        (
            ("assistant", "I queried the verified batch counter."),
            ("tool", "batch_counter: batch_id=BATCH-42; record_status=verified; succeeded=17; failed=3."),
            ("user", "Return batch_id and total_items as two key=value fields. Compute total_items as succeeded plus failed."),
        ),
        (r"\bbatch_id\s*=\s*BATCH-42\b", r"\btotal_items\s*=\s*20\b"),
        ("succeeded=17", "failed=3"),
    ),
    (
        "error_retry",
        "Use the successful verified retry, not the timed-out attempt.",
        (
            ("assistant", "I checked approval for ticket TCK-88."),
            ("tool", "approval_service attempt=1: status=timeout; approval=unknown; ticket_id=TCK-88."),
            ("assistant", "The first call timed out; I retried the approval lookup."),
            ("tool", "approval_service attempt=2: status=verified; approval=denied; ticket_id=TCK-88."),
            ("user", "From the successful retry, return ticket_id and approval as two key=value fields."),
        ),
        (r"\bticket_id\s*=\s*TCK-88\b", r"\bapproval\s*=\s*denied\b"),
        ("status=verified", "approval=denied"),
    ),
    (
        "policy_filter",
        "Choose the allowed region for restricted data using verified policy results.",
        (
            ("assistant", "I checked two candidate regions for restricted data."),
            ("tool", "region_policy: record_status=verified; region=eu-west; data_class=restricted; allowed=true."),
            ("tool", "region_policy: record_status=verified; region=ap-south; data_class=restricted; allowed=false."),
            ("user", "Return chosen_region and data_class as two key=value fields for the allowed restricted-data region."),
        ),
        (r"\bchosen_region\s*=\s*eu-west\b", r"\bdata_class\s*=\s*restricted\b"),
        ("region=eu-west", "allowed=true", "region=ap-south", "allowed=false"),
    ),
)


def _visible_answer(answer: str) -> str:
    """Score only the answer after a leading reasoning block, never its contents."""
    opening = re.match(r"\s*<think\b[^>]*>", answer, re.IGNORECASE)
    if opening:
        closing = re.search(r"</think\s*>", answer[opening.end():], re.IGNORECASE)
        if closing is None:
            return ""
        answer = answer[opening.end() + closing.end():]
    return answer.strip()


def _matches_answer(answer: str, patterns: Sequence[str]) -> bool:
    """Require the requested fields once each in visible answer text."""
    fields = re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([A-Za-z0-9_-]+)\b", answer)
    if len(fields) != len(patterns) or len({key.lower() for key, _ in fields}) != len(patterns):
        return False
    return all(re.search(pattern, answer, re.IGNORECASE) for pattern in patterns)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dify DeepSeek 模型节点的配对验证")
    parser.add_argument("--dify-base-url", default="https://api.dify.ai/v1")
    parser.add_argument("--api-key-env", default="DIFY_DEEPSEEK_API_KEY")
    parser.add_argument("--suite", choices=("core", "diverse"), default="core")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-workflow-runs", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--out", type=Path, default=Path("runs/stage5-dify-deepseek-ab"))
    parser.add_argument("--experiment-id")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--plan", action="store_true")
    return parser


def _history(task_id: str, current: str) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": "Use the latest verified record; older drafts are superseded."}]
    for index in range(14):
        old = {
            "incident": f"incident_id=INC-{100 + index}; rollback_trigger=false",
            "release": f"release_id=VEGA-{index}; security_gate=passed",
            "migration": f"migration_id=CYGNUS-{index}; target_region=eu-west",
        }[task_id]
        messages.append({"role": "assistant", "content": (f"Superseded draft {index}: {old}. ") * 3})
        messages.append({"role": "user", "content": (f"Archived request {index}: review old record {old}. ") * 3})
    messages.append({"role": "user", "content": current})
    return messages


def _diverse_history(tail: Sequence[tuple[str, str]]) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": "Archived planning notes are non-authoritative. Use later verified tool results and the final user request."}]
    for index in range(10):
        messages.append({"role": "assistant", "content": (f"Archived simulation {index}: region=obsolete-{index}; approval=pending; record_status=draft. ") * 5})
        messages.append({"role": "user", "content": (f"Historical planning note {index}: this draft was never verified. ") * 3})
    messages.extend({"role": role, "content": content} for role, content in tail)
    return messages


def _selected_tasks(suite: str) -> list[tuple[str, str, list[dict[str, str]], tuple[str, ...]]]:
    if suite == "core":
        return [(task_id, current, _history(task_id, current), patterns) for task_id, current, patterns in TASKS]
    return [(task_id, state, _diverse_history(tail), patterns)
            for task_id, state, tail, patterns, _ in DIVERSE_TASKS]


def _run_workflow(endpoint: str, api_key: str, inputs: Mapping[str, str], timeout: float) -> dict[str, Any]:
    body = {
        "inputs": dict(inputs),
        "response_mode": "blocking",
        "user": "context-pruner-dify-deepseek-ab",
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
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
                values = (failure.get("code"), failure.get("message"))
                detail = ": ".join(value for value in values if isinstance(value, str))[:300]
                detail = detail.replace(api_key, "[redacted]")
        except (ValueError, UnicodeDecodeError):
            pass
        if not detail:
            content_type = error.headers.get("Content-Type", "unknown").split(";", 1)[0]
            detail = f"non-JSON response; content-type={content_type}"
        raise RuntimeError(f"Dify workflow HTTP {error.code}: {detail}") from error
    if not isinstance(raw, Mapping):
        raise RuntimeError("Dify workflow returned a non-object response")
    data = raw.get("data")
    if not isinstance(data, Mapping) or data.get("status") != "succeeded":
        status = data.get("status") if isinstance(data, Mapping) else "missing"
        reason = str(data.get("error") or "").replace(api_key, "[redacted]")[:1200] if isinstance(data, Mapping) else ""
        run_id = raw.get("workflow_run_id")
        raise RuntimeError(f"Dify workflow status={status} run_id={run_id}: {reason}")
    outputs = data.get("outputs")
    if not isinstance(outputs, Mapping):
        raise RuntimeError("Dify workflow response has no outputs")
    answer = outputs.get("answer")
    if not isinstance(answer, str):
        raise RuntimeError("Dify workflow response has no answer string")
    for field in ("full_context_tokens", "served_context_tokens", "budget_violations"):
        if not isinstance(outputs.get(field), (int, float)) or isinstance(outputs[field], bool):
            raise RuntimeError(f"Dify workflow output {field} is missing or nonnumeric")
    for field in ("finalized", "deleted"):
        if not isinstance(outputs.get(field), bool):
            raise RuntimeError(f"Dify workflow output {field} is missing or nonboolean")
    return {
        "workflow_run_id": raw.get("workflow_run_id"),
        "answer": answer,
        "total_model_tokens": data.get("total_tokens"),
        "full_context_tokens": int(outputs["full_context_tokens"]),
        "served_context_tokens": int(outputs["served_context_tokens"]),
        "budget_violations": int(outputs["budget_violations"]),
        "finalized": outputs["finalized"],
        "deleted": outputs["deleted"],
    }


def _load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    keys = [(row["task_id"], row["repeat"], row["method"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise RuntimeError("results.jsonl contains duplicate case keys")
    return rows


def _summary(rows: list[dict[str, Any]], repeats: int, tasks: Sequence[tuple[str, str, list[dict[str, str]], tuple[str, ...]]]) -> dict[str, Any]:
    indexed = {(row["task_id"], row["repeat"], row["method"]): row for row in rows}
    paired_view_savings = []
    paired_model_savings = []
    both_success = 0
    treated_regressions = 0
    treated_improvements = 0
    per_task = {task_id: {"baseline_success_n": 0, "treated_success_n": 0}
                for task_id, _, _, _ in tasks}
    for repeat in range(repeats):
        for task_id, _, _, _ in tasks:
            base = indexed[(task_id, repeat, "none")]
            treated = indexed[(task_id, repeat, "pruner_v1")]
            base_success = bool(base["success"])
            treated_success = bool(treated["success"])
            both_success += base_success and treated_success
            treated_regressions += base_success and not treated_success
            treated_improvements += not base_success and treated_success
            per_task[task_id]["baseline_success_n"] += base_success
            per_task[task_id]["treated_success_n"] += treated_success
            base_view = base["served_context_tokens"]
            if base_view <= 0:
                raise RuntimeError("baseline served context tokens must be positive")
            paired_view_savings.append((base_view - treated["served_context_tokens"]) / base_view)
            base_tokens = base["total_model_tokens"]
            treated_tokens = treated["total_model_tokens"]
            if isinstance(base_tokens, (int, float)) and isinstance(treated_tokens, (int, float)) and base_tokens > 0:
                paired_model_savings.append((base_tokens - treated_tokens) / base_tokens)
    return {
        "case_count": len(rows),
        "paired_n": len(paired_view_savings),
        "baseline_success_n": sum(row["success"] for row in rows if row["method"] == "none"),
        "treated_success_n": sum(row["success"] for row in rows if row["method"] == "pruner_v1"),
        "paired_both_success_n": both_success,
        "paired_treated_regression_n": treated_regressions,
        "paired_treated_improvement_n": treated_improvements,
        "per_task": per_task,
        "baseline_budget_violations": sum(row["budget_violations"] for row in rows if row["method"] == "none"),
        "treated_budget_violations": sum(row["budget_violations"] for row in rows if row["method"] == "pruner_v1"),
        "all_finalized": all(row["finalized"] for row in rows),
        "all_deleted": all(row["deleted"] for row in rows),
        "paired_served_context_savings_rate_mean": sum(paired_view_savings) / len(paired_view_savings),
        "paired_total_model_token_savings_rate_mean": (
            sum(paired_model_savings) / len(paired_model_savings) if paired_model_savings else None
        ),
        "paired_total_model_token_n": len(paired_model_savings),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.repeats <= 0 or args.max_workflow_runs <= 0 or args.timeout <= 0:
        raise SystemExit("repeats, max-workflow-runs and timeout must be positive")
    if any(marker in args.dify_base_url for marker in "[]()"):
        raise SystemExit("dify-base-url must be a plain URL, not a Markdown link")
    if not args.dify_base_url.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
        raise SystemExit("Dify endpoint must use HTTPS unless it is loopback")
    tasks = _selected_tasks(args.suite)
    planned = len(tasks) * len(METHODS) * args.repeats
    if planned > args.max_workflow_runs:
        raise SystemExit(f"planned workflow runs {planned} exceed --max-workflow-runs={args.max_workflow_runs}")
    experiment_id = args.experiment_id or (
        "dify-deepseek-diverse-6x1-v1" if args.suite == "diverse" else "dify-deepseek-ab-3x1-v2"
    )
    manifest = {
        "protocol_version": "dify-deepseek-diverse-v1" if args.suite == "diverse" else "dify-deepseek-ab-v2",
        "dify_base_url": args.dify_base_url.rstrip("/"),
        "dsl_sha256": hashlib.sha256(DSL.read_bytes()).hexdigest(),
        "tasks": [task_id for task_id, _, _, _ in tasks],
        "methods": list(METHODS),
        "repeats": args.repeats,
        "seed": args.seed,
        "planned_workflow_runs": planned,
        "maximum_model_calls": planned,
        "synthetic_data_only": True,
    }
    if args.suite == "diverse":
        fixture = json.dumps(tasks, ensure_ascii=False, separators=(",", ":"))
        manifest["suite"] = "diverse"
        manifest["taskset_sha256"] = hashlib.sha256(fixture.encode("utf-8")).hexdigest()
        manifest["scorer_version"] = "visible-exact-two-fields-v1"
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if args.plan:
        print("计划模式：未读取密钥、未请求 Dify 或 DeepSeek、未创建报告。")
        return 0
    if not args.confirm_send_synthetic_data:
        raise SystemExit("Dify/DeepSeek execution requires --confirm-send-synthetic-data")
    api_key = os.getenv(args.api_key_env) or ""
    if not api_key:
        raise SystemExit(f"{args.api_key_env} is not set")
    output = (PROJECT_ROOT / args.out / experiment_id).resolve()
    manifest_path = output / "manifest.json"
    rows_path = output / "results.jsonl"
    report_path = output / "report.json"
    if output.exists() and not args.resume:
        raise SystemExit(f"实验目录已有结果，拒绝覆盖：{output}")
    if args.resume:
        if not manifest_path.exists() or json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise SystemExit("resume manifest missing or mismatched")
        if report_path.exists():
            raise SystemExit("实验已有完整报告，不需要 resume")
    else:
        output.mkdir(parents=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = _load_rows(rows_path)
    completed = {(row["task_id"], row["repeat"], row["method"]) for row in rows}
    endpoint = args.dify_base_url.rstrip("/") + "/workflows/run"
    order = [(task_id, state, messages, patterns, repeat, method)
             for repeat in range(args.repeats)
             for task_id, state, messages, patterns in tasks
             for method in METHODS]
    random.Random(args.seed).shuffle(order)
    for task_id, state, messages, patterns, repeat, method in order:
        key = (task_id, repeat, method)
        if key in completed:
            print(f"skip {task_id} r{repeat:02d} {method}")
            continue
        session_id = f"dify:llm:{task_id}:r{repeat}:{method}:{uuid.uuid4().hex[:8]}"
        print(f"start {task_id} r{repeat:02d} {method}: session_id={session_id}", flush=True)
        start = time.monotonic()
        result = _run_workflow(endpoint, api_key, {
            "session_id": session_id,
            "method": method,
            "task_state": state,
            "messages_json": json.dumps(messages, ensure_ascii=False),
        }, args.timeout)
        result["answer"] = _visible_answer(result["answer"])
        row = {
            "task_id": task_id,
            "repeat": repeat,
            "method": method,
            "session_id": session_id,
            "success": _matches_answer(result["answer"], patterns),
            "latency_seconds": time.monotonic() - start,
            **result,
        }
        with rows_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows.append(row)
        print(f"{task_id} r{repeat:02d} {method}: success={row['success']} total_model_tokens={row['total_model_tokens']}")
    summary = _summary(rows, args.repeats, tasks)
    report_path.write_text(json.dumps({"manifest": manifest, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Dify DeepSeek 配对报告已生成：{output}")
    return 0 if summary["all_finalized"] and summary["all_deleted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
