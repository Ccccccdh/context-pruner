"""Optional SDK tests: actual event batches, immutable input and native validation."""
import os
from pathlib import Path

import unittest

os.environ['LITELLM_LOCAL_MODEL_COST_MAP'] = 'True'
os.environ['TIKTOKEN_CACHE_DIR'] = str(Path(__file__).resolve().parents[1] / '.tooling/tiktoken')
try:
    import openhands.sdk
except ImportError:
    raise unittest.SkipTest('Optional OpenHands SDK is not installed')

from openhands.sdk.context.view import View
from openhands.sdk.event import ActionEvent, ObservationEvent, MessageEvent, SystemPromptEvent
from openhands.sdk.event.condenser import Condensation
from openhands.sdk.llm import LLM, Message, TextContent, MessageToolCall
from openhands.tools.file_editor.definition import FileEditorAction, FileEditorObservation
from context_pruner.adapters.openhands import ContextPrunerCondenser


def history():
    events = [SystemPromptEvent(system_prompt=TextContent(text='Keep instructions'), tools=[]),
              MessageEvent(source='user', llm_message=Message(role='user', content=[TextContent(text='Fix parser. Only edit parser.py.')]))]
    for i in range(5):
        call = MessageToolCall(id=f'call{i}', name='file_editor', arguments='{"command":"view","path":"parser.py"}', origin='completion')
        action = ActionEvent(thought=[], tool_name='file_editor', tool_call_id=call.id, tool_call=call,
                             action=FileEditorAction(command='view', path='parser.py'), llm_response_id=f'response{i}')
        observation = ObservationEvent(tool_name='file_editor', tool_call_id=call.id, action_id=action.id,
                                       observation=FileEditorObservation.from_text(command='view', text='irrelevant historical log ' * 1500))
        events.extend([action, observation])
    return View(events=events)


def safe_prefix_keeps_latest_batch_and_authoritative_events():
    view = history()
    original = view.model_dump(mode='json')
    llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
    adapter = ContextPrunerCondenser(trigger_tokens=3000, target_tokens=2000, hard_tokens=5000)
    result = adapter.condense(view, llm)
    assert isinstance(result, Condensation)
    assert view.model_dump(mode='json') == original
    assert not set(e.id for e in view.events[:2] + view.events[-2:]) & result.forgotten_event_ids
    projected = result.apply(view.events)
    checked = View(events=list(projected))
    checked.enforce_properties(view.events)
    assert [e.id for e in checked.events] == [e.id for e in projected]
    assert adapter.export_state()['audit'][-1]['after'] < adapter.export_state()['audit'][-1]['before']
    assert adapter.finalize({'success': True})['middleware']


def under_threshold_is_identity_and_no_state():
    view = history()
    adapter = ContextPrunerCondenser(trigger_tokens=1000000)
    assert adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused')) is view
    assert adapter.export_state()['middleware'] is None


def later_user_requirement_not_forgotten():
    view = history()
    requirement = MessageEvent(source='user', llm_message=Message(role='user', content=[TextContent(text='New requirement: preserve all hash characters.')]))
    view.events.insert(8, requirement)
    adapter = ContextPrunerCondenser(trigger_tokens=3000, target_tokens=2000, hard_tokens=5000)
    result = adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused'))
    assert isinstance(result, Condensation)
    assert requirement.id not in result.forgotten_event_ids


class OpenHandsAdapterTests(unittest.TestCase):
    def test_safe_prefix(self):
        safe_prefix_keeps_latest_batch_and_authoritative_events()

    def test_under_threshold(self):
        under_threshold_is_identity_and_no_state()

    def test_user_requirement(self):
        later_user_requirement_not_forgotten()
