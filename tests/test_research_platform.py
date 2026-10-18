from __future__ import annotations

import math
import sys
import unittest
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ibkr_etl.research import backtester, experiment_log, point_in_time, validation  # noqa: E402
from ibkr_etl.research.point_in_time import LookaheadError, PointInTimeView, Version  # noqa: E402

UTC = timezone.utc


def _weekdays(count: int, start: date = date(2024, 1, 1)) -> list[str]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


class PointInTimeTests(unittest.TestCase):
    def test_as_known_keeps_the_latest_version_known_at_the_cutoff(self) -> None:
        original = Version("nav", "2026-10-15", datetime(2026, 10, 16, 8, 20, tzinfo=UTC), 100.0)
        corrected = Version("nav", "2026-10-15", datetime(2026, 10, 17, 8, 20, tzinfo=UTC), 101.0)
        before = point_in_time.as_known([original, corrected], datetime(2026, 10, 16, 12, 0, tzinfo=UTC))
        after = point_in_time.as_known([original, corrected], datetime(2026, 10, 17, 12, 0, tzinfo=UTC))
        self.assertEqual(before["nav"].value, 100.0)
        self.assertEqual(after["nav"].value, 101.0)

    def test_a_withdrawn_record_disappears_and_ties_keep_the_later_row(self) -> None:
        moment = datetime(2026, 10, 16, 8, 20, tzinfo=UTC)
        rows = [Version("t1", "2026-10-15", moment, "first"), Version("t1", "2026-10-15", moment, "resent"),
                Version("t2", "2026-10-15", moment, 1), Version("t2", "2026-10-15", moment + timedelta(days=1), None, True)]
        known = point_in_time.as_known(rows, moment + timedelta(days=2))
        self.assertEqual(known["t1"].value, "resent")
        self.assertNotIn("t2", known)

    def test_naive_times_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            point_in_time.as_known([Version("k", "2026-10-15", datetime(2026, 10, 16), 1)], datetime.now(UTC))

    def test_knowledge_time_counts_business_days_when_asked(self) -> None:
        friday = "2026-10-16"
        self.assertEqual(point_in_time.knowledge_time(friday), datetime(2026, 10, 17, 8, 20, tzinfo=UTC))
        self.assertEqual(point_in_time.knowledge_time(friday, weekends=False),
                         datetime(2026, 10, 19, 8, 20, tzinfo=UTC))

    def test_view_refuses_to_look_ahead(self) -> None:
        series = {"px": {"2026-10-14": 1.0, "2026-10-15": 2.0, "2026-10-16": 3.0},
                  "eps": {"2026-10-14": 5.0, "2026-10-15": 6.0}}
        view = PointInTimeView(series, timing={"eps": {"lag_days": 2, "cutoff": time(0, 0)}})
        at = view.at(datetime(2026, 10, 15, 23, 59, tzinfo=UTC))
        self.assertEqual(at.latest("px"), ("2026-10-15", 2.0))
        self.assertEqual(at.history("px"), [("2026-10-14", 1.0), ("2026-10-15", 2.0)])
        self.assertIsNone(at.latest("eps"))  # 14 Oct's figure is known only on 16 Oct
        with self.assertRaises(LookaheadError):
            at.value("px", "2026-10-16")

    def test_replay_order_turns_bronze_rows_into_versions(self) -> None:
        rows = [{"conid": 1, "day": "2026-10-15", "qty": 10, "ingested": datetime(2026, 10, 16, 9, tzinfo=UTC)},
                {"conid": 1, "day": "2026-10-15", "qty": 12, "ingested": datetime(2026, 10, 17, 9, tzinfo=UTC)}]
        versions = point_in_time.replay_order(list(reversed(rows)), key_fields=("conid", "day"), date_field="day",
                                              ingested_field="ingested", value_fields=("qty",))
        known = point_in_time.as_known(versions, datetime(2026, 10, 18, tzinfo=UTC))
        self.assertEqual(known[(1, "2026-10-15")].value, {"qty": 12})


