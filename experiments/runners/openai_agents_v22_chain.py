"""v22 chain: the v21 chain with the protection window selected by a pinned K configuration.

This module is ``openai_agents_v21_chain`` with exactly one thing made explicit: **which window the
mechanism runs at**.  The v21 chain imports the frozen mechanism module directly and therefore
always runs K=3; this round pre-registers a dose over K in {3, 1, 0}
(``integrations/openai_agents/V22_WINDOW_DOSE_PLAN_20261010.json``), so the chain reads the
configuration module the runner installed instead.

Nothing else changes, and nothing below this round is edited:

* the same two stages run beneath the elision - the frozen v12 duplicate rule and the v5 lineage
  evidence-safe retention - reached through the same two entry points;
* the same :class:`~experiments.runners.openai_agents_v20_chain.FirstCallPassthroughGate` with the
  same ``soft_limit_tokens = 0`` setting, arm scope and policy text;
* the same counters, note form, literal invariant and fail-closed rules.

The window is asserted, not inferred
------------------------------------
``WINDOW_LINES`` is set by :func:`install_configuration` from the configuration module, and
:func:`mechanism_block` publishes it together with the configuration's class and module so a batch
manifest states the K it was produced with.  The runner calls :func:`install_configuration` before
anything is built, and the gate re-reads the effective window from the class in use.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from experiments.runners import openai_agents_long_baseline_boundary_v8 as frozen_long
from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_multitask_registry_v14 as default_registry
from experiments.runners import run_openai_agents_repo_diagnostic_v8 as diagnostic_v8
from experiments.runners import trigger_gate as frozen_gate
from experiments.runners import openai_agents_v21_carrier_elision as mechanism
from experiments.runners import openai_agents_v22_window as window_module
from experiments.runners.openai_agents_evidence_v9 import NarrowGuardRecorder
from experiments.runners.openai_agents_v20_chain import FirstCallPassthroughGate

#: The registry this chain resolves tasks through.  The runner points it at the v17 registry
#: before a sample is built.
registry = default_registry

# --- set by install_configuration() --------------------------------------------------------
#: The configuration module for the K in force (``openai_agents_v22_window_k<k>``).
CONFIG_MODULE: Any = None
#: The filter class for the K in force.
FILTER_CLASS: Any = None
#: The window in force: 3, 1 or 0.
WINDOW_LINES = mechanism.WINDOW_LINES
MECHANISM_SCHEMA = "openai_agents_v22_window_unset"
EVIDENCE_SCHEMA = "openai_literal_carrier_elision_v22_unset"
MECHANISM_NAME = mechanism.MECHANISM_NAME
COMPOSITION_SCHEMA = mechanism.COMPOSITION_SCHEMA

#: The disclosure and the forbidden wording, carried here as data.
#:
#: They live in the frozen v21 *chain* module, which this round must not edit (its digest is
#: pinned by the v21 ledger and by the v21 stage-1 batch manifest).  The text below is therefore
#: restated, and ``tests/test_openai_agents_v22_window_dose.py`` asserts it is byte-identical to
#: the v21 chain's own text, so the two cannot drift apart silently.
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

FORBIDDEN_WORDING = (
    "compression alone saved X percent",
    "the four criteria are met",
    "the retry tax is gone",
    "the plugin saves about 12.5 percent of context",
)

#: The trigger threshold, inherited from v20 through v21 unchanged.
TRIGGER_GATE_SOFT_LIMIT_TOKENS = 0

TRIGGER_GATE_POLICY = (
    "the plugin arm's BudgetTriggeredFilter is installed with soft_limit_tokens=0, which the "
    "frozen class floors to 1 (trigger_gate_soft_limit_tokens therefore reports 1), so its "
    "condition last_estimated_tokens <= soft_limit_tokens is false for every non-empty payload "
    "and the gate fires on every model input after the first; the gate stays in place, keeps "
    "measuring each payload and keeps publishing its counters. The setting is inherited from v20 "
    "unchanged, so this round measures the window and does not re-measure the gate. Trigger policy "
    "stays symmetric_budget so the wrapper is not removed from the plugin arm."
)

#: The v14 chain's persisted fields plus every counter this lineage publishes.  The v21 counters
#: must appear here: a counter that is not in this tuple never reaches a sample row.
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


def install_configuration(config_module: Any) -> dict[str, Any]:
    """Bind one pinned K configuration to this chain and return its record."""
    global CONFIG_MODULE, FILTER_CLASS, WINDOW_LINES, MECHANISM_SCHEMA, EVIDENCE_SCHEMA
    global MECHANISM_NAME, COMPOSITION_SCHEMA

    config = dict(config_module.CONFIG)
    window = int(config["k"])
    if int(config_module.WINDOW_LINES) != window:
        raise ValueError("the configuration module disagrees with its own record")
    # The configuration module already refuses an unregistered K when it is imported (its
    # ``CONFIG`` is built by ``window.configuration``), so this is a second, cheap guard on the
    # value the chain is about to bind.
    if window not in window_module.AUTHORISED_WINDOWS:
        raise ValueError(
            f"K={window} is not pre-registered: the v22 plan authorises "
            f"{window_module.AUTHORISED_WINDOWS}"
        )
    CONFIG_MODULE = config_module
    FILTER_CLASS = getattr(config_module, config["class_name"])
    if int(getattr(FILTER_CLASS, "WINDOW_LINES", -1)) != window:
        raise ValueError("the filter class does not carry the configuration's window")
    WINDOW_LINES = window
    MECHANISM_SCHEMA = str(config["mechanism_schema"])
    EVIDENCE_SCHEMA = str(config["evidence_schema"])
    MECHANISM_NAME = str(config["mechanism_instance"])
    COMPOSITION_SCHEMA = str(config["composition_schema"])
    return config


def register_task_data(task_id: str) -> None:
    """Runtime data registration so the frozen guard resolves this task's literals."""
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
    """The v14 recorder, with every registry lookup resolved through this module."""

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
        record["v22_window_lines"] = WINDOW_LINES
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
    """Install this K configuration's filter in the plugin arm only."""
    case = kwargs.get("case")
    if method != "pruner_v1" or case is None:
        return original(method, **kwargs)
    if FILTER_CLASS is None:
        raise RuntimeError(
            "no window configuration is installed: call install_configuration() before the "
            "chain is used, so that the K a batch runs at is a pinned module rather than a "
            "default"
        )
    wrapper = original(method, **kwargs)
    retention = FILTER_CLASS(
        task=str(case.scenario),
        task_statement=str(case.task_statement),
        hard_limit_bytes=int(filter_hard_bytes or 65536),
    )
    retention.self_check()
    if int(retention.v21_window_lines) != WINDOW_LINES:
        raise RuntimeError(
            "the filter instance carries a different window than the chain: "
            f"{retention.v21_window_lines} != {WINDOW_LINES}"
        )
    if isinstance(wrapper, frozen_gate.BudgetTriggeredFilter):
        gate = FirstCallPassthroughGate(
            retention,
            soft_limit_tokens=max(1, int(TRIGGER_GATE_SOFT_LIMIT_TOKENS)),
        )
        gate.first_call_passthrough_calls = 0
        return diagnostic_v8._GateAware(gate, retention)
    return retention


