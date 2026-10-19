"""Phase 3 part A: daily volumes and factor returns, without a database or the network
(docs/PHASE3_DATA.md).

The volume array of synthetic Yahoo chart responses (known values, nulls and bad values skipped,
exchange-local dates, the bar still trading left out, the same dates as fetch_daily_closes); which
captures a run reads; the newest capture winning. Synthetic Kenneth French daily files (percent to
fraction, the notes, annual section and copyright line ignored, the momentum file), the zip, the
vintage, and each file stored as received in bronze (kenneth-french, with its licence) before it is
parsed. Both loaders doing nothing when off, and stopping with the held exit code, having read and
fetched nothing, when the table is missing. Their registrations and job definitions.

The holdings' own volumes fetch, with a fake fetcher (or a fake Yahoo behind market_data's real
fetch) and a fake bronze that raw capture writes into: the platform's symbol mapping and its order;
holdings not mapped or not found listed, never guessed; each response kept in bronze before its
volumes are parsed back from it; pacing with a fake clock (the pause, the cap, the time budget);
nothing fetched when DAILY_VOLUMES_FETCH is off or raw capture is not set up; the held exit code
still first; the job's Bicep and its identity's container role.

The network is shut for the whole module: every fetch is a fake. The SQL is in
test_phase3_volumes_factors_db.py.
"""
from __future__ import annotations

import ast
import contextlib
import email.message
import hashlib
import importlib.util
import io
import json
import os
import re
import unittest
import urllib.error
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import daily_volumes as dv
from ibkr_etl import factor_returns as fr
from ibkr_etl import (fama_french, layer_freshness, layer_quality, market_data, publish_gate, raw_capture,
                      sector_classification)

ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "infra" / "medallion" / "catalog.json").read_text(encoding="utf-8"))
ACCOUNT_URL = "https://example.blob.core.windows.net"
CONTAINER = "bronze-market-data"
VOLUMES_JOB, FACTORS_JOB = "ibkrv2stg-daily-volumes", "ibkrv2stg-factor-returns"


def setUpModule():
    # No test here may reach a network service: every fetch below is a fake.
    for target in ("urllib.request.urlopen", "ibkr_etl.cloud_storage.container_client"):
        patcher = patch(target, side_effect=AssertionError(f"network: {target}"))
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_under_test", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


# ---------------------------------------------------------------- synthetic chart responses


def _ts(moment: str) -> int:
    return int(datetime.fromisoformat(moment).replace(tzinfo=timezone.utc).timestamp())


def chart(bars, *, symbol: str = "SPY", currency: str = "USD", zone: str = "America/New_York",
          regular: tuple[str, str] | None = None) -> bytes:
    """A Yahoo chart response: bars are (UTC moment of the bar, close, volume)."""
    meta = {"symbol": symbol, "currency": currency, "exchangeTimezoneName": zone}
    if regular:
        meta["currentTradingPeriod"] = {"regular": {"start": _ts(regular[0]), "end": _ts(regular[1])}}
    result = {"meta": meta, "timestamp": [_ts(moment) for moment, _, _ in bars],
              "indicators": {"quote": [{"close": [close for _, close, _ in bars],
                                        "volume": [volume for _, _, volume in bars]}]}}
    return json.dumps({"chart": {"result": [result], "error": None}}).encode("utf-8")


# New York's daily bars are stamped at the 09:30 open (13:30 UTC in October).
SPY_BARS = [
    ("2026-10-12T13:30:00", 580.10, 41_200_300),
    ("2026-10-13T13:30:00", 581.00, None),  # no volume: skipped
    ("2026-10-14T13:30:00", 579.50, 0),  # zero: kept, as Yahoo reports it
    ("2026-10-15T13:30:00", 582.00, -1),  # below zero: skipped
    ("2026-10-16T13:30:00", 583.00, float("nan")),  # not finite: skipped
    ("2026-10-19T13:30:00", 584.00, "n/a"),  # not a number: skipped
    ("2026-10-20T13:30:00", 585.25, 38_000_000.0),
]


class _Bronze:
    """bronze-market-data as a dict of blob name -> (bytes, requested at)."""

    container = CONTAINER

    def __init__(self, files: dict[str, tuple[bytes, datetime]] | None = None):
        self.files = dict(files or {})
        self.prefixes: list[str] = []
        self.reads: list[str] = []

    def add(self, folder: str, key: str, content: bytes, stored_at: datetime) -> str:
        name = f"{dv.FEED}/{folder}/{key}-{hashlib.sha256(content).hexdigest()[:16]}.json"
        self.files[name] = (content, stored_at)
        return name

    def list(self, prefix):
        self.prefixes.append(prefix)
        return [dv.CaptureRef(name, stored, f"https://query1.finance.yahoo.com/v8/finance/chart/{name[-30:]}")
                for name, (_, stored) in sorted(self.files.items()) if name.startswith(prefix)]

    def read(self, name):
        self.reads.append(name)
        return self.files[name][0]


class _Record:
    """A lineage run that keeps what it is told."""

    def __init__(self):
        self.inputs: list[tuple[str, dict]] = []
        self.outputs: list[tuple[str, dict]] = []

    def input(self, name, **facets):
        self.inputs.append((name, facets))

    def output(self, name, **facets):
        self.outputs.append((name, facets))


