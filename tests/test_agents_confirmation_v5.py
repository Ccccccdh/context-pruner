import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from experiments.runners import run_agents_confirmation_v5 as v5
from experiments.runners.run_agents_confirmation_v5 import HostPhaseWorkspace,prepare_development,verify,run_batch,configured
from experiments.runners.agents_registry_tasks_v4 import make_task
from experiments.runners import run_agents_confirmation_v4 as base


class PhaseFixTest(unittest.TestCase):
    def test_repeated_closure_preserves_summary_and_returns_complete(self):
        with tempfile.TemporaryDirectory() as d:
            w=HostPhaseWorkspace(make_task(101,'dev101'),Path(d))
            for e in w.entities:w.read_document(e,'current')
            w.close_research('first summary')
            p=w.close_research('replacement')
            self.assertEqual(p['kind'],'stage_complete');self.assertTrue(p['already_completed'])
            self.assertEqual((w.out/'research.txt').read_text(),'first summary')
            self.assertFalse(w.ordered)

    def test_scoped_candidate_configuration_and_shared_budget(self):
        original=base.StudyAdapter
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);pre=r/'prior.json';pre.write_text(json.dumps({'phase':'development','mock':False,'samples_completed':2,
                'stopped':{'reason':'RequestRejected'},'ledger':[{} for i in range(27)],'reserved_peak_allmiss_usd':.1954488}))
            bound={'summary':str(pre.resolve()),'summary_sha256':base.file_sha(pre),'requests':27,'reserved_usd':.1954488}
            with patch.object(v5,'predecessor',return_value=bound):f=prepare_development(r/'f',pre)
            self.assertEqual(f['max_requests'],261);self.assertAlmostEqual(f['cap_usd'],3.25-.1954488)
            self.assertIs(base.StudyAdapter,original)
            with patch.object(v5,'predecessor',return_value=bound):verify(r/'f')
            with configured():self.assertIsNot(base.StudyAdapter,original)
            self.assertIs(base.StudyAdapter,original)

    def test_unbound_prior_cannot_reset_budget(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'fake.json';p.write_text(json.dumps({'phase':'development','mock':False,
                'stopped':{'reason':'RequestRejected'},'ledger':[],'reserved_peak_allmiss_usd':0}))
            with self.assertRaises(ValueError):v5.predecessor(p)

    def test_operation_successor_is_single_use(self):
        with tempfile.TemporaryDirectory() as d:
            r=Path(d);f={'operation_claim':str(r/'claim'),'predecessor':{'summary':'prior','requests':27}}
            v5.reserve_successor(f,r/'f1',r/'out1')
            with self.assertRaises(FileExistsError):v5.reserve_successor(f,r/'f2',r/'out2')

if __name__=='__main__':unittest.main()
