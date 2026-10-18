from __future__ import annotations

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import DISCLAIMER, ModellingInputError  # noqa: E402
from ibkr_etl.quant import desk_risk as dr  # noqa: E402
from ibkr_etl.quant import holdings_risk as hr  # noqa: E402

NOW = "2026-10-16T14:00:00Z"
FX = {"HKD": {"rate": 0.1282, "prior_close": 0.128}}
POSITIONS = [
    {"account_id": "U1", "conid": "1", "symbol": "AAA", "currency": "USD", "quantity": 100},
    {"account_id": "U1", "conid": "2", "symbol": "BBB", "currency": "HKD", "quantity": 1000},
]
QUOTES = [
    {"conid": "1", "observed_at": NOW, "currency": "USD", "last_price": 101, "bid_price": 100.9, "ask_price": 101.1,
     "prior_close": 100, "market_data_type": 1},
    {"conid": "2", "observed_at": "2026-10-16T13:58:00+00:00", "currency": "HKD", "bid_price": 9.9, "ask_price": 10.1,
     "prior_close": 9.5, "market_data_type": 3},
]


def _row(result, key):
    return next(r for r in result["holdings"] if r["key"] == key)


class IntradayPnlTests(unittest.TestCase):
    def test_hand_computed_pnl_splits_fx_from_price(self) -> None:
        result = dr.intraday_pnl(POSITIONS, QUOTES, fx_rates=FX, reporting_currency="USD")
        a, b = _row(result, "1"), _row(result, "2")
        self.assertEqual((a["price_source"], b["price_source"]), ("last", "mid"))
        self.assertAlmostEqual(a["pnl_local"], 100.0)
        self.assertAlmostEqual(b["pnl_local"], 1000 * (10.0 - 9.5))
        self.assertAlmostEqual(b["price_pnl_reporting"], 500 * 0.1282)            # local P&L at today's rate
        self.assertAlmostEqual(b["fx_pnl_reporting"], 1000 * 9.5 * (0.1282 - 0.128))  # FX on the opening value
        self.assertAlmostEqual(b["pnl_reporting"], 1000 * (10 * 0.1282 - 9.5 * 0.128))
        self.assertAlmostEqual(b["return"], 10 / 9.5 - 1)
        self.assertAlmostEqual(b["fx_return"], 0.1282 / 0.128 - 1)
        total = result["total"]
        self.assertAlmostEqual(total["pnl"], 100 + 66.0)
        self.assertAlmostEqual(total["price_pnl"] + total["fx_pnl"], total["pnl"])
        self.assertAlmostEqual(result["totals_by_currency"]["HKD"]["pnl"], 500.0)
        self.assertEqual(result["as_of"], "2026-10-16T14:00:00+00:00")
        self.assertEqual(result["as_of_source"], "newest quote")
        self.assertEqual(b["quote_age_seconds"], 120.0)
        self.assertTrue(b["delayed"])
        self.assertEqual(result["excluded"], [])
        json.dumps(result, allow_nan=False)

    def test_stale_missing_and_unusable_quotes_are_flagged_not_guessed(self) -> None:
        positions = POSITIONS + [
            {"conid": "3", "symbol": "OLD", "currency": "USD", "quantity": 10},
            {"conid": "4", "symbol": "NONE", "currency": "USD", "quantity": 10},
            {"conid": "5", "symbol": "CLOSEONLY", "currency": "USD", "quantity": 10},
            {"conid": "6", "symbol": "NOCLOSE", "currency": "USD", "quantity": 10},
            {"conid": "7", "symbol": "CROSSED", "currency": "USD", "quantity": 10},
            {"conid": "8", "symbol": "WRONGCCY", "currency": "USD", "quantity": 10},
        ]
        quotes = QUOTES + [
            {"conid": "3", "observed_at": "2026-10-16T13:00:00Z", "currency": "USD", "last_price": 50, "prior_close": 40},
            {"conid": "5", "observed_at": NOW, "currency": "USD", "prior_close": 40},
            {"conid": "6", "observed_at": NOW, "currency": "USD", "last_price": 40},
            {"conid": "7", "observed_at": NOW, "currency": "USD", "bid_price": 41, "ask_price": 40, "prior_close": 40},
            {"conid": "8", "observed_at": NOW, "currency": "EUR", "last_price": 41, "prior_close": 40},
        ]
        result = dr.intraday_pnl(positions, quotes, fx_rates=FX, reporting_currency="USD")
        reasons = {r["key"]: r["status"] for r in result["excluded"]}
        self.assertEqual(reasons, {"3": "stale", "4": "no_quote", "5": "no_price", "6": "no_prior_close",
                                   "7": "no_price", "8": "currency_mismatch"})
        stale = next(r for r in result["excluded"] if r["key"] == "3")
        self.assertEqual(stale["quote_age_seconds"], 3600.0)
        self.assertAlmostEqual(result["total"]["pnl"], 166.0)                 # unchanged by the excluded holdings
        self.assertEqual(result["counted"], 2)
        self.assertIsNone(_row(result, "3")["pnl_local"])
        self.assertEqual(_row(result, "3")["price"], 50)                     # shown, but not counted
        freshness = result["freshness"]
        self.assertEqual((freshness["stale"], freshness["missing"], freshness["oldest_age_seconds"]), (1, 1, 3600.0))
        loose = dr.intraday_pnl(positions, quotes, fx_rates=FX, reporting_currency="USD", max_quote_age_seconds=7200)
        self.assertAlmostEqual(_row(loose, "3")["pnl_local"], 100.0)

    def test_as_of_picks_the_quote_in_force_then(self) -> None:
        quotes = [
            {"conid": "1", "observed_at": "2026-10-16T13:50:00Z", "last_price": 102, "prior_close": 100},
            {"conid": "1", "observed_at": "2026-10-16T14:10:00Z", "last_price": 105, "prior_close": 100},
            {"conid": "2", "observed_at": "2026-10-16T14:20:00Z", "last_price": 10, "prior_close": 9.5},
        ]
        result = dr.intraday_pnl(POSITIONS, quotes, fx_rates=FX, reporting_currency="USD", as_of=NOW)
        self.assertEqual(result["as_of_source"], "given")
        self.assertAlmostEqual(_row(result, "1")["pnl_local"], 200.0)
        self.assertEqual(_row(result, "1")["quote_age_seconds"], 600.0)
        self.assertEqual(_row(result, "2")["status"], "quote_after_as_of")
        naive = dr.intraday_pnl(POSITIONS, quotes, fx_rates=FX, reporting_currency="USD", as_of="2026-10-16T14:00:00")
        self.assertEqual(naive["as_of"], result["as_of"])                      # a time without a zone is UTC

    def test_fills_today_earn_from_their_price(self) -> None:
        positions = [{"conid": "1", "currency": "USD", "quantity": 100}]
        quotes = [{"conid": "1", "observed_at": NOW, "last_price": 101, "prior_close": 100},
                  {"conid": "9", "observed_at": "2026-10-16T10:00:00Z", "currency": "USD", "last_price": 21,
                   "prior_close": 20}]
        executions = [
            {"conid": "1", "side": "BUY", "quantity": 40, "price": 102},       # 60 held overnight, 40 bought today
            {"conid": "9", "side": "SELL", "quantity": 50, "price": 21.5, "currency": "USD"},   # sold out today
        ]
        result = dr.intraday_pnl(positions, quotes, reporting_currency="USD", executions=executions)
        bought = _row(result, "1")
        self.assertAlmostEqual(bought["pnl_local"], 60 * (101 - 100) + 40 * (101 - 102))
        self.assertAlmostEqual(bought["trading_pnl_local"], 40 * (100 - 102))
        sold = _row(result, "9")
        self.assertEqual(sold["status"], "ok")                                  # needs only its prior close
        self.assertAlmostEqual(sold["pnl_local"], 50 * (21.5 - 20))
        self.assertAlmostEqual(result["total"]["trading_pnl"], -80 + 75)
        round_trip = dr.intraday_pnl(positions, quotes, reporting_currency="USD", executions=[
            {"conid": "1", "side": "BUY", "quantity": 10, "price": 100.5},
            {"conid": "1", "side": "SELL", "quantity": 10, "price": 100.8}])
        # 100 held overnight; the round trip adds what the sale made over the purchase.
        self.assertAlmostEqual(_row(round_trip, "1")["pnl_local"], 100 * (101 - 100) + 10 * (100.8 - 100.5))

    def test_composite_is_summed_and_multiplier_is_inferred(self) -> None:
        positions = [
            {"account_id": "U1", "conid": "1", "currency": "USD", "quantity": 60},
            {"account_id": "U2", "conid": "1", "currency": "USD", "quantity": 40},
            {"account_id": "U1", "conid": "5", "currency": "USD", "quantity": 2, "market_price": 3.5,
             "market_value": 2 * 3.5 * 100},                                    # an option: multiplier 100
        ]
        quotes = [{"conid": "1", "observed_at": NOW, "last_price": 101, "prior_close": 100},
                  {"conid": "5", "observed_at": NOW, "last_price": 3.5, "prior_close": 3.0}]
        result = dr.intraday_pnl(positions, quotes, reporting_currency="USD")
        self.assertEqual(_row(result, "1")["quantity"], 100)
        self.assertEqual(_row(result, "1")["accounts"], ["U1", "U2"])
        self.assertEqual(_row(result, "5")["multiplier"], 100)
        self.assertAlmostEqual(_row(result, "5")["pnl_local"], 2 * 100 * 0.5)
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl([{"conid": "1", "currency": "USD", "quantity": 1},
                             {"conid": "1", "currency": "HKD", "quantity": 1}], quotes, reporting_currency="USD")

    def test_missing_fx_rate_counts_in_local_currency_only(self) -> None:
        result = dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="USD")
        self.assertEqual(_row(result, "2")["status"], "no_fx_rate")
        self.assertAlmostEqual(result["totals_by_currency"]["HKD"]["pnl"], 500.0)
        self.assertAlmostEqual(result["total"]["pnl"], 100.0)
        self.assertEqual([r["key"] for r in result["excluded"]], ["2"])

    def test_foreign_cash_is_revalued(self) -> None:
        result = dr.intraday_pnl(POSITIONS, QUOTES, fx_rates=FX, reporting_currency="USD",
                                 cash={"HKD": 100000, "USD": 5000, "EUR": 10})
        self.assertAlmostEqual(result["total"]["cash_fx_pnl"], 100000 * 0.0002)
        self.assertAlmostEqual(result["total"]["pnl"], 166.0 + 20.0)
        self.assertEqual(result["cash_without_fx_rate"], ["EUR"])

    def test_hkd_reporting_inverts_nothing(self) -> None:
        fx = {"USD": {"rate": 7.80, "prior_close": 7.78}}
        result = dr.intraday_pnl(POSITIONS, QUOTES, fx_rates=fx, reporting_currency="HKD")
        a = _row(result, "1")
        self.assertAlmostEqual(a["pnl_reporting"], 100 * (101 * 7.80 - 100 * 7.78))
        self.assertAlmostEqual(_row(result, "2")["pnl_reporting"], 500.0)

    def test_bad_input_is_refused(self) -> None:
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="US")
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="USD", max_quote_age_seconds=0)
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="USD", as_of="yesterday")
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl([{"currency": "USD", "quantity": 1}], QUOTES, reporting_currency="USD")
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="USD", fx_rates={"HKD": 0.128})
        with self.assertRaises(ModellingInputError):
            dr.intraday_pnl(POSITIONS, QUOTES, reporting_currency="USD",
                            executions=[{"conid": "1", "side": "HOLD", "quantity": 1, "price": 1}])


