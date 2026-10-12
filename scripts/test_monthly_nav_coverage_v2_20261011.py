import copy,unittest
from test_legacy_monthly_reporting_20261011 import fixture
from audit_monthly_nav_coverage_v2_20261011 import audit
class EndpointCoverageTests(unittest.TestCase):
    def payload(self):
        p,f=fixture();p.update(subject_id='alpha-world-t',reporting_currency='HKD',monthly_reporting=f['data'])
        for r in p['performance']['series']:r['currency']='HKD'
        p['monthly_reporting'].update(method_version='fixed-unit-component-delta-over-prior-month-nav-v1',reporting_currency='HKD',period_start='2025-01-31')
        del p['monthly_reporting']['daily_observations'];return p
    def test_documented_endpoint_contract_passes_without_mutation(self):
        p=self.payload();old=copy.deepcopy(p);self.assertTrue(audit(p)['passed']);self.assertEqual(p,old)
    def reject(self,edit):
        p=self.payload();edit(p)
        with self.assertRaises(ValueError):audit(p)
    def test_wrong_endpoint_rejected(self):self.reject(lambda p:p['monthly_reporting'].update(period_start='2025-02-03'))
    def test_wrong_return_rejected(self):self.reject(lambda p:p['monthly_reporting'].update(portfolio_monthly_return=.09))
    def test_wrong_contribution_rejected(self):self.reject(lambda p:p['monthly_reporting'].update(residual_contribution=.03))
    def test_wrong_method_rejected(self):self.reject(lambda p:p['monthly_reporting'].update(method_version='unrecognized'))
    def test_false_interval_count_rejected(self):self.reject(lambda p:p['monthly_reporting'].update(daily_observations=1))
    def test_physical_missing_interval_still_rejected(self):
        p,f=fixture();p['monthly_reporting']=f['data'];p['monthly_reporting']['daily_observations']=1
        with self.assertRaises(ValueError):audit(p)
if __name__=='__main__':unittest.main()
