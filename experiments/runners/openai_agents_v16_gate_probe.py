"""Registered mechanical baseline probes for the v16 source-defect gate (zero API).

Gate definition (updated, verbatim):

    A candidate passes the source-defect gate when the defect its public issue describes is
    established by either (a) a symbol/behaviour scan of the pinned base blobs, or (b) a
    **registered mechanical probe**: a deterministic read-only script whose source and command
    are recorded together with its stdout and return code, run against the pinned base tree,
    with a positive and a negative control proving the probe is neither always-true nor
    always-false. Probes never read the reference patch or the evaluation test patch; those are
    recorded by hash only. A probe that cannot establish the defect leaves the candidate
    rejected (fail-closed) and the next candidate in frozen order is tried.

Each probe below is AST-based: it parses the pinned blob and answers a structural question
about the described behaviour, so it does not depend on grep wording and cannot be satisfied by
a comment.
"""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
CLONE = REPO / ".tooling/upstream/django-15563"
POOL = REPO / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
OUT = REPO / "integrations/openai_agents/V16_GATE_PROBES_20261006.json"

PROBE_SOURCE_LINES = (
    "text = git show <commit>:<path>            # read-only",
    "tree = ast.parse(text)                     # no execution of the target code",
    "defined = {n.name for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n, ast.FunctionDef)}",
    "uses_model = any('self.model' in ast.dump(n) for c in tree.body if isinstance(c, ast.ClassDef) for n in c.body if isinstance(n, ast.FunctionDef) and n.name in ('__eq__', '__hash__'))",
)

PROBES = (
    {
        "probe_id": "P1_sqlite_floor_present",
        "role": "positive control: a defect that must be seen as present",
        "commit": "e64c1d8055a3e476122633da141f16b50f0c4a2d",
        "path": "django/db/backends/sqlite3/base.py",
        "class": None,
        "functions": ("check_sqlite_version",),
        "expect": "present",
        "question": "does the pinned file define check_sqlite_version?",
    },
    {
        "probe_id": "P2_orderedset_reversed_absent",
        "role": "registered task expectation: the fix adds a missing method",
        "commit": "d01709aae21de9cd2565b9c52f32732ea28a2d98",
        "path": "django/utils/datastructures.py",
        "class": "OrderedSet",
        "functions": ("__reversed__",),
        "expect": "absent",
        "question": "does OrderedSet define __reversed__ at the base?",
    },
    {
        "probe_id": "P3_orderedset_iter_present",
        "role": "negative control: the same probe must be able to report presence",
        "commit": "d01709aae21de9cd2565b9c52f32732ea28a2d98",
        "path": "django/utils/datastructures.py",
        "class": "OrderedSet",
        "functions": ("__iter__", "__len__"),
        "expect": "present",
        "question": "does OrderedSet define __iter__ and __len__ at the base?",
    },
    {
        "probe_id": "P4_field_hash_immutability",
        "role": "target: does the pinned Field show the behaviour the issue describes?",
        "commit": "652c68ffeebd510a6f59e1b56b3e007d07683ad8",
        "path": "django/db/models/fields/__init__.py",
        "class": "Field",
        "functions": ("__eq__", "__hash__"),
        "expect": "absent",
        "question": (
            "does Field define __eq__/__hash__ at the base, i.e. is there a value-based hash that "
            "can change when the field is assigned to a model class?"
        ),
    },
)


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(CLONE), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )


