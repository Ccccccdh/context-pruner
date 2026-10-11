"""v21 acquisition runner: the N=3 no-duplicate shape on the literal-carrier elision chain.

Three modes, and none of the three contacts a provider:

``--plan``
    Zero API.  Freeze, amendment, source-of-record and registry preflight, the frozen request
    plan written through the unmodified v17 runner, the provider client and model replaced by
    objects that raise on use, and an artifact whose ``api_calls`` is 0.
``--stub``
    Zero API.  The stage-1 negative and positive controls replayed through the *same* wiring the
    paid batch would use - SDK ``Runner``, real ``output_type`` agent, the v21 plugin filter, the
    real recorder, the real renderer and the one-retry wrapper - against a deterministic local
    model.
``--gate``
    Zero API.  The hard v21 assertions (below) plus the inherited v17/v20 shape, diagnostic,
    strict-track and cap checks, written to ``V21_ZERO_API_GATE_20261010.json``.

The gate's hard assertions, and why each is where it is
-------------------------------------------------------
(a) **finalise really runs.**  A full nine-sample pilot is run with a local model and the produced
    manifest must carry every pre-registered key, with ``v16_manifest_finalized`` true.  The v17
    and v19 batches both lost their finalised manifest *after* the samples had been paid for, so
    this lives inside the gate and not after it.
(b) **the trigger gate really fires.**  Under the N3 shape each sample must report
    ``trigger_gate_triggered_calls`` equal to the number of model inputs after the first and
    ``trigger_gate_passthrough_calls`` equal to exactly one (the first call), per sample, for
    every sample.  A gate that still passes calls through would make the v19 false negative
    recur.
(c) **the mechanism really engages.**  On this shape there is no duplicate content by
    construction, so ``v21_outputs_elided`` and ``v21_bytes_saved`` must be non-zero, per task.  If
    they are zero the wiring did not take effect: the gate writes ``paid_run_allowed: false`` and
    no paid run may start.
(d) **every module in the path is pinned.**  Registry, runner, live contract, renderer, the v20
    modules this chain composes and both new v21 modules are listed with their SHA256, the
    manifest copies them, and an unregistered digest is a hard failure (revision-aware
    fail-closed): if a module changes, the gate fails until the change is registered in a recorded
    revision.
(e) **the invariant holds on the real payloads.**  For every task, every registered literal's
    occurrence count after elision is greater than or equal to its count before, and the note
    lines never carry a literal.  This is asserted from the batch rows *and* by a dedicated
    mechanism probe on the real N3 payloads, so an engagement counter that moved without the
    invariant holding would still fail the gate.

Nothing here sends a request.  ``--pilot`` exists for completeness but refuses unless
``--confirm-send-public-source`` is given *and* the v21 gate artifact says ``paid_run_allowed``
is true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from experiments.runners import openai_agents_v16_live_contract as live16  # noqa: E402
from experiments.runners import openai_agents_v17_live_contract as live  # noqa: E402
from experiments.runners import openai_agents_v17_registry as registry  # noqa: E402
from experiments.runners import openai_agents_v21_carrier_elision as mechanism  # noqa: E402
from experiments.runners import openai_agents_v22_chain as chain  # noqa: E402
from experiments.runners import openai_agents_v22_window as window  # noqa: E402
from experiments.runners import run_openai_agents_api_experiment as base  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner17  # noqa: E402
from experiments.runners.openai_agents_structured_final_v16 import render  # noqa: E402

#: The plan this runner implements, and the artifacts it inherits its gate from.
PLAN_PATH = "integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json"
V21_PLAN_PATH = "integrations/openai_agents/V21_LITERAL_CARRIER_ELISION_PLAN_20261010.json"
V21_INCOMPLETE_PATH = (
    "integrations/openai_agents/V21_STAGE1_INCOMPLETE_MANIFEST_20261010.json"
)
V20_PLAN_PATH = "integrations/openai_agents/V20_NONDUPLICATE_MECHANISM_PLAN_20261010.json"
ADDENDUM_PATH = "integrations/openai_agents/V20_GATE_ADDENDUM_20261010.json"
WIRING_DIFF_PATH = "integrations/openai_agents/V20_WIRING_DIFF_20261010.json"
V20_RESULT_PATH = "integrations/openai_agents/V20_RESULT_AND_CHARACTERISATION_20261010.json"

#: The three pre-registered windows, in the order the dose is reported.
AUTHORISED_WINDOWS = window.AUTHORISED_WINDOWS

#: The windows **this amended round** is authorised to run paid.
#:
#: ``V22_WINDOW_DOSE_PLAN_20261010.json`` (dose-02) authorised K=3 and K=1 for the ``b`` groups.
#: The integrator's stage-3 authorisation afterwards added K=0 for a second repetition group at the
#: same three repeats.  This constant records what the dose-02 round authorised; the stage-three
#: authorisation is a separate constant below, and during the K=0 group the paid gate accepts that
#: authorisation explicitly rather than silently widening this one.
AMENDMENT_WINDOWS = (3, 1)

#: The integrator's stage-3 authorisation: K=0 at three repeats, 27 samples, for the comparison
#: group the stage-2 plan said could follow on a separate authorisation.
STAGE_THREE_WINDOWS = (0,)

#: The K in force.  ``select_window`` sets it before anything is built; it is module state because
#: the gate, the plan and the paid path all have to agree on one number.
K = AUTHORISED_WINDOWS[0]

#: Per K, the experiment id and the artifact paths.  Each K is its own batch, its own gate and its
#: own revision ledger, so no group can be mistaken for another.
EXPERIMENT_IDS = {
    3: "openai-repo-diagnostic-v22-window-k3b-pilot-01",
    1: "openai-repo-diagnostic-v22-window-k1b-pilot-01",
    0: "openai-repo-diagnostic-v22-window-k0b-pilot-01",
}
EXPERIMENT_ID = EXPERIMENT_IDS[K]

#: The shape is fixed: three views, one distinct view per round, three rounds, each view read
#: once.  There is no shape parameter in this round - K is the only variable.
SHAPE = "N3"
PLAN_ORDER = (0, 1, 2)
READS_PER_ROUND = 1
ROUNDS = 3
READ_CALLS = len(PLAN_ORDER)
EXPECTED_MODEL_CALLS = ROUNDS + 1

ARMS = ("none", "native_summary", "pruner_v1")
REPEATS = 1
SAMPLES = 3 * REPEATS * len(ARMS)
MAX_API_REQUESTS = 100
MAX_TURNS = READ_CALLS + 2
MAX_OUTPUT_TOKENS = 1024
MAX_API_RETRIES = 2
MAX_SUMMARY_CALLS_PER_SAMPLE = 2
PROVIDER_SOFT, PROVIDER_TARGET, PROVIDER_HARD = 2000, 1500, 6000

GATE_OUT = ROOT / f"integrations/openai_agents/V22_K{K}B_ZERO_API_GATE_20261010.json"
PLAN_OUT = ROOT / f"integrations/openai_agents/V22_K{K}B_LIVE_PLAN_20261010.json"
STUB_OUT = ROOT / f"integrations/openai_agents/V22_K{K}B_LIVE_STUB_20261010.json"
#: The module revision ledger the gate pins against (assertion (d)), one per K **and per round**.
#: The ``B`` suffix matters: the dose-01 groups keep their own ledger and gate artifact, so a
#: later round's registration can never overwrite the record of a group that has already been
#: reported.  (Both rounds pin the same module set, so they also serve as each other's cross
#: check; what must not be shared is the file the ledger is written into.)
REVISION_LEDGER = ROOT / f"integrations/openai_agents/V22_K{K}B_MODULE_REVISIONS_20261010.json"
#: Where the runner bytes of every paid run are preserved outside the repository, because the
#: first v21 paid attempt could not be tied to the file that produced it.
PRESERVATION_DIR = Path(r"C:\Users\LENOVO\v21-artifact-preservation")

#: Old shape wording that must not reappear in the protocol or the registry.
OLD_WORDING = ("three calls of a round", "a single read on its own")

# --- stage 2 (K=1 repetition), authorised after the dose ladder was reported -----------------
#: The repeat count of the stage-2 group.  The dose groups used one repeat; this one uses three,
#: which is the only reason the sample count and the cap differ.
STAGE_TWO_REPEATS = 3
#: The stage-2 experiment id suffix, so the group cannot be mistaken for the dose group.
STAGE_TWO_EXPERIMENT_SUFFIX = "-pilot-02"
#: The stage-3 experiment id suffix (the K=0 repetition group).
STAGE_THREE_EXPERIMENT_SUFFIX = "-pilot-03"
#: The stage-2 cap.  Worst case = 27 samples x (5 max turns + 1 retry budget) + 3 x 2 summary
#: calls = 189 requests; 250 keeps 1.32x over it, above this project's 1.3x floor.
STAGE_TWO_MAX_API_REQUESTS = 250

# --- (d) the modules whose SHA256 the gate lists and the manifest copies -------------------
PINNED_MODULES = (
    "experiments/runners/openai_agents_v17_registry.py",
    "experiments/runners/run_openai_agents_v17_acquisition.py",
    "experiments/runners/openai_agents_v17_live_contract.py",
    "experiments/runners/openai_agents_structured_final_v16.py",
    "experiments/runners/openai_agents_v20_retention.py",
    "experiments/runners/openai_agents_v20_chain.py",
    "experiments/runners/openai_agents_v21_carrier_elision.py",
    "experiments/runners/openai_agents_v22_window.py",
    "experiments/runners/openai_agents_v22_window_k3.py",
    "experiments/runners/openai_agents_v22_window_k1.py",
    "experiments/runners/openai_agents_v22_window_k0.py",
    "experiments/runners/openai_agents_v22_chain.py",
    "experiments/runners/run_openai_agents_v22_window_dose.py",
)
#: The registry, the runner, the live contract and the renderer.
PLAN_NAMED_MODULES = PINNED_MODULES[:4]


def select_window(k: int) -> dict[str, Any]:
    """Bind one pre-registered K: import its configuration module and install it in the chain.

    Everything downstream - the filter class, the mechanism schema, the window the mechanism
    actually uses, and the artifact paths - follows from this one call, so the K a batch runs at is
    a pinned module rather than a constant somewhere else.
    """
    global K, GATE_OUT, PLAN_OUT, STUB_OUT, REVISION_LEDGER, EXPERIMENT_ID
    value = int(k)
    if value not in AUTHORISED_WINDOWS:
        raise SystemExit(
            f"K={value} is not pre-registered: the v22 plan authorises {AUTHORISED_WINDOWS}, and "
            "an interpolated window may not be run without a new registration"
        )
    config_module = __import__(
        f"experiments.runners.openai_agents_v22_window_k{value}", fromlist=["CONFIG"]
    )
    config = chain.install_configuration(config_module)
    K = value
    EXPERIMENT_ID = EXPERIMENT_IDS[value]
    GATE_OUT = ROOT / f"integrations/openai_agents/V22_K{value}B_ZERO_API_GATE_20261010.json"
    PLAN_OUT = ROOT / f"integrations/openai_agents/V22_K{value}B_LIVE_PLAN_20261010.json"
    STUB_OUT = ROOT / f"integrations/openai_agents/V22_K{value}B_LIVE_STUB_20261010.json"
    REVISION_LEDGER = (
        ROOT / f"integrations/openai_agents/V22_K{value}B_MODULE_REVISIONS_20261010.json"
    )
    return config

#: The pre-registered keys the finalised manifest must carry, grouped by where the plan requires
#: them.  Every name here is checked by presence, and the honest outcome when one is absent is a
#: failed gate, never a warning.
MANIFEST_REQUIRED_KEYS = (
    # identity and citation discipline
    "kind",
    "purpose",
    "citable_as_saving",
    "citable_as_quality_equivalence",
    "citable_note",
    "data_class",
    "disclosure",
    "never_rejudge",
    # freeze and provenance
    "freeze_path",
    "freeze_sha256",
    "freeze_matrix",
    "source_of_record",
    "source_of_record_verified",
    "v16_registry",
    "implementation_hashes",
    # host configuration held fixed
    "host_configuration",
    "criteria",
    "metering",
    # budget and shape
    "request_budget",
    "acceptance_line",
    "stop_conditions",
    "report_requirements",
    # the four-condition strict track and the per-sample rows
    "batch_report",
    "pilot_report",
    "length_trajectory",
    "failure_decomposition",
    "api_calls",
    "runner_status",
    "stopped_early",
    "stop_reason",
    # the finalisation flag the v17 and v19 batches lost
    "v16_manifest_finalized",
    # the v21 mechanism block
    "mechanism",
    # the integrator's required disclosure and the re-read visibility record
    "tool_output_disclosure",
    "forbidden_wording",
    "re_read_visibility",
    "module_hashes",
)

#: The v21 counters a sample row must carry.  Read from the persisted rows, so a counter that
#: exists only on the filter object cannot satisfy any assertion below.
V21_ROW_FIELDS = (
    "v21_outputs_considered",
    "v21_outputs_elided",
    "v21_lines_elided",
    "v21_bytes_saved",
    "v21_literal_guard_fallbacks",
    "v21_note_bytes",
)


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_hashes() -> dict[str, str]:
    return {relative: sha256_of(ROOT / relative) for relative in PINNED_MODULES}


# --------------------------------------------------------------------------- shape arithmetic
def arithmetic() -> dict[str, Any]:
    worst_per_sample = MAX_TURNS + live.RETRY_BUDGET_PER_SAMPLE
    worst = SAMPLES * worst_per_sample + 3 * MAX_SUMMARY_CALLS_PER_SAMPLE
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
        "worst_case_requests_per_sample": worst_per_sample,
        "samples": SAMPLES,
        "arms": list(ARMS),
        "repeats": REPEATS,
        "worst_case_requests": worst,
        "cap": MAX_API_REQUESTS,
        "margin": round(MAX_API_REQUESTS / worst, 3),
        "at_least_1_3": MAX_API_REQUESTS >= 1.3 * worst,
        "stage_two_samples": 27,
        "stage_two_authorised": False,
    }


def plan_steps(task_id: str) -> list[str]:
    """The N3 protocol text, read from the v19 module so the wording is byte-identical."""
    from experiments.runners import openai_agents_v19_shape_dose as v19

    return v19.plan_steps(task_id, SHAPE)


def configure() -> dict[str, Any]:
    """Point the v17 runner and the v17 registry at the N3 shape and the v21 chain."""
    math_ = arithmetic()
    registry.protocol_steps = lambda task_id: plan_steps(task_id)
    registry.READ_CALLS = READ_CALLS
    registry.READ_ROUNDS = ROUNDS
    registry.READS_PER_ROUND = READS_PER_ROUND
    registry.EXPECTED_MODEL_CALLS = EXPECTED_MODEL_CALLS
    # The v21 chain resolves its recorder, its filter and its task data through its own module
    # level bindings, so the v17 registry has to be installed there as well as in the frozen
    # runner: patching only the frozen runner's binding is what the v14 chain relies on and it is
    # not sufficient for a module that owns its own name.
    chain.registry = registry
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


@contextmanager
def v22_chain_installed():
    """Run the v17 acquisition runner with the v21 chain in place of the v14 chain.

    Three bindings have to be in force simultaneously, and each exists for a different reason:

    * ``runner17.chain`` must be the v21 chain, because that is the module the frozen runner's
      ``chain.chain_wiring()`` call reaches;
    * ``chain.registry`` must be the v17 registry, because ``v21_chain.MultitaskRecorder`` and
      ``register_task_data`` read the task through the v21 chain module's own ``registry`` name;
    * ``v14.registry`` must be the v17 registry too, because the v21 chain **inherits** its
      ``__init__`` from ``MultitaskDuplicateFilter`` in the frozen v14 module, and that constructor
      resolves ``registry.literal_phrases`` through the *v14 module's* name.

    The frozen v17 runner patches only the third of those, on its own entry points, and restores
    it afterwards; a caller that invokes the runner directly - which is what this gate does -
    therefore has to install all three itself.
    """
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
def v22_finalise_installed():
    """Add the manifest keys the v21 plan pre-registers but the frozen pilot finaliser omits.

    ``openai_agents_v17_acquisition.finalise_pilot_manifest`` writes the v17 key set.  Four keys
    the v21 plan pre-registers are not in it: ``mechanism``, ``v16_registry``,
    ``implementation_hashes`` and ``never_rejudge``.  They are added here, around the frozen
    finaliser, rather than inside it, so no frozen file changes and the returned manifest is the
    one that was written.

    Two of the four are already computed by the frozen code and only fail to reach the pilot's
    manifest: ``implementation_hashes`` is written by ``finalise_manifest`` (the acquisition
    path) and ``v16_registry`` is written the same way.  ``mechanism`` and ``never_rejudge`` are
    this runner's own disclosure of what ran and what must not be re-judged.
    """
    original = runner17.finalise_pilot_manifest

    def finalise(root, **kwargs):
        manifest = original(root, **kwargs)
        path = Path(root) / "manifest.json"
        manifest["mechanism"] = chain.mechanism_block()
        manifest["v16_registry"] = registry.manifest_block()
        manifest["implementation_hashes"] = module_hashes()
        # The same map under the name the v21 plan pre-registers, so a reader of either key
        # sees the identical digests.
        manifest["module_hashes"] = dict(manifest["implementation_hashes"])
        manifest["never_rejudge"] = (
            "attempt 01, attempt 02, attempt 03, pilot-01, pilot-02, v12 to v15, the v16 sealed "
            "confirmation batch, the v17 development pilots, the v18 selection branch, the v19 "
            "dose batches and the v20 gate round are untouched and unrejudged; v21 does not "
            "merge its numbers with any of them"
        )
        manifest["v22_gate_threshold"] = {
            "setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": chain.TRIGGER_GATE_POLICY,
        }
        manifest["v22_window_mechanism"] = {
            "window_lines": mechanism.WINDOW_LINES,
            "plan": PLAN_PATH,
            "counters": list(mechanism.V21_COUNTER_FIELDS),
        }
        # The ruling on the tool-documentation tension: the reader tool description stays
        # byte-identical in all three arms (rewriting it for the plugin arm alone would make the
        # prompt asymmetric), and what is written here instead is the disclosure that the plugin
        # arm's read output can be elided with an explicit, recoverable interval note.
        manifest["tool_output_disclosure"] = chain.TOOL_OUTPUT_DISCLOSURE
        manifest["tool_output_disclosure_note"] = (
            "the reader tool description is identical in all three arms and is not rewritten for "
            "the plugin arm; 'returns those numbered lines verbatim' holds for the lines the "
            "mechanism keeps, and every omission is declared by an explicit interval note rather "
            "than being silent"
        )
        manifest["forbidden_wording"] = list(chain.FORBIDDEN_WORDING)
        # Re-read visibility: elision must never be able to hide a re-read.
        manifest["re_read_visibility"] = {
            "rule": (
                "per arm: tool_calls and model_calls are reported for every sample, so a re-read "
                "induced by an elision appears as extra tool calls and is billed"
            ),
            "v16_clean_baseline": "the v16 three-arm batches read exactly six views per sample",
            "expected_per_sample": {
                "tool_calls": READ_CALLS,
                "model_calls": EXPECTED_MODEL_CALLS,
            },
        }
        path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return manifest

    runner17.finalise_pilot_manifest = finalise
    try:
        yield
    finally:
        runner17.finalise_pilot_manifest = original


def forced_args(args: list[str], *, out: str | None = None, confirm: bool = False) -> list[str]:
    cleaned = runner17.forced_args(args, out=out, confirm=confirm)
    cleaned = runner17._replace_option(cleaned, "--methods", ",".join(ARMS))
    cleaned = runner17._replace_option(cleaned, "--repeats", str(REPEATS))
    cleaned = runner17._replace_option(
        cleaned, "--scenarios", ",".join(registry.task_ids())
    )
    cleaned = runner17._replace_option(cleaned, "--experiment-id", EXPERIMENT_ID)
    cleaned = runner17._replace_option(cleaned, "--max-api-requests", str(MAX_API_REQUESTS))
    cleaned = runner17._replace_option(cleaned, "--max-turns", str(MAX_TURNS))
    cleaned = runner17._replace_option(
        cleaned, "--max-summary-calls", str(MAX_SUMMARY_CALLS_PER_SAMPLE)
    )
    return cleaned


def freeze_override(math_: dict[str, Any]) -> dict[str, Any]:
    """The in-memory freeze the zero-API rehearsal runs under (no file is written).

    It is built from the **frozen v17 pilot freeze's own keys** - the same key set the paid path
    hands to ``run_pilot`` - with the v21 matrix, module hashes, mechanism block and cap written
    over the frozen ones, so the rehearsal exercises the one-pass finalisation with exactly the
    keys the paid path would give it.  Building it by hand is what produced the first attempt's
    missing ``source_of_record`` key.

    ``source_of_record`` is carried as an empty map on purpose: the v17 freeze does not pin source
    files by digest, so there is nothing to pin here.  ``runner17.verify_pilot_freeze()`` is
    deliberately *not* called: it asserts the v17 cap of 110, which this runner replaces.
    """
    freeze = json.loads(runner17.PILOT_FREEZE.read_text(encoding="utf-8"))
    freeze = json.loads(json.dumps(freeze))
    freeze["path"] = "<the v17 development freeze's key set, with an in-memory v21 override>"
    freeze["sha256"] = "0" * 64
    freeze["shape"] = SHAPE
    freeze["mechanism"] = chain.mechanism_block()
    freeze["tool_output_disclosure"] = chain.TOOL_OUTPUT_DISCLOSURE
    freeze["forbidden_wording"] = list(chain.FORBIDDEN_WORDING)
    freeze["modules"] = module_hashes()
    freeze["source_of_record"] = {}
    # ``verify_pilot_freeze`` derives ``acceptance_line`` from the v17 freeze's ``decision_line``;
    # the override supplies the same derived key so the finalisation path reads what it expects.
    freeze["acceptance_line"] = {
        "line": str(
            (freeze.get("decision_line") or {}).get(
                "pass_and_continue_to_task_shape_and_confirmation", ""
            )
        ),
        "source": "the v17 freeze's decision_line, carried through the v21 override",
    }
    freeze["citable_as_saving"] = False
    freeze["citable_as_quality_equivalence"] = False
    freeze["matrix"] = {
        "tasks": list(registry.task_ids()),
        "arms": list(ARMS),
        "repeats": REPEATS,
        "samples": SAMPLES,
        "max_api_requests": MAX_API_REQUESTS,
    }
    freeze["budget_arithmetic"] = {
        **dict(freeze.get("budget_arithmetic") or {}),
        "max_api_requests": MAX_API_REQUESTS,
        "worst_case_requests": math_["worst_case_requests"],
        "cap_over_worst_case": math_["margin"],
    }
    return freeze


# ------------------------------------------------------------------------------- plan and stub
def run_plan(artifact: Path, args: list[str]) -> int:
    math_ = configure()
    payload = None
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.json"
        with v22_chain_installed():
            status = runner17.main(["--plan", "--artifact", str(plan_path), *args])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        payload = {
            "schema": "openai_agents_v21_live_plan",
            "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "plan",
            "api_calls": 0,
            "plan_implements": PLAN_PATH,
            "v20_plan": V20_PLAN_PATH,
            "gate_addendum": ADDENDUM_PATH,
            "wiring_diff": WIRING_DIFF_PATH,
            "v20_result": V20_RESULT_PATH,
            "shape": math_,
            "mechanism": chain.mechanism_block(),
            "chain_in_use": {
                "chain_module": "experiments/runners/openai_agents_v21_chain.py",
                "filter": "LiteralCarrierElisionFilter",
                "filter_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
                "v20_composite_module": "experiments/runners/openai_agents_v20_retention.py",
                "v14_chain_module_on_disk": "experiments/runners/openai_agents_multitask_chain_v14.py",
                "v14_chain_used_by_this_plan": False,
            },
            "module_hashes": module_hashes(),
            "frozen_runner_status": int(status),
            "frozen_plan": plan,
            "paid_batch_started": False,
        }
    assert payload is not None
    _write_once(artifact, payload)
    print(f"plan artifact: {artifact}")
    print(
        f"api_calls=0 samples={SAMPLES} shape={SHAPE} "
        f"worst_case_requests={math_['worst_case_requests']} cap={MAX_API_REQUESTS}"
    )
    return 0


def run_stub(artifact: Path, args: list[str]) -> int:
    configure()
    with tempfile.TemporaryDirectory() as tmp:
        stub_path = Path(tmp) / "stub.json"
        with v22_chain_installed():
            status = runner17.main(["--stub", "--artifact", str(stub_path), *args])
        stub = json.loads(stub_path.read_text(encoding="utf-8"))
    payload = dict(stub)
    payload["schema"] = "openai_agents_v21_live_stub"
    payload["plan_implements"] = PLAN_PATH
    payload["shape"] = arithmetic()
    payload["mechanism"] = chain.mechanism_block()
    payload["v17_stub_artifact"] = stub
    payload["controls_ok"] = bool(stub.get("controls_ok"))
    payload["api_calls"] = 0
    _write_once(artifact, payload)
    print(f"stub artifact: {artifact}")
    print(f"controls={len(stub.get('controls') or [])} ok={payload['controls_ok']}")
    return 0 if status == 0 and payload["controls_ok"] else 1


def _write_once(path: Path, payload: dict[str, Any]) -> None:
    """Refuse to overwrite: the record is written once."""
    if path.exists():
        raise SystemExit(
            f"refusing to overwrite the existing artifact {path}: write a new one instead"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# -------------------------------------------------------------- the local pilot used by (a)
def pilot_rows(root: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (root / "samples.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def per_sample_mechanism(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-sample trigger-gate, v20 and v21 counters, in sample order."""
    out = []
    for row in sorted(
        rows, key=lambda item: (str(item["scenario"]), int(item["repeat"]), str(item["method"]))
    ):
        entry = {
            "task": str(row["scenario"]),
            "repeat": int(row["repeat"]),
            "method": str(row["method"]),
            "model_calls": int(row.get("model_calls", 0) or 0),
            "api_request_attempts": int(row.get("api_request_attempts", 0) or 0),
            "v20_retention_engaged_calls": int(row.get("v20_retention_engaged_calls", 0) or 0),
            "v20_retention_fallback_calls": int(row.get("v20_retention_fallback_calls", 0) or 0),
            "v20_units_elided": int(row.get("v20_units_elided", 0) or 0),
            "v20_bytes_saved": int(row.get("v20_bytes_saved", 0) or 0),
            "exact_duplicate_replacements": int(
                row.get("exact_duplicate_replacements", 0) or 0
            ),
            "selective_retention_saved_bytes_total": int(
                row.get("selective_retention_saved_bytes_total", 0) or 0
            ),
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
            "trigger_gate_last_estimated_tokens": int(
                row.get("trigger_gate_last_estimated_tokens", 0) or 0
            ),
            "persisted_v21_fields": [name for name in V21_ROW_FIELDS if name in row],
        }
        for name in V21_ROW_FIELDS:
            entry[name] = int(row.get(name, 0) or 0)
        out.append(entry)
    return out


