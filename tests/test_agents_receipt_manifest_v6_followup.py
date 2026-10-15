import tempfile
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from experiments.runners import run_agents_receipt_manifest_v6 as v6
from experiments.runners import run_agents_receipt_manifest_v6_followup as follow


class FollowupTest(unittest.TestCase):
    def test_truncated_predecessor_manifest_cannot_become_new_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'frozen').mkdir();(root/'paid').mkdir()
            original=follow.PRIOR
            for name in ('frozen/freeze.json','paid/summary.json'):
                (root/name).write_bytes((original/name).read_bytes())
            (root/'completion_manifest.json').write_text('{}')
            (root/'paid-operation.claim').write_text(json.dumps(dict(
                freeze=str((root/'frozen').resolve()),out=str((root/'paid').resolve()),
                freeze_sha256=v6.base.file_sha(root/'frozen/freeze.json'),max_requests=96,cap_usd=1.)))
            with patch.object(follow,'PRIOR',root),patch.object(v6,'verify',return_value={}):
                with self.assertRaisesRegex(ValueError,'manifest identity'):
                    follow.predecessor_binding()

    def test_configuration_restored_even_on_error(self):
        before=(v6.SEEDS,v6.OPERATION_CLAIM,v6.PROTOCOL,v6.SOURCES)
        with self.assertRaises(RuntimeError):
            with follow.cohort():
                self.assertEqual(v6.SEEDS,(227,229))
                self.assertNotEqual(v6.OPERATION_CLAIM,before[1])
                self.assertEqual(v6.PROTOCOL['prior_run_binding']['http_requests'],35)
                raise RuntimeError('controlled')
        self.assertEqual((v6.SEEDS,v6.OPERATION_CLAIM,v6.PROTOCOL,v6.SOURCES),before)

    def test_new_matrix_actual_sdk_mock_and_exclusive_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);f=follow.prepare(root/'frozen')
            self.assertEqual(f['samples'],6);self.assertEqual(f['cap_usd'],1.)
            self.assertEqual(f['tasks'],['receiptdev227','receiptdev229'])
            s=follow.run_batch(root/'frozen',root/'mock','offline',mock=True)
            self.assertEqual(s['samples_completed'],6)
            self.assertTrue(all(x['independent']['passed'] for x in s['samples']))
            a=follow.audit_output(root/'frozen',root/'mock',mock=True)
            self.assertTrue(a['complete']);self.assertEqual(a['requests'],66)
            with patch.object(follow,'OPERATION_CLAIM',root/'operation.claim'):
                follow.reserve_operation(root/'frozen',root/'paid')
                with self.assertRaises(FileExistsError):follow.reserve_operation(root/'frozen',root/'again')

    def test_unbound_prior_and_nested_cohorts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(follow,'PRIOR',Path(directory)):
                with self.assertRaises(ValueError):follow.predecessor_binding()
        with follow.cohort():
            with self.assertRaises(RuntimeError):
                with follow.cohort():pass


if __name__=='__main__':unittest.main()
