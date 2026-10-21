"""Quant analytics for the two Kiwoom accounts (docs/QUANT_ANALYTICS.md, "Kiwoom accounts").

Korea iStrategy reports in won and holds Korean shares; U.S. iStrategy reports in dollars and holds
U.S. shares bought with won top-ups. Checked here without a database, on made-up values:
the factor model each account gets (factor_model_for); Korea's market model (KOSPI 200 and the
previous U.S. day's won/dollar move) recovering known betas, and why the alignment matters;
Korea's macro set (won per dollar, every series at its previous U.S. day) recovering known
sensitivities and the won shock; the U.S. account's top-ups read as flows; Kiwoom trades held
back with the reason; one-currency accounts; crowding's reasons and the reviewed ticker list;
the shadow run's masking and broker groups; and the Korea cards as the page renders them.
tests/test_quant_kiwoom_db.py reads both accounts through load_inputs on a real PostgreSQL.
"""
from __future__ import annotations

import json
import math
import random
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from test_quant_phase3 import _Connection
from test_quant_snapshots import AS_OF, business_days
from ibkr_etl import market_inputs as mi
from ibkr_etl import quant_snapshots as qs
from ibkr_etl import sec_13f

KOREA, US_ISTRATEGY = "KIWOOM-K0000001", "KIWOOM-U0000002"  # made-up ids, never real account numbers


def _shift(day: str, days: int) -> str:
    return date.fromordinal(date.fromisoformat(day).toordinal() + days).isoformat()


def _positions(day: str, holdings: dict[str, tuple[str, float]]) -> list[dict]:
    return [{"date": day, "conid": conid, "currency": currency, "market_value": str(value), "quantity": "1"}
            for conid, (currency, value) in holdings.items()]


class FactorModelChoiceTests(unittest.TestCase):
    """factor_model_for: by the share of the holdings' value in each trading currency."""

    DAY = "2026-10-15"
    FX = [{"date": "2026-10-14", "from": "USD", "to": "KRW", "rate": 1400.0},
          {"date": "2026-10-14", "from": "HKD", "to": "USD", "rate": 0.128}]

    def _model(self, base, holdings, *, day=DAY):
        data = {"reporting_currency": base, "fx": self.FX, "positions": _positions(self.DAY, holdings)}
        return qs.factor_model_for(data, day)

    def test_korea_iStrategy_takes_the_korea_market_model(self):
        self.assertEqual(self._model("KRW", {"KRX:005930": ("KRW", 9e6), "KRX:000660": ("KRW", 4e6)}),
                         qs.KOREA_MARKET_MODEL)

    def test_us_iStrategy_takes_the_us_factors(self):
        self.assertEqual(self._model("USD", {"US:AAPL": ("USD", 5000.0), "US:NVDA": ("USD", 4000.0)}), "us")

    def test_mostly_won_in_a_dollar_report_is_still_korea(self):
        # 7,000,000 won at 1,400 is 5,000 dollars, beside 4,000 dollars of U.S. shares: 56% won.
        self.assertEqual(self._model("USD", {"KRX:005930": ("KRW", 7e6), "US:AAPL": ("USD", 4000.0)}),
                         qs.KOREA_MARKET_MODEL)

    def test_a_mixed_account_keeps_the_modelling_pages_default_region(self):
        # 85% dollars and 15% Hong Kong dollars: under the 90% rule, so Developed markets as before.
        hkd = 1500.0 / 0.128
        self.assertEqual(self._model("USD", {"US:AAPL": ("USD", 8500.0), "0700": ("HKD", hkd)}), qs.FACTOR_REGION)
        self.assertEqual(self._model("USD", {"US:AAPL": ("USD", 9100.0), "0700": ("HKD", 900.0 / 0.128)}), "us")

    def test_with_no_holdings_the_reporting_currency_decides(self):
        self.assertEqual(self._model("KRW", {}), qs.KOREA_MARKET_MODEL)
        self.assertEqual(self._model("USD", {}), qs.FACTOR_REGION)
        # Holdings on another day are not today's holdings.
        self.assertEqual(self._model("USD", {"US:AAPL": ("USD", 5000.0)}, day="2026-10-16"), qs.FACTOR_REGION)


