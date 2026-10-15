import math
import unittest
from experiments.audits.agents_v8_confirmation_statistics import quality_lower,student_cdf,critical_t,confirm,sensitivity


def rows():
    return [dict(task=str(i),arm=a,quality_passed=True,total_tokens=1000 if a=='B0' else 750,cost_low=1. if a=='B0' else .7,cost_high=2. if a=='B0' else 1.4) for i in range(48) for a in ('B0','B1','B2')]


class ConfirmationStatisticsTest(unittest.TestCase):
    def test_quality_exact_and_joint(self):
        self.assertAlmostEqual(quality_lower(48,48),.025**(1/48),places=12)
        self.assertLess(quality_lower(47,48),.90)
        self.assertTrue(confirm(rows(),True)['joint_confirmed'])
        data=rows();data[1]['quality_passed']=False
        self.assertFalse(confirm(data,True)['joint_confirmed'])

    def test_incomplete_duplicates_and_boundaries(self):
        self.assertFalse(confirm(rows(),False)['joint_confirmed'])
        self.assertFalse(confirm(rows()[:-1],False)['joint_confirmed'])
        data=rows();data[-1]=data[0]
        with self.assertRaises(ValueError):confirm(data,True)
        data=rows()
        for r in data:
            if r['arm']=='B1':r['total_tokens']=950
        self.assertFalse(confirm(data,True)['joint_confirmed'])
        data=rows();data[1]['cost_low']=None
        self.assertFalse(confirm(data,True)['joint_confirmed'])

    def test_student_distribution_and_sensitivity(self):
        self.assertAlmostEqual(student_cdf(1,1),.75,places=10)
        self.assertAlmostEqual(student_cdf(1,2),.5+1/(2*math.sqrt(3)),places=10)
        t=critical_t(47);self.assertTrue(1.67<t<1.68)
        self.assertAlmostEqual(student_cdf(t,47),.95,places=10)
        result=sensitivity();self.assertGreater(result['normal_approx_cost_n']['sigma0.3_ratio0.85'],result['normal_approx_cost_n']['sigma0.3_ratio0.8'])

    def test_variable_effect_upper_exceeds_geomean(self):
        data=rows()
        for r in data:
            if r['arm']=='B1':r['cost_low']*=.7 if int(r['task'])%2 else 1.3
        c=confirm(data,True)['endpoints']['cost_low']
        self.assertGreater(c['ratio_upper'],c['geomean_ratio'])


if __name__=='__main__':unittest.main()
