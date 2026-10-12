import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from ibkr_etl.alpha_world_ms_presentation import build_model
from ibkr_etl.alpha_world_t_presentation import extended_metrics

class MsPresentationModelTests(unittest.TestCase):
    def setUp(self):
        self.metrics={'total_return':.1,'annualized_return':.1,'annualized_volatility':.2,
            'maximum_drawdown':-.1,'cvar_95_daily':.03,'var_95_daily':.02,
            'risk_free_annual_rate':.025,'sharpe_ratio':.4,'win_rate':.5}
        self.evidence={'start':'2025-01-20','end':'2025-01-31','source_value_sha256':'source','excluded_intervals':[]}
        self.payload={'subject_id':'alpha-world-ms','reporting_currency':'HKD','publication_allowed':False,
            'quality_status':'warn','risk':{'metrics':self.metrics.copy()},
            'performance':{'series':[{'nav':100}]},'month_end_performance':[{'date':'2025-01-31','portfolio':110}],
            'monthly_reporting':{'available':True,'portfolio_monthly_return':.1,
                'security_contributions':[{'contribution':.1}],'residual_contribution':0},
            'holdings':{'holdings':[{'asset_category':'cash','currency':'HKD','market_value_text':'58'}]},
            'portfolio_summary':{'latest_nav':{'total_text':'58'}},
            'sector_allocation':{'gross_market_value':58,'allocation':[{'weight':1}]}}
    def build(self,payload,daily=(.1,.2)):
        with patch('ibkr_etl.alpha_world_ms_presentation.analyze',return_value=(self.metrics,list(daily),self.evidence)):
            return build_model(payload)
    def test_one_year_drawdowns_use_the_t_bootstrap_on_observed_returns(self):
        daily=[(-1)**i*.004+i*1e-5 for i in range(300)]
        risk=self.build(self.payload,daily)['risk'];expected=extended_metrics(daily)
        for key in ('expected_maximum_drawdown_1y','actual_maximum_drawdown_1y','tail_maximum_drawdown_1y',
                    'drawdown_simulation_paths','drawdown_horizon_sessions','drawdown_block_length','drawdown_tail_confidence'):
            self.assertEqual(risk[key],expected[key])
        self.assertEqual(risk['drawdown_simulation_paths'],5000)
        self.assertIsNotNone(risk['actual_maximum_drawdown_1y'])
        self.assertIsNone(risk['blue_mean_daily_return'])
    def test_short_history_or_gaps_leave_drawdowns_unavailable(self):
        risk=self.build(self.payload,[.01]*10)['risk']
        self.assertEqual(risk['drawdown_simulation_paths'],0)
        self.assertIsNone(risk['expected_maximum_drawdown_1y'])
        self.assertIsNone(risk['actual_maximum_drawdown_1y'])
        self.evidence['excluded_intervals']=[{'from':'2025-02-01','to':'2025-02-05'}]
        risk=self.build(self.payload,[.001]*300)['risk']
        self.assertIsNone(risk['expected_maximum_drawdown_1y'])
        self.assertIsNone(risk['drawdown_simulation_paths'])
    def test_source_is_unchanged_and_nulls_stay_unavailable(self):
        before=copy.deepcopy(self.payload);model=self.build(self.payload)
        self.assertEqual(self.payload,before)
        self.assertIsNone(model['primary']['benchmark_total_return'])
        self.assertIsNone(model['risk']['crisis_maximum_drawdown'])
        self.assertIsNone(model['risk']['actual_maximum_drawdown_1y'])
        self.assertEqual(model['risk']['daily_expected_tail_loss'],self.metrics['cvar_95_daily'])
        self.assertAlmostEqual(model['month_rows'][0]['portfolio_monthly_return'],.1)
        self.assertEqual(model['constant_cash'],58)
    def test_wrong_subject_or_currency_rejected(self):
        for field,value in [('subject_id','alpha-world-t'),('reporting_currency','USD')]:
            payload=copy.deepcopy(self.payload);payload[field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'qualified HKD'):self.build(payload)
    def test_inconsistent_metrics_rejected(self):
        self.payload['risk']['metrics']['total_return']=.5
        with self.assertRaisesRegex(ValueError,'metrics differ'):self.build(self.payload)
    def test_unsupported_benchmark_claim_rejected(self):
        self.payload['performance_comparison']={'metrics':{'beta':1}}
        with self.assertRaisesRegex(ValueError,'separate qualification'):self.build(self.payload)
    def test_unreconciled_contributions_rejected(self):
        self.payload['monthly_reporting']['residual_contribution']=.3
        with self.assertRaisesRegex(ValueError,'do not reconcile'):self.build(self.payload)
    def test_inconsistent_monthly_nav_rejected(self):
        self.payload['month_end_performance'][0]['portfolio']=115
        with self.assertRaisesRegex(ValueError,'NAV return differs'):self.build(self.payload)
    def test_publication_boundary_rejected(self):
        self.payload['publication_allowed']=True
        with self.assertRaisesRegex(ValueError,'private'):self.build(self.payload)
    def test_missing_cash_or_monthly_analytics_rejected(self):
        payload=copy.deepcopy(self.payload);payload['monthly_reporting']['available']=False
        with self.assertRaisesRegex(ValueError,'monthly reporting'):self.build(payload)
        payload=copy.deepcopy(self.payload);payload['holdings']['holdings']=[]
        with self.assertRaisesRegex(ValueError,'component values|cash sleeve'):self.build(payload)

if __name__=='__main__':unittest.main()
