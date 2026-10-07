"""Independent batch audit of an r20 order-free run, with the r20 criteria wired in (zero API).

This module is the batch audit the pre-registration referred to when it said the criteria
module must not be decorative.  It does three things the acquisition-side result cannot do
for itself:

1. **Recomputes every criterion independently.**  ``CRIT_R20_COVERAGE``,
   ``CRIT_R20_REPEAT_REPORT`` and ``CRIT_R20_SAME_WORK_REPORT`` are each recomputed here from
   the recorded rows, via :mod:`experiments.audits.criteria_crewai_r20_orderfree`, and any
   value recorded elsewhere is only *cross-checked* against the recomputation.  A criterion
   whose recorded value disagrees with the recomputation is an error.
2. **Keeps the gate split honest.**  Only ``CRIT_R20_COVERAGE`` is allowed onto the quality
   side.  The repeat and same-work criteria are emitted with ``gate: false`` and the audit
   asserts, at runtime, that ``criteria.QUALITY_CRITERIA`` contains coverage and neither of
   the report ids.  A batch that promoted them would recreate the r17 failure.
3. **States the errors rule.**  When ``errors`` is non-empty the numbers in this artifact are
   **not** an effective saving and must not be quoted as one; the flag ``can_be_quoted_as_saving``
   is then ``false`` regardless of the sign of the computed bound.

The audit imports the criteria module (a pinned source) but never a runner, and it resolves
pins amendment-first and fail-closed: the freeze file is never rewritten, so a source that
changed after it was written is accepted only when its digest equals the one an amendment
registers for that path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.audits import criteria_crewai_r20_orderfree as criteria  # noqa: E402

#: Frozen guard control texts.  It is not enough to record that no guard was *installed*; the
#: audit also proves that no control text reached any prepared message.  The strings are
#: duplicated here as literals on purpose -- importing the guards would make this audit
#: depend on the code it is supposed to police.
CONTROL_TEXTS = {
    "experiments/runners/crewai_loop_guard_v16.py": (
        "Host control: the evidence loop is not finished."
    ),
    "experiments/runners/crewai_loop_guard_v17.py": (
        "Host control: the evidence loop is not finished."
    ),
    "experiments/runners/crewai_loop_guard_v18.py": (
        "Host control: the frozen evidence order is mandatory."
    ),
}

DEFAULT_BATCH = ROOT / "runs/stage5-crewai/crewai-r20-orderfree-fullcapture-acquisition-api-01"
DEFAULT_FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_ACQUISITION_01.json"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json"
RECORDED_RESULT = ROOT / "integrations/crewai/R20_ORDERFREE_ACQUISITION_RESULT_20261006.json"
BATCH_BOUND = ROOT / "integrations/crewai/R20_ORDERFREE_BOUND_20261006.json"
DEFAULT_OUT = ROOT / "integrations/crewai/R20_ORDERFREE_BATCH_AUDIT_CRITERIA_20261006.json"

ERRORS_RULE = (
    "errors non-empty: the criteria and cost numbers in this artifact are NOT an effective "
    "saving and must not be quoted as one; a report-only item is never a pass and a pass "
    "never depends on a report-only item"
)


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rel(path: Path) -> str:
    """A workspace-relative label; a path outside the workspace is shown absolute.

    ``Path.relative_to`` raises for such a path, and an audit that crashes on an unexpected
    directory cannot report that the directory is unexpected.
    """
    path = Path(path)
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def load_rows(directory: Path) -> list[dict]:
    text = (Path(directory) / "results.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def pin_authority(freeze_path: Path) -> tuple[dict, dict, dict]:
    """Amendment-first, fail-closed pin authority.  Returns (pins, freeze, amendment)."""
    freeze = json.loads(Path(freeze_path).read_text(encoding="utf-8"))
    amendment_path = Path(freeze_path).with_name(
        Path(freeze_path).stem + "_FREEZE_AMENDMENT_20261006.json"
    )
    amendment: dict = {}
    authority = dict(freeze.get("source_sha256") or {})
    if amendment_path.is_file():
        amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
        authority.update(amendment.get("pinned_sources_after_amendment") or {})
    return authority, freeze, amendment


def _view_texts(attempt: Mapping[str, object]) -> list[str]:
    texts: list[str] = []
    for message in attempt.get("full_input_messages") or []:
        if isinstance(message, Mapping):
            content = message.get("content")
            if isinstance(content, str):
                texts.append(content)
    return texts


def criteria_blocks(rows: Sequence[Mapping[str, object]]) -> dict:
    """Recompute all three criteria here; never trust a recorded value."""
    expected_by_task = {
        str(t["task_id"]): [str(tool["name"]) for tool in (t.get("tools") or [])]
        for t in json.loads(TASK_FILE.read_text(encoding="utf-8"))
    }
    units: list[dict] = []
    for row in rows:
        task_id = str(row.get("task_id"))
        executed = [str(x) for x in (row.get("first_role_trace") or [])]
        declared = expected_by_task.get(task_id, [])
        row_expected = [str(x) for x in (row.get("expected_first_role_trace") or [])]
        units.append(
            {
                "task_id": task_id,
                "repeat": int(row.get("repeat", 0) or 0),
                "arm": str(row.get("method")),
                "declared_expected_trace_matches_task_file": row_expected == declared,
                criteria.CRIT_COVERAGE: criteria.coverage(declared, executed),
                criteria.CRIT_REPEAT: criteria.repeat_report(declared, executed),
            }
        )
    covered = sum(1 for unit in units if unit[criteria.CRIT_COVERAGE]["satisfied"])
    expected_units = len(expected_by_task) or len(units)
    # An empty batch satisfies "every source was read" vacuously.  Vacuous truth is not a pass,
    # so completeness is checked here as well as in the caller.
    complete_grid = len(units) == expected_units and bool(units)
    coverage_block = {
        "criterion": criteria.CRIT_COVERAGE,
        "side": "quality",
        "gate": True,
        "computed_from": "first_role_trace of each recorded row, against the frozen task file",
        "units": len(units),
        "expected_units": expected_units,
        "grid_complete": complete_grid,
        "satisfied": covered,
        "satisfied_percent": round(100.0 * covered / len(units), 2) if units else None,
        "all_satisfied": complete_grid and covered == len(units),
    }
    repeat_block = {
        "criterion": criteria.CRIT_REPEAT,
        "side": "report_only",
        "gate": False,
        "call_count_total": sum(unit[criteria.CRIT_REPEAT]["call_count"] for unit in units),
        "expected_count_total": sum(
            unit[criteria.CRIT_REPEAT]["expected_count"] for unit in units
        ),
        "repeat_calls_total": sum(
            unit[criteria.CRIT_REPEAT]["repeat_calls"] for unit in units
        ),
        "unexpected_tools_total": sum(
            len(unit[criteria.CRIT_REPEAT]["unexpected_tools"]) for unit in units
        ),
    }
    # Same work is a *pairwise* report: it needs a baseline row and a controller row for the
    # same task.  A single-arm batch therefore has nothing to pair, and that is reported as
    # "not applicable", not as a pass.
    by_task: dict[str, dict] = {}
    for row in rows:
        by_task.setdefault(str(row.get("task_id")), {})[str(row.get("method"))] = row
    pairs = []
    for task_id, arms in sorted(by_task.items()):
        baseline = arms.get("none")
        if baseline is None:
            continue
        for arm, row in sorted(arms.items()):
            if arm == "none":
                continue
            report = criteria.same_work_report(baseline, row)
            report["task_id"] = task_id
            pairs.append(report)
    same_work_block = {
        "criterion": criteria.CRIT_SAME_WORK,
        "side": "report_only",
        "gate": False,
        "applicable": bool(pairs),
        "why": "same-work needs a baseline row and a controller row on the same task",
        "pairs": pairs,
        "same_work_pairs": sum(1 for p in pairs if p["same_work"]),
        "resend_overrun_calls": sum(p["resend_overrun_calls"] for p in pairs),
    }
    return {
        "criterion_ids": list(criteria.CRITERION_IDS),
        "quality_criteria": list(criteria.QUALITY_CRITERIA),
        "gate_ids": list(criteria.QUALITY_CRITERIA),
        "report_only_ids": [
            cid for cid in criteria.CRITERION_IDS if cid not in criteria.QUALITY_CRITERIA
        ],
        criteria.CRIT_COVERAGE: coverage_block,
        criteria.CRIT_REPEAT: repeat_block,
        criteria.CRIT_SAME_WORK: same_work_block,
        "units": units,
    }


def guard_scan(rows: Sequence[Mapping[str, object]]) -> dict:
    """No guard installed, and no control text anywhere in any prepared message."""
    observed: dict[str, int] = {path: 0 for path in CONTROL_TEXTS}
    installed = 0
    allowance_ok = 0
    attempts = 0
    for row in rows:
        for attempt in row.get("full_attempt_capture") or []:
            attempts += 1
            if attempt.get("guard_installed") is False:
                installed += 1
            if attempt.get("guard_allowance") == "not_installed_acquisition":
                allowance_ok += 1
            for text in _view_texts(attempt):
                for path, marker in CONTROL_TEXTS.items():
                    if marker in text:
                        observed[path] += 1
    return {
        "attempts": attempts,
        "attempts_with_no_guard_installed": installed,
        "attempts_with_acquisition_allowance": allowance_ok,
        "control_text_occurrences": observed,
        "control_text_occurrences_total": sum(observed.values()),
        "baseline_zero_trigger": sum(observed.values()) == 0,
    }


def saving_bound(batch_bound_path: Path, rows: Sequence[Mapping[str, object]]) -> dict:
    """The per-task bound this batch measured, plus what was charged in full.

    The batch carries both copies of every call: ``payload.*_frame_deltas`` hold the real
    provider input of the uncompressed arm, and the rows' own attempt captures hold the
    controller side.  The bound is therefore *measured* on this batch, not projected from
    another family.  Every repeat, remedy and guard request is charged in full -- in the
    acquisition batch there were none of the first two, and the guard is not installed, so
    the charged extras are zero and are reported rather than assumed away.
    """
    charged_fields = ("repeats_charged", "remedy_requests_charged", "resend_overrun_calls")
    charged: dict = {}
    absent: list[str] = []
    for field in charged_fields:
        if any(field in row for row in rows):
            charged[field] = sum(int(row.get(field, 0) or 0) for row in rows)
        else:
            # Do NOT report a zero for a quantity that was never recorded: an absent field
            # would otherwise read as "nothing was charged", which is exactly the kind of
            # silent assumption this audit exists to prevent.
            charged[field] = "not_recorded"
            absent.append(field)
    block: dict = {
        "formula": "sum over calls of (uncompressed per-call input - compressed per-call "
                   "input) - protection cost",
        "charges": "repeats, remedies and guard re-sends are charged in full",
        "charged_in_this_batch": charged,
        "charged_fields_not_recorded": absent,
        "independent_of_the_projection": "the uncompressed side is this batch's own provider "
                                         "counts, so the bound is measured, not projected",
        "source": rel(batch_bound_path) if Path(batch_bound_path).is_file() else None,
    }
    if Path(batch_bound_path).is_file():
        recorded = json.loads(Path(batch_bound_path).read_text(encoding="utf-8"))
        block["recorded"] = recorded.get("batch_totals") or {}
        block["per_task"] = recorded.get("per_task") or recorded.get("units") or []
        block["recorded_gate_3"] = (recorded.get("batch_totals") or {}).get("gate_3")
    else:
        block["recorded"] = {}
        block["per_task"] = []
        block["recorded_gate_3"] = None
    return block


def audit(
    directory: Path = DEFAULT_BATCH,
    freeze_path: Path = DEFAULT_FREEZE,
    out_path: Path | None = DEFAULT_OUT,
) -> dict:
    directory, freeze_path = Path(directory), Path(freeze_path)
    errors: list[str] = []
    authority, freeze, amendment = pin_authority(freeze_path)
    for relative, expected in sorted(authority.items()):
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"pinned source missing: {relative}")
        elif expected and sha(path) != expected:
            errors.append(f"source drift (unregistered digest): {relative}")

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = load_rows(directory)
    task_data = json.loads(TASK_FILE.read_text(encoding="utf-8"))
    expected_ids = [str(t["task_id"]) for t in task_data]
    if manifest.get("tasks") != expected_ids:
        errors.append("manifest task grid does not match the frozen task file")
    if [str(r.get("task_id")) for r in rows] != expected_ids:
        errors.append("missing, duplicate or reordered sample")
    if not rows:
        errors.append("no rows: an empty batch is never a pass")
    if manifest.get("citable_as_saving") is not False or (
        manifest.get("citable_as_quality_equivalence") is not False
    ):
        errors.append("batch is incorrectly marked citable")

    # Cheap internal consistency of the trace the criteria are computed from.
    for row in rows:
        stage_flat = [name for stage in (row.get("role_tool_traces") or []) for name in stage]
        if stage_flat[: len(row.get("first_role_trace") or [])] != list(
            row.get("first_role_trace") or []
        ):
            errors.append(f"first_role_trace is not the head of role_tool_traces: {row.get('task_id')}")

    blocks = criteria_blocks(rows)
    if list(criteria.QUALITY_CRITERIA) != [criteria.CRIT_COVERAGE]:
        errors.append("the quality side is not exactly CRIT_R20_COVERAGE")
    for cid in (criteria.CRIT_REPEAT, criteria.CRIT_SAME_WORK):
        if blocks[cid]["gate"] is not False:
            errors.append(f"{cid} was promoted to a gate")
    for unit in blocks["units"]:
        if not unit["declared_expected_trace_matches_task_file"]:
            errors.append(f"row expected trace disagrees with the task file: {unit['task_id']}")

    guards = guard_scan(rows)
    if guards["attempts_with_no_guard_installed"] != guards["attempts"]:
        errors.append("an attempt in this batch ran with a guard installed")
    if not guards["baseline_zero_trigger"]:
        errors.append("a guard control text reached a prepared message")

    bound = saving_bound(BATCH_BOUND, rows)

    # Cross-check, never trust: the acquisition result records the same criteria.
    recorded_cross_check = {"artifact": rel(RECORDED_RESULT),
                            "present": RECORDED_RESULT.is_file()}
    if recorded_cross_check["present"]:
        recorded = json.loads(RECORDED_RESULT.read_text(encoding="utf-8"))
        rec = (recorded.get("criteria") or {}).get("batch") or {}
        mine = blocks[criteria.CRIT_COVERAGE]
        recorded_cross_check.update(
            {
                "recorded_units": rec.get("units"),
                "recomputed_units": mine["units"],
                "recorded_coverage_satisfied": rec.get("coverage_satisfied"),
                "recomputed_coverage_satisfied": mine["satisfied"],
                "recorded_repeat_calls_total": rec.get("repeat_calls_total"),
                "recomputed_repeat_calls_total": blocks[criteria.CRIT_REPEAT][
                    "repeat_calls_total"
                ],
            }
        )
        recorded_cross_check["agrees"] = (
            rec.get("units") == mine["units"]
            and rec.get("coverage_satisfied") == mine["satisfied"]
            and rec.get("repeat_calls_total")
            == blocks[criteria.CRIT_REPEAT]["repeat_calls_total"]
        )
        if not recorded_cross_check["agrees"]:
            errors.append("recorded criteria disagree with the independent recomputation")

    report = {
        "artifact": "R20_ORDERFREE_BATCH_AUDIT_CRITERIA_20261006",
        "independent": True,
        "imports_a_runner": False,
        "directory": rel(directory),
        "freeze": rel(freeze_path),
        "freeze_sha256": sha(freeze_path),
        "amendment_present": bool(amendment),
        "pins_checked": sorted(authority),
        "criteria": blocks,
        "guard_configuration": {
            "order_guard": "not_installed",
            "repeat_guard": "not_installed",
            "actionless_rule": "not_installed",
            "scan": guards,
        },
        "bound": bound,
        "recorded_cross_check": recorded_cross_check,
        "quality_decision": {
            "quality_criteria": list(criteria.QUALITY_CRITERIA),
            "coverage_passed": blocks[criteria.CRIT_COVERAGE]["all_satisfied"],
            "verdict": (
                "pass" if blocks[criteria.CRIT_COVERAGE]["all_satisfied"] else "fail"
            ),
        },
        "report_only": {
            "ids": blocks["report_only_ids"],
            "note": "repeats and same work are experimental control quantities on these "
                    "tasks; they are reported and never gated",
            criteria.CRIT_REPEAT: blocks[criteria.CRIT_REPEAT],
            criteria.CRIT_SAME_WORK: blocks[criteria.CRIT_SAME_WORK],
        },
        "errors_rule": ERRORS_RULE,
        "can_be_quoted_as_saving": not errors,
        "not_citable_note": "purpose=" + str(manifest.get("purpose"))
        + "; a payload-acquisition batch is never a saving or a quality-equivalence claim, "
        "whatever the sign of a bound computed on it",
        "errors": errors,
        "complete": not errors,
        "criteria_module_sha256": sha(
            ROOT / "experiments/audits/criteria_crewai_r20_orderfree.py"
        ),
        "audit_module_sha256": sha(Path(__file__)),
        "zero_api": True,
    }
    if out_path is not None:
        Path(out_path).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return report


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    directory = Path(argv[0]) if argv else DEFAULT_BATCH
    out = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUT
    report = audit(directory, DEFAULT_FREEZE, out)
    lines = [
        f"wrote {out.relative_to(ROOT)}",
        "errors: " + json.dumps(report["errors"], ensure_ascii=False),
        "quality: " + json.dumps(report["quality_decision"], ensure_ascii=False),
        "coverage: " + json.dumps(
            {
                k: v
                for k, v in report["criteria"][criteria.CRIT_COVERAGE].items()
                if k != "computed_from"
            },
            ensure_ascii=False,
        ),
        "report_only: " + json.dumps(
            {
                "ids": report["report_only"]["ids"],
                "repeat_calls_total": report["report_only"][criteria.CRIT_REPEAT][
                    "repeat_calls_total"
                ],
                "same_work_applicable": report["report_only"][criteria.CRIT_SAME_WORK][
                    "applicable"
                ],
            },
            ensure_ascii=False,
        ),
        "guards: " + json.dumps(
            {
                k: v
                for k, v in report["guard_configuration"]["scan"].items()
                if k != "control_text_occurrences"
            },
            ensure_ascii=False,
        ),
        "bound: " + json.dumps(
            {
                "recorded": report["bound"]["recorded"],
                "charged_in_this_batch": report["bound"]["charged_in_this_batch"],
            },
            ensure_ascii=False,
        ),
        "can_be_quoted_as_saving: " + str(report["can_be_quoted_as_saving"]),
    ]
    text = "\n".join(lines)
    print(text)
    tmp = ROOT / ".tooling/tmp/r20_batch_audit.txt"
    if tmp.parent.is_dir():
        tmp.write_text(text, encoding="utf-8")
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