class PnlExplainTests(unittest.TestCase):
    RETURNS = {"A": 0.02, "B": -0.01}
    BETAS = {"A": {"MKT": 1.0, "HML": 0.5}, "B": {"MKT": 0.8, "HML": -0.2}}
    FACTORS = {"MKT": 0.01, "HML": 0.004}
    WEIGHTS = {"A": 0.6, "B": 0.3}

    def test_hand_computed_split(self) -> None:
        result = dr.pnl_explain(self.RETURNS, self.BETAS, self.FACTORS, weights=self.WEIGHTS, fx_returns={"B": 0.01})
        self.assertEqual(result["market_factor"], "MKT")
        self.assertAlmostEqual(result["market"], 0.6 * 0.01 + 0.3 * 1.01 * 0.8 * 0.01, places=15)
        hml = next(f for f in result["factors"] if f["factor"] == "HML")
        self.assertAlmostEqual(hml["contribution"], 0.6 * 0.5 * 0.004 + 0.3 * 1.01 * -0.2 * 0.004, places=15)
        self.assertAlmostEqual(hml["exposure"], 0.6 * 0.5 + 0.3 * 1.01 * -0.2, places=15)
        self.assertAlmostEqual(result["fx"], 0.003, places=15)
        self.assertAlmostEqual(result["specific"], 0.6 * 0.008 + 0.303 * (-0.01 - 0.0072), places=15)
        self.assertAlmostEqual(result["total_return"], 0.6 * 0.02 + 0.3 * (0.99 * 1.01 - 1), places=15)
        self.assertAlmostEqual(result["total_return"], 0.01197, places=15)
        self.assertEqual([p["kind"] for p in result["parts"]], ["market", "factor", "fx", "specific"])
        self.assertAlmostEqual(result["residual"], 0.0, places=15)
        self.assertAlmostEqual(result["cash_weight"], 0.1)
        self.assertEqual(result["disclaimer"], DISCLAIMER)

    def test_parts_add_up_with_a_fitted_factor_model(self) -> None:
        generator = random.Random(11)
        days, names = 250, [f"H{i}" for i in range(12)]
        factors = {f: [generator.gauss(0, 0.01) for _ in range(days)] for f in ("Mkt-RF", "SMB", "HML")}
        assets = {}
        for i, name in enumerate(names):
            betas = (0.5 + 0.1 * i, 0.3 - 0.05 * i, generator.uniform(-0.5, 0.5))
            assets[name] = [sum(b * factors[f][t] for b, f in zip(betas, factors)) + generator.gauss(0, 0.008)
                            for t in range(days)]
        model = hr.factor_risk_model(assets, factors)
        weights = {name: generator.uniform(-0.05, 0.15) for name in names}
        today = {name: generator.gauss(0, 0.02) for name in names}
        moves = {f: generator.gauss(0, 0.01) for f in factors}
        fx = {name: generator.gauss(0, 0.003) for name in names[::2]}
        result = dr.pnl_explain(today, model, moves, weights=weights, fx_returns=fx)
        self.assertEqual(result["market_factor"], "Mkt-RF")
        self.assertAlmostEqual(sum(p["contribution"] for p in result["parts"]), result["total_return"], places=15)
        expected = sum(w * ((1 + today[k]) * (1 + fx.get(k, 0.0)) - 1) for k, w in weights.items())
        self.assertAlmostEqual(result["total_return"], expected, places=15)
        # A day whose return is exactly the factor prediction has no specific part.
        betas = dict(zip(model["factors"], model["betas"][0]))
        exact = {"H0": sum(b * moves[f] for f, b in betas.items())}
        alone = dr.pnl_explain(exact, model, moves, weights={"H0": 1.0})
        self.assertAlmostEqual(alone["specific"], 0.0, places=16)
        self.assertAlmostEqual(alone["fx"], 0.0)

    def test_holding_without_betas_is_all_specific(self) -> None:
        result = dr.pnl_explain({"A": 0.02, "NEW": 0.05}, self.BETAS, self.FACTORS,
                                weights={"A": 0.5, "NEW": 0.1}, market_factor="HML")
        self.assertEqual(result["no_factor_betas"], ["NEW"])
        new = next(h for h in result["holdings"] if h["name"] == "NEW")
        self.assertAlmostEqual(new["specific"], 0.005)
        self.assertEqual(result["market_factor"], "HML")
        self.assertEqual(dr.pnl_explain({"A": 0.01}, {"A": {"SMB": 1.0}}, {"SMB": 0.01, "UMD": 0.0},
                                        weights={"A": 1.0})["market_factor"], None)

    def test_bad_input_is_refused(self) -> None:
        with self.assertRaises(ModellingInputError):
            dr.pnl_explain({"A": 0.01}, self.BETAS, self.FACTORS, weights=self.WEIGHTS)       # B has no return
        with self.assertRaises(ModellingInputError):
            dr.pnl_explain(self.RETURNS, self.BETAS, {"MKT": 0.01}, weights=self.WEIGHTS)     # HML has no return
        with self.assertRaises(ModellingInputError):
            dr.pnl_explain({"A": -1.5, "B": 0.0}, self.BETAS, self.FACTORS, weights=self.WEIGHTS)
        with self.assertRaises(ModellingInputError):
            dr.pnl_explain(self.RETURNS, self.BETAS, self.FACTORS, weights={"A": 0.0})
        with self.assertRaises(ModellingInputError):
            dr.pnl_explain(self.RETURNS, self.BETAS, self.FACTORS, weights=self.WEIGHTS, market_factor="UMD")


