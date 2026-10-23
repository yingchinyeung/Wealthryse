from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from ibkr_etl import kiwoom_bronze
from ibkr_etl.kiwoom_bronze import KiwoomBronze, request_key
from ibkr_etl.kiwoom_client import INQUIRY_APIS, KiwoomClient, KiwoomError, RawAnswer
from ibkr_etl.kiwoom_collector import run

APP_KEY = "synthetic-app-key-7f3a"
APP_SECRET = "synthetic-app-secret-91c2"
TOKEN = "synthetic-access-token-55d0"
ACCOUNT = "5234567890"


class FakeKiwoom:
    """Answers every inquiry with raw bytes (odd spacing on purpose), two pages for kt00015."""

    def __init__(self, refuse=None):
        self.refuse = refuse or set()
        self.now = datetime(2026, 10, 23, 7, 30)
        self.pages_left = {"kt00015": 1}

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def __call__(self, url, headers, body):
        if url.endswith("/oauth2/token"):
            return 200, {}, {"return_code": 0, "token": TOKEN, "expires_dt": "20261024090000"}
        if url.endswith("/oauth2/revoke"):
            return 200, {}, {"return_code": 0}
        api_id = headers["api-id"]
        if api_id in self.refuse:
            raw = b'{"return_code": 2, "return_msg": "synthetic refusal"}'
            return 200, {}, RawAnswer(json.loads(raw), raw)
        raw = ('{ "return_code" : 0,  "api" : "%s", "page_from" : "%s" }'
               % (api_id, headers.get("next-key", ""))).encode()
        more = {}
        if self.pages_left.get(api_id):
            self.pages_left[api_id] -= 1
            more = {"cont-yn": "Y", "next-key": "nk-1", "authorization": "Bearer leaked?"}
        else:
            more = {"cont-yn": "N"}
        return 200, more, RawAnswer(json.loads(raw), raw)


class FakeCursor:
    def __init__(self, row=None, rowcount=1):
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class FakeDatabase:
    """Records every statement and parameter the bronze writer sends."""

    def __init__(self, fail_on=None):
        self.statements = []
        self.fail_on = fail_on
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._next_run = 900
        self.pages = set()

    def execute(self, sql, parameters=()):
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("synthetic database failure")
        if "ON CONFLICT" in sql:
            page = (parameters[0], parameters[3], parameters[4], parameters[5])
            if page in self.pages:
                return FakeCursor(rowcount=0)
            self.pages.add(page)
        self.statements.append((sql, tuple(parameters)))
        if "RETURNING run_id" in sql:
            self._next_run += 1
            return FakeCursor({"run_id": self._next_run})
        return FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True

    def rows(self):
        return [p for sql, p in self.statements if "bronze.kiwoom_responses" in sql]


TARGET = SimpleNamespace(connection_id="00000000-0000-0000-0000-00000000c0de", broker_account_id=ACCOUNT)


def _client(fake):
    return KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=fake,
                        clock=fake.clock, sleep=fake.sleep)


def _captured(database, fake=None, apis=None):
    bronze = KiwoomBronze(lambda: database, "00000000-0000-0000-0000-000000000001")
    capture = bronze.for_target(TARGET)
    client = _client(fake or FakeKiwoom())
    client.set_capture(capture)
    for api_id in apis or INQUIRY_APIS:
        try:
            client.inquire(api_id, {"qry_dt": "20261022", "stk_cd": "SYN"})
        except KiwoomError:
            pass
    client.revoke()
    capture.finish(True)
    return bronze, capture


class SwitchTests(unittest.TestCase):
    def test_off_unless_switched_on(self):
        self.assertFalse(kiwoom_bronze.enabled({}))
        self.assertFalse(kiwoom_bronze.enabled({"KIWOOM_BRONZE": "false"}))
        self.assertTrue(kiwoom_bronze.enabled({"KIWOOM_BRONZE": "true"}))
        self.assertIsNone(KiwoomBronze.from_environment(lambda: self.fail("connected"), "t", {}))

    def test_a_client_without_capture_behaves_as_before(self):
        pages = _client(FakeKiwoom()).inquire("kt00015", {"strt_dt": "20261001"})
        self.assertEqual([p["page_from"] for p in pages], ["", "nk-1"])


