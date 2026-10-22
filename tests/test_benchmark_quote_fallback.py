"""A finished session's benchmark close from Yahoo's quote when the history reply has it without a close."""
from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from ibkr_etl import provisional_benchmark as pb
from ibkr_etl.provisional_benchmark import BenchmarkSpec

GSPC = BenchmarkSpec("GSPC", "^GSPC", "S&P 500 Price Index", "primary")
HSI = BenchmarkSpec("HSI", "^HSI", "Hang Seng Index", "comparison", currency="HKD")
KS200 = BenchmarkSpec("KS200", "069500.KS", "KODEX 200", "primary", currency="KRW")
HISTORY = [("2026-10-16", 6600.0), ("2026-10-19", 6620.0), ("2026-10-20", 6640.0)]
HK_HISTORY = [("2026-10-16", 26000.0), ("2026-10-20", 26100.0)]


def _stamp(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp())


def _quote(symbol: str, market_time: str, *, price: float = 6700.0, previous: float = 6650.0,
           currency: str = "USD") -> bytes:
    row = {"symbol": symbol, "currency": currency, "regularMarketTime": _stamp(market_time),
           "regularMarketPrice": price, "regularMarketPreviousClose": previous}
    return json.dumps({"quoteResponse": {"result": [row], "error": None}}).encode()


class _Fetcher:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.symbols: list[str] = []

    def __call__(self, symbol: str):
        self.symbols.append(symbol)
        return self.body, 200


class QuoteFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        pb._QUOTE_CLOSES.clear()
        capture = patch.object(pb.raw_capture, "capture_http", return_value=None)
        capture.start()
        self.addCleanup(capture.stop)

    def _fill(self, spec, received, expected_as_of, fetcher, now):
        with patch("builtins.print"):
            return pb.fill_missing_session(spec, received, date.fromisoformat(expected_as_of), fetcher,
                                           now=datetime.fromisoformat(now))

    def test_previous_close_fills_the_session_before_the_quotes_session(self) -> None:
        # Thu 22 Oct 15:55 UTC: the quote is on 22 Oct, so its previous close is 21 Oct's.
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T15:55:00+00:00"))
        rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", fetcher, "2026-10-22T15:55:00+00:00")
        self.assertEqual(rows[-1], ("2026-10-21", 6650.0))
        self.assertEqual(rows[:-1], HISTORY)
        self.assertEqual(filled["field"], "regularMarketPreviousClose")
        self.assertEqual(filled["source_reference"], "yfinance:quote(^GSPC):regularMarketPreviousClose")
        self.assertEqual(fetcher.symbols, ["^GSPC"])

    def test_regular_price_fills_the_quotes_own_session_once_it_has_closed(self) -> None:
        # Fri 23 Oct 08:30 UTC: the U.S. quote still shows Thu 22 Oct's close.
        history = HISTORY + [("2026-10-21", 6650.0)]
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T20:00:02+00:00"))
        rows, filled = self._fill(GSPC, history, "2026-10-22", fetcher, "2026-10-23T08:30:00+00:00")
        self.assertEqual(rows[-1], ("2026-10-22", 6700.0))
        self.assertEqual(filled["field"], "regularMarketPrice")

    def test_a_price_from_before_the_close_is_not_a_close(self) -> None:
        history = HISTORY + [("2026-10-21", 6650.0)]
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T19:45:00+00:00"))
        rows, filled = self._fill(GSPC, history, "2026-10-22", fetcher, "2026-10-23T08:30:00+00:00")
        self.assertIs(rows, history)
        self.assertIsNone(filled)

    def test_the_hong_kong_quote_uses_the_hong_kong_calendar(self) -> None:
        # 19 Oct 2026 was a Hong Kong holiday; 22 Oct is the session after 21 Oct.
        fetcher = _Fetcher(_quote("^HSI", "2026-10-22T08:08:00+00:00", price=26300.0, previous=26200.0,
                                  currency="HKD"))
        rows, filled = self._fill(HSI, HK_HISTORY, "2026-10-21", fetcher, "2026-10-22T16:00:00+00:00")
        self.assertEqual(rows[-1], ("2026-10-21", 26200.0))
        self.assertEqual(filled["field"], "regularMarketPreviousClose")

    def test_a_hole_before_a_later_bar_is_filled_too(self) -> None:
        # After 18:30 New York the job also asks for 22 Oct, which Yahoo has, while the NAV ends on 21 Oct.
        history = HISTORY + [("2026-10-22", 6700.0)]
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T20:00:02+00:00"))
        rows, filled = self._fill(GSPC, history, "2026-10-21", fetcher, "2026-10-22T23:00:00+00:00")
        self.assertEqual([day for day, _ in rows][-3:], ["2026-10-20", "2026-10-21", "2026-10-22"])
        self.assertEqual(dict(rows)["2026-10-21"], 6650.0)
        self.assertEqual(filled["field"], "regularMarketPreviousClose")

    def test_a_quote_two_sessions_on_names_a_different_previous_close(self) -> None:
        # Fri 23 Oct after Hong Kong's open: the previous close is 22 Oct's, not 21 Oct's.
        fetcher = _Fetcher(_quote("^HSI", "2026-10-23T02:00:00+00:00", currency="HKD",
                                  price=26300.0, previous=26200.0))
        rows, filled = self._fill(HSI, HK_HISTORY, "2026-10-21", fetcher, "2026-10-23T02:00:00+00:00")
        self.assertIs(rows, HK_HISTORY)
        self.assertIsNone(filled)

    def test_wrong_symbol_currency_or_size_is_refused(self) -> None:
        for body in (_quote("^IXIC", "2026-10-22T15:55:00+00:00"),
                     _quote("^GSPC", "2026-10-22T15:55:00+00:00", currency="EUR"),
                     _quote("^GSPC", "2026-10-22T15:55:00+00:00", previous=66500.0),
                     _quote("^GSPC", "2026-10-22T15:55:00+00:00", previous=0.0),
                     b'{"quoteResponse": {"result": []}}'):
            pb._QUOTE_CLOSES.clear()
            rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", _Fetcher(body), "2026-10-22T15:55:00+00:00")
            self.assertIs(rows, HISTORY)
            self.assertIsNone(filled)

    def test_nothing_is_asked_when_the_history_reaches_the_session(self) -> None:
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T15:55:00+00:00"))
        history = HISTORY + [("2026-10-21", 6650.0)]
        rows, filled = self._fill(GSPC, history, "2026-10-21", fetcher, "2026-10-22T15:55:00+00:00")
        self.assertIs(rows, history)
        self.assertIsNone(filled)
        self.assertEqual(fetcher.symbols, [])

    def test_a_session_still_open_locally_is_never_filled(self) -> None:
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T15:55:00+00:00"))
        rows, filled = self._fill(GSPC, HISTORY + [("2026-10-21", 6650.0)], "2026-10-22", fetcher,
                                  "2026-10-22T19:00:00+00:00")
        self.assertIsNone(filled)
        self.assertEqual(fetcher.symbols, [])

    def test_unmapped_benchmarks_and_no_fetcher_change_nothing(self) -> None:
        fetcher = _Fetcher(_quote("069500.KS", "2026-10-22T06:30:00+00:00", currency="KRW"))
        rows, filled = self._fill(KS200, HISTORY, "2026-10-21", fetcher, "2026-10-22T15:55:00+00:00")
        self.assertIs(rows, HISTORY)
        self.assertEqual(fetcher.symbols, [])
        rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", None, "2026-10-22T15:55:00+00:00")
        self.assertIs(rows, HISTORY)
        self.assertIsNone(filled)

    def test_a_failing_quote_never_fails_the_job_and_is_tried_once(self) -> None:
        calls = []

        def broken(symbol):
            calls.append(symbol)
            raise TimeoutError("no answer")
        for _ in range(2):
            rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", broken, "2026-10-22T15:55:00+00:00")
            self.assertIs(rows, HISTORY)
            self.assertIsNone(filled)
        self.assertEqual(calls, ["^GSPC"])
        pb._QUOTE_CLOSES.clear()
        rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", _Fetcher(b"<html>Too Many Requests</html>"),
                                  "2026-10-22T15:55:00+00:00")
        self.assertIsNone(filled)

    def test_one_quote_per_symbol_and_session_in_a_run(self) -> None:
        fetcher = _Fetcher(_quote("^GSPC", "2026-10-22T15:55:00+00:00"))
        for _ in range(3):
            self._fill(GSPC, HISTORY, "2026-10-21", fetcher, "2026-10-22T15:55:00+00:00")
        self.assertEqual(fetcher.symbols, ["^GSPC"])

    def test_the_raw_quote_goes_to_bronze_with_the_benchmark_history(self) -> None:
        body = _quote("^GSPC", "2026-10-22T15:55:00+00:00")
        with patch.object(pb.raw_capture, "capture_http", return_value="blob") as capture, \
                patch.object(pb.raw_capture, "sha256_of", return_value="ab" * 32):
            _rows, filled = self._fill(GSPC, HISTORY, "2026-10-21", _Fetcher(body), "2026-10-22T15:55:00+00:00")
        self.assertEqual(capture.call_args.args[0], "yahoo-benchmark-history")
        self.assertEqual(capture.call_args.kwargs["body"], body)
        self.assertEqual(capture.call_args.kwargs["url"], pb.QUOTE_URL + "?symbols=^GSPC")
        self.assertEqual(filled["sha256"], "ab" * 32)

    def test_switch(self) -> None:
        self.assertTrue(pb.quote_fallback_enabled({}))
        self.assertTrue(pb.quote_fallback_enabled({"BENCHMARK_QUOTE_FALLBACK": "on"}))
        self.assertFalse(pb.quote_fallback_enabled({"BENCHMARK_QUOTE_FALLBACK": "OFF "}))


