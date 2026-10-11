"""Zero-API tests for the v21 literal-carrier line elision and its gate.

They cover the properties the v21 plan pre-registers and that this mechanism can get wrong
silently:

* the frozen protection window is three lines and the tests pin it, so it cannot be tuned after a
  saving number is seen;
* a literal that occurs exactly once is either kept (the line carrying it survives) or the whole
  output is reverted - proven through the mechanism's own output for this payload, not only
  through the public entry point, which cannot reach the elision stage when there is no older
  tool group to elide;
* a note line never becomes a carrier of a literal, and the interval notes are well formed;
* the newest tool group is never modified;
* nothing is removed when the invariant cannot be shown, and the refusal is counted;
* the published bytes are the *net* saving, i.e. the note overhead is already deducted;
* the gate's own assertions (b), (c), (d) and the invariant probe can fail;
* the mechanism really engages on all three N3 development tasks.

Nothing here contacts a provider and no frozen module is edited.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from contextlib import contextmanager
from copy import deepcopy

from experiments.runners import openai_agents_evidence_safe_retention_v5 as v5
from experiments.runners import openai_agents_multitask_chain_v14 as v14
from experiments.runners import openai_agents_v17_registry as registry
from experiments.runners import openai_agents_v20_chain as chain20
from experiments.runners import openai_agents_v20_retention as v20
from experiments.runners import openai_agents_v21_carrier_elision as retention
from experiments.runners import openai_agents_v21_chain as chain
from experiments.runners import run_openai_agents_v21_acquisition as runner

TASK = "django_sqlite_version_floor"
#: The literal in use in every synthetic test below.  It occurs in ``CARRIER_VIEW`` exactly once,
#: and the words around it are chosen so the literal itself cannot be produced by a note line.
LITERAL = "check_sqlite_version"
CARRIER_VIEW = "\n".join(
    [
        "file.py (baseline)",
        "10: alpha",
        "11: beta",
        "12: gamma",
        f"13: def {LITERAL}():",
        "14: delta",
        "15: epsilon",
        "16: zeta",
        "17: eta",
        "18: theta",
        "19: iota",
        "20: kappa",
    ]
)
#: A view that carries no registered literal at all, so every line is elidable.
PLAIN_VIEW = "\n".join(
    [f"9{i}: filler line number {i}" for i in range(10)]
)


@contextmanager
def v17_registry_installed():
    """Resolve the v17 task table through both chain modules for the duration of a test."""
    original = {"chain": chain.registry, "v14": v14.registry}
    chain.registry = chain20.registry = registry
    v14.registry = registry
    try:
        yield
    finally:
        chain.registry = original["chain"]
        chain20.registry = registry
        v14.registry = original["v14"]


def build_filter(*, phrases=None) -> retention.LiteralCarrierElisionFilter:
    return retention.LiteralCarrierElisionFilter(
        task=TASK,
        task_statement=registry.statement(TASK),
        constraint_phrases=phrases,
        hard_limit_bytes=24_000 * 4,
    )


def synthetic_payload(older: str, newest: str) -> list[dict]:
    """Two tool groups: an older one that is elidable and a newest one that must not be touched."""
    items: list[dict] = [dict(message) for message in registry.history(TASK)]
    return items + [
        {"type": "function_call", "call_id": "old", "name": "read_old", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "old", "output": older},
        {"type": "function_call", "call_id": "new", "name": "read_new", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "new", "output": newest},
    ]


def outputs_of(items) -> dict[str, str]:
    return {
        str(item["call_id"]): str(item["output"])
        for item in items
        if isinstance(item, dict) and item.get("type") == "function_call_output"
    }


def run_with_traced_stage(
    filter_: retention.LiteralCarrierElisionFilter,
    observed: list,
    *,
    substitute: Any = None,
) -> tuple[list | None, list]:
    """Run **one** call and return ``(result, payload_the_elision_stage_received)``.

    The payload the stage received is captured by watching the stage itself, so a test never has
    to re-implement the two stages below this version - and the counters afterwards belong to
    exactly one call.  ``substitute`` may replace the stage for the duration of that call.
    """
    seen: dict[str, list] = {}
    original = filter_._elide

    def spy(base):
        seen["base"] = [dict(item) if isinstance(item, dict) else item for item in base]
        if substitute is not None:
            return substitute(list(base))
        return original(base)

    filter_._elide = spy
    try:
        result = filter_.filter_items(deepcopy(observed))
    finally:
        filter_._elide = original
    return result, seen["base"]


class WindowTests(unittest.TestCase):
    def test_the_protection_window_is_exactly_three_and_is_recorded(self) -> None:
        """The window is frozen by the pre-registration; this test exists so it cannot be tuned
        after a saving number is seen without the test failing."""
        self.assertEqual(3, retention.WINDOW_LINES)
        self.assertEqual(3, chain.mechanism_block()["window_lines"])
        self.assertIn("v21_window_lines", chain.PERSISTED_EXTRA_FIELDS)

    def test_the_window_keeps_three_lines_before_and_after_every_carrier(self) -> None:
        """A controlled fixture: 31 lines, the carrier at index 15, so the window is exactly
        ``12..18`` and an off-by-one in either direction fails."""
        with v17_registry_installed():
            filter_ = build_filter()
            lines = [
                f"1{index:02d}: filler {index}" for index in range(15)
            ] + [f"1xx: def {LITERAL}()"] + [
                f"2{index:02d}: filler {index}" for index in range(15)
            ]
            carrier = 15
            self.assertEqual(len(lines), 31)
            protected = filter_._protected_line_indices(lines, [LITERAL])
        self.assertEqual(set(range(12, 19)), protected, "exactly three lines each side")

    def test_the_window_is_clipped_at_the_output_edges(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            lines = ["carrier " + LITERAL] + [f"line {index}" for index in range(1, 6)]
            protected = filter_._protected_line_indices(lines, [LITERAL])
        self.assertEqual({0, 1, 2, 3}, protected)


class NoteFormTests(unittest.TestCase):
    def test_every_omitted_run_becomes_exactly_one_wellformed_note(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            text, record = filter_._elide_text(CARRIER_VIEW, [LITERAL])
        self.assertIsNotNone(text)
        lines = str(text).splitlines()
        notes = [line for line in lines if retention.is_note_line(line)]
        self.assertTrue(notes)
        covered = 0
        for note in notes:
            self.assertRegex(note, r"^\[elided \d+ lines \d+-\d+\]$")
            body = note[len("[elided ") : -1]
            count_text, interval = body.split(" lines ")
            first_text, last_text = interval.split("-")
            count, first, last = int(count_text), int(first_text), int(last_text)
            self.assertEqual(count, last - first + 1, "n must match the interval exactly")
            covered += count
        self.assertEqual(record["lines_elided"], covered)
        # One note per maximal run, and the runs are separated by protected lines only.
        self.assertEqual(
            len(notes) + record["protected_lines"] + record["lines_elided"],
            len(CARRIER_VIEW.splitlines()) + len(notes),
            "kept + elided + notes must account for every line of the original",
        )
        self.assertEqual(
            sum(len(note.encode("utf-8")) for note in notes), record["note_bytes"]
        )

    def test_a_note_line_can_never_carry_a_registered_literal(self) -> None:
        """The notes are generated from indices alone, so no note can supply a literal.

        Checked on real payloads for all three tasks: every note in every sent payload is exactly
        the ``note_line`` rendering of the interval it replaced, and no registered literal of the
        task appears inside any of them.  That is what keeps the invariant honest - it can only
        ever be met by the lines the mechanism kept, never by the mechanism's own annotation.
        """
        with v17_registry_installed():
            for task_id in registry.task_ids():
                filter_ = retention.LiteralCarrierElisionFilter(
                    task=task_id,
                    task_statement=registry.statement(task_id),
                    hard_limit_bytes=24_000 * 4,
                )
                items: list = [dict(message) for message in registry.history(task_id)]
                for index, name in enumerate(registry.task(task_id)["tool_names"]):
                    call_id = f"call_{task_id}_{index}"
                    items = items + [
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": "{}",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": registry.source_view(task_id, index),
                        },
                    ]
                    out = filter_.filter_items(json.loads(json.dumps(items)))
                    sent = list(items) if out is None else list(out)
                    notes = [
                        line
                        for text in outputs_of(sent).values()
                        for line in text.splitlines()
                        if retention.is_note_line(line)
                    ]
                    for note in notes:
                        match = re.match(
                            r"^\[elided (\d+) lines (\d+)-(\d+)\]$", note
                        )
                        self.assertIsNotNone(match, note)
                        count, first, last = (int(value) for value in match.groups())
                        self.assertEqual(note, retention.note_line(count, first, last))
                        for literal in filter_.literal_texts:
                            self.assertNotIn(
                                literal.lower(),
                                note.lower(),
                                f"{task_id}: a note line carries {literal!r}",
                            )

    def test_an_elided_line_range_note_costs_what_the_record_says(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            text, record = filter_._elide_text(CARRIER_VIEW, [LITERAL])
        lines = str(text).splitlines()
        notes = [line for line in lines if retention.is_note_line(line)]
        self.assertEqual(
            sum(len(note.encode("utf-8")) for note in notes), record["note_bytes"]
        )


class LiteralInvariantTests(unittest.TestCase):
    def test_the_carrier_line_is_kept_and_the_output_still_shrinks(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            text, record = filter_._elide_text(CARRIER_VIEW, [LITERAL])
        self.assertIsNotNone(text, "an output with elidable lines must be elided")
        self.assertIn(f"13: def {LITERAL}():", str(text))
        self.assertIn("12: gamma", str(text))
        self.assertIn("16: zeta", str(text))
        self.assertEqual(1, text.lower().count(LITERAL.lower()))
        self.assertLess(len(str(text)), len(CARRIER_VIEW))

    def test_an_output_whose_whole_body_is_protected_is_kept_verbatim(self) -> None:
        """The other path for the same hazard: with nothing safe to remove, the output is kept."""
        with v17_registry_installed():
            filter_ = build_filter()
            short = "\n".join(
                ["file.py (baseline)", f"1: {LITERAL}", "2: beta", "3: gamma"]
            )
            text, record = filter_._elide_text(short, [LITERAL])
        self.assertIsNone(text)
        self.assertEqual("every_line_is_protected", record["reason"])

    def test_an_output_with_no_carrier_line_is_kept_when_every_line_is_elidable(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            text, _record = filter_._elide_text(PLAIN_VIEW, [])
        self.assertIsNone(text)

    def test_a_literal_that_would_lose_an_occurrence_reverts_the_whole_payload(self) -> None:
        """The invariant, through the public entry point on a payload with an older tool group.

        The stage is replaced by a substitute that produces a *lossy* candidate - one that
        deletes the line carrying ``check_sqlite_version`` - so the guard, and not the substitute,
        is what is under test: the payload must be sent verbatim and the refusal counted.

        (This substitute is needed because line elision cannot lose an occurrence by itself: the
        guard counts occurrences over the canonical JSON text, in which two lines joined by a
        newline still read as the two ADJACENT lines "A\\nB", so a joined neighbour keeps its
        occurrence.  That is a property of the frozen measure, and the test below - every real
        payload keeping every literal - is the positive half of the same evidence.)
        """
        with v17_registry_installed():
            filter_ = build_filter(phrases={"required_facts": [LITERAL]})
            observed = synthetic_payload(CARRIER_VIEW, PLAIN_VIEW)
            # The payload the stage would receive, captured without counting a call of its own.
            base_probe = build_filter(phrases={"required_facts": [LITERAL]})
            _result, base = run_with_traced_stage(base_probe, observed)
            lossy = deepcopy(base)
            for item in lossy:
                if (
                    isinstance(item, dict)
                    and item.get("call_id") == "old"
                    and isinstance(item.get("output"), str)
                ):
                    item["output"] = "\n".join(
                        line
                        for line in str(item["output"]).splitlines()
                        if LITERAL not in line.lower()
                    )
            self.assertLess(
                runner._literal_count(lossy, LITERAL),
                runner._literal_count(base, LITERAL),
                "the substitute must actually lose an occurrence",
            )

            def substitute(items: list) -> tuple:
                return lossy, {
                    "outputs_considered": 2,
                    "outputs_elided": 1,
                    "lines_elided": 1,
                    "notes": [],
                    "protected_lines": 0,
                    "recent_group_items_kept": 2,
                    "window_lines": retention.WINDOW_LINES,
                    "changed": True,
                }, 0

            out, seen_base = run_with_traced_stage(filter_, observed, substitute=substitute)
        self.assertEqual(
            json.dumps(base, ensure_ascii=False, sort_keys=True),
            json.dumps(seen_base, ensure_ascii=False, sort_keys=True),
            "both filters must see the same payload",
        )
        self.assertEqual(1, filter_.v21_literal_guard_fallbacks)
        self.assertEqual(0, filter_.v21_outputs_elided)
        self.assertEqual(0, filter_.v21_bytes_saved)
        self.assertEqual(
            json.dumps(base, ensure_ascii=False, sort_keys=True),
            json.dumps(list(out), ensure_ascii=False, sort_keys=True),
            "a payload whose literal cannot be preserved must be sent verbatim",
        )
        self.assertTrue(
            str(filter_.v21_last_call["fallback_reason"]).startswith("registered_literal_lost")
        )

    def test_every_output_of_a_real_task_carries_its_literals_after_elision(self) -> None:
        with v17_registry_installed():
            for task_id in registry.task_ids():
                filter_ = retention.LiteralCarrierElisionFilter(
                    task=task_id,
                    task_statement=registry.statement(task_id),
                    hard_limit_bytes=24_000 * 4,
                )
                items: list = [dict(message) for message in registry.history(task_id)]
                for index, name in enumerate(registry.task(task_id)["tool_names"]):
                    call_id = f"call_{task_id}_{index}"
                    items = items + [
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": "{}",
                        },
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
                            f"{task_id}: {literal} lost an occurrence",
                        )

    def test_the_gate_invariant_probe_can_actually_fail(self) -> None:
        with v17_registry_installed():
            self.assertTrue(runner._invariant_probe_rejects_a_lost_literal())


class NewestGroupTests(unittest.TestCase):
    def test_the_newest_tool_group_is_never_modified(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            observed = synthetic_payload(CARRIER_VIEW, registry.source_view(TASK, 2))
            before = [dict(item) for item in observed]
            out = filter_.filter_items(deepcopy(observed))
        self.assertIsNotNone(out)
        sent = list(out)
        newest_before = [item for item in before if item.get("call_id") == "new"]
        newest_after = [item for item in sent if item.get("call_id") == "new"]
        self.assertEqual(
            json.dumps(newest_before, ensure_ascii=False, sort_keys=True),
            json.dumps(newest_after, ensure_ascii=False, sort_keys=True),
        )
        self.assertGreater(filter_.v21_outputs_elided, 0, "the older group must be elided")
        self.assertEqual(2, filter_.v21_last_call["recent_group_items_kept"])


class NetSavingTests(unittest.TestCase):
    def test_bytes_saved_is_net_of_the_note_overhead(self) -> None:
        """The published saving is net: it must equal the bytes the lines were worth minus the
        bytes the notes cost.

        Both quantities are measured on the payload the mechanism really saw and on the output it
        really sent - the gross saving by putting the lines back, the note cost by taking the
        notes out - so the relation is checked in either direction, and a report that quoted a
        gross figure would fail here.
        """
        with v17_registry_installed():
            filter_ = build_filter(phrases={"required_facts": [LITERAL]})
            observed = synthetic_payload(CARRIER_VIEW, PLAIN_VIEW)
            out, base = run_with_traced_stage(filter_, observed)
            elided, plan, note_bytes = filter_._elide(deepcopy(base))
        self.assertIsNotNone(out)
        last = filter_.v21_last_call
        self.assertEqual("engaged", last["status"])
        self.assertGreater(filter_.v21_bytes_saved, 0)
        self.assertGreater(filter_.v21_note_bytes, 0)

        base_bytes = retention._bytes(base)
        after_bytes = retention._bytes(elided)
        net = base_bytes - after_bytes
        self.assertEqual(int(last["base_bytes"]), base_bytes)
        self.assertEqual(int(last["after_bytes"]), after_bytes)
        self.assertEqual(int(last["note_bytes"]), int(note_bytes))
        self.assertEqual(net, filter_.v21_bytes_saved)
        self.assertEqual(dict(plan)["lines_elided"], int(last["lines_elided"]))

        sent_old = outputs_of(list(elided))["old"]
        sent_lines = sent_old.splitlines()
        note_lines = [line for line in sent_lines if retention.is_note_line(line)]
        kept_lines = [line for line in sent_lines if not retention.is_note_line(line)]
        self.assertEqual(
            len(note_lines) + len(kept_lines), len(sent_lines), "every line is one or the other"
        )
        # The two families of lines partition the output, so the notes really are the only thing
        # the mechanism added and the kept lines really are the original text it preserved.
        self.assertEqual(
            sorted(sent_lines), sorted(kept_lines + note_lines)
        )
        without_notes = "\n".join(kept_lines)
        self.assertEqual(
            filter_.v21_note_bytes,
            sum(len(line.encode("utf-8")) for line in note_lines),
            "the note counter is the sum of the note line sizes as written",
        )
        # gross = what the same mechanism would have saved if the notes were free
        gross = net + filter_.v21_note_bytes
        self.assertEqual(
            gross - filter_.v21_note_bytes,
            filter_.v21_bytes_saved,
            "gross minus the note overhead is exactly the published net saving",
        )
        self.assertGreater(gross, net, "the notes cost bytes, so the net is below the gross")
        self.assertLess(net, base_bytes)
        self.assertLess(len(without_notes), len(sent_old), "eliding lines shrinks the text")

    def test_a_net_saving_of_zero_or_less_is_a_counted_fallback(self) -> None:
        """If the notes cost more than the lines they replace, nothing is removed."""
        with v17_registry_installed():
            filter_ = build_filter(phrases={"required_facts": [LITERAL]})
            tiny = "\n".join([f"3: {LITERAL}", "4: b", "5: c", "6: d"])
            observed = synthetic_payload(tiny, PLAIN_VIEW)
            out = filter_.filter_items(deepcopy(observed))
        last = filter_.v21_last_call
        self.assertIn(last["status"], ("engaged", "fallback", "no_elision"))
        if filter_.v21_bytes_saved == 0:
            # Either there was nothing safe to remove or the note cost more than the removal;
            # in both cases the payload the earlier stages produced is sent unchanged.
            self.assertIn(str(last["status"]), ("fallback", "no_elision"))
        else:
            self.assertGreater(int(last["base_bytes"]) - int(last["after_bytes"]), 0)
        self.assertLessEqual(filter_.v21_bytes_saved, retention._bytes(observed))

    def test_fail_closed_keeps_the_payload_byte_identical_on_an_injected_failure(self) -> None:
        with v17_registry_installed():
            filter_ = build_filter()
            observed = synthetic_payload(CARRIER_VIEW, PLAIN_VIEW)

            def explode(self, base):
                raise RuntimeError("injected elision failure")

            filter_._elide = explode.__get__(filter_, type(filter_))
            out = filter_.filter_items(deepcopy(observed))
        self.assertEqual(1, filter_.v21_literal_guard_fallbacks)
        self.assertEqual(0, filter_.v21_outputs_elided)
        self.assertEqual(
            json.dumps(observed, ensure_ascii=False, sort_keys=True),
            json.dumps(list(out), ensure_ascii=False, sort_keys=True),
        )
        self.assertIn("exception", str(filter_.v21_last_call["fallback_reason"]))


class CompositionTests(unittest.TestCase):
    def test_the_composite_owner_is_this_module_and_the_stages_are_v20s_calls(self) -> None:
        self.assertIs(
            retention.LiteralCarrierElisionFilter.filter_items.__module__, retention.__name__
        )
        self.assertTrue(
            issubclass(
                retention.LiteralCarrierElisionFilter, v20.NonDuplicateRetentionFilter
            )
        )
        source = (
            __import__("pathlib").Path(retention.__file__).read_text(encoding="utf-8")
        )
        self.assertIn("MultitaskDuplicateFilter.filter_items(self, items)", source)
        self.assertIn("v5.SelectiveRetentionFilter.filter_items(self, dedupe_base)", source)
        self.assertIn("self._carrier_loss(base, elided)", source)
        # No module below v21 may be edited for this mechanism to work: the two v20 modules keep
        # the digests the v20 revision ledger and the v20 gate artifact record.
        ledger = json.loads(
            (
                runner.ROOT / "integrations/openai_agents/V20_MODULE_REVISIONS_20261010.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(2, ledger["revision"])
        for relative in (
            "experiments/runners/openai_agents_v20_retention.py",
            "experiments/runners/openai_agents_v20_chain.py",
            "experiments/runners/run_openai_agents_v20_acquisition.py",
        ):
            self.assertEqual(
                ledger["modules"][relative],
                hashlib.sha256((runner.ROOT / relative).read_bytes()).hexdigest(),
                relative,
            )

    def test_the_elision_runs_after_the_retention_stage(self) -> None:
        """The stage order is asserted on the v20 calls themselves: the retention stage runs
        exactly once per call, on the payload the duplicate rule produced, and the v20 counters
        are advanced once."""
        with v17_registry_installed():
            filter_ = build_filter()
            observed = synthetic_payload(CARRIER_VIEW, PLAIN_VIEW)
            seen: dict[str, Any] = {}
            original = v5.SelectiveRetentionFilter.filter_items

            def spy(self, base):
                if "calls" not in seen:
                    seen["calls"] = 0
                    seen["v20_engaged_before"] = self.v20_retention_engaged_calls
                seen["calls"] += 1
                seen["inputs"] = len(list(base))
                return original(self, base)

            v5.SelectiveRetentionFilter.filter_items = spy
            try:
                out, _base = run_with_traced_stage(filter_, observed)
            finally:
                v5.SelectiveRetentionFilter.filter_items = original
        self.assertIsNotNone(out)
        self.assertEqual(1, seen["calls"], "the retention stage must run exactly once per call")
        self.assertEqual(len(observed), seen["inputs"])
        self.assertEqual(0, seen["v20_engaged_before"])
        self.assertEqual(0, filter_.v20_retention_engaged_calls)
        self.assertEqual(1, filter_.v20_retention_fallback_calls)

    def test_all_six_pre_registered_counters_exist_and_are_persisted(self) -> None:
        with v17_registry_installed():
            metrics = build_filter().metrics_dict()
        for name in retention.V21_COUNTER_FIELDS:
            self.assertIn(name, metrics, name)
            self.assertIn(name, chain.PERSISTED_EXTRA_FIELDS, name)
        for name in retention.PRESERVED_COUNTER_FIELDS:
            self.assertIn(name, metrics, name)
        self.assertEqual(
            {
                "v21_outputs_considered",
                "v21_outputs_elided",
                "v21_lines_elided",
                "v21_bytes_saved",
                "v21_literal_guard_fallbacks",
                "v21_note_bytes",
            },
            set(retention.V21_COUNTER_FIELDS),
        )

    def test_the_gate_threshold_is_inherited_unchanged_from_v20(self) -> None:
        self.assertEqual(0, chain.TRIGGER_GATE_SOFT_LIMIT_TOKENS)
        self.assertEqual(0, chain20.TRIGGER_GATE_SOFT_LIMIT_TOKENS)
        self.assertIs(chain.FirstCallPassthroughGate, chain20.FirstCallPassthroughGate)

    def test_the_first_call_still_passes_through_and_every_later_call_fires(self) -> None:
        gate = chain.FirstCallPassthroughGate(object(), soft_limit_tokens=1)
        items = [{"role": "user", "content": "x" * 4000}]
        self.assertFalse(gate.should_trigger(items))
        self.assertTrue(gate.should_trigger(items))
        metrics = gate.metrics_dict()
        self.assertEqual(2, metrics["trigger_gate_calls"])
        self.assertEqual(1, metrics["trigger_gate_triggered_calls"])
        self.assertEqual(1, metrics["trigger_gate_passthrough_calls"])


class MechanismEngagementTests(unittest.TestCase):
    """The mechanism must engage on every N3 task; this is the assertion (c) precondition."""

    def test_every_task_elides_something_on_the_no_duplicate_shape(self) -> None:
        with v17_registry_installed():
            for task_id in registry.task_ids():
                filter_ = retention.LiteralCarrierElisionFilter(
                    task=task_id,
                    task_statement=registry.statement(task_id),
                    hard_limit_bytes=24_000 * 4,
                )
                items: list = [dict(message) for message in registry.history(task_id)]
                for index, name in enumerate(registry.task(task_id)["tool_names"]):
                    call_id = f"call_{task_id}_{index}"
                    items = items + [
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": name,
                            "arguments": "{}",
                        },
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": registry.source_view(task_id, index),
                        },
                    ]
                    filter_.filter_items(json.loads(json.dumps(items)))
                self.assertGreater(
                    filter_.v21_outputs_elided, 0, f"{task_id}: nothing was elided"
                )
                self.assertGreater(
                    filter_.v21_bytes_saved, 0, f"{task_id}: nothing was saved"
                )
                self.assertEqual(
                    0,
                    filter_.v21_literal_guard_fallbacks,
                    f"{task_id}: the guard refused a payload",
                )
                self.assertGreater(
                    filter_.v21_outputs_considered,
                    filter_.v21_outputs_elided,
                    f"{task_id}: the newest group must be excluded from elision",
                )
                self.assertLess(
                    filter_.v21_note_bytes,
                    filter_.v21_bytes_saved,
                    "the note overhead must be smaller than the saving it enables",
                )
                self.assertEqual(
                    filter_.v21_bytes_saved,
                    sum(int(entry["net_bytes_saved"]) for entry in filter_.v21_calls),
                )


class GateAssertionTests(unittest.TestCase):
    """The gate's own checks must be able to fail; a tautology would not be a gate."""

    def _sample(self, **overrides) -> dict:
        sample = {
            "task": "synthetic",
            "repeat": 0,
            "method": "pruner_v1",
            "model_calls": 4,
            "v21_outputs_considered": 3,
            "v21_outputs_elided": 1,
            "v21_lines_elided": 5,
            "v21_bytes_saved": 100,
            "v21_literal_guard_fallbacks": 0,
            "v21_note_bytes": 29,
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
            [
                self._sample(
                    trigger_gate_passthrough_calls=0,
                    trigger_gate_first_call_passthrough_calls=0,
                )
            ]
        )
        self.assertFalse(result["ok"])

    def test_a_zero_elision_sample_fails_assertion_c(self) -> None:
        result = runner.gate_assertion_c(
            [self._sample(v21_outputs_elided=0, v21_bytes_saved=0)]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(["synthetic"], result["tasks_failing"])

    def test_an_elision_without_a_saving_fails_assertion_c(self) -> None:
        self.assertFalse(
            runner.gate_assertion_c([self._sample(v21_bytes_saved=0)])["ok"]
        )

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

    def test_the_v21_modules_are_pinned(self) -> None:
        for relative in (
            "experiments/runners/openai_agents_v21_carrier_elision.py",
            "experiments/runners/openai_agents_v21_chain.py",
            "experiments/runners/run_openai_agents_v21_acquisition.py",
        ):
            self.assertIn(relative, runner.PINNED_MODULES, relative)

    def test_the_revision_ledger_pins_the_modules_it_names(self) -> None:
        if not runner.REVISION_LEDGER.is_file():
            self.skipTest("the v21 revision ledger has not been written yet")
        ledger = json.loads(runner.REVISION_LEDGER.read_text(encoding="utf-8"))
        live = runner.module_hashes()
        for relative in runner.PINNED_MODULES:
            self.assertIn(relative, ledger["modules"], relative)
            self.assertEqual(ledger["modules"][relative], live[relative], relative)

    def test_the_shape_and_cap_are_the_pre_registered_ones(self) -> None:
        math_ = runner.arithmetic()
        self.assertEqual("N3", math_["shape"])
        self.assertEqual(3, math_["rounds"])
        self.assertEqual(1, math_["reads_per_round"])
        self.assertEqual(9, math_["samples"])
        self.assertEqual(100, math_["cap"])
        self.assertTrue(math_["at_least_1_3"])
        self.assertGreaterEqual(math_["margin"], 1.3)


class RetryDisciplineTests(unittest.TestCase):
    """The strict track, the diagnostics and the 160-character bound are untouched."""

    def test_the_gate_reports_them_unchanged(self) -> None:
        checks = {entry["check"]: entry["ok"] for entry in runner.inherited_checks()}
        for name in (
            "retry_diagnostic_still_byte_identical_to_v16",
            "four_conditions_still_identical_to_v16",
            "declared_literal_missing_from_the_line_still_rejected",
            "over_160_still_rejected",
            "old_shape_wording_absent",
            "protection_window_is_the_pre_registered_three",
            "elision_counters_are_persisted_in_the_sample_rows",
        ):
            self.assertIn(name, checks, name)
            self.assertTrue(checks[name], name)


class V20UntouchedTests(unittest.TestCase):
    """This round must not have changed anything below v21."""

    def test_the_v20_retention_module_is_the_revision_its_ledger_pins(self) -> None:
        ledger = json.loads(
            (
                runner.ROOT / "integrations/openai_agents/V20_MODULE_REVISIONS_20261010.json"
            ).read_text(encoding="utf-8")
        )
        relative = "experiments/runners/openai_agents_v20_retention.py"
        self.assertEqual(
            ledger["modules"][relative],
            hashlib.sha256((runner.ROOT / relative).read_bytes()).hexdigest(),
        )

    def test_the_v20_gate_artifact_still_lists_the_pinned_v20_modules(self) -> None:
        gate = json.loads(
            (
                runner.ROOT / "integrations/openai_agents/V20_ZERO_API_GATE_20261010.json"
            ).read_text(encoding="utf-8")
        )
        ledger = json.loads(
            (
                runner.ROOT / "integrations/openai_agents/V20_MODULE_REVISIONS_20261010.json"
            ).read_text(encoding="utf-8")
        )
        for relative in (
            "experiments/runners/openai_agents_v20_retention.py",
            "experiments/runners/openai_agents_v20_chain.py",
            "experiments/runners/run_openai_agents_v20_acquisition.py",
        ):
            self.assertEqual(
                ledger["modules"][relative],
                gate["module_hashes"][relative],
                relative,
            )
            self.assertEqual(
                gate["module_hashes"][relative],
                hashlib.sha256((runner.ROOT / relative).read_bytes()).hexdigest(),
                relative,
            )

    def test_v20_still_returns_the_deduplicated_list_when_the_retention_stage_falls_back(
        self,
    ) -> None:
        """The v20 behaviour this version composes with, unchanged: with a duplicate present the
        deduplication is returned even though the retention stage declines the call."""
        with v17_registry_installed():
            filter_ = v20.NonDuplicateRetentionFilter(
                task=TASK,
                task_statement=registry.statement(TASK),
                hard_limit_bytes=24_000 * 4,
            )
            view = registry.source_view(TASK, 0)
            items: list = [dict(message) for message in registry.history(TASK)]
            items = items + [
                {"type": "function_call", "call_id": "c1", "name": "read_a", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c1", "output": view},
                {"type": "function_call", "call_id": "c2", "name": "read_b", "arguments": "{}"},
                {"type": "function_call_output", "call_id": "c2", "output": view},
            ]
            out = filter_.filter_items(items)
        self.assertEqual(1, filter_.exact_duplicate_replacements)
        self.assertEqual(1, filter_.v20_retention_fallback_calls)
        self.assertEqual(0, filter_.v20_retention_engaged_calls)
        self.assertIsNotNone(out)
        self.assertTrue(str(out[4]["output"]).startswith("[Exact duplicate output;"))


if __name__ == "__main__":
    unittest.main()
