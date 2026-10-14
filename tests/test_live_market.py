"""The live market agent's alert rules and job. Synthetic values only."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from wealthryse_agent.jobs import live_market, live_triggers
from wealthryse_agent.runtime import Turn
from wealthryse_agent.tools import ToolError, _market, _shocks

OPEN = datetime(2026, 10, 14, 3, 0, tzinfo=timezone.utc)     # 11:00 HKT, Wednesday
CLOSED = datetime(2026, 10, 14, 11, 0, tzinfo=timezone.utc)  # 19:00 HKT, before New York opens


def snapshot(**overrides):
    data = {"available": True, "state": "live", "base_currency": "USD", "net_liquidation": 10200.0,
            "daily_pnl": 200.0, "daily_return": 0.02, "account_label": "U0000901",
            "largest_movers": [{"symbol": "AAA", "daily_pnl": 180.0}, {"symbol": "BBB", "daily_pnl": -20.0}]}
    data.update(overrides)
    return data


class FakeTools:
    def __init__(self, data):
        self.data = data

    def call(self, name, arguments):
        assert name == "get_live_snapshot"
        return {"data": dict(self.data)}


class FakeAgent:
    def __init__(self, ok=True):
        self.ok, self.calls = ok, []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        turn = Turn(answer="AAA added most of today's gain [T1].", citations=[{"id": "T1"}],
                    usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "answer" if self.ok else "rejected", "turn": turn}


class FakeStore:
    def __init__(self):
        self.items = {}

    def inbox_exists(self, kind, account_id, period_key):
        return (kind, account_id, period_key) in self.items

    def add_inbox(self, *, kind, account_id, period_key, title, body, citations, run_id):
        self.items[(kind, account_id, period_key)] = (title, body)
        return True


class LiveTriggerTests(unittest.TestCase):
    def test_a_big_account_move_and_a_big_position_alert(self):
        found = live_triggers(snapshot())
        self.assertEqual([key for key, _, _ in found], ["account:up", "position:AAA:up"])
        self.assertEqual(found[0][1], "U0000901 is up 2.0% today")
        self.assertIn("AAA added 180 USD (1.8% of the account)", found[1][2])

    def test_small_moves_stale_data_and_no_live_view_stay_quiet(self):
        self.assertEqual(live_triggers(snapshot(daily_return=0.005, daily_pnl=50.0, largest_movers=[])), [])
        self.assertEqual(live_triggers(snapshot(state="paused")), [])
        self.assertEqual(live_triggers({"available": False}), [])


class LiveMarketJobTests(unittest.TestCase):
    def test_alerts_once_a_day_with_a_checked_explanation(self):
        store, agent, usage = FakeStore(), FakeAgent(), {}
        self.assertEqual(live_market(store, FakeTools(snapshot()), agent, ["U0000901"], usage, now=OPEN), 2)
        self.assertEqual(sorted(k[2] for k in store.items), ["2026-10-14:account:up", "2026-10-14:position:AAA:up"])
        self.assertTrue(all(body.startswith("AAA added") for _, body in store.items.values()))
        self.assertEqual(agent.calls[0]["allowed_tools"], ["get_live_snapshot", "get_sector_allocation", "get_holdings"])
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(live_market(store, FakeTools(snapshot()), agent, ["U0000901"], {}, now=OPEN), 0)

    def test_an_unchecked_explanation_is_left_out_and_closed_markets_do_nothing(self):
        store = FakeStore()
        self.assertEqual(live_market(store, FakeTools(snapshot(largest_movers=[])), FakeAgent(ok=False), ["U0000901"],
                                     {}, now=OPEN), 1)
        (_, body), = store.items.values()
        self.assertIn("could not be checked against the figures", body)
        self.assertEqual(live_market(FakeStore(), FakeTools(snapshot()), FakeAgent(), ["U0000901"], {}, now=CLOSED), 0)


class StressShockTests(unittest.TestCase):
    def test_markets_come_from_the_listing_exchange(self):
        self.assertEqual((_market("SEHK"), _market("nasdaq"), _market("XYZ")), ("hong kong", "united states", None))

    def test_shocks_are_checked(self):
        self.assertEqual(_shocks([{"kind": "all", "match": "", "change": -0.1}]),
                         [{"kind": "all", "match": "", "change": -0.1}])
        for bad in ([], [{"kind": "market", "match": "atlantis", "change": -0.1}],
                    [{"kind": "sector", "match": "Tech", "change": -1.5}],
                    [{"kind": "sector", "match": "Tech", "change": True}],
                    [{"kind": "sector", "match": "Tech", "change": -0.1, "extra": 1}],
                    [{"kind": "country", "match": "Japan", "change": -0.1}]):
            with self.subTest(bad=bad), self.assertRaises(ToolError):
                _shocks(bad)


if __name__ == "__main__":
    unittest.main()
