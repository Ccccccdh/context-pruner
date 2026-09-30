import importlib.util
from pathlib import Path
import unittest
from test_openhands_adapter import history, LLM, Condensation
spec=importlib.util.spec_from_file_location('prototype_v9',Path(__file__).resolve().parents[1]/'integrations/openhands/prototype_v9.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class EditingWindowTests(unittest.TestCase):
    def test_recent_four_complete_batches_survive_when_budget_fits(self):
        view=history();adapter=module.ContextPrunerCondenserV9(trigger_tokens=1000,target_tokens=20000,hard_tokens=32000)
        result=adapter.condense(view,LLM(model='openai/deepseek-v4-flash',api_key='unused'))
        self.assertIsInstance(result,Condensation)
        self.assertFalse({e.id for e in view.events[-8:]} & result.forgotten_event_ids)
        self.assertLessEqual(adapter.export_state()['audit'][-1]['after'],32000)
    def test_window_shrinks_at_safe_boundaries_under_tighter_budget(self):
        view=history();adapter=module.ContextPrunerCondenserV9(trigger_tokens=1000,target_tokens=8000,hard_tokens=12000)
        result=adapter.condense(view,LLM(model='openai/deepseek-v4-flash',api_key='unused'))
        self.assertIsInstance(result,Condensation)
        self.assertFalse({e.id for e in view.events[-2:]} & result.forgotten_event_ids)
        self.assertTrue({e.id for e in view.events[-8:]} & result.forgotten_event_ids)
        self.assertLessEqual(adapter.export_state()['audit'][-1]['after'],12000)
if __name__=='__main__': unittest.main()
