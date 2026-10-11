"""v24 batch: the same K=1 mechanism on the same three sealed tasks, with a length-compliant prompt.

What this round is
------------------
The v22 dose round measured the literal-carrier line elision on the host's *development* tasks and
calibrated the protection window ``K`` on them.  This round runs the same mechanism, at the same
window that round's own stage 2 used (K=1), on a task set that took no part in building the host
configuration: the three **v18 sealed** tasks registered in
``integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json``.

Nothing below this round is edited.  The modules that carry the mechanism are imported unchanged:

* the frozen v17 acquisition runner drives the batch, its renderer is the frozen v16 renderer, and
  the frozen v17 live contract decides the four conditions and the retry diagnostic;
* the v22 chain composes the frozen v12 exact-duplicate rule, the v5 lineage evidence-safe
  retention and the v21 literal-carrier line elision, with the window bound by the pinned
  ``openai_agents_v22_window_k1`` configuration module;
* the v18 registry, whose source artifact this round's zero-API step wrote, supplies the tasks.

Two things about this task set are disclosed rather than claimed
-----------------------------------------------------------------
* The registered shape is three views, one per round (view-shape amendment 03), so a sample makes
  three reads and one answer call: ``READ_CALLS = 3``, ``MAX_TURNS = READ_CALLS + 2 = 5``.
* Each task's defect-carrying view sits at a known round.  For django-15572 it is round 3, the last
  read, so the newest-tool-group rule keeps it verbatim on every later model input; for
  django-16255 and django-15569 it is round 1, so it is ordinary elidable content under the literal
  invariant.  Both facts are written into the registration, the freeze and this module, because a
  reader has to know them to read the saving correctly.

Why the frozen plan/stub entry points are not reused as-is
-----------------------------------------------------------
``run_openai_agents_v17_acquisition.verify_freeze`` asserts that the *v16 acquisition freeze's*
matrix equals the registry's task list, arms, repeats and cap; the frozen stub controls are written
against two named v16 development tasks and their contract values.  Neither can be pointed at this
task set without either editing a frozen module or writing a matrix into a freeze artifact that is
not this batch's, so this runner does both halves honestly and separately:

* ``--plan`` and ``--stub`` below are this runner's own, and they carry the same zero-API
  guarantees the frozen ones carry (the provider client and the provider model are replaced by
  objects that raise, and the frozen runner returns from ``--plan`` before any client is built);
* the *frozen* plan and stub are replayed in the gate on the set they were written for, under the
  v17 development registry, so the host contract itself is re-shown to be unchanged;
* a control set for the three registered tasks is run through the same wiring (SDK ``Runner``, the
  real structured wiring, the real renderer and the one-retry wrapper) against the same local model,
  covering the same rejection classes and the same expected verdicts.

Modes (all zero API except ``--pilot``)
---------------------------------------
``--write-freeze``       write the confirmation freeze, once, before any paid request
``--register-revision``  write this configuration's module revision ledger
``--plan``               the frozen request plan for this task set, ``api_calls = 0``
``--stub``               the frozen stub controls *and* the v18 control set, ``api_calls = 0``
``--gate``               every hard assertion; writes the gate artifact once
``--pilot``              the paid 45-sample batch; refuses unless the gate allows it
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import io
import json
import sys
import tempfile
from contextlib import contextmanager, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v16_live_contract as live16  # noqa: E402
from experiments.runners import openai_agents_v17_live_contract as live17  # noqa: E402
from experiments.runners import openai_agents_v17_registry as registry17  # noqa: E402
from experiments.runners import openai_agents_v24_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v24_registry as registry  # noqa: E402
from experiments.runners import openai_agents_v21_carrier_elision as mechanism  # noqa: E402
from experiments.runners import openai_agents_v22_chain as chain  # noqa: E402
from experiments.runners import openai_agents_v22_window as window  # noqa: E402
from experiments.runners import run_openai_agents_api_experiment as base  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner17  # noqa: E402
from experiments.runners.openai_agents_structured_final_v16 import render  # noqa: E402

# --------------------------------------------------------------------------------- identity
K = 1
SHAPE = "N3"
ROUNDS = 3
READS_PER_ROUND = 1
READ_CALLS = READ_ROUNDS = ROUNDS * READS_PER_ROUND
EXPECTED_MODEL_CALLS = ROUNDS + 1
MAX_TURNS = READ_CALLS + 2
ARMS = ("none", "native_summary", "pruner_v1")
REPEATS = 5
SAMPLES = 3 * REPEATS * len(ARMS)
NATIVE_SAMPLES = 3 * REPEATS
MAX_SUMMARY_CALLS_PER_SAMPLE = 2
MAX_API_REQUESTS = 390
MAX_OUTPUT_TOKENS = 1024
MAX_API_RETRIES = 2
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000
EXPERIMENT_ID = "openai-repo-diagnostic-v24-length-compliance-k1-01"
PLAN_ORDER = (0, 1, 2)

REGISTRATION = ROOT / "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json"
FREEZE = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_CONFIRMATION_FREEZE_20261010.json"
)
REVISION_LEDGER = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_MODULE_REVISIONS_20261010.json"
)
GATE_OUT = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_ZERO_API_GATE_20261010.json"
)
PLAN_OUT = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_LIVE_PLAN_20261010.json"
)
#: This batch's own source registration: the same three tasks, re-registered with this
#: configuration's digests, its prompt change and the disclosure that the task set was already
#: run once.  It is written by ``--register-source`` before the freeze.
V24_REGISTRATION = (
    ROOT / "integrations/openai_agents/V24_SOURCE_REGISTRATION_20261010.json"
)
STUB_OUT = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_K1_LIVE_STUB_20261010.json"
)
PRESERVATION_DIR = Path(r"C:\Users\LENOVO\v21-artifact-preservation")

#: The batch this one follows on from, and the diagnosis written before this batch existed.  Both
#: are named in the freeze, the registration and the report, because the ruling requires the prior
#: run to be disclosed verbatim rather than quietly forgotten.
STOPPED_BATCH = (
    "runs/stage5-openai-agents-api/openai-repo-diagnostic-v23-v18-sealed-window-k1-01"
)
DIAGNOSIS = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_DIAGNOSIS_20261010.json"
)
PLAN_ARTIFACT = (
    ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_FIX_PLAN_20261010.json"
)

#: The artifacts this batch's numbers rest on, pinned by digest in the freeze and re-verified by
#: the frozen finaliser for every entry.
SOURCE_OF_RECORD = (
    "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json",
    "integrations/openai_agents/V18_VIEW_DERIVATION_RECORD_20261010.json",
    "integrations/openai_agents/V18_CANDIDATE_REDERIVATION_VERIFICATION_20261010.json",
    "integrations/openai_agents/V18_LAZY_NEGATIVE_CONTROL_SUPPLEMENT_20261010.json",
    "integrations/openai_agents/V18_DEFECT_ENTITY_DECLARATION_20261010.json",
    "integrations/openai_agents/V18_LITERAL_DISCRIMINATIVENESS_RULE_20261010.json",
    "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_20261010.json",
    "integrations/openai_agents/V18_LITERAL_RULE_AMENDMENT_02_20261010.json",
    "integrations/openai_agents/V18_VIEW_SHAPE_AMENDMENT_03_20261010.json",
    "integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json",
)

#: Every module in this configuration's path.  The set is the v23 round's set with the two forked
#: configuration modules this round adds - the v24 registry, which carries the length-discipline
#: sentence, and the v24 live contract, which carries the strengthened over-length retry - and with
#: this runner; both parents stay in the set and stay pinned, so the pin is a superset of the one
#: the previous round used and no module was removed.
PINNED_MODULES = (
    "experiments/runners/openai_agents_v24_registry.py",
    "experiments/runners/openai_agents_v24_live_contract.py",
    "experiments/runners/openai_agents_v18_registry.py",
    "experiments/runners/openai_agents_v17_live_contract.py",
    "experiments/runners/run_openai_agents_v17_acquisition.py",
    "experiments/runners/openai_agents_structured_final_v16.py",
    "experiments/runners/openai_agents_v18_sealed_registration.py",
    "experiments/runners/run_openai_agents_api_experiment.py",
    "experiments/runners/openai_agents_multitask_chain_v14.py",
    "experiments/runners/openai_agents_v19_shape_dose.py",
    "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
    "experiments/runners/openai_agents_evidence_safe_retention_v5.py",
    "experiments/runners/openai_agents_v20_chain.py",
    "experiments/runners/openai_agents_v20_retention.py",
    "experiments/runners/openai_agents_v21_carrier_elision.py",
    "experiments/runners/openai_agents_v22_window.py",
    "experiments/runners/openai_agents_v22_window_k1.py",
    "experiments/runners/openai_agents_v22_chain.py",
    "experiments/runners/run_openai_agents_v24_length_compliance_k1.py",
)
#: The four the plan names: the renderer, the live contract, the frozen runner and the registry.
PLAN_NAMED_MODULES = (
    "experiments/runners/openai_agents_v24_registry.py",
    "experiments/runners/openai_agents_v24_live_contract.py",
    "experiments/runners/openai_agents_v18_registry.py",
    "experiments/runners/openai_agents_v17_live_contract.py",
    "experiments/runners/run_openai_agents_v17_acquisition.py",
    "experiments/runners/openai_agents_structured_final_v16.py",
)

#: The pre-registered manifest keys, copied verbatim from the v22 round's own list so that this
#: batch cannot quietly drop a key the earlier rounds were required to carry.
MANIFEST_REQUIRED_KEYS = (
    "kind",
    "purpose",
    "citable_as_saving",
    "citable_as_quality_equivalence",
    "citable_note",
    "data_class",
    "disclosure",
    "never_rejudge",
    "freeze_path",
    "freeze_sha256",
    "freeze_matrix",
    "source_of_record",
    "source_of_record_verified",
    "v16_registry",
    "implementation_hashes",
    "host_configuration",
    "criteria",
    "metering",
    "request_budget",
    "acceptance_line",
    "stop_conditions",
    "report_requirements",
    "batch_report",
    "pilot_report",
    "length_trajectory",
    "failure_decomposition",
    "api_calls",
    "runner_status",
    "stopped_early",
    "stop_reason",
    "v16_manifest_finalized",
    "mechanism",
    "tool_output_disclosure",
    "forbidden_wording",
    "re_read_visibility",
    "module_hashes",
)
#: Keys this round adds on top of the copied list, declared here so the gate can require them too.
EXTRA_MANIFEST_KEYS = (
    "required_wording",
    "v24_source_registration",
    "v24_confirmation_freeze",
    "v24_defect_view_round_disclosure",
    "v24_primary_criterion",
    "v24_co_reported",
    "v24_length_compliance_configuration",
    "v24_prior_batch_disclosure",
    "v24_task_set_exhaustion",
)
V21_ROW_FIELDS = (
    "v21_outputs_considered",
    "v21_outputs_elided",
    "v21_lines_elided",
    "v21_bytes_saved",
    "v21_literal_guard_fallbacks",
    "v21_note_bytes",
)
#: Wording that must not appear in any report of this batch; the fourth entry is the one the
#: integrator required for the v16 sealed batch and it is still forbidden here.
FORBIDDEN_WORDING = (
    "compression alone saved X percent",
    "the four criteria are met",
    "the retry tax is gone",
    "the plugin saves about 12.5 percent of context",
)
REQUIRED_WORDING = "under this host configuration"

ACCEPTANCE_LINE = (
    "per task, the plugin arm's strict track is at least the baseline's (the four conditions "
    "judged on the rendered line) AND the paired complete provider-token saving is positive for "
    "a majority of pairs AND the batch mean is positive. The verdict is decided on every pair of "
    "the frozen matrix; the symmetric matched-prefix reading, the byte reading, the strict-track "
    "and semantic-accuracy endpoints per arm and the per-arm call distribution are reported "
    "beside it and never substitute for it. Failures, cap hits, retries and summary requests are "
    "all charged to their arm."
)

PRIMARY_CRITERION = {
    "name": "as-paired complete provider tokens",
    "definition": (
        "for every (task, repeat) pair: (baseline total tokens - plugin total tokens) / baseline "
        "total tokens, where a sample's total is its complete provider input tokens plus its "
        "complete provider output tokens, with retries and summary requests charged to the arm "
        "they belong to"
    ),
    "verdict": (
        "per task: the plugin arm's strict track is at least the baseline's; across the batch: a "
        "majority of pairs positive and the mean positive"
    ),
    "frozen_in": "integrations/openai_agents/V16_CONFIRMATION_FREEZE_20261006.json (same form)",
    "never_merged_with": (
        "the v16 sealed confirmation batch's 12.54 percent is reported beside this batch's number "
        "and the two are never merged and never pooled, because the host configuration and the "
        "mechanism differ"
    ),
}
CO_REPORTED = [
    {
        "name": "symmetric matched prefix, provider tokens",
        "definition": (
            "for each pair, take the first k model calls of EACH arm, where k = "
            "min(baseline calls, plugin calls), and compare the summed complete provider input "
            "tokens of those two prefixes; the truncation must be symmetric, so an arm's own "
            "retry cannot hide inside its own sum"
        ),
    },
    {
        "name": "matched prefix, context bytes",
        "definition": (
            "the same symmetric prefix over the per-boundary context bytes the recorder wrote for "
            "each model input"
        ),
    },
    {"name": "strict track and semantic accuracy per arm", "definition": "rate per arm, per task and pooled"},
    {"name": "per-arm tool_calls and model_calls", "definition": "counts and distribution per arm"},
    {"name": "which arm retried, per pair", "definition": "render_repair_calls per arm per pair"},
    {"name": "mechanism counters and guard fallbacks", "definition": "v21_* per task and per sample"},
    {
        "name": "determinism of the mechanism's byte saving per repeat",
        "definition": "the v21 counters of the three repeats of each task side by side",
    },
    {"name": "requests, cap and cache", "definition": "api_request_attempts against the cap, and the provider cache split"},
]

#: The window this configuration runs at must be the one pre-registered for it.
CONFIG_MODULE = "experiments/runners/openai_agents_v22_window_k1.py"

#: The frozen v17 case builder, captured before ``configure()`` replaces it, so the frozen
#: control replay can put the frozen development configuration back exactly as it was.
_FROZEN_BUILD_CASE = runner17.build_case

LOCK_STATE: dict[str, Any] = {}


# ------------------------------------------------------------------------------ small helpers
def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_hashes() -> dict[str, str]:
    return {relative: sha256_of(ROOT / relative) for relative in PINNED_MODULES}


def source_of_record_hashes() -> dict[str, str]:
    return {relative: sha256_of(ROOT / relative) for relative in SOURCE_OF_RECORD}


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def arithmetic() -> dict[str, Any]:
    """The pre-registered budget arithmetic, written out so the cap is a consequence.

    ``READ_CALLS = 3`` gives ``MAX_TURNS = READ_CALLS + 2 = 5`` (three reads, one answer and one
    turn of slack), so the worst case per sample is ``MAX_TURNS + RETRY_BUDGET_PER_SAMPLE = 6``.
    Forty-five samples give 45 x 6 = 270 provider requests, and the fifteen ``native_summary``
    samples add two summary requests each, 15 x 2 = 30, so the worst case is 300 and the cap of 390
    keeps exactly 1.3x over it.
    """

    worst_per_sample = MAX_TURNS + live.RETRY_BUDGET_PER_SAMPLE
    model_calls_worst = SAMPLES * worst_per_sample
    summary_worst = NATIVE_SAMPLES * MAX_SUMMARY_CALLS_PER_SAMPLE
    worst = model_calls_worst + summary_worst
    return {
        "shape": SHAPE,
        "rounds": ROUNDS,
        "reads_per_round": READS_PER_ROUND,
        "read_calls": READ_CALLS,
        "plan_order": list(PLAN_ORDER),
        "views": 3,
        "views_read_once_each": True,
        "duplicate_content_by_construction": False,
        "expected_requests_per_sample": EXPECTED_MODEL_CALLS,
        "max_turns": MAX_TURNS,
        "retry_budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
        "worst_case_requests_per_sample": worst_per_sample,
        "samples": SAMPLES,
        "native_samples": NATIVE_SAMPLES,
        "arms": list(ARMS),
        "repeats": REPEATS,
        "worst_case_model_calls": model_calls_worst,
        "summary_calls_worst_case": summary_worst,
        "worst_case_requests": worst,
        "cap": MAX_API_REQUESTS,
        "margin": round(MAX_API_REQUESTS / worst, 3),
        "at_least_1_3": MAX_API_REQUESTS >= 1.3 * worst,
        "derivation": (
            "READ_CALLS=3 => MAX_TURNS = 3+2 = 5 => worst case 6 per sample => "
            "45x6 + 15x2 = 300 => 1.3x => 390"
        ),
    }


# --------------------------------------------------------------------------- registry binding
@contextmanager
def sealed_v24_registry_installed(registered_tasks_only: bool = False):
    """Point the v24 registry at this round's registration and at the three-round shape.

    The registry reads a *sealed* task set from its own registration artifact; this round's
    registration is that artifact, so the module constant is redirected here and every shape
    constant is set to the registered shape before any task is built (the protocol text and the
    derived tool documents are computed when a task is first built and are cached, so the order
    matters).

    ``registered_tasks_only`` makes the batch's task set explicit: the registry appends its
    stage-1 development task to the registered three whenever it is in development mode, and that
    appended task carries the six-read shape, so a batch that must be exactly the three registered
    tasks of the registration asks for them by name here rather than relying on the mode.
    """

    saved = {
        "sealed": registry.SEALED_REGISTRATION,
        "read_rounds": registry.READ_ROUNDS,
        "reads_per_round": registry.READS_PER_ROUND,
        "read_calls": registry.READ_CALLS,
        "expected_model_calls": registry.EXPECTED_MODEL_CALLS,
        "active": registry.ACTIVE,
        "cache": dict(registry._CACHE),
        "task_ids": registry.task_ids,
    }
    registry.SEALED_REGISTRATION = REGISTRATION
    registry.READ_ROUNDS = ROUNDS
    registry.READS_PER_ROUND = READS_PER_ROUND
    registry.READ_CALLS = READ_CALLS
    registry.EXPECTED_MODEL_CALLS = EXPECTED_MODEL_CALLS
    if registered_tasks_only:
        registered = len(
            json.loads(REGISTRATION.read_text(encoding="utf-8"))["task_ids"]
        )
        registry.task_ids = lambda: tuple(registry.tasks())[:registered]
    registry._CACHE.clear()
    registry.set_active("sealed")
    try:
        yield registry
    finally:
        registry._CACHE.clear()
        registry._CACHE.update(saved["cache"])
        registry.SEALED_REGISTRATION = saved["sealed"]
        registry.READ_ROUNDS = saved["read_rounds"]
        registry.READS_PER_ROUND = saved["reads_per_round"]
        registry.READ_CALLS = saved["read_calls"]
        registry.EXPECTED_MODEL_CALLS = saved["expected_model_calls"]
        registry.task_ids = saved["task_ids"]
        registry.set_active(saved["active"])


def plan_steps(task_id: str) -> list[str]:
    """The N3 protocol text, taken from the v19 module so the wording is byte-identical."""

    from experiments.runners import openai_agents_v19_shape_dose as v19

    return v19.plan_steps(task_id, SHAPE)


def select_window(k: int) -> dict[str, Any]:
    """Bind the window this batch runs at, and refuse every other value.

    K=1 is hard-coded above.  This function still goes through the pre-registered configuration
    module, so the window that reaches the filter is a pinned module's value rather than a literal
    in this file, and an unregistered window (K=2, for example) is refused by the configuration
    module itself before anything is built.
    """

    value = int(k)
    if value != K:
        raise SystemExit(
            f"this batch runs at K={K} only: the v22 plan authorises {window.AUTHORISED_WINDOWS} "
            f"and this round pre-registered K={K}, not K={value}"
        )
    config_module = __import__(
        f"experiments.runners.openai_agents_v22_window_k{value}", fromlist=["CONFIG"]
    )
    if _relative(Path(config_module.__file__).resolve()) != CONFIG_MODULE:
        raise SystemExit("the configuration module on disk is not the pre-registered one")
    return chain.install_configuration(config_module)


def configure() -> dict[str, Any]:
    """Point the frozen runner, the v22 chain and the N3 protocol at this round's task set.

    The frozen v17 acquisition runner resolves its live contract, its retry diagnostic and its
    structured wiring through its own module-level ``live`` name, and the v22 chain resolves its
    registry through its own binding.  Both are pointed at this round's forks here, and the frozen
    v17 modules themselves are left on disk untouched; ``frozen_v17_controls_installed`` puts the
    frozen bindings back for the frozen control replay.
    """

    math_ = arithmetic()
    registry.protocol_steps = lambda task_id: plan_steps(task_id)
    # The frozen runner resolves its scenarios, its tools, its contracts and its task table through
    # its own module-level ``registry`` name; pointing it at the v24 registry is what makes the
    # batch run this task set end to end with the length-discipline sentence in the prompt.
    runner17.registry = registry
    # ...and its retry wording, its over-length diagnostic and its structured wiring through
    # ``live``, which is the v24 fork here.
    runner17.live = live
    # ``chain`` resolves task data through its own binding, and the v14 ancestor - whose
    # constructor the chain's filter inherits - resolves it through the v14 module's binding.
    chain.registry = registry
    from experiments.runners import openai_agents_multitask_chain_v14 as v14
    from experiments.runners import openai_agents_v19_shape_dose as v19

    v14.registry = registry
    v19.registry = registry
    runner17.MAX_TURNS = MAX_TURNS
    runner17.PILOT_METHODS = ARMS
    runner17.PILOT_REPEATS = REPEATS
    runner17.PILOT_MAX_API_REQUESTS = MAX_API_REQUESTS
    runner17.PILOT_MAX_SUMMARY_CALLS_PER_SAMPLE = MAX_SUMMARY_CALLS_PER_SAMPLE
    runner17.PILOT_EXPERIMENT_ID = EXPERIMENT_ID

    def build_case(task_id: str, repeat: int):
        entry = registry.task(task_id)
        contract = entry["contract"]
        case = base.ApiCase(
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
    return math_


# ------------------------------------------------------------------------ context managers
@contextmanager
def v24_chain_installed():
    """Run the frozen runner with the v22 chain and the v18 registry bound together."""

    from experiments.runners import openai_agents_multitask_chain_v14 as v14

    original = {
        "runner_chain": runner17.chain,
        "chain_registry": chain.registry,
        "v14_registry": v14.registry,
    }
    runner17.chain = chain
    chain.registry = registry
    v14.registry = registry
    try:
        yield
    finally:
        runner17.chain = original["runner_chain"]
        chain.registry = original["chain_registry"]
        v14.registry = original["v14_registry"]


@contextmanager
def frozen_v17_controls_installed():
    """Put the frozen v17 development configuration back, for the frozen control replay only.

    The frozen stub controls name two v16 development tasks and their contract values, and the
    frozen ``verify_freeze`` asserts the v16 acquisition freeze's own task list.  Both are shown
    unchanged by running them exactly as the earlier rounds ran them, with the v17 development
    registry installed everywhere and this round's bindings suspended.
    """

    from experiments.runners import openai_agents_multitask_chain_v14 as v14
    from experiments.runners import openai_agents_v19_shape_dose as v19

    saved = {
        "runner_registry": runner17.registry,
        "runner_build_case": runner17.build_case,
        "runner_max_turns": runner17.MAX_TURNS,
        "runner_methods": runner17.METHODS,
        "runner_repeats": runner17.REPEATS,
        "runner_cap": runner17.MAX_API_REQUESTS,
        "chain_registry": chain.registry,
        "v14_registry": v14.registry,
        "v19_registry": v19.registry,
        "registry_protocol_steps": registry.protocol_steps,
    }
    runner17.registry = registry17
    runner17.build_case = _FROZEN_BUILD_CASE
    runner17.MAX_TURNS = 10
    runner17.METHODS = ("none",)
    runner17.REPEATS = 1
    runner17.MAX_API_REQUESTS = 30
    chain.registry = registry17
    v14.registry = registry17
    v19.registry = registry17
    try:
        yield
    finally:
        registry.protocol_steps = saved["registry_protocol_steps"]
        runner17.registry = saved["runner_registry"]
        runner17.build_case = saved["runner_build_case"]
        runner17.MAX_TURNS = saved["runner_max_turns"]
        runner17.METHODS = saved["runner_methods"]
        runner17.REPEATS = saved["runner_repeats"]
        runner17.MAX_API_REQUESTS = saved["runner_cap"]
        chain.registry = saved["chain_registry"]
        v14.registry = saved["v14_registry"]
        v19.registry = saved["v19_registry"]


def _frozen_build_case(task_id: str, repeat: int):
    """The frozen v17 case builder, kept for the record of what the replay restores."""

    return _FROZEN_BUILD_CASE(task_id, repeat)


@contextmanager
def v24_finalise_installed():
    """Add the pre-registered keys the frozen pilot finaliser does not write.

    The frozen v17 finaliser writes its own key set.  This wrapper adds the keys the v22 round
    pre-registered and this round inherits, plus this round's own disclosure keys, around the
    frozen function rather than inside it, so no frozen file changes and the returned manifest is
    the one that was written.
    """

    original = runner17.finalise_pilot_manifest

    def finalise(root, **kwargs):
        manifest = original(root, **kwargs)
        path = Path(root) / "manifest.json"
        manifest["mechanism"] = chain.mechanism_block()
        manifest["v16_registry"] = registry.manifest_block()
        manifest["implementation_hashes"] = module_hashes()
        manifest["module_hashes"] = dict(manifest["implementation_hashes"])
        manifest["never_rejudge"] = (
            "attempt 01 to attempt 03, pilot-01, pilot-02, v12 to v15, the v16 sealed "
            "confirmation batch, the v17 development pilots, the v18 selection branch, the v19 "
            "dose batches, the v20 gate round, the v21 round and every v22 dose, b and stage "
            "group are untouched and unrejudged; this batch does not merge its numbers with any "
            "of them, and in particular the v22 development numbers are not citable"
        )
        manifest["v22_gate_threshold"] = {
            "setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": chain.TRIGGER_GATE_POLICY,
        }
        manifest["v22_window_mechanism"] = {
            "window_lines": mechanism.WINDOW_LINES,
            "config_module": CONFIG_MODULE,
            "k_in_force": K,
            "plan": _relative(ROOT / "integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json"),
            "counters": list(mechanism.V21_COUNTER_FIELDS),
        }
        manifest["tool_output_disclosure"] = chain.TOOL_OUTPUT_DISCLOSURE
        manifest["tool_output_disclosure_note"] = (
            "the reader tool description is identical in all three arms and is not rewritten for "
            "the plugin arm; 'returns those numbered lines verbatim' holds for the lines the "
            "mechanism keeps, and every omission is declared by an explicit interval note rather "
            "than being silent"
        )
        manifest["forbidden_wording"] = list(FORBIDDEN_WORDING)
        manifest["required_wording"] = REQUIRED_WORDING
        manifest["re_read_visibility"] = {
            "rule": (
                "per arm: tool_calls and model_calls are reported for every sample, so a re-read "
                "induced by an elision appears as extra tool calls and is billed"
            ),
            "expected_per_sample": {"tool_calls": READ_CALLS, "model_calls": EXPECTED_MODEL_CALLS},
            "newest_group_policy": (
                "the most recent tool group is kept verbatim, so the third read of every round of "
                "reads is never elided on the call that follows it"
            ),
        }
        manifest["v24_source_registration"] = {
            "path": _relative(V24_REGISTRATION),
            "sha256": sha256_of(V24_REGISTRATION) if V24_REGISTRATION.is_file() else None,
            "task_data_registration": _relative(REGISTRATION),
            "task_data_registration_sha256": sha256_of(REGISTRATION),
            "task_ids": list(registry.task_ids()),
            "instance_ids": [registry.task(t)["instance_id"] for t in registry.task_ids()],
            "view_shape": {
                "rounds": ROUNDS,
                "reads_per_round": READS_PER_ROUND,
                "read_calls": READ_CALLS,
                "expected_model_calls": EXPECTED_MODEL_CALLS,
                "max_turns": MAX_TURNS,
                "max_view_lines": 60,
                "views_pairwise_disjoint": True,
            },
            "citable_as_saving_for_the_v16_sealed_batch": False,
        }
        manifest["v24_confirmation_freeze"] = {
            "path": _relative(FREEZE),
            "sha256": sha256_of(FREEZE),
            "id": str((json.loads(FREEZE.read_text(encoding="utf-8"))).get("id")) if FREEZE.is_file() else "",
            "prompt_change": registry.fork_block(),
            "retry_change": live.fork_report(),
        }
        manifest["v24_length_compliance_configuration"] = length_compliance_configuration()
        manifest["v24_prior_batch_disclosure"] = prior_batch_disclosure()
        manifest["v24_task_set_exhaustion"] = {
            "task_set": list(registry.task_ids()),
            "runs_written_down": 2,
            "first_run": STOPPED_BATCH,
            "first_run_outcome": "stopped at 39 of 45 samples, django-15572 failed on all fifteen",
            "this_run": EXPERIMENT_ID,
            "exhausted_after_this_batch": True,
            "never_run_again": True,
            "never_replaced_by_another_id": True,
            "statement": (
                "these three tasks were run once before, under the v23 configuration, and the batch "
                "stopped at 39 of 45 samples because django-15572 failed rendering on all fifteen of "
                "its samples; this batch is the second run of the same task set, under the v24 "
                "configuration, and the task set is exhausted by it"
            ),
        }
        manifest["v24_defect_view_round_disclosure"] = defect_view_round_disclosure()
        manifest["v24_primary_criterion"] = PRIMARY_CRITERION
        manifest["v24_co_reported"] = CO_REPORTED
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    runner17.finalise_pilot_manifest = finalise
    try:
        yield
    finally:
        runner17.finalise_pilot_manifest = original


def defect_view_round_disclosure() -> dict[str, Any]:
    """Per task, the round its defect-carrying view sits in, and what the shape does with it."""

    out: dict[str, Any] = {}
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
        row = registration["tasks"][task_id]
        defect = row["defect_view"]
        out[task_id] = {
            "instance_id": entry["instance_id"],
            "defect_view_round": int(defect["round"]),
            "defect_symbol": str(defect["symbol"]),
            "defect_file": str(defect["file"]),
            "defect_range": [int(defect["first_line"]), int(defect["last_line"])],
            "is_the_newest_tool_group": bool(defect["is_the_newest_tool_group"]),
            "consequence": str(defect["recency_consequence"]),
            "reported_as": (
                "an explanatory fact of the task shape, disclosed before the batch and not claimed "
                "as a merit of the mechanism"
            ),
        }
    return out


# --------------------------------------------------------------------------- plan and stub
def _write_once(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        raise SystemExit(
            f"refusing to overwrite the existing artifact {path}: write a new one instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def preflight() -> dict[str, Any]:
    """This round's own preflight: the registration, the freeze, the renderer and the cap."""

    problems = registry.all_problems()
    if any(problems.values()):
        raise SystemExit(f"the v18 sealed registration did not verify: {problems}")
    if not FREEZE.is_file():
        raise SystemExit(f"the confirmation freeze is missing: {FREEZE}")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    live_modules = module_hashes()
    recorded = dict(freeze["host_configuration"]["modules"])
    drifted = sorted(
        relative
        for relative, digest in live_modules.items()
        if recorded.get(relative) != digest
    )
    if drifted:
        raise SystemExit(
            "the freeze does not pin the modules on disk now (revision-aware fail-closed): "
            + ", ".join(drifted)
        )
    sources = source_of_record_hashes()
    source_drift = sorted(
        relative
        for relative, digest in sources.items()
        if str((freeze.get("source_of_record") or {}).get(relative)) != digest
    )
    if source_drift:
        raise SystemExit("source-of-record drift: " + ", ".join(source_drift))
    self_test = live.self_test(registry.task(registry.task_ids()[0])["contract"]["issue_value"])
    if not self_test["ok"]:
        raise SystemExit(f"the renderer self-test failed: {self_test['checks']}")
    math_ = arithmetic()
    if not math_["at_least_1_3"]:
        raise SystemExit("the cap does not keep the 1.3x margin over the worst case")
    return {
        "registration_problems": problems,
        "freeze": {
            "path": _relative(FREEZE),
            "sha256": sha256_of(FREEZE),
            "status": str(freeze.get("status")),
            "matrix": dict(freeze.get("matrix") or {}),
            "cap": int((freeze.get("budget_arithmetic") or {}).get("cap", 0)),
        },
        "modules_pinned": recorded,
        "modules_live": live_modules,
        "source_of_record": sources,
        "renderer_self_test": self_test,
        "arithmetic": math_,
    }


