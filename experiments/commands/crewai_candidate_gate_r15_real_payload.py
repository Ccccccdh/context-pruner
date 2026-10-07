"""R15 candidate gate on **real recorded CrewAI payloads** (read-only, zero API).

Three gates, in order, on the payload acquired by
``experiments.commands.run_crewai_r15_payload_acquisition``:

  **Gate 1 -- real payload.**  The batch directory must exist, its manifest must
  declare the uncompressed acquisition purpose, every row must carry a payload block
  with per-call message fingerprints and a payload hash, and the recorded source
  hashes (results / manifest) must match the files on disk.  A mock batch is refused
  outright: mock data may validate interfaces but cannot feed an upper bound.

  **Gate 2 -- quality-safe candidate space.**  For every recorded unit (task, repeat):
  the first role's tool sequence equals the frozen tool list, every frozen evidence
  source was actually called, the first-pass HANDOFF carries all ``handle_facts``
  literals, and no remedy call was needed.  Only such units may be counted.

  **Gate 3 -- >=3% optimistic upper bound, >=3 tasks.**  In the same workload, the
  complete provider token spend is ``uncompressed total - what compression could
  remove + what protection costs``.  Two bounds are computed per unit, and the
  **stricter one decides the gate**, because a per-call compressor pays for its own
  output on every later call:

  * ``ceiling``:  ``first-call frame tokens`` -- the whole provider input of the first
    call, i.e. the text that exists before any tool evidence does.  This assumes the
    compressed replacement is free, so it is an over-estimate by construction;
  * ``realizable`` (**the gate**): the frame text that is charged **exactly once** in
    the uncompressed run, i.e. the first-call frame characters minus the bytes by
    which later calls grew -- because those bytes are the compressed replacement being
    re-sent on every subsequent call.  Protection cost (pins / whole-prefix fallback /
    auxiliary summaries) is subtracted from both.  In this uncompressed acquisition run
    all three protection terms are genuinely zero, so the numbers are the mechanism's
    own ceilings.

  Instrumental work (fewer tool calls, fewer model calls) is never counted as saving:
  gate 2 requires the frozen tool list to be called in order, so every counted unit did
  the same work.

Output: ``integrations/crewai/R15_CANDIDATE_GATE_REAL_PAYLOAD_<date>.json``.

Run:

    & .\\.venv-crewai\\Scripts\\python.exe -m experiments.commands.crewai_candidate_gate_r15_real_payload
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = ROOT / "runs/stage5-crewai/crewai-r15-payload-none-01"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r15.json"
OUTPUT = ROOT / "integrations/crewai/R15_CANDIDATE_GATE_REAL_PAYLOAD_20261005.json"
THRESHOLD_PERCENT = 3.0
MIN_TASKS_WITH_HEADROOM = 3


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load(directory: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (directory / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return manifest, rows


def _tasks() -> dict[str, dict[str, Any]]:
    return {
        str(task["task_id"]): task
        for task in json.loads(TASK_FILE.read_text(encoding="utf-8"))
    }


def gate1(directory: Path, manifest: dict[str, Any],
          rows: list[dict[str, Any]], *, allow_mock: bool = False) -> dict[str, Any]:
    problems: list[str] = []
    if not directory.is_dir():
        return {"pass": False, "problems": [f"batch directory missing: {directory}"]}
    if manifest.get("mode") != "api" and not allow_mock:
        problems.append("batch was not a real host run (mode != api)")
    if manifest.get("purpose") != "payload_acquisition_only":
        problems.append("manifest does not declare payload-acquisition purpose")
    if manifest.get("citable_as_saving") is not False:
        problems.append("manifest does not state that this batch is not a saving")
    if manifest.get("methods") != ["none"]:
        problems.append("acquisition batch must contain the uncompressed arm only")
    for row in rows:
        payload = row.get("payload") or {}
        if not payload:
            problems.append(f"{row['task_id']}/{row['repeat']}: no payload block")
            continue
        if payload.get("compression_enabled") is not False:
            problems.append(f"{row['task_id']}/{row['repeat']}: compression was enabled")
        for role in ("first_role_payloads", "decider_payloads"):
            calls = payload.get(role) or []
            if not calls:
                problems.append(f"{row['task_id']}/{row['repeat']}: empty {role}")
                continue
            for index, call in enumerate(calls):
                if not call.get("message_fingerprints"):
                    problems.append(
                        f"{row['task_id']}/{row['repeat']}: {role}[{index}] has no fingerprints"
                    )
                if index == 0 and not call.get("messages"):
                    problems.append(
                        f"{row['task_id']}/{row['repeat']}: {role}[0] has no verbatim frame"
                    )
        if not payload.get("payload_sha256"):
            problems.append(f"{row['task_id']}/{row['repeat']}: no payload hash")
    sources = {
        name: _sha256_bytes((directory / name).read_bytes())
        for name in ("results.jsonl", "manifest.json")
        if (directory / name).is_file()
    }
    return {
        "pass": not problems,
        "problems": problems,
        "interface_check_only": bool(allow_mock),
        "batch": directory.name,
        "row_count": len(rows),
        "mode": manifest.get("mode"),
        "purpose": manifest.get("purpose"),
        "tasks_in_manifest": manifest.get("tasks"),
        "repeats": manifest.get("repeats"),
        "filler_history_pairs": manifest.get("filler_history_pairs"),
        "runner_sha256": manifest.get("runner_sha256"),
        "task_sha256": manifest.get("task_sha256"),
        "source_sha256": sources,
    }


def pinned_evidence_fragments(task: dict[str, Any]) -> list[str]:
    """Verbatim evidence fragments the frozen v9 rule pins, per required literal.

    The pinned fragment for a literal is the shortest frozen tool-result JSON that
    carries it -- exactly what ``crewai_pinned_evidence_v9._evidence_pins`` chooses.
    """
    fragments: list[str] = []
    for literal in [str(item) for item in task["handle_facts"]]:
        needle = literal.casefold().replace(" ", "")
        best = ""
        for tool in task["tools"]:
            payload = json.dumps(tool["result"], ensure_ascii=False, sort_keys=True)
            if needle and needle in payload.casefold().replace(" ", ""):
                if not best or len(payload) < len(best):
                    best = payload
        fragments.append(
            best or json.dumps(task["tools"][-1]["result"], ensure_ascii=False,
                               sort_keys=True)
        )
    return fragments


def _facet_breakdown(row: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """The four facets the prescreen requires to be reported separately.

    1. **history removable** -- the historical conversation text, measured per role
       from the recorded verbatim first frame, plus the share of the frame it is;
    2. **current evidence and tool rounds** -- the tool rounds and their evidence
       bytes, which the frozen rule protects verbatim and which therefore may never
       be counted as removable;
    3. **role decision behaviour** -- what the recorded roles actually did (calls,
       tool sequence, first-pass completeness, remedy calls), i.e. whether the run is
       a clean baseline at all;
    4. **task-level dispersion** -- filled in by the caller across tasks, so a single
       long history can never stand in for a stable result.
    """
    payload = row.get("payload") or {}
    first_calls = payload.get("first_role_payloads") or []
    frame_messages = (first_calls[0].get("messages") if first_calls else []) or []
    history_by_role: dict[str, int] = {}
    for message in frame_messages:
        role = str(message.get("role", ""))
        content = str(message.get("content", ""))
        if role == "system":
            continue
        # The frame is: constraint, one acknowledgement, N archive pairs, statement.
        if role == "assistant":
            history_by_role["assistant"] = history_by_role.get("assistant", 0) + len(content)
        else:
            history_by_role["user"] = history_by_role.get("user", 0) + len(content)
    frame_characters = int(
        (payload.get("first_role_frame_deltas") or [{"frame_characters": 0}])[0][
            "frame_characters"
        ]
    )
    tool_rounds = len(payload.get("first_role_frame_deltas") or [])
    evidence_bytes = sum(
        len(marker.encode("utf-8"))
        for tool in task["tools"]
        for marker in tool["evidence_parts"]
    )
    return {
        "history_removable_facet": {
            "assistant_history_characters": history_by_role.get("assistant", 0),
            "user_history_characters": history_by_role.get("user", 0),
            "frame_characters": frame_characters,
            "history_share_of_frame_percent": round(
                sum(history_by_role.values()) / frame_characters * 100, 2
            ) if frame_characters else 0.0,
            "note": (
                "the prescreen shows this facet alone does not predict saving "
                "(r4/r9: 572-646 assistant characters yet -0.26% to +22.25%); it is "
                "reported separately and never used as the saving estimate"
            ),
        },
        "current_evidence_and_tool_rounds_facet": {
            "frozen_tool_count": len(task["tools"]),
            "first_role_recorded_rounds": tool_rounds,
            "evidence_marker_bytes": evidence_bytes,
            "protected_verbatim": True,
            "note": (
                "every frozen tool's evidence bytes are protected verbatim by the "
                "frozen rule, so they are never removable"
            ),
        },
        "role_decision_behaviour_facet": {
            "first_role_calls": int(payload.get("first_role_call_count", 0)),
            "decider_calls": int(payload.get("decider_call_count", 0)),
            "first_role_trace": list(row.get("first_role_trace") or []),
            "expected_trace": [str(tool["name"]) for tool in task["tools"]],
            "first_pass_complete": bool(row.get("first_pass_complete")),
            "remedy_calls": int(row.get("recovery_invocations", 0)),
            "strict_success": bool(row.get("strict_success")),
            "semantic_success": bool(row.get("semantic_success")),
            "error": row.get("error") or "",
        },
    }


def _guard_survivors(task: dict[str, Any]) -> dict[str, Any]:
    """What the frozen literal guards keep, reported next to the potential set.

    ``potential_old_units_upper_bound`` is how many earlier units a recency policy
    could touch at all; ``guard_preserved_safe_candidates`` is what survives the
    frozen guards (every ``handle_facts`` literal plus its verbatim evidence fragment
    must stay visible).  The two are never merged: the first is an upper bound on
    opportunity, the second is what may be counted.
    """
    required = [str(item) for item in task["handle_facts"]]
    fragments = {
        tab: fragment
        for tab, fragment in zip(required, pinned_evidence_fragments(task))
    }
    return {
        "required_literals": required,
        "shortest_evidence_fragment_per_literal": {
            literal: len(fragment) for literal, fragment in fragments.items()
        },
        "fragments_must_stay_visible": True,
        "note": (
            "a guard-preserved safe candidate keeps every literal and its shortest "
            "evidence fragment in the view; the historical text is what remains "
            "removable after those guards"
        ),
    }


def gate2(rows: list[dict[str, Any]], tasks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Gate 2: which recorded units are quality-safe candidates for counting."""
    units: list[dict[str, Any]] = []
    for row in rows:
        task = tasks.get(str(row["task_id"]))
        if task is None:
            units.append({"task_id": row["task_id"], "repeat": row["repeat"],
                          "safe_candidate": False, "reason": "unknown task"})
            continue
        expected = [str(tool["name"]) for tool in task["tools"]]
        trace = list(row.get("first_role_trace") or [])
        called_all = all(name in trace for name in expected)
        safe = bool(
            trace == expected
            and called_all
            and bool(row.get("first_pass_complete"))
            and int(row.get("recovery_invocations", 0)) == 0
            and not row.get("error")
        )
        units.append(
            {
                "task_id": row["task_id"],
                "repeat": int(row["repeat"]),
                "arm": row.get("method"),
                "first_role_trace": trace,
                "expected_trace": expected,
                "sources_called": len(trace),
                "sources_required": len(expected),
                "first_pass_complete": bool(row.get("first_pass_complete")),
                "remedy_calls": int(row.get("recovery_invocations", 0)),
                "strict_success": bool(row.get("strict_success")),
                "semantic_success": bool(row.get("semantic_success")),
                "error": row.get("error") or "",
                "safe_candidate": safe,
            }
        )
    by_task: dict[str, dict[str, Any]] = {}
    for unit in units:
        entry = by_task.setdefault(
            str(unit["task_id"]), {"units": [], "safe_units": 0, "total_units": 0}
        )
        entry["units"].append(unit)
        entry["total_units"] += 1
        entry["safe_units"] += int(unit["safe_candidate"])
    return {
        "pass": bool(by_task) and all(
            entry["safe_units"] >= 1 for entry in by_task.values()
        ),
        "tasks_with_a_safe_unit": sum(
            1 for entry in by_task.values() if entry["safe_units"] >= 1
        ),
        "tasks_total": len(by_task),
        "by_task": by_task,
        "units": units,
    }