class PreviousUsDayTests(unittest.TestCase):
    """Korea closes at 06:30 UTC, before New York opens: a Korean day sees the previous U.S. day."""

    FRIDAY, MONDAY, TUESDAY, WEDNESDAY = "2026-10-09", "2026-10-12", "2026-10-13", "2026-10-14"

    def test_each_korean_day_takes_the_move_between_the_two_fx_closes_before_it(self):
        closes = {"2026-10-08": 1380.0, self.FRIDAY: 1390.0, self.MONDAY: 1385.0, self.TUESDAY: 1400.0}
        data = {"fx": [{"date": d, "from": "USD", "to": "KRW", "rate": r} for d, r in closes.items()]}
        moves = qs._previous_day_fx_moves(data, [self.FRIDAY, self.MONDAY, self.TUESDAY, self.WEDNESDAY])
        # Friday has one close before it (Thursday's), so no move yet.
        self.assertEqual(set(moves), {self.MONDAY, self.TUESDAY, self.WEDNESDAY})
        self.assertAlmostEqual(moves[self.MONDAY], math.log(1390.0 / 1380.0), places=15)  # Friday's U.S. move
        self.assertAlmostEqual(moves[self.TUESDAY], math.log(1385.0 / 1390.0), places=15)
        self.assertAlmostEqual(moves[self.WEDNESDAY], math.log(1400.0 / 1385.0), places=15)
        # Won to dollar rows read inverted give the same moves.
        inverted = {"fx": [{"date": d, "from": "KRW", "to": "USD", "rate": 1.0 / r} for d, r in closes.items()]}
        self.assertEqual(qs._previous_day_fx_moves(inverted, [self.WEDNESDAY]).keys(), {self.WEDNESDAY})
        self.assertAlmostEqual(qs._previous_day_fx_moves(inverted, [self.WEDNESDAY])[self.WEDNESDAY],
                               moves[self.WEDNESDAY], places=12)

    def test_a_day_without_two_recent_closes_before_it_has_no_move(self):
        data = {"fx": [{"date": "2026-09-28", "from": "USD", "to": "KRW", "rate": 1380.0},
                       {"date": "2026-09-29", "from": "USD", "to": "KRW", "rate": 1381.0}]}
        self.assertEqual(qs._previous_day_fx_moves(data, ["2026-09-29"]), {})  # one close before it
        self.assertEqual(set(qs._previous_day_fx_moves(data, ["2026-09-30"])), {"2026-09-30"})
        self.assertEqual(qs._previous_day_fx_moves(data, ["2026-10-08"]), {})  # the latest close is 9 days old

    def test_series_levels_are_re_dated_to_the_previous_us_day(self):
        levels = {self.FRIDAY: 4.10, self.MONDAY: 4.12, self.TUESDAY: 4.15}
        found = qs._previous_day_levels(levels, [self.FRIDAY, self.MONDAY, self.TUESDAY, self.WEDNESDAY,
                                                 "2026-10-26"])
        self.assertEqual(found, {self.MONDAY: 4.10, self.TUESDAY: 4.12, self.WEDNESDAY: 4.15})


def _korea_account(days: list[str], *, seed: int = 5, beta_market: float = 0.9, beta_won: float = -0.4,
                   alpha: float = 0.0002, rate: float = 0.03) -> dict:
    """Korea iStrategy as load_inputs reads it: won reporting, a KOSPI 200 benchmark, reference
    won/dollar closes, and daily returns that respond to the benchmark and to the previous U.S.
    day's won/dollar move."""
    rng = random.Random(seed)
    level, closes = 1350.0, {}
    for day in days:
        level *= math.exp(rng.gauss(0.0, 0.006))
        closes[day] = level
    daily_rf = (1.0 + rate) ** (1.0 / 252.0) - 1.0
    comparison = []
    for index, day in enumerate(days):
        benchmark = rng.gauss(0.0003, 0.012)
        move = math.log(closes[days[index - 1]] / closes[days[index - 2]]) if index >= 2 else 0.0
        value = daily_rf + alpha + beta_market * (benchmark - daily_rf) + beta_won * move + rng.gauss(0.0, 0.0007)
        comparison.append({"date": day, "portfolio_return": value, "benchmark_return": benchmark})
    return {"subject_id": KOREA, "as_of": days[-1], "subject_type": "physical_account",
            "reporting_currency": "KRW", "benchmark_id": "KS200",
            "risk_free": {"rate_date": "2026-09-01", "annual_rate": rate, "currency": "KRW",
                          "series_id": "OECD-KR-CD-3M"},
            "fx": [{"date": day, "from": "USD", "to": "KRW", "rate": closes[day]} for day in days],
            "comparison": comparison, "positions": [], "closes": closes}


