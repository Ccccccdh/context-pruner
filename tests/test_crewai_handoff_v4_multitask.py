"""Frozen multi-task CrewAI r4 audit gate, without API requests."""

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from experiments.audits.audit_crewai_handoff_v4_multitask import audit


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / 'integrations/crewai/PRE_RUN_FREEZE_TWO_ROLE_R4_MULTITASK_DEV.json'
MOCK = ROOT / 'runs/stage5-crewai/crewai-two-role-r4-multitask-mock-01'


class CrewAIMultitaskAuditTest(unittest.TestCase):
    def test_mock_grid_matches_frozen_multitask_contract(self):
        result = audit(MOCK, FREEZE, dry_run_mock=True)
        self.assertTrue(result['complete'] and result['freeze_checked'])
        self.assertFalse(result['errors'])
        self.assertEqual(result['rows'], 27)
        self.assertEqual(result['request_attempts'], 0)

    def test_freeze_rejects_source_and_manifest_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            batch = tmp_path / 'mock'
            batch.mkdir()
            for name in ('manifest.json', 'results.jsonl'):
                shutil.copy2(MOCK / name, batch / name)
            frozen = json.loads(FREEZE.read_text(encoding='utf-8'))
            source = next(iter(frozen['source_sha256']))
            frozen['source_sha256'][source] = '0' * 64
            wrong = tmp_path / 'wrong-freeze.json'
            wrong.write_text(json.dumps(frozen), encoding='utf-8')
            self.assertTrue(any('frozen source mismatch' in issue for issue in
                                audit(batch, wrong, dry_run_mock=True)['errors']))
            manifest = json.loads((batch / 'manifest.json').read_text(encoding='utf-8'))
            manifest['max_api_requests'] += 1
            (batch / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.assertIn('manifest mismatch: max_api_requests',
                          audit(batch, FREEZE, dry_run_mock=True)['errors'])


if __name__ == '__main__':
    unittest.main()
