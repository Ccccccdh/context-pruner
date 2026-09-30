"""Concrete v10 risks: scaffold starvation and protected batch budget."""
import unittest
from test_openhands_adapter import history, LLM, Condensation, View
from test_openhands_v5 import batch
from context_pruner.adapters.openhands_v13 import ContextPrunerCondenserV13, evidence_units, confirmed_edit_notes, symbol_locations, workflow_facts


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
        adapter = ContextPrunerCondenserV13(trigger_tokens=1000, target_tokens=8000, hard_tokens=12000)
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


class SymbolRecoveryTests(unittest.TestCase):
    def test_confirmed_edit_pins_enclosing_function_but_failure_does_not(self):
        events = batch(1, 'str_replace', 'success', 'def helper():\n    return 2\n')
        events[0] = events[0].model_copy(update={'action': events[0].action.model_copy(update={'new_str': 'return 2'})})
        notes, touched = confirmed_edit_notes(events)
        self.assertIn(('parser.py', 'helper'), touched)
        self.assertEqual(notes[0]['symbols'], ['helper'])
        events[1] = events[1].model_copy(update={'observation': events[1].observation.model_copy(update={'is_error': True})})
        self.assertEqual(confirmed_edit_notes(events), ([], set()))

    def test_body_omission_keeps_observed_symbol_location(self):
        events = batch(1, 'view', '10\tdef completion():\n11\t    return 1')
        _, units, _, _, _ = evidence_units(events, 'something else')
        locations = symbol_locations(units)
        self.assertIn('completion:10-11', locations)
        self.assertNotIn('return 1', locations)


class WorkflowEvidenceTests(unittest.TestCase):
    def observation(self, text, error=False):
        from types import SimpleNamespace
        return SimpleNamespace(observation=SimpleNamespace(is_error=error),tool_name='scoped_tests',
            to_llm_message=lambda: __import__('test_openhands_adapter').Message(role='tool',content=[__import__('test_openhands_adapter').TextContent(text=text)],tool_call_id='test'))
    def test_passing_public_checkpoint_invalidated_by_successful_edit(self):
        passed=self.observation('Public tests only\n{"passed": true, "code_revision": "abc"}')
        self.assertEqual(workflow_facts([passed])['public_verification'],'passed')
        events=[passed]+batch(1,'str_replace','success','x = 2')
        self.assertEqual(workflow_facts(events)['public_verification'],'stale_after_edit')
        self.assertEqual(workflow_facts(events)['feature_acceptance'],'not_inferred')
    def test_failed_edit_does_not_invalidate_and_bad_json_does_not_pass(self):
        passed=self.observation('{"passed": true, "code_revision": "abc"}')
        events=[passed]+batch(1,'str_replace','failed',error=True)
        self.assertEqual(workflow_facts(events)['public_verification'],'passed')
        self.assertEqual(workflow_facts([self.observation('tests passed!',error=False)])['public_verification'],'unknown')
    def test_editor_changes_without_test_are_pending_not_verified(self):
        facts=workflow_facts(batch(1,'str_replace','success','x = 2'))
        self.assertEqual(facts['public_verification'],'not_observed')
        self.assertEqual(facts['successful_editor_mutations'],1)


if __name__ == '__main__':
    unittest.main()