class LimitCheckTests(unittest.TestCase):
    METRICS = {
        "gross_exposure": 1.2, "net_exposure": 0.9, "largest_position_weight": 0.06, "ex_ante_volatility": 0.25,
        "var_1d": 0.019, "liquidity_days": 3.0,
        "factor_exposures": {"Mkt-RF": 1.1, "HML": -0.45, "SMB": 0.1},
    }
    LIMITS = {
        "gross_exposure": 1.5, "net_exposure": (-0.2, 1.0), "largest_position_weight": 0.1,
        "ex_ante_volatility": 0.2, "var_1d": 0.02, "liquidity_days": 5,
        "factor_exposures": {"Mkt-RF": (0.8, 1.2), "*": 0.4},
    }

    def test_hand_computed_usage_and_status(self) -> None:
        result = dr.limit_check(self.METRICS, self.LIMITS)
        checks = {c["metric"]: c for c in result["checks"]}
        expected = {
            "gross_exposure": (0.8, "amber"), "net_exposure": (0.9, "amber"),
            "largest_position_weight": (0.6, "green"), "ex_ante_volatility": (1.25, "red"),
            "var_1d": (0.95, "amber"), "liquidity_days": (0.6, "green"),
            "factor_exposures.Mkt-RF": (0.5, "green"),          # from the middle of 0.8-1.2 to its edge
            "factor_exposures.HML": (1.125, "red"), "factor_exposures.SMB": (0.25, "green"),
        }
        for metric, (usage, status) in expected.items():
            self.assertAlmostEqual(checks[metric]["usage"], usage, msg=metric)
            self.assertEqual(checks[metric]["status"], status, metric)
        self.assertEqual([b["metric"] for b in result["breaches"]], ["ex_ante_volatility", "factor_exposures.HML"])
        self.assertAlmostEqual(checks["factor_exposures.HML"]["excess"], -0.05)
        self.assertEqual(result["status"], "red")
        self.assertEqual(result["counts"], {"green": 4, "amber": 3, "red": 2})

    def test_exactly_at_the_limit_is_amber_not_red(self) -> None:
        result = dr.limit_check({"var_1d": 0.02, "net_exposure": -0.2}, {"var_1d": 0.02, "net_exposure": (-0.2, 1.0)})
        self.assertEqual([c["status"] for c in result["checks"]], ["amber", "amber"])
        self.assertEqual([c["usage"] for c in result["checks"]], [1.0, 1.0])

    def test_zero_bounds_missing_metrics_and_unlimited_metrics(self) -> None:
        result = dr.limit_check({"net_exposure": -0.1, "gross_exposure": 3.0, "beta": 1.0},
                                {"net_exposure": (0.0, 1.0), "gross_exposure": 2.0, "var_1d": 0.02,
                                 "liquidity_days": None})
        self.assertEqual([b["metric"] for b in result["breaches"]], ["net_exposure", "gross_exposure"])
        self.assertIsNone(result["breaches"][0]["usage"])                   # past a bound of zero
        self.assertEqual(result["missing_metrics"], ["var_1d"])
        self.assertEqual(result["metrics_without_limits"], ["beta"])
        self.assertEqual(dr.limit_check({"x": 0.0}, {"x": 0})["checks"][0]["status"], "green")

    def test_amber_level_can_be_set(self) -> None:
        result = dr.limit_check({"gross_exposure": 1.2}, {"gross_exposure": 1.5}, amber_at=0.9)
        self.assertEqual(result["status"], "green")

    def test_bad_limits_are_refused(self) -> None:
        for limits in ({"x": -1}, {"x": (1.0, 0.5)}, {"x": (0, 1, 2)}, {"x": "big"}):
            with self.assertRaises(ModellingInputError, msg=limits):
                dr.limit_check({"x": 0.5}, limits)
        with self.assertRaises(ModellingInputError):
            dr.limit_check({"x": 0.5}, {"x": 1}, amber_at=0)


