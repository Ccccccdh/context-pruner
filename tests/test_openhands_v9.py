import importlib.util
from pathlib import Path
import unittest
from test_openhands_adapter import history, LLM, View, Condensation
from test_openhands_v5 import batch
from context_pruner.adapters.openhands_v9 import ContextPrunerCondenserV9
spec=importlib.util.spec_from_file_location('frozen_v9',Path(__file__).resolve().parents[1]/'integrations/openhands/prototype_v9.py')
prototype=importlib.util.module_from_spec(spec);spec.loader.exec_module(prototype)
class PackagedAdapterParityTests(unittest.TestCase):
    def test_defaults_and_compaction_match_frozen_experimental_configuration(self):
        released=ContextPrunerCondenserV9()
        self.assertEqual((released.trigger_tokens,released.target_tokens,released.hard_tokens),(24000,20000,32000))
        events=list(history().events)
        for i in range(3):events+=batch(110+i,'view','historical trace '*2000)
        view=View(events=events);original=view.model_dump(mode='json')
        llm=LLM(model='openai/deepseek-v4-flash',api_key='unused')
        frozen=prototype.ContextPrunerCondenserV9(trigger_tokens=24000,target_tokens=20000,hard_tokens=32000)
        expected=frozen.condense(view,llm);actual=released.condense(view,llm)
        self.assertIsInstance(actual,Condensation)
        self.assertEqual(actual.summary,expected.summary)
        self.assertEqual(actual.forgotten_event_ids,expected.forgotten_event_ids)
        self.assertEqual(released.export_state()['audit'],frozen.export_state()['audit'])
        self.assertEqual(view.model_dump(mode='json'),original)
if __name__=='__main__':unittest.main()
