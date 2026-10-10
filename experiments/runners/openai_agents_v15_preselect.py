"""Preselect fresh public diagnosis instances without inspecting answers or lengths.

Only SWE-bench Verified metadata (ID, repository, base commit, difficulty,
problem statement hash) is read. Patches, test patches, response lengths and
previous model outputs are never loaded into the selection procedure.
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
PARQUET = REPO / ".tooling/swebench-verified/test.parquet"
LOCAL_GIT = REPO / ".tooling/upstream/django-16263"
OUT = REPO / "integrations/openai_agents/V15_PRESELECT_20261006.json"
ID_RE = re.compile(r"django__django-\d+")
SOURCE_PROBES = {
    "django__django-11880": "django/forms/fields.py",
    "django__django-14787": "django/utils/decorators.py",
    "django__django-13512": "django/contrib/admin/widgets.py",
    "django__django-11964": "django/db/models/fields/__init__.py",
}


def _used_ids() -> set[str]:
    command = ["rg", "-o", "--glob", "!V15_*", "--glob", "!openai_agents_v15_preselect.py", ID_RE.pattern, "integrations", "experiments", "tests", "tasks"]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return set(ID_RE.findall(result.stdout))


def _object_available(spec: str) -> bool:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(["git", "-C", str(LOCAL_GIT), "cat-file", "-e", spec], capture_output=True, check=False, env=env)
    return result.returncode == 0


def main() -> None:
    used = _used_ids()
    rows = duckdb.read_parquet(str(PARQUET)).project(
        "instance_id, repo, base_commit, difficulty, problem_statement"
    ).filter("repo = 'django/django' AND difficulty = '<15 min fix'").fetchall()
    candidates = []
    for instance, repo, commit, difficulty, statement in rows:
        if instance in used or (REPO / ".tooling/swebench-verified" / instance.split("__", 1)[1]).exists():
            continue
        if not _object_available(f"{commit}^{{commit}}"):
            continue
        candidates.append({
            "instance_id": instance,
            "repo": repo,
            "base_commit": commit,
            "difficulty": difficulty,
            "problem_statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "selection_key": hashlib.sha256(instance.encode("utf-8")).hexdigest(),
        })
    candidates.sort(key=lambda row: row["selection_key"])
    selected = candidates[:4]
    if len(selected) < 4:
        raise RuntimeError(f"only {len(selected)} fresh locally available candidates")
    for row in selected:
        path = SOURCE_PROBES[row["instance_id"]]
        row["source_blob_probe"] = path
        row["source_blob_locally_available"] = _object_available(f"{row['base_commit']}:{path}")
    output = {
        "schema": "openai_agents_v15_preselect",
        "status": "CANDIDATE_ONLY_SOURCE_BLOBS_UNVERIFIED",
        "paid_requests": 0,
        "selection": "SWE-bench Verified django/django <15 min fix; no instance ID in integrations/experiments/tests/tasks; commit object present in local partial Git clone; sort by SHA256(instance_id), take first four",
        "local_clone_is_promisor_partial": True,
        "excluded_previously_referenced_ids": len(used),
        "eligible_count": len(candidates),
        "selected": selected,
        "not_examined_for_selection": ["patch", "test_patch", "model answers", "answer lengths", "expected literals"],
        "next_gate": "obtain and hash exact baseline source blobs, then register three read-only views and answer pattern before any acquisition request",
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"eligible": len(candidates), "selected": [x["instance_id"] for x in selected]}))


if __name__ == "__main__":
    main()
