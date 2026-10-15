import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx

from experiments.runners import run_agents_archive_first_v7 as v7
from experiments.runners import run_agents_confirmation_v4 as base


class ArchiveFirstSDKTest(unittest.TestCase):
    def test_source_validation_counter_includes_final_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            w=v7.ArchiveFirstWorkspace(v7.task_set()[0],root/'workspace')
            w.verify_sources()
            w.evaluate()
            stored=json.loads((root/'host_read_metrics.json').read_text())
            self.assertEqual(w.validation_calls,2)
            self.assertEqual(stored['source_validation_calls'],w.validation_calls)

    def test_schema_same_and_actual_sdk_payload_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);f=v7.prepare(root/'frozen')
            self.assertEqual(f['samples'],12);self.assertEqual(f['cap_usd'],2.)
            w=v7.ArchiveFirstWorkspace(v7.task_set()[0],root/'schema')
            self.assertEqual(base.schema(v7.tools_for(w)),base.schema(v7.OLD_TOOLS(w)))
            summary=v7.run_batch(root/'frozen',root/'mock','offline',mock=True)
            self.assertEqual(summary['samples_completed'],12)
            a=v7.audit_output(root/'frozen',root/'mock',mock=True)
            self.assertTrue(a['complete']);self.assertEqual(a['requests'],112)
            self.assertEqual(a['host_archive_hits_prepared'],8)
            self.assertEqual(a['host_archive_hits_delivered'],8)
            self.assertEqual(a['mock_A1_A2_equal_body_pairs'],40)
            self.assertTrue(all(not r['recovery_used'] for r in summary['samples']))
            p=root/'mock'/summary['samples'][2]['sample_id']/'host_reads.json'
            events=json.loads(p.read_text());hit=next(e for e in events if e['origin']=='archive')
            next(iter(hit['proof']['groups'].values()))['hash']='0'*64;p.write_text(json.dumps(events))
            with self.assertRaises(ValueError):v7.audit_output(root/'frozen',root/'mock',mock=True)

    def test_caps_and_single_operation_cannot_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v7.prepare(root/'frozen')
            with patch.object(v7,'OPERATION_CLAIM',root/'operation.claim'):
                v7.reserve_operation(root/'frozen',root/'paid')
                with self.assertRaises(FileExistsError):v7.reserve_operation(root/'frozen',root/'again')
            f=json.loads((root/'frozen/freeze.json').read_text());f['cap_usd']=3
            base.write(root/'frozen/freeze.json',f);(root/'frozen/freeze.sha256').write_text(base.file_sha(root/'frozen/freeze.json'))
            with self.assertRaises(ValueError):v7.verify(root/'frozen')

    def test_transport_failure_retains_denominator_and_unknown_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v7.prepare(root/'frozen')
            def fail(request):raise httpx.ReadError('controlled failure')
            with patch.object(v7,'ControlledMock',return_value=fail):v7.run_batch(root/'frozen',root/'mock','offline',mock=True)
            audit=json.loads((root/'mock/audit.json').read_text())
            self.assertFalse(audit['complete']);self.assertEqual(audit['unknown_usage_requests'],1)
            self.assertEqual(audit['unattempted_samples'],11)

    def test_prepared_archive_hit_is_not_delivered_after_presend_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);v7.prepare(root/'frozen');original=base.StudyTransport.handle_async_request
            async def reject(self,request):
                scope=base.SCOPE.get()
                if '-A2-' in scope['sample_id'] and scope['sample_id'].endswith('/turn7'):
                    raise base.RequestRejected('controlled presend rejection')
                return await original(self,request)
            with patch.object(base.StudyTransport,'handle_async_request',reject):v7.run_batch(root/'frozen',root/'mock','offline',mock=True)
            a=json.loads((root/'mock/audit.json').read_text())
            self.assertFalse(a['complete']);self.assertEqual(a['host_archive_hits_prepared'],1)
            self.assertEqual(a['host_archive_hits_delivered'],0);self.assertEqual(len(a['unsent_boundaries']),1)


if __name__=='__main__':unittest.main()
