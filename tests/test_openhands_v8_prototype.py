import importlib.util
from pathlib import Path
import unittest
from test_openhands_v5 import batch
spec=importlib.util.spec_from_file_location('prototype_v8',Path(__file__).resolve().parents[1]/'integrations/openhands/prototype_v8.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
class CapacityAllocationTests(unittest.TestCase):
    def test_multiple_small_required_methods_precede_large_method(self):
        source='def command():\n'+''.join('    value_'+str(i)+' = '+str(i)+'\n' for i in range(100))+'    return value_99\n\ndef add_command():\n    return 1\n\ndef get_command():\n    return 2\n'
        _,units,_,_,_=module.evidence_units(batch(104,'str_replace','success',source),'Implement command add_command get_command')
        names=[u['name'] for u in units]
        self.assertLess(names.index('add_command'),names.index('command'))
        self.assertLess(names.index('get_command'),names.index('command'))
        self.assertIn('return value_99',next(u['text'] for u in units if u['name']=='command'))
if __name__=='__main__': unittest.main()