def planned_requests() -> dict[str, Any]:
    samples = len(registry.task_ids()) * len(ARMS) * REPEATS
    logical = samples * EXPECTED_MODEL_CALLS
    with_retries = samples * (EXPECTED_MODEL_CALLS + live.RETRY_BUDGET_PER_SAMPLE)
    math_ = arithmetic()
    return {
        "samples": samples,
        "reads_plus_answer_per_sample": EXPECTED_MODEL_CALLS,
        "minimum_planned_requests": logical,
        "worst_case_logical_requests_with_one_retry_each": with_retries,
        "pre_registered_worst_case": math_["worst_case_requests"],
        "max_api_requests": MAX_API_REQUESTS,
        "max_api_retries_per_model_call": MAX_API_RETRIES,
        "transport_retries_share_the_cap": True,
        "worst_case_fits_the_cap": math_["worst_case_requests"] <= MAX_API_REQUESTS,
    }


def run_plan(artifact: Path, args: list[str]) -> int:
    checks = preflight()
    plan = planned_requests()
    if not plan["worst_case_fits_the_cap"]:
        raise SystemExit("the planned worst case does not fit inside the pre-registered cap")
    cleaned = forced_args(args)
    original = {
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
    }
    printed: list[str] = []
    base.SCENARIOS = tuple(registry.task_ids())
    base.build_case = lambda scenario, repeat: runner17.build_case(scenario, repeat)
    base.AsyncOpenAI = lambda **kwargs: runner17._NoNetworkClient()
    base.OpenAIChatCompletionsModel = runner17._NoProviderModel
    buffer = io.StringIO()
    try:
        with redirect_stdout(buffer):
            status = base.main(cleaned + ["--plan"])
        printed = buffer.getvalue().splitlines()
    finally:
        base.AsyncOpenAI = original["client"]
        base.OpenAIChatCompletionsModel = original["provider_model"]
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
    payload = {
        "schema": "openai_agents_v24_length_compliance_k1_plan",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "plan",
        "api_calls": 0,
        "experiment_id": EXPERIMENT_ID,
        "window_lines": K,
        "config_module": CONFIG_MODULE,
        "zero_api_enforced_by": [
            "base.AsyncOpenAI replaced by a client whose completions call raises",
            "base.OpenAIChatCompletionsModel replaced by a model whose responses raise",
            "the frozen runner returns from --plan before any client is built",
        ],
        "runner_status": int(status),
        "task_ids": list(registry.task_ids()),
        "case_shape": {
            "expected_model_calls": EXPECTED_MODEL_CALLS,
            "expected_tool_names": READ_CALLS,
            "max_turns": MAX_TURNS,
        },
        "preflight": checks,
        "planned_requests": plan,
        "arithmetic": arithmetic(),
        "mechanism": chain.mechanism_block(),
        "frozen_plan_print_lines": printed,
        "paid_batch_started": False,
    }
    _write_once(artifact, payload)
    print(f"plan artifact: {artifact}")
    print(
        f"api_calls=0 samples={SAMPLES} shape={SHAPE} K={K} "
        f"worst_case_requests={arithmetic()['worst_case_requests']} cap={MAX_API_REQUESTS} "
        f"margin={arithmetic()['margin']}"
    )
    return 0


