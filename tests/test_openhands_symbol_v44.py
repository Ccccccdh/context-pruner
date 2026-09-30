"""Replay the two v43 tool-schema failures with the v44 action schema."""
import json
from pathlib import Path

from integrations.openhands.source_navigation_v42 import SourceNavigatorV42
from integrations.openhands.symbol_tool_v44 import ScopedSymbolsActionV44, ScopedSymbolsExecutorV44


def test_search_verb_from_v43_trajectory_is_accepted(tmp_path):
    source = tmp_path / 'sample.py'
    source.write_text('class Query:\n    def has_select_fields(self):\n        return True\n', encoding='utf-8')
    navigator = SourceNavigatorV42(tmp_path, [source], 'has_select_fields')
    action = ScopedSymbolsActionV44.model_validate(
        {'command': 'search', 'path': str(source), 'query': 'has_select_fields'})
    observation = ScopedSymbolsExecutorV44(navigator)(action)
    assert not observation.is_error
    assert 'has_select_fields' in json.dumps(observation.model_dump(mode='json'))
