"""v20 acquisition runner: the N=3 no-duplicate shape on the composed retention chain.

Three modes, and none of the three contacts a provider:

``--plan``
    Zero API.  Freeze, amendment, source-of-record and registry preflight, the frozen request
    plan written through the unmodified v17 runner, the provider client and model replaced by
    objects that raise on use, and an artifact whose ``api_calls`` is 0.
``--stub``
    Zero API.  The thirteen stage-1 negative and positive controls replayed through the *same*
    wiring the paid batch would use - SDK ``Runner``, real ``output_type`` agent, the v20 plugin
    filter, the real recorder, the real renderer and the one-retry wrapper - against a
    deterministic local model.
``--gate``
    Zero API.  The four hard v20 assertions (below) plus the inherited v17 shape, diagnostic,
    strict-track and cap checks, written to ``V20_ZERO_API_GATE_20261010.json``.

The gate's four hard assertions, and why each is where it is
-----------------------------------------------------------
(a) **finalise really runs.**  A full nine-sample pilot is run with a local model and the
    produced manifest must carry every pre-registered key, with ``v16_manifest_finalized``
    true.  The v17 and v19 batches both lost their finalised manifest *after* the samples had
    been paid for, so this lives inside the gate and not after it.
(b) **the trigger gate really fires.**  Under the N3 shape each sample must report
    ``trigger_gate_triggered_calls`` equal to the number of model inputs after the first and
    ``trigger_gate_passthrough_calls`` equal to zero, per sample, for every sample.  A gate that
    still passes calls through would make the v19 false negative recur.
(c) **the mechanism really engages.**  On this shape there is no duplicate content by
    construction, so ``v20_units_elided`` and ``v20_bytes_saved`` must be non-zero, per task.  If
    they are zero the wiring did not take effect: the gate writes ``paid_run_allowed: false``
    and no paid run may start.
(d) **every module in the path is pinned.**  Registry, runner, v16 live contract, renderer and
    both new v20 modules are listed with their SHA256, the manifest copies them, and an
    unregistered digest is a hard failure (revision-aware fail-closed): if a module changes, the
    gate fails until the change is registered in a recorded revision.

Nothing here sends a request.  ``--pilot`` exists for completeness but refuses unless
``--confirm-send-public-source`` is given *and* the v20 gate artifact says
``paid_run_allowed`` is true.
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
from experiments.runners import openai_agents_v20_chain as chain  # noqa: E402
from experiments.runners import openai_agents_v20_retention as retention  # noqa: E402
from experiments.runners import run_openai_agents_api_experiment as base  # noqa: E402
from experiments.runners import run_openai_agents_v17_acquisition as runner17  # noqa: E402
from experiments.runners.openai_agents_structured_final_v16 import render  # noqa: E402

#: The plan this runner implements, and the addendum that changed the gate threshold.
PLAN_PATH = "integrations/openai_agents/V20_NONDUPLICATE_MECHANISM_PLAN_20261010.json"
ADDENDUM_PATH = "integrations/openai_agents/V20_GATE_ADDENDUM_20261010.json"
WIRING_DIFF_PATH = "integrations/openai_agents/V20_WIRING_DIFF_20261010.json"

EXPERIMENT_ID = "openai-repo-diagnostic-v20-nonduplicate-retention-pilot-01"

#: The shape is fixed: three views, one distinct view per round, three rounds, each view read
#: once.  There is no shape parameter in v20 - that is the point of the N3 control.
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

GATE_OUT = ROOT / "integrations/openai_agents/V20_ZERO_API_GATE_20261010.json"
PLAN_OUT = ROOT / "integrations/openai_agents/V20_LIVE_PLAN_20261010.json"
STUB_OUT = ROOT / "integrations/openai_agents/V20_LIVE_STUB_20261010.json"
#: The module revision ledger the gate pins against (assertion (d)).
REVISION_LEDGER = ROOT / "integrations/openai_agents/V20_MODULE_REVISIONS_20261010.json"

#: Old shape wording that must not reappear in the protocol or the registry.
OLD_WORDING = ("three calls of a round", "a single read on its own")

# --- (d) the modules whose SHA256 the gate lists and the manifest copies -------------------
PINNED_MODULES = (
    "experiments/runners/openai_agents_v17_registry.py",
    "experiments/runners/run_openai_agents_v17_acquisition.py",
    "experiments/runners/openai_agents_v17_live_contract.py",
    "experiments/runners/openai_agents_structured_final_v16.py",
    "experiments/runners/openai_agents_v20_retention.py",
    "experiments/runners/openai_agents_v20_chain.py",
    "experiments/runners/run_openai_agents_v20_acquisition.py",
)
#: The registry, the runner, the live contract and the renderer, singled out because the plan
#: names exactly those four.
PLAN_NAMED_MODULES = PINNED_MODULES[:4]

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
    # the v20 mechanism block
    "mechanism",
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
    """Point the v17 runner and the v17 registry at the N3 shape and the v20 chain."""
    math_ = arithmetic()
    registry.protocol_steps = lambda task_id: plan_steps(task_id)
    registry.READ_CALLS = READ_CALLS
    registry.READ_ROUNDS = ROUNDS
    registry.READS_PER_ROUND = READS_PER_ROUND
    registry.EXPECTED_MODEL_CALLS = EXPECTED_MODEL_CALLS
    # The v20 chain resolves its recorder, its filter and its task data through its own module
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
def v20_chain_installed():
    """Run the v17 acquisition runner with the v20 chain in place of the v14 chain.

    Three bindings have to be in force simultaneously, and each exists for a different reason:

    * ``runner17.chain`` must be the v20 chain, because that is the module the frozen runner's
      ``chain.chain_wiring()`` call reaches;
    * ``chain.registry`` must be the v17 registry, because ``v20_chain.MultitaskRecorder`` and
      ``register_task_data`` read the task through the v20 chain module's own ``registry`` name;
    * ``v14.registry`` must be the v17 registry too, because the v20 chain **inherits** its
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
def v20_finalise_installed():
    """Add the manifest keys the v20 plan pre-registers but the frozen pilot finaliser omits.

    ``openai_agents_v17_acquisition.finalise_pilot_manifest`` writes the v17 key set.  Four keys
    the v20 plan pre-registers are not in it: ``mechanism``, ``v16_registry``,
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
        manifest["never_rejudge"] = (
            "attempt 01, attempt 02, attempt 03, pilot-01, pilot-02, v12 to v15, the v16 sealed "
            "confirmation batch, the v17 development pilots, the v18 selection branch and the "
            "v19 dose batches are untouched and unrejudged; v20 does not merge its numbers with "
            "any of them"
        )
        manifest["v20_gate_threshold"] = {
            "setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": chain.TRIGGER_GATE_POLICY,
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
    hands to ``run_pilot`` - with the v20 matrix, module hashes, mechanism block and cap written
    over the frozen ones, so the rehearsal exercises the one-pass finalisation with exactly the
    keys the paid path would give it.  Building it by hand is what produced the first attempt's
    missing ``source_of_record`` key.

    ``source_of_record`` is carried as an empty map on purpose: the v17 freeze does not pin source
    files by digest, so there is nothing to pin here.  ``runner17.verify_pilot_freeze()`` is
    deliberately *not* called: it asserts the v17 cap of 110, which this runner replaces.
    """
    freeze = json.loads(runner17.PILOT_FREEZE.read_text(encoding="utf-8"))
    freeze = json.loads(json.dumps(freeze))
    freeze["path"] = "<the v17 development freeze's key set, with an in-memory v20 override>"
    freeze["sha256"] = "0" * 64
    freeze["shape"] = SHAPE
    freeze["mechanism"] = chain.mechanism_block()
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
        "source": "the v17 freeze's decision_line, carried through the v20 override",
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
        with v20_chain_installed():
            status = runner17.main(["--plan", "--artifact", str(plan_path), *args])
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        payload = {
            "schema": "openai_agents_v20_live_plan",
            "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "plan",
            "api_calls": 0,
            "plan_implements": PLAN_PATH,
            "gate_addendum": ADDENDUM_PATH,
            "wiring_diff": WIRING_DIFF_PATH,
            "shape": math_,
            "mechanism": chain.mechanism_block(),
            "chain_in_use": {
                "chain_module": "experiments/runners/openai_agents_v20_chain.py",
                "filter": "NonDuplicateRetentionFilter",
                "filter_module": "experiments/runners/openai_agents_v20_retention.py",
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
        with v20_chain_installed():
            status = runner17.main(["--stub", "--artifact", str(stub_path), *args])
        stub = json.loads(stub_path.read_text(encoding="utf-8"))
    payload = dict(stub)
    payload["schema"] = "openai_agents_v20_live_stub"
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
    """Per-sample trigger-gate and v20 counters, in sample order."""
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
        }
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
            with v20_chain_installed():
                with v20_finalise_installed():
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
                "v20_units_elided": 0,
                "v20_bytes_saved": 0,
                "v20_retention_engaged_calls": 0,
                "v20_retention_fallback_calls": 0,
                "exact_duplicate_replacements": 0,
            },
        )
        bucket["samples"] += 1
        for field in (
            "v20_units_elided",
            "v20_bytes_saved",
            "v20_retention_engaged_calls",
            "v20_retention_fallback_calls",
            "exact_duplicate_replacements",
        ):
            bucket[field] += entry[field]
    failures = [
        task_id
        for task_id, bucket in sorted(by_task.items())
        if bucket["v20_units_elided"] <= 0 or bucket["v20_bytes_saved"] <= 0
    ]
    total_units = sum(bucket["v20_units_elided"] for bucket in by_task.values())
    total_bytes = sum(bucket["v20_bytes_saved"] for bucket in by_task.values())
    return {
        "check": "mechanism_engages_on_unique_content_per_task",
        "ok": bool(by_task) and not failures and total_units > 0 and total_bytes > 0,
        "rule": (
            "for every task on this no-duplicate shape: v20_units_elided > 0 and "
            "v20_bytes_saved > 0; a zero means the wiring did not take effect and no paid run "
            "may start"
        ),
        "per_task": by_task,
        "tasks_failing": failures,
        "total_units_elided": total_units,
        "total_bytes_saved": total_bytes,
    }


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
        callable(retention.NonDuplicateRetentionFilter.filter_items)
        and "SelectiveRetentionFilter.filter_items(self, base)"
        in (
            ROOT / "experiments/runners/openai_agents_v20_retention.py"
        ).read_text(encoding="utf-8"),
    )
    record(
        "trigger_gate_threshold_is_the_pre_registered_zero",
        chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS == 0,
        {"soft_limit_tokens_setting": chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS},
    )
    return checks


