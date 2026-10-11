"""Zero-API tests for the v22 window dose: one mechanism, three pre-registered windows.

They cover what the dose can get wrong silently:

* the window a configuration declares is the window the mechanism actually uses, read from the
  class, from the configuration record and from the instance attribute, all three agreeing;
* an unregistered window (K=2) is refused rather than run;
* the frozen v21 mechanism module is untouched, its own default window is still three, and the
  three configurations are distinct classes with distinct schema strings;
* the three windows really do produce different elision on the real N3 payloads, monotonically in
  the direction the plan predicts, with the guard falling back zero times at every K;
* the K=0 edge cases: an output whose every line carries a literal is kept rather than emptied, an
  ordinary view keeps every carrier line, no note is malformed, and no note can carry a literal;
* the dose report's own guard: the per-K counters that a batch row must carry are persisted.

Nothing here contacts a provider and no frozen file is edited.
"""

from __future__ import annotations

import importlib
import json
import re
import unittest

from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_v17_registry as registry
from experiments.runners import openai_agents_v21_carrier_elision as mechanism
from experiments.runners import openai_agents_v22_window as window
from experiments.runners import run_openai_agents_v22_window_dose as runner

TASKS = registry.task_ids()


def payloads(task_id: str) -> list[list[dict]]:
    """The N3 payload sequence: one distinct view per round, three rounds, then the answer turn."""
    entry = registry.task(task_id)
    items: list[dict] = [dict(message) for message in registry.history(task_id)]
    out: list[list[dict]] = []
    for index, name in enumerate(entry["tool_names"]):
        call_id = f"call_{task_id}_{index}"
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


def run_task(k: int, task_id: str) -> object:
    """Run one K configuration over one task's N3 payloads and return the filter."""
    runner.select_window(k)
    filter_ = runner.chain.FILTER_CLASS(
        task=task_id,
        task_statement=registry.statement(task_id),
        hard_limit_bytes=24_000 * 4,
    )
    for payload in payloads(task_id):
        filter_.filter_items(payload)
    return filter_


class ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        original = v14.registry
        v14.registry = registry
        self.addCleanup(setattr, v14, "registry", original)

    def test_the_three_windows_are_the_pre_registered_ones(self) -> None:
        self.assertEqual((3, 1, 0), window.AUTHORISED_WINDOWS)
        self.assertEqual((3, 1, 0), runner.AUTHORISED_WINDOWS)

    def test_an_unregistered_window_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            window.configuration(2)
        with self.assertRaises(SystemExit):
            runner.select_window(2)

    def test_each_configuration_is_a_distinct_class_with_its_own_schema(self) -> None:
        names, schemas, classes = set(), set(), set()
        for k in window.AUTHORISED_WINDOWS:
            config = runner.select_window(k)
            names.add(config["class_name"])
            schemas.add(config["mechanism_schema"])
            classes.add(runner.chain.FILTER_CLASS)
        self.assertEqual(3, len(names))
        self.assertEqual(3, len(schemas))
        self.assertEqual(3, len(classes))

    def test_the_frozen_v21_mechanism_module_keeps_its_own_window_and_digest_surface(
        self,
    ) -> None:
        self.assertEqual(3, mechanism.WINDOW_LINES)
        source = (
            __import__("pathlib").Path(mechanism.__file__).read_text(encoding="utf-8")
        )
        self.assertIn("WINDOW_LINES = 3", source)
        # The mechanism reads the instance attribute when present and its own constant otherwise.
        self.assertIn('getattr(self, "v21_window_lines", WINDOW_LINES)', source)

    def test_the_declared_window_is_the_window_the_mechanism_uses(self) -> None:
        for k in window.AUTHORISED_WINDOWS:
            config = runner.select_window(k)
            self.assertEqual(k, int(config["k"]))
            self.assertEqual(k, int(runner.chain.FILTER_CLASS.WINDOW_LINES))
            self.assertEqual(k, int(runner.chain.WINDOW_LINES))
            check = runner._effective_window_matches_k()
            self.assertTrue(check["ok"], check)

    def test_the_batch_rows_persist_the_counters_the_dose_reports(self) -> None:
        for name in mechanism.V21_COUNTER_FIELDS:
            self.assertIn(name, runner.chain.PERSISTED_EXTRA_FIELDS, name)
        self.assertIn("v21_window_lines", runner.chain.PERSISTED_EXTRA_FIELDS)


