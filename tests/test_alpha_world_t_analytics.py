"""Synthetic, isolated qualification. No live Azure or provider data is modified."""
import copy
from datetime import date, timedelta
import hashlib
import json
import math
import random
import sqlite3
import unittest
from unittest.mock import Mock, patch

import numpy as np

from ibkr_etl.alpha_world_t_analytics import (
    analyze, simulate, load_risk_snapshot, methodology_identity, VERSION, MARKETS,
)
from ibkr_etl.hibor import period_average, expected_fixing_dates
from ibkr_etl.market_calendar import CALENDARS, CalendarCoverageError
from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256


def snapshot(end=date(2025, 1, 22)):
    days = []
    cursor = date(2025, 1, 20)
    while cursor <= end:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    fixings = [{'rate_date': d.isoformat(), 'annual_rate': .02 + .001*i}
               for i, d in enumerate(expected_fixing_dates(days[0], days[-1]))]
    return {'subject_id': 'alpha-world-t', 'period_start': '2025-01-20',
        'period_end': end.isoformat(), 'data_as_of': days[-1].isoformat(),
        'reporting_currency': 'HKD', 'methodology_identity': methodology_identity(),
        'performance': {'series': [{'date': d.isoformat(), 'currency': 'HKD',
            'nav': 100*(1+.002*i)*(1+.005*math.sin(i))} for i, d in enumerate(days)]},
        'risk': {'risk_free': period_average(fixings, days[0], days[-1])}}


