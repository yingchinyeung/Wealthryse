"""The gold quant sections (ibkr_etl.quant_snapshots) on synthetic inputs, without a database.

Every builder runs on made-up rows shaped as load_inputs() returns them: known answers where the
engines give one (equal weights, contributions that add up, a hand-worked order), the unavailable
paths, the fingerprint, the build setting and JSON-ready payloads. tests/test_quant_snapshots_db.py
covers the migration, the loaders, the job and the reader on a real PostgreSQL.
"""
from __future__ import annotations

import importlib.util
import json
import math
import random
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from ibkr_etl import quant_snapshots as qs

ROOT = Path(__file__).resolve().parents[1]
ACCOUNT = "U9100001"
AS_OF = "2026-10-16"


def business_days(end: str, count: int) -> list[str]:
    days, day = [], date.fromisoformat(end)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day -= timedelta(days=1)
    return sorted(days)


def synthetic_data(*, holdings: int = 8, days: int = 320, seed: int = 7, as_of: str = AS_OF,
                   subject_type: str = "physical_account", trade_every: int = 7) -> dict:
    """Rows for one made-up account: USD and HKD holdings driven by one market factor, a trade
    every few days, one deposit, the benchmark comparison and the HKD rate. Deterministic."""
    rng = random.Random(seed)
    calendar = business_days(as_of, days)
    conids = [str(9001 + i) for i in range(holdings)]
    currency = {conid: ("HKD" if i % 3 == 2 else "USD") for i, conid in enumerate(conids)}
    beta = {conid: 0.6 + 0.8 * i / max(1, holdings - 1) for i, conid in enumerate(conids)}
    price = {conid: (40.0 + 7 * i) * (7.8 if currency[conid] == "HKD" else 1.0) for i, conid in enumerate(conids)}
    quantity = {conid: 100.0 + 10 * i for i, conid in enumerate(conids)}
    hkd = 0.1282
    cash, level = 20000.0, 100.0
    data = {"subject_id": ACCOUNT, "as_of": as_of, "subject_type": subject_type, "reporting_currency": "USD",
            "benchmark_id": "synthetic-benchmark", "comparison": [], "positions": [], "trades": [], "nav": [],
            "flows": [], "fx": [], "risk_free": {"rate_date": as_of, "annual_rate": 0.04},
            "classifications": {conid: ("Technology", "Financials", "Energy")[i % 3]
                                for i, conid in enumerate(conids[:-1])}}
    previous_nav = None
    for t, day in enumerate(calendar):
        # Calm and stressed spells of 80 sessions, so the returns have regimes to find.
        market = rng.gauss(0.0003, 0.006 if (t // 80) % 2 == 0 else 0.02) if t else 0.0
        hkd *= 1.0 + (rng.gauss(0.0, 0.0005) if t else 0.0)
        data["fx"].append({"date": day, "from": "HKD", "to": "USD", "rate": hkd})
        for conid in conids:
            if t:
                price[conid] *= 1.0 + beta[conid] * market + rng.gauss(0.0, 0.012)
        level *= 1.0 + market
        flow = 0.0
        if t == days // 3:
            flow = 5000.0
            cash += flow
            data["flows"].append({"date": day, "amount": "5000", "currency": "USD"})
        if t and t % trade_every == 0:
            conid = conids[t % holdings]
            side = "BUY" if (t // trade_every) % 2 else "SELL"
            fill = price[conid] * (1.0005 if side == "BUY" else 0.9995)
            size = 10.0
            rate = hkd if currency[conid] == "HKD" else 1.0
            quantity[conid] += size if side == "BUY" else -size
            cash -= (size if side == "BUY" else -size) * fill * rate + 1.0
            data["trades"].append({"trade_id": f"T{t}", "execution_id": f"E{t}.1", "trade_date": day,
                                   "conid": conid, "symbol": f"SYN{conid[-1]}", "side": side,
                                   "quantity": str(size if side == "BUY" else -size), "price": f"{fill:.6f}",
                                   "commission": "-1.0", "commission_currency": "USD", "currency": currency[conid]})
        nav = cash
        for conid in conids:
            rate = hkd if currency[conid] == "HKD" else 1.0
            value = quantity[conid] * price[conid]
            nav += value * rate
            data["positions"].append({"date": day, "conid": conid, "symbol": f"SYN{conid[-1]}",
                                      "currency": currency[conid], "quantity": f"{quantity[conid]:.4f}",
                                      "mark": f"{price[conid]:.6f}", "market_value": f"{value:.4f}"})
        data["nav"].append({"date": day, "nav": f"{nav:.4f}"})
        data["comparison"].append({
            "date": day, "portfolio_value": nav, "benchmark_level": level,
            "portfolio_return": None if previous_nav is None else (nav - flow) / previous_nav - 1.0,
            "benchmark_return": None if t == 0 else market})
        previous_nav = nav
    return data


def _finite(value) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


class SyntheticBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = synthetic_data(days=560)
        cls.inputs = qs.prepare_inputs(cls.data)
        cls.payloads = {name: qs.BUILDERS[name](cls.inputs[name]) for name in qs.SECTION_NAMES}

    def test_every_section_has_the_payload_keys_and_is_available(self):
        for name, payload in self.payloads.items():
            with self.subTest(section=name):
                self.assertEqual(set(payload), {"section", "model_id", "model_version", "as_of", "available",
                                                "reason", "inputs", "results"})
                self.assertEqual(payload["section"], name)
                self.assertEqual(payload["model_id"], qs.SECTION_BY_NAME[name].model_id)
                self.assertEqual(payload["as_of"], AS_OF)
                self.assertTrue(payload["available"], payload["reason"])
                self.assertIsNone(payload["reason"])

    def test_results_are_the_engine_outputs_under_the_call_names(self):
        expected = {
            "skill": {"skill_summary"},
            "decisions": {"trade_value_added", "skill_profile", "timing_cost", "counterfactual_portfolios"},
            "currency": {"mtd", "ytd"},
            "tail": {"evt_var_es", "tail_backtest_report", "forecasts"},
            "regime": {"regime_model", "regime_risk"},
            "holdings_risk": {"ledoit_wolf_covariance", "ex_ante_risk", "statistical_factors"},
            "correlation": {"dcc_correlation"},
            "concentration": {"concentration", "concentration_by_sector", "correlation_clusters"},
            "execution": {"implementation_shortfall", "by_month", "skipped"},
        }
        for name, keys in expected.items():
            with self.subTest(section=name):
                results = self.payloads[name]["results"]
                self.assertEqual(set(results), keys)
                for key, value in results.items():
                    if isinstance(value, dict) and "available" in value:
                        self.assertTrue(value["available"], f"{key}: {value.get('reason')}")
        self.assertEqual(self.payloads["skill"]["results"]["skill_summary"]["model_version"],
                         "quant-skill-statistics-v1")

    def test_payloads_are_json_with_finite_numbers(self):
        for name, payload in self.payloads.items():
            with self.subTest(section=name):
                json.dumps(payload, allow_nan=False)
                self.assertTrue(_finite(payload))
                self.assertEqual(qs.quality_status(payload), "pass")

    def test_ex_ante_contributions_add_up_to_the_volatility(self):
        risk = self.payloads["holdings_risk"]["results"]["ex_ante_risk"]
        total = math.fsum(row["contribution"] for row in risk["holdings"])
        self.assertAlmostEqual(total, risk["volatility"], places=12)
        self.assertGreater(risk["volatility"], 0)
        self.assertEqual(len(self.payloads["holdings_risk"]["results"]["statistical_factors"]["factors"]), 3)

    def test_weights_are_of_nav_and_the_five_largest_are_correlated(self):
        summary = self.payloads["correlation"]["inputs"]
        names = summary["names"]
        self.assertEqual(len(summary["correlated"]), 5)
        weights = sorted((abs(item["weight"]) for item in names.values()), reverse=True)
        self.assertEqual(sorted((abs(names[n]["weight"]) for n in summary["correlated"]), reverse=True), weights[:5])
        self.assertLess(summary["invested_weight"], 1.0)  # the cash is not a holding
        dcc = self.payloads["correlation"]["results"]["dcc_correlation"]
        self.assertTrue(dcc["available"])

    def test_currency_effects_add_up_to_the_excess_return(self):
        for period in ("mtd", "ytd"):
            with self.subTest(period=period):
                result = self.payloads["currency"]["results"][period]
                self.assertAlmostEqual(result["totals"]["total"], result["excess_return"], places=12)
                self.assertIn("HKD", {row["currency"] for row in result["currencies"]})
        ytd = self.inputs["currency"]["periods"]["ytd"]
        self.assertEqual(ytd["start_date"], "2025-12-31")  # the last NAV date before 1 Jan

    def test_tail_forecasts_are_today_s_one_day_figures(self):
        tail = self.payloads["tail"]["results"]
        forecast = tail["forecasts"]
        self.assertEqual((forecast["as_of"], forecast["horizon_days"], forecast["basis"]), (AS_OF, 1, "conditional"))
        self.assertEqual(set(forecast["levels"]), {"99", "99.5"})
        evt = tail["evt_var_es"]["conditional"]["levels"]["99"]
        self.assertEqual(forecast["levels"]["99"]["var"], evt["var"])
        self.assertGreater(forecast["levels"]["99.5"]["var"], forecast["levels"]["99"]["var"])

    def test_decisions_cover_the_trade_year_and_the_counterfactual_starts_before_1_january(self):
        decisions = self.payloads["decisions"]
        added = decisions["results"]["trade_value_added"]
        window_start = (date.fromisoformat(AS_OF) - timedelta(days=qs.TRADE_WINDOW_DAYS)).isoformat()
        self.assertTrue(all(row["trade_date"] > window_start for row in added["trades"]))
        self.assertEqual(added["horizons"], [5, 21, 63])
        self.assertEqual(decisions["results"]["skill_profile"]["horizon"], 21)
        counterfactual = decisions["results"]["counterfactual_portfolios"]
        self.assertEqual(counterfactual["start_date"], "2025-12-31")
        self.assertAlmostEqual(counterfactual["total_flows"], 0.0)  # the deposit was before the year
        self.assertIn("benchmark", counterfactual["summary"])

    def test_execution_is_summarised_by_month(self):
        execution = self.payloads["execution"]["results"]
        orders = execution["implementation_shortfall"]
        self.assertEqual(sum(month["orders"] for month in execution["by_month"]), len(orders))
        total = math.fsum(o["implementation_shortfall"]["total"]["amount"] for o in orders)
        self.assertAlmostEqual(total, math.fsum(month["total"] for month in execution["by_month"]), places=6)


class KnownAnswerTests(unittest.TestCase):
    def _holdings(self, weights: dict, sectors: dict, days: int = 300) -> dict:
        rng = random.Random(3)
        calendar = business_days(AS_OF, days)
        prices = {}
        for name in weights:
            level, series = 100.0, {}
            for day in calendar:
                level *= 1.0 + rng.gauss(0.0, 0.01)
                series[day] = level
            prices[name] = series
        return {"as_of": AS_OF, "holdings_date": AS_OF, "nav": 1.0, "prices": prices,
                "holdings": [{"name": name, "conid": str(i + 1), "currency": "USD", "sector": sectors[name],
                              "weight": weight, "market_value": weight} for i, (name, weight) in
                             enumerate(weights.items())]}

    def test_equal_weights_give_one_over_n(self):
        inputs = self._holdings({"A": 0.25, "B": 0.25, "C": 0.25, "D": 0.25},
                                {"A": "Tech", "B": "Tech", "C": "Energy", "D": "Energy"})
        results = qs.build_concentration(inputs)["results"]
        self.assertAlmostEqual(results["concentration"]["herfindahl"], 0.25)
        self.assertAlmostEqual(results["concentration"]["effective_holdings"], 4.0)
        self.assertAlmostEqual(results["concentration_by_sector"]["herfindahl"], 0.5)
        self.assertEqual(results["concentration_by_sector"]["holdings"], 2)
        # Independent random walks: no pair is correlated above 0.7, so every holding is its own cluster.
        self.assertTrue(results["correlation_clusters"]["available"])

    def test_one_order_against_its_previous_close(self):
        inputs = {"as_of": AS_OF, "currency": "USD", "no_trades": None,
                  "closes": {"1": {"2026-10-14": 10.0, "2026-10-15": 10.2}},
                  "trades": [{"trade_id": "T1", "trade_date": "2026-10-15", "conid": "1", "symbol": "A",
                              "side": "BUY", "quantity": 60.0, "price": 10.1, "commission": 0.5},
                             {"trade_id": "T2", "trade_date": "2026-10-15", "conid": "1", "symbol": "A",
                              "side": "BUY", "quantity": 40.0, "price": 10.1, "commission": 0.5}]}
        payload = qs.build_execution(inputs)
        order = payload["results"]["implementation_shortfall"][0]
        result = order["implementation_shortfall"]
        self.assertEqual((order["fills"], order["decision_date"]), (2, "2026-10-14"))
        self.assertAlmostEqual(result["decision_price"], 10.0)
        self.assertAlmostEqual(result["final_price"], 10.2)
        # 100 shares filled 0.10 above the previous close, plus 1.00 of commission.
        self.assertAlmostEqual(result["components"]["trading"]["amount"], 10.0)
        self.assertAlmostEqual(result["total"]["amount"], 11.0)
        self.assertAlmostEqual(payload["results"]["by_month"][0]["total_bps"], 11.0 / 1000.0 * 10_000)

    def test_a_buy_that_rises_adds_its_move_less_commission(self):
        calendar = business_days(AS_OF, 80)
        closes = {"1": {day: 10.0 + 0.1 * i for i, day in enumerate(calendar)}}
        trade_day = calendar[10]
        inputs = {"as_of": AS_OF, "currency": "USD", "no_trades": None, "closes": closes,
                  "sizes": {day: {"1": 1000.0} for day in calendar},
                  "trades": [{"trade_id": "T1", "trade_date": trade_day, "conid": "1", "symbol": "A", "side": "BUY",
                              "quantity": 100.0, "price": 11.0, "commission": 2.0}],
                  "counterfactual": {"unavailable": "not needed here"}}
        results = qs.build_decisions(inputs)["results"]
        five = results["trade_value_added"]["trades"][0]["horizons"][0]
        self.assertAlmostEqual(five["end_price"], 10.0 + 0.1 * 15)
        self.assertAlmostEqual(five["value_added"], (11.5 - 11.0) * 100 - 2.0)
        self.assertAlmostEqual(results["timing_cost"]["trades"][0]["decision_price"], 10.0 + 0.1 * 9)
        self.assertEqual(results["counterfactual_portfolios"], {"available": False, "reason": "not needed here"})


class UnavailableTests(unittest.TestCase):
    def test_own_pipelines_are_unavailable_in_every_section(self):
        data = {"subject_id": "alpha-world-ms", "as_of": AS_OF, "unavailable": "Alpha World MS and T report "
                "through their own HKD pipeline."}
        inputs = qs.prepare_inputs(data)
        for name in qs.SECTION_NAMES:
            with self.subTest(section=name):
                payload = qs.BUILDERS[name](inputs[name])
                self.assertFalse(payload["available"])
                self.assertIn("own HKD pipeline", payload["reason"])
                self.assertEqual(payload["results"], {})
                self.assertEqual(qs.quality_status(payload), "warn")

    def test_without_a_comparison_the_return_sections_say_why(self):
        data = synthetic_data(days=120)
        data["comparison"] = []
        inputs = qs.prepare_inputs(data)
        for name in ("skill", "tail", "regime"):
            payload = qs.BUILDERS[name](inputs[name])
            self.assertFalse(payload["available"])
            self.assertIn("No primary benchmark comparison", payload["reason"])

    def test_a_short_history_keeps_what_it_can(self):
        tail = qs.build_tail(qs.prepare_inputs(synthetic_data(days=320))["tail"])
        self.assertTrue(tail["available"])  # the tail fit works on 319 returns
        self.assertTrue(tail["results"]["evt_var_es"]["available"])
        self.assertFalse(tail["results"]["tail_backtest_report"]["available"])
        self.assertIn("500", tail["results"]["tail_backtest_report"]["reason"])
        self.assertEqual(qs.quality_status(tail), "warn")
        inputs = qs.prepare_inputs(synthetic_data(days=120))
        short = qs.build_tail(inputs["tail"])
        self.assertFalse(short["available"])
        self.assertIn("at least 30 losses", short["reason"])
        regime = qs.build_regime(inputs["regime"])
        self.assertFalse(regime["available"])
        self.assertIn("126", regime["reason"])
        correlation = qs.build_correlation(inputs["correlation"])
        self.assertFalse(correlation["available"])  # DCC needs 250 days

    def test_a_derived_subject_has_no_trades(self):
        data = synthetic_data(days=300, subject_type="filtered_portfolio")
        inputs = qs.prepare_inputs(data)
        decisions = qs.build_decisions(inputs["decisions"])
        self.assertTrue(decisions["available"])  # the counterfactual still works
        self.assertIn("physical accounts only", decisions["results"]["trade_value_added"]["reason"])
        execution = qs.build_execution(inputs["execution"])
        self.assertFalse(execution["available"])
        self.assertIn("physical accounts only", execution["reason"])

    def test_no_holdings(self):
        data = synthetic_data(days=60)
        data["positions"] = []
        inputs = qs.prepare_inputs(data)
        for name in ("holdings_risk", "correlation", "concentration"):
            payload = qs.BUILDERS[name](inputs[name])
            self.assertFalse(payload["available"])
            self.assertIn("no holdings", payload["reason"])

    def test_non_finite_numbers_are_stored_as_null_and_warn(self):
        payload = {"available": True, "results": {"x": {"value": float("nan"), "list": [1.0, float("inf")]}}}
        cleaned, replaced = qs.finite_copy(payload)
        self.assertEqual(replaced, 2)
        self.assertEqual(cleaned["results"]["x"], {"value": None, "list": [1.0, None]})
        self.assertEqual(qs.quality_status(cleaned, replaced), "warn")


class FingerprintTests(unittest.TestCase):
    def test_same_inputs_same_fingerprint_and_any_change_shows(self):
        first = qs.prepare_inputs(synthetic_data(days=90))
        again = qs.prepare_inputs(synthetic_data(days=90))
        for name in qs.SECTION_NAMES:
            with self.subTest(section=name):
                print_one = qs.section_fingerprint(name, first[name])
                self.assertRegex(print_one, "^[0-9a-f]{64}$")
                self.assertEqual(print_one, qs.section_fingerprint(name, again[name]))
                reordered = json.loads(json.dumps(first[name]), object_pairs_hook=lambda pairs: dict(reversed(pairs)))
                self.assertEqual(print_one, qs.section_fingerprint(name, reordered))
        changed = synthetic_data(days=90)
        changed["comparison"][-1]["portfolio_return"] += 0.001
        moved = qs.prepare_inputs(changed)
        self.assertNotEqual(qs.section_fingerprint("skill", first["skill"]),
                            qs.section_fingerprint("skill", moved["skill"]))
        self.assertEqual(qs.section_fingerprint("concentration", first["concentration"]),
                         qs.section_fingerprint("concentration", moved["concentration"]))
        self.assertNotEqual(qs.section_fingerprint("skill", first["skill"]),
                            qs.section_fingerprint("tail", first["skill"]))

    def test_the_versions_are_part_of_it(self):
        inputs = qs.prepare_inputs(synthetic_data(days=60))["tail"]
        before = qs.section_fingerprint("tail", inputs)
        with patch.object(qs, "QUANT_ANALYTICS_VERSION", "quant-analytics-v2"):
            self.assertNotEqual(before, qs.section_fingerprint("tail", inputs))
        with patch.object(qs.model_cards, "version", return_value="tail-risk-v9"):
            self.assertNotEqual(before, qs.section_fingerprint("tail", inputs))

    def test_non_finite_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            qs.section_fingerprint("tail", {"as_of": AS_OF, "returns": [[AS_OF, float("nan")]]})


class SettingTests(unittest.TestCase):
    def test_only_on_switches_the_build_on(self):
        for value, expected in (("on", True), (" ON ", True), ("off", False), ("", False), ("true", False),
                                ("of", False)):
            with self.subTest(value=value):
                self.assertEqual(qs.build_enabled({qs.BUILD_SETTING: value}), expected)
        self.assertFalse(qs.build_enabled({}))

    def test_the_job_does_nothing_when_off(self):
        spec = importlib.util.spec_from_file_location("build_quant_analytics",
                                                      ROOT / "scripts" / "build_quant_analytics.py")
        job = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(job)
        with patch.dict("os.environ", {qs.BUILD_SETTING: "off"}), \
                patch.object(job, "connect", side_effect=AssertionError("must not connect")), \
                patch("builtins.print") as printed:
            self.assertEqual(job.main([]), 0)
        self.assertIn('"status": "off"', printed.call_args.args[0])

    def test_sections_are_named(self):
        self.assertEqual(len(qs.SECTIONS), 9)
        self.assertEqual(qs.section_names(["tail", "skill", "tail"]), ("skill", "tail"))
        with self.assertRaises(ValueError):
            qs.section_names(["liquidity"])


class PerformanceTests(unittest.TestCase):
    def test_sixty_holdings_and_three_years_build_well_under_a_minute(self):
        data = synthetic_data(holdings=60, days=756, trade_every=2)
        started = time.perf_counter()
        inputs = qs.prepare_inputs(data)
        timings = {"prepare": time.perf_counter() - started}
        for name in qs.SECTION_NAMES:
            section_started = time.perf_counter()
            payload = qs.BUILDERS[name](inputs[name])
            timings[name] = time.perf_counter() - section_started
            self.assertTrue(payload["available"], f"{name}: {payload['reason']}")
        total = time.perf_counter() - started
        print("\nquant build, 60 holdings and 3 years: " + ", ".join(f"{k} {v:.2f}s" for k, v in timings.items())
              + f"; total {total:.2f}s")
        self.assertLess(total, 60.0)


if __name__ == "__main__":
    unittest.main()
