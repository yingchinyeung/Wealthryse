"""Settled dates for the scheduled jobs, the month-end commentary job's start, and the month-end team's
task record (no network, no database; synthetic values only)."""
from __future__ import annotations

import argparse
import os
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from wealthryse_agent import jobs, settled_days  # noqa: E402
from wealthryse_agent.month_team.agents import BudgetExhausted  # noqa: E402
from wealthryse_agent.supervisor import team_record  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402

# Friday 30 Oct is October's last business day. IBKR has it; Korea's is not final until Monday's run;
# U.S. iStrategy's waits for two more sessions.
DATES = {
    "IBKR": {"latest_valuation_date": "2026-10-30", "latest_holdings_date": "2026-10-30", "settled_through": "2026-10-30"},
    "KOREA": {"latest_valuation_date": "2026-10-30", "latest_holdings_date": "2026-10-30", "settled_through": "2026-10-29"},
    "US": {"latest_valuation_date": "2026-11-02", "latest_holdings_date": "2026-11-02", "settled_through": "2026-10-29"},
}


class _Tools:
    def __init__(self, dates, fail=()):
        self.dates, self.fail, self.calls = dates, fail, []

    def call(self, name, arguments):
        self.calls.append((name, arguments["account_id"]))
        if arguments["account_id"] in self.fail:
            raise ToolCallFailed("tool_error", "The tool failed.")
        return {"data": dict(self.dates[arguments["account_id"]])}


class SettledDateTests(unittest.TestCase):
    def test_settled_dates(self):
        self.assertEqual(settled_days.settled(DATES["US"], "latest_valuation_date"), "2026-10-29")
        self.assertEqual(settled_days.settled(DATES["IBKR"], "latest_holdings_date"), "2026-10-30")
        self.assertIsNone(settled_days.settled({**DATES["US"], "settled_through": None}, "latest_valuation_date"))
        self.assertIsNone(settled_days.settled({"latest_valuation_date": None, "settled_through": None},
                                               "latest_valuation_date"))
        self.assertEqual(settled_days.settled({"latest_valuation_date": "2026-11-02"}, "latest_valuation_date"),
                         "2026-11-02", "an older tool server: every date counts as final, as before")

    def test_an_older_tool_server_answers_through_freshness(self):
        class Older:
            def call(self, name, arguments):
                if name == settled_days.TOOL:
                    raise ToolCallFailed("unknown_tool", "No such tool.")
                return {"data": {"latest_valuation_date": "2026-10-30"}}

        self.assertEqual(settled_days.read_dates(Older().call, "U1"), {"latest_valuation_date": "2026-10-30"})

    def test_a_month_is_final_when_its_last_business_day_is_settled(self):
        october = date(2026, 10, 31)
        self.assertEqual(settled_days.last_business_day(october), date(2026, 10, 30))
        self.assertEqual(settled_days.month_end(date(2026, 11, 2)), october)
        self.assertTrue(settled_days.month_final(DATES["IBKR"], october))
        self.assertFalse(settled_days.month_final(DATES["KOREA"], october))
        self.assertFalse(settled_days.month_final(DATES["US"], october))
        self.assertTrue(settled_days.month_final({**DATES["US"], "settled_through": "2026-10-30"}, october))