class KoreaMarketModelTests(unittest.TestCase):
    BETAS = {qs.KOREA_MARKET_FACTOR: 0.9, qs.KOREA_FX_FACTOR: -0.4}

    @classmethod
    def setUpClass(cls):
        cls.days = business_days(AS_OF, 300)
        cls.data = _korea_account(cls.days)
        cls.inputs = qs.prepare_phase3_inputs(cls.data, {}, {"factor_attribution": {"model": qs.KOREA_MARKET_MODEL}})
        cls.payload = qs.build_factor_attribution(cls.inputs["factor_attribution"])

    def test_the_inputs_name_the_model_its_benchmark_and_the_alignment(self):
        inputs = self.inputs["factor_attribution"]
        self.assertEqual((inputs["model"], inputs["region"], inputs["region_label"]),
                         (qs.KOREA_MARKET_MODEL, "korea", "Korea market model"))
        self.assertEqual(inputs["factor_names"], [qs.KOREA_MARKET_FACTOR, qs.KOREA_FX_FACTOR])
        self.assertEqual((inputs["benchmark_id"], inputs["risk_free_currency"], inputs["risk_free_series"]),
                         ("KS200", "KRW", "OECD-KR-CD-3M"))
        self.assertIn("previous U.S.-time day", inputs["alignment"])
        daily_rf = 1.03 ** (1.0 / 252.0) - 1.0
        day = self.days[-1]
        row = inputs["factors"][day]
        benchmark = next(r["benchmark_return"] for r in self.data["comparison"] if r["date"] == day)
        self.assertAlmostEqual(row["RF"], daily_rf, places=15)
        self.assertAlmostEqual(row[qs.KOREA_MARKET_FACTOR], benchmark - daily_rf, places=15)
        closes = self.data["closes"]
        self.assertAlmostEqual(row[qs.KOREA_FX_FACTOR], math.log(closes[self.days[-2]] / closes[self.days[-3]]),
                               places=15)

    def test_known_betas_and_alpha_are_recovered_in_both_periods(self):
        payload = self.payload
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        summary = payload["inputs"]
        self.assertEqual((summary["model"], summary["benchmark_id"], summary["risk_free_currency"]),
                         (qs.KOREA_MARKET_MODEL, "KS200", "KRW"))
        self.assertEqual(summary["factors"], list(self.BETAS))
        for period in ("ytd", "past_year"):
            with self.subTest(period=period):
                found = payload["results"][period]
                betas = {row["factor"]: row["beta"] for row in found["exposures"]}
                for name, beta in self.BETAS.items():
                    self.assertAlmostEqual(betas[name], beta, delta=0.03, msg=name)
                self.assertAlmostEqual(found["alpha"]["per_period"], 0.0002, delta=0.00015)
                self.assertGreater(found["r_squared"], 0.95)
        # The first year-to-date day has no FX move before it in this made-up history only if it is
        # the history's first or second day; here every day of the year has one.
        self.assertEqual(payload["inputs"]["ytd"]["observations"],
                         len([d for d in self.days if d >= AS_OF[:4] + "-01-01"]))

    def test_the_same_day_won_move_would_miss_the_exposure(self):
        # What the alignment is for: Korean prices cannot react to a U.S.-time move that has not happened.
        inputs = json.loads(json.dumps(self.inputs["factor_attribution"]))
        closes = self.data["closes"]
        for index, day in enumerate(self.days):
            if day in inputs["factors"]:
                inputs["factors"][day][qs.KOREA_FX_FACTOR] = math.log(closes[day] / closes[self.days[index - 1]])
        found = qs.build_factor_attribution(inputs)["results"]["past_year"]
        betas = {row["factor"]: row["beta"] for row in found["exposures"]}
        self.assertLess(abs(betas[qs.KOREA_FX_FACTOR]), 0.1)
        self.assertLess(found["r_squared"], self.payload["results"]["past_year"]["r_squared"])

    def test_without_reference_won_rates_it_says_why(self):
        data = {**self.data, "fx": []}
        inputs = qs.prepare_phase3_inputs(data, {}, {"factor_attribution": {"model": qs.KOREA_MARKET_MODEL}})
        payload = qs.build_factor_attribution(inputs["factor_attribution"])
        self.assertFalse(payload["available"])
        self.assertIn("won/dollar reference rate have no day in common", payload["reason"])
        self.assertEqual(payload["inputs"]["model"], qs.KOREA_MARKET_MODEL)

    def test_the_loader_reads_no_factor_file_for_korea_and_the_us_file_for_us_iStrategy(self):
        from ibkr_etl import lineage

        record = lineage.Run("ibkrv2stg-quant-analytics")
        record.active = True
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}]})
        korea = {**self.data, "positions": _positions(AS_OF, {"KRX:005930": ("KRW", 9e6)})}
        us = {"subject_id": US_ISTRATEGY, "as_of": AS_OF, "reporting_currency": "USD", "fx": [],
              "positions": _positions(AS_OF, {"US:AAPL": ("USD", 5000.0), "US:NVDA": ("USD", 3000.0)})}
        with patch.object(lineage, "_CURRENT", [record]), \
                patch.object(mi, "read_factor_returns", return_value={"factors": {}}) as factors:
            found = qs.load_phase3_inputs(connection, tenant_id="t", subject_id=KOREA, as_of=AS_OF, data=korea,
                                          sections=["factor_attribution"])
            self.assertEqual(found, {"factor_attribution": {"model": qs.KOREA_MARKET_MODEL}})
            factors.assert_not_called()
            # Korea's model reads no factor file, so lineage records none.
            self.assertEqual([name for direction, _, name in record._datasets if direction == "input"], [])
            found = qs.load_phase3_inputs(connection, tenant_id="t", subject_id=US_ISTRATEGY, as_of=AS_OF, data=us,
                                          sections=["factor_attribution"])
        self.assertEqual(found, {"factor_attribution": {"factors": {}, "region": "us"}})
        self.assertEqual(factors.call_args.kwargs["region"], "us")