class BacktesterTests(unittest.TestCase):
    def _prices(self) -> dict[str, dict[str, float]]:
        days = _weekdays(300)
        return {"A": {d: 100.0 * 1.001 ** i for i, d in enumerate(days)},
                "B": {d: 50.0 * (1.0 + 0.02 * math.sin(i / 5.0)) for i, d in enumerate(days)}}

    def test_buy_and_hold_without_costs_matches_the_price_path(self) -> None:
        prices = self._prices()
        result = backtester.run_backtest(prices, lambda view, day: {"A": 1.0}, rebalance="monthly",
                                         commission_bps=0, spread_bps=0)
        days = sorted(prices["A"])
        # Bought at the close of day 1 (one-session lag), then drifts with A.
        expected = prices["A"][days[-1]] / prices["A"][days[1]]
        self.assertAlmostEqual(result["final_nav"], expected, places=9)
        self.assertEqual(result["observations"], len(days) - 1)

    def test_costs_are_charged_and_reported(self) -> None:
        prices = self._prices()
        free = backtester.run_backtest(prices, lambda v, d: {"A": 0.5, "B": 0.5}, rebalance="weekly",
                                       commission_bps=0, spread_bps=0)
        paid = backtester.run_backtest(prices, lambda v, d: {"A": 0.5, "B": 0.5}, rebalance="weekly",
                                       commission_bps=5, spread_bps=10,
                                       impact=lambda asset, value, day: 0.0005)
        self.assertLess(paid["final_nav"], free["final_nav"])
        self.assertGreater(paid["total_cost"], 0)
        self.assertGreater(paid["cost_drag_per_year"], 0)
        self.assertEqual(free["total_cost"], 0)

    def test_the_signal_never_sees_the_day_it_trades_on(self) -> None:
        prices = self._prices()
        seen = []

        def signal(view, day):
            latest = view.latest("A")
            seen.append((day, latest[0]))
            with self.assertRaises(LookaheadError):
                later = [d for d in sorted(prices["A"]) if d > day]
                if later:
                    view.value("A", later[0])
                else:
                    raise LookaheadError("end")
            return {"A": 1.0}

        backtester.run_backtest(prices, signal, rebalance="monthly")
        self.assertTrue(seen)
        self.assertTrue(all(day == known for day, known in seen))

    def test_bad_settings_are_refused(self) -> None:
        prices = self._prices()
        with self.assertRaises(backtester.BacktestError):
            backtester.run_backtest(prices, lambda v, d: {"A": 1.0}, execution_lag=0)
        with self.assertRaises(backtester.BacktestError):
            backtester.run_backtest(prices, lambda v, d: {"A": -0.5})
        with self.assertRaises(backtester.BacktestError):
            backtester.run_backtest(prices, lambda v, d: {"A": 0.8, "B": 0.8})


class ValidationTests(unittest.TestCase):
    def test_walk_forward_never_tests_on_training_data(self) -> None:
        splits = validation.walk_forward_splits(100, train=40, test=10, gap=2)
        self.assertEqual(len(splits), 5)
        for train, test in splits:
            self.assertLess(max(train) + 2, min(test))
        expanding = validation.walk_forward_splits(100, train=40, test=10, expanding=True)
        self.assertTrue(all(train[0] == 0 for train, _ in expanding))

    def test_purged_kfold_removes_overlapping_labels_and_embargoes(self) -> None:
        spans = [(i, min(i + 4, 99)) for i in range(100)]
        splits = validation.purged_kfold(spans, folds=5, embargo=0.03)
        self.assertEqual(len(splits), 5)
        for train, test in splits:
            test_first, test_last = spans[test[0]][0], max(spans[i][1] for i in test)
            for i in train:
                self.assertFalse(spans[i][0] <= test_last and spans[i][1] >= test_first)
                self.assertFalse(test[-1] < i <= test[-1] + 3)
            self.assertTrue(set(train).isdisjoint(test))

    def test_labels_cannot_start_before_their_observation(self) -> None:
        with self.assertRaises(ValueError):
            validation.purged_kfold([(0, 1), (0, 2), (2, 3)], folds=2)


class ExperimentLogTests(unittest.TestCase):
    def _trial(self, sharpe: float, **changes) -> experiment_log.Trial:
        fields = dict(family="momentum", hypothesis="Twelve-month winners keep winning for a month",
                      parameters={"lookback": 252}, data_fingerprint=experiment_log.data_fingerprint("prices", "2026-10-18"),
                      code_version="abc123", metrics={"sharpe_ratio": sharpe})
        fields.update(changes)
        return experiment_log.Trial(**fields)

    def test_every_trial_is_counted_once(self) -> None:
        log = experiment_log.ExperimentLog()
        first = log.record(self._trial(0.4))
        self.assertEqual(log.record(self._trial(0.4, recorded_at="later")), first)
        log.record(self._trial(0.1, parameters={"lookback": 126}))
        log.record(self._trial(0.9, parameters={"lookback": 63}))
        stats = log.family_statistics("momentum")
        self.assertEqual(stats["trials"], 3)
        self.assertAlmostEqual(stats["variance"], 0.16333333333333336)
        self.assertEqual(stats["best"], 0.9)

    def test_a_recorded_trial_cannot_be_rewritten(self) -> None:
        store = experiment_log.MemoryStore()
        log = experiment_log.ExperimentLog(store)
        trial = self._trial(0.4)
        log.record(trial)
        store._lines[0] = store._lines[0].replace('"sharpe_ratio":0.4', '"sharpe_ratio":0.8')
        with self.assertRaises(experiment_log.TrialConflict):
            log.record(trial)

    def test_a_trial_needs_a_hypothesis(self) -> None:
        with self.assertRaises(ValueError):
            experiment_log.ExperimentLog().record(self._trial(0.1, hypothesis="  "))


if __name__ == "__main__":
    unittest.main()
