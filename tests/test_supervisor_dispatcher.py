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
            self.assertIn((member.team, member.kind), {("client_analysis", "plan"), ("reporting", "specialist"),
                                                       ("reporting", "plan")}, wake.job)
        self.assertEqual([w.job for w in dispatcher.WAKES if not w.model], ["export_packs"])
        self.assertNotIn("month_team", dispatcher.BY_JOB, "the month-end team wakes on events after December")

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
        self.assertIn("replicaTimeout: %d" % (dispatcher.JOB_TIMEOUT_MINUTES * 60), job)


class ScheduleTests(unittest.TestCase):
    def test_next_fixed_run(self):
        self.assertEqual(dispatcher.next_fixed_run("30 0 * * *", NOW), datetime(2026, 10, 30, 0, 30, tzinfo=UTC))
        friday = datetime(2026, 10, 30, 2, 0, tzinfo=UTC)
        self.assertEqual(dispatcher.next_fixed_run("15 1 * * 1-5", friday), datetime(2026, 11, 2, 1, 15, tzinfo=UTC))
        self.assertEqual(dispatcher.next_fixed_run("30 1 * * 6", NOW), datetime(2026, 10, 31, 1, 30, tzinfo=UTC))
        at = datetime(2026, 10, 31, 1, 30, tzinfo=UTC)
        self.assertEqual(dispatcher.next_fixed_run("30 1 * * 6", at), at + timedelta(days=7), "strictly after")
        # The month-end commentary job: 02:00 on the 2nd to the 7th.
        self.assertEqual(dispatcher.next_fixed_run("0 2 2-7 * *", NOW), datetime(2026, 11, 2, 2, 0, tzinfo=UTC))
        self.assertEqual(dispatcher.next_fixed_run("0 2 2-7 * *", datetime(2026, 11, 3, 1, 0, tzinfo=UTC)),
                         datetime(2026, 11, 3, 2, 0, tzinfo=UTC))
        self.assertEqual(dispatcher.next_fixed_run("0 2 2-7 * *", datetime(2026, 11, 7, 3, 0, tzinfo=UTC)),
                         datetime(2026, 12, 2, 2, 0, tzinfo=UTC))
        self.assertEqual(dispatcher.next_fixed_run("0 2 1 * 1", datetime(2026, 11, 1, 3, 0, tzinfo=UTC)),
                         datetime(2026, 11, 2, 2, 0, tzinfo=UTC), "either day field, as cron")


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

    def test_a_kiwoom_account_wakes_jobs_only_for_its_settled_day(self):
        # U.S. iStrategy: the 28th and 29th are provisional; the 27th is settled.
        kiwoom = {"latest_holdings_date": "2026-10-29", "latest_valuation_date": "2026-10-29",
                  "settled_through": "2026-10-27"}
        event = self._pending("holdings_explainer", kiwoom)
        self.assertEqual((event.data_date, event.period_key), ("2026-10-27", "2026-10-27"))
        self.assertEqual(self._pending("risk_watch", kiwoom).key, "event:risk_watch:U1:2026-10-27")
        self.assertIsNone(self._pending("risk_watch", {**kiwoom, "settled_through": None}), "nothing settled yet")
        # Friday's data are in but not settled: the week is not closed until a later week starts.
        friday = {"latest_holdings_date": "2026-10-30", "latest_valuation_date": "2026-10-30",
                  "settled_through": "2026-10-28"}
        self.assertIsNone(self._pending("weekly_brief", friday, today=date(2026, 10, 31)))
        self.assertEqual(self._pending("weekly_brief", friday, today=date(2026, 11, 2)).period_key, "2026-W44")

    def test_with_the_certified_view_a_date_wakes_jobs_once_its_published_build_covers_it(self):
        behind = {**FRESH["U1"], "certified": {"view": True, "as_of_date": "2026-10-28", "build_id": "b-41"}}
        self.assertIsNone(self._pending("holdings_explainer", behind), "the build for the 29th is not published")
        covered = {**FRESH["U1"], "certified": {"view": True, "as_of_date": "2026-10-29", "build_id": "b-42"}}
        event = self._pending("holdings_explainer", covered)
        self.assertEqual((event.layer, event.build_id, event.data_date), ("certified", "b-42", "2026-10-29"))
        rows = dispatcher.tree(event, mode_="watch", detected_at=NOW)
        self.assertEqual((rows[0]["detail"]["layer"], rows[0]["build_ids"]), ("certified", ["b-42"]))
        for silver in ({**FRESH["U1"], "certified": {"view": False, "as_of_date": None}},
                       {**FRESH["U1"], "certified": {"view": True, "as_of_date": None}}, FRESH["U1"]):
            event = self._pending("holdings_explainer", silver)
            self.assertEqual((event.layer, event.build_id), ("silver", None), "no view, or the build skips the account")

    def test_a_final_month_wakes_the_month_end_draft_on_the_2nd_to_the_7th(self):
        ibkr = {"latest_holdings_date": "2026-10-30", "latest_valuation_date": "2026-10-30", "settled_through": "2026-10-30"}
        event = self._pending("month_end_drafter", ibkr, today=date(2026, 11, 2))
        self.assertEqual((event.data_date, event.period_key, event.key),
                         ("2026-10-31", "2026-10", "event:month_end_drafter:U1:2026-10"))
        self.assertIsNone(self._pending("month_end_drafter", ibkr, today=date(2026, 11, 1)), "the job's window starts on the 2nd")
        self.assertIsNone(self._pending("month_end_drafter", ibkr, today=date(2026, 11, 8)))
        self.assertIsNone(self._pending("month_end_drafter", ibkr, today=date(2026, 11, 2),
                                        inbox=[("month_end_draft", "U1", "2026-10")]))
        # Korea iStrategy: Friday the 30th is final only once Monday's run stores the official close.
        korea = {"latest_holdings_date": "2026-10-30", "latest_valuation_date": "2026-10-30", "settled_through": "2026-10-29"}
        self.assertIsNone(self._pending("month_end_drafter", korea, today=date(2026, 11, 2)))
        korea.update(latest_holdings_date="2026-11-02", latest_valuation_date="2026-11-02", settled_through="2026-10-30")
        self.assertEqual(self._pending("month_end_drafter", korea, today=date(2026, 11, 3)).period_key, "2026-10")
        self.assertIsNone(self._pending("month_end_drafter", {"latest_holdings_date": "2026-10-29",
                                                              "latest_valuation_date": "2026-10-30"},
                                        today=date(2026, 11, 2)), "holdings short of the month's last day")
        # With Medallion's view, the draft also waits for the build that covers the month's last business day.
        self.assertIsNone(self._pending("month_end_drafter", {**ibkr, "certified": {"as_of_date": "2026-10-29"}},
                                        today=date(2026, 11, 2)))
        event = self._pending("month_end_drafter", {**ibkr, "certified": {"as_of_date": "2026-10-30", "build_id": "b-9"}},
                              today=date(2026, 11, 2))
        self.assertEqual((event.layer, event.build_id, event.data_date), ("certified", "b-9", "2026-10-31"))

    def test_export_packs_wake_on_a_new_valuation_date_without_a_model(self):
        event = self._pending("export_packs", FRESH["U1"], inbox=[("export_pack", "U1", "2026-10-29")])
        self.assertEqual(event.key, "event:export_packs:U1:2026-10-29")
        self.assertFalse(event.wake.model)

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
        self.assertEqual(keys, ["event:export_packs:U1:2026-10-29", "event:export_packs:U2:2026-10-30",
                                "event:holdings_explainer:U1:2026-10-29", "event:holdings_explainer:U2:2026-10-30",
                                "event:risk_watch:U1:2026-10-29", "event:risk_watch:U2:2026-10-30",
                                "event:weekly_brief:U2:2026-W44"],
                         "U2's week closes with its Friday; U1's has not; no month-end on the 29th")
        self.assertEqual(counts["recorded"], 7)
        rows = store.trees[0]
        self.assertEqual([(r["level"], r["role"]) for r in rows],
                         [("supervisor", "supervisor"), ("lead", "event_dispatcher"), ("specialist", "holdings_explainer")])
        self.assertTrue(all(r["mode"] == "watch" and r["source"] == "event" for r in rows))
        self.assertEqual(rows[0]["status"], "shadow")
        self.assertEqual(rows[0]["detail"]["minutes_before_fixed_run"], 80)
        self.assertEqual(rows[0]["pinned_as_of"], "2026-10-29")
        self.assertTrue(all(c.closed for c in opened))
        self.assertEqual({c.job for c in opened}, {w.job for w in dispatcher.WAKES}, "each job is asked as itself")
        # The dates are read once per account, not once per job.
        reads = [a for c in opened for name, a in c.calls if name == "get_settled_dates"]
        self.assertEqual(sorted(reads), ["U1", "U2"])

        again, _, _ = self._run(store, "watch")
        self.assertEqual((again["events"], len(store.trees)), (0, 7), "an event is recorded once")

    def test_live_runs_only_the_named_jobs(self):
        store = _Store()
        counts, ran, _ = self._run(store, "live", jobs=frozenset({"holdings_explainer"}))
        self.assertEqual(sorted(ran), [("holdings_explainer", "U1"), ("holdings_explainer", "U2")])
        by_job = {t[0]["detail"]["job"]: t[0] for t in store.trees}
        self.assertEqual(by_job["holdings_explainer"]["status"], "done")
        self.assertEqual(by_job["holdings_explainer"]["detail"]["written"], 1)
        self.assertEqual(by_job["holdings_explainer"]["usage"], {"input_tokens": 40, "output_tokens": 10})
        self.assertEqual(by_job["risk_watch"]["status"], "shadow", "not named: recorded only")
        self.assertEqual((counts["ran"], counts["recorded"]), (2, 7))

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

        store = _Store()
        counts, ran, _ = self._run(store, "live", jobs=frozenset({"export_packs"}), accounts=("U1",), budget=False)
        self.assertEqual(ran, [("export_packs", "U1")], "export packs call no model, so the token budget is not theirs")

    def test_off_or_no_table_does_nothing(self):
        for store, mode in ((_Store(), "off"), (_Store(ready=False), "watch")):
            counts, ran, opened = self._run(store, mode, jobs=frozenset({"holdings_explainer"}))
            self.assertEqual((counts["events"], store.trees, ran, opened), (0, [], [], []))

    def test_a_long_run_leaves_the_rest_for_the_next_run(self):
        times = iter([NOW] + [NOW + timedelta(seconds=dispatcher.RUN_SECONDS + 1)] * 10)
        store = _Store()
        counts, _, _ = self._run(store, "watch", clock=lambda: next(times))
        self.assertEqual((counts["recorded"], counts["left_for_next_run"]), (0, 7))
        self.assertEqual(store.trees, [])

    def test_in_live_mode_no_model_job_starts_late_in_a_run(self):
        late = NOW + timedelta(seconds=dispatcher.MODEL_START_SECONDS + 1)
        times = iter([NOW] + [late] * 10)
        store = _Store()
        counts, ran, _ = self._run(store, "live", jobs=frozenset({"holdings_explainer", "export_packs"}),
                                   clock=lambda: next(times))
        self.assertEqual(sorted(ran), [("export_packs", "U1"), ("export_packs", "U2")])
        self.assertEqual(counts["left_for_next_run"], 2, "the two explainer runs wait for the next run")
        self.assertEqual(counts["recorded"], 5, "recording alone still fits")

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


