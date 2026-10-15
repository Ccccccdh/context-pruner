import unittest
from pathlib import Path
import tempfile,json
from unittest.mock import patch
from experiments.runners.run_agents_quality_confirmation_v5 import quality_cost_gate,prepare_formal,verify,development_binding,analyze
from experiments.runners.run_agents_confirmation_v4 import ROOT


class QualityConfirmationTest(unittest.TestCase):
    def test_original_recovery_gate_failure_can_be_valid_qc_scope(self):
        rows=[{'sample_id':t+'-'+a+f'-r{i}','task':t,'arm':a,'repeat':i,'status':'completed',
               'independent':{'passed':True},'recovery_used':False,'wrong_version_recovered':False,'all_tool_groups_paired':True}
              for i in (1,2,3) for t in ('dev101','dev103') for a in ('A0','A1','A2')]
        s={'mock':False,'phase':'development','samples_completed':18,'samples':rows,'stopped':None}
        g=quality_cost_gate(s)
        self.assertTrue(g['passed']);self.assertFalse(g['original_recovery_gate']['passed'])
        self.assertEqual(g['recovery_role'],'secondary')
        s['mock']=True;self.assertFalse(quality_cost_gate(s)['passed'])

    def test_pairing_violation_rejected_by_qc_gate(self):
        rows=[{'sample_id':t+'-'+a+f'-r{i}','task':t,'arm':a,'repeat':i,'status':'completed','all_tool_groups_paired':True,
            'independent':{'passed':True},'recovery_used':False,'wrong_version_recovered':False}
            for i in (1,2,3) for t in ('dev101','dev103') for a in ('A0','A1','A2')]
        rows[0]['all_tool_groups_paired']=False
        self.assertFalse(quality_cost_gate({'mock':False,'phase':'development','samples_completed':18,'samples':rows,'stopped':None})['passed'])

    def test_real_ledger_cannot_authorize_inconsistent_sample_accounting(self):
        root=ROOT/'runs/agents-confirmation-v5-20261014';p=root/'development-paid/summary.json'
        if not p.exists():self.skipTest('requires preserved development evidence')
        original=Path.read_text;data=json.loads(p.read_text(encoding='utf-8'))
        data['samples'][0].update(http_requests=0,prompt_tokens=0,cost_offpeak_usd=0)
        def read(path,*args,**kw):return json.dumps(data) if path.resolve()==p.resolve() else original(path,*args,**kw)
        with patch.object(Path,'read_text',read):
            with self.assertRaises(ValueError):development_binding(root/'development-frozen02',p)

    def test_incomplete_analysis_keeps_planned_denominators(self):
        s={'phase':'formal','samples_planned':45,'samples_completed':0,'stopped':{'reason':'RequestRejected'},
            'samples':[{'arm':'A0','task':'confirm1009','repeat':1,'status':'infrastructure_error','independent':{'passed':False}}]}
        a=analyze(s)
        self.assertTrue(a['study_incomplete'])
        self.assertEqual(a['arms']['A0']['planned_n'],15);self.assertEqual(a['arms']['A0']['observed_n'],1)
        self.assertEqual(a['arms']['A1']['unattempted_n'],15)
        self.assertIsNone(a['arms']['A0']['quality_rate'])

    def test_failed_or_duplicate_development_matrix_rejected(self):
        self.assertFalse(quality_cost_gate({'mock':False,'phase':'development','samples_completed':0,'samples':[],'stopped':None})['passed'])

if __name__=='__main__':unittest.main()
