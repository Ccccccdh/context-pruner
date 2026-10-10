"""v19: read shape as a parameter, dose-response on the fixed development tasks.

The branch pauses new-task registration. The three existing development tasks keep their views,
ranges, hashes, literals, frozen regex and prompt exactly as the v17 registry has them; only the
batching of the reads changes:

* ``N2`` three views batched three per round, two rounds (already measured in the v17 pilot);
* ``N3`` three views, one distinct view per round, three rounds;
* ``N6`` three views, one view per round, two cycles, six rounds; the content equals N2's.

Everything is implemented by configuration of the existing v17 modules at run time, so no written
file changes. ``--mode gate`` runs the zero-API gates, ``--mode pilot`` runs the paid pilot.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v16_live_contract as live16  # noqa: E402
from experiments.runners import openai_agents_v17_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v17_registry as registry  # noqa: E402
from experiments.runners import run_openai_agents_api_experiment as base  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner  # noqa: E402
from experiments.runners.openai_agents_structured_final_v16 import render  # noqa: E402

SHAPES: dict[str, dict[str, Any]] = {
    "N2": {"plan": [0, 1, 2, 0, 1, 2], "reads_per_round": 3, "rounds": 2},
    "N3": {"plan": [0, 1, 2], "reads_per_round": 1, "rounds": 3},
    "N6": {"plan": [0, 1, 2, 0, 1, 2], "reads_per_round": 1, "rounds": 6},
}
GATE_OUT = ROOT / "integrations/openai_agents/V19_ZERO_API_GATE_20261010.json"
OLD_WORDING = ("three calls of a round", "a single read on its own")
ARMS = ("none", "native_pruner_placeholder", "pruner_v1")  # replaced below by runner.PILOT_METHODS


def plan_steps(task_id: str, shape: str) -> list[str]:
    spec = SHAPES[shape]
    entry = registry.task(task_id)
    names = list(entry["tool_names"])
    views = list(entry["views"])
    rounds = []
    for index in range(spec["rounds"]):
        start = index * spec["reads_per_round"]
        chunk = [
            (names[position % len(names)], views[position % len(views)])
            for position in spec["plan"][start : start + spec["reads_per_round"]]
        ]
        rounds.append(
            f"round {index + 1}: "
            + ", ".join(
                f"{name} ({view['file']} {view['first_line']}-{view['last_line']})"
                for name, view in chunk
            )
        )
    if spec["reads_per_round"] == 1:
        batching = (
            "Issue each round's single read in its own turn and wait for its result before the "
            "next round: do not merge two rounds into one turn, and do not issue more than one "
            "read in a turn."
        )
    else:
        batching = (
            f"Issue the {spec['reads_per_round']} reads of a round together in one turn, as a "
            "single batch, and wait for their results before the next round: do not split a round "
            "across turns."
        )
    return [
        f"Read {spec['reads_per_round']} registered view(s) per round for {spec['rounds']} "
        f"rounds, in this order, and never read a registered view more often than the plan "
        f"requires: " + "; ".join(rounds) + f". That is {len(spec['plan'])} read calls.",
        batching,
        f"After the last read, having issued all {len(spec['plan'])} reads, answer with the JSON "
        "object the request describes and call no more tools: the host renders your JSON into "
        "the single RESULT line.",
    ]


def cap_for(shape: str) -> dict[str, Any]:
    spec = SHAPES[shape]
    read_calls = len(spec["plan"])
    max_turns = read_calls + 2
    worst_per_sample = max_turns + live.RETRY_BUDGET_PER_SAMPLE
    samples = 9
    summary_worst = 3 * 2
    worst = samples * worst_per_sample + summary_worst
    cap = int(math.ceil(1.3 * worst / 10.0) * 10)
    return {
        "shape": shape,
        "rounds": spec["rounds"],
        "reads_per_round": spec["reads_per_round"],
        "read_calls": read_calls,
        "expected_requests_per_sample": spec["rounds"] + 1,
        "max_turns": max_turns,
        "worst_case_requests_per_sample": worst_per_sample,
        "samples": samples,
        "worst_case_requests": worst,
        "cap": cap,
        "margin": round(cap / worst, 3),
        "at_least_1_3": cap >= 1.3 * worst,
    }


def configure(shape: str) -> dict[str, Any]:
    spec = SHAPES[shape]
    arithmetic = cap_for(shape)
    registry.protocol_steps = lambda task_id: plan_steps(task_id, shape)
    registry.READ_CALLS = len(spec["plan"])
    registry.READ_ROUNDS = spec["rounds"]
    registry.READS_PER_ROUND = spec["reads_per_round"]
    registry.EXPECTED_MODEL_CALLS = spec["rounds"] + 1
    runner.MAX_TURNS = arithmetic["max_turns"]
    runner.PILOT_MAX_API_REQUESTS = arithmetic["cap"]
    runner.PILOT_EXPERIMENT_ID = f"openai-repo-diagnostic-v19-shape-{shape.lower()}-pilot-01"

    def build_case(task_id: str, repeat: int):
        entry = registry.task(task_id)
        contract = entry["contract"]
        case = base.ApiCase(
            scenario=task_id,
            repeat=int(repeat),
            codename=entry["instance_id"],
            history=registry.history(task_id),
            tools=runner.tools_for(task_id),
            expected_terms=tuple(str(fact) for fact in contract["required_facts"]),
            expected_tool_names=tuple(
                entry["tool_names"][position % len(entry["tool_names"])]
                for position in spec["plan"]
            ),
            expected_model_calls=spec["rounds"] + 1,
            final_contract=entry["contract_prompt"],
            answer_pattern=str(contract["frozen_regex"]),
            allow_repeat_tools=True,
            disable_thinking=True,
        )
        return runner._CaseWithTaskStatement(case, registry.statement(task_id))

    runner.build_case = build_case
    return arithmetic


def freeze_override(shape: str, arithmetic: dict[str, Any]) -> dict[str, Any]:
    freeze = json.loads(runner.PILOT_FREEZE.read_text(encoding="utf-8"))
    freeze = json.loads(json.dumps(freeze))
    freeze["path"] = f"<v17 development freeze with an in-memory cap override for shape {shape}>"
    freeze["sha256"] = "0" * 64
    freeze["budget_arithmetic"] = {
        **freeze.get("budget_arithmetic", {}),
        "max_api_requests": arithmetic["cap"],
        "worst_case_requests": arithmetic["worst_case_requests"],
        "cap_over_worst_case": arithmetic["margin"],
    }
    freeze["shape"] = shape
    return freeze


def gate(shape: str) -> dict[str, Any]:
    arithmetic = configure(shape)
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.json"
        plan_status = runner.main(["--plan", "--artifact", str(plan_path)])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        record(
            "plan_is_zero_api",
            plan_status == 0 and plan.get("api_calls") == 0,
            {"api_calls": plan.get("api_calls")},
        )
        stub_path = Path(tmp) / "stub.json"
        stub_status = runner.main(["--stub", "--artifact", str(stub_path)])
        stub = json.loads(stub_path.read_text(encoding="utf-8"))
        controls = stub.get("controls") or []
        record(
            "stub_controls_all_pass_and_at_least_twelve",
            stub_status == 0 and stub.get("controls_ok") and len(controls) >= 12,
            {"controls": len(controls), "ok": stub.get("controls_ok")},
        )
        record(
            "stub_is_zero_api",
            stub.get("api_calls") == 0,
            {"api_calls": stub.get("api_calls")},
        )
        # the pilot finalise path must really be exercised, with a local model
        pilot_status = runner.main(["--pilot-stub"])
        record(
            "pilot_finalise_path_exercised_with_a_local_model",
            pilot_status == 0,
            {"pilot_stub_status": pilot_status},
        )

    spec = SHAPES[shape]
    steps = plan_steps(registry.task_ids()[0], shape)
    text = "\n".join(steps)
    record(
        "protocol_matches_the_shape",
        f"{spec['rounds']} rounds" in text
        and f"{len(spec['plan'])} read calls" in text
        and all(f"round {index + 1}:" in text for index in range(spec["rounds"])),
        {"rounds": spec["rounds"], "read_calls": len(spec["plan"])},
    )
    record(
        "one_read_per_turn_rule_matches_the_batching",
        (
            "do not issue more than one read in a turn" in text
            if spec["reads_per_round"] == 1
            else "together in one turn" in text
        ),
        {"reads_per_round": spec["reads_per_round"]},
    )
    record(
        "dedupe_and_no_merge_rules_present",
        "never read a registered view more often" in text
        and ("do not merge two rounds into one turn" in text or "do not split a round" in text),
    )
    record(
        "old_shape_wording_absent",
        all(probe not in text for probe in OLD_WORDING)
        and all(
            probe
            not in Path("experiments/runners/openai_agents_v17_registry.py").read_text(
                encoding="utf-8"
            )
            for probe in OLD_WORDING
        ),
    )
    case = runner.build_case(registry.task_ids()[0], 0)
    record(
        "case_shape_matches_the_plan",
        case.expected_model_calls == spec["rounds"] + 1
        and len(case.expected_tool_names) == len(spec["plan"]),
        {
            "expected_model_calls": case.expected_model_calls,
            "tool_calls": len(case.expected_tool_names),
        },
    )
    issue = registry.task(registry.task_ids()[0])["contract"]["issue_value"]
    fixed = {
        "raw": json.dumps(
            {
                "issue": issue,
                "cause": registry.task(registry.task_ids()[0])["cause_token"],
                "fix": registry.task(registry.task_ids()[0])["fix_token"],
            }
        ),
        "expected_issue": issue,
        "required_facts": ["__hash__", "__eq__", "creation_counter"],
        "cause_token": "__hash__",
        "fix_token": "creation_counter",
    }
    record(
        "retry_diagnostic_still_byte_identical_to_v16",
        all(
            live16.diagnostic_for(reason, **fixed) == live.diagnostic_for(reason, **fixed)
            for reason in ("over_160_chars", "declared_fact_missing_from_rendered_answer")
        ),
    )
    record(
        "four_conditions_still_identical_to_v16",
        live16.conditions(
            "RESULT issue=x cause=y fix=z", "x", "RESULT issue=x cause=.*y.* fix=.*z.*", ["y", "z"]
        )
        == live.conditions(
            "RESULT issue=x cause=y fix=z", "x", "RESULT issue=x cause=.*y.* fix=.*z.*", ["y", "z"]
        ),
    )
    missing = json.dumps(
        {
            "issue": issue,
            "cause": "a cause",
            "fix": "a fix",
            "facts": ["__hash__", "__eq__"],
        }
    )
    verdict = render(missing, expected_issue=issue)
    record(
        "declared_literal_missing_from_the_line_still_rejected",
        (not verdict.accepted) and verdict.reason == "declared_fact_missing_from_rendered_answer",
        {"reason": verdict.reason},
    )
    long_answer = json.dumps(
        {
            "issue": issue,
            "cause": ("a " * 120).strip(),
            "fix": "a fix",
            "facts": ["a"],
        }
    )
    long_verdict = render(long_answer, expected_issue=issue)
    record(
        "over_160_still_rejected", long_verdict.reason == "over_160_chars", {"reason": long_verdict.reason}
    )
    record(
        "cap_arithmetic_written_into_the_gate_and_at_least_1_3x",
        arithmetic["at_least_1_3"] and arithmetic["margin"] >= 1.3,
        arithmetic,
    )
    return {"shape": shape, "checks": checks, "ok": all(entry["ok"] for entry in checks)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shape", choices=sorted(SHAPES), required=True)
    parser.add_argument("--mode", choices=("gate", "pilot"), default="gate")
    parser.add_argument("--confirm-send-public-source", action="store_true")
    args = parser.parse_args()
    if args.mode == "pilot":
        arithmetic = configure(args.shape)
        if not args.confirm_send_public_source:
            raise SystemExit("refusing to send public source without --confirm-send-public-source")
        return runner.run_pilot(
            ["--confirm-send-public-source"],
            freeze_override=freeze_override(args.shape, arithmetic),
        )
    result = gate(args.shape)
    existing = json.loads(GATE_OUT.read_text(encoding="utf-8")) if GATE_OUT.is_file() else {
        "schema": "openai_agents_v19_zero_api_gate",
        "date": "20261010",
        "shapes": {},
        "paid_requests": 0,
    }
    existing["shapes"][args.shape] = result
    existing["all_shapes_ok"] = all(
        entry.get("ok") for entry in existing["shapes"].values()
    )
    GATE_OUT.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"shape": args.shape, "ok": result["ok"]}, ensure_ascii=False))
    for entry in result["checks"]:
        print(f"  {entry['check']}: ok={entry['ok']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
