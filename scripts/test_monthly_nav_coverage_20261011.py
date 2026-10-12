import unittest
from test_legacy_monthly_reporting_20261011 import fixture
from audit_monthly_nav_coverage_20261011 import audit
class NavCoverageTests(unittest.TestCase):
    def payload(self):
        p,f=fixture();p['monthly_reporting']=f['data'];return p
    def test_exact_month(self):self.assertTrue(audit(self.payload())['passed'])
    def test_composite_same_independent_guard(self):
        p=self.payload();p['subject_id']='alpha-world-t';self.assertTrue(audit(p)['passed'])
    def reject(self,edit):
        p=self.payload();edit(p)
        with self.assertRaises(ValueError):audit(p)
    def test_missing_interval(self):self.reject(lambda p:p['monthly_reporting'].update(daily_observations=1))
    def test_wrong_return(self):self.reject(lambda p:p['monthly_reporting'].update(portfolio_monthly_return=.09))
    def test_wrong_residual(self):self.reject(lambda p:p['monthly_reporting'].update(residual_contribution=.03))
    def test_wrong_currency(self):self.reject(lambda p:p['performance']['series'][0].update(currency='HKD'))
    def test_nonfinite_nav(self):self.reject(lambda p:p['performance']['series'][0].update(nav=float('nan')))
    def test_duplicate_dates(self):self.reject(lambda p:p['performance']['series'].append(p['performance']['series'][-1]))
if __name__=='__main__':unittest.main()