class KoreaMacroTests(unittest.TestCase):
    """Korea's macro set: each series at its previous U.S. day, and won per dollar for USD/CNH."""

    # Portfolio return per bp (the yield and the curve) or per unit log return (the others).
    TRUE = {"DGS10": -0.0001, "DGS10-DGS2": 0.00005, "DTWEXBGS": -0.2, "DCOILBRENTEU": 0.05, "DEXKOUS": -0.4}

    @classmethod
    def setUpClass(cls):
        rng = random.Random(21)
        days = business_days(AS_OF, 3 * 260)
        level = {"DGS10": 4.0, "DGS2": 3.5, "DTWEXBGS": 120.0, "DCOILBRENTEU": 80.0, "DEXKOUS": 1350.0,
                 "USDCNH": 7.2}
        levels = {name: {} for name in level}
        effect, comparison = 0.0, []
        for day in days:
            # Korea's day first: it closes before the U.S. day's moves happen.
            comparison.append({"date": day, "portfolio_return": effect + rng.gauss(0.0, 0.0005),
                               "benchmark_return": 0.0})
            ten, two = rng.gauss(0.0, 5.0), rng.gauss(0.0, 5.0)
            logs = {"DTWEXBGS": rng.gauss(0.0, 0.003), "DCOILBRENTEU": rng.gauss(0.0, 0.02),
                    "DEXKOUS": rng.gauss(0.0, 0.005), "USDCNH": rng.gauss(0.0, 0.003)}
            level["DGS10"] += ten / 100.0
            level["DGS2"] += two / 100.0
            for name, change in logs.items():
                level[name] *= math.exp(change)
            for name in level:
                levels[name][day] = level[name]
            effect = (cls.TRUE["DGS10"] * ten + cls.TRUE["DGS10-DGS2"] * (ten - two)
                      + sum(cls.TRUE[name] * logs[name] for name in ("DTWEXBGS", "DCOILBRENTEU", "DEXKOUS")))
        cls.days, cls.levels = days, levels
        cls.data = {"subject_id": KOREA, "as_of": AS_OF, "reporting_currency": "KRW", "positions": [],
                    "comparison": comparison}
        cls.market = {"macro": {"series": levels, "units": {name: "x" for name in levels},
                                "sources": {name: "fred" for name in levels}}}
        cls.inputs = qs.prepare_phase3_inputs(cls.data, {}, cls.market)["macro"]
        cls.payload = qs.build_macro(cls.inputs)

    def test_the_levels_are_re_dated_and_the_inputs_say_so(self):
        self.assertEqual(self.inputs["model"], qs.KOREA_MARKET_MODEL)
        self.assertIn("previous U.S. day", self.inputs["alignment"])
        monday = next(d for d in self.days[5:] if date.fromisoformat(d).weekday() == 0)
        friday = self.days[self.days.index(monday) - 1]
        self.assertEqual(self.inputs["levels"]["DEXKOUS"][monday], self.levels["DEXKOUS"][friday])
        self.assertNotIn(self.days[0], self.inputs["levels"]["DEXKOUS"])  # nothing before the first level
        # A dollar account keeps every series on its own date and the standard set.
        us = qs.prepare_phase3_inputs({**self.data, "reporting_currency": "USD"}, {}, self.market)["macro"]
        self.assertNotIn("model", us)
        self.assertEqual(us["levels"]["DEXKOUS"][monday], self.levels["DEXKOUS"][monday])

    def test_known_sensitivities_are_recovered_and_the_won_shock_follows(self):
        payload = self.payload
        self.assertTrue(payload["available"], payload["reason"])
        self.assertEqual(qs.quality_status(payload), "pass")
        self.assertEqual(payload["inputs"]["model"], qs.KOREA_MARKET_MODEL)
        self.assertEqual(payload["inputs"]["factors"], [n for n in qs.MACRO_FACTORS_KOREA if n != "VIXCLS"])
        self.assertEqual(payload["inputs"]["factors_missing"], ["VIXCLS"])
        fitted = payload["results"]["macro_exposures"]
        betas = {row["factor"]: row["beta"] for row in fitted["exposures"]}
        self.assertNotIn("USDCNH", betas)
        for name, beta in self.TRUE.items():
            tolerance = 0.00002 if name.startswith("DGS10") else 0.03
            self.assertAlmostEqual(betas[name], beta, delta=tolerance, msg=name)
        self.assertGreater(fitted["r_squared"], 0.9)
        shocks = payload["results"]["macro_sensitivity"]
        self.assertEqual(set(shocks), set(qs.MACRO_SHOCKS_KOREA) - {"volatility_up_50pct"})
        self.assertNotIn("yuan_down_5pct", shocks)
        self.assertAlmostEqual(shocks["won_down_5pct"]["impact"], betas["DEXKOUS"] * math.log(1.05), places=12)
        risk_off = sum(betas[name] * (shock if name.startswith("DGS10") else math.log1p(shock))
                       for name, shock in qs.MACRO_SHOCKS_KOREA["risk_off"].items() if name != "VIXCLS")
        self.assertAlmostEqual(shocks["risk_off"]["impact"], risk_off, places=12)
        self.assertEqual({row["factor"] for row in shocks["risk_off"]["factors"]},
                         {"DGS10", "DTWEXBGS", "DCOILBRENTEU", "DEXKOUS"})

    def test_on_same_day_levels_the_won_exposure_comes_out_too_small(self):
        # One day of each week's U.S. moves falls in the next Korean week, so about a fifth is lost.
        inputs = {**self.inputs, "levels": {name: {d: v for d, v in series.items() if d in self.inputs["returns"]}
                                            for name, series in self.levels.items()}}
        fitted = qs.build_macro(inputs)["results"]["macro_exposures"]
        beta = {row["factor"]: row["beta"] for row in fitted["exposures"]}["DEXKOUS"]
        self.assertLess(abs(beta), 0.95 * abs(self.TRUE["DEXKOUS"]))
        self.assertGreater(abs(beta), 0.6 * abs(self.TRUE["DEXKOUS"]))
        self.assertLess(fitted["r_squared"], 0.8)  # against over 0.9 aligned


