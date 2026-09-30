"""Regression for missing task scope in a compressed OpenHands view."""
from pathlib import Path

from test_openhands_adapter import history, LLM, Condensation, View

from context_pruner.adapters.openhands_v41 import ContextPrunerCondenserV41
from integrations.openhands.edit_scope_guard_v41 import scope_error


def test_host_scope_is_in_safe_condensation_and_original_view_is_unchanged():
    contract = ('Edit only django/db/models/query.py and '
                'django/db/models/sql/compiler.py; backend operations are read-only.')
    view = history()
    before = view.model_dump(mode='json')
    adapter = ContextPrunerCondenserV41(
        trigger_tokens=1000, target_tokens=8000, hard_tokens=12000,
        protected_contract=contract,
    )
    result = adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused'))
    assert isinstance(result, Condensation)
    assert contract in result.summary
    assert view.model_dump(mode='json') == before
    checked = View(events=list(result.apply(view.events)))
    checked.enforce_properties(view.events)
    audit = adapter.export_state()['audit'][-1]
    assert audit['task_contract_chars'] >= len(contract)
    assert audit['after'] < audit['before']


def test_disallowed_backend_edit_receives_immediate_specific_scope(tmp_path):
    root = tmp_path / 'workspace'
    allowed = [root / 'django/db/models/query.py',
               root / 'django/db/models/sql/compiler.py']
    backend = root / 'django/db/backends/postgresql/operations.py'
    message = scope_error('str_replace', str(backend), root, allowed)
    assert 'Read-only source' in message
    assert 'django/db/models/query.py' in message
    assert 'django/db/models/sql/compiler.py' in message
    assert scope_error('view', str(backend), root, allowed) is None
    assert scope_error('str_replace', str(allowed[0]), root, allowed) is None
    assert scope_error('view', str(tmp_path / 'other.py'), root, allowed) == 'Outside workspace blocked.'
