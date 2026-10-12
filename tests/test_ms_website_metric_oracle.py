import unittest

from audit_ms_website_benchmark_metrics import oracle


class IndependentMetricOracle(unittest.TestCase):
    def test_compounding_dates_capture_and_drawdown_follow_input_values(self):
        days=['2025-01-20','2025-01-21','2026-01-20']
        nav=dict(zip(days,[100,120,90])); benchmark=dict(zip(days,[100,110,95]))
        result=oracle(days,nav,benchmark)
        self.assertEqual(len(result),20)
        self.assertEqual(result['observations'],3)
        self.assertEqual(result['return_observations'],2)
        self.assertAlmostEqual(result['portfolio_total_return'],-.10)
        self.assertAlmostEqual(result['benchmark_total_return'],-.05)
        self.assertAlmostEqual(result['excess_total_return'],-.05)
        self.assertAlmostEqual(result['portfolio_max_drawdown'],-.25)
        self.assertAlmostEqual(result['benchmark_max_drawdown'],95/110-1)
        self.assertAlmostEqual(result['portfolio_annualized_return'],.9**(365.25/365)-1)
        self.assertEqual(result['relative_win_rate'],.5)
        self.assertEqual(result['period_start'],days[0])
        self.assertEqual(result['period_end'],days[-1])

    def test_no_negative_benchmark_intervals_has_no_downside_capture(self):
        days=['2026-09-21','2026-09-22','2026-09-23']
        result=oracle(days,dict(zip(days,[100,101,103])),dict(zip(days,[100,102,105])))
        self.assertIsNone(result['downside_capture_pct'])
        self.assertEqual(result['portfolio_max_drawdown'],0)
