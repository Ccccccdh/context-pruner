import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from experiments.runners import run_agents_minimal_evidence_confirmation_v8 as c


class ConfirmationRunnerTest(unittest.TestCase):
    def test_protocol_excludes_obsolete_inference_metadata(self):
        obsolete={'quality_interval','cost_test','formal_seeds','formal_repeats','development_repeats',
                  'screen_max_geomean_ratio','screen_min_consistent_instances'}
        self.assertFalse(obsolete.intersection(c.PROTOCOL))

    def test_float_artifact_is_quality_failure_not_audit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c.prepare(root/'frozen');original=c.ControlledMock
            def factory(w):
                if w.arm=='B1':
                    def fail(request):raise httpx.ReadError('stop after first sample')
                    return fail
                return original(w)
            with patch.object(c,'ControlledMock',side_effect=factory):c.run_batch(root/'frozen',root/'mock','offline',mock=True)
            summary=json.loads((root/'mock/summary.json').read_text());row=summary['samples'][0]
            trial=root/'mock'/row['sample_id'];artifact=json.loads((trial/'workspace/artifact.json').read_text())
            for item in artifact['rows']:item['value']=float(item['value'])
            c.base.write(trial/'workspace/artifact.json',artifact)
            row['independent']=dict(passed=False,schema_valid=False,reason='artifact_missing_or_wrong')
            c.base.write(trial/'report.json',row);c.base.write(root/'mock/summary.json',summary)
            a=c.audit_output(root/'frozen',root/'mock',mock=True)
            self.assertEqual(a['passed'],0);self.assertFalse(a['complete'])

    def test_fixed_cohort_and_mock_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);f=c.prepare(root/'frozen')
            self.assertEqual((len(c.task_set()),f['samples'],f['max_requests'],f['cap_usd']),(48,144,2304,30.))
            s=c.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=c.audit_output(root/'frozen',root/'mock',mock=True)
            self.assertEqual(s['samples_completed'],144);self.assertEqual(a['requests'],1296)
            self.assertTrue(a['offline_gate_passed']);self.assertEqual(a['host_archive_hits_delivered'],96)
            self.assertEqual(a['mock_B1_B2_equal_body_pairs'],432)
            with patch.object(c,'OPERATION_CLAIM',root/'operation.claim'):
                c.reserve_operation(root/'frozen',root/'paid')
                with self.assertRaises(FileExistsError):c.reserve_operation(root/'frozen',root/'again')
            f=json.loads((root/'frozen/freeze.json').read_text());f['cap_usd']=31
            c.base.write(root/'frozen/freeze.json',f);(root/'frozen/freeze.sha256').write_text(c.base.file_sha(root/'frozen/freeze.json'))
            with self.assertRaises(ValueError):c.verify(root/'frozen')

    def test_bound_predecessor_and_failure_denominator(self):
        original=c.base.file_sha
        def changed(path):return '0'*64 if Path(path).resolve()==c.PREDECESSOR.resolve() else original(path)
        with patch.object(c.base,'file_sha',side_effect=changed):
            with self.assertRaises(ValueError):c.predecessor()
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c.prepare(root/'frozen')
            def fail(request):raise httpx.ReadError('controlled')
            with patch.object(c,'ControlledMock',return_value=fail):c.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=json.loads((root/'mock/audit.json').read_text());self.assertEqual(a['unknown_usage_requests'],1)
            self.assertEqual(a['unattempted_samples'],143);self.assertFalse(a['complete'])


if __name__=='__main__':unittest.main()