def gate3(rows: list[dict[str, Any]], tasks: dict[str, dict[str, Any]],
          gate2_result: dict[str, Any]) -> dict[str, Any]:
    """The optimistic upper bound of what compression could remove, per task."""
    safe_keys = {
        (unit["task_id"], unit["repeat"])
        for unit in gate2_result["units"] if unit["safe_candidate"]
    }
    per_task: dict[str, Any] = {}
    for task_id, task in tasks.items():
        units = [
            row for row in rows
            if str(row["task_id"]) == task_id
            and (str(row["task_id"]), int(row["repeat"])) in safe_keys
        ]
        if not units:
            per_task[task_id] = {
                "safe_units": 0,
                "headroom_tokens": None,
                "upper_bound_percent": None,
                "potential_old_units_upper_bound": len(task["tools"]),
                "guard_preserved_safe_candidates": 0,
                "note": "no quality-safe unit for this task",
            }
            continue
        headrooms: list[int] = []
        ratios: list[float] = []
        ceilings: list[float] = []
        detail: list[dict[str, Any]] = []
        for row in units:
            payload = row.get("payload") or {}
            first_role_deltas = payload.get("first_role_frame_deltas") or []
            decider_deltas = payload.get("decider_frame_deltas") or []
            if not first_role_deltas:
                continue
            frame_tokens = int(first_role_deltas[0]["input_tokens"])
            frame_characters = int(first_role_deltas[0]["frame_characters"])
            total = int(row["all_arm_total_tokens"])
            characters_per_token = (
                frame_characters / frame_tokens if frame_tokens else 0.0
            )
            # Text charged exactly once: the first frame minus the bytes later calls
            # grew by, because that growth is the compressed replacement being re-sent.
            later_growth_characters = max(
                int(delta["frame_characters"]) - frame_characters
                for delta in first_role_deltas
            )
            once_only_characters = max(0, frame_characters - later_growth_characters)
            once_only_tokens = (
                round(once_only_characters / characters_per_token)
                if characters_per_token else 0
            )
            # Protection cost: on the uncompressed arm every term is genuinely zero.
            pinned_characters = int(row.get("pinned_characters_total", 0))
            fallback_tokens = int(row.get("required_fact_whole_prefix_fallbacks", 0)) * frame_tokens
            summary_tokens = int(row.get("summary_input_tokens", 0)) + int(
                row.get("summary_output_tokens", 0)
            )
            pin_tokens = (
                round(pinned_characters / characters_per_token)
                if characters_per_token else 0
            )
            protection = pin_tokens + fallback_tokens + summary_tokens
            ceiling_percent = (
                round(max(0, frame_tokens - protection) / total * 100, 4) if total else 0.0
            )
            headroom = max(0, once_only_tokens - protection)
            ratios.append(round(headroom / total * 100, 4) if total else 0.0)
            ceilings.append(ceiling_percent)
            headrooms.append(headroom)
            detail.append(
                {
                    "repeat": int(row["repeat"]),
                    "first_frame_input_tokens": frame_tokens,
                    "first_frame_characters": frame_characters,
                    "later_growth_characters": later_growth_characters,
                    "once_only_characters": once_only_characters,
                    "once_only_tokens": once_only_tokens,
                    "characters_per_token": round(characters_per_token, 4),
                    "decider_first_frame_input_tokens": (
                        int(decider_deltas[0]["input_tokens"]) if decider_deltas else 0
                    ),
                    "uncompressed_total_tokens": total,
                    "protection_cost_tokens": protection,
                    "protection_terms": {
                        "pinned_characters": pinned_characters,
                        "pin_tokens": pin_tokens,
                        "whole_prefix_fallback_tokens": fallback_tokens,
                        "auxiliary_summary_tokens": summary_tokens,
                    },
                    "headroom_tokens": headroom,
                    "upper_bound_percent": ratios[-1],
                    "ceiling_upper_bound_percent": ceiling_percent,
                    "first_role_call_count": int(payload.get("first_role_call_count", 0)),
                    "decider_call_count": int(payload.get("decider_call_count", 0)),
                }
            )
        per_task[task_id] = {
            "safe_units": len(units),
            "headroom_tokens": round(mean(headrooms), 2) if headrooms else None,
            "upper_bound_percent": round(mean(ratios), 4) if ratios else None,
            "ceiling_upper_bound_percent": round(mean(ceilings), 4) if ceilings else None,
            "potential_old_units_upper_bound": len(task["tools"]),
            "guard_preserved_safe_candidates": len(units),
            "guard_survivors": _guard_survivors(task),
            "facets_first_unit": _facet_breakdown(units[0], task) if units else None,
            "per_repeat": detail,
        }
    bounds = [
        entry["upper_bound_percent"] for entry in per_task.values()
        if entry["upper_bound_percent"] is not None
    ]
    ceilings = [
        entry["ceiling_upper_bound_percent"] for entry in per_task.values()
        if entry.get("ceiling_upper_bound_percent") is not None
    ]
    non_zero = [
        task_id for task_id, entry in per_task.items()
        if (entry["upper_bound_percent"] or 0) > 0
    ]
    batch_mean = round(mean(bounds), 4) if bounds else None
    passes = bool(
        len(non_zero) >= MIN_TASKS_WITH_HEADROOM
        and batch_mean is not None
        and batch_mean >= THRESHOLD_PERCENT
    )
    return {
        "pass": passes,
        "method": (
            "upper_bound(task) = mean over quality-safe units of "
            "((first-call frame characters - later-call growth characters) converted "
            "to tokens - protection cost), divided by that unit's complete uncompressed "
            "provider tokens.  The frame's growth is subtracted because those bytes are "
            "the compressed replacement being re-sent on every later call."
        ),
        "deciding_bound": "realizable (once-only frame text minus protection cost)",
        "ceiling_bound_note": (
            "the un-subtracted ceiling is reported per task for contrast and is not the "
            "gate: it assumes the compressed replacement costs nothing"
        ),
        "batch_mean_ceiling_upper_bound_percent": (
            round(mean(ceilings), 4) if ceilings else None
        ),
        "threshold_percent": THRESHOLD_PERCENT,
        "min_tasks_with_headroom": MIN_TASKS_WITH_HEADROOM,
        "tasks_with_nonzero_headroom": len(non_zero),
        "tasks_with_nonzero_headroom_ids": non_zero,
        "batch_mean_upper_bound_percent": batch_mean,
        "per_task": per_task,
        "protection_cost_note": (
            "pins, whole-prefix fallbacks and auxiliary summary tokens are all zero in "
            "this uncompressed acquisition run; the subtraction is implemented and "
            "becomes non-zero when a plugin arm exists (e.g. the K=3 structure rule "
            "re-adds protected tool rounds, whose token cost must be paid out of this "
            "headroom)"
        ),
        "not_counted_as_saving": (
            "fewer tool calls, fewer model calls and any other instrumental shortcut: "
            "gate 2 requires the frozen tool list to be called in order, so the units "
            "counted here did the same work"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    parser.add_argument("--force-mock", action="store_true",
                        help="allow a mock batch for interface checks only")
    args = parser.parse_args()

    tasks = _tasks()
    if not args.batch.is_dir():
        result = {
            "gate_1_real_payload": {"pass": False,
                                    "problems": [f"batch directory missing: {args.batch}"]},
            "gate_2_quality_safe_space": {"pass": False},
            "gate_3_upper_bound": {"pass": False},
            "paid_candidate_gate": "fail_missing_new_task_real_payload",
        }
    else:
        manifest, rows = _load(args.batch)
        real_payload = gate1(args.batch, manifest, rows, allow_mock=args.force_mock)
        if manifest.get("mode") != "api" and not args.force_mock:
            real_payload["pass"] = False
            real_payload.setdefault("problems", []).append(
                "mock-mode batch cannot feed the upper bound (use --force-mock for "
                "interface checks only)"
            )
        quality = gate2(rows, tasks) if real_payload["pass"] else {"pass": False}
        bound = (gate3(rows, tasks, quality) if real_payload["pass"] and quality["pass"]
                 else {"pass": False})
        if real_payload["pass"] and quality["pass"] and bound["pass"]:
            verdict = "pass_all_three_gates_prepare_three_arm_draft"
        elif not real_payload["pass"]:
            verdict = "fail_gate_1_missing_or_unusable_real_payload"
        elif not quality["pass"]:
            verdict = "fail_gate_2_no_quality_safe_space_on_every_task"
        else:
            verdict = "fail_gate_3_upper_bound_below_threshold"
        result = {
            "batch": str(args.batch.resolve().relative_to(ROOT)),
            "gate_1_real_payload": real_payload,
            "gate_2_quality_safe_space": quality,
            "gate_3_upper_bound": bound,
            "paid_candidate_gate": verdict,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
    print(json.dumps({
        "gate_1": result["gate_1_real_payload"].get("pass"),
        "gate_2": result["gate_2_quality_safe_space"].get("pass"),
        "gate_3": result["gate_3_upper_bound"].get("pass"),
        "verdict": result["paid_candidate_gate"],
        "batch_mean_upper_bound_percent":
            result["gate_3_upper_bound"].get("batch_mean_upper_bound_percent"),
        "per_task": {
            task_id: entry.get("upper_bound_percent")
            for task_id, entry in (result["gate_3_upper_bound"].get("per_task") or {}).items()
        },
        "problems": result["gate_1_real_payload"].get("problems"),
        "out": str(args.out.resolve().relative_to(ROOT)),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    main()
