"""The Kiwoom conform check (ibkr_etl.kiwoom_conform): replay a bronze run's stored answers through the
collector's own functions and compare with silver. Synthetic accounts and answers only; Kiwoom is never called.

The live side is the collector's real run(): a KiwoomClient over a synthetic Kiwoom, its pages captured by
the real KiwoomBronze writer, and its silver writes kept by a recording database.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from ibkr_etl import kiwoom_client, kiwoom_collector
from ibkr_etl.kiwoom_bronze import TABLE, KiwoomBronze
from ibkr_etl.kiwoom_client import REVOKE_PATH, TOKEN_PATH, KiwoomClient, KiwoomError, KiwoomUnknownStock, RawAnswer
from ibkr_etl.kiwoom_collector import _KEYS, Target
from ibkr_etl.kiwoom_conform import (
    REPLAY_TOKEN,
    MissingAnswer,
    ReplayTransport,
    check_run,
    compare,
    rebuild,
    replay_client,
    summary,
)

ROOT = Path(__file__).resolve().parents[1]
TENANT = "00000000-0000-0000-0000-000000000001"
ACCOUNT = "5234567890"  # synthetic, reported in KRW
US_ACCOUNT = "6234567890"  # synthetic, reported in USD
APP_KEY = "synthetic-app-key-4d1e"
APP_SECRET = "synthetic-app-secret-8b2f"
LIVE_TOKEN = "synthetic-live-token-c3a9"
NOW = datetime(2026, 10, 20, 8, 20, tzinfo=timezone.utc)  # Tuesday, 17:20 in Seoul: the daily run
STARTED = NOW + timedelta(seconds=3)  # the bronze run opens a moment after the collector's now
TODAY = date(2026, 10, 20)
START = date(2026, 10, 14)
CLOSED = date(2026, 10, 16)  # a KRX holiday in this synthetic history
AFTER_MARKET = date(2026, 10, 19)  # ka01690 lists the after-market price; the close comes from ka10081
BOOKKEEPING = ("row_sha256", "last_seen_run_id")


def _raw(payload) -> bytes:
    # Kiwoom's spacing, not Python's: the replay must hand back these exact bytes.
    return json.dumps(payload, ensure_ascii=False, separators=(" , ", " : ")).encode("utf-8")


OK = {"return_code": 0, "return_msg": "정상적으로 처리되었습니다"}
US_CLOSES = {"20261014": "300.0000", "20261015": "301.0000", "20261016": "302.0000", "20261019": "305.0000"}


class LiveKiwoom:
    """A synthetic Kiwoom for one KRW and one USD account, shaped like the live answers.

    kt00015 runs to three pages (next-keys NK-1, NK-2). Kiwoom does not know the U.S. stock on NASDAQ (its
    1903 refusal) and answers on NYSE. ``refuse`` names api-ids answered with a refusal; ``refuse_day`` refuses
    ka01690 for one day only.
    """

    def __init__(self, refuse=(), refuse_day=None):
        self.refuse, self.refuse_day = set(refuse), refuse_day
        self.now = NOW.replace(tzinfo=None)  # the live client's clock is naive, in the container's UTC
        self.headers = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def __call__(self, url, headers, body):
        self.headers.append(dict(headers))
        if url.endswith(TOKEN_PATH):
            return 200, {}, {"return_code": 0, "token": LIVE_TOKEN, "expires_dt": "20261021090000"}
        if url.endswith(REVOKE_PATH):
            return 200, {}, {"return_code": 0}
        api_id, request = headers["api-id"], json.loads(body)
        if api_id in self.refuse or (api_id == "ka01690" and request["qry_dt"] == self.refuse_day):
            return self._page(200, {}, {"return_code": 2, "return_msg": "synthetic refusal [2000:no data]"})
        if api_id == "usa06012" and request["stex_tp"] == "ND":
            return self._page(200, {}, {"return_code": 7, "return_msg": "error[1903:no such stock, stex_tp=ND]"})
        pages = self.pages(api_id, request)
        index = int(headers["next-key"].split("-")[1]) if headers.get("next-key") else 0
        paging = {"cont-yn": "Y", "next-key": f"NK-{index + 1}"} if index + 1 < len(pages) else {"cont-yn": "N"}
        return self._page(200, paging, pages[index])

    @staticmethod
    def _page(status, paging, payload):
        raw = _raw(payload)
        return status, paging, RawAnswer(json.loads(raw), raw)

    def pages(self, api_id, request):
        if api_id == "kt00004":
            return [{**OK, "acnt_nm": "Synthetic", "entr": "000000001234567", "d2_entra": "000000001234567",
                     "prsm_dpst_aset_amt": "000000008222517"}]
        if api_id == "kt00018":
            return [{**OK, "acnt_evlt_remn_indv_tot": [
                {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "000000000100", "pur_pric": "000000060000",
                 "cur_prc": "+000000070000", "evlt_amt": "000000007000000"}]}]
        if api_id == "ust21070":
            return [{**OK, "crnc_code": "USD", "tot_evlt_amt": "610.0000", "result_list": [
                {"stex_nm": "NYSE", "crnc_code": "USD", "stk_cd": "AMGN", "frgn_stk_nm": "암젠",
                 "poss_qty": "000000000002", "frgn_stk_book_uv": "300.00", "now_pric": "+305.00",
                 "evlt_amt": "610.00", "evlt_amt_krw": "000000854000"}]}]
        if api_id in ("ust21120", "ust21132"):
            day = request.get("base_dt", "20261020")
            value = "615.00" if day == "20261019" else f"{2 * float(US_CLOSES.get(day, '305')):.2f}"
            return [{**OK, "result_list": [{"crnc_code": "USD", "fx_entr": "40.00", "evlt_amt": value,
                                            "crnc_rt": "1400.00"}]}]
        if api_id == "kt00015":
            def row(day, label, code, quantity, amount):
                return {"trde_dt": day, "rmrk_nm": "", "io_tp_nm": label, "stk_cd": code, "trde_qty_jwa_cnt": quantity,
                        "exct_amt": amount, "crnc_cd": "KRW"}
            return [{**OK, "trst_ovrl_trde_prps_array": [row("20261014", "입금", "", "0", "000000007000000")]},
                    {**OK, "trst_ovrl_trde_prps_array": [row("20261015", "매수", "A005930", "100", "000006000000")]},
                    {**OK, "trst_ovrl_trde_prps_array": [row("20261019", "배당금입금", "A005930", "0", "000000036100"),
                                                         row("20261019", "배당금입금", "A005930", "0", "000000036100")]}]
        if api_id == "ust21100":
            def row(day, kind, label, quantity, amount, cash, deal):
                return {"deal_dt": day, "deal_kind_nm": kind, "rmrk_nm": label, "stk_cd": "AMGN", "deal_qty": quantity,
                        "crnc_code": "USD", "fc_exct_amt": amount, "exct_amt": "000000840000", "fc_entra": cash,
                        "proc_time": "08:11:40", "deal_no": deal}
            # The dividend leaves Kiwoom's cash 100 USD above the cash before it: a deposit (balance_gaps).
            return [{**OK, "result_list": [row("20261016", "매매", "매수", "2", "600.00", "40.00", "000000003"),
                                           row("20261019", "입출금", "배당금(외화)입금", "0", "1.00", "141.00",
                                               "000000001")]}]
        if api_id == "ka01690":
            day = request["qry_dt"]
            answered = "20261015" if day == CLOSED.strftime("%Y%m%d") else day  # a closed day: another day's figures
            return [{**OK, "dt": answered, "dbst_bal": "000000001234567", "day_stk_asst": "000000008222517",
                     "day_bal_rt": [{"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "100", "buy_uv": "60000",
                                     "cur_prc": "+70000", "evlt_amt": "7000000"}]}]
        if api_id == "kt00016":
            held = "000000007100000" if request["fr_dt"] == AFTER_MARKET.strftime("%Y%m%d") else "000000007000000"
            return [{**OK, "entr_to": "000000001234567", "scrt_evlt_amt_to": held}]
        if api_id == "ka10081":
            # 19 Oct's official close is 20 Oct's base price: 72,000 less its +1,000 change.
            return [{**OK, "stk_cd": request["stk_cd"], "stk_dt_pole_chart_qry": [
                {"dt": "20261020", "cur_prc": "72000", "pred_pre": "+1000"},
                {"dt": "20261019", "cur_prc": "70000", "pred_pre": "-500"},
                {"dt": "20261016", "cur_prc": "70500", "pred_pre": "0"}]}]
        if api_id == "usa06012":
            return [{**OK, "result_list": [{"dt": day, "cur_prc": close}
                                           for day, close in sorted(US_CLOSES.items(), reverse=True)]}]
        if api_id == "usa21730":
            return [{**OK, "result_list": [
                {"frst_base_dt": "2026-10-13", "last_base_dt": "2026-10-14", "frst_qty": "0", "last_qty": "2"},
                {"frst_base_dt": "2026-10-14", "last_base_dt": "2026-10-17", "frst_qty": "2", "last_qty": "2"}]}]
        raise AssertionError(f"no synthetic answer for {api_id}")


class _Cursor:
    def __init__(self, rows=(), rowcount=None):
        self._rows = list(rows)
        self.rowcount = len(self._rows) if rowcount is None else rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class BronzeRecorder:
    """The bronze writer's database: keeps each page as the row bronze.kiwoom_responses would hold."""

    COLUMNS = ("run_id", "tenant_id", "connection_id", "api_id", "request_key", "page_no", "cont_yn", "next_key",
               "requested_at", "received_at", "http_status", "return_code", "body", "body_sha256")

    def __init__(self):
        self.runs, self.rows = {}, []

    def execute(self, sql, parameters=()):
        if sql.startswith("INSERT INTO ingestion_runs"):
            run_id = len(self.runs) + 1
            self.runs[run_id] = {"run_id": run_id, "run_type": parameters[0], "started_at": parameters[3],
                                 "status": "running"}
            return _Cursor([{"run_id": run_id}])
        if sql.startswith(f"INSERT INTO {TABLE}"):
            row = dict(zip(self.COLUMNS, parameters))
            page = (row["run_id"], row["api_id"], row["request_key"], row["page_no"])
            if any((r["run_id"], r["api_id"], r["request_key"], r["page_no"]) == page for r in self.rows):
                return _Cursor(rowcount=0)
            self.rows.append(row)
            return _Cursor()
        if sql.startswith("UPDATE ingestion_runs"):
            self.runs[parameters[4]]["status"] = parameters[0]
            return _Cursor()
        raise AssertionError("unexpected bronze statement")

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def pages(self, run_id):
        return [row for row in self.rows if row["run_id"] == run_id]


