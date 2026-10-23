from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from ibkr_etl.kiwoom_client import (
    ACCOUNT_PATH,
    CHART_PATH,
    HOSTS,
    INQUIRY_APIS,
    US_ACCOUNT_PATH,
    US_CHART_PATH,
    KiwoomClient,
    KiwoomError,
    KiwoomUnknownStock,
    kiwoom_number,
    chart_closes,
    kiwoom_selling_cost,
    krx_code,
    reconcile_us_valuation,
    reconcile_valuation,
    selling_cost_rule_gap,
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
        self.assertEqual(set(INQUIRY_APIS.values()), {ACCOUNT_PATH, US_ACCOUNT_PATH, CHART_PATH, US_CHART_PATH})
        # The price chart paths carry the daily close only; they have no order or account change.
        self.assertEqual([api for api, path in INQUIRY_APIS.items() if path == CHART_PATH], ["ka10081"])
        self.assertEqual([api for api, path in INQUIRY_APIS.items() if path == US_CHART_PATH], ["usa06012"])

    def test_a_slow_answer_is_asked_again_then_named(self) -> None:
        fake = FakeKiwoom({"kt00004": [ACCOUNT]})
        calls = []

        def flaky(url, headers, body):
            calls.append(headers.get("api-id"))
            if headers.get("api-id") == "kt00004" and calls.count("kt00004") == 1:
                raise TimeoutError("The read operation timed out")
            return fake(url, headers, body)

        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=flaky,
                              clock=fake.clock, sleep=fake.sleep)
        self.assertEqual(client.inquire("kt00004", {})[0]["acnt_nm"], "Synthetic")
        self.assertEqual(calls.count("kt00004"), 2)

        def dead(url, headers, body):
            if url.endswith("/oauth2/token"):
                return fake(url, headers, body)
            raise TimeoutError("The read operation timed out")

        client = KiwoomClient(environment="mock", app_key=APP_KEY, app_secret=APP_SECRET, transport=dead,
                              clock=fake.clock, sleep=fake.sleep)
        with self.assertRaisesRegex(KiwoomError, "did not answer the call ka10081 after 3 tries"):
            client.inquire("ka10081", {})

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
        self.assertEqual((valuation.deposit, valuation.settled_deposit), (Decimal("500000"), Decimal("1000000")))
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
        self.assertIn("10,000 KRW above", result["message"])

    def test_kiwoom_total_after_its_selling_cost_passes(self) -> None:
        # 0.215% of 1,680,000 KRW of holdings: 0.20% tax plus the fee.
        net = dict(ACCOUNT, prsm_dpst_aset_amt=f"{2180000 - 3612:015d}")
        result = reconcile_valuation(_client(FakeKiwoom({"kt00004": [net], "kt00018": [HOLDINGS_PAGE_1,
                                                                                         HOLDINGS_PAGE_2]})).valuation())
        self.assertEqual((result["status"], result["evidence"]["selling_cost"]), ("pass", "3612"))
        self.assertIn("selling cost of 3,612 KRW", result["message"])
        # More than any selling cost could be is still a gap.
        far = dict(ACCOUNT, prsm_dpst_aset_amt=f"{2180000 - 5000:015d}")
        result = reconcile_valuation(_client(FakeKiwoom({"kt00004": [far], "kt00018": [HOLDINGS_PAGE_1,
                                                                                         HOLDINGS_PAGE_2]})).valuation())
        self.assertEqual(result["status"], "fail")
        self.assertIn("more than its selling cost", result["message"])


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


DAY_DETAIL = {"return_code": 0, "entr_fr": "000000001000000", "entr_to": "000000001000000",
              "scrt_evlt_amt_fr": "000000006900000", "scrt_evlt_amt_to": "000000007000000",
              "tot_amt_to": "000000008000000"}


