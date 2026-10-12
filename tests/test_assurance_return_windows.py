import copy,unittest
from ibkr_etl.portfolio_assurance import _composite_return_findings

class ReturnWindows(unittest.TestCase):
    def setUp(self):
        self.series=[{'date':d,'currency':'HKD','nav':v} for d,v in [('2025-01-20',100),('2025-01-21',99.99240928498396),('2026-10-08',121.98149004845745)]]
        self.comparison=[{'date':r['date'],'portfolio':r['nav']/self.series[1]['nav']*100} for r in self.series[1:]]
        self.risk={'reporting_currency':'HKD','metrics':{'total_return':self.series[-1]['nav']/100-1},'analytics_evidence':{'version':'alpha-world-ms-factsheet-hkd-hibor-v3','start':'2025-01-20','end':'2026-10-08','nav_observations':3,'reporting_currency':'HKD'}}
    def findings(self):return _composite_return_findings('alpha-world-ms',self.series,self.comparison,self.risk)
    def test_different_actual_start_windows_pass(self):self.assertEqual(self.findings(),[])
    def test_t_same_window_passes(self):
        self.risk['analytics_evidence']['version']='alpha-world-t-hkd-hibor-v1'
        comparison=[{'date':r['date'],'portfolio':r['nav']} for r in self.series]
        self.assertEqual(_composite_return_findings('alpha-world-t',self.series,comparison,self.risk),[])
    def test_wrong_total_not_tolerated(self):
        self.risk['metrics']['total_return']=self.comparison[-1]['portfolio']/100-1
        self.assertIn('metrics.total_return',[r['check_id'] for r in self.findings()])
    def test_tampered_intermediate_or_final_comparison_rejected(self):
        for index in (0,1):
            with self.subTest(index=index):
                self.comparison[index]['portfolio']+=0.001
                self.assertIn('benchmark.portfolio_reconciliation',[r['check_id'] for r in self.findings()])
                self.comparison[index]['portfolio']-=0.001
    def test_missing_comparison_date_rejected(self):
        self.comparison[0]['date']='2025-01-22'
        self.assertIn('benchmark.portfolio_reconciliation',[r['check_id'] for r in self.findings()])
    def test_missing_or_stale_evidence_rejected(self):
        for key,value in [('version','old'),('start','2025-01-21'),('end','2026-10-07'),('nav_observations',2),('reporting_currency','USD')]:
            with self.subTest(key=key):
                prior=copy.deepcopy(self.risk);self.risk['analytics_evidence'][key]=value
                self.assertEqual(self.findings()[0]['check_id'],'metrics.return_period');self.risk=prior
    def test_invalid_nav_and_currency_rejected(self):
        for key,value in [('nav',float('nan')),('nav',0),('currency','USD')]:
            with self.subTest(key=key):
                prior=copy.deepcopy(self.series);self.series[1][key]=value
                self.assertEqual(self.findings()[0]['check_id'],'metrics.return_period');self.series=prior
    def test_duplicate_and_reordered_nav_rejected(self):
        self.series[1]['date']=self.series[0]['date']
        self.assertEqual(self.findings()[0]['check_id'],'metrics.return_period')
    def test_unknown_subject_rejected(self):
        self.assertEqual(_composite_return_findings('other',self.series,self.comparison,self.risk)[0]['check_id'],'metrics.return_period')
    def test_nonfinite_report_or_comparison_rejected(self):
        self.risk['metrics']['total_return']=float('nan');self.comparison[-1]['portfolio']=float('inf')
        self.assertEqual({r['check_id'] for r in self.findings()},{'metrics.total_return','benchmark.portfolio_reconciliation'})

if __name__=='__main__':unittest.main()
