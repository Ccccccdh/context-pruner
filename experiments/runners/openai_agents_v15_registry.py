"""Exact-source registry for v15 public read-only diagnosis tasks."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
LOCAL_GIT = REPO / ".tooling/upstream/django-16263"
TASKS_FILE = REPO / "integrations/openai_agents/V15_SOURCE_REGISTRATION_20261006.json"
REGISTRY_SCHEMA = "openai_agents_v15_exact_source_registry"


def payload() -> dict:
    return json.loads(TASKS_FILE.read_text(encoding="utf-8"))


def tasks() -> dict[str, dict]:
    return payload()["tasks"]


def task_ids() -> tuple[str, ...]:
    return tuple(tasks())


def task(task_id: str) -> dict:
    return tasks()[task_id]


def views(task_id: str) -> list[dict]:
    return list(task(task_id)["views"])


def source_bytes(task_id: str, path: str) -> bytes:
    entry = task(task_id)
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(
        ["git", "-C", str(LOCAL_GIT), "show", f"{entry['base_commit']}:{path}"],
        capture_output=True, check=False, env=env,
    )
    if result.returncode:
        raise FileNotFoundError(f"pinned Git source blob unavailable: {entry['base_commit']}:{path}")
    return result.stdout


def source_view(task_id: str, index: int) -> str:
    entry = task(task_id)
    view = entry["views"][index]
    lines = source_bytes(task_id, view["file"]).decode("utf-8").splitlines()
    first, last = int(view["first_line"]), int(view["last_line"])
    body = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1))
    return f"{entry['repo']}/{view['file']} (baseline)\n{body}"


def view_texts(task_id: str) -> list[str]:
    return [source_view(task_id, i) for i in range(len(task(task_id)["views"]))]


def history(task_id: str) -> list[dict]:
    entry = task(task_id)
    return [
        {"role": "user", "content": f"Public issue {entry['instance_id']}:\n{entry['problem_statement']}"},
        {"role": "user", "content": entry["request"]},
        {"role": "user", "content": "\n".join(entry["protocol_steps"])},
    ]


def statement(task_id: str) -> str:
    entry = task(task_id)
    return "\n".join([entry["problem_statement"], entry["request"], "\n".join(entry["protocol_steps"])])


def literal_phrases(task_id: str) -> dict[str, tuple[str, ...]]:
    return {name: tuple(phrases) for name, phrases in task(task_id)["literals"].items()}


def fingerprint(task_id: str) -> str:
    entry = task(task_id)
    seed = {name: value for name, value in entry.items() if name != "problem_statement"}
    return hashlib.sha256(json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify(task_id: str) -> list[str]:
    entry = task(task_id)
    problems: list[str] = []
    statement_hash = hashlib.sha256(entry["problem_statement"].encode("utf-8")).hexdigest()
    if statement_hash != entry["problem_statement_sha256"]:
        problems.append("problem_statement_sha256")
    if hashlib.sha256(statement(task_id).encode("utf-8")).hexdigest() != entry["statement_sha256"]:
        problems.append("statement_sha256")
    for index, view in enumerate(entry["views"]):
        try:
            raw = source_bytes(task_id, view["file"])
            text = source_view(task_id, index)
        except (FileNotFoundError, IndexError, UnicodeDecodeError):
            problems.append(f"view_{index}_unavailable")
            continue
        if hashlib.sha256(raw).hexdigest() != view["source_sha256"]:
            problems.append(f"view_{index}_source_sha256")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != view["view_sha256"]:
            problems.append(f"view_{index}_view_sha256")
    joined = "\n".join(view_texts(task_id)) if not problems else ""
    for term in entry["required_terms"]:
        if term not in joined:
            problems.append(f"required_term_missing:{term}")
    for label, phrases in entry["literals"].items():
        for phrase in phrases:
            if phrase not in joined:
                problems.append(f"registered_literal_missing:{label}:{phrase}")
    if len(entry["views"]) != 3 or entry["read_calls"] != 6 or entry["expected_model_calls"] != 7:
        problems.append("view_or_call_shape")
    return problems


def all_problems() -> dict[str, list[str]]:
    return {task_id: verify(task_id) for task_id in task_ids()}


def manifest_block() -> dict:
    return {
        "schema": REGISTRY_SCHEMA,
        "tasks_file": str(TASKS_FILE.relative_to(REPO)).replace("\\", "/"),
        "tasks_file_sha256": hashlib.sha256(TASKS_FILE.read_bytes()).hexdigest(),
        "task_ids": list(task_ids()),
        "per_task": {task_id: {"instance_id": task(task_id)["instance_id"], "fingerprint": fingerprint(task_id), "problems": verify(task_id)} for task_id in task_ids()},
    }