UPSERT = re.compile(r"INSERT INTO (\w+) \(([^)]*)\) VALUES \([^)]*\) ON CONFLICT")
SELECT_SILVER = re.compile(r"SELECT \* FROM (\w+) WHERE account_id = \? AND (\w+) BETWEEN \? AND \?$")


class SilverRecorder:
    """The collector's database: every read finds nothing, and the six silver tables keep what is upserted."""

    def __init__(self):
        self.tables = {table: {} for table in _KEYS}
        self.runs = 0

    def execute(self, sql, parameters=()):
        if "RETURNING run_id" in sql:
            self.runs += 1
            return _Cursor([{"run_id": self.runs}])
        found = UPSERT.match(sql)
        if found and found.group(1) in self.tables:
            table = found.group(1)
            row = dict(zip([name.strip() for name in found.group(2).split(",")], parameters))
            self.tables[table][tuple(row[name] for name in _KEYS[table])] = row
        return _Cursor()

    def commit(self):
        pass

    def rollback(self):
        pass

    def silver(self):
        return {table: [dict(row) for row in rows.values()] for table, rows in self.tables.items()}


def _target(account, currency):
    return Target(connection_id="00000000-0000-0000-0000-00000000c0de", environment="mock",
                  secret_name="broker-kiwoom-rest-synthetic", broker_account_id=account, case_id="case-" + account,
                  stage="load_and_reconcile", display_name="Synthetic", history_start=START,
                  reporting_currency=currency)