class KiwoomFlowTests(unittest.TestCase):
    """U.S. iStrategy's won-to-dollar top-ups are money from outside, as the benchmark job reads them."""

    ROWS = [
        {"source_kind": "physical_account", "effective_date": "2026-03-02", "transaction_type": "DEPOSIT",
         "description": "KRW to USD", "amount_text": "3000", "currency": "USD", "included": None,
         "external_flow": None},
        {"source_kind": "physical_account", "effective_date": "2026-05-04", "transaction_type": "WITHDRAWAL",
         "description": "USD to KRW", "amount_text": "-500", "currency": "USD", "included": None,
         "external_flow": None},
        {"source_kind": "physical_account", "effective_date": "2026-06-01", "transaction_type": "DIVIDEND",
         "description": "AAPL dividend", "amount_text": "4.2", "currency": "USD", "included": None,
         "external_flow": None},
    ]

    def _flows(self, kiwoom: bool):
        connection = _Connection({"FROM subject_cash_activity_series": self.ROWS})
        return qs._external_flows(connection, tenant_id="t", subject_id=US_ISTRATEGY, start="2026-01-01",
                                  end=AS_OF, kiwoom_cash_flows=kiwoom)

    def test_a_kiwoom_dollar_accounts_deposits_and_withdrawals_are_flows(self):
        self.assertEqual(self._flows(True), [{"date": "2026-03-02", "amount": "3000", "currency": "USD"},
                                             {"date": "2026-05-04", "amount": "-500", "currency": "USD"}])
        # Read by IBKR's labels instead, they would count as performance.
        self.assertEqual(self._flows(False), [])

    def test_only_kiwoom_dollar_accounts_read_them(self):
        def answer(base, kind="kiwoom"):
            rows = [{"base_currency": base}] if kind == "kiwoom" else []
            return _Connection({"FROM accounts": lambda params: rows})

        self.assertTrue(qs._kiwoom_cash_flows(answer("USD"), tenant_id="t", account_id=US_ISTRATEGY))
        # Korea's won account is left as the benchmark job has it, and an IBKR account reads its labels.
        self.assertFalse(qs._kiwoom_cash_flows(answer("KRW"), tenant_id="t", account_id=KOREA))
        self.assertFalse(qs._kiwoom_cash_flows(answer("USD", "ibkr"), tenant_id="t", account_id="U1234567"))
        connection = answer("USD")
        qs._kiwoom_cash_flows(connection, tenant_id="t", account_id=US_ISTRATEGY)
        sql, params = connection.statements[-1]
        self.assertIn("account_type = 'kiwoom'", sql)
        self.assertEqual(params, ("t", US_ISTRATEGY))


