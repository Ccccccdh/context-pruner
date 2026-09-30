"""Offline semantic projection and SDK structural gates for v2."""
import unittest
from test_openhands_adapter import history, LLM, View, Condensation
from context_pruner.adapters.openhands_v2 import ContextPrunerCondenserV2, project_events
from context_pruner.adapters.openhands_v3 import ContextPrunerCondenserV3, compact_path
from context_pruner.adapters.openhands_v4 import ContextPrunerCondenserV4


class ProjectionTests(unittest.TestCase):
    def test_semantic_code_and_flat_memory(self):
        view = history()
        view.events[3].observation.content[0].text = '1 def decode_escapes(s):\n2     return s.replace("\\\\", "\\")'
        rows = project_events(view.events[2:4])
        self.assertIn('def decode_escapes', rows[1]['content'])
        self.assertIn('\n2 ', rows[1]['content'])
        self.assertTrue(rows[1]['content'].startswith('文件=parser.py;'))
        summary = Condensation(summary='Plain memory\nsource code', summary_offset=2,
                               llm_response_id='offline').summary_event
        self.assertEqual(project_events([summary])[0]['content'], summary.summary)

    def test_task_state_structure_and_repeated_compression(self):
        view = history()
        original = view.model_dump(mode='json')
        adapter = ContextPrunerCondenserV2(trigger_tokens=3000, target_tokens=2000, hard_tokens=5000)
        llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
        result = adapter.condense(view, llm)
        self.assertIsInstance(result, Condensation)
        self.assertEqual(view.model_dump(mode='json'), original)
        self.assertFalse({e.id for e in view.events[:2] + view.events[-2:]} & result.forgotten_event_ids)
        state = adapter.export_state()
        self.assertIn('Only edit parser.py', state['middleware']['plugin']['task_state'])
        projected = result.apply(view.events)
        checked = View(events=list(projected))
        checked.enforce_properties(view.events)
        self.assertEqual([e.id for e in checked.events], [e.id for e in projected])
        adapter.condense(checked, llm)
        for record in adapter.export_state()['audit']:
            if 'forgotten_ids' in record:
                self.assertLess(record['after'], record['before'])

    def test_noop_control(self):
        adapter = ContextPrunerCondenserV2(trigger_tokens=1000000)
        view = history()
        self.assertIs(adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused')), view)
        self.assertIsNone(adapter.export_state()['middleware'])

    def test_v3_compact_path_and_safe_batch(self):
        self.assertEqual(compact_path('C:/experiment/workspaces/w005/src/parser.py'), 'src/parser.py')
        self.assertEqual(compact_path('C:/experiment/workspace/src/parser.py'), 'src/parser.py')
        view = history()
        adapter = ContextPrunerCondenserV3(trigger_tokens=3000, target_tokens=2000, hard_tokens=5000)
        result = adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused'))
        self.assertIsInstance(result, Condensation)
        self.assertFalse({e.id for e in view.events[:2] + view.events[-2:]} & result.forgotten_event_ids)
        checked = View(events=list(result.apply(view.events)))
        checked.enforce_properties(view.events)
        self.assertEqual(len(checked.events), len(result.apply(view.events)))
        self.assertIn('Only edit parser.py', adapter.export_state()['middleware']['plugin']['task_state'])

    def test_v4_never_recompresses_memory_without_new_history(self):
        view = history()
        llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
        adapter = ContextPrunerCondenserV4(trigger_tokens=100, target_tokens=80, hard_tokens=5000)
        summary = Condensation(summary='def critical_function():\n    return preserved_evidence\n' * 300,
                               summary_offset=2, forgotten_event_ids={e.id for e in view.events[2:-2]},
                               llm_response_id='offline')
        projected = View(events=list(summary.apply(view.events)))
        self.assertIs(adapter.condense(projected, llm), projected)
        self.assertEqual(adapter.export_state()['audit'][-1]['skipped'], 'no_new_history')
        self.assertIsNone(adapter.export_state()['middleware'])
