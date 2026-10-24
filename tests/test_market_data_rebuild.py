"""Market-data rebuild check: silver rows against what the stored raw files parse to.
Synthetic data only; the files are written by the real RawCapture into an in-memory store."""
from __future__ import annotations

import hashlib
import json
import unittest
from datetime import datetime, timezone

import pandas as pd

from ibkr_etl import market_data_rebuild as rebuild
from ibkr_etl.provisional_benchmark import normalize_yfinance_history
from ibkr_etl.raw_capture import RawCapture

NOW = datetime(2026, 10, 23, 9, 40, tzinfo=timezone.utc)
CLOSES = (5996.659912109375, 0.1 + 0.2, 6049.240234375)


class _Store:
    """bronze-market-data in memory: uploads through RawCapture land here."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, datetime]] = {}
        self.reads: list[str] = []

    def factory(self, account_url, container):
        def upload(name, content, *, content_type, metadata):
            self.files[name] = (content, NOW)
            return name
        return upload

    def list(self, prefix):
        return [(name, modified) for name, (_content, modified) in sorted(self.files.items())
                if name.startswith(prefix)]

    def read(self, name):
        self.reads.append(name)
        return self.files[name][0]


def _frame(closes=CLOSES):
    index = pd.DatetimeIndex(["2026-10-20", "2026-10-21", "2026-10-22"], tz="America/New_York", name="Date")
    return pd.DataFrame({"Open": closes, "Close": closes, "Adj Close": closes, "Volume": [1, 2, 3]}, index=index)


def _capture(store):
    return RawCapture(container="bronze-market-data", account_url="https://example.invalid",
                      uploader_factory=store.factory)


def _benchmark_rows(capture, store, frame, *, requested=NOW):
    name = capture.frame("yahoo-benchmark-history", "Yahoo", frame=frame, request={"symbol": "^GSPC"},
                         key="GSPC_2025-01-13_2026-10-23_1d", requested_at=requested)
    digest = capture.digest_of(name)
    return [{"benchmark_id": "GSPC", "observation_date": day, "level": level, "raw_sha256": digest,
             "source_reference": "yfinance:TICKER(^GSPC):history(interval=1d,auto_adjust=False):Close"}
            for day, level in normalize_yfinance_history(frame)]


TREASURY = b"""<feed xmlns:m="m" xmlns:d="d">
<entry><content><m:properties><d:NEW_DATE>%s</d:NEW_DATE><d:BC_3MONTH>%s</d:BC_3MONTH></m:properties></content></entry>
<entry><content><m:properties><d:NEW_DATE>%s</d:NEW_DATE><d:BC_3MONTH>%s</d:BC_3MONTH></m:properties></content></entry>
</feed>"""


def _treasury(first=("2026-10-21T00:00:00", "4.01"), second=("2026-10-22T00:00:00", "3.99")):
    return TREASURY % (first[0].encode(), first[1].encode(), second[0].encode(), second[1].encode())


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.capture = _capture(self.store)

    def test_rows_equal_to_their_file_match(self):
        rows = _benchmark_rows(self.capture, self.store, _frame())
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual((result.checked, result.matched, result.differing), (3, 3, []))
        self.assertEqual(result.files_read, 1)
        self.assertEqual(len(self.store.reads), 1, "one file is read once for all its rows")

    def test_a_changed_level_differs_and_is_named_without_its_value(self):
        rows = _benchmark_rows(self.capture, self.store, _frame())
        rows[1] = {**rows[1], "level": rows[1]["level"] + 1e-9}
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual(result.differing, ["GSPC 2026-10-21"])
        self.assertNotIn(str(rows[1]["level"]), json.dumps(result.summary()))

    def test_a_date_the_file_does_not_have_and_a_row_with_no_file(self):
        rows = _benchmark_rows(self.capture, self.store, _frame())
        rows.append({**rows[0], "observation_date": "2026-10-23"})
        rows.append({**rows[0], "raw_sha256": hashlib.sha256(b"digest of parsed closes").hexdigest()})
        rows.append({**rows[0], "raw_sha256": None})
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual((result.matched, result.missing_from_file, result.no_raw_file), (3, 1, 2))
        self.assertEqual(result.differing, [])

    def test_a_null_close_in_the_file_is_missing_not_matched(self):
        rows = _benchmark_rows(self.capture, self.store, _frame((CLOSES[0], float("nan"), CLOSES[2])))
        self.assertEqual(len(rows), 2)
        rows.append({**rows[0], "observation_date": "2026-10-21", "level": 5000.0})
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual((result.matched, result.missing_from_file), (2, 1))

    def test_a_file_whose_content_no_longer_matches_its_sha256_differs(self):
        rows = _benchmark_rows(self.capture, self.store, _frame())
        name = next(iter(self.store.files))
        self.store.files[name] = (self.store.files[name][0] + b"\n", NOW)
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual(result.unreadable_files, 1)
        self.assertEqual(len(result.differing), 3)

    def test_a_close_filled_from_the_quote_is_rebuilt_from_the_quote_file(self):
        rows = _benchmark_rows(self.capture, self.store, _frame())
        body = json.dumps({"quoteResponse": {"result": [
            {"symbol": "^GSPC", "regularMarketPrice": 6100.5, "regularMarketPreviousClose": 6049.240234375}]}}).encode()
        name = self.capture.http("yahoo-benchmark-history", "Yahoo", url="https://q", body=body,
                                 key="quote-GSPC", extension="json", requested_at=NOW)
        rows[2] = {**rows[2], "raw_sha256": self.capture.digest_of(name),
                   "source_reference": "yfinance:quote(^GSPC):regularMarketPreviousClose"}
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual((result.matched, result.differing), (3, []))
        rows[2] = {**rows[2], "source_reference": "yfinance:quote(^GSPC):regularMarketPrice"}
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual(result.differing, ["GSPC 2026-10-22"])

    def test_files_older_than_the_window_are_not_searched(self):
        rows = _benchmark_rows(self.capture, self.store, _frame(), requested=datetime(2026, 10, 1, tzinfo=timezone.utc))
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=7)
        self.assertEqual(result.no_raw_file, 3)
        result = rebuild.check_benchmarks(rows, self.store, now=NOW, days=30)
        self.assertEqual(result.matched, 3)


class TreasuryTests(unittest.TestCase):
    def setUp(self):
        self.store = _Store()
        self.capture = _capture(self.store)

    def _store_year(self, body, requested, *, history=False):
        return self.capture.http("treasury-par-yield", "U.S. Treasury", url="https://t", body=body,
                                 key="year-2026", extension="xml", requested_at=requested, history=history)

    def test_rows_match_the_newest_file(self):
        self._store_year(_treasury(second=("2026-10-22T00:00:00", "3.50")), datetime(2026, 10, 22, 23, 1, tzinfo=timezone.utc))
        self._store_year(_treasury(), NOW)
        rows = [{"rate_date": "2026-10-21", "annual_rate": 4.01 / 100.0},
                {"rate_date": "2026-10-22", "annual_rate": 3.99 / 100.0}]
        result = rebuild.check_treasury(rows, self.store, now=NOW, days=7)
        self.assertEqual((result.checked, result.matched, result.differing), (2, 2, []))

    def test_a_changed_rate_differs_and_a_year_with_no_file_is_counted(self):
        self._store_year(_treasury(), NOW)
        rows = [{"rate_date": "2026-10-21", "annual_rate": 0.0402},
                {"rate_date": "2026-10-20", "annual_rate": 0.04},
                {"rate_date": "2025-12-31", "annual_rate": 0.04}]
        result = rebuild.check_treasury(rows, self.store, now=NOW, days=7)
        self.assertEqual(result.differing, ["UST-PAR-3M 2026-10-21"])
        self.assertEqual((result.missing_from_file, result.no_raw_file), (1, 1))

    def test_a_finished_year_is_read_from_history(self):
        body = _treasury(("2025-12-30T00:00:00", "3.70"), ("2025-12-31T00:00:00", "3.68"))
        self.capture.http("treasury-par-yield", "U.S. Treasury", url="https://t", body=body, key="year-2025",
                          extension="xml", requested_at=NOW, history=True)
        result = rebuild.check_treasury([{"rate_date": "2025-12-31", "annual_rate": 0.0368}], self.store,
                                        now=NOW, days=7)
        self.assertEqual(result.matched, 1)


class _Connection:
    def __init__(self, benchmarks, treasury):
        self.benchmarks, self.treasury = benchmarks, treasury
        self.params = []

    def execute(self, sql, params):
        self.params.append(params)
        rows = self.benchmarks if "benchmark_prices" in sql else self.treasury
        return type("Cursor", (), {"fetchall": lambda _self: rows})()


class RunTests(unittest.TestCase):
    def test_passes_when_nothing_differs_and_require_files_counts_missing_files(self):
        store = _Store()
        capture = _capture(store)
        rows = _benchmark_rows(capture, store, _frame())
        connection = _Connection(rows + [{**rows[0], "raw_sha256": None}], [])
        summary = rebuild.run(connection, store, now=NOW, days=7)
        self.assertTrue(summary["passed"])
        self.assertEqual(connection.params[0], ("2026-10-16T09:40:00+00:00",))
        self.assertEqual(connection.params[1], ("UST-PAR-3M", "2026-10-16T09:40:00+00:00"))
        self.assertFalse(rebuild.run(connection, store, now=NOW, days=7, require_files=True)["passed"])
        connection = _Connection([{**rows[0], "level": 1.0}], [])
        summary = rebuild.run(connection, store, now=NOW, days=7)
        self.assertFalse(summary["passed"])
        self.assertEqual(summary["tables"][0]["differing_rows"], ["GSPC 2026-10-20"])

    def test_main_needs_its_settings(self):
        lines = []
        self.assertEqual(rebuild.main([], environ={}, out=lines.append), 2)
        self.assertTrue(lines[0].startswith("MARKET_DATA_REBUILD "))
        self.assertFalse(json.loads(lines[0].split(" ", 1)[1])["passed"])


if __name__ == "__main__":
    unittest.main()
