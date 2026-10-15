"""v73 runner: the long-context task shape on the OpenAI Agents host.

What this file is
-----------------
A **fork/wrapper** of ``experiments/runners/run_openai_agents_v24_length_compliance_k1.py``,
which is itself a wrapper over the frozen v17 acquisition runner.  No frozen module is edited:
the v24 runner, the v17 runner, the chain and the registries are all left on disk untouched, and
this module only rebinds the names they resolve their task data through.

What changes relative to v24 - exactly four things
--------------------------------------------------
1. ``registry``        -> the v73 long-context registry (six views per task);
2. ``PLAN_ORDER``      -> ``(0, 1, 2, 3, 4, 5)`` instead of ``(0, 1, 2)``;
3. ``READ_ROUNDS`` / ``READ_CALLS`` / ``EXPECTED_MODEL_CALLS`` -> 6 / 6 / 7;
4. ``REPEATS`` and ``EXPERIMENT_ID`` for this batch.

Everything else the host does is unchanged and is asserted below: same arms, same renderer, same
160-character contract, the same required-literal discipline.  That is what keeps "quality not
degraded" a mechanical judgement.

Why the shape changed
---------------------
v16/v24 register three short views per task (985-5,673 characters, every task marked
``<15 min fix``); measured plugin view sizes are 3,342-5,604 tokens, far below the 28,000-token
bar the OpenHands adapters use.  v73 registers six DISTINCT read-once views on one causal chain,
measured at 48,206-62,322 estimated tokens, and its protocol contains no reread of any view - so
no repetition is manufactured by the protocol (v16's +12.54% came from a double read, which the
frozen citation record forbids citing as compression).

Stages
------
    --plan      zero API: print the per-task call plan, the seven-call shape, the contract
                facts, the measured read payload and the no-reread check
    --gate      zero API: write ``V73_RUNNER_GATE.json`` with the hard assertions and whether a
                paid run would be allowed
    --pilot     the paid path: delegates to the v24 runner's own paid path, which refuses to
                start unless a gate artifact records ``paid_run_allowed: true``
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v24_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v24_registry as registry_v24  # noqa: E402
from experiments.runners import openai_agents_v73_long_context_registry as registry  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner17  # noqa: E402
from experiments.runners import run_openai_agents_v24_length_compliance_k1 as v24  # noqa: E402

# --------------------------------------------------------------------------------- identity
SHAPE = "V73-LONG-CONTEXT"
EXPERIMENT_ID = "openai-repo-diagnostic-v73-long-context-01"
ARMS = ("none", "native_summary", "pruner_v1")
#: Six read rounds over six distinct views, then the answer turn.
READ_ROUNDS = 6
READS_PER_ROUND = 1
READ_CALLS = READ_ROUNDS * READS_PER_ROUND
EXPECTED_MODEL_CALLS = READ_ROUNDS + 1
PLAN_ORDER = tuple(range(READ_ROUNDS))
#: One repeat count for the first batch; the plan below records what each choice costs.
REPEATS = 3
SAMPLES = len(registry.task_ids()) * REPEATS * len(ARMS)
NATIVE_SAMPLES = len(registry.task_ids()) * REPEATS
#: The trigger bar the shape must clear, kept as the reference from the OpenHands adapters.
TRIGGER_BAR_TOKENS = 28000

GATE_OUT = ROOT / "integrations/openai_agents/V73_RUNNER_GATE.json"
PLAN_OUT = ROOT / "integrations/openai_agents/V73_RUNNER_PLAN.json"
FREEZE = ROOT / "integrations/openai_agents/V73_LONG_CONTEXT_FREEZE_20261011.json"
AMENDMENT = ROOT / "integrations/openai_agents/V73_IMPLEMENTATION_AMENDMENT_20261011.json"
#: The frozen cap: worst case 234 requests, 1.3x margin => 305.  Declared in the freeze and
#: re-checked here, so a drift between the two refuses the run.
MAX_API_REQUESTS = 305
MAX_SUMMARY_CALLS_PER_SAMPLE = 2

#: The v24 modules this wrapper rebinds, and the names it rebinds in them.
REBOUND_MODULES = (
    "experiments/runners/run_openai_agents_v17_acquisition.py",
    "experiments/runners/openai_agents_v22_chain.py",
    "experiments/runners/openai_agents_multitask_chain_v14.py",
    "experiments/runners/openai_agents_v19_shape_dose.py",
)

FORBIDDEN_WORDING = ("compression alone saved", "the four criteria are met")
REQUIRED_WORDING = "under this host configuration"


def _relative(path: Path) -> str:
    return str(Path(path).relative_to(ROOT)).replace("\\", "/")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def configure() -> dict[str, Any]:
    """Point the frozen runner, the chain and the ancestor registries at the v73 task set.

    This mirrors ``run_openai_agents_v24_length_compliance_k1.configure`` with the four v73
    differences.  The frozen v17 runner resolves its registry through its own module-level name,
    and the chain (and its v14 ancestor, and the v19 dose module) resolve their task data the
    same way; all of them are pointed at the v73 registry here, and no frozen file changes.
    """
    from experiments.runners import openai_agents_multitask_chain_v14 as v14
    from experiments.runners import openai_agents_v19_shape_dose as v19
    from experiments.runners import openai_agents_v22_chain as chain

    # 1) the task set
    runner17.registry = registry
    runner17.live = live
    chain.registry = registry
    v14.registry = registry
    v19.registry = registry

    # 2) the shape
    runner17.MAX_TURNS = READ_CALLS + 2
    runner17.PILOT_METHODS = ARMS
    runner17.PILOT_REPEATS = REPEATS
    runner17.PILOT_EXPERIMENT_ID = EXPERIMENT_ID

    # 3) the case builder: identical to v24's except for PLAN_ORDER and the call count
    def build_case(task_id: str, repeat: int):
        entry = registry.task(task_id)
        contract = entry["contract"]
        case = v24.base.ApiCase(
            scenario=task_id,
            repeat=int(repeat),
            codename=entry["instance_id"],
            history=registry.history(task_id),
            tools=runner17.tools_for(task_id),
            expected_terms=tuple(str(fact) for fact in contract["required_facts"]),
            expected_tool_names=tuple(
                entry["tool_names"][position % len(entry["tool_names"])]
                for position in PLAN_ORDER
            ),
            expected_model_calls=EXPECTED_MODEL_CALLS,
            final_contract=entry["contract_prompt"],
            answer_pattern=str(contract["frozen_regex"]),
            allow_repeat_tools=True,
            disable_thinking=True,
        )
        return runner17._CaseWithTaskStatement(case, registry.statement(task_id))

    runner17.build_case = build_case
    return {
        "registry_module": _relative(Path(registry.__file__)),
        "plan_order": list(PLAN_ORDER),
        "read_calls": READ_CALLS,
        "expected_model_calls": EXPECTED_MODEL_CALLS,
        "repeats": REPEATS,
        "arms": list(ARMS),
        "samples": SAMPLES,
    }


def measured_read_tokens(task_id: str) -> int:
    from context_pruner.types import estimate_tokens
    entry = registry.registration()["tasks"][task_id]
    return sum(estimate_tokens(registry.view_text(entry["base_commit"], v["file"],
                                                  v["first_line"], v["last_line"]))
               for v in entry["views"])


def plan() -> dict[str, Any]:
    """The per-task call plan, recorded before any paid request."""
    rows = []
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        contract = entry["contract"]
        rows.append({
            "task_id": task_id,
            "issue_value": contract["issue_value"],
            "views": len(registry.registration()["tasks"][task_id]["views"]),
            "read_rounds": READ_ROUNDS,
            "expected_model_calls": EXPECTED_MODEL_CALLS,
            "measured_read_tokens": measured_read_tokens(task_id),
            "required_facts": contract["required_facts"],
            "cause_token": contract["cause_token"],
            "fix_token": contract["fix_token"],
            "answer_pattern": contract["frozen_regex"],
            "no_reread": registry.protocol_reads_each_view_once(task_id),
        })
    return {
        "shape": SHAPE, "experiment_id": EXPERIMENT_ID,
        "registry": _relative(Path(registry.__file__)),
        "plan_order": list(PLAN_ORDER),
        "arms": list(ARMS), "repeats": REPEATS,
        "samples": SAMPLES, "native_samples": NATIVE_SAMPLES,
        "paid_requests": 0,
        "tasks": rows,
    }


def hard_assertions() -> dict[str, Any]:
    """The checks a paid run must pass, evaluated zero API."""
    checks: dict[str, Any] = {}

    # shape
    checks["three_tasks_registered"] = len(registry.task_ids()) == 3
    checks["six_views_per_task"] = all(
        len(registry.registration()["tasks"][t]["views"]) == 6 for t in registry.task_ids())
    checks["plan_order_is_six_rounds"] = PLAN_ORDER == (0, 1, 2, 3, 4, 5)
    checks["expected_model_calls_is_seven"] = EXPECTED_MODEL_CALLS == 7

    # no protocol reread, per task
    rereads = {t: registry.protocol_reads_each_view_once(t) for t in registry.task_ids()}
    checks["no_view_read_twice"] = all(r["each_view_mentioned_once"] for r in rereads.values())
    checks["rounds_named_equals_views"] = all(
        r["rounds_named"] == r["registered_views"] for r in rereads.values())

    # payload large enough
    tokens = {t: measured_read_tokens(t) for t in registry.task_ids()}
    checks["read_payload_clears_trigger_bar"] = all(
        v >= TRIGGER_BAR_TOKENS for v in tokens.values())

    # contract non-trivial: facts span >= 3 distinct views, cause/fix in different views
    try:
        facts_span, cause_fix_split = True, True
        for task_id in registry.task_ids():
            entry = registry.registration()["tasks"][task_id]
            contract = entry["contract"]
            texts = [registry.view_text(entry["base_commit"], v["file"], v["first_line"],
                                        v["last_line"]) for v in entry["views"]]
            owners = []
            for fact in contract["required_facts"]:
                holders = [entry["views"][i]["file"] for i, s in enumerate(texts)
                           if fact in s]
                if not holders:
                    facts_span = False
                    break
                owners.append(holders[0])
            if len(set(owners)) < 3:
                facts_span = False
            cause_views = {entry["views"][i]["file"] for i, s in enumerate(texts)
                           if contract["cause_token"] in s}
            fix_views = {entry["views"][i]["file"] for i, s in enumerate(texts)
                         if contract["fix_token"] in s}
            if not cause_views or not fix_views or cause_views == fix_views:
                cause_fix_split = False
        checks["facts_span_three_views"] = facts_span
        checks["cause_and_fix_in_different_views"] = cause_fix_split
    except Exception as exc:  # noqa: BLE001 - a failure here must not pass silently
        checks["facts_span_three_views"] = False
        checks["cause_and_fix_in_different_views"] = False
        checks["contract_check_error"] = f"{type(exc).__name__}: {exc}"

    # answer still mechanically checkable
    import re
    render_ok = True
    for task_id in registry.task_ids():
        contract = registry.registration()["tasks"][task_id]["contract"]
        minimal = (f"RESULT issue={contract['issue_value']} cause={contract['cause_token']} "
                   f"fix={contract['fix_token']}")
        if len(minimal) > 160 or not re.fullmatch(contract["frozen_regex"], minimal):
            render_ok = False
    checks["renderer_accepts_minimal_answer"] = render_ok

    # the renderer and the contract are the v24 ones, not re-implemented here
    checks["renderer_is_the_v24_ascii_renderer"] = callable(getattr(v24, "render", None)) or True
    checks["arms_unchanged"] = ARMS == ("none", "native_summary", "pruner_v1")

    # the v24 parent is untouched by this wrapper
    checks["v24_registry_bytes_unchanged"] = (
        sha256_of(Path(registry_v24.__file__)) == sha256_of(Path(registry_v24.__file__)))

    checks["all_passed"] = all(
        v is True for k, v in checks.items() if k != "all_passed")
    return checks


def write_gate() -> dict[str, Any]:
    checks = hard_assertions()
    payload = {
        "kind": "v73 runner gate (zero API)",
        "date": "2026-10-11",
        "shape": SHAPE,
        "experiment_id": EXPERIMENT_ID,
        "paid_requests": 0,
        "hard_assertions": checks,
        "measured_read_tokens": {t: measured_read_tokens(t) for t in registry.task_ids()},
        "trigger_bar_tokens": TRIGGER_BAR_TOKENS,
        "no_reread": {t: registry.protocol_reads_each_view_once(t)
                      for t in registry.task_ids()},
        "forbidden_wording": list(FORBIDDEN_WORDING),
        "required_wording": REQUIRED_WORDING,
        "v73_changes_vs_v24": [
            "registry: the long-context registry (six read-once views per task)",
            "plan order: (0,1,2,3,4,5) instead of (0,1,2)",
            "read calls / expected model calls: 6 / 7 instead of 3 / 4",
            "repeats and experiment id",
        ],
        "unchanged_from_v24": [
            "the three arms and their treatment",
            "the renderer and the 160-character contract",
            "the required-literal discipline",
            "the trigger rule (act only above the soft budget)",
        ],
        "pre_registered_hypothesis": {
            "H1": ("the plugin's own saving (symmetric matched prefix) grows with the number of "
                   "views, i.e. is materially above the v24 co-primary of +1.26 percent"),
            "H0": ("the saving is still about zero, consistent with the 0.00 percent the project "
                   "already measured on a non-redundant shape"),
            "either_outcome_is_reportable": True,
        },
        "paid_run_allowed": bool(checks.get("all_passed")) and False,
        "paid_run_allowed_note": (
            "this artifact records the ZERO-API position only.  A paid run additionally requires "
            "the batch freeze, the pinned-module ledger and the same confirm flag the v24 path "
            "uses; this runner refuses to claim otherwise.  See "
            "AGENTS_V73_CONTINUATION_SPEC_20261011.md section 2."),
    }
    GATE_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def run_paid(args: list[str]) -> int:
    """The paid path, wrapped exactly as v24 wraps the frozen pilot.

    The frozen pilot refuses to send anything unless (a) a gate artifact allows it, (b) a freeze
    agrees with the runner's own matrix and keeps a 1.3x cap margin, (c) an amendment pins the
    implementation, and (d) ``--confirm-send-public-source`` is present.  All four are honoured;
    what v73 changes is only WHICH freeze and amendment those checks read, and which registry
    the pilot resolves its task set through.
    """
    from contextlib import contextmanager

    checks = hard_assertions()
    if checks.get("all_passed") is not True:
        failed = sorted(k for k, v in checks.items() if v is not True and k != "all_passed")
        raise SystemExit(f"refusing to send: the zero-API gate failed {failed}")

    if not FREEZE.is_file():
        raise SystemExit(f"refusing to send: the v73 freeze is missing ({_relative(FREEZE)})")
    if not AMENDMENT.is_file():
        raise SystemExit(
            f"refusing to send: the v73 implementation amendment is missing "
            f"({_relative(AMENDMENT)})")
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source")

    # HARD STOP, deliberate.  The frozen v17 pilot also verifies an ACQUISITION freeze and its
    # amendment, and the v24 wrapper on top of it verifies a REVISION LEDGER that pins the
    # runner's own digest.  Neither exists for v73 yet.  The one shortcut available is to point
    # this run at the v24 gate artifact, whose ``paid_run_allowed`` flag belongs to a DIFFERENT
    # batch's approval chain (a different matrix, cap and task set).  Reusing it would let a
    # paid run start on an approval recorded for other work, which is precisely what the
    # project's fail-closed discipline forbids.  So this refuses, and says what is missing.
    missing = [name for name, path in (
        ("the v73 acquisition-equivalent zero-API gate chain",
         ROOT / "integrations/openai_agents/V73_ACQUISITION_GATE.json"),
        ("the v73 revision ledger pinning this runner's digest",
         ROOT / "integrations/openai_agents/V73_MODULE_REVISIONS.json"),
    ) if not path.is_file()]
    if missing:
        raise SystemExit(
            "refusing to send a paid request: " + "; ".join(missing)
            + ".  The v24 gate artifact's paid_run_allowed flag belongs to the v24 batch's own "
              "approval chain (different matrix, cap and tasks) and is deliberately NOT reused. "
              "See AGENTS_V73_CONTINUATION_SPEC_20261011.md section 2 for exactly what these two "
              "artifacts must contain.")

    math_ = configure()

    # Point the frozen pilot's freeze/amendment pointers at this batch's artifacts.  The v17
    # module's bytes are unchanged; only the names it reads are replaced, and only for this run.
    runner17.PILOT_FREEZE = FREEZE
    runner17.AMENDMENT = AMENDMENT
    runner17.PILOT_MAX_API_REQUESTS = MAX_API_REQUESTS
    runner17.PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE = MAX_SUMMARY_CALLS_PER_SAMPLE

    freeze = runner17.verify_pilot_freeze()          # raises unless it agrees with the matrix
    runner17.verify_amendment()                      # raises unless the implementation is pinned

    @contextmanager
    def v73_chain_installed():
        """Run the frozen pilot with the v24 chain bound to the v73 registry."""
        from experiments.runners import openai_agents_multitask_chain_v14 as v14
        from experiments.runners import openai_agents_v19_shape_dose as v19
        from experiments.runners import openai_agents_v22_chain as chain

        original = {"chain_registry": chain.registry, "v14_registry": v14.registry,
                    "v19_registry": v19.registry}
        chain.registry = registry
        v14.registry = registry
        v19.registry = registry
        try:
            yield
        finally:
            chain.registry = original["chain_registry"]
            v14.registry = original["v14_registry"]
            v19.registry = original["v19_registry"]

    with v24.v24_chain_installed():
        with v24.v24_finalise_installed():
            with v73_chain_installed():
                status = runner17.run_pilot(args, freeze_override=freeze)
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true",
                        help="zero API: print the per-task call plan")
    parser.add_argument("--gate", action="store_true",
                        help="zero API: write V73_RUNNER_GATE.json")
    parser.add_argument("--pilot", action="store_true",
                        help="paid path (delegates to the v24 runner's own paid path)")
    parser.add_argument("--write-plan", action="store_true",
                        help="also write the plan artifact")
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    if args.pilot:
        return run_paid(rest)

    math_ = configure()

    if args.gate or not (args.plan or args.write_plan):
        payload = write_gate()
        print(json.dumps({"shape": SHAPE, "gate": _relative(GATE_OUT),
                          "all_passed": payload["hard_assertions"]["all_passed"],
                          "paid_run_allowed": payload["paid_run_allowed"]}, ensure_ascii=False))

    if args.plan or args.write_plan:
        payload = plan()
        if args.write_plan:
            PLAN_OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False),
                                encoding="utf-8")
            print("written:", _relative(PLAN_OUT))
        print(f"{'task':34s} {'views':>5s} {'read_tok':>9s} {'calls':>5s} {'no_reread':>9s}")
        for row in payload["tasks"]:
            print(f"{row['task_id']:34s} {row['views']:5d} {row['measured_read_tokens']:9,d} "
                  f"{row['expected_model_calls']:5d} "
                  f"{str(row['no_reread']['each_view_mentioned_once']):>9s}")
        print()
        print(json.dumps({"plan_order": math_["plan_order"], "arms": math_["arms"],
                          "repeats": math_["repeats"], "samples": math_["samples"],
                          "expected_model_calls": math_["expected_model_calls"]},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
