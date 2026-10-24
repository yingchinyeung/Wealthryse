"""S2 raw market-data capture: off by default, right names and metadata when on,
never fails a fetch, and parsed results are the same either way. Synthetic data only."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import socket
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from ibkr_etl import raw_capture
from ibkr_etl.raw_capture import RawCapture

ROOT = Path(__file__).resolve().parents[1]
REQUESTED = datetime(2026, 10, 16, 8, 31, 5, tzinfo=timezone.utc)
ACCOUNT_URL = "https://example.invalid"
# Thread start-up and wake-up time allowed on top of a time limit in these tests.
SCHEDULING_SLACK = 0.25


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
        with patch.dict("os.environ", {"AZURE_CLIENT_ID": "00000000-0000-0000-0000-00000000000a"}), \
                patch("azure.identity.DefaultAzureCredential", side_effect=AssertionError("no chain")), \
                patch("azure.identity.ManagedIdentityCredential") as credential, \
                patch("azure.storage.blob.BlobServiceClient", return_value=service) as client_class:
            capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL)
            name = capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>",
                                key="year-2026", extension="xml", requested_at=REQUESTED)
            capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<y/>",
                         key="year-2026", extension="xml", requested_at=REQUESTED)
        # Managed identity only: one token request, short timeouts, no retries.
        credential.assert_called_once_with(client_id="00000000-0000-0000-0000-00000000000a",
                                           connection_timeout=3, read_timeout=5, retry_total=0)
        client_class.assert_called_once()  # one client for the whole process
        self.assertIs(client_class.call_args.kwargs["credential"], credential.return_value)
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
        uploads = _Uploads()
        slow = uploads.upload

        def upload(*args, **kwargs):
            time.sleep(0.2)
            return slow(*args, **kwargs)

        capture = _capture(uploads, budget_seconds=0.5)
        capture._uploader_factory = lambda *_: upload
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            for index in range(6):
                capture.http("hkab-hibor", "HKAB", url="https://x", body=str(index).encode(), key=str(index))
        self.assertLessEqual(len(uploads.calls), 3)
        self.assertIn("time budget", capture.disabled_reason)
        self.assertLessEqual(capture.summary()["seconds"], 0.5 + SCHEDULING_SLACK)

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
            used.update(re.findall(r'RAW_FEED = "([a-z0-9-]+)"', text))
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
        from ibkr_etl.crisis_replay import RAW_FEED, fetch_adjusted_history

        calls = self._three_ways(lambda: fetch_adjusted_history(
            "SPY", lambda _symbol: _Ticker(_frame()), start_date=date(2025, 1, 20), end_date=date(2025, 1, 23),
            capture_feed=RAW_FEED))
        # A finished crisis window is filed once, without a date folder.
        self.assertTrue(calls[0]["name"].startswith("yahoo-crisis-history/history/SPY_2025-01-20_2025-01-24_1d-"))

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


class _HungServer:
    """A local endpoint that accepts connections and never answers."""

    def __init__(self):
        self.connections: list[socket.socket] = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(16)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            self.connections.append(connection)

    def close(self):
        self.server.close()
        for connection in self.connections:
            connection.close()


def _without_proxies(extra: dict) -> dict:
    environment = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    environment.update(extra)
    return environment


class HungIdentityEndpointTests(unittest.TestCase):
    """Container Apps hands out tokens from IDENTITY_ENDPOINT; here it never answers."""

    def setUp(self):
        self.endpoint = _HungServer()
        self.addCleanup(self.endpoint.close)
        environment = _without_proxies({
            "IDENTITY_ENDPOINT": f"http://127.0.0.1:{self.endpoint.port}/msi/token",
            "IDENTITY_HEADER": "synthetic-header",
            "AZURE_CLIENT_ID": "00000000-0000-0000-0000-00000000000a",
        })
        for name in ("MSI_ENDPOINT", "IMDS_ENDPOINT", "IDENTITY_SERVER_THUMBPRINT", "AZURE_FEDERATED_TOKEN_FILE"):
            environment.pop(name, None)
        patcher = patch.dict("os.environ", environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        # azure.identity logs each expected failure; keep the test output quiet.
        identity_log = logging.getLogger("azure.identity")
        self.addCleanup(identity_log.setLevel, identity_log.level)
        identity_log.setLevel(logging.CRITICAL)

    def test_the_credential_gives_up_after_one_request_and_its_read_timeout(self):
        from ibkr_etl.cloud_storage import best_effort_credential

        credential = best_effort_credential(connection_timeout=1, read_timeout=1)
        started = time.monotonic()
        with self.assertRaises(Exception):
            credential.get_token("https://storage.azure.com/.default")
        self.assertLess(time.monotonic() - started, 1 + 1.5)
        self.assertEqual(len(self.endpoint.connections), 1)

    def test_capture_switches_off_within_the_timeout_and_never_asks_again(self):
        with patch.object(raw_capture, "CONNECTION_TIMEOUT_SECONDS", 1), \
                patch.object(raw_capture, "READ_TIMEOUT_SECONDS", 1):
            # Bearer tokens need https; nothing listens on port 9, but the token comes first.
            capture = RawCapture(container="bronze-market-data", account_url="https://127.0.0.1:9")
            started = time.monotonic()
            with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
                self.assertIsNone(capture.http("treasury-par-yield", "U.S. Treasury", url="https://x",
                                               body=b"<x/>", key="year-2026", extension="xml"))
            self.assertLess(time.monotonic() - started, 1 + 1.5)
            self.assertIsNotNone(capture.disabled_reason)
            self.assertIsNone(capture.http("treasury-par-yield", "U.S. Treasury", url="https://x",
                                           body=b"<y/>", key="year-2026", extension="xml"))
        self.assertEqual(len(self.endpoint.connections), 1)
        self.assertEqual((capture.stats["failed"], capture.stats["skipped"]), (1, 1))


class HungUploadTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        self.started = []

    def _hung_factory(self, account_url, container):
        def upload(name, content, **_options):
            self.started.append(name)
            self.release.wait(60)
        return upload

    def _capture(self, **options):
        return RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL,
                          uploader_factory=self._hung_factory, **options)

    def test_a_hung_upload_holds_the_job_no_longer_than_the_budget(self):
        capture = self._capture(budget_seconds=1.0)
        started = time.monotonic()
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            self.assertIsNone(capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="a"))
        self.assertLess(time.monotonic() - started, 1.0 + SCHEDULING_SLACK)
        self.assertIn("TimeoutError", capture.disabled_reason)
        started = time.monotonic()
        self.assertIsNone(capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="b"))
        self.assertLess(time.monotonic() - started, 0.05)
        self.assertEqual(len(self.started), 1)

    def test_each_upload_waits_at_most_one_write_allowance(self):
        capture = self._capture(budget_seconds=30, write_wait_seconds=0.3)
        started = time.monotonic()
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="a")
        self.assertLess(time.monotonic() - started, 0.3 + SCHEDULING_SLACK)
        self.assertIsNotNone(capture.disabled_reason)


class SharedBudgetTests(unittest.TestCase):
    """Four threads, like the sector job's workers, share one budget."""

    def _run_threads(self, capture, *, threads=4, per_thread=6):
        barrier = threading.Barrier(threads)
        waits = []
        lock = threading.Lock()

        def worker(number):
            barrier.wait()
            for index in range(per_thread):
                started = time.monotonic()
                capture.http("yahoo-sector-search", "Yahoo", url="https://x",
                             body=f"{number}-{index}".encode(), key=f"T{number}-{index}")
                with lock:
                    waits.append(time.monotonic() - started)

        pool = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            for thread in pool:
                thread.start()
            for thread in pool:
                thread.join(30)
        return waits

    def test_slow_uploads_from_many_threads_stay_within_one_budget(self):
        def slow_factory(account_url, container):
            def upload(name, content, **_options):
                time.sleep(0.3)
            return upload

        capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL,
                             budget_seconds=1.0, uploader_factory=slow_factory)
        waits = self._run_threads(capture)
        self.assertEqual(len(waits), 24)
        self.assertLessEqual(sum(waits), 1.0 + SCHEDULING_SLACK)
        self.assertLessEqual(capture.summary()["seconds"], 1.0 + SCHEDULING_SLACK)
        self.assertGreaterEqual(capture.stats["written"], 1)
        self.assertIsNotNone(capture.disabled_reason)

    def test_hung_uploads_from_many_threads_stay_within_one_budget(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hung_factory(account_url, container):
            def upload(name, content, **_options):
                release.wait(60)
            return upload

        capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL,
                             budget_seconds=1.0, uploader_factory=hung_factory)
        waits = self._run_threads(capture)
        self.assertEqual(len(waits), 24)
        self.assertLessEqual(sum(waits), 1.0 + SCHEDULING_SLACK)
        self.assertEqual(capture.stats["written"], 0)
        self.assertIsNotNone(capture.disabled_reason)


