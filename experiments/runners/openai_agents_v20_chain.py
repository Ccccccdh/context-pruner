"""v20 chain: the v14 chain with the non-duplicate retention filter and a gate that always fires.

Two changes against ``openai_agents_multitask_chain_v14``, and nothing else:

1. **the plugin arm's filter** is :class:`~experiments.runners.openai_agents_v20_retention.
   NonDuplicateRetentionFilter`, which composes the frozen v12 duplicate rule with the shadowed
   v5 lineage evidence-safe selective retention.  Wiring only: no mechanism is written here.
2. **the trigger gate fires on every model input after the first.**  The v20 gate addendum
   (``integrations/openai_agents/V20_GATE_ADDENDUM_20261010.json``) measured that at the N3
   control shape the budget gate passes most calls straight through: with
   ``soft_limit_tokens = 5435`` two of the three tasks never reach the threshold at all
   (last estimated 4,698 and 5,211), so the plugin arm never even invoked its filter.  Wrapping
   the v20 filter in the *same* :class:`~experiments.runners.trigger_gate.BudgetTriggeredFilter`
   with ``soft_limit_tokens = 0`` makes the gate's own condition
   ``last_estimated_tokens <= soft_limit_tokens`` false for every payload that has any content,
   so every call after the first runs the filter - while the gate keeps being the component that
   decides, keeps measuring each payload, and keeps publishing
   ``trigger_gate_calls`` / ``trigger_gate_triggered_calls`` / ``trigger_gate_passthrough_calls``
   / ``trigger_gate_soft_limit_tokens`` / ``trigger_gate_last_estimated_tokens`` per sample.

Why the threshold is zero rather than the ``always`` trigger policy
------------------------------------------------------------------
``--trigger-policy always`` would remove the gate entirely (``run_openai_agents_api_experiment.
_build_filter`` returns the bare arm filter), which would (a) discard the per-call measurement
the addendum requires and (b) put the plugin arm's gate in a different shape from the one the
v16 and v19 batches measured.  Keeping the gate installed with a zero threshold changes exactly
one number, and that number is recorded in every row and in the manifest.

Scope of the change
-------------------
The gate is installed in the plugin arm only.  The ``none`` arm installs no filter at all and
the ``native_summary`` arm keeps the arm filter with its own budget threshold, so this module
does not touch the baseline.  Because the threshold changed, **v20 is not comparable as a whole
configuration to v16 or v19**; only its paired within-batch comparison is.

The frozen v14 module is not edited: this module imports it, imports the v20 filter, and installs
its own runtime patches.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v8 as frozen_long
from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_multitask_registry_v14 as default_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as diagnostic_v8
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder
from experiments.runners.trigger_gate import BudgetTriggeredFilter
from experiments.runners.openai_agents_v20_retention import (
    COMPOSITION_SCHEMA,
    MECHANISM_NAME,
    NonDuplicateRetentionFilter,
)

#: The registry this chain resolves tasks through.  The runner points it at the v17 registry
#: (``openai_agents_v17_registry``) before a sample is built.  Every lookup below goes through
#: this module-level name rather than through ``v14.registry``, so patching the v14 module - which
#: the frozen v17 runner also does for its own paths - cannot leave the recorder or the filter
#: reading the v14 task table.
registry = default_registry

from experiments.runners.openai_agents_v20_retention import (  # noqa: E402
    COMPOSITION_SCHEMA,
    MECHANISM_NAME,
    NonDuplicateRetentionFilter,
)

#: Mechanism schema of this chain.  Distinct from every earlier one on purpose: a v20 row must
#: never be mistakable for a v14/v16/v19 row.
MECHANISM_SCHEMA = "openai_agents_v20_nonduplicate_chain"
EVIDENCE_SCHEMA = "openai_nonduplicate_retention_v20"

#: The trigger threshold this chain installs in the plugin arm.  Zero is the intended setting;
#: ``BudgetTriggeredFilter.__init__`` floors it with ``max(1, ...)``, so the value that actually
#: takes effect - and the value the gate reports in ``trigger_gate_soft_limit_tokens`` - is 1.
#: Either way the gate's condition ``last_estimated_tokens <= soft_limit_tokens`` is false for
#: every payload that carries any content, i.e. it fires on every model input after the first.
#: Lowering the threshold per task, or after seeing a saving number, is forbidden by the v20
#: gate addendum.
TRIGGER_GATE_SOFT_LIMIT_TOKENS = 0

#: How the threshold above is described in artifacts, so a reader does not have to infer it.
TRIGGER_GATE_POLICY = (
    "the plugin arm's BudgetTriggeredFilter is installed with soft_limit_tokens=0, which the "
    "frozen class floors to 1 (trigger_gate_soft_limit_tokens therefore reports 1), so its "
    "condition last_estimated_tokens <= soft_limit_tokens is false for every non-empty payload "
    "and the gate fires on every model input after the first; the gate stays in place, keeps "
    "measuring each payload and keeps publishing its counters. Trigger policy stays "
    "symmetric_budget so the wrapper is not removed from the plugin arm."
)

#: The v14 chain's persisted fields, plus the v20 counters, the first-call gate counter and the
#: composition identity.
PERSISTED_EXTRA_FIELDS = tuple(v14.PERSISTED_EXTRA_FIELDS) + (
    "trigger_gate_first_call_passthrough_calls",
    "v20_mechanism_schema",
    "v20_composition_schema",
    "v20_retention_engaged_calls",
    "v20_retention_fallback_calls",
    "v20_units_elided",
    "v20_bytes_saved",
)

EVIDENCE_SCHEMA = "openai_nonduplicate_retention_v20"
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

    It is a copy of ``openai_agents_multitask_chain_v14``'s recorder for the same reason the v20
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
    """Install this task's non-duplicate retention filter in the plugin arm only.

    The wrapped arm filter is built exactly as the frozen runner builds it, so the arc from the
    SDK down is unchanged; only the innermost filter and the gate's behaviour differ.
    """
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    wrapper = original(method, **kwargs)
    retention = NonDuplicateRetentionFilter(
        task=str(case.scenario),
        task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    # Fail loudly here rather than silently at run time: the v5 entry point this composite calls
    # reads a fixed attribute set, and the self check names every one that is absent.
    retention.self_check()
    if isinstance(wrapper, BudgetTriggeredFilter):
        # Build a fresh gate of this chain's class rather than mutating the frozen wrapper's
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


class FirstCallPassthroughGate(BudgetTriggeredFilter):
    """The frozen budget gate, specialised so that the first model input always passes through.

    Why the gate's own method needs this
    ------------------------------------
    ``soft_limit_tokens = 0`` (floored to 1 by the base constructor) makes the frozen condition
    ``last_estimated_tokens <= soft_limit_tokens`` false for every real payload, which means the
    gate fires on the **first** model input as well.  The addendum asks for the threshold to
    "fire on every model input after the first", and the first input is the frozen history alone:
    it carries no tool output, so there is nothing for either stage to remove, and invoking the
    filter on it would only mean the gate reported one more trigger than the rule allows.

    This subclass is the smallest change that expresses the rule exactly, and it keeps every
    counter the audit reads:

    * ``trigger_gate_calls`` still counts every model input;
    * ``trigger_gate_passthrough_calls`` counts the first input, so a passing gate is visible
      rather than implied;
    * ``trigger_gate_triggered_calls`` counts the inputs the mechanism actually ran on, which is
      every input after the first;
    * ``trigger_gate_soft_limit_tokens`` and ``trigger_gate_last_estimated_tokens`` are unchanged
      in meaning.

    ``trigger_gate_first_call_passthrough_calls`` is added so the first-call rule is an explicit,
    checkable counter instead of an inference from the other two.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.first_call_passthrough_calls = 0

    def should_trigger(self, items) -> bool:
        if self.calls == 0:
            # The first model input of this sample: count it and pass it through untouched.
            self.calls += 1
            self.last_estimated_tokens = self.estimated_tokens(items)
            self.first_call_passthrough_calls += 1
            self.passthrough_calls += 1
            return False
        return bool(super().should_trigger(items))

    def metrics_dict(self) -> dict[str, Any]:
        metrics = super().metrics_dict()
        metrics["trigger_gate_first_call_passthrough_calls"] = (
            self.first_call_passthrough_calls
        )
        return metrics


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


