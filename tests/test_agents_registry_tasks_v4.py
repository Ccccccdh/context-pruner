import json
from pathlib import Path
import tempfile
import unittest
from experiments.runners.agents_registry_tasks_v4 import make_task,RegistryWorkspace,admission


class RegistryTaskTest(unittest.TestCase):
    def test_seeded_documents_and_controls(self):
        t=make_task(101,'dev101');self.assertEqual(t,make_task(101,'dev101'))
        self.assertEqual(sum(len(d['records']) for d in t['documents'].values()),48)
        self.assertEqual(len(t['queries']),6)
        with tempfile.TemporaryDirectory() as d:
            c=admission(t,Path(d));self.assertTrue(c['passed']);self.assertFalse(c['empty_passed']);self.assertFalse(c['historical_passed'])

    def test_stage_and_reread_source_immutability(self):
        with tempfile.TemporaryDirectory() as d:
            w=RegistryWorkspace(make_task(103,'dev103'),Path(d))
            self.assertEqual(w.close_research('plan')['kind'],'error')
            for e in w.entities:w.read_document(e,'current')
            self.assertEqual(w.close_research('plan')['kind'],'stage_complete')
            w.read_document(w.entities[0],'current');self.assertEqual(w.rereads,1)
            (w.out/'docs/north.json').write_text('changed',encoding='utf-8')
            with self.assertRaises(ValueError):w.verify_sources()

    def test_private_quality_and_strict_public_schema(self):
        with tempfile.TemporaryDirectory() as d:
            w=RegistryWorkspace(make_task(101,'dev101'),Path(d))
            for e in w.entities:w.read_document(e,'current')
            w.close_research('plan');w.work_order();w.write_artifact(json.dumps(w.reference()))
            self.assertTrue(w.evaluate()['passed'])
            a=w.reference();a['rows'][0]['value']=True;w.write_artifact(json.dumps(a))
            self.assertFalse(w.check_artifact()['schema_valid']);self.assertFalse(w.evaluate()['passed'])
            a=w.reference();a['rows'][0]['label']='wrong';w.write_artifact(json.dumps(a))
            self.assertTrue(w.check_artifact()['schema_valid']);self.assertFalse(w.evaluate()['passed'])
            self.assertNotIn('expected',json.dumps(w.check_artifact()))

if __name__=='__main__':unittest.main()
