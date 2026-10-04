"""Public-source v5 diagnosis with evidence-safe retention and per-sample persistence.

v5 keeps the frozen v1/v2/v3/v4 runners, protocols, freezes and result
directories untouched:

* the entrypoint is new and writes a new batch id;
* the plugin arm installs :class:`SelectiveRetentionFilter` around the shared
  trigger gate, and compression eligibility now consults the **registered
  literal constraint set** instead of "has this text already been delivered";
* evidence is recorded at both SDK boundaries with the v5 schema, including the
  per-output eligibility manifest, the registered-literal counts per boundary,
  ``task_anchor_restore_failures`` and the protected-group hashes;
* ``--offline-gate`` runs the identical code path against a local stub model so
  the whole grid can be exercised without a provider request;
* the manifest is honest about the payload: these are public SWE-bench issue
  statements and public baseline source ranges, not synthetic data.

The shared runner module is never edited: it is hash-frozen in the v1-v4 freeze
files and their audits verify those digests.  This entrypoint therefore wraps the
case object instead of adding a field to the shared dataclass.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_literal_registry_v5 as registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import token_policy
from experiments.runners.openai_agents_evidence_v5 import (
    EvidenceSafeModelProxy,
    EvidenceSafeRetentionRecorder,
)
from experiments.runners.openai_agents_evidence_safe_retention_v5 import (
    DEFAULT_HARD_LIMIT_BYTES,
    GateAwareSelectiveRetention,
    SelectiveRetentionFilter,
)
from experiments.runners.trigger_gate import BudgetTriggeredFilter

HOST = "openai_agents"

DISCLOSURE = (
    "Public SWE-bench issue statements and selected public baseline source "
    "ranges are sent through real SDK tools. No reference patches, host test "
    "patches, local secrets, or expected answers are sent. The plugin arm pins "
    "every registered literal constraint - including those that appear only "
    "inside a source-code tool output - verbatim in the final model input, keeps "
    "the most recent tool group in full, and only ever replaces an already "
    "delivered, pointer-addressable tool output text with a note that states its "
    "file path and line range."
)

#: Estimated input bytes per token used to derive the filter's byte ceiling from
#: the arm's provider hard budget.  Fixed here and frozen in the protocol so the
#: ceiling is a pre-registered number, not a runtime guess.
BYTES_PER_ESTIMATED_TOKEN = 4

MANIFEST_EXTRA = {
    "kind": "openai_agents_runner_real_api_paired_evidence_safe_retention",
    "data_class": "public_source_diagnostic",
    "disclosure": DISCLOSURE,
    "mechanism": {
        "arm": "pruner_v1",
        "name": "evidence_safe_retention_v5",
        "registry": registry.REGISTRY_SCHEMA,
        "eligibility": (
            "registered literal set first: a tool output carrying a registered "
            "literal constraint is pinned verbatim; then the most recent tool "
            "group; then already-delivered text with a parseable path/line pointer"
        ),
        "protected": (
            "registered literal units and literal-carrying tool outputs, the most "
            "recent tool group, and every Responses tool group boundary"
        ),
        "reduction": (
            "exact-duplicate units; an already-delivered non-constraint tool output "
            "with a registered path/line range replaced by a pointer note"
        ),
        "fallback": (
            "whole-payload fallback for the model call, counted in "
            "task_anchor_restore_failures (hard) or budget_fallbacks (budget)"
        ),
        "deterministic": "no model, no randomness, no text rewriting",
    },
}

#: Per-sample counters this runner persists explicitly.
PERSISTED_RETENTION_FIELDS = (
    "task_anchor_restore_failures",
    "selective_retention_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_fallbacks_by_model_call",
    "budget_fallbacks_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "input_evidence_task_anchor_restore_failures",
    "selective_retention_compacted_calls",
    "selective_retention_compacted_tool_outputs",
    "selective_retention_duplicate_units_dropped",
    "selective_retention_saved_bytes_total",
    "selective_retention_task_unit_count",
    "selective_retention_literal_unit_count",
    "selective_retention_literal_text_count",
    "selective_retention_constraint_unit_count",
    "selective_retention_protected_unit_count",
    "selective_retention_protected_group_count",
    "selective_retention_pinned_outputs",
    "selective_retention_pinned_by_reason",
    "selective_retention_elided_outputs",
    "selective_retention_notes_with_source_pointer",
    "selective_retention_pointer_coverage",
    "selective_retention_recent_group_items_kept",
    "selective_retention_recent_group_kept_in_full",
    "selective_retention_literal_guard_fallbacks",
    "selective_retention_registry_fingerprint",
    "selective_retention_fallback_reasons",
    "selective_retention_last_fallback_reason",
    # Evidence-layer derived facts, recomputed from the persisted evidence file.
    "evidence_registered_literals_present_final",
    "evidence_registered_literal_counts_final",
    "evidence_constraint_carrying_outputs_final",
    "evidence_pinned_outputs_final",
    "evidence_outputs_replaced_by_note_final",
    "evidence_note_pointer_coverage_final",
    "evidence_most_recent_tool_group_kept_in_full_final",
    "evidence_output_manifest_final",
)


def filter_hard_bytes(args: list[str]) -> int:
    """Byte ceiling for one filtered payload, derived from the provider budget.

    The arm budget is declared in provider tokens and converted to estimator
    units with the host's measured calibration ratio
    (``experiments/runners/token_policy.py``).  The filter's ceiling is the same
    physical budget expressed in bytes: estimator units times
    ``BYTES_PER_ESTIMATED_TOKEN``.  Both steps are pre-registered, so the ceiling
    is a frozen number rather than a runtime guess.
    """
    provider_hard = _int_option(args, "--provider-hard", 0)
    if provider_hard <= 0:
        return DEFAULT_HARD_LIMIT_BYTES
    estimated_hard = token_policy.provider_to_estimated(HOST, provider_hard)
    return int(estimated_hard) * BYTES_PER_ESTIMATED_TOKEN


class _CaseWithTaskStatement:
    """Delegate to the frozen ``ApiCase`` and add the registered task statement."""

    def __init__(self, case: Any, task_statement: str) -> None:
        self.__dict__["_wrapped"] = case
        self.__dict__["task_statement"] = str(task_statement)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_wrapped"], name)


#: (scenario, repeat) -> case carrying the registered task statement.
REGISTERED_CASES: dict[tuple[str, int], Any] = {}


def task_statement(case: Any) -> str:
    """The literal text whose units must survive filtering, per scenario."""
    parts = [v1.issue_text(case.scenario)]
    parts.extend(
        str(item.get("content", "")) for item in case.history if item.get("role") == "user"
    )
    return "\n".join(part for part in parts if part)


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the v5 filter for this public task in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = SelectiveRetentionFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        hard_limit_bytes=int(filter_hard_bytes or DEFAULT_HARD_LIMIT_BYTES),
    )
    if isinstance(context_filter, BudgetTriggeredFilter):
        # The shared trigger gate stays installed, with evidence-safe retention
        # wrapped *around* it so the retention memory sees every model input,
        # including the calls the gate passes through untouched.
        context_filter.inner = retention
        return GateAwareSelectiveRetention(context_filter, retention)
    return retention


def _registered_case(case: Any) -> Any:
    """Return the case carrying the registered task statement, if one exists."""
    if hasattr(case, "task_statement"):
        return case
    registered = REGISTERED_CASES.get(
        (str(getattr(case, "scenario", "")), int(getattr(case, "repeat", 0)))
    )
    return registered if registered is not None else case


def build_filter_with_recorder(
    original,
    method: str,
    case: Any,
    recorder: EvidenceSafeRetentionRecorder,
    *,
    filter_hard_bytes: int = 0,
    **kwargs,
):
    """Build the arm filter and attach the v5 evidence observers."""
    statement = str(getattr(case, "task_statement", "") or "")
    if method == "pruner_v1" and not statement.strip():
        raise RuntimeError(
            f"registered task statement is empty: type={type(case).__name__} "
            f"scenario={getattr(case, 'scenario', '')!r} keys={sorted(getattr(case, '__dict__', {}))}"
        )
    candidate = build_retentive_filter(
        original, method, case=case, filter_hard_bytes=filter_hard_bytes, **kwargs
    )
    if isinstance(candidate, GateAwareSelectiveRetention):
        recorder.retention = candidate.retention
        candidate.observer_before = recorder.observe_filter_before
        candidate.observer_after = recorder.observe_filter_after
    return candidate


@contextmanager
def evidence_wiring(output: Path, hard_bytes: int):
    """Persist v5 evidence for every sample of one run.

    Installed *outside* the SDK runner, so it also wraps the model the runner
    builds when it records a failure (that model is the one that reports usage).
    """
    original_filter = base._build_filter
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    active: dict[str, Any] = {}

    def build_filter(method: str, *, case, **kwargs):
        recorder = EvidenceSafeRetentionRecorder(case.scenario)
        active["key"] = (case.scenario, case.repeat, method)
        active["recorder"] = recorder
        return build_filter_with_recorder(
            original_filter,
            method,
            _registered_case(case),
            recorder,
            filter_hard_bytes=hard_bytes,
            **kwargs,
        )

    def recording_model(*args, **kwargs):
        return EvidenceSafeModelProxy(original_model(*args, **kwargs), active["recorder"])

    async def run_case(case, *, method: str, context_filter=None, gate_model=None, **kwargs):
        key = (case.scenario, case.repeat, method)
        if active.get("key") != key:
            raise RuntimeError("evidence-safe retention recorder does not match sample")
        recorder = active["recorder"]
        wrapped = _registered_case(case)
        if gate_model is not None:
            gate_model = EvidenceSafeModelProxy(gate_model, recorder)
        try:
            result = await original_case(
                wrapped, method=method, context_filter=context_filter, gate_model=gate_model, **kwargs
            )
        finally:
            evidence = output / "input-evidence" / f"{case.scenario}-{case.repeat}-{method}.jsonl"
            recorder.save(evidence)
        result["input_evidence_file"] = str(evidence.relative_to(output)).replace("\\", "/")
        result["input_evidence_model_calls"] = recorder.model_calls
        result["input_evidence_filter_calls"] = recorder.filter_calls
        result.update(_retention_counters(recorder, context_filter))
        result.update(_evidence_derived(result, recorder))
        return result

    base._build_filter = build_filter
    base.RecordingRetryModel = recording_model
    base.run_case = run_case
    try:
        yield
    finally:
        base._build_filter = original_filter
        base.RecordingRetryModel = original_model
        base.run_case = original_case


def _count(values: Any) -> int:
    """Length of a possibly-absent sequence, without truthiness shortcuts."""
    try:
        return len(values)
    except TypeError:  # pragma: no cover - defensive
        return 0


def _retention_counters(
    recorder: EvidenceSafeRetentionRecorder, context_filter: Any
) -> dict[str, Any]:
    """Per-sample counters taken from the live filter and the per-sample evidence."""
    retention = getattr(context_filter, "retention", None) or recorder.retention
    if retention is not None and not getattr(retention, "task_units", None):
        raise RuntimeError(
            "evidence-safe retention filter built without a registered task statement: "
            f"filter={type(context_filter).__name__} task={getattr(retention, 'task', '')!r} "
            f"units={len(getattr(retention, 'task_units', []))}"
        )
    if retention is not None and getattr(retention, "registry_problems", None):
        raise RuntimeError(
            f"literal registry did not verify for this task: {retention.registry_problems}"
        )
    metrics = context_filter.metrics_dict() if hasattr(context_filter, "metrics_dict") else {}
    failures = int(getattr(retention, "task_anchor_restore_failures", 0))
    fallbacks = int(getattr(retention, "task_restore_fallbacks", 0))
    budget = int(getattr(retention, "budget_fallbacks", 0))
    by_call = list(recorder.restore_failures_by_model_call)
    if retention is not None and not by_call:
        raise RuntimeError("restore-failure ledger missing from the evidence file")
    if retention is not None and by_call[-1] != failures:
        raise RuntimeError("restore-failure ledger disagreement between filter and evidence")
    return {
        "task_anchor_restore_failures": failures,
        "selective_retention_restore_failures": failures,
        "task_restore_fallbacks": fallbacks,
        "budget_fallbacks": budget,
        "restore_failures_by_model_call": by_call,
        "restore_fallbacks_by_model_call": list(recorder.restore_fallbacks_by_model_call),
        "budget_fallbacks_by_model_call": list(recorder.budget_fallbacks_by_model_call),
        # A restore failure replaces the filtered payload with the observed input
        # for that whole model call; it is never a partial restore.
        "restore_failure_is_whole_prefix_fallback": True,
        "input_evidence_task_anchor_restore_failures": by_call[-1] if by_call else 0,
        "selective_retention_compacted_calls": int(
            metrics.get("selective_retention_compacted_calls", 0)
        ),
        "selective_retention_compacted_tool_outputs": int(
            metrics.get("selective_retention_compacted_tool_outputs", 0)
        ),
        "selective_retention_duplicate_units_dropped": int(
            metrics.get("selective_retention_duplicate_units_dropped", 0)
        ),
        "selective_retention_saved_bytes_total": int(
            metrics.get("selective_retention_saved_bytes_total", 0)
        ),
        "selective_retention_task_unit_count": _count(getattr(retention, "task_units", ())),
        "selective_retention_literal_unit_count": _count(
            getattr(retention, "literal_units", ())
        ),
        "selective_retention_literal_text_count": _count(
            getattr(retention, "literal_texts", ())
        ),
        "selective_retention_constraint_unit_count": _count(
            getattr(retention, "constraint_units", ())
        ),
        "selective_retention_protected_unit_count": _count(
            getattr(retention, "protected_unit_sha256", ())
        ),
        "selective_retention_protected_group_count": int(
            metrics.get("selective_retention_protected_group_count", 0)
        ),
        "selective_retention_pinned_outputs": int(
            metrics.get("selective_retention_pinned_outputs", 0)
        ),
        "selective_retention_pinned_by_reason": dict(
            metrics.get("selective_retention_pinned_by_reason", {})
        ),
        "selective_retention_elided_outputs": int(
            metrics.get("selective_retention_elided_outputs", 0)
        ),
        "selective_retention_notes_with_source_pointer": int(
            metrics.get("selective_retention_notes_with_source_pointer", 0)
        ),
        "selective_retention_pointer_coverage": bool(
            metrics.get("selective_retention_pointer_coverage", False)
        ),
        "selective_retention_recent_group_items_kept": int(
            metrics.get("selective_retention_recent_group_items_kept", 0)
        ),
        "selective_retention_recent_group_kept_in_full": bool(
            metrics.get("selective_retention_recent_group_kept_in_full", False)
        ),
        "selective_retention_literal_guard_fallbacks": int(
            metrics.get("selective_retention_literal_guard_fallbacks", 0)
        ),
        "selective_retention_registry_fingerprint": str(
            metrics.get("selective_retention_registry_fingerprint", "")
        ),
        "selective_retention_fallback_reasons": dict(metrics.get("fallback_reasons", {})),
        "selective_retention_last_fallback_reason": str(
            metrics.get("last_fallback_reason", "")
        ),
    }


def _evidence_derived(
    result: dict[str, Any], recorder: EvidenceSafeRetentionRecorder
) -> dict[str, Any]:
    """Read the persisted evidence back and state the v5 guarantees per sample.

    These fields are recomputed from the evidence records, not from the live
    objects, so the row states what a reader can independently re-derive.
    """
    model = [record for record in recorder.records if record.get("stage") == "model_input"]
    final = model[-1] if model else {}
    manifest = list(final.get("output_manifest", []))
    carriers = [entry for entry in manifest if entry.get("registered_literals")]
    notes = [entry for entry in manifest if entry.get("output_replaced_by_note")]
    return {
        "evidence_registered_literals_present_final": dict(
            final.get("registered_literals_present", {})
        ),
        "evidence_registered_literal_counts_final": dict(
            final.get("registered_literal_counts", {})
        ),
        "evidence_constraint_carrying_outputs_final": len(carriers),
        "evidence_pinned_outputs_final": sum(
            1 for entry in manifest if entry.get("pinned")
        ),
        "evidence_outputs_replaced_by_note_final": len(notes),
        "evidence_note_pointer_coverage_final": bool(
            final.get("note_pointer_coverage", False)
        ),
        "evidence_most_recent_tool_group_kept_in_full_final": bool(
            final.get("most_recent_tool_group_kept_in_full", False)
        ),
        "evidence_output_manifest_final": manifest,
        "evidence_model_input_records": len(model),
        "evidence_registry_fingerprint": str(final.get("registry_fingerprint", "")),
        "evidence_final_input_sha256": str(final.get("input_sha256", "")),
        "evidence_compaction_note_count_final": int(final.get("compaction_note_count", 0)),
    }


@contextmanager
def offline_gate_wiring():
    """Replace the provider model and client with local stubs for one run."""
    helpers = _gate_helpers()

    original_client = base.AsyncOpenAI
    holder: dict[str, Any] = {}

    class OfflineStubClient(helpers.StubAsyncOpenAI):
        """Stub client bound to the request budget the runner actually created."""

        def __init__(self) -> None:
            holder.setdefault("budget", base.RequestBudget(10**6))
            super().__init__(holder["budget"])

    client_holder: dict[str, Any] = {}

    def stub_client(**_kwargs):
        return client_holder.setdefault("client", OfflineStubClient())

    def wrap_model(inner, request_budget, **_kwargs):
        return helpers.StubApiModel(holder["case"], request_budget=request_budget)

    base.AsyncOpenAI = stub_client
    holder["model_factory"] = wrap_model
    try:
        yield holder
    finally:
        base.AsyncOpenAI = original_client


def _install_offline_model(holder: dict[str, Any]) -> None:
    """Point the runner's provider model at the local stub for this run."""

    def build(_inner, request_budget, **_kwargs):
        return holder["model_factory"](None, request_budget)

    base.RecordingRetryModel = build


