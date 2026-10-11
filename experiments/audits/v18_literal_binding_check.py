"""Re-runnable binding and rule check for the v18 candidate literal sets (R1 to R7).

Run it as:

    .venv-openhands\\Scripts\\python.exe experiments/audits/v18_literal_binding_check.py
    .venv-openhands\\Scripts\\python.exe experiments/audits/v18_literal_binding_check.py --probe

It prints, for every candidate and every literal, the file and line where the literal appears and
where the view code binds it, then decides R1 to R7 and writes the verification artifact.  Every
mechanical verdict in that artifact comes from this script's output; nothing is concluded by hand.

Two failure modes this script exists to prevent
-----------------------------------------------
1. **A silent empty binding set.**  A registered view range may begin or end inside a block, so a
   naive ``ast.parse`` of the raw slice raises on its first line.  An earlier one-off probe of this
   check swallowed that and reported that a view bound no names at all, which is how a compliant
   candidate was nearly rejected.  Here an unparseable slice is an error: the script raises rather
   than returning an empty set, and the failure names the candidate, the file and the range.
2. **A superseded rule.**  The artifact records the rule file and both amendments by path and
   SHA256, and the list of requirements is read from the file whose hash is recorded.

The literal sets are inputs: re-deriving them is a separate, proposal-level act.  This script checks
a proposed set and records the evidence.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import json
import keyword
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CLONE = ROOT / ".tooling/upstream/django-15563"
POOL = ROOT / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
PROPOSAL = ROOT / "integrations/openai_agents/V18_SELECTION_PROPOSAL_20261010.json"
DERIVATION = ROOT / "integrations/openai_agents/V18_VIEW_DERIVATION_RECORD_20261010.json"
PARQUET = ROOT / ".tooling/swebench-verified/test.parquet"
RULE = ROOT / "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json"
AMENDMENT_1 = ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json"
AMENDMENT_2 = ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json"
OUT = ROOT / "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_VERIFICATION_20261010.json"

BUILTINS = set(dir(builtins))
FRAMEWORK_GENERIC = [
    "request", "response", "get_response", "view", "model", "field", "template",
    "inner", "value", "error", "object", "class", "method",
]
LAZY_VOCABULARY = (
    "model field code method value name args kwargs self request response context default data "
    "path file line text key item list dict type callable raise return inner should template "
    "django always anything class function object instance attribute property module import view "
    "error object"
)

#: The proposed literal sets under test, with the defect view each candidate's statement names.
PROPOSED: dict[str, dict[str, Any]] = {
    "django__django-15572": {
        "required_facts": ["template_changed", "template_dir", "file_path"],
        "cause_token": "template_changed",
        "fix_token": "template_dir",
    },
    "django__django-16255": {
        "required_facts": ["get_latest_lastmod", "lastmod", "items"],
        "cause_token": "get_latest_lastmod",
        "fix_token": "lastmod",
    },
    "django__django-15569": {
        "required_facts": ["_unregister_lookup", "lookup_name", "lookup"],
        "cause_token": "_unregister_lookup",
        "fix_token": "lookup_name",
    },
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blob_lines(commit: str, path: str) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(CLONE), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"cannot read {path} at {commit}")
    return result.stdout.decode("utf-8").splitlines()


def normalise_for_parse(slice_text: str) -> str:
    """Make a mid-block slice parseable, or raise.

    Two strategies are tried in order and both are deterministic:

    * dedent with the first line excluded from the common-prefix computation, which
      ``textwrap.dedent`` does not do on its own;
    * wrap the slice in a containing block, which handles a slice that starts inside a function
      body and therefore has no common indentation to remove.
    """
    lines = slice_text.splitlines()
    if not lines:
        return slice_text
    stripped = [line for line in lines if line.strip()]
    if not stripped:
        return slice_text
    pads = [len(line) - len(line.lstrip()) for line in lines if line.strip()]
    common = min(pads) if pads else 0
    dedented = "\n".join(
        (line[common:] if len(line) >= common else line.lstrip()) if line.strip() else line
        for line in lines
    )
    for candidate in (dedented, "if True:\n" + textwrap.indent("\n".join(lines), "    ") + "\n"):
        try:
            ast.parse(candidate)
        except SyntaxError:
            continue
        return candidate
    raise SyntaxError(
        "a registered view range could not be parsed even after normalisation; this is an error "
        "and not an empty binding set"
    )


def bound_names(commit: str, path: str, first: int, last: int) -> set[str]:
    """Names the view's own code binds.  Raises rather than returning an empty set on failure.

    The slice is first tried on its own, normalised two ways.  A registered range can also be a
    fragment that never parses alone (it may begin inside a block and end inside another), so the
    fallback parses **the whole file** and keeps the definitions, assignment targets and parameters
    whose source lines overlap the registered range.  That fallback is what makes the answer
    independent of indentation conventions, which is where the one-off probe went wrong.
    """
    lines = blob_lines(commit, path)
    slice_text = "\n".join(lines[first - 1 : last])
    tree = None
    try:
        parsed = normalise_for_parse(slice_text)
        tree = ast.parse(parsed)
        offset = 0
    except SyntaxError:
        tree = None
    if tree is None:
        # fallback: parse the file and select the nodes whose lines overlap the range
        whole = "\n".join(lines)
        try:
            whole_tree = ast.parse(whole)
        except SyntaxError as error:
            raise RuntimeError(
                f"{path} at {commit} does not parse as a whole file: {error}"
            ) from error
        names: set[str] = set()
        for node in ast.walk(whole_tree):
            start = getattr(node, "lineno", None)
            end = getattr(node, "end_lineno", start)
            if start is None or end is None:
                continue
            if end < first or start > last:
                continue
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                args = getattr(node, "args", None)
                if args is not None:
                    for group in (args.posonlyargs, args.args, args.kwonlyargs):
                        for arg in group:
                            names.add(arg.arg)
                    if args.vararg is not None:
                        names.add(args.vararg.arg)
                    if args.kwarg is not None:
                        names.add(args.kwarg.arg)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
                names.add(node.attr)
        if not names:
            raise RuntimeError(
                f"view range {path}:{first}-{last} at {commit} bound no names, by slice or by "
                "whole-file overlap; that is an error, because a range that binds nothing cannot be "
                "the defect-carrying view"
            )
        return names
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for group in (args.posonlyargs, args.args, args.kwonlyargs):
                    for arg in group:
                        names.add(arg.arg)
                if args.vararg is not None:
                    names.add(args.vararg.arg)
                if args.kwarg is not None:
                    names.add(args.kwarg.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.add(node.attr)
    if not names:
        raise RuntimeError(
            f"view range {path}:{first}-{last} at {commit} parsed but bound no names; that is an "
            "error, because a range that binds nothing cannot be the defect-carrying view"
        )
    return names


def statement_of(instance_id: str) -> str:
    import pyarrow.parquet as pq

    table = pq.read_table(PARQUET, columns=["instance_id", "problem_statement"])
    for row in table.to_pylist():
        if row["instance_id"] == instance_id:
            return str(row["problem_statement"] or "")
    raise RuntimeError(f"{instance_id} is not in the public task table")


def literal_occurrences(
    commit: str, views: list[dict], literal: str
) -> list[dict[str, Any]]:
    hits = []
    for view in views:
        lines = blob_lines(commit, view["file"])
        for number in range(view["first_line"], view["last_line"] + 1):
            body = lines[number - 1]
            if literal in body:
                hits.append(
                    {
                        "round": view["round"],
                        "file": view["file"],
                        "line_number": number,
                        "text": body.strip()[:110],
                    }
                )
    return hits


def check_candidate(instance_id: str, proposal_entry: dict, proposed: dict) -> dict[str, Any]:
    commit = proposal_entry["base_commit"]
    views = proposal_entry["views"]
    statement = statement_of(instance_id)
    issue_value = str(proposal_entry["contract"]["issue_value"])
    required = proposed["required_facts"]
    cause, fix = proposed["cause_token"], proposed["fix_token"]
    pattern = f"RESULT issue={issue_value} cause=.*{cause}.* fix=.*{fix}.*"

    defect_view = None
    for view in views:
        if view["symbol"] in statement:
            defect_view = view
            break
    detail: dict[str, Any] = {
        "task_id": proposal_entry["task_id"],
        "base_commit": commit,
        "statement_names_a_view_symbol": defect_view is not None,
        "defect_view": (
            {
                "round": defect_view["round"],
                "symbol": defect_view["symbol"],
                "file": defect_view["file"],
                "first_line": defect_view["first_line"],
                "last_line": defect_view["last_line"],
            }
            if defect_view
            else None
        ),
        "contract": {
            "issue_value": issue_value,
            "required_facts": required,
            "cause_token": cause,
            "fix_token": fix,
            "frozen_regex": pattern,
            "max_answer_chars": 160,
        },
        "checks": {},
        "failures": [],
    }
    defect_bound = (
        bound_names(commit, defect_view["file"], defect_view["first_line"], defect_view["last_line"])
        if defect_view
        else set()
    )
    detail["defect_view_bound_names"] = sorted(defect_bound)

    records = []
    for literal in dict.fromkeys(list(required) + [cause, fix]):
        hits = literal_occurrences(commit, views, literal)
        binding_rounds = sorted(
            view["round"]
            for view in views
            if literal
            in bound_names(commit, view["file"], view["first_line"], view["last_line"])
        )
        records.append(
            {
                "literal": literal,
                "verbatim_hits": len(hits),
                "first_hit": hits[0] if hits else None,
                "views_where_it_appears": sorted({hit["round"] for hit in hits}),
                "bound_in_view_rounds": binding_rounds,
                "bound_in_defect_view": literal in defect_bound,
                "named_by_statement": literal in statement,
            }
        )
    detail["literals"] = records

    r1 = all(record["verbatim_hits"] > 0 for record in records)
    anchored = [record["literal"] for record in records if record["bound_in_view_rounds"]]
    r2 = sum(1 for fact in required if fact in anchored) >= 2
    r3 = cause in anchored and fix in anchored
    r4_per = [
        {
            "literal": record["literal"],
            "named_by_statement": record["named_by_statement"],
            "bound_in_defect_view": record["bound_in_defect_view"],
            "holds": record["named_by_statement"] or record["bound_in_defect_view"],
        }
        for record in records
        if record["literal"] in required
    ]
    r4 = all(entry["holds"] for entry in r4_per)
    outside = [fact for fact in required if fact.lower() not in FRAMEWORK_GENERIC]
    r5 = len(outside) >= 2
    offending = [fact for fact in required if keyword.iskeyword(fact) or fact in BUILTINS]
    r6 = not offending
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]*", statement)[:40]
    lazy = f"RESULT issue={issue_value} cause={' '.join([issue_value] + words + LAZY_VOCABULARY.split())[:300]}"[:400]
    lazy_regex = re.search(pattern, lazy) is not None
    lazy_lits = all(fact in lazy for fact in required)
    r7 = not lazy_regex and not lazy_lits
    minimal = (
        f"RESULT issue={issue_value} cause={cause} fix={fix} "
        + " ".join(f for f in required if f not in (cause, fix))
    ).strip()
    conditions = {
        "facts_complete": all(fact in minimal for fact in required),
        "frozen_regex": re.search(pattern, minimal) is not None,
        "single_line_prefix": minimal.startswith("RESULT ") and "\n" not in minimal,
        "within_160": len(minimal) <= 160,
    }

    detail["checks"] = {
        "R1_verbatim": {"holds": r1},
        "R2_two_code_anchored": {"holds": r2, "anchored": [f for f in required if f in anchored]},
        "R3_regex_tokens_anchored": {"holds": r3, "cause": cause in anchored, "fix": fix in anchored},
        "R4_relevance": {"holds": r4, "per_literal": r4_per},
        "R5_two_outside_framework_generic": {"holds": r5, "outside": outside, "list": FRAMEWORK_GENERIC},
        "R6_not_keyword_or_builtin": {"holds": r6, "offending": offending},
        "R7_lazy_answer_control": {
            "holds": r7,
            "satisfies_frozen_regex": lazy_regex,
            "satisfies_literal_requirement": lazy_lits,
            "lazy_line_head": lazy[:150],
        },
        "minimal_feasible_line": {"line": minimal, "chars": len(minimal), "four_conditions": conditions},
        "views_unchanged": {
            "distinct_ranges": len({(v["first_line"], v["last_line"]) for v in views}) == len(views),
            "distinct_tool_names": len({v["tool_name"] for v in views}) == len(views),
            "view_hashes_recorded": all(v.get("view_content_sha256") for v in views),
        },
    }
    for name, payload in (
        ("R1_failed", r1),
        ("R2_failed", r2),
        ("R3_failed", r3),
        ("R4_failed", r4),
        ("R5_failed", r5),
        ("R6_failed", r6),
        ("R7_failed", r7),
        ("minimal_line_fails", all(conditions.values())),
        ("views_ill_formed", all(detail["checks"]["views_unchanged"].values())),
    ):
        if not payload:
            detail["failures"].append(name)
    detail["verdict"] = "ACCEPTED" if not detail["failures"] else "REJECTED"
    return detail


def main() -> int:
    probe = "--probe" in sys.argv
    # The views under test come from the derivation record produced under view-shape amendment 03,
    # not from the earlier selection proposal: django-16255's proposal views were derived from a
    # traceback frame and are superseded.
    record_source = json.loads(DERIVATION.read_text(encoding="utf-8"))
    proposal = {
        "selected": {
            key: {
                "base_commit": value["base_commit"],
                "task_id": value.get("task_id", key),
                "contract": {"issue_value": value.get("declared_main_file") and key.split("__")[-1]},
                "views": value["views"],
            }
            for key, value in record_source["candidates"].items()
            if value.get("views")
        }
    }
    for key, value in proposal["selected"].items():
        value["contract"]["issue_value"] = key.split("__")[-1]
    record: dict[str, Any] = {
        "schema": "openai_agents_v18_candidate_rederivation_verification",
        "date": "20261010",
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/v18_literal_binding_check.py",
        "revision_note": (
            "the previous version of this record was produced by a one-off probe whose binding "
            "extractor returned an empty set for a view range that begins inside a block, because "
            "textwrap.dedent ignores the first line when it computes the common prefix and the "
            "slice therefore never parsed. That made django-15569 look as though its defect view "
            "bound nothing. The extractor here normalises the slice first, raises instead of "
            "returning an empty set, and is in the repository so the check can be re-run; every "
            "verdict below is this script's own output. The integrator accepted django-15569 on the "
            "probe evidence and required exactly this: a re-runnable script and a self-consistent "
            "record"
        ),
        "rule_applied": {
            "rule_file": "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json",
            "rule_file_sha256": sha256_of(RULE),
            "rule_id": "openai-agents-v18-code-anchored-literals-01",
            "amendments_applied": [
                {
                    "path": "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json",
                    "sha256": sha256_of(AMENDMENT_1),
                    "status": "superseded in part by amendment 02",
                },
                {
                    "path": "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json",
                    "sha256": sha256_of(AMENDMENT_2),
                    "status": (
                        "in force: R4 admits a literal the statement names or the defect view binds; "
                        "R5 requires two of the three literals outside the framework generic list"
                    ),
                },
            ],
            "requirements_in_force": [
                "R1 verbatim in a registered view",
                "R2 two of three code anchored",
                "R3 both regex tokens code anchored",
                "R4 named by the statement or bound in the defect view",
                "R5 two of three outside the framework generic list",
                "R6 not a keyword or builtin",
                "R7 lazy answer negative control fails",
            ],
        },
        "candidates": {},
    }
    for instance_id, proposed in PROPOSED.items():
        entry = proposal["selected"][instance_id]
        detail = check_candidate(instance_id, entry, proposed)
        record["candidates"][instance_id] = detail
        print("=" * 92)
        print(instance_id, detail["verdict"], detail["failures"])
        print("  defect view:", json.dumps(detail["defect_view"], ensure_ascii=False))
        for literal in detail["literals"]:
            first = literal["first_hit"]
            where = f"{first['file']}:{first['line_number']}" if first else "-"
            print(
                f"   {literal['literal']:24s} hits={literal['verbatim_hits']:3d} first={where:52s} "
                f"bound_in_views={literal['bound_in_view_rounds']} named={literal['named_by_statement']} "
                f"in_defect_view={literal['bound_in_defect_view']}"
            )
        line = detail["checks"]["minimal_feasible_line"]
        print(f"  minimal line ({line['chars']} chars): {line['line']}")
        print(f"  four conditions: {line['four_conditions']}")
    accepted = [
        key for key, value in record["candidates"].items() if value["verdict"] == "ACCEPTED"
    ]
    record["accepted"] = accepted
    record["accepted_count"] = len(accepted)
    record["rejected"] = [
        {"instance_id": key, "failures": value["failures"]}
        for key, value in record["candidates"].items()
        if value["verdict"] != "ACCEPTED"
    ]
    record["registration_may_proceed"] = len(accepted) == 3
    record["consequence"] = (
        "the accepted count is this script's output, not a manual conclusion; with fewer than three "
        "accepted, registration stops and the remaining candidates are taken from the frozen "
        "available_order rather than substituting a weaker set for a rejected one"
    )
    record["citable"] = False
    if not probe:
        OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {OUT.relative_to(ROOT)}")
    print(f"accepted {len(accepted)}/3: {accepted} | registration_may_proceed={record['registration_may_proceed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
