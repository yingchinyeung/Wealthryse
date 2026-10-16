"""S2 raw market-data capture: off by default, right names and metadata when on,
never fails a fetch, and parsed results are the same either way. Synthetic data only."""
from __future__ import annotations

import hashlib
import io
import json
import re
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from ibkr_etl import raw_capture
from ibkr_etl.raw_capture import RawCapture

ROOT = Path(__file__).resolve().parents[1]
REQUESTED = datetime(2026, 10, 16, 8, 31, 5, tzinfo=timezone.utc)
ACCOUNT_URL = "https://example.invalid"


class _Uploads:
    """Records uploads; optionally fails like Azure would."""

    def __init__(self, error: Exception | None = None):
        self.calls: list[dict] = []
        self.error = error
        self.factory_calls = 0

    def factory(self, account_url, container):
        self.factory_calls += 1
        self.account_url, self.container = account_url, container
        return self.upload

    def upload(self, name, content, *, content_type, metadata):
        self.calls.append({"name": name, "content": content, "content_type": content_type,
                           "metadata": metadata})
        if self.error is not None:
            raise self.error
        return ACCOUNT_URL + "/bronze-market-data/" + name


def _frame(closes=(5996.659912109375, 0.1 + 0.2, 6049.240234375)) -> pd.DataFrame:
    index = pd.DatetimeIndex(["2025-01-21", "2025-01-22", "2025-01-23"], tz="America/New_York", name="Date")
    return pd.DataFrame({"Open": closes, "Close": closes, "Adj Close": closes,
                         "Volume": [1, 2, 3]}, index=index)


class _Ticker:
    def __init__(self, frame):
        self.frame = frame
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        return self.frame


def _capture(uploads: _Uploads, **options) -> RawCapture:
    return RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL,
                      uploader_factory=uploads.factory, **options)


class OffByDefaultTests(unittest.TestCase):
    def tearDown(self):
        raw_capture.reset()

    def test_needs_both_container_and_storage_account(self):
        self.assertFalse(RawCapture.from_environment({}).enabled)
        self.assertFalse(RawCapture.from_environment({"MARKET_DATA_RAW_CONTAINER": "bronze-market-data"}).enabled)
        self.assertFalse(RawCapture.from_environment({"AZURE_STORAGE_ACCOUNT_URL": ACCOUNT_URL}).enabled)
        self.assertTrue(RawCapture.from_environment({"MARKET_DATA_RAW_CONTAINER": "bronze-market-data",
                                                     "AZURE_STORAGE_ACCOUNT_URL": ACCOUNT_URL}).enabled)

    def test_off_is_a_no_op_that_never_touches_storage(self):
        uploads = _Uploads()
        capture = RawCapture(container="", account_url=ACCOUNT_URL, uploader_factory=uploads.factory)
        self.assertIsNone(capture.frame("yahoo-benchmark-history", "p", frame=_frame(), request={}, key="k"))
        self.assertIsNone(capture.http("treasury-par-yield", "p", url="https://x", body=b"<x/>", key="k"))
        self.assertIsNone(capture.document("yahoo-sector-profile", "p", payload={}, request={}, key="k"))
        self.assertEqual((uploads.factory_calls, uploads.calls), (0, []))

    def test_module_functions_are_off_without_the_environment(self):
        with patch.dict("os.environ", {}, clear=True), \
                patch.object(raw_capture, "_default_uploader", side_effect=AssertionError("no storage")):
            raw_capture.reset()
            self.assertIsNone(raw_capture.capture_frame("yahoo-crisis-history", "p", frame=_frame(),
                                                        request={}, key="k"))
            raw_capture.set_job_run_id(7)
            self.assertFalse(raw_capture.default().enabled)


