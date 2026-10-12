import unittest
from unittest.mock import patch

from ibkr_etl.derived_portfolio import _flow_adjusted_nav
from ibkr_etl.monthly_artifacts import (
    MonthlyArtifactError,
    _normalize_nav,
    _trailing_twelve_month_return,
)


class FactsheetFlowAdjustedReturnTests(unittest.TestCase):
    def test_deposit_is_not_counted_as_return(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 150.0, "flow": 50.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 100.0)
        self.assertEqual(rows[-1]["nav"], 150.0)

    def test_modified_dietz_matches_website_formula(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 160.0, "flow": 50.0},
        ])
        # r = (160 - 100 - 50) / (100 + 25)
        self.assertAlmostEqual(rows[-1]["value"], 100.0 * (1 + 10.0 / 125.0))

    def test_withdrawal_is_not_counted_as_loss(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 200.0},
            {"date": "2026-01-05", "nav": 100.0, "flow": -100.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 100.0)

    def test_rows_without_flows_keep_the_old_nav_ratio(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0},
            {"date": "2026-01-05", "nav": 110.0},
            {"date": "2026-01-06", "nav": 99.0},
        ])
        self.assertAlmostEqual(rows[-1]["value"], 99.0)

    def test_first_row_flow_is_ignored(self):
        rows = _normalize_nav([
            {"date": "2026-01-02", "nav": 100.0, "flow": 100.0},
            {"date": "2026-01-05", "nav": 100.0},
        ])
        self.assertEqual(rows[0]["value"], 100.0)
        self.assertAlmostEqual(rows[-1]["value"], 100.0)

    def test_non_positive_denominator_is_rejected(self):
        with self.assertRaises(MonthlyArtifactError):
            _normalize_nav([
                {"date": "2026-01-02", "nav": 100.0},
                {"date": "2026-01-05", "nav": 1.0, "flow": -250.0},
            ])

    def test_trailing_twelve_months_ignores_deposits(self):
        rows = _normalize_nav([
            {"date": "2025-01-31", "nav": 100.0},
            {"date": "2025-06-30", "nav": 1100.0, "flow": 1000.0},
            {"date": "2026-01-31", "nav": 1210.0},
        ])
        self.assertAlmostEqual(_trailing_twelve_month_return(rows, "2026-01-31"), 0.10)


class CompositeSleeveFlowTests(unittest.TestCase):
    def adjusted(self, values, flows):
        with patch("ibkr_etl.provisional_benchmark._external_flows", return_value=flows) as loader:
            result = _flow_adjusted_nav(object(), "U1", values, subject_type="account")
        loader.assert_called_once()
        self.assertEqual(loader.call_args.kwargs, {"subject_type": "account"})
        return result

    def test_sleeve_without_flows_keeps_raw_nav_exactly(self):
        values = {"2026-01-02": 100.0, "2026-01-05": 103.3, "2026-01-06": 101.7}
        self.assertEqual(self.adjusted(values, {}), values)

    def test_deposit_does_not_inflate_sleeve(self):
        values = {"2026-01-02": 100.0, "2026-01-05": 150.0, "2026-01-06": 165.0}
        result = self.adjusted(values, {"2026-01-03": 50.0})
        self.assertAlmostEqual(result["2026-01-05"], 100.0)
        self.assertAlmostEqual(result["2026-01-06"], 110.0)


if __name__ == "__main__":
    unittest.main()