def extended_retention_counters(original, recorder: Any, context_filter: Any) -> dict[str, Any]:
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
    """Install the runtime patches the frozen modules cannot make themselves."""
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
    """The mechanism description every v22 manifest carries, including the window in force."""
    config = dict(getattr(CONFIG_MODULE, "CONFIG", {}) or {}) if CONFIG_MODULE else {}
    return {
        "arm": "pruner_v1",
        "name": MECHANISM_NAME,
        "schema": MECHANISM_SCHEMA,
        "composition_schema": COMPOSITION_SCHEMA,
        "window_lines": WINDOW_LINES,
        "window_configuration": config,
        "window_is_the_only_variable": (
            "the three v22 groups differ only in the window K; renderer, contract, four strict "
            "track conditions, 160 character bound, registry, read shape, arms, repeats, request "
            "cap and trigger gate threshold are identical, and no K may be changed after any "
            "saving number has been seen"
        ),
        "rule": (
            "the frozen v12 exact duplicate rule runs first; the v5 lineage evidence-safe "
            "selective retention then runs on the deduplicated list, with its registered-literal "
            "guard, its recovery-pointer requirement and its whole-payload fallback unchanged; "
            "the literal-carrier line elision then runs on that result, keeping verbatim every "
            "line that contains a registered literal or constraint phrase together with a fixed "
            f"window of {WINDOW_LINES} lines before and after it, eliding every other line and "
            "replacing each maximal contiguous omitted run with exactly one note line of the form "
            "[elided n lines first-last]; the most recent tool group is kept verbatim. Any "
            "registered-literal occurrence loss, any non-reduction after the note overhead, or "
            "any exception sends the payload this stage received verbatim and is counted in "
            "v21_literal_guard_fallbacks"
        ),
        "frozen_rule_module": "experiments/runners/openai_agents_exact_duplicate_replay_v12.py",
        "retention_module": "experiments/runners/openai_agents_evidence_safe_retention_v5.py",
        "v20_composite_module": "experiments/runners/openai_agents_v20_retention.py",
        "mechanism_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
        "composite_module": "experiments/runners/openai_agents_v21_carrier_elision.py",
        "config_module": str(config.get("config_module", "")),
        "chain_module": "experiments/runners/openai_agents_v22_chain.py",
        "chain_blueprint": "experiments/runners/openai_agents_multitask_chain_v14.py",
        "window_frozen": (
            "the window was declared per K in the v22 pre-registration before any paid request; it "
            "may not be changed after any saving number is seen, and an unregistered window (for "
            "example K=2) may not be run at all"
        ),
        "invariant": (
            "for every registered literal and constraint phrase the number of verbatim "
            "occurrences after elision is greater than or equal to the number before it, per "
            "occurrence and not per distinct string, computed with the same v5 lineage "
            "_carrier_loss that already vetoed the older mechanism"
        ),
        "fail_closed": (
            "an output is kept verbatim whenever the mechanism cannot prove the invariant for the "
            "payload it would send; nothing is removed on the strength of an argument the "
            "mechanism cannot check, and the note lines it writes can never satisfy the invariant "
            "themselves"
        ),
        "trigger_gate": {
            "soft_limit_tokens": TRIGGER_GATE_SOFT_LIMIT_TOKENS,
            "policy": TRIGGER_GATE_POLICY,
            "arm_scope": "plugin arm only; the none and native_summary arms are untouched",
            "comparability": (
                "the three v22 groups are comparable to each other as a dose, and each is "
                "internally paired; none of them is comparable as a whole configuration to v16, "
                "v19, v20 or v21, and none of them may be pooled with another"
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
    "CONFIG_MODULE",
    "EVIDENCE_SCHEMA",
    "FILTER_CLASS",
    "FirstCallPassthroughGate",
    "MECHANISM_SCHEMA",
    "MultitaskRecorder",
    "PERSISTED_EXTRA_FIELDS",
    "TRIGGER_GATE_POLICY",
    "TRIGGER_GATE_SOFT_LIMIT_TOKENS",
    "WINDOW_LINES",
    "build_retentive_filter",
    "chain_wiring",
    "extended_retention_counters",
    "gate_counters",
    "install_configuration",
    "mechanism_block",
    "registry",
    "v20_counters",
    "v21_counters",
]
