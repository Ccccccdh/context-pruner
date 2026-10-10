"""Inventory of every strict-track failure recorded so far (zero API, read-only).

For each failing sample this collects exactly what a reviewer needs in order to see what a
relaxation would be relaxing: the frozen conditions it failed, whether the required literals
were present, whether any registered literal lost presence, and whether the plugin's
substituted items are recomputable from the record.

Nothing here writes to a batch, changes a score or re-runs anything.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_multitask_registry_v14 as registry_v14
from experiments.runners import openai_agents_requests_task_registry_v13 as registry_v13

REPO = Path(__file__).resolve().parents[2]
RUNS = REPO / "runs/stage5-openai-agents-api"
OUT = REPO / "integrations/openai_agents/STRICT_FAILURE_INVENTORY_20261005.json"
V12_BOUNDARY = REPO / "integrations/openai_agents/V12_DECISION_BOUNDARY_TABLE_20261005.json"


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def v12_entries() -> list[dict]:
    report = json.loads(V12_BOUNDARY.read_text(encoding="utf-8"))
    rows = read_jsonl(RUNS / "openai-repo-diagnostic-v12-exact-duplicate-dev-01/samples.jsonl")
    invariants = {row["boundary"]: row for row in report["boundaries"]}
    entries = []
    for row in rows:
        quality = report["quality"][str(row["method"])]
        if quality["recorded_final_format_correct"] and quality["recorded_success"]:
            continue
        failing = [name for name, ok in quality["format_conditions"].items() if not ok]
        entries.append(
            {
                "batch": "v12",
                "task": "django_long_investigation",
                "repeat": 0,
                "method": str(row["method"]),
                "answer_chars": quality["answer_chars"],
                "answer": quality["answer"],
                "frozen_conditions_failed": failing,
                "only_length_failed": failing == ["at_most_160_chars"],
                "required_literals_present": quality["required_terms_present"],
                "frozen_regex_fullmatch": quality["frozen_regex_fullmatch"],
                "presence_zero_loss": all(
                    not entry["lost_presence"] for entry in invariants.values()
                ),
                "pointer_recomputable": all(
                    entry["plugin_outputs_match_record"] for entry in invariants.values()
                ),
                "frozen_contract": report["frozen_regex"],
                "required_terms": report["required_terms"],
                "recorded_success": quality["recorded_success"],
            }
        )
    return entries


def v13_entries() -> list[dict]:
    audit = json.loads(
        (
            RUNS
            / "openai-repo-diagnostic-v13-requests1766-confirm-01/audit.json"
        ).read_text(encoding="utf-8")
    )
    entries = []
    for key, quality in audit["quality"].items():
        if quality["track_A"]["pass"]:
            continue
        task, method, repeat = key.split("|")
        failing = [name for name, ok in quality["track_A"]["conditions"].items() if not ok]
        entries.append(
            {
                "batch": "v13",
                "task": task,
                "repeat": int(repeat[1:]),
                "method": method,
                "answer_chars": quality["track_A"]["characters"],
                "answer": quality["answer"],
                "frozen_conditions_failed": failing,
                "only_length_failed": failing == ["within_max_chars"],
                "required_literals_present": all(quality["track_A"]["required_terms"].values()),
                "frozen_regex_fullmatch": quality["track_A"]["conditions"][
                    "frozen_regex_fullmatch"
                ],
                "presence_zero_loss": not any(
                    error.startswith("literal_absent") for error in audit["errors"]
                ),
                "pointer_recomputable": not any(
                    error.startswith("pointer_recompute") for error in audit["errors"]
                ),
                "frozen_contract": audit["quality"][key]["answer"],
                "required_terms": list(registry_v13.REQUIRED_TERMS),
                "recorded_success": quality["recorded_success"],
            }
        )
    return entries


def v14_entries() -> list[dict]:
    audit = json.loads(
        (
            RUNS / "openai-repo-diagnostic-v14-multitask-confirm-01/audit.json"
        ).read_text(encoding="utf-8")
    )
    rows = {
        (str(row["scenario"]), int(row["repeat"]), str(row["method"])): row
        for row in read_jsonl(RUNS / "openai-repo-diagnostic-v14-multitask-confirm-01/samples.jsonl")
    }
    entries = []
    for key, quality in audit["quality"].items():
        if quality["track_A"]["pass"]:
            continue
        task, method, repeat = key.split("|")
        repeat_index = int(repeat[1:])
        failing = [name for name, ok in quality["track_A"]["conditions"].items() if not ok]
        entry = next(
            item
            for item in audit["boundaries"]
            if item["task_id"] == task and item["repeat"] == repeat_index and item["method"] == method
        )
        entries.append(
            {
                "batch": "v14",
                "task": task,
                "repeat": repeat_index,
                "method": method,
                "answer_chars": quality["answer_chars"],
                "answer": rows[(task, repeat_index, method)]["final_output"],
                "frozen_conditions_failed": failing,
                "only_length_failed": failing == ["within_max_chars"],
                "required_literals_present": all(quality["track_A"]["required_terms"].values()),
                "frozen_regex_fullmatch": quality["track_A"]["conditions"][
                    "frozen_regex_fullmatch"
                ],
                "presence_zero_loss": not any(
                    error.startswith("literal_absent") for error in audit["errors"]
                ),
                "pointer_recomputable": not any(
                    error.startswith("unrecomputable_change") for error in audit["errors"]
                ),
                "frozen_contract": registry_v14.task(task)["contract"],
                "required_terms": list(registry_v14.task(task)["required_terms"]),
                "recorded_success": quality["recorded_success"],
                "boundaries": entry["boundaries"],
            }
        )
    return entries


def main() -> int:
    entries = v12_entries() + v13_entries() + v14_entries()
    report = {
        "schema": "openai_agents_strict_failure_inventory",
        "date": "20261005",
        "scope": "every recorded strict-track (Track A) failure in v12, v13 and v14",
        "total_failures": len(entries),
        "all_failures_are_plugin_arm": all(entry["method"] == "pruner_v1" for entry in entries),
        "all_only_length_failed": all(entry["only_length_failed"] for entry in entries),
        "all_required_literals_present": all(
            entry["required_literals_present"] for entry in entries
        ),
        "all_frozen_regex_matched": all(entry["frozen_regex_fullmatch"] for entry in entries),
        "presence_zero_loss_everywhere": all(entry["presence_zero_loss"] for entry in entries),
        "pointers_recomputable": all(entry["pointer_recomputable"] for entry in entries),
        "entries": entries,
        "reading": (
            "every strict failure is the frozen at-most-160-characters condition on the plugin "
            "arm's wording; the answer facts, the frozen regex, literal presence and the "
            "pointer reconstruction all hold. This inventory is a description of what a "
            "relaxation would relax, and changes nothing."
        ),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)}")
    print(
        f"total failures {report['total_failures']} | all plugin arm {report['all_failures_are_plugin_arm']} "
        f"| only length failed {report['all_only_length_failed']} | literals present "
        f"{report['all_required_literals_present']} | regex matched {report['all_frozen_regex_matched']} "
        f"| presence zero loss {report['presence_zero_loss_everywhere']} | pointers recomputable "
        f"{report['pointers_recomputable']}"
    )
    for entry in entries:
        print(
            f"  {entry['batch']:>4} {entry['task']:<28} r{entry['repeat']} {entry['method']:<9} "
            f"chars={entry['answer_chars']:>3} failing={entry['frozen_conditions_failed']}"
        )
        print(f"       answer: {entry['answer'][:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
