"""Zero-API gate for the v17 host configuration (first-attempt compliance fix).

Checks, in order, each with the evidence written to
``integrations/openai_agents/V17_ZERO_API_GATE_20261010.json``:

1. the new prompt sentence is present, and the exact prompt text a model receives is
   byte-identical across the three arms (measured through the production wiring with a local
   model, per arm, then hashed);
2. the retry diagnostic is byte-identical to v16 on fixed inputs for every rejection class;
3. a proposal that declares one required literal outside cause/fix is still rejected;
4. a proposal that declares a string outside the registered set and outside the rendered line
   is still rejected;
5. the four conditions recomputed on a fixed rendered line agree with v16;
6. the frozen cap arithmetic: 9 samples, cap 110, worst case 78, margin at least 1.3x;
7. no frozen file changed and ``criteria_changed`` is false.

Nothing here spends a request.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_multitask_chain_v14 as chain  # noqa: E402
from experiments.runners import openai_agents_v16_live_contract as live16  # noqa: E402
from experiments.runners import openai_agents_v17_live_contract as live17  # noqa: E402
from experiments.runners import openai_agents_v17_registry as registry  # noqa: E402
from experiments.runners import run_openai_agents_api_experiment as base  # noqa: E402
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner  # noqa: E402
from experiments.runners.openai_agents_structured_final_v16 import render  # noqa: E402

OUT = ROOT / "integrations/openai_agents/V17_ZERO_API_GATE_20261010.json"
FREEZE = ROOT / "integrations/openai_agents/V17_DEV_PILOT_FREEZE_20261010.json"
DIFF = ROOT / "integrations/openai_agents/V17_HOST_CONFIGURATION_DIFF_20261010.json"
RENDERER = ROOT / "experiments/runners/openai_agents_structured_final_v16.py"


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def instruction_text_per_arm(task_id: str, arm: str) -> dict[str, Any]:
    """Run one sample through the production wiring with a local model and read its prompt."""

    case = runner.build_case(task_id, 0)
    contracts = runner.contract_table()
    budget, _ = base.resolve_budget(base.build_parser().parse_args(runner.forced_args([])))
    shared: dict[str, Any] = {}
    previous_chain_registry = chain.registry
    chain.registry = registry
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        request_budget = base.RequestBudget(64)
        with chain.chain_wiring():
            with live17.inner_model_wiring(
                lambda inner, request_budget=None, **kwargs: live17.ScriptedStubModel(
                    case,
                    request_budget=request_budget,
                    final_text_value=json.dumps(
                        {
                            "issue": case.scenario and registry.task(task_id)["contract"]["issue_value"],
                            "cause": "cause " + registry.task(task_id)["cause_token"],
                            "fix": "fix " + registry.task(task_id)["fix_token"],
                            "facts": list(
                                registry.task(task_id)["contract"]["required_facts"]
                            ),
                        }
                    ),
                )
            ):
                with v8.evidence_wiring(
                    root, 65536, recorder_factory=chain.MultitaskRecorder
                ):
                    with live17.structured_wiring(
                        root, contracts=contracts, state=shared
                    ):
                        context_filter = base._build_filter(
                            arm,
                            case=case,
                            client=runner._NoNetworkClient(),
                            model_name="stub",
                            budget=budget,
                            fixed_reserved_tokens=512,
                            max_summary_tokens=1024,
                            max_summary_calls=0,
                            timeout=5.0,
                            request_budget=request_budget,
                            trigger_policy="symmetric_budget",
                        )
                        await base.run_case(
                            case,
                            method=arm,
                            client=runner._NoNetworkClient(),
                            model_name="stub",
                            request_budget=request_budget,
                            budget=budget,
                            fixed_reserved_tokens=512,
                            max_turns=10,
                            max_output_tokens=1024,
                            max_api_retries=0,
                            retry_base_delay=0.0,
                            input_cost_per_million=0.0,
                            output_cost_per_million=0.0,
                            context_filter=context_filter,
                        )
        wrapper = shared.get("structured")
        inner = getattr(wrapper, "inner", None)
        instructions = list(getattr(inner, "instructions", []) or [])
    chain.registry = previous_chain_registry
    return {
        "instructions": instructions,
        "instructions_sha256": [sha_text(item) for item in instructions],
        "prompt_adaptations": (shared.get("prompt_adaptations") or {}),
        "task_prompt": registry.task(task_id)["contract_prompt"],
    }


def main() -> int:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # 1 -- the rule sentence, present and identical across arms ------------------------------
    sentence = "Every one of those strings must also appear verbatim inside"
    per_task = {}
    for task_id in registry.task_ids():
        prompt = registry.task(task_id)["contract_prompt"]
        per_task[task_id] = {
            "sentence_present": sentence in prompt,
            "prompt_sha256": sha_text(prompt),
        }
    record(
        "rule_sentence_present_in_every_task_prompt",
        all(entry["sentence_present"] for entry in per_task.values()),
        per_task,
    )
    arm_prompts: dict[str, dict[str, Any]] = {}
    task_id = registry.task_ids()[0]
    for arm in runner.PILOT_METHODS:
        result = asyncio.run(instruction_text_per_arm(task_id, arm))
        arm_prompts[arm] = {
            "instructions_sha256": result["instructions_sha256"],
            "instruction_count": len(result["instructions"]),
            "sentence_present": any(sentence in text for text in result["instructions"]),
            "prompt_adaptations": result["prompt_adaptations"],
        }
    hashes = {tuple(entry["instructions_sha256"]) for entry in arm_prompts.values()}
    record(
        "prompt_text_byte_identical_across_arms",
        len(hashes) == 1 and all(entry["sentence_present"] for entry in arm_prompts.values()),
        {"per_arm": arm_prompts, "distinct_hash_sets": len(hashes)},
    )

    # 2 -- the retry diagnostic is byte-identical to v16 --------------------------------------
    fixed = {
        "raw": json.dumps(
            {
                "issue": "django-13821",
                "cause": "check_sqlite_version floor",
                "fix": "require 3.9.0",
                "facts": ["check_sqlite_version", "3.8.3", "3.12.0"],
            }
        ),
        "expected_issue": "django-13821",
        "required_facts": ["check_sqlite_version", "3.8.3", "3.9.0"],
        "cause_token": "check_sqlite_version",
        "fix_token": "3.9.0",
    }
    diagnostic_pairs = {}
    for reason in (
        "over_160_chars",
        "declared_fact_missing_from_rendered_answer",
        "invalid_json_or_duplicate_key",
        "missing_or_extra_field",
        "invalid_fact_list",
        "invalid_cause_or_fix",
        "embedded_field_delimiter",
        "issue_mismatch",
        "invalid_issue",
    ):
        a = live16.diagnostic_for(reason, **fixed)
        b = live17.diagnostic_for(reason, **fixed)
        diagnostic_pairs[reason] = {
            "v16_sha256": sha_text(a),
            "v17_sha256": sha_text(b),
            "identical": a == b,
        }
    record(
        "retry_diagnostic_identical_to_v16",
        all(entry["identical"] for entry in diagnostic_pairs.values()),
        diagnostic_pairs,
    )

    # 3/4 -- the renderer's own conditions are untouched --------------------------------------
    issue = "django-13821"
    one_missing = json.dumps(
        {
            "issue": issue,
            "cause": "check_sqlite_version floor is 3.8.3",
            "fix": "require 3.9.0",
            "facts": ["check_sqlite_version", "3.8.3", "3.9.0", "__iter__"],
        }
    )
    verdict = render(one_missing, expected_issue=issue)
    record(
        "declared_literal_outside_cause_fix_still_rejected",
        (not verdict.accepted) and verdict.reason == "declared_fact_missing_from_rendered_answer",
        {"reason": verdict.reason, "missing": ["__iter__"]},
    )
    extra = json.dumps(
        {
            "issue": issue,
            "cause": "check_sqlite_version floor is 3.8.3",
            "fix": "require 3.9.0",
            "facts": ["check_sqlite_version", "3.8.3", "3.9.0", "not-registered-string"],
        }
    )
    extra_verdict = render(extra, expected_issue=issue)
    record(
        "declared_string_outside_the_line_still_rejected",
        (not extra_verdict.accepted)
        and extra_verdict.reason == "declared_fact_missing_from_rendered_answer",
        {
            "reason": extra_verdict.reason,
            "note": (
                "the renderer enforces declared-subset-of-rendered; a declared string that IS in "
                "the line is accepted, exactly as in v16, because neither version filters facts "
                "against the registered set"
            ),
        },
    )

    # 5 -- four conditions agree with v16 on a fixed line ------------------------------------
    line = "RESULT issue=django-13821 cause=check_sqlite_version floor is 3.8.3 fix=require 3.9.0"
    pattern = registry.task("django_sqlite_version_floor")["answer_pattern"]
    facts = ["check_sqlite_version", "3.8.3", "3.9.0"]
    a = live16.conditions(line, issue, pattern, facts)
    b = live17.conditions(line, issue, pattern, facts)
    record("four_conditions_identical_to_v16", a == b, {"v16": a, "v17": b})

    # 6 -- cap arithmetic --------------------------------------------------------------------
    arithmetic = runner.pilot_arithmetic(runner.pilot_draft_freeze())
    worst = arithmetic["worst_case_requests"]
    record(
        "cap_arithmetic_keeps_a_1_3x_margin",
        worst <= runner.PILOT_MAX_API_REQUESTS
        and runner.PILOT_MAX_API_REQUESTS >= 1.3 * worst
        and arithmetic["samples"] == 9,
        {
            "samples": arithmetic["samples"],
            "worst_case_requests_computed": worst,
            "frozen_worst_case": 78,
            "cap": runner.PILOT_MAX_API_REQUESTS,
            "margin": round(runner.PILOT_MAX_API_REQUESTS / worst, 2),
        },
    )

    # 7 -- nothing frozen changed ------------------------------------------------------------
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    diff = json.loads(DIFF.read_text(encoding="utf-8"))
    import re as _re

    unchanged = " ".join(freeze["host_configuration_diff_against_v16"]["unchanged"])
    match = _re.search(r"sha256 ([0-9a-f]{64})", unchanged)
    renderer_expected = match.group(1) if match else ""
    record(
        "renderer_not_forked_and_unchanged",
        sha_file(RENDERER) == renderer_expected
        and diff["renderer"]["forked"] is False
        and diff["renderer"]["sha256"] == sha_file(RENDERER),
        {"sha256": sha_file(RENDERER), "freeze_states": renderer_expected},
    )
    record(
        "no_frozen_file_changed_and_criteria_unchanged",
        diff["criteria_changed"] is False
        and int(diff["frozen_files_changed_after_freeze"]) == 0
        and diff["retry_diagnostic_text_changed"] is False,
        {
            "criteria_changed": diff["criteria_changed"],
            "frozen_files_changed_after_freeze": diff["frozen_files_changed_after_freeze"],
            "missing_literal_count_is_one_per_case": True,
        },
    )

    payload = {
        "schema": "openai_agents_v17_zero_api_gate",
        "date": "20261010",
        "freeze": {
            "path": str(FREEZE.relative_to(ROOT)).replace("\\", "/"),
            "sha256": sha_file(FREEZE),
        },
        "diff": {"path": str(DIFF.relative_to(ROOT)).replace("\\", "/"), "sha256": sha_file(DIFF)},
        "checks": checks,
        "ok": all(entry["ok"] for entry in checks),
        "api_calls": 0,
    }
    if OUT.exists():
        raise SystemExit(f"refusing to overwrite {OUT}")
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"gate artifact: {OUT} sha256={sha_file(OUT)[:16]}")
    for entry in checks:
        print(f"  {entry['check']}: ok={entry['ok']}")
    print("ok", payload["ok"])
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
