import copy,unittest
from test_legacy_monthly_reporting_20261011 import fixture as base
from reconcile_august_monthly_reporting_20261011 import correct
def fixture():
    p,f=base();p.update(subject_id='U16088606',period_end='2026-08-31')
    for row,day in zip(p['performance']['series'],('2026-07-31','2026-08-03','2026-08-31')):row['date']=day
    f['data'].update(period_start='2026-08-03',period_end='2026-08-31')
    p['monthly_reporting']=copy.deepcopy(f['data']);p['monthly_reporting']['daily_observations']=1
    f['data'].update(method_version='monthly-reporting-attribution-v2-full-nav-coverage',position_coverage={'nav_intervals':2,'matched_position_intervals':2,'missing_position_dates':[],'residual_only_intervals':[]})
    return p,f
class AugustRepairTests(unittest.TestCase):
    def test_narrow_pure_repair(self):
        p,f=fixture();old=copy.deepcopy(p);new=correct(p,f);self.assertEqual(p,old)
        self.assertEqual(new['performance'],p['performance']);self.assertEqual(new['risk'],p['risk']);self.assertEqual(new['monthly_reporting']['portfolio_monthly_return'],p['monthly_reporting']['portfolio_monthly_return'])
        self.assertEqual(new['monthly_reporting']['cash_activity'],p['monthly_reporting']['cash_activity'])
    def reject(self,change):
        p,f=fixture();change(p,f)
        with self.assertRaises(ValueError):correct(p,f)
    def test_other_subject(self):self.reject(lambda p,f:p.update(subject_id='U16088571'))
    def test_other_month(self):self.reject(lambda p,f:p.update(period_end='2026-09-30'))
    def test_not_stale(self):self.reject(lambda p,f:p['monthly_reporting'].update(daily_observations=2))
    def test_changed_return(self):self.reject(lambda p,f:f['data'].update(portfolio_monthly_return=.2))
    def test_changed_cash(self):self.reject(lambda p,f:f['data']['cash_activity'].update(other_internal_cash=5))
    def test_missing_position(self):self.reject(lambda p,f:f['data']['position_coverage'].update(missing_position_dates=['2026-08-03']))
    def test_nonfinite_contribution(self):self.reject(lambda p,f:f['data']['security_contributions'][0].update(contribution=float('nan')))
    def test_wrong_sector(self):self.reject(lambda p,f:f['data']['sector_contributions'][0].update(contribution=.07))
    def test_missing_evidence(self):self.reject(lambda p,f:f.update(citations=[]))
if __name__=='__main__':unittest.main()