class ReportingJobTests(unittest.TestCase):
    def test_the_month_end_draft_runs_the_unchanged_job_for_that_account(self):
        class Agent:
            calls = []

            def run(self, **kwargs):
                self.calls.append(kwargs)
                yield {"type": "answer", "turn": Turn(answer="October rose 2% [T1].", citations=[],
                                                      usage={"input_tokens": 50, "output_tokens": 9})}

        store, agent = _Store(), Agent()
        event = dispatcher.Event(dispatcher.BY_JOB["month_end_drafter"], "U1", "2026-10-31", "2026-10")
        written, usage = dispatcher._run_job(store, lambda job: _Client(job, ["U1", "U2"]), lambda: agent, event)
        self.assertEqual((written, usage), (1, {"input_tokens": 50, "output_tokens": 9}))
        self.assertEqual([k[:2] for k in store.inbox], [("month_end_draft", "U1")])
        self.assertEqual([c["account_id"] for c in agent.calls], ["U1"])

    def test_export_packs_run_for_that_account_without_a_model(self):
        class Store(_Store):
            def active_export_packs(self):
                return [{"pack_id": "p1", "subject_id": "U1", "schedule": "monthly", "name": "Board pack",
                         "created_at": "2026-09-15", "user_object_id": "u"},
                        {"pack_id": "p2", "subject_id": "U2", "schedule": "monthly", "name": "Other",
                         "created_at": "2026-09-15", "user_object_id": "u"}]

        def no_model():
            raise AssertionError("export packs never start a model")

        fresh = {"U1": {"latest_valuation_date": "2026-11-02", "latest_holdings_date": "2026-11-02"}}

        class Client(_Client):
            def call(self, name, arguments):
                if name in ("get_settled_dates", "get_data_freshness"):
                    return {"data": dict(fresh[arguments["account_id"]])}
                return super().call(name, arguments)

        store = Store()
        event = dispatcher.Event(dispatcher.BY_JOB["export_packs"], "U1", "2026-11-02", "2026-11-02")
        written, usage = dispatcher._run_job(store, lambda job: Client(job, ["U1"]), no_model, event)
        self.assertEqual((written, usage), (1, {}))
        self.assertEqual([k[:2] for k in store.inbox], [("export_pack", "U1")])


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
        self.assertEqual(events["by_job"], {"export_packs": 2, "holdings_explainer": 2, "risk_watch": 2,
                                            "weekly_brief": 1})
        self.assertEqual(events["median_minutes_before_fixed_run"]["holdings_explainer"], 80)
        self.assertEqual(events["live"], {"done": 2, "shadow": 5})
        self.assertEqual(events["written"], 2)
        self.assertEqual(events["layer"], {"silver": 7})
        text = report.render(summary, "2026-11-17", "2026-11-24")
        self.assertIn("Events: new data would have woken 7 job run(s)", text)
        self.assertIn("Woken on: silver 7.", text)
        self.assertIn("2 inbox card(s) written", text)

    def test_the_month_end_teams_runs_are_counted_apart_from_chat(self):
        run = {"mode": "watch", "source": "job", "status": "done", "usage": {"input_tokens": 900, "output_tokens": 100},
               "detail": {"job": "month_team", "stages": {"words": 2, "data": 1}}}
        summary = report.summarise([run, {**run, "status": "failed", "detail": {"job": "month_team", "stages": {}}}])
        self.assertEqual(summary["requests"], 0)
        self.assertEqual(summary["teams"], {"runs": 2, "by_job": {"month_team": 2}, "outcomes": {"done": 1, "failed": 1},
                                            "stages": {"words": 2, "data": 1}, "tokens": 2000})
        self.assertIn("Month-end team runs: 2, done 1, failed 1; accounts by stage: data 1, words 2; 2000 tokens.",
                      report.render(summary, "2026-11-01", "2026-11-08"))


if __name__ == "__main__":
    unittest.main()
