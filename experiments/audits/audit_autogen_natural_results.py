"""Read-only evaluator audit for AutoGen natural-task result directories."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from experiments.runners.run_autogen_natural_experiment import (
    _answer_correct,
    _report,
    load_tasks,
    summarize,
)


def audit(
    run_dir: Path,
    *,
    tasks_path: Path,
    max_final_chars: int,
) -> list[dict[str, Any]]:
    task_map = {task["task_id"]: task for task in load_tasks(tasks_path)}
    source = run_dir / "results.jsonl"
    if not source.exists():
        raise FileNotFoundError(source)
    audited: list[dict[str, Any]] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        original = json.loads(line)
        task = task_map[str(original["task_id"])]
        output = str(original.get("final_output") or "")
        answer_correct = _answer_correct(
            str(task["task_id"]),
            output.lower(),
            tuple(str(term) for term in task["expected_terms"]),
        )
        final_format_correct = (
            output.startswith(f"RESULT task={task['task_id']} ")
            and "\n" not in output
            and len(output) <= max_final_chars
        )
        row = dict(original)
        row["raw_success"] = bool(original.get("success"))
        row["raw_answer_correct"] = bool(original.get("answer_correct"))
        row["raw_final_format_correct"] = bool(
            original.get("final_format_correct")
        )
        row["answer_correct"] = answer_correct
        row["final_format_correct"] = final_format_correct
        row["success"] = all(
            (
                not row.get("error_type"),
                answer_correct,
                bool(row.get("tool_correct")),
                final_format_correct,
                bool(row.get("constraint_preserved")),
                bool(row.get("structure_safe")),
            )
        )
        row["audit_max_final_chars"] = max_final_chars
        audited.append(row)
    return audited


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir")
    parser.add_argument(
        "--tasks", default="tasks/stage5_autogen/natural_tasks.json"
    )
    parser.add_argument("--max-final-chars", type=int, default=300)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    rows = audit(
        run_dir,
        tasks_path=Path(args.tasks),
        max_final_chars=args.max_final_chars,
    )
    mode = str(rows[0]["mode"]) if rows else "api"
    summary = summarize(rows, mode)
    summary["audit"] = {
        "source": "results.jsonl",
        "max_final_chars": args.max_final_chars,
        "raw_rows_unchanged": True,
        "raw_success_count": sum(bool(row["raw_success"]) for row in rows),
        "audited_success_count": sum(bool(row["success"]) for row in rows),
    }
    (run_dir / "audited_results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    (run_dir / "audited_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "audited_report.md").write_text(
        _report(summary, rows)
        + "\n审计未修改原始 `results.jsonl`；仅把单行最终输出上限从 220 调整为 300 字符。\n",
        encoding="utf-8",
    )
    print(
        f"audited {len(rows)} rows: raw_success={summary['audit']['raw_success_count']}, "
        f"audited_success={summary['audit']['audited_success_count']}"
    )
    print(f"report: {run_dir / 'audited_report.md'}")


if __name__ == "__main__":
    main()