def collector_run(live_for=lambda account: LiveKiwoom(), accounts=((ACCOUNT, "KRW"), (US_ACCOUNT, "USD"))):
    """The collector's daily run for the accounts, with bronze capture on. Returns (bronze, silver, results)."""

    bronze_db, silver = BronzeRecorder(), SilverRecorder()
    bronze = KiwoomBronze(lambda: bronze_db, TENANT, clock=lambda: STARTED)

    def client_for(target):
        live = live_for(target.broker_account_id)
        return KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=live,
                            clock=live.clock, sleep=live.sleep, min_interval_seconds=0)

    with mock.patch.object(kiwoom_collector, "targets", return_value=[_target(*item) for item in accounts]), \
            mock.patch.object(kiwoom_collector, "_connection_result"), \
            mock.patch.object(kiwoom_collector, "_record", return_value="load_and_reconcile"):
        results = kiwoom_collector.run(silver, TENANT, client_for=client_for, today=TODAY, now=NOW,
                                       include_us=True, bronze=bronze)
    return bronze_db, silver, results


def _without_bookkeeping(rows):
    return sorted((json.dumps({k: v for k, v in row.items() if k not in BOOKKEEPING}, sort_keys=True)
                   for row in rows))


def _replayed(bronze_db, run_id, started_at=STARTED, drop=lambda row: False):
    transport = ReplayTransport(row for row in bronze_db.pages(run_id) if not drop(row))
    return transport, rebuild(transport, started_at=started_at, known={})


