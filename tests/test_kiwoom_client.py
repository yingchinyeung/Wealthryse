from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from ibkr_etl.kiwoom_client import (
    HOSTS,
    INQUIRY_APIS,
    KiwoomClient,
    KiwoomError,
    kiwoom_number,
    krx_code,
    reconcile_valuation,
)

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
        for api_id in ("kt10000", "kt10001", "kt10002", "kt10003", "au10001"):
            with self.subTest(api_id=api_id), self.assertRaisesRegex(KiwoomError, "not an allowed"):
                client.inquire(api_id, {})
        with self.assertRaisesRegex(KiwoomError, "inquiry calls"):
            client._post("/api/dostk/ordr", {}, {})
        self.assertEqual(fake.requests, [])
        self.assertTrue(all(path == "/api/dostk/acnt" for path in INQUIRY_APIS.values()))

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
        self.assertEqual([h.krx_code for h in valuation.holdings], ["005930", "000660"])
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
        self.assertEqual((movements[0].krx_code, movements[0].amount), ("005930", Decimal("-600000")))
        self.assertEqual((movements[1].krx_code, movements[1].currency), (None, "KRW"))
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


if __name__ == "__main__":
    unittest.main()
