"""View derivation for the v18 candidates: three disjoint views per task, one per read round.

    # the gate: this must run end to end, and its complete output is what a pass rests on
    .venv-openhands\\Scripts\\python.exe experiments/audits/v18_view_derivation.py
    .venv-openhands\\Scripts\\python.exe experiments/audits/v18_view_derivation.py --probe

The rule, and where each part of it comes from
----------------------------------------------
*Three views, one per read round.*  ``V18_VIEW_SHAPE_AMENDMENT_03`` fixes the sealed confirmation at
three read rounds with one distinct view each, because the K dose was calibrated on this host's N3
shape; confirming on four rounds would calibrate on one shape and confirm on another.

*Pairwise disjoint ranges.*  The same amendment: overlapping ranges present the same lines with the
same line numbers twice, and the plugin arm runs the exact duplicate rule first, so part of any
measured saving would come from duplicate removal rather than from the elision mechanism.  The
control shape is supposed to contain no duplicates.  This script asserts disjointness and **raises**
on violation.

*The defect entity is declared, not guessed.*  Three positional rules were tried and each failed on
one candidate, because a statement may name a class before the method it is about, or may name the
real entity only in the body's description of the fix.  The entity comes from
``V18_DEFECT_ENTITY_DECLARATION_20261010.json`` and this script proves two things about it: verbatim
appearance in the problem statement, with the excerpt recorded, and binding inside the main file.

*The large-definition clause.*  A view must contain the defect segment and must not exceed
``MAX_VIEW_LINES``; a longer definition is represented by its head block, and an enclosed definition
is skipped rather than registered alongside its enclosing one, which is what keeps the ranges
disjoint.

Nothing here is chosen by hand, nothing here reads a test or reference patch, and nothing is paid for.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

CLONE = ROOT / ".tooling/upstream/django-15563"
PROPOSAL = ROOT / "integrations/openai_agents/V18_SELECTION_PROPOSAL_20261010.json"
PARQUET = ROOT / ".tooling/swebench-verified/test.parquet"
RULE = ROOT / "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json"
AMENDMENT_1 = ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json"
AMENDMENT_2 = ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json"
DECLARATION = ROOT / "integrations/openai_agents/V18_DEFECT_ENTITY_DECLARATION_20261010.json"
VIEW_SHAPE_AMENDMENT = (
    ROOT / "integrations/openai_agents/V18_VIEW_SHAPE_AMENDMENT_03_20261010.json"
)
OUT = ROOT / "integrations/openai_agents/V18_VIEW_DERIVATION_RECORD_20261010.json"

#: Three views, one per read round: the shape at which K was calibrated.
VIEWS_PER_CANDIDATE = 3
#: The declared cap on one registered view's length, applied to every candidate.
MAX_VIEW_LINES = 60
PATH_B_FILE_LIMIT = 40
TOP_LEVEL = ("django", "tests", "docs", "scripts", "extras")
FIX_WORDS = ("should", "solution", "fix", "breaks", "wrong", "instead", "must")
STOPWORDS = frozenset(
    """the and for with that this from when not but are was were has have had can could should
    would will its into over under about after before while during without within because however
    therefore description issue version django python file code traceback error raise raises raised
    must may might only also more most much less least such same other another all any both each
    few many some none one two three four five six seven eight nine ten""".split()
)


def git(*args: str) -> str:
    result = subprocess.run(["git", "-C", str(CLONE), *args], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8")


def tracked_files(commit: str) -> list[str]:
    return git("ls-tree", "-r", "--name-only", commit).splitlines()


def file_bytes(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(CLONE), "show", f"{commit}:{path}"], capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{path} is not at {commit}")
    return result.stdout


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def statement_of(instance_id: str) -> str:
    import pyarrow.parquet as pq

    table = pq.read_table(PARQUET, columns=["instance_id", "problem_statement"])
    for row in table.to_pylist():
        if row["instance_id"] == instance_id:
            return str(row["problem_statement"] or "")
    raise RuntimeError(f"{instance_id} is not in the public task table")


def statement_identifiers(statement: str) -> list[str]:
    """Names worth matching a file against, never plain English words."""
    camel = set(re.findall(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b", statement))
    underscored = set(re.findall(r"\b[A-Za-z0-9]*_[A-Za-z0-9_]+\b", statement))
    dotted = set(re.findall(r"\b[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\b", statement))
    names: set[str] = set()
    for value in camel | underscored:
        if value.lower() not in STOPWORDS and not value.startswith("__"):
            names.add(value)
    for value in dotted:
        left, _, right = value.partition(".")
        if left.startswith("github"):
            continue
        names.add(value)
        for part in (left, right):
            if part and part.lower() not in STOPWORDS:
                names.add(part)
    return sorted(names, key=lambda value: (-len(value), value))


def normalise_path(token: str) -> str | None:
    text = token.strip().strip("(),.\"'`")
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)
    segments = [segment for segment in text.split("/") if segment]
    if not text.endswith(".py") or len(segments) < 2:
        return None
    for index, segment in enumerate(segments):
        if segment in TOP_LEVEL:
            candidate = "/".join(segments[index:])
            return candidate if len(candidate.split("/")) >= 2 else None
    return None


def path_candidates(statement: str) -> list[dict[str, Any]]:
    rows = []
    for token in re.findall(r"[A-Za-z0-9_./+:-]*\.py", statement):
        normalised = normalise_path(token)
        if normalised is None:
            continue
        position = statement.find(token)
        window = statement[max(0, position - 260) : position + 260].lower()
        rows.append(
            {
                "token": token,
                "normalised": normalised,
                "basename": normalised.split("/")[-1],
                "position": position,
                "near_fix_language": any(word in window for word in FIX_WORDS),
                "depth": len(normalised.split("/")),
            }
        )
    return rows


def definitions(commit: str, path: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Every definition, enclosing and enclosed, with its own range and depth."""
    source = file_bytes(commit, path).decode("utf-8")
    lines = source.splitlines()
    tree = ast.parse(source)
    found: list[dict[str, Any]] = []

    def walk(node: ast.AST, depth: int) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = node.lineno
            for decorator in getattr(node, "decorator_list", []):
                start = min(start, decorator.lineno)
            found.append(
                {
                    "symbol": node.name,
                    "kind": type(node).__name__,
                    "first_line": start,
                    "last_line": node.end_lineno,
                    "depth": depth,
                }
            )
            for child in ast.iter_child_nodes(node):
                walk(child, depth + 1)
        else:
            for child in ast.iter_child_nodes(node):
                walk(child, depth)

    for node in tree.body:
        walk(node, 0)
    found.sort(key=lambda entry: (entry["first_line"], entry["depth"]))
    if not found:
        raise RuntimeError(f"{path} at {commit} defines nothing")
    return found, lines


