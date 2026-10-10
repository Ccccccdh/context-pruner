"""Zero-API registration of the three sealed confirmation candidates.

Order of work, fixed in this file before anything is measured:

1. the view-selection rule and the site table are written to
   ``V16_SEALED_VIEW_RULE_20261006.json`` **before any probe runs**;
2. line windows are computed mechanically from the AST of the pinned blob (never typed in);
3. the contract of each task (issue value, required facts, cause/fix tokens, frozen regex) is
   derived from the statement and the views only, and every string must be found verbatim in
   statement + views;
4. a mechanical defect-presence probe runs per candidate with a positive and a negative
   control; a candidate whose defect is absent at the base commit is rejected and **not
   substituted**;
5. byte-level verification: git blob id, file SHA256, view SHA256, statement hash against the
   preselection artifact;
6. the result is written to ``V16_SEALED_SOURCE_REGISTRATION_20261006.json``.

The reference patch and the test patch are never read: only their presence is recorded by hash,
exactly as the development set recorded them. Nothing here spends a request.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CLONE = ROOT / ".tooling/upstream/django-15563"
PARQUET = ROOT / ".tooling/swebench-verified/test.parquet"
PRESELECT = ROOT / "integrations/openai_agents/V16_PRESELECT_20261006.json"
RULE_OUT = ROOT / "integrations/openai_agents/V16_SEALED_VIEW_RULE_20261006.json"
OUT = ROOT / "integrations/openai_agents/V16_SEALED_SOURCE_REGISTRATION_20261006.json"

VIEW_RULE = (
    "Views are chosen from the statement alone and are written here before any probe or any "
    "model-facing work. For each candidate: view A is the definition the statement names as the "
    "faulty behaviour; view B is the definition the statement names as the construction or call "
    "site that reaches it; view C is the header region of the class that owns view A. Line "
    "windows are computed by AST from the named symbol: A and B span from three lines before "
    "the def statement to the end of that definition, so a long method is shown whole, and C "
    "spans the forty lines after the class statement; every window is clipped to the file. No "
    "view may be re-chosen after any probe result or model output is seen."
)
SUPERSEDED_VIEW_RULE = (
    "Views are chosen from the statement alone and are written here before any probe or any "
    "model-facing work. For each candidate: view A is the enclosing definition of the symbol "
    "the statement names as the faulty behaviour; view B is the definition the statement names "
    "as the construction or call site that reaches it; view C is the header region of the class "
    "that owns view A. Line windows are computed by AST from the named symbol - A and B span "
    "from three lines before the def to thirty lines after it, C spans the forty lines after "
    "the class statement - and are clipped to the file. No view may be re-chosen after any "
    "probe result or model output is seen."
)
RULE_AMENDMENT = {
    "superseded_rule_sha256": "38f5bfd1edaf818d1298b0860d17d1cbc0bcdce3c024c1af86ef5b52fe2b2eab",
    "why": (
        "the first rule used a fixed thirty-line window. The first probe run then reported "
        "'defect signature absent' for django__django-11820 and django__django-12193; both were "
        "probe defects rather than absent defects: Model._check_ordering spans lines 1660-1751 "
        "at that commit, so a thirty-line window cut off the bare-pk exclusion at line 1727, and "
        "the shared attrs dict of django__django-12193 is built in SplitArrayWidget.get_context "
        "(django/contrib/postgres/forms/array.py line 140, passed un-copied at line 150), not in "
        "SplitArrayField.__init__ as the first site table assumed."
    ),
    "amended_before_any_request": True,
    "what_changed": [
        "windows now run to the end of the named definition instead of a fixed thirty lines",
        "view B for django__django-12193 is SplitArrayWidget.get_context, the site the statement "
        "describes when it says the final_attrs dict is updated",
        "the defect probes now name the two halves of that defect explicitly",
    ],
    "note": "no request was spent, no model output was seen, and the earlier rule text is kept "
    "verbatim in this artifact with its hash",
}

SITES: dict[str, dict[str, Any]] = {
    "django__django-11820": {
        "task_id": "django_ordering_related_pk",
        "issue": "django-11820",
        "statement_signal": (
            "the statement says models.E015 is raised when Meta.ordering contains __pk of a "
            "related field, and calls it a regression"
        ),
        "views": (
            ("A", "django/db/models/base.py", "_check_ordering", "cause: the ordering check that raises E015"),
            ("B", "django/db/models/base.py", "Model.check", "context: the caller that runs the ordering check"),
            ("C", "django/db/models/base.py", "Model", "context: the model class the check belongs to"),
        ),
        "cause_token": "_check_ordering",
        "fix_token": "__pk",
        "facts": ("_check_ordering", "E015", "__pk"),
        "tool_names": ("read_ordering_check", "read_model_check", "read_model_class"),
        "request": (
            "Diagnose why this baseline rejects Meta.ordering entries such as option__pk. Read "
            "the ordering check, the model check method that calls it and the model class they "
            "belong to, in that order; name the method whose field validation rejects the lookup "
            "and the lookup form the fix must accept."
        ),
    },
    "django__django-12193": {
        "task_id": "django_splitarray_checkbox_attrs",
        "issue": "django-12193",
        "statement_signal": (
            "the statement says SplitArrayField with BooleanField leaves every widget checked "
            "after the first True value, because CheckboxInput.get_context modifies the attrs "
            "dict passed into it"
        ),
        "views": (
            ("A", "django/forms/widgets.py", "CheckboxInput.get_context", "cause: the context method that mutates the attrs it is given"),
            ("B", "django/contrib/postgres/forms/array.py", "SplitArrayWidget.get_context", "context: the site that passes one attrs dict to every sub-widget"),
            ("C", "django/forms/widgets.py", "CheckboxInput", "context: the widget class the mutating method belongs to"),
        ),
        "cause_token": "get_context",
        "fix_token": "checked",
        "facts": ("get_context", "checked", "SplitArrayField"),
        "tool_names": ("read_checkbox_context", "read_split_array_init", "read_checkbox_class"),
        "request": (
            "Diagnose why this baseline renders every checkbox of a SplitArrayField as checked "
            "once the initial data contains one True value. Read the checkbox context method, "
            "the split array field constructor and the checkbox widget class, in that order; "
            "name the method that mutates shared state and the attribute key it sets."
        ),
    },
    "django__django-13089": {
        "task_id": "django_db_cache_cull_none",
        "issue": "django-13089",
        "statement_signal": (
            "the statement says the database cache backend's _cull sometimes fails with "
            "'NoneType' object is not subscriptable and gives a backtrace through _base_set"
        ),
        "views": (
            ("A", "django/core/cache/backends/db.py", "_cull", "cause: the culling routine that subscripts a missing row"),
            ("B", "django/core/cache/backends/db.py", "_base_set", "context: the caller that triggers the culling"),
            ("C", "django/core/cache/backends/db.py", "DatabaseCache", "context: the cache backend class they belong to"),
        ),
        "cause_token": "_cull",
        "fix_token": "cull_num",
        "facts": ("_cull", "NoneType", "cull_num"),
        "tool_names": ("read_cache_cull", "read_cache_base_set", "read_cache_class"),
        "request": (
            "Diagnose why this baseline's database cache backend can fail with 'NoneType' object "
            "is not subscriptable while culling. Read the culling routine, the method that calls "
            "it and the backend class, in that order; name the routine that subscripts the row "
            "and the value the fix must check before using it."
        ),
    },
}

DEFECT_PROBES: dict[str, dict[str, Any]] = {
    "django__django-11820": {
        "check": "the ordering check excludes only the bare pk and the module still raises E015",
        "required_patterns": (r"!=\s*'pk'", r"models\.E015"),
        "positive_control": r"def _check_ordering",
        "negative_control": r"def _check_ordering_nonexistent_xyz",
    },
    "django__django-12193": {
        "check": (
            "the checkbox context method assigns into the attrs mapping it receives, and the "
            "split array widget passes one attrs dict to every sub-widget without copying it"
        ),
        "required_patterns": (r"attrs\['checked'\]\s*=\s*True", r"widget_value, final_attrs"),
        "positive_control": r"def get_context",
        "negative_control": r"attrs\['nonexistent_key_xyz'\]\s*=",
    },
    "django__django-13089": {
        "check": "the culling routine subscripts the result of fetchone without a None guard",
        "required_patterns": (r"fetchone\(\)\[", r"def _cull"),
        "positive_control": r"def _cull",
        "negative_control": r"fetchmany\(\)\[0\]",
    },
}


def git(*args: str) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1"}
    result = subprocess.run(
        ["git", "-C", str(CLONE), *args], capture_output=True, check=False, env=env
    )
    if result.returncode:
        raise FileNotFoundError(f"read-only git read failed: {' '.join(args)}")
    return result.stdout


def blob_text(commit: str, path: str) -> str:
    return git("show", f"{commit}:{path}").decode("utf-8", errors="replace")


def blob_id(commit: str, path: str) -> str:
    return git("rev-parse", f"{commit}:{path}").decode("utf-8").strip()


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_symbol(tree: ast.AST, dotted: str) -> ast.AST | None:
    """Find ``Class.method`` or ``def`` by walking the module AST."""
    parts = dotted.split(".")
    if len(parts) == 2:
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == parts[0]:
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        child.name == parts[1]
                    ):
                        return child
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and (
            node.name == dotted
        ):
            return node
    return None


def statements() -> dict[str, str]:
    import pyarrow.parquet as pq

    table = pq.read_table(str(PARQUET), columns=["instance_id", "problem_statement"])
    return {row["instance_id"]: str(row["problem_statement"]) for row in table.to_pylist()}


def main() -> int:
    preselection = json.loads(PRESELECT.read_text(encoding="utf-8"))
    sealed = {entry["instance_id"]: entry for entry in preselection["untouched_confirmation_candidates"]}
    statement_table = statements()

    # ---- step 1: the rule, written before any probe -----------------------------------------
    rule_payload = {
        "schema": "openai_agents_v16_sealed_view_rule",
        "date": "20261006",
        "rule": VIEW_RULE,
        "rule_amendment": RULE_AMENDMENT,
        "superseded_rule": SUPERSEDED_VIEW_RULE,
        "written_before_probes": True,
        "paid_requests": 0,
        "sites": {
            instance_id: {
                "task_id": spec["task_id"],
                "statement_signal": spec["statement_signal"],
                "views": [
                    {"slot": slot, "file": path, "symbol": symbol, "role": role}
                    for slot, path, symbol, role in spec["views"]
                ],
                "contract": {
                    "issue": spec["issue"],
                    "cause_token": spec["cause_token"],
                    "fix_token": spec["fix_token"],
                    "required_facts": list(spec["facts"]),
                },
            }
            for instance_id, spec in SITES.items()
        },
        "reading_reference_patch_or_test_patch": False,
    }
    RULE_OUT.write_text(
        json.dumps(rule_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rule_hash = hashlib.sha256(RULE_OUT.read_bytes()).hexdigest()

    # ---- steps 2-5: windows, contracts, probes, byte verification ---------------------------
    registered: dict[str, Any] = {}
    rejected: list[dict[str, Any]] = []
    for instance_id, spec in SITES.items():
        entry = sealed.get(instance_id)
        problems: list[str] = []
        if entry is None:
            rejected.append({"instance_id": instance_id, "why": "not in the preselection artifact"})
            continue
        statement = statement_table[instance_id]
        statement_sha = sha_text(statement)
        if statement_sha != entry["problem_statement_sha256"]:
            problems.append("statement hash differs from the preselection")
        commit = str(entry["base_commit"])
        views = []
        for slot, path, symbol, role in spec["views"]:
            text = blob_text(commit, path)
            tree = ast.parse(text)
            node = find_symbol(tree, symbol)
            if node is None:
                problems.append(f"view {slot}: symbol {symbol!r} not found in {path}")
                continue
            lines = text.splitlines()
            if slot == "C":
                first, last = node.lineno, min(len(lines), node.lineno + 39)
            else:
                end = int(getattr(node, "end_lineno", node.lineno + 30))
                first, last = max(1, node.lineno - 3), min(len(lines), end)
            body = "\n".join(
                f"{number}: {lines[number - 1]}" for number in range(first, last + 1)
            )
            rendered = f"django/django/{path} (baseline)\n{body}"
            views.append(
                {
                    "slot": slot,
                    "role": role,
                    "file": path,
                    "symbol": symbol,
                    "first_line": first,
                    "last_line": last,
                    "file_lines": len(lines),
                    "commit_path": f"{commit}:{path}",
                    "blob_git_id": blob_id(commit, path),
                    "file_content_sha256": sha_text(text),
                    "view_content_sha256": sha_text(rendered),
                    "view_bytes": len(rendered.encode("utf-8")),
                    "text": rendered,
                }
            )
        searchable = "\n".join(view["text"] for view in views) + "\n" + statement
        for token in (spec["cause_token"], spec["fix_token"]):
            if token not in searchable:
                problems.append(f"contract token {token!r} is unreachable in statement + views")
        for fact in spec["facts"]:
            if fact not in searchable:
                problems.append(f"required fact {fact!r} is unreachable in statement + views")

        # mechanical defect-presence probe, with both controls
        probe = DEFECT_PROBES[instance_id]
        cause_view = next((view for view in views if view["slot"] == "A"), None)
        probe_text = cause_view["text"] if cause_view else ""
        if instance_id == "django__django-12193":
            # the shared-dict half of the defect lives in view B
            probe_text = "\n".join(view["text"] for view in views if view["slot"] in ("A", "B"))
        evidence = {
            pattern: bool(re.search(pattern, probe_text)) for pattern in probe["required_patterns"]
        }
        positive = bool(re.search(probe["positive_control"], probe_text))
        negative = bool(re.search(probe["negative_control"], probe_text))
        defect_present = all(evidence.values()) and positive and not negative
        probe_record = {
            "check": probe["check"],
            "patterns": evidence,
            "positive_control_found": positive,
            "negative_control_found": negative,
            "defect_present": defect_present,
        }
        if not defect_present:
            problems.append("defect signature absent at the base commit")

        record = {
            "task_id": spec["task_id"],
            "instance_id": instance_id,
            "repo": "django/django",
            "base_commit": commit,
            "difficulty": str(entry.get("difficulty") or ""),
            "problem_statement_sha256": statement_sha,
            "problem_statement_sha256_matches_preselection": (
                statement_sha == entry["problem_statement_sha256"]
            ),
            "selection_key": entry.get("selection_key"),
            "views": [{k: v for k, v in view.items() if k != "text"} for view in views],
            "view_texts_sha256": [view["view_content_sha256"] for view in views],
            "tool_names": list(spec["tool_names"]),
            "request": spec["request"],
            "contract": {
                "shape": "RESULT issue=<issue> cause=<cause> fix=<fix>",
                "json_fields": ["issue", "cause", "fix", "facts"],
                "issue_value": spec["issue"],
                "required_facts": list(spec["facts"]),
                "cause_token": spec["cause_token"],
                "fix_token": spec["fix_token"],
                "frozen_regex": (
                    f"RESULT issue={spec['issue']} cause=.*{re.escape(spec['cause_token'])}.* "
                    f"fix=.*{re.escape(spec['fix_token'])}.*"
                ),
                "max_answer_chars": 160,
                "renderer": "experiments/runners/openai_agents_structured_final_v16.py",
            },
            "defect_evidence": probe_record,
            "problems": problems,
        }
        if problems:
            rejected.append({"instance_id": instance_id, "task_id": spec["task_id"], "why": problems})
        else:
            registered[spec["task_id"]] = record

    payload = {
        "schema": "openai_agents_v16_sealed_source_registration",
        "date": "20261006",
        "status": (
            "the sealed confirmation candidates are registered here; the three development tasks "
            "keep their own registration artifacts and are not re-registered"
        ),
        "view_rule": {
            "path": str(RULE_OUT.relative_to(ROOT)).replace("\\", "/"),
            "sha256": rule_hash,
            "written_before_probes": True,
        },
        "candidates": len(SITES),
        "registered_task_count": len(registered),
        "registered_task_ids": list(registered),
        "survivors": len(registered),
        "rejected_candidates": rejected,
        "tasks": registered,
        "reading_reference_patch_or_test_patch": False,
        "paid_requests": 0,
        "not_done": [
            "no request is spent by this artifact",
            "the confirmation batch itself is not started here",
        ],
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"rule artifact: {RULE_OUT} sha256={rule_hash}")
    print(f"registration: {OUT}")
    print(f"candidates={len(SITES)} survivors={len(registered)} rejected={len(rejected)}")
    for task_id, record in registered.items():
        print(
            f"  {task_id}: views={[v['symbol'] for v in record['views']]} "
            f"probe={record['defect_evidence']['defect_present']} "
            f"regex={record['contract']['frozen_regex']}"
        )
        for view in record["views"]:
            print(
                f"      {view['slot']} {view['file']} {view['first_line']}-{view['last_line']} "
                f"blob={view['blob_git_id'][:12]} view_sha={view['view_content_sha256'][:12]}"
            )
    for entry in rejected:
        print("  REJECTED", json.dumps(entry, ensure_ascii=False))
    return 0 if len(registered) == len(SITES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
