"""Deterministic v16 candidate selection without reading model answers.

Run in the OpenHands environment with duckdb. This is metadata selection only:
it neither fetches source nor registers contracts or sends API requests.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

import duckdb


REPO = Path(__file__).resolve().parents[2]
PARQUET = REPO / ".tooling/swebench-verified/test.parquet"
OUT = REPO / "integrations/openai_agents/V16_PRESELECT_20261006.json"
V15_PRESELECT = REPO / "integrations/openai_agents/V15_PRESELECT_20261006.json"
SALT = "v16-structured-final-2026-10-06"
ID_RE = re.compile(r"django__django-\d+")


def used_ids() -> set[str]:
    command = ["rg", "-o", "--glob", "!V16_*", "--glob", "!openai_agents_v16_preselect.py", ID_RE.pattern, "integrations", "experiments", "tests", "tasks"]
    result = subprocess.run(command, cwd=REPO, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr)
    return set(ID_RE.findall(result.stdout))


def main() -> None:
    old = {row["instance_id"] for row in json.loads(V15_PRESELECT.read_text(encoding="utf-8"))["selected"]}
    referenced = used_ids()
    rows = duckdb.read_parquet(str(PARQUET)).project(
        "instance_id, repo, base_commit, difficulty, problem_statement"
    ).filter("repo = 'django/django' AND difficulty = '<15 min fix'").fetchall()
    candidates = []
    for instance, repo, commit, difficulty, statement in rows:
        if instance in old or instance in referenced:
            continue
        # Existing extracted instances are spent, even if their ID is absent
        # from protocol text. No source or answer content is inspected here.
        if (REPO / ".tooling/swebench-verified" / instance.split("__", 1)[1]).exists():
            continue
        key = hashlib.sha256(f"{SALT}\0{instance}".encode()).hexdigest()
        candidates.append({
            "instance_id": instance,
            "repo": repo,
            "base_commit": commit,
            "difficulty": difficulty,
            "problem_statement_sha256": hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            "selection_key": key,
        })
    candidates.sort(key=lambda row: row["selection_key"])
    if len(candidates) < 6:
        raise RuntimeError("fewer than six metadata-eligible fresh candidates")
    selected = candidates[:6]
    output = {
        "schema": "openai_agents_v16_metadata_preselection",
        "status": "CANDIDATE_ONLY_NO_SOURCE_CONTRACT_OR_API_ADMISSION",
        "paid_requests": 0,
        "selection_rule": "Verified django/django <15 min fix; exclude every v15 preselected ID including unspent fourth, previously referenced IDs and extracted instances; sort SHA256(salt + NUL + instance_id)",
        "salt": SALT,
        "eligible_count": len(candidates),
        "development_candidates": selected[:3],
        "untouched_confirmation_candidates": selected[3:],
        "not_read_for_selection": ["reference patch", "test patch", "model answer", "answer length", "answer regex result", "provider cost"],
        "rule_after_selection": "all selected outcomes count; no replacing a baseline-format failure with the next ID; new task or endpoint requires new explicit freeze",
        "v15_fourth_candidate_not_substituted": True,
    }
    OUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"eligible": len(candidates), "development": [r["instance_id"] for r in selected[:3]], "confirmation": [r["instance_id"] for r in selected[3:]]}))


if __name__ == "__main__":
    main()