class MonthEndStartTests(unittest.TestCase):
    def test_the_scheduled_draft_waits_for_each_accounts_final_month(self):
        tools = _Tools(DATES)
        self.assertEqual(jobs.month_end_ready(tools, ["IBKR", "KOREA", "US"], date(2026, 11, 2)), ["IBKR"])
        korea_final = {**DATES, "KOREA": {**DATES["KOREA"], "latest_valuation_date": "2026-11-02",
                                          "latest_holdings_date": "2026-11-02", "settled_through": "2026-10-30"}}
        self.assertEqual(jobs.month_end_ready(_Tools(korea_final), ["IBKR", "KOREA", "US"], date(2026, 11, 3)),
                         ["IBKR", "KOREA"])

    def test_the_last_day_drafts_every_account_and_a_failed_read_waits(self):
        tools = _Tools(DATES, fail=("IBKR",))
        self.assertEqual(jobs.month_end_ready(tools, ["IBKR", "KOREA", "US"], date(2026, 11, 7)),
                         ["IBKR", "KOREA", "US"], "the 7th is the job's last day: no month goes without a draft")
        self.assertEqual(tools.calls, [])
        self.assertEqual(jobs.month_end_ready(tools, ["IBKR"], date(2026, 11, 2)), [])

    def test_one_setting_turns_the_wait_off(self):
        self.assertTrue(jobs.month_end_waits({}))
        self.assertTrue(jobs.month_end_waits({"AGENT_MONTH_END_WAITS_FOR_DATA": "on"}))
        self.assertFalse(jobs.month_end_waits({"AGENT_MONTH_END_WAITS_FOR_DATA": " OFF "}))

    def test_the_commentary_job_itself_is_unchanged(self):
        import inspect

        source = inspect.getsource(jobs.month_end_drafter)
        self.assertNotIn("settled", source)
        self.assertNotIn("month_final", source)


STEPS = [
    {"subject_id": "U1", "period_end": "2026-10-31", "stage": "words", "agent_name": "coordinator", "outcome": "pass",
     "usage": {}},
    {"subject_id": "U1", "period_end": "2026-10-31", "stage": "words", "agent_name": "data", "outcome": "pass",
     "usage": {}},
    {"subject_id": "U1", "period_end": "2026-10-31", "stage": "words", "agent_name": "writer", "outcome": "pass",
     "usage": {"input_tokens": 300, "output_tokens": 40}},
    {"subject_id": "U1", "period_end": "2026-10-31", "stage": "words", "agent_name": "reviewer", "outcome": "objection",
     "usage": {"input_tokens": 100}},
    {"subject_id": "U1", "period_end": "2026-10-31", "stage": "words", "agent_name": "writer", "outcome": "pass",
     "usage": {"input_tokens": 200}},
    {"subject_id": "K1", "period_end": "2026-10-31", "stage": "held", "agent_name": "data", "outcome": "blocked",
     "usage": {}},
]


class _RecordStore:
    def __init__(self, steps=(), ready=True, refuse=False, broken=False):
        self.steps, self.ready, self.refuse, self.broken, self.trees = list(steps), ready, refuse, broken, []
        self.db = SimpleNamespace(rollback=lambda: None)

    def tasks_ready(self):
        return self.ready

    def month_team_steps(self, correlation_id):
        if self.broken:
            raise RuntimeError("connection lost")
        return self.steps

    def add_tasks(self, rows):
        if self.refuse:
            return False
        self.trees.append(rows)
        return True


