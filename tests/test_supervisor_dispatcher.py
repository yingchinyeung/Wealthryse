"""The event dispatcher: which job new data wakes, what it records, and what it runs (no network)."""
from __future__ import annotations

import json
import os
import re
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from wealthryse_agent.runtime import Turn  # noqa: E402
from wealthryse_agent.supervisor import dispatcher, registry, report  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
# Thursday 29 Oct 2026, 23:10 UTC: Thursday's holdings are in, Friday's are not.
NOW = datetime(2026, 10, 29, 23, 10, tzinfo=UTC)
FRESH = {"U1": {"latest_holdings_date": "2026-10-29", "latest_valuation_date": "2026-10-29"},
         "U2": {"latest_holdings_date": "2026-10-30", "latest_valuation_date": "2026-10-30"}}


class _Client:
    def __init__(self, job, accounts, fail=()):
        self.job, self.accounts, self.fail, self.calls, self.closed = job, accounts, fail, [], False

    def call(self, name, arguments):
        self.calls.append((name, arguments.get("account_id")))
        if name == "list_accounts":
            return {"data": {"accounts": [{"account_id": a} for a in self.accounts]}}
        if name in self.fail or arguments.get("account_id") in self.fail:
            raise ToolCallFailed("tool_error", "The tool failed.")
        return {"data": dict(FRESH[arguments["account_id"]])}

    def close(self):
        self.closed = True


class _Store:
    def __init__(self, ready=True, inbox=(), seen=()):
        self.ready, self.inbox, self.seen = ready, set(inbox), set(seen)
        self.trees = []

    def tasks_ready(self):
        return self.ready

    def inbox_exists(self, kind, account_id, period_key):
        return (kind, account_id, period_key) in self.inbox

    def add_inbox(self, *, kind, account_id, period_key, **_):
        if (kind, account_id, period_key) in self.inbox:
            return False
        self.inbox.add((kind, account_id, period_key))
        return True

    def event_keys(self, days):
        return set(self.seen) | {t[0]["correlation_id"] for t in self.trees}

    def add_tasks(self, rows):
        self.trees.append(rows)
        return True


def _clients(accounts=("U1",), fail=()):
    opened = []

    def open_client(job):
        client = _Client(job, list(accounts), fail)
        opened.append(client)
        return client
    return open_client, opened


class SwitchTests(unittest.TestCase):
    def test_off_unless_watch_or_live_and_live_runs_only_named_jobs(self):
        self.assertEqual(dispatcher.mode({}), "off")
        self.assertEqual(dispatcher.mode({"AGENT_DISPATCHER": " Watch "}), "watch")
        self.assertEqual(dispatcher.mode({"AGENT_DISPATCHER": "on"}), "off")
        self.assertEqual(dispatcher.live_jobs({}), frozenset())
        self.assertEqual(dispatcher.live_jobs({"AGENT_DISPATCHER_JOBS": "risk_watch, month_team ,"}),
                         frozenset({"risk_watch"}), "only the jobs it knows how to wake")

    def test_every_wake_is_a_scheduled_job_in_the_registry(self):
        for wake in dispatcher.WAKES:
            member = registry.BY_NAME[registry.JOB_MEMBER[wake.job]]
            self.assertEqual((member.team, member.kind), ("client_analysis", "plan"))

    def test_fixed_times_match_the_bicep_schedules(self):
        apps = (ROOT / "infra" / "agent-apps.bicep").read_text()
        for wake in dispatcher.WAKES:
            match = re.search(r"job: '" + wake.job + r"', cron: '([^']+)'", apps)
            self.assertIsNotNone(match, wake.job)
            self.assertEqual(match.group(1), wake.cron, wake.job)

    def test_the_bicep_ships_it_undeployed_and_names_no_jobs(self):
        apps = (ROOT / "infra" / "agent-apps.bicep").read_text()
        self.assertIn("param deployDispatcher bool = false", apps)
        self.assertIn("param dispatcherJobs string = ''", apps)
        self.assertIn("resource dispatcherJob 'Microsoft.App/jobs@2024-03-01' = if (deployDispatcher) {", apps)
        job = apps[apps.index("resource dispatcherJob"):apps.index("output agentApiUrl")]
        self.assertIn("['python', '-m', 'wealthryse_agent.supervisor.dispatcher']", job)
        self.assertIn("{ name: 'AGENT_DISPATCHER', value: dispatcherMode }", job)
        self.assertIn("{ name: 'AGENT_DISPATCHER_JOBS', value: dispatcherJobs }", job)
        self.assertIn("jobsIdentity", job)
        self.assertNotIn("agentIdentity", job)