class KiwoomPastDayTests(unittest.TestCase):
    def test_a_past_day_reads_holdings_and_cash(self) -> None:
        fake = FakeKiwoom({"ka01690": [DAY_BALANCE], "kt00016": [DAY_DETAIL]})
        valuation = _client(fake).balance_on(date(2025, 6, 2))
        self.assertEqual([body for _, headers, body in fake.requests if headers.get("api-id") == "ka01690"],
                         [{"qry_dt": "20250602"}])
        self.assertEqual((valuation.deposit, valuation.estimated_assets), (Decimal("1000000"), Decimal("8000000")))
        # A stock sold out by that day is left out.
        self.assertEqual([(h.code, h.quantity, h.price) for h in valuation.holdings],
                         [("005930", Decimal("100"), Decimal("70000"))])
        self.assertEqual(reconcile_valuation(valuation)["status"], "pass")

    def test_a_past_days_cash_is_the_deposit_and_the_total_is_after_selling_cost(self) -> None:
        # Kiwoom's total is 11,550 KRW (0.165% of the holdings) below holdings plus the deposit.
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, day_stk_asst="000000007988450")], "kt00016": [DAY_DETAIL]})
        valuation = _client(fake).balance_on(date(2025, 6, 2))
        self.assertEqual((valuation.deposit, valuation.settled_deposit), (Decimal("1000000"), None))
        self.assertEqual(valuation.selling_cost, Decimal("11550"))
        self.assertEqual(reconcile_valuation(valuation)["status"], "pass")

    def test_after_market_prices_are_replaced_by_the_official_close(self) -> None:
        # From 14 Sep 2026 ka01690 lists the 20:00 after-market price (70,000); Kiwoom's total values
        # the holdings at the official close (71,000: 7,100,000 KRW), less its per-stock selling cost.
        detail = dict(DAY_DETAIL, scrt_evlt_amt_to="000000007100000")
        total = 1000000 + 7100000 - 15260
        # The chart's cur_prc is the day's last trade (70,000 on 14 Sep, after-market); the official
        # close is 15 Sep's base price: 72,000 less its +1,000 change.
        chart = {"return_code": 0, "stk_cd": "005930", "stk_dt_pole_chart_qry": [
            {"dt": "20260915", "cur_prc": "72000", "pred_pre": "+1000"},
            {"dt": "20260914", "cur_prc": "70000", "pred_pre": "-500"},
            {"dt": "20260911", "cur_prc": "70500", "pred_pre": "0"}]}
        older = {"return_code": 0, "stk_dt_pole_chart_qry": [{"dt": "20260910", "cur_prc": "1", "pred_pre": "0"}]}
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, dt="20260914", day_stk_asst=f"{total:015d}")],
                           "kt00016": [detail], "ka10081": [chart, older]})
        valuation = _client(fake).balance_on(date(2026, 9, 14))
        # One page of the chart only, unadjusted prices, with the close for the day asked.
        self.assertEqual([(url, body) for url, headers, body in fake.requests if headers.get("api-id") == "ka10081"],
                         [("https://mockapi.kiwoom.com/api/dostk/chart",
                           {"stk_cd": "005930", "base_dt": "20261013", "upd_stkpc_tp": "0"})])
        self.assertEqual([(h.price, h.market_value) for h in valuation.holdings],
                         [(Decimal("71000"), Decimal("7100000"))])
        self.assertEqual((valuation.holdings_value, valuation.price_basis_gap, valuation.repriced_at_close),
                         (Decimal("7100000"), Decimal("0"), True))
        self.assertEqual(valuation.selling_cost, kiwoom_selling_cost(Decimal("7100000"), date(2026, 9, 14)))
        self.assertEqual(reconcile_valuation(valuation)["status"], "pass")

    def test_a_close_that_does_not_add_up_keeps_the_listed_prices(self) -> None:
        detail = dict(DAY_DETAIL, scrt_evlt_amt_to="000000007100000")
        total = 1000000 + 7100000 - 15260
        chart = {"return_code": 0, "stk_dt_pole_chart_qry": [{"dt": "20260915", "cur_prc": "71000", "pred_pre": "+500"},
                                                             {"dt": "20260914", "cur_prc": "70000", "pred_pre": "0"}]}
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, dt="20260914", day_stk_asst=f"{total:015d}")],
                           "kt00016": [detail], "ka10081": [chart]})
        valuation = _client(fake).balance_on(date(2026, 9, 14))
        self.assertEqual((valuation.holdings_value, valuation.valued_holdings, valuation.repriced_at_close),
                         (Decimal("7000000"), Decimal("7100000"), False))
        self.assertEqual(valuation.selling_cost, Decimal("15260"))

    def test_the_official_close_is_the_next_days_base_price(self) -> None:
        # Kiwoom's chart, SK hynix, 14-16 Sep 2026: cur_prc 1,683,000 on 14 Sep is the 20:00 last
        # trade; 15 Sep's base price 1,712,000 - 15,000 = 1,697,000 is the official 15:30 close.
        pages = [{"stk_dt_pole_chart_qry": [
            {"dt": "20260916", "cur_prc": "1757000", "pred_pre": "+67000"},
            {"dt": "20260915", "cur_prc": "1712000", "pred_pre": "+15000"},
            {"dt": "20260914", "cur_prc": "1683000", "pred_pre": "-129000"},
            {"dt": "20260911", "cur_prc": "1812000", "pred_pre": "-41000"}]}]
        self.assertEqual(chart_closes(pages), {"20260911": Decimal("1812000"), "20260914": Decimal("1697000"),
                                               "20260915": Decimal("1690000")})

    def test_kiwooms_selling_cost_rule_per_stock(self) -> None:
        # 2025: 0.15% tax; the 0.015% fee rounded down to 10 won.
        self.assertEqual(kiwoom_selling_cost(Decimal("7000000"), date(2025, 6, 2)), Decimal("11550"))
        # From 26 Dec 2025: 0.05% and 0.15%, each rounded down. Kiwoom's own kt00018 figures, 19 Oct 2026:
        # DL 43 x 44,950 has tax 3,865 and fee 280; SK hynix 14 x 1,277,000 has 35,756 and 2,680.
        self.assertEqual(kiwoom_selling_cost(Decimal("1932850"), date(2026, 10, 19)), Decimal("4145"))
        self.assertEqual(kiwoom_selling_cost(Decimal("17878000"), date(2026, 10, 19)), Decimal("38436"))

    # A day with a buy not yet settled at the close: the account's first day, 100 shares bought for
    # 7,000,000 KRW plus a 1,050 fee. ka01690 counts the buy on the trade date; kt00016 only once it
    # settles two trading days later, so it still has the cash and no holdings.
    UNSETTLED = dict(DAY_DETAIL, entr_fr="0", entr_to="000000008001050", scrt_evlt_amt_fr="0", scrt_evlt_amt_to="0",
                     tot_amt_to="000000008001050")

    def test_a_day_with_trades_not_yet_settled_is_ka01690s_alone(self) -> None:
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, day_stk_asst="000000007988450")],
                           "kt00016": [self.UNSETTLED]})
        valuation = _client(fake).balance_on(date(2025, 6, 2))
        self.assertEqual((valuation.deposit, valuation.estimated_assets), (Decimal("1000000"), Decimal("7988450")))
        self.assertEqual([(h.code, h.quantity, h.market_value) for h in valuation.holdings],
                         [("005930", Decimal("100"), Decimal("7000000"))])
        # kt00016's value of the holdings (nothing yet) is not used; the listed holdings add up.
        self.assertEqual((valuation.securities_value, valuation.valued_holdings, valuation.price_basis_gap),
                         (None, Decimal("7000000"), Decimal("0")))
        self.assertEqual(valuation.selling_cost, kiwoom_selling_cost(Decimal("7000000"), date(2025, 6, 2)))
        self.assertEqual((valuation.unsettled_trades, valuation.repriced_at_close), (True, False))
        self.assertNotIn("ka10081", [headers.get("api-id") for _, headers, _ in fake.requests])
        self.assertEqual(reconcile_valuation(valuation)["status"], "pass")

    def test_a_day_with_trades_not_yet_settled_is_priced_at_the_close_when_that_adds_up(self) -> None:
        # From 14 Sep 2026 ka01690 lists the after-market price (70,000); Kiwoom's total is at the close (71,000).
        total = 1000000 + 7100000 - 15260
        chart = {"return_code": 0, "stk_dt_pole_chart_qry": [{"dt": "20260915", "cur_prc": "72000", "pred_pre": "+1000"},
                                                             {"dt": "20260914", "cur_prc": "70000", "pred_pre": "0"}]}
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, dt="20260914", day_stk_asst=f"{total:015d}")],
                           "kt00016": [self.UNSETTLED], "ka10081": [chart]})
        valuation = _client(fake).balance_on(date(2026, 9, 14))
        self.assertEqual([(h.price, h.market_value) for h in valuation.holdings],
                         [(Decimal("71000"), Decimal("7100000"))])
        self.assertEqual((valuation.securities_value, valuation.unsettled_trades, valuation.repriced_at_close),
                         (None, True, True))
        self.assertEqual(valuation.selling_cost, Decimal("15260"))

    def test_a_day_with_trades_not_yet_settled_must_add_up_to_the_won(self) -> None:
        # Two won more than the per-stock rule allows for one holding; the close adds up no better.
        chart = {"return_code": 0, "stk_dt_pole_chart_qry": [{"dt": "20250603", "cur_prc": "70500", "pred_pre": "+500"},
                                                             {"dt": "20250602", "cur_prc": "70000", "pred_pre": "0"}]}
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, day_stk_asst="000000007988448")],
                           "kt00016": [self.UNSETTLED], "ka10081": [chart]})
        with self.assertRaisesRegex(KiwoomError, "do not add up") as raised:
            _client(fake).balance_on(date(2025, 6, 2))
        # The message names the day and the reason, never an amount.
        for amount in ("1,000,000", "1000000", "8,001,050", "8001050", "7,988,448", "7988448"):
            self.assertNotIn(amount, str(raised.exception))

    def test_an_unsettled_day_with_no_close_yet_fails(self) -> None:
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, day_stk_asst="000000007988448")],
                           "kt00016": [self.UNSETTLED],
                           "ka10081": [{"return_code": 0, "stk_dt_pole_chart_qry": []}]})
        with self.assertRaisesRegex(KiwoomError, "not yet settled"):
            _client(fake).balance_on(date(2025, 6, 2))

    def test_the_history_checks_selling_cost_rule_allows_a_won_a_holding(self) -> None:
        values = [Decimal("7000000"), Decimal("1932850")]
        rule = kiwoom_selling_cost(values[0], date(2026, 10, 19)) + kiwoom_selling_cost(values[1], date(2026, 10, 19))
        self.assertEqual(selling_cost_rule_gap(values, rule + 2, date(2026, 10, 19)), Decimal("2"))
        self.assertEqual(selling_cost_rule_gap([], Decimal("0"), date(2026, 10, 19)), Decimal("0"))

    def test_a_closed_day_is_none(self) -> None:
        fake = FakeKiwoom({"ka01690": [dict(DAY_BALANCE, dt="20250530")]})
        self.assertIsNone(_client(fake).balance_on(date(2025, 6, 2)))

    def test_the_nightly_rebuild_fails_loudly_instead_of_reading_empty_figures(self) -> None:
        empty = {"return_code": 0, "dt": "", "dbst_bal": "", "day_stk_asst": "",
                 "return_msg": "전일자 자동일지 데이터 생성 중입니다. (매일 오전 1~3시, 4~7시)",
                 "day_bal_rt": [{"stk_cd": "", "rmnd_qty": "", "cur_prc": "", "evlt_amt": ""}]}
        with self.assertRaisesRegex(KiwoomError, "rebuilds its daily records"):
            _client(FakeKiwoom({"ka01690": [empty]})).balance_on(date(2026, 9, 14))

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

    def test_us_holdings_are_checked_against_the_total_in_the_same_answer(self) -> None:
        # As on 21 Oct 2026: ust21070 carries its own total of the holdings, and ust21120, asked a
        # moment later, prices the same holdings after U.S. prices moved.
        holdings = dict(US_HOLDINGS, tot_evlt_amt="4802.5000")
        moved = dict(US_BALANCES, result_list=[dict(US_BALANCES["result_list"][0], evlt_amt="4801.76")])
        valuation = _client(FakeKiwoom({"ust21070": [holdings], "ust21120": [moved]})).us_valuation()
        self.assertEqual(valuation.holdings_totals, {"USD": Decimal("4802.5000")})
        result = reconcile_us_valuation(valuation)
        self.assertEqual(result["status"], "pass", result["message"])
        usd = result["evidence"]["currencies"]["USD"]
        self.assertEqual((usd["gap"], usd["price_move_between_requests"]), ("0.0000", "-0.7400"))
        # A holding missing from the answer still fails against its own total.
        short = dict(holdings, result_list=US_HOLDINGS["result_list"][:1])
        result = reconcile_us_valuation(_client(FakeKiwoom({"ust21070": [short], "ust21120": [moved]})).us_valuation())
        self.assertEqual(result["status"], "fail")
        self.assertIn("-1,800.0000 USD away", result["message"])
        # A balance further off than prices move between two requests (0.5%) fails too.
        far = dict(US_BALANCES, result_list=[dict(US_BALANCES["result_list"][0], evlt_amt="4700.00")])
        result = reconcile_us_valuation(_client(FakeKiwoom({"ust21070": [holdings], "ust21120": [far]})).us_valuation())
        self.assertEqual(result["status"], "fail")
        self.assertIn("more than prices move between two requests", result["message"])

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
             "deal_qty": "10", "crnc_code": "USD", "fc_exct_amt": "-2501.00", "exct_amt": "-000003501400",
             "fc_entra": "-14.32", "proc_time": "08:11:40", "deal_no": "000000003"},
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
        # Kiwoom's cash after a foreign-currency row, and the row's place in its posting date.
        self.assertEqual((buy.balance, buy.order), (Decimal("-14.32"), "08:11:40|000000003"))
        self.assertIsNone(deposit.balance)
        self.assertEqual(fake.requests[-1][2]["tp"], "0")
        missing = {"return_code": 0, "result_list": [{"deal_dt": "20260105", "crnc_code": "USD", "exct_amt": "1"}]}
        with self.assertRaisesRegex(KiwoomError, "missing fc_exct_amt"):
            _client(FakeKiwoom({"ust21100": [missing]})).us_transactions(date(2026, 1, 1), date(2026, 1, 31))


