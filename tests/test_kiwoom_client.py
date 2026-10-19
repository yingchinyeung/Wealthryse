from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from ibkr_etl.kiwoom_client import (
    ACCOUNT_PATH,
    HOSTS,
    INQUIRY_APIS,
    US_ACCOUNT_PATH,
    KiwoomClient,
    KiwoomError,
    kiwoom_number,
    krx_code,
    reconcile_us_valuation,
    reconcile_valuation,
)
from ibkr_etl.security_master import SecurityIdentifier

APP_KEY = "synthetic-app-key"
APP_SECRET = "synthetic-app-secret"


class FakeKiwoom:
    """Records every request and answers from a script keyed by api-id."""

    def __init__(self, answers=None):
        self.requests = []
        self.answers = answers or {}
        self.now = datetime(2026, 10, 13, 9, 0, 0)

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)

    def __call__(self, url, headers, body):
        self.requests.append((url, dict(headers), json.loads(body)))
        if url.endswith("/oauth2/token"):
            return 200, {}, {"return_code": 0, "token": "synthetic-token", "expires_dt": "20261014090000"}
        if url.endswith("/oauth2/revoke"):
            return 200, {}, {"return_code": 0}
        script = self.answers[headers["api-id"]]
        page = script.pop(0)
        more = {"cont-yn": "Y", "next-key": f"k{len(script)}"} if script else {"cont-yn": "N"}
        return 200, more, page


def _client(fake, environment="mock"):
    return KiwoomClient(environment=environment, app_key=APP_KEY, app_secret=APP_SECRET,
                        transport=fake, clock=fake.clock, sleep=fake.sleep)


ACCOUNT = {"return_code": 0, "acnt_nm": "Synthetic", "entr": "000000001000000",
           "d2_entra": "000000000500000", "prsm_dpst_aset_amt": "000000002180000"}
HOLDINGS_PAGE_1 = {"return_code": 0, "acnt_evlt_remn_indv_tot": [
    {"stk_cd": "A005930", "stk_nm": "Synthetic Electronics", "rmnd_qty": "000000000010",
     "pur_pric": "000000060000", "cur_prc": "-000000071500", "evlt_amt": "000000000715000"},
]}
HOLDINGS_PAGE_2 = {"return_code": 0, "acnt_evlt_remn_indv_tot": [
    {"stk_cd": "A000660", "stk_nm": "Synthetic Hynix", "rmnd_qty": "000000000005",
     "pur_pric": "000000150000", "cur_prc": "+000000193000", "evlt_amt": "000000000965000"},
]}


class KiwoomNumberTests(unittest.TestCase):
    def test_signed_zero_padded_numbers(self) -> None:
        self.assertEqual(kiwoom_number("000000012345"), Decimal("12345"))
        self.assertEqual(kiwoom_number("-00000001234"), Decimal("-1234"))
        self.assertEqual(kiwoom_number("+71500", unsigned=True), Decimal("71500"))
        self.assertEqual(kiwoom_number("-71500", unsigned=True), Decimal("71500"))
        self.assertEqual(kiwoom_number("0000"), Decimal("0"))
        self.assertEqual(kiwoom_number("-000.50"), Decimal("-0.50"))
        for bad in ("", None, "12a"):
            with self.subTest(bad=bad), self.assertRaises(KiwoomError):
                kiwoom_number(bad)

    def test_share_codes_lose_their_prefix(self) -> None:
        self.assertEqual(krx_code("A005930"), "005930")
        self.assertEqual(krx_code("005930"), "005930")
        with self.assertRaises(ValueError):
            krx_code("A0059")