class KiwoomTradesAndCurrencyTests(unittest.TestCase):
    def test_kiwoom_trades_are_held_back_with_the_reason(self):
        import test_quant_snapshots as build_data

        data = build_data.synthetic_data(days=300)
        prepared = qs.prepare_inputs({**data, "trade_detail_missing": qs.TRADE_DETAIL_MISSING["kiwoom"]})
        for section in ("decisions", "execution"):
            with self.subTest(section=section):
                self.assertEqual(prepared[section]["trades"], [])
                self.assertEqual(prepared[section]["no_trades"], qs.TRADE_DETAIL_MISSING["kiwoom"])
                payload = qs.BUILDERS[section](prepared[section])
                text = json.dumps(payload)
                self.assertIn("trade-by-trade analysis is not yet available", text)
        # The same data without the flag has trades, so the hold is the flag's doing.
        self.assertTrue(qs.prepare_inputs(data)["execution"]["trades"])

    def test_an_account_holding_only_its_own_currency_says_there_is_no_currency_effect(self):
        period = {"start_nav": "1000000", "benchmark_start": "100", "benchmark_end": "104",
                  "holdings": [{"currency": "KRW", "start_value": "900000", "end_value": "950000"}]}
        payload = qs.build_currency({"as_of": AS_OF, "base_currency": "KRW", "periods": {"mtd": period, "ytd": period}})
        self.assertTrue(payload["inputs"]["single_currency"])
        mixed = {**period, "holdings": [*period["holdings"],
                                        {"currency": "USD", "start_value": "50000", "end_value": "51000"}]}
        payload = qs.build_currency({"as_of": AS_OF, "base_currency": "KRW", "periods": {"mtd": mixed, "ytd": mixed}})
        self.assertNotIn("single_currency", payload["inputs"])


