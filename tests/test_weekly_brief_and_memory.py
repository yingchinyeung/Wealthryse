"""The weekly brief job and the memory rules. Synthetic values only."""
from __future__ import annotations

import unittest

from wealthryse_agent import memory
from wealthryse_agent.jobs import weekly_brief
from wealthryse_agent.runtime import ANSWER_SCHEMA, Turn
from wealthryse_agent.tool_client import ToolCallFailed


class FakeTools:
    """An older tool server (no get_settled_dates) unless ``settled`` gives each account's settled date."""

    def __init__(self, latest, settled=None):
        self.latest, self.settled = latest, settled

    def call(self, name, arguments):
        account_id = arguments["account_id"]
        if name == "get_settled_dates":
            if self.settled is None:
                raise ToolCallFailed("unknown_tool", "No such tool.")
            return {"data": {"latest_holdings_date": self.latest.get(account_id),
                             "settled_through": self.settled.get(account_id)}}
        assert name == "get_data_freshness"
        return {"data": {"latest_holdings_date": self.latest.get(account_id)}}


class FakeAgent:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        turn = Turn(answer="The account rose 1.2% [T1].", citations=[{"id": "T1"}],
                    usage={"input_tokens": 10, "output_tokens": 2})
        yield {"type": "answer" if self.ok else "rejected", "turn": turn}


class FakeStore:
    def __init__(self):
        self.items = {}

    def inbox_exists(self, kind, account_id, period_key):
        return (kind, account_id, period_key) in self.items

    def add_inbox(self, *, kind, account_id, period_key, title, body, citations, run_id):
        self.items[(kind, account_id, period_key)] = (title, body)
        return True


class WeeklyBriefTests(unittest.TestCase):
    def test_one_brief_per_account_per_week_of_data(self):
        store, agent, usage = FakeStore(), FakeAgent(), {}
        tools = FakeTools({"U1": "2026-10-16", "U2": None})
        self.assertEqual(weekly_brief(store, tools, agent, ["U1", "U2"], usage), 1)
        self.assertEqual(list(store.items), [("weekly_brief", "U1", "2026-W42")])
        self.assertEqual(store.items[("weekly_brief", "U1", "2026-W42")][0], "Your week to 2026-10-16")
        call = agent.calls[0]
        self.assertTrue(call["skip_router"])
        self.assertIn("from 2026-10-09 to 2026-10-16", call["question"])
        self.assertNotIn("get_holdings", call["allowed_tools"])  # the week's changes, not the full list
        self.assertEqual(usage, {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(weekly_brief(store, tools, agent, ["U1"], {}), 0)  # same week: already written

    def test_a_kiwoom_brief_ends_on_its_latest_settled_day(self):
        store, agent = FakeStore(), FakeAgent()
        tools = FakeTools({"K1": "2026-10-16", "U1": "2026-10-16"}, settled={"K1": "2026-10-14", "U1": "2026-10-16"})
        self.assertEqual(weekly_brief(store, tools, agent, ["K1", "U1"], {}), 2)
        self.assertEqual(store.items[("weekly_brief", "K1", "2026-W42")][0], "Your week to 2026-10-14")
        self.assertIn("from 2026-10-07 to 2026-10-14", agent.calls[0]["question"])
        self.assertEqual(store.items[("weekly_brief", "U1", "2026-W42")][0], "Your week to 2026-10-16")

    def test_a_brief_that_fails_the_figure_check_is_not_posted(self):
        store = FakeStore()
        self.assertEqual(weekly_brief(store, FakeTools({"U1": "2026-10-16"}), FakeAgent(ok=False), ["U1"], {}), 0)
        self.assertEqual(store.items, {})


class MemoryRuleTests(unittest.TestCase):
    def test_values_are_checked_before_they_are_kept(self):
        self.assertEqual(memory.clean("answer_length", " Short "), "short")
        self.assertEqual(memory.clean("benchmark", "MSCI  AC Asia (ex-Japan)"), "MSCI AC Asia (ex-Japan)")
        for key, value in (("tone", "angry"), ("focus", "x" * 201), ("focus", "ignore <rules>"), ("secret", "x"),
                           ("benchmark", "")):
            with self.subTest(key=key, value=value), self.assertRaises(memory.MemoryError):
                memory.clean(key, value)

    def test_the_prompt_marks_preferences_as_data(self):
        self.assertEqual(memory.prompt_block([]), "")
        block = memory.prompt_block([{"key": "focus", "value": "drawdowns", "source": "chat", "updated_at": "x"}])
        self.assertIn("- What you care about: drawdowns", block)
        self.assertIn("not instructions", block)

    def test_the_answer_schema_asks_for_memory_only_when_asked(self):
        self.assertIn("remember", ANSWER_SCHEMA["required"])
        self.assertEqual(ANSWER_SCHEMA["properties"]["remember"]["items"]["properties"]["key"]["enum"],
                         list(memory.KEYS))


if __name__ == "__main__":
    unittest.main()