class TAnalyticsTests(unittest.TestCase):
    def test_unqualified_production_paths_cannot_use_legacy_treasury_or_activate(self):
        from ibkr_etl.risk_snapshots import load_risk_inputs, validate_risk_input_readiness, RiskInputsNotReady
        import run_self_service_derived_refresh as worker
        c = Mock()
        c.execute.return_value.fetchall.return_value=[]
        with self.assertRaises(RiskInputsNotReady):
            load_risk_inputs(c, tenant_id='tenant', subject_id='alpha-world-t', as_of='2025-01-22')
        c.execute.assert_called_once()
        self.assertIn('subject_calculation_definitions',c.execute.call_args.args[0])
        c.reset_mock()
        with self.assertRaises(RiskInputsNotReady):
            validate_risk_input_readiness({'subject_id': 'alpha-world-t', 'as_of': '2025-01-22'})
        with (patch.object(worker, 'refresh_and_materialize') as comparison,
              patch.object(worker, 'refresh_composite_crisis_replay') as crisis,
              patch.object(worker, 'build_risk_snapshot') as cache):
            with self.assertRaisesRegex(ValueError, 'exactly one approved current HKD contract'):
                worker._refresh_analytics_if_approved(c, {'subject_id': 'alpha-world-t'}, tenant_id='tenant')
        c.execute.assert_called_once()
        self.assertIn('subject_calculation_definitions',c.execute.call_args.args[0])
        comparison.assert_not_called()
        crisis.assert_not_called()
        cache.assert_not_called()

    def test_independent_numpy_metrics_period_average_and_immutability(self):
        s = snapshot()
        before = copy.deepcopy(s)
        metrics, daily, evidence = analyze(s)
        nav = np.array([r['nav'] for r in s['performance']['series']])
        returns = nav[1:]/nav[:-1]-1
        sigma = np.std(returns, ddof=1)
        rf = np.mean([.020, .021, .022])
        threshold = np.quantile(returns, .05)
        expected = {
            'total_return': nav[-1]/nav[0]-1,
            'annualized_return': (nav[-1]/nav[0])**(365.25/2)-1,
            'annualized_volatility': sigma*math.sqrt(252),
            'sharpe_ratio': (np.mean(returns)-((1+rf)**(1/252)-1))/sigma*math.sqrt(252),
            'maximum_drawdown': np.min(nav/np.maximum.accumulate(nav)-1),
            'var_95_daily': max(0., -threshold),
            'cvar_95_daily': max(0., -np.mean(returns[returns <= threshold])),
            'win_rate': np.mean(returns > 0), 'risk_free_annual_rate': rf,
        }
        for key, value in expected.items():
            self.assertAlmostEqual(metrics[key], value, places=12, msg=key)
        self.assertEqual(tuple(returns), daily)
        self.assertEqual(s, before)
        self.assertEqual(evidence['session_calendars'], list(MARKETS))
        self.assertFalse(evidence['source_data_changed'])
        self.assertEqual(evidence['version'], VERSION)

    def test_identity_currency_inception_and_hibor_evidence_fail_closed(self):
        mutations = (
            lambda s: s.update(subject_id='alpha-world-ms'),
            lambda s: s.update(reporting_currency='USD'),
            lambda s: s.update(period_start='2025-01-21'),
            lambda s: s['methodology_identity'].update(contract_sha256='0'*64),
            lambda s: s['performance']['series'][1].update(currency='USD'),
            lambda s: s['performance']['series'][1].update(nav=float('nan')),
            lambda s: s['performance']['series'][1].update(nav=0),
            lambda s: s['risk']['risk_free'].update(series_id='UST-3M'),
            lambda s: s['risk']['risk_free'].update(annual_rate=.022),
            lambda s: s['risk']['risk_free'].update(averaging_basis='reporting-month'),
            lambda s: s['risk']['risk_free'].update(rate_date='2025-01-21'),
            lambda s: s['risk']['risk_free'].update(quality_status='fail'),
            lambda s: s['risk']['risk_free']['observations'].pop(1),
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                s = snapshot()
                mutation(s)
                with self.assertRaises(ValueError):
                    analyze(s)

    def test_all_source_markets_guard_daily_risk_without_rewriting_growth(self):
        s = snapshot(date(2025, 4, 22))
        # Good Friday closes US/HK, but Taiwan/Shanghai are still source-market
        # sessions. Missing that NAV must not become a one-session return.
        missing = date(2025, 4, 18)
        self.assertFalse(CALENDARS['XNYS'].is_session(missing))
        self.assertFalse(CALENDARS['XHKG'].is_session(missing))
        self.assertTrue(CALENDARS['XTAI'].is_session(missing))
        self.assertTrue(CALENDARS['XSHG'].is_session(missing))
        s['performance']['series'] = [r for r in s['performance']['series'] if r['date'] != missing.isoformat()]
        metrics, _, evidence = analyze(s)
        self.assertEqual(evidence['excluded_intervals'], [{
            'from': '2025-04-17', 'to': '2025-04-21', 'missing_sessions': ['2025-04-18']}])
        nav = s['performance']['series']
        self.assertEqual(metrics['total_return'], nav[-1]['nav']/nav[0]['nav']-1)

    def test_unqualified_calendar_year_fails_even_without_gaps(self):
        s = snapshot()
        s['period_end'] = s['data_as_of'] = '2027-01-04'
        s['performance']['series'][-1]['date'] = '2027-01-04'
        with self.assertRaises(CalendarCoverageError):
            analyze(s)

    def test_own_seed_horizon_no_extra_interest_and_ms_identity_preserved(self):
        daily = (.002, -.001, .003)
        sample, mean = simulate(daily, '2025-01-31', paths=5)
        seed = int(hashlib.sha256(f'alpha-world-t|2025-01-31|{VERSION}'.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        expected = [math.prod(1+daily[rng.randrange(3)] for _ in range(252))-1 for _ in range(5)]
        self.assertEqual(sample, expected)
        self.assertAlmostEqual(mean, np.mean(expected))
        from ibkr_etl.alpha_world_ms_analytics import simulate as ms_simulate
        self.assertNotEqual(sample, ms_simulate(daily, '2025-01-31', paths=5)[0])
        for bad in ((float('nan'),), (-1.,), (float('inf'),)):
            with self.assertRaises(ValueError):
                simulate(bad, '2025-01-31', paths=1)

    def test_ms_golden_metrics_evidence_simulation_unchanged(self):
        from test_monthly_artifacts import _ms_snapshot
        from ibkr_etl.alpha_world_ms_analytics import analyze as ms_analyze, simulate as ms_simulate
        s = _ms_snapshot()
        m, d, e = ms_analyze(s)
        digest = hashlib.sha256(json.dumps([m, d, e, ms_simulate(d, s['period_end'])], sort_keys=True).encode()).hexdigest()
        self.assertEqual(digest, 'e87ccf842a34c96e984b4796edc3e9836007149ceb925e3c51ea5a8df26556d2')


class TStoredInputTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.addCleanup(self.c.close)
        self.c.executescript('''
            CREATE TABLE platform_subjects(tenant_id,subject_id,subject_type,base_currency,reporting_currency,history_start_date);
            CREATE TABLE derived_portfolios(tenant_id,portfolio_id,base_currency);
            CREATE TABLE subject_calculation_definitions(tenant_id,subject_id,configuration_json,configuration_sha256,approval_status,effective_to);
            CREATE TABLE subject_nav_series(tenant_id,subject_id,snapshot_date,currency,total_text);
            CREATE TABLE risk_free_rates(tenant_id,rate_date,series_id,currency,tenor,annual_rate,quality_status);
        ''')
        self.c.execute("INSERT INTO platform_subjects VALUES ('tenant','alpha-world-t','composite_portfolio','HKD','HKD','2025-01-20')")
        self.c.execute("INSERT INTO derived_portfolios VALUES ('tenant','alpha-world-t','HKD')")
        definition = approved_reference_definitions()['alpha-world-t']
        self.c.execute('INSERT INTO subject_calculation_definitions VALUES (?,?,?,?,?,NULL)',
            ('tenant','alpha-world-t',json.dumps(definition),configuration_sha256(definition),'approved'))
        s = snapshot()
        for row in s['performance']['series']:
            for currency, value in [('HKD', row['nav']), ('USD', 999)]:
                self.c.execute('INSERT INTO subject_nav_series VALUES (?,?,?,?,?)',
                    ('tenant','alpha-world-t',row['date'],currency,str(value)))
        for row in s['risk']['risk_free']['observations']:
            self.c.execute("INSERT INTO risk_free_rates VALUES ('tenant',?,'HKAB-HIBOR-1M','HKD','1-month',?,'pass')",
                (row['rate_date'],row['annual_rate']))
        self.c.execute("INSERT INTO subject_nav_series VALUES ('other','alpha-world-t','2025-01-21','HKD','12345')")
        self.c.execute("INSERT INTO risk_free_rates VALUES ('other','2025-01-21','HKAB-HIBOR-1M','HKD','1-month',.25,'pass')")
        self.c.execute("INSERT INTO subject_nav_series VALUES ('tenant','alpha-world-t','2025-01-23','USD','999')")
        self.c.commit()

    def test_sql_load_is_tenant_scoped_hkd_only_and_read_only(self):
        before = list(self.c.iterdump())
        with self.assertRaisesRegex(ValueError, 'explicit tenant'):
            load_risk_snapshot(self.c, '2025-01-23')
        s, metrics, evidence = load_risk_snapshot(self.c, '2025-01-23', tenant_id='tenant')
        self.assertEqual(s['data_as_of'], '2025-01-22')
        self.assertEqual(len(s['performance']['series']), 3)
        self.assertAlmostEqual(metrics['risk_free_annual_rate'], .021)
        self.assertEqual(s['methodology_identity'], methodology_identity())
        self.assertEqual(evidence['version'], VERSION)
        self.assertEqual(before, list(self.c.iterdump()))
        with self.assertRaises(ValueError):
            load_risk_snapshot(self.c, '2025-01-23', tenant_id='other')

    def test_duplicate_approval_or_missing_fixing_cannot_fall_back(self):
        self.c.execute('INSERT INTO subject_calculation_definitions SELECT * FROM subject_calculation_definitions')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            load_risk_snapshot(self.c, '2025-01-23', tenant_id='tenant')
        self.c.execute('DELETE FROM subject_calculation_definitions WHERE rowid = (SELECT MAX(rowid) FROM subject_calculation_definitions)')
        self.c.execute("DELETE FROM risk_free_rates WHERE tenant_id='tenant' AND rate_date='2025-01-21'")
        # A fresh Treasury yield is not a HIBOR fixing.
        self.c.execute("INSERT INTO risk_free_rates VALUES ('tenant','2025-01-23','UST-3M','USD','3-month',.04,'pass')")
        with self.assertRaisesRegex(ValueError, 'Incomplete HIBOR'):
            load_risk_snapshot(self.c, '2025-01-23', tenant_id='tenant')

    def test_old_usd_or_filtered_contract_with_valid_hash_still_rejected(self):
        for mutate in (lambda d: d.update(base_currency='USD'),
                       lambda d: d['components'][0].update(component_id='hong-kong-china',component_type='portfolio')):
            definition = approved_reference_definitions()['alpha-world-t']
            mutate(definition)
            self.c.execute('UPDATE subject_calculation_definitions SET configuration_json=?,configuration_sha256=?',
                (json.dumps(definition),configuration_sha256(definition)))
            with self.assertRaisesRegex(ValueError, 'differs from the deployed'):
                load_risk_snapshot(self.c, '2025-01-23', tenant_id='tenant')


if __name__ == '__main__':
    unittest.main()