class VolumeParsingTests(unittest.TestCase):
    def test_known_values_are_read_and_unusable_days_skipped(self):
        parsed = dv.parse_chart_volumes(chart(SPY_BARS))
        self.assertEqual((parsed.symbol, parsed.currency), ("SPY", "USD"))
        self.assertEqual(parsed.volumes, [("2026-10-12", 41_200_300.0), ("2026-10-14", 0.0),
                                          ("2026-10-20", 38_000_000.0)])
        self.assertEqual(parsed.skipped, 4)
        self.assertTrue(all(isinstance(volume, float) for _, volume in parsed.volumes))

    def test_dates_are_the_exchanges_own(self):
        # Sydney opens at 10:00 AEDT, 23:00 UTC the day before: the bar is the local day.
        parsed = dv.parse_chart_volumes(chart([("2026-10-14T23:00:00", 8900.0, 612_000)], symbol="^AXJO",
                                              currency="AUD", zone="Australia/Sydney"))
        self.assertEqual(parsed.volumes, [("2026-10-15", 612_000.0)])
        # An unknown zone falls back to UTC, as market_data does.
        parsed = dv.parse_chart_volumes(chart([("2026-10-14T23:00:00", 1.0, 5)], zone="Nowhere/Unknown"))
        self.assertEqual(parsed.volumes, [("2026-10-14", 5.0)])

    def test_the_same_dates_as_the_closes_fetch_daily_closes_stores(self):
        bars = [("2026-10-13T23:00:00", 8890.0, 600_000), ("2026-10-14T23:00:00", 8900.0, 612_000),
                ("2026-10-15T23:00:00", 8910.0, 598_000)]
        content = chart(bars, symbol="^AXJO", currency="AUD", zone="Australia/Sydney")
        instrument = market_data.MarketInstrument("asx-200", "^AXJO", "S&P/ASX 200", "AUD", "price_index_close")
        with patch.object(market_data, "_download_json", return_value=json.loads(content)):
            _, closes = market_data.fetch_daily_closes(instrument, date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual([day for day, _ in dv.parse_chart_volumes(content).volumes], [day for day, _ in closes])

    def test_a_bar_still_trading_when_captured_is_left_out(self):
        bars = SPY_BARS[-2:]
        session = ("2026-10-20T13:30:00", "2026-10-20T20:00:00")
        during = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
        after = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)
        self.assertEqual(dv.parse_chart_volumes(chart(bars, regular=session), requested_at=during).volumes, [])
        self.assertEqual(dv.parse_chart_volumes(chart(bars, regular=session), requested_at=during).skipped, 2)
        self.assertEqual(dv.parse_chart_volumes(chart(bars, regular=session), requested_at=after).volumes,
                         [("2026-10-20", 38_000_000.0)])
        self.assertEqual(dv.parse_chart_volumes(chart(bars, regular=session)).volumes, [("2026-10-20", 38_000_000.0)])

    def test_responses_that_are_not_a_chart_are_refused(self):
        for content, reason in (
                (b"<html>busy</html>", "not JSON"),
                (json.dumps({"chart": {"result": None, "error": {"code": "Not Found"}}}).encode(), "rejected"),
                (json.dumps({"chart": {"result": [], "error": None}}).encode(), "no chart result"),
                (json.dumps({"finance": {}}).encode(), "no chart"),
                (chart(SPY_BARS).replace(b'"volume": [41200300', b'"volume": [41200300, 1'), "does not match")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(dv.CaptureError, reason):
                    dv.parse_chart_volumes(content)

    def test_the_setting_is_on_only_when_it_says_on(self):
        for value, expected in (("on", True), (" ON ", True), ("", False), ("off", False), ("true", False),
                                ("1", False)):
            with self.subTest(value=value):
                self.assertIs(dv.load_enabled({dv.SETTING: value}), expected)
                self.assertIs(fr.load_enabled({fr.SETTING: value}), expected)
        self.assertFalse(dv.load_enabled({}))
        self.assertFalse(fr.load_enabled({}))


class CaptureSelectionTests(unittest.TestCase):
    def test_capture_names(self):
        self.assertEqual(dv.capture_folder_day("yahoo-chart/2026/10/16/SPY_2025-01-13_2026-10-16_1d-0123.json"),
                         date(2026, 10, 16))
        self.assertIsNone(dv.capture_folder_day("yahoo-chart/history/SPY_2025-01-13_2026-10-01_1d-0123.json"))
        self.assertIsNone(dv.capture_folder_day("yahoo-chart/2026/13/40/SPY_x-0123.json"))
        name = "yahoo-chart/2026/10/16/SPY_2025-01-13_2026-10-16_1d-0123abcd.json"
        self.assertTrue(dv.capture_names_match(name, "SPY"))
        self.assertFalse(dv.capture_names_match(name, "SP"))
        self.assertFalse(dv.capture_names_match(name.replace("SPY_", "SPYG_"), "SPY"))
        # Index symbols lose their caret in the request key, as fetch_daily_closes names them.
        self.assertTrue(dv.capture_names_match("yahoo-chart/2026/10/16/HSI_2025-01-13_2026-10-16_1d-01.json", "^HSI"))

    def test_the_daily_run_reads_the_last_dated_folders_and_a_backfill_everything_kept(self):
        stored = datetime(2026, 10, 16, 9, 0, tzinfo=timezone.utc)
        bronze = _Bronze()
        names = {folder: bronze.add(folder, f"SPY_{folder.replace('/', '-')}", chart(SPY_BARS[:1]), stored)
                 for folder in ("2026/10/16", "2026/10/15", "2026/10/14", "2026/10/13", "2026/09/30", "history")}
        daily = dv.list_captures(bronze, today=date(2026, 10, 16), days=3)
        self.assertEqual(bronze.prefixes, ["yahoo-chart/2026/10/16/", "yahoo-chart/2026/10/15/",
                                           "yahoo-chart/2026/10/14/"])
        self.assertEqual(sorted(ref.name for ref in daily),
                         sorted(names[f] for f in ("2026/10/16", "2026/10/15", "2026/10/14")))
        # A backfill from 1 Oct: history/, and every dated folder from the day before (a capture
        # never holds a day after its request date, but an Asian date can be a day ahead of UTC).
        backfill = dv.list_captures(bronze, today=date(2026, 10, 16), since=date(2026, 10, 1))
        self.assertEqual(bronze.prefixes[-1], "yahoo-chart/")
        self.assertEqual(sorted(ref.name for ref in backfill), sorted(names[f] for f in (
            "2026/10/16", "2026/10/15", "2026/10/14", "2026/10/13", "2026/09/30", "history")))
        later = dv.list_captures(bronze, today=date(2026, 10, 16), since=date(2026, 10, 15))
        self.assertEqual(sorted(ref.name for ref in later), sorted(names[f] for f in (
            "2026/10/16", "2026/10/15", "2026/10/14", "history")))

    def test_the_newest_capture_wins_and_each_one_used_is_an_input(self):
        bronze = _Bronze()
        older = bronze.add("2026/10/15", "SPY_2025-01-13_2026-10-15_1d",
                           chart([("2026-10-13T13:30:00", 1, 100), ("2026-10-14T13:30:00", 1, 200)]),
                           datetime(2026, 10, 15, 8, 50, tzinfo=timezone.utc))
        newer = bronze.add("2026/10/16", "SPY_2025-01-13_2026-10-16_1d",
                           chart([("2026-10-14T13:30:00", 1, 210), ("2026-10-15T13:30:00", 1, 300)]),
                           datetime(2026, 10, 16, 8, 50, tzinfo=timezone.utc))
        broken = bronze.add("2026/10/16", "SPY_2025-01-13_2026-10-16_1d_retry", b"{not json",
                            datetime(2026, 10, 16, 8, 51, tzinfo=timezone.utc))
        other_currency = bronze.add("2026/10/16", "SPY_2025-01-13_2026-10-16_1d_eur",
                                    chart([("2026-10-15T13:30:00", 1, 999)], currency="EUR"),
                                    datetime(2026, 10, 16, 8, 52, tzinfo=timezone.utc))
        record = _Record()
        result = dv.InstrumentResult(dv.Instrument("spy-settlement", "SPY", "USD"))
        refs = list(reversed(bronze.list("yahoo-chart/")))  # the order listed must not matter
        chosen = dv._newest_volumes(result.instrument, refs, bronze, result, record)
        self.assertEqual({day: (volume, ref.name) for day, (volume, ref) in chosen.items()},
                         {"2026-10-13": (100.0, older), "2026-10-14": (210.0, newer), "2026-10-15": (300.0, newer)})
        self.assertEqual(result.captures, 2)
        self.assertEqual(sorted(item["capture"] for item in result.unreadable), sorted([broken, other_currency]))
        self.assertEqual(record.inputs, [
            (f"blob://{CONTAINER}/{name}", {"sha256": hashlib.sha256(bronze.files[name][0]).hexdigest(),
                                            "feed": "yahoo-chart"}) for name in (older, newer)])

    def test_a_load_writes_one_transaction_per_instrument_within_its_dates(self):
        bronze = _Bronze()
        stored = datetime(2026, 10, 16, 8, 50, tzinfo=timezone.utc)
        spy = bronze.add("2026/10/16", "SPY_2025-01-13_2026-10-16_1d", chart(SPY_BARS[:3]), stored)
        bronze.add("2026/10/16", "SPYG_2025-01-13_2026-10-16_1d", chart(SPY_BARS[:1], symbol="SPYG"), stored)
        connection = _Connection([{"instrument_id": "spy-settlement", "symbol": "SPY", "currency": "usd"},
                                  {"instrument_id": "hang-seng-price-index", "symbol": "^HSI", "currency": "HKD"}])
        record = _Record()
        summary = dv.load(connection, bronze, today=date(2026, 10, 16), since=date(2026, 10, 13),
                          until=date(2026, 10, 31), record=record)
        self.assertEqual(connection.queries[0][1], (dv.CHART_SOURCE,))
        self.assertEqual(len(connection.batches), 1)  # SPY only: ^HSI has no capture
        self.assertEqual(connection.batches[0], [
            ("spy-settlement", "2026-10-14", 0.0, "yahoo-chart",
             "https://query1.finance.yahoo.com/v8/finance/chart/" + spy[-30:],
             "2026-10-16T08:50:00+00:00")])
        self.assertEqual(connection.commits, 1)
        self.assertEqual({key: summary[key] for key in ("mode", "since", "until", "rows", "written", "failed",
                                                        "without_captures")},
                         {"mode": "backfill", "since": "2026-10-13", "until": "2026-10-31", "rows": 1, "written": 1,
                          "failed": [], "without_captures": ["hang-seng-price-index"]})
        self.assertEqual(record.outputs, [(dv.TABLE, {"rows": 1})])
        self.assertEqual(summary["instruments"][0]["skipped_days"], 1)


class _Cursor:
    def __init__(self, connection):
        self.connection, self.rowcount = connection, -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def executemany(self, sql, rows):
        self.connection.batches.append(list(rows))
        self.rowcount = len(rows)


class _Connection:
    """Enough of db.PostgresConnection for a load: instrument rows in, upserted rows recorded."""

    def __init__(self, instruments=(), relation: str | None = "daily_volumes"):
        self.instrument_rows, self.relation = list(instruments), relation
        self.queries: list[tuple[str, tuple]] = []
        self.batches: list[list[tuple]] = []
        self.commits = self.rollbacks = 0
        self.closed = False
        self._connection = self

    def execute(self, sql, params=()):
        self.queries.append((" ".join(sql.split()), tuple(params)))
        connection = self

        class _Result:
            def fetchone(self):
                return {"relation": connection.relation}

            def fetchall(self):
                return connection.instrument_rows

        return _Result()

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


# ---------------------------------------------------------------- the holdings' own fetch


FETCH_NOW = datetime(2026, 10, 19, 6, 30, tzinfo=timezone.utc)  # the job's 06:30 run
FETCH_TODAY = FETCH_NOW.date()
YEAR_AGO = date(2025, 10, 19)


class _HoldingsConnection(_Connection):
    """A load's connection that also answers the fetch's two reads: the holdings, and the latest
    stored day of each instrument."""

    def __init__(self, holdings=(), loaded=None, **kwargs):
        super().__init__(**kwargs)
        self.holding_rows, self.loaded = list(holdings), dict(loaded or {})

    def execute(self, sql, params=()):
        result = super().execute(sql, params)
        if sql == dv.HOLDINGS_SQL:
            rows = self.holding_rows
        elif sql == dv.LOADED_SQL:
            rows = [{"instrument_id": k, "latest": v} for k, v in self.loaded.items() if k in params[0]]
        else:
            return result

        class _Rows:
            def fetchall(self):
                return rows

        return _Rows()

    def ran(self, sql) -> bool:
        return " ".join(sql.split()) in [query for query, _ in self.queries]


def _holding(conid, symbol, currency="USD", *, exchange="NASDAQ", category="STK", lookup=None):
    return {"conid": conid, "symbol": symbol, "listing_exchange": exchange, "currency": currency,
            "asset_category": category, "lookup_symbol": lookup}


class _CapturingBronze(_Bronze):
    """bronze-market-data that raw capture writes into (its uploader) and the load reads back, with
    every capture and read in order beside the fetches."""

    def __init__(self):
        super().__init__()
        self.events: list[tuple[str, str]] = []
        self.metadata: dict[str, dict] = {}
        self.fail_uploads = False

    def factory(self, account_url, container):
        return self.upload

    def upload(self, name, content, *, content_type, metadata):
        if self.fail_uploads:
            raise OSError("storage is down")
        self.events.append(("captured", name))
        self.files[name] = (content, datetime.fromisoformat(metadata["requested_at"]))
        self.metadata[name] = metadata
        return f"{ACCOUNT_URL}/{CONTAINER}/{name}"

    def list(self, prefix):
        self.prefixes.append(prefix)
        return [dv.CaptureRef(name, stored, self.metadata.get(name, {}).get("source_url", ""))
                for name, (_, stored) in sorted(self.files.items()) if name.startswith(prefix)]

    def read(self, name):
        self.events.append(("read", name))
        return super().read(name)


def _capture_into(test: unittest.TestCase, bronze: _CapturingBronze, container: str = CONTAINER,
                  account_url: str = ACCOUNT_URL) -> raw_capture.RawCapture:
    capture = raw_capture.RawCapture(container=container, account_url=account_url, uploader_factory=bronze.factory)
    raw_capture.reset(capture)
    test.addCleanup(raw_capture.reset)
    return capture


def _not_found(url: str) -> RuntimeError:
    """What market_data raises once Yahoo has answered 404 three times."""
    error = RuntimeError(f"Market data request failed after 3 attempts: {url}")
    error.__cause__ = urllib.error.HTTPError(url, 404, "Not Found", None, None)
    return error


class _Yahoo:
    """market_data.fetch_daily_closes as the fetch calls it: the response is kept raw first, as
    market_data._download_json keeps it, then checked as fetch_daily_closes checks it. charts maps a
    Yahoo symbol to its response; a symbol not there is a 404, an exception is raised as it is."""

    def __init__(self, bronze, charts, *, clock=None, seconds=0.5, at=FETCH_NOW):
        self.bronze, self.charts, self.clock, self.seconds, self.at = bronze, charts, clock, seconds, at
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, instrument, start, end):
        self.calls.append((instrument.symbol, start, end))
        self.bronze.events.append(("fetch", instrument.symbol))
        if self.clock is not None:
            self.clock.now += self.seconds
        url = market_data._chart_url(instrument.symbol, start, end)
        answer = self.charts.get(instrument.symbol)
        if answer is None:
            raise _not_found(url)
        if isinstance(answer, BaseException):
            raise answer
        raw_capture.capture_http("yahoo-chart", "Yahoo Finance chart API", url=url, body=answer,
                                 key=raw_capture.request_key(instrument.symbol, start.isoformat(), end.isoformat(),
                                                             "1d"),
                                 status=200, extension="json", requested_at=self.at,
                                 history=raw_capture.settled(end, now=self.at))
        currency = json.loads(answer)["chart"]["result"][0]["meta"]["currency"]
        if currency != instrument.currency:
            raise RuntimeError(f"Unexpected currency for {instrument.symbol}: {currency}, expected {instrument.currency}")
        return url, []