class ReplayEqualsLiveTests(unittest.TestCase):
    """The rows rebuilt from bronze are the rows the collector wrote from the live answers."""

    @classmethod
    def setUpClass(cls):
        cls.bronze, cls.silver, cls.results = collector_run()

    def test_both_accounts_loaded_with_a_bronze_run_each(self):
        self.assertEqual([item["status"] for item in self.results], ["loaded", "loaded"])
        self.assertEqual({run["status"] for run in self.bronze.runs.values()}, {"success"})
        self.assertEqual(len(self.bronze.runs), 2)

    def _check(self, run_id, account_id, market):
        transport, result = _replayed(self.bronze, run_id)
        self.assertEqual((result.kind, result.market, result.account_id), ("load", market, account_id))
        self.assertEqual(result.parts, {"movements": "rebuilt", "history": "rebuilt"})
        self.assertTrue(result.day_matches_requests)
        silver = {table: [row for row in rows if row["account_id"] == account_id]
                  for table, rows in self.silver.silver().items()}
        for table in _KEYS:
            with self.subTest(table=table):
                self.assertTrue(silver[table], "the synthetic run writes every table")
                self.assertEqual(_without_bookkeeping(result.rows[table]), _without_bookkeeping(silver[table]))
        tables = compare(result, silver)
        for table, counts in tables.items():
            self.assertEqual((counts["matched"], counts["differ"], counts["only_in_rebuild"], counts["only_in_silver"]),
                             (len(silver[table]), 0, 0, 0), table)
        answers = transport.summary()
        self.assertEqual((answers["served"], answers["unused"], answers["missing"]), (answers["stored"], 0, 0))
        self.assertTrue(summary(result, transport, tables)["clean"])
        return transport, result

    def test_a_krw_account(self):
        transport, result = self._check(1, "KIWOOM-" + ACCOUNT, "KRW")
        # 14, 15 and 19 Oct replayed; 16 Oct Kiwoom answered with another day: closed.
        self.assertEqual(result.history, {"asked": 4, "replayed": 3, "closed": 1, "missing": 0})
        self.assertEqual(result.days, ("2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19"))
        # 19 Oct is priced at the official close from the chart, as the collector priced it.
        prices = {row["snapshot_date"]: row["mark_price_text"] for row in result.rows["position_snapshots"]
                  if row["conid"] == "KRX:005930"}
        self.assertEqual(prices["2026-10-19"], "71000")
        self.assertEqual(transport.summary()["refusals_replayed"], 0)

    def test_a_usd_account(self):
        transport, result = self._check(2, "KIWOOM-" + US_ACCOUNT, "USD")
        # 19 Oct's positions do not add up to Kiwoom's total: the collector could have kept older positions.
        self.assertEqual(result.history, {"positions_not_from_answers": 1, "asked": 4, "replayed": 4, "closed": 0,
                                          "missing": 0})
        # Kiwoom's 1903 refusal on NASDAQ is replayed, and the stock is found on NYSE, as it was live.
        self.assertEqual(transport.summary()["refusals_replayed"], 1)
        self.assertIn("DEPOSIT", {row["transaction_type"] for row in result.rows["cash_transactions"]})


