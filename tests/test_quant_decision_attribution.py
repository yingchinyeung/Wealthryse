from __future__ import annotations

import json
import math
import random
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.modelling import ModellingInputError  # noqa: E402
from ibkr_etl.quant import decision_attribution as da  # noqa: E402


def _weekdays(count: int, start: date = date(2024, 1, 1)) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


DAYS = _weekdays(10)  # 2024-01-01 (Mon) to 2024-01-12 (Fri)
CLOSES = {
    "1": dict(zip(DAYS, [100.0, 102.0, 104.0, 103.0, 101.0, 99.0, 98.0, 100.0, 105.0, 110.0])),
    "2": dict(zip(DAYS, [50.0, 49.0, 48.0, 47.0, 46.0, 45.0, 44.0, 43.0, 42.0, 41.0])),
}


class TradeValueAddedTests(unittest.TestCase):
    def test_known_answers_for_a_buy_and_a_sell(self) -> None:
        trades = [
            {"trade_date": DAYS[0], "conid": 1, "symbol": "AAA", "buy_sell": "BUY", "quantity": 10,
             "trade_price": 100.5, "commission": -1.0},
            {"trade_date": DAYS[2], "conid": "1", "symbol": "AAA", "side": "SELL", "quantity": -4,
             "price": 104.5, "commission": -0.5},
        ]
        result = da.trade_value_added(trades, CLOSES, horizons=(1, 3, 20))
        buy, sell = result["trades"]
        # Buy: close one day later is 102, three days later 103.
        self.assertAlmostEqual(buy["horizons"][0]["value_added"], (102.0 - 100.5) * 10 - 1.0)
        self.assertAlmostEqual(buy["horizons"][1]["value_added"], (103.0 - 100.5) * 10 - 1.0)
        self.assertAlmostEqual(buy["horizons"][1]["decision_return"], 2.5 / 100.5)
        self.assertFalse(buy["horizons"][2]["available"])
        # Sell of 4 (IBKR's signed quantity): avoided the fall to 103 then to 99.
        self.assertEqual(sell["quantity"], 4.0)
        self.assertAlmostEqual(sell["horizons"][0]["value_added"], (104.5 - 103.0) * 4 - 0.5)
        self.assertAlmostEqual(sell["horizons"][1]["value_added"], (104.5 - 99.0) * 4 - 0.5)
        self.assertAlmostEqual(sell["horizons"][1]["price_return"], 99.0 / 104.5 - 1.0)
        one_day = result["totals"][0]
        self.assertAlmostEqual(one_day["value_added"], 14.0 + 5.5)
        self.assertAlmostEqual(one_day["commission"], 1.5)
        self.assertAlmostEqual(one_day["gross_value_added"] - one_day["commission"], one_day["value_added"])
        self.assertEqual(result["totals"][2]["trades"], 0)
        self.assertEqual(result["totals"][2]["unavailable"], 2)
        self.assertEqual(result["model_version"], "decision-attribution-v1")
        json.dumps(result)

    def test_totals_by_holding_add_up_to_the_horizon_totals(self) -> None:
        generator = random.Random(4)
        trades = [{"trade_date": DAYS[generator.randrange(8)], "conid": generator.choice(["1", "2"]),
                   "side": generator.choice(["BUY", "SELL"]), "quantity": generator.randint(1, 50),
                   "price": 60.0 + generator.random() * 40, "commission": -generator.random()} for _ in range(30)]
        result = da.trade_value_added(trades, CLOSES, horizons=(1, 2))
        for index, total in enumerate(result["totals"]):
            by_holding = sum(h["horizons"][index]["value_added"] for h in result["by_holding"])
            self.assertAlmostEqual(by_holding, total["value_added"])
            self.assertAlmostEqual(total["buys"]["value_added"] + total["sells"]["value_added"], total["value_added"])

    def test_trade_on_a_day_without_a_close_counts_from_the_next_close(self) -> None:
        trades = [{"trade_date": "2024-01-06", "conid": "1", "side": "BUY", "quantity": 1, "price": 100.0}]
        result = da.trade_value_added(trades, CLOSES, horizons=(1,))
        outcome = result["trades"][0]["horizons"][0]
        self.assertEqual(outcome["end_date"], "2024-01-08")  # the first close after a Saturday
        self.assertAlmostEqual(outcome["value_added"], 99.0 - 100.0)

    def test_bad_trades_are_skipped_and_listed(self) -> None:
        trades = [
            {"trade_date": DAYS[0], "conid": "1", "side": "BUY", "quantity": 1, "price": None},
            {"trade_date": DAYS[0], "conid": "1", "side": "BUY", "quantity": float("nan"), "price": 100},
            {"trade_date": DAYS[0], "conid": "1", "side": "HOLD", "quantity": 1, "price": 100},
            {"trade_date": "yesterday", "conid": "1", "side": "BUY", "quantity": 1, "price": 100},
            {"trade_date": DAYS[0], "conid": "9", "side": "BUY", "quantity": 1, "price": 100},
        ]
        result = da.trade_value_added(trades, CLOSES)
        self.assertEqual([s["index"] for s in result["skipped"]], [0, 1, 2, 3])
        self.assertEqual(result["trades"][0]["horizons"][0]["reason"], "no closing prices for this holding")
        with self.assertRaises(ModellingInputError):
            da.trade_value_added(trades[:4], CLOSES)
        with self.assertRaises(ModellingInputError):
            da.trade_value_added(trades[4:], CLOSES, horizons=(0, 5))

    def test_results_at_horizon_flattens_available_trades(self) -> None:
        trades = [{"trade_date": DAYS[0], "conid": "1", "side": "BUY", "quantity": 2, "price": 100.0},
                  {"trade_date": DAYS[8], "conid": "1", "side": "SELL", "quantity": 2, "price": 106.0}]
        result = da.trade_value_added(trades, CLOSES, horizons=(1, 5))
        rows = da.results_at_horizon(result, 5)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0]["value_added"], (99.0 - 100.0) * 2)
        self.assertEqual(len(da.results_at_horizon(result, 1)), 2)
        with self.assertRaises(ModellingInputError):
            da.results_at_horizon(result, 21)