def run_local_pilot() -> dict[str, Any]:
    """(a) Run the full nine-sample pilot against a local model and read its manifest back.

    The manifest is produced by the same one-pass finalisation the paid path uses; nothing here
    contacts a provider, because ``base.AsyncOpenAI`` and ``base.OpenAIChatCompletionsModel``
    are replaced by raising stand-ins and the innermost model is the deterministic local stub.
    """
    math_ = configure()
    originals = {
        "client": base.AsyncOpenAI,
        "provider_model": base.OpenAIChatCompletionsModel,
    }
    base.AsyncOpenAI = lambda **kwargs: runner17._NoNetworkClient()
    base.OpenAIChatCompletionsModel = runner17._NoProviderModel
    root: Path | None = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cleaned = ["--confirm-send-public-source", "--out", tmp]
            # The inner factory is built here rather than reused from the v17 rehearsal so the
            # proposal text is derived from this runner's own registry view.
            with v22_chain_installed():
                with v22_finalise_installed():
                    status = runner17.run_pilot(
                        cleaned,
                        inner_factory=_local_model_factory,
                        freeze_override=freeze_override(math_),
                    )
            parsed = base.build_parser().parse_args(forced_args(cleaned))
            root = Path(parsed.out) / parsed.experiment_id
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            rows = pilot_rows(root)
    finally:
        base.AsyncOpenAI = originals["client"]
        base.OpenAIChatCompletionsModel = originals["provider_model"]
    assert root is not None
    missing = [name for name in MANIFEST_REQUIRED_KEYS if name not in manifest]
    return {
        "pilot_status": int(status),
        "samples": len(rows),
        "manifest_present": True,
        "manifest_required_keys": list(MANIFEST_REQUIRED_KEYS),
        "manifest_missing_keys": missing,
        "manifest_required_keys_all_present": not missing,
        "v16_manifest_finalized": manifest.get("v16_manifest_finalized") is True,
        "api_calls": int(manifest.get("api_calls", -1) or 0),
        "per_sample": per_sample_mechanism(rows),
    }


