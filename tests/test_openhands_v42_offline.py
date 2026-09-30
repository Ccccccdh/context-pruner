"""Offline gates for v42 state, source and failure feedback."""
from pathlib import Path

from test_openhands_adapter import history, LLM, Condensation

from context_pruner.adapters.openhands_v42 import ContextPrunerCondenserV42
from integrations.openhands.task_state_v42 import TaskState
from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
from integrations.openhands.feedback_v42 import format_correction_feedback
from integrations.openhands.symbol_tool_v42 import ScopedSymbolsActionV42, ScopedSymbolsExecutorV42


def test_current_state_marks_stale_verification_and_enters_memory():
    state = TaskState('Fix update conflict', 'query.py and compiler.py only')
    state.record_verification('rev1', '51 tests passed')
    state.record_feedback('rev1', 'One host assertion failed')
    rendered = state.render('rev2')
    assert 'STALE after source edit' in rendered
    assert '51 tests passed' in rendered
    adapter = ContextPrunerCondenserV42(trigger_tokens=1000, target_tokens=8000,
                                         hard_tokens=12000, protected_contract='Allowed edits only')
    adapter.set_current_state(rendered)
    result = adapter.condense(history(), LLM(model='openai/deepseek-v4-flash', api_key='unused'))
    assert isinstance(result, Condensation)
    assert rendered in result.summary
    assert adapter.protected_contract == 'Allowed edits only'


def test_source_member_check_and_covered_read_hint(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    source = root / 'where.py'
    source.write_text('class WhereNode:\n    def get_group_by_cols(self):\n        return []\n', encoding='utf-8')
    navigator = SourceNavigatorV42(root, [source], 'WhereNode get_refs')
    missing = navigator.inspect_member(source, 'WhereNode', 'get_refs')
    assert missing['defined_here'] is False
    assert 'get_group_by_cols' in missing['defined_methods']
    assert navigator.inspect_member(source, 'WhereNode', 'get_group_by_cols')['defined_here']
    navigator.view_hint(source, [1, 3])
    assert 'already covered' in navigator.view_hint(source, [2, 3])
    result = ScopedSymbolsExecutorV42(navigator)(ScopedSymbolsActionV42(
        path=str(source), class_name='WhereNode', member_name='get_refs'))
    assert '"defined_here": false' in str(result.model_dump(mode='json'))


def test_feedback_groups_new_regression_and_root_cause():
    old = 'ERROR: t1 (suite.Test.test_one)\nAssertionError: old\nRan 1 test\nFAILED (errors=1)'
    new = ('ERROR: t1 (suite.Test.test_one)\nAttributeError: WhereNode has no get_refs\n'
           'ERROR: t2 (suite.Test.test_two)\nAttributeError: WhereNode has no get_refs\n'
           'Ran 2 tests\nFAILED (errors=2)')
    feedback = format_correction_feedback(new, previous_log=old)
    assert 'new=1' in feedback
    assert '2x AttributeError: WhereNode has no get_refs' in feedback
    assert 't2' in feedback