class KiwoomTransportTests(unittest.TestCase):
    def test_sends_a_named_user_agent_and_reads_non_json_refusals(self) -> None:
        import io
        import urllib.error
        from unittest import mock

        from ibkr_etl import kiwoom_client

        seen = {}

        def refusing(request, timeout):
            seen.update({k.lower(): v for k, v in request.header_items()})
            raise urllib.error.HTTPError(request.full_url, 400, "Bad Request", {},
                                         io.BytesIO(b"<html>\n<body>Bad Request</body></html>"))

        with mock.patch.object(kiwoom_client.urllib.request, "urlopen", refusing):
            status, _, payload = kiwoom_client._urllib_transport("https://api.kiwoom.com/oauth2/token",
                                                                 {"Content-Type": "application/json"}, b"{}")
        self.assertEqual(seen["user-agent"], kiwoom_client.USER_AGENT)
        self.assertEqual(seen["content-type"], "application/json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["return_msg"], "non-JSON answer: <html> <body>Bad Request</body></html>")

    def test_refusal_names_the_call_and_masks_keys(self) -> None:
        def refusing(url, headers, body):
            return 400, {}, {"return_msg": f"bad appkey {APP_KEY}"}

        client = KiwoomClient(environment="live", app_key=APP_KEY, app_secret=APP_SECRET,
                              transport=refusing, min_interval_seconds=0)
        with self.assertRaises(KiwoomError) as caught:
            client.inquire("kt00004", {})
        self.assertIn("/oauth2/token (HTTP 400", str(caught.exception))
        self.assertNotIn(APP_KEY, str(caught.exception))



class UsPriceAndQuantityTests(unittest.TestCase):
    """usa06012 (daily chart, read backwards) and usa21730 (shares held per Korean date)."""

    def test_daily_closes_start_at_the_latest_day_and_keep_the_asked_range(self) -> None:
        chart = [{"return_code": 0, "result_list": [
            {"dt": "20250402", "cur_prc": "152.5000"}, {"dt": "20250401", "cur_prc": "+150.0100"}]},
                 {"return_code": 0, "result_list": [{"dt": "20250331", "cur_prc": "149.0000"},
                                                     {"dt": "20250328", "cur_prc": "147.0000"}]}]
        fake = FakeKiwoom({"usa06012": chart})
        closes = _client(fake).us_daily_closes("SYN", "NY", date(2025, 3, 31), date(2025, 4, 2))
        self.assertEqual(closes, {date(2025, 4, 2): Decimal("152.5000"), date(2025, 4, 1): Decimal("150.0100"),
                                  date(2025, 3, 31): Decimal("149.0000")})
        url, headers, body = fake.requests[1]
        self.assertTrue(url.endswith(US_CHART_PATH))
        self.assertEqual(body, {"stex_tp": "NY", "stk_cd": "SYN", "strt_dt": "20250402", "upd_stkpc_tp": "0",
                                "exrt_appl_tp": "0"})

    def test_quantities_are_read_per_korean_date(self) -> None:
        fake = FakeKiwoom({"usa21730": [{"return_code": 0, "result_list": [
            {"frst_base_dt": "2026-02-12", "last_base_dt": "2026-02-13", "frst_qty": "96", "last_qty": "96"},
            {"frst_base_dt": "2026-02-11", "last_base_dt": "2026-02-12", "frst_qty": "0", "last_qty": "96"}]}]})
        quantities = _client(fake).us_stock_quantities("SYN", "NY", date(2026, 2, 11), date(2026, 2, 13))
        self.assertEqual(quantities, {date(2026, 2, 11): Decimal(0), date(2026, 2, 12): Decimal(96),
                                      date(2026, 2, 13): Decimal(96)})
        self.assertEqual(fake.requests[1][2], {"from": "20260211", "to": "20260213", "stex_tp": "NY", "stk_cd": "SYN"})

    def test_a_stock_kiwoom_does_not_know_is_its_own_refusal(self) -> None:
        fake = FakeKiwoom({"usa06012": [{"return_code": 7, "return_msg": "error[1903:no such stock, stex_tp=ND]"}]})
        with self.assertRaises(KiwoomUnknownStock):
            _client(fake).us_daily_closes("GONE", "ND", date(2025, 3, 27), date(2025, 4, 2))

if __name__ == "__main__":
    unittest.main()