class DoseMonotonicityTests(unittest.TestCase):
    """The window must really govern the saving, in the direction the plan predicts."""

    @classmethod
    def setUpClass(cls) -> None:
        original = v14.registry
        v14.registry = registry
        cls.addClassCleanup(setattr, v14, "registry", original)
        cls.measured: dict[int, dict[str, dict[str, int]]] = {}
        for k in window.AUTHORISED_WINDOWS:
            cls.measured[k] = {}
            for task_id in TASKS:
                filter_ = run_task(k, task_id)
                cls.measured[k][task_id] = {
                    "elided": int(filter_.v21_outputs_elided),
                    "lines": int(filter_.v21_lines_elided),
                    "bytes": int(filter_.v21_bytes_saved),
                    "notes": int(filter_.v21_note_bytes),
                    "fallbacks": int(filter_.v21_literal_guard_fallbacks),
                    "considered": int(filter_.v21_outputs_considered),
                }

    def test_the_guard_never_falls_back_at_any_k(self) -> None:
        for k, per_task in self.measured.items():
            for task_id, row in per_task.items():
                self.assertEqual(0, row["fallbacks"], f"K={k} {task_id}")

    def test_every_k_engages_on_every_task(self) -> None:
        for k, per_task in self.measured.items():
            for task_id, row in per_task.items():
                self.assertGreater(row["bytes"], 0, f"K={k} {task_id}")
                self.assertGreater(row["elided"], 0, f"K={k} {task_id}")

    def test_saving_is_non_increasing_in_k_per_task(self) -> None:
        """A narrower window protects less, so it can never save fewer bytes at the same task.

        The series is read in the order (K=3, K=1, K=0), so the assertion is that it never falls.
        """
        for task_id in TASKS:
            series = [self.measured[k][task_id]["bytes"] for k in (3, 1, 0)]
            self.assertEqual(
                sorted(series),
                series,
                f"{task_id}: bytes saved must not fall as K falls, got {series}",
            )
            self.assertLess(series[0], series[-1], f"{task_id}: K=0 must save more than K=3")

    def test_lines_elided_rise_as_k_falls(self) -> None:
        for task_id in TASKS:
            series = [self.measured[k][task_id]["lines"] for k in (3, 1, 0)]
            self.assertEqual(
                sorted(series),
                series,
                f"{task_id}: a narrower window elides at least as many lines, got {series}",
            )

    def test_the_note_overhead_is_reported_and_stays_below_the_saving(self) -> None:
        for k, per_task in self.measured.items():
            for task_id, row in per_task.items():
                self.assertGreater(row["notes"], 0, f"K={k} {task_id}")
                self.assertLess(
                    row["notes"],
                    row["bytes"],
                    f"K={k} {task_id}: the notes must cost less than they save",
                )

    def test_a_narrower_window_elides_at_least_as_many_outputs(self) -> None:
        for task_id in TASKS:
            series = [self.measured[k][task_id]["elided"] for k in (3, 1, 0)]
            self.assertEqual(sorted(series), series, f"{task_id}: {series}")

    def test_the_measured_dose_is_recorded_for_the_report(self) -> None:
        """The numbers the report quotes are the ones this test measured, not re-derived."""
        recorded = {
            task_id: {str(k): self.measured[k][task_id] for k in (3, 1, 0)} for task_id in TASKS
        }
        path = runner.ROOT / ".tooling/tmp/v22_dose_measurement.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(recorded, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertTrue(path.is_file())


