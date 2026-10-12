"""Standalone factsheet history must use full portfolio NAV, not benchmark dates."""
import copy
import importlib.util
import unittest
from pathlib import Path
from ibkr_etl.monthly_artifacts import factsheet_inputs, MonthlyArtifactError

# Load the adjacent synthetic fixture in both local and isolated native tests.
_spec = importlib.util.spec_from_file_location('_factsheet_history_fixture', Path(__file__).with_name('test_monthly_artifacts.py'))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
_snapshot = _fixture._snapshot


class FactsheetPortfolioHistoryTests(unittest.TestCase):
    def fixture(self):
        p = _snapshot()
        p['period_end'] = p['data_as_of'] = '2025-01-31'
        p['performance']['series'] = [
            {'date': '2025-01-20', 'nav': 100000},
            {'date': '2025-01-21', 'nav': 100625},
            {'date': '2025-01-31', 'nav': 99820},
        ]
        p['month_end_performance'] = [{'date': '2025-01-31', 'portfolio': 99820 / 100625 * 100}]
        return p

    def test_full_first_interval_not_benchmark_intersection(self):
        result = factsheet_inputs(self.fixture())
        self.assertAlmostEqual(result.history[-1]['normalized_nav'], 99.82)
        self.assertAlmostEqual(result.history[-1]['monthly_return'], -.0018)
        self.assertAlmostEqual(result.history[-1]['cumulative_return'], -.0018)

    def test_comparison_input_and_frozen_payload_unchanged(self):
        p = self.fixture(); before = copy.deepcopy(p)
        factsheet_inputs(p)
        self.assertEqual(p, before)

    def test_month_ends_use_last_actual_portfolio_observation(self):
        p = self.fixture()
        p['period_end'] = p['data_as_of'] = '2025-03-31'
        p['performance']['series'] += [{'date': '2025-02-28', 'nav': 101000}, {'date': '2025-03-31', 'nav': 102000}]
        rows = factsheet_inputs(p).history
        self.assertEqual([r['date'] for r in rows], ['2025-01-20', '2025-01-31', '2025-02-28', '2025-03-31'])
        self.assertAlmostEqual(rows[2]['monthly_return'], 101000 / 99820 - 1)
        self.assertAlmostEqual(rows[-1]['cumulative_return'], .02)

    def test_late_benchmark_end_does_not_shorten_portfolio_history(self):
        p = self.fixture()
        p['month_end_performance'][0]['date'] = '2025-01-30'
        self.assertEqual(factsheet_inputs(p).history[-1]['date'], '2025-01-31')

    def test_invalid_nav_does_not_fall_back_to_comparison(self):
        p = self.fixture(); p['performance']['series'][0]['nav'] = 0
        with self.assertRaises(MonthlyArtifactError): factsheet_inputs(p)


if __name__ == '__main__':
    unittest.main()
