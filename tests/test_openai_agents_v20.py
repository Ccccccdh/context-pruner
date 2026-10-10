"""Zero-API tests for the v20 non-duplicate retention wiring and the v20 gate.

They cover the properties the v20 plan pre-registers and that the wiring can get wrong silently:

* the composite runs the duplicate rule before the retention stage;
* the retention stage is fail-closed: an exception, or a ``None`` return, sends the deduplicated
  list verbatim and is counted, and never removes content;
* the shadowed v5 entry point's state is checked at construction time, so a missing attribute is
  an error and not a silent degradation;
* the four published counters exist and keep their names;
* the trigger-gate assertion and the mechanism-engagement assertion can actually fail - a check
  that cannot fail is not a gate.

Nothing here contacts a provider, and no frozen module is edited.
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest import mock

from experiments.runners import openai_agents_evidence_safe_retention_v5 as v5
from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_v17_registry as registry
from experiments.runners import openai_agents_v20_chain as chain
from experiments.runners import openai_agents_v20_retention as v20
from experiments.runners import run_openai_agents_v20_acquisition as runner

TASK = "django_sqlite_version_floor"


@contextmanager
def v17_registry_installed():
    """Resolve the v17 task table through both chain modules for the duration of a test."""
    original = {"chain": chain.registry, "v14": v14.registry}
    chain.registry = registry
    v14.registry = registry
    try:
        yield
    finally:
        chain.registry = original["chain"]
        v14.registry = original["v14"]


def build_filter() -> v20.NonDuplicateRetentionFilter:
    return v20.NonDuplicateRetentionFilter(
        task=TASK,
        task_statement=registry.statement(TASK),
        hard_limit_bytes=24_000 * 4,
    )


def payloads(task_id: str = TASK) -> list[list[dict]]:
    """The N3 payload sequence: one distinct view per round, three rounds, then the answer turn."""
    entry = registry.task(task_id)
    items: list[dict] = [dict(message) for message in registry.history(task_id)]
    out: list[list[dict]] = []
    for index, name in enumerate(entry["tool_names"]):
        call_id = f"call_{task_id}_{index}_{name}"
        items = items + [
            {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": registry.source_view(task_id, index),
            },
        ]
        out.append(json.loads(json.dumps(items)))
    return out


class CompositeShapeTests(unittest.TestCase):
    def test_self_check_passes_and_reports_the_protection_set(self) -> None:
        with v17_registry_installed():
            report = build_filter().self_check()
        self.assertTrue(report["ok"])
        self.assertGreater(report["task_units"], 0)
        self.assertGreater(report["protected_unit_sha256"], 0)
        self.assertGreater(report["literal_texts"], 0)

    def test_the_composite_owner_is_this_module_and_the_ancestor_is_explicit(self) -> None:
        self.assertIs(v20.NonDuplicateRetentionFilter.filter_items.__module__, v20.__name__)
        self.assertTrue(
            issubclass(v20.NonDuplicateRetentionFilter, v14.MultitaskDuplicateFilter)
        )
        source = (
            __import__("pathlib").Path(v20.__file__).read_text(encoding="utf-8")
        )
        self.assertIn("v5.SelectiveRetentionFilter.filter_items(self, base)", source)

    def test_the_duplicate_rule_runs_before_the_retention_stage(self) -> None:
        """On a payload with an exact duplicate the v12 stage replaces it, and the v20 stage
        receives the deduplicated list - so the second stage never sees the duplicated text."""
        with v17_registry_installed():
            filter_ = build_filter()
            view = registry.source_view(TASK, 0)
            items = [dict(message) for message in registry.history(TASK)]
            items = items + [
                {"type": "function_call", "call_id": "c1", "name": "read_a", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": view},
                {"type": "function_call", "call_id": "c2", "name": "read_b", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c2", "output": view},
            ]
            seen: dict[str, object] = {}

            def spy(self, base):
                seen["len"] = len(base)
                seen["outputs"] = [
                    item.get("output")
                    for item in base
                    if item.get("type") == "function_call_output"
                ]
                return None

            with mock.patch.object(v5.SelectiveRetentionFilter, "filter_items", spy):
                out = filter_.filter_items(items)
        # The payload is three history messages plus two call/output pairs.  The retention stage
        # must have received the deduplicated list: both outputs are still there, and the older
        # one's text is the v12 pointer rather than the duplicated view.
        self.assertEqual(7, seen["len"], "the retention stage must see the deduplicated list")
        outputs = seen["outputs"]
        self.assertEqual(2, len(outputs))
        self.assertEqual(view, outputs[1])
        self.assertTrue(str(outputs[0]).startswith("[Exact duplicate output;"))
        self.assertEqual(1, filter_.exact_duplicate_replacements)
        self.assertEqual(1, filter_.v20_last_call["duplicate_replacements_after_dedupe"])
        self.assertEqual(1, filter_.v20_retention_fallback_calls)
        self.assertEqual("fallback", filter_.v20_last_call["status"])
        # A None from the retention stage is not a failure to report upstream: the composite
        # still returns the deduplicated list, so the duplicate rule's saving is kept.
        self.assertIsNotNone(out)
        self.assertEqual(7, len(out))
        self.assertEqual(
            "[Exact duplicate output;",
            str(out[4]["output"])[: len("[Exact duplicate output;")],
        )

    def test_counters_exist_under_the_pre_registered_names(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            metrics = filter_.metrics_dict()
        for name in v20.V20_COUNTER_FIELDS:
            self.assertIn(name, metrics, name)
        for name in v20.PRESERVED_COUNTER_FIELDS:
            self.assertIn(name, metrics, name)
        for name in v20.V20_COUNTER_FIELDS:
            self.assertIn(name, chain.PERSISTED_EXTRA_FIELDS, name)


class FailClosedTests(unittest.TestCase):
    def test_an_exception_in_the_retention_stage_keeps_the_content_verbatim(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            observed = payloads(TASK)[-1]

            def explode(self, base):
                raise RuntimeError("injected retention failure")

            before = json.dumps(observed, ensure_ascii=False, sort_keys=True)
            with mock.patch.object(v5.SelectiveRetentionFilter, "filter_items", explode):
                out = filter_.filter_items(observed)
            after = json.dumps(out, ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after, "an injected failure must not remove any content")
        self.assertEqual(1, filter_.v20_retention_fallback_calls)
        self.assertEqual(0, filter_.v20_retention_engaged_calls)
        self.assertEqual(0, filter_.v20_units_elided)
        self.assertEqual(0, filter_.v20_bytes_saved)
        self.assertEqual("exception", filter_.v20_last_call["status"])
        self.assertIn("RuntimeError", filter_.v20_last_call["fallback_reason"])

    def test_a_none_return_from_the_retention_stage_is_counted_as_a_fallback(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            observed = payloads(TASK)[-1]
            with mock.patch.object(
                v5.SelectiveRetentionFilter, "filter_items", lambda self, base: None
            ):
                out = filter_.filter_items(observed)
        self.assertEqual(observed, out)
        self.assertEqual(1, filter_.v20_retention_fallback_calls)
        self.assertEqual(0, filter_.v20_retention_engaged_calls)
        self.assertEqual("fallback", filter_.v20_last_call["status"])

    def test_a_filtered_return_is_counted_as_engaged_with_a_measured_saving(self) -> None:
        """The byte counter is measured on the two item lists; the unit counter is the v5
        stage's own bookkeeping.  A substituted return therefore moves the bytes and not the
        units, which is exactly the property that keeps the two counters distinguishable."""
        with v17_registry_installed():
            filter_ = build_filter()
            observed = payloads(TASK)[-1]
            trimmed = [dict(item) for item in observed]
            trimmed[-1] = dict(trimmed[-1], output="short")
            with mock.patch.object(
                v5.SelectiveRetentionFilter, "filter_items", lambda self, base: trimmed
            ):
                out = filter_.filter_items(observed)
        self.assertEqual(trimmed, out)
        self.assertEqual(1, filter_.v20_retention_engaged_calls)
        self.assertEqual(0, filter_.v20_retention_fallback_calls)
        self.assertGreater(filter_.v20_bytes_saved, 0)
        self.assertEqual(0, filter_.v20_units_elided)
        self.assertIn("units_elided", filter_.v20_last_call)


class SelfCheckTests(unittest.TestCase):
    def test_a_missing_v5_state_attribute_raises_rather_than_degrading(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            del filter_.protected_unit_sha256
            with self.assertRaises(RuntimeError) as raised:
                filter_.self_check()
        self.assertIn("protected_unit_sha256", str(raised.exception))
        self.assertIn("wiring is incomplete", str(raised.exception))

    def test_every_required_attribute_is_present_after_construction(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
        missing = [name for name in v20.V5_REQUIRED_ATTRIBUTES if not hasattr(filter_, name)]
        self.assertEqual([], missing)
        unreachable = [
            name
            for name in v20.V5_REQUIRED_CALLABLES
            if not callable(getattr(filter_, name, None))
        ]
        self.assertEqual([], unreachable)

    def test_an_empty_protection_set_raises(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            filter_.protected_unit_sha256 = []
            with self.assertRaises(RuntimeError) as raised:
                filter_.self_check()
        self.assertIn("protected_unit_sha256", str(raised.exception))

    def test_an_unreachable_callable_raises(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            filter_._carrier_loss = None
            with self.assertRaises(RuntimeError) as raised:
                filter_.self_check()
        self.assertIn("unreachable callables", str(raised.exception))


class GateAssertionTests(unittest.TestCase):
    """The gate's own checks must be able to fail; a tautology would not be a gate."""

    def _sample(self, **overrides) -> dict:
        sample = {
            "task": "synthetic",
            "repeat": 0,
            "method": "pruner_v1",
            "model_calls": 4,
            "v20_units_elided": 1,
            "v20_bytes_saved": 100,
            "v20_retention_engaged_calls": 1,
            "v20_retention_fallback_calls": 0,
            "exact_duplicate_replacements": 0,
            "trigger_gate_calls": 4,
            "trigger_gate_triggered_calls": 3,
            "trigger_gate_passthrough_calls": 1,
            "trigger_gate_first_call_passthrough_calls": 1,
            "trigger_gate_soft_limit_tokens": 1,
            "trigger_gate_last_estimated_tokens": 5000,
        }
        sample.update(overrides)
        return sample

    def test_a_healthy_sample_passes_both_assertions(self) -> None:
        self.assertTrue(runner.gate_assertion_b([self._sample()])["ok"])
        self.assertTrue(runner.gate_assertion_c([self._sample()])["ok"])

    def test_a_gate_passthrough_sample_fails_assertion_b(self) -> None:
        result = runner.gate_assertion_b(
            [self._sample(trigger_gate_triggered_calls=1, trigger_gate_passthrough_calls=3)]
        )
        self.assertFalse(result["ok"])

    def test_a_gate_that_fires_on_the_first_call_fails_assertion_b(self) -> None:
        result = runner.gate_assertion_b(
            [self._sample(trigger_gate_passthrough_calls=0, trigger_gate_first_call_passthrough_calls=0)]
        )
        self.assertFalse(result["ok"])

    def test_a_zero_elision_sample_fails_assertion_c(self) -> None:
        result = runner.gate_assertion_c(
            [self._sample(v20_units_elided=0, v20_bytes_saved=0, v20_retention_engaged_calls=0)]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(["synthetic"], result["tasks_failing"])

    def test_the_core_gate_function_reports_each_assertion_can_fail(self) -> None:
        """The gate itself asserts this, so the property is recorded in every gate artifact."""
        self.assertFalse(runner.gate_assertion_b([self._sample(trigger_gate_triggered_calls=0)])["ok"])
        self.assertFalse(runner.gate_assertion_c([self._sample(v20_units_elided=0)])["ok"])
        self.assertFalse(runner.gate_assertion_d({})["ok"])

    def test_the_revision_ledger_pins_the_modules_it_names(self) -> None:
        ledger = json.loads(runner.REVISION_LEDGER.read_text(encoding="utf-8"))
        live = runner.module_hashes()
        for relative in runner.PINNED_MODULES:
            self.assertIn(relative, ledger["modules"], relative)
            self.assertEqual(ledger["modules"][relative], live[relative], relative)

    def test_an_unregistered_digest_fails_assertion_d(self) -> None:
        registered = dict(runner.module_hashes())
        registered[runner.PINNED_MODULES[0]] = "0" * 64
        result = runner.gate_assertion_d(registered)
        self.assertFalse(result["ok"])
        self.assertEqual([runner.PINNED_MODULES[0]], result["drifted_modules"])

    def test_a_module_absent_from_the_ledger_fails_assertion_d(self) -> None:
        registered = dict(runner.module_hashes())
        del registered[runner.PINNED_MODULES[0]]
        result = runner.gate_assertion_d(registered)
        self.assertFalse(result["ok"])
        self.assertEqual([runner.PINNED_MODULES[0]], result["unregistered_modules"])


class GateThresholdTests(unittest.TestCase):
    def test_the_pre_registered_threshold_is_zero_and_the_policy_is_recorded(self) -> None:
        self.assertEqual(0, chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS)
        self.assertIn("soft_limit_tokens=0", chain.TRIGGER_GATE_POLICY)
        self.assertIn("plugin arm only", json.dumps(chain.mechanism_block()))

    def test_the_first_call_passes_through_and_every_later_call_fires(self) -> None:
        gate = chain.FirstCallPassthroughGate(
            object(), soft_limit_tokens=max(1, chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS)
        )
        gate.first_call_passthrough_calls = 0
        items = [{"role": "user", "content": "x" * 4000}]
        self.assertFalse(gate.should_trigger(items))
        self.assertTrue(gate.should_trigger(items))
        self.assertTrue(gate.should_trigger(items))
        metrics = gate.metrics_dict()
        self.assertEqual(3, metrics["trigger_gate_calls"])
        self.assertEqual(2, metrics["trigger_gate_triggered_calls"])
        self.assertEqual(1, metrics["trigger_gate_passthrough_calls"])
        self.assertEqual(1, metrics["trigger_gate_first_call_passthrough_calls"])

    def test_the_shape_and_cap_are_the_pre_registered_ones(self) -> None:
        math_ = runner.arithmetic()
        self.assertEqual("N3", math_["shape"])
        self.assertEqual(3, math_["rounds"])
        self.assertEqual(1, math_["reads_per_round"])
        self.assertEqual(9, math_["samples"])
        self.assertEqual(100, math_["cap"])
        self.assertTrue(math_["at_least_1_3"])
        self.assertGreaterEqual(math_["margin"], 1.3)


if __name__ == "__main__":
    unittest.main()
