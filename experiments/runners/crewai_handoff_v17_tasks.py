"""Task loading and prompt shapes for the r17 batch (pay for the pilot set, confirm later).

Two task files
--------------
* ``natural_tasks_r17.json`` -- the four tasks the paid r17 acquisition and three-arm pilot
  use.  They are newer than the r16 set but were written while the v17 guard was being
  developed, so they are **not** a blind confirmation set either.
* ``natural_tasks_r17_confirmation.json`` -- the four tasks designed for the **blind**
  confirmation run that must follow, because the r15/r16/r17 tasks are all no longer blind
  to the guard.

Shared shape
------------
Both files reuse the frozen two-role contract verbatim: the first role reads four frozen
evidence sources in order and hands off one factual line; the second role makes one
checkable decision and answers with the exact ``RESULT`` contract.  The filler history is
the same constructed template as r15/r16 but with **its own pair count** (10), so the
homogeneity boundary applies here as well: the tasks are same-shape by construction and
cross-task uniformity is not evidence of task-level heterogeneity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PILOT_TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r17.json"
CONFIRMATION_TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r17_confirmation.json"
#: The r17 filler history uses its own pair count; it is pinned in the r17 freeze.
FILLER_PAIRS = 10


def _load(path: Path) -> list[dict[str, Any]]:
    tasks = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"{path} must contain a non-empty list")
    for task in tasks:
        for field in (
            "task_id", "title", "task", "history_constraint", "history_topic",
            "tools", "decision", "handle_facts", "answer_facts", "forbidden_facts",
            "expected_terms",
        ):
            if not task.get(field):
                raise ValueError(f"task {task.get('task_id')} lacks {field}")
        for tool in task["tools"]:
            for key in ("name", "call", "result", "evidence_parts"):
                if not isinstance(tool, dict) or not tool.get(key):
                    raise ValueError(
                        f"task {task['task_id']} has a malformed tool entry"
                    )
    return tasks


def load_pilot_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return _load(path or PILOT_TASK_FILE)


def load_confirmation_tasks(path: Path | None = None) -> list[dict[str, Any]]:
    return _load(path or CONFIRMATION_TASK_FILE)


def fixed_history(task: dict[str, Any], repeat: int,
                  filler_pairs: int = FILLER_PAIRS) -> list[dict[str, str]]:
    """The r17 investigator prompt shape: ``filler_pairs`` filler pairs + the statement."""
    anchor = str(task["expected_terms"][0])
    topic = str(task["history_topic"])
    messages: list[dict[str, str]] = [
        {"role": "user", "content": str(task["history_constraint"])},
        {"role": "assistant", "content": f"已记录当前约束，并会在后续判断中保留 {anchor}。"},
    ]
    for index in range(filler_pairs + repeat):
        messages.extend(
            [
                {
                    "role": "user",
                    "content": (
                        f"关于{topic}的讨论片段（archive-{index + 1}）：当时提到过容量、排期与"
                        "负责人，也列过几个备选方案；这些内容只是背景，不能替代当前实测数据。"
                    ),
                },
                {
                    "role": "assistant",
                    "content": (
                        f"archive-{index + 1} 已记录：相关结论时间较早，部分已经关闭或撤销，"
                        "判断时应优先采用当前工具返回的事实。"
                    ),
                },
            ]
        )
    tools = "、".join(str(tool["name"]) for tool in task["tools"])
    messages.append(
        {
            "role": "user",
            "content": (
                f"调查阶段：{task['title']}。请按固定顺序对每个可用证据源各调用一次（{tools}），"
                "记录每次返回的当前事实。最终只输出一行以 HANDOFF 开头的交接内容，"
                "包含固定标识与关键数值；不要给出最终 decision。"
            ),
        }
    )
    return messages


def filler_scale(task: dict[str, Any], repeat: int = 0) -> dict[str, int]:
    history = fixed_history(task, repeat)
    return {
        "messages": len(history),
        "characters": sum(len(message["content"]) for message in history),
        "filler_pairs": FILLER_PAIRS + repeat,
    }


__all__ = [
    "CONFIRMATION_TASK_FILE",
    "FILLER_PAIRS",
    "PILOT_TASK_FILE",
    "filler_scale",
    "fixed_history",
    "load_confirmation_tasks",
    "load_pilot_tasks",
]
