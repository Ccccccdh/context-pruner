"""Public-source v8 diagnosis: the long-baseline side of the applicability boundary.

v7 measured the short side: on the frozen Django task the baseline finishes in K = 2
model calls, so with the frozen recency window N = 1 the plugin has ``K - 1 - N = 0``
elidable turns and sends a payload byte-identical to the baseline's.  v8 measures the
other side by lengthening the *task input* - not the mechanism:

* the task is the same public issue and the same three public baseline source ranges,
  extended with a pre-registered read-only investigation protocol that asks for six
  separate, one-tool-per-turn reads before the answer, so the baseline runs to
  K ~= 10-12 model calls;
* the plugin arm is v7's recency-restricted retention (same N = 1), the same v6
  reduction, the same guards, the same whole-payload fallback, the same budgets;
* ``max(0, K - 1 - N)`` is therefore positive and the module can actually elide the
  older turns without touching the newest one.

Batch id: ``openai-repo-diagnostic-v8-long-baseline-boundary``.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from experiments.runners import openai_agents_literal_registry_v6 as base_registry
from experiments.runners import openai_agents_long_baseline_boundary_v8 as policy
from experiments.runners import openai_agents_long_task_registry_v8 as long_registry
from experiments.runners import run_openai_agents_api_experiment as base
from experiments.runners import run_openai_agents_repo_diagnostic_v1 as v1
from experiments.runners import token_policy
from experiments.runners.trigger_gate import BudgetTriggeredFilter

HOST = "openai_agents"

#: True while this process runs with ``--offline-gate``: a local stub model answers and
#: no provider request is sent.  Recorded on the manifest so a reader can tell a paid
#: batch from an offline grid without trusting the directory name.
OFFLINE_GATE = False

#: Optional recorder class override.  ``None`` means this module's own recorder; v9 sets it
#: to its narrowed-guard recorder so the two versions differ in one place only.
RECORDER_FACTORY: Any = None

DISCLOSURE = (
    "Public SWE-bench issue statements, a pre-registered read-only investigation "
    "protocol, and selected public baseline source ranges are sent through real SDK "
    "tools. No reference patches, host test patches, local secrets, or expected answers "
    "are sent. The plugin arm applies the plugin's registered-span line-range reduction "
    "ONLY to tool turns at least one turn older than the newest one; the newest turn and "
    "every model message are sent verbatim."
)

BYTES_PER_ESTIMATED_TOKEN = 4

MANIFEST_EXTRA = {
    "kind": "openai_agents_runner_real_api_paired_long_baseline_boundary",
    "data_class": "public_source_diagnostic",
    "disclosure": DISCLOSURE,
    "mechanism": {
        "arm": "pruner_v1",
        "name": "long_baseline_boundary_v8",
        "schema": policy.MECHANISM_SCHEMA,
        "registry": long_registry.REGISTRY_SCHEMA,
        "base_registry": base_registry.REGISTRY_SCHEMA,
        **policy.policy_dict(),
        "purpose": (
            "measure the long-baseline side of the applicability boundary: with "
            "K - 1 - N > 0 the reduction has older turns it may compress without "
            "changing what the newest turn sees"
        ),
    },
    "task_input_change": {
        "long_task_id": policy.LONG_TASK_ID,
        "extends": policy.SHORT_TASK_ID,
        "investigation_steps": list(long_registry.INVESTIGATION_STEPS),
        "note": (
            "only the task input changes: the same public issue, the same three public "
            "baseline source ranges, the same literals, the same mechanism"
        ),
    },
}

PERSISTED_RETENTION_FIELDS = (
    "long_baseline_task_id",
    "long_baseline_recent_turns_kept_verbatim",
    "long_baseline_registry_schema",
    "long_baseline_registry_fingerprint",
    "long_baseline_base_registry_fingerprint",
    "long_baseline_registry_problems",
    "long_baseline_literal_unit_count",
    "long_baseline_elided_sources",
    "long_baseline_baseline_model_calls",
    "long_baseline_baseline_input_tokens",
    "long_baseline_baseline_payload_sha256_by_call",
    "long_baseline_plugin_payload_sha256_by_call",
    "long_baseline_payload_identical_to_baseline",
    "long_baseline_shared_boundary_count",
    "long_baseline_elidable_turns_available",
    "long_baseline_plugin_can_elide_anything",
    "long_baseline_plugin_wins_possible",
    "long_baseline_boundary_proposition",
    "long_baseline_regime",
    "long_baseline_turn_count_max",
    "task_anchor_restore_failures",
    "task_restore_fallbacks",
    "budget_fallbacks",
    "restore_failures_by_model_call",
    "restore_fallback_by_model_call",
    "restore_failure_is_whole_prefix_fallback",
    "selective_retention_pointer_coverage",
    "selective_retention_elided_outputs",
    "selective_retention_literal_guard_fallbacks",
    "selective_retention_fallback_reasons",
    "pointer_retention_elided_sources",
    "pointer_retention_elided_lines",
    "pointer_retention_kept_literal_lines",
    "pointer_retention_registry_fingerprint",
    "pointer_retention_base_registry_fingerprint",
    "evidence_registered_literals_present_final",
    "evidence_registered_literal_counts_final",
    "evidence_registered_literals_present_by_boundary",
    "evidence_pointer_coverage_final",
    "evidence_pointer_completeness_final",
    "evidence_span_elision_records",
    "evidence_output_manifest_final",
    "evidence_input_bytes_by_boundary",
    "evidence_recency_decisions",
    "evidence_payload_sha256_by_call",
)


def offline_gate_used() -> bool:
    return bool(OFFLINE_GATE)


def filter_hard_bytes(args: list[str]) -> int:
    provider_hard = _int_option(args, "--provider-hard", 0)
    if provider_hard <= 0:
        return 65536
    return int(token_policy.provider_to_estimated(HOST, provider_hard)) * BYTES_PER_ESTIMATED_TOKEN


#: (scenario, repeat) -> case carrying the long task statement.
REGISTERED_CASES: dict[tuple[str, int], dict[str, Any]] = {}


def protocol_message() -> str:
    """The investigation protocol, sent as its own user message.

    It is worded so that **no line of it repeats text that already exists elsewhere in
    the payload**, and it is a separate message so the frozen issue and request messages
    stay byte-identical to the Django task.  Both properties matter for the same reason:
    the frozen reduction drops *repeated* message units, and a zero-API measurement of
    the long-baseline side must not be blocked by that rule firing on text the mechanism
    was never meant to be measured on.  (The first v8 offline grid hit exactly that: the
    protocol message opened with the request line the task ships with, the repeat rule
    dropped the duplicate, the protected-unit check reported ``missing_protected_unit``
    and every call fell back, so nothing could be compressed.)
    """
    return (
        "Read the three baseline views twice, strictly one tool call per turn, and "
        "answer only after the sixth read returns.\n"
        + "\n".join(
            f"Turn {index}: {step.split(': ', 1)[1]}"
            for index, step in enumerate(long_registry.INVESTIGATION_STEPS, start=1)
        )
    )


def task_statement(case: Any) -> str:
    """The registered task statement: the frozen issue text plus the frozen request.

    The investigation protocol is **not** part of it.  Registered units come from this
    statement and are protected verbatim, so registering the protocol would protect the
    seven STEP lines as well - and since the message carrying them is part of the frozen
    history the mechanism never rebuilds them, the protection would be untestable.  The
    protocol therefore travels as its own user message (see :func:`build_case`) and the
    registered units stay exactly the ones v4-v7 were measured against.
    """
    del case
    return long_registry.task_statement(
        policy.LONG_TASK_ID,
        v1.issue_text(policy.SHORT_TASK_ID),
        str(v1._META[policy.SHORT_TASK_ID]["request"]),
    )


def registered_statement() -> str:
    """The same statement, computed without a case object."""
    return long_registry.task_statement(
        policy.LONG_TASK_ID,
        v1.issue_text(policy.SHORT_TASK_ID),
        str(v1._META[policy.SHORT_TASK_ID]["request"]),
    )


#: The frozen v1 Django case builder, captured before this module patches it.  v1's
#: ``main`` reads ``v1.build_case``, so the v8 entrypoint has to substitute its own case
#: builder there; this reference is what lets it build the *underlying* Django case
#: without recursing into itself.
_ORIGINAL_BUILD_CASE = v1.build_case


def build_case(scenario: str, repeat: int):
    """Build one v8 case: the frozen Django case plus one protocol message.

    The history keeps the frozen issue statement and request verbatim and appends the
    investigation protocol as an additional user message, so the baseline is longer while
    every literal the frozen contracts depend on is still carried by the same, unmodified
    text units.
    """
    base_case = _ORIGINAL_BUILD_CASE(policy.SHORT_TASK_ID, repeat)
    statement = registered_statement()
    history = list(base_case.history) + [
        {"role": "user", "content": protocol_message()}
    ]
    case = base.ApiCase(
        scenario=policy.LONG_TASK_ID,
        repeat=repeat,
        codename=base_case.codename,
        history=history,
        tools=base_case.tools,
        expected_terms=base_case.expected_terms,
        expected_tool_names=base_case.expected_tool_names,
        expected_model_calls=base_case.expected_model_calls,
        final_contract=base_case.final_contract,
        allow_repeat_tools=base_case.allow_repeat_tools,
        answer_pattern=base_case.answer_pattern,
        disable_thinking=base_case.disable_thinking,
    )
    REGISTERED_CASES[(policy.LONG_TASK_ID, int(repeat))] = {
        "case": case,
        "statement": statement,
    }
    return _CaseWithTaskStatement(case, statement)


class _CaseWithTaskStatement:
    def __init__(self, case: Any, task_statement: str) -> None:
        self.__dict__["_wrapped"] = case
        self.__dict__["task_statement"] = str(task_statement)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.__dict__["_wrapped"], name)


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install the v8 recency-restricted filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    context_filter = original(method, **kwargs)
    retention = policy.LongBaselineBoundaryFilter(
        task=str(getattr(case, "scenario", "")),
        task_statement=str(getattr(case, "task_statement", "")),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    if isinstance(context_filter, BudgetTriggeredFilter):
        context_filter.inner = retention
        return _GateAware(context_filter, retention)
    return retention


class _GateAware:
    """The v5 gate-aware wrapper, reused without editing the frozen module."""

    def __init__(self, gate: Any, retention: Any) -> None:
        self.gate = gate
        self.retention = retention
        self.observer_before: Any = None
        self.observer_after: Any = None

    async def __call__(self, data: Any) -> Any:
        import inspect

        model_data = data.model_data
        items = list(model_data.input or [])
        instructions = model_data.instructions
        self.retention.observe(items)
        self.retention.observe_delivered(items)
        index = -1
        if self.observer_before is not None:
            index = self.observer_before(items, instructions)
        result = self.gate(data)
        if inspect.isawaitable(result):
            result = await result
        if self.observer_after is not None:
            outputs = list(getattr(result, "input", None) or items)
            self.observer_after(outputs, instructions, index)
        return result

    def metrics_dict(self) -> dict[str, Any]:
        metrics = dict(self.gate.metrics_dict()) if hasattr(self.gate, "metrics_dict") else {}
        metrics.update(self.retention.metrics_dict())
        return metrics


def build_filter_with_recorder(
    original,
    method: str,
    case: Any,
    recorder: Any,
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
    if isinstance(candidate, _GateAware):
        recorder.retention = candidate.retention
        candidate.observer_before = recorder.observe_filter_before
        candidate.observer_after = recorder.observe_filter_after
    return candidate


@contextmanager
def evidence_wiring(output: Path, hard_bytes: int, recorder_factory: Any = None):
    """Persist v8 evidence for every sample.

    ``recorder_factory`` lets a later version (v9) supply its own recorder without this
    module being edited for it; the default is this module's own recorder.
    """
    from experiments.runners.openai_agents_evidence_v8 import (
        LongBaselineRecorder,
        PointerRetentionModelProxy,
    )

    make_recorder = recorder_factory or LongBaselineRecorder

    original_filter = base._build_filter
    original_model = base.RecordingRetryModel
    original_case = base.run_case
    active: dict[str, Any] = {}

    def build_filter(method: str, *, case, **kwargs):
        recorder = make_recorder(case.scenario)
        active["key"] = (case.scenario, case.repeat, method)
        active["recorder"] = recorder
        return build_filter_with_recorder(
            original_filter,
            method,
            case,
            recorder,
            filter_hard_bytes=hard_bytes,
            **kwargs,
        )

    def recording_model(*args, **kwargs):
        return PointerRetentionModelProxy(original_model(*args, **kwargs), active["recorder"])

    async def run_case(case, *, method: str, context_filter=None, gate_model=None, **kwargs):
        key = (case.scenario, case.repeat, method)
        if active.get("key") != key:
            raise RuntimeError("long-baseline recorder does not match sample")
        recorder = active["recorder"]
        if gate_model is not None:
            gate_model = PointerRetentionModelProxy(gate_model, recorder)
        try:
            result = await original_case(
                case,
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


def _count(values: Any) -> int:
    try:
        return len(values)
    except TypeError:  # pragma: no cover - defensive
        return 0


def _retention_counters(recorder: Any, context_filter: Any) -> dict[str, Any]:
    retention = getattr(context_filter, "retention", None) or recorder.retention
    if retention is not None and not getattr(retention, "task_units", None):
        raise RuntimeError("long-baseline filter built without a registered task statement")
    problems = list(getattr(retention, "registry_problems", [])) + list(
        getattr(retention, "long_registry_problems", [])
    )
    if problems:
        raise RuntimeError(f"v8 registration did not verify for this task: {problems}")
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
        "long_baseline_task_id": str(getattr(retention, "task", "")),
        "long_baseline_recent_turns_kept_verbatim": int(
            metrics.get("long_baseline_recent_turns_kept_verbatim", policy.RECENT_TURNS_KEPT)
        ),
        "long_baseline_registry_schema": str(
            metrics.get("long_baseline_registry_schema", "")
        ),
        "long_baseline_registry_fingerprint": str(
            metrics.get("long_baseline_registry_fingerprint", "")
        ),
        "long_baseline_base_registry_fingerprint": str(
            metrics.get("long_baseline_base_registry_fingerprint", "")
        ),
        "long_baseline_registry_problems": list(
            metrics.get("long_baseline_registry_problems", [])
        ),
        "long_baseline_literal_unit_count": int(
            metrics.get("long_baseline_literal_unit_count", 0)
        ),
        "long_baseline_elided_sources": int(
            metrics.get("long_baseline_elided_sources", 0)
        ),
        "long_baseline_turn_count_max": int(
            metrics.get("selective_retention_recent_group_items_kept", 0)
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
        "selective_retention_literal_guard_fallbacks": int(
            metrics.get("selective_retention_literal_guard_fallbacks", 0)
        ),
        "selective_retention_fallback_reasons": dict(metrics.get("fallback_reasons", {})),
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
        "selective_retention_saved_bytes_total": int(
            metrics.get("selective_retention_saved_bytes_total", 0)
        ),
    }


def _payload_hashes(recorder: Any) -> list[str]:
    return [
        str(record.get("input_sha256", ""))
        for record in recorder.records
        if record.get("stage") == "model_input"
    ]


def _evidence_derived(result: dict[str, Any], recorder: Any) -> dict[str, Any]:
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
    del result
    return {
        "long_baseline_plugin_payload_sha256_by_call": _payload_hashes(recorder),
        "long_baseline_baseline_payload_sha256_by_call": [],
        "long_baseline_baseline_model_calls": 0,
        "long_baseline_baseline_input_tokens": 0,
        "long_baseline_shared_boundary_count": 0,
        "long_baseline_payload_identical_to_baseline": None,
        "long_baseline_elidable_turns_available": None,
        "long_baseline_plugin_can_elide_anything": None,
        "long_baseline_plugin_wins_possible": None,
        "long_baseline_boundary_proposition": {},
        "long_baseline_regime": "",
        "evidence_payload_sha256_by_call": _payload_hashes(recorder),
        "evidence_registered_literals_present_final": dict(
            final.get("registered_literals_present", {})
        ),
        "evidence_registered_literal_counts_final": dict(
            final.get("registered_literal_counts", {})
        ),
        "evidence_registered_literals_present_by_boundary": [
            {
                "index": record.get("index"),
                "present": dict(record.get("registered_literals_present", {})),
                "counts": dict(record.get("registered_literal_counts", {})),
            }
            for record in model
        ],
        "evidence_pointer_coverage_final": bool(final.get("pointer_coverage", False)),
        "evidence_pointer_completeness_final": bool(
            final.get("pointer_completeness", False)
        ),
        "evidence_span_elision_records": span_records,
        "evidence_output_manifest_final": manifest,
        "evidence_outputs_elided_final": len(elided),
        "evidence_input_bytes_by_boundary": [
            {
                "stage": record.get("stage"),
                "index": record.get("index"),
                "input_bytes": int(record.get("input_bytes", 0)),
            }
            for record in recorder.records
        ],
        "evidence_recency_decisions": [
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
        ],
        "evidence_model_input_records": len(model),
        "evidence_base_registry_fingerprint": str(
            final.get("base_registry_fingerprint", "")
        ),
        "evidence_registry_fingerprint": str(final.get("registry_fingerprint", "")),
    }


def annotate_boundary_columns(batch: Path) -> dict[str, Any]:
    """Add the cross-arm boundary columns to a finished batch, then rewrite its files."""
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
            list(baseline.get("evidence_payload_sha256_by_call") or []) if baseline else []
        )
        plugin_hashes = list(plugin.get("evidence_payload_sha256_by_call") or [])
        shared = min(len(baseline_hashes), len(plugin_hashes))
        identical = shared > 0 and baseline_hashes[:shared] == plugin_hashes[:shared]
        baseline_calls = int(baseline.get("model_calls", 0)) if baseline else 0
        baseline_tokens = int(baseline.get("actual_input_tokens", 0)) if baseline else 0
        proposition = policy.long_task_boundary(baseline_calls, baseline_tokens)
        annotation = {
            "long_baseline_baseline_model_calls": baseline_calls,
            "long_baseline_baseline_input_tokens": baseline_tokens,
            "long_baseline_elidable_turns_available": proposition[
                "elidable_turns_available_to_the_plugin"
            ],
            "long_baseline_plugin_can_elide_anything": proposition[
                "plugin_can_elide_anything"
            ],
            "long_baseline_plugin_wins_possible": proposition["plugin_wins_possible"],
            "long_baseline_regime": proposition["regime"],
            "long_baseline_baseline_payload_sha256_by_call": baseline_hashes,
            "long_baseline_plugin_payload_sha256_by_call": plugin_hashes,
            "long_baseline_payload_identical_to_baseline": identical,
            "long_baseline_shared_boundary_count": shared,
            "long_baseline_boundary_proposition": proposition,
        }
        for method in arms:
            annotations[(scenario, repeat, method)] = dict(annotation)
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
        report["long_baseline_boundary"] = boundary_by_pair
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return boundary_by_pair


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
        return helpers.LongBaselineStub(holder["case"], request_budget=request_budget)

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
    """Load the v8 zero-API gate helper (one tool call per turn, per the protocol)."""
    import importlib.util
    import sys

    name = "experiments.runners._v8_offline_gate_helpers"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        name, root / ".tooling" / "gate_openai_agents_long_baseline_v8.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise SystemExit("v8 offline gate helper is missing")
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

    for scenario in (policy.LONG_TASK_ID, policy.SHORT_TASK_ID):
        problems = long_registry.verify(
            scenario,
            task_statement(build_case(scenario, 0))
            if scenario == policy.LONG_TASK_ID
            else None,
        )
        if problems:
            raise SystemExit("v8 registration did not verify: " + "; ".join(problems))

    hard_bytes = filter_hard_bytes(args)
    output_root = _output_root(args)
    original_build_case = base.build_case
    original_disclosure = base.SYNTHETIC_DISCLOSURE
    offline_holder: dict[str, Any] | None = None

    def case_builder(scenario: str, repeat: int):
        wrapped = build_case(scenario, repeat)
        if offline_holder is not None:
            offline_holder["case"] = wrapped
        return wrapped

    base.SCENARIOS = (policy.LONG_TASK_ID,)
    base.build_case = case_builder
    base.SYNTHETIC_DISCLOSURE = DISCLOSURE
    # ``v1.main`` rewrites ``base.build_case``/``base.SCENARIOS`` from its own module-level
    # names and validates ``--scenarios`` against ``v1.TASKS``, so the v8 task id and the
    # v8 case builder have to be visible there too.  Both are restored in the ``finally``
    # block below.
    original_v1_tasks = v1.TASKS
    original_v1_build_case = v1.build_case
    v1.TASKS = (policy.LONG_TASK_ID,)
    v1.build_case = case_builder
    for repeat in range(max(1, _int_option(args, "--repeats", 1))):
        for scenario in _scenario_options(args):
            case_builder(scenario, repeat)
    injected_key = False
    if offline and not (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")):
        os.environ["DEEPSEEK_API_KEY"] = "offline-gate-not-a-credential"
        injected_key = True
    try:
        if offline:
            with offline_gate_wiring() as holder:
                holder["case"] = case_builder(policy.LONG_TASK_ID, 0)
                offline_holder = holder
                _install_offline_model(holder)
                status = _run(args, hard_bytes)
        else:
            status = _run(args, hard_bytes)
    finally:
        base.build_case = original_build_case
        base.SYNTHETIC_DISCLOSURE = original_disclosure
        v1.TASKS = original_v1_tasks
        v1.build_case = original_v1_build_case
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
    return [policy.LONG_TASK_ID]


def _run(args, hard_bytes: int) -> int:
    root = _output_root(args)
    if root is None:  # pragma: no cover - argument errors surface in the runner
        return v1.main(list(args))
    with evidence_wiring(root, hard_bytes, recorder_factory=RECORDER_FACTORY):
        return v1.main(list(args))


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
            "this batch was produced with --offline-gate against a local stub model; no "
            "provider request was sent and api_request_attempts counts local stub calls "
            "only"
        )
    manifest["v8_registry"] = long_registry.manifest_block()
    manifest["literal_registry"] = {
        "schema": base_registry.REGISTRY_SCHEMA,
        "base_schema": base_registry.base.REGISTRY_SCHEMA,
        "fingerprints": {policy.SHORT_TASK_ID: base_registry.registry_fingerprint(
            policy.SHORT_TASK_ID
        )},
        "base_fingerprints": {
            policy.SHORT_TASK_ID: base_registry.base_registry_fingerprint(policy.SHORT_TASK_ID)
        },
        "problems": base_registry.all_problems([policy.SHORT_TASK_ID]),
    }
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    raise SystemExit(main())