def frozen_control_replay() -> dict[str, Any]:
    """Replay the frozen v17 stub controls on the task set they were written for."""

    with tempfile.TemporaryDirectory() as tmp:
        artifact = Path(tmp) / "frozen_stub.json"
        with frozen_v17_controls_installed():
            with v24_chain_installed():
                status = runner17.main(["--stub", "--artifact", str(artifact)])
        payload = json.loads(artifact.read_text(encoding="utf-8"))
    controls = list(payload.get("controls") or [])
    return {
        "check": "the_frozen_v17_stub_controls_still_land_on_their_frozen_verdicts",
        "task_set": "the v17 development tasks the frozen controls were written for",
        "why": (
            "the frozen control set names two v16 development tasks and their contract values, so "
            "it is replayed on that set, under the v17 development registry, to show the host "
            "contract (renderer, retry diagnostic, wiring) is unchanged; the registered tasks of "
            "this batch get their own control set below"
        ),
        "runner_status": int(status),
        "controls": len(controls),
        "controls_ok": bool(payload.get("controls_ok")),
        "api_calls": int(payload.get("api_calls", -1)),
        "verdicts": [
            {"control": str(entry.get("control")), "task": str(entry.get("task")), "ok": bool(entry.get("ok"))}
            for entry in controls
        ],
        "ok": bool(status == 0 and payload.get("controls_ok") and int(payload.get("api_calls", -1)) == 0),
    }


def v18_control_set() -> list[dict[str, Any]]:
    """The rejection classes of the frozen controls, instantiated for the three registered tasks."""

    controls: list[dict[str, Any]] = []
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        contract = entry["contract"]
        issue = str(contract["issue_value"])
        facts = [str(fact) for fact in contract["required_facts"]]
        cause, fix = str(entry["cause_token"]), str(entry["fix_token"])
        valid = {
            "issue": issue,
            "cause": f"cause {cause} " + " ".join(facts),
            "fix": f"fix {fix}",
            "facts": facts,
        }
        missing_field = {"issue": issue, "cause": f"{cause}", "fix": f"{fix}"}
        extra_field = {**valid, "extra": "not allowed"}
        duplicate_key = (
            f'{{"issue": "{issue}", "issue": "{issue}", "cause": "{cause}", '
            f'"fix": "{fix}", "facts": {json.dumps(facts)}}}'
        )
        wrong_type = {**valid, "facts": " ".join(facts)}
        # Every declared fact must appear in the rendered line, and the line is built from cause
        # and fix only.  The proposal below carries neither regex token, so no declared fact can
        # ride inside one: on django-15569 in particular the fact ``lookup`` is a substring of both
        # ``_unregister_lookup`` and ``lookup_name``, so a proposal that keeps the two tokens can
        # never fail the declared-fact condition.  That property is recorded beside the control.
        rendered_probe = f"RESULT issue={issue} cause=cause unknown fix=fix unknown"
        unreachable = [fact for fact in facts if fact in rendered_probe]
        declared_absent = {
            "issue": issue,
            "cause": "cause unknown",
            "fix": "fix unknown",
            "facts": facts,
        }
        over_long = {
            "issue": issue,
            "cause": (
                f"cause {cause} "
                + " ".join(facts)
                + " the comparison accepts the wrong value today and never enforces the newer "
                "requirement anywhere in this method at all "
                * 2
            ).strip(),
            "fix": f"fix {fix}",
            "facts": facts,
        }
        controls.extend(
            [
                {
                    "control": "invalid_json",
                    "requirement": "unparseable proposal",
                    "task": task_id,
                    "proposal": f"RESULT issue={issue} cause={cause} fix={fix}",
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:invalid_json_or_duplicate_key",
                        "repair_calls": 1,
                        "candidate_chars": 0,
                    },
                },
                {
                    "control": "missing_field",
                    "requirement": "a required field is absent",
                    "task": task_id,
                    "proposal": json.dumps(missing_field),
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:missing_or_extra_field",
                        "repair_calls": 1,
                        "candidate_chars": 0,
                    },
                },
                {
                    "control": "extra_field",
                    "requirement": "an unexpected field is present",
                    "task": task_id,
                    "proposal": json.dumps(extra_field),
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:missing_or_extra_field",
                        "repair_calls": 1,
                        "candidate_chars": 0,
                    },
                },
                {
                    "control": "duplicate_key",
                    "requirement": "the same key appears twice",
                    "task": task_id,
                    "proposal": duplicate_key,
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:invalid_json_or_duplicate_key",
                        "repair_calls": 1,
                        "candidate_chars": 0,
                    },
                },
                {
                    "control": "wrong_type",
                    "requirement": "facts is a string instead of a list of strings",
                    "task": task_id,
                    "proposal": json.dumps(wrong_type),
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:invalid_fact_list",
                        "repair_calls": 1,
                        "candidate_chars": 0,
                    },
                },
                {
                    "control": "missing_literal",
                    "requirement": "a declared fact does not appear in the rendered line",
                    "task": task_id,
                    "proposal": json.dumps(declared_absent),
                    "note": (
                        "no required fact is a substring of the rendered line; facts that would "
                        "still be carried by a regex token are recorded as unreachable: "
                        f"{sorted(unreachable)}"
                    ),
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:declared_fact_missing_from_rendered_answer",
                        "repair_calls": 1,
                    },
                },
                {
                    "control": "artificially_over_long",
                    "requirement": "the rendered answer exceeds 160 characters",
                    "task": task_id,
                    "proposal": json.dumps(over_long),
                    "expected": {
                        "render_accepted": False,
                        "reason": "retry_rejected:over_160_chars",
                        "repair_calls": 1,
                        "candidate_over_160": True,
                    },
                },
                {
                    "control": "diagnostic_used_by_model",
                    "requirement": (
                        "the host's rejection reason reaches the retry, and a model that acts on "
                        "it is accepted on the retry"
                    ),
                    "task": task_id,
                    "proposal": json.dumps(declared_absent),
                    "repair_proposal": json.dumps(valid),
                    "uses_diagnostic": True,
                    "expected": {
                        "render_accepted": True,
                        "reason": "accepted_retry",
                        "repair_calls": 1,
                        "diagnostic_carried": True,
                        "within_160": True,
                    },
                },
                {
                    "control": "well_formed_positive",
                    "requirement": "a well-formed proposal is accepted first time",
                    "task": task_id,
                    "proposal": json.dumps(valid),
                    "expected": {
                        "render_accepted": True,
                        "reason": "accepted_first",
                        "repair_calls": 0,
                        "substituted": True,
                        "frozen_regex": True,
                        "facts_complete": True,
                        "within_160": True,
                    },
                },
            ]
        )
    return controls


