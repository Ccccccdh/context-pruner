"""CrewAI r20 order-free three-arm batch: the pinned r17 flow with NO guard installed.

Why a new file
--------------
``experiments/runners/run_crewai_handoff_v17.py`` is pinned and installs the v17 controller
guard, by default on **every** arm.  Design D for the r20 order-free family is the opposite
decision: no order guard, no repeat guard, no action-less rule -- an order-free contract asks
for the evidence SET, so a guard that refuses order and repetition would refuse turns the
contract permits.  A pinned source is never edited, so this file is the r20 entry point and
makes the removal explicit and testable instead of silently reconfiguring v17:

* ``guard_module.GuardedCrewAILLM`` is replaced by the plain provider adapter, and
* ``guard_module.create_guard_state`` is replaced by a builder that returns a disabled state,

so every code path that could install or fire a guard sees ``enabled=False``.  The runner's own
``guard_installed_arms`` and ``guard_rejections`` fields are then read back and asserted to be
empty and zero, which is the failure line the pre-registration froze.

Everything else -- the case flow, the adapter wiring, the budget handling, the accounting --
is the pinned r17 code, reached by delegation, not by copying.

Usage is fail-closed behind ``integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json``:
the batch id, the task set, the three arms, three repeats, the 390-request cap, the model, the
no-guard scope and the non-citable purpose must all match, and every pinned source must hash to
its frozen digest, or the run refuses before any request is sent.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from context_pruner import ContextPluginConfig
from experiments.runners import crewai_capture_v20 as capture
from experiments.runners import crewai_loop_guard_v17 as guard_module
from experiments.runners import crewai_semantic_equivalence_v8 as judge
from experiments.runners import run_crewai_experiment as base
from experiments.runners import run_crewai_handoff_v17 as v17

ROOT = Path(__file__).resolve().parents[2]
FREEZE = ROOT / "integrations/crewai/PRE_RUN_FREEZE_R20_ORDERFREE_3ARM_01.json"
TASK_FILE = ROOT / "tasks/stage5_autogen/natural_tasks_r20_orderfree.json"
TASK_IDS = (
    "ledger_lock_attestation",
    "telemetry_export_attestation",
    "replica_resync_attestation",
    "policy_signoff_attestation",
)
ARM_ORDER = ("none", "pruner_v1", "native_summary")
REPEATS = 3
MAX_API_REQUESTS = 390
BATCH = "crewai-r20-orderfree-3arm-01"
MODEL = "deepseek-v4-flash"
MAX_OUTPUT_TOKENS = 512
MAX_SUMMARY_TOKENS = 1024
MAX_SUMMARY_CALLS = 4
GUARD_INSTALLED_ARMS: tuple[str, ...] = ()
NO_GUARD_SCOPE = "none"
CACHE_FIELDS = (
    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_tokens_source",
    "utc_started", "utc_finished", "off_peak_window",
)

REUSED_FROM_V17 = {
    "source_file": "experiments/runners/run_crewai_handoff_v17.py",
    "source_sha256": "3a93a70b9604624862449a6f6f22354dfa5482419ac22ef681a29ae72175d1d7",
    "frozen_by": [
        "integrations/crewai/PRE_RUN_FREEZE_R17_CONFIRMATION_3ARM_01.json",
        "integrations/crewai/PRE_RUN_FREEZE_R17_GUARDED_3ARM_01.json",
    ],
    "reused_changes": [
        "guard_module.GuardedCrewAILLM is replaced by the plain provider adapter",
        "guard_module.create_guard_state is replaced by a disabled-state builder",
        "the task file, task ids, protocol, batch id, purpose, repeats and cap are the r20 ones",
        "the LLM factory adds the r19/r20 per-attempt capture and the summary meter",
        "everything else (case flow, adapter wiring, extra accounting) runs as the pinned code",
    ],
    "why_the_guard_removal_is_visible": (
        "both replacements are named here and asserted after the run from the recorded rows; "
        "a batch that silently kept the guard would show non-empty guard_installed_arms or a "
        "non-zero guard_rejections and would fail its own audit"
    ),
}


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


#: Captured before the replacement below, so the disabled-state builder never calls itself.
_ORIGINAL_GUARD_STATE_BUILDER = guard_module.create_guard_state
#: Captured before this module replaces them, so the wrappers never call themselves.
_ORIGINAL_MAKE_LLM = v17._make_llm
_ORIGINAL_RUN_CASE = v17.run_case

#: Every request to install an *enabled* guard, recorded instead of honoured.  The failure line
#: reports this list, so "no guard was installed" is an observation and not an assumption.
SUPPRESSED_GUARD_REQUESTS: list[dict[str, Any]] = []


def _disabled_guard_state(required_tools: Any = (), *, enabled: bool = False,
                          max_rejections: int = 0) -> Any:
    """A guard state that can never fire; a request to *enable* one is recorded, not honoured.

    ``_ORIGINAL_GUARD_STATE_BUILDER`` is the builder captured before this module replaced the
    attribute: calling through the module attribute would recurse into this function.

    The pinned r17 flow asks for an *enabled* guard on every arm (``--guard-scope all_arms``
    is its default and its design).  Design D asks for the opposite, so those requests are
    suppressed and each one is appended to ``SUPPRESSED_GUARD_REQUESTS``.  Suppression alone
    would be indistinguishable from a guard that quietly failed to install, so the count is
    written into the manifest and the audit asserts the stronger, observable facts instead:
    every row records ``guard_enabled=false``, zero rejections, zero rejections records, no
    guard exhaustion, and no control text in any prepared message.
    """
    if enabled:
        SUPPRESSED_GUARD_REQUESTS.append(
            {
                "required_tools": [str(name) for name in required_tools],
                "max_rejections": int(max_rejections),
            }
        )
    return _ORIGINAL_GUARD_STATE_BUILDER((), enabled=False)


def check_freeze(freeze_path: Path) -> dict:
    """The r20 three-arm enforcer: fail-closed, before a single request is sent."""
    if not freeze_path.is_file():
        raise SystemExit(f"missing write-once freeze: {freeze_path}")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if freeze.get("purpose") != "three_arm_pilot":
        raise SystemExit("freeze purpose mismatch")
    # A rehearsal freeze names the mock batch so that the mock can validate this enforcer
    # without touching the frozen name; an API run may never use one.
    expected_batch = freeze.get("batch")
    if expected_batch not in (BATCH, f"{BATCH.rsplit('-0', 1)[0]}-mock-01"):
        raise SystemExit(f"freeze batch mismatch: {expected_batch}")
    if int(freeze.get("max_api_requests", 0)) != MAX_API_REQUESTS:
        raise SystemExit("request hard cap mismatch against the freeze")
    if tuple(freeze.get("tasks") or ()) != TASK_IDS:
        raise SystemExit("freeze task set mismatch")
    if list(freeze.get("methods") or []) != list(ARM_ORDER):
        raise SystemExit("freeze arm mismatch")
    if int(freeze.get("repeats", 0)) != REPEATS:
        raise SystemExit("freeze repeat count mismatch")
    if freeze.get("citable_as_saving") is not False or freeze.get(
        "citable_as_quality_equivalence"
    ) is not False:
        raise SystemExit("a pilot must not be citable")
    guard = freeze.get("guard_configuration") or {}
    if guard.get("guard_mode") != NO_GUARD_SCOPE or list(
        guard.get("guard_installed_arms") or []
    ) != list(GUARD_INSTALLED_ARMS):
        raise SystemExit("freeze guard scope mismatch: this batch installs no guard")
    if guard.get("actionless_rule") != "not_installed":
        raise SystemExit(
            "the frozen action-less rule is not_installed; a batch that wants it enabled "
            "needs its own freeze and its own entry point"
        )
    if freeze.get("model") != MODEL or int(freeze.get("max_output_tokens", 0)) != MAX_OUTPUT_TOKENS:
        raise SystemExit("model or output cap differs from the freeze")
    for relative, expected in (freeze.get("source_sha256") or {}).items():
        if not expected:
            raise SystemExit(f"freeze pin has no digest: {relative}")
        path = ROOT / relative
        if not path.is_file() or sha(path) != expected:
            raise SystemExit(f"unregistered source drift: {relative}")
    if sha(freeze_path) != sha(freeze_path):  # pragma: no cover - keeps the read honest
        raise SystemExit("freeze changed while being read")
    return freeze


def _wrap_run_case(clients: list[Any], summary_records: list[dict[str, Any]]) -> Any:
    inner = _ORIGINAL_RUN_CASE

    def run_case(task: dict[str, Any], method: str, repeat: int, mode: str, client: Any,
                 request_budget: Any, args: argparse.Namespace, budget: Any,
                 **kwargs: Any) -> dict[str, Any]:
        # ``v17.main`` assigns its module globals at the top of its own body, so the r20
        # replacements are re-applied on every call: that way the pinned code cannot undo them
        # after the moment this command injected them.
        v17.load_tasks = lambda path=None: judge.load_tasks(TASK_FILE)
        kwargs.pop("guard_state", None)  # no guard on any arm, so the state is not built
        before_clients = len(clients)
        before_summary = len(summary_records)
        row = inner(task, method, repeat, mode, client, request_budget, args, budget, **kwargs)
        stage_llms = clients[before_clients:]
        for llm in stage_llms:
            binder = getattr(llm, "bind_executed_tools", None)
            if binder is not None:
                binder()
        attempts = [record for llm in stage_llms for record in getattr(llm, "capture", [])]
        summaries = summary_records[before_summary:]
        row["full_attempt_capture"] = attempts
        row["native_summary_capture"] = summaries
        row["summary_recorded_calls"] = len(summaries)
        row["capture_complete"] = len(attempts) == int(row.get("api_request_attempts", 0) or 0)
        for field in CACHE_FIELDS:
            if field in ("utc_started", "utc_finished"):
                stamps = [str(entry.get(field) or "") for entry in attempts]
                row[f"agent_{field}_first"] = stamps[0] if stamps else ""
                row[f"agent_{field}_last"] = stamps[-1] if stamps else ""
            elif field == "off_peak_window":
                tiers = [entry.get(field) for entry in attempts]
                row["agent_attempts_off_peak"] = sum(1 for tier in tiers if tier is True)
                row["agent_attempts_timestamped"] = sum(1 for tier in tiers if tier is not None)
            else:
                row[f"agent_{field}"] = sum(
                    int(entry.get(field, 0) or 0) for entry in attempts
                    if isinstance(entry.get(field), int)
                )
        row["agent_input_tokens_from_capture"] = sum(
            int(entry.get("input_tokens", 0) or 0) for entry in attempts
        )
        row["agent_output_tokens_from_capture"] = sum(
            int(entry.get("output_tokens", 0) or 0) for entry in attempts
        )
        row["summary_input_tokens_from_capture"] = sum(
            int(entry.get("input_tokens", 0) or 0) for entry in summaries
        )
        row["summary_output_tokens_from_capture"] = sum(
            int(entry.get("output_tokens", 0) or 0) for entry in summaries
        )
        row["guard_installed"] = False
        row["guard_scope"] = NO_GUARD_SCOPE
        return row

    return run_case


def _make_llm_factory(clients: list[Any], summary_records: list[dict[str, Any]]) -> Any:
    def make_llm(method: str, mode: str, task: dict[str, Any], stage: int, client: Any,
                 request_budget: Any, args: argparse.Namespace, budget: Any,
                 trace: list[str], **kwargs: Any) -> Any:
        if mode == "mock":
            return _ORIGINAL_MAKE_LLM(method, mode, task, stage, client, request_budget, args,
                                     budget, trace, **kwargs)
        frozen_tools = [str(tool["name"]) for tool in task["tools"]] if stage == 0 else [
            str(task["tools"][-1]["name"])
        ]
        if method == "native_summary":
            llm = capture.CapturedNativeSummaryCrewAILLM(
                client=client, model=args.model, request_budget=request_budget,
                max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
                summary_client_factory=lambda: v17.AsyncOpenAI(
                    api_key=(base.os.getenv("OPENAI_API_KEY")
                             or base.os.getenv("DEEPSEEK_API_KEY") or ""),
                    base_url=args.base_url, timeout=60.0, max_retries=0,
                ),
                summary_model=args.model, soft_limit_tokens=budget.soft_limit_tokens,
                hard_limit_tokens=budget.hard_limit_tokens,
                target_tokens=budget.target_tokens or 1,
                fixed_reserved_tokens=args.fixed_reserved_tokens,
                max_summary_tokens=args.max_summary_tokens,
                max_summary_calls=args.max_summary_calls,
                executed_tool_trace=trace, frozen_tool_names=frozen_tools,
                summary_records=summary_records,
            )
            llm.attach_summary_meter()
        else:
            llm = capture.CapturedOpenAICompatCrewAILLM(
                client=client, model=args.model, request_budget=request_budget,
                max_output_tokens=args.max_output_tokens, thinking_mode="disabled",
                executed_tool_trace=trace, frozen_tool_names=frozen_tools,
            )
        clients.append(llm)
        return llm

    return make_llm


def augment_manifest(output: Path, freeze: dict, args: argparse.Namespace,
                     freeze_path: Path | None = None) -> None:
    """Record the r20 fields the frozen acceptance line needs, without rewriting history."""
    freeze_path = Path(freeze_path) if freeze_path is not None else FREEZE
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update({
        "protocol": "crewai-two-role-r20-orderfree",
        "batch": BATCH,
        "purpose": "three_arm_pilot",
        "purpose_limit": freeze.get("purpose_note"),
        "freeze_path": str(freeze_path).replace("\\", "/"),
        "freeze_sha256": sha(freeze_path),
        "freeze_declared_self_sha256": freeze.get("freeze_sha256"),
        "guard_scope": NO_GUARD_SCOPE,
        "guard_installed_arms": list(GUARD_INSTALLED_ARMS),
        "guard_configuration": freeze.get("guard_configuration"),
        "enabled_guard_requests_suppressed": len(SUPPRESSED_GUARD_REQUESTS),
        "enabled_guard_requests_suppressed_note": (
            "the pinned r17 flow asks for an enabled guard on every arm; design D installs "
            "none, so those requests were answered with a disabled state. The observable "
            "facts -- guard_enabled, guard_rejections, guard_exhausted and the absence of any "
            "control text in a prepared message -- are asserted per row by the audit."
        ),
        "criteria_ids": freeze.get("acceptance", {}).get("report_only") and [
            "CRIT_R20_COVERAGE", "CRIT_R20_REPEAT_REPORT", "CRIT_R20_SAME_WORK_REPORT",
        ],
        "quality_gate": freeze.get("acceptance", {}).get("quality_gate"),
        "cost_gate": freeze.get("acceptance", {}).get("cost_gate"),
        "failure_line": freeze.get("failure_line"),
        "cache_fields": list(CACHE_FIELDS),
        "cache_rule": "cache changes dollars only, never token counts",
        "request_cap_arithmetic": freeze.get("request_arithmetic"),
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "attribution": freeze.get("attribution"),
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")


def assert_no_guard_activity(output: Path, rows: list[dict[str, Any]]) -> list[str]:
    """The frozen failure line, checked from the recorded rows."""
    problems: list[str] = []
    for row in rows:
        where = f"{row.get('task_id')}/{row.get('repeat')}/{row.get('method')}"
        if bool(row.get("guard_enabled")):
            problems.append(f"guard installed on {where}")
        if int(row.get("guard_rejections", 0) or 0) != 0:
            problems.append(f"guard rejections on {where}")
        if bool(row.get("guard_exhausted")):
            problems.append(f"guard_exhausted on {where}")
        if row.get("guard_rejection_records"):
            problems.append(f"guard rejection records on {where}")
        if row.get("guard_installed") is not False:
            problems.append(f"row is not marked guardless on {where}")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if list(manifest.get("guard_installed_arms") or []):
        problems.append("manifest lists installed guard arms")
    return problems


def load_rows(output: Path) -> list[dict[str, Any]]:
    text = (output / "results.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "api"), default="mock")
    parser.add_argument("--freeze", default=str(FREEZE))
    parser.add_argument("--out", default=str(ROOT / "runs/stage5-crewai"))
    parser.add_argument("--experiment-id", default=BATCH)
    parser.add_argument("--confirm-send-synthetic-data", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--plan", action="store_true")
    args, passthrough = parser.parse_known_args(argv)
    freeze = check_freeze(Path(args.freeze))
    output = Path(args.out) / args.experiment_id
    if args.mode == "api" and not args.experiment_id == BATCH:
        raise SystemExit("API experiment ID must match the frozen batch ID")
    if args.resume and not (output / "results.jsonl").is_file():
        raise SystemExit("--resume needs an existing partial batch")
    if args.plan:
        plan = [
            (task_id, repeat, method)
            for task_id in TASK_IDS for repeat in range(REPEATS) for method in ARM_ORDER
        ]
        print(f"r20 order-free three-arm plan: {len(plan)} samples, {len(TASK_IDS)} tasks, "
              f"{REPEATS} repeats, arms {list(ARM_ORDER)}, cap {freeze['max_api_requests']}")
        print("guard: none installed; cache fields recorded per attempt: "
              + ", ".join(CACHE_FIELDS))
        print("No API request was sent in --plan mode.")
        return
    if args.mode == "api" and not args.confirm_send_synthetic_data:
        raise SystemExit("API mode requires --confirm-send-synthetic-data")
    if args.mode == "api" and not (
        base.os.getenv("OPENAI_API_KEY") or base.os.getenv("DEEPSEEK_API_KEY")
    ):
        raise SystemExit("API key absent")

    clients: list[Any] = []
    summary_records: list[dict[str, Any]] = []
    # No guard, on any arm: both the class and the state builder are replaced, so a path that
    # wanted to install the controller has nothing to install.
    guard_module.GuardedCrewAILLM = base.OpenAICompatCrewAILLM
    guard_module.create_guard_state = _disabled_guard_state

    v17.TASK_FILE = TASK_FILE
    v17.load_tasks = lambda path=None: judge.load_tasks(TASK_FILE)
    v17.TASK_IDS = TASK_IDS
    v17.PROTOCOL = "crewai-two-role-r20-orderfree"
    v17.DEFAULT_PURPOSE = "three_arm_pilot"
    v17.GUARD_ALL_ARMS = False
    v17._make_llm = _make_llm_factory(clients, summary_records)
    v17.run_case = _wrap_run_case(clients, summary_records)
    # The pinned runner reads its own default freeze path; point it at the freeze this command
    # already validated, so the run cannot record a different freeze than the one it enforced.
    v17.FREEZE = str(Path(args.freeze).resolve())

    forward = [
        "--mode", args.mode,
        "--freeze", str(Path(args.freeze).resolve()),
        "--task-ids", ",".join(TASK_IDS),
        "--methods", ",".join(ARM_ORDER),
        "--repeats", str(REPEATS),
        "--max-api-requests", str(MAX_API_REQUESTS),
        "--model", MODEL,
        "--max-output-tokens", str(MAX_OUTPUT_TOKENS),
        "--max-summary-tokens", str(MAX_SUMMARY_TOKENS),
        "--max-summary-calls", str(MAX_SUMMARY_CALLS),
        # The pinned flow computes "should a guard be enabled here" as
        # ``is_first and (guard_all_arms or method == "pruner_v1")``.  With scope
        # ``all_arms`` the first factor is False, so the flow itself asks for no enabled guard
        # on any arm -- which is what design D freezes.  With ``pruner_only`` the plugin arm
        # would still ask for one and this command would have to suppress it, and the failure
        # line reports a suppression, so the two scopes are not equivalent: this one leaves
        # nothing to suppress.
        "--guard-scope", "all_arms",
        "--out", args.out,
        "--experiment-id", args.experiment_id,
        "--purpose", "three_arm_pilot",
    ]
    forward += passthrough
    if args.confirm_send_synthetic_data:
        forward.append("--confirm-send-synthetic-data")
    if args.resume:
        forward.append("--resume")

    v17.main(forward)

    rows = load_rows(output)
    augment_manifest(output, freeze, args, Path(args.freeze))
    problems = assert_no_guard_activity(output, rows)
    if problems:
        print("FAILURE LINE TRIGGERED (no guard must be active on any arm):")
        for problem in problems:
            print("  -", problem)
        print("these numbers must not be treated as an effective saving")
        raise SystemExit(2)
    print(f"no-guard assertion holds across {len(rows)} recorded rows; enabled-guard requests "
          f"from the pinned flow suppressed: {len(SUPPRESSED_GUARD_REQUESTS)}")
    print("note: the pinned runner's own console line above mentions the guard and an exact "
          "tool order; both are r17 text on a reused code path. The frozen r20 acceptance line "
          "is the one in manifest.json (quality: r8 judge + CRIT_R20_COVERAGE; cost: complete "
          "provider tokens), and repeats/order/same-work are report items only.")


if __name__ == "__main__":
    main()
