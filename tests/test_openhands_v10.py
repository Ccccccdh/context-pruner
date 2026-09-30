"""Concrete v10 risks: scaffold starvation and protected batch budget."""
import unittest
from test_openhands_adapter import history, LLM, Condensation, View
from test_openhands_v5 import batch
from context_pruner.adapters.openhands_v10 import ContextPrunerCondenserV10, evidence_units


class WorkingCodeTests(unittest.TestCase):
    def test_imports_do_not_rank_after_unrelated_functions(self):
        events = batch(1, 'view', '1\tfrom packaging.version import Version\n2\tclass Requirement:\n3\t    def unrelated(self):\n4\t        return 1')
        _, units, _, _, _ = evidence_units(events, 'Implement matches version')
        self.assertEqual(units[0]['name'], 'module_context')
        self.assertIn('from packaging.version', units[0]['text'])
        self.assertTrue(units[0]['scaffold'])

    def test_code_room_uses_safe_boundary_and_keeps_latest_batch(self):
        view = history()
        original = view.model_dump(mode='json')
        adapter = ContextPrunerCondenserV10(trigger_tokens=1000, target_tokens=8000, hard_tokens=12000)
        result = adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused'))
        self.assertIsInstance(result, Condensation)
        self.assertEqual(view.model_dump(mode='json'), original)
        self.assertFalse({e.id for e in view.events[-2:]} & result.forgotten_event_ids)
        self.assertTrue({e.id for e in view.events[-8:]} & result.forgotten_event_ids)
        checked = View(events=list(result.apply(view.events)))
        checked.enforce_properties(view.events)
        self.assertEqual([e.id for e in checked.events], [e.id for e in result.apply(view.events)])
        audit = adapter.export_state()['audit'][-1]
        self.assertLessEqual(audit['after'], 12000)
        self.assertGreaterEqual(audit['available_code_tokens'], 2048)


if __name__ == '__main__':
    unittest.main()