class CaptureTests(unittest.TestCase):
    def test_every_inquiry_api_is_captured_page_by_page(self):
        database = FakeDatabase()
        bronze, capture = _captured(database)
        rows = database.rows()
        self.assertEqual({r[3] for r in rows}, set(INQUIRY_APIS))
        self.assertEqual(len(rows), len(INQUIRY_APIS) + 1)  # kt00015 has two pages
        pages = [(r[5], r[6], r[7]) for r in rows if r[3] == "kt00015"]
        self.assertEqual(pages, [(1, "Y", "nk-1"), (2, "N", None)])
        self.assertTrue(all(r[0] == capture.run_id for r in rows))
        self.assertEqual(bronze.summary(), {"enabled": True, "required": False, "runs": 1, "written": len(rows), "repeated": 0,
                                            "failed": 0, "skipped": 0})

    def test_the_body_is_kept_exactly_as_received_with_its_sha256(self):
        database = FakeDatabase()
        _captured(database, apis=["ust21132"])
        (row,) = database.rows()
        self.assertEqual(row[12], b'{ "return_code" : 0,  "api" : "ust21132", "page_from" : "" }')
        self.assertEqual(row[13], hashlib.sha256(row[12]).hexdigest())
        self.assertEqual(json.loads(row[4]), {"account": ACCOUNT, "qry_dt": "20261022", "stk_cd": "SYN"})
        self.assertLessEqual(row[8], row[9])
        self.assertEqual((row[10], row[11]), (200, 0))

    def test_a_refused_call_is_stored_and_still_refused(self):
        database = FakeDatabase()
        _captured(database, fake=FakeKiwoom(refuse={"ka01690"}), apis=["ka01690"])
        (row,) = database.rows()
        self.assertEqual((row[3], row[10], row[11]), ("ka01690", 200, 2))

    def test_no_key_secret_or_token_reaches_any_row(self):
        database = FakeDatabase()
        _captured(database)
        written = repr(database.statements)
        for secret in (APP_KEY, APP_SECRET, TOKEN, "Bearer"):
            self.assertNotIn(secret, written)
        self.assertNotIn("oauth2", written)

    def test_a_credential_field_never_goes_into_request_key(self):
        for name in ("appkey", "secretkey", "token", "authorization"):
            with self.assertRaises(ValueError):
                request_key(ACCOUNT, {name: "x"})
        self.assertEqual(request_key(ACCOUNT, {"next_key": "", "base_dt": "20261022"}),
                         {"account": ACCOUNT, "next_key": "", "base_dt": "20261022"})

    def test_the_run_row_closes_with_the_page_count_and_combined_sha256(self):
        database = FakeDatabase()
        _captured(database, apis=["kt00015"])
        insert, update = [p for sql, p in database.statements if "ingestion_runs" in sql]
        self.assertEqual(insert[0], "kiwoom_rest_bronze")
        digests = [r[13] for r in database.rows()]
        self.assertEqual(update[0], "success")
        self.assertEqual(update[2], 2)
        self.assertEqual(update[3], hashlib.sha256("\n".join(digests).encode()).hexdigest())


class RepeatTests(unittest.TestCase):
    def test_the_same_request_twice_in_a_run_keeps_its_first_answer(self):
        database = FakeDatabase()
        bronze, _ = _captured(database, apis=["kt00004", "kt00004"])
        self.assertEqual(len(database.rows()), 1)
        self.assertEqual((bronze.stats["written"], bronze.stats["repeated"], bronze.enabled), (1, 1, True))


class FailureTests(unittest.TestCase):
    def test_a_failed_bronze_write_never_fails_the_inquiry(self):
        database = FakeDatabase(fail_on="bronze.kiwoom_responses")
        bronze = KiwoomBronze(lambda: database, "t")
        client = _client(FakeKiwoom())
        client.set_capture(bronze.for_target(TARGET))
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING") as logs:
            pages = client.inquire("kt00015", {"strt_dt": "20261001"})
        self.assertEqual(len(pages), 2)
        self.assertEqual((bronze.stats["failed"], bronze.stats["skipped"], bronze.enabled), (1, 1, False))
        self.assertEqual(len(logs.output), 1)
        self.assertNotIn(ACCOUNT, logs.output[0])

    def test_no_database_means_no_capture_and_the_load_goes_on(self):
        def unreachable():
            raise ConnectionError("synthetic")

        bronze = KiwoomBronze(unreachable, "t")
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"):
            self.assertIsNone(bronze.for_target(TARGET))
        self.assertIsNone(bronze.for_target(TARGET))
        self.assertEqual(bronze.stats["failed"], 1)


class FlakyDatabase(FakeDatabase):
    """Fails the first ``times`` bronze inserts, then works."""

    def __init__(self, times):
        super().__init__()
        self.times = times

    def execute(self, sql, parameters=()):
        if "bronze.kiwoom_responses" in sql and self.times:
            self.times -= 1
            raise RuntimeError("synthetic dropped connection")
        return super().execute(sql, parameters)


