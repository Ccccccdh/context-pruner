"""v73 long-context shape: registration, registry and zero-API gate, mechanised.

The v73 batch replaces the OpenAI Agents task shape.  v16/v24 registered three short views per
task (985-5,673 characters, every task marked ``<15 min fix``) and answered in one line, which
the audit measured at 3,342-5,604 plugin view tokens - far below the 28,000-token bar the
OpenHands adapters use.  v73 registers six DISTINCT read-once views per task on one causal
chain, so the context is large and no repetition is manufactured by the protocol.

These tests pin the four properties the batch's validity rests on:

1. the shape really is six distinct views read exactly once, with no reread anywhere in the
   protocol text;
2. the agent-visible read payload is large enough to matter, computed with the host's own
   counter;
3. each contract genuinely needs evidence from at least three different views, so no single or
   double read can answer it;
4. the answer stays mechanically checkable (one line, <=160 characters, required literals
   present), so "quality not degraded" remains a mechanical judgement.

Zero API: no network, no provider, no paid path.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from context_pruner.types import estimate_tokens  # noqa: E402
from experiments.runners import openai_agents_v73_long_context_registry as reg  # noqa: E402

REGISTRATION = ROOT / 'integrations/openai_agents/V73_LONG_CONTEXT_SOURCE_REGISTRATION.json'
GATE = ROOT / 'integrations/openai_agents/V73_ZERO_API_GATE.json'
#: The bar the OpenHands adapters use, kept as the reference the shape must clear.
TRIGGER_BAR_TOKENS = 28000


def gate_payload() -> dict:
    return json.loads(GATE.read_text(encoding='utf-8'))


# -- 1. the shape -----------------------------------------------------------------------
def test_registration_declares_three_long_context_tasks() -> None:
    payload = json.loads(REGISTRATION.read_text(encoding='utf-8'))
    assert payload['registered_task_count'] == 3
    assert payload['paid_requests'] == 0
    assert payload['reading_reference_patch_or_test_patch'] is False
    assert set(payload['tasks']) == set(reg.task_ids())


def test_every_task_registers_six_distinct_views() -> None:
    for task_id in reg.task_ids():
        entry = reg.registration()['tasks'][task_id]
        views = entry['views']
        assert len(views) == 6, task_id
        pairs = [(v['file'], v['first_line'], v['last_line']) for v in views]
        assert len(set(pairs)) == 6, (task_id, 'views are not all distinct')
        assert len({v['slot'] for v in views}) == 6, task_id


def test_the_protocol_never_reads_a_registered_view_twice() -> None:
    """The property that separates v73 from v16, whose saving came from a double read."""
    for task_id in reg.task_ids():
        row = reg.protocol_reads_each_view_once(task_id)
        assert row['registered_views'] == 6, task_id
        assert row['each_view_mentioned_once'], (task_id, row['per_view_range_mentions'])
        assert row['per_view_range_mentions'] == [1] * 6, (task_id,
                                                           row['per_view_range_mentions'])
        assert row['rounds_named'] == 6, (task_id, row['rounds_named'])


def test_read_rounds_and_model_calls_match_the_six_view_shape() -> None:
    assert reg.READ_ROUNDS == 6
    assert reg.READS_PER_ROUND == 1
    assert reg.READ_CALLS == 6
    assert reg.EXPECTED_MODEL_CALLS == 7


# -- 2. the payload is large enough -----------------------------------------------------
def measured_read_tokens(task_id: str) -> int:
    entry = reg.registration()['tasks'][task_id]
    return sum(estimate_tokens(reg.view_text(entry['base_commit'], v['file'],
                                             v['first_line'], v['last_line']))
               for v in entry['views'])


def test_every_task_clears_the_trigger_bar_on_the_read_payload() -> None:
    for task_id in reg.task_ids():
        tokens = measured_read_tokens(task_id)
        assert tokens >= TRIGGER_BAR_TOKENS, (task_id, tokens)
        row = next(r for r in gate_payload()['tasks'] if r['task_id'] == task_id)
        assert row['final_read_payload_estimated_tokens'] == tokens, task_id
        assert row['reaches_28000_final_read'] is True, task_id


def test_the_shape_is_much_larger_than_the_v24_shape_it_replaces() -> None:
    """Recorded comparison so a reader sees what changed, in the host's own units."""
    v24_reference = {  # measured in the v24 batch's own gate counters
        'autoreload_template_changed': 3342,
        'sitemap_callable_lastmod': 5604,
        'query_utils_unregister_lookup': 4115,
    }
    largest_v24 = max(v24_reference.values())
    for task_id in reg.task_ids():
        assert measured_read_tokens(task_id) > largest_v24, task_id


