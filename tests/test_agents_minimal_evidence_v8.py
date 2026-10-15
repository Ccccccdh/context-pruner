import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
from experiments.runners import run_agents_minimal_evidence_v8 as v8
from experiments.runners import run_agents_confirmation_v4 as base


class MinimalEvidenceSDKTest(unittest.TestCase):
    def test_screening_code_is_frozen_and_drift_rejected(self):
        name='experiments/audits/analyze_agents_minimal_evidence_v8.py'
        self.assertIn(name,v8.SOURCES)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v8.prepare(root/'frozen');original=base.file_sha
            def changed(path):
                return '0'*64 if Path(path).as_posix().endswith(name) else original(path)
            with patch.object(base,'file_sha',side_effect=changed):
                with self.assertRaises(ValueError):v8.verify(root/'frozen')
            f=json.loads((root/'frozen/freeze.json').read_text());del f['sources_sha256'][name]
            base.write(root/'frozen/freeze.json',f);(root/'frozen/freeze.sha256').write_text(original(root/'frozen/freeze.json'))
            with self.assertRaises(ValueError):v8.verify(root/'frozen')

    def test_actual_sdk_three_arm_offline_gates_and_tamper_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v8.prepare(root/'frozen')
            summary=v8.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=v8.audit_output(root/'frozen',root/'mock',mock=True)
            self.assertEqual(summary['samples_completed'],12);self.assertEqual(a['passed'],12)
            self.assertTrue(a['offline_gate_passed']);self.assertEqual(a['host_archive_hits_delivered'],8)
            self.assertEqual(a['mock_B1_B2_equal_body_pairs'],36)
            self.assertGreaterEqual(a['response_text_reduction'],.5)
            self.assertGreaterEqual(a['aggregate_wire_reduction'],.15)
            self.assertTrue(all(not r['recovery_used'] for r in summary['samples']))
            trial=root/'mock'/summary['samples'][2]['sample_id']
            path=trial/'host_reads.json';events=json.loads(path.read_text())
            hit=next(e for e in events if e['origin']=='archive');hit['payload_sha256']='0'*64;path.write_text(json.dumps(events))
            with self.assertRaises(ValueError):v8.audit_output(root/'frozen',root/'mock',mock=True)

    def test_request_errors_and_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            w=v8.MinimalWorkspace(v8.task_set()[0],Path(directory)/'workspace');w.arm='B2'
            self.assertEqual(w.read_records('north',['N-00'],'current',call_id='before')['kind'],'error')
            for e in w.entities:w.read_document(e,'current',call_id=e)
            w.close_research('summary');w.namespace='task';w.snapshot_provider=lambda:dict(namespace='wrong')
            p=w.read_records('north',['N-00'],'current',call_id='selected')
            self.assertEqual(p['kind'],'registry_record_evidence');self.assertEqual(w.host_reads[-1]['origin'],'source')
            self.assertEqual(w.read_records('north',['N-00','N-00'],'current',call_id='bad')['kind'],'error')
            (w.out/'docs/north.json').write_text('changed')
            with self.assertRaises(ValueError):w.read_records('north',['N-00'],'current',call_id='changed')

    def test_caps_claim_and_unknown_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v8.prepare(root/'frozen')
            with patch.object(v8,'OPERATION_CLAIM',root/'operation.claim'):
                v8.reserve_operation(root/'frozen',root/'paid')
                with self.assertRaises(FileExistsError):v8.reserve_operation(root/'frozen',root/'again')
            def fail(request):raise httpx.ReadError('controlled')
            with patch.object(v8,'ControlledMock',return_value=fail):v8.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=json.loads((root/'mock/audit.json').read_text());self.assertEqual(a['unknown_usage_requests'],1)
            self.assertEqual(a['unattempted_samples'],11)

    def test_presend_prepared_not_delivered(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v8.prepare(root/'frozen');original=base.StudyTransport.handle_async_request
            async def reject(self,request):
                scope=base.SCOPE.get()
                if '-B2-' in scope['sample_id'] and scope['sample_id'].endswith('/turn6'):raise base.RequestRejected('controlled presend')
                return await original(self,request)
            with patch.object(base.StudyTransport,'handle_async_request',reject):v8.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=json.loads((root/'mock/audit.json').read_text());self.assertEqual(a['host_archive_hits_prepared'],1)
            self.assertEqual(a['host_archive_hits_delivered'],0);self.assertEqual(len(a['unsent_boundaries']),1)


if __name__=='__main__':unittest.main()