def run_v18_controls() -> dict[str, Any]:
    """Run the v18 control set through the same wiring the paid batch uses."""

    contracts = runner17.contract_table()
    results: list[dict[str, Any]] = []
    original = {
        "scenarios": base.SCENARIOS,
        "build_case": base.build_case,
        "disclosure": base.SYNTHETIC_DISCLOSURE,
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
    }
    base.build_case = lambda scenario, repeat: runner17.build_case(scenario, repeat)
    base.SYNTHETIC_DISCLOSURE = runner17.DISCLOSURE
    base.AsyncOpenAI = lambda **kwargs: runner17._NoNetworkClient()
    base.OpenAIChatCompletionsModel = runner17._NoProviderModel
    runner17._placeholder_key_installed()
    try:
        for control in v18_control_set():
            task_id = str(control["task"])
            base.SCENARIOS = (task_id,)
            holder = runner17._StubHolder(
                str(control["proposal"]),
                repair_proposal=str(control.get("repair_proposal") or ""),
                uses_diagnostic=bool(control.get("uses_diagnostic", True)),
            )
            cleaned = forced_args(["--confirm-send-public-source"])
            # One sample per control, on the arm the frozen control set used, so the row the
            # assertions read is the control's own row.
            cleaned = runner17._replace_option(cleaned, "--methods", "none")
            cleaned = runner17._replace_option(cleaned, "--repeats", "1")
            cleaned = runner17._replace_option(cleaned, "--scenarios", task_id)
            cleaned = runner17._replace_option(
                cleaned, "--experiment-id", f"{EXPERIMENT_ID}-stub-{task_id}-{control['control']}"
            )
            with tempfile.TemporaryDirectory() as tmp:
                cleaned_run = runner17._replace_option(cleaned, "--out", tmp)
                parsed = base.build_parser().parse_args(cleaned_run)
                root = Path(parsed.out) / parsed.experiment_id
                budget, _ = base.resolve_budget(parsed)
                shared: dict[str, Any] = {}
                with chain.chain_wiring():
                    with live.inner_model_wiring(holder.factory(shared)):
                        from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

                        with v8.evidence_wiring(
                            root,
                            int(budget.hard_limit_tokens) * 4,
                            recorder_factory=chain.MultitaskRecorder,
                        ):
                            with live.structured_wiring(
                                root, contracts=contracts, state=shared
                            ):
                                status = base.main(cleaned_run)
                row = json.loads(
                    (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()[-1]
                )
            expected = dict(control["expected"])
            report = dict(row.get("render_report") or {})
            observed_reason = str(row.get("render_reason", ""))
            observed = {
                "status": int(status),
                "model_calls": int(row.get("model_calls", 0)),
                "api_request_attempts": int(row.get("api_request_attempts", 0)),
                "render_accepted": bool(row.get("render_accepted")),
                "reason": observed_reason,
                "repair_calls": int(row.get("render_repair_calls", 0)),
                "final_output": str(row.get("final_output", "")),
                "final_output_chars": len(str(row.get("final_output", ""))),
                "frozen_regex": bool(report.get("frozen_regex")),
                "facts_complete": bool(report.get("facts_complete")),
                "within_160": bool(report.get("within_160")),
                "single_line_prefix": bool(report.get("single_line_prefix")),
                "candidate_chars": int(row.get("render_rendered_chars", 0) or 0),
                "had_candidate": bool(row.get("render_had_candidate")),
                "substituted": bool(row.get("render_substituted_into_final_answer")),
                "diagnostic_carried": bool(row.get("render_diagnostic_carried")),
                "diagnostic_chars": len(str(row.get("render_diagnostic") or "")),
                "truncated": bool(row.get("render_truncated")),
                "fields_dropped": bool(row.get("render_fields_dropped")),
                "success": bool(row.get("success")),
            }
            adaptations = dict(row.get("prompt_adaptations") or {})
            checks = {
                "reason": observed_reason == str(expected["reason"])
                or observed_reason.startswith(str(expected["reason"])),
                "render_accepted": observed["render_accepted"] == bool(expected["render_accepted"]),
                "repair_calls": observed["repair_calls"] == int(expected["repair_calls"]),
                "never_truncated": observed["truncated"] is False,
                "no_field_dropped": observed["fields_dropped"] is False,
                "model_calls_are_reads_plus_answer_plus_retry": observed["model_calls"]
                == EXPECTED_MODEL_CALLS + observed["repair_calls"],
                "frozen_prompt_adapted": not adaptations.get("unapplied")
                and len(adaptations.get("applied") or []) == len(live.PROMPT_REPLACEMENTS),
            }
            if expected["render_accepted"] is False:
                checks["empty_final_answer"] = observed["final_output"] == ""
            if expected.get("candidate_chars") is not None:
                checks["candidate_chars"] = observed["candidate_chars"] == int(
                    expected["candidate_chars"]
                )
            if expected.get("candidate_over_160"):
                checks["candidate_over_160"] = observed["candidate_chars"] > live.MAX_CHARS
            if expected.get("substituted"):
                checks["rendered_line_substituted"] = observed["substituted"] is True
            if "frozen_regex" in expected:
                checks["frozen_regex"] = observed["frozen_regex"] == bool(expected["frozen_regex"])
            if "facts_complete" in expected:
                checks["facts_complete"] = observed["facts_complete"] == bool(
                    expected["facts_complete"]
                )
            if "diagnostic_carried" in expected:
                checks["diagnostic_carried"] = observed["diagnostic_carried"] == bool(
                    expected["diagnostic_carried"]
                )
            results.append(
                {
                    "control": str(control["control"]),
                    "task": task_id,
                    "note": str(control.get("note", "")),
                    "expected": expected,
                    "observed": observed,
                    "checks": checks,
                    "ok": all(checks.values()),
                }
            )
    finally:
        base.SCENARIOS = original["scenarios"]
        base.build_case = original["build_case"]
        base.SYNTHETIC_DISCLOSURE = original["disclosure"]
        base.AsyncOpenAI = original["client"]
        base.OpenAIChatCompletionsModel = original["provider_model"]
    failed = [f"{entry['task']}/{entry['control']}" for entry in results if not entry["ok"]]
    return {
        "check": "the_registered_tasks_pass_the_same_rejection_classes_through_the_same_wiring",
        "ok": bool(results) and not failed,
        "controls": len(results),
        "failed": failed,
        "detail": results,
    }


def run_stub(artifact: Path, args: list[str]) -> int:
    """The zero-API stub: the frozen controls replayed, and this task set's own controls."""

    preflight()
    replay = frozen_control_replay()
    controls = run_v18_controls()
    payload = {
        "schema": "openai_agents_v24_length_compliance_k1_stub",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "stub",
        "api_calls": 0,
        "experiment_id": EXPERIMENT_ID,
        "window_lines": K,
        "zero_api_enforced_by": [
            "base.AsyncOpenAI replaced by a client whose completions call raises",
            "base.OpenAIChatCompletionsModel replaced by a model whose responses raises",
            "the provider model is replaced by a deterministic local model before evidence wiring",
        ],
        "frozen_control_replay": replay,
        "registered_task_controls": controls,
        "controls_ok": bool(replay["ok"] and controls["ok"]),
        "task_ids": list(registry.task_ids()),
        "mechanism": chain.mechanism_block(),
    }
    _write_once(artifact, payload)
    print(f"stub artifact: {artifact}")
    print(
        f"frozen controls ok={replay['controls_ok']} ({replay['controls']} controls); "
        f"registered-task controls ok={controls['ok']} ({controls['controls']} controls)"
    )
    return 0 if payload["controls_ok"] else 1


# ------------------------------------------------------------------------- forced arguments
def forced_args(args: list[str], *, out: str | None = None, confirm: bool = False) -> list[str]:
    cleaned = runner17.forced_args(args, out=out, confirm=confirm)
    cleaned = runner17._replace_option(cleaned, "--methods", ",".join(ARMS))
    cleaned = runner17._replace_option(cleaned, "--repeats", str(REPEATS))
    cleaned = runner17._replace_option(cleaned, "--scenarios", ",".join(registry.task_ids()))
    cleaned = runner17._replace_option(cleaned, "--experiment-id", EXPERIMENT_ID)
    cleaned = runner17._replace_option(cleaned, "--max-api-requests", str(MAX_API_REQUESTS))
    cleaned = runner17._replace_option(cleaned, "--max-turns", str(MAX_TURNS))
    cleaned = runner17._replace_option(
        cleaned, "--max-summary-calls", str(MAX_SUMMARY_CALLS_PER_SAMPLE)
    )
    cleaned = runner17._replace_option(cleaned, "--max-output-tokens", str(MAX_OUTPUT_TOKENS))
    cleaned = runner17._replace_option(cleaned, "--provider-soft", str(PROVIDER_SOFT))
    cleaned = runner17._replace_option(cleaned, "--provider-target", str(PROVIDER_TARGET))
    cleaned = runner17._replace_option(cleaned, "--provider-hard", str(PROVIDER_HARD))
    cleaned = runner17._replace_option(cleaned, "--max-api-retries", str(MAX_API_RETRIES))
    return cleaned


# ---------------------------------------------------------------------------- freeze file
def write_freeze() -> dict[str, Any]:
    """Write the confirmation freeze, once, before any paid request."""

    if FREEZE.exists():
        raise SystemExit(
            f"refusing to overwrite the existing freeze {FREEZE}: write a new one instead"
        )
    math_ = arithmetic()
    modules = module_hashes()
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    supplement = json.loads(
        (ROOT / "integrations/openai_agents/V18_LAZY_NEGATIVE_CONTROL_SUPPLEMENT_20261010.json")
        .read_text(encoding="utf-8")
    )
    payload = {
        "schema": "openai_agents_v24_confirmation_freeze",
        "id": "V24-LENGTH-COMPLIANCE-K1-01",
        "status": "FROZEN",
        "purpose": "sealed_confirmation_of_the_literal_carrier_elision_at_k1_under_the_v24_length_compliant_prompt",
        "written_utc_date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "written_before_any_paid_request": True,
        "api_requests_spent_when_written": 0,
        "experiment_id": EXPERIMENT_ID,
        "produced_by": "experiments/runners/run_openai_agents_v24_length_compliance_k1.py",
        "mechanism": {
            "name": chain.MECHANISM_NAME,
            "schema": chain.MECHANISM_SCHEMA,
            "composition_schema": chain.COMPOSITION_SCHEMA,
            "k": K,
            "window_lines": K,
            "window_is_the_only_variable": (
                "K is the only parameter of this mechanism; it was pre-registered per K in "
                "integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json and may not be "
                "changed after any saving number is seen"
            ),
            "config_module": CONFIG_MODULE,
            "chain_module": "experiments/runners/openai_agents_v22_chain.py",
            "mechanism_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
            "frozen_default_window": mechanism.WINDOW_LINES,
            "rule": (
                "the frozen v12 exact duplicate rule runs first; the v5 lineage evidence-safe "
                "selective retention runs on the deduplicated list; the literal-carrier line "
                "elision then keeps verbatim every line that carries a registered literal or "
                "constraint phrase together with one line before and after it (K=1), elides every "
                "other line and replaces each maximal omitted run with one interval note, and "
                "keeps the most recent tool group verbatim; any literal loss, any non-reduction or "
                "any exception sends the payload this stage received, verbatim"
            ),
        },
        "task_set": list(registration["task_ids"]),
        "task_set_note": (
            "the three v18 sealed candidates registered in "
            "integrations/openai_agents/V18_SOURCE_REGISTRATION_20261010.json; they were run once "
            "before, under the v23 configuration, where django-15572 failed rendering on all "
            "fifteen of its samples and the batch stopped at 39 of 45; this batch is the second "
            "and final run of the same task set, under the v24 configuration, and after it the "
            "task set is exhausted"
        ),
        "task_set_is_exhausted_after_this_batch": True,
        "prior_batch": {
            "batch": "runs/stage5-openai-agents-api/openai-repo-diagnostic-v23-v18-sealed-window-k1-01",
            "configuration": "V18-SEALED-WINDOW-K1-01",
            "stopped_at": "39 of 45 samples",
            "failure": (
                "task django-15572 (autoreload_template_changed) failed rendering on all "
                "fifteen of its samples across all three arms: the first answer exceeded the "
                "160-character bound and the single billed retry still exceeded it, so the "
                "answers closed empty"
            ),
            "audit": "fail-closed: complete=false, usable_as_saving=false, fifteen errors",
            "citable": False,
            "no_number_from_it_is_used_here": True,
        },
        "this_is_the_second_run_of_the_same_task_set": True,
        "diagnosis": {
            "path": "integrations/openai_agents/V24_LENGTH_COMPLIANCE_DIAGNOSIS_20261010.json",
            "sha256": sha256_of(
                ROOT / "integrations/openai_agents/V24_LENGTH_COMPLIANCE_DIAGNOSIS_20261010.json"
            ),
            "conclusion": (
                "the failure was a first-answer length-compliance failure, not a contract "
                "that forces a long answer: the shortest compliant rendered line for that "
                "task is 75 characters against the 160-character bound"
            ),
        },
        "task_set_provenance": {
            "registration": _relative(REGISTRATION),
            "v24_registration": {
                "path": _relative(V24_REGISTRATION),
                "sha256": sha256_of(V24_REGISTRATION),
                "role": (
                    "the same three tasks re-registered for this batch: the task data is the v18 "
                    "registration's, byte for byte, and this artifact carries this batch's "
                    "configuration digests, its prompt change and its task-set exhaustion"
                ),
            },
            "registration_sha256": sha256_of(REGISTRATION),
            "view_shape": registration["view_shape"],
            "literal_rule_verdicts": registration["rules_in_force"]["literal_rule"]["rule_id"],
            "lazy_negative_control_complete": bool(supplement.get("complete")),
            "lazy_negative_control_holds_per_candidate": {
                key: bool(value["holds"])
                for key, value in supplement["lazy_negative_control"].items()
            },
        },
        "defect_view_round_disclosure": defect_view_round_disclosure(),
        "matrix": {
            "tasks": list(registration["task_ids"]),
            "arms": list(ARMS),
            "repeats": REPEATS,
            "samples": SAMPLES,
            "max_api_requests": MAX_API_REQUESTS,
            "repeats_rationale": (
                "at three repeats one sample is about 11 to 13 percent of a task's strict track "
                "and therefore decides it on its own; five repeats bring the granularity to about "
                "7 percent, which is the repeat count the v16 sealed confirmation batch used"
            ),
        },
        "read_shape": {
            "shape": SHAPE,
            "rounds": ROUNDS,
            "reads_per_round": READS_PER_ROUND,
            "read_calls": READ_CALLS,
            "expected_model_calls": EXPECTED_MODEL_CALLS,
            "max_turns": MAX_TURNS,
            "max_view_lines": 60,
            "views_pairwise_disjoint": True,
        },
        "host_configuration": {
            "structured_final_answer": (
                "JSON issue/cause/fix/facts asked for in the prompt, identical for every arm, "
                "rendered by the frozen renderer, which is the final-answer path and the only "
                "validator; a rejected proposal is closed to an empty answer"
            ),
            "renderer": {
                "module": "experiments/runners/openai_agents_structured_final_v16.py",
                "sha256": sha256_of(
                    ROOT / "experiments/runners/openai_agents_structured_final_v16.py"
                ),
                "truncates": False,
                "drops_fields": False,
            },
            "retry": {
                "budget_per_sample": live.RETRY_BUDGET_PER_SAMPLE,
                "billed": True,
                "carries_host_diagnostic": True,
                "diagnostic_is_two_sided": True,
            },
            "summary_cap_per_sample": MAX_SUMMARY_CALLS_PER_SAMPLE,
            "summary_requests_are_charged_to_the_arm": True,
            "trigger_gate": {
                "soft_limit_tokens": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS,
                "policy": chain.TRIGGER_GATE_POLICY,
                "arm_scope": "plugin arm only",
            },
            "modules": modules,
            "module_set_note": (
                "the module set is the v23 round's set with the two forked configuration modules "
                "this round adds - the v24 registry, which carries the length-discipline sentence, "
                "and the v24 live contract, which carries the strengthened over-length retry - and "
                "with this runner; both parents stay in the set and stay pinned, so the pin is a "
                "superset and no module was removed"
            ),
            "v24_length_compliance_configuration": length_compliance_configuration(),
            "revision_aware_fail_closed": (
                "every module in the path must have a SHA256 recorded here and in this "
                "configuration's revision ledger; a module whose digest is absent or differs fails "
                "the gate, and the paid path refuses to start until the change is registered in a "
                "new revision"
            ),
        },
        "budget_arithmetic": math_,
        "acceptance_line": {
            "line": ACCEPTANCE_LINE,
            "decided_on": "every pair of the frozen matrix",
            "strict_track": (
                "the four conditions judged on the rendered line: required facts present, frozen "
                "regex full match, single line with the contract prefix, at most 160 characters"
            ),
            "paired_statistic": (
                "paired complete provider-token totals per (task, repeat) pair, with retries and "
                "summary tokens charged to the arm they belong to"
            ),
            "bounds_are_not_relaxed": "at most 160 characters remains an acceptance condition",
        },
        "primary_criterion": PRIMARY_CRITERION,
        "co_reported": CO_REPORTED,
        "manifest_contract": {
            "required_keys": list(MANIFEST_REQUIRED_KEYS),
            "copied_from": (
                "experiments/runners/run_openai_agents_v22_window_dose.py::MANIFEST_REQUIRED_KEYS"
            ),
            "extra_keys_this_round_adds": list(EXTRA_MANIFEST_KEYS),
            "writer": "the frozen v17 finalise_pilot_manifest, wrapped by this runner",
            "written_in_one_pass": True,
            "writer_refuses_overwrite": True,
        },
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "not_evidence_yet": (
            "both citation flags stay false until the independent audit passes; the three tasks of "
            "this batch took no part in building the host configuration, but the configuration "
            "itself - and the window K - was calibrated on other tasks, so this batch confirms the "
            "configuration and the mechanism on an untouched task set rather than establishing a "
            "general result"
        ),
        "tool_output_disclosure": chain.TOOL_OUTPUT_DISCLOSURE,
        "re_read_visibility": {
            "rule": (
                "per arm: tool_calls and model_calls are reported for every sample, so a re-read "
                "induced by an elision appears as extra tool calls and is billed"
            ),
            "expected_per_sample": {"tool_calls": READ_CALLS, "model_calls": EXPECTED_MODEL_CALLS},
        },
        "forbidden_wording": list(FORBIDDEN_WORDING),
        "required_wording": REQUIRED_WORDING,
        "report_requirements": [
            "the primary criterion: per-task and pooled paired saving in complete provider tokens, "
            "with dispersion and the count of positive pairs",
            "the symmetric matched-prefix reading in provider tokens and in context bytes",
            "the strict track and the semantic accuracy per arm, per task and pooled",
            "per-arm tool_calls and model_calls, and which arm retried in each pair",
            "the mechanism counters per task and per repeat, including the guard fallbacks",
            "the determinism of the mechanism's byte saving across the repeats of each task",
            "actual requests against the cap, dollar cost and the cache split, with the off-peak flag",
            "the acceptance verdict recomputed independently by the audit",
            "the first-attempt acceptance rate and its rejection-class decomposition per arm, "
            "which is what this round repairs",
            "which retry wording each retried sample received, from v24_retry_wording and "
            "v24_force_retry_wording in the sample row",
            f"wording: {REQUIRED_WORDING}; the v16 sealed batch's number is reported beside this "
            "one and never merged with it",
        ],
        "stop_conditions": [
            "batch mean at or below zero -> stop and report",
            "any task whose plugin strict track or semantic accuracy is below its baseline -> stop "
            "and report, do not add samples and do not change the reading",
            "any render failure, truncation or dropped field -> stop and report",
            "request cap reached -> stop, keep the partial directory, continue under a new id; "
            "--resume may never be used to bypass the budget",
            "any request failure -> stop and report",
            "audit errors non-empty -> the batch may not be cited as an effective saving",
        ],
        "never_rejudge": (
            "v12 to v15, the v16 sealed confirmation batch and its audit, the v17 and v19 to v21 "
            "rounds, every v22 group and the stopped v23 batch are untouched and unrejudged; no "
            "number from the stopped v23 batch is used anywhere in this batch"
        ),
        "source_of_record": source_of_record_hashes(),
        "source_of_record_note": (
            "the frozen finaliser re-verifies every entry of this map before it writes the batch "
            "manifest, and the paid path re-verifies it before the first request; this runner and "
            "the two forked configuration modules are pinned in host_configuration.modules as well, "
            "so the batch's own code cannot drift unnoticed"
        ),
        "discipline": {
            "manifest_written_in_one_pass": True,
            "writer_refuses_overwrite": True,
            "no_resume_to_bypass_budget": True,
            "no_git": True,
            "old_batches_untouched": True,
            "claimed_task_set_spent_once_used": True,
            "gate_must_pass_before_any_request": True,
        },
    }
    _write_once(FREEZE, payload)
    return payload


def write_revision_ledger() -> dict[str, Any]:
    previous: list[dict[str, Any]] = []
    revision = 1
    if REVISION_LEDGER.is_file():
        old = json.loads(REVISION_LEDGER.read_text(encoding="utf-8"))
        previous = list(old.get("previous_revisions") or [])
        previous.append(
            {"revision": int(old.get("revision", 1)), "modules": dict(old.get("modules") or {})}
        )
        revision = int(old.get("revision", 1)) + 1
    payload = {
        "schema": "openai_agents_v24_length_compliance_k1_module_revision_ledger",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "experiment_id": EXPERIMENT_ID,
        "window_lines": K,
        "config_module": CONFIG_MODULE,
        "api_requests_spent": 0,
        "purpose": (
            "pin every module in this K=1 configuration's path by SHA256 so the gate can fail "
            "closed on an unregistered change, exactly as the v20, v21 and v22 ledgers do"
        ),
        "rules": [
            "a module whose digest is absent from this ledger fails the gate",
            "a module whose digest differs from the digest recorded here fails the gate",
            "a change is admitted only by registering a new revision of this ledger",
        ],
        "modules": module_hashes(),
        "plan_named_modules": list(PLAN_NAMED_MODULES),
        "revision": revision,
        "previous_revisions": previous,
        "gate_class": "experiments.runners.openai_agents_v20_chain.FirstCallPassthroughGate",
        "filter_class": "openai_agents_v22_window_k1.LiteralCarrierElisionFilterK1",
    }
    REVISION_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    REVISION_LEDGER.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


# ------------------------------------------------------------------------------- the gate
def write_source_registration() -> dict[str, Any]:
    """Write this batch's own source registration, once, before the freeze.

    The task data is not re-derived here and must not be: the three tasks, their views, ranges,
    blob ids, view hashes, literals, frozen regexes and registered requests are the v18
    registration's, and this artifact records that provenance by digest.  What it adds is what is
    new in this round: the configuration the three tasks are about to be run under, the prompt
    change with its digest, the retry change with its digest, and the disclosure that the same task
    set was already run once under the v23 configuration and is exhausted after this batch.
    """

    if V24_REGISTRATION.exists():
        raise SystemExit(
            f"refusing to overwrite the existing registration {V24_REGISTRATION}: write a new one"
        )
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    discipline = {
        task_id: registry.task(task_id)["length_discipline"] for task_id in registry.task_ids()
    }
    payload = {
        "schema": "openai_agents_v24_source_registration",
        "registration_id": "V24-LENGTH-COMPLIANCE-01",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "REGISTERED",
        "written_before_any_paid_request": True,
        "api_requests_spent_when_written": 0,
        "produced_by": "experiments/runners/run_openai_agents_v24_length_compliance_k1.py",
        "plan": {
            "path": _relative(PLAN_ARTIFACT),
            "sha256": sha256_of(PLAN_ARTIFACT),
            "decided_by": "integrator",
        },
        "diagnosis": {
            "path": _relative(DIAGNOSIS),
            "sha256": sha256_of(DIAGNOSIS),
            "conclusion": "a first-answer length-compliance failure",
            "raw_answer_text_available": False,
        },
        "task_data_provenance": {
            "path": _relative(REGISTRATION),
            "sha256": sha256_of(REGISTRATION),
            "rule": (
                "the task data of this registration is that artifact's, byte for byte; this "
                "artifact re-registers the same three tasks for this batch's configuration and does "
                "not re-derive, re-cut or re-verify a view"
            ),
            "task_ids": list(registration["task_ids"]),
            "view_shape": dict(registration["view_shape"]),
        },
        "registered_task_ids": list(registry.task_ids()),
        "registered_task_count": len(registry.task_ids()),
        "per_task": {
            task_id: {
                "instance_id": registry.task(task_id)["instance_id"],
                "base_commit": registry.task(task_id)["base_commit"],
                "contract": dict(registry.task(task_id)["contract"]),
                "cause_token": registry.task(task_id)["cause_token"],
                "fix_token": registry.task(task_id)["fix_token"],
                "required_facts": list(registry.task(task_id)["contract"]["required_facts"]),
                "frozen_regex": registry.task(task_id)["contract"]["frozen_regex"],
                "minimal_feasible_line": registry.minimal_feasible_line(task_id),
                "request": registry.task(task_id)["request"],
                "problem_statement_sha256": registry.task(task_id)["problem_statement_sha256"],
                "protocol_steps": registry.protocol_steps(task_id),
                "views": [
                    {
                        "file": view["file"],
                        "first_line": view["first_line"],
                        "last_line": view["last_line"],
                        "role": view["role"],
                        "view_content_sha256": view["view_content_sha256"],
                    }
                    for view in registry.task(task_id)["views"]
                ],
                "length_discipline": discipline[task_id],
                "problems": registry.verify(task_id),
                "contract_prompt_sha256": registry.task(task_id)["contract_prompt_sha256"],
                "fingerprint": registry.fingerprint(task_id),
            }
            for task_id in registry.task_ids()
        },
        "configuration": length_compliance_configuration(),
        "matrix": {
            "tasks": list(registry.task_ids()),
            "arms": list(ARMS),
            "repeats": REPEATS,
            "samples": SAMPLES,
            "k": K,
            "read_shape": SHAPE,
            "max_api_requests": MAX_API_REQUESTS,
        },
        "prior_batch_disclosure": prior_batch_disclosure(),
        "task_set_is_exhausted_after_this_batch": True,
        "citable_as_saving": False,
        "citable_as_quality_equivalence": False,
        "not_evidence_yet": (
            "this registration carries no result; the batch's numbers exist only if the gate "
            "passes, the batch runs and the independent audit accepts it"
        ),
    }
    _write_once(V24_REGISTRATION, payload)
    return payload


def per_sample_mechanism(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in sorted(
        rows, key=lambda item: (str(item["scenario"]), int(item["repeat"]), str(item["method"]))
    ):
        entry = {
            "task": str(row["scenario"]),
            "repeat": int(row["repeat"]),
            "method": str(row["method"]),
            "model_calls": int(row.get("model_calls", 0) or 0),
            "tool_calls": int(row.get("tool_calls", 0) or 0),
            "api_request_attempts": int(row.get("api_request_attempts", 0) or 0),
            "render_accepted": bool(row.get("render_accepted")),
            "render_repair_calls": int(row.get("render_repair_calls", 0) or 0),
            "exact_duplicate_replacements": int(row.get("exact_duplicate_replacements", 0) or 0),
            "trigger_gate_calls": int(row.get("trigger_gate_calls", 0) or 0),
            "trigger_gate_triggered_calls": int(
                row.get("trigger_gate_triggered_calls", 0) or 0
            ),
            "trigger_gate_passthrough_calls": int(
                row.get("trigger_gate_passthrough_calls", 0) or 0
            ),
            "trigger_gate_first_call_passthrough_calls": int(
                row.get("trigger_gate_first_call_passthrough_calls", 0) or 0
            ),
            "trigger_gate_soft_limit_tokens": int(
                row.get("trigger_gate_soft_limit_tokens", 0) or 0
            ),
            "persisted_v21_fields": [name for name in V21_ROW_FIELDS if name in row],
        }
        for name in V21_ROW_FIELDS:
            entry[name] = int(row.get(name, 0) or 0)
        out.append(entry)
    return out


def _local_model_factory(shared: dict[str, Any]):
    def proposal(task_id: str) -> str:
        entry = registry.task(task_id)
        contract = entry["contract"]
        facts = [str(fact) for fact in contract["required_facts"]]
        return json.dumps(
            {
                "issue": str(contract["issue_value"]),
                "cause": ("cause: " + " ".join(facts[:2]) + " " + entry["cause_token"]).strip(),
                "fix": ("fix: " + " ".join(facts[2:]) + " " + entry["fix_token"]).strip(),
                "facts": facts,
            }
        )

    def build(inner: Any, request_budget: Any, **kwargs: Any):
        key = shared.get("key")
        task_id = str(key[0]) if key else registry.task_ids()[0]
        repeat = int(key[1]) if key else 0
        return live.ScriptedStubModel(
            runner17.build_case(task_id, repeat),
            request_budget=request_budget,
            final_text_value=proposal(task_id),
        )

    return build


def run_local_pilot() -> dict[str, Any]:
    """Run the full 45-sample pilot against the deterministic local model and read it back."""
    math_ = arithmetic()
    originals = {"client": base.AsyncOpenAI, "provider_model": base.OpenAIChatCompletionsModel}
    base.AsyncOpenAI = lambda **kwargs: runner17._NoNetworkClient()
    base.OpenAIChatCompletionsModel = runner17._NoProviderModel
    runner17._placeholder_key_installed()
    root: Path | None = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cleaned = ["--confirm-send-public-source", "--out", tmp]
            with v24_chain_installed():
                with v24_finalise_installed():
                    status = runner17.run_pilot(
                        cleaned,
                        inner_factory=_local_model_factory,
                        freeze_override=freeze_override(math_),
                    )
            parsed = base.build_parser().parse_args(forced_args(cleaned))
            root = Path(parsed.out) / parsed.experiment_id
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            rows = [
                json.loads(line)
                for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    finally:
        base.AsyncOpenAI = originals["client"]
        base.OpenAIChatCompletionsModel = originals["provider_model"]
    assert root is not None
    missing = [name for name in MANIFEST_REQUIRED_KEYS if name not in manifest]
    missing_extra = [name for name in EXTRA_MANIFEST_KEYS if name not in manifest]
    # The v24 additions must really be in the finalised manifest and really be on every sample
    # row, or the paid batch would run without the wording this round exists to install.
    wording_rows = [
        {
            "task": str(row.get("scenario")),
            "repeat": int(row.get("repeat", -1)),
            "method": str(row.get("method")),
            "v24_retry_wording": str(row.get("v24_retry_wording") or ""),
            "v24_force_retry_wording": bool(row.get("v24_force_retry_wording")),
            "render_repair_calls": int(row.get("render_repair_calls", 0) or 0),
        }
        for row in rows
    ]
    wording_installed = bool(wording_rows) and all(
        entry["v24_retry_wording"] for entry in wording_rows
    )
    forced_rows = [entry for entry in wording_rows if entry["v24_force_retry_wording"]]
    return {
        "pilot_status": int(status),
        "samples": len(rows),
        "manifest_required_keys": list(MANIFEST_REQUIRED_KEYS),
        "manifest_missing_keys": missing,
        "manifest_extra_keys": list(EXTRA_MANIFEST_KEYS),
        "manifest_missing_extra_keys": missing_extra,
        "manifest_required_keys_all_present": not missing and not missing_extra,
        "v16_manifest_finalized": manifest.get("v16_manifest_finalized") is True,
        "freeze_path": str(manifest.get("freeze_path", "")),
        "freeze_sha256": str(manifest.get("freeze_sha256", "")),
        "api_calls": int(manifest.get("api_calls", -1) or 0),
        "v24_retry_wording_installed_on_every_sample": wording_installed,
        "v24_forced_wording_samples": len(forced_rows),
        "v24_manifest_keys_present": all(
            name in manifest
            for name in ("v24_confirmation_freeze", "v24_length_compliance_configuration")
        ),
        "v24_prior_batch_disclosed_in_the_manifest": bool(
            (manifest.get("v24_prior_batch_disclosure") or {}).get("prior_outcome")
            == "stopped at 39 of 45 samples"
        ),
        "per_sample_wording": wording_rows,
        "per_sample": per_sample_mechanism(rows),
    }


def freeze_override(math_: dict[str, Any]) -> dict[str, Any]:
    """The freeze the batch runs under, read from the artifact written before any request."""

    if not FREEZE.is_file():
        raise SystemExit(f"the confirmation freeze is missing: {FREEZE}")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    live_modules = module_hashes()
    recorded = dict(freeze["host_configuration"]["modules"])
    drifted = sorted(
        relative for relative, digest in live_modules.items() if recorded.get(relative) != digest
    )
    if drifted:
        raise SystemExit("the freeze no longer pins the modules on disk: " + ", ".join(drifted))
    if dict(freeze["matrix"]) != {
        "tasks": list(registry.task_ids()),
        "arms": list(ARMS),
        "repeats": REPEATS,
        "samples": SAMPLES,
        "max_api_requests": MAX_API_REQUESTS,
        "repeats_rationale": freeze["matrix"]["repeats_rationale"],
    }:
        raise SystemExit("the freeze matrix differs from the implemented matrix")
    if int(freeze["budget_arithmetic"]["cap"]) != MAX_API_REQUESTS:
        raise SystemExit("the frozen cap differs from the implemented one")
    if freeze["acceptance_line"]["line"] != ACCEPTANCE_LINE:
        raise SystemExit("the frozen acceptance line differs from the implemented one")
    return {
        "path": _relative(FREEZE),
        "sha256": sha256_of(FREEZE),
        "status": str(freeze["status"]),
        "purpose": str(freeze["purpose"]),
        "matrix": dict(freeze["matrix"]),
        "acceptance_line": dict(freeze["acceptance_line"]),
        "source_of_record": dict(freeze["source_of_record"]),
        "stop_conditions": list(freeze["stop_conditions"]),
        "report_requirements": list(freeze["report_requirements"]),
        "modules": dict(recorded),
        "budget_arithmetic": dict(freeze["budget_arithmetic"]),
        "arithmetic": math_,
    }


def gate_assertion_b(per_sample: list[dict[str, Any]]) -> dict[str, Any]:
    """The trigger gate must fire on every model input after the first, per sample."""

    details = []
    ok = True
    for entry in per_sample:
        if entry["method"] != "pruner_v1":
            continue
        calls = entry["trigger_gate_calls"]
        expected_triggered = max(0, entry["model_calls"] - 1)
        triggered_ok = entry["trigger_gate_triggered_calls"] == expected_triggered
        passthrough_ok = entry["trigger_gate_passthrough_calls"] == min(1, entry["model_calls"])
        first_call_ok = entry["trigger_gate_first_call_passthrough_calls"] == min(
            1, entry["model_calls"]
        )
        calls_ok = calls == entry["model_calls"]
        entry_ok = triggered_ok and passthrough_ok and first_call_ok and calls_ok
        ok = ok and entry_ok
        details.append(
            {
                "task": entry["task"],
                "repeat": entry["repeat"],
                "model_calls": entry["model_calls"],
                "trigger_gate_calls": calls,
                "expected_triggered_calls": expected_triggered,
                "trigger_gate_triggered_calls": entry["trigger_gate_triggered_calls"],
                "trigger_gate_passthrough_calls": entry["trigger_gate_passthrough_calls"],
                "trigger_gate_first_call_passthrough_calls": entry[
                    "trigger_gate_first_call_passthrough_calls"
                ],
                "trigger_gate_soft_limit_tokens": entry["trigger_gate_soft_limit_tokens"],
                "ok": entry_ok,
            }
        )
    return {
        "check": "trigger_gate_fires_on_every_model_input_after_the_first",
        "ok": bool(ok and details),
        "samples_checked": len(details),
        "detail": details,
    }


def gate_assertion_c(per_sample: list[dict[str, Any]]) -> dict[str, Any]:
    """The mechanism must engage on unique content: per-task non-zero elision and bytes saved."""

    by_task: dict[str, dict[str, int]] = {}
    for entry in per_sample:
        if entry["method"] != "pruner_v1":
            continue
        bucket = by_task.setdefault(
            entry["task"],
            {
                "samples": 0,
                "v21_outputs_considered": 0,
                "v21_outputs_elided": 0,
                "v21_lines_elided": 0,
                "v21_bytes_saved": 0,
                "v21_literal_guard_fallbacks": 0,
                "v21_note_bytes": 0,
                "exact_duplicate_replacements": 0,
            },
        )
        bucket["samples"] += 1
        for field in (
            "v21_outputs_considered",
            "v21_outputs_elided",
            "v21_lines_elided",
            "v21_bytes_saved",
            "v21_literal_guard_fallbacks",
            "v21_note_bytes",
            "exact_duplicate_replacements",
        ):
            bucket[field] += int(entry.get(field, 0) or 0)
    failures = [
        task_id
        for task_id, bucket in sorted(by_task.items())
        if bucket["v21_outputs_elided"] <= 0 or bucket["v21_bytes_saved"] <= 0
    ]
    return {
        "check": "mechanism_engages_on_unique_content_per_task",
        "ok": bool(by_task) and not failures,
        "rule": (
            "for every task on this no-duplicate shape: v21_outputs_elided > 0 and "
            "v21_bytes_saved > 0; a zero means the wiring did not take effect and no paid run may "
            "start"
        ),
        "per_task": by_task,
        "tasks_failing": failures,
        "total_outputs_elided": sum(b["v21_outputs_elided"] for b in by_task.values()),
        "total_bytes_saved": sum(b["v21_bytes_saved"] for b in by_task.values()),
        "total_literal_guard_fallbacks": sum(
            b["v21_literal_guard_fallbacks"] for b in by_task.values()
        ),
    }


def _literal_count(items: list[Any], literal: str) -> int:
    from context_pruner.adapters.openai_agents import _jsonable_item

    text = json.dumps(
        [_jsonable_item(item) for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).lower()
    return text.count(str(literal).lower())


def gate_assertion_e() -> dict[str, Any]:
    """The literal invariant, measured on the real payloads of every task."""

    details: list[dict[str, Any]] = []
    failures: list[str] = []
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        items: list[Any] = [dict(message) for message in registry.history(task_id)]
        filter_ = chain.FILTER_CLASS(
            task=task_id,
            task_statement=registry.statement(task_id),
            hard_limit_bytes=24_000 * 4,
        )
        task_ok = True
        payload_rows: list[dict[str, Any]] = []
        if int(filter_.v21_window_lines) != K:
            failures.append(f"{task_id}: the filter instance carries K={filter_.v21_window_lines}")
        for index, _name in enumerate(entry["tool_names"]):
            call_id = f"call_{task_id}_{index}"
            items = items + [
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": entry["tool_names"][index],
                    "arguments": "{}",
                },
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": registry.source_view(task_id, index),
                },
            ]
            observed = json.loads(json.dumps(items))
            before = {
                literal: _literal_count(observed, literal) for literal in filter_.literal_texts
            }
            outcome = filter_.filter_items(observed)
            sent = list(observed) if outcome is None else list(outcome)
            after = {literal: _literal_count(sent, literal) for literal in filter_.literal_texts}
            lost = sorted(literal for literal in before if after[literal] < before[literal])
            last = dict(filter_.v21_last_call)
            note_lines = [
                note["text"]
                for note in (last.get("notes") or [])
                if isinstance(note, dict) and note.get("text")
            ]
            note_carries = sorted(
                {
                    literal
                    for note in note_lines
                    for literal in filter_.literal_texts
                    if literal.lower() in str(note).lower()
                }
            )
            row_ok = not lost and not note_carries
            task_ok = task_ok and row_ok
            payload_rows.append(
                {
                    "call_index": index,
                    "status": str(last.get("status", "")),
                    "base_bytes": int(last.get("base_bytes", 0) or 0),
                    "after_bytes": int(last.get("after_bytes", 0) or 0),
                    "net_bytes_saved": int(last.get("net_bytes_saved", 0) or 0),
                    "note_bytes": int(last.get("note_bytes", 0) or 0),
                    "outputs_considered": int(last.get("outputs_considered", 0) or 0),
                    "outputs_elided": int(last.get("outputs_elided", 0) or 0),
                    "lines_elided": int(last.get("lines_elided", 0) or 0),
                    "literal_counts_before": before,
                    "literal_counts_after": after,
                    "literals_lost": lost,
                    "note_lines": len(note_lines),
                    "note_lines_carrying_a_literal": note_carries,
                    "note_form_wellformed": all(
                        mechanism.is_note_line(note) for note in note_lines
                    ),
                    "ok": row_ok,
                }
            )
        if not task_ok:
            failures.append(task_id)
        details.append(
            {
                "task": task_id,
                "window_lines": int(getattr(filter_, "v21_window_lines", -1)),
                "payloads": payload_rows,
                "ok": task_ok,
            }
        )
    return {
        "check": "literal_preservation_invariant_holds_on_the_real_payloads",
        "ok": bool(details) and not failures,
        "rule": (
            "for every task and every model input: every registered literal's occurrence count "
            "after elision is >= its count before, and no note line written by the mechanism "
            "carries any registered literal"
        ),
        "window_lines": mechanism.WINDOW_LINES,
        "tasks_failing": failures,
        "detail": details,
    }


def gate_assertion_d(registered: dict[str, str] | None) -> dict[str, Any]:
    """Every module in the path is pinned, and an unregistered digest is a hard failure."""

    current = module_hashes()
    registered = dict(registered or {})
    unregistered = [
        relative for relative, _digest in sorted(current.items()) if relative not in registered
    ]
    drifted = [
        relative
        for relative, digest in sorted(current.items())
        if relative in registered and registered[relative] != digest
    ]
    return {
        "check": "every_module_in_the_path_is_pinned_with_revision_aware_fail_closed",
        "ok": not unregistered and not drifted,
        "modules": current,
        "plan_named_modules": {relative: current[relative] for relative in PLAN_NAMED_MODULES},
        "revision_ledger_entries": sorted(registered),
        "unregistered_modules": unregistered,
        "drifted_modules": drifted,
        "module_count": len(current),
    }


def length_compliance_configuration() -> dict[str, Any]:
    """The v24 configuration this batch runs under, with the two fork reports and the wording.

    This is the block that makes the batch self-describing: the prompt change, the retry change,
    the digests of the two forked modules and of their untouched parents, and the evidence that
    both changes are arm neutral.
    """

    registry_report = registry.fork_block()
    live_report = dict(live.fork_report())
    wording = dict(live.wording_report())
    return {
        "schema": "openai_agents_v24_length_compliance_configuration",
        "registry_fork": registry_report,
        "live_contract_fork": live_report,
        "arm_neutrality": {
            "first_prompt": {
                "where": "the registry's contract prompt, which the frozen runner passes to every "
                "arm as the case's final_contract",
                "evidence": registry.arm_neutral_prompt_evidence(),
                "sentence": registry.LENGTH_DISCIPLINE_SENTENCE,
                "sentence_sha256": registry.fork_block()["length_discipline_sentence_sha256"],
                "budget_sentence_template": registry.LENGTH_DISCIPLINE_BUDGET_SENTENCE,
                "why_it_cannot_vary_by_arm": (
                    "the arm is a property of the model input filter; it is not an argument of the "
                    "function that builds the contract prompt, and no code path in this batch "
                    "rewrites the agent instructions after they are assembled"
                ),
            },
            "retry": {
                "where": "the v24 live contract's RETRY_INSTRUCTION and its over_160_chars "
                "diagnostic",
                "evidence": wording,
                "why_it_cannot_vary_by_arm": (
                    "both are module constants of the live contract; every sample of every arm runs "
                    "through the same StructuredFinalModel subclass, which reads them from the "
                    "module, and no arm supplies a different one"
                ),
            },
        },
        "what_is_unchanged": [
            "the 160-character bound stays an acceptance condition of the strict track",
            "the literal rule R1 to R7",
            "the required literals of all three tasks",
            "the frozen regexes",
            "the renderer module, imported unchanged",
            "the read shape: 3 rounds, one disjoint view per round, K=1",
            "the trigger gate and its threshold",
            "the retry budget of one metered request per sample",
            "the two-sided diagnostic",
        ],
        "what_changed": [
            "the registry's contract prompt gains one length-discipline text, appended after the "
            "existing sentences",
            "the live contract's over_160_chars retry wording gains the JSON-only rule, the compact "
            "target and the repetition of the bound, and keeps every sentence it had",
        ],
        "carried_from_a_void_batch": prior_batch_disclosure(),
    }


def prior_batch_disclosure() -> dict[str, Any]:
    """The disclosure the ruling requires in the freeze and in the report, in its own words."""

    return {
        "prior_batch": STOPPED_BATCH,
        "prior_configuration": "V18-SEALED-WINDOW-K1-01",
        "prior_outcome": "stopped at 39 of 45 samples",
        "prior_failure": (
            "the three sealed tasks are the same three tasks, and under the v23 configuration task "
            "django-15572 (autoreload_template_changed) failed rendering on all fifteen of its "
            "samples across all three arms; the batch reached 39 of its 45 samples before the hard "
            "stop"
        ),
        "this_batch_is_the_second_run_of_the_same_task_set": True,
        "under_this_configuration": "v24 length-compliant prompt",
        "task_set_exhaustion": (
            "after this batch the three-task set is exhausted: it is never run again and never "
            "replaced by another id"
        ),
        "prior_batch_is_not_citable": True,
        "no_number_from_the_prior_batch_is_used_here": True,
        "prior_audit": (
            "the independent audit of the stopped batch returned complete=false, "
            "usable_as_saving=false and fifteen errors, so no number from it is cited anywhere"
        ),
        "diagnosis_path": "integrations/openai_agents/V24_LENGTH_COMPLIANCE_DIAGNOSIS_20261010.json",
        "diagnosis_conclusion": (
            "the failure was a first-answer length-compliance failure rather than a contract that "
            "forces a long answer"
        ),
        "the_prompt_change_is_the_repair": (
            "the configuration change raises first-answer compliance for every task and every arm, "
            "so no task's evidence bar was lowered and no task was dropped after being seen to fail"
        ),
    }


def _retry_wording_check() -> dict[str, Any]:
    """The over-length retry must be strengthened and must still state both sides."""

    wording = live.wording_report()
    two_sided = dict(wording["two_sided_requirements"])
    return {
        "check": "the_over_length_retry_wording_is_strengthened_and_keeps_the_v17_trade_off",
        "ok": bool(
            wording["retry_instruction_sha256"] != wording["v17_retry_instruction_sha256"]
            and wording["over_160_diagnostic_sha256"]
            != wording["v17_over_160_diagnostic_sha256"]
            and wording["the_v17_over_160_text_is_kept_whole"]
            and wording["every_class_but_over_160_is_byte_identical_to_v17"]
            and wording["the_retry_instruction_states_the_json_only_rule"]
            and wording["the_retry_instruction_states_a_compact_target"]
            and two_sided["the_over_160_retry_states_all_of_them"]
        ),
        "retry_instruction_chars": wording["retry_instruction_chars"],
        "retry_instruction_sha256": wording["retry_instruction_sha256"],
        "v17_retry_instruction_sha256": wording["v17_retry_instruction_sha256"],
        "over_160_diagnostic_chars": wording["over_160_diagnostic_chars"],
        "over_160_diagnostic_sha256": wording["over_160_diagnostic_sha256"],
        "v17_over_160_diagnostic_sha256": wording["v17_over_160_diagnostic_sha256"],
        "the_v17_over_160_text_is_kept_whole": wording["the_v17_over_160_text_is_kept_whole"],
        "every_class_but_over_160_is_byte_identical_to_v17": wording[
            "every_class_but_over_160_is_byte_identical_to_v17"
        ],
        "two_sided_requirements": two_sided,
        "bound_still_160": live.MAX_CHARS == 160,
        "retry_budget_still_one": live.RETRY_BUDGET_PER_SAMPLE == 1,
    }


def _first_prompt_discipline_check() -> dict[str, Any]:
    """The discipline sentence is in the first prompt of every arm, verbatim and identically.

    The prompt the model sees on its first turn is assembled by the frozen runner from the fixed
    sentence and the case's contract prompt.  This function rebuilds that prompt for each arm from
    the same modules, checks that the discipline text is present in each, and checks that the three
    assembled prompts are identical: the arms differ only in the model input items, and the frozen
    runner's own prompt replacement removes the words "at most 160 characters" from the fixed
    sentence, which is exactly why the discipline text has to be in the contract prompt.
    """

    prompts: dict[str, str] = {}
    for arm in ARMS:
        case = runner17.build_case(registry.task_ids()[0], 0)
        prompts[arm] = _assemble_first_prompt(str(case.final_contract))
    discipline = str(registry.task(registry.task_ids()[0])["length_discipline"]["text"])
    per_arm = {
        arm: {
            "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "discipline_text_present": discipline in prompt,
            "discipline_text_verbatim_at_the_end_of_the_contract_prompt": prompt.rstrip().endswith(
                discipline.rstrip()
            ),
            "states_the_bound_is_a_rejection": "rejects the ENTIRE answer" in prompt,
            "gives_a_working_target": str(
                registry.WORKING_TARGET_CAUSE_PLUS_FIX_CHARS
            )
            in prompt,
            "keeps_the_two_sided_literal_rule": "must still appear verbatim" in prompt,
        }
        for arm, prompt in prompts.items()
    }
    one_digest = len({entry["prompt_sha256"] for entry in per_arm.values()}) == 1
    return {
        "check": "the_length_discipline_sentence_is_identical_in_every_arms_first_prompt",
        "ok": bool(
            len(per_arm) == len(ARMS)
            and all(
                entry["discipline_text_present"]
                and entry["states_the_bound_is_a_rejection"]
                and entry["gives_a_working_target"]
                and entry["keeps_the_two_sided_literal_rule"]
                for entry in per_arm.values()
            )
            and one_digest
        ),
        "arms": list(ARMS),
        "per_arm": per_arm,
        "the_three_arms_get_one_prompt_digest": one_digest,
        "discipline_sentence": registry.LENGTH_DISCIPLINE_SENTENCE,
        "discipline_text_chars": len(discipline),
        "discipline_text_sha256": hashlib.sha256(discipline.encode("utf-8")).hexdigest(),
        "the_frozen_prompt_replacement_no_longer_carries_the_bound": (
            "the fixed sentence's words \"at most 160 characters\" are replaced before the prompt is "
            "sent, so the discipline text is the part of the first prompt that states the bound as a "
            "rejection criterion"
        ),
    }


def _assemble_first_prompt(final_contract: str) -> str:
    """The instruction text the frozen runner builds for the first turn, from the frozen source."""

    template = (
        "Follow the latest user request exactly. Use the supplied tools in the requested order. "
        "Never invent tool results. The final response must be exactly one line, begin with RESULT, "
        "contain at most 160 characters, and follow this contract: {contract}. Replace "
        "angle-bracket fields with tool values. Do not discuss old notes, trust, safety, reasoning, "
        "or possible next steps."
    )
    adapted, applied, unapplied = live.adapt_instructions(
        template.replace("{contract}", str(final_contract))
    )
    if unapplied:
        raise RuntimeError(f"the frozen prompt replacement did not apply: {unapplied}")
    return adapted + " " + live.STRUCTURED_INSTRUCTION


def literal_invariant_special_tests() -> dict[str, Any]:
    """The two paths of a literal that appears exactly once, the note rule, and the recency rule.

    The registered literals of django-15572 (``template_changed``) and django-15569
    (``_unregister_lookup``) each appear exactly once in their views, so they are the sharpest
    available cases:

    * **path one, the carrier is protected.**  The output that carries the literal is elided
      elsewhere and the single occurrence survives; the counts are read before and after.
    * **path two, the guard refuses.**  If the payload as it would be sent had lost that
      occurrence, the v5 lineage's own ``_carrier_loss`` - the function the mechanism itself calls -
      reports it, which is what makes the mechanism send the payload verbatim instead.
    * **the newest tool group is never touched**, byte for byte, and its plan entry says why.
    * **no note line can carry a literal**, on the same payloads.
    """

    from experiments.runners import openai_agents_evidence_safe_retention_v5 as v5

    details: dict[str, Any] = {}
    ok = True
    registration = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        single = sorted(
            str(row["literal"])
            for row in registration["tasks"][task_id]["literals"]
            if int(row["verbatim_hits"]) == 1
        )
        rarest = sorted(
            registration["tasks"][task_id]["literals"],
            key=lambda row: (int(row["verbatim_hits"]), str(row["literal"])),
        )
        filter_ = chain.FILTER_CLASS(
            task=task_id,
            task_statement=registry.statement(task_id),
            hard_limit_bytes=24_000 * 4,
        )
        literals = [str(text).lower() for text in filter_.literal_texts]
        # A payload with exactly one occurrence of a chosen literal, in an output that is not the
        # newest tool group, so the elision really has to protect it.  Two of the three tasks have a
        # literal that occurs exactly once in their registered views; for a task that has none, the
        # rarest registered literal is used and the difference is recorded.
        chosen = single[0] if single else (
            str(rarest[0]["literal"]) if rarest else sorted({str(t) for t in filter_.literal_texts})[0]
        )
        carrier_output = "\n".join(
            [f"{index}: filler line {index}" for index in range(12)]
            + [f"12: def {chosen}(self, value):"]
            + [f"{index}: filler line {index}" for index in range(13, 30)]
        )
        filler_output = "\n".join(f"{index}: plain line {index}" for index in range(20))
        items = [
            {"type": "function_call_output", "call_id": "older", "output": carrier_output},
            {"type": "function_call_output", "call_id": "middle", "output": filler_output},
            {"type": "function_call_output", "call_id": "newest", "output": filler_output},
        ]
        before = {literal: _literal_count(items, literal) for literal in filter_.literal_texts}
        # Path 1: the elision touches the older and middle outputs, never the newest group.
        elided_old = filter_._elide_text(carrier_output, literals)
        text_after, record_after = elided_old
        after = (
            _literal_count([{"type": "function_call_output", "output": text_after}], chosen)
            if text_after is not None
            else _literal_count(
                [{"type": "function_call_output", "output": carrier_output}], chosen
            )
        )
        note_lines = [
            line
            for line in str(text_after or "").splitlines()
            if mechanism.is_note_line(line)
        ]
        note_carries = sorted(
            {literal for line in note_lines for literal in literals if literal in line.lower()}
        )
        # Path 2: the mechanism's own guard sees a lost occurrence.
        keeps = [{"type": "function_call_output", "call_id": "older", "output": carrier_output}]
        loses = [
            {
                "type": "function_call_output",
                "call_id": "older",
                "output": carrier_output.replace(chosen, "something_else"),
            }
        ]
        guard_sees_loss = bool(filter_._carrier_loss(keeps, loses))
        guard_accepts_keep = filter_._carrier_loss(keeps, keeps) == []
        # The recency rule, on the real view texts: the reads are built as call/output pairs, in
        # registered order, so the last pair is the most recent tool group.  The mechanism must
        # copy that group through byte for byte and say so in its own plan.
        real_items: list[Any] = []
        for index in range(len(entry["views"])):
            call_id = f"real_{task_id}_{index}"
            real_items.append(
                {
                    "type": "function_call",
                    "call_id": call_id,
                    "name": entry["tool_names"][index],
                    "arguments": "{}",
                }
            )
            real_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": registry.source_view(task_id, index),
                }
            )
        real_result, plan, _note_bytes = filter_._elide(real_items)
        newest_call_id = f"real_{task_id}_{len(entry['views']) - 1}"
        newest_input = registry.source_view(task_id, len(entry["views"]) - 1)
        newest_output = next(
            (
                str(item.get("output", ""))
                for item in real_result
                if isinstance(item, dict)
                and item.get("type") == "function_call_output"
                and item.get("call_id") == newest_call_id
            ),
            "",
        )
        newest_note = next(
            (
                note
                for note in plan["notes"]
                if isinstance(note, dict) and note.get("call_id") == newest_call_id
            ),
            {},
        )
        newest_kept_verbatim = (
            newest_output == newest_input
            and str(newest_note.get("reason", "")) == "most_recent_tool_group"
            and newest_note.get("elided") is False
        )
        real_notes = [
            note["text"]
            for note in plan["notes"]
            if isinstance(note, dict) and note.get("text")
        ]
        real_note_carries = sorted(
            {literal for line in real_notes for literal in literals if literal in line.lower()}
        )
        entry_ok = (
            after >= 1
            and int(before.get(chosen, 0)) >= 1
            and not note_carries
            and guard_sees_loss
            and guard_accepts_keep
            and newest_kept_verbatim
            and not real_note_carries
            and int(plan["recent_group_items_kept"]) >= 1
        )
        ok = ok and entry_ok
        details[task_id] = {
            "single_occurrence_literals": single,
            "has_a_literal_that_occurs_exactly_once": bool(single),
            "literal_under_test": chosen,
            "literal_occurrence_counts": {
                str(row["literal"]): int(row["verbatim_hits"])
                for row in registration["tasks"][task_id]["literals"]
            },
            "path_one_carrier_protected": {
                "elided": text_after is not None,
                "lines_elided": int(record_after.get("lines_elided", 0)),
                "occurrences_before_in_the_output": _literal_count(
                    [{"type": "function_call_output", "output": carrier_output}], chosen
                ),
                "occurrences_after_in_the_output": after,
                "note_lines": len(note_lines),
                "note_lines_carrying_a_literal": note_carries,
            },
            "path_two_guard_refuses_a_lost_occurrence": {
                "carrier_loss_on_the_keep": filter_._carrier_loss(keeps, keeps),
                "carrier_loss_on_the_losing_payload": filter_._carrier_loss(keeps, loses),
                "guard_sees_the_loss": guard_sees_loss,
            },
            "newest_group_kept_verbatim": {
                "newest_call_id": newest_call_id,
                "recent_group_items_kept": int(plan["recent_group_items_kept"]),
                "newest_kept_verbatim": newest_kept_verbatim,
                "newest_output_identical_to_the_read": newest_output == newest_input,
                "newest_plan_entry": newest_note,
            },
            "real_payload_notes_carrying_a_literal": real_note_carries,
            "real_payload_outputs_elided": int(plan["outputs_elided"]),
            "v5_guard_is_the_frozen_one": v5.SelectiveRetentionFilter._carrier_loss.__module__,
            "ok": entry_ok,
        }
    tasks_with_a_single_occurrence = sorted(
        task_id
        for task_id, detail in details.items()
        if detail["has_a_literal_that_occurs_exactly_once"]
    )
    return {
        "check": "the_single_occurrence_paths_the_note_rule_and_the_recency_rule_hold",
        "ok": ok and len(tasks_with_a_single_occurrence) >= 2,
        "rule": (
            "every task must keep the occurrence of the literal under test, must show the v5 "
            "lineage guard refusing a payload that loses it, must keep the newest tool group byte "
            "for byte and must write no note that carries a literal; at least two tasks must do it "
            "for a literal that occurs exactly once in their registered views"
        ),
        "tasks_with_a_literal_that_occurs_exactly_once": tasks_with_a_single_occurrence,
        "detail": details,
    }