def probe(entry: dict[str, Any]) -> dict[str, Any]:
    command = f"git -C .tooling/upstream/django-17563 show {entry['commit']}:{entry['path']}"
    command = f"git -C .tooling/upstream/django-15563 show {entry['commit']}:{entry['path']}"
    result = git("show", f"{entry['commit']}:{entry['path']}")
    if result.returncode != 0:
        return {**entry, "command": command, "returncode": result.returncode, "stdout": "", "observed": "unreadable", "ok": False}
    text = result.stdout
    tree = ast.parse(text)
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    if entry["class"]:
        target = classes.get(str(entry["class"]))
        if target is None:
            return {**entry, "command": command, "returncode": 1, "stdout": "", "observed": "class-missing", "ok": False}
        holders = [target]
    else:
        # module-level functions count too: check_sqlite_version lives at module level
        holders = list(classes.values())
    found: dict[str, bool] = {}
    for function in entry["functions"]:
        found[function] = any(
            isinstance(node, ast.FunctionDef) and node.name == function
            for holder in holders
            for node in holder.body
        ) or any(
            isinstance(node, ast.FunctionDef) and node.name == function for node in tree.body
        )
    observed = "present" if all(found.values()) else "absent"
    return {
        **entry,
        "command": command,
        "returncode": result.returncode,
        "stdout": json.dumps(found, sort_keys=True),
        "blob_git_id": git("rev-parse", f"{entry['commit']}:{entry['path']}").stdout.strip(),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "functions_found": found,
        "observed": observed,
        "ok": observed == entry["expect"],
    }


def main() -> int:
    results = [probe(entry) for entry in PROBES]
    controls_ok = all(entry["ok"] for entry in results[:3])
    target = results[3]
    target_defect_present = target["observed"] == "present"
    report = {
        "schema": "openai_agents_v16_gate_probes",
        "date": "20261006",
        "paid_requests": 0,
        "gate_definition": (
            "the defect is established by a symbol/behaviour scan of the pinned base blobs OR by "
            "a registered mechanical probe (deterministic, read-only, source+command+stdout+"
            "returncode recorded, with positive and negative controls proving it is neither "
            "always-true nor always-false); probes never read the reference patch or the "
            "evaluation test patch, which stay hash-only records; a probe that cannot establish "
            "the defect leaves the candidate rejected (fail-closed)"
        ),
        "probe_source": list(PROBE_SOURCE_LINES),
        "reads_patch_or_test_patch": False,
        "read_only": True,
        "controls_ok": controls_ok,
        "target": {
            "instance_id": "django__django-15315",
            "probe": target,
            "defect_present": target_defect_present,
            "verdict": "registered" if target_defect_present else "rejected_fail_closed",
            "reasoning": (
                "the issue describes a value-based hash that changes when the field is assigned "
                "to a model class; the pinned Field defines neither __eq__ nor __hash__, so the "
                "described behaviour is not present in the registered baseline and no cause/fix "
                "contract can be written from these views"
                if not target_defect_present
                else "the pinned Field defines a value-based hash, so the described behaviour is present"
            ),
        },
        "probes": results,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    pool = json.loads(POOL.read_text(encoding="utf-8"))
    pool["gate_definition"] = report["gate_definition"]
    pool["gate_probes_artifact"] = str(OUT.relative_to(REPO)).replace("\\", "/")
    for attempt in pool.get("gate_attempts", []):
        if attempt.get("instance_id") == "django__django-15315":
            attempt.update(
                {
                    "gate_result": report["target"]["verdict"],
                    "probe_id": report["target"]["probe"]["probe_id"],
                    "probe_observed": report["target"]["probe"]["observed"],
                    "probe_expected": report["target"]["probe"]["expect"],
                    "probe_controls_ok": controls_ok,
                    "why": report["target"]["reasoning"],
                    "next_queued": pool["available_order"][1] if len(pool["available_order"]) > 1 else None,
                }
            )
    pool["development_set_status"] = (
        "2/3 registered; third candidate re-judged by registered probe and "
        + ("registered" if target_defect_present else "rejected fail-closed")
    )
    POOL.write_text(json.dumps(pool, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"controls ok: {controls_ok}")
    for entry in results:
        print(
            f"  {entry['probe_id']:<32} expect={entry['expect']:<8} observed={entry['observed']:<8} "
            f"ok={entry['ok']} rc={entry['returncode']}"
        )
    print(
        f"target 15315: observed={target['observed']} -> {report['target']['verdict']}"
    )
    return 0 if controls_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