class KiwoomClientTests(unittest.TestCase):
    def test_only_inquiries_are_reachable(self) -> None:
        fake = FakeKiwoom()
        client = _client(fake)
        # Korean orders, US orders and the US currency exchange application.
        for api_id in ("kt10000", "kt10001", "kt10002", "kt10003", "au10001",
                       "ust20000", "ust20001", "ust20002", "ust20003", "ust31302"):
            with self.subTest(api_id=api_id), self.assertRaisesRegex(KiwoomError, "not an allowed"):
                client.inquire(api_id, {})
        for path in ("/api/dostk/ordr", "/api/us/ordr", "/api/us/exchange"):
            with self.subTest(path=path), self.assertRaisesRegex(KiwoomError, "inquiry calls"):
                client._post(path, {}, {})
        self.assertEqual(fake.requests, [])
        self.assertEqual(set(INQUIRY_APIS.values()), {ACCOUNT_PATH, US_ACCOUNT_PATH})

    def test_mock_and_live_hosts_only(self) -> None:
        self.assertEqual(set(HOSTS), {"live", "mock"})
        with self.assertRaises(ValueError):
            KiwoomClient(environment="staging", app_key=APP_KEY, app_secret=APP_SECRET)
        fake = FakeKiwoom({"kt00004": [ACCOUNT]})
        _client(fake).inquire("kt00004", {})
        self.assertTrue(all(url.startswith("https://mockapi.kiwoom.com/") for url, _, _ in fake.requests))

    def test_valuation_follows_pages_and_reuses_the_token(self) -> None:
        fake = FakeKiwoom({"kt00004": [ACCOUNT], "kt00018": [HOLDINGS_PAGE_1, HOLDINGS_PAGE_2]})
        valuation = _client(fake).valuation()
        self.assertEqual([h.code for h in valuation.holdings], ["005930", "000660"])
        self.assertEqual(valuation.holdings[0].identifiers, [SecurityIdentifier("krx_code", "005930")])
        self.assertEqual(valuation.holdings[0].price, Decimal("71500"))
        self.assertEqual(valuation.deposit, Decimal("500000"))
        token_calls = [r for r in fake.requests if r[0].endswith("/oauth2/token")]
        self.assertEqual(len(token_calls), 1)
        second_page = fake.requests[-1][1]
        self.assertEqual((second_page["cont-yn"], second_page["next-key"]), ("Y", "k1"))
        self.assertEqual(second_page["authorization"], "Bearer synthetic-token")

    def test_calls_are_spaced_out(self) -> None:
        fake = FakeKiwoom({"kt00004": [ACCOUNT], "kt00001": [ACCOUNT]})
        client = _client(fake)
        start = fake.now
        client.inquire("kt00004", {})
        client.inquire("kt00001", {})
        self.assertGreaterEqual((fake.now - start).total_seconds(), 2.0)

    def test_refusals_never_echo_secrets(self) -> None:
        def refusing(url, headers, body):
            return 200, {}, {"return_code": 8005, "return_msg": "Token invalid"}

        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET,
                              transport=refusing, min_interval_seconds=0)
        with self.assertRaises(KiwoomError) as caught:
            client.inquire("kt00004", {})
        self.assertIn("code 8005", str(caught.exception))
        for secret in (APP_KEY, APP_SECRET):
            self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, repr(client))

    def test_missing_fields_fail_instead_of_reading_zero(self) -> None:
        broken = {"return_code": 0, "acnt_evlt_remn_indv_tot": [{"stk_cd": "A005930", "stk_nm": "X"}]}
        fake = FakeKiwoom({"kt00004": [ACCOUNT], "kt00018": [broken]})
        with self.assertRaisesRegex(KiwoomError, "missing rmnd_qty"):
            _client(fake).valuation()

    def test_transactions_and_revoke(self) -> None:
        page = {"return_code": 0, "trst_ovrl_trde_prps_array": [
            {"trde_dt": "20260102", "rmrk_nm": "Buy", "io_tp_nm": "Buy", "stk_cd": "A005930",
             "trde_qty_jwa_cnt": "10", "exct_amt": "-000000600000", "crnc_cd": "KRW"},
            {"trde_dt": "20260105", "rmrk_nm": "Deposit", "io_tp_nm": "Deposit", "stk_cd": "",
             "exct_amt": "000001000000", "crnc_cd": ""},
        ]}
        fake = FakeKiwoom({"kt00015": [page]})
        client = _client(fake)
        movements = client.transactions(date(2026, 1, 1), date(2026, 1, 31))
        self.assertEqual([m.trade_date for m in movements], ["2026-01-02", "2026-01-05"])
        self.assertEqual((movements[0].security.value, movements[0].amount), ("005930", Decimal("-600000")))
        self.assertEqual((movements[1].security, movements[1].currency), (None, "KRW"))
        self.assertEqual(fake.requests[-1][2]["strt_dt"], "20260101")
        client.revoke()
        self.assertTrue(fake.requests[-1][0].endswith("/oauth2/revoke"))
        with self.assertRaises(ValueError):
            client.transactions(date(2026, 2, 1), date(2026, 1, 1))