class HistoryNamingTests(unittest.TestCase):
    """Finished windows get one date-free, content-addressed name; repeats stay quiet."""

    def tearDown(self):
        raw_capture.reset()

    def test_settled_means_ended_over_a_week_ago(self):
        now = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)
        self.assertTrue(raw_capture.settled(date(2026, 10, 9), now=now))
        self.assertFalse(raw_capture.settled(date(2026, 10, 10), now=now))
        self.assertFalse(raw_capture.settled("2026-10-17", now=now))
        self.assertTrue(raw_capture.settled("2009-03-09", now=now))
        self.assertFalse(raw_capture.settled("not a date", now=now))

    def test_a_repeated_history_request_is_the_quiet_existing_case(self):
        from azure.core.exceptions import ResourceExistsError

        uploads = _Uploads()
        first = _capture(uploads).http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<2025/>",
                                       key="year-2025", extension="xml", requested_at=REQUESTED, history=True)
        digest = hashlib.sha256(b"<2025/>").hexdigest()[:16]
        self.assertEqual(first, f"treasury-par-yield/history/year-2025-{digest}.xml")
        # The next day's run asks again and gets the same file: same name, no new blob.
        tomorrow = _Uploads(ResourceExistsError("BlobAlreadyExists"))
        capture = _capture(tomorrow)
        with self.assertNoLogs("ibkr_etl.raw_capture", level="WARNING"):
            second = capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<2025/>",
                                  key="year-2025", extension="xml",
                                  requested_at=REQUESTED + timedelta(days=1), history=True)
        self.assertEqual(second, first)
        self.assertEqual((capture.stats["existing"], capture.stats["written"]), (1, 0))
        self.assertIsNone(capture.disabled_reason)

    def test_treasury_files_finished_years_once_and_this_year_daily(self):
        from ibkr_etl.reference_data import HttpBody
        from ibkr_etl.risk_free import fetch_treasury_3m_rates

        def xml(year):
            body = HttpBody(f"""<feed xmlns:m="m" xmlns:d="d"><entry><content><m:properties>
                <d:NEW_DATE>{year}-01-15T00:00:00</d:NEW_DATE><d:BC_3MONTH>4.01</d:BC_3MONTH>
                </m:properties></content></entry></feed>""".encode())
            body.status = 200
            return body

        uploads = _Uploads()
        raw_capture.reset(_capture(uploads))
        this_year = raw_capture.utc_now().year
        with patch("ibkr_etl.risk_free._get", side_effect=[xml(this_year - 1), xml(this_year)]):
            fetch_treasury_3m_rates(date(this_year - 1, 1, 1), date(this_year, 12, 31))
        names = [call["name"] for call in uploads.calls]
        self.assertTrue(names[0].startswith(f"treasury-par-yield/history/year-{this_year - 1}-"))
        self.assertRegex(names[1], rf"^treasury-par-yield/{this_year}/\d\d/\d\d/year-{this_year}-")

    def test_a_benchmark_window_ending_today_keeps_a_date_folder(self):
        from ibkr_etl.provisional_benchmark import BENCHMARKS, fetch_benchmark_yfinance

        uploads = _Uploads()
        raw_capture.reset(_capture(uploads))
        today = raw_capture.utc_now().date()
        index = pd.DatetimeIndex([today.isoformat()], name="Date")
        frame = pd.DataFrame({"Close": [6000.5], "Adj Close": [6000.5], "Volume": [1]}, index=index)
        fetch_benchmark_yfinance(BENCHMARKS[0], today - timedelta(days=3), today, lambda _symbol: _Ticker(frame))
        self.assertTrue(uploads.calls[0]["name"].startswith(f"yahoo-benchmark-history/{today:%Y/%m/%d}/"))

    def test_fama_french_stand_ins_are_never_filed_as_crisis_history(self):
        from ibkr_etl import fama_french

        uploads = _Uploads()
        raw_capture.reset(_capture(uploads))
        ticker = _Ticker(_frame())
        fake_yf = Mock()
        fake_yf.Ticker.side_effect = lambda _symbol: ticker
        with patch.dict("sys.modules", {"yfinance": fake_yf}):
            try:
                fama_french.load_proxy_factors(sorted(fama_french.PROXY_LEGS)[0], today=date(2025, 1, 23))
            except fama_french.FactorDataError:
                pass  # three synthetic days are not enough factors; the fetch is what matters
        self.assertTrue(ticker.calls, "the default Yahoo fetch must have run")
        self.assertEqual(uploads.calls, [])