class TimingCostTests(unittest.TestCase):
    def test_previous_close_is_the_decision_price(self) -> None:
        trades = [
            {"trade_date": DAYS[1], "conid": "1", "side": "BUY", "quantity": 10, "price": 101.0, "commission": -2},
            {"trade_date": DAYS[1], "conid": "2", "side": "SELL", "quantity": 20, "price": 49.5},
            {"trade_date": DAYS[3], "conid": "1", "side": "BUY", "quantity": 5, "price": 103.0,
             "decision_price": 104.0},
            {"trade_date": DAYS[0], "conid": "1", "side": "BUY", "quantity": 5, "price": 100.0},
        ]
        result = da.timing_cost(trades, CLOSES)
        buy, sell, own = result["trades"]
        self.assertEqual(buy["decision_date"], DAYS[0])
        self.assertAlmostEqual(buy["timing_cost"], 10.0)            # paid 101 against 100
        self.assertAlmostEqual(buy["timing_cost_bps"], 100.0)
        self.assertAlmostEqual(buy["total_cost_bps"], 120.0)
        self.assertAlmostEqual(sell["timing_cost"], 10.0)           # received 49.5 against 50
        self.assertAlmostEqual(sell["timing_cost_bps"], 100.0)
        self.assertEqual(own["decision_source"], "trade")
        self.assertAlmostEqual(own["timing_cost"], -5.0)            # a better fill is a negative cost
        self.assertEqual(result["skipped"], [{"index": 3, "reason": "no price before the trade date"}])
        total = result["totals"]["all"]
        self.assertAlmostEqual(total["timing_cost"], 15.0)
        self.assertAlmostEqual(total["decision_value"], 1000.0 + 1000.0 + 520.0)
        self.assertAlmostEqual(total["timing_cost_bps"], 15.0 / 2520.0 * 1e4)
        self.assertAlmostEqual(total["total_cost"], 17.0)
        self.assertEqual(result["totals"]["sells"]["trades"], 1)

    def test_no_decision_price_at_all_is_refused(self) -> None:
        with self.assertRaises(ModellingInputError):
            da.timing_cost([{"trade_date": DAYS[0], "conid": "1", "side": "BUY", "quantity": 1, "price": 1.0}], {})


