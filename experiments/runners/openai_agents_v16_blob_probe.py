"""Offline exact-source availability probe; never triggers a promisor fetch."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from experiments.runners import openai_agents_v15_registry as old_registry


REPO = old_registry.REPO
GIT = REPO / ".tooling/upstream/django-16263"
PRESELECT = REPO / "integrations/openai_agents/V16_PRESELECT_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_BLOB_PROBE_20261006.json"
PATHS = {
    "django__django-13821": [
        "django/db/backends/sqlite3/base.py",
        "django/db/backends/sqlite3/features.py",
        "django/db/backends/sqlite3/operations.py",
    ],
    "django__django-13410": ["django/core/files/locks.py"],
    "django__django-14089": ["django/utils/datastructures.py"],
}


def main() -> None:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    selected = {row["instance_id"]: row for row in json.loads(PRESELECT.read_text(encoding="utf-8"))["development_candidates"]}
    results = {}
    for instance, paths in PATHS.items():
        commit = selected[instance]["base_commit"]
        per_path = {}
        for path in paths:
            result = subprocess.run(["git", "-C", str(GIT), "cat-file", "-e", f"{commit}:{path}"], env=env, capture_output=True, check=False)
            per_path[path] = result.returncode == 0
        results[instance] = {"base_commit": commit, "paths": per_path}
    OUT.write_text(json.dumps({"schema": "openai_agents_v16_offline_blob_probe", "paid_requests": 0, "lazy_fetch_disabled": True, "results": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