class CaptureWhenOnTests(unittest.TestCase):
    def test_frame_name_metadata_and_lossless_csv(self):
        uploads = _Uploads()
        capture = _capture(uploads, job_execution="ibkrv2stg-benchmark-daily-abc1234")
        capture.job_run_id = "4321"
        frame = _frame()
        request = {"symbol": "^GSPC", "method": "history", "start": "2025-01-20", "end": "2025-01-24",
                   "interval": "1d", "auto_adjust": False}
        key = raw_capture.request_key("^GSPC", "2025-01-20", "2025-01-24", "1d")
        name = capture.frame("yahoo-benchmark-history", "Yahoo Finance via yfinance", frame=frame,
                             request=request, key=key, requested_at=REQUESTED)

        self.assertEqual(len(uploads.calls), 1)
        call = uploads.calls[0]
        digest = hashlib.sha256(call["content"]).hexdigest()
        self.assertEqual(name, f"yahoo-benchmark-history/2026/10/16/GSPC_2025-01-20_2025-01-24_1d-{digest[:16]}.csv")
        self.assertEqual(call["name"], name)
        self.assertEqual(uploads.container, "bronze-market-data")
        self.assertEqual(call["content_type"], "text/csv; charset=utf-8")
        metadata = call["metadata"]
        self.assertEqual(metadata["feed"], "yahoo-benchmark-history")
        self.assertEqual(metadata["provider"], "Yahoo Finance via yfinance")
        self.assertEqual(metadata["requested_at"], "2026-10-16T08:31:05+00:00")
        self.assertEqual(metadata["sha256"], digest)
        self.assertEqual(metadata["job_run_id"], "4321")
        self.assertEqual(metadata["job_execution"], "ibkrv2stg-benchmark-daily-abc1234")
        self.assertEqual(json.loads(metadata["request"])["symbol"], "^GSPC")
        for key_name, value in metadata.items():
            self.assertRegex(key_name, r"^[a-z_][a-z0-9_]*$")
            value.encode("ascii")
        # The CSV keeps the index and every float bit for bit.
        back = pd.read_csv(io.StringIO(call["content"].decode()), index_col=0, float_precision="round_trip")
        self.assertEqual(list(back.columns), list(frame.columns))
        self.assertEqual(back.index.name, "Date")
        self.assertTrue((back["Close"].to_numpy() == frame["Close"].to_numpy()).all())
        self.assertEqual(list(pd.to_datetime(back.index, utc=True)), list(frame.index.tz_convert("UTC")))

    def test_http_body_is_stored_as_received_without_credentials(self):
        uploads = _Uploads()
        capture = _capture(uploads)
        body = b'<?xml version="1.0"?><feed>\xe2\x80\x94</feed>'
        name = capture.http("treasury-par-yield", "U.S. Treasury",
                            url="https://user:pw@home.treasury.gov/xml?data=x&api_key=SECRET&year=2026",
                            body=body, key="year-2026", status=200, extension="xml", requested_at=REQUESTED)
        call = uploads.calls[0]
        self.assertTrue(name.startswith("treasury-par-yield/2026/10/16/year-2026-"))
        self.assertTrue(name.endswith(".xml"))
        self.assertEqual(call["content"], body)
        self.assertEqual(call["metadata"]["http_status"], "200")
        self.assertEqual(call["metadata"]["source_url"], "https://home.treasury.gov/xml?data=x&year=2026")
        self.assertNotIn("SECRET", json.dumps(call["metadata"]))
        self.assertNotIn("pw", call["metadata"]["source_url"])

    def test_status_and_content_type_come_from_the_shared_getter_body(self):
        from ibkr_etl.reference_data import HttpBody

        uploads = _Uploads()
        body = HttpBody(b'{"ok": true}')
        body.status, body.content_type = 203, "application/json; charset=utf-8"
        name = _capture(uploads).http("hkma-hibor", "HKMA", url="https://api.example/x?offset=0",
                                      body=body, key="offset-0")
        self.assertTrue(name.endswith(".json"))
        self.assertEqual(uploads.calls[0]["metadata"]["http_status"], "203")
        self.assertIs(type(uploads.calls[0]["content"]), bytes)

    def test_identical_content_twice_is_written_once(self):
        uploads = _Uploads()
        capture = _capture(uploads)
        first = capture.http("hkab-hibor", "HKAB", url="https://x/y", body=b"{}", key="2026-10-15",
                             requested_at=REQUESTED)
        second = capture.http("hkab-hibor", "HKAB", url="https://x/y", body=b"{}", key="2026-10-15",
                              requested_at=REQUESTED)
        self.assertEqual(first, second)
        self.assertEqual(len(uploads.calls), 1)
        self.assertEqual(capture.stats["existing"], 1)

    def test_existing_blob_under_legal_hold_is_not_an_error(self):
        from azure.core.exceptions import ResourceExistsError

        uploads = _Uploads(ResourceExistsError("BlobAlreadyExists"))
        capture = _capture(uploads)
        self.assertIsNotNone(capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="k"))
        self.assertIsNone(capture.disabled_reason)
        self.assertEqual(capture.stats["existing"], 1)