class SkillProfileTests(unittest.TestCase):
    def test_hit_rate_and_payoff_known_answer(self) -> None:
        rows = [{"side": "BUY", "value_added": 10.0}, {"side": "BUY", "value_added": -5.0},
                {"side": "SELL", "value_added": 20.0}, {"side": "SELL", "value_added": -10.0},
                {"side": "SELL", "value_added": float("nan")}]
        result = da.skill_profile(rows, [])
        self.assertEqual(result["trades"], 4)
        self.assertAlmostEqual(result["hit_rate"], 0.5)
        self.assertAlmostEqual(result["average_win"], 15.0)
        self.assertAlmostEqual(result["average_loss"], -7.5)
        self.assertAlmostEqual(result["payoff_ratio"], 2.0)
        self.assertAlmostEqual(result["by_side"]["sells"]["payoff_ratio"], 2.0)
        self.assertIsNone(result["sizing_skill"]["spearman"])
        with self.assertRaises(ModellingInputError):
            da.skill_profile([{"value_added": None}], [])

    def test_ranks_share_ties_and_spearman_is_rank_based(self) -> None:
        self.assertEqual(da._ranks([3.0, 1.0, 2.0, 2.0]), [4.0, 1.0, 2.5, 2.5])
        self.assertAlmostEqual(da.spearman([1, 2, 3, 4], [1, 8, 27, 64]), 1.0)
        self.assertAlmostEqual(da.spearman([1, 2, 3, 4], [4, 3, 2, 1]), -1.0)
        self.assertIsNone(da.spearman([1, 1, 1], [1, 2, 3]))
        # Hand-computed: ranks (1,2,3,4,5) against (2,1,4,3,5): 1 - 6*4/(5*24) = 0.8.
        self.assertAlmostEqual(da.spearman([10, 20, 30, 40, 50], [0.2, 0.1, 0.4, 0.3, 0.5]), 0.8)

    def test_sizing_skill_and_sell_discipline(self) -> None:
        panel = []
        for day in ("2024-01-02", "2024-02-01"):
            for conid, size, ret in (("A", 1.0, -0.02), ("B", 2.0, 0.01), ("C", 3.0, 0.03), ("D", 4.0, 0.05)):
                panel.append({"date": day, "conid": conid, "size": size, "return": ret})
        sells = [{"side": "SELL", "conid": "A", "trade_date": "2024-01-02", "price_return": -0.1, "value_added": 3.0},
                 {"side": "SELL", "conid": "B", "trade_date": "2024-03-01", "price_return": 0.0, "value_added": -1.0}]
        result = da.skill_profile(sells, panel)
        self.assertAlmostEqual(result["sizing_skill"]["spearman"], 1.0)
        self.assertEqual(result["sizing_skill"]["dates"], 2)
        discipline = result["sell_discipline"]
        kept = (2.0 * 0.01 + 3.0 * 0.03 + 4.0 * 0.05) / 9.0
        self.assertEqual(discipline["sells"], 1)
        self.assertEqual(discipline["unassessed"], 1)          # no holdings dated on 1 March
        self.assertAlmostEqual(discipline["average_kept_return"], kept)
        self.assertAlmostEqual(discipline["average_advantage"], kept + 0.1)
        self.assertEqual(discipline["hit_rate"], 1.0)

    def test_horizons_must_match(self) -> None:
        with self.assertRaises(ModellingInputError):
            da.skill_profile([{"value_added": 1.0, "horizon": 21}],
                             [{"date": "2024-01-02", "conid": "A", "size": 1.0, "return": 0.0, "horizon": 5}])

    def test_end_to_end_from_trades(self) -> None:
        trades = [{"trade_date": DAYS[1], "conid": "1", "side": "BUY", "quantity": 10, "price": 102.0},
                  {"trade_date": DAYS[1], "conid": "2", "side": "SELL", "quantity": 10, "price": 49.0}]
        replay = da.trade_value_added(trades, CLOSES, horizons=(3,))
        positions = {DAYS[1]: {"1": 1000.0, "2": 500.0}}
        panel = da.holding_forward_returns(positions, CLOSES, horizon=3)
        self.assertAlmostEqual(panel[0]["return"], 101.0 / 102.0 - 1.0)
        result = da.skill_profile(da.results_at_horizon(replay, 3), panel)
        self.assertEqual(result["horizon"], 3)
        self.assertAlmostEqual(result["hit_rate"], 0.5)          # the buy lost, the sale avoided a fall
        self.assertAlmostEqual(result["sell_discipline"]["average_sold_return"], 46.0 / 49.0 - 1.0)
        self.assertAlmostEqual(result["sell_discipline"]["average_kept_return"], 101.0 / 102.0 - 1.0)
        json.dumps(result)