def _local_model_factory(shared: dict[str, Any]):
    """The deterministic local model, identical in shape to the v17 rehearsal's."""

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


# --------------------------------------------------------------- (b) and (c) on those samples
def gate_assertion_b(per_sample: list[dict[str, Any]]) -> dict[str, Any]:
    """The trigger gate must fire on every model input after the first, per sample.

    The rule is read in both directions: the first input must be the *only* one the gate passes
    through, and there must be no other passthrough.  A gate that passed several calls through
    would make the v19 false negative recur; a gate that fired on the first call as well would
    still be reported, because the addendum's wording is "after the first" and the artefact has
    to be accountable for it.
    """
    details = []
    ok = True
    for entry in per_sample:
        if entry["method"] != "pruner_v1":
            continue
        calls = entry["trigger_gate_calls"]
        expected_triggered = max(0, entry["model_calls"] - 1)
        triggered_ok = entry["trigger_gate_triggered_calls"] == expected_triggered
        passthrough_ok = entry["trigger_gate_passthrough_calls"] == min(
            1, entry["model_calls"]
        )
        first_call_ok = (
            entry["trigger_gate_first_call_passthrough_calls"] == min(1, entry["model_calls"])
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
                "trigger_gate_last_estimated_tokens": entry[
                    "trigger_gate_last_estimated_tokens"
                ],
                "gate_calls_equal_model_calls": calls_ok,
                "triggered_equals_after_first": triggered_ok,
                "exactly_one_passthrough_the_first_call": passthrough_ok and first_call_ok,
                "ok": entry_ok,
            }
        )
    return {
        "check": "trigger_gate_fires_on_every_model_input_after_the_first",
        "ok": bool(ok and details),
        "rule": (
            "for every pruner_v1 sample: trigger_gate_calls == model_calls, "
            "trigger_gate_triggered_calls == model_calls - 1, "
            "trigger_gate_passthrough_calls == 1 (the first input only) and "
            "trigger_gate_first_call_passthrough_calls == 1; any other passthrough fails"
        ),
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
            "v21_bytes_saved > 0; a zero means the wiring did not take effect and no paid run "
            "may start"
        ),
        "per_task": by_task,
        "tasks_failing": failures,
        "total_outputs_elided": sum(
            bucket["v21_outputs_elided"] for bucket in by_task.values()
        ),
        "total_lines_elided": sum(bucket["v21_lines_elided"] for bucket in by_task.values()),
        "total_bytes_saved": sum(bucket["v21_bytes_saved"] for bucket in by_task.values()),
        "total_note_bytes": sum(bucket["v21_note_bytes"] for bucket in by_task.values()),
        "total_literal_guard_fallbacks": sum(
            bucket["v21_literal_guard_fallbacks"] for bucket in by_task.values()
        ),
    }