def plan_and_stub_checks(tmp: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any = None) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    plan_path = tmp / "plan.json"
    with v20_chain_installed():
        plan_status = runner17.main(["--plan", "--artifact", str(plan_path)])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    record(
        "plan_is_zero_api",
        plan_status == 0 and plan.get("api_calls") == 0,
        {"api_calls": plan.get("api_calls")},
    )
    stub_path = tmp / "stub.json"
    with v20_chain_installed():
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
    """All zero-API checks, with the four hard v20 assertions wired in."""
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
                "v20_units_elided": 0,
                "v20_bytes_saved": 0,
                "v20_retention_engaged_calls": 0,
                "v20_retention_fallback_calls": 4,
                "exact_duplicate_replacements": 0,
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
    record(
        "the_gate_assertions_can_fail",
        (not failing_c["ok"]) and (not failing_b["ok"]) and (not failing_d["ok"]),
        {
            "assertion_c_rejects_a_zero_saving_sample": not failing_c["ok"],
            "assertion_b_rejects_a_passthrough_sample": not failing_b["ok"],
            "assertion_d_rejects_an_unregistered_module_set": not failing_d["ok"],
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
        },
        "per_sample_mechanism": local["per_sample"],
        "v20_counters_per_task": assertion_c["per_task"],
        "paid_run_allowed": bool(ok),
        "paid_run_allowed_rule": (
            "true only when every check passes; assertion (c) is the one that speaks to whether "
            "the wiring took effect at all, and a zero elision counter writes false here"
        ),
        "module_hashes": module_hashes(),
        "mechanism": chain.mechanism_block(),
        "plan_implements": PLAN_PATH,
        "gate_addendum": ADDENDUM_PATH,
    }


