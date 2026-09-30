import unittest
from test_openhands_v5 import batch
from test_openhands_adapter import history, LLM, View, Condensation
from context_pruner.adapters.openhands_v6 import ContextPrunerCondenserV6, evidence_units

class BudgetedEvidenceTests(unittest.TestCase):
    def test_full_task_contract_and_atomic_function_selection(self):
        task = 'Implement target_func. Preserve atomic collisions and generator identity. ' * 30
        events = batch(90, 'view', '1\t' + task)
        events[0] = events[0].model_copy(update={'action': events[0].action.model_copy(update={'path': 'TASK.md'})})
        source = '\n\n'.join(f'def unrelated_{i}():\n    return {i}' for i in range(200))
        source += '\n\ndef target_func():\n    first = 7\n    return first + 2\n'
        events += batch(91, 'str_replace', 'success', source)
        contracts, units, index, failures, stats = evidence_units(events, task)
        self.assertEqual(contracts, [task])
        self.assertEqual(units[0]['name'], 'target_func')
        self.assertIn('return first + 2', units[0]['text'])
        self.assertEqual(stats['file_versions']['parser.py'], 1)

    def test_exact_budget_and_immutable_source(self):
        view = history()
        source = '\n\n'.join(f'def function_{i}():\n    return "value_{i}"' for i in range(300))
        view = View(events=view.events[:-2] + batch(94, 'str_replace', 'success', source) + view.events[-2:])
        original = view.model_dump(mode='json')
        llm = LLM(model='openai/deepseek-v4-flash', api_key='unused')
        adapter = ContextPrunerCondenserV6(trigger_tokens=3000, target_tokens=2500, hard_tokens=5000)
        result = adapter.condense(view, llm)
        self.assertIsInstance(result, Condensation)
        self.assertEqual(view.model_dump(mode='json'), original)
        audit = adapter.export_state()['audit'][-1]
        self.assertLessEqual(audit['after'], 5000)
        self.assertLessEqual(audit['after'], max(2500, audit['protected_tokens']))
        self.assertGreater(audit['omitted_units'], 0)
        self.assertIn('Recover it using scoped_editor view', result.summary)
        self.assertFalse(set(e.id for e in view.events[-2:]) & result.forgotten_event_ids)

    def test_oversized_task_is_never_silently_truncated(self):
        events = batch(95, 'view', '1\t' + 'required constraint ' * 5000)
        events[0] = events[0].model_copy(update={'action': events[0].action.model_copy(update={'path': 'TASK.md'})})
        view = history()
        view = View(events=view.events[:-2] + events + view.events[-2:])
        adapter = ContextPrunerCondenserV6(trigger_tokens=1000, target_tokens=2000, hard_tokens=3000)
        self.assertIs(adapter.condense(view, LLM(model='openai/deepseek-v4-flash', api_key='unused')), view)
        self.assertEqual(adapter.export_state()['audit'][-1]['skipped'], 'protected_context_exceeds_hard_budget')