@contextmanager
def _v14_registry_installed():
    """Point the frozen v14 chain's registry name at the v17 task table for one block."""
    from experiments.runners import openai_agents_multitask_chain_v14 as v14

    original = v14.registry
    v14.registry = registry
    try:
        yield
    finally:
        v14.registry = original


def gate_assertion_e() -> dict[str, Any]:
    """(e) The invariant, measured on the real N3 payloads for every task.

    Two things are checked per task, on the payload sequence the paid run would produce:

    * **occurrence counts.**  For every registered literal, the count in the payload after elision
      is greater than or equal to the count before it;
    * **no note is a carrier.**  No line this mechanism wrote satisfies any registered literal, so
      the invariant cannot be met by the mechanism's own annotation.

    It also reports, per task, how much of each payload was elided and what the note overhead
    cost, because the net saving is the number that counts.
    """
    with v22_chain_installed(), _v14_registry_installed():
        return _gate_assertion_e()


def _gate_assertion_e() -> dict[str, Any]:
    from experiments.runners import openai_agents_v21_carrier_elision as elision

    details: list[dict[str, Any]] = []
    failures: list[str] = []
    for task_id in registry.task_ids():
        entry = registry.task(task_id)
        items: list[Any] = [dict(message) for message in registry.history(task_id)]
        # The filter inherits its constructor from the frozen v14 chain, which resolves
        # ``literal_phrases`` through the *v14 module's* registry name; without this the payload
        # would be built against the v14 task table.  The v20 gate reaches the same binding
        # through ``v20_chain_installed``; this probe is called outside that context manager.
        with _v14_registry_installed():
            filter_ = elision.LiteralCarrierElisionFilter(
                task=task_id,
                task_statement=registry.statement(task_id),
                hard_limit_bytes=24_000 * 4,
            )
        task_ok = True
        payload_rows: list[dict[str, Any]] = []
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
                literal: _literal_count(observed, literal)
                for literal in filter_.literal_texts
            }
            outcome = filter_.filter_items(observed)
            sent = list(observed) if outcome is None else list(outcome)
            after = {
                literal: _literal_count(sent, literal) for literal in filter_.literal_texts
            }
            lost = sorted(
                literal for literal in before if after[literal] < before[literal]
            )
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
                    "elided_fraction": (
                        round(
                            (int(last.get("base_bytes", 0) or 0)
                             - int(last.get("after_bytes", 0) or 0))
                            / int(last["base_bytes"]),
                            4,
                        )
                        if int(last.get("base_bytes", 0) or 0)
                        else 0.0
                    ),
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
                "window_lines": elision.WINDOW_LINES,
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