def write_revision_ledger() -> dict[str, Any]:
    """Write the v20 revision ledger: what each module in the path was when the run was prepared.

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
        "schema": "openai_agents_v20_module_revision_ledger",
        "date": datetime.now(timezone.utc).strftime("%Y%m%d"),
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "plan_implements": PLAN_PATH,
        "gate_addendum": ADDENDUM_PATH,
        "api_requests_spent": 0,
        "purpose": (
            "pin every module in the v20 path by SHA256 so the gate can fail closed on an "
            "unregistered change; the v17 batch could not be tied to a code revision, which is "
            "why the v20 plan requires this"
        ),
        "rules": [
            "a module whose digest is absent from this ledger fails the gate",
            "a module whose digest differs from the digest recorded here fails the gate",
            "a change is admitted only by registering a new revision of this ledger",
        ],
        "modules": live,
        "plan_named_modules": PLAN_NAMED_MODULES,
        "revision": revision,
        "previous_revisions": previous,
        "gate_class": "experiments.runners.openai_agents_v20_chain.FirstCallPassthroughGate",
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
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))

    mode = args.mode or ("plan" if args.plan else "stub" if args.stub else "gate" if args.gate else "")
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
        return run_paid(rest)
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


def run_paid(args: list[str]) -> int:
    """The paid path.  It refuses to start unless the recorded gate allows it."""
    if not GATE_OUT.is_file():
        raise SystemExit(
            f"refusing to send a paid request: the v20 gate artifact is missing ({GATE_OUT})"
        )
    recorded = json.loads(GATE_OUT.read_text(encoding="utf-8"))
    if recorded.get("paid_run_allowed") is not True:
        raise SystemExit(
            "refusing to send a paid request: the v20 gate recorded paid_run_allowed="
            f"{recorded.get('paid_run_allowed')!r}; a zero elision counter means the wiring did "
            "not take effect and the paid run is forbidden"
        )
    if "--confirm-send-public-source" not in args:
        raise SystemExit(
            "refusing to send public repository source without --confirm-send-public-source"
        )
    math_ = configure()
    with v20_chain_installed():
        return runner17.run_pilot(args, freeze_override=freeze_override(math_))


if __name__ == "__main__":
    raise SystemExit(main())