def v20_counters(metrics: dict[str, Any]) -> dict[str, Any]:
    """The v20 counters, for the gate artifact and the per-sample report."""
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

    Identical in shape to the v14 context manager, with this module's filter builder and counter
    builder.  Restores everything on exit, including on an exception.
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


def mechanism_block() -> dict[str, Any]:
    """The mechanism description every v20 manifest carries."""
    return {
        "arm": "pruner_v1",
        "name": MECHANISM_NAME,
        "schema": MECHANISM_SCHEMA,
        "composition_schema": COMPOSITION_SCHEMA,
        "rule": (
            "the frozen v12 exact duplicate rule runs first; the v5 lineage evidence-safe "
            "selective retention then runs on the deduplicated list, with its registered-literal "
            "guard, its recovery-pointer requirement and its whole-payload fallback unchanged. "
            "Any exception from the retention stage, or a None return, sends the deduplicated "
            "list verbatim and is counted in v20_retention_fallback_calls"
        ),
        "frozen_rule_module": "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
        "retention_module": "experiments/runners/openai_agents_evidence_safe_retention_v5.py",
        "composite_module": "experiments/runners/openai_agents_v20_retention.py",
        "chain_module": "experiments/runners/openai_agents_v20_chain.py",
        "chain_blueprint": "experiments/runners/openai_agents_multitask_chain_v14.py",
        "trigger_gate": {
            "soft_limit_tokens": TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": TRIGGER_GATE_POLICY,
            "arm_scope": "plugin arm only; the none and native_summary arms are untouched",
            "comparability": (
                "because the threshold changed, v20 is not comparable as a whole configuration "
                "to v16 or v19; only its paired within-batch comparison is"
            ),
        },
        "counter_names": {
            "engaged": "v20_retention_engaged_calls",
            "fallback": "v20_retention_fallback_calls",
            "units": "v20_units_elided",
            "bytes": "v20_bytes_saved",
        },
    }


__all__ = [
    "EVIDENCE_SCHEMA",
    "MECHANISM_SCHEMA",
    "MultitaskRecorder",
    "PERSISTED_EXTRA_FIELDS",
    "TRIGGER_GATE_POLICY",
    "TRIGGER_GATE_SOFT_LIMIT_TOKENS",
    "build_retentive_filter",
    "chain_wiring",
    "extended_retention_counters",
    "gate_counters",
    "mechanism_block",
    "registry",
    "v20_counters",
]