def _required(database, **options):
    return KiwoomBronze(lambda: database, "t", required=True, sleep=lambda _: None, **options)


class RequiredTests(unittest.TestCase):
    def test_switch(self):
        self.assertFalse(kiwoom_bronze.required({}))
        self.assertTrue(kiwoom_bronze.required({"KIWOOM_BRONZE_REQUIRED": "TRUE"}))
        made = KiwoomBronze.from_environment(lambda: None, "t", {"KIWOOM_BRONZE": "true",
                                                                 "KIWOOM_BRONZE_REQUIRED": "true"})
        self.assertTrue(made.required)
        self.assertFalse(KiwoomBronze.from_environment(lambda: None, "t", {"KIWOOM_BRONZE": "true"}).required)
        self.assertIsNone(KiwoomBronze.from_environment(lambda: None, "t", {"KIWOOM_BRONZE_REQUIRED": "true"}))

    def test_a_page_that_cannot_be_stored_fails_its_call_before_the_answer_is_used(self):
        database = FakeDatabase(fail_on="bronze.kiwoom_responses")
        bronze = _required(database)
        client = _client(FakeKiwoom())
        client.set_capture(bronze.for_target(TARGET))
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING") as logs, \
                self.assertRaises(kiwoom_bronze.BronzeNotStored) as raised:
            client.inquire("kt00015", {"strt_dt": "20261001"})
        self.assertNotIsInstance(raised.exception, KiwoomError)
        self.assertNotIn(ACCOUNT, str(raised.exception) + "".join(logs.output))
        self.assertEqual((bronze.stats["failed"], bronze.enabled), (1, False))
        # Every later call fails at once, without another try.
        with self.assertRaises(kiwoom_bronze.BronzeNotStored):
            client.inquire("kt00004", {"qry_tp": "0"})
        with self.assertRaises(kiwoom_bronze.BronzeNotStored):
            bronze.for_target(TARGET)

    def test_a_write_that_fails_once_is_tried_again_on_a_fresh_connection(self):
        database = FlakyDatabase(times=2)
        pauses = []
        bronze = KiwoomBronze(lambda: database, "t", required=True, sleep=pauses.append)
        client = _client(FakeKiwoom())
        capture = bronze.for_target(TARGET)
        client.set_capture(capture)
        pages = client.inquire("kt00015", {"strt_dt": "20261001"})
        self.assertEqual(len(pages), 2)
        self.assertEqual(pauses, list(kiwoom_bronze.REQUIRED_RETRY_PAUSES))
        self.assertEqual((bronze.stats["written"], bronze.stats["failed"], bronze.enabled), (2, 0, True))
        self.assertGreaterEqual(database.rollbacks, 2)

    def test_a_retry_that_finds_its_own_page_counts_it_once(self):
        database = FakeDatabase()
        bronze = _required(database)
        capture = bronze.for_target(TARGET)
        real = database.execute
        lost = {"once": True}

        def execute(sql, parameters=()):
            cursor = real(sql, parameters)
            if "bronze.kiwoom_responses" in sql and lost.pop("once", False):
                raise RuntimeError("synthetic: stored, but the commit's answer was lost")
            return cursor

        database.execute = execute
        client = _client(FakeKiwoom())
        client.set_capture(capture)
        client.inquire("kt00004", {"qry_tp": "0"})
        capture.finish(True)
        self.assertEqual((bronze.stats["written"], bronze.stats["repeated"]), (1, 0))
        self.assertEqual(len(database.rows()), 1)

    def test_no_database_fails_the_account_before_kiwoom_is_called(self):
        def unreachable():
            raise ConnectionError("synthetic")

        bronze = KiwoomBronze(unreachable, "t", required=True, sleep=lambda _: None)
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"), \
                self.assertRaises(kiwoom_bronze.BronzeNotStored):
            bronze.for_target(TARGET)
        self.assertEqual(bronze.summary()["required"], True)

    def test_a_request_that_cannot_go_into_bronze_fails_its_call(self):
        bronze = _required(FakeDatabase())
        client = _client(FakeKiwoom())
        client.set_capture(bronze.for_target(TARGET))
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"), \
                self.assertRaises(kiwoom_bronze.BronzeNotStored):
            client.inquire("kt00004", {"qry_tp": "0", "appkey": "x"})

    def test_the_collector_script_collects_nothing_when_required_without_bronze(self):
        import importlib.util
        import io
        import os
        from contextlib import redirect_stdout
        from pathlib import Path
        from unittest import mock

        path = Path(__file__).resolve().parents[1] / "scripts" / "run_kiwoom_collector.py"
        spec = importlib.util.spec_from_file_location("run_kiwoom_collector_required", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        environ = {"KIWOOM_COLLECTOR_ENABLED": "true", "DEPLOYMENT_ENVIRONMENT": "internal-production",
                   "FLEX_TOKEN_VAULT_URI": "https://vault.invalid", "AZURE_CLIENT_ID": "synthetic",
                   "KIWOOM_BRONZE_REQUIRED": "true"}
        out = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), redirect_stdout(out), \
                mock.patch.object(script, "run", side_effect=AssertionError("collected")):
            self.assertEqual(script.main(), 0)
        printed = json.loads(out.getvalue())
        self.assertEqual(printed["status"], "error")
        self.assertIn("KIWOOM_BRONZE_REQUIRED", printed["error"])

    def test_best_effort_is_unchanged(self):
        database = FakeDatabase(fail_on="bronze.kiwoom_responses")
        bronze = KiwoomBronze(lambda: database, "t", sleep=lambda _: self.fail("best effort never waits"))
        client = _client(FakeKiwoom())
        client.set_capture(bronze.for_target(TARGET))
        with self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"):
            self.assertEqual(len(client.inquire("kt00015", {"strt_dt": "20261001"})), 2)
        self.assertEqual(bronze.summary()["required"], False)