class K0EdgeCaseTests(unittest.TestCase):
    """At K=0 nothing but the carrier lines survives, so the edge cases are structural."""

    def setUp(self) -> None:
        original = v14.registry
        v14.registry = registry
        self.addCleanup(setattr, v14, "registry", original)
        runner.select_window(0)

    def test_an_output_whose_every_line_carries_a_literal_is_kept_verbatim(self) -> None:
        literals = sorted(
            {
                str(phrase)
                for values in registry.literal_phrases(TASKS[0]).values()
                for phrase in values
            }
        )
        carrier = literals[0]
        text = "\n".join(f"{index}: {carrier} line {index}" for index in range(6))
        filter_ = runner.chain.FILTER_CLASS(
            task=TASKS[0],
            task_statement=registry.statement(TASKS[0]),
            hard_limit_bytes=24_000 * 4,
        )
        out, record = filter_._elide_text(text, literals)
        self.assertIsNone(out, "every line is a carrier, so nothing may be removed")
        self.assertEqual("every_line_is_protected", record["reason"])

    def test_a_note_only_result_is_never_returned(self) -> None:
        """A text whose only non-carrier lines are at the edges keeps the carriers, not a note."""
        literals = sorted(
            {
                str(phrase)
                for values in registry.literal_phrases(TASKS[0]).values()
                for phrase in values
            }
        )
        carrier = literals[0]
        text = f"{carrier}\nplain one\nplain two"
        filter_ = runner.chain.FILTER_CLASS(
            task=TASKS[0],
            task_statement=registry.statement(TASKS[0]),
            hard_limit_bytes=24_000 * 4,
        )
        out, _record = filter_._elide_text(text, literals)
        if out is not None:
            lines = str(out).splitlines()
            self.assertTrue(any(carrier in line for line in lines))
            self.assertFalse(str(out).strip().startswith("[elided"))

    def test_every_omitted_run_becomes_exactly_one_wellformed_note_at_k0(self) -> None:
        literals = sorted(
            {
                str(phrase)
                for values in registry.literal_phrases(TASKS[2]).values()
                for phrase in values
            }
        )
        view = registry.source_view(TASKS[2], 0)
        filter_ = runner.chain.FILTER_CLASS(
            task=TASKS[2],
            task_statement=registry.statement(TASKS[2]),
            hard_limit_bytes=24_000 * 4,
        )
        out, record = filter_._elide_text(view, literals)
        self.assertIsNotNone(out)
        notes = [line for line in str(out).splitlines() if mechanism.is_note_line(line)]
        self.assertTrue(notes)
        covered = 0
        for note in notes:
            match = re.match(r"^\[elided (\d+) lines (\d+)-(\d+)\]$", note)
            self.assertIsNotNone(match, note)
            count, first, last = (int(value) for value in match.groups())
            self.assertEqual(count, last - first + 1)
            covered += count
        self.assertEqual(record["lines_elided"], covered)

    def test_no_note_can_carry_a_literal_at_k0(self) -> None:
        for task_id in TASKS:
            literals = sorted(
                {
                    str(phrase)
                    for values in registry.literal_phrases(task_id).values()
                    for phrase in values
                }
            )
            view = registry.source_view(task_id, 0)
            filter_ = runner.chain.FILTER_CLASS(
                task=task_id,
                task_statement=registry.statement(task_id),
                hard_limit_bytes=24_000 * 4,
            )
            out, _record = filter_._elide_text(view, literals)
            for line in str(out or "").splitlines():
                if mechanism.is_note_line(line):
                    for literal in literals:
                        self.assertNotIn(literal.lower(), line.lower())

    def test_every_literal_keeps_its_occurrences_at_k0(self) -> None:
        """K=0 is the sharpest test of the invariant, and the invariant is per **occurrence**.

        A line carrying a literal may be elided at K=0 when the same literal still occurs
        elsewhere in the payload, because what the guard promises is that no occurrence is lost,
        not that every carrier line survives.  This test pins that reading on the real payloads:
        for every registered literal the count after elision is at least the count before.
        """
        for task_id in TASKS:
            runner.select_window(0)
            filter_ = runner.chain.FILTER_CLASS(
                task=task_id,
                task_statement=registry.statement(task_id),
                hard_limit_bytes=24_000 * 4,
            )
            items: list = [dict(message) for message in registry.history(task_id)]
            for index, name in enumerate(registry.task(task_id)["tool_names"]):
                call_id = f"call_{task_id}_{index}"
                items = items + [
                    {"type": "function_call", "call_id": call_id, "name": name, "arguments": "{}"},
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": registry.source_view(task_id, index),
                    },
                ]
                observed = json.loads(json.dumps(items))
                out = filter_.filter_items(observed)
                sent = list(observed) if out is None else list(out)
                for literal in filter_.literal_texts:
                    self.assertGreaterEqual(
                        runner._literal_count(sent, literal),
                        runner._literal_count(observed, literal),
                        f"K=0 {task_id}: {literal} lost an occurrence",
                    )
            self.assertEqual(
                0,
                filter_.v21_literal_guard_fallbacks,
                f"K=0 {task_id}: the guard refused a payload, so the stage is not engaged",
            )
            self.assertGreater(filter_.v21_bytes_saved, 0, task_id)

    def test_the_gate_k0_check_is_green(self) -> None:
        runner.select_window(0)
        with runner.v22_chain_installed():
            details = runner._k0_edge_case_checks()
        self.assertTrue(details["ok"], details)