def _invariant_probe_rejects_a_lost_literal() -> bool:
    """Assertion (e) must be able to fail: a payload that loses a literal is rejected.

    The payload is built so that it really carries a registered literal - some registered views
    contain none, and a probe on an empty carrier would pass for the wrong reason - and the losing
    copy has the literal removed.  Both calls go to the v5 lineage's own ``_carrier_loss``, which is
    the function the mechanism itself uses, so the probe tests the guard rather than a second
    implementation of it.
    """

    task_id = registry.task_ids()[0]
    filter_ = mechanism.LiteralCarrierElisionFilter(
        task=task_id,
        task_statement=registry.statement(task_id),
        hard_limit_bytes=24_000 * 4,
    )
    literal = str(filter_.literal_texts[0])
    carrier = "\n".join(
        f"{index}: line {index}" if index != 3 else f"3: def {literal}(self):"
        for index in range(8)
    )
    before = [{"type": "function_call_output", "call_id": "c1", "output": carrier}]
    keeps = [{"type": "function_call_output", "call_id": "c1", "output": carrier}]
    loses = [
        {
            "type": "function_call_output",
            "call_id": "c1",
            "output": carrier.replace(literal, "something_else"),
        }
    ]
    return (
        bool(literal)
        and "def " in carrier
        and filter_._carrier_loss(before, keeps) == []
        and bool(filter_._carrier_loss(before, loses))
    )


