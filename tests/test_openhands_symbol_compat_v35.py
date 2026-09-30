"""Keep optional read-only command from poisoning OpenHands tool batches."""
from pathlib import Path
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations' / 'openhands'))
from symbol_tool_v35 import ScopedSymbolsAction, ScopedSymbolsTool
from source_navigation_v31 import SourceNavigator


def test_view_alias_returns_observation_and_rejects_other_commands(tmp_path):
    source = tmp_path / 'module.py'
    source.write_text('class Item:\n    pass\n', encoding='utf-8')
    navigator = SourceNavigator(tmp_path, [source], 'Inspect Item')
    tool = ScopedSymbolsTool.create(None, navigator)[0]
    action = ScopedSymbolsAction.model_validate({
        'path': str(source), 'query': 'Item', 'command': 'view'})
    observation = tool.executor(action)
    assert not observation.is_error and 'Item' in observation.text
    with pytest.raises(ValidationError):
        ScopedSymbolsAction.model_validate({
            'path': str(source), 'query': 'Item', 'command': 'delete'})
