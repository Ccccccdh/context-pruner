"""运行记录：每轮一行 JSONL + 每任务一行 summary。"""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import Enum
from pathlib import Path

from .agent import RunRecord, TurnRecord


def write_run(
    out_dir: Path,
    record: RunRecord,
    run_id: str = "0",
    summary_metadata: dict | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    turns_path = out_dir / f"{record.task.task_id}.{run_id}.turns.jsonl"
    with turns_path.open("w", encoding="utf-8") as f:
        for t in record.turns:
            f.write(json.dumps(_turn_line(record, t), ensure_ascii=False) + "\n")

    summary_path = out_dir / f"{record.task.task_id}.{run_id}.summary.json"
    summary = record_summary(record)
    summary.update(summary_metadata or {})
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    events_path = out_dir / f"{record.task.task_id}.{run_id}.events.jsonl"
    with events_path.open("w", encoding="utf-8") as f:
        for event in record.runtime_events:
            f.write(json.dumps({"stream": "runtime", **_jsonable(asdict(event))}, ensure_ascii=False) + "\n")
        for event in record.lifecycle_records:
            f.write(json.dumps({"stream": "lifecycle", **_jsonable(asdict(event))}, ensure_ascii=False) + "\n")


def _turn_line(record: RunRecord, t: TurnRecord) -> dict:
    return {
        "task_id": record.task.task_id,
        "method": record.method,
        "step": t.step,
        "tokens_in": t.tokens_in,
        "full_tokens_in": t.full_tokens_in,
        "input_tokens_saved": max(0, t.full_tokens_in - t.tokens_in),
        "tokens_out": t.tokens_out,
        "reply": t.reply,
        "action": t.action,
        "observation": t.observation,
        "latency": round(t.latency, 4),
        "kirr": round(t.kirr, 4) if t.kirr is not None else None,
        "archive_count": t.archive_count,
        "context_reply": t.context_reply,
        "reply_sanitized": t.reply_sanitized,
        "dropped_output_tokens": t.dropped_output_tokens,
        "tool_ledger_tokens": t.tool_ledger_tokens,
    }


def record_summary(record: RunRecord) -> dict:
    return {
        "task_id": record.task.task_id,
        "scenario": record.task.scenario,
        "method": record.method,
        "success": record.success,
        "partial_score": round(record.partial_score, 4),
        "final_kirr": round(record.final_kirr, 4),
        "recovery_count": record.recovery_count,
        "recovery_events": record.recovery_events,
        "compression_overhead_tokens": record.compression_overhead_tokens,
        "compression_overhead_input_tokens": record.compression_overhead_input_tokens,
        "compression_overhead_output_tokens": record.compression_overhead_output_tokens,
        "full_context_tokens_total": record.full_context_tokens_total,
        "gross_input_tokens_saved": record.gross_input_tokens_saved,
        "net_input_tokens_saved": record.net_input_tokens_saved,
        "net_input_savings_rate": round(record.net_input_savings_rate, 6),
        "peak_context_tokens": record.peak_context_tokens,
        "total_latency": round(record.total_latency, 6),
        "p95_turn_latency": round(record.p95_turn_latency, 6),
        "estimated_agent_cost": round(record.estimated_agent_cost, 8),
        "estimated_compression_cost": round(record.estimated_compression_cost, 8),
        "estimated_total_cost": round(record.estimated_total_cost, 8),
        "archive_count": record.archive_count,
        "recovered_tokens": record.recovered_tokens,
        "recovery_precision": round(record.recovery_precision, 6),
        "recovery_recall": round(record.recovery_recall, 6),
        "recovery_amplification": round(record.recovery_amplification, 6),
        "constraint_adherence": round(record.constraint_adherence, 6),
        "tool_correctness": round(record.tool_correctness, 6),
        "parse_error_count": record.parse_error_count,
        "tool_error_count": record.tool_error_count,
        "repeated_action_count": record.repeated_action_count,
        "post_sufficiency_tool_call_count": record.post_sufficiency_tool_call_count,
        "max_turn_failure": record.max_turn_failure,
        "budget_pressure_count": record.budget_pressure_count,
        "budget_violation_count": record.budget_violation_count,
        "checkpoint_count": record.checkpoint_count,
        "sanitized_output_count": record.sanitized_output_count,
        "sanitized_output_tokens": record.sanitized_output_tokens,
        "tool_ledger_tokens_total": record.tool_ledger_tokens_total,
        "method_config": record.method_config,
        "pricing": record.pricing,
        "lifecycle_event_count": len(record.lifecycle_records),
        "runtime_event_count": len(record.runtime_events),
        "lifecycle_records": [_jsonable(asdict(item)) for item in record.lifecycle_records],
        "runtime_events": [_jsonable(asdict(item)) for item in record.runtime_events],
        "num_turns": len(record.turns),
        "tokens_in_total": record.tokens_in_total,
        "tokens_out_total": record.tokens_out_total,
        "avg_tokens_in_per_turn": round(record.avg_tokens_in_per_turn, 2),
        "final_answer": record.final_answer,
    }


# 保留旧的内部名称，兼容已有调用。
_summary = record_summary


def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value