def _effective_window_matches_k() -> dict[str, Any]:
    config = dict(getattr(chain.CONFIG_MODULE, "CONFIG", {}) or {})
    config_window = int(config.get("k", -1))
    declaration = str(config.get("effective_window_declaration", ""))
    declared = int(config.get("declared_window_value", -1))
    class_window = int(getattr(chain.FILTER_CLASS, "WINDOW_LINES", -1))
    return {
        "ok": (
            config_window == K
            and class_window == K
            and declared == K
            and "self.v21_window_lines" in declaration
        ),
        "k_in_force": K,
        "config_window": config_window,
        "declared_window_value": declared,
        "effective_window_declaration": declaration,
        "filter_class": getattr(chain.FILTER_CLASS, "__name__", ""),
        "filter_class_window": class_window,
        "config_module": config.get("config_module"),
        "frozen_mechanism_default": mechanism.WINDOW_LINES,
    }


def k_is_hard_coded_and_k2_is_refused() -> dict[str, Any]:
    """K is written down here and every other value is refused, not interpolated."""

    refused_by_configuration = False
    detail = ""
    try:
        window.configuration(2)
    except ValueError as error:
        refused_by_configuration = True
        detail = str(error)
    refused_by_import = False
    try:
        __import__("experiments.runners.openai_agents_v22_window_k2", fromlist=["CONFIG"])
    except ModuleNotFoundError:
        refused_by_import = True
    refused_by_selector = False
    selector_error = ""
    try:
        select_window(2)
    except SystemExit as error:
        refused_by_selector = True
        selector_error = str(error)
    return {
        "check": "the_window_is_pinned_to_k1_and_k2_is_refused",
        "ok": (
            K == 1
            and refused_by_configuration
            and refused_by_import
            and refused_by_selector
            and int(getattr(chain.FILTER_CLASS, "WINDOW_LINES", -1)) == 1
            and mechanism.WINDOW_LINES == 3
        ),
        "k_written_in_this_module": K,
        "authorised_windows": list(window.AUTHORISED_WINDOWS),
        "k2_refused_by_the_configuration_module": refused_by_configuration,
        "k2_configuration_error": detail[:200],
        "k2_refused_by_import": refused_by_import,
        "k2_refused_by_this_runners_selector": refused_by_selector,
        "k2_selector_error": selector_error[:200],
        "filter_class_window": int(getattr(chain.FILTER_CLASS, "WINDOW_LINES", -1)),
        "frozen_mechanism_default_window": mechanism.WINDOW_LINES,
    }