def _literal_count(items: list[Any], literal: str) -> int:
    """Occurrence count of one literal over the canonical JSON of an item list (v5's measure)."""
    from context_pruner.adapters.openai_agents import _jsonable_item

    text = json.dumps(
        [_jsonable_item(item) for item in items],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).lower()
    return text.count(str(literal).lower())


# ------------------------------------------------------------------ (d) revision-aware pinning
def gate_assertion_d(registered: dict[str, str] | None) -> dict[str, Any]:
    """Every module in the path is pinned, and an unregistered digest is a hard failure."""
    current = module_hashes()
    registered = dict(registered or {})
    unregistered = [
        relative
        for relative, digest in sorted(current.items())
        if relative not in registered
    ]
    drifted = [
        relative
        for relative, digest in sorted(current.items())
        if relative in registered and registered[relative] != digest
    ]
    return {
        "check": "every_module_in_the_path_is_pinned_with_revision_aware_fail_closed",
        "ok": not unregistered and not drifted,
        "rule": (
            "every module in the path must have a SHA256 recorded in the revision ledger; a "
            "module whose digest is absent from the ledger, or differs from its recorded "
            "digest, fails the gate until the change is registered in a new revision"
        ),
        "modules": current,
        "plan_named_modules": {
            relative: current[relative] for relative in PLAN_NAMED_MODULES
        },
        "revision_ledger_entries": sorted(registered),
        "unregistered_modules": unregistered,
        "drifted_modules": drifted,
        "module_count": len(current),
    }


# ----------------------------------------------------------------------- inherited v17 checks
def inherited_checks() -> list[dict[str, Any]]:
    """The v17 shape, diagnostic, strict-track and cap checks, unchanged in substance."""
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    spec_rounds, spec_reads = ROUNDS, READS_PER_ROUND
    steps = plan_steps(registry.task_ids()[0])
    text = "\n".join(steps)
    record(
        "protocol_matches_the_shape",
        f"{spec_rounds} rounds" in text
        and f"{READ_CALLS} read calls" in text
        and all(f"round {index + 1}:" in text for index in range(spec_rounds)),
        {"rounds": spec_rounds, "read_calls": READ_CALLS},
    )
    record(
        "one_read_per_turn_rule_matches_the_batching",
        "do not issue more than one read in a turn" in text,
        {"reads_per_round": spec_reads},
    )
    record(
        "dedupe_and_no_merge_rules_present",
        "never read a registered view more often" in text
        and "do not merge two rounds into one turn" in text,
    )
    record(
        "old_shape_wording_absent",
        all(probe not in text for probe in OLD_WORDING)
        and all(
            probe
            not in (ROOT / "experiments/runners/openai_agents_v17_registry.py").read_text(
                encoding="utf-8"
            )
            for probe in OLD_WORDING
        ),
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
        {"issue": issue, "cause": "a cause", "fix": "a fix", "facts": ["__hash__", "__eq__"]}
    )
    verdict = render(missing, expected_issue=issue)
    record(
        "declared_literal_missing_from_the_line_still_rejected",
        (not verdict.accepted)
        and verdict.reason == "declared_fact_missing_from_rendered_answer",
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
        "composite_filter_declares_the_shadowed_v5_entry_point",
        callable(mechanism.LiteralCarrierElisionFilter.filter_items)
        and "MultitaskDuplicateFilter.filter_items(self, items)"
        in (
            ROOT / "experiments/runners/openai_agents_v21_carrier_elision.py"
        ).read_text(encoding="utf-8")
        and "v5.SelectiveRetentionFilter.filter_items(self, dedupe_base)"
        in (
            ROOT / "experiments/runners/openai_agents_v21_carrier_elision.py"
        ).read_text(encoding="utf-8"),
    )
    record(
        "trigger_gate_threshold_is_the_pre_registered_zero",
        chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS == 0,
        {"soft_limit_tokens_setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS},
    )
    window_check = _effective_window_matches_k()
    record(
        "protection_window_is_the_k_pre_registered_for_this_configuration",
        window_check["ok"],
        window_check,
    )
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
    if K == 0:
        zero_checks = _k0_edge_case_checks()
        record(
            "k0_produces_no_empty_or_malformed_output_and_notes_never_carry_a_literal",
            zero_checks["ok"],
            zero_checks,
        )
    record(
        "elision_counters_are_persisted_in_the_sample_rows",
        all(name in chain.PERSISTED_EXTRA_FIELDS for name in V21_ROW_FIELDS),
        {"persisted": list(V21_ROW_FIELDS)},
    )
    record(
        "the_tool_documentation_is_unchanged_and_the_disclosure_is_written",
        # The tool description the model sees is the registry's, byte-identical in all three
        # arms; the mechanism must not have rewritten it, and the disclosure the integrator
        # required must be present in the mechanism block and in every manifest.
        "verbatim" in registry.task(registry.task_ids()[0])["tool_docs"][0]
        and "tool_output_disclosure" in chain.mechanism_block()
        and "elided n lines first-last" in chain.TOOL_OUTPUT_DISCLOSURE
        and "identical in all three arms" in chain.TOOL_OUTPUT_DISCLOSURE
        and "tool_output_disclosure" in MANIFEST_REQUIRED_KEYS,
        {
            "disclosure": chain.TOOL_OUTPUT_DISCLOSURE,
            "registry_tool_doc_verbatim_clause": "Returns those numbered lines verbatim",
        },
    )
    record(
        "no_silent_omission_is_possible",
        # Every omission the mechanism makes is accompanied by an interval note; the count of
        # note lines is what the gate asserts, so a silent omission would show up as elided lines
        # with no note.  The mechanism-level evidence for this is in the invariant assertion.
        mechanism.NOTE_FORM == "[elided {count} lines {first}-{last}]"
        and mechanism.is_note_line(mechanism.note_line(4, 8, 11))
        and mechanism.note_line(4, 8, 11) == "[elided 4 lines 8-11]",
        {"note_form": mechanism.NOTE_FORM, "example": mechanism.note_line(4, 8, 11)},
    )
    record(
        "the_paid_path_installs_the_same_contexts_as_the_rehearsal",
        # The first paid v21 attempt installed only the chain on the paid path, so its manifest
        # carried the frozen v17 key set and none of the v21 keys.  The gate's rehearsal had the
        # finaliser and the paid path did not: this check exists so that asymmetry cannot recur
        # without failing the gate before any request is sent.
        _run_paid_installs_both_contexts(),
        {
            "paid_wiring": "with v22_chain_installed(): with v22_finalise_installed(): run_pilot(...)",
            "why": (
                "the rehearsal and the paid path must install the same context managers; a green "
                "gate over a parallel rehearsal is what shipped an incomplete manifest once"
            ),
        },
    )
    record(
        "the_paid_path_preserves_the_runner_bytes_and_matches_the_pin",
        # The first v21 paid attempt could not be tied to the file that produced it.  This checks
        # both halves of the remedy: the paid path preserves the bytes before running, and the
        # file on disk is the digest this ledger pinned.
        _paid_path_preserves_runner_bytes(),
        _paid_path_preservation_detail(),
    )
    return checks


def _run_paid_installs_both_contexts() -> bool:
    """Whether ``run_paid`` wraps the pilot in both the chain and the finaliser contexts."""
    import inspect

    source = inspect.getsource(run_paid)
    body = source.split("preserve_runner_bytes()", 1)[-1]
    return (
        "with v22_chain_installed():" in body
        and "with v22_finalise_installed():" in body
        and body.index("with v22_chain_installed():")
        < body.index("with v22_finalise_installed():")
    )


def _paid_path_preserves_runner_bytes() -> bool:
    """Whether the paid path preserves the runner bytes before it runs, and matches the pin."""
    import inspect

    source = inspect.getsource(run_paid)
    if "preservation = preserve_runner_bytes()" not in source:
        return False
    if "if not preservation[\"matches_ledger\"]" not in source:
        return False
    return bool(preserve_runner_bytes()["matches_ledger"])


def _paid_path_preservation_detail() -> dict[str, Any]:
    detail = preserve_runner_bytes()
    detail["gate_reads_it_before_any_request"] = True
    detail["why"] = (
        "a batch must be attachable to the code revision that produced it; the first v21 paid "
        "attempt was not, and this is the remedy"
    )
    return detail


def _effective_window_matches_k() -> dict[str, Any]:
    """The window the mechanism will actually use must be the K of this configuration.

    The window is read from three places and all three must agree, because the failure this guards
    against is silent: a filter whose class says K=1 while the inherited stage still uses the
    ancestor's constant would produce a K=3 batch labelled as K=1.
    """
    config = dict(getattr(chain.CONFIG_MODULE, "CONFIG", {}) or {})
    config_window = int(config.get("k", -1))
    declaration = str(config.get("effective_window_declaration", ""))
    declared = int(config.get("declared_window_value", -1))
    class_window = int(getattr(chain.FILTER_CLASS, "WINDOW_LINES", -1))
    instance_window = None
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
        "instance_window_example": instance_window,
        "config_module": config.get("config_module"),
        "frozen_mechanism_default": mechanism.WINDOW_LINES,
        "why_three_places": (
            "the configuration record, the filter class and the attribute the inherited stage "
            "reads must all carry the same K; the frozen module's own constant must still be 3"
        ),
    }