def _head_block(entry: dict[str, Any]) -> dict[str, Any]:
    """One definition as a single view of at most MAX_VIEW_LINES lines.

    A short definition is its own view; a long one is represented by its head block, the first
    MAX_VIEW_LINES lines aligned to its first line.  Emitting only the head block is what stops a
    large enclosing definition from contributing ranges that overlap the definitions inside it.
    """
    if entry["last_line"] - entry["first_line"] + 1 <= MAX_VIEW_LINES:
        return {
            "symbol": entry["symbol"],
            "kind": entry["kind"],
            "first_line": entry["first_line"],
            "last_line": entry["last_line"],
            "strategy": "definition",
            "source_depth": entry["depth"],
        }
    return {
        "symbol": f"{entry['symbol']}_head",
        "kind": "Block",
        "first_line": entry["first_line"],
        "last_line": min(entry["first_line"] + MAX_VIEW_LINES - 1, entry["last_line"]),
        "strategy": "head-block-of-a-large-definition",
        "source_depth": entry["depth"],
    }


def _overlaps(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return not (a["last_line"] < b["first_line"] or b["last_line"] < a["first_line"])


def choose_views(
    found: list[dict[str, Any]], defect: dict[str, Any], total_lines: int
) -> list[dict[str, Any]]:
    """Three pairwise disjoint views, each within the cap, containing the defect segment."""
    carrier = _head_block(
        {
            "symbol": defect["symbol"],
            "kind": defect["kind"],
            "first_line": defect["first_line"],
            "last_line": defect["last_line"],
            "depth": defect["depth"],
        }
    )
    if not (carrier["first_line"] <= defect["first_line"] <= carrier["last_line"]):
        raise RuntimeError("the carrier block does not contain the defect's first line")
    ranges: list[dict[str, Any]] = [carrier]

    def candidate_blocks() -> list[dict[str, Any]]:
        out = []
        for entry in found:
            if entry["symbol"] == defect["symbol"] and entry["first_line"] == defect["first_line"]:
                continue
            # an enclosed definition is skipped: its enclosing definition already covers it
            if any(
                taken["first_line"] <= entry["first_line"]
                and entry["last_line"] <= taken["last_line"]
                for taken in ranges
            ):
                continue
            block = _head_block(entry)
            if any(_overlaps(block, taken) for taken in ranges):
                continue
            out.append(block)
        return out

    while len(ranges) < VIEWS_PER_CANDIDATE:
        pool = candidate_blocks()
        right_edge = max(taken["last_line"] for taken in ranges)
        left_edge = min(taken["first_line"] for taken in ranges)
        after = sorted(
            [block for block in pool if block["first_line"] > right_edge],
            key=lambda block: block["first_line"],
        )
        before = sorted(
            [block for block in pool if block["last_line"] < left_edge],
            key=lambda block: -block["last_line"],
        )
        if after:
            ranges.append(after[0])
            continue
        if before:
            ranges.append(before[0])
            continue
        raise RuntimeError(
            f"only {len(ranges)} pairwise disjoint views could be formed, fewer than the "
            f"{VIEWS_PER_CANDIDATE} the read shape requires"
        )

    ranges.sort(key=lambda item: item["first_line"])
    for index, item in enumerate(ranges, start=1):
        item["round"] = index
    for item in ranges:
        if item["first_line"] < 1 or item["last_line"] > total_lines:
            raise RuntimeError(f"view {item['symbol']} falls outside the file")
        if item["last_line"] - item["first_line"] + 1 > MAX_VIEW_LINES:
            raise RuntimeError(
                f"view {item['symbol']} is {item['last_line'] - item['first_line'] + 1} lines, "
                f"over the declared cap of {MAX_VIEW_LINES}"
            )
    for index, first in enumerate(ranges):
        for second in ranges[index + 1 :]:
            if _overlaps(first, second):
                raise RuntimeError(
                    f"views {first['symbol']} ({first['first_line']}-{first['last_line']}) and "
                    f"{second['symbol']} ({second['first_line']}-{second['last_line']}) overlap; "
                    "the views of a task must be pairwise disjoint"
                )
    if not any(item["first_line"] <= defect["first_line"] <= item["last_line"] for item in ranges):
        raise RuntimeError("the defect segment is not inside any chosen view")
    return ranges


def locate_main_file(
    record: dict[str, Any],
    statement: str,
    identifiers: list[str],
    commit: str,
    tracked: list[str],
    declared_main_file: str = "",
) -> str | None:
    """Where the candidate's views come from; a declared main file is tried first."""
    paths = path_candidates(statement)
    record["paths_named_in_statement"] = [row["token"] for row in paths]
    existing = [row for row in paths if row["normalised"] in tracked]
    record["paths_that_exist_at_the_base_commit"] = [row["normalised"] for row in existing]
    record["declared_main_file_named_by_the_statement"] = bool(
        declared_main_file and declared_main_file in [row["normalised"] for row in existing]
    )
    if existing:
        preferred = [row for row in existing if row["near_fix_language"]]
        pool = sorted(
            preferred or existing,
            key=lambda row: (
                0 if declared_main_file and row["normalised"] == declared_main_file else 1,
                -row["depth"],
                row["position"],
            ),
        )
        for row in pool:
            text = file_bytes(commit, row["normalised"]).decode("utf-8", errors="replace")
            if any(name in text for name in identifiers):
                record["derivation_path"] = "A: the statement names a path"
                if declared_main_file and row["normalised"] == declared_main_file:
                    record["why_this_path"] = (
                        "the main file the integrator declared, which the statement also names and "
                        "which passes route A's own condition"
                    )
                else:
                    record["why_this_path"] = (
                        "a path the statement mentions next to its own fix language"
                        if preferred
                        else "the deepest surviving path; no path sat near the statement's fix "
                        "language"
                    )
                record["not_the_first_named_path"] = (
                    "the first named path mentions no identifier from the statement, so it is a "
                    "traceback frame rather than the module the defect is in"
                    if existing[0]["normalised"] != row["normalised"]
                    else "the first named path is also the chosen one"
                )
                return row["normalised"]
    if paths:
        for basename in dict.fromkeys(row["basename"] for row in paths):
            hits = [path for path in tracked if path.endswith("/" + basename)]
            record.setdefault("basename_hits", {})[basename] = len(hits)
            if len(hits) != 1:
                continue
            text = file_bytes(commit, hits[0]).decode("utf-8", errors="replace")
            if any(name in text for name in identifiers):
                record["derivation_path"] = (
                    "A': the statement's path is absent; its basename is unique"
                )
                record["basename_unique_hit"] = basename
                return hits[0]
    record["derivation_path"] = "B: no usable path; identifier matching over tracked files"
    for path in [item for item in tracked if item.endswith(".py")][:PATH_B_FILE_LIMIT]:
        text = file_bytes(commit, path).decode("utf-8", errors="replace")
        if any(name in text for name in identifiers):
            return path
    record["rejected"] = (
        f"no tracked python file among the first {PATH_B_FILE_LIMIT} mentions any identifier from "
        "the statement"
    )
    return None


def derive(
    instance_id: str, statement: str, commit: str, tracked: list[str], declared: dict[str, Any]
) -> dict[str, Any]:
    record: dict[str, Any] = {"instance_id": instance_id, "base_commit": commit}
    identifiers = statement_identifiers(statement)
    record["statement_identifiers"] = identifiers
    main_file = locate_main_file(
        record, statement, identifiers, commit, tracked, str(declared.get("main_file") or "")
    )
    if main_file is None:
        record["usable"] = False
        return record
    record["main_file"] = main_file

    declared_entity = str(declared["defect_entity"])
    declared_file = str(declared["main_file"])
    record["declared_defect_entity"] = declared_entity
    record["declared_main_file"] = declared_file
    record["main_file_matches_the_declaration"] = declared_file == main_file
    failures: list[str] = []
    if declared_file != main_file:
        failures.append("derived_main_file_differs_from_the_declaration")

    proof: dict[str, Any] = {"declared_entity": declared_entity}
    matches = list(re.finditer(re.escape(declared_entity), statement))
    proof["verbatim_in_statement"] = bool(matches)
    proof["statement_occurrence_count"] = len(matches)
    if matches:
        first = matches[0]
        proof["statement_excerpt"] = statement[
            max(0, first.start() - 70) : min(len(statement), first.end() + 70)
        ]
        proof["statement_offsets"] = [match.start() for match in matches]
    else:
        failures.append("declared_entity_not_in_statement")

    found, lines = definitions(commit, main_file)
    entity_entry = next((entry for entry in found if entry["symbol"] == declared_entity), None)
    proof["bound_in_main_file"] = entity_entry is not None
    if entity_entry is not None:
        proof["bound_range"] = {
            "first_line": entity_entry["first_line"],
            "last_line": entity_entry["last_line"],
            "kind": entity_entry["kind"],
            "depth": entity_entry["depth"],
        }
    else:
        failures.append("declared_entity_not_bound_in_main_file")
    record["declaration_proof"] = proof
    if failures:
        record["failures"] = failures
        record["usable"] = False
        record["rejected_because"] = (
            "the declaration could not be proved by the script; the candidate is rejected and no "
            "criterion is relaxed"
        )
        return record

    defect = {
        "symbol": declared_entity,
        "kind": entity_entry["kind"],
        "first_line": entity_entry["first_line"],
        "last_line": entity_entry["last_line"],
        "depth": entity_entry["depth"],
    }
    record["defect_segment"] = {
        "symbol": defect["symbol"],
        "kind": defect["kind"],
        "first_line": defect["first_line"],
        "last_line": defect["last_line"],
        "source": "the integrator's declaration; this script quoted it as the defect-carrying view",
    }
    views = choose_views(found, defect, len(lines))
    raw = file_bytes(commit, main_file)
    blob = git("rev-parse", f"{commit}:{main_file}").strip()
    rows = []
    for view in views:
        leaf = "\n".join(lines[view["first_line"] - 1 : view["last_line"]])
        rows.append(
            {
                "round": view["round"],
                "file": main_file,
                "symbol": view["symbol"],
                "kind": view["kind"],
                "strategy": view["strategy"],
                "first_line": view["first_line"],
                "last_line": view["last_line"],
                "line_count": view["last_line"] - view["first_line"] + 1,
                "tool_name": "read_"
                + re.sub(r"[^a-z0-9]+", "_", view["symbol"].lower()).strip("_"),
                "blob_git_id": blob,
                "file_content_sha256": hashlib.sha256(raw).hexdigest(),
                "view_content_sha256": hashlib.sha256(leaf.encode("utf-8")).hexdigest(),
                "view_content_sha256_convention": (
                    "sha256 of the leaf text: the range's lines joined by a newline, no line "
                    "numbers, no trailing newline"
                ),
            }
        )
    record["views"] = rows
    record["view_count"] = len(rows)
    record["pairwise_disjoint"] = all(
        not _overlaps(first, second)
        for index, first in enumerate(rows)
        for second in rows[index + 1 :]
    )
    record["distinct_tool_names"] = len({row["tool_name"] for row in rows}) == len(rows)
    record["defect_segment_included"] = any(
        row["first_line"] <= defect["first_line"] <= row["last_line"] for row in rows
    )
    record["views_within_cap"] = all(row["line_count"] <= MAX_VIEW_LINES for row in rows)
    record["usable"] = bool(
        record["main_file_matches_the_declaration"]
        and proof["verbatim_in_statement"]
        and proof["bound_in_main_file"]
        and len(rows) == VIEWS_PER_CANDIDATE
        and record["pairwise_disjoint"]
        and record["distinct_tool_names"]
        and record["defect_segment_included"]
        and record["views_within_cap"]
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    parser.add_argument("--candidate", default="")
    parser.add_argument("--declaration", default=str(DECLARATION))
    args = parser.parse_args()

    declaration_path = Path(args.declaration)
    declaration = json.loads(declaration_path.read_text(encoding="utf-8"))
    declared: dict[str, dict[str, Any]] = {}
    for row in declaration["declarations"]:
        for key in (row.get("task"), row.get("task_id"), row.get("instance_id")):
            if key:
                declared[str(key)] = row
    proposal = json.loads(PROPOSAL.read_text(encoding="utf-8"))
    record: dict[str, Any] = {
        "schema": "openai_agents_v18_view_derivation_record",
        "date": "20261010",
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/v18_view_derivation.py",
        "pass_evidence_rule": (
            "a pass rests on the complete output of one run of this script; the artifact records "
            "that run, and no verdict is written here that the run did not print"
        ),
        "rule_files": {
            "literal_rule": {
                "path": "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json",
                "sha256": sha256_of(RULE),
            },
            "amendment_1": {
                "path": "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json",
                "sha256": sha256_of(AMENDMENT_1),
                "status": "superseded in part by amendment 02",
            },
            "amendment_2": {
                "path": "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json",
                "sha256": sha256_of(AMENDMENT_2),
                "status": "in force",
            },
            "view_shape_amendment_3": {
                "path": "integrations/openai_agents/V18_VIEW_SHAPE_AMENDMENT_03_20261010.json",
                "sha256": sha256_of(VIEW_SHAPE_AMENDMENT),
                "status": "in force: three views, one per round, pairwise disjoint",
            },
            "defect_entity_declaration": {
                "path": str(declaration_path.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(declaration_path),
                "decided_by": declaration.get("decided_by"),
                "role": (
                    "an input: it fixes which definition is the defect-carrying view; ranges, the "
                    "length cap, disjointness and every hash stay script derived"
                ),
            },
        },
        "derivation_rule": {
            "route_A": (
                "a path the statement names that exists at the base commit and mentions an "
                "identifier from the statement, with a declared main file tried first"
            ),
            "route_A_prime": (
                "the statement's path is absent but its basename matches exactly one tracked python "
                "file that mentions an identifier from the statement"
            ),
            "route_B": (
                "no usable path: the statement's identifiers matched over the tracked python files "
                "in fixed order, bounded"
            ),
            "view_count": f"{VIEWS_PER_CANDIDATE} views, one per read round",
            "large_definition_clause": (
                f"a view must contain the defect segment and must not exceed MAX_VIEW_LINES = "
                f"{MAX_VIEW_LINES}; a longer definition is represented by its head block and an "
                "enclosed definition is skipped rather than registered beside its enclosing one"
            ),
            "disjointness": (
                "the views of a task must be pairwise disjoint; the script asserts this and raises "
                "on violation, because overlapping views would let the exact duplicate rule claim "
                "part of the saving on a shape whose purpose is to contain no duplicates"
            ),
            "defect_entity": (
                "declared by the integrator and proved by this script: verbatim in the statement, "
                "bound in the main file"
            ),
        },
        "constants": {
            "VIEWS_PER_CANDIDATE": VIEWS_PER_CANDIDATE,
            "MAX_VIEW_LINES": MAX_VIEW_LINES,
            "PATH_B_FILE_LIMIT": PATH_B_FILE_LIMIT,
        },
        "candidates": {},
    }
    for instance_id in ([args.candidate] if args.candidate else list(proposal["selected"])):
        entry = proposal["selected"][instance_id]
        commit = entry["base_commit"]
        chosen = declared.get(instance_id)
        if chosen is None:
            for key, row in declared.items():
                if key.split("__")[-1] == instance_id.split("__")[-1]:
                    chosen = row
                    break
        if chosen is None:
            record["candidates"][instance_id] = {
                "instance_id": instance_id,
                "usable": False,
                "failures": ["no declaration exists for this candidate"],
            }
            print(f"{instance_id} | no declaration; rejected")
            continue
        derived = derive(
            instance_id, statement_of(instance_id), commit, tracked_files(commit), chosen
        )
        record["candidates"][instance_id] = derived
        print("=" * 96)
        print(instance_id, "| usable:", derived.get("usable"), "|", derived.get("derivation_path"))
        print(
            "  main file:",
            derived.get("main_file"),
            "| declared entity:",
            derived.get("declared_defect_entity"),
        )
        proof = derived.get("declaration_proof") or {}
        print(
            "  proof verbatim-in-statement:",
            proof.get("verbatim_in_statement"),
            "| occurrences:",
            proof.get("statement_occurrence_count"),
        )
        print("  proof excerpt:", repr(proof.get("statement_excerpt", "")))
        print(
            "  proof bound-in-main-file:",
            proof.get("bound_in_main_file"),
            "|",
            json.dumps(proof.get("bound_range"), ensure_ascii=False),
        )
        for row in derived.get("views", []):
            print(
                f"   r{row['round']} {row['file']}:{row['first_line']}-{row['last_line']} "
                f"({row['line_count']:3d} lines) {row['symbol']:38s} {row['strategy']:32s} "
                f"view_sha={row['view_content_sha256'][:16]}"
            )
        print(
            "  disjoint:",
            derived.get("pairwise_disjoint"),
            "| views:",
            derived.get("view_count"),
            "| within cap:",
            derived.get("views_within_cap"),
            "| defect included:",
            derived.get("defect_segment_included"),
        )
        if derived.get("failures"):
            print("  FAILURES:", derived["failures"])
    usable = [key for key, value in record["candidates"].items() if value.get("usable")]
    record["usable_candidates"] = usable
    record["usable_count"] = len(usable)
    record["registration_may_proceed"] = len(usable) == 3
    print(
        f"\nusable {len(usable)}/3: {usable} "
        f"| registration_may_proceed={record['registration_may_proceed']}"
    )
    if not args.probe:
        OUT.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
