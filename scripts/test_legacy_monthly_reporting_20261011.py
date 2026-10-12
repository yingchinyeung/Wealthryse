import copy,unittest
from reconcile_legacy_monthly_reporting_20261011 import enrich
def fixture():
    p={'subject_id':'U16088571','reporting_currency':'USD','period_end':'2025-02-28','monthly_reporting':None,'source_citations':{},'risk':{'unchanged':True},
       'performance':{'series':[{'date':d,'currency':'USD','nav':n} for d,n in [('2025-01-31',100),('2025-02-03',105),('2025-02-28',110)]]}}
    m={'available':True,'reporting_currency':'USD','method_version':'monthly-reporting-attribution-v1','fx_conversion':{'missing_currencies':[]},'daily_observations':2,
       'period_start':'2025-02-03','period_end':'2025-02-28','portfolio_monthly_return':.1,'equity_contribution':.08,'residual_contribution':.02,
       'security_contributions':[{'conid':'1','sector':'Tech','contribution':.08}], 'sector_contributions':[{'sector':'Tech','contribution':.08}],
       'cash_activity':{'total_dividends':3,'interest_received':1,'interest_paid':-1,'fees_and_withholding':-2,'other_internal_cash':4,'net_listed_cash_activity':5,'dividends_by_symbol':[{'symbol':'A','amount':3}]}}
    fresh={'quality_status':'pass','data':m,'citations':[{'table':t} for t in ('subject_nav_series','subject_position_series','subject_cash_activity_series','instrument_classifications')]}
    return p,fresh
class MonthlyEnrichmentTests(unittest.TestCase):
    def test_pure_and_narrow(self):
        p,f=fixture();old=copy.deepcopy((p,f));new=enrich(p,f)
        self.assertEqual((p,f),old);self.assertEqual(new['risk'],p['risk']);self.assertEqual(new['performance'],p['performance']);self.assertTrue(new['monthly_reporting']['available'])
    def reject(self,edit):
        p,f=fixture();edit(p,f)
        with self.assertRaises(ValueError):enrich(p,f)
    def test_existing_analytics(self):self.reject(lambda p,f:p.update(monthly_reporting={'available':True}))
    def test_other_subject(self):self.reject(lambda p,f:p.update(subject_id='alpha-world-t'))
    def test_wrong_currency(self):self.reject(lambda p,f:p.update(reporting_currency='HKD'))
    def test_failed_quality(self):self.reject(lambda p,f:f.update(quality_status='fail'))
    def test_missing_fx(self):self.reject(lambda p,f:f['data']['fx_conversion'].update(missing_currencies=['HKD']))
    def test_frozen_nav_difference(self):self.reject(lambda p,f:p['performance']['series'][-1].update(nav=111))
    def test_missing_daily_observation(self):self.reject(lambda p,f:f['data'].update(daily_observations=1))
    def test_wrong_start(self):self.reject(lambda p,f:f['data'].update(period_start='2025-02-04'))
    def test_wrong_end(self):self.reject(lambda p,f:f['data'].update(period_end='2025-02-27'))
    def test_equity_difference(self):self.reject(lambda p,f:f['data'].update(equity_contribution=.07))
    def test_residual_difference(self):self.reject(lambda p,f:f['data'].update(residual_contribution=.01))
    def test_sector_difference(self):self.reject(lambda p,f:f['data']['sector_contributions'][0].update(contribution=.07))
    def test_cash_difference(self):self.reject(lambda p,f:f['data']['cash_activity'].update(net_listed_cash_activity=4))
    def test_dividend_difference(self):self.reject(lambda p,f:f['data']['cash_activity']['dividends_by_symbol'][0].update(amount=2))
    def test_duplicate_identity(self):self.reject(lambda p,f:f['data']['security_contributions'].append(copy.deepcopy(f['data']['security_contributions'][0])))
    def test_missing_sources(self):self.reject(lambda p,f:f['citations'].pop())
    def test_nan(self):self.reject(lambda p,f:f['data'].update(residual_contribution=float('nan')))
    def test_boolean(self):self.reject(lambda p,f:f['data'].update(residual_contribution=True))
    def test_inception_partial_month(self):
        p,f=fixture();p['performance']['series']=p['performance']['series'][1:];f['data'].update(daily_observations=1,period_start='2025-02-28',portfolio_monthly_return=110/105-1,residual_contribution=110/105-1-.08)
        self.assertTrue(enrich(p,f)['monthly_reporting']['available'])
    def test_full_nav_method_with_explicit_residual_interval(self):
        p,f=fixture();f['data'].update(method_version='monthly-reporting-attribution-v2-full-nav-coverage',position_coverage={'missing_position_dates':['2025-01-31'],'residual_only_intervals':['2025-02-03'],'matched_position_intervals':1,'nav_intervals':2})
        self.assertTrue(enrich(p,f)['monthly_reporting']['available'])
    def test_full_nav_method_requires_coverage(self):self.reject(lambda p,f:f['data'].update(method_version='monthly-reporting-attribution-v2-full-nav-coverage'))
    def test_full_nav_method_rejects_inconsistent_coverage(self):self.reject(lambda p,f:f['data'].update(method_version='monthly-reporting-attribution-v2-full-nav-coverage',position_coverage={'missing_position_dates':[],'residual_only_intervals':[],'matched_position_intervals':1,'nav_intervals':2}))
if __name__=='__main__':unittest.main()
