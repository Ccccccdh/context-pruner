"""Registry for the v14 multi-task confirmation chain.

Four held-out closed-book diagnostic tasks, each shaped like the v13 one: three read-only
views of public baseline source at a pinned commit, read twice, then one single-line answer.
The task file is data with hashes, so a task's views, contract and provenance are auditable
without reading this module's code.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
TOOLING = REPO / ".tooling"
TASKS_FILE = REPO / "integrations/openai_agents/V14_MULTITASK_TASKS_20261005.json"
REGISTRY_SCHEMA = "openai_agents_multitask_registry_v14"

_CACHE: dict[str, Any] = {}


def payload() -> dict:
    if "payload" not in _CACHE:
        _CACHE["payload"] = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    return _CACHE["payload"]


def tasks() -> dict[str, dict]:
    return payload()["tasks"]


def task_ids() -> tuple[str, ...]:
    return tuple(tasks())


def task(task_id: str) -> dict:
    return tasks()[str(task_id)]


def checkout(task_id: str) -> Path:
    return REPO / task(task_id)["checkout"]


def views(task_id: str) -> list[dict]:
    return list(task(task_id)["views"])


def source_view(task_id: str, index: int) -> str:
    """One registered view, exactly as its tool returns it: header plus numbered lines."""
    entry = views(task_id)[index]
    path = checkout(task_id) / entry["file"]
    lines = path.read_text(encoding="utf-8").splitlines()
    first, last = int(entry["first_line"]), min(int(entry["last_line"]), len(lines))
    body = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1))
    return f"{task(task_id)['repo']}/{entry['file']} (baseline)\n{body}"


def view_texts(task_id: str) -> list[str]:
    return [source_view(task_id, index) for index in range(len(views(task_id)))]


def history(task_id: str) -> list[dict]:
    """The three message units the model receives for this task."""
    entry = task(task_id)
    statement = problem_statement(task_id)
    return [
        {"role": "user", "content": f"Public issue {entry['instance_id']}:\n{statement}"},
        {"role": "user", "content": entry["request"]},
        {"role": "user", "content": "\n".join(entry["protocol_steps"])},
    ]


def problem_statement(task_id: str) -> str:
    record = TOOLING / "swebench-verified" / f"{task(task_id)['instance_id'].replace('__', '-').lower()}-harness/instance.json"
    return str(json.loads(record.read_text(encoding="utf-8"))["problem_statement"])


def statement(task_id: str) -> str:
    entry = task(task_id)
    return "\n".join(
        [problem_statement(task_id), entry["request"], "\n".join(entry["protocol_steps"])]
    )


def literal_phrases(task_id: str) -> dict[str, tuple[str, ...]]:
    return {
        label: tuple(phrases) for label, phrases in task(task_id)["literals"].items()
    }


def input_hashes(task_id: str) -> dict[str, str]:
    entry = task(task_id)
    hashes = {}
    for view in entry["views"]:
        relative = str((checkout(task_id) / view["file"]).relative_to(REPO)).replace("\\", "/")
        hashes[relative] = view["file_sha256"]
    harness = (
        TOOLING
        / "swebench-verified"
        / f"{entry['instance_id'].replace('__', '-').lower()}-harness"
    )
    for name in ("instance.json", "host-tests.patch", "reference.patch"):
        path = harness / name
        if path.is_file():
            hashes[str(path.relative_to(REPO)).replace("\\", "/")] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashes


def fingerprint(task_id: str) -> str:
    entry = task(task_id)
    seed = {
        "schema": REGISTRY_SCHEMA,
        "task": entry["task_id"],
        "instance": entry["instance_id"],
        "base_commit": entry["base_commit"],
        "views": [
            [view["file"], view["first_line"], view["last_line"], view["file_sha256"]]
            for view in entry["views"]
        ],
        "tool_names": list(entry["tool_names"]),
        "request": entry["request"],
        "protocol_steps": list(entry["protocol_steps"]),
        "answer_pattern": entry["answer_pattern"],
        "required_terms": list(entry["required_terms"]),
        "literals": {label: list(v) for label, v in entry["literals"].items()},
        "contract": entry["contract"],
    }
    return hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def verify(task_id: str) -> list[str]:
    """Registration problems for one task (empty list means verified)."""
    problems: list[str] = []
    entry = task(task_id)
    if entry["head_matches_base_commit"] is not True:
        problems.append(f"{task_id}: the checkout is not the pinned base commit")
    for index, view in enumerate(views(task_id)):
        path = checkout(task_id) / view["file"]
        if not path.is_file():
            problems.append(f"{task_id}/view{index + 1}: missing {view['file']}")
            continue
        if hashlib.sha256(path.read_bytes()).hexdigest() != view["file_sha256"]:
            problems.append(f"{task_id}/view{index + 1}: file hash changed since registration")
        text = source_view(task_id, index)
        if f"{view['first_line']}: " not in text:
            problems.append(f"{task_id}/view{index + 1}: view is not numbered as registered")
    joined = "\n".join(view_texts(task_id))
    for term in entry["required_terms"]:
        if term not in joined:
            problems.append(f"{task_id}: required term {term!r} is not in the views")
    for label, phrases in entry["literals"].items():
        for phrase in phrases:
            if phrase not in joined:
                problems.append(f"{task_id}: literal {label} is not in the views: {phrase!r}")
    if int(entry["expected_model_calls"]) != int(entry["read_calls"]) + 1:
        problems.append(
            f"{task_id}: expected model calls must be the read calls plus the answer turn"
        )
    return problems


def all_problems() -> dict[str, list[str]]:
    return {task_id: verify(task_id) for task_id in task_ids()}


def manifest_block() -> dict[str, Any]:
    return {
        "schema": REGISTRY_SCHEMA,
        "tasks_file": str(TASKS_FILE.relative_to(REPO)).replace("\\", "/"),
        "tasks_file_sha256": hashlib.sha256(TASKS_FILE.read_bytes()).hexdigest(),
        "task_ids": list(task_ids()),
        "per_task": {
            task_id: {
                "instance_id": task(task_id)["instance_id"],
                "repo": task(task_id)["repo"],
                "base_commit": task(task_id)["base_commit"],
                "views": [
                    {
                        "file": view["file"],
                        "first_line": view["first_line"],
                        "last_line": view["last_line"],
                        "file_sha256": view["file_sha256"],
                    }
                    for view in views(task_id)
                ],
                "tool_names": list(task(task_id)["tool_names"]),
                "request": task(task_id)["request"],
                "protocol_steps": list(task(task_id)["protocol_steps"]),
                "contract": task(task_id)["contract"],
                "answer_pattern": task(task_id)["answer_pattern"],
                "required_terms": list(task(task_id)["required_terms"]),
                "literals": task(task_id)["literals"],
                "fingerprint": fingerprint(task_id),
                "statement_sha256": task(task_id)["statement_sha256"],
                "judging_metadata": task(task_id)["judging_metadata"],
                "problems": verify(task_id),
            }
            for task_id in task_ids()
        },
        "max_answer_chars": payload()["max_answer_chars"],
        "track_b_max_chars": payload()["track_b_max_chars"],
        "track_b_forbidden": list(payload()["track_b_forbidden"]),
    }


__all__ = [
    "REGISTRY_SCHEMA",
    "REPO",
    "TASKS_FILE",
    "TOOLING",
    "all_problems",
    "checkout",
    "fingerprint",
    "history",
    "input_hashes",
    "literal_phrases",
    "manifest_block",
    "payload",
    "problem_statement",
    "source_view",
    "statement",
    "task",
    "task_ids",
    "tasks",
    "verify",
    "view_texts",
    "views",
]