class ScheduleTests(unittest.TestCase):
    def test_next_fixed_run(self):
        self.assertEqual(dispatcher.next_fixed_run("30 0 * * *", NOW), datetime(2026, 10, 30, 0, 30, tzinfo=UTC))
        friday = datetime(2026, 10, 30, 2, 0, tzinfo=UTC)
        self.assertEqual(dispatcher.next_fixed_run("15 1 * * 1-5", friday), datetime(2026, 11, 2, 1, 15, tzinfo=UTC))
        self.assertEqual(dispatcher.next_fixed_run("30 1 * * 6", NOW), datetime(2026, 10, 31, 1, 30, tzinfo=UTC))
        at = datetime(2026, 10, 31, 1, 30, tzinfo=UTC)
        self.assertEqual(dispatcher.next_fixed_run("30 1 * * 6", at), at + timedelta(days=7), "strictly after")


class PendingTests(unittest.TestCase):
    def _pending(self, job, fresh, today=date(2026, 10, 29), inbox=()):
        return dispatcher.pending(dispatcher.BY_JOB[job], "U1", fresh, today=today,
                                  inbox_exists=lambda *key: key in set(inbox))

    def test_a_new_holdings_date_wakes_the_explainer_until_its_card_exists(self):
        event = self._pending("holdings_explainer", FRESH["U1"])
        self.assertEqual((event.data_date, event.period_key), ("2026-10-29", "2026-10-29"))
        self.assertIsNone(self._pending("holdings_explainer", FRESH["U1"],
                                        inbox=[("holdings_explainer", "U1", "2026-10-29")]))
        self.assertIsNone(self._pending("holdings_explainer", {"latest_holdings_date": None}))

    def test_the_weekly_brief_waits_for_the_week_to_close(self):
        self.assertIsNone(self._pending("weekly_brief", FRESH["U1"]), "Thursday's data, same week")
        friday = self._pending("weekly_brief", FRESH["U2"], today=date(2026, 10, 31))
        self.assertEqual(friday.period_key, "2026-W44")
        holiday = self._pending("weekly_brief", FRESH["U1"], today=date(2026, 11, 2))
        self.assertEqual(holiday.period_key, "2026-W44", "no Friday data, but the next week has started")
        self.assertIsNone(self._pending("weekly_brief", FRESH["U2"], today=date(2026, 10, 31),
                                        inbox=[("weekly_brief", "U1", "2026-W44")]))

    def test_risk_watch_wakes_on_a_new_valuation_date(self):
        event = self._pending("risk_watch", FRESH["U1"], inbox=[("risk_alert", "U1", "2026-10-29")])
        self.assertEqual(event.key, "event:risk_watch:U1:2026-10-29")

    def test_event_keys_fit_the_table(self):
        event = dispatcher.Event(dispatcher.BY_JOB["weekly_brief"], "C" * 80, "2026-10-30", "2026-W44")
        self.assertLessEqual(len(event.key), 64)
        self.assertTrue(event.key.startswith("event:"))
        rows = dispatcher.tree(event, mode_="watch", detected_at=NOW)
        self.assertIsNone(rows[0]["subject_id"])


