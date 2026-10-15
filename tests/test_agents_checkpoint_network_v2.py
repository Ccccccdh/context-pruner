import unittest
from pathlib import Path
import tempfile,json
from unittest.mock import patch
from experiments.runners.run_agents_checkpoint_network_v2 import remaining_budget,prepare_successor


class NetworkBudgetTest(unittest.TestCase):
    def test_failed_attempt_is_subtracted_from_original_limits(self):
        freeze={"max_requests":72,"cap_usd":1.}
        previous={"samples_completed":0,"stopped":{"reason":"APIConnectionError"},
                  "ledger":[{"status":"transport_error","reserved_peak_allmiss_usd":.0047604}]}
        b=remaining_budget(freeze,previous)
        self.assertEqual(71,b['max_requests'])
        self.assertAlmostEqual(.9952396,b['cap_usd'])

    def test_successful_or_billed_response_cannot_use_infrastructure_successor(self):
        for status in ('returned','http_error'):
            with self.assertRaises(ValueError):
                remaining_budget({"max_requests":72,"cap_usd":1.},{"samples_completed":0,
                    "stopped":{"reason":"APIConnectionError"},"ledger":[{"status":status,"reserved_peak_allmiss_usd":.01}]})

    def test_relative_predecessor_path_is_resolved_before_freezing(self):
        from experiments.runners.run_agents_checkpoint_natural_v2 import ROOT
        with tempfile.TemporaryDirectory(dir=ROOT/".tooling/agents-natural-v2-tests") as tmp:
            p=Path(tmp);previous=p/'summary.json'
            previous.write_text(json.dumps({'samples_completed':0,'stopped':{'reason':'APIConnectionError'},
                'ledger':[{'status':'transport_error','reserved_peak_allmiss_usd':.0047604}]}))
            config={'max_requests':72,'cap_usd':1.,'files_sha256':{},'sources_sha256':{},'experiment_id':'test'}
            with patch('experiments.runners.run_agents_checkpoint_network_v2.runner.verify_freeze',return_value=config):
                result=prepare_successor(p/'old',previous.relative_to(ROOT),p/'new')
            self.assertEqual(previous.relative_to(ROOT).as_posix(),result['predecessor_summary'])


if __name__=='__main__':unittest.main()