class CounterfactualTests(unittest.TestCase):
    CALENDAR = ["2024-03-28", "2024-03-29", "2024-04-01", "2024-04-02"]

    def test_buy_and_hold_and_quarterly_rebalance_known_answer(self) -> None:
        closes = {"A": dict(zip(self.CALENDAR, [100.0, 200.0, 200.0, 400.0]))}
        nav = dict(zip(self.CALENDAR, [1000.0, 1400.0, 1400.0, 2000.0]))
        result = da.counterfactual_portfolios(self.CALENDAR[0], {"A": 5}, closes, nav)
        self.assertAlmostEqual(result["start_weights"]["A"], 0.5)
        self.assertAlmostEqual(result["start_weights"]["cash"], 0.5)
        hold = [row["buy_and_hold"] for row in result["series"]]
        self.assertEqual([round(v, 9) for v in hold], [1000.0, 1500.0, 1500.0, 2500.0])
        # 29 March ends the quarter: 1500 reset to half in A (3.75 shares) and half cash.
        rebalanced = [row["quarterly_rebalance"] for row in result["series"]]
        self.assertEqual([round(v, 9) for v in rebalanced], [1000.0, 1500.0, 1500.0, 2250.0])
        self.assertEqual(result["rebalances"], 1)
        self.assertAlmostEqual(result["summary"]["buy_and_hold"]["actual_minus_counterfactual"], -500.0)
        self.assertAlmostEqual(result["summary"]["quarterly_rebalance"]["actual_minus_counterfactual"], -250.0)
        json.dumps(result)

    def test_flows_are_added_pro_rata_and_match_the_actual_holdings(self) -> None:
        days = _weekdays(60)
        generator = random.Random(9)
        prices = {"A": [100.0], "B": [20.0]}
        for _ in days[1:]:
            for key in prices:
                prices[key].append(prices[key][-1] * (1 + generator.gauss(0, 0.01)))
        closes = {key: dict(zip(days, values)) for key, values in prices.items()}
        # The actual investor also adds each flow pro rata, so the paths must agree exactly.
        units, cash, nav, flows = {"A": 30.0, "B": 100.0}, 1000.0, {}, []
        for k, day in enumerate(days):
            value = cash + sum(units[c] * prices[c][k] for c in units)
            flow = 2500.0 if k == 20 else (-1200.0 if k == 40 else 0.0)
            if flow:
                flows.append((day, flow))
                scale = (value + flow) / value
                units, cash = {c: u * scale for c, u in units.items()}, cash * scale
            nav[day] = value + flow
        result = da.counterfactual_portfolios(days[0], {"A": 30, "B": 100}, closes, nav, flows=flows)
        for row in result["series"]:
            self.assertAlmostEqual(row["actual_minus_buy_and_hold"], 0.0, places=6)
        self.assertAlmostEqual(result["total_flows"], 1300.0)
        self.assertEqual(result["ignored_flows"], [])

    def test_flat_prices_grow_only_by_flows_and_benchmark_buys_units(self) -> None:
        days = _weekdays(5)
        closes = {"A": {day: 10.0 for day in days}}
        nav = {day: 1000.0 for day in days}
        benchmark = dict(zip(days, [100.0, 110.0, 110.0, 121.0, 121.0]))
        flows = [{"date": days[2], "amount": 220.0}, {"date": "2023-12-01", "amount": 5.0}]
        result = da.counterfactual_portfolios(days[0], {"A": 50}, closes, nav, flows=flows, benchmark=benchmark)
        self.assertAlmostEqual(result["series"][-1]["buy_and_hold"], 1220.0)
        self.assertAlmostEqual(result["series"][-1]["quarterly_rebalance"], 1220.0)
        # 10 units at 100, 2 more at 110, then the index rises 10%: 12 × 121.
        self.assertAlmostEqual(result["series"][-1]["benchmark"], 12 * 121.0)
        self.assertEqual(len(result["ignored_flows"]), 1)

    def test_unpriced_holding_counts_as_cash_and_bad_starts_are_refused(self) -> None:
        days = _weekdays(3)
        closes = {"A": dict(zip(days, [10.0, 11.0, 12.0]))}
        nav = dict(zip(days, [1000.0, 1050.0, 1100.0]))
        result = da.counterfactual_portfolios(days[0], {"A": 50, "Z": 10, "Y": -3}, closes, nav)
        self.assertEqual({row["conid"] for row in result["left_out_holdings"]}, {"Z", "Y"})
        self.assertAlmostEqual(result["start_weights"]["cash"], 0.5)
        with self.assertRaises(ModellingInputError):
            da.counterfactual_portfolios("2023-06-01", {"A": 50}, closes, nav)
        with self.assertRaises(ModellingInputError):
            da.counterfactual_portfolios(days[-1], {"A": 50}, closes, nav)
        with self.assertRaises(ModellingInputError):
            da.counterfactual_portfolios(days[0], {"A": 50}, closes, nav, benchmark={days[1]: 100.0})

    def test_ten_years_of_sixty_holdings(self) -> None:
        days = _weekdays(2520, date(2015, 1, 1))
        generator = random.Random(2)
        closes = {}
        for conid in range(60):
            level, series = 100.0, {}
            for day in days:
                level *= 1 + generator.gauss(0.0003, 0.015)
                series[day] = level
            closes[str(conid)] = series
        nav = {day: 1_000_000.0 for day in days}
        result = da.counterfactual_portfolios(days[0], {str(c): 100 for c in range(60)}, closes, nav)
        self.assertEqual(len(result["series"]), 2520)
        self.assertGreater(result["rebalances"], 35)
        self.assertTrue(all(math.isfinite(row["quarterly_rebalance"]) for row in result["series"]))


if __name__ == "__main__":
    unittest.main()