class CrowdingTests(unittest.TestCase):
    APPLE = "037833100"

    def test_each_holding_gets_the_right_reason(self):
        cases = [
            ({"conid": "US:AAPL", "currency": "USD", "asset_category": "STK"},
             "No reviewed CUSIP for this U.S. ticker yet: its broker gives tickers, not ISINs."),
            ({"conid": "US:AAPL", "currency": "USD", "asset_category": "STK", "cusip_from_ticker": self.APPLE}, None),
            ({"conid": "KRX:005930", "currency": "KRW", "asset_category": "STK"}, "13F covers U.S.-listed shares only."),
            ({"conid": "KRX:005930", "isin": "KR7005930003", "currency": "KRW", "asset_category": "STK"},
             "Not a US ISIN, so not in 13F filings."),
            ({"conid": "265598", "isin": "US0378331005", "currency": "USD", "asset_category": "STK"}, None),
            ({"conid": "265599", "currency": "USD", "asset_category": "STK"}, "No ISIN in silver instruments."),
            ({"conid": "KRX:069500", "currency": "KRW", "asset_category": "ETF"}, "Only shares are in 13F filings."),
        ]
        for info, reason in cases:
            with self.subTest(info=info):
                self.assertEqual(qs._not_in_13f(info), reason)
        self.assertEqual(qs._holding_cusip(cases[1][0]), self.APPLE)
        self.assertEqual(qs._holding_cusip(cases[4][0]), self.APPLE)
        self.assertIsNone(qs._holding_cusip(cases[0][0]))

    def test_the_section_lists_covered_holdings_by_cusip(self):
        holdings = {"holdings_date": AS_OF, "holdings": [
            {"name": "AAPL", "conid": "US:AAPL", "weight": 0.6}, {"name": "NVDA", "conid": "US:NVDA", "weight": 0.4}]}
        market = {"crowding": {"instruments": {
            "US:AAPL": {"conid": "US:AAPL", "isin": None, "currency": "USD", "asset_category": "STK",
                        "cusip_from_ticker": self.APPLE},
            "US:NVDA": {"conid": "US:NVDA", "isin": None, "currency": "USD", "asset_category": "STK"}},
            "filings": {"filers": [], "stakes": []}}}
        data = {"as_of": AS_OF, "reporting_currency": "USD", "positions": [], "comparison": []}
        found = qs.prepare_phase3_inputs(data, {"holdings_risk": holdings}, market)["crowding"]
        self.assertEqual(found["covered"], [{"name": "AAPL", "conid": "US:AAPL", "weight": 0.6, "cusip": self.APPLE}])
        self.assertEqual([row["conid"] for row in found["not_covered"]], ["US:NVDA"])
        self.assertIn("No reviewed CUSIP", found["not_covered"][0]["reason"])

    def test_the_loader_matches_a_reviewed_ticker_and_asks_13f_for_its_cusip(self):
        connection = _Connection({"pg_catalog.pg_class": [{"present": True}]})
        data = {"subject_id": US_ISTRATEGY, "as_of": AS_OF, "reporting_currency": "USD", "fx": [],
                "positions": _positions(AS_OF, {"US:AAPL": ("USD", 5000.0), "US:NVDA": ("USD", 3000.0)})}
        instruments = {conid: {"symbol": conid[3:], "listing_exchange": None, "currency": "USD", "isin": None,
                               "asset_category": "STK"} for conid in ("US:AAPL", "US:NVDA")}
        with patch.object(mi, "read_instruments", return_value=instruments), \
                patch.object(sec_13f, "reviewed_ticker_cusips", return_value={"AAPL": self.APPLE}), \
                patch.object(mi, "read_institutional_holdings", return_value={"filers": [], "stakes": []}) as filings:
            found = qs.load_phase3_inputs(connection, tenant_id="t", subject_id=US_ISTRATEGY, as_of=AS_OF,
                                          data=data, sections=["crowding"])
        self.assertEqual(found["crowding"]["instruments"]["US:AAPL"]["cusip_from_ticker"], self.APPLE)
        self.assertNotIn("cusip_from_ticker", found["crowding"]["instruments"]["US:NVDA"])
        self.assertEqual(filings.call_args.kwargs["cusips"], [self.APPLE])

    def _list(self, document) -> Path:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "us_ticker_cusips.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_the_ticker_list_counts_only_once_reviewed_and_refuses_a_bad_entry(self):
        self.assertEqual(sec_13f.reviewed_ticker_cusips(), {}, "the shipped list is empty and not reviewed")
        shipped = json.loads(sec_13f.TICKER_CUSIPS_FILE.read_text(encoding="utf-8"))
        self.assertIs(shipped["reviewed"], False)
        self.assertEqual(sec_13f.reviewed_ticker_cusips(Path("/nonexistent/us_ticker_cusips.json")), {})
        entries = {"aapl": {"cusip": self.APPLE, "issuer": "APPLE INC"}, "MSFT": "594918104"}
        self.assertEqual(sec_13f.reviewed_ticker_cusips(self._list({"reviewed": False, "tickers": entries})), {})
        self.assertEqual(sec_13f.reviewed_ticker_cusips(self._list({"reviewed": True, "tickers": entries})),
                         {"AAPL": self.APPLE, "MSFT": "594918104"})
        for bad in ({"AAPL": "037833101"}, {"AAPL": "03783310"}, {"AAPL": {"issuer": "APPLE INC"}},
                    {"AAPL": self.APPLE, "aapl": self.APPLE}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sec_13f.reviewed_ticker_cusips(self._list({"reviewed": True, "tickers": bad}))


class ShadowRunTests(unittest.TestCase):
    def setUp(self):
        from test_quant_shadow_run import _module

        self.module = _module()

    def test_a_kiwoom_account_number_and_its_subject_id_take_one_label(self):
        masker = self.module.Masker(["U1234567", KOREA, US_ISTRATEGY], aliases={"60000001": KOREA, "": KOREA,
                                                                            "60000002": "KIWOOM-UNKNOWN"})
        self.assertEqual(masker.labels[KOREA], "u01")
        self.assertEqual(masker.text(f"{KOREA} is account 60000001; {US_ISTRATEGY} and U1234567"),
                         "u01 is account u01; u02 and u03")
        self.assertNotIn("60000002", masker.labels)  # an alias of no known subject is not a label
        self.assertEqual(masker.text("KIWOOM-Z9 failed"), "u?? failed")
        self.assertEqual(masker.clean("KIWOOM-Z9 failed on 2026-10-30 after 12 tries"), "u?? failed on <date> after <n> tries")

    def test_each_subject_is_grouped_by_broker(self):
        group = self.module._broker_group
        self.assertEqual(group("kiwoom", "physical_account"), "kiwoom")
        self.assertEqual(group("KIWOOM", "physical_account"), "kiwoom")
        self.assertEqual(group("Individual", "physical_account"), "ibkr")
        self.assertEqual(group(None, "physical_account"), "ibkr")
        self.assertEqual(group(None, "composite_portfolio"), "derived")
        self.assertEqual(group("kiwoom", "filtered_portfolio"), "derived")


class KoreaCardRenderingTests(unittest.TestCase):
    """Korea's factor and macro cards and a one-currency currency card, as the page renders them."""

    @classmethod
    def setUpClass(cls):
        import test_web_quant as wq

        if not wq.NODE:
            raise unittest.SkipTest("Node is not installed in this test environment")
        factor = KoreaMarketModelTests
        factor.setUpClass()
        macro = KoreaMacroTests
        macro.setUpClass()
        payloads = {"factor_attribution": qs.finite_copy(factor.payload)[0],
                    "macro": qs.finite_copy(macro.payload)[0]}
        cls.out, cls.responses = wq.Phase3RenderingTest._render(payloads)
        period = {"start_nav": "1000000", "benchmark_start": "100", "benchmark_end": "104",
                  "holdings": [{"currency": "KRW", "start_value": "900000", "end_value": "950000"}]}
        currency = qs.build_currency({"as_of": AS_OF, "base_currency": "KRW", "periods": {"mtd": period, "ytd": period}})
        code = wq._page_helpers() + f"const p={json.dumps(qs.finite_copy(currency)[0])};\n" + \
            "console.log(JSON.stringify({note:quantRenderCurrency(p.results,p)[1]}));"
        cls.currency = wq._node(code)

    def test_the_factor_card_names_the_korea_model(self):
        shown = self.out["factor_attribution"]
        for bad in ("undefined", "NaN", "[object Object]", "null"):
            self.assertNotIn(bad, shown["body"] + shown["note"])
        self.assertIn("Korean market (the benchmark)", shown["body"])
        self.assertIn("Won per dollar (previous U.S. day)", shown["body"])
        self.assertIn("Korea market model: the account's benchmark (KS200)", shown["note"])
        self.assertIn("No daily style factors cover Korean stocks.", shown["note"])

    def test_the_macro_card_shows_the_won_and_the_alignment(self):
        shown = self.out["macro"]
        for bad in ("undefined", "NaN", "[object Object]", "null"):
            self.assertNotIn(bad, shown["body"] + shown["note"])
        self.assertIn('<div class="label">Won 5% weaker</div>', shown["body"])
        self.assertNotIn("Yuan 5% weaker", shown["body"])
        self.assertIn("Won per dollar", shown["body"])
        self.assertIn('title="Yield −50bp, dollar +5%, Brent −20%, won per dollar +3% together; VIX left out"',
                      shown["body"])
        self.assertIn("Korean returns are compared with each series at the previous U.S. day", shown["note"])

    def test_the_currency_card_says_there_is_no_currency_effect(self):
        self.assertIn("All holdings are in KRW, so there is no currency effect.", self.currency["note"])


if __name__ == "__main__":
    unittest.main()
