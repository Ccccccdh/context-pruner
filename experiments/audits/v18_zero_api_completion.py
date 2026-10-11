"""Zero-API completion of the v18 sealed set: the lazy negative control, printed per candidate.

Why this file exists
--------------------
``V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json`` names five computations the next zero-API
step must run before registration.  Four of them are carried by the frozen artifacts already:

* the lexer-level verification of every required literal and both regex tokens, with the source
  view and line of each (``V18_CANDIDATE_REDERIVATION_VERIFICATION_20261010.json``);
* the four conditions on the minimal feasible rendered line (75 / 68 / 73 characters);
* unchanged token counts, tool names, distinct ranges and distinct file hashes;
* the freshly recomputed problem-statement reconciliation.

The fifth - **R7, the lazy answer negative control, per candidate** - is computed by
``experiments/audits/v18_literal_binding_check.py`` and written into its record, but its
``main()`` prints only the verdict, the defect view, the literals and the minimal line.  The
project's rule is that *a pass rests on the complete output of one run*, so a check that is in
the artifact and not in the output is not yet discharged.  The frozen script and the frozen
record are inputs and may not be edited; this script therefore discharges that item the only way
the discipline allows:

* it imports the frozen check module and calls **its own** :func:`check_candidate` and
  :func:`bound_names`, so R7 is decided by the code that decided it before, not by a second
  implementation of the rule;
* it prints R7 per candidate, in full, including the vocabulary it was refused against;
* it re-runs both frozen audit scripts with ``--probe`` (which writes nothing) and records their
  complete stdout, so the record of this round rests on the output of one run of each;
* it asserts that the freshly computed candidate entries are **equal** to the frozen record's own
  entries, so the frozen verdicts are shown to be reproducible right now rather than trusted.

Nothing here writes over an existing artifact.  ``api_requests_spent`` is 0 and no provider
client is constructed.
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

from experiments.audits import v18_literal_binding_check as binding  # noqa: E402

OUT = ROOT / "integrations/openai_agents/V18_LAZY_NEGATIVE_CONTROL_SUPPLEMENT_20261010.json"

DERIVATION = binding.DERIVATION
VERIFICATION = binding.OUT
RULE = binding.RULE
AMENDMENT_1 = binding.AMENDMENT_1
AMENDMENT_2 = binding.AMENDMENT_2
DERIVATION_SCRIPT = ROOT / "experiments/audits/v18_view_derivation.py"
BINDING_SCRIPT = ROOT / "experiments/audits/v18_literal_binding_check.py"
DECLARATION = ROOT / "integrations/openai_agents/V18_DEFECT_ENTITY_DECLARATION_20261010.json"
VIEW_SHAPE_AMENDMENT_3 = (
    ROOT / "integrations/openai_agents/V18_VIEW_SHAPE_AMENDMENT_03_20261010.json"
)
REDERIVATION_PROPOSAL = (
    ROOT / "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json"
)
SELECTION_PROPOSAL_REV2 = (
    ROOT / "integrations/openai_agents/V18_SELECTION_PROPOSAL_REV2_20261010.json"
)
POOL = binding.POOL
PARQUET = binding.PARQUET

#: The candidates of the sealed set, in the order the derivation record lists them.
CANDIDATES = ("django__django-15572", "django__django-16255", "django__django-15569")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _proposal_view(entry: dict[str, Any], instance_id: str) -> dict[str, Any]:
    """The proposal entry the frozen check builds for one candidate, byte for byte.

    This is ``v18_literal_binding_check.main()``'s own construction, written out so that this
    script cannot silently test a different view set than the one the record was produced from.
    """

    return {
        "base_commit": entry["base_commit"],
        "task_id": entry.get("task_id", instance_id),
        "contract": {"issue_value": instance_id.split("__")[-1]},
        "views": entry["views"],
    }


def fresh_candidate_checks() -> dict[str, dict[str, Any]]:
    """Run the frozen checker over every candidate, through the frozen checker's own functions."""

    derivation = json.loads(DERIVATION.read_text(encoding="utf-8"))
    fresh: dict[str, dict[str, Any]] = {}
    for instance_id in CANDIDATES:
        entry = _proposal_view(derivation["candidates"][instance_id], instance_id)
        entry["contract"]["issue_value"] = instance_id.split("__")[-1]
        fresh[instance_id] = binding.check_candidate(
            instance_id, entry, binding.PROPOSED[instance_id]
        )
    return fresh


