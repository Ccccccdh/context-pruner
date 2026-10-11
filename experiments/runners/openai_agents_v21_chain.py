"""v21 chain: the v20 chain with the literal-carrier line elision installed after the duplicate rule.

Two changes against ``openai_agents_v20_chain``, and nothing else:

1. **the plugin arm's filter** is :class:`~experiments.runners.openai_agents_v21_carrier_elision.
   LiteralCarrierElisionFilter`, which runs the frozen v12 exact duplicate rule, then the v5
   lineage evidence-safe selective retention, then the pre-registered line elision from
   ``integrations/openai_agents/V21_LITERAL_CARRIER_ELISION_PLAN_20261010.json``.  The elision
   stage is the only new mechanism; the two stages under it are v20's, called through v20's own
   ``_compose_base`` seam so no v20 counter is advanced twice.
2. **the gate threshold stays where v20 put it** and the gate behaviour is inherited unchanged:
   :class:`FirstCallPassthroughGate` with ``soft_limit_tokens = 0`` (floored to 1 by the frozen
   base constructor), so the gate fires on every model input after the first.  The v20 gate
   addendum measured that at the N3 shape the *original* threshold of 5435 passed two of three
   tasks straight through; v21 keeps the fixed threshold, because the whole point of this round
   is to measure a new mechanism and not to re-measure a gate.

Scope of the change
-------------------
The gate and the filter are installed in the plugin arm only.  The ``none`` arm installs no
filter at all and the ``native_summary`` arm keeps the arm filter with its own budget threshold,
so this module does not touch the baseline, and the paired within-batch comparison stays valid.

Comparability
-------------
v21 is **not** comparable as a whole configuration to v16, v19 or v20: the mechanism differs from
v20's and the gate differs from v16's and v19's.  Only its paired within-batch comparison is.

The frozen v14, v20 and v12 modules are not edited: this module imports them, imports the v21
filter, and installs its own runtime patches.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v8 as frozen_long
from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_multitask_registry_v14 as default_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as diagnostic_v8
from experiments.runners import trigger_gate as frozen_gate
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder
from experiments.runners.openai_agents_v20_chain import FirstCallPassthroughGate
from experiments.runners.openai_agents_v21_carrier_elision import (
    COMPOSITION_SCHEMA,
    MECHANISM_NAME,
    LiteralCarrierElisionFilter,
    WINDOW_LINES,
)

#: The registry this chain resolves tasks through.  The runner points it at the v17 registry
#: (``openai_agents_v17_registry``) before a sample is built.  Every lookup below goes through
#: this module-level name rather than through ``v14.registry``, so patching the v14 module - which
#: the frozen v17 runner also does for its own paths - cannot leave the recorder or the filter
#: reading the v14 task table.
registry = default_registry

#: Mechanism schema of this chain.  Distinct from every earlier one on purpose: a v21 row must
#: never be mistakable for a v14/v16/v19/v20 row.
MECHANISM_SCHEMA = "openai_agents_v21_carrier_elision_chain"
EVIDENCE_SCHEMA = "openai_literal_carrier_elision_v21"

#: The trigger threshold this chain installs in the plugin arm, inherited from v20 unchanged.
#: Zero is the intended setting; ``BudgetTriggeredFilter.__init__`` floors it with ``max(1, ...)``,
#: so the value that actually takes effect - and the value the gate reports in
#: ``trigger_gate_soft_limit_tokens`` - is 1.  Either way the gate's condition
#: ``last_estimated_tokens <= soft_limit_tokens`` is false for every payload that carries any
#: content, i.e. it fires on every model input after the first.  Lowering the threshold per task,
#: or after seeing a saving number, is forbidden by the v20 gate addendum.
TRIGGER_GATE_SOFT_LIMIT_TOKENS = 0

#: How the threshold above is described in artifacts, so a reader does not have to infer it.
TRIGGER_GATE_POLICY = (
    "the plugin arm's BudgetTriggeredFilter is installed with soft_limit_tokens=0, which the "
    "frozen class floors to 1 (trigger_gate_soft_limit_tokens therefore reports 1), so its "
    "condition last_estimated_tokens <= soft_limit_tokens is false for every non-empty payload "
    "and the gate fires on every model input after the first; the gate stays in place, keeps "
    "measuring each payload and keeps publishing its counters. The setting is inherited from "
    "v20 unchanged, so v21 measures a new mechanism and does not re-measure the gate. Trigger "
    "policy stays symmetric_budget so the wrapper is not removed from the plugin arm."
)

#: The v14 chain's persisted fields, plus the v21 counters, the v20 counters, the first-call gate
#: counter and the composition identity.  The v21 counters *must* appear here: a counter that is
#: not in this tuple never reaches a sample row, and a gate that read the filter object instead
#: would pass while the persisted batch showed nothing.
PERSISTED_EXTRA_FIELDS = tuple(v14.PERSISTED_EXTRA_FIELDS) + (
    "trigger_gate_first_call_passthrough_calls",
    "v20_mechanism_schema",
    "v20_composition_schema",
    "v20_retention_engaged_calls",
    "v20_retention_fallback_calls",
    "v20_units_elided",
    "v20_bytes_saved",
    "v21_mechanism_schema",
    "v21_composition_schema",
    "v21_window_lines",
    "v21_outputs_considered",
    "v21_outputs_elided",
    "v21_lines_elided",
    "v21_bytes_saved",
    "v21_literal_guard_fallbacks",
    "v21_note_bytes",
)

GUARD_SCHEMA = v14.GUARD_SCHEMA
MIN_UNIT_CHARS = v14.MIN_UNIT_CHARS


def register_task_data(task_id: str) -> None:
    """Runtime data registration so the frozen guard resolves this task's literals.

    Same body as the v14 helper, but resolving ``registry`` through this module, so the task
    table in force is the one the runner installed here.
    """
    from experiments.runners import openai_agents_literal_registry_v5 as frozen_v5
    from experiments.runners import openai_agents_literal_registry_v6 as frozen_v6

    constraints = frozen_v5.CONSTRAINTS
    if task_id not in constraints:
        constraints[task_id] = {
            label: {"literals": tuple(phrases), "units": tuple(phrases)}
            for label, phrases in registry.literal_phrases(task_id).items()
        }
    frozen_v5.SOURCES.setdefault(task_id, ())
    frozen_v6._SPAN_CACHE.setdefault(task_id, ())


def statement_units(task_statement: str) -> list[str]:
    return v14.statement_units(task_statement)


def protectable_units(task_id: str, task_statement: str) -> list[str]:
    phrases = [
        phrase.lower()
        for values in registry.literal_phrases(task_id).values()
        for phrase in values
    ]
    return [
        unit
        for unit in statement_units(task_statement)
        if any(phrase in unit.lower() for phrase in phrases)
    ]


class MultitaskRecorder(NarrowGuardRecorder):
    """The v14 recorder, with every registry lookup resolved through this module.

    It is a copy of ``openai_agents_multitask_chain_v14``'s recorder for the same reason the v21
    chain exists at all: the frozen v14 module binds its registry at import time and several
    frozen runners patch *that* binding, so a mechanism that must read the v17 task table needs
    its own binding rather than a shared one.
    """

    def __init__(self, task: str, retention: Any | None = None) -> None:
        register_task_data(str(task))
        super().__init__(frozen_long.SHORT_TASK_ID, retention)
        self.task = str(task)
        self.base_registry_fingerprint = registry.fingerprint(self.task)
        self.registry_fingerprint = registry.fingerprint(self.task)

    def _literal_counts(self, searchable: str) -> dict[str, int]:
        lowered = str(searchable).lower()
        return {
            label: sum(lowered.count(str(phrase).lower()) for phrase in phrases)
            for label, phrases in registry.literal_phrases(self.task).items()
        }

    def _snapshot(self, items, instructions, stage: str, index: int) -> dict[str, Any]:
        record = super()._snapshot(items, instructions, stage, index)
        entry = registry.task(self.task)
        record["schema"] = EVIDENCE_SCHEMA
        record.update(
            {
                "long_baseline_task_id": self.task,
                "long_baseline_registry_schema": registry.REGISTRY_SCHEMA,
                "long_baseline_registry_fingerprint": registry.fingerprint(self.task),
                "long_baseline_base_registry_fingerprint": registry.fingerprint(self.task),
                "long_baseline_source_registration_task": self.task,
                "long_baseline_investigation_steps": list(entry["protocol_steps"]),
                "long_baseline_literal_context_units": list(entry["required_terms"]),
                "heldout_instance_id": entry["instance_id"],
                "heldout_base_commit": entry["base_commit"],
                "narrow_guard_literal_labels": list(entry["literals"]),
                "registered_literal_counts": self._literal_counts(
                    "\n".join(_searchable_text(item) for item in items)
                ),
            }
        )
        return record


def _searchable_text(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("content", "output", "arguments"):
            value = item.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, list):
                parts = [
                    str(part.get("text", "")) for part in value if isinstance(part, dict)
                ]
                if any(parts):
                    return "\n".join(parts)
    return ""


def build_retentive_filter(original, method: str, *, filter_hard_bytes: int = 0, **kwargs):
    """Install this task's literal-carrier elision filter in the plugin arm only.

    The wrapped arm filter is built exactly as the frozen runner builds it, so the arc from the
    SDK down is unchanged; only the innermost filter and the gate's behaviour differ.
    """
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    wrapper = original(method, **kwargs)
    retention = LiteralCarrierElisionFilter(
        task=str(case.scenario),
        task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    # Fail loudly here rather than silently at run time: the v5 entry point this composite calls
    # reads a fixed attribute set, and the self check names every one that is absent.
    retention.self_check()
    if isinstance(wrapper, frozen_gate.BudgetTriggeredFilter):
        # Build a fresh gate of the v20 chain's class rather than mutating the frozen wrapper's
        # class in place: assigning ``__class__`` does not run the new class's ``__init__``, so
        # the extra counter it owns would not exist.  The frozen wrapper is discarded and a
        # configured gate is constructed instead, which is why the gate's own metrics are read
        # from the returned wrapper and not from the discarded one.
        gate = FirstCallPassthroughGate(
            retention,
            soft_limit_tokens=max(1, int(TRIGGER_GATE_SOFT_LIMIT_TOKENS)),
        )
        gate.first_call_passthrough_calls = 0
        return diagnostic_v8._GateAware(gate, retention)
    return retention


def extended_retention_counters(original, recorder: Any, context_filter: Any) -> dict[str, Any]:
    """The frozen counter builder plus the mechanism counters the audits assert on.

    ``original`` is ``openai_agents_repo_diagnostic_v8._retention_counters``, which raises when
    the retention filter was built without a registered task statement or when the
    restore-failure ledger disagrees with the filter.  Both checks stay in force.
    """
    counters = original(recorder, context_filter)
    metrics = context_filter.metrics_dict() if hasattr(context_filter, "metrics_dict") else {}
    extra = {}
    for name in PERSISTED_EXTRA_FIELDS:
        if name in metrics:
            extra[name] = metrics[name]
    counters.update(extra)
    counters["persisted_extra_fields"] = sorted(extra)
    return counters


def gate_counters(metrics: dict[str, Any]) -> dict[str, Any]:
    """The six trigger-gate counters, for the gate artifact and the per-sample report."""
    return {
        name: metrics.get(name)
        for name in (
            "trigger_gate_calls",
            "trigger_gate_triggered_calls",
            "trigger_gate_passthrough_calls",
            "trigger_gate_first_call_passthrough_calls",
            "trigger_gate_soft_limit_tokens",
            "trigger_gate_last_estimated_tokens",
        )
    }


def v21_counters(metrics: dict[str, Any]) -> dict[str, Any]:
    """The v21 counters, for the gate artifact and the per-sample report."""
    return {
        name: metrics.get(name)
        for name in (
            "v21_outputs_considered",
            "v21_outputs_elided",
            "v21_lines_elided",
            "v21_bytes_saved",
            "v21_literal_guard_fallbacks",
            "v21_note_bytes",
        )
    }


def v20_counters(metrics: dict[str, Any]) -> dict[str, Any]:
    """The v20 counters, kept so a v21 row reports both stages."""
    return {
        name: metrics.get(name)
        for name in (
            "v20_retention_engaged_calls",
            "v20_retention_fallback_calls",
            "v20_units_elided",
            "v20_bytes_saved",
        )
    }


@contextmanager
def chain_wiring():
    """Install the runtime patches the frozen modules cannot make themselves.

    Identical in shape to the v14 and v20 context managers, with this module's filter builder and
    counter builder.  Restores everything on exit, including on an exception.
    """
    from experiments.runners import run_openai_agents_api_experiment as base
    from experiments.runners import run_openai_agents_repo_diagnostic_v8 as v8

    original_counters = v8._retention_counters
    original_filter = v8.build_retentive_filter
    original_report = base.build_report

    def single_arm_report(samples):
        report = original_report(samples)
        paired = report.get("paired")
        if isinstance(paired, dict) and "paired_n" not in paired:
            report["paired"] = {
                "paired_n": 0,
                "actual_input_savings_rate_vs_baseline": 0.0,
                "success_delta": 0.0,
                **paired,
            }
            report["paired_note"] = (
                "single-arm payload-acquisition batch: no paired comparison exists, and none "
                "may be reported from this batch"
            )
        return report

    v8._retention_counters = (
        lambda recorder, context_filter: extended_retention_counters(
            original_counters, recorder, context_filter
        )
    )
    v8.build_retentive_filter = build_retentive_filter
    base.build_report = single_arm_report
    try:
        yield
    finally:
        v8._retention_counters = original_counters
        v8.build_retentive_filter = original_filter
        base.build_report = original_report


#: The disclosure required for this mechanism, written verbatim into every v21 manifest and
#: freeze.  The tool descriptions themselves are **not** changed: they are identical in all three
#: arms (the frozen registry builds them once per task), and rewriting them for the plugin arm
#: alone would make the arms asymmetric and contaminate the paired comparison.  What is disclosed
#: instead is that the plugin arm's reader output can be elided, explicitly annotated and
#: recoverable - so "the tool returns those numbered lines verbatim" holds for the lines that are
#: kept, and the gap is declared rather than silent.
TOOL_OUTPUT_DISCLOSURE = (
    "the plugin arm's read output may be elided: lines that carry no registered literal are "
    "removed and every maximal omitted run is replaced by exactly one explicit interval note of "
    "the form [elided n lines first-last], so the omission is declared and the omitted range is "
    "recoverable by re-issuing the read. Every registered literal keeps at least as many verbatim "
    "occurrences as it had before the elision; the reader tool's own description is identical in "
    "all three arms, because the frozen registry builds it once per task and v21 does not rewrite "
    "it for any arm, so 'returns those numbered lines verbatim' holds for every line that is kept. "
    "No silent omission occurs: nothing is removed without an interval note."
)

#: Wording the pre-registration forbids in a v21 artifact.
FORBIDDEN_WORDING = (
    "compression alone saved X percent",
    "the four criteria are met",
    "the retry tax is gone",
    "the plugin saves about 12.5 percent of context",
)


def mechanism_block() -> dict[str, Any]:
    """The mechanism description every v21 manifest carries."""
    return {
        "arm": "pruner_v1",
        "name": MECHANISM_NAME,
        "schema": MECHANISM_SCHEMA,
        "composition_schema": COMPOSITION_SCHEMA,
        "rule": (
            "the frozen v12 exact duplicate rule runs first; the v5 lineage evidence-safe "
            "selective retention then runs on the deduplicated list, with its registered-literal "
            "guard, its recovery-pointer requirement and its whole-payload fallback unchanged; "
            "the literal-carrier line elision then runs on that result, keeping verbatim every "
            "line that contains a registered literal or constraint phrase together with a fixed "
            "window of "
            f"{WINDOW_LINES} lines before and after it, eliding every other line and replacing "
            "each maximal contiguous omitted run with exactly one note line of the form "
            "[elided n lines first-last]; the most recent tool group is kept verbatim. Any "
            "registered-literal occurrence loss, any non-reduction after the note overhead, or "
            "any exception sends the payload this stage received verbatim and is counted in "
            "v21_literal_guard_fallbacks"
        ),
        "frozen_rule_module": "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
        "retention_module": "experiments/runners/openai_agents_evidence_safe_retention_v5.py",
        "v20_composite_module": "experiments/runners/openai_agents_v20_retention.py",
        "composite_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
        "chain_module": "experiments/runners/openai_agents_v21_chain.py",
        "chain_blueprint": "experiments/runners/openai_agents_multitask_chain_v14.py",
        "window_lines": WINDOW_LINES,
        "window_frozen": (
            "the protection window is three lines before and after every literal-carrying line; "
            "it was declared in the pre-registration (V21_LITERAL_CARRIER_ELISION_PLAN) and may "
            "not be changed after any saving number is seen"
        ),
        "invariant": (
            "for every registered literal and constraint phrase the number of verbatim "
            "occurrences after elision is greater than or equal to the number before it, per "
            "occurrence and not per distinct string, computed with the same v5 lineage "
            "_carrier_loss that already vetoed the older mechanism"
        ),
        "fail_closed": (
            "an output is kept verbatim whenever the mechanism cannot prove the invariant for "
            "the payload it would send; nothing is removed on the strength of an argument the "
            "mechanism cannot check, and the note lines it writes can never satisfy the "
            "invariant themselves"
        ),
        "trigger_gate": {
            "soft_limit_tokens": TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": TRIGGER_GATE_POLICY,
            "arm_scope": "plugin arm only; the none and native_summary arms are untouched",
            "comparability": (
                "because the mechanism and the gate both differ from earlier batches, v21 is not "
                "comparable as a whole configuration to v16, v19 or v20; only its paired "
                "within-batch comparison is"
            ),
        },
        "counter_names": {
            "considered": "v21_outputs_considered",
            "elided_outputs": "v21_outputs_elided",
            "elided_lines": "v21_lines_elided",
            "bytes": "v21_bytes_saved",
            "fallback": "v21_literal_guard_fallbacks",
            "note_overhead": "v21_note_bytes",
        },
        "tool_output_disclosure": TOOL_OUTPUT_DISCLOSURE,
        "tool_descriptions_unchanged": (
            "the reader tool descriptions are byte-identical in all three arms; this mechanism "
            "does not rewrite them for the plugin arm, because doing so would make the prompt "
            "asymmetric and contaminate the paired comparison"
        ),
        "forbidden_wording": list(FORBIDDEN_WORDING),
    }


__all__ = [
    "EVIDENCE_SCHEMA",
    "FORBIDDEN_WORDING",
    "FirstCallPassthroughGate",
    "MECHANISM_SCHEMA",
    "MultitaskRecorder",
    "PERSISTED_EXTRA_FIELDS",
    "TOOL_OUTPUT_DISCLOSURE",
    "TRIGGER_GATE_POLICY",
    "TRIGGER_GATE_SOFT_LIMIT_TOKENS",
    "build_retentive_filter",
    "chain_wiring",
    "extended_retention_counters",
    "gate_counters",
    "mechanism_block",
    "registry",
    "v20_counters",
    "v21_counters",
]
