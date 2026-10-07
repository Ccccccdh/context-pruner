"""Zero-API static gate for task/decision lexical collisions."""

from __future__ import annotations

import json
from pathlib import Path

from experiments.runners import crewai_semantic_equivalence_v8 as judge

ROOT = Path(__file__).resolve().parents[2]
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r28_fresh.json"


def inspect_task(task: dict) -> list[str]:
    errors = []
    names = [str(tool["name"]) for tool in task["tools"]]
    if len(names) not in (3, 5) or len(set(names)) != len(names):
        errors.append("source count or names invalid")
    handle = "HANDOFF " + " ".join(str(x) for x in task["handle_facts"])
    handle_verdict = judge.judge_handoff(judge.handle_rule(task), handle)
    if not handle_verdict.strict_pass:
        errors.append(f"minimal handoff contract fails: {handle_verdict.strict['issues']}")
    answer = (f"RESULT task={task['task_id']} decision={task['decision']} evidence="
              + " ".join(str(x) for x in task["answer_facts"]))
    answer_verdict = judge.judge_answer(judge.answer_rule(task), answer)
    if not answer_verdict.strict_pass:
        errors.append(f"minimal answer contract fails: {answer_verdict.strict['issues']}")
    # Real models may carry any returned field into the handoff. The mock only
    # emits handle_facts, so check the entire possible source text before API use.
    full_evidence = "HANDOFF " + " ".join(
        f"{key}={value}" for tool in task["tools"]
        for key, value in tool["result"].items()
    )
    full_verdict = judge.judge_handoff(judge.handle_rule(task), full_evidence)
    if full_verdict.strict.get("decision_leak"):
        errors.append("decision label collides with a full tool-result field")
    return errors


def inspect(path: Path = TASK_FILE) -> dict:
    tasks = judge.load_tasks(path)
    errors = {task["task_id"]: inspect_task(task) for task in tasks}
    return {"task_count": len(tasks), "errors": {k: v for k, v in errors.items() if v},
            "passed": not any(errors.values())}


if __name__ == "__main__":
    result = inspect()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