class _Clock:
    """A monotonic clock that moves only when the fetch sleeps or a fake request takes time."""

    def __init__(self):
        self.now, self.sleeps = 100.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def _us(symbol: str, bars) -> bytes:
    return chart([(f"{day}T13:30:00", 50.0, volume) for day, volume in bars], symbol=symbol)


def _hk(symbol: str, bars) -> bytes:
    # Hong Kong trades 01:30 to 08:00 UTC, so at 06:30 UTC the 19 October bar is still trading.
    return chart([(f"{day}T01:30:00", 400.0, volume) for day, volume in bars], symbol=symbol, currency="HKD",
                 zone="Asia/Hong_Kong", regular=("2026-10-19T01:30:00", "2026-10-19T08:00:00"))


def _kr(symbol: str, bars) -> bytes:
    return chart([(f"{day}T00:00:00", 60_000.0, volume) for day, volume in bars], symbol=symbol, currency="KRW",
                 zone="Asia/Seoul")


class HoldingsMappingTests(unittest.TestCase):
    def test_the_fetch_setting_is_on_only_when_it_says_on(self):
        for value, expected in (("on", True), (" ON ", True), ("", False), ("off", False), ("true", False)):
            with self.subTest(value=value):
                self.assertIs(dv.fetch_enabled({dv.FETCH_SETTING: value}), expected)
        self.assertFalse(dv.fetch_enabled({}))
        self.assertFalse(dv.fetch_enabled({dv.SETTING: "on"}))  # the load's setting does not turn it on

    def test_symbols_are_the_platforms_own_candidates_in_its_order(self):
        def mapped(**kwargs):
            return dv.yahoo_symbols(dv.Holding(**{"conid": "9100001", "listing_exchange": "", "asset_category": "STK",
                                                  **kwargs}))

        # Exactly sector_classification.candidate_symbols, tried in order as crisis_replay tries them.
        for symbol, exchange, currency in (("BRK B", "NYSE", "USD"), ("700", "SEHK", "HKD"), ("7203", "TSEJ", "JPY"),
                                           ("5930", "KRX", "KRW"), ("2330", "TWSE", "TWD"), ("VOD", "LSE", "GBP"),
                                           ("2888.OLD", "TWSE", "TWD")):
            with self.subTest(symbol=symbol):
                self.assertEqual(mapped(symbol=symbol, listing_exchange=exchange, currency=currency),
                                 (sector_classification.candidate_symbols(symbol, exchange, currency), None))
        self.assertEqual(mapped(symbol="BRK B", currency="USD"), (["BRK-B"], None))
        self.assertEqual(mapped(symbol="700", listing_exchange="SEHK", currency="HKD"), (["0700.HK", "700"], None))
        # The symbol the sector lookup matched goes first when it is one of the candidates.
        self.assertEqual(mapped(symbol="5930", listing_exchange="KRX", currency="KRW", lookup_symbol="005930.KQ"),
                         (["005930.KQ", "005930.KS", "5930"], None))
        # Not otherwise: an owner's override to another line (a warrant's underlying) is not its volume.
        self.assertEqual(mapped(symbol="SKYD", currency="USD", lookup_symbol="PSKY"), (["SKYD"], None))

    def test_a_holding_that_cannot_be_mapped_says_why_and_is_never_guessed(self):
        for kwargs, reason in (
                ({"symbol": "SYNA  261120C00050000", "asset_category": "OPT"}, "asset category OPT"),
                ({"symbol": "SKYD WS", "asset_category": "WAR", "lookup_symbol": "SKYD"}, "asset category WAR"),
                ({"symbol": "T 4 1/2 11/15/33", "asset_category": "BOND"}, "asset category BOND"),
                ({"symbol": "SYNA", "asset_category": ""}, "asset category unknown"),
                ({"symbol": "SYNB.REC"}, "receivable"),
                ({"symbol": ""}, "no IBKR symbol"),
                ({"symbol": "9100001"}, "no IBKR symbol"),  # the conid stands in for a missing symbol
                ({"symbol": "SYNA", "currency": ""}, "no currency")):
            with self.subTest(**kwargs):
                holding = dv.Holding(**{"conid": "9100001", "listing_exchange": "", "currency": "USD",
                                        "asset_category": "STK", **kwargs})
                candidates, why = dv.yahoo_symbols(holding)
                self.assertEqual(candidates, [])
                self.assertIn(reason, why)

    def test_the_window_is_a_year_at_first_then_ten_days_without_gaps(self):
        today = FETCH_TODAY
        for latest, start in ((None, YEAR_AGO), ("", YEAR_AGO), ("soon", YEAR_AGO),
                              ("2026-10-16", date(2026, 10, 9)),  # recent: the last 10 days
                              ("2026-10-18", date(2026, 10, 9)),
                              ("2026-09-20", date(2026, 9, 19)),  # a pause: from the day before the latest
                              ("2024-03-01", YEAR_AGO)):  # never more than a year
            with self.subTest(latest=latest):
                self.assertEqual(dv.fetch_window(latest, today), (start, today))


