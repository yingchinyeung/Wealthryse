from datetime import date, timedelta
import copy
import hashlib
import json
import math
import random
import unittest

import numpy as np

from ibkr_etl.alpha_world_ms_analytics import analyze, simulate, VERSION
from test_monthly_artifacts import _ms_snapshot


class MSAnalyticsTests(unittest.TestCase):
    def test_independent_numpy_reconciliation_and_input_immutability(self):
        snapshot = _ms_snapshot()
        before = copy.deepcopy(snapshot)
        metrics, daily, evidence = analyze(snapshot)
        nav = np.array([r['nav'] for r in snapshot['performance']['series']])
        returns = nav[1:] / nav[:-1] - 1
        sigma = np.std(returns, ddof=1)
        days = (date.fromisoformat(snapshot['data_as_of']) - date(2025, 1, 20)).days
        threshold = np.quantile(returns, .05)
        expected = {
            'total_return': nav[-1]/nav[0]-1,
            'annualized_return': (nav[-1]/nav[0])**(365.25/days)-1,
            'annualized_volatility': sigma*math.sqrt(252),
            'sharpe_ratio': (np.mean(returns)-((1+.0265)**(1/252)-1))/sigma*math.sqrt(252),
            'maximum_drawdown': np.min(nav/np.maximum.accumulate(nav)-1),
            'var_95_daily': max(0., -threshold),
            'cvar_95_daily': max(0., -np.mean(returns[returns <= threshold])),
            'win_rate': np.mean(returns > 0),
        }
        for key, value in expected.items():
            self.assertAlmostEqual(metrics[key], value, places=12, msg=key)
        self.assertEqual(tuple(returns), daily)
        self.assertEqual(snapshot, before)
        self.assertFalse(evidence['source_data_changed'])

    def test_gap_excluded_without_changing_observed_growth(self):
        snapshot = _ms_snapshot()
        snapshot['performance']['series'] = [r for r in snapshot['performance']['series'] if r['date'] not in {'2025-08-27','2025-08-28'}]
        metrics, daily, evidence = analyze(snapshot)
        self.assertEqual(evidence['excluded_intervals'], [{'from':'2025-08-26', 'to':'2025-08-29', 'missing_sessions':['2025-08-27','2025-08-28']}])
        rows = snapshot['performance']['series']
        self.assertEqual(len(daily), len(rows)-2)
        self.assertEqual(metrics['total_return'], rows[-1]['nav']/rows[0]['nav']-1)

    def test_calendar_holiday_not_a_missing_session(self):
        snapshot = _ms_snapshot()
        snapshot['performance']['series'] = [r for r in snapshot['performance']['series'] if r['date'] != '2025-12-25']
        self.assertEqual(analyze(snapshot)[2]['excluded_intervals'], [])

    def test_fixed_252_horizon_independent_seed_replay(self):
        daily = (.002, -.001, .003)
        sample, mean = simulate(daily, '2025-01-31', paths=5)
        seed = int(hashlib.sha256(f'alpha-world-ms|2025-01-31|{VERSION}'.encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        replay = [math.prod(1+daily[rng.randrange(3)] for _ in range(252))-1 for _ in range(5)]
        self.assertEqual(sample, replay)
        self.assertEqual(sample, simulate(daily, '2025-01-31', paths=5)[0])
        self.assertAlmostEqual(mean, np.mean(sample))

    def test_source_value_fingerprint_not_just_counts(self):
        snapshot = _ms_snapshot()
        before = analyze(snapshot)[2]['source_value_sha256']
        snapshot['performance']['series'][3]['nav'] += .01
        self.assertNotEqual(before, analyze(snapshot)[2]['source_value_sha256'])

    def test_bad_identity_dates_and_values_fail_closed(self):
        for mutate in (
            lambda s: s.update(subject_id='alpha-world-t'),
            lambda s: s['performance']['series'][3].update(nav=float('nan')),
            lambda s: s['performance']['series'][3].update(nav=0),
            lambda s: s['performance']['series'][3].update(date='2025-01-20'),
            lambda s: s.update(period_start='2025-01-19'),
            lambda s: s.update(data_as_of='2026-01-29'),
            lambda s: s['risk']['risk_free'].update(rate_date='2026-02-02'),
        ):
            snapshot = _ms_snapshot()
            mutate(snapshot)
            with self.assertRaises(ValueError): analyze(snapshot)

    def test_zero_variance_has_no_invented_sharpe(self):
        snapshot = _ms_snapshot()
        for row in snapshot['performance']['series']: row['nav'] = 100.
        metrics, _, _ = analyze(snapshot)
        self.assertIsNone(metrics['sharpe_ratio'])
        self.assertEqual(metrics['annualized_volatility'], 0.)

    def test_short_history_explicitly_identified(self):
        snapshot = _ms_snapshot()
        snapshot['period_end'] = snapshot['data_as_of'] = '2025-01-31'
        snapshot['performance']['series'] = snapshot['performance']['series'][:10]
        snapshot['risk']['risk_free']['rate_date'] = '2025-01-31'
        from ibkr_etl.hibor import period_average
        snapshot['risk']['risk_free'] = period_average(snapshot['risk']['risk_free']['observations'],date(2025,1,20),date(2025,1,31))
        self.assertTrue(analyze(snapshot)[2]['short_history'])


if __name__ == '__main__': unittest.main()
