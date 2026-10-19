"""Phase 3 data, part B: macro series and 13F holdings (docs/PHASE3_DATA_MACRO_13F.md). Offline.

No test here reads the network: every HTTP call goes to a fake, and urllib's opener is patched to
fail if anything reaches it.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ibkr_etl import macro_series as ms
from ibkr_etl import raw_capture, sec_13f

ROOT = Path(__file__).resolve().parents[1]
AGENT = "Test Operator ops@example.com"
NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location("phase3_" + name.replace(".py", ""), ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Captured:
    """Stands in for the process-wide RawCapture and records each call."""

    def __init__(self, events=None):
        self.calls = []
        self.events = events if events is not None else []
        self.job_run_id = ""

    def http(self, feed, provider, **kwargs):
        self.calls.append({"feed": feed, "provider": provider, **kwargs})
        self.events.append(("capture", feed))
        return f"{feed}/x"


class _NoNetwork(unittest.TestCase):
    """Any real urlopen fails the test."""

    def setUp(self):
        guard = patch("urllib.request.urlopen", side_effect=AssertionError("no network in tests"))
        guard.start()
        self.addCleanup(guard.stop)
        self.capture = _Captured()
        raw_capture.reset(self.capture)
        self.addCleanup(raw_capture.reset)


class _FakeConnection:
    def __init__(self, present=True):
        self.present = present
        self.statements = []
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        return SimpleNamespace(fetchone=lambda: {"present": self.present}, fetchall=lambda: [])

    def rollback(self):
        self.rollbacks += 1

    def commit(self):
        pass

    def close(self):
        self.closed = True


def _settings():
    return SimpleNamespace(database_url="postgresql://test/none", database_path=Path("/nonexistent"),
                           tenant_id="00000000-0000-0000-0000-000000000001")


@contextlib.contextmanager
def _environment(**values):
    with patch.dict(os.environ, {}, clear=False):
        for key in (ms.SETTING, ms.LICENSE_SETTING, sec_13f.SETTING, sec_13f.USER_AGENT_SETTING):
            os.environ.pop(key, None)
        os.environ.update(values)
        yield


# ---------------------------------------------------------------- FRED


FRED_CSV = "DATE,DGS10\n2026-10-12,.\n2026-10-13,4.05\n2026-10-14,4.10\n2026-10-15,\n2026-10-16,4.12\n"


class FredCsvTests(unittest.TestCase):
    def test_dot_and_empty_days_are_skipped_and_counted(self):
        observations, skipped = ms.parse_fred_csv(FRED_CSV.encode(), "DGS10")
        self.assertEqual(observations, [("2026-10-13", 4.05), ("2026-10-14", 4.10), ("2026-10-16", 4.12)])
        self.assertEqual(skipped, 2)

    def test_both_header_styles_and_a_byte_order_mark(self):
        text = "﻿observation_date,DTWEXBGS\n2026-10-16,121.5\n"
        self.assertEqual(ms.parse_fred_csv(text.encode("utf-8"), "DTWEXBGS"), ([("2026-10-16", 121.5)], 0))
        self.assertEqual(ms.parse_fred_csv("DATE,DTWEXBGS\n2026-10-16,121.5\n", "DTWEXBGS")[0],
                         [("2026-10-16", 121.5)])

    def test_out_of_order_rows_come_back_sorted_and_equal_repeats_are_one(self):
        text = "DATE,DGS2\n2026-10-14,3.6\n2026-10-13,3.5\n2026-10-14,3.6\n"
        self.assertEqual(ms.parse_fred_csv(text, "DGS2")[0], [("2026-10-13", 3.5), ("2026-10-14", 3.6)])

    def test_anything_unexpected_rejects_the_whole_response(self):
        bad = {
            "a web page": "<!DOCTYPE html><html>error</html>",
            "another series": "DATE,DGS2\n2026-10-16,3.5\n",
            "a third column": "DATE,DGS10\n2026-10-16,4.1,9\n",
            "a word": "DATE,DGS10\n2026-10-16,n/a\n",
            "not finite": "DATE,DGS10\n2026-10-16,nan\n",
            "infinite": "DATE,DGS10\n2026-10-16,inf\n",
            "a US date": "DATE,DGS10\n10/16/2026,4.1\n",
            "an impossible date": "DATE,DGS10\n2026-02-30,4.1\n",
            "two values for a date": "DATE,DGS10\n2026-10-16,4.1\n2026-10-16,4.2\n",
            "nothing": "",
        }
        for label, text in bad.items():
            with self.subTest(label), self.assertRaises(ValueError):
                ms.parse_fred_csv(text, "DGS10")

    def test_urls_only_for_reviewed_fred_series_never_ice_bofa(self):
        url = ms.fred_url("DGS10", date(2015, 1, 1))
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://fred.stlouisfed.org/graph/fredgraph.csv")
        self.assertEqual(urllib.parse.parse_qs(parts.query), {"id": ["DGS10"], "cosd": ["2015-01-01"]})
        for refused in ("BAMLH0A0HYM2", "baml_anything", "UNRATE", "USDCNH"):
            with self.subTest(refused), self.assertRaises(ValueError):
                ms.fred_url(refused)
        self.assertFalse(any(s.series_id.startswith("BAML") for s in ms.SERIES))

    def test_the_registry_is_the_design_list_with_hibor_left_in_risk_free_rates(self):
        self.assertEqual([s.series_id for s in ms.SERIES],
                         ["DGS10", "DGS2", "DTWEXBGS", "DCOILBRENTEU", "VIXCLS", "USDCNH"])
        self.assertFalse(any("HIBOR" in s.series_id for s in ms.SERIES))
        for series in ms.SERIES:
            self.assertIn(series.units, ms.UNITS)
            self.assertEqual(series.source, "yahoo-chart" if series.series_id == "USDCNH" else "fred")


class LicenceTests(unittest.TestCase):
    def test_vix_is_left_out_until_its_licence_is_confirmed(self):
        chosen, left_out = ms.series_to_load({})
        self.assertNotIn("VIXCLS", [s.series_id for s in chosen])
        self.assertEqual([item["series_id"] for item in left_out], ["VIXCLS"])
        self.assertIn(ms.LICENSE_SETTING, left_out[0]["reason"])
        chosen, left_out = ms.series_to_load({ms.LICENSE_SETTING: " vixcls , "})
        self.assertIn("VIXCLS", [s.series_id for s in chosen])
        self.assertEqual(left_out, [])

    def test_each_series_names_its_licence_and_only_public_ones_load_by_default(self):
        for series in ms.SERIES:
            with self.subTest(series.series_id):
                self.assertTrue(series.licence and series.citation and series.notes)
                if series.source == "fred" and series.public:
                    self.assertEqual(series.licence, ms.PUBLIC_DOMAIN)
        self.assertEqual(ms.BY_ID["VIXCLS"].licence, ms.CBOE_COPYRIGHT)
        self.assertIn("Copyright", ms.BY_ID["VIXCLS"].notes)
        self.assertEqual(ms.BY_ID["USDCNH"].licence, "yfinance-internal-only")


class VintageTests(unittest.TestCase):
    def test_only_new_dates_and_revised_values_are_written(self):
        latest = {"2026-10-13": (4.05, "2026-10-14"), "2026-10-14": (4.10, "2026-10-15")}
        observations = [("2026-10-13", 4.05), ("2026-10-14", 4.11), ("2026-10-15", 4.2)]
        self.assertEqual(ms.changed(observations, latest), [("2026-10-14", 4.11), ("2026-10-15", 4.2)])
        self.assertEqual(ms.changed(observations[:1], latest), [])

    def test_values_are_checked_against_their_units_range_inside_the_window(self):
        series = ms.BY_ID["DGS10"]
        kept = ms.checked(series, [("2014-12-31", 99.0), ("2015-01-02", 2.1), ("2026-10-20", 99.0)],
                          date(2015, 1, 1), date(2026, 10, 19))
        self.assertEqual(kept, [("2015-01-02", 2.1)])
        with self.assertRaises(ValueError):
            ms.checked(series, [("2026-10-16", 410.0)], date(2015, 1, 1), date(2026, 10, 19))
        with self.assertRaises(ValueError):
            ms.checked(ms.BY_ID["USDCNH"], [("2026-10-16", 0.14)], date(2015, 1, 1), date(2026, 10, 19))

    def test_the_daily_window(self):
        at = lambda h, m: datetime(2026, 10, 19, h, m, tzinfo=timezone.utc)  # noqa: E731
        self.assertFalse(ms.in_daily_window(at(7, 49)))
        self.assertTrue(ms.in_daily_window(at(7, 50)))
        self.assertTrue(ms.in_daily_window(at(9, 25)))
        self.assertFalse(ms.in_daily_window(at(9, 26)))
        self.assertFalse(ms.in_daily_window(at(22, 30)))


class FredFetchTests(_NoNetwork):
    def test_the_raw_csv_is_captured_first_with_its_licence(self):
        events = self.capture.events

        def getter(url):
            events.append(("get", url))
            return FRED_CSV.encode()

        original = ms.parse_fred_csv

        def parse(body, series_id):
            events.append(("parse", series_id))
            return original(body, series_id)

        with patch.object(ms, "parse_fred_csv", side_effect=parse):
            fetched = ms.fetch_fred(ms.BY_ID["DGS10"], date(2015, 1, 1), getter=getter)
        self.assertEqual([event[0] for event in events], ["get", "capture", "parse"])
        self.assertEqual(len(fetched["observations"]), 3)
        call = self.capture.calls[0]
        self.assertEqual(call["feed"], "fred-series")
        self.assertEqual(call["extension"], "csv")
        self.assertEqual(call["body"], FRED_CSV.encode())
        self.assertIn(ms.PUBLIC_DOMAIN, call["licence"])
        self.assertIn("H.15", call["licence"])
        self.assertEqual(call["key"], "DGS10_2015-01-01")

    def test_a_response_that_does_not_parse_is_still_captured(self):
        with self.assertRaises(ValueError):
            ms.fetch_fred(ms.BY_ID["DGS2"], date(2015, 1, 1), getter=lambda url: b"<html>maintenance</html>")
        self.assertEqual([c["feed"] for c in self.capture.calls], ["fred-series"])
        self.assertEqual(self.capture.calls[0]["body"], b"<html>maintenance</html>")

    def test_retries_server_errors_with_backoff_and_refuses_a_client_error(self):
        sleeps = []
        responses = [urllib.error.HTTPError("u", 503, "busy", {}, None), _Response(b"DATE,DGS2\n")]

        def opener(request, timeout=None, context=None):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("urllib.request.urlopen", side_effect=opener):
            body = ms._http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2", sleep=sleeps.append)
        self.assertEqual((bytes(body), sleeps), (b"DATE,DGS2\n", [2]))
        with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 400, "bad", {}, None)), \
                self.assertRaises(RuntimeError):
            ms._http_get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2", sleep=sleeps.append)
        self.assertEqual(sleeps, [2])


class _Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/csv"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chart(closes, currency="CNH"):
    stamps = [int(datetime(2026, 10, d, 21, 0, tzinfo=timezone.utc).timestamp()) for d in (13, 14, 15)]
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"currency": currency, "exchangeTimezoneName": "Europe/London"},
        "timestamp": stamps, "indicators": {"quote": [{"close": closes}]}}]}}).encode()


class CnhTests(_NoNetwork):
    def test_usd_cnh_comes_through_market_data_and_is_captured_as_yahoo_chart(self):
        with patch("urllib.request.urlopen", return_value=_Response(_chart([7.12, None, 7.15]))):
            fetched = ms.fetch_cnh(date(2026, 10, 13), date(2026, 10, 15))
        self.assertEqual(fetched["observations"], [("2026-10-13", 7.12), ("2026-10-15", 7.15)])
        self.assertIn("CNH%3DX", fetched["url"])
        self.assertEqual([c["feed"] for c in self.capture.calls], ["yahoo-chart"])
        self.assertEqual(ms.checked(ms.BY_ID["USDCNH"], fetched["observations"], date(2026, 10, 13),
                                    date(2026, 10, 15)), fetched["observations"])

    def test_a_chart_in_another_currency_is_refused(self):
        with patch("urllib.request.urlopen", return_value=_Response(_chart([7.1, 7.1, 7.1], currency="USD"))), \
                self.assertRaises(RuntimeError):
            ms.fetch_cnh(date(2026, 10, 13), date(2026, 10, 15))


class MacroLoadTests(_NoNetwork):
    def test_one_failing_series_never_stops_the_others_and_nothing_half_written(self):
        connection = _FakeConnection()
        result = ms.load(connection, today=date(2026, 10, 19), environ={},
                         getter=lambda url: (_ for _ in ()).throw(RuntimeError("FRED down")),
                         cnh_fetch=lambda instrument, start, end: (_ for _ in ()).throw(RuntimeError("Yahoo down")))
        self.assertEqual(sorted(f["series_id"] for f in result["failed"]),
                         ["DCOILBRENTEU", "DGS10", "DGS2", "DTWEXBGS", "USDCNH"])
        self.assertEqual((result["series"], result["rows_stored"]), ([], 0))
        self.assertEqual([item["series_id"] for item in result["not_loaded_for_licence"]], ["VIXCLS"])
        self.assertEqual(connection.rollbacks, 5)
        self.assertEqual(result["vintage"], "2026-10-19")

    def test_an_empty_response_is_a_failure_and_the_window_stops_the_rest(self):
        calls = iter([False, True, True, True, True])
        result = ms.load(_FakeConnection(), today=date(2026, 10, 19), environ={},
                         getter=lambda url: b"DATE,DGS10\n2026-10-16,.\n", stop=lambda: next(calls))
        self.assertEqual([f["series_id"] for f in result["failed"]], ["DGS10"])
        self.assertIn("no observations", result["failed"][0]["error"])
        self.assertEqual(len(result["not_started"]), 4)

    def test_cnh_asks_for_completed_days_only(self):
        seen = []
        ms.load(_FakeConnection(), start=date(2026, 10, 1), today=date(2026, 10, 19), environ={},
                getter=lambda url: (_ for _ in ()).throw(RuntimeError("skip")),
                cnh_fetch=lambda instrument, start, end: seen.append((instrument.symbol, start, end)) or ("u", []))
        self.assertEqual(seen, [("CNH=X", date(2026, 10, 1), date(2026, 10, 18))])


# ---------------------------------------------------------------- 13F parsing


def _line(cusip, issuer, value, shares, kind="SH", put_call=None):
    option = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (f"<infoTable><nameOfIssuer>{issuer}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
            f"<cusip>{cusip}</cusip><value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>"
            f"<sshPrnamtType>{kind}</sshPrnamtType></shrsOrPrnAmt>{option}"
            "<investmentDiscretion>SOLE</investmentDiscretion><votingAuthority><Sole>1</Sole><Shared>0</Shared>"
            "<None>0</None></votingAuthority></infoTable>")


def information_table(*lines: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><informationTable xmlns="{NS}">'
            + "".join(lines) + "</informationTable>").encode()


TABLE = information_table(
    _line("037833100", "APPLE INC", 150, 1000),
    _line("037833100", "APPLE INC", 75, 500),  # the same security from another manager: summed
    _line("037833100", "APPLE INC", 9, 100, put_call="Call"),
    _line("037833100", "APPLE INC", 4, 50, kind="PRN"),  # PRN beside SH on the same key: left out
    _line("912810TM0", "UNITED STATES TREAS BD", 990, 1000000, kind="PRN"),
    _line("94918104", "MICROSOFT CORP", 30, 100),  # 8 digits whose zero-padded check digit is wrong: dropped
    _line("38259P508", "GOOGLE INC", 20, 10, put_call="Put"),
    _line("BAD", "NOT A CUSIP", 1, 1),
    _line("88160R101", "TESLA INC", "1,0", 5),
)


class InformationTableTests(unittest.TestCase):
    def test_thousands_before_2023_and_dollars_after_by_filing_date(self):
        self.assertEqual(sec_13f.value_multiplier("2022-11-14"), 1000)
        self.assertEqual(sec_13f.value_multiplier("2023-01-02"), 1000)
        self.assertEqual(sec_13f.value_multiplier("2023-01-03"), 1)
        before, details = sec_13f.parse_information_table(TABLE, filed_at="2022-11-14")
        after, later = sec_13f.parse_information_table(TABLE, filed_at="2023-02-14")
        self.assertEqual((details["value_unit"], later["value_unit"]), ("thousands", "dollars"))
        apple = {(r["cusip"], r["put_call"]): r for r in before}[("037833100", "")]
        self.assertEqual((apple["shares"], apple["value_usd"]), (1500.0, 225000.0))
        apple_after = {(r["cusip"], r["put_call"]): r for r in after}[("037833100", "")]
        self.assertEqual(apple_after["value_usd"], 225.0)

    def test_share_types_put_call_and_line_problems(self):
        rows, details = sec_13f.parse_information_table(TABLE, filed_at="2024-05-15")
        keyed = {(r["cusip"], r["put_call"]): r for r in rows}
        self.assertEqual(sorted(keyed), [("037833100", ""), ("037833100", "CALL"), ("38259P508", "PUT"),
                                         ("912810TM0", "")])
        self.assertEqual(keyed[("037833100", "")]["shares_type"], "SH")
        self.assertEqual(keyed[("912810TM0", "")]["shares_type"], "PRN")
        self.assertEqual(keyed[("912810TM0", "")]["shares"], 1000000.0)
        self.assertEqual(keyed[("037833100", "CALL")]["shares"], 100.0)
        self.assertEqual(details["lines"], 9)
        self.assertEqual(details["prn_left_out_beside_sh"], 1)
        self.assertEqual(details["cusip_invalid"], 2)  # BAD, and 94918104 whose zero-padded check digit fails
        self.assertEqual(details["value_invalid"], 1)
        self.assertEqual(details["rows"], 4)

    def test_a_cusip_that_lost_its_leading_zero_is_repaired_only_when_its_check_digit_agrees(self):
        rows, details = sec_13f.parse_information_table(
            information_table(_line("37833100", "APPLE INC", 1, 1)), filed_at="2024-05-15")
        self.assertEqual(([r["cusip"] for r in rows], details["cusip_repaired"]), (["037833100"], 1))
        self.assertEqual(sec_13f.normalise_cusip(" 0378 33100 "), ("037833100", False))
        self.assertEqual(sec_13f.normalise_cusip("38259p508"), ("38259P508", False))
        self.assertEqual(sec_13f.normalise_cusip("37833101"), (None, False))

    def test_a_unit_that_looks_wrong_is_reported_not_changed(self):
        lines = [_line(f"00000000{i}", f"ISSUER {i}", 5, 1000) for i in range(5)]
        rows, details = sec_13f.parse_information_table(information_table(*lines), filed_at="2024-05-15")
        self.assertEqual(details["median_value_per_share"], 0.005)
        self.assertIn("unit_warning", details)
        self.assertEqual(rows[0]["value_usd"], 5.0)
        rows, details = sec_13f.parse_information_table(information_table(*lines), filed_at="2022-05-15")
        self.assertNotIn("unit_warning", details)

    def test_documents_that_are_not_information_tables(self):
        with self.assertRaises(sec_13f.FilingSkipped):
            sec_13f.parse_information_table(b"<edgarSubmission><x/></edgarSubmission>", filed_at="2024-05-15")
        with self.assertRaises(ValueError):
            sec_13f.parse_information_table(b'<!DOCTYPE x [<!ENTITY a "b">]><informationTable/>',
                                            filed_at="2024-05-15")
        self.assertEqual(sec_13f.parse_information_table(information_table(), filed_at="2024-05-15")[0], [])


def cover(period="09-30-2024", amendment=None, report_type="13F HOLDINGS REPORT") -> bytes:
    info = (f"<isAmendment>true</isAmendment><amendmentInfo><amendmentType>{amendment}</amendmentType>"
            "</amendmentInfo>" if amendment else "<isAmendment>false</isAmendment>")
    return (f'<edgarSubmission xmlns="http://www.sec.gov/edgar/thirteenffiler"><headerData>'
            "<submissionType>13F-HR</submissionType></headerData><formData><coverPage>"
            f"<reportCalendarOrQuarter>{period}</reportCalendarOrQuarter>{info}"
            f"<reportType>{report_type}</reportType></coverPage><summaryPage><tableEntryTotal>4</tableEntryTotal>"
            "<tableValueTotal>1234</tableValueTotal></summaryPage></formData></edgarSubmission>").encode()


class CoverAndAmendmentTests(unittest.TestCase):
    def test_the_cover_page(self):
        found = sec_13f.parse_cover(cover())
        self.assertEqual((found["period"], found["is_amendment"], found["amendment_type"]),
                         ("2024-09-30", False, ""))
        self.assertEqual((found["table_entry_total"], found["table_value_total"]), (4, 1234.0))
        found = sec_13f.parse_cover(cover(amendment="New Holdings"))
        self.assertEqual((found["is_amendment"], found["amendment_type"]), (True, "NEW HOLDINGS"))
        with self.assertRaises(sec_13f.FilingSkipped):
            sec_13f.parse_cover(b"<edgarSubmission><formData/></edgarSubmission>")

    def test_amendment_kinds(self):
        self.assertEqual(sec_13f.amendment_kind("13F-HR", {}), sec_13f.ORIGINAL)
        self.assertEqual(sec_13f.amendment_kind("13F-HR/A", {"amendment_type": "RESTATEMENT"}), sec_13f.RESTATEMENT)
        self.assertEqual(sec_13f.amendment_kind("13F-HR/A", {"amendment_type": "NEW HOLDINGS"}),
                         sec_13f.NEW_HOLDINGS)
        with self.assertRaises(sec_13f.FilingSkipped):
            sec_13f.amendment_kind("13F-HR/A", {"amendment_type": ""})

    def test_a_new_holdings_amendment_completes_the_report_before_it(self):
        base = [{"cusip": "037833100", "put_call": "", "shares_type": "SH", "shares": 1500.0, "value_usd": 9.0,
                 "issuer": "APPLE INC"},
                {"cusip": "912810TM0", "put_call": "", "shares_type": "PRN", "shares": 10.0, "value_usd": 1.0,
                 "issuer": "UST"}]
        added = [{"cusip": "037833100", "put_call": "", "shares_type": "SH", "shares": 500.0, "value_usd": 3.0,
                  "issuer": "APPLE INC"},
                 {"cusip": "912810TM0", "put_call": "", "shares_type": "SH", "shares": 99.0, "value_usd": 99.0,
                  "issuer": "UST"},
                 {"cusip": "594918104", "put_call": "", "shares_type": "SH", "shares": 7.0, "value_usd": 2.0,
                  "issuer": "MICROSOFT CORP"}]
        rows = {(r["cusip"], r["put_call"]): r for r in sec_13f.compose_new_holdings(base, added)}
        self.assertEqual((rows[("037833100", "")]["shares"], rows[("037833100", "")]["value_usd"]), (2000.0, 12.0))
        self.assertEqual(rows[("912810TM0", "")]["shares"], 10.0)  # another share type is left out
        self.assertEqual(rows[("594918104", "")]["shares"], 7.0)
        self.assertEqual(base[0]["shares"], 1500.0, "the base rows are not changed in place")

    def test_submissions_and_the_quarter_window(self):
        body = json.dumps({"name": "TEST  FILER LLC", "filings": {
            "recent": {"accessionNumber": ["0000000001-24-000003", "0000000001-24-000002", "0000000001-24-000001",
                                           "0000000001-23-000009", "bad"],
                       "form": ["13F-HR/A", "13F-NT", "13F-HR", "13F-HR", "13F-HR"],
                       "filingDate": ["2024-12-01", "2024-11-14", "2024-11-14", "2023-11-14", "2024-11-14"],
                       "reportDate": ["2024-09-30", "2024-09-30", "2024-09-30", "2023-09-30", "2024-09-30"]},
            "files": [{"name": "CIK0000000001-submissions-001.json", "filingFrom": "2001-01-01",
                       "filingTo": "2023-12-31"}]}})
        found = sec_13f.parse_submissions(body)
        self.assertEqual(found["name"], "TEST FILER LLC")
        self.assertEqual(found["pages"][0]["to"], "2023-12-31")
        chosen = sec_13f.in_window(found["filings"], since="2024-06-30")
        self.assertEqual([f["accession"] for f in chosen], ["0000000001-24-000001", "0000000001-24-000003"])

    def test_the_filing_index_names_the_cover_and_the_candidates(self):
        index = json.dumps({"directory": {"item": [{"name": "primary_doc.xml"}, {"name": "infotable.xml"},
                                                   {"name": "0000000001-24-000001.txt"}]}})
        self.assertEqual(sec_13f.parse_filing_index(index), ("primary_doc.xml", ["infotable.xml"]))
        with self.assertRaises(sec_13f.FilingSkipped):
            sec_13f.parse_filing_index(json.dumps({"directory": {"item": [{"name": "infotable.xml"}]}}))

    def test_quarter_ends(self):
        self.assertEqual(sec_13f.quarter_ends(date(2026, 11, 20), 4),
                         ["2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31"])
        self.assertEqual(sec_13f.quarter_ends(date(2026, 3, 31), 1), ["2026-03-31"])
        self.assertEqual(sec_13f.quarter_ends(date(2026, 2, 20), 2), ["2025-12-31", "2025-09-30"])
        self.assertTrue(sec_13f.quarter_end("2024-12-31"))
        self.assertFalse(sec_13f.quarter_end("2024-12-30"))


# ---------------------------------------------------------------- CUSIP from ISIN


class CusipFromIsinTests(unittest.TestCase):
    def test_a_us_isin_holds_the_cusip(self):
        self.assertEqual(sec_13f.cusip_from_isin("US0378331005"), "037833100")  # Apple
        self.assertEqual(sec_13f.cusip_from_isin(" us5949181045 "), "594918104")  # Microsoft
        self.assertEqual(sec_13f.cusip_from_isin("US02079K3059"), "02079K305")  # Alphabet C

    def test_anything_else_is_none(self):
        for value in ("GB0002634946", "HK0000069689", "KYG017191142", "US0378331006", "US037833100",
                      "", None, 12345):
            with self.subTest(value=value):
                self.assertIsNone(sec_13f.cusip_from_isin(value))

    def test_check_digits(self):
        self.assertEqual(sec_13f.cusip_check_digit("03783310"), 0)
        self.assertEqual(sec_13f.cusip_check_digit("59491810"), 4)
        self.assertEqual(sec_13f.cusip_check_digit("38259P50"), 8)
        self.assertEqual(sec_13f.isin_check_digit("US037833100"), 5)
        self.assertEqual(sec_13f.isin_check_digit("GB000263494"), 6)


# ---------------------------------------------------------------- SEC requests


class UserAgentTests(unittest.TestCase):
    def test_refused_without_a_name_and_a_contact_address(self):
        for value in ("", "   ", "ops@example.com", "Test Operator", "Test Operator ops@example",
                      "Test Operator\nops@example.com", "x " * 100 + "ops@example.com"):
            with self.subTest(value=value), self.assertRaises(sec_13f.UserAgentMissing):
                sec_13f.user_agent({sec_13f.USER_AGENT_SETTING: value})
        with self.assertRaises(sec_13f.UserAgentMissing):
            sec_13f.user_agent({})

    def test_a_usable_one_is_kept_on_one_line(self):
        self.assertEqual(sec_13f.user_agent({sec_13f.USER_AGENT_SETTING: "  Test   Operator ops@example.com "}), AGENT)
        with self.assertRaises(sec_13f.UserAgentMissing):
            sec_13f.SecClient("")

    def test_no_contact_address_is_written_in_the_code_or_the_jobs(self):
        email = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
        for path in (ROOT / "src/ibkr_etl/sec_13f.py", ROOT / "src/ibkr_etl/macro_series.py",
                     ROOT / "scripts/load_institutional_holdings.py", ROOT / "scripts/load_macro_series.py",
                     ROOT / "src/ibkr_etl/data/sec_13f_filers.json"):
            with self.subTest(path=path.name):
                self.assertEqual(email.findall(path.read_text(encoding="utf-8")), [])
        for name in ("staging-macro-series-job.bicep", "staging-institutional-holdings-job.bicep"):
            self.assertEqual(email.findall((ROOT / "infra" / name).read_text(encoding="utf-8")), [])


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class _Opener:
    """Answers from a list (bytes or an exception), records each request and when it started."""

    def __init__(self, clock, answers, duration=0.01):
        self.clock, self.answers, self.duration = clock, list(answers), duration
        self.requests, self.starts = [], []

    def __call__(self, request, timeout):
        self.requests.append(request)
        self.starts.append(self.clock.now)
        self.clock.now += self.duration
        answer = self.answers.pop(0) if self.answers else b"{}"
        if isinstance(answer, BaseException):
            raise answer
        return answer


def _http_error(code, retry_after=None):
    return urllib.error.HTTPError("https://www.sec.gov/x", code, "error",
                                  {"Retry-After": retry_after} if retry_after is not None else {}, None)


class PacingTests(_NoNetwork):
    def test_request_starts_are_spaced_well_under_secs_limit(self):
        self.assertLess(sec_13f.MAX_REQUESTS_PER_SECOND, sec_13f.SEC_LIMIT_PER_SECOND / 2)
        clock = _Clock()
        opener = _Opener(clock, [b"{}"] * 12)
        client = sec_13f.SecClient(AGENT, opener=opener, clock=clock, sleep=clock.sleep)
        for i in range(12):
            client.get(f"https://data.sec.gov/x{i}", key=f"k{i}", extension="json", history=False)
        gaps = [b - a for a, b in zip(opener.starts, opener.starts[1:])]
        self.assertTrue(all(gap >= 0.25 - 1e-9 for gap in gaps), gaps)
        for start in opener.starts:  # never more than 4 starts in any one second
            self.assertLessEqual(sum(1 for s in opener.starts if start <= s < start + 1.0), 4)
        self.assertAlmostEqual(client.pacer.waited, 11 * (0.25 - 0.01), places=6)
        self.assertEqual(client.requests, 12)

    def test_the_pacer_refuses_a_rate_at_or_over_secs_limit(self):
        for rate in (0, -1, 10, 25):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                sec_13f.Pacer(rate)
        clock = _Clock()
        pacer = sec_13f.Pacer(2.0, clock=clock, sleep=clock.sleep)
        pacer.wait()
        clock.now += 0.1
        pacer.wait()
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 0.4, places=9)

    def test_every_request_declares_the_user_agent_and_each_answer_is_captured_with_its_licence(self):
        clock = _Clock()
        opener = _Opener(clock, [b'{"name": "x"}'])
        client = sec_13f.SecClient(AGENT, opener=opener, clock=clock, sleep=clock.sleep)
        body = client.get("https://data.sec.gov/submissions/CIK0000000001.json", key="submissions_CIK0000000001",
                          extension="json", history=False)
        self.assertEqual(body, b'{"name": "x"}')
        self.assertEqual(opener.requests[0].get_header("User-agent"), AGENT)
        call = self.capture.calls[0]
        self.assertEqual((call["feed"], call["provider"], call["history"]), ("sec-13f", "SEC EDGAR", False))
        self.assertEqual(call["licence"], sec_13f.LICENCE)

    def test_retries_with_backoff_and_retry_after_then_gives_up(self):
        clock = _Clock()
        opener = _Opener(clock, [_http_error(503), _http_error(429, "7"), OSError("reset"), b"ok"])
        client = sec_13f.SecClient(AGENT, opener=opener, clock=clock, sleep=clock.sleep)
        self.assertEqual(client.get("https://www.sec.gov/a", key="a", extension="xml", history=True), b"ok")
        backoffs = [s for s in clock.sleeps if s >= 1]
        self.assertEqual(backoffs, [2.0, 7.0, 8.0])
        self.assertEqual((client.requests, client.retries), (4, 3))
        self.assertEqual(len(self.capture.calls), 1, "only the answer is captured")

        clock = _Clock()
        opener = _Opener(clock, [_http_error(500)] * 4)
        client = sec_13f.SecClient(AGENT, opener=opener, clock=clock, sleep=clock.sleep)
        with self.assertRaises(sec_13f.SecRequestError):
            client.get("https://www.sec.gov/b", key="b", extension="xml", history=True)
        self.assertEqual((client.requests, client.retries), (4, 3))
        opener = _Opener(clock, [_http_error(429, "600"), _http_error(429, "600")])
        client = sec_13f.SecClient(AGENT, opener=opener, clock=clock, sleep=clock.sleep, attempts=2)
        with self.assertRaises(sec_13f.SecRequestError):
            client.get("https://www.sec.gov/c", key="c", extension="xml", history=True)
        self.assertEqual(clock.sleeps[-1], sec_13f.MAX_BACKOFF_SECONDS)

    def test_a_refusal_or_a_missing_document_is_not_retried(self):
        clock = _Clock()
        client = sec_13f.SecClient(AGENT, opener=_Opener(clock, [_http_error(403)]), clock=clock, sleep=clock.sleep)
        with self.assertRaisesRegex(sec_13f.SecRequestError, "SEC_USER_AGENT"):
            client.get("https://www.sec.gov/a", key="a", extension="xml", history=True)
        self.assertEqual(client.requests, 1)
        client = sec_13f.SecClient(AGENT, opener=_Opener(clock, [_http_error(404)]), clock=clock, sleep=clock.sleep)
        with self.assertRaises(sec_13f.SecNotFound):
            client.get("https://www.sec.gov/a", key="a", extension="xml", history=True)
        self.assertEqual((client.requests, self.capture.calls), (1, []))


class FilerListTests(unittest.TestCase):
    def test_the_starter_list_is_marked_unreviewed_and_refused(self):
        data = json.loads(sec_13f.FILERS_FILE.read_text(encoding="utf-8"))
        self.assertIs(data["reviewed"], False)
        self.assertIn("STARTER LIST, NOT REVIEWED", data["status"])
        self.assertIn("needs review", data["target"])
        with self.assertRaises(sec_13f.FilerListNotReviewed):
            sec_13f.load_filers()
        filers = sec_13f.load_filers(require_reviewed=False)
        self.assertGreaterEqual(len(filers), 10)
        self.assertLessEqual(len(filers), 50)
        self.assertTrue(all(re.fullmatch(r"[0-9]{10}", f.cik) for f in filers))

    def test_a_reviewed_list_loads_and_a_bad_one_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "filers.json"
            path.write_text(json.dumps({"reviewed": True, "filers": [{"cik": "0000000001", "name": " A  B "}]}))
            self.assertEqual(sec_13f.load_filers(path), [sec_13f.Filer("0000000001", "A B")])
            for filers in ([{"cik": "1", "name": "short"}], [{"cik": "0000000001", "name": ""}],
                           [{"cik": "0000000001", "name": "A"}, {"cik": "0000000001", "name": "B"}], []):
                path.write_text(json.dumps({"reviewed": True, "filers": filers}))
                with self.subTest(filers=filers), self.assertRaises(ValueError):
                    sec_13f.load_filers(path)
            path.write_text(json.dumps({"reviewed": "yes", "filers": [{"cik": "0000000001", "name": "A"}]}))
            with self.assertRaises(sec_13f.FilerListNotReviewed):
                sec_13f.load_filers(path)


# ---------------------------------------------------------------- the jobs


class _Run:
    def __init__(self, test, script):
        self.module = _load_script(script)
        self.test = test

    def main(self, argv=(), *, now=datetime(2026, 11, 26, 22, 30, tzinfo=timezone.utc), connection=None):
        output = io.StringIO()
        with patch.object(self.module, "_now", return_value=now), \
                patch.object(self.module, "load_settings", return_value=_settings()) as settings, \
                patch.object(self.module, "connect", return_value=connection) as connect, \
                contextlib.redirect_stdout(output):
            code = self.module.main(list(argv))
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        return code, lines, settings, connect


class LoaderJobTests(_NoNetwork):
    def test_both_do_nothing_when_off(self):
        for script, setting in (("load_macro_series.py", ms.SETTING),
                                ("load_institutional_holdings.py", sec_13f.SETTING)):
            for value in (None, "", "off", "true", "yes", "onn"):
                with self.subTest(script=script, value=value), \
                        _environment(**({setting: value} if value is not None else {})):
                    run = _Run(self, script)
                    output = io.StringIO()
                    with patch.object(run.module, "load_settings", side_effect=AssertionError("no settings")), \
                            patch.object(run.module, "connect", side_effect=AssertionError("no database")), \
                            patch.object(run.module.lineage, "run_main", side_effect=AssertionError("no lineage")), \
                            contextlib.redirect_stdout(output):
                        self.assertEqual(run.module.entry([]), 0)
                    self.assertEqual(json.loads(output.getvalue())["status"], "off")
        self.assertTrue(ms.enabled({ms.SETTING: " ON "}))
        self.assertTrue(sec_13f.enabled({sec_13f.SETTING: "On"}))
        self.assertFalse(sec_13f.enabled({sec_13f.SETTING: "1"}))

    def test_the_macro_job_exits_held_when_the_table_is_missing(self):
        with _environment(MACRO_SERIES_LOAD="on"):
            run = _Run(self, "load_macro_series.py")
            connection = _FakeConnection(present=False)
            with patch.object(run.module.ms, "load", side_effect=AssertionError("nothing loads")):
                code, lines, _, connect = run.main(connection=connection)
        self.assertEqual(code, ms.EXIT_HELD)
        self.assertEqual(code, 65)
        self.assertEqual(lines[-1]["status"], "held")
        self.assertTrue(connection.closed)
        self.assertEqual(self.capture.calls, [])

    def test_the_macro_job_refuses_the_daily_window(self):
        with _environment(MACRO_SERIES_LOAD="on"):
            run = _Run(self, "load_macro_series.py")
            code, lines, settings, connect = run.main(now=datetime(2026, 11, 26, 8, 0, tzinfo=timezone.utc))
        self.assertEqual((code, lines[-1]["status"]), (ms.EXIT_DAILY_WINDOW, "daily_window"))
        connect.assert_not_called()

    def test_the_macro_job_loads_and_reports(self):
        with _environment(MACRO_SERIES_LOAD="on"):
            run = _Run(self, "load_macro_series.py")
            result = {"rows_stored": 3, "failed": [], "not_started": [], "series": []}
            with patch.object(run.module.ms, "load", return_value=result) as load:
                code, lines, _, _ = run.main(["--since", "2020-01-01"], connection=_FakeConnection())
        self.assertEqual((code, lines[-1]["status"]), (0, "success"))
        self.assertEqual(load.call_args.kwargs["start"], date(2020, 1, 1))
        self.assertTrue(lines[-1]["job_run_id"].startswith("macro-series-"))
        with _environment(MACRO_SERIES_LOAD="on"):
            with patch.object(run.module.ms, "load", return_value={**result, "failed": [{"series_id": "DGS2"}]}):
                code, lines, _, _ = run.main(connection=_FakeConnection())
        self.assertEqual((code, lines[-1]["status"]), (1, "failed"))

    def test_the_13f_job_refuses_without_sec_user_agent_before_any_work(self):
        for agent in (None, "", "no contact address"):
            values = {sec_13f.SETTING: "on"}
            if agent is not None:
                values[sec_13f.USER_AGENT_SETTING] = agent
            with self.subTest(agent=agent), _environment(**values):
                run = _Run(self, "load_institutional_holdings.py")
                code, lines, settings, connect = run.main()
                self.assertEqual((code, lines[-1]["status"]), (sec_13f.EXIT_REFUSED, "refused"))
                self.assertIn("SEC_USER_AGENT", lines[-1]["reason"])
                settings.assert_not_called()
                connect.assert_not_called()

    def test_the_13f_job_refuses_the_unreviewed_starter_list(self):
        with _environment(INSTITUTIONAL_HOLDINGS_LOAD="on", SEC_USER_AGENT=AGENT):
            run = _Run(self, "load_institutional_holdings.py")
            code, lines, settings, connect = run.main()
        self.assertEqual((code, lines[-1]["status"]), (78, "refused"))
        self.assertIn("reviewed", lines[-1]["reason"])
        connect.assert_not_called()

    def test_the_13f_job_exits_held_and_refuses_the_daily_window(self):
        filers = [sec_13f.Filer("0000000001", "Test Filer")]
        with _environment(INSTITUTIONAL_HOLDINGS_LOAD="on", SEC_USER_AGENT=AGENT):
            run = _Run(self, "load_institutional_holdings.py")
            with patch.object(run.module.sec_13f, "load_filers", return_value=filers), \
                    patch.object(run.module.sec_13f, "load", side_effect=AssertionError("nothing loads")):
                code, lines, _, _ = run.main(connection=_FakeConnection(present=False))
                self.assertEqual((code, lines[-1]["status"]), (65, "held"))
                code, lines, _, connect = run.main(now=datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc))
                self.assertEqual((code, lines[-1]["status"]), (75, "daily_window"))
                connect.assert_not_called()

    def test_the_13f_job_reads_four_quarters_by_default_and_names_its_filers(self):
        filers = [sec_13f.Filer("0000000001", "A"), sec_13f.Filer("0000000002", "B")]
        result = {"rows_stored": 0, "failed": [], "not_started": [], "stored": 0}
        with _environment(INSTITUTIONAL_HOLDINGS_LOAD="on", SEC_USER_AGENT=AGENT):
            run = _Run(self, "load_institutional_holdings.py")
            with patch.object(run.module.sec_13f, "load_filers", return_value=filers), \
                    patch.object(run.module.sec_13f, "load", return_value=result) as load:
                code, lines, _, _ = run.main(connection=_FakeConnection())
                self.assertEqual(code, 0)
                self.assertEqual(load.call_args.kwargs["since"], "2025-12-31")
                self.assertEqual(load.call_args.args[2], filers)
                self.assertEqual(load.call_args.args[1].agent, AGENT)
                run.main(["--cik", "2", "--quarters", "1"], connection=_FakeConnection())
                self.assertEqual(load.call_args.args[2], filers[1:])
                self.assertEqual(load.call_args.kwargs["since"], "2026-09-30")
                with self.assertRaises(SystemExit):
                    run.main(["--cik", "3"], connection=_FakeConnection())


class JobDefinitionTests(unittest.TestCase):
    JOBS = {"staging-macro-series-job.bicep": ("ibkrv2stg-macro-series", "scripts/load_macro_series.py",
                                                "param macroSeriesLoad string = 'off'", "'30 22 * * *'"),
            "staging-institutional-holdings-job.bicep": (
                "ibkrv2stg-institutional-holdings", "scripts/load_institutional_holdings.py",
                "param institutionalHoldingsLoad string = 'off'", "'45 22 20 2,5,8,11 *'")}

    def test_off_by_default_outside_the_daily_window_no_new_roles(self):
        catalog = json.loads((ROOT / "infra/medallion/catalog.json").read_text(encoding="utf-8"))
        for name, (job, script, setting, cron) in self.JOBS.items():
            text = (ROOT / "infra" / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn(f"param jobName string = '{job}'", text)
                self.assertIn(f"args: ['{script}']", text)
                self.assertIn(setting, text)
                self.assertIn(f"cronExpression: {cron}", text)
                hour = int(cron.strip("'").split()[1])
                self.assertFalse(7 <= hour <= 9, "outside 07:50 to 09:25 UTC")
                self.assertNotIn("roleAssignments", text)
                self.assertNotIn("Microsoft.Authorization", text)
                self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "no email address")
                self.assertIn("/ibkr-platform@${imageDigest}'", text)
                self.assertIn("existing = {\n  name: last(split(identityResourceId, '/'))", text)
                self.assertEqual(text.count("IBKR_RUN_MIGRATIONS"), 1)
                self.assertIn("{ name: 'IBKR_RUN_MIGRATIONS', value: 'false' }", text)
                self.assertIn("param rawMarketDataContainer string = 'bronze-market-data'", text)
                self.assertRegex(text, r"\], (\w+Env, )?rawMarketDataEnv\)")
                self.assertIn(job, catalog["jobs"])
                self.assertLessEqual(set(catalog["jobs"][job]["hops"]), set(catalog["hops"]))
        holdings = (ROOT / "infra/staging-institutional-holdings-job.bicep").read_text(encoding="utf-8")
        self.assertIn("param secUserAgent string = ''", holdings)
        self.assertIn("var userAgentEnv = empty(secUserAgent) ? [] : [", holdings)

    def test_the_scripts_record_lineage_under_their_catalog_names(self):
        for job, script in (("ibkrv2stg-macro-series", "load_macro_series.py"),
                            ("ibkrv2stg-institutional-holdings", "load_institutional_holdings.py")):
            text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
            self.assertIn("lineage.run_main(JOB", text)
            self.assertIn(f'JOB = "{job}"', text)
            self.assertNotIn(".bind(", text, "silver market data carries no lineage run id")


class RegistrationTests(unittest.TestCase):
    def test_feeds_tables_and_monitoring_are_registered_as_held(self):
        from ibkr_etl import layer_freshness, layer_quality

        self.assertIn("fred-series", raw_capture.FEEDS)
        self.assertIn("sec-13f", raw_capture.FEEDS)
        catalog = json.loads((ROOT / "infra/medallion/catalog.json").read_text(encoding="utf-8"))["postgres"]
        for table in ("macro_series", "institutional_holdings"):
            with self.subTest(table=table):
                entry = catalog[f"public.{table}"]
                self.assertEqual((entry["layer"], entry["target_schema"], entry["source"]),
                                 ("silver", "silver", "market-data"))
                self.assertEqual(entry["held"], f"infra/medallion/drafts/{table}.sql")
                self.assertTrue((ROOT / entry["held"]).exists())
                self.assertTrue((ROOT / f"infra/medallion/drafts/{table}_undo.sql").exists())
                self.assertFalse(list((ROOT / "migrations/postgres").glob(f"*_{table}.sql")), "held, not numbered")
                volume = next(v for v in layer_quality.VOLUMES if v.table == table)
                self.assertTrue(volume.held)
                rule = next(r for r in layer_freshness.RULES if r.target == f"public.{table}")
                self.assertTrue(rule.held)
                self.assertFalse(rule.required)
                self.assertTrue((ROOT / f"infra/medallion/contracts/silver/{table}.yaml").exists())

    def test_the_capture_licence_is_kept_in_the_blob_metadata_only_when_named(self):
        uploads = []

        def factory(account_url, container):
            return lambda name, content, *, content_type, metadata: uploads.append(metadata) or name

        capture = raw_capture.RawCapture(container="bronze-market-data", account_url="https://acct.example",
                                         uploader_factory=factory)
        capture.http("fred-series", "FRED", url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
                     body=b"DATE,DGS10\n", key="k1", licence="public-domain-citation-requested; H.15")
        capture.http("fred-series", "FRED", url="https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS2",
                     body=b"DATE,DGS2\n", key="k2")
        self.assertEqual(uploads[0]["dataLicense"], "public-domain-citation-requested; H.15")
        self.assertNotIn("dataLicense", uploads[1])

    def test_market_inputs_is_left_to_the_gold_worker(self):
        self.assertFalse((ROOT / "src/ibkr_etl/macro_series.py").read_text().count("market_inputs"))
        self.assertFalse((ROOT / "src/ibkr_etl/sec_13f.py").read_text().count("market_inputs"))


if __name__ == "__main__":
    unittest.main()
