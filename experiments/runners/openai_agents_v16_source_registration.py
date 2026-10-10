"""v16 development-task source registration (zero API).

Registers the exact baseline source views for the v16 development candidates:

* every view is pinned by ``commit:path`` **blob hash** plus the view's own content SHA256, so
  a later edit of the working tree cannot silently change the task;
* every contract token and required fact is checked to occur in the frozen views or in the
  registered public statement, so no contract can depend on the reference fix;
* a candidate whose base source does **not** contain the defect its public issue describes is
  *rejected* and recorded as rejected - it is not replaced by the next id in the preselection
  list, and no unspent candidate is consumed to fill a gate.

The source is read from the local partial clone's git objects (read-only: ``git rev-parse``
and ``git show`` for the pinned paths). Nothing is fetched, written upstream, staged or
committed.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

REPO = Path(__file__).resolve().parents[2]
TOOLING = REPO / ".tooling"
PARQUET = TOOLING / "swebench-verified/test.parquet"
CLONE = TOOLING / "upstream/django-15563"
PRESELECT = REPO / "integrations/openai_agents/V16_PRESELECT_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_SOURCE_REGISTRATION_20261006.json"

SCHEMA = "openai_agents_v16_source_registration"
MAX_ANSWER_CHARS = 160
CONTRACT_FIELDS = ("issue", "cause", "fix", "facts")

TASKS = (
    {
        "task_id": "django_sqlite_version_floor",
        "instance_id": "django__django-13821",
        "views": (
            ("django/db/backends/sqlite3/base.py", 60, 75, "cause: the version floor check"),
            ("django/db/backends/sqlite3/features.py", 30, 50, "context: version-gated features"),
            ("django/db/backends/sqlite3/features.py", 60, 95, "context: json field support"),
        ),
        "tool_names": (
            "read_sqlite_version_check",
            "read_sqlite_features_a",
            "read_sqlite_features_b",
        ),
        "cause_token": "check_sqlite_version",
        "fix_token": "3.9.0",
        "facts": ("check_sqlite_version", "3.8.3", "3.9.0"),
        "defect_marker": "(3, 8, 3)",
        "request": (
            "Diagnose why this baseline still accepts an SQLite version below 3.9.0. Read the "
            "version check, the version-gated feature flags and the json field support code in "
            "order; name the function that sets the floor and the version the fix must require."
        ),
    },
    {
        "task_id": "django_orderedset_reversed",
        "instance_id": "django__django-14089",
        "views": (
            ("django/utils/datastructures.py", 5, 37, "cause: the OrderedSet class"),
            ("django/utils/datastructures.py", 42, 75, "context: container conventions"),
            ("django/utils/datastructures.py", 265, 335, "context: mapping iteration"),
        ),
        "tool_names": (
            "read_orderedset_class",
            "read_datastructures_mvd",
            "read_datastructures_mapping",
        ),
        "cause_token": "OrderedSet",
        "fix_token": "__reversed__",
        "facts": ("OrderedSet", "__iter__", "__reversed__"),
        "defect_marker": "def __reversed__",
        "defect_marker_must_be_absent": True,
        "request": (
            "Diagnose why this baseline OrderedSet cannot be passed to Python's reversed(). Read "
            "the OrderedSet class, the multi-value container conventions and the mapping "
            "iteration code in order; name the class that is missing the method and the method "
            "the fix must add."
        ),
    },
)

REJECTED = (
    {
        "instance_id": "django__django-13410",
        "task_id": "django_locks_posix_return",
        "gate": "source_defect_present",
        "decision": "rejected_not_substituted",
        "why": (
            "the public issue describes a posix lock() that returns the result of fcntl.lockf(), "
            "which is None on success and therefore always falsy; the pinned base source "
            "contains no 'lockf' call at all - its posix branch already calls fcntl.flock() and "
            "returns 'ret == 0', and its fallback branch returns False/True by design - so the "
            "described defect is not present in the registered baseline and no defensible "
            "cause/fix contract can be registered from these views"
        ),
        "evidence": {
            "path": "django/core/files/locks.py",
            "blob_sha_at_base": "c46b00b90576cca644e9dae1870b3a944ae1e4fa",
            "file_lines": 115,
            "posix_lock_body": [
                "        def lock(f, flags):",
                "            ret = fcntl.flock(_fd(f), flags)",
                "            return ret == 0",
            ],
            "occurences_of_lockf": 0,
            "occurences_of_flock": 2,
        },
        "not_done": [
            "no substitution from the preselection list",
            "the untouched confirmation candidates were not consumed",
            "the unspent fourth v15 candidate was not consumed",
        ],
    },
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(CLONE), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def blob_sha(commit: str, path: str) -> str:
    return git("rev-parse", f"{commit}:{path}").strip()


def blob_text(commit: str, path: str) -> str:
    return git("show", f"{commit}:{path}")


def view_payload(commit: str, path: str, first: int, last: int) -> dict[str, Any]:
    text = blob_text(commit, path)
    lines = text.splitlines()
    selected = lines[first - 1 : last]
    body = "\n".join(f"{number}: {line}" for number, line in enumerate(selected, first))
    header = f"django/django/{path} (baseline)"
    rendered = f"{header}\n{body}"
    return {
        "file": path,
        "first_line": first,
        "last_line": last,
        "file_lines": len(lines),
        "commit_path": f"{commit}:{path}",
        "blob_sha256_git": blob_sha(commit, path),
        "file_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "view_content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "view_bytes": len(rendered.encode("utf-8")),
        "text": rendered,
    }


def rejected_evidence() -> list[dict[str, Any]]:
    """Mechanically recompute the evidence behind each rejected candidate.

    A rejection removes a candidate from the development set, so the record has to be
    checkable by a third party: the instance, the pinned commit, the inspected path, the git
    blob id *and* the content SHA256, the occurrence counts that decide the gate, and the exact
    read-only commands. The verdict is unchanged; only the evidence is completed.
    """
    rows = {row["instance_id"]: row for row in pq.read_table(str(PARQUET)).to_pylist()}
    out = []
    for entry in REJECTED:
        instance = rows[entry["instance_id"]]
        commit = instance["base_commit"]
        path = entry["evidence"]["path"]
        text = blob_text(commit, path)
        rev_parse = f"git -C .tooling/upstream/django-15563 rev-parse {commit}:{path}"
        show = f"git -C .tooling/upstream/django-15563 show {commit}:{path}"
        lines = text.splitlines()
        posix_lock = [
            line for line in lines if "fcntl.flock" in line or "return ret == 0" in line
        ]
        out.append(
            {
                **{key: value for key, value in entry.items() if key != "evidence"},
                "base_commit": commit,
                "repo": instance["repo"],
                "checked_files": [
                    {
                        "path": path,
                        "git_blob_id": blob_sha(commit, path),
                        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                        "file_lines": len(lines),
                        "blob_bytes": len(text.encode("utf-8")),
                    }
                ],
                "occurrence_counts": {
                    "lockf": text.count("lockf"),
                    "flock": text.count("flock"),
                },
                "defect_signature_count": text.count("lockf"),
                "gate_rule": (
                    "the defect the public issue describes must be present in the pinned base "
                    "source before a cause/fix contract can be registered from those views"
                ),
                "read_method": (
                    "read-only git object reads (no fetch, no write, no staging): "
                    f"`{rev_parse}` for the git blob id and `{show}` for the content"
                ),
                "commands": [rev_parse, show],
                "evidence": {
                    **entry["evidence"],
                    "occurrences_lockf": text.count("lockf"),
                    "occurrences_flock": text.count("flock"),
                    "posix_lock_body": posix_lock,
                },
            }
        )
    return out


def build() -> dict[str, Any]:
    rows = {row["instance_id"]: row for row in pq.read_table(str(PARQUET)).to_pylist()}
    preselect = json.loads(PRESELECT.read_text(encoding="utf-8"))
    preselected = {
        entry["instance_id"]: entry
        for entry in preselect["development_candidates"]
        + preselect["untouched_confirmation_candidates"]
    }
    tasks: dict[str, Any] = {}
    problems: list[str] = []
    for spec in TASKS:
        instance = rows[spec["instance_id"]]
        commit = instance["base_commit"]
        statement = str(instance["problem_statement"])
        statement_sha = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        recorded = preselected.get(spec["instance_id"], {}).get(
            "problem_statement_sha256"
        )
        if recorded and recorded != statement_sha:
            problems.append(f"{spec['task_id']}: statement hash differs from the preselection")
        views = [
            view_payload(commit, path, first, last)
            for path, first, last, _role in spec["views"]
        ]
        joined = "\n".join(view["text"] for view in views)
        searchable = f"{joined}\n{statement}"
        for token in (spec["cause_token"], spec["fix_token"]):
            if token not in searchable:
                problems.append(f"{spec['task_id']}: contract token {token!r} is not reachable")
        for fact in spec["facts"]:
            if fact not in searchable:
                problems.append(f"{spec['task_id']}: required fact {fact!r} is not reachable")
        marker = spec["defect_marker"]
        must_be_absent = bool(spec.get("defect_marker_must_be_absent"))
        present = marker in joined
        if must_be_absent and present:
            problems.append(f"{spec['task_id']}: the defect marker {marker!r} is already present")
        if not must_be_absent and not present:
            problems.append(f"{spec['task_id']}: the defect marker {marker!r} is missing")
        short = spec["instance_id"].split("__")[-1]
        tasks[spec["task_id"]] = {
            "task_id": spec["task_id"],
            "instance_id": spec["instance_id"],
            "repo": instance["repo"],
            "base_commit": commit,
            "difficulty": instance["difficulty"],
            "problem_statement_bytes": len(statement.encode("utf-8")),
            "problem_statement_sha256": statement_sha,
            "problem_statement_sha256_matches_preselection": recorded == statement_sha,
            "views": [{key: view[key] for key in view if key != "text"} for view in views],
            "view_texts_sha256": [
                view["view_content_sha256"] for view in views
            ],
            "tool_names": list(spec["tool_names"]),
            "request": spec["request"],
            "read_calls": 6,
            "expected_model_calls": 7,
            "contract": {
                "shape": "RESULT issue=<issue> cause=<cause> fix=<fix>",
                "json_fields": list(CONTRACT_FIELDS),
                "issue_value": short,
                "required_facts": list(spec["facts"]),
                "cause_token": spec["cause_token"],
                "fix_token": spec["fix_token"],
                "frozen_regex": (
                    f"RESULT issue={short} cause=.*{spec['cause_token']}.* "
                    f"fix=.*{spec['fix_token']}.*"
                ),
                "max_answer_chars": MAX_ANSWER_CHARS,
                "renderer": "experiments/runners/openai_agents_structured_final_v16.py",
                "render_rule": (
                    "the host renders the JSON fields verbatim, rejects any proposal whose "
                    "declared facts do not appear in the rendered answer, and never truncates"
                ),
            },
            "defect_evidence": {
                "marker": marker,
                "expected": "absent" if must_be_absent else "present",
                "observed": "present" if present else "absent",
                "ok": present != must_be_absent,
            },
            "registration_problems": [],
        }
    for problem in problems:
        task_id = problem.split(":", 1)[0]
        tasks.get(task_id, {}).setdefault("registration_problems", []).append(problem)
    return {
        "schema": SCHEMA,
        "status": "SOURCE_AND_CONTRACT_REGISTERED_NO_API_ADMISSION",
        "date": "20261006",
        "paid_requests": 0,
        "preselection": str(PRESELECT.relative_to(REPO)).replace("\\", "/"),
        "source_read": (
            "read-only git object reads against the local partial clone "
            "(.tooling/upstream/django-15563): git rev-parse <commit>:<path> for the blob hash "
            "and git show <commit>:<path> for the content. No fetch, no write, no staging."
        ),
        "registered_task_count": len(tasks),
        "registered_task_ids": list(tasks),
        "tasks": tasks,
        "rejected_candidates": rejected_evidence(),
        "selection_block": {
            "status": "NO_FRESH_CANDIDATE_UNDER_THE_SAME_RULE",
            "measured": {
                "django_15min_rows_in_parquet": 92,
                "excluded_by_v15_preselection": 4,
                "excluded_as_referenced_in_the_repository": 88,
                "eligible_after_exclusions": 0,
                "distinct_django_ids_referenced_in_the_repository": 231,
            },
            "why": (
                "the preselection rule excludes every instance whose id is referenced anywhere in "
                "integrations/, experiments/, tests/ or tasks/. That reference set has grown "
                "(231 django ids), so a fresh run of the identical rule now yields no eligible "
                "candidate at all; the recorded preselection artifact kept only its top six ids, "
                "so a next item cannot be recovered from it either."
            ),
            "not_done": [
                "no candidate was substituted for the rejected slot",
                "the untouched confirmation candidates were not consumed",
                "the unspent v15 candidate 13512 was not consumed",
                "the exclusion rule was not relaxed to reach a third task",
            ],
            "options_for_the_user": [
                "approve a narrower exclusion: only instances actually spent in a paid batch "
                "(computable from run records) rather than any textual mention",
                "widen the candidate pool beyond django/django under the same metadata rule",
                "accept a two-task development set and keep the three-task pilot gate unmet",
            ],
        },
        "problems": problems,
        "verified": not problems,
        "not_yet_done": [
            "the three-arm run is not started and must not start from this file alone",
            "one development task is short (two registered, one rejected), so the development "
            "set is 2/3 until a new candidate is registered under a new decision",
            "no paid request has been made for any v16 task",
        ],
    }


def main() -> int:
    report = build()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"registered: {report['registered_task_ids']} | problems: {report['problems']}")
    for task_id, task in report["tasks"].items():
        print(
            f"  {task_id:<28} {task['instance_id']:<24} base={task['base_commit'][:10]} "
            f"views={len(task['views'])} defect={task['defect_evidence']['observed']} "
            f"regex={task['contract']['frozen_regex'][:70]}"
        )
    for rejected in report["rejected_candidates"]:
        print(f"  REJECTED {rejected['instance_id']}: {rejected['gate']}")
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