class DefaultUploaderTests(unittest.TestCase):
    def test_uses_the_blob_helper_with_short_timeouts_no_retries_and_no_overwrite(self):
        service = Mock()
        with patch("azure.identity.DefaultAzureCredential") as credential, \
                patch("azure.storage.blob.BlobServiceClient", return_value=service) as client_class:
            capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL)
            name = capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>",
                                key="year-2026", extension="xml", requested_at=REQUESTED)
            capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<y/>",
                         key="year-2026", extension="xml", requested_at=REQUESTED)
        credential.assert_called_once_with(exclude_interactive_browser_credential=True)
        client_class.assert_called_once()  # one client for the whole process
        self.assertEqual(client_class.call_args.kwargs["account_url"], ACCOUNT_URL)
        for option, value in (("connection_timeout", 3), ("read_timeout", 5), ("retry_total", 0)):
            self.assertEqual(client_class.call_args.kwargs[option], value)
        service.get_container_client.assert_called_once_with("bronze-market-data")
        blob = service.get_container_client.return_value.get_blob_client
        self.assertEqual(blob.call_args_list[0].args, (name,))
        upload = blob.return_value.upload_blob
        self.assertEqual(upload.call_count, 2)
        self.assertEqual(upload.call_args_list[0].args, (b"<x/>",))
        options = upload.call_args_list[0].kwargs
        self.assertIs(options["overwrite"], False)
        self.assertEqual(options["metadata"]["feed"], "treasury-par-yield")
        self.assertEqual(options["content_settings"].content_type, "application/xml")


