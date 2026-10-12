import unittest

from ibkr_etl.presentation_portfolio_returns import portfolio_return_model


def _snapshot(series, as_of):
    return {"data_as_of": as_of, "performance": {"series": series}}


class PresentationPortfolioReturnTests(unittest.TestCase):
    def test_returns_use_full_portfolio_history_not_benchmark_dates(self):
        # 20 Jan 2025 was a US holiday, so the benchmark comparison starts on
        # 21 Jan. The portfolio return still starts at the first NAV.
        model = portfolio_return_model(_snapshot([
            {"date": "2025-01-20", "nav": 100.0},
            {"date": "2025-01-21", "nav": 99.0},
            {"date": "2025-01-31", "nav": 102.0},
            {"date": "2025-02-27", "nav": 105.0},
            {"date": "2025-02-28", "nav": 107.1},
        ], "2025-02-28"))
        self.assertEqual(model["period_start"], "2025-01-20")
        self.assertAlmostEqual(model["total_return"], 0.071)
        self.assertEqual([row["date"] for row in model["month_rows"]], ["2025-01-31", "2025-02-28"])
        self.assertAlmostEqual(model["month_rows"][0]["portfolio_monthly_return"], 0.02)
        self.assertAlmostEqual(model["month_rows"][1]["portfolio_monthly_return"], 107.1 / 102.0 - 1)

    def test_deposits_are_not_returns(self):
        model = portfolio_return_model(_snapshot([
            {"date": "2025-01-31", "nav": 100.0},
            {"date": "2025-02-28", "nav": 200.0, "flow": 100.0},
        ], "2025-02-28"))
        self.assertAlmostEqual(model["total_return"], 0.0)
        self.assertAlmostEqual(model["month_rows"][-1]["portfolio_monthly_return"], 0.0)

    def test_series_must_end_on_data_as_of(self):
        with self.assertRaises(ValueError):
            portfolio_return_model(_snapshot([
                {"date": "2025-01-30", "nav": 100.0},
                {"date": "2025-01-31", "nav": 101.0},
            ], "2025-02-03"))


if __name__ == "__main__":
    unittest.main()