class CollectorTests(unittest.TestCase):
    def test_the_collector_hands_each_account_a_capture_and_closes_its_run(self):
        from unittest import mock

        from ibkr_etl import kiwoom_collector

        database = FakeDatabase()
        bronze = KiwoomBronze(lambda: database, "t")
        target = SimpleNamespace(connection_id=TARGET.connection_id, broker_account_id=ACCOUNT, case_id=None,
                                 stage=None, history_start=None)
        fake = FakeKiwoom()

        class Valuing(KiwoomClient):
            def valuation(self):
                self.inquire("kt00004", {"qry_tp": "0"})
                return SimpleNamespace(account_name="Synthetic")

        client = Valuing(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=fake,
                         clock=fake.clock, sleep=fake.sleep)
        with mock.patch.object(kiwoom_collector, "targets", return_value=[target]), \
                mock.patch.object(kiwoom_collector, "_connection_result"):
            results = run(SimpleNamespace(commit=lambda: None, rollback=lambda: None), "t",
                          client_for=lambda _: client, today=date(2026, 10, 23),
                          now=datetime(2026, 10, 23), bronze=bronze)
        self.assertEqual(results[0]["status"], "checked")
        self.assertEqual(results[0]["bronze_run_id"], 901)
        self.assertEqual([r[3] for r in database.rows()], ["kt00004"])
        update = [p for sql, p in database.statements if sql.startswith("UPDATE ingestion_runs")]
        self.assertEqual(update[0][:1] + update[0][2:3], ("success", 1))

    def test_required_capture_that_cannot_open_its_run_fails_the_account_without_calling_kiwoom(self):
        from unittest import mock

        from ibkr_etl import kiwoom_collector

        def unreachable():
            raise ConnectionError("synthetic")

        bronze = KiwoomBronze(unreachable, "t", required=True, sleep=lambda _: None)
        target = SimpleNamespace(connection_id=TARGET.connection_id, broker_account_id=ACCOUNT, case_id=None,
                                 stage=None, history_start=None)
        fake = FakeKiwoom()
        calls = []

        def transport(url, headers, body):
            calls.append(url)
            return fake(url, headers, body)

        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=transport,
                              clock=fake.clock, sleep=fake.sleep)
        with mock.patch.object(kiwoom_collector, "targets", return_value=[target]), \
                mock.patch.object(kiwoom_collector, "_connection_result") as recorded, \
                self.assertLogs("ibkr_etl.kiwoom_bronze", "WARNING"):
            results = run(SimpleNamespace(commit=lambda: None, rollback=lambda: None), "t",
                          client_for=lambda _: client, today=date(2026, 10, 23),
                          now=datetime(2026, 10, 23), bronze=bronze)
        self.assertEqual(results[0]["status"], "failed")
        self.assertTrue(results[0]["error"].startswith("BronzeNotStored"))
        self.assertNotIn(ACCOUNT, results[0]["error"])
        self.assertFalse([url for url in calls if not url.endswith(("/oauth2/token", "/oauth2/revoke"))])
        self.assertFalse(recorded.call_args.kwargs["ok"])


if __name__ == "__main__":
    unittest.main()