class FailSoftTests(unittest.TestCase):
    def test_storage_error_is_swallowed_logged_once_and_switches_capture_off(self):
        uploads = _Uploads(RuntimeError("AuthorizationPermissionMismatch"))
        capture = _capture(uploads)
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING") as logs:
            self.assertIsNone(capture.frame("yahoo-crisis-history", "p", frame=_frame(), request={}, key="SPY"))
            self.assertIsNone(capture.frame("yahoo-crisis-history", "p", frame=_frame(), request={}, key="QQQ"))
        self.assertEqual(len(uploads.calls), 1)  # no retry, nothing after the first failure
        self.assertEqual(len(logs.records), 1)
        self.assertIn("AuthorizationPermissionMismatch", capture.disabled_reason)
        self.assertEqual(capture.stats["skipped"], 1)

    def test_uploader_setup_failure_is_swallowed(self):
        capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL,
                             uploader_factory=Mock(side_effect=ImportError("azure missing")))
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            self.assertIsNone(capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="k"))
        self.assertIsNotNone(capture.disabled_reason)

    def test_unserialisable_response_is_skipped_but_capture_stays_on(self):
        uploads = _Uploads()
        capture = _capture(uploads)
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            self.assertIsNone(capture.frame("yahoo-crisis-history", "p", frame=object(), request={}, key="k"))
        self.assertIsNone(capture.disabled_reason)
        self.assertIsNotNone(capture.frame("yahoo-crisis-history", "p", frame=_frame(), request={}, key="k"))

    def test_time_budget_caps_the_delay_a_job_can_see(self):
        ticks = iter(range(0, 1000, 4))  # each capture "takes" 4 seconds
        uploads = _Uploads()
        capture = _capture(uploads, budget_seconds=10, clock=lambda: next(ticks))
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            for index in range(6):
                capture.http("hkab-hibor", "HKAB", url="https://x", body=str(index).encode(), key=str(index))
        self.assertEqual(len(uploads.calls), 3)
        self.assertIn("time budget", capture.disabled_reason)

    def test_unknown_feed_is_rejected_without_raising(self):
        uploads = _Uploads()
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            self.assertIsNone(_capture(uploads).http("not-a-feed", "p", url="https://x", body=b"", key="k"))
        self.assertEqual(uploads.calls, [])

    def test_every_capture_call_in_the_code_names_a_known_feed(self):
        used = set()
        for path in (ROOT / "src" / "ibkr_etl").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            used.update(re.findall(r"capture_(?:http|frame|document)\(\s*\"([a-z0-9-]+)\"", text))
            used.update(re.findall(r"feed='([a-z0-9-]+)'", text))
            used.update(re.findall(r'"(yahoo-(?:fx|benchmark)-history)"', text))
        self.assertTrue(used)
        self.assertLessEqual(used, raw_capture.FEEDS)
        self.assertEqual(raw_capture.FEEDS - used, set())


