import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from experiments.runners.run_agents_confirmation_v4 import prepare_development,prepare_formal,verify,run_batch,development_gate,analyze,sample_report,StudyLedger
from experiments.runners.agents_registry_tasks_v4 import make_task,RegistryWorkspace
from context_pruner.adapters.openai_agents_lifecycle_v1 import digest
from experiments.runners.agents_provider_audit_v2 import RequestRejected


class ConfirmationTest(unittest.TestCase):
    def test_sdk_zero_api_and_mock_not_formal_admission(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);f=prepare_development(r/'dev');self.assertEqual(len(f['plan']),18)
            s=run_batch(r/'dev',r/'mock','offline',mock=True)
            self.assertIsNone(s['stopped']);self.assertEqual(s['samples_completed'],18)
            self.assertFalse(development_gate(s)['passed'])
            a2=[x for x in s['samples'] if x['arm']=='A2']
            self.assertTrue(all(x['recovery_used'] and x['independent']['passed'] for x in a2))
            with self.assertRaises(ValueError):prepare_formal(r/'formal',r/'dev',r/'mock/summary.json')

    def test_gate_keeps_nontrigger_and_failure_denominators(self):
        rows=[{'task':t,'arm':a,'repeat':i,'status':'completed','independent':{'passed':True},'recovery_used':a=='A2',
              'wrong_version_recovered':False} for t in ('dev101','dev103') for a in ('A0','A1','A2') for i in (1,2,3)]
        s={'mock':False,'phase':'development','samples':rows,'samples_completed':18,'stopped':None,'ledger':[]}
        self.assertTrue(development_gate(s)['passed'])
        for x in rows:x['recovery_used']=False
        self.assertFalse(development_gate(s)['passed'])
        rows.pop();self.assertFalse(development_gate(s)['passed'])

    def test_source_drift_prevents_frozen_run(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);prepare_development(r/'dev');(r/'dev/tasks.json').write_text('[]')
            with self.assertRaises(ValueError):verify(r/'dev')

    def test_three_cluster_all_pass_does_not_prove_noninferiority(self):
        rows=[{'task':t,'arm':a,'repeat':i,'status':'completed','independent':{'passed':True},'recovery_used':False,
              'http_requests':1,'prompt_tokens':100,'completion_tokens':20,'cost_offpeak_usd':.001,'cost_peak_usd':.002}
              for t in ('f1','f2','f3') for a in ('A0','A1','A2') for i in (1,2,3,4,5)]
        a=analyze({'samples':rows,'phase':'formal','samples_completed':45,'ledger':[]})
        self.assertFalse(a['quality_noninferiority_confirmed']);self.assertLess(a['quality_difference_interval'][0],-.1)
        self.assertEqual(a['cost_task_signflip_p'],1.)

    def test_cost_test_uses_mean_log_paired_ratios_not_ratio_of_sums(self):
        rows=[]
        for t in ('f1','f2','f3'):
            for a in ('A0','A1','A2'):
                for i in range(1,6):
                    c=(1000 if i==5 else 1) if a!='A2' else (900 if i==5 else 10)
                    rows.append({'task':t,'arm':a,'repeat':i,'independent':{'passed':True},'recovery_used':False,'cost_offpeak_usd':c})
        result=analyze({'phase':'formal','samples_completed':45,'samples':rows})
        self.assertEqual(result['cost_task_signflip_p'],1.)

    def test_later_reread_correcting_artifact_not_attributed_to_restore(self):
        with tempfile.TemporaryDirectory() as d:
            w=RegistryWorkspace(make_task(101,'dev101'),Path(d))
            for e in w.entities:w.read_document(e,'current')
            w.close_research('plan');w.work_order();w.retrieve_evidence('north')
            items=[{'type':'function_call','call_id':'c','name':'read_document','arguments':'{}'},
                   {'type':'function_call_output','call_id':'c','output':'original'}]
            g={'items':items,'hash':digest(items),'sources':[{'entity':'north','source_version':w.versions['north']} ]}
            boundary={'clock':7,'request_scope':'s/turn7','tool_count_before_model':5,'visible':items,'paired':True,'recovered_groups':['g'],'group_statuses':{'g':'recovered'}}
            w.write_artifact('{"rows":[]}');w.read_document('north','current');w.write_artifact(json.dumps(w.reference()))
            a=SimpleNamespace(groups={'g':g},boundaries=[boundary],metrics={})
            ls=SimpleNamespace(records=[{'sample_id':'s/turn7','status':'returned','usage':{'prompt_tokens':100,'completion_tokens':10},'offpeak_usage_priced_usd':.1,'peak_usage_priced_usd':.2}])
            r=sample_report(w,a,{'sample_id':'s','task':'dev101','arm':'A2','repeat':1},ls,'completed','done')
            self.assertFalse(r['recovery_used'])

    def test_per_sample16_guard(self):
        with tempfile.TemporaryDirectory() as d:
            l=StudyLedger(Path(d),max_requests=288,cap_usd=3.25,max_body_bytes=24000)
            for i in range(16):n=l.begin('s/turn'+str(i),'initial',{'a':1});l.finish(n,status='returned')
            with self.assertRaises(RequestRejected):l.begin('s/turn16','initial',{'a':1})

if __name__=='__main__':unittest.main()