class ReconciliationTests(unittest.TestCase):
    def test_holdings_plus_cash_must_match_the_broker_total(self) -> None:
        fake = FakeKiwoom({"kt00004": [ACCOUNT], "kt00018": [HOLDINGS_PAGE_1, HOLDINGS_PAGE_2]})
        valuation = _client(fake).valuation()
        result = reconcile_valuation(valuation)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["evidence"]["holdings_value"], "1680000")
        off = dict(ACCOUNT, prsm_dpst_aset_amt="000000002190000")
        fake = FakeKiwoom({"kt00004": [off], "kt00018": [HOLDINGS_PAGE_1, HOLDINGS_PAGE_2]})
        result = reconcile_valuation(_client(fake).valuation())
        self.assertEqual(result["status"], "fail")
        self.assertIn("-10,000 KRW", result["message"])


US_HOLDINGS = {"return_code": 0, "crnc_code": "USD", "result_list": [
    {"stex_nm": "NASDAQ", "crnc_code": "USD", "stk_cd": "AMGN", "frgn_stk_nm": "Synthetic Biotech",
     "poss_qty": "000000000010", "frgn_stk_book_uv": "250.10", "now_pric": "+300.25",
     "evlt_amt": "3002.50", "evlt_amt_krw": "000004203500"},
    {"stex_nm": "NYSE", "crnc_code": "USD", "stk_cd": "BRK B", "frgn_stk_nm": "Synthetic Holdings B",
     "poss_qty": "000000000004", "frgn_stk_book_uv": "400.00", "now_pric": "-450.00",
     "evlt_amt": "1800.00", "evlt_amt_krw": "000002520000"},
]}
US_BALANCES = {"return_code": 0, "won_entr": "000000100000", "result_list": [
    {"crnc_code": "USD", "fx_entr": "1250.75", "evlt_amt": "4802.50", "crnc_rt": "1400.00"},
]}


DAY_BALANCE = {"return_code": 0, "dt": "20250602", "dbst_bal": "000000001000000",
               "day_stk_asst": "000000008000000", "day_bal_rt": [
                   {"stk_cd": "A005930", "stk_nm": "삼성전자", "rmnd_qty": "000000000100", "buy_uv": "60000",
                    "cur_prc": "+70000", "evlt_amt": "000007000000"},
                   {"stk_cd": "000660", "stk_nm": "SK하이닉스", "rmnd_qty": "0", "buy_uv": "0", "cur_prc": "0",
                    "evlt_amt": "0"},
               ]}


class KiwoomPastDayTests(unittest.TestCase):
    def test_a_past_day_reads_holdings_and_cash(self) -> None:
        fake = FakeKiwoom({"ka01690": [DAY_BALANCE]})
        valuation = _client(fake).balance_on(date(2025, 6, 2))
        self.assertEqual(fake.requests[-1][2], {"qry_dt": "20250602"})
        self.assertEqual((valuation.deposit, valuation.estimated_assets), (Decimal("1000000"), Decimal("8000000")))
        # A stock sold out by that day is left out.
        self.assertEqual([(h.code, h.quantity, h.price) for h in valuation.holdings],
                         [("005930", Decimal("100"), Decimal("70000"))])
        self.assertEqual(reconcile_valuation(valuation)["status"], "pass")

    def test_a_closed_day_is_none(self) -> None:
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, dt="20250530")]})
        self.assertIsNone(_client(fake).balance_on(date(2025, 6, 2)))

    def test_a_missing_total_fails_loudly(self) -> None:
        page = {key: value for key, value in DAY_BALANCE.items() if key != "day_stk_asst"}
        with self.assertRaisesRegex(KiwoomError, "day_stk_asst"):
            _client(FakeKiwoom({"ka01690": [page]})).balance_on(date(2025, 6, 2))


