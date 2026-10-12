import unittest
from ibkr_etl.fx_selection import latest_fx_quote, InvalidFxObservation
from ibkr_etl.report import FxConverter
from ibkr_etl.derived_portfolio import _FxRates, _CrossFxRates
from ibkr_etl.monthly_reporting_analytics import _FxHistory


class FxSelectionTests(unittest.TestCase):
    def rows(self, value="0"):
        return [dict(account_id="a", from_currency="HKD", to_currency="USD", rate_date=day, rate_text=rate)
                for day, rate in [("2026-09-28", ".125"), ("2026-09-29", value)]]

    def test_latest_invalid_never_reveals_prior_positive(self):
        for invalid in ("0", "-1", "NaN", "Infinity", "", None, "broken", "1e-9999"):
            with self.subTest(value=invalid), self.assertRaises(InvalidFxObservation):
                latest_fx_quote([("2026-09-28", ".125"), ("2026-09-29", invalid)], "2026-09-30")

    def test_future_invalid_does_not_change_historical_result(self):
        self.assertEqual(latest_fx_quote([("2026-09-28", ".125"), ("2026-09-29", "0")], "2026-09-28"), (.125, "2026-09-28"))

    def test_new_valid_observation_restores_selection(self):
        quotes = [("2026-09-28", ".125"), ("2026-09-29", "0"), ("2026-09-30", ".126")]
        self.assertEqual(latest_fx_quote(quotes, "2026-09-30"), (.126, "2026-09-30"))

    def test_missing_and_duplicate_observations(self):
        self.assertIsNone(latest_fx_quote([], "2026-09-30"))
        self.assertIsNone(latest_fx_quote([("2026-10-01", 1)], "2026-09-30"))
        with self.assertRaises(InvalidFxObservation):
            latest_fx_quote([("2026-09-30", 1), ("2026-09-30", 1)], "2026-09-30")

    def test_report_converter_preserves_invalid_barrier(self):
        converter = FxConverter(self.rows(), {"a": "USD"})
        self.assertEqual(converter.rate("a", "HKD", "2026-09-28"), .125)
        self.assertEqual(converter.rate("a", "USD", "2026-09-30"), 1.)
        with self.assertRaises(InvalidFxObservation):
            converter.rate("a", "HKD", "2026-09-30")
        with self.assertRaises(InvalidFxObservation):
            converter.rate("a", "HKD", None)

    def test_derived_converter_preserves_invalid_barrier(self):
        converter = _FxRates(self.rows(), "a", "USD")
        self.assertEqual(converter.rate("HKD", "2026-09-28"), .125)
        with self.assertRaises(InvalidFxObservation):
            converter.rate("HKD", "2026-09-30")

    def test_cross_account_converter_cannot_hide_invalid_preferred_quote(self):
        rows = self.rows() + [dict(account_id="b", from_currency="HKD", to_currency="USD", rate_date="2026-09-30", rate_text=".126")]
        converter = _CrossFxRates(rows)
        with self.assertRaises(InvalidFxObservation):
            converter.rate("HKD", "USD", "2026-09-30", preferred_accounts=["a"])

    def test_monthly_direct_invalid_cannot_fall_back_to_inverse(self):
        rows = self.rows() + [dict(account_id="a", from_currency="USD", to_currency="HKD", rate_date="2026-09-30", rate_text="8")]
        converter = _FxHistory(rows)
        with self.assertRaises(InvalidFxObservation):
            converter.rate("HKD", "USD", "2026-09-30")

    def test_valid_inverse_still_available_when_direct_absent(self):
        rows = [dict(account_id="a", from_currency="USD", to_currency="HKD", rate_date="2026-09-30", rate_text="8")]
        self.assertEqual(_FxHistory(rows).rate("HKD", "USD", "2026-09-30"), .125)

    def test_unrelated_invalid_currency_does_not_block_valid_conversion(self):
        rows = self.rows() + [dict(account_id="a", from_currency="EUR", to_currency="USD", rate_date="2026-09-30", rate_text="1.1")]
        self.assertEqual(FxConverter(rows, {"a": "USD"}).rate("a", "EUR", "2026-09-30"), 1.1)