class HoldingsFetchTests(unittest.TestCase):
    def _run(self, connection, bronze, fetcher, **kwargs):
        record = _Record()
        clock = kwargs.pop("clock", None) or _Clock()
        with patch.object(raw_capture, "utc_now", return_value=FETCH_NOW):
            summary = dv.fetch_and_load(connection, bronze, fetcher=fetcher, sleep=clock.sleep, clock=clock,
                                        record=record, **kwargs)
        return summary, record

    def test_each_holding_is_kept_in_bronze_first_and_its_volumes_parsed_from_that_capture(self):
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        holdings = [
            _holding("9100001", "SYNA"),
            _holding("9100002", "700", "HKD", exchange="SEHK", lookup="0700.HK"),
            _holding("9100003", "SYNA  261120C00050000", category="OPT"),
            _holding("9100004", "SYNB.REC"),
            _holding("9100005", ""),
            _holding("9100006", "5930", "KRW", exchange="KRX"),
            _holding("9100007", "GONE"),
            _holding("9100008", "VOD", "GBP", exchange="LSE"),
        ]
        yahoo = _Yahoo(bronze, {
            "SYNA": _us("SYNA", [("2026-10-15", 1_200_000), ("2026-10-16", 1_350_000)]),
            "0700.HK": _hk("0700.HK", [("2026-10-16", 18_000_000), ("2026-10-19", 4_000_000)]),
            "005930.KQ": _kr("005930.KQ", [("2026-10-16", 9_000_000)]),  # .KS is not on Yahoo
            "VOD": _us("VOD", [("2026-10-16", 5_000_000)]),  # the US line, in dollars: not this holding
        })
        connection = _HoldingsConnection(holdings)
        summary, record = self._run(connection, bronze, yahoo, today=FETCH_TODAY)

        # Asked in order (none has volumes yet, so by conid), each for the last year.
        self.assertEqual(yahoo.calls, [(symbol, YEAR_AGO, FETCH_TODAY) for symbol in (
            "SYNA", "0700.HK", "005930.KS", "005930.KQ", "GONE", "VOD.L", "VOD")])
        self.assertEqual({key: summary[key] for key in (
            "status", "holdings", "mapped", "requests", "fetched", "captured", "rows", "written", "failed",
            "deferred", "deferred_because", "unreadable_captures")},
            {"status": "success", "holdings": 8, "mapped": 5, "requests": 7, "fetched": 3, "captured": 3,
             "rows": 4, "written": 4, "failed": [], "deferred": [], "deferred_because": None,
             "unreadable_captures": 0})
        self.assertEqual([(item["instrument_id"], item["symbol"]) for item in summary["not_mapped"]],
                         [("9100003", "SYNA  261120C00050000"), ("9100004", "SYNB.REC"), ("9100005", "")])
        self.assertTrue(all(item["reason"] for item in summary["not_mapped"]))
        self.assertEqual(summary["not_found"], [{"instrument_id": "9100007", "symbol": "GONE", "tried": ["GONE"]},
                                                {"instrument_id": "9100008", "symbol": "VOD",
                                                 "tried": ["VOD.L", "VOD"]}])
        # One transaction per holding, under its conid; Hong Kong's 19 October bar was still trading.
        self.assertEqual([[row[:3] for row in batch] for batch in connection.batches], [
            [("9100001", "2026-10-15", 1_200_000.0), ("9100001", "2026-10-16", 1_350_000.0)],
            [("9100002", "2026-10-16", 18_000_000.0)],
            [("9100006", "2026-10-16", 9_000_000.0)]])
        self.assertEqual(connection.commits, 3)
        self.assertEqual({batch[0][4].split("?")[0] for batch in connection.batches},
                         {f"{market_data.YAHOO_CHART_URL.format(symbol=s)}" for s in ("SYNA", "0700.HK", "005930.KQ")})
        self.assertEqual({batch[0][5] for batch in connection.batches}, {"2026-10-19T06:30:00+00:00"})
        # Every capture is written before anything is parsed, and each volume is read back from bronze.
        events = bronze.events
        last_fetch = max(i for i, (kind, _) in enumerate(events) if kind == "fetch")
        reads = [(i, name) for i, (kind, name) in enumerate(events) if kind == "read"]
        self.assertEqual(len(reads), 3)
        for index, name in reads:
            self.assertGreater(index, last_fetch)
            self.assertLess(events.index(("captured", name)), index)
            self.assertTrue(name.startswith("yahoo-chart/2026/10/19/"))
        by_id = {item["instrument_id"]: item for item in summary["instruments"]}
        self.assertEqual(by_id["9100006"]["tried"], ["005930.KS", "005930.KQ"])
        self.assertEqual((by_id["9100006"]["outcome"], by_id["9100006"]["yahoo_symbol"]), ("loaded", "005930.KQ"))
        self.assertNotIn("error", by_id["9100006"])  # the refusal of .KS is not carried on
        self.assertEqual(by_id["9100002"]["skipped_days"], 1)
        self.assertEqual(by_id["9100002"]["window"], ["2025-10-19", "2026-10-19"])
        # Lineage: the captures used, and the rows written.
        self.assertEqual(sorted(name for name, _ in record.inputs), sorted(f"blob://{CONTAINER}/{n}" for _, n in reads))
        self.assertEqual(record.outputs, [(dv.TABLE, {"rows": 4})])
        self.assertEqual(summary["raw_capture"]["written"], 4)  # the US VOD answer is kept too, never used

    def test_one_request_per_symbol_and_window_and_a_failed_request_is_not_tried_further(self):
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        down = RuntimeError("Market data request failed after 3 attempts")
        down.__cause__ = OSError("connection reset")
        yahoo = _Yahoo(bronze, {"SYNA": _us("SYNA", [("2026-10-16", 100)]), "0700.HK": down})
        connection = _HoldingsConnection([_holding("9100001", "SYNA"), _holding("9100002", "SYNA"),
                                          _holding("9100003", "700", "HKD", exchange="SEHK")])
        summary, _ = self._run(connection, bronze, yahoo, today=FETCH_TODAY)
        # Two holdings share a Yahoo line: asked once. A network failure is not a 404, so "700" is not tried.
        self.assertEqual([call[0] for call in yahoo.calls], ["SYNA", "0700.HK"])
        self.assertEqual([batch[0][0] for batch in connection.batches], ["9100001", "9100002"])
        self.assertEqual((summary["status"], summary["requests"], summary["captured"]), ("failed", 2, 2))
        self.assertEqual([(item["instrument_id"], item["reason"][:12]) for item in summary["failed"]],
                         [("9100003", "RuntimeError")])

    def test_pacing_with_a_fake_clock_a_cap_and_a_time_budget(self):
        holdings = [_holding(f"910000{n}", f"SYN{n}") for n in range(1, 5)]
        loaded = {"9100001": "2026-10-18", "9100002": "2026-10-16"}
        charts = {f"SYN{n}": _us(f"SYN{n}", [("2026-10-16", 1000 * n)]) for n in range(1, 5)}

        # The cap: three requests, a fixed pause between two of them and none before the first.
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        clock = _Clock()
        yahoo = _Yahoo(bronze, charts, clock=clock)
        summary, _ = self._run(_HoldingsConnection(holdings, loaded), bronze, yahoo, today=FETCH_TODAY, clock=clock,
                               max_symbols=3, pace_seconds=1.5, budget_seconds=100)
        # The least recently loaded first: never loaded (by conid), then the oldest latest day.
        self.assertEqual(yahoo.calls, [("SYN3", YEAR_AGO, FETCH_TODAY), ("SYN4", YEAR_AGO, FETCH_TODAY),
                                       ("SYN2", date(2026, 10, 9), FETCH_TODAY)])
        self.assertEqual(clock.sleeps, [1.5, 1.5])
        self.assertEqual((summary["requests"], summary["deferred"], summary["deferred_because"], summary["status"]),
                         (3, ["9100001"], "the cap of 3 Yahoo requests per run", "success"))
        self.assertEqual((summary["max_symbols"], summary["pace_seconds"], summary["seconds"]), (3, 1.5, 4.5))

        # The time budget: no new request once it is spent; the rest wait for the next run.
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        clock = _Clock()
        yahoo = _Yahoo(bronze, charts, clock=clock)
        summary, _ = self._run(_HoldingsConnection(holdings, loaded), bronze, yahoo, today=FETCH_TODAY, clock=clock,
                               pace_seconds=1.0, budget_seconds=2.0)
        self.assertEqual([call[0] for call in yahoo.calls], ["SYN3", "SYN4"])
        self.assertEqual(clock.sleeps, [1.0])
        self.assertEqual((summary["deferred"], summary["deferred_because"]),
                         (["9100001", "9100002"], "the fetch time budget of 2 seconds"))
        self.assertEqual(summary["captured"], 2)

    def test_yahoo_asking_us_to_slow_down_stops_the_run_asking(self):
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        url = market_data._chart_url("SYN1", YEAR_AGO, FETCH_TODAY)
        slow_down = RuntimeError(f"Market data request failed after 3 attempts: {url}")
        slow_down.__cause__ = urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)
        yahoo = _Yahoo(bronze, {"SYN1": slow_down, "SYN2": _us("SYN2", [("2026-10-16", 20)])})
        summary, _ = self._run(_HoldingsConnection([_holding("9100001", "SYN1"), _holding("9100002", "SYN2")]),
                               bronze, yahoo, today=FETCH_TODAY)
        self.assertEqual([call[0] for call in yahoo.calls], ["SYN1"])  # not "SYN1" again, nor SYN2
        self.assertEqual((summary["status"], summary["failed"][0]["instrument_id"], summary["deferred"]),
                         ("failed", "9100001", ["9100002"]))
        self.assertIn("429", summary["deferred_because"])

    def test_a_response_raw_capture_did_not_keep_is_never_parsed(self):
        bronze = _CapturingBronze()
        bronze.fail_uploads = True
        _capture_into(self, bronze)
        yahoo = _Yahoo(bronze, {"SYN1": _us("SYN1", [("2026-10-16", 10)]), "SYN2": _us("SYN2", [("2026-10-16", 20)])})
        connection = _HoldingsConnection([_holding("9100001", "SYN1"), _holding("9100002", "SYN2")])
        with self.assertLogs(raw_capture.LOGGER, "WARNING"):
            summary, record = self._run(connection, bronze, yahoo, today=FETCH_TODAY)
        # Storage failed, so capture switched itself off: the answer is not parsed and the rest wait.
        self.assertEqual([call[0] for call in yahoo.calls], ["SYN1"])
        self.assertEqual((summary["status"], summary["fetched"], summary["captured"], summary["written"]),
                         ("failed", 1, 0, 0))
        self.assertEqual(summary["failed"][0]["instrument_id"], "9100001")
        self.assertIn("did not keep", summary["failed"][0]["reason"])
        self.assertEqual(summary["deferred"], ["9100002"])
        self.assertIn("raw capture is off for the rest of the run: OSError", summary["deferred_because"])
        self.assertEqual((connection.batches, bronze.reads, bronze.prefixes), ([], [], []))
        self.assertEqual(record.outputs, [(dv.TABLE, {"rows": 0})])

    def test_with_raw_capture_off_or_elsewhere_nothing_is_fetched(self):
        for container, account_url in (("", ""), ("", ACCOUNT_URL), (CONTAINER, ""), ("bronze-other", ACCOUNT_URL)):
            with self.subTest(container=container, account_url=account_url):
                bronze = _CapturingBronze()
                _capture_into(self, bronze, container, account_url)
                summary, _ = self._run(_HoldingsConnection([_holding("9100001", "SYN1"), _holding("9100002", "")]),
                                       bronze, _Exploding(), today=FETCH_TODAY)
                self.assertEqual((summary["status"], summary["requests"], summary["deferred"]),
                                 ("capture_off", 0, ["9100001"]))
                self.assertIn(f"MARKET_DATA_RAW_CONTAINER={CONTAINER}", summary["deferred_because"])
                self.assertEqual(len(summary["not_mapped"]), 1)
                self.assertEqual(bronze.events, [])

    def test_the_chart_fetch_is_market_datas_own_with_its_retries_and_its_capture(self):
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        body = _us("SYNA", [("2026-10-15", 700_000), ("2026-10-16", 800_000)])
        requests, retries = [], []

        def urlopen(request, timeout=None, context=None):
            requests.append((request.full_url, request.get_header("User-agent"), timeout))
            if "/chart/GONE?" in request.full_url:
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)
            if len(requests) == 1:
                raise OSError("connection reset")  # one retry, after market_data's backoff
            return _Response(body, {"Content-Type": "application/json"})

        connection = _HoldingsConnection([_holding("9100001", "SYNA"), _holding("9100002", "GONE")])
        clock = _Clock()
        with patch("urllib.request.urlopen", side_effect=urlopen), \
                patch.object(market_data.time, "sleep", side_effect=retries.append), \
                patch.object(raw_capture, "utc_now", return_value=FETCH_NOW):
            summary = dv.fetch_and_load(connection, bronze, sleep=clock.sleep, clock=clock, record=_Record())
        url = market_data._chart_url("SYNA", YEAR_AGO, FETCH_TODAY)
        self.assertEqual(requests[:2], [(url, market_data.USER_AGENT, dv.FETCH_TIMEOUT_SECONDS)] * 2)
        self.assertEqual(len(requests), 5)  # SYNA twice, GONE three times (market_data's attempts)
        self.assertEqual(retries, [1, 1, 2])  # market_data's backoff: SYNA's one retry, then GONE's two
        self.assertEqual(clock.sleeps, [dv.PACE_SECONDS])  # and the fetch's own pause between the two
        [name] = [name for kind, name in bronze.events if kind == "captured"]
        self.assertEqual(bronze.files[name][0], body)  # as received
        self.assertEqual(bronze.metadata[name]["source_url"], url)
        self.assertEqual(connection.batches, [[
            ("9100001", "2026-10-15", 700_000.0, "yahoo-chart", url, "2026-10-19T06:30:00+00:00"),
            ("9100001", "2026-10-16", 800_000.0, "yahoo-chart", url, "2026-10-19T06:30:00+00:00")]])
        self.assertEqual((summary["status"], summary["not_found"]),
                         ("success", [{"instrument_id": "9100002", "symbol": "GONE", "tried": ["GONE"]}]))
        # A response that came back but was not a usable chart (fetch_daily_closes's own check) is a refusal.
        self.assertTrue(dv._refused(RuntimeError("No market data returned for X")))
        reset = RuntimeError("failed")
        reset.__cause__ = urllib.error.URLError("timed out")
        self.assertFalse(dv._refused(reset))
        self.assertTrue(dv._refused(_not_found("https://query1.finance.yahoo.com/v8/finance/chart/GONE")))