class DispatchTests(unittest.TestCase):
    def _run(self, store, mode, jobs=frozenset(), accounts=("U1", "U2"), run_job=None, budget=True, clock=None):
        ran = []

        def default_run(event):
            ran.append((event.wake.job, event.account_id))
            return 1, {"input_tokens": 40, "output_tokens": 10}

        open_client, opened = _clients(accounts)
        counts = dispatcher.dispatch(store, open_client, mode_=mode, jobs=jobs, run_job=run_job or default_run,
                                     now=clock or (lambda: NOW), budget_left=lambda: budget)
        return counts, ran, opened

    def test_watch_records_what_new_data_would_wake_and_runs_nothing(self):
        store = _Store()
        counts, ran, opened = self._run(store, "watch", jobs=frozenset({"holdings_explainer"}))
        self.assertEqual(ran, [])
        keys = sorted(t[0]["correlation_id"] for t in store.trees)
        self.assertEqual(keys, ["event:holdings_explainer:U1:2026-10-29", "event:holdings_explainer:U2:2026-10-30",
                                "event:risk_watch:U1:2026-10-29", "event:risk_watch:U2:2026-10-30",
                                "event:weekly_brief:U2:2026-W44"],
                         "U2's week closes with its Friday; U1's has not")
        self.assertEqual(counts["recorded"], 5)
        rows = store.trees[0]
        self.assertEqual([(r["level"], r["role"]) for r in rows],
                         [("supervisor", "supervisor"), ("lead", "event_dispatcher"), ("specialist", "holdings_explainer")])
        self.assertTrue(all(r["mode"] == "watch" and r["source"] == "event" for r in rows))
        self.assertEqual(rows[0]["status"], "shadow")
        self.assertEqual(rows[0]["detail"]["minutes_before_fixed_run"], 80)
        self.assertEqual(rows[0]["pinned_as_of"], "2026-10-29")
        self.assertTrue(all(c.closed for c in opened))
        self.assertEqual({c.job for c in opened}, {w.job for w in dispatcher.WAKES}, "each job is asked as itself")
        # Freshness is read once per account, not once per job.
        reads = [a for c in opened for name, a in c.calls if name == "get_data_freshness"]
        self.assertEqual(sorted(reads), ["U1", "U2"])

        again, _, _ = self._run(store, "watch")
        self.assertEqual((again["events"], len(store.trees)), (0, 5), "an event is recorded once")

    def test_live_runs_only_the_named_jobs(self):
        store = _Store()
        counts, ran, _ = self._run(store, "live", jobs=frozenset({"holdings_explainer"}))
        self.assertEqual(sorted(ran), [("holdings_explainer", "U1"), ("holdings_explainer", "U2")])
        by_job = {t[0]["detail"]["job"]: t[0] for t in store.trees}
        self.assertEqual(by_job["holdings_explainer"]["status"], "done")
        self.assertEqual(by_job["holdings_explainer"]["detail"]["written"], 1)
        self.assertEqual(by_job["holdings_explainer"]["usage"], {"input_tokens": 40, "output_tokens": 10})
        self.assertEqual(by_job["risk_watch"]["status"], "shadow", "not named: recorded only")
        self.assertEqual((counts["ran"], counts["recorded"]), (2, 5))

    def test_a_failed_run_or_no_budget_is_recorded(self):
        def broken(event):
            raise ToolCallFailed("tool_error", "The tool failed.")

        store = _Store()
        counts, _, _ = self._run(store, "live", jobs=frozenset({"risk_watch"}), accounts=("U1",), run_job=broken)
        root = [t[0] for t in store.trees if t[0]["detail"]["job"] == "risk_watch"][0]
        self.assertEqual((root["status"], root["detail"]["error_code"]), ("failed", "tool_error"))
        self.assertEqual(counts["failed"], 1)

        store = _Store()
        counts, ran, _ = self._run(store, "live", jobs=frozenset({"risk_watch"}), accounts=("U1",), budget=False)
        root = [t[0] for t in store.trees if t[0]["detail"]["job"] == "risk_watch"][0]
        self.assertEqual((root["status"], root["detail"]["error_code"], ran), ("blocked", "budget_exhausted", []))

    def test_off_or_no_table_does_nothing(self):
        for store, mode in ((_Store(), "off"), (_Store(ready=False), "watch")):
            counts, ran, opened = self._run(store, mode, jobs=frozenset({"holdings_explainer"}))
            self.assertEqual((counts["events"], store.trees, ran, opened), (0, [], [], []))

    def test_a_long_run_leaves_the_rest_for_the_next_run(self):
        times = iter([NOW] + [NOW + timedelta(seconds=dispatcher.RUN_SECONDS + 1)] * 10)
        store = _Store()
        counts, _, _ = self._run(store, "watch", clock=lambda: next(times))
        self.assertEqual((counts["recorded"], counts["left_for_next_run"]), (0, 5))
        self.assertEqual(store.trees, [])

    def test_one_account_that_fails_does_not_stop_the_others(self):
        open_client, _ = _clients(("U1", "U2"), fail=("U1",))
        events = dispatcher.find_events(open_client, today=date(2026, 10, 29), inbox_exists=lambda *k: False, seen=())
        self.assertEqual({e.account_id for e in events}, {"U2"})

    def test_records_say_nothing_about_values(self):
        store = _Store()
        self._run(store, "watch")
        text = json.dumps(store.trees)
        self.assertNotIn("latest_holdings_date", text)