class ParsedResultsUnchangedTests(unittest.TestCase):
    """Each fetch returns exactly the same parsed values with capture off, on, or failing."""

    def tearDown(self):
        raw_capture.reset()

    def _three_ways(self, fetch):
        results = {}
        raw_capture.reset(RawCapture())
        results["off"] = fetch()
        working = _Uploads()
        raw_capture.reset(_capture(working))
        results["on"] = fetch()
        failing = _Uploads(RuntimeError("storage down"))
        raw_capture.reset(_capture(failing))
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            results["failing"] = fetch()
        self.assertEqual(results["off"], results["on"])
        self.assertEqual(results["off"], results["failing"])
        self.assertTrue(working.calls, "capture on must write the raw response")
        return working.calls

    def test_benchmark_and_fx_history(self):
        from ibkr_etl.provisional_benchmark import BENCHMARKS, fetch_benchmark_yfinance, fetch_fx_to_reporting_yfinance

        frame = _frame()
        ticker = _Ticker(frame)
        calls = self._three_ways(lambda: fetch_benchmark_yfinance(
            BENCHMARKS[0], date(2025, 1, 20), date(2025, 1, 23), lambda _symbol: ticker))
        self.assertTrue(calls[0]["name"].startswith("yahoo-benchmark-history/"))
        self.assertIn("/GSPC_2025-01-20_2025-01-24_1d-", calls[0]["name"])
        self.assertEqual(ticker.calls[0]["end"], "2025-01-24")
        fx = self._three_ways(lambda: fetch_fx_to_reporting_yfinance(
            "TWD", "USD", date(2025, 1, 20), date(2025, 1, 23), lambda _symbol: _Ticker(_frame((32.0, 31.5, 31.25)))))
        self.assertTrue(fx[0]["name"].startswith("yahoo-fx-history/"))
        self.assertEqual(json.loads(fx[0]["metadata"]["request"])["symbol"], "TWD=X")

    def test_crisis_history(self):
        from ibkr_etl.crisis_replay import fetch_adjusted_history

        calls = self._three_ways(lambda: fetch_adjusted_history(
            "SPY", lambda _symbol: _Ticker(_frame()), start_date=date(2025, 1, 20), end_date=date(2025, 1, 23)))
        self.assertTrue(calls[0]["name"].startswith("yahoo-crisis-history/"))

    def test_treasury_xml(self):
        from ibkr_etl.reference_data import HttpBody
        from ibkr_etl.risk_free import fetch_treasury_3m_rates

        xml = HttpBody(b"""<feed xmlns:m="m" xmlns:d="d"><entry><content><m:properties>
            <d:NEW_DATE>2026-10-15T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.01</d:BC_3MONTH>
            </m:properties></content></entry></feed>""")
        xml.status, xml.content_type = 200, "application/xml"
        with patch("ibkr_etl.risk_free._get", return_value=xml):
            calls = self._three_ways(lambda: fetch_treasury_3m_rates(date(2026, 10, 1), date(2026, 10, 16)))
        self.assertTrue(calls[0]["name"].startswith("treasury-par-yield/"))
        self.assertIn("/year-2026-", calls[0]["name"])
        self.assertEqual(calls[0]["content"], bytes(xml))
        self.assertEqual(calls[0]["metadata"]["http_status"], "200")

    def test_hibor_pages_and_owner_fallback(self):
        from ibkr_etl.hibor import fetch

        page = {"header": {"success": True},
                "result": {"records": [{"end_of_day": "2025-01-20", "ir_1m": 4.1},
                                       {"end_of_day": "2025-01-17", "ir_1m": 4.0}]}}
        owner = {"ds_is_fb": "N", "year": 2025, "month": 1, "day": 21, "1 Month": 4.2}

        def run():
            getter = Mock(side_effect=[json.dumps(page).encode(), json.dumps(owner).encode()])
            return fetch(date(2025, 1, 20), date(2025, 1, 21), getter=getter)

        calls = self._three_ways(run)
        self.assertEqual([c["name"].split("/")[0] for c in calls], ["hkma-hibor", "hkab-hibor"])
        self.assertIn("/offset-0-", calls[0]["name"])
        self.assertIn("/2025-01-21-", calls[1]["name"])

    def test_sector_search_and_profile(self):
        from ibkr_etl import sector_classification

        body = json.dumps({"quotes": [{"symbol": "AAPL", "sector": "Technology",
                                       "industry": "Consumer Electronics", "exchange": "NMS"}]}).encode()

        class _Response:
            status = 200

            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        holding = {"conid": "265598", "symbol": "AAPL", "listing_exchange": "NASDAQ", "currency": "USD"}

        def run():
            result = sector_classification.lookup_classification(holding)
            result.pop("checked_at")
            return result

        with patch("urllib.request.urlopen", return_value=_Response()):
            calls = self._three_ways(run)
        self.assertTrue(calls[0]["name"].startswith("yahoo-sector-search/"))
        self.assertEqual(calls[0]["content"], body)

        fake_yf = Mock()
        fake_yf.Ticker.return_value.get_info.return_value = {"symbol": "AAPL", "sector": "Technology"}
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            profile = self._three_ways(lambda: dict(sector_classification._fetch_yfinance_profile("AAPL")))
        self.assertTrue(profile[0]["name"].startswith("yahoo-sector-profile/"))
        self.assertEqual(json.loads(profile[0]["content"])["sector"], "Technology")

    def test_market_data_chart(self):
        from ibkr_etl import market_data

        body = json.dumps({"chart": {"result": [{
            "meta": {"currency": "USD", "exchangeTimezoneName": "America/New_York"},
            "timestamp": [1737469800], "indicators": {"quote": [{"close": [601.5]}]}}]}}).encode()

        class _Response:
            status = 200

            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        instrument = market_data.ALPHA_WORLD_MARKET_INSTRUMENTS[1]
        with patch("urllib.request.urlopen", return_value=_Response()):
            calls = self._three_ways(lambda: market_data.fetch_daily_closes(
                instrument, date(2025, 1, 20), date(2025, 1, 23)))
        self.assertTrue(calls[0]["name"].startswith("yahoo-chart/"))


if __name__ == "__main__":
    unittest.main()
