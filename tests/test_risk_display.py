import ast,json,os,shutil,subprocess,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(os.environ.get('RISK_DISPLAY_SOURCE',str(ROOT/'src/ibkr_etl/risk_display.py')))
SCRIPT=ast.literal_eval(next(n.value for n in ast.parse(SOURCE.read_text(encoding='utf-8')).body
    if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='RISK_DISPLAY_SCRIPT' for t in n.targets)))

class RiskDisplay(unittest.TestCase):
    def render(self,payload):
        node=os.environ.get('NODE_BINARY') or shutil.which('node')
        self.assertTrue(node,'Actual browser JavaScript must be executed')
        code=SCRIPT+'\nconst data='+json.dumps(payload)+'; const original=JSON.stringify(data);'+\
            "const result=riskDisplayNotes(data,v=>typeof v==='number'&&Number.isFinite(v)?(v*100).toFixed(2)+'%':'—');"+\
            "if(original!==JSON.stringify(data))throw Error('Display mutated financial payload');console.log(JSON.stringify(result));"
        run=subprocess.run([node,'-e',code],capture_output=True,text=True,encoding='utf-8',timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        return json.loads(run.stdout)
    def test_t_counts_come_from_validated_evidence(self):
        value=self.render({'metrics':{'return_observations':428},'analytics_evidence':{'nav_observations':431},
            'unavailable_capabilities':{'security_tail_attribution':'not-qualified'},'runtime_version':'t-hkd-v1'})
        self.assertIn('intervals: 428; NAV valuations: 431',value['risk'])
        self.assertIn('Calculation: t-hkd-v1',value['risk'])
        self.assertNotIn('USD',value['attribution']);self.assertFalse(value['attributionAvailable'])
    def test_missing_counts_not_zero(self):
        value=self.render({})
        self.assertIn('intervals: unavailable; NAV valuations: unavailable',value['risk'])
        self.assertNotIn('undefined',value['risk'])
        self.assertNotIn('0 deterministic',value['risk'])
    def test_zero_is_retained_when_explicit(self):
        self.assertIn('intervals: 0; NAV valuations: 1',self.render({'metrics':{'return_observations':0,'observations':1}})['risk'])
    def test_invalid_counts_not_used(self):
        value=self.render({'metrics':{'return_observations':True,'observations':-1},'analytics_evidence':{'nav_observations':'431'}})
        self.assertIn('intervals: unavailable; NAV valuations: unavailable',value['risk'])
    def test_physical_attribution_retains_usd_method(self):
        value=self.render({'expected_tail_loss_contributors':{'tail_observations':21,'residual_contribution':-0.002}})
        self.assertIn('USD positions on 21',value['attribution'])
        self.assertIn('-0.20%',value['attribution']);self.assertTrue(value['attributionAvailable'])
    def test_empty_attribution_does_not_claim_zero_tail_days(self):
        self.assertIn('unavailable',self.render({})['attribution'])
    def test_t_crisis_missing_provider_never_undefined(self):
        value=self.render({'crisis_replay':{'holdings_as_of':'2026-10-07','crisis_start':'2008-09-15','crisis_end':'2009-03-09',
            'direct_components':4,'proxy_components':2,'provider_prices_independently_verified':False,
            'limitations':'Constant HKD cash; no projected financing.'}})['risk']
        self.assertNotIn('undefined',value);self.assertNotIn('Cash weight:',value)
        self.assertIn('Provider: not recorded',value);self.assertIn('not been independently verified',value)
        self.assertIn('not for client distribution',value);self.assertIn('Constant HKD cash',value)
    def test_physical_crisis_provider_and_cash_retained(self):
        value=self.render({'crisis_replay':{'provider':'recorded-source','cash_weight':0.1}})['risk']
        self.assertIn('Provider: recorded-source',value);self.assertIn('Cash weight: 10.00%',value)
    def test_simulation_uses_actual_counts(self):
        value=self.render({'metrics':{'drawdown_simulation_paths':5000,'drawdown_horizon_sessions':252}})['risk']
        self.assertIn('5000 deterministic',value);self.assertIn('252 sessions',value)
    def test_unqualified_simulation_not_described_as_available(self):
        value=self.render({'metrics':{'drawdown_simulation_paths':5000,'drawdown_horizon_sessions':252},
            'unavailable_capabilities':{'one_year_drawdown':'not-qualified'}})['risk']
        self.assertNotIn('5000 deterministic',value);self.assertIn('simulated drawdown is unavailable',value)
    def test_hibor_period_basis_visible(self):
        value=self.render({'risk_free':{'provider':'HKAB','tenor':'1-month','rate_date':'2026-09-30',
            'period_start':'2025-01-20','period_end':'2026-09-30','averaging_basis':'published-fixings'}})['risk']
        self.assertIn('2025-01-20 through 2026-09-30',value);self.assertIn('basis: published-fixings',value)
    def test_missing_runtime_not_false_version(self):
        value=self.render({})['risk']
        self.assertIn('Calculation: not recorded',value);self.assertNotIn('benchmark-comparison-v1',value)

if __name__=='__main__':unittest.main()