class RunJobTests(unittest.TestCase):
    def test_a_woken_job_runs_its_scheduled_code_for_that_account_only(self):
        class Agent:
            def run(self, **kwargs):
                self.kwargs = kwargs
                yield {"type": "answer", "turn": Turn(answer="Two positions changed [T1].", citations=[],
                                                      usage={"input_tokens": 30, "output_tokens": 8})}

        store, agent, clients = _Store(), Agent(), []

        def tools_for(job):
            client = _Client(job, ["U1", "U2"])
            clients.append(client)
            return client

        event = dispatcher.pending(dispatcher.BY_JOB["holdings_explainer"], "U1", FRESH["U1"], today=date(2026, 10, 29),
                                   inbox_exists=store.inbox_exists)
        written, usage = dispatcher._run_job(store, tools_for, lambda: agent, event)
        self.assertEqual((written, usage), (1, {"input_tokens": 30, "output_tokens": 8}))
        self.assertEqual(store.inbox, {("holdings_explainer", "U1", "2026-10-29")})
        self.assertEqual(agent.kwargs["account_id"], "U1")
        self.assertEqual([c.job for c in clients], ["holdings_explainer"])
        self.assertTrue(clients[0].closed)


class ReportTests(unittest.TestCase):
    def test_events_are_counted_apart_from_chat(self):
        store = _Store()
        DispatchTests()._run(store, "live", jobs=frozenset({"holdings_explainer"}))
        roots = [{"mode": t[0]["mode"], "source": t[0]["source"], "status": t[0]["status"], "usage": t[0]["usage"],
                  "detail": t[0]["detail"]} for t in store.trees]
        chat = {"mode": "watch", "source": "chat", "status": "done", "usage": {"input_tokens": 100},
                "detail": {"route": {"lead": "fast_path"}, "agreement": "agree"}}
        summary = report.summarise([chat, *roots])
        self.assertEqual(summary["requests"], 1)
        events = summary["events"]
        self.assertEqual(events["by_job"], {"holdings_explainer": 2, "risk_watch": 2, "weekly_brief": 1})
        self.assertEqual(events["median_minutes_before_fixed_run"]["holdings_explainer"], 80)
        self.assertEqual(events["live"], {"done": 2, "shadow": 3})
        self.assertEqual(events["written"], 2)
        text = report.render(summary, "2026-11-17", "2026-11-24")
        self.assertIn("Events: new data would have woken 5 job run(s)", text)
        self.assertIn("2 inbox card(s) written", text)


if __name__ == "__main__":
    unittest.main()
