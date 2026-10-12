import copy
import unittest
from audit_factsheet_history_20261011 import audit


class AuditTests(unittest.TestCase):
    def fixture(self):
        return {'subject_id':'U16088571','performance':{'series':[{'date':'2025-01-20','nav':100000},{'date':'2025-01-31','nav':99820}]},
                'performance_comparison':{'series':[{'date':'2025-01-21'}]},
                'month_end_performance':[{'date':'2025-01-31','portfolio':99.82}]}
    def test_full_history_passes(self): self.assertTrue(audit(self.fixture())['passed'])
    def test_benchmark_rebase_detected(self):
        p=self.fixture();p['month_end_performance'][0]['portfolio']=99.2
        with self.assertRaisesRegex(ValueError,'differs from full NAV'): audit(p)
    def test_benchmark_holiday_endpoint_detected(self):
        p=self.fixture();p['month_end_performance'][0]['date']='2025-01-30'
        with self.assertRaisesRegex(ValueError,'differs from full NAV'): audit(p)
    def test_missing_month_detected(self):
        p=self.fixture();p['month_end_performance']=[]
        with self.assertRaisesRegex(ValueError,'omits portfolio months'): audit(p)
    def test_payload_unchanged(self):
        p=self.fixture();before=copy.deepcopy(p);audit(p);self.assertEqual(p,before)
    def test_nonfinite_nav_rejected(self):
        p=self.fixture();p['performance']['series'][0]['nav']=float('nan')
        with self.assertRaises(ValueError): audit(p)
    def test_composites_not_reinterpreted(self):
        p=self.fixture();p['subject_id']='alpha-world-t'
        with self.assertRaises(ValueError): audit(p)


if __name__=='__main__': unittest.main()