# -- 3. the contract needs more than one view ------------------------------------------
def test_each_contract_needs_at_least_three_distinct_views() -> None:
    for task_id in reg.task_ids():
        entry = reg.registration()['tasks'][task_id]
        contract = entry['contract']
        facts = contract['required_facts']
        assert len(facts) >= 3, (task_id, facts)
        views_by_file = {v['file']: reg.view_text(entry['base_commit'], v['file'],
                                                  v['first_line'], v['last_line'])
                         for v in entry['views']}
        # each fact must appear in at least one registered view
        for fact in facts:
            assert any(fact in text for text in views_by_file.values()), (task_id, fact)
        # and the cause/fix tokens must live in DIFFERENT views
        cause_views = [f for f, t in views_by_file.items() if contract['cause_token'] in t]
        fix_views = [f for f, t in views_by_file.items() if contract['fix_token'] in t]
        assert cause_views, (task_id, 'cause token is in no view')
        assert fix_views, (task_id, 'fix token is in no view')
        assert set(cause_views) != set(fix_views), (task_id, cause_views, fix_views)


def test_required_facts_are_verbatim_and_multi_source() -> None:
    """Every declared fact must be findable; at least three facts must be in three views."""
    for task_id in reg.task_ids():
        entry = reg.registration()['tasks'][task_id]
        contract = entry['contract']
        texts = [reg.view_text(entry['base_commit'], v['file'], v['first_line'], v['last_line'])
                 for v in entry['views']]
        owner_files = []
        for fact in contract['required_facts']:
            holders = [entry['views'][i]['file'] for i, t in enumerate(texts) if fact in t]
            assert holders, (task_id, fact, 'fact absent from every registered view')
            owner_files.append(holders[0])
        assert len(set(owner_files)) >= 3, (task_id, list(zip(contract['required_facts'],
                                                             owner_files)))


# -- 4. the answer stays mechanically checkable ----------------------------------------
def test_answer_contract_is_one_line_and_within_160_characters() -> None:
    for task_id in reg.task_ids():
        contract = reg.registration()['tasks'][task_id]['contract']
        assert contract['max_answer_chars'] == 160, task_id
        assert contract['shape'] == 'RESULT issue=<issue> cause=<cause> fix=<fix>', task_id
        minimal = (f"RESULT issue={contract['issue_value']} "
                   f"cause={contract['cause_token']} fix={contract['fix_token']}")
        assert len(minimal) <= 160, (task_id, len(minimal))
        # the frozen regex must accept that minimal line
        assert re.fullmatch(contract['frozen_regex'], minimal), (task_id, minimal)
        # and the tool names the plan expects must exist for every planned position
        assert len(reg.registration()['tasks'][task_id]['tool_names']) == 6, task_id


def test_every_view_range_exists_in_its_file() -> None:
    for task_id in reg.task_ids():
        entry = reg.registration()['tasks'][task_id]
        for view in entry['views']:
            text = reg.view_text(entry['base_commit'], view['file'],
                                 view['first_line'], view['last_line'])
            lines = text.splitlines()
            assert lines, (task_id, view['file'])
            assert len(lines) == view['last_line'] - view['first_line'] + 2, (
                task_id, view['file'], 'header line plus the registered range')


# -- 5. the gate artifact agrees with the code -----------------------------------------
def test_gate_artifact_is_consistent_with_the_registry() -> None:
    payload = gate_payload()
    assert payload['paid_requests'] == 0
    assert payload['all_gates_passed'] is True
    assert payload['read_rounds'] == reg.READ_ROUNDS
    assert payload['expected_model_calls'] == reg.EXPECTED_MODEL_CALLS
    assert payload['registration_sha256'] == __import__('hashlib').sha256(
        REGISTRATION.read_bytes()).hexdigest()
    rows = {r['task_id']: r for r in payload['tasks']}
    assert set(rows) == set(reg.task_ids())
    for task_id, row in rows.items():
        assert row['views'] == 6, task_id
        assert row['protocol_reads_each_view_once'] is True, task_id
        assert row['reaches_28000_final_read'] is True, task_id
    for task_id, row in payload['renderer_feasibility'].items():
        assert row['within_160'] is True, task_id


def test_trigger_behaviour_keeps_the_below_threshold_passthrough() -> None:
    """v24 set soft=0/1 so the gate fired on every call; v73 keeps a finite limit, which is
    what makes the below-threshold pass-through property meaningful."""
    row = gate_payload()['trigger_behaviour']
    assert row['v16_soft_limit_used'] == 5435
    assert row['above_v16_soft_limit_acts'] is True


def test_the_registry_does_not_edit_the_v24_module() -> None:
    """The fork rebinds names; it must not rewrite the parent's bytes."""
    import hashlib
    parent = ROOT / 'experiments/runners/openai_agents_v24_registry.py'
    before = hashlib.sha256(parent.read_bytes()).hexdigest()
    import importlib
    importlib.reload(reg)
    after = hashlib.sha256(parent.read_bytes()).hexdigest()
    assert before == after
