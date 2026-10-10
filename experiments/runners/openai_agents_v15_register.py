"""Offline registration of v15 read-only public baseline diagnosis tasks.

Selection was fixed in V15_PRESELECT before any model answer. This script reads
only Verified issue statements and the named base-commit source blobs. It does
not load reference patches, test patches, answer lengths, or model outputs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import duckdb


REPO = Path(__file__).resolve().parents[2]
LOCAL_GIT = REPO / ".tooling/upstream/django-16263"
PARQUET = REPO / ".tooling/swebench-verified/test.parquet"
PRESELECT = REPO / "integrations/openai_agents/V15_PRESELECT_20261006.json"
OUT = REPO / "integrations/openai_agents/V15_SOURCE_REGISTRATION_20261006.json"


SPECS = {
    "django__django-11880": {
        "task_id": "django_field_error_messages_copy",
        "views": [
            ("django/forms/fields.py", 95, 115, "read_field_message_init"),
            ("django/forms/fields.py", 195, 205, "read_field_deepcopy"),
            ("django/forms/fields.py", 120, 145, "read_field_error_use"),
        ],
        "request": "Diagnose why editing one copied Form Field's error_messages changes another instance. Read Field message initialization, Field.__deepcopy__, and validation use in order; identify the shared object and the required copy fix.",
        "contract": "RESULT issue=django-11880 cause=<...> fix=<...>",
        "answer_pattern": r"RESULT issue=django-11880 cause=.*error_messages.* fix=.*deepcopy.*",
        "required_terms": ["error_messages", "deepcopy"],
        "literals": {"message_init": ["self.error_messages = messages"], "deepcopy": ["def __deepcopy__", "result.validators = self.validators[:]"], "error_use": ["self.error_messages['required']"]},
    },
    "django__django-14787": {
        "task_id": "django_method_decorator_partial",
        "views": [
            ("django/utils/decorators.py", 10, 20, "read_method_wrapper_helper"),
            ("django/utils/decorators.py", 22, 51, "read_multi_decorate"),
            ("django/utils/decorators.py", 53, 85, "read_method_decorator"),
        ],
        "request": "Diagnose why a method decorator expecting the wrapped function's __name__ receives a partial without it. Read the wrapper helper, _multi_decorate and method_decorator in order; identify the object lacking metadata and where wrapper metadata must be copied.",
        "contract": "RESULT issue=django-14787 cause=<...> fix=<...>",
        "answer_pattern": r"RESULT issue=django-14787 cause=.*partial.*__name__.* fix=.*update_wrapper.*",
        "required_terms": ["partial", "__name__", "update_wrapper"],
        "literals": {"helper": ["def _update_method_wrapper", "update_wrapper(_wrapper, dummy)"], "partial": ["bound_method = partial(method.__get__"], "outer_wrapper": ["update_wrapper(_wrapper, method)"], "entry": ["def method_decorator"]},
    },
    "django__django-11964": {
        "task_id": "django_textchoices_string_value",
        "views": [
            ("django/db/models/enums.py", 52, 72, "read_text_choices_classes"),
            ("django/db/models/fields/__init__.py", 1004, 1018, "read_charfield_conversion"),
            ("django/db/models/enums.py", 38, 51, "read_choice_values"),
        ],
        "request": "Diagnose why a fresh CharField value assigned from TextChoices stringifies as an Enum member name while the database-retrieved value is plain text. Read the choices classes, CharField conversion and choices values in order; name the representation method and value the fix must use.",
        "contract": "RESULT issue=django-11964 cause=<...> fix=<...>",
        "answer_pattern": r"RESULT issue=django-11964 cause=.*TextChoices.*__str__.* fix=.*value.*",
        "required_terms": ["TextChoices", "value"],
        "literals": {"text_choices": ["class TextChoices(str, Choices)"], "charfield": ["def to_python(self, value):", "if isinstance(value, str) or value is None:"], "values": ["def values(cls):"]},
    },
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source(commit: str, path: str) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(["git", "-C", str(LOCAL_GIT), "show", f"{commit}:{path}"], capture_output=True, check=False, env=env)
    if result.returncode:
        raise RuntimeError(f"exact offline source unavailable: {commit}:{path}: {result.stderr.decode(errors='replace')[:200]}")
    return result.stdout


def _view(source: bytes, first: int, last: int, repo: str, path: str) -> str:
    lines = source.decode("utf-8").splitlines()
    if first < 1 or last > len(lines) or first > last:
        raise ValueError(f"invalid view {path}:{first}-{last}")
    body = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1))
    return f"{repo}/{path} (baseline)\n{body}"


def main() -> None:
    selected = {row["instance_id"]: row for row in json.loads(PRESELECT.read_text(encoding="utf-8"))["selected"]}
    statements = dict(duckdb.read_parquet(str(PARQUET)).project("instance_id, problem_statement").fetchall())
    tasks = {}
    for instance, spec in SPECS.items():
        meta = selected[instance]
        statement = statements[instance]
        assert _sha(statement.encode("utf-8")) == meta["problem_statement_sha256"]
        sources: dict[str, bytes] = {}
        views = []
        texts = []
        for path, first, last, tool in spec["views"]:
            sources.setdefault(path, _source(meta["base_commit"], path))
            text = _view(sources[path], first, last, meta["repo"], path)
            texts.append(text)
            views.append({
                "file": path,
                "first_line": first,
                "last_line": last,
                "tool_name": tool,
                "source_sha256": _sha(sources[path]),
                "view_sha256": _sha(text.encode("utf-8")),
                "view_bytes": len(text.encode("utf-8")),
            })
        joined = "\n".join(texts)
        if any(term not in joined for term in spec["required_terms"]):
            raise AssertionError(f"missing required term in source views: {instance}")
        if any(phrase not in joined for phrases in spec["literals"].values() for phrase in phrases):
            raise AssertionError(f"missing registered literal in source views: {instance}")
        if re.fullmatch(spec["answer_pattern"], f"RESULT issue={instance.split('-')[-1]} cause=x fix=y"):
            raise AssertionError("answer pattern accepts a term-free answer")
        tasks[spec["task_id"]] = {
            "instance_id": instance,
            "repo": meta["repo"],
            "base_commit": meta["base_commit"],
            "problem_statement": statement,
            "problem_statement_sha256": meta["problem_statement_sha256"],
            "views": views,
            "request": spec["request"],
            "protocol_steps": [
                "Read the three baseline views twice, strictly one tool call per turn, and answer only after the sixth read returns.",
                *[
                    f"Turn {turn}: call {tool} alone, then stop and wait for its result."
                    for turn, tool in enumerate([view["tool_name"] for view in views] * 2, start=1)
                ],
                "Turn 7: only after all six reads have returned, state the final diagnosis.",
            ],
            "contract": spec["contract"],
            "answer_pattern": spec["answer_pattern"],
            "required_terms": spec["required_terms"],
            "literals": spec["literals"],
            "tool_names": [view["tool_name"] for view in views],
            "tool_docs": [f"Read only the pinned public baseline source view {index + 1} for {instance}." for index in range(len(views))],
            "read_calls": 6,
            "expected_model_calls": 7,
            "workload_role": "controlled_six_read_repeated_source_diagnostic; not natural agent behavior",
            "natural_agent_behavior_claim": False,
        }
        tasks[spec["task_id"]]["statement_sha256"] = _sha(
            "\n".join([statement, spec["request"], "\n".join(tasks[spec["task_id"]]["protocol_steps"])]).encode("utf-8")
        )
    result = {
        "schema": "openai_agents_v15_source_registration",
        "status": "THREE_TASKS_REGISTERED_PENDING_ZERO_API_REPLAY",
        "paid_requests": 0,
        "preselect_sha256": _sha(PRESELECT.read_bytes()),
        "source_origin": "exact Git blobs at Verified base commits; GIT_NO_LAZY_FETCH=1",
        "workload_role": "controlled diagnostic workload with repeated exact source outputs; not evidence of natural agent behavior or broad plugin efficacy",
        "excluded_from_registration": ["reference patch", "test patch", "model answers", "answer lengths"],
        "tasks": tasks,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"registered": list(tasks), "status": result["status"]}))


if __name__ == "__main__":
    main()
