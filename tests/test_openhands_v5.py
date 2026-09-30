"""SDK-safe compaction, snapshot invalidation and duplicate-read regression gates."""
import unittest
from test_openhands_adapter import history, LLM, View, Condensation, ActionEvent, ObservationEvent, MessageEvent, Message, TextContent, MessageToolCall, FileEditorAction, FileEditorObservation
from context_pruner.adapters.openhands_v5 import ContextPrunerCondenserV5, project_current_evidence


def batch(number, command, text, new_content=None, error=False):
    call = MessageToolCall(id=f'v5-{number}', name='file_editor', arguments='{}', origin='completion')
    action = ActionEvent(thought=[], tool_name='file_editor', tool_call_id=call.id, tool_call=call,
                         action=FileEditorAction(command=command, path='parser.py', old_str='old' if command == 'str_replace' else None),
                         llm_response_id=f'v5-response-{number}')
    observation = FileEditorObservation.from_text(command=command, text=text, is_error=error)
    observation = observation.model_copy(update={'new_content': new_content})
    result = ObservationEvent(tool_name='file_editor', tool_call_id=call.id, action_id=action.id, observation=observation)
    return [action, result]


class VersionedEvidenceTests(unittest.TestCase):
    def test_edit_invalidates_old_read_and_failed_edit_does_not_update(self):
        events = batch(0, 'view', '1\tdef old_behavior():\n2\t    return "old"')
        events += batch(1, 'str_replace', 'Edit succeeded', 'def new_behavior():\n    return "new"\n')
        events += batch(2, 'str_replace', 'Could not replace old_behavior', 'WRONG SNAPSHOT', error=True)
        rows, stats = project_current_evidence(events)
        code = '\n'.join(r['content'] for r in rows if r['role'] == 'tool')
        self.assertIn('def new_behavior', code)
        self.assertNotIn('old_behavior', code)
        self.assertNotIn('WRONG SNAPSHOT', code)
        self.assertEqual(stats['file_versions'], {'parser.py': 1})
        self.assertEqual(stats['superseded_observations'], 1)
        self.assertTrue(any('Tool failed' in r['content'] for r in rows))

    def test_numbered_reads_merge_without_losing_disjoint_ranges(self):
        events = batch(0, 'view', '1\tfirst\n2\tsecond') + batch(1, 'view', '2\tsecond\n5\tfifth')
        rows, stats = project_current_evidence(events)
        content = '\n'.join(r['content'] for r in rows)
        self.assertEqual(content.count('2\tsecond'), 1)
        self.assertIn('1\tfirst', content)
        self.assertIn('5\tfifth', content)
        self.assertEqual(stats['duplicate_read_lines'], 1)

    def test_two_condensations_rebuild_once_from_originals(self):
        view = history()
        original = view.model_dump(mode='json')
        adapter = ContextPrunerCondenserV5(trigger_tokens=3000, target_tokens=2000, hard_tokens=5000)
        llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
        first = adapter.condense(view, llm)
        self.assertIsInstance(first, Condensation)
        self.assertEqual(view.model_dump(mode='json'), original)
        later = MessageEvent(source='user', llm_message=Message(role='user', content=[TextContent(text='Preserve the new requirement')]))
        next_view = View(events=list(first.apply(view.events)) + [later] + batch(9, 'view', '1\tdef keep_code():\n2\t    return 9'))
        second = adapter.condense(next_view, llm)
        self.assertIsInstance(second, Condensation)
        self.assertEqual(second.summary.count('Archived evidence rebuilt from original events.'), 1)
        self.assertNotIn(later.id, second.forgotten_event_ids)
        self.assertFalse({e.id for e in next_view.events[-2:]} & second.forgotten_event_ids)
        checked = View(events=list(second.apply(next_view.events)))
        checked.enforce_properties(next_view.events)
        self.assertEqual([e.id for e in checked.events], [e.id for e in second.apply(next_view.events)])

    def test_unknown_summary_after_restart_is_preserved(self):
        view = history()
        summary = Condensation(summary='external evidence ' * 1000, summary_offset=2,
                               forgotten_event_ids={e.id for e in view.events[2:-2]}, llm_response_id='external')
        restored = View(events=list(summary.apply(view.events)))
        adapter = ContextPrunerCondenserV5(trigger_tokens=100)
        self.assertIs(adapter.condense(restored, LLM(model='openai/deepseek-v4-flash', api_key='unused')), restored)
        self.assertEqual(adapter.export_state()['audit'][-1]['skipped'], 'unknown_summary_preserved')
