"""Read-only scoring audit for AutoGen team experiment results."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from experiments.runners.run_autogen_team_experiment import (
    _contains_terms,
    _report,
    _valid_prefixed_line,
    load_tasks,
    summarize,
)


def audit(run_dir: Path, *, tasks_path: Path) -> list[dict[str, Any]]:
    task_map = {str(task["task_id"]): task for task in load_tasks(tasks_path)}
    source = run_dir / "results.jsonl"
    if not source.exists():
        raise FileNotFoundError(source)
    audited: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        original = json.loads(line)
        task = task_map[str(original["task_id"])]
        planner_output = str(original.get("planner_output") or "")
        final_output = str(original.get("final_output") or "")
        handoff_correct = _contains_terms(
            planner_output,
            task["expected_handoff_terms"],
        )
        final_correct = _contains_terms(
            final_output,
            task["expected_final_terms"],
        )
        format_correct = _valid_prefixed_line(
            planner_output, "HANDOFF"
        ) and _valid_prefixed_line(final_output, "RESULT")
        row = dict(original)
        row.update(
            {
                "raw_success": bool(original.get("success")),
                "raw_handoff_correct": bool(original.get("handoff_correct")),
                "raw_final_correct": bool(original.get("final_correct")),
                "raw_format_correct": bool(original.get("format_correct")),
                "handoff_correct": handoff_correct,
                "final_correct": final_correct,
                "format_correct": format_correct,
            }
        )
        row["success"] = all(
            (
                not row.get("error_type"),
                bool(row.get("pre_handoff_routing_isolation")),
                bool(row.get("state_roundtrip_ok")),
                bool(row.get("shared_visible")),
                handoff_correct,
                bool(row.get("handoff_preserved")),
                bool(row.get("handoff_target_only")),
                bool(row.get("model_input_privacy")),
                final_correct,
                format_correct,
                int(row.get("restore_failure_count", 0)) == 0,
                int(row.get("resync_count", 0)) == 0,
            )
        )
        row["audit_rule"] = "HANDOFF/RESULT accepts one space or one colon"
        audited.append(row)
    return audited


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument(
        "--tasks", default="tasks/stage5_autogen_team/team_tasks.json"
    )
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    source = run_dir / "results.jsonl"
    source_hash_before = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = audit(run_dir, tasks_path=Path(args.tasks))
    mode = str(rows[0]["mode"]) if rows else "api"
    summary = summarize(rows, mode)
    summary["audit"] = {
        "source": "results.jsonl",
        "raw_rows_unchanged": True,
        "source_sha256": source_hash_before,
        "raw_success_count": sum(bool(row["raw_success"]) for row in rows),
        "audited_success_count": sum(bool(row["success"]) for row in rows),
        "rule": "HANDOFF and RESULT accept either whitespace or colon after prefix",
    }
    (run_dir / "audited_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (run_dir / "audited_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "audited_report.md").write_text(
        _report(summary, rows)
        + "\n审计未修改原始 `results.jsonl`；只接受 `HANDOFF:` 与 `HANDOFF `、"
        "`RESULT:` 与 `RESULT ` 的等价格式。\n",
        encoding="utf-8",
    )
    source_hash_after = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_hash_before != source_hash_after:
        raise RuntimeError("raw results changed during read-only audit")
    print(
        f"audited {len(rows)} rows: raw_success={summary['audit']['raw_success_count']}, "
        f"audited_success={summary['audit']['audited_success_count']}"
    )
    print(f"report: {run_dir / 'audited_report.md'}")


if __name__ == "__main__":
    main()
