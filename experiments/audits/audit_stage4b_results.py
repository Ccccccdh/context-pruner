"""只读重评分阶段四 B 结果，保留原始 summary 不变。"""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_demo.agent import _constraint_adherence, _evaluate, _partial_score
from agent_demo.env import Task, load_tasks
from metrics.summary import _write_csv, build_report, discover


def main(argv: list[str] | None = None) -> Path:
    parser = argparse.ArgumentParser(description="阶段四 B 真实工作区结果只读审计")
    parser.add_argument("root")
    parser.add_argument("--tasks", default="tasks/stage4b/tasks.json")
    args = parser.parse_args(argv)
    root = Path(args.root)
    tasks = {task.task_id: task for task in load_tasks(args.tasks)}
    samples = discover(root)
    corrected = []
    audit_rows: list[dict[str, Any]] = []
    for sample in samples:
        task = tasks[sample.task_id]
        summary_path = root / sample.method / f"{sample.task_id}.{sample.run_id}.summary.json"
        raw = _read_json(summary_path)
        workspace = root / "workspaces" / sample.method / f"{sample.task_id}.{sample.run_id}"
        env_success, checks = _audit_workspace(task, workspace, raw)
        answer_success = _evaluate(task, sample.final_answer)
        corrected_success = answer_success and env_success
        updated = replace(
            sample,
            success=corrected_success,
            partial_score=_partial_score(task, sample.final_answer),
            final_kirr=_partial_score(task, sample.final_answer),
            constraint_adherence=_constraint_adherence(task, sample.final_answer),
        )
        corrected.append(updated)
        audit_rows.append(
            {
                "task_id": sample.task_id,
                "method": sample.method,
                "run_id": sample.run_id,
                "original_success": sample.success,
                "corrected_success": corrected_success,
                "answer_success": answer_success,
                "environment_success": env_success,
                "original_partial_score": sample.partial_score,
                "corrected_partial_score": updated.partial_score,
                "original_constraint_adherence": sample.constraint_adherence,
                "corrected_constraint_adherence": updated.constraint_adherence,
                "failed_environment_checks": [
                    check for check in checks if not check["passed"]
                ],
                "reason": "semantic_pass_status_and_behavioral_workspace_validation",
            }
        )

    report = build_report(corrected, baseline="none")
    _write_json(root / "audited_report.json", report)
    _write_csv(root / "audited_method_summary.csv", report["methods"])
    _write_csv(root / "audited_paired_comparison.csv", report["paired_to_baseline"])
    _write_jsonl(root / "audit_corrections.jsonl", audit_rows)
    _write_json(
        root / "audited_workspace_validation.json",
        {
            "sample_count": len(audit_rows),
            "passed": sum(row["environment_success"] for row in audit_rows),
            "corrected_successes": sum(row["corrected_success"] for row in audit_rows),
            "changed_success_labels": sum(
                row["original_success"] != row["corrected_success"]
                for row in audit_rows
            ),
            "raw_summaries_modified": False,
        },
    )
    print(f"阶段四 B 只读审计已生成：{root}")
    return root


def _audit_workspace(
    task: Task,
    workspace: Path,
    raw_summary: dict[str, Any],
) -> tuple[bool, list[dict[str, Any]]]:
    expectations = dict(task.workspace_expectations or {})
    checks: list[dict[str, Any]] = []
    for relative, rules in dict(expectations.get("files") or {}).items():
        path = (workspace / relative).resolve()
        inside = workspace.resolve() == path or workspace.resolve() in path.parents
        try:
            text = path.read_text(encoding="utf-8") if inside else ""
        except (OSError, UnicodeError):
            text = ""
        for needle in rules.get("contains", []):
            checks.append(
                {
                    "check": "file_contains",
                    "path": relative,
                    "value": str(needle),
                    "passed": str(needle) in text,
                }
            )
        for needle in rules.get("absent", []):
            checks.append(
                {
                    "check": "file_absent",
                    "path": relative,
                    "value": str(needle),
                    "passed": bool(text) and str(needle) not in text,
                }
            )
    metrics = dict(raw_summary.get("environment_metrics") or {})
    if expectations.get("tests_pass"):
        checks.append(
            {
                "check": "tests_pass",
                "passed": bool(metrics.get("tests_passed"))
                and metrics.get("test_state") == "PASS",
            }
        )
    tool_counts = dict(metrics.get("tool_counts") or {})
    for tool in expectations.get("required_tools", []):
        checks.append(
            {
                "check": "tool_used",
                "tool": str(tool),
                "passed": int(tool_counts.get(str(tool), 0)) > 0,
            }
        )
    return all(check["passed"] for check in checks), checks


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    temporary.replace(path)


if __name__ == "__main__":
    main()