class _Day:
    def __init__(self, day: date) -> None:
        self._day = day

    def date(self) -> date:
        return self._day


class _History:
    """Yahoo's history as Azure got it on 22 Oct 2026: every session to 21 Oct, 21 Oct without a close."""

    def __init__(self, days) -> None:
        self.days = days

    def iterrows(self):
        for number, day in enumerate(self.days):
            close = float("nan") if day == date(2026, 10, 21) else 6000.0 + number
            yield _Day(day), {"Close": close}

    def to_csv(self) -> str:
        return ""


class RefreshWiringTests(unittest.TestCase):
    """The job itself: the missing close is filled, stored with the quote's lineage and flagged."""

    def setUp(self) -> None:
        pb._QUOTE_CLOSES.clear()

    def _run(self, quote_fetcher):
        from datetime import timedelta
        from unittest.mock import MagicMock

        days, day = [], date(2026, 7, 1)
        while day <= date(2026, 10, 21):
            if pb.CALENDARS["XNYS"].is_session(day):
                days.append(day)
            day += timedelta(days=1)
        nav = {d.isoformat(): 1000.0 + n for n, d in enumerate(days)}

        class Ticker:
            def __init__(self, symbol):
                assert symbol == "^GSPC"

            def history(self, **_kwargs):
                return _History(days)

        connection = MagicMock()
        with patch.object(pb, "_account_contract", return_value=("USD", "USD", date(2026, 7, 1))), \
                patch.object(pb, "mapped_benchmarks", return_value=(GSPC,)), \
                patch.object(pb, "website_all_benchmarks_enabled", return_value=False), \
                patch.object(pb, "_portfolio_values", return_value=nav), \
                patch.object(pb, "_external_flows", return_value={}), \
                patch.object(pb, "_store_reporting_usd_fx", return_value={}), \
                patch.object(pb.publish_gate, "before_commit"), \
                patch.object(pb.raw_capture, "utc_now",
                             return_value=datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)), \
                patch("builtins.print"):
            result = pb.refresh_and_materialize(connection, account_id="U1", start_date=date(2026, 7, 1),
                                                end_date=date(2026, 10, 21), ticker_factory=Ticker,
                                                quote_fetcher=quote_fetcher)
        return result, [call.args for call in connection.execute.call_args_list]

    def test_the_subject_publishes_with_the_quote_close(self) -> None:
        result, calls = self._run(_Fetcher(_quote("^GSPC", "2026-10-22T15:55:00+00:00", previous=6080.0)))
        self.assertEqual(result["benchmarks"][0]["latest_benchmark_date"], "2026-10-21")
        prices = [params for sql, *rest in calls if "INSERT INTO benchmark_prices" in sql for params in rest]
        quoted = [params for params in prices if params[1] == "2026-10-21"]
        self.assertEqual(len(quoted), 1)
        self.assertEqual(quoted[0][2], 6080.0)
        self.assertEqual(quoted[0][6], "yfinance:quote(^GSPC):regularMarketPreviousClose")
        self.assertTrue(all(params[6].startswith("yfinance:TICKER(^GSPC)") for params in prices
                            if params[1] != "2026-10-21"))
        warned = [sql for sql, *_ in calls if "benchmark_close_from_quote" in sql]
        self.assertEqual(len(warned), 1)

    def test_without_the_quote_the_subject_waits_as_before(self) -> None:
        with self.assertRaises(pb.BenchmarkNotCurrent):
            self._run(None)


if __name__ == "__main__":
    unittest.main()