def _replay(path: Path, argv: list[str]) -> dict[str, Any]:
    """Run one frozen audit script with ``--probe`` and capture its complete output."""

    result = subprocess.run(
        [sys.executable, str(path), *argv],
        capture_output=True,
        cwd=str(ROOT),
        check=False,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    stderr = result.stderr.decode("utf-8", errors="replace")
    return {
        "script": str(path.relative_to(ROOT)).replace("\\", "/"),
        "script_sha256": sha256_of(path),
        "argv": [str(path.relative_to(ROOT)).replace("\\", "/"), *argv],
        "writes_nothing": True,
        "exit_code": int(result.returncode),
        "stdout_sha256": hashlib.sha256(stdout.encode("utf-8")).hexdigest(),
        "stdout_line_count": len(stdout.splitlines()),
        "stdout": stdout,
        "stderr": stderr,
    }


def _statement_reconciliation() -> dict[str, Any]:
    """The freshly recomputed statement hash per candidate, against the values on record."""

    import pyarrow.parquet as pq

    table = pq.read_table(
        str(PARQUET),
        columns=["instance_id", "base_commit", "difficulty", "problem_statement"],
    )
    rows = {row["instance_id"]: row for row in table.to_pylist()}
    rev2 = json.loads(SELECTION_PROPOSAL_REV2.read_text(encoding="utf-8"))
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    pool_values: dict[str, str] = {}
    for value in pool.get("candidates") or []:
        if isinstance(value, dict) and value.get("problem_statement_sha256"):
            pool_values[str(value["instance_id"])] = str(value["problem_statement_sha256"])
    out: dict[str, Any] = {}
    for instance_id in CANDIDATES:
        row = rows[instance_id]
        statement = str(row["problem_statement"])
        recomputed = hashlib.sha256(statement.encode("utf-8")).hexdigest()
        diagnosis = (rev2.get("statement_hash_diagnosis") or {}).get(instance_id) or {}
        out[instance_id] = {
            "public_table_statement_sha256": recomputed,
            "public_table_statement_bytes": len(statement.encode("utf-8")),
            "base_commit": str(row["base_commit"]),
            "difficulty": str(row["difficulty"]),
            "rev2_diagnosis_pool_value": str(diagnosis.get("pool_value", "")),
            "rev2_diagnosis_true_sha256": str(diagnosis.get("true_sha256", "")),
            "rev2_matching_hypothesis": str(diagnosis.get("matching_hypothesis", "")),
            "candidate_pool_value": pool_values.get(instance_id, ""),
            "matches_the_diagnosis": recomputed
            == str(diagnosis.get("true_sha256", "")),
            "matches_the_candidate_pool": recomputed == pool_values.get(instance_id, ""),
        }
    return out


def _defect_view_rounds() -> dict[str, Any]:
    """The round each defect view sits in, and what the shape does with that round.

    This is an explanatory fact of the sealed set and is disclosed as such, per task, before any
    request is spent.  It is not a property the mechanism is credited with.
    """

    verification = json.loads(VERIFICATION.read_text(encoding="utf-8"))
    out: dict[str, Any] = {}
    for instance_id in CANDIDATES:
        defect = verification["candidates"][instance_id]["defect_view"]
        rounds = len(verification["candidates"][instance_id]["contract"]["required_facts"])
        round_number = int(defect["round"])
        out[instance_id] = {
            "defect_view_round": round_number,
            "registered_rounds": 3,
            "defect_symbol": str(defect["symbol"]),
            "defect_file": str(defect["file"]),
            "defect_range": [int(defect["first_line"]), int(defect["last_line"])],
            "is_the_newest_tool_group": round_number == 3,
            "consequence": (
                "the defect-carrying view is the third and last read, so it is the most recent "
                "tool group on every model input that follows it and the mechanism keeps it "
                "verbatim; its registered literals are therefore never at risk from the elision, "
                "and that says nothing about how much the mechanism saves"
                if round_number == 3
                else (
                    "the defect-carrying view is the first read, so on later model inputs it is "
                    "elidable content: every line that carries a registered literal, with a "
                    "one-line window on each side, is kept and the rest of it may be elided, and "
                    "the literal invariant is what has to hold"
                )
            ),
            "reported_as": "an explanatory fact of the task shape, disclosed and not claimed as a merit",
        }
        out[instance_id]["registered_rounds"] = 3
        out[instance_id]["required_fact_count"] = rounds
    return out


def build() -> dict[str, Any]:
    derivation = json.loads(DERIVATION.read_text(encoding="utf-8"))
    verification = json.loads(VERIFICATION.read_text(encoding="utf-8"))
    fresh = fresh_candidate_checks()

    lazy: dict[str, Any] = {}
    literals: dict[str, Any] = {}
    minimal: dict[str, Any] = {}
    unchanged: dict[str, Any] = {}
    reproduces: dict[str, bool] = {}
    for instance_id in CANDIDATES:
        record_entry = verification["candidates"][instance_id]
        computed = fresh[instance_id]
        reproduces[instance_id] = computed == record_entry
        check = computed["checks"]["R7_lazy_answer_control"]
        line = str(check["lazy_line_head"])
        lazy[instance_id] = {
            "task_id": str(computed["task_id"]),
            "issue_value": str(computed["contract"]["issue_value"]),
            "frozen_regex": str(computed["contract"]["frozen_regex"]),
            "required_facts": list(computed["contract"]["required_facts"]),
            "holds": bool(check["holds"]),
            "satisfies_frozen_regex": bool(check["satisfies_frozen_regex"]),
            "satisfies_literal_requirement": bool(check["satisfies_literal_requirement"]),
            "lazy_line_chars": len(line),
            "lazy_line_head": line,
            "vocabulary": (
                "the issue identifier, the problem statement's own word tokens in order (first 40) "
                "and the frozen lazy vocabulary of Python and framework generic words"
            ),
            "vocabulary_size": len(binding.LAZY_VOCABULARY.split()),
            "rule": (
                "R7 fails the control: the line assembled from that vocabulary alone must satisfy "
                "neither the frozen regex nor the literal requirement"
            ),
            "record_value": {
                "holds": record_entry["checks"]["R7_lazy_answer_control"]["holds"],
                "satisfies_frozen_regex": record_entry["checks"]["R7_lazy_answer_control"][
                    "satisfies_frozen_regex"
                ],
                "satisfies_literal_requirement": record_entry["checks"][
                    "R7_lazy_answer_control"
                ]["satisfies_literal_requirement"],
            },
        }
        literals[instance_id] = [
            {
                "literal": str(record["literal"]),
                "verbatim_hits": int(record["verbatim_hits"]),
                "views_where_it_appears": list(record["views_where_it_appears"]),
                "bound_in_view_rounds": list(record["bound_in_view_rounds"]),
                "bound_in_defect_view": bool(record["bound_in_defect_view"]),
                "named_by_statement": bool(record["named_by_statement"]),
                "first_hit_round": (
                    int(record["first_hit"]["round"]) if record["first_hit"] else None
                ),
                "first_hit_file": (
                    str(record["first_hit"]["file"]) if record["first_hit"] else None
                ),
                "first_hit_line": (
                    int(record["first_hit"]["line_number"]) if record["first_hit"] else None
                ),
                "first_hit_text": (
                    str(record["first_hit"]["text"]) if record["first_hit"] else None
                ),
            }
            for record in computed["literals"]
        ]
        feasible = computed["checks"]["minimal_feasible_line"]
        minimal[instance_id] = {
            "line": str(feasible["line"]),
            "chars": int(feasible["chars"]),
            "four_conditions": dict(feasible["four_conditions"]),
            "all_four_hold": all(bool(v) for v in feasible["four_conditions"].values()),
        }
        unchanged[instance_id] = dict(computed["checks"]["views_unchanged"])

    replay = [
        _replay(DERIVATION_SCRIPT, ["--probe"]),
        _replay(BINDING_SCRIPT, ["--probe"]),
    ]

    accepted = [k for k in CANDIDATES if fresh[k]["verdict"] == "ACCEPTED"]
    errors: list[str] = []
    for instance_id in CANDIDATES:
        if not lazy[instance_id]["holds"]:
            errors.append(f"{instance_id}: the lazy answer negative control does not hold")
        if not reproduces[instance_id]:
            errors.append(
                f"{instance_id}: the frozen verification record is not reproduced by this run"
            )
        if not minimal[instance_id]["all_four_hold"]:
            errors.append(f"{instance_id}: the minimal feasible line fails a condition")
        if not all(bool(v) for v in unchanged[instance_id].values()):
            errors.append(f"{instance_id}: a view shape check regressed")
    for entry in replay:
        if entry["exit_code"] != 0:
            errors.append(f"{entry['script']} --probe exited {entry['exit_code']}")
        if not entry["stdout"].strip():
            errors.append(f"{entry['script']} --probe printed nothing")

    payload = {
        "schema": "openai_agents_v18_zero_api_completion",
        "record_id": "openai-agents-v18-zero-api-completion-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "api_requests_spent": 0,
        "produced_by": "experiments/audits/v18_zero_api_completion.py",
        "pass_evidence_rule": (
            "a pass rests on the complete output of one run of this script; the artifact carries "
            "the run's own captured output for both frozen checks and prints R7 per candidate"
        ),
        "why": (
            "the frozen verification record carries R7, the lazy answer negative control, for "
            "each candidate, but the frozen script's main() does not print it; the project's "
            "evidence rule requires the check to appear in the output of a run, so this "
            "supplement prints it, using the frozen module's own check_candidate rather than a "
            "re-implementation, and shows the frozen record to be reproducible from its inputs"
        ),
        "required_computations": {
            "source": "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_PROPOSAL_20261010.json",
            "lazy_negative_control_per_candidate": "carried here, printed per candidate",
            "lexer_verification_of_literals_and_regex_tokens": "tabulated here from the frozen checker and recorded in the verification artifact",
            "minimal_feasible_line_with_the_four_conditions": "asserted here on the freshly computed lines",
            "token_counts_tool_names_ranges_and_file_hashes_unchanged": "asserted here",
            "statement_hash_recomputed_at_registration": "recomputed here and written into V18_SOURCE_REGISTRATION_20261010.json",
        },
        "inputs": {
            "view_derivation_record": {
                "path": str(DERIVATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DERIVATION),
            },
            "candidate_rederivation_verification": {
                "path": str(VERIFICATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(VERIFICATION),
            },
            "literal_rule": {
                "path": str(RULE.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(RULE),
            },
            "literal_rule_amendment": {
                "path": str(AMENDMENT_1.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(AMENDMENT_1),
            },
            "literal_rule_amendment_02": {
                "path": str(AMENDMENT_2.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(AMENDMENT_2),
            },
            "view_shape_amendment_03": {
                "path": str(VIEW_SHAPE_AMENDMENT_3.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(VIEW_SHAPE_AMENDMENT_3),
            },
            "defect_entity_declaration": {
                "path": str(DECLARATION.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DECLARATION),
                "decided_by": "integrator",
            },
            "rederivation_proposal": {
                "path": str(REDERIVATION_PROPOSAL.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(REDERIVATION_PROPOSAL),
            },
            "view_derivation_script": {
                "path": str(DERIVATION_SCRIPT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(DERIVATION_SCRIPT),
            },
            "literal_binding_script": {
                "path": str(BINDING_SCRIPT.relative_to(ROOT)).replace("\\", "/"),
                "sha256": sha256_of(BINDING_SCRIPT),
            },
        },
        "lazy_negative_control": lazy,
        "literals": literals,
        "minimal_feasible_lines": minimal,
        "views_unchanged": unchanged,
        "frozen_record_reproduced_by_this_run": reproduces,
        "statement_reconciliation": _statement_reconciliation(),
        "defect_view_round_disclosure": _defect_view_rounds(),
        "same_run_replay": replay,
        "accepted": accepted,
        "accepted_count": len(accepted),
        "errors": errors,
        "complete": not errors and len(accepted) == 3,
        "registration_may_proceed": bool(not errors and len(accepted) == 3),
        "citable": False,
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe", action="store_true", help="print only, write nothing")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    payload = build()
    for instance_id in CANDIDATES:
        entry = payload["lazy_negative_control"][instance_id]
        print("=" * 92)
        print(f"{instance_id}  task_id={entry['task_id']}")
        print(f"  R7 lazy answer negative control : holds={entry['holds']}")
        print(f"     satisfies_frozen_regex        : {entry['satisfies_frozen_regex']}")
        print(f"     satisfies_literal_requirement : {entry['satisfies_literal_requirement']}")
        print(f"     vocabulary size               : {entry['vocabulary_size']} words")
        print(f"     lazy line ({entry['lazy_line_chars']} chars): {entry['lazy_line_head']}")
        print(f"  required facts : {entry['required_facts']}")
        print(f"  frozen regex   : {entry['frozen_regex']}")
        line = payload["minimal_feasible_lines"][instance_id]
        print(f"  minimal feasible line ({line['chars']} chars): {line['line']}")
        print(f"  four conditions: {line['four_conditions']}")
        print(
            "  frozen record reproduced by this run: "
            f"{payload['frozen_record_reproduced_by_this_run'][instance_id]}"
        )
    for entry in payload["same_run_replay"]:
        print("-" * 92)
        print(
            f"{entry['script']} {' '.join(entry['argv'][1:])} -> exit {entry['exit_code']}, "
            f"{entry['stdout_line_count']} stdout lines, sha256 {entry['stdout_sha256']}"
        )
    print("=" * 92)
    print(f"accepted {payload['accepted_count']}/3: {payload['accepted']}")
    print(f"errors: {payload['errors']}")
    print(f"complete: {payload['complete']}")
    if not args.probe:
        out = Path(args.out)
        if out.exists():
            raise SystemExit(f"refusing to overwrite the existing artifact {out}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {out.relative_to(ROOT)}")
    return 0 if payload["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