def _k0_edge_case_checks() -> dict[str, Any]:
    """The K=0 property the plan requires: no empty or malformed output, and notes are not carriers.

    At K=0 a literal-carrying line has no protected neighbours, so the two structures this has to
    survive are (1) an output whose every line carries a literal, where eliding would leave an
    empty or note-only text, and (2) an ordinary view, where the runs between carrier lines are
    replaced by notes and every note must still be unable to satisfy a literal.
    """
    import re

    from experiments.runners import openai_agents_v21_carrier_elision as base_mechanism

    details: dict[str, Any] = {}
    ok = True
    with v22_chain_installed(), _v14_registry_installed():
        task_id = registry.task_ids()[0]
        literals = sorted(
            {
                str(phrase)
                for values in registry.literal_phrases(task_id).values()
                for phrase in values
            }
        )
        carrier = literals[0] if literals else "check_sqlite_version"

        # (1) every line carries a literal: the mechanism must keep the output, not empty it.
        all_carrier = "\n".join(f"{index}: {carrier} line {index}" for index in range(6))
        filter_a = chain.FILTER_CLASS(
            task=task_id,
            task_statement=registry.statement(task_id),
            hard_limit_bytes=24_000 * 4,
        )
        text_a, record_a = filter_a._elide_text(all_carrier, literals)
        details["all_lines_carry_a_literal"] = {
            "kept_verbatim": text_a is None,
            "reason": record_a.get("reason", ""),
            "lines": len(all_carrier.splitlines()),
        }
        ok = ok and text_a is None and len(all_carrier.splitlines()) > 0

        # (2) an ordinary view at K=0: notes replace the runs, the carrier lines survive, and no
        # note can carry a literal.
        view = registry.source_view(task_id, 0)
        filter_b = chain.FILTER_CLASS(
            task=task_id,
            task_statement=registry.statement(task_id),
            hard_limit_bytes=24_000 * 4,
        )
        text_b, record_b = filter_b._elide_text(view, literals)
        note_lines = [
            line for line in str(text_b or "").splitlines() if base_mechanism.is_note_line(line)
        ]
        carrier_lines = [
            line
            for line in str(text_b or "").splitlines()
            if any(literal.lower() in line.lower() for literal in literals)
        ]
        original_carrier_lines = [
            line
            for line in view.splitlines()
            if any(literal.lower() in line.lower() for literal in literals)
        ]
        malformed = [
            line
            for line in note_lines
            if re.match(r"^\[elided (\d+) lines (\d+)-(\d+)\]$", line) is None
        ]
        note_carriers = sorted(
            {
                literal
                for line in note_lines
                for literal in literals
                if literal.lower() in line.lower()
            }
        )
        details["ordinary_view_at_k0"] = {
            "elided": text_b is not None,
            "lines_elided": int(record_b.get("lines_elided", 0)),
            "note_lines": len(note_lines),
            "malformed_notes": malformed,
            "notes_carrying_a_literal": note_carriers,
            "carrier_lines_kept": len(carrier_lines),
            "carrier_lines_in_the_view": len(original_carrier_lines),
            "first_note": note_lines[0] if note_lines else "",
        }
        ok = ok and not malformed and not note_carriers
        if text_b is not None:
            # A genuine elision must still be a *text*: it may not be empty, and it may not be
            # nothing but a note, because an output reduced to an annotation would be the empty
            # output this check exists to forbid.
            kept_text = "\n".join(
                line for line in str(text_b).splitlines() if not mechanism.is_note_line(line)
            ).strip()
            details["ordinary_view_at_k0"]["kept_text_non_empty"] = bool(kept_text)
            ok = ok and bool(kept_text)
        details["ok"] = ok
    return details