class RefusalTests(unittest.TestCase):
    def _capture(self, live, call):
        bronze_db = BronzeRecorder()
        capture = KiwoomBronze(lambda: bronze_db, TENANT).for_target(_target(ACCOUNT, "KRW"))
        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=live,
                              clock=live.clock, sleep=live.sleep, min_interval_seconds=0)
        client.set_capture(capture)
        try:
            call(client)
        except KiwoomError as error:
            return bronze_db.rows, error
        return bronze_db.rows, None

    def test_a_stored_refusal_raises_the_same_error(self):
        rows, live_error = self._capture(LiveKiwoom(refuse={"ka01690"}), lambda c: c.balance_on(date(2026, 10, 14)))
        self.assertIsNotNone(live_error)
        with self.assertRaises(KiwoomError) as replayed:
            replay_client(rows, now=STARTED).balance_on(date(2026, 10, 14))
        self.assertIs(type(replayed.exception), type(live_error))
        self.assertEqual(str(replayed.exception), str(live_error))

    def test_kiwooms_unknown_stock_is_still_its_own_refusal(self):
        rows, live_error = self._capture(LiveKiwoom(), lambda c: c.us_daily_closes("AMGN", "ND", START, TODAY))
        self.assertIsInstance(live_error, KiwoomUnknownStock)
        with self.assertRaises(KiwoomUnknownStock):
            replay_client(rows, now=STARTED).us_daily_closes("AMGN", "ND", START, TODAY)

    def test_a_refusal_that_is_not_json_reads_as_it_did_live(self):
        body = b"<html>\n<body>Bad Request</body></html>"

        class Refusing(LiveKiwoom):
            def __call__(self, url, headers, request):
                if url.endswith(TOKEN_PATH):
                    return super().__call__(url, headers, request)
                # As _urllib_transport hands over an HTTP error whose body is not JSON.
                return 400, {}, RawAnswer({"return_msg": "non-JSON answer: <html> <body>Bad Request</body></html>"},
                                          body)

        rows, live_error = self._capture(Refusing(), lambda c: c.inquire("kt00004", {"qry_tp": "0"}))
        self.assertEqual(bytes(rows[0]["body"]), body)
        with self.assertRaises(KiwoomError) as replayed:
            replay_client(rows, now=STARTED).inquire("kt00004", {"qry_tp": "0"})
        self.assertEqual(str(replayed.exception), str(live_error))

    def test_a_refused_history_is_not_compared_as_the_live_run_wrote_none(self):
        bronze_db, silver, results = collector_run(lambda account: LiveKiwoom(refuse_day="20261015"),
                                                   accounts=((ACCOUNT, "KRW"),))
        self.assertEqual(results[0]["history"]["check"], "fail")
        self.assertEqual(silver.tables["nav_snapshots"], {})  # the history pass rolled back
        transport, result = _replayed(bronze_db, 1)
        self.assertEqual(result.parts, {"movements": "rebuilt", "history": "refused"})
        tables = compare(result, silver.silver())
        self.assertEqual(sorted(tables), ["cash_transactions", "trades"])
        self.assertEqual(transport.summary()["refusals_replayed"], 1)
        self.assertTrue(summary(result, transport, tables)["clean"])

    def test_a_refused_load_writes_nothing_to_compare(self):
        bronze_db, silver, results = collector_run(lambda account: LiveKiwoom(refuse={"kt00015"}),
                                                   accounts=((ACCOUNT, "KRW"),))
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(bronze_db.runs[1]["status"], "failed")
        transport, result = _replayed(bronze_db, 1)
        self.assertEqual(result.parts, {"movements": "refused"})
        self.assertEqual(compare(result, silver.silver()), {})


class MissingAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze, cls.silver, _ = collector_run(accounts=((ACCOUNT, "KRW"),))

    def test_a_missing_past_day_is_counted_and_left_out(self):
        transport, result = _replayed(self.bronze, 1, drop=lambda row: row["api_id"] == "kt00016"
                                      and '"fr_dt": "20261015"' in row["request_key"])
        self.assertEqual(result.parts["history"], "rebuilt")
        self.assertEqual(result.history, {"asked": 4, "replayed": 2, "closed": 1, "missing": 1})
        self.assertNotIn("2026-10-15", result.days)
        self.assertEqual(transport.summary()["missing"], 1)
        tables = compare(result, self.silver.silver())
        self.assertEqual(tables["nav_snapshots"]["only_in_silver"], 0)  # the day is not counted against silver
        self.assertFalse(summary(result, transport, tables)["clean"])

    def test_a_missing_page_leaves_the_movements_uncompared(self):
        transport, result = _replayed(self.bronze, 1, drop=lambda row: row["api_id"] == "kt00015"
                                      and row["page_no"] == 2)
        self.assertEqual(result.parts["movements"], "missing")
        self.assertEqual(result.parts["history"], "rebuilt")
        self.assertNotIn("trades", compare(result, self.silver.silver()))
        self.assertEqual(transport.summary()["missing"], 1)

    def test_a_day_derived_differently_shows_as_missing(self):
        # A day earlier, the movement list would have ended on 19 Oct: the run asked for no such list.
        transport, result = _replayed(self.bronze, 1, started_at=STARTED - timedelta(days=1))
        self.assertFalse(result.day_matches_requests)
        self.assertEqual(result.parts["movements"], "missing")
        self.assertGreater(transport.summary()["unused"], 0)

    def test_a_missing_answer_is_never_fetched(self):
        transport = ReplayTransport(self.bronze.pages(1))
        with mock.patch.object(kiwoom_client.urllib.request, "urlopen", side_effect=AssertionError("fetched")):
            with self.assertRaises(MissingAnswer) as missing:
                replay_client(transport, now=STARTED).balance_on(date(2026, 9, 1))
        self.assertNotIn(ACCOUNT, str(missing.exception))
        self.assertNotIn("20260901", str(missing.exception))
        self.assertEqual(transport.summary()["missing"], 1)


class PagingTests(unittest.TestCase):
    def setUp(self):
        bronze, _, _ = collector_run(accounts=((ACCOUNT, "KRW"),))
        self.pages = bronze.pages(1)
        self.body = {"strt_dt": "20261014", "end_dt": "20261020", "tp": "0", "stk_cd": "", "crnc_cd": "",
                     "gds_tp": "0", "frgn_stex_code": "", "dmst_stex_tp": "%"}

    def test_next_keys_replay_each_page_in_turn_with_its_exact_bytes(self):
        stored = sorted((row for row in self.pages if row["api_id"] == "kt00015"), key=lambda row: row["page_no"])
        self.assertEqual([(row["page_no"], row["cont_yn"], row["next_key"]) for row in stored],
                         [(1, "Y", "NK-1"), (2, "Y", "NK-2"), (3, "N", None)])
        pages = replay_client(self.pages, now=STARTED).inquire("kt00015", self.body)
        self.assertEqual([page.raw for page in pages], [bytes(row["body"]) for row in stored])

    def test_a_page_is_found_by_the_next_key_it_follows(self):
        transport = ReplayTransport(self.pages)
        url = "https://api.kiwoom.com/api/dostk/acnt"
        status, headers, page = transport(url, {"api-id": "kt00015", "cont-yn": "Y", "next-key": "NK-2"},
                                          json.dumps(self.body).encode())
        self.assertEqual((status, headers), (200, {"cont-yn": "N"}))
        self.assertIn(b'"20261019"', page.raw)
        with self.assertRaises(MissingAnswer):
            transport(url, {"api-id": "kt00015", "cont-yn": "Y", "next-key": "NK-9"}, json.dumps(self.body).encode())
        with self.assertRaises(MissingAnswer):  # the same fields asked of another API
            transport(url, {"api-id": "kt00004"}, json.dumps(self.body).encode())


class ConfidentialityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze, cls.silver, _ = collector_run()

    def test_the_placeholder_token_never_leaves_the_replay(self):
        seen = []
        transport = ReplayTransport(self.bronze.pages(1))

        def watching(url, headers, body):
            seen.append(dict(headers))
            return transport(url, headers, body)

        with mock.patch.object(kiwoom_client.urllib.request, "urlopen", side_effect=AssertionError("fetched")):
            client = replay_client(self.bronze.pages(1), now=STARTED)
            client._transport = watching
            client.valuation()
            client.revoke()
        self.assertEqual({headers.get("authorization") for headers in seen if "api-id" in headers},
                         {f"Bearer {REPLAY_TOKEN}"})
        stored = repr(self.bronze.rows)
        for secret in (LIVE_TOKEN, REPLAY_TOKEN, APP_KEY, APP_SECRET, "Bearer"):
            self.assertNotIn(secret, stored)

    def test_results_hold_counts_only(self):
        for run_id in (1, 2):
            transport, result = _replayed(self.bronze, run_id)
            text = json.dumps(summary(result, transport, compare(result, self.silver.silver())), ensure_ascii=False)
            for restricted in (ACCOUNT, US_ACCOUNT, "KIWOOM-", "Synthetic", "삼성전자", "암젠", "AMGN", "005930",
                               "1234567", "7000000", "2026-10", "202610", "NK-", REPLAY_TOKEN, LIVE_TOKEN):
                self.assertNotIn(restricted, text)


class ReadOnlyDatabase:
    """Answers check_run's reads from the recorded bronze and silver; any other statement fails the test."""

    def __init__(self, bronze, silver, *, latest=()):
        self.bronze, self.silver, self.latest, self.statements = bronze, silver, list(latest), []

    def execute(self, sql, parameters=()):
        self.statements.append(sql)
        if sql == "SET TRANSACTION READ ONLY":
            return _Cursor()
        if not sql.startswith("SELECT"):
            raise AssertionError("the conform check only reads")
        if "DISTINCT ON" in sql:
            return _Cursor({"run_id": run_id} for run_id in self.latest)
        if "FROM ingestion_runs" in sql:
            return _Cursor([self.bronze.runs[parameters[0]]] if parameters[0] in self.bronze.runs else [])
        if f"FROM {TABLE}" in sql:
            return _Cursor(self.bronze.pages(parameters[0]))
        if "FROM security_identifiers" in sql:
            return _Cursor()
        found = SELECT_SILVER.match(sql)
        if found:
            table, column = found.groups()
            return _Cursor(row for row in self.silver.tables[table].values()
                           if row["account_id"] == parameters[0] and parameters[1] <= row[column] <= parameters[2])
        raise AssertionError(sql)

    def rollback(self):
        pass

    def close(self):
        pass


class CheckRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze, cls.silver, _ = collector_run()

    def test_a_run_is_checked_with_reads_only(self):
        database = ReadOnlyDatabase(self.bronze, self.silver)
        result = check_run(database, 1)
        self.assertEqual((result["run_id"], result["run_status"], result["kind"], result["clean"]),
                         (1, "success", "load", True))
        self.assertTrue(all(sql.startswith("SELECT") for sql in database.statements))

    def test_silver_changed_since_shows_as_differences(self):
        bronze, silver, _ = collector_run(accounts=((ACCOUNT, "KRW"),))
        trade = next(iter(silver.tables["trades"].values()))
        trade["quantity_text"] = "99"
        silver.tables["cash_snapshots"].pop(next(iter(silver.tables["cash_snapshots"])))
        extra = dict(next(iter(silver.tables["cash_transactions"].values())), source_key="not-from-kiwoom")
        silver.tables["cash_transactions"][("KIWOOM-" + ACCOUNT, "not-from-kiwoom")] = extra
        outside = dict(extra, source_key="before-the-history", effective_date="2026-01-02")
        silver.tables["cash_transactions"][("KIWOOM-" + ACCOUNT, "before-the-history")] = outside
        tables = check_run(ReadOnlyDatabase(bronze, silver), 1)["tables"]
        self.assertEqual((tables["trades"]["differ"], tables["trades"]["differ_columns"]), (1, {"quantity_text": 1}))
        self.assertEqual(tables["cash_snapshots"]["only_in_rebuild"], 1)
        self.assertEqual(tables["cash_transactions"]["only_in_silver"], 1)  # the row outside the run's dates is not

    def test_a_run_that_is_not_kiwoom_bronze_is_refused(self):
        from ibkr_etl.kiwoom_conform import NotABronzeRun

        with self.assertRaises(NotABronzeRun):
            check_run(ReadOnlyDatabase(self.bronze, self.silver), 99)

    def test_a_connection_check_writes_no_silver(self):
        bronze_db = BronzeRecorder()
        live = LiveKiwoom()
        capture = KiwoomBronze(lambda: bronze_db, TENANT).for_target(_target(ACCOUNT, "KRW"))
        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=live,
                              clock=live.clock, sleep=live.sleep, min_interval_seconds=0)
        client.set_capture(capture)
        client.valuation()  # kiwoom_collector.run's check for an account not being onboarded
        transport, result = _replayed(bronze_db, 1)
        self.assertEqual((result.kind, result.parts), ("no_silver", {"check": "rebuilt"}))
        self.assertEqual(transport.summary()["unused"], 0)
        self.assertEqual(compare(result, {}), {})


class ScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bronze, cls.silver, _ = collector_run()
        spec = importlib.util.spec_from_file_location("run_kiwoom_conform", ROOT / "scripts" / "run_kiwoom_conform.py")
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def _main(self, argv, database=None, environ=None):
        environ = {"DATABASE_URL": "postgresql://unused"} if environ is None else environ
        out = io.StringIO()
        with mock.patch.dict(os.environ, environ, clear=True), redirect_stdout(out), \
                mock.patch.object(self.module, "connect", return_value=database) as connect:
            code = self.module.main(argv)
        return code, json.loads(out.getvalue()) if out.getvalue() else None, connect

    def test_latest_checks_each_account_in_one_read_only_transaction(self):
        database = ReadOnlyDatabase(self.bronze, self.silver, latest=[1, 2])
        code, printed, _ = self._main(["check", "--latest"], database)
        self.assertEqual(code, 0)
        self.assertEqual(database.statements[0], "SET TRANSACTION READ ONLY")
        self.assertEqual([run["run_id"] for run in printed["runs"]], [1, 2])
        self.assertEqual((printed["status"], printed["mode"]), ("checked", "read-only"))

    def test_differences_still_exit_zero(self):
        bronze, silver, _ = collector_run(accounts=((ACCOUNT, "KRW"),))
        next(iter(silver.tables["trades"].values()))["net_cash_text"] = "1"
        code, printed, _ = self._main(["check", "--run-id", "1"], ReadOnlyDatabase(bronze, silver))
        self.assertEqual(code, 0)
        self.assertFalse(printed["runs"][0]["clean"])

    def test_usage_and_database_errors(self):
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            for argv in ([], ["check"], ["check", "--latest", "--run-id", "1"]):
                with self.assertRaises(SystemExit) as stopped:
                    self.module.main(argv)
                self.assertEqual(stopped.exception.code, 2)
        self.assertEqual(self._main(["check", "--latest"], environ={})[0], 2)
        code, printed, _ = self._main(["check", "--run-id", "99"], ReadOnlyDatabase(self.bronze, self.silver))
        self.assertEqual((code, printed["status"]), (2, "error"))

        class Broken(ReadOnlyDatabase):
            def execute(self, sql, parameters=()):
                if "FROM ingestion_runs" in sql:
                    raise RuntimeError(f"synthetic failure quoting {ACCOUNT}")
                return super().execute(sql, parameters)

        code, printed, _ = self._main(["check", "--run-id", "1"], Broken(self.bronze, self.silver))
        self.assertEqual((code, printed), (1, {"status": "error", "error": "RuntimeError"}))


if __name__ == "__main__":
    unittest.main()
