import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.runners.agents_lifecycle_tasks_v3 import CANDIDATES, TaskWorkspace, expected_rows, admit


class TaskContractTest(unittest.TestCase):
    def test_three_candidates_have_independent_discriminating_controls(self):
        self.assertEqual(len(CANDIDATES),3)
        for t in CANDIDATES:
            c=admit(t)
            self.assertTrue(c['passed'])
            self.assertTrue(c['reference_passed'])
            self.assertFalse(c['empty_passed'])
            self.assertFalse(c['old_version_passed'])

    def test_admission_runs_real_evaluator_instead_of_self_comparison(self):
        with patch.object(TaskWorkspace,'evaluate',return_value={'passed':False}) as evaluate:
            self.assertFalse(admit(CANDIDATES[0])['passed'])
            self.assertGreaterEqual(evaluate.call_count,3)

    def test_stages_versions_reread_and_public_checker(self):
        with tempfile.TemporaryDirectory() as d:
            w=TaskWorkspace(CANDIDATES[0],Path(d))
            self.assertEqual(w.close_research('plan')['kind'],'error')
            old=w.read_policy('historical');current=w.read_policy('current')
            self.assertNotEqual(old['source_version'],current['source_version'])
            self.assertEqual(w.close_research('retain key rules')['kind'],'stage_complete')
            self.assertEqual(w.work_order()['kind'],'work_order')
            w.read_policy('current');self.assertEqual(w.rereads,1)
            w.write_artifact(json.dumps({'rows':expected_rows(w.task)}))
            p=w.check_artifact()
            self.assertEqual(p['kind'],'missing_evidence')
            self.assertNotIn('rows',p)

    def test_reference_and_wrong_version_independent_validation(self):
        with tempfile.TemporaryDirectory() as d:
            w=TaskWorkspace(CANDIDATES[1],Path(d));w.read_policy('current');w.close_research('plan')
            self.assertFalse(w.evaluate()['passed'])
            artifact=w.reference();w.write_artifact(json.dumps(artifact))
            self.assertTrue(w.evaluate()['passed'])
            artifact['evidence']['source_version']=w.old_version
            w.write_artifact(json.dumps(artifact));self.assertFalse(w.evaluate()['passed'])

    def test_tool_payloads_never_emit_independent_expected_rows(self):
        with tempfile.TemporaryDirectory() as d:
            w=TaskWorkspace(CANDIDATES[2],Path(d));w.read_policy('current');w.close_research('plan')
            w.write_artifact(json.dumps({'rows':[]}))
            self.assertNotIn('expected',json.dumps(w.check_artifact()))
            self.assertEqual(w.report_missing('unrelated')['entity'],'unrelated')

if __name__=='__main__':unittest.main()