# ---------------------------------------------------------------- synthetic French files


FIVE_FACTOR_CSV = (
    "This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using the 202608 CRSP database.\n"
    "The Tbill return is the simple daily rate that, over the number of trading days\n"
    "in the month, compounds to 1-month TBill rate from Ibbotson and Associates Inc.\n"
    "\n"
    "        ,Mkt-RF,SMB,HML,RMW,CMA,RF\n"
    "20260827,   0.52,  -0.31,   0.12,   0.05,  -0.08,  0.017\n"
    "20260828,  -1.10,   0.44, -99.99,   0.20,   0.11,  0.017\n"
    "20260831,   0.07,   0.02,   0.03,  -0.04,   0.01,  0.017\n"
    "\n"
    " Annual Factors: January-December \n"
    ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
    "  2025,  21.50,  -3.10,   1.20,   4.40,  -2.00,   5.10\n"
    "\n"
    "Copyright 2026 Eugene F. Fama and Kenneth R. French \xa9\n"
)
MOMENTUM_CSV = (
    "This file was created by CMPT_ME_PRIOR_RETS_DAILY using the 202608 CRSP database.\n"
    "It contains a momentum factor, constructed from six value-weight portfolios formed using\n"
    "independent sorts on size and prior return of NYSE, AMEX, and NASDAQ stocks.\n"
    "\n"
    "Missing data are indicated by -99.99.\n"
    "\n"
    "        ,Mom   \n"
    "20260827,   0.33\n"
    "20260828,  -0.71\n"
    "20260831, -99.99\n"
    "\n"
    "Copyright 2026 Eugene F. Fama and Kenneth R. French\n"
)


def french_zip(text: str, member: str = "F-F_Research_Data_5_Factors_2x3_daily.CSV") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, text.encode("latin-1"))
    return buffer.getvalue()


def download(url: str, body: bytes, *, last_modified: str | None = "Fri, 02 Oct 2026 21:40:12 GMT",
             requested_at: datetime = datetime(2026, 10, 17, 6, 0, 5, tzinfo=timezone.utc)) -> fr.Download:
    return fr.Download(url, body, requested_at, 200, "application/x-zip-compressed", last_modified)


class FrenchParsingTests(unittest.TestCase):
    def test_percent_becomes_a_fraction_and_only_daily_rows_are_kept(self):
        rows = fr.parse_factor_file(FIVE_FACTOR_CSV)
        self.assertEqual(sorted(rows), ["2026-08-27", "2026-08-28", "2026-08-31"])  # no annual 2025 row
        self.assertEqual(rows["2026-08-27"], {"Mkt-RF": 0.52 / 100, "SMB": -0.31 / 100, "HML": 0.12 / 100,
                                              "RMW": 0.05 / 100, "CMA": -0.08 / 100, "RF": 0.017 / 100})
        # The library's missing marker is skipped for that factor and day only.
        self.assertEqual(sorted(rows["2026-08-28"]), ["CMA", "Mkt-RF", "RF", "RMW", "SMB"])
        self.assertAlmostEqual(rows["2026-08-28"]["Mkt-RF"], -0.011)

    def test_the_momentum_file_is_stored_as_mom(self):
        rows = fr.parse_factor_file(MOMENTUM_CSV, momentum=True)
        self.assertEqual(rows, {"2026-08-27": {"MOM": 0.33 / 100}, "2026-08-28": {"MOM": -0.71 / 100}})
        # The developed and Asian files call the column WML.
        self.assertEqual(fr.parse_factor_file(MOMENTUM_CSV.replace(",Mom   ", ",WML"), momentum=True),
                         rows)

    def test_columns_the_table_does_not_hold_are_dropped(self):
        text = ",Mkt-RF,Size-Q1,RF\n20260827,0.52,9.0,0.017\n"
        self.assertEqual(fr.parse_factor_file(text), {"2026-08-27": {"Mkt-RF": 0.0052, "RF": 0.017 / 100}})
        with self.assertRaisesRegex(fama_french.FactorDataError, "none of the expected"):
            fr.parse_factor_file(",Size-Q1,Size-Q2\n20260827,1.0,2.0\n")
        with self.assertRaisesRegex(fama_french.FactorDataError, "no daily rows"):
            fr.parse_factor_file(FIVE_FACTOR_CSV.split("\n\n")[0])

    def test_the_zip_and_the_last_day_it_holds(self):
        text = fr.csv_text(french_zip(FIVE_FACTOR_CSV))
        self.assertEqual(text, FIVE_FACTOR_CSV)  # read as latin-1, as the library writes it
        self.assertEqual(fr.window_end(text), date(2026, 8, 31))
        self.assertIsNone(fr.window_end("no dates here\n2025,1.0\n"))
        with self.assertRaisesRegex(fama_french.FactorDataError, "Not a factor library zip"):
            fr.csv_text(b"<html>moved</html>")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("readme.txt", "no csv")
        with self.assertRaisesRegex(fama_french.FactorDataError, "no CSV inside"):
            fr.csv_text(buffer.getvalue())

    def test_the_vintage_is_the_publication_day_in_utc(self):
        url = fama_french.BASE_URL + "F-F_Momentum_Factor_daily_CSV.zip"
        self.assertEqual(fr.vintage_of(download(url, b"")), "2026-10-02")
        self.assertEqual(fr.vintage_of(download(url, b"", last_modified="Sat, 03 Oct 2026 08:00:00 +1300")),
                         "2026-10-02")
        for missing in (None, "", "yesterday"):
            with self.subTest(last_modified=missing):
                self.assertEqual(fr.vintage_of(download(url, b"", last_modified=missing)), "2026-10-17")


