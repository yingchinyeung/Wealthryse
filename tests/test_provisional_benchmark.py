from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock

from ibkr_etl.provisional_benchmark import (
    BENCHMARKS,
    convert_levels_to_reporting_currency,
    fetch_benchmark_yfinance,
    fetch_fx_to_reporting_yfinance,
    last_completed_us_close,
    _portfolio_values,
)


class _Index:
    def __init__(self, value: str) -> None:
        self._value = value

    def date(self):
        return datetime.fromisoformat(self._value).date()


class _Frame:
    def iterrows(self):
        yield _Index("2025-01-20"), {"Close": 100.0}
        yield _Index("2025-01-21"), {"Close": float("nan")}
        yield _Index("2025-01-22"), {"Close": 102.5}


class _Ticker:
    def __init__(self, symbol: str) -> None:
        if symbol not in {"^GSPC", "^IXIC"}:
            raise AssertionError(symbol)

    def history(self, **kwargs):
        if kwargs["interval"] != "1d" or kwargs["auto_adjust"] is not False:
            raise AssertionError(kwargs)
        return _Frame()


class _FxFrame:
    def iterrows(self):
        yield _Index("2025-01-20"), {"Close": 32.0}
        yield _Index("2025-01-21"), {"Close": 31.25}


class _FxTicker:
    def __init__(self, symbol: str) -> None:
        if symbol != "TWD=X":
            raise AssertionError(symbol)

    def history(self, **kwargs):
        return _FxFrame()


class ProvisionalBenchmarkTests(unittest.TestCase):
    def test_yfinance_rows_are_validated_and_date_bounded(self) -> None:
        rows = fetch_benchmark_yfinance(
            BENCHMARKS[0],
            datetime(2025, 1, 20).date(),
            datetime(2025, 1, 22).date(),
            _Ticker,
        )
        self.assertEqual(rows, [("2025-01-20", 100.0), ("2025-01-22", 102.5)])

    def test_primary_and_secondary_price_index_contracts_are_explicit(self) -> None:
        self.assertEqual(
            [
                (spec.benchmark_id, spec.symbol, spec.mapping_type)
                for spec in BENCHMARKS
            ],
            [
                ("GSPC", "^GSPC", "primary"),
                ("IXIC", "^IXIC", "secondary"),
            ],
        )
        self.assertTrue(all(spec.return_convention == "price" for spec in BENCHMARKS))

    def test_current_us_day_is_excluded_before_eod_cutoff(self) -> None:
        before_cutoff = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
        after_cutoff = datetime(2026, 9, 22, 23, 0, tzinfo=timezone.utc)
        self.assertEqual(last_completed_us_close(before_cutoff).isoformat(), "2026-09-21")
        self.assertEqual(last_completed_us_close(after_cutoff).isoformat(), "2026-09-22")

    def test_native_benchmark_uses_reciprocal_yahoo_fx_quote(self) -> None:
        rows, symbol = fetch_fx_to_reporting_yfinance(
            "TWD",
            "USD",
            datetime(2025, 1, 20).date(),
            datetime(2025, 1, 21).date(),
            _FxTicker,
        )
        self.assertEqual(symbol, "TWD=X")
        self.assertEqual(rows, [("2025-01-20", 1 / 32.0), ("2025-01-21", 1 / 31.25)])

    def test_conversion_uses_only_same_day_or_prior_fx(self) -> None:
        converted, audit = convert_levels_to_reporting_currency(
            [("2025-01-20", 1000.0), ("2025-01-22", 1100.0)],
            [("2025-01-20", 0.03125), ("2025-01-23", 0.032)],
        )
        self.assertEqual(converted, [("2025-01-20", 31.25), ("2025-01-22", 34.375)])
        self.assertEqual(audit[1]["fx_date"], "2025-01-20")
        self.assertEqual(audit[1]["fx_age_days"], 2)

    def test_conversion_fails_closed_when_only_future_fx_exists(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "non-forward-looking"):
            convert_levels_to_reporting_currency(
                [("2025-01-20", 1000.0)], [("2025-01-21", 0.03125)]
            )

    def test_usd_to_hkd_uses_direct_quote_not_reciprocal(self) -> None:
        class HKDTicker(_FxTicker):
            def __init__(self, symbol):
                self.assert_symbol = symbol
                if symbol != 'HKD=X': raise AssertionError(symbol)
        rows, symbol = fetch_fx_to_reporting_yfinance(
            'USD', 'HKD', datetime(2025,1,20).date(), datetime(2025,1,21).date(), HKDTicker)
        self.assertEqual(symbol, 'HKD=X')
        self.assertEqual(rows, [('2025-01-20',32.0),('2025-01-21',31.25)])

    def test_krw_uses_reciprocal_to_usd_and_direct_quote_from_usd(self) -> None:
        class KRWTicker(_FxTicker):
            def __init__(self, symbol):
                if symbol != 'KRW=X': raise AssertionError(symbol)
        start, end = datetime(2025,1,20).date(), datetime(2025,1,21).date()
        rows, symbol = fetch_fx_to_reporting_yfinance('KRW', 'USD', start, end, KRWTicker)
        self.assertEqual((symbol, rows), ('KRW=X', [('2025-01-20',1/32.0),('2025-01-21',1/31.25)]))
        rows, symbol = fetch_fx_to_reporting_yfinance('USD', 'KRW', start, end, KRWTicker)
        self.assertEqual((symbol, rows), ('KRW=X', [('2025-01-20',32.0),('2025-01-21',31.25)]))

    def test_fx_unsupported_cross_pair_and_identical_currency_fail_closed(self) -> None:
        for pair in (('CNY','HKD'), ('USD','EUR'), ('HKD','HKD'), ('KRW','HKD'), ('USD','TWD')):
            with self.subTest(pair=pair), self.assertRaises((ValueError,RuntimeError)):
                fetch_fx_to_reporting_yfinance(*pair, datetime(2025,1,20).date(),
                                               datetime(2025,1,21).date(), _FxTicker)

    def test_fx_rejects_unsorted_duplicate_invalid_and_stale_rates(self) -> None:
        for fx in ([('2025-01-21',7.8),('2025-01-20',7.8)],
                   [('2025-01-20',7.8),('2025-01-20',7.9)],
                   [('2025-01-20',float('inf'))], [('2025-01-20',0)],
                   [('2025-01-14',7.8)]):
            with self.subTest(fx=fx), self.assertRaises(RuntimeError):
                convert_levels_to_reporting_currency([('2025-01-20',100)],fx)

    def test_nav_reader_filters_currency_before_building_date_dictionary(self) -> None:
        connection=Mock()
        connection.execute.return_value=[{'snapshot_date':'2025-01-20','total_text':'100'}]
        self.assertEqual(_portfolio_values(connection,'alpha-world-ms','2025-01-20','HKD'),
                         {'2025-01-20':100.0})
        sql, params=connection.execute.call_args.args
        self.assertIn('AND currency = ?',sql)
        self.assertEqual(params,('alpha-world-ms','HKD','2025-01-20'))


if __name__ == "__main__":
    unittest.main()