def inherited_checks() -> list[dict[str, Any]]:
    """The shape, diagnostic, strict-track and cap checks the earlier rounds carried."""

    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    steps = plan_steps(registry.task_ids()[0])
    text = "\n".join(steps)
    record(
        "protocol_matches_the_shape",
        f"{ROUNDS} rounds" in text
        and f"{READ_CALLS} read calls" in text
        and all(f"round {index + 1}:" in text for index in range(ROUNDS)),
        {"rounds": ROUNDS, "read_calls": READ_CALLS},
    )
    record(
        "one_read_per_turn_rule_matches_the_batching",
        "do not issue more than one read in a turn" in text,
        {"reads_per_round": READS_PER_ROUND},
    )
    record(
        "no_duplicate_and_no_merge_rules_present",
        "never read a registered view more often" in text
        and "do not merge two rounds into one turn" in text,
    )
    record(
        "old_shape_wording_absent",
        all(probe not in text for probe in ("three calls of a round", "a single read on its own")),
    )
    case = runner17.build_case(registry.task_ids()[0], 0)
    record(
        "case_shape_matches_the_plan",
        int(case.expected_model_calls) == EXPECTED_MODEL_CALLS
        and len(case.expected_tool_names) == READ_CALLS,
        {
            "expected_model_calls": int(case.expected_model_calls),
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
        "the_over_length_retry_wording_is_strengthened_and_keeps_the_v17_trade_off",
        _retry_wording_check()["ok"],
        _retry_wording_check(),
    )
    record(
        "every_other_retry_class_is_still_byte_identical_to_v16",
        all(
            live16.diagnostic_for(reason, **fixed) == live.diagnostic_for(reason, **fixed)
            for reason in live17.DIAGNOSTIC_TEXT
            if reason != "over_160_chars"
        ),
        {
            "classes_compared": sorted(
                reason for reason in live17.DIAGNOSTIC_TEXT if reason != "over_160_chars"
            ),
            "why": (
                "the v24 fork intentionally changes one class, over_160_chars, and no other; the "
                "unstrengthened classes must still be the v16 wording the frozen runner pins"
            ),
        },
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
        {"issue": issue, "cause": "a cause", "fix": "a fix", "facts": ["__hash__", "__eq__"]}
    )
    verdict = render(missing, expected_issue=issue)
    record(
        "declared_literal_missing_from_the_line_still_rejected",
        (not verdict.accepted) and verdict.reason == "declared_fact_missing_from_rendered_answer",
        {"reason": verdict.reason},
    )
    long_answer = json.dumps(
        {"issue": issue, "cause": ("a " * 120).strip(), "fix": "a fix", "facts": ["a"]}
    )
    long_verdict = render(long_answer, expected_issue=issue)
    record(
        "over_160_still_rejected",
        long_verdict.reason == "over_160_chars",
        {"reason": long_verdict.reason},
    )
    math_ = arithmetic()
    record(
        "cap_arithmetic_written_into_the_gate_and_at_least_1_3x",
        math_["at_least_1_3"] and math_["margin"] >= 1.3,
        math_,
    )
    record(
        "trigger_gate_threshold_is_the_pre_registered_zero",
        chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS == 0,
        {"soft_limit_tokens_setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS},
    )
    window_check = _effective_window_matches_k()
    record("protection_window_is_the_k_pre_registered_for_this_configuration", window_check["ok"], window_check)
    record(
        "the_mechanism_module_is_untouched_and_its_default_window_is_still_three",
        mechanism.WINDOW_LINES == 3 and int(window_check.get("config_window", -1)) == K,
        {
            "frozen_v21_default_window": mechanism.WINDOW_LINES,
            "config_window": window_check.get("config_window"),
            "config_module": window_check.get("config_module"),
            "k_in_force": K,
        },
    )
    record(
        "the_k_assertion_and_its_negative_control",
        k_is_hard_coded_and_k2_is_refused()["ok"],
        k_is_hard_coded_and_k2_is_refused(),
    )
    record(
        "elision_counters_are_persisted_in_the_sample_rows",
        all(name in chain.PERSISTED_EXTRA_FIELDS for name in V21_ROW_FIELDS),
        {"persisted": list(V21_ROW_FIELDS)},
    )
    record(
        "the_tool_documentation_is_unchanged_and_the_disclosure_is_written",
        "verbatim" in registry.task(registry.task_ids()[0])["tool_docs"][0]
        and "tool_output_disclosure" in chain.mechanism_block()
        and "elided n lines first-last" in chain.TOOL_OUTPUT_DISCLOSURE
        and "identical in all three arms" in chain.TOOL_OUTPUT_DISCLOSURE
        and "tool_output_disclosure" in MANIFEST_REQUIRED_KEYS,
        {"disclosure": chain.TOOL_OUTPUT_DISCLOSURE},
    )
    record(
        "no_silent_omission_is_possible",
        mechanism.NOTE_FORM == "[elided {count} lines {first}-{last}]"
        and mechanism.is_note_line(mechanism.note_line(4, 8, 11))
        and mechanism.note_line(4, 8, 11) == "[elided 4 lines 8-11]",
        {"note_form": mechanism.NOTE_FORM, "example": mechanism.note_line(4, 8, 11)},
    )
    record(
        "the_paid_path_installs_the_same_contexts_as_the_rehearsal",
        _run_paid_installs_both_contexts(),
        {
            "paid_wiring": "with v24_chain_installed(): with v24_finalise_installed(): run_pilot(...)",
            "why": (
                "the rehearsal and the paid path must install the same context managers; a green "
                "gate over a parallel rehearsal is what shipped an incomplete manifest once"
            ),
        },
    )
    record(
        "the_paid_path_preserves_the_runner_bytes_and_matches_the_pin",
        _paid_path_pins_this_runner_or_delegates_it()["ok"],
        _paid_path_pins_this_runner_or_delegates_it(),
    )
    record(
        "the_pre_registered_wording_is_written_and_the_forbidden_wording_is_recorded",
        REQUIRED_WORDING == "under this host configuration"
        and set(FORBIDDEN_WORDING)
        == {
            "compression alone saved X percent",
            "the four criteria are met",
            "the retry tax is gone",
            "the plugin saves about 12.5 percent of context",
        }
        and all(term in MANIFEST_REQUIRED_KEYS for term in ("mechanism", "tool_output_disclosure")),
        {"required_wording": REQUIRED_WORDING, "forbidden_wording": list(FORBIDDEN_WORDING)},
    )
    record(
        "both_citation_flags_start_false",
        (lambda f: f.get("citable_as_saving") is False and f.get("citable_as_quality_equivalence") is False)(
            json.loads(FREEZE.read_text(encoding="utf-8")) if FREEZE.is_file() else {}
        ),
    )
    record(
        "the_frozen_batch_is_not_merged_with_the_v16_sealed_batch",
        "never merged" in PRIMARY_CRITERION["never_merged_with"]
        and "12.54" in PRIMARY_CRITERION["never_merged_with"],
    )
    return checks


def _run_paid_installs_both_contexts() -> bool:
    source = inspect.getsource(run_paid)
    body = source.split("preserve_runner_bytes()", 1)[-1]
    return (
        "with v24_chain_installed():" in body
        and "with v24_finalise_installed():" in body
        and body.index("with v24_chain_installed():")
        < body.index("with v24_finalise_installed():")
    )


def _paid_path_preserves_runner_bytes() -> bool:
    source = inspect.getsource(run_paid)
    if "preservation = preserve_runner_bytes()" not in source:
        return False
    if 'if not preservation["matches_ledger"]' not in source:
        return False
    return bool(preserve_runner_bytes()["matches_ledger"])


def _paid_path_pins_this_runner_or_delegates_it() -> dict[str, Any]:
    """The paid pass must pin this runner's bytes, directly or through a delegate that does.

    A small batch may not run ``run_paid`` itself - the recalibration driver calls it, so that both
    batches share one paid path - and in that case the preservation this assertion protects is the
    delegate's.  The check follows the delegate rather than weakening the claim: it requires either
    this module's own paid path to preserve and compare the runner's bytes, or a caller in the
    repository that reaches this function and therefore inherits the same preservation.
    """

    own = _paid_path_preserves_runner_bytes()
    delegates: list[str] = []
    if not own:
        for path in sorted((ROOT / "experiments/runners").glob("*.py")):
            if path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8")
            if "run_openai_agents_v24_length_compliance_k1" not in text:
                continue
            if "run_paid" in text:
                delegates.append(
                    str(path.relative_to(ROOT)).replace("\\", "/")
                )
    return {
        "own_paid_path_preserves_the_runner_bytes": own,
        "delegating_callers_that_reach_this_paid_path": delegates,
        "paying_caller": _relative(Path(__file__)),
        "ok": bool(own or delegates),
        "rule": (
            "the bytes of the module that runs the paid batch are preserved outside the repository "
            "and compared with the revision ledger before the first request; a batch that delegates "
            "to this module's paid path inherits that step"
        ),
    }


def _paid_path_preservation_detail() -> dict[str, Any]:
    detail = preserve_runner_bytes()
    detail["gate_reads_it_before_any_request"] = True
    return detail


def gate(revision_ledger: dict[str, str] | None = None) -> dict[str, Any]:
    math_ = arithmetic()
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.json"
        plan_status = run_plan(plan_path, [])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        record(
            "plan_is_zero_api_for_this_task_set_and_its_cap",
            plan_status == 0
            and plan.get("api_calls") == 0
            and plan["planned_requests"]["worst_case_fits_the_cap"]
            and plan["case_shape"]["expected_model_calls"] == EXPECTED_MODEL_CALLS,
            {
                "api_calls": plan.get("api_calls"),
                "samples": plan["planned_requests"]["samples"],
                "cap": plan["planned_requests"]["max_api_requests"],
                "worst_case": plan["planned_requests"]["pre_registered_worst_case"],
            },
        )
        stub_path = Path(tmp) / "stub.json"
        stub_status = run_stub(stub_path, [])
        stub = json.loads(stub_path.read_text(encoding="utf-8"))
        record(
            "the_frozen_host_contract_still_lands_on_its_frozen_verdicts",
            bool(stub["frozen_control_replay"]["ok"]),
            {**stub["frozen_control_replay"], "stub_mode_status": int(stub_status)},
        )
        record(
            "the_registered_tasks_pass_the_control_set_through_the_real_wiring",
            stub_status == 0
            and stub["registered_task_controls"]["ok"]
            and stub["registered_task_controls"]["controls"] >= 24,
            {
                "controls": stub["registered_task_controls"]["controls"],
                "failed": stub["registered_task_controls"]["failed"],
            },
        )
        record(
            "the_stub_is_zero_api",
            stub.get("api_calls") == 0,
            {"api_calls": stub.get("api_calls")},
        )
        local = run_local_pilot()

    record(
        "a_local_pilot_produces_a_finalised_manifest_with_every_pre_registered_key",
        local["pilot_status"] == 0
        and local["manifest_required_keys_all_present"]
        and local["v16_manifest_finalized"] is True
        and local["samples"] == SAMPLES,
        {
            "pilot_status": local["pilot_status"],
            "samples": local["samples"],
            "manifest_missing_keys": local["manifest_missing_keys"],
            "manifest_missing_extra_keys": local["manifest_missing_extra_keys"],
            "v16_manifest_finalized": local["v16_manifest_finalized"],
            "freeze_path": local["freeze_path"],
            "freeze_sha256": local["freeze_sha256"],
            "api_calls": local["api_calls"],
        },
    )
    record(
        "the_v24_wording_is_installed_and_the_prior_batch_is_disclosed_in_the_manifest",
        local["v24_retry_wording_installed_on_every_sample"]
        and local["v24_manifest_keys_present"]
        and local["v24_prior_batch_disclosed_in_the_manifest"],
        {
            "samples_with_a_wording_label": sum(
                1 for entry in local["per_sample_wording"] if entry["v24_retry_wording"]
            ),
            "of_samples": len(local["per_sample_wording"]),
            "samples_whose_retry_used_the_strengthened_wording": local[
                "v24_forced_wording_samples"
            ],
            "v24_manifest_keys_present": local["v24_manifest_keys_present"],
            "prior_batch_disclosed": local["v24_prior_batch_disclosed_in_the_manifest"],
            "why": (
                "the local model's proposals are short, so this rehearsal exercises the installed "
                "wording path and the manifest keys rather than the retry itself; the retry's own "
                "delivery is proven by the control set, which drives an over-length proposal "
                "through the same wrapper"
            ),
        },
    )
    assertion_b = gate_assertion_b(local["per_sample"])
    checks.append({"check": assertion_b["check"], "ok": assertion_b["ok"], "detail": assertion_b})
    assertion_c = gate_assertion_c(local["per_sample"])
    checks.append({"check": assertion_c["check"], "ok": assertion_c["ok"], "detail": assertion_c})
    assertion_e = gate_assertion_e()
    checks.append({"check": assertion_e["check"], "ok": assertion_e["ok"], "detail": assertion_e})
    assertion_d = gate_assertion_d(revision_ledger)
    checks.append({"check": assertion_d["check"], "ok": assertion_d["ok"], "detail": assertion_d})
    specials = literal_invariant_special_tests()
    checks.append({"check": specials["check"], "ok": specials["ok"], "detail": specials})
    checks.extend(inherited_checks())
    # The two assertions this round adds, on top of the ones the v23 round carried: the prompt
    # change is really in every arm's first prompt, and the retry change is really the strengthened
    # two-sided wording.  Both are recorded in the gate artifact with their own digests.
    prompt_check = _first_prompt_discipline_check()
    checks.append(
        {"check": prompt_check["check"], "ok": prompt_check["ok"], "detail": prompt_check}
    )
    wording_check = _retry_wording_check()
    checks.append(
        {"check": wording_check["check"], "ok": wording_check["ok"], "detail": wording_check}
    )
    # Negative controls for the two new assertions: a prompt without the sentence must fail the
    # check, and the v17 wording must fail it as well, so neither can pass for free.
    stripped_contract = runner17.build_case(registry.task_ids()[0], 0).final_contract
    without_discipline = str(stripped_contract).replace(
        str(registry.task(registry.task_ids()[0])["length_discipline"]["text"]), ""
    )
    stripped_prompt = _assemble_first_prompt(without_discipline)
    negative_prompt_ok = bool(
        str(registry.LENGTH_DISCIPLINE_SENTENCE) not in stripped_prompt
        and len(stripped_prompt) < len(_assemble_first_prompt(str(stripped_contract)))
    )
    negative_wording_ok = bool(
        wording_check["v17_retry_instruction_sha256"]
        != wording_check["retry_instruction_sha256"]
        and wording_check["v17_over_160_diagnostic_sha256"]
        != wording_check["over_160_diagnostic_sha256"]
    )
    record(
        "the_two_new_assertions_can_fail",
        negative_prompt_ok and negative_wording_ok,
        {
            "a_prompt_without_the_sentence_fails_the_prompt_assertion": negative_prompt_ok,
            "the_v17_wording_fails_the_retry_assertion": negative_wording_ok,
            "stripped_prompt_chars": len(stripped_prompt),
            "full_prompt_chars": len(_assemble_first_prompt(str(stripped_contract))),
        },
    )

    failing_c = gate_assertion_c(
        [
            {
                "task": "synthetic",
                "repeat": 0,
                "method": "pruner_v1",
                "v21_outputs_elided": 0,
                "v21_bytes_saved": 0,
            }
        ]
    )
    failing_b = gate_assertion_b(
        [
            {
                "task": "synthetic",
                "repeat": 0,
                "method": "pruner_v1",
                "model_calls": 4,
                "trigger_gate_calls": 4,
                "trigger_gate_triggered_calls": 1,
                "trigger_gate_passthrough_calls": 3,
                "trigger_gate_first_call_passthrough_calls": 1,
                "trigger_gate_soft_limit_tokens": 5435,
            }
        ]
    )
    failing_d = gate_assertion_d({})
    failing_e = _invariant_probe_rejects_a_lost_literal()
    record(
        "the_gate_assertions_can_fail",
        (not failing_c["ok"]) and (not failing_b["ok"]) and (not failing_d["ok"]) and failing_e,
        {
            "assertion_c_rejects_a_zero_saving_sample": not failing_c["ok"],
            "assertion_b_rejects_a_passthrough_sample": not failing_b["ok"],
            "assertion_d_rejects_an_unregistered_module_set": not failing_d["ok"],
            "assertion_e_rejects_an_output_that_loses_a_literal": failing_e,
        },
    )

    ok = all(entry["ok"] for entry in checks)
    return {
        "schema": "openai_agents_v24_length_compliance_k1_zero_api_gate",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "gate",
        "api_calls": 0,
        "experiment_id": EXPERIMENT_ID,
        "window_lines": K,
        "config_module": CONFIG_MODULE,
        "shape": SHAPE,
        "arithmetic": math_,
        "checks": checks,
        "ok": ok,
        "failed_checks": [entry["check"] for entry in checks if not entry["ok"]],
        "hard_assertions": {
            "a_finalise_really_runs": (
                local["pilot_status"] == 0
                and local["manifest_required_keys_all_present"]
                and local["v16_manifest_finalized"] is True
                and local["samples"] == SAMPLES
            ),
            "b_trigger_gate_fires_every_call": assertion_b["ok"],
            "c_mechanism_engages_on_unique_content": assertion_c["ok"],
            "d_every_module_pinned": assertion_d["ok"],
            "e_literal_invariant_holds_on_real_payloads": assertion_e["ok"],
            "f_single_occurrence_paths_note_rule_and_recency": specials["ok"],
            "g_length_discipline_sentence_identical_in_every_arms_first_prompt": prompt_check["ok"],
            "h_over_length_retry_wording_strengthened_and_two_sided": wording_check["ok"],
        },
        "per_sample_mechanism": local["per_sample"],
        "v21_counters_per_task": assertion_c["per_task"],
        "literal_invariant_per_task": assertion_e["detail"],
        "registration": {
            "path": _relative(REGISTRATION),
            "sha256": sha256_of(REGISTRATION),
            "task_ids": list(registry.task_ids()),
        },
        "v24_source_registration": {
            "path": _relative(V24_REGISTRATION),
            "sha256": sha256_of(V24_REGISTRATION) if V24_REGISTRATION.is_file() else None,
            "present": V24_REGISTRATION.is_file(),
        },
        "freeze": {
            "path": _relative(FREEZE),
            "sha256": sha256_of(FREEZE),
        },
        "defect_view_round_disclosure": defect_view_round_disclosure(),
        "length_compliance_configuration": length_compliance_configuration(),
        "prior_batch_disclosure": prior_batch_disclosure(),
        "the_two_new_checks": {
            "g_first_prompt_discipline_is_identical_in_every_arm": prompt_check,
            "h_over_length_retry_wording": wording_check,
        },
        "module_hashes": module_hashes(),
        "mechanism": chain.mechanism_block(),
        "paid_run_allowed": bool(ok),
        "paid_run_allowed_rule": (
            "true only when every check passes; a zero elision counter, an unregistered module "
            "digest or an incomplete finalised manifest writes false here and no paid run may start"
        ),
    }


# ------------------------------------------------------------------------------- paid path
def preserve_runner_bytes() -> dict[str, Any]:
    own = Path(__file__).resolve()
    digest = sha256_of(own)
    recorded: dict[str, str] = {}
    if REVISION_LEDGER.is_file():
        recorded = {
            str(key): str(value)
            for key, value in (
                json.loads(REVISION_LEDGER.read_text(encoding="utf-8")).get("modules") or {}
            ).items()
        }
    relative = _relative(own)
    target = PRESERVATION_DIR / "run_openai_agents_v24_length_compliance_k1.py"
    try:
        PRESERVATION_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(own.read_bytes())
        preserved = True
    except OSError:
        preserved = False
    return {
        "module": relative,
        "digest": digest,
        "pinned_digest": recorded.get(relative, ""),
        "matches_ledger": recorded.get(relative) == digest,
        "preserved_to": str(target),
        "preserved": preserved,
        "k": K,
    }


def _record_preservation(args: list[str], preservation: dict[str, Any]) -> None:
    parsed = base.build_parser().parse_args(forced_args(args))
    root = Path(parsed.out) / parsed.experiment_id
    if not root.is_dir():
        return
    record = {
        "schema": "openai_agents_v24_runner_preservation",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "window_lines": K,
        "experiment_id": parsed.experiment_id,
        **preservation,
        "why": (
            "a batch must be attachable to the code revision that produced it, so the runner bytes "
            "are preserved outside the repository and their digest is compared with the ledger "
            "before the first request"
        ),
    }
    (root / "runner-bytes.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_paid(args: list[str]) -> int:
    """The paid path.  It refuses to start unless the recorded gate allows it."""

    if K != 1:
        raise SystemExit(f"refusing to run: this batch is K=1 only, not K={K}")
    if not GATE_OUT.is_file():
        raise SystemExit(
            f"refusing to send a paid request: the zero-API gate artifact is missing ({GATE_OUT})"
        )
    recorded = json.loads(GATE_OUT.read_text(encoding="utf-8"))
    if recorded.get("paid_run_allowed") is not True:
        raise SystemExit(
            "refusing to send a paid request: the gate recorded paid_run_allowed="
            f"{recorded.get('paid_run_allowed')!r}"
        )
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    math_ = configure()
    preservation = preserve_runner_bytes()
    if not preservation["matches_ledger"]:
        raise SystemExit(
            "refusing to send a paid request: the runner on disk does not match the digest the "
            f"gate pinned ({preservation['digest']} not in the ledger for this module)"
        )
    with v24_chain_installed():
        with v24_finalise_installed():
            status = runner17.run_pilot(args, freeze_override=freeze_override(math_))
    _record_preservation(args, preservation)
    return status


# ------------------------------------------------------------------------------------ main
def load_revision_ledger(path: Path | None = None) -> dict[str, str]:
    target = Path(path) if path is not None else REVISION_LEDGER
    if not target.is_file():
        return {}
    record = json.loads(target.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in (record.get("modules") or {}).items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--write-freeze", action="store_true")
    parser.add_argument("--register-revision", action="store_true")
    parser.add_argument("--register-source", action="store_true")
    parser.add_argument("--artifact", default="")
    parser.add_argument("--mode", choices=("plan", "stub", "gate"), default="")
    parser.add_argument("--confirm-send-public-source", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--revision-ledger", default="")
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    with sealed_v24_registry_installed():
        config = select_window(K)
        if args.register_source:
            payload = write_source_registration()
            print(f"source registration: {V24_REGISTRATION}")
            print(
                f"tasks={payload['registered_task_count']} samples={payload['matrix']['samples']} "
                f"prior_batch_disclosed={payload['prior_batch_disclosure']['prior_outcome']}"
            )
            return 0
        if args.write_freeze:
            payload = write_freeze()
            print(f"freeze written: {FREEZE}")
            print(
                f"matrix={payload['matrix']['samples']} samples cap={payload['matrix']['max_api_requests']} "
                f"worst={payload['budget_arithmetic']['worst_case_requests']} "
                f"margin={payload['budget_arithmetic']['margin']} modules={len(payload['host_configuration']['modules'])}"
            )
            return 0
        math_ = configure()
        print(
            f"window K={K} config={config['config_module']} schema={config['mechanism_schema']} "
            f"repeats={REPEATS} samples={SAMPLES} cap={MAX_API_REQUESTS} experiment={EXPERIMENT_ID}",
            file=sys.stderr,
        )
        if args.register_revision:
            payload = write_revision_ledger()
            print(f"revision ledger: {REVISION_LEDGER}")
            print(f"revision={payload['revision']} modules={len(payload['modules'])}")
            return 0
        mode = args.mode or (
            "plan" if args.plan else "stub" if args.stub else "gate" if args.gate else ""
        )
        if mode == "plan":
            artifact = Path(args.artifact) if args.artifact else PLAN_OUT
            return run_plan(artifact, rest)
        if mode == "stub":
            artifact = Path(args.artifact) if args.artifact else STUB_OUT
            return run_stub(artifact, rest)
        if args.pilot:
            paid_args = list(rest)
            if args.confirm_send_public_source and "--confirm-send-public-source" not in paid_args:
                paid_args.append("--confirm-send-public-source")
            return run_paid(paid_args)
        ledger = load_revision_ledger(
            Path(args.revision_ledger) if args.revision_ledger else None
        )
        result = gate(ledger)
        _write_once(GATE_OUT, result)
        print(json.dumps({"shape": SHAPE, "k": K, "ok": result["ok"]}, ensure_ascii=False))
        for name, value in result["hard_assertions"].items():
            print(f"  hard assertion {name}: {value}")
        for entry in result["checks"]:
            if not entry["ok"]:
                print(f"  FAILED: {entry['check']}")
        print(f"  paid_run_allowed: {result['paid_run_allowed']}")
        print(f"  gate artifact: {GATE_OUT}")
        print(f"  worst_case_requests={math_['worst_case_requests']} cap={MAX_API_REQUESTS}")
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