class _Uploads:
    """Records raw-capture uploads (test_raw_capture's fake)."""

    def __init__(self):
        self.calls: list[dict] = []

    def factory(self, account_url, container):
        return self.upload

    def upload(self, name, content, *, content_type, metadata):
        self.calls.append({"name": name, "content": content, "content_type": content_type, "metadata": metadata})
        return f"{ACCOUNT_URL}/{CONTAINER}/{name}"


def _capturing(test: unittest.TestCase) -> _Uploads:
    uploads = _Uploads()
    raw_capture.reset(raw_capture.RawCapture(container=CONTAINER, account_url=ACCOUNT_URL,
                                             uploader_factory=uploads.factory))
    test.addCleanup(raw_capture.reset)
    return uploads


class FactorCaptureTests(unittest.TestCase):
    def test_each_file_is_stored_as_received_with_its_licence(self):
        uploads = _capturing(self)
        _, five, _ = fama_french.REGIONS["us"]
        body = french_zip(FIVE_FACTOR_CSV)
        name = fr._capture(download(fama_french.BASE_URL + five, body), "us", five)
        digest = hashlib.sha256(body).hexdigest()
        # The data ended over a week before the request: finished history, filed once.
        self.assertEqual(name, f"kenneth-french/history/us_F-F_Research_Data_5_Factors_2x3_daily_CSV-{digest[:16]}.zip")
        [call] = uploads.calls
        self.assertEqual(call["content"], body)
        self.assertEqual({key: call["metadata"][key] for key in (
            "feed", "provider", "dataLicense", "source_url", "http_status", "sha256", "requested_at")}, {
            "feed": "kenneth-french", "provider": "Kenneth French data library",
            "dataLicense": "kenneth-french-terms-unchecked-internal-only", "source_url": fama_french.BASE_URL + five,
            "http_status": "200", "sha256": digest, "requested_at": "2026-10-17T06:00:05+00:00"})

    def test_a_recent_or_unreadable_file_goes_to_its_dated_folder(self):
        uploads = _capturing(self)
        recent = FIVE_FACTOR_CSV.replace("20260831", "20261014")
        self.assertTrue(fr._capture(download("https://x/a.zip", french_zip(recent)), "us", "a.zip")
                        .startswith("kenneth-french/2026/10/17/us_a-"))
        self.assertTrue(fr._capture(download("https://x/b.zip", b"<html>moved</html>"), "us", "b.zip")
                        .startswith("kenneth-french/2026/10/17/us_b-"))
        self.assertEqual(len(uploads.calls), 2)

    def test_only_the_feeds_with_a_known_licence_record_one(self):
        uploads = _capturing(self)
        raw_capture.capture_http("yahoo-chart", "Yahoo Finance chart API", url="https://q/chart", body=b"{}",
                                 key="SPY_2026-10-01_2026-10-16_1d", extension="json")
        self.assertNotIn("dataLicense", uploads.calls[0]["metadata"])
        self.assertIn("kenneth-french", raw_capture.FEEDS)
        self.assertEqual(set(raw_capture.FEED_LICENSES), {"kenneth-french"})
        self.assertTrue(set(raw_capture.FEED_LICENSES) <= raw_capture.FEEDS)
        self.assertTrue(raw_capture.FEED_LICENSES["kenneth-french"].endswith("internal-only"))

    def test_a_file_is_captured_before_it_is_parsed_and_a_bad_one_never_stops_the_run(self):
        uploads = _capturing(self)
        fetched = []

        def fetch(url):
            fetched.append(url)
            return download(url, b"<html>The page has moved</html>")

        connection = _Connection()
        record = _Record()
        summary = fr.load(connection, ["us", "developed"], fetch=fetch, record=record)
        self.assertEqual(fetched, [fama_french.BASE_URL + name for region in ("us", "developed")
                                   for name in fama_french.REGIONS[region][1:]])
        self.assertEqual(len(uploads.calls), 4)  # every response kept, though none could be read
        self.assertEqual(summary["failed"], ["us", "developed"])
        self.assertEqual(summary["written"], 0)
        for region in summary["regions"]:
            for item in region["files"]:
                self.assertTrue(item["captured"].startswith("kenneth-french/2026/10/17/"))
                self.assertIn("Not a factor library zip", item["error"])
        self.assertEqual(connection.queries, [])  # nothing to compare, nothing written
        self.assertEqual(record.outputs, [(fr.TABLE, {"rows": 0})])

    def test_the_stand_ins_are_asked_for_only_with_the_flag(self):
        def proxies(region):
            raise AssertionError("the ETF stand-ins were fetched without --etf-proxy")

        def fetch(url):
            raise fama_french.FactorDataError("offline")

        summary = fr.load(_Connection(), ["us"], fetch=fetch, proxies=proxies, record=_Record())
        self.assertNotIn("etf_proxy", summary["regions"][0])
        self.assertFalse(summary["etf_proxy"])


class _Response:
    def __init__(self, body: bytes, headers: dict[str, str]):
        self.body, self.status = body, 200
        self.headers = email.message.Message()
        for key, value in headers.items():
            self.headers[key] = value

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


class DownloadTests(unittest.TestCase):
    def test_the_file_and_its_headers_with_two_retries(self):
        body = french_zip(MOMENTUM_CSV)
        sleeps, requests = [], []

        def urlopen(request, timeout):
            requests.append((request.full_url, request.get_header("User-agent"), timeout))
            if len(requests) < 3:
                raise OSError("connection reset")
            return _Response(body, {"Content-Type": "application/x-zip-compressed",
                                    "Last-Modified": "Fri, 02 Oct 2026 21:40:12 GMT"})

        with patch("urllib.request.urlopen", side_effect=urlopen):
            got = fr.download("https://library.test/F-F_Momentum_Factor_daily_CSV.zip", sleep=sleeps.append)
        self.assertEqual((got.body, got.status, got.content_type, got.last_modified),
                         (body, 200, "application/x-zip-compressed", "Fri, 02 Oct 2026 21:40:12 GMT"))
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(requests[0], ("https://library.test/F-F_Momentum_Factor_daily_CSV.zip", fr.USER_AGENT, 60))
        self.assertEqual(fr.vintage_of(got), "2026-10-02")

    def test_a_file_that_never_arrives_is_a_factor_data_error(self):
        sleeps = []
        with patch("urllib.request.urlopen", side_effect=OSError("no route")):
            with self.assertRaisesRegex(fama_french.FactorDataError, "Could not download"):
                fr.download("https://library.test/x.zip", sleep=sleeps.append)
        self.assertEqual(sleeps, [1, 2])


# ---------------------------------------------------------------- the two jobs


class _Exploding:
    """A bronze reader, fetcher or stand-in source that must not be used."""

    container = CONTAINER

    def __call__(self, *args, **kwargs):
        raise AssertionError("fetched")

    def list(self, prefix):
        raise AssertionError("read bronze")

    def read(self, name):
        raise AssertionError("read bronze")


class _Undefined(Exception):
    sqlstate = "42P01"


