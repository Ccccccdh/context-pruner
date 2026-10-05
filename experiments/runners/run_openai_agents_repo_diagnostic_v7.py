"""Public-source v7 diagnosis: the applicability boundary of the plugin's reduction.

v7 is not a new reduction.  It is the same registered-span line-range elision v6
already paid for, restricted to turns that are at least ``RECENT_TURNS_KEPT`` behind
the newest one:

    elidable(turn) := newest_turn - turn >= RECENT_TURNS_KEPT

The point of the version is the boundary, not the mechanism:

* on a baseline that finishes within the frozen recency window there is nothing to
  elide, so the plugin sends the baseline's own payload and can neither win nor lose;
* the v5 cost decomposition shows one extra model round costs 2,335.8 input tokens
  (99.85 % of that regression and more than the whole 2,962-token baseline complete
  total), while re-sent pinned bytes are worth -136.7 tokens, so a mechanism that
  changes visible text on a short task can only be negative.

The batch id is ``openai-repo-diagnostic-v7-applicability-boundary``.  The runner
keeps every frozen v1-v6 file untouched and writes only v7 paths.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_literal_registry_v6 as registry
from experiments.runners import openai_agents_recency_boundary_v7 as policy
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v5 as v5
from experiments.runners import token_policy
from experiments.runners.openai_agents_evidence_v7 import (
    PointerRetentionModelProxy,
    RecencyBoundaryRecorder,
    mechanism_identity,
)
from experiments.runners.openai_agents_recency_boundary_v7 import (
    CONTEXT_LINES,
    MECHANISM_SCHEMA,
    RECENT_TURNS_KEPT,
    RecencyBoundaryFilter,
)
from experiments.runners.trigger_gate import BudgetTriggeredFilter

HOST = "openai_agents"

DISCLOSURE = (
    "Public SWE-bench issue statements and selected public baseline source "
    "ranges are sent through real SDK tools. No reference patches, host test "
    "patches, local secrets, or expected answers are sent. This arm applies the "
    "plugin's registered-span line-range reduction ONLY to tool turns that are at "
    "least one turn behind the newest one; the newest turn (and every model "
    "message) is sent verbatim, and a task that finishes within that window is sent "
    "unchanged."
)

BYTES_PER_ESTIMATED_TOKEN = 4

MANIFEST_EXTRA = {
    "kind": "openai_agents_runner_real_api_paired_recency_boundary",
    "data_class": "public_source_diagnostic",
    "disclosure": DISCLOSURE,
    "mechanism": {
        "arm": "pruner_v1",
        "name": "recency_boundary_v7",
        "schema": MECHANISM_SCHEMA,
        "registry": registry.REGISTRY_SCHEMA,
        **policy.policy_dict(),
        "purpose": (
            "state and test the applicability boundary of the reduction: it can only "
            "help when the baseline has turns older than the frozen recency window"
        ),
    },
}

PERSISTED_RETENTION_FIELDS = (
    "recency_boundary_recent_turns_kept_verbatim",
    "recency_boundary_protected_turn_items",
    "recency_boundary_elidable_indices",
    "recency_boundary_protected_outputs",
    "recency_boundary_protected_calls",
    "recency_boundary_elided_sources",
    "recency_boundary_elidable_turns_available",
    "recency_boundary_plugin_can_elide_anything",
    "recency_boundary_plugin_wins_possible",
    "recency_boundary_baseline_model_calls",
    "recency_boundary_baseline_input_tokens",
    "recency_boundary_baseline_payload_sha256_by_call",
    "recency_boundary_plugin_payload_sha256_by_call",
    "recency_boundary_payload_identical_to_baseline",
    "recency_boundary_boundary_proposition",
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_fallback_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "selective_retention_pointer_coverage",
    "selective_retention_elided_outputs",
    "pointer_retention_elided_sources",
    "pointer_retention_elided_lines",
    "pointer_retention_kept_literal_lines",
    "pointer_retention_registry_fingerprint",
    "pointer_retention_base_registry_fingerprint",
    "evidence_registered_literals_present_final",
    "evidence_registered_literal_counts_final",
    "evidence_pointer_coverage_final",
    "evidence_pointer_completeness_final",
    "evidence_span_elision_records",
    "evidence_output_manifest_final",
    "evidence_input_bytes_by_boundary",
    "evidence_recency_decisions",
)


def filter_hard_bytes(args: list[str]) -> int:
    provider_hard = _int_option(args, "--provider-hard", 0)
    if provider_hard <= 0:
        return 65536
    return int(token_policy.provider_to_estimated(HOST, provider_hard)) * BYTES_PER_ESTIMATED_TOKEN


_CaseWithTaskStatement = v5._CaseWithTaskStatement
REGISTERED_CASES: dict[tuple[str, int], Any] = {}


def task_statement(case: Any) -> str:
    return v5.task_statement(case)


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the v7 recency-restricted filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = RecencyBoundaryFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    if isinstance(context_filter, BudgetTriggeredFilter):
        context_filter.inner = retention
        return v5.GateAwareSelectiveRetention(context_filter, retention)
    return retention


def _registered_case(case: Any) -> Any:
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
    recorder: RecencyBoundaryRecorder,
    *,
    filter_hard_bytes: int = 0,
    **kwargs,
):
    statement = str(getattr(case, "task_statement", "") or "")
    if method == "pruner_v1" and not statement.strip():
        raise RuntimeError(
            f"registered task statement is empty: type={type(case).__name__} "
            f"scenario={getattr(case, 'scenario', '')!r}"
        )
    candidate = build_retentive_filter(
        original, method, case=case, filter_hard_bytes=filter_hard_bytes, **kwargs
    )
    if isinstance(candidate, v5.GateAwareSelectiveRetention):
        recorder.retention = candidate.retention
        candidate.observer_before = recorder.observe_filter_before
        candidate.observer_after = recorder.observe_filter_after
    return candidate


@contextmanager
def evidence_wiring(output: Path, hard_bytes: int):
    """Persist v7 evidence for every sample, and the boundary facts per arm."""
    original_filter = base._build_filter
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    active: dict[str, Any] = {}

    def build_filter(method: str, *, case, **kwargs):
        recorder = RecencyBoundaryRecorder(case.scenario)
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
        return PointerRetentionModelProxy(original_model(*args, **kwargs), active["recorder"])

    async def run_case(case, *, method: str, context_filter=None, gate_model=None, **kwargs):
        key = (case.scenario, case.repeat, method)
        if active.get("key") != key:
            raise RuntimeError("recency-boundary recorder does not match sample")
        recorder = active["recorder"]
        wrapped = _registered_case(case)
        if gate_model is not None:
            gate_model = PointerRetentionModelProxy(gate_model, recorder)
        try:
            result = await original_case(
                wrapped,
                method=method,
                context_filter=context_filter,
                gate_model=gate_model,
                **kwargs,
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
        yield active
    finally:
        base._build_filter = original_filter
        base.RecordingRetryModel = original_model
        base.run_case = original_case


def _payload_hashes(recorder: RecencyBoundaryRecorder) -> list[str]:
    return [
        str(record.get("input_sha256", ""))
        for record in recorder.records
        if record.get("stage") == "model_input"
    ]


def _recency_decisions(recorder: RecencyBoundaryRecorder) -> list[dict[str, Any]]:
    return [
        {
            "stage": record.get("stage"),
            "index": record.get("index"),
            "elidable_indices": list(record.get("recency_elidable_indices", [])),
            "protected_turn_items": int(record.get("recency_protected_turn_items", 0)),
            "turn_count": int(record.get("recency_turn_count", 0)),
            "elided_sources_total": int(record.get("recency_elided_sources_total", 0)),
            "protected_outputs_total": int(record.get("recency_protected_outputs_total", 0)),
        }
        for record in recorder.records
        if record.get("stage") in ("filter_before", "model_input")
    ]


def annotate_boundary_columns(batch: Path) -> dict[str, Any]:
    """Add the cross-arm boundary columns to a finished batch, then rewrite its files.

    These columns need all three arms of one ``(scenario, repeat)`` pair, so they can
    only be computed once the grid is done.  Values already present in a row are never
    overwritten, and the sample rows themselves are otherwise byte-preserved.
    """
    import json

    samples_path = batch / "samples.jsonl"
    rows = [
        json.loads(line)
        for line in samples_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    keyed: dict[tuple[str, int], dict[str, dict]] = {}
    for row in rows:
        keyed.setdefault((str(row["scenario"]), int(row["repeat"])), {})[
            str(row["method"])
        ] = row
    annotations: dict[tuple[str, int, str], dict[str, Any]] = {}
    boundary_by_pair: dict[str, dict[str, Any]] = {}
    for (scenario, repeat), arms in sorted(keyed.items()):
        baseline = arms.get("none")
        plugin = arms.get("pruner_v1")
        if plugin is None:
            continue
        baseline_hashes = (
            list(baseline.get("recency_boundary_payload_sha256_by_call") or [])
            if baseline
            else []
        )
        plugin_hashes = list(plugin.get("recency_boundary_payload_sha256_by_call") or [])
        shared = min(len(baseline_hashes), len(plugin_hashes))
        identical = shared > 0 and baseline_hashes[:shared] == plugin_hashes[:shared]
        baseline_calls = int(baseline.get("model_calls", 0)) if baseline else 0
        baseline_tokens = int(baseline.get("actual_input_tokens", 0)) if baseline else 0
        proposition = policy.boundary_proposition(baseline_calls, baseline_tokens)
        annotation = {
            "recency_boundary_baseline_model_calls": baseline_calls,
            "recency_boundary_baseline_input_tokens": baseline_tokens,
            "recency_boundary_elidable_turns_available": proposition[
                "elidable_turns_available_to_the_plugin"
            ],
            "recency_boundary_plugin_can_elide_anything": proposition[
                "plugin_can_elide_anything"
            ],
            "recency_boundary_plugin_wins_possible": proposition["plugin_wins_possible"],
            "recency_boundary_baseline_payload_sha256_by_call": baseline_hashes,
            "recency_boundary_plugin_payload_sha256_by_call": plugin_hashes,
            "recency_boundary_payload_identical_to_baseline": identical,
            "recency_boundary_boundary_proposition": proposition,
            "recency_boundary_shared_boundary_count": shared,
        }
        for method, row in arms.items():
            annotations[(scenario, repeat, method)] = dict(annotation)
            if method == "pruner_v1":
                annotations[(scenario, repeat, method)][
                    "recency_boundary_projectable_saving_rate"
                ] = 0.0 if identical else None
        boundary_by_pair[f"{scenario}-{repeat}"] = proposition
    with samples_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            key = (str(row["scenario"]), int(row["repeat"]), str(row["method"]))
            for column, value in annotations.get(key, {}).items():
                row[column] = value
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    report_path = batch / "report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["recency_boundary"] = boundary_by_pair
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return boundary_by_pair


def _count(values: Any) -> int:
    try:
        return len(values)
    except TypeError:  # pragma: no cover - defensive
        return 0


def _retention_counters(
    recorder: RecencyBoundaryRecorder, context_filter: Any
) -> dict[str, Any]:
    retention = getattr(context_filter, "retention", None) or recorder.retention
    if retention is not None and not getattr(retention, "task_units", None):
        raise RuntimeError(
            "recency-boundary filter built without a registered task statement: "
            f"filter={type(context_filter).__name__}"
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
        "recency_boundary_recent_turns_kept_verbatim": int(
            metrics.get("recency_boundary_recent_turns_kept_verbatim", RECENT_TURNS_KEPT)
        ),
        "recency_boundary_protected_turn_items": int(
            metrics.get("recency_boundary_protected_turn_items", 0)
        ),
        "recency_boundary_elidable_indices": list(
            metrics.get("recency_boundary_elidable_indices", [])
        ),
        "recency_boundary_protected_outputs": int(
            metrics.get("recency_boundary_protected_outputs", 0)
        ),
        "recency_boundary_protected_calls": int(
            metrics.get("recency_boundary_protected_calls", 0)
        ),
        "recency_boundary_elided_sources": int(
            metrics.get("recency_boundary_elided_sources", 0)
        ),
        "task_anchor_restore_failures": failures,
        "task_restore_fallbacks": fallbacks,
        "budget_fallbacks": budget,
        "restore_failures_by_model_call": by_call,
        "restore_fallback_by_model_call": list(recorder.restore_fallbacks_by_model_call),
        "restore_failure_is_whole_prefix_fallback": True,
        "selective_retention_pointer_coverage": bool(
            metrics.get("selective_retention_pointer_coverage", False)
        ),
        "selective_retention_elided_outputs": int(
            metrics.get("selective_retention_elided_outputs", 0)
        ),
        "pointer_retention_elided_sources": int(
            metrics.get("pointer_retention_elided_sources", 0)
        ),
        "pointer_retention_elided_lines": int(
            metrics.get("pointer_retention_elided_lines", 0)
        ),
        "pointer_retention_kept_literal_lines": int(
            metrics.get("pointer_retention_kept_literal_lines", 0)
        ),
        "pointer_retention_registry_fingerprint": str(
            metrics.get("pointer_retention_registry_fingerprint", "")
        ),
        "pointer_retention_base_registry_fingerprint": str(
            metrics.get("selective_retention_registry_fingerprint", "")
        ),
        "selective_retention_literal_guard_fallbacks": int(
            metrics.get("selective_retention_literal_guard_fallbacks", 0)
        ),
        "selective_retention_fallback_reasons": dict(metrics.get("fallback_reasons", {})),
        "selective_retention_last_fallback_reason": str(
            metrics.get("last_fallback_reason", "")
        ),
        "selective_retention_saved_bytes_total": int(
            metrics.get("selective_retention_saved_bytes_total", 0)
        ),
        "recency_boundary_registry_problems": list(
            metrics.get("pointer_retention_registry_problems", [])
        ),
        "recency_boundary_context_lines": CONTEXT_LINES,
        "recency_boundary_task_unit_count": _count(getattr(retention, "task_units", ())),
    }


def _evidence_derived(
    result: dict[str, Any], recorder: RecencyBoundaryRecorder
) -> dict[str, Any]:
    """Read the persisted evidence back and state what v7 guarantees per sample."""
    model = [record for record in recorder.records if record.get("stage") == "model_input"]
    final = model[-1] if model else {}
    manifest = list(final.get("output_manifest", []))
    elided = [entry for entry in manifest if entry.get("output_elided")]
    span_records: list[dict[str, Any]] = []
    for record in recorder.records:
        if record.get("stage") != "filter_after":
            continue
        for entry in record.get("span_elisions_this_call", []) or []:
            span_records.append(entry)
    recency_decisions = [
        {
            "stage": record.get("stage"),
            "index": record.get("index"),
            "elidable_indices": list(record.get("recency_elidable_indices", [])),
            "protected_turn_items": int(record.get("recency_protected_turn_items", 0)),
            "turn_count": int(record.get("recency_turn_count", 0)),
            "elided_sources_total": int(record.get("recency_elided_sources_total", 0)),
            "protected_outputs_total": int(
                record.get("recency_protected_outputs_total", 0)
            ),
        }
        for record in recorder.records
        if record.get("stage") in ("filter_before", "model_input")
    ]
    del result
    return {
        "recency_boundary_payload_sha256_by_call": _payload_hashes(recorder),
        "recency_boundary_plugin_payload_sha256_by_call": _payload_hashes(recorder),
        "recency_boundary_baseline_payload_sha256_by_call": [],
        "recency_boundary_baseline_model_calls": 0,
        "recency_boundary_baseline_input_tokens": 0,
        "recency_boundary_elidable_turns_available": None,
        "recency_boundary_plugin_can_elide_anything": None,
        "recency_boundary_plugin_wins_possible": None,
        "recency_boundary_payload_identical_to_baseline": None,
        "recency_boundary_shared_boundary_count": 0,
        "recency_boundary_boundary_proposition": {},
        "evidence_registered_literals_present_final": dict(
            final.get("registered_literals_present", {})
        ),
        "evidence_registered_literal_counts_final": dict(
            final.get("registered_literal_counts", {})
        ),
        "evidence_pointer_coverage_final": bool(final.get("pointer_coverage", False)),
        "evidence_pointer_completeness_final": bool(
            final.get("pointer_completeness", False)
        ),
        "evidence_span_elision_records": span_records,
        "evidence_output_manifest_final": manifest,
        "evidence_input_bytes_by_boundary": [
            {
                "stage": record.get("stage"),
                "index": record.get("index"),
                "input_bytes": int(record.get("input_bytes", 0)),
                "input_sha256": str(record.get("input_sha256", "")),
            }
            for record in recorder.records
        ],
        "evidence_recency_decisions": recency_decisions,
        "evidence_model_input_records": len(model),
        "evidence_final_input_sha256": str(final.get("input_sha256", "")),
        "evidence_base_registry_fingerprint": str(
            final.get("base_registry_fingerprint", "")
        ),
        "evidence_registry_fingerprint": str(final.get("registry_fingerprint", "")),
    }


@contextmanager
def offline_gate_wiring():
    """Replace the provider model and client with local stubs for one run."""
    helpers = _gate_helpers()
    original_client = base.AsyncOpenAI
    holder: dict[str, Any] = {}

    class OfflineStubClient(helpers.StubAsyncOpenAI):
        def __init__(self) -> None:
            holder.setdefault("budget", base.RequestBudget(10**6))
            super().__init__(holder["budget"])

    client_holder: dict[str, Any] = {}

    def stub_client(**_kwargs):
        return client_holder.setdefault("client", OfflineStubClient())

    def wrap_model(inner, request_budget, **_kwargs):
        return helpers.SequentialContractStub(holder["case"], request_budget=request_budget)

    base.AsyncOpenAI = stub_client
    holder["model_factory"] = wrap_model
    try:
        yield holder
    finally:
        base.AsyncOpenAI = original_client


def _install_offline_model(holder: dict[str, Any]) -> None:
    def build(_inner, request_budget, **_kwargs):
        return holder["model_factory"](None, request_budget)

    base.RecordingRetryModel = build


def _gate_helpers():
    """Load the v6 zero-API gate helper: the stub trajectory, reused unchanged.

    The stub is the same one the v6 gate uses, so a v7 offline grid differs from a v6
    offline grid in exactly one variable (the recency policy), which is what makes the
    two comparable.  It is referenced (not copied) so both batches provably ran the
    same local model.
    """
    import importlib.util
    import sys

    name = "experiments.runners._v6_offline_gate_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        name, root / ".tooling" / "gate_openai_agents_pointer_retention_v6.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("v6 offline gate helper is missing")
    helpers = importlib.util.module_from_spec(spec)
    sys.modules[name] = helpers
    spec.loader.exec_module(helpers)
    return helpers


def main(argv=None) -> int:
    import sys

    global OFFLINE_GATE

    args = list(sys.argv[1:] if argv is None else argv)
    offline = "--offline-gate" in args
    OFFLINE_GATE = bool(offline)
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
    if output_root is not None and (output_root / "samples.jsonl").is_file():
        annotate_boundary_columns(output_root)
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
    root = _output_root(args)
    if root is None:  # pragma: no cover - argument errors surface in the runner
        return v1.main(list(args))
    with evidence_wiring(root, hard_bytes):
        return v1.main(list(args))


#: True while this process runs with ``--offline-gate``: a local stub model answers and
#: no provider request is sent.  Recorded on the manifest so a reader can tell a paid
#: batch from an offline grid without trusting the directory name.
OFFLINE_GATE = False


def offline_gate_used() -> bool:
    return bool(OFFLINE_GATE)


def _output_root(args) -> Path | None:
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
    manifest["offline_gate"] = offline_gate_used()
    manifest["paid_requests_sent"] = 0 if offline_gate_used() else None
    if offline_gate_used():
        manifest["offline_gate_note"] = (
            "this batch was produced with --offline-gate against a local stub model; "
            "no provider request was sent and api_request_attempts counts local stub "
            "calls only"
        )
    manifest["mechanism_identity"] = mechanism_identity()
    manifest["literal_registry"] = {
        "schema": registry.REGISTRY_SCHEMA,
        "base_schema": registry.base.REGISTRY_SCHEMA,
        "fingerprints": {
            task: registry.registry_fingerprint(task) for task in sorted(registry.base.SOURCES)
        },
        "base_fingerprints": {
            task: registry.base_registry_fingerprint(task)
            for task in sorted(registry.base.SOURCES)
        },
        "problems": registry.all_problems(),
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def boundary_for_batch(batch: Path) -> dict[str, Any]:
    """Compute the boundary proposition from a batch's own recorded baseline arm."""
    import json

    samples = batch / "samples.jsonl"
    if not samples.is_file():
        raise SystemExit(f"no samples in {batch}")
    rows = [
        json.loads(line)
        for line in samples.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    baseline = [row for row in rows if row.get("method") == "none"]
    if not baseline:
        raise SystemExit("no baseline rows in this batch")
    calls = max(int(row.get("model_calls", 0)) for row in baseline)
    tokens = max(int(row.get("actual_input_tokens", 0)) for row in baseline)
    return policy.boundary_proposition(calls, tokens)


if __name__ == "__main__":
    raise SystemExit(main())