if __name__ == "__main__":
    unittest.main()


class _Flaky(_Uploads):
    """Fails the first `failures` uploads, then works."""

    def __init__(self, failures: int, error: Exception | None = None):
        super().__init__()
        self.failures = failures
        self.failure = error or ConnectionError("storage blinked")

    def upload(self, name, content, *, content_type, metadata):
        self.calls.append({"name": name, "content": content, "content_type": content_type,
                           "metadata": metadata})
        if len(self.calls) <= self.failures:
            raise self.failure
        return ACCOUNT_URL + "/bronze-market-data/" + name


class RequiredCaptureTests(unittest.TestCase):
    """MARKET_DATA_RAW_REQUIRED: a response whose raw copy cannot be stored fails its fetch."""

    def tearDown(self):
        raw_capture.reset()

    def _required(self, uploads, **options):
        self.pauses = []
        options.setdefault("sleep", self.pauses.append)
        return _capture(uploads, required=True, **options)

    def test_the_setting_turns_it_on_with_the_longer_timeouts(self):
        environment = {"MARKET_DATA_RAW_CONTAINER": "bronze-market-data",
                       "AZURE_STORAGE_ACCOUNT_URL": ACCOUNT_URL}
        self.assertFalse(RawCapture.from_environment(environment).required)
        for value in ("true", "on", "1", "yes"):
            capture = RawCapture.from_environment({**environment, "MARKET_DATA_RAW_REQUIRED": value})
            self.assertTrue(capture.required)
            self.assertIs(capture._uploader_factory, raw_capture._required_uploader)
            self.assertEqual(capture.attempts, raw_capture.REQUIRED_ATTEMPTS)
            self.assertEqual(capture.write_wait_seconds, raw_capture.REQUIRED_WRITE_WAIT_SECONDS)
        self.assertFalse(RawCapture.from_environment({**environment, "MARKET_DATA_RAW_REQUIRED": "off"}).required)
        self.assertEqual(RawCapture(container="c", account_url=ACCOUNT_URL).attempts, 1)

    def test_required_without_storage_settings_fails_every_fetch(self):
        capture = RawCapture.from_environment({"MARKET_DATA_RAW_REQUIRED": "true"})
        with self.assertRaisesRegex(raw_capture.RawCopyNotStored, "MARKET_DATA_RAW_CONTAINER"):
            capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>", key="k")
        raw_capture.reset(capture)
        with self.assertRaises(raw_capture.RawCopyNotStored):
            raw_capture.capture_http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>", key="k")
        with self.assertRaises(raw_capture.RawCopyNotStored):
            raw_capture.capture_frame("yahoo-benchmark-history", "Yahoo", frame=_frame(), request={}, key="k")
        with self.assertRaises(raw_capture.RawCopyNotStored):
            raw_capture.capture_document("yahoo-sector-profile", "Yahoo", payload={}, request={}, key="k")

    def test_a_blink_is_retried_and_the_copy_is_kept(self):
        uploads = _Flaky(2)
        capture = self._required(uploads)
        name = capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>",
                            key="year-2026", extension="xml", requested_at=REQUESTED)
        self.assertTrue(name.startswith("treasury-par-yield/2026/10/16/year-2026-"))
        self.assertEqual(len(uploads.calls), 3)
        self.assertEqual({call["name"] for call in uploads.calls}, {name})
        self.assertEqual(self.pauses, [1.0, 3.0])
        self.assertEqual(capture.stats["written"], 1)
        self.assertEqual(capture.stats["failed"], 0)
        self.assertEqual(capture.digest_of(name), hashlib.sha256(b"<x/>").hexdigest())

    def test_a_copy_that_fails_every_try_fails_the_fetch_and_the_rest_of_the_run(self):
        uploads = _Uploads(ConnectionError("storage down"))
        capture = self._required(uploads, budget_seconds=0.0)
        with self.assertLogs("ibkr_etl.raw_capture", level="ERROR"):
            with self.assertRaisesRegex(raw_capture.RawCopyNotStored, "after 3 tries .*ConnectionError"):
                capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>", key="a")
        self.assertEqual(len(uploads.calls), 3)
        self.assertIsNone(capture.disabled_reason)
        self.assertIn("ConnectionError", capture.failed_reason)
        with self.assertRaisesRegex(raw_capture.RawCopyNotStored, "earlier raw copy"):
            capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<y/>", key="b")
        self.assertEqual(len(uploads.calls), 3, "after a copy failed every try, nothing more is sent")
        self.assertEqual(capture.stats["failed"], 2)
        summary = capture.summary()
        self.assertTrue(summary["required"])
        self.assertIn("ConnectionError", summary["failed_reason"])

    def test_a_file_already_there_counts_as_kept(self):
        from azure.core.exceptions import ResourceExistsError

        capture = self._required(_Uploads(ResourceExistsError("BlobAlreadyExists")))
        name = capture.http("treasury-par-yield", "U.S. Treasury", url="https://x", body=b"<x/>", key="a")
        self.assertIsNotNone(name)
        self.assertEqual(capture.stats["existing"], 1)
        self.assertEqual(self.pauses, [])

    def test_no_time_budget_applies(self):
        uploads = _Uploads()
        capture = self._required(uploads, budget_seconds=0.0)
        for key in ("a", "b", "c"):
            self.assertIsNotNone(capture.http("hkab-hibor", "HKAB", url="https://x", body=key.encode(), key=key))
        self.assertEqual(capture.stats["written"], 3)
        self.assertIsNone(capture.disabled_reason)

    def test_a_hung_upload_fails_after_each_try_waits_its_allowance(self):
        release = threading.Event()
        self.addCleanup(release.set)

        def hung(account_url, container):
            def upload(name, content, **_options):
                release.wait(60)
            return upload

        capture = RawCapture(container="bronze-market-data", account_url=ACCOUNT_URL, uploader_factory=hung,
                             required=True, attempts=2, write_wait_seconds=0.2, sleep=lambda _s: None)
        started = time.monotonic()
        with self.assertLogs("ibkr_etl.raw_capture", level="ERROR"):
            with self.assertRaisesRegex(raw_capture.RawCopyNotStored, "TimeoutError"):
                capture.http("hkab-hibor", "HKAB", url="https://x", body=b"{}", key="a")
        self.assertLess(time.monotonic() - started, 2 * 0.2 + SCHEDULING_SLACK)

    def test_a_response_that_cannot_be_serialised_or_an_unknown_feed_fails_too(self):
        capture = self._required(_Uploads())

        class Broken:
            def to_csv(self):
                raise ValueError("not a frame")

        with self.assertLogs("ibkr_etl.raw_capture", level="WARNING"):
            with self.assertRaisesRegex(raw_capture.RawCopyNotStored, "ValueError"):
                capture.frame("yahoo-benchmark-history", "Yahoo", frame=Broken(), request={}, key="k")
            with self.assertRaises(raw_capture.RawCopyNotStored):
                capture.http("not-a-feed", "Nobody", url="https://x", body=b"{}", key="k")

    def test_the_benchmark_fetch_fails_before_parsing_when_its_copy_is_not_kept(self):
        from ibkr_etl.provisional_benchmark import BENCHMARKS, fetch_benchmark_yfinance

        ticker = _Ticker(_frame())
        raw_capture.reset(self._required(_Uploads(ConnectionError("storage down"))))
        with self.assertLogs("ibkr_etl.raw_capture", level="ERROR"):
            with self.assertRaises(raw_capture.RawCopyNotStored):
                fetch_benchmark_yfinance(BENCHMARKS[0], date(2025, 1, 20), date(2025, 1, 23), lambda _s: ticker)
        working = _Uploads()
        raw_capture.reset(self._required(working))
        observations = fetch_benchmark_yfinance(BENCHMARKS[0], date(2025, 1, 20), date(2025, 1, 23),
                                                lambda _s: ticker)
        self.assertEqual([day for day, _ in observations], ["2025-01-21", "2025-01-22", "2025-01-23"])
        self.assertEqual(len(working.calls), 1)

    def test_the_treasury_fetch_fails_when_its_copy_is_not_kept(self):
        from ibkr_etl import risk_free

        raw_capture.reset(self._required(_Uploads(ConnectionError("storage down"))))
        with patch.object(risk_free, "_get", return_value=b"<feed/>"):
            with self.assertLogs("ibkr_etl.raw_capture", level="ERROR"):
                with self.assertRaises(raw_capture.RawCopyNotStored):
                    risk_free.fetch_treasury_3m_rates(date(2026, 10, 1), date(2026, 10, 2))