class DeskSnapshotTests(unittest.TestCase):
    def test_snapshot_combines_pnl_explain_and_limits(self) -> None:
        executions = [{"conid": "1", "side": "BUY", "quantity": 40, "price": 102}]
        result = dr.desk_snapshot(
            POSITIONS, QUOTES, reporting_currency="USD", nav=20000, fx_rates=FX, executions=executions,
            cash={"HKD": 50000}, exposures={"1": {"MKT": 1.2, "SMB": 0.3}, "2": {"MKT": 0.8}},
            factor_returns={"MKT": 0.004, "SMB": -0.002},
            metrics={"ex_ante_volatility": 0.15, "var_1d": 0.012},
            limits={"gross_exposure": 0.8, "largest_position_weight": 0.4, "ex_ante_volatility": 0.2,
                    "factor_exposures": {"*": 1.0}})
        exposures = result["exposures"]
        self.assertAlmostEqual(exposures["gross_exposure"], (100 * 101 + 1000 * 10 * 0.1282) / 20000)
        self.assertAlmostEqual(exposures["largest_position_weight"], 100 * 101 / 20000)
        self.assertAlmostEqual(exposures["factor_exposures"]["MKT"],
                               (1.2 * 100 * 101 + 0.8 * 1000 * 10 * 0.1282) / 20000)
        explain = result["explain"]
        rec = explain["reconciliation"]
        self.assertAlmostEqual(rec["difference"], 0.0, places=9)
        self.assertAlmostEqual(rec["trading_pnl"], 40 * (100 - 102))
        self.assertAlmostEqual(rec["cash_fx_pnl"], 50000 * 0.0002)
        self.assertAlmostEqual(sum(explain["amounts"].values()), rec["explained_position_pnl"], places=9)
        self.assertAlmostEqual(rec["explained_position_pnl"], 100 * 1 + 66.0, places=9)
        checks = {c["metric"]: c["status"] for c in result["limits"]["checks"]}
        self.assertEqual(checks["largest_position_weight"], "red")
        self.assertEqual(checks["ex_ante_volatility"], "green")
        self.assertIn("factor_exposures.SMB", checks)
        self.assertEqual(result["as_of"], "2026-10-16T14:00:00+00:00")
        self.assertEqual(result["freshness"]["delayed"], 1)
        self.assertAlmostEqual(result["pnl_return"], result["pnl"]["total"]["pnl"] / 20000)
        self.assertEqual(result["disclaimer"], DISCLAIMER)
        json.dumps(result, allow_nan=False)

    def test_stale_holding_still_counts_in_exposure(self) -> None:
        quotes = [dict(QUOTES[0]), dict(QUOTES[1], observed_at="2026-10-16T12:00:00Z")]
        result = dr.desk_snapshot(POSITIONS, quotes, reporting_currency="USD", nav=20000, fx_rates=FX)
        self.assertEqual(result["exposures"]["valued_at_stale_prices"], ["2"])
        self.assertAlmostEqual(result["exposures"]["gross_exposure"], (10100 + 1282) / 20000)
        self.assertAlmostEqual(result["pnl"]["total"]["pnl"], 100.0)
        self.assertIsNone(result["explain"])
        self.assertIsNone(result["limits"])

    def test_explain_needs_nav(self) -> None:
        result = dr.desk_snapshot(POSITIONS, QUOTES, reporting_currency="USD", fx_rates=FX,
                                  exposures={"1": {"MKT": 1.0}}, factor_returns={"MKT": 0.01})
        self.assertFalse(result["explain"]["available"])
        self.assertIsNone(result["exposures"])
        with self.assertRaises(ModellingInputError):
            dr.desk_snapshot(POSITIONS, QUOTES, reporting_currency="USD", nav=0)


if __name__ == "__main__":
    unittest.main()