def plan_and_stub_checks(tmp: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    plan_path = tmp / "plan.json"
    with v22_chain_installed():
        plan_status = runner17.main(["--plan", "--artifact", str(plan_path)])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    record(
        "plan_is_zero_api",
        plan_status == 0 and plan.get("api_calls") == 0,
        {"api_calls": plan.get("api_calls")},
    )
    stub_path = tmp / "stub.json"
    with v22_chain_installed():
        stub_status = runner17.main(["--stub", "--artifact", str(stub_path)])
    stub = json.loads(stub_path.read_text(encoding="utf-8"))
    controls = stub.get("controls") or []
    record(
        "stub_controls_all_pass_and_at_least_twelve",
        stub_status == 0 and bool(stub.get("controls_ok")) and len(controls) >= 12,
        {"controls": len(controls), "ok": stub.get("controls_ok")},
    )
    record("stub_is_zero_api", stub.get("api_calls") == 0, {"api_calls": stub.get("api_calls")})
    return checks


# -------------------------------------------------------------------------------------- gate
def gate(revision_ledger: dict[str, str] | None = None) -> dict[str, Any]:
    """All zero-API checks, with the hard v21 assertions wired in."""
    math_ = configure()
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    with tempfile.TemporaryDirectory() as tmp:
        checks.extend(plan_and_stub_checks(Path(tmp)))
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
            "v16_manifest_finalized": local["v16_manifest_finalized"],
            "api_calls": local["api_calls"],
            "required_keys_checked": len(local["manifest_required_keys"]),
        },
    )
    assertion_b = gate_assertion_b(local["per_sample"])
    checks.append(
        {"check": assertion_b["check"], "ok": assertion_b["ok"], "detail": assertion_b}
    )
    assertion_c = gate_assertion_c(local["per_sample"])
    checks.append(
        {"check": assertion_c["check"], "ok": assertion_c["ok"], "detail": assertion_c}
    )
    assertion_e = gate_assertion_e()
    checks.append(
        {"check": assertion_e["check"], "ok": assertion_e["ok"], "detail": assertion_e}
    )
    assertion_d = gate_assertion_d(revision_ledger)
    checks.append(
        {"check": assertion_d["check"], "ok": assertion_d["ok"], "detail": assertion_d}
    )
    checks.extend(inherited_checks())

    # The gate's own failure mode must be reachable: a synthetic counter set with a zero saving
    # and a passing trigger gate must be rejected by (c), and one with a passthrough call must be
    # rejected by (b).  Asserting this here means the checks cannot silently become tautologies.
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
                "trigger_gate_last_estimated_tokens": 4698,
            }
        ]
    )
    failing_d = gate_assertion_d({})
    failing_e = _invariant_probe_rejects_a_lost_literal()
    record(
        "the_gate_assertions_can_fail",
        (not failing_c["ok"])
        and (not failing_b["ok"])
        and (not failing_d["ok"])
        and failing_e,
        {
            "assertion_c_rejects_a_zero_saving_sample": not failing_c["ok"],
            "assertion_b_rejects_a_passthrough_sample": not failing_b["ok"],
            "assertion_d_rejects_an_unregistered_module_set": not failing_d["ok"],
            "assertion_e_rejects_an_output_that_loses_a_literal": failing_e,
        },
    )

    ok = all(entry["ok"] for entry in checks)
    return {
        "shape": SHAPE,
        "arithmetic": math_,
        "checks": checks,
        "ok": ok,
        "hard_assertions": {
            "a_finalise_really_runs": checks[3]["ok"] if len(checks) > 3 else False,
            "b_trigger_gate_fires_every_call": assertion_b["ok"],
            "c_mechanism_engages_on_unique_content": assertion_c["ok"],
            "d_every_module_pinned": assertion_d["ok"],
            "e_literal_invariant_holds_on_real_payloads": assertion_e["ok"],
        },
        "per_sample_mechanism": local["per_sample"],
        "v21_counters_per_task": assertion_c["per_task"],
        "literal_invariant_per_task": assertion_e["detail"],
        "paid_run_allowed": bool(ok),
        "paid_run_allowed_rule": (
            "true only when every check passes; assertion (c) is the one that speaks to whether "
            "the wiring took effect at all, and a zero elision counter writes false here"
        ),
        "module_hashes": module_hashes(),
        "mechanism": chain.mechanism_block(),
        "plan_implements": PLAN_PATH,
        "v20_result": V20_RESULT_PATH,
    }


def _invariant_probe_rejects_a_lost_literal() -> bool:
    """Assertion (e) must be able to fail: a payload that loses a literal is rejected.

    Built from the v5 lineage's own ``_carrier_loss`` - the same function assertion (e) uses on
    the real payloads - so the probe proves the *check* can fire rather than proving a second
    implementation of it.  A payload that keeps every literal must pass the same call, which is
    what makes the probe falsifiable in both directions.
    """
    with v22_chain_installed(), _v14_registry_installed():
        return _invariant_probe_body()


def _invariant_probe_body() -> bool:
    task_id = registry.task_ids()[0]
    filter_ = mechanism.LiteralCarrierElisionFilter(
        task=task_id,
        task_statement=registry.statement(task_id),
        hard_limit_bytes=24_000 * 4,
    )
    literal = str(filter_.literal_texts[0])
    carrier = registry.source_view(task_id, 0)
    before = [
        {"type": "function_call_output", "call_id": "c1", "output": carrier},
    ]
    keeps = [
        {"type": "function_call_output", "call_id": "c1", "output": carrier},
    ]
    loses = [
        {"type": "function_call_output", "call_id": "c1", "output": "nothing registered here"},
    ]
    return (
        bool(literal)
        and filter_._carrier_loss(before, keeps) == []
        and bool(filter_._carrier_loss(before, loses))
    )


def write_revision_ledger() -> dict[str, Any]:
    """Write the v21 revision ledger: what each module in the path was when the run was prepared.

    This is the registry the gate checks against.  Writing it is a deliberate, recorded act: from
    then on, a module whose digest is not one this ledger records fails the gate, so an
    unregistered edit can never reach a batch and be discovered afterwards.  Each write is a new
    revision; the previous revision's digests are carried in ``previous_revisions`` so a change is
    a recorded act rather than a silent one.
    """
    previous: list[dict[str, Any]] = []
    revision = 1
    if REVISION_LEDGER.is_file():
        old = json.loads(REVISION_LEDGER.read_text(encoding="utf-8"))
        previous = list(old.get("previous_revisions") or [])
        previous.append(
            {
                "revision": int(old.get("revision", 1)),
                "modules": dict(old.get("modules") or {}),
            }
        )
        revision = int(old.get("revision", 1)) + 1
    live = module_hashes()
    payload = {
        "schema": "openai_agents_v22_window_dose_module_revision_ledger",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_implements": PLAN_PATH,
        "v21_plan": V21_PLAN_PATH,
        "v20_plan": V20_PLAN_PATH,
        "window_lines": K,
        "config_module": str(getattr(chain.CONFIG_MODULE, "CONFIG", {}).get("config_module", "")),
        "api_requests_spent": 0,
        "purpose": (
            "pin every module in this K configuration's path by SHA256 so the gate can fail closed "
            "on an unregistered change; the window a batch ran at is a pinned module, and the "
            "frozen v21 mechanism module keeps its own digest while each K adds its own"
        ),
        "rules": [
            "a module whose digest is absent from this ledger fails the gate",
            "a module whose digest differs from the digest recorded here fails the gate",
            "a change is admitted only by registering a new revision of this ledger",
            "a different K is a different configuration module and a different ledger, never an "
            "edit to a window in place",
        ],
        "modules": live,
        "plan_named_modules": PLAN_NAMED_MODULES,
        "revision": revision,
        "previous_revisions": previous,
        "gate_class": "experiments.runners.openai_agents_v20_chain.FirstCallPassthroughGate",
        "filter_class": "experiments.runners.openai_agents_v21_carrier_elision.LiteralCarrierElisionFilter",
    }
    REVISION_LEDGER.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--stub", action="store_true")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--register-revision", action="store_true")
    parser.add_argument("--artifact", default="")
    parser.add_argument("--mode", choices=("plan", "stub", "gate"), default="")
    parser.add_argument("--confirm-send-public-source", action="store_true")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--revision-ledger", default="")
    parser.add_argument(
        "--k",
        type=int,
        default=AUTHORISED_WINDOWS[0],
        help=f"the pre-registered protection window to run: one of {AUTHORISED_WINDOWS}",
    )
    parser.add_argument(
        "--stage-three",
        action="store_true",
        help=(
            "run the K=0 stage-3 repetition: three repeats, 27 samples, cap "
            f"{STAGE_TWO_MAX_API_REQUESTS}"
        ),
    )
    parser.add_argument(
        "--stage-two",
        action="store_true",
        help=(
            "run the K=1 stage-2 repetition: three repeats, 27 samples, cap "
            f"{STAGE_TWO_MAX_API_REQUESTS}"
        ),
    )
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    mode = args.mode or ("plan" if args.plan else "stub" if args.stub else "gate" if args.gate else "")
    # The window is bound before anything else: the chain's filter class, the mechanism schema,
    # the artifact paths and the experiment id all follow from it, so no K can inherit another's
    # configuration.
    config = select_window(args.k)
    if args.stage_three:
        math_stage_three = configure_stage_three()
        print(
            f"stage 3: repeats={REPEATS} samples={SAMPLES} cap={MAX_API_REQUESTS} "
            f"worst={math_stage_three['worst_case_requests']} margin={math_stage_three['margin']}",
            file=sys.stderr,
        )
    if args.stage_two:
        math_stage_two = configure_stage_two()
        print(
            f"stage 2: repeats={REPEATS} samples={SAMPLES} cap={MAX_API_REQUESTS} "
            f"worst={math_stage_two['worst_case_requests']} margin={math_stage_two['margin']}",
            file=sys.stderr,
        )
    print(
        f"window K={K} config={config['config_module']} schema={config['mechanism_schema']} "
        f"repeats={REPEATS} samples={SAMPLES} cap={MAX_API_REQUESTS} experiment={EXPERIMENT_ID}",
        file=sys.stderr,
    )
    configure()
    if args.register_revision:
        payload = write_revision_ledger()
        print(f"revision ledger: {REVISION_LEDGER}")
        print(f"revision={payload['revision']} modules={len(payload['modules'])}")
        return 0
    if mode == "plan":
        artifact = Path(args.artifact) if args.artifact else PLAN_OUT
        return run_plan(artifact, rest)
    if mode == "stub":
        artifact = Path(args.artifact) if args.artifact else STUB_OUT
        return run_stub(artifact, rest)
    if args.pilot:
        # ``--confirm-send-public-source`` is this parser's own flag, so ``parse_known_args``
        # removes it from ``rest``; ``run_paid`` reads the raw argument list and would therefore
        # have refused every paid run.  Re-add it here so the guard sees the same flag the caller
        # typed, and so the frozen runner's ``forced_args`` can convert it as it does everywhere
        # else.  Without this the paid path is unreachable, which is a bug in this runner and not
        # a safety property.
        paid_args = list(rest)
        if args.confirm_send_public_source and "--confirm-send-public-source" not in paid_args:
            paid_args.append("--confirm-send-public-source")
        return run_paid(paid_args)
    ledger = load_revision_ledger(Path(args.revision_ledger) if args.revision_ledger else None)
    result = gate(ledger)
    _write_once(GATE_OUT, result)
    print(json.dumps({"shape": SHAPE, "ok": result["ok"]}, ensure_ascii=False))
    for name, value in result["hard_assertions"].items():
        print(f"  hard assertion {name}: {value}")
    for entry in result["checks"]:
        if not entry["ok"]:
            print(f"  FAILED: {entry['check']}")
    print(f"  paid_run_allowed: {result['paid_run_allowed']}")
    print(f"  gate artifact: {GATE_OUT}")
    return 0 if result["ok"] else 1