def _gate_helpers():
    """Load the shared zero-API gate helpers once, in a stable module slot."""
    import importlib.util
    import sys

    name = "experiments.runners._v5_offline_gate_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        name, root / ".tooling" / "gate_openai_agents_3arm.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("offline gate helper is missing")
    helpers = importlib.util.module_from_spec(spec)
    sys.modules[name] = helpers
    spec.loader.exec_module(helpers)
    return helpers


def main(argv=None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline-gate" in args
    if offline:
        args = [item for item in args if item != "--offline-gate"]
    if "--confirm-send-synthetic-data" in args:
        raise SystemExit("use --confirm-send-public-source for these public repository tasks")

    problems = registry.all_problems()
    if problems:
        raise SystemExit("literal registry did not verify: " + "; ".join(problems))

    hard_bytes = filter_hard_bytes(args)
    output_root = _output_root(args)
    original_build_case = base.build_case
    original_disclosure = base.SYNTHETIC_DISCLOSURE
    offline_holder: dict[str, Any] | None = None

    def case_builder(scenario: str, repeat: int):
        key = (str(scenario), int(repeat))
        if key not in REGISTERED_CASES:
            base_case = v1.build_case(key[0], key[1])
            REGISTERED_CASES[key] = _CaseWithTaskStatement(
                base_case, task_statement(base_case)
            )
        wrapped = REGISTERED_CASES[key]
        if offline_holder is not None:
            offline_holder["case"] = wrapped
        return wrapped

    base.SCENARIOS = v1.TASKS
    base.build_case = case_builder
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    for repeat in range(max(1, _int_option(args, "--repeats", 1))):
        for scenario in _scenario_options(args):
            case_builder(scenario, repeat)
    # The offline gate never contacts a provider, so it must not require a real
    # credential; the placeholder lives in this process only and is never written
    # to a file, a log line or a result row.
    injected_key = False
    if offline and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        os.environ["DEEPSEEK_API_KEY"] = "offline-gate-not-a-credential"
        injected_key = True
    try:
        if offline:
            case_builder("django_count_annotations", 0)
            with offline_gate_wiring() as holder:
                holder["case"] = case_builder("django_count_annotations", 0)
                offline_holder = holder
                _install_offline_model(holder)
                status = _run(args, hard_bytes)
        else:
            status = _run(args, hard_bytes)
    finally:
        base.build_case = original_build_case
        base.SYNTHETIC_DISCLOSURE = original_disclosure
        if injected_key:
            del os.environ["DEEPSEEK_API_KEY"]
    _amend_manifest(output_root, hard_bytes)
    return status


def _int_option(args: list[str], flag: str, default: int) -> int:
    if flag in args:
        index = args.index(flag)
        if index + 1 < len(args):
            try:
                return int(args[index + 1])
            except ValueError:  # pragma: no cover - argparse reports this too
                return default
    return default


def _scenario_options(args: list[str]) -> list[str]:
    if "--scenarios" in args:
        index = args.index("--scenarios")
        if index + 1 < len(args):
            return [part.strip() for part in args[index + 1].split(",") if part.strip()]
    return list(v1.TASKS)


def _run(args, hard_bytes: int) -> int:
    """Run the repo-diagnostic entrypoint with the v5 evidence wiring installed."""
    root = _output_root(args)
    if root is None:  # pragma: no cover - argument errors surface in the runner
        return v1.main(list(args))
    with evidence_wiring(root, hard_bytes):
        return v1.main(list(args))


def _output_root(args) -> Path | None:
    """Best-effort path of the batch directory this invocation writes to."""
    try:
        parsed = base.build_parser().parse_args(
            [
                "--confirm-send-synthetic-data"
                if item == "--confirm-send-public-source"
                else item
                for item in args
            ]
        )
    except SystemExit:  # pragma: no cover - argument errors surface in the runner
        return None
    return Path(parsed.out) / parsed.experiment_id


def _amend_manifest(root: Path | None, hard_bytes: int) -> None:
    """Record the honest payload class and the mechanism identity on the manifest."""
    if root is None:
        return
    path = root / "manifest.json"
    if not path.is_file():
        return
    import json

    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(MANIFEST_EXTRA)
    manifest["shared_runner_disclosure_amended"] = True
    manifest["persisted_retention_fields"] = list(PERSISTED_RETENTION_FIELDS)
    manifest["filter_hard_bytes"] = int(hard_bytes)
    manifest["bytes_per_estimated_token"] = BYTES_PER_ESTIMATED_TOKEN
    manifest["filter_hard_bytes_derivation"] = (
        "provider_hard / measured provider-per-estimated ratio "
        f"({token_policy.ratio_for(HOST)}) * {BYTES_PER_ESTIMATED_TOKEN} bytes per estimated token"
    )
    manifest["literal_registry"] = {
        "schema": registry.REGISTRY_SCHEMA,
        "fingerprints": {
            task: registry.registry_fingerprint(task) for task in sorted(registry.SOURCES)
        },
        "problems": registry.all_problems(),
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
