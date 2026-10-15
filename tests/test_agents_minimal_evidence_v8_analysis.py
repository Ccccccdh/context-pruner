import copy
import unittest
from experiments.audits.analyze_agents_minimal_evidence_v8 import screen


def rows():
    return [dict(task=str(i),arm=arm,quality_passed=True,total_tokens=1000 if arm=='B0' else 800,
                 cost_low=1. if arm=='B0' else .8,cost_high=2. if arm=='B0' else 1.6)
            for i in range(4) for arm in ('B0','B1','B2')]


class ScreenTest(unittest.TestCase):
    def test_fixed_gate_and_incomplete(self):
        self.assertTrue(screen(rows(),True)['passed'])
        self.assertFalse(screen(rows(),False)['passed'])
        self.assertFalse(screen(rows()[:-1],False)['passed'])
        data=rows()
        for r in data:
            if r['arm']=='B1':r['total_tokens']=950
        self.assertFalse(screen(data,True)['passed'])

    def test_quality_and_both_prices_and_direction(self):
        for field,value in [('quality_passed',False),('cost_high',2.1)]:
            data=rows()
            for r in data:
                if r['arm']=='B1':r[field]=value
            self.assertFalse(screen(data,True)['passed'])
        data=rows()
        for r in data:
            if r['arm']=='B1' and r['task'] in {'0','1'}:r['cost_low']=1.01;r['cost_high']=2.02
            elif r['arm']=='B1':r['cost_low']=.1;r['cost_high']=.2
        self.assertFalse(screen(data,True)['passed'])


if __name__=='__main__':unittest.main()
