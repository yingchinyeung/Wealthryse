"""The risk watch job's rules, monthly model check and inbox cards. Synthetic values only."""
from __future__ import annotations

import unittest
from datetime import date

from wealthryse_agent.jobs import model_check_findings, risk_triggers, risk_watch
from wealthryse_agent.tool_client import ToolCallFailed
from wealthryse_agent.runtime import Turn


def outlook(**overrides):
    data = {"window_end": "2026-10-14", "forecast_volatility_next_month_annual": 0.18, "long_run_volatility_annual": 0.15,
            "volatility_vs_long_run": 1.2,
            "latest_day": {"date": "2026-10-14", "return": -0.004, "var_95": 0.015, "breached": False},
            "backtest": {"available": True, "kupiec_p_value": 0.4,
                         "verdict": "Losses broke the 95% VaR line on 26 of 500 days (about 25 expected): in line."}}
    data.update(overrides)
    return data


def regimes(**overrides):
    data = {"window_end": "2026-10-14", "current_regime": "Calm", "regime_a_week_ago": "Calm",
            "current_probability": 0.9, "regimes": [{"regime": "Calm"}, {"regime": "Stressed"}],
            "score": {"available": True, "coverage_90": 0.88, "tested_months": 30, "directional_skill": False}}
    data.update(overrides)
    return data


class FakeTools:
    def __init__(self, risk, regime, fail=()):
        self.risk, self.regime, self.fail, self.calls = risk, regime, fail, []

    def call(self, name, arguments):
        self.calls.append(name)
        if name in self.fail:
            raise ToolCallFailed("not_enough_history", "Too short.")
        return {"data": dict(self.risk if name == "get_risk_outlook" else self.regime)}


class FakeAgent:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "answer" if self.ok else "rejected",
               "turn": Turn(answer="Technology holdings drove it [T1].", citations=[{"id": "T1"}],
                            usage={"input_tokens": 4, "output_tokens": 1})}


class FakeStore:
    def __init__(self):
        self.items = {}

    def inbox_exists(self, kind, account_id, period_key):
        return (kind, account_id, period_key) in self.items

    def add_inbox(self, *, kind, account_id, period_key, title, body, citations, run_id):
        self.items[(kind, account_id, period_key)] = (title, body)
        return True


class RiskTriggerTests(unittest.TestCase):
    def test_quiet_day_has_no_alerts(self):
        self.assertEqual(risk_triggers(outlook(), regimes()), [])

    def test_breach_regime_switch_and_volatility_jump(self):
        found = risk_triggers(
            outlook(volatility_vs_long_run=1.8, forecast_volatility_next_month_annual=0.27,
                    latest_day={"date": "2026-10-14", "return": -0.031, "var_95": 0.015, "breached": True}),
            regimes(current_regime="Stressed", current_probability=0.75))
        self.assertEqual([k for k, _, _ in found], ["var_breach:2026-10-14", "regime:stressed:2026-10-14",
                                                    "volatility:2026-W42"])
        self.assertIn("returned -3.10%, beyond its 95% one-day VaR of 1.50%", found[0][2])
        self.assertIn("(75% likely), up from calm a week ago", found[1][2])

    def test_an_unsure_or_old_stressed_regime_does_not_alert(self):
        self.assertEqual(risk_triggers(outlook(), regimes(current_regime="Stressed", current_probability=0.5)), [])
        self.assertEqual(risk_triggers(outlook(), regimes(current_regime="Stressed", regime_a_week_ago="Stressed")), [])


class ModelCheckTests(unittest.TestCase):
    def test_in_line_models_pass(self):
        problems, passes = model_check_findings(outlook(), regimes())
        self.assertEqual(problems, [])
        self.assertTrue(any("no reliable directional skill" in p for p in passes))

    def test_failed_backtest_and_narrow_ranges_are_problems(self):
        problems, _ = model_check_findings(outlook(backtest={"available": True, "kupiec_p_value": 0.01,
                                                             "verdict": "Too many breaches."}),
                                           regimes(score={"available": True, "coverage_90": 0.6, "tested_months": 20,
                                                          "directional_skill": False}))
        self.assertEqual(len(problems), 2)
        self.assertIn("outside 80% to 97%", problems[1])


class RiskWatchJobTests(unittest.TestCase):
    def test_alerts_once_with_explanation_and_one_model_check_a_month(self):
        store, agent = FakeStore(), FakeAgent()
        tools = FakeTools(outlook(latest_day={"date": "2026-10-14", "return": -0.03, "var_95": 0.015, "breached": True}),
                          regimes())
        self.assertEqual(risk_watch(store, tools, agent, ["U1"], {}, today=date(2026, 10, 15)), 2)
        self.assertIn(("risk_alert", "U1", "var_breach:2026-10-14"), store.items)
        self.assertIn(("model_check", "U1", "2026-10"), store.items)
        self.assertEqual(store.items[("model_check", "U1", "2026-10")][0], "Risk model check for 2026-10: in line")
        self.assertIn("get_holdings_changes", agent.calls[0]["allowed_tools"])
        self.assertEqual(risk_watch(store, tools, agent, ["U1"], {}, today=date(2026, 10, 16)), 0)

    def test_rejected_explanation_still_alerts_and_short_history_is_skipped(self):
        store = FakeStore()
        tools = FakeTools(outlook(volatility_vs_long_run=2.0), regimes())
        self.assertEqual(risk_watch(store, tools, FakeAgent(ok=False), ["U1"], {}, today=date(2026, 10, 15)), 2)
        body = store.items[("risk_alert", "U1", "volatility:2026-W42")][1]
        self.assertIn("could not be checked", body)
        self.assertEqual(risk_watch(FakeStore(), FakeTools(outlook(), regimes(), fail=("get_risk_outlook",)),
                                    FakeAgent(), ["U1"], {}, today=date(2026, 10, 15)), 0)


if __name__ == "__main__":
    unittest.main()