class JobSwitchTests(unittest.TestCase):
    JOBS = (("load_daily_volumes", dv, VOLUMES_JOB), ("load_factor_returns", fr, FACTORS_JOB))

    def _run(self, script, function, argv, *, setting: str, **kwargs) -> tuple[int, list[dict]]:
        output = io.StringIO()
        with patch.dict(os.environ, {setting: "on", "DATABASE_URL": "postgresql://nobody@127.0.0.1:9/none",
                                     "PORTFOLIO_TENANT_ID": "00000000-0000-0000-0000-000000000001"}), \
                contextlib.redirect_stdout(output):
            code = getattr(script, function)(argv, **kwargs)
        return code, _lines(output.getvalue())

    def test_off_they_do_nothing_at_all(self):
        for name, module, _job in self.JOBS:
            script = _script(name)
            for value in ("", "off", "true"):
                with self.subTest(job=name, value=value), \
                        patch.dict(os.environ, {module.SETTING: value}), \
                        patch.object(script.lineage, "run_main", side_effect=AssertionError("lineage")), \
                        patch.object(script, "connect", side_effect=AssertionError("connected")), \
                        patch.object(script, "load_settings", side_effect=AssertionError("settings")), \
                        contextlib.redirect_stdout(io.StringIO()) as printed:
                    self.assertEqual(script.entry([]), 0)
                    self.assertEqual(script.main([]), 0)
                    self.assertEqual(_lines(printed.getvalue())[0]["status"], "off")

    def test_before_the_held_migration_they_stop_with_65_having_read_nothing(self):
        for name, module, _job in self.JOBS:
            script = _script(name)
            connection = _Connection(relation=None)
            extra = {"bronze": _Exploding()} if module is dv else {"fetch": _Exploding(), "proxies": _Exploding()}
            with self.subTest(job=name), patch.object(script, "connect", return_value=connection):
                code, lines = self._run(script, "main", ["--etf-proxy"] if module is fr else [],
                                        setting=module.SETTING, **extra)
            self.assertEqual(code, 65)
            self.assertEqual(module.EXIT_HELD, 65)
            self.assertEqual(lines, [{"status": "held", "table": module.TABLE, "exit_code": 65,
                                      "note": lines[0]["note"]}])
            self.assertEqual(connection.queries, [("SELECT to_regclass(?) AS relation", (module.RELATION,))])
            self.assertTrue(connection.closed)

    def test_a_table_dropped_during_the_run_is_held_too(self):
        for name, module, _job in self.JOBS:
            script = _script(name)
            with self.subTest(job=name), patch.object(script, "connect", return_value=_Connection()), \
                    patch.object(module, "load", side_effect=_Undefined("relation does not exist")):
                code, lines = self._run(script, "main", [], setting=module.SETTING,
                                        **({"bronze": _Exploding()} if module is dv else {"fetch": _Exploding()}))
            self.assertEqual((code, lines[-1]["status"]), (65, "held"))

    def test_on_each_runs_under_its_catalog_job_name(self):
        for name, module, job in self.JOBS:
            script = _script(name)
            seen = []

            def run_main(job_name, main):
                seen.append(job_name)
                return main()

            with self.subTest(job=name), patch.object(script.lineage, "run_main", side_effect=run_main), \
                    patch.object(script, "connect", return_value=_Connection(relation=None)):
                code, _ = self._run(script, "entry", [], setting=module.SETTING)
            self.assertEqual((code, seen), (65, [job]))
            tree = ast.parse((ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8"))
            names = [node.args[0].id for node in ast.walk(tree) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute) and node.func.attr == "run_main"]
            self.assertEqual((names, script.JOB), (["JOB"], job))

    def test_the_fetch_does_nothing_unless_its_setting_is_on(self):
        script = _script("load_daily_volumes")
        plain = None
        for argv, value in (([], "on"), (["--fetch"], ""), (["--fetch"], "off"), (["--fetch"], "true")):
            connection = _HoldingsConnection([_holding("9100001", "SYN1")])
            with self.subTest(argv=argv, value=value), patch.object(script, "connect", return_value=connection), \
                    patch.dict(os.environ, {dv.FETCH_SETTING: value}), \
                    patch.object(raw_capture, "set_job_run_id", side_effect=AssertionError("capture touched")):
                code, lines = self._run(script, "main", argv, setting=dv.SETTING, bronze=_Bronze(),
                                        fetcher=_Exploding())
            self.assertEqual(code, 0)
            self.assertFalse(connection.ran(dv.HOLDINGS_SQL))  # the holdings are not even read
            if not argv:  # on, but no --fetch (as Part A deployed the job): no fetch and no new key
                self.assertNotIn("fetch", lines[-1])
                plain = lines
                continue
            self.assertEqual(lines[-1]["fetch"]["status"], "off")
            self.assertIn(dv.FETCH_SETTING, lines[-1]["fetch"]["note"])
            # Otherwise the run is the load as it was: the same lines and summary.
            self.assertEqual(lines[:-1], plain[:-1])
            self.assertEqual({k: v for k, v in lines[-1].items() if k != "fetch"}, plain[-1])

    def test_the_held_exit_code_still_comes_before_any_fetch(self):
        script = _script("load_daily_volumes")
        connection = _HoldingsConnection([_holding("9100001", "SYN1")], relation=None)
        with patch.object(script, "connect", return_value=connection), patch.dict(os.environ, {dv.FETCH_SETTING: "on"}):
            code, lines = self._run(script, "main", ["--fetch"], setting=dv.SETTING, bronze=_Exploding(),
                                    fetcher=_Exploding())
        self.assertEqual((code, lines), (65, [{"status": "held", "table": dv.TABLE, "exit_code": 65,
                                               "note": lines[0]["note"]}]))
        self.assertEqual(connection.queries, [("SELECT to_regclass(?) AS relation", (dv.RELATION,))])
        # A table dropped while the fetch runs is held too.
        with patch.object(script, "connect", return_value=_HoldingsConnection()), \
                patch.dict(os.environ, {dv.FETCH_SETTING: "on"}), \
                patch.object(dv, "fetch_and_load", side_effect=_Undefined("relation does not exist")):
            code, lines = self._run(script, "main", ["--fetch"], setting=dv.SETTING, bronze=_Exploding())
        self.assertEqual((code, lines[-1]["status"]), (65, "held"))

    def test_on_it_prints_each_holding_then_the_load_then_a_summary_with_the_fetch(self):
        script = _script("load_daily_volumes")
        bronze = _CapturingBronze()
        _capture_into(self, bronze)
        yahoo = _Yahoo(bronze, {"SYN1": _us("SYN1", [("2026-10-16", 10)])})
        connection = _HoldingsConnection([_holding("9100001", "SYN1"), _holding("9100002", "SYN2.REC")],
                                         instruments=[{"instrument_id": "spy-settlement", "symbol": "SPY",
                                                       "currency": "USD"}])
        clock = _Clock()
        with patch.object(script, "connect", return_value=connection), patch.dict(os.environ, {dv.FETCH_SETTING: "on"}), \
                patch.object(raw_capture, "utc_now", return_value=FETCH_NOW):
            code, lines = self._run(script, "main", ["--fetch", "--max-symbols", "5"], setting=dv.SETTING,
                                    bronze=bronze, fetcher=yahoo, sleep=clock.sleep, clock=clock)
        self.assertEqual(code, 0, lines)
        self.assertEqual([(line["instrument_id"], line.get("outcome")) for line in lines[:-1]],
                         [("9100001", "loaded"), ("9100002", "not_mapped"), ("spy-settlement", None)])
        summary = lines[-1]
        # The benchmark had no capture (derived-daily keeps none), but the fetch did: not "no_captures".
        self.assertEqual((summary["status"], summary["written"], summary["without_captures"]),
                         ("success", 0, ["spy-settlement"]))
        self.assertEqual({key: summary["fetch"][key] for key in ("status", "fetched", "captured", "written",
                                                                 "max_symbols")},
                         {"status": "success", "fetched": 1, "captured": 1, "written": 1, "max_symbols": 5})
        self.assertEqual(summary["fetch"]["not_mapped"][0]["instrument_id"], "9100002")
        self.assertNotIn("instruments", summary["fetch"])
        self.assertEqual(connection.batches[0][0][:3], ("9100001", "2026-10-16", 10.0))
        # Raw capture off while the fetch is on: nothing fetched, and the run fails so it is seen.
        raw_capture.reset(raw_capture.RawCapture())
        with patch.object(script, "connect", return_value=_HoldingsConnection([_holding("9100001", "SYN1")])), \
                patch.dict(os.environ, {dv.FETCH_SETTING: "on"}):
            code, lines = self._run(script, "main", ["--fetch"], setting=dv.SETTING, bronze=_Bronze(),
                                    fetcher=_Exploding())
        self.assertEqual((code, lines[-1]["status"], lines[-1]["fetch"]["status"]), (1, "failed", "capture_off"))

    def test_the_arguments(self):
        script = _script("load_daily_volumes")
        args = script._arguments(["--from", "2026-01-02", "--to", "2026-03-31", "--instrument", "spy-settlement"])
        self.assertEqual((args.since, args.until, args.instrument, args.days),
                         (date(2026, 1, 2), date(2026, 3, 31), ["spy-settlement"], dv.DEFAULT_DAYS))
        self.assertEqual((args.fetch, args.max_symbols), (False, dv.DEFAULT_MAX_SYMBOLS))
        args = script._arguments(["--fetch", "--max-symbols", "5", "--instrument", "9100001", "--days", "1"])
        self.assertEqual((args.fetch, args.max_symbols, args.instrument, args.days), (True, 5, ["9100001"], 1))
        for bad in (["--to", "2026-03-31"], ["--from", "2026-03-31", "--to", "2026-01-01"], ["--from", "soon"],
                    ["--fetch", "--from", "2026-01-02"], ["--fetch", "--max-symbols", "0"]):
            with self.subTest(argv=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                script._arguments(bad)
        script = _script("load_factor_returns")
        args = script._arguments(["--region", "us", "--region", "developed"])
        self.assertEqual((args.region, args.etf_proxy), (["us", "developed"], False))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            script._arguments(["--region", "japan"])


class RegistrationTests(unittest.TestCase):
    """Registered as held everywhere a silver table is, and off everywhere a job is."""

    def test_catalog(self):
        for name, job in (("daily_volumes", VOLUMES_JOB), ("factor_returns", FACTORS_JOB)):
            with self.subTest(table=name):
                entry = CATALOG["postgres"][f"public.{name}"]
                self.assertEqual({key: entry[key] for key in ("kind", "layer", "target_schema", "source", "held")},
                                 {"kind": "table", "layer": "silver", "target_schema": "silver",
                                  "source": "market-data", "held": f"infra/medallion/drafts/{name}.sql"})
                self.assertTrue((ROOT / entry["held"]).is_file())
                self.assertTrue((ROOT / "infra" / "medallion" / "drafts" / f"{name}_undo.sql").is_file())
                self.assertFalse(list((ROOT / "migrations" / "postgres").glob(f"*_{name}.sql")))
                self.assertIn(job, CATALOG["jobs"])
                self.assertIn("does nothing unless", CATALOG["jobs"][job]["note"].lower())
                contract = (ROOT / "infra" / "medallion" / "contracts" / "silver" / f"{name}.yaml").read_text(
                    encoding="utf-8")
                self.assertIn(f"physicalName: silver.{name}", contract)
                self.assertIn("property: dataLicense", contract)
        self.assertEqual(CATALOG["jobs"][VOLUMES_JOB]["hops"], ["conform"])
        self.assertEqual(CATALOG["jobs"][FACTORS_JOB]["hops"], ["ingest", "conform"])

    def test_the_drafts_grant_nothing_and_each_undo_drops_only_its_own(self):
        drafts = ROOT / "infra" / "medallion" / "drafts"
        for name in ("daily_volumes", "factor_returns"):
            text = (drafts / f"{name}.sql").read_text(encoding="utf-8")
            undo = (drafts / f"{name}_undo.sql").read_text(encoding="utf-8")
            with self.subTest(draft=name):
                # Only the view copies the table's grants; bootstrap gives them.
                self.assertEqual(re.findall(r"^\s*GRANT\b", text, re.M | re.I), [])
                self.assertIn("FORCE ROW LEVEL SECURITY", text)
                self.assertIn("security_invoker = true", text)
                self.assertEqual(sorted(set(re.findall(r"DROP (?:TABLE|VIEW)(?: IF EXISTS)? (\w+\.\w+)", undo))),
                                 [f"public.{name}", f"silver.{name}"])

    def test_layer_quality_and_freshness_wait_for_the_tables(self):
        volumes = {v.table: v for v in layer_quality.VOLUMES}
        self.assertEqual((volumes["daily_volumes"].held, volumes["daily_volumes"].judged,
                          volumes["daily_volumes"].date_column), (True, True, "price_date"))
        self.assertEqual((volumes["factor_returns"].held, volumes["factor_returns"].judged,
                          volumes["factor_returns"].date_column), (True, False, "return_date"))
        rules = {rule.check_id: rule for rule in layer_freshness.RULES}
        for check_id, column in (("silver.daily_volumes", "price_date"), ("silver.factor_returns", "return_date")):
            with self.subTest(rule=check_id):
                self.assertTrue(rules[check_id].held)
                self.assertFalse(rules[check_id].required)
                self.assertIn(f"MAX({column})", rules[check_id].query)
        # Neither table is gold, so the publish gate knows neither.
        known = set(publish_gate.TABLES) | {t for tables in publish_gate.JOB_TABLES.values() for t in tables}
        self.assertFalse(known & {"silver.daily_volumes", "silver.factor_returns"})

    def test_each_job_runs_alone_off_and_outside_the_daily_window(self):
        window = (7 * 60 + 50, 9 * 60 + 25)  # 07:50 to 09:25 UTC
        for bicep, script, setting, job in (
                ("staging-daily-volumes-job.bicep", "load_daily_volumes.py", "dailyVolumesLoad", VOLUMES_JOB),
                ("staging-factor-returns-job.bicep", "load_factor_returns.py", "factorReturnsLoad", FACTORS_JOB)):
            text = (ROOT / "infra" / bicep).read_text(encoding="utf-8")
            with self.subTest(bicep=bicep):
                self.assertIn(f"param jobName string = '{job}'", text)
                self.assertIn(f"param {setting} string = 'off'", text)
                self.assertRegex(text, rf"args: \['scripts/{re.escape(script)}'(, '--[a-z-]+')*\]")
                self.assertIn("ibkr-platform@${imageDigest}", text)
                self.assertIn("{ name: 'IBKR_RUN_MIGRATIONS', value: 'false' }", text)
                self.assertNotIn("roleAssignments", text)
                self.assertNotIn("Microsoft.Authorization", text)
                self.assertIn("dataLicense:", text)
                minute, hour = (int(part) for part in re.search(r"cronExpression: '(\d+) (\d+) ", text).groups())
                timeout = int(re.search(r"replicaTimeout: (\d+)", text).group(1))
                start, end = hour * 60 + minute, hour * 60 + minute + timeout / 60
                self.assertTrue(end <= window[0] or start >= window[1], (start, end))
                # Its own job: no other job definition runs the script.
                others = [path.name for path in (ROOT / "infra").glob("*.bicep")
                          if path.name != bicep and script in path.read_text(encoding="utf-8")]
                self.assertEqual(others, [])
        # Both keep the raw copies of what they fetch themselves.
        self.assertIn("MARKET_DATA_RAW_CONTAINER", (ROOT / "infra" / "staging-daily-volumes-job.bicep").read_text())
        self.assertIn("MARKET_DATA_RAW_CONTAINER", (ROOT / "infra" / "staging-factor-returns-job.bicep").read_text())

    def test_the_volumes_job_fetches_only_when_switched_on_and_keeps_its_raw_copies_where_it_reads(self):
        text = (ROOT / "infra" / "staging-daily-volumes-job.bicep").read_text(encoding="utf-8")
        for line in ("param dailyVolumesFetch string = 'off'",
                     f"{{ name: '{dv.FETCH_SETTING}', value: dailyVolumesFetch }}",
                     "args: ['scripts/load_daily_volumes.py', '--fetch']",
                     f"param bronzeContainer string = '{dv.DEFAULT_CONTAINER}'",
                     f"{{ name: '{dv.CONTAINER_ENV}', value: bronzeContainer }}",
                     f"{{ name: '{raw_capture.CONTAINER_ENV}', value: bronzeContainer }}",
                     f"{{ name: '{raw_capture.ACCOUNT_URL_ENV}', value:"):
            with self.subTest(line=line):
                self.assertIn(line, text)
        budget = int(re.search(rf"{raw_capture.BUDGET_ENV}', value: '(\d+)'", text).group(1))
        self.assertGreater(budget, raw_capture.DEFAULT_BUDGET_SECONDS)
        # Run time: the fetch stops asking well inside the job's limit, leaving time to load.
        timeout = int(re.search(r"replicaTimeout: (\d+)", text).group(1))
        self.assertLessEqual(dv.FETCH_BUDGET_SECONDS + 3 * dv.FETCH_TIMEOUT_SECONDS + 3, timeout - 240)
        # The job's identity can already write there: the ingestion identity's role on the
        # bronze-market-data container itself (staging.bicep), so no role assignment is added.
        staging = (ROOT / "infra" / "staging.bicep").read_text(encoding="utf-8")
        block = re.search(r"resource ingestionBronzeMarketDataContributor [^\n]*\{(.*?)\n\}", staging, re.S).group(1)
        for line in ("scope: bronzeMarketDataContainer", "principalId: ingestionIdentity.properties.principalId",
                     "roleDefinitionId: storageBlobContributorRoleId"):
            with self.subTest(line=line):
                self.assertIn(line, block)
        self.assertRegex(staging, r"resource bronzeMarketDataContainer [^\n]*existing = \{\s*parent: blobService\s*"
                                  r"name: 'bronze-market-data'")
        self.assertIn("'Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe')", staging)
        self.assertIn("identityResourceId=<ingestion identity id>", (ROOT / "docs" / "PHASE3_DATA.md").read_text())

    def test_the_stand_ins_store_their_fund_histories_under_a_known_feed(self):
        self.assertIn(fr.PROXY_CAPTURE_FEED, raw_capture.FEEDS)
        self.assertEqual(fr.RAW_FEED, "kenneth-french")
        self.assertEqual(set(fr.FACTORS), {"Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "MOM"})
        draft = (ROOT / "infra" / "medallion" / "drafts" / "factor_returns.sql").read_text(encoding="utf-8")
        for factor in fr.FACTORS:
            self.assertIn(f"'{factor}'", draft)
        self.assertIn("'etf-proxy'", draft)


if __name__ == "__main__":
    unittest.main()
