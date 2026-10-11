"""Write the v18 sealed source registration, in one pass, from the frozen v18 artifacts.

What this script is
-------------------
``V18_SOURCE_REGISTRATION_20261010.json`` is the artifact
``experiments/runners/openai_agents_v18_registry.py`` reads as its *sealed* task table.  Nothing
in it is typed by hand where a value exists in an artifact or can be recomputed:

* base commit, difficulty and the problem statement come from the public SWE-bench table;
* the problem-statement digest is **recomputed** here and compared afresh with the value the
  candidate pool pinned, which is the action
  ``V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json`` requires before registration (the
  ``statement_hash_matches_preselection`` booleans carried in the two selection proposals are
  stale, and are not carried forward);
* the views, ranges, tool names and blob/file hashes are read from
  ``V18_VIEW_DERIVATION_RECORD_20261010.json`` and re-verified against the pinned base commits;
* the rendered-view digest the registry verifies against is recomputed from the blob with the
  registry's own rendering convention, and the derivation record's leaf-text digest is carried
  beside it, because the two artifacts hash different strings and conflating them is exactly the
  double-hashing incident this project already had once;
* the contract, the literal rule verdicts R1 to R7, the minimal feasible line and the
  "views unchanged" checks are read from the frozen verification record, whose digest is recorded
  here, together with the supplement that prints R7 per candidate.

Two v18 artifacts are deliberately **not** registered:

* the earlier selection branch's view set for django-16255, which was derived from a traceback
  frame and is superseded by view-shape amendment 03;
* the earlier erroneous derivation that named ``django/core/handlers/exception.py`` as
  django-16255's main file.  That derivation is a process record, it stays on record in the
  superseded proposal artifacts, and it is not registered here.

Nothing is overwritten: the artifact is written once and a second write is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v18_registry as registry  # noqa: E402

OUT = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"

DERIVATION = ROOT / "integrations/openai_agents/V18_VIEW_DERIVATION_RECORD_20261010.json"
VERIFICATION = (
    ROOT / "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_VERIFICATION_20261010.json"
)
SUPPLEMENT = (
    ROOT / "integrations/openai_agents/V18_LAZY_NEGATIVE_CONTROL_SUPPLEMENT_20261010.json"
)
DECLARATION = ROOT / "integrations/openai_agents/V18_DEFECT_ENTITY_DECLARATION_20261010.json"
VIEW_SHAPE_AMENDMENT_3 = (
    ROOT / "integrations/openai_agents/V18_VIEW_SHAPE_AMENDMENT_03_20261010.json"
)
LITERAL_RULE = (
    ROOT / "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json"
)
LITERAL_AMENDMENT_1 = (
    ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json"
)
LITERAL_AMENDMENT_2 = (
    ROOT / "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json"
)
REDERIVATION_PROPOSAL = (
    ROOT / "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json"
)
SELECTION_PROPOSAL_REV2 = (
    ROOT / "integrations/openai_agents/V18_SELECTION_PROPOSAL_REV2_20261010.json"
)
POOL = ROOT / "integrations/openai_agents/V16_CANDIDATE_POOL_20261006.json"
PARQUET = ROOT / ".tooling/swebench-verified/test.parquet"
VIEW_DERIVATION_SCRIPT = ROOT / "experiments/audits/v18_view_derivation.py"
BINDING_SCRIPT = ROOT / "experiments/audits/v18_literal_binding_check.py"

MAX_VIEW_LINES = 60
VIEWS_PER_TASK = 3

#: The declared defect entity per candidate, read from the declaration; the instance id and
#: task id are the integrator's own names for the same three tasks.
TASKS = (
    {
        "instance_id": "django__django-15572",
        "declared_short_id": "django-15572",
        "role_rounds": (
            "context: the loader reset helper the watcher calls",
            "context: the watcher that listens for template changes",
            "cause: the signal handler whose directory comparison is wrong",
        ),
        "request": (
            "Diagnose why this baseline's autoreload watcher can raise on an empty string when "
            "the template directories are empty or absent. Read the loader reset helper, the "
            "watcher that listens for template changes and the signal handler that decides "
            "whether a template file changed, in that order; name the handler whose directory "
            "comparison is wrong and the parameter the fix must compare against the template "
            "directories."
        ),
    },
    {
        "instance_id": "django__django-16255",
        "declared_short_id": "django-16255",
        "role_rounds": (
            "cause: the method whose maximum over an empty sequence raises",
            "context: the method that builds the url list",
            "context: the sitemap class that supplies the items",
        ),
        "request": (
            "Diagnose why this baseline's sitemap index can raise ValueError when a sitemap has "
            "no items and its lastmod is callable. Read the method that takes the latest lastmod "
            "over the sitemap's items, the method that builds the url list and the generic "
            "sitemap class that supplies the items, in that order; name the method whose maximum "
            "over an empty sequence raises and the keyword argument the fix must accept."
        ),
    },
    {
        "instance_id": "django__django-15569",
        "declared_short_id": "django-15569",
        "role_rounds": (
            "cause: the method that leaves the cached lookups in place",
            "context: the descent helper beside it",
            "context: the references expression resolver",
        ),
        "request": (
            "Diagnose why this baseline keeps serving a stale registered lookup after a lookup is "
            "unregistered. Read the method that unregisters one lookup from the registry, the "
            "function that descends a select_related chain and the function that resolves a "
            "references expression, in that order; name the method that leaves the cached lookups "
            "in place and the parameter that carries the name of the lookup it must clear."
        ),
    },
)

ROUTING_RULE = {
    "route_A": (
        "a path the statement names that exists at the base commit and mentions an identifier "
        "from the statement, with a declared main file tried first"
    ),
    "route_A_prime": (
        "the statement's path is absent but its basename matches exactly one tracked python file "
        "that mentions an identifier from the statement"
    ),
    "route_B": (
        "no usable path: the statement's identifiers matched over the tracked python files in "
        "fixed order, bounded"
    ),
    "large_definition_clause": (
        "a view must contain the defect segment and must not exceed MAX_VIEW_LINES = 60; a longer "
        "definition is represented by its head block and an enclosed definition is skipped rather "
        "than registered beside its enclosing one"
    ),
    "disjointness": (
        "the three views of a task are pairwise disjoint line ranges; the deriving script asserts "
        "this and raises on violation"
    ),
    "defect_entity": (
        "declared by the integrator and proved by the deriving script: verbatim in the statement "
        "and bound inside the main file"
    ),
    "routing_note": (
        "route B was not used by any candidate of this set; the two routes that decided it are A "
        "for django-15572 and django-16255 and A' for django-15569, whose statement names only a "
        "blame url rather than a repository path"
    ),
}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(commit: str, path: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(registry.CLONE), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise SystemExit(f"cannot read {path} at {commit} from the pinned clone")
    return result.stdout


def statement_of(instance_id: str) -> dict[str, Any]:
    import pyarrow.parquet as pq

    table = pq.read_table(
        str(PARQUET),
        columns=["instance_id", "repo", "base_commit", "difficulty", "problem_statement"],
    )
    for row in table.to_pylist():
        if row["instance_id"] == instance_id:
            return row
    raise SystemExit(f"{instance_id} is absent from the public task table")


def pool_values() -> dict[str, str]:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    return {
        str(entry["instance_id"]): str(entry.get("problem_statement_sha256", ""))
        for entry in pool.get("candidates") or []
    }


def build_task(
    spec: dict[str, Any],
    declaration_row: dict[str, Any],
    derivation_row: dict[str, Any],
    verification_row: dict[str, Any],
    pool: dict[str, str],
) -> dict[str, Any]:
    instance_id = spec["instance_id"]
    row = statement_of(instance_id)
    commit = str(row["base_commit"])
    repo = str(row["repo"])
    statement = str(row["problem_statement"])
    recomputed = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    pool_value = pool.get(instance_id, "")

    if commit != str(derivation_row["base_commit"]) or commit != str(
        verification_row["base_commit"]
    ):
        raise SystemExit(f"{instance_id}: the artifacts disagree about the base commit")

    views: list[dict[str, Any]] = []
    for position, view in enumerate(derivation_row["views"]):
        raw = _git(commit, str(view["file"]))
        if hashlib.sha256(raw).hexdigest() != str(view["file_content_sha256"]):
            raise SystemExit(f"{instance_id}: the pinned file content hash changed")
        lines = raw.decode("utf-8").splitlines()
        first, last = int(view["first_line"]), int(view["last_line"])
        leaf = "\n".join(lines[first - 1 : last])
        leaf_sha = hashlib.sha256(leaf.encode("utf-8")).hexdigest()
        if leaf_sha != str(view["view_content_sha256"]):
            raise SystemExit(
                f"{instance_id}/round {view['round']}: the derivation record's leaf digest is "
                "not reproduced from the pinned blob"
            )
        rendered = registry.view_text(commit, str(view["file"]), first, last, repo)
        views.append(
            {
                "slot": "ABC"[position],
                "round": int(view["round"]),
                "role": spec["role_rounds"][position],
                "file": str(view["file"]),
                "symbol": str(view["symbol"]),
                "kind": str(view["kind"]),
                "strategy": str(view["strategy"]),
                "first_line": first,
                "last_line": last,
                "line_count": int(view["line_count"]),
                "within_the_60_line_cap": int(view["line_count"]) <= MAX_VIEW_LINES,
                "tool_name": str(view["tool_name"]),
                "commit_path": f"{commit}:{view['file']}",
                "blob_git_id": str(view["blob_git_id"]),
                "file_content_sha256": str(view["file_content_sha256"]),
                "view_content_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
                "leaf_view_content_sha256": leaf_sha,
                "view_bytes": len(rendered.encode("utf-8")),
                "view_content_sha256_convention": (
                    "sha256 of the rendered view text: '<repo>/<file> (baseline)' and then each "
                    "line of the range as '<number>: <text>'; this is the string the registry "
                    "re-renders and verifies against"
                ),
                "leaf_view_content_sha256_convention": (
                    "sha256 of the leaf text: the range's lines joined by a newline, no line "
                    "numbers, no trailing newline; this is the derivation record's own convention "
                    "and it is carried beside the rendered digest rather than instead of it"
                ),
            }
        )

    ranges = [(v["file"], v["first_line"], v["last_line"]) for v in views]
    disjoint = len(set(ranges)) == len(ranges) and all(
        a[1] > b[2] or b[1] > a[2] or a[0] != b[0]
        for i, a in enumerate(ranges)
        for b in ranges[i + 1 :]
    )
    if len(views) != VIEWS_PER_TASK or not disjoint:
        raise SystemExit(f"{instance_id}: the registered views are not three disjoint ranges")

    contract = dict(verification_row["contract"])
    rule_block = verification_row["checks"]
    defect = verification_row["defect_view"]
    return {
        "task_id": str(declaration_row["task_id"]),
        "instance_id": instance_id,
        "declared_short_id": spec["declared_short_id"],
        "repo": repo,
        "base_commit": commit,
        "difficulty": str(row["difficulty"]),
        "problem_statement_bytes": len(statement.encode("utf-8")),
        "problem_statement_sha256": recomputed,
        "problem_statement_sha256_matches_preselection": recomputed == pool_value,
        "statement_hash_reconciliation": {
            "recomputed_from_the_public_table": recomputed,
            "candidate_pool_value": pool_value,
            "matches_the_candidate_pool": recomputed == pool_value,
            "stale_booleans_not_carried_forward": (
                "V18_SELECTION_PROPOSAL_20261010.json and V18_SELECTION_PROPOSAL_REV2_20261010.json "
                "both record statement_hash_matches_preselection = false; that comparison was "
                "made before the diagnosis and the diagnosis, in the same artifact, already shows "
                "the pool value equal to the true sha256, so the boolean is recomputed here"
            ),
            "matching_hypothesis": "sha256 of the raw statement string, with no normalisation",
            "reconciliation_of_record": (
                "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json"
            ),
            "registration_rule": (
                "the registration may not silently carry false: either the digest matches the "
                "pool on a fresh comparison, or the mismatch is recorded as an open provenance gap"
            ),
        },
        "derivation_path": str(derivation_row["derivation_path"]),
        "derivation_why_this_path": str(
            derivation_row.get("why_this_path")
            or (
                "the statement names no repository path, so route A' decided it: the basename "
                f"{derivation_row.get('basename_unique_hit', '')!r} matches exactly one tracked "
                "python file that mentions an identifier from the statement"
            )
        ),
        "derivation_not_the_first_named_path": str(
            derivation_row.get("not_the_first_named_path", "")
        ),
        "declared_main_file": str(declaration_row["main_file"]),
        "main_file_matches_the_declaration": bool(
            derivation_row.get("main_file_matches_the_declaration")
        ),
        "declared_defect_entity": str(declaration_row["defect_entity"]),
        "declared_by": "integrator",
        "declaration_proof": dict(derivation_row["declaration_proof"]),
        "defect_view": {
            "round": int(defect["round"]),
            "symbol": str(defect["symbol"]),
            "file": str(defect["file"]),
            "first_line": int(defect["first_line"]),
            "last_line": int(defect["last_line"]),
            "is_the_newest_tool_group": int(defect["round"]) == VIEWS_PER_TASK,
            "recency_consequence": (
                "the defect view is the last read, so the mechanism keeps that tool group "
                "verbatim on every later model input; its literals are never at risk and no "
                "saving may be attributed to it"
                if int(defect["round"]) == VIEWS_PER_TASK
                else (
                    "the defect view is the first read, so it is elidable content on later model "
                    "inputs under the literal invariant"
                )
            ),
        },
        "views": views,
        "view_texts_sha256": [str(view["view_content_sha256"]) for view in views],
        "views_pairwise_disjoint": True,
        "view_count": len(views),
        "max_view_lines": MAX_VIEW_LINES,
        "tool_names": [str(view["tool_name"]) for view in views],
        "request": str(spec["request"]),
        "contract": contract,
        "literal_rule_verdicts": {
            "R1_verbatim": dict(rule_block["R1_verbatim"]),
            "R2_two_code_anchored": dict(rule_block["R2_two_code_anchored"]),
            "R3_regex_tokens_anchored": dict(rule_block["R3_regex_tokens_anchored"]),
            "R4_relevance": dict(rule_block["R4_relevance"]),
            "R5_two_outside_framework_generic": dict(rule_block["R5_two_outside_framework_generic"]),
            "R6_not_keyword_or_builtin": dict(rule_block["R6_not_keyword_or_builtin"]),
            "R7_lazy_answer_control": dict(rule_block["R7_lazy_answer_control"]),
            "minimal_feasible_line": dict(rule_block["minimal_feasible_line"]),
            "views_unchanged": dict(rule_block["views_unchanged"]),
            "verdict": str(verification_row["verdict"]),
            "printed_by_the_supplement": (
                "integrations/openai_agents/V18_LAZY_NEGATIVE_CONTROL_SUPPLEMENT_20261010.json"
            ),
        },
        "literals": [dict(record) for record in verification_row["literals"]],
        "defect_view_bound_names": list(verification_row["defect_view_bound_names"]),
        "problems": [],
        "usable": True,
    }


def build() -> dict[str, Any]:
    declaration = json.loads(DECLARATION.read_text(encoding="utf-8"))
    derivation = json.loads(DERIVATION.read_text(encoding="utf-8"))
    verification = json.loads(VERIFICATION.read_text(encoding="utf-8"))
    supplement_path = SUPPLEMENT
    supplement = (
        json.loads(supplement_path.read_text(encoding="utf-8"))
        if supplement_path.is_file()
        else {}
    )
    if not supplement.get("complete"):
        raise SystemExit(
            "the zero-API completion artifact is missing or incomplete: the lazy answer negative "
            f"control per candidate must be discharged before registration ({supplement_path})"
        )
    pool = pool_values()

    declared_by_short = {
        str(row["task"]): row for row in declaration["declarations"]
    }
    tasks: dict[str, Any] = {}
    order: list[str] = []
    for spec in TASKS:
        instance_id = spec["instance_id"]
        if instance_id not in derivation["candidates"]:
            raise SystemExit(f"{instance_id}: absent from the view derivation record")
        if verification["candidates"][instance_id]["verdict"] != "ACCEPTED":
            raise SystemExit(f"{instance_id}: the literal re-derivation did not accept it")
        entry = build_task(
            spec,
            declared_by_short[spec["declared_short_id"]],
            derivation["candidates"][instance_id],
            verification["candidates"][instance_id],
            pool,
        )
        tasks[entry["task_id"]] = entry
        order.append(entry["task_id"])

    rule_applied = dict(verification["rule_applied"])
    payload = {
        "schema": "openai_agents_v18_sealed_source_registration",
        "registration_id": "openai-agents-v18-sealed-source-registration-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": (
            "the three sealed confirmation candidates of the v18 branch are registered here, from "
            "the artifacts named below; this is the first registration of this task set and it "
            "registers nothing else"
        ),
        "produced_by": "experiments/runners/openai_agents_v18_sealed_registration.py",
        "api_requests_spent": 0,
        "written_before_any_request": True,
        "source_artifacts": {
            "problem_statements": {
                "path": ".tooling/swebench-verified/test.parquet",
                "role": "the public table the statements, base commits and difficulties are read from",
            },
            "pinned_clone": {
                "path": ".tooling/upstream/django-15563",
                "role": "the read-only clone every blob, file hash and rendered view is re-read from",
            },
            "view_derivation_record": {
                "path": str(DERIVATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DERIVATION),
            },
            "candidate_rederivation_verification": {
                "path": str(VERIFICATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(VERIFICATION),
            },
            "lazy_negative_control_supplement": {
                "path": str(SUPPLEMENT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(SUPPLEMENT),
                "complete": bool(supplement.get("complete")),
                "lazy_control_holds_per_candidate": {
                    key: bool(value["holds"])
                    for key, value in supplement["lazy_negative_control"].items()
                },
            },
            "defect_entity_declaration": {
                "path": str(DECLARATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DECLARATION),
                "decided_by": str(declaration.get("decided_by")),
            },
            "candidate_rederivation_proposal": {
                "path": str(REDERIVATION_PROPOSAL.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(REDERIVATION_PROPOSAL),
            },
            "superseded_selection_proposal_rev2": {
                "path": str(SELECTION_PROPOSAL_REV2.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(SELECTION_PROPOSAL_REV2),
                "role": "the statement-hash diagnosis this registration reconciles against",
            },
            "candidate_pool": {
                "path": str(POOL.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(POOL),
            },
            "view_derivation_script": {
                "path": str(VIEW_DERIVATION_SCRIPT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(VIEW_DERIVATION_SCRIPT),
            },
            "literal_binding_script": {
                "path": str(BINDING_SCRIPT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(BINDING_SCRIPT),
            },
        },
        "rules_in_force": {
            "route_rule": {
                "route_A": ROUTING_RULE["route_A"],
                "route_A_prime": ROUTING_RULE["route_A_prime"],
                "route_B": ROUTING_RULE["route_B"],
                "routing_note": ROUTING_RULE["routing_note"],
            },
            "large_definition_clause": {
                "max_view_lines": MAX_VIEW_LINES,
                "text": ROUTING_RULE["large_definition_clause"],
                "declared_in": {
                    "path": str(DERIVATION.relative_to(ROOT)).replace("\\", "/"),
                    "field": "derivation_rule.large_definition_clause",
                    "view_shape_amendment_03": {
                        "path": str(VIEW_SHAPE_AMENDMENT_3.relative_to(ROOT)).replace("\\", "/"),
                        "sha256": sha256_of(VIEW_SHAPE_AMENDMENT_3),
                    },
                },
            },
            "disjointness": {
                "text": ROUTING_RULE["disjointness"],
                "view_shape_amendment_03": {
                    "path": str(VIEW_SHAPE_AMENDMENT_3.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": sha256_of(VIEW_SHAPE_AMENDMENT_3),
                    "status": "in force: three views, one per round, pairwise disjoint",
                },
            },
            "defect_entity": {
                "text": ROUTING_RULE["defect_entity"],
                "declaration": {
                    "path": str(DECLARATION.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": sha256_of(DECLARATION),
                    "decided_by": str(declaration.get("decided_by")),
                    "role": (
                        "an input: it fixes which definition is the defect-carrying view; ranges, "
                        "the length cap, disjointness and every hash stay script derived"
                    ),
                },
            },
            "literal_rule": {
                "rule_file": {
                    "path": str(LITERAL_RULE.relative_to(ROOT)).replace("\\", "/"),
                    "sha256": sha256_of(LITERAL_RULE),
                },
                "rule_id": str(rule_applied.get("rule_id", "")),
                "amendments_applied": [
                    {
                        "path": str(LITERAL_AMENDMENT_1.relative_to(ROOT)).replace("\\", "/"),
                        "sha256": sha256_of(LITERAL_AMENDMENT_1),
                        "status": "superseded in part by amendment 02",
                    },
                    {
                        "path": str(LITERAL_AMENDMENT_2.relative_to(ROOT)).replace("\\", "/"),
                        "sha256": sha256_of(LITERAL_AMENDMENT_2),
                        "status": "in force",
                    },
                ],
                "requirements_in_force": list(rule_applied.get("requirements_in_force") or []),
                "registration_gate": (
                    "the source registration must record, per candidate, which rule file and "
                    "which amendments were applied, and must not be written while any candidate's "
                    "set fails R1 to R7"
                ),
            },
        },
        "excluded_from_this_registration": {
            "exception_py_error_derivation": {
                "what": (
                    "an earlier derivation of django-16255 named "
                    "django/core/handlers/exception.py as its main file and derived four views "
                    "from the first traceback frame's module"
                ),
                "why_excluded": (
                    "the statement's traceback frame is not the module the defect is in; the "
                    "integrator's declaration names django/contrib/sitemaps/__init__.py, and "
                    "view-shape amendment 03 re-derived three disjoint views from there"
                ),
                "where_it_stays_on_record": (
                    "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json "
                    "and integrations/openai_agents/V18_SELECTION_PROPOSAL_20261010.json"
                ),
                "status": "a process record; it is registered nowhere and is not a task of this set",
            },
            "superseded_fourth_view_rule": (
                "the earlier four-view shape of the selection branch is not registered here; the "
                "registered shape is three views, one per round, per amendment 03"
            ),
        },
        "registered_task_count": len(tasks),
        "registered_task_ids": order,
        "task_ids": order,
        "tasks": tasks,
        "view_shape": {
            "views_per_task": VIEWS_PER_TASK,
            "rounds": VIEWS_PER_TASK,
            "reads_per_round": 1,
            "read_calls": VIEWS_PER_TASK,
            "max_view_lines": MAX_VIEW_LINES,
            "pairwise_disjoint": True,
            "note": (
                "the registry is pointed at this artifact in its sealed mode, so the registered "
                "task table of this batch is exactly the three tasks above: the stage-1 "
                "development task the registry appends in development mode is not part of this "
                "matrix"
            ),
        },
        "citable": False,
        "registration_may_proceed": True,
        "paid_requests": 0,
        "not_done": [
            "no request is spent by this artifact",
            "the confirmation batch itself is not started here",
        ],
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    payload = build()
    for task_id in payload["task_ids"]:
        entry = payload["tasks"][task_id]
        print("=" * 92)
        print(f"{task_id}  instance={entry['instance_id']}  {entry['base_commit']}")
        print(f"  main file      : {entry['declared_main_file']}")
        print(f"  defect entity  : {entry['declared_defect_entity']}  round {entry['defect_view']['round']}")
        print(f"  statement sha  : {entry['problem_statement_sha256']}")
        print(f"  matches pool   : {entry['problem_statement_sha256_matches_preselection']}")
        for view in entry["views"]:
            print(
                f"   round {view['round']}: {view['file']} {view['first_line']}-{view['last_line']} "
                f"{view['symbol']} tool={view['tool_name']} bytes={view['view_bytes']} "
                f"cap_ok={view['within_the_60_line_cap']}"
            )
        print(f"  contract       : {entry['contract']['frozen_regex']}")
        print(f"  required facts : {entry['contract']['required_facts']}")
        print(f"  R7 holds       : {entry['literal_rule_verdicts']['R7_lazy_answer_control']['holds']}")
    print("=" * 92)
    print(f"registered tasks: {payload['registered_task_count']} {payload['task_ids']}")
    if not args.probe:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(
                f"refusing to overwrite the existing registration {out}: write a new one instead"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