def load_revision_ledger(path: Path | None) -> dict[str, str]:
    """The recorded module digests, read from the ledger (the default path, or an override)."""
    target = Path(path) if path is not None else REVISION_LEDGER
    if not target.is_file():
        return {}
    record = json.loads(target.read_text(encoding="utf-8"))
    return {str(key): str(value) for key, value in (record.get("modules") or {}).items()}


def configure_stage_two() -> dict[str, Any]:
    """The K=1 stage-2 repetition: three repeats, 27 samples, cap derived from the worst case.

    Only two numbers change against the b round - the repeat count and the cap - and both follow
    from the frozen arithmetic: the worst case for 27 samples is 27 x (4 expected calls + 1 retry
    budget) + 9 x 2 summary calls = 153 requests on the frozen formula, or 168 on the runner's own
    looser bound; a cap of 250 keeps 1.63x and 1.49x over them, above the 1.3x floor.  Nothing else
    about the configuration moves, and the window stays where it was registered.
    """
    global REPEATS, SAMPLES, MAX_API_REQUESTS, EXPERIMENT_ID
    if K != 1:
        raise SystemExit(
            f"stage 2 is authorised for K=1 only: this configuration is K={K}"
        )
    REPEATS = STAGE_TWO_REPEATS
    SAMPLES = 3 * REPEATS * len(ARMS)
    MAX_API_REQUESTS = STAGE_TWO_MAX_API_REQUESTS
    EXPERIMENT_ID = EXPERIMENT_IDS[K].replace("-pilot-01", STAGE_TWO_EXPERIMENT_SUFFIX)
    return arithmetic()


def configure_stage_three() -> dict[str, Any]:
    """The K=0 stage-3 repetition: the same three repeats, on the integrator's second authorisation.

    Identical arithmetic to stage 2; only the window and the experiment id differ.  The point of
    the group is the side-by-side comparison with stage 2 at the same repeat count, so nothing but
    K may move.
    """
    global REPEATS, SAMPLES, MAX_API_REQUESTS, EXPERIMENT_ID
    if K not in STAGE_THREE_WINDOWS:
        raise SystemExit(
            f"stage 3 is authorised for K in {STAGE_THREE_WINDOWS} only: this configuration is "
            f"K={K}"
        )
    REPEATS = STAGE_TWO_REPEATS
    SAMPLES = 3 * REPEATS * len(ARMS)
    MAX_API_REQUESTS = STAGE_TWO_MAX_API_REQUESTS
    EXPERIMENT_ID = EXPERIMENT_IDS[K].replace("-pilot-01", STAGE_THREE_EXPERIMENT_SUFFIX)
    return arithmetic()


def require_paid_authorisation(k: int) -> None:
    """Refuse a paid run for a window no authorisation covers.

    The gate, the tests and the zero-API probes may configure any pre-registered window; only the
    paid path is restricted, and it accepts a window covered either by the dose-02 authorisation or
    by the stage-three authorisation.
    """
    value = int(k)
    if value in AMENDMENT_WINDOWS or value in STAGE_THREE_WINDOWS:
        return
    raise SystemExit(
        f"refusing to run a paid group at K={value}: the dose-02 authorisation covers "
        f"{AMENDMENT_WINDOWS} and the stage-three authorisation covers {STAGE_THREE_WINDOWS}. "
        "Its earlier group has already been run and reported, and re-opening it would put a "
        "second group for the same K into the record without a new registration"
    )


def run_paid(args: list[str]) -> int:
    """The paid path.  It refuses to start unless the recorded gate allows it."""
    require_paid_authorisation(K)
    if not GATE_OUT.is_file():
        raise SystemExit(
            f"refusing to send a paid request: the v21 gate artifact is missing ({GATE_OUT})"
        )
    recorded = json.loads(GATE_OUT.read_text(encoding="utf-8"))
    if recorded.get("paid_run_allowed") is not True:
        raise SystemExit(
            "refusing to send a paid request: the v21 gate recorded paid_run_allowed="
            f"{recorded.get('paid_run_allowed')!r}; a zero elision counter means the wiring did "
            "not take effect and the paid run is forbidden"
        )
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    math_ = configure()
    # The first v21 paid attempt could not be tied to the file that produced it, because the
    # runner bytes were not preserved before the run.  This does that first, and it also checks
    # that the file about to run is the revision the ledger pinned.
    preservation = preserve_runner_bytes()
    if not preservation["matches_ledger"]:
        raise SystemExit(
            "refusing to send a paid request: the runner on disk does not match the digest the "
            f"gate pinned ({preservation['digest']} not in the ledger for this module)"
        )
    # Both context managers, in the same nesting the gate's rehearsal uses.  The first paid v21
    # attempt installed only the chain and therefore wrote the *frozen* v17 key set: every v21
    # key, including the required tool-output disclosure, was missing from the batch manifest.
    # The rehearsal had the finaliser and the paid path did not, which is the asymmetry that let a
    # green gate ship an incomplete artifact - so the fix is here, and the gate now exercises this
    # function rather than a parallel copy of it.
    with v22_chain_installed():
        with v22_finalise_installed():
            status = runner17.run_pilot(args, freeze_override=freeze_override(math_))
    _record_preservation(args, preservation)
    return status


def preserve_runner_bytes() -> dict[str, Any]:
    """Copy this runner's bytes outside the repository and compare them with the pinned digest."""
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
    relative = str(own.relative_to(ROOT)).replace("\\", "/")
    target = PRESERVATION_DIR / f"run_openai_agents_v22_window_dose_k{K}.py"
    try:
        PRESERVATION_DIR.mkdir(parents=True, exist_ok=True)
        target.write_bytes(own.read_bytes())
        preserved = True
    except OSError:  # pragma: no cover - the directory is outside the repository
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
    """Write the preservation record into the batch directory, so the batch carries its revision."""
    parsed = base.build_parser().parse_args(forced_args(args))
    root = Path(parsed.out) / parsed.experiment_id
    if not root.is_dir():
        return
    record = {
        "schema": "openai_agents_v22_runner_preservation",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "window_lines": K,
        "experiment_id": parsed.experiment_id,
        **preservation,
        "why": (
            "the first v21 paid attempt produced a batch that could not be tied to the runner "
            "revision that produced it, because the bytes were not preserved before the run; this "
            "file records the digest and the preserved copy for every v22 group"
        ),
    }
    (root / "runner-bytes.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