class DoseIsolationTests(unittest.TestCase):
    """K is the only variable: everything else must be identical across the three groups."""

    def test_the_shape_arms_and_cap_are_identical_for_every_k(self) -> None:
        records = []
        for k in window.AUTHORISED_WINDOWS:
            runner.select_window(k)
            records.append(
                (
                    runner.SHAPE,
                    runner.ROUNDS,
                    runner.READS_PER_ROUND,
                    runner.ARMS,
                    runner.REPEATS,
                    runner.SAMPLES,
                    runner.MAX_API_REQUESTS,
                    runner.MAX_TURNS,
                    runner.MAX_SUMMARY_CALLS_PER_SAMPLE,
                    runner.chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS,
                )
            )
        self.assertEqual(1, len(set(records)), "the three groups must differ only in K")
        self.assertEqual((3, 1, 0), tuple(sorted({3, 1, 0}, reverse=True)))

    def test_each_k_has_its_own_experiment_id_and_artifact_paths(self) -> None:
        ids, gates, ledgers = set(), set(), set()
        for k in runner.AMENDMENT_WINDOWS:
            runner.select_window(k)
            ids.add(runner.EXPERIMENT_ID)
            gates.add(str(runner.GATE_OUT))
            ledgers.add(str(runner.REVISION_LEDGER))
            self.assertIn(f"k{k}b", runner.EXPERIMENT_ID)
        self.assertEqual(2, len(ids))
        self.assertEqual(2, len(gates))
        self.assertEqual(2, len(ledgers))

    def test_the_authorisations_are_explicit_and_k2_stays_refused(self) -> None:
        """Two authorisations exist, each naming its own groups, and an interpolated K is refused.

        The dose-02 authorisation covers the ``b`` groups at K=3 and K=1; the integrator's stage-3
        authorisation covers the K=0 repetition group.  A window covered by neither is refused, so
        no unregistered window can be paid for, while configuring any pre-registered window for the
        gate or a zero-API probe stays possible.
        """
        self.assertEqual((3, 1), runner.AMENDMENT_WINDOWS)
        self.assertEqual((0,), runner.STAGE_THREE_WINDOWS)
        self.assertEqual((3, 1, 0), runner.AUTHORISED_WINDOWS)
        for k in (3, 1, 0):
            runner.require_paid_authorisation(k)
        with self.assertRaises(SystemExit):
            runner.select_window(2)
        with self.assertRaises(SystemExit):
            runner.require_paid_authorisation(2)
        for k in runner.AUTHORISED_WINDOWS:
            self.assertEqual(k, int(runner.select_window(k)["k"]))

    def test_the_disclosure_text_is_identical_to_the_frozen_v21_chain_text(self) -> None:
        """The v22 chain restates the disclosure because it may not edit the frozen v21 chain.

        This asserts the restatement is byte-identical, so the required disclosure cannot drift
        between the two rounds.
        """
        from experiments.runners import openai_agents_v21_chain as chain21
        from experiments.runners import openai_agents_v22_chain as chain22

        self.assertEqual(chain21.TOOL_OUTPUT_DISCLOSURE, chain22.TOOL_OUTPUT_DISCLOSURE)
        self.assertEqual(
            tuple(chain21.FORBIDDEN_WORDING), tuple(chain22.FORBIDDEN_WORDING)
        )
        block = chain22.mechanism_block()
        self.assertIn("tool_output_disclosure", block)
        self.assertEqual(
            chain21.TOOL_OUTPUT_DISCLOSURE, block["tool_output_disclosure"]
        )

    def test_the_runner_uses_the_v22_chain_and_the_frozen_mechanism(self) -> None:
        source = (
            __import__("pathlib").Path(runner.__file__).read_text(encoding="utf-8")
        )
        self.assertIn("openai_agents_v21_carrier_elision as mechanism", source)
        self.assertIn("openai_agents_v22_chain as chain", source)
        self.assertNotIn("import openai_agents_v21_chain", source)


if __name__ == "__main__":
    unittest.main()
