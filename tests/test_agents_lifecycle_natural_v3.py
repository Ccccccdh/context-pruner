import json
from pathlib import Path
import tempfile
import unittest
import asyncio
import httpx
from unittest.mock import patch
from agents import Runner
from experiments.runners.agents_lifecycle_tasks_v3 import TaskWorkspace

from experiments.runners.agents_provider_audit_v2 import RequestRejected
from experiments.runners.run_agents_lifecycle_natural_v3 import prepare,verify_freeze,run_batch,SampleLedger,run_sdk_and_close


class NaturalLifecycleTest(unittest.TestCase):
    def test_frozen_mock_sdk_matrix_and_initial_parity(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);f=prepare(root/'freeze')
            self.assertEqual(len(f['plan']),6);self.assertEqual(len(f['admission']),3)
            s=run_batch(root/'freeze',root/'mock','offline',mock=True)
            self.assertIsNone(s['stopped']);self.assertEqual(s['samples_completed'],6)
            self.assertEqual(s['provider_requests'],0)
            self.assertTrue(s['initial_body_parity'])
            for x in s['samples']:
                self.assertTrue(x['all_tool_groups_paired'])
                if x['arm']=='A2':
                    self.assertTrue(x['recovery_original_delivered'])
                    self.assertTrue(x['independent']['passed'])
                    self.assertEqual(x['wrong_version_recovered'],False)
                    self.assertTrue(x['ttl_original_removed'])

    def test_freeze_drift_and_consumed_claim_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prepare(root/'f')
            p=root/'f/tasks.json';p.write_text('[]',encoding='utf-8')
            with self.assertRaises(ValueError):verify_freeze(root/'f')

    def test_failure_during_evaluation_still_saves_summary(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prepare(root/'f')
            with patch.object(TaskWorkspace,'evaluate',side_effect=ValueError('source drift')):
                s=run_batch(root/'f',root/'mock','offline',mock=True)
            self.assertIsNotNone(s['stopped'])
            self.assertTrue((root/'mock/summary.json').exists())

    def test_setup_source_drift_saved_as_batch_stop(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prepare(root/'f')
            from experiments.runners import run_agents_lifecycle_natural_v3 as m
            actual=m.verify_freeze;calls=[]
            def drifting(path):
                calls.append(1)
                if len(calls)>1:raise ValueError('source drift')
                return actual(path)
            with patch.object(m,'verify_freeze',side_effect=drifting):
                s=run_batch(root/'f',root/'mock','offline',mock=True)
            self.assertIsNotNone(s['stopped']);self.assertEqual(s['http_requests'],0)

    def test_per_sample_limit_before_global_cap(self):
        with tempfile.TemporaryDirectory() as d:
            l=SampleLedger(Path(d),max_requests=144,cap_usd=2,max_body_bytes=32000)
            for i in range(24):
                n=l.begin('one/turn'+str(i),'initial',{'a':1});l.finish(n,status='returned')
            with self.assertRaises(RequestRejected):l.begin('one/turn24','initial',{'a':1})
            n=l.begin('two/turn0','initial',{'a':1});l.finish(n,status='returned')

    def test_client_closed_on_same_loop_as_sync_sdk(self):
        from types import SimpleNamespace
        state={}
        class Adapter:
            def run_sync(self,*args,**kwargs):
                state['loop']=asyncio.get_event_loop()
                return SimpleNamespace(final_output='done')
        class Client:
            async def close(self):
                self_loop=asyncio.get_running_loop()
                if self_loop is not state['loop']:raise RuntimeError('cross-loop cleanup')
                state['closed']=True
        self.assertEqual(run_sdk_and_close(Adapter(),None,Client()).final_output,'done')
        self.assertTrue(state['closed']);self.assertTrue(state['loop'].is_closed())

    def test_archive_after_artifact_is_not_dependency_opportunity(self):
        from experiments.runners import run_agents_lifecycle_natural_v3 as m
        class LateArchive:
            def __init__(self,w):self.w=w;self.step=0
            def __call__(self,req):
                seq=[[('read_policy',{'revision':'current'}),('close_research',{'summary':'retained rules'})],
                     [('work_order',{}),('write_artifact',{'content':json.dumps(self.w.reference())})],None]
                i=self.step;self.step+=1;calls=seq[i]
                message={'role':'assistant','content':'{"done":true}'}
                if calls is not None:
                    message={'role':'assistant','content':None,'tool_calls':[{'id':f'c-{i}-{j}','type':'function','function':{'name':n,'arguments':json.dumps(a)}} for j,(n,a) in enumerate(calls)]}
                return httpx.Response(200,json={'id':f'm-{i}','object':'chat.completion','created':0,'model':m.MODEL,
                    'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if calls is not None else 'stop'}],
                    'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2,'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':1}})
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);prepare(root/'f')
            with patch.object(m,'ScriptedMock',LateArchive):s=run_batch(root/'f',root/'mock','offline',mock=True)
            self.assertIsNone(s['stopped'])
            self.assertTrue(all(not x['opportunity_formed'] for x in s['samples']))

    def test_failed_early_write_does_not_hide_later_opportunity(self):
        from types import SimpleNamespace
        from experiments.runners.run_agents_lifecycle_natural_v3 import trial_report
        with tempfile.TemporaryDirectory() as d:
            from experiments.runners.agents_lifecycle_tasks_v3 import CANDIDATES
            w=TaskWorkspace(CANDIDATES[0],Path(d));w.write_artifact('{}');w.read_policy('current');w.close_research('plan');w.work_order();w.write_artifact(json.dumps(w.reference()))
            a=SimpleNamespace(groups={'g':{'sources':[{'source_version':w.version}],'hash':'hash'}},
                boundaries=[{'clock':4,'request_scope':'sample/turn4','tool_count_before_model':3,'visible':[],
                    'group_statuses':{'g':'archived'},'recovered_groups':[],'paired':True}],events=[],metrics={},clock=4)
            ledger=SimpleNamespace(records=[{'sample_id':'sample/turn4','status':'returned'}])
            report=trial_report(w,a,'A2','sample',ledger,'completed',None)
            self.assertTrue(report['opportunity_formed'])

if __name__=='__main__':unittest.main()
