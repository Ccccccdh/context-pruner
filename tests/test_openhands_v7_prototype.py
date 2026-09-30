import importlib.util
from pathlib import Path
import unittest
from test_openhands_v5 import batch

spec = importlib.util.spec_from_file_location('prototype_v7', Path(__file__).resolve().parents[1] / 'integrations/openhands/prototype_v7.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SparseMethodTests(unittest.TestCase):
    def test_multiline_method_without_class_header(self):
        source = '100\t    def add_command(\n101\t        self, cmd, name=None, aliases=None\n102\t    ):\n103\t        if aliases:\n104\t            self._aliases.update(aliases)\n105\t        return cmd\n106\t\n107\t    def unrelated(self):\n108\t        return None'
        _, units, _, _, _ = module.evidence_units(batch(101, 'view', source), 'Implement add_command with aliases')
        target = next(u for u in units if u['name'] == 'add_command')
        self.assertEqual(target['range'], [100, 106])
        self.assertIn('return cmd', target['text'])
        self.assertNotIn('def unrelated', target['text'])
        self.assertEqual(units[0]['name'], 'add_command')

    def test_unobserved_gap_is_not_claimed_as_code(self):
        source = '100\t    def get_command(self, ctx, name):\n101\t        result = self.commands.get(name)\n110\t        return result'
        _, units, _, _, _ = module.evidence_units(batch(102, 'view', source), 'Implement get_command')
        target = next(u for u in units if u['name'] == 'get_command')
        self.assertEqual(target['range'], [100, 101])
        self.assertNotIn('return result', target['text'])

if __name__ == '__main__':
    unittest.main()