class TeamRecordTests(unittest.TestCase):
    def test_one_tree_per_run_with_each_agent_that_acted_on_each_account(self):
        rows = team_record.month_team_tree(STEPS, correlation_id="cid-1", live=True, mode="month",
                                           period="2026-10-31", counts={"words": 1, "held": 1},
                                           usage={"input_tokens": 600, "output_tokens": 40})
        root, lead, *members = rows
        self.assertEqual((root["level"], root["source"], root["mode"], root["status"], root["parent_id"]),
                         ("supervisor", "job", "live", "done", None))
        self.assertEqual(root["detail"], {"job": "month_team", "mode": "month", "period": "2026-10-31",
                                          "stages": {"words": 1, "held": 1}, "accounts": 2})
        self.assertEqual((lead["level"], lead["team"], lead["role"], lead["parent_id"]),
                         ("lead", "reporting", "month_end_coordinator", root["task_id"]))
        self.assertEqual(lead["detail"], {"coordinator_steps": {"pass": 1}})
        got = {(m["subject_id"], m["role"]): (m["level"], m["status"]) for m in members}
        self.assertEqual(got, {("K1", "month_end_data"): ("specialist", "needs_input"),
                               ("U1", "month_end_data"): ("specialist", "done"),
                               ("U1", "month_end_writer"): ("specialist", "done"),
                               ("U1", "month_end_reviewer"): ("checker", "rejected")})
        writer = next(m for m in members if m["role"] == "month_end_writer")
        self.assertEqual(writer["usage"], {"input_tokens": 500, "output_tokens": 40})
        self.assertEqual((writer["pinned_as_of"], writer["detail"]), ("2026-10-31", {"stage": "words",
                                                                                     "outcomes": {"pass": 2}}))
        self.assertTrue(all(m["parent_id"] == lead["task_id"] and m["root_id"] == root["task_id"] for m in members))
        self.assertTrue(all(r["correlation_id"] == "cid-1" for r in rows))

    def test_a_failed_run_and_a_team_in_shadow(self):
        rows = team_record.month_team_tree([], correlation_id="cid-2", live=False, mode="month", period="2026-10-31",
                                           counts={}, usage={}, error_code="budget_exhausted")
        self.assertEqual([(r["level"], r["status"], r["mode"]) for r in rows],
                         [("supervisor", "failed", "watch"), ("lead", "failed", "watch")])
        self.assertEqual(rows[0]["detail"]["error_code"], "budget_exhausted")

    def test_recording_never_stops_the_team(self):
        self.assertTrue(team_record.record_month_team(_RecordStore(STEPS), "c", live=False, mode="month",
                                                      period="2026-10-31", counts={}, usage={}))
        for store in (_RecordStore(ready=False), _RecordStore(STEPS, refuse=True), _RecordStore(broken=True)):
            self.assertFalse(team_record.record_month_team(store, "c", live=False, mode="month", period="2026-10-31",
                                                           counts={}, usage={}))

    def test_the_month_team_job_records_each_run_even_when_it_stops(self):
        settings = SimpleNamespace(validate=lambda: None, monthly_token_budget=1000, live=False)
        recorded = []

        class Coordinator:
            outcome = {"words": 2}

            def __init__(self, *args, **kwargs):
                pass

            def run_month(self, accounts, period, mode):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome

        def record(store, correlation_id, **kwargs):
            recorded.append(kwargs)
            return True

        store = SimpleNamespace(db=object(), tenant_id="t")
        tools = SimpleNamespace(call=lambda *a, **k: {})
        args = argparse.Namespace(replay=False, period=None, retry=False)
        with mock.patch("wealthryse_agent.month_team.settings.TeamSettings.from_env", return_value=settings), \
                mock.patch("wealthryse_agent.month_team.settings.validate_report_model"), \
                mock.patch.object(jobs.ModelSettings, "from_env", return_value=object()), \
                mock.patch("wealthryse_agent.month_team.store.MonthStore.tokens_used_this_month", return_value=0), \
                mock.patch("wealthryse_agent.month_team.coordinator.Coordinator", Coordinator), \
                mock.patch.object(team_record, "record_month_team", record), \
                mock.patch.dict(os.environ, {"AGENT_MONTH_TEAM_RECORD": "on"}):
            self.assertEqual(jobs.month_team(store, tools, ["U1", "U2"], {}, args, "cid", today=date(2026, 11, 2)), 0)
            Coordinator.outcome = BudgetExhausted()
            with self.assertRaises(BudgetExhausted):
                jobs.month_team(store, tools, ["U1"], {}, args, "cid", today=date(2026, 11, 2))
        self.assertEqual([(r["period"], r["counts"], r["error_code"]) for r in recorded],
                         [("2026-10-31", {"words": 2}, None), ("2026-10-31", {}, "budget_exhausted")])

    def test_the_record_is_off_until_its_setting_is_on(self):
        self.assertFalse(jobs.month_team_records({}))
        self.assertFalse(jobs.month_team_records({"AGENT_MONTH_TEAM_RECORD": "watch"}))
        self.assertTrue(jobs.month_team_records({"AGENT_MONTH_TEAM_RECORD": " ON "}))


if __name__ == "__main__":
    unittest.main()