class KiwoomUsTests(unittest.TestCase):
    def test_us_valuation_reads_holdings_and_balances(self) -> None:
        fake = FakeKiwoom({"ust21070": [US_HOLDINGS], "ust21120": [US_BALANCES]})
        valuation = _client(fake).us_valuation()
        self.assertEqual([h.code for h in valuation.holdings], ["AMGN", "BRK.B"])
        first = valuation.holdings[0]
        self.assertEqual((first.currency, first.exchange, first.price, first.market_value_krw),
                         ("USD", "NASDAQ", Decimal("300.25"), Decimal("4203500")))
        self.assertEqual(valuation.holdings[1].identifiers, [SecurityIdentifier("us_ticker", "BRK.B")])
        self.assertEqual(valuation.balances[0].deposit, Decimal("1250.75"))
        urls = [url for url, _, _ in fake.requests if "/oauth2/" not in url]
        self.assertEqual(urls, ["https://mockapi.kiwoom.com/api/us/acnt"] * 2)
        self.assertEqual(fake.requests[1][2], {"stex_tp": "", "stk_cd": ""})
        result = reconcile_us_valuation(valuation)
        self.assertEqual(result["status"], "pass", result["message"])
        self.assertEqual(result["evidence"]["currencies"]["USD"]["holdings_value"], "4802.50")

    def test_us_reconciliation_fails_on_a_missing_holding_or_wrong_unit(self) -> None:
        missing = dict(US_HOLDINGS, result_list=US_HOLDINGS["result_list"][:1])
        fake = FakeKiwoom({"ust21070": [missing], "ust21120": [US_BALANCES]})
        result = reconcile_us_valuation(_client(fake).us_valuation())
        self.assertEqual(result["status"], "fail")
        self.assertIn("-1,800.00 USD away", result["message"])
        # A price read as whole cents instead of dollars.
        cents = [dict(row) for row in US_HOLDINGS["result_list"]]
        cents[0]["now_pric"] = "30025"
        fake = FakeKiwoom({"ust21070": [dict(US_HOLDINGS, result_list=cents)], "ust21120": [US_BALANCES]})
        result = reconcile_us_valuation(_client(fake).us_valuation())
        self.assertEqual((result["status"], result["evidence"]["mispriced"]), ("fail", ["AMGN"]))
        # Securities Kiwoom values with no holdings listed, and no total at all.
        fake = FakeKiwoom({"ust21070": [dict(US_HOLDINGS, result_list=[])], "ust21120": [US_BALANCES]})
        self.assertEqual(reconcile_us_valuation(_client(fake).us_valuation())["status"], "fail")
        fake = FakeKiwoom({"ust21070": [US_HOLDINGS], "ust21120": [dict(US_BALANCES, result_list=[])]})
        result = reconcile_us_valuation(_client(fake).us_valuation())
        self.assertIn("no USD total", result["message"])

    def test_a_currency_listed_twice_is_refused(self) -> None:
        twice = dict(US_BALANCES, result_list=US_BALANCES["result_list"] * 2)
        fake = FakeKiwoom({"ust21070": [US_HOLDINGS], "ust21120": [twice]})
        with self.assertRaisesRegex(KiwoomError, "currency twice"):
            _client(fake).us_valuation()

    def test_balances_on_a_past_date(self) -> None:
        fake = FakeKiwoom({"ust21132": [US_BALANCES]})
        balances = _client(fake).us_balances_on(date(2026, 9, 30))
        self.assertEqual((balances[0].currency, balances[0].securities_value), ("USD", Decimal("4802.50")))
        self.assertEqual(fake.requests[-1][2], {"base_dt": "20260930"})

    def test_us_transactions_keep_both_currencies(self) -> None:
        page = {"return_code": 0, "result_list": [
            {"deal_dt": "20260105", "deal_kind_nm": "Buy", "rmrk_nm": "Buy", "stk_cd": "AMGN",
             "deal_qty": "10", "crnc_code": "USD", "fc_exct_amt": "-2501.00", "exct_amt": "-000003501400"},
            {"deal_dt": "20260102", "deal_kind_nm": "Deposit", "rmrk_nm": "KRW deposit", "stk_cd": "",
             "deal_qty": "", "crnc_code": "", "fc_exct_amt": "0", "exct_amt": "000005000000"},
        ]}
        fake = FakeKiwoom({"ust21100": [page]})
        movements = _client(fake).us_transactions(date(2026, 1, 1), date(2026, 1, 31))
        buy, deposit = movements
        self.assertEqual((buy.security, buy.amount, buy.currency, buy.amount_krw),
                         (SecurityIdentifier("us_ticker", "AMGN"), Decimal("-2501.00"), "USD", Decimal("-3501400")))
        self.assertEqual((deposit.security, deposit.amount, deposit.currency, deposit.amount_krw),
                         (None, Decimal("5000000"), "KRW", None))
        self.assertEqual(fake.requests[-1][2]["tp"], "0")
        missing = {"return_code": 0, "result_list": [{"deal_dt": "20260105", "crnc_code": "USD", "exct_amt": "1"}]}
        with self.assertRaisesRegex(KiwoomError, "missing fc_exct_amt"):
            _client(FakeKiwoom({"ust21100": [missing]})).us_transactions(date(2026, 1, 1), date(2026, 1, 31))


if __name__ == "__main__":
    unittest.main()
