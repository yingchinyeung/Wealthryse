"""The event dispatcher: scheduled agents wake when their data is in, not at a fixed time.

    python -m wealthryse_agent.supervisor.dispatcher      # every 5 minutes, when deployed

Each run asks the tool server, as each job and with that job's own accounts, what the job is
waiting for: a new holdings date (holdings explainer), a new valuation date (risk watch, export
packs), a finished week (weekly brief) or, on the 2nd to the 7th, a month whose data are final
(the month-end commentary job). The dates come from the job-only get_settled_dates, the same read
the jobs make themselves (settled_days.py):

- A Kiwoom account's newest sessions are provisional (a Korean day until the next trading day's
  official close, U.S. iStrategy's latest two sessions until they add up), so it wakes a job only
  for its latest settled date.
- Once Medallion's agent.certified_latest view is installed, a date wakes a job only when the
  account's newest published certified build covers it (layer "certified"); a held build wakes
  nothing and the job's fixed time stays the backup. Without the view, or for an account the
  build does not cover, the silver date is used (layer "silver").

Code only; it never calls a model to decide.

``AGENT_DISPATCHER=watch`` records in ``agent.tasks`` (source 'event') which job it would have
started, for which account and data date, and how long before that job's fixed time. Nothing
runs. ``live`` also runs the jobs named in ``AGENT_DISPATCHER_JOBS`` for exactly that account,
through the same job code and access as their scheduled run; the scheduled jobs stay as a
backup, and whichever comes first writes the inbox card while the other finds it and skips.
Off (the default, and any other value) does nothing at all.

Held builds (the gate investigator) join once the gate investigator's tools exist, and the
month-end team after December's month-end, as decided; until then it runs on its own schedule,
with its runs in the task record (team_record.py).
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from .. import settled_days
from . import registry

LOGGER = logging.getLogger("wealthryse_agent.supervisor.dispatcher")
SETTING = "AGENT_DISPATCHER"
JOBS_SETTING = "AGENT_DISPATCHER_JOBS"
MODES = ("off", "watch", "live")
# How far back a run looks for its own earlier records, so one event is recorded and run once.
SEEN_DAYS = 10
# A run starts no new event after this long, so it ends before the next run starts; what it leaves
# is not recorded yet, so the next run finds it again.
RUN_SECONDS = 240
# A job that calls a model takes a minute or two, so in live mode none starts after this long.
MODEL_START_SECONDS = 120
# The job's own limit (replicaTimeout in infra/agent-apps.bicep). A run that is still going, say on a slow
# model answer, makes the next one skip, so the same event is never run twice at once.
JOB_TIMEOUT_MINUTES = 15


@dataclass(frozen=True)
class Wake:
    job: str  # the scheduled job, and the job name the tool server knows it by
    event: str  # what wakes it
    inbox_kind: str | None  # the card it writes, keyed by the event's period; None: keyed otherwise
    cron: str  # its fixed time today (UTC), from infra/agent-apps.bicep
    model: bool = True  # whether a run calls a model (and so needs today's token budget)


WAKES = (
    Wake("holdings_explainer", "new_holdings_date", "holdings_explainer", "30 0 * * *"),
    Wake("risk_watch", "new_valuation_date", None, "15 1 * * 1-5"),
    Wake("weekly_brief", "week_closed", "weekly_brief", "30 1 * * 6"),
    # Reporting: a month whose data are final starts that account's month-end commentary draft, and a new
    # valuation date lets saved export packs whose period has closed post their card (no model).
    Wake("month_end_drafter", "month_final", "month_end_draft", "0 2 2-7 * *"),
    Wake("export_packs", "new_valuation_date", None, "45 1 * * *", model=False),
)
BY_JOB = {wake.job: wake for wake in WAKES}


@dataclass(frozen=True)
class Event:
    wake: Wake
    account_id: str
    data_date: str  # the holdings or valuation date the job will work on (a month's last day for month-end)
    period_key: str  # the job's own key for it (the date, the ISO week or the month)
    layer: str = "silver"  # silver, or certified when the account's published build covers the date
    build_id: str | None = None

    @property
    def key(self) -> str:
        """The event's id in agent.tasks (correlation_id, at most 64 characters)."""
        raw = f"event:{self.wake.job}:{self.account_id}:{self.period_key}"
        return raw if len(raw) <= 64 else "event:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def mode(environ: Mapping[str, str] | None = None) -> str:
    """off, watch or live. Anything unrecognised is off."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(SETTING, "")).strip().lower()
    return value if value in MODES else "off"


def live_jobs(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """The jobs the dispatcher may run in live mode. Empty (the default) runs none."""
    environ = os.environ if environ is None else environ
    named = {part.strip() for part in str(environ.get(JOBS_SETTING, "")).split(",") if part.strip()}
    return frozenset(named & set(BY_JOB))


def _days(field: str, *, weekday: bool = False) -> set[int] | None:
    """The values a cron day field allows (ranges and lists); None for "*"."""
    if field == "*":
        return None
    values: set[int] = set()
    for part in field.split(","):
        first, _, last = part.partition("-")
        values |= set(range(int(first), int(last or first) + 1))
    return {v % 7 for v in values} if weekday else values


def next_fixed_run(cron: str, after: datetime) -> datetime:
    """The next time a "M H DOM * DOW" schedule (one minute and hour; 0 is Sunday) fires after ``after``."""
    minute, hour, dom, _, dow = cron.split()
    month_days, week_days = _days(dom), _days(dow, weekday=True)
    candidate = after.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    for _ in range(400):
        in_month = month_days is None or candidate.day in month_days
        in_week = week_days is None or candidate.isoweekday() % 7 in week_days
        # As cron: when both day fields are restricted, either one matching is enough.
        if (in_month or in_week) if (month_days is not None and week_days is not None) else (in_month and in_week):
            return candidate
        candidate += timedelta(days=1)
    raise ValueError(f"no run time in a year for {cron!r}")


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def pending(wake: Wake, account_id: str, freshness: Mapping[str, Any], *, today: date,
            inbox_exists: Callable[[str, str, str], bool]) -> Event | None:
    """What the job is waiting for on this account, if anything, from the account's settled dates."""
    certified = freshness.get("certified") or {}
    if wake.event == "month_final":
        if today.day not in settled_days.MONTH_END_DAYS:
            return None
        period_end = settled_days.month_end(today)
        period = period_end.strftime("%Y-%m")
        if not settled_days.month_final(freshness, period_end) or inbox_exists(wake.inbox_kind, account_id, period):
            return None
        layer = _layer(certified, settled_days.last_business_day(period_end).isoformat())
        if layer is None:
            return None
        return Event(wake, account_id, period_end.isoformat(), period, *layer)
    key = "latest_valuation_date" if wake.event == "new_valuation_date" else "latest_holdings_date"
    data_date = settled_days.settled(freshness, key)
    if not data_date:
        return None
    layer = _layer(certified, data_date)
    if layer is None:
        return None
    day = date.fromisoformat(str(data_date))
    if wake.event == "week_closed":
        # The week is done once its Friday is in, or once a later week has started.
        if day.weekday() < 4 and today.isocalendar()[:2] <= day.isocalendar()[:2]:
            return None
        period = _iso_week(day)
    else:
        period = day.isoformat()
    # Risk alerts and export pack cards are keyed otherwise, so only the dispatcher's own record says a date
    # was handled.
    if wake.inbox_kind and inbox_exists(wake.inbox_kind, account_id, period):
        return None
    return Event(wake, account_id, day.isoformat(), period, *layer)


def _layer(certified: Mapping[str, Any], data_date: str) -> tuple[str, str | None] | None:
    """Which layer an event wakes on: certified when the account's newest published build covers the date,
    silver while Medallion's view is not there or has no build for the account, None to wait for the build."""
    as_of = certified.get("as_of_date")
    if not as_of:
        return "silver", None
    if str(as_of)[:10] < data_date:
        return None  # the account's newest published build does not cover the date yet
    build_id = certified.get("build_id")
    return "certified", str(build_id) if build_id else None


def tree(event: Event, *, mode_: str, detected_at: datetime, status: str = "shadow", written: int | None = None,
         usage: Mapping[str, int] | None = None, error_code: str | None = None) -> list[dict[str, Any]]:
    """The rows for one event: the supervisor, the dispatcher that decided, and the job it woke."""
    member = registry.BY_NAME[registry.JOB_MEMBER[event.wake.job]]
    fixed = next_fixed_run(event.wake.cron, detected_at)
    root_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    common = {"root_id": root_id, "mode": mode_, "source": "event",
              "subject_id": event.account_id if len(event.account_id) <= 64 else None,
              "correlation_id": event.key, "conversation_id": None, "pinned_as_of": event.data_date,
              "build_ids": [event.build_id] if event.build_id else []}
    detail = {"event": event.wake.event, "job": event.wake.job, "layer": event.layer, "period": event.period_key,
              "detected_at": detected_at.isoformat(timespec="seconds"),
              "fixed_run_at": fixed.isoformat(timespec="minutes"),
              "minutes_before_fixed_run": int((fixed - detected_at).total_seconds() // 60),
              **({"written": written} if written is not None else {}),
              **({"error_code": error_code} if error_code else {})}
    rows = [
        {**common, "task_id": root_id, "parent_id": None, "level": "supervisor", "team": None, "role": "supervisor",
         "autonomy": member.autonomy, "status": status, "usage": dict(usage or {}), "detail": detail},
        {**common, "task_id": lead_id, "parent_id": root_id, "level": "lead", "team": "operations",
         "role": "event_dispatcher", "autonomy": "answer", "status": "done" if status != "failed" else "failed"},
        {**common, "task_id": str(uuid.uuid4()), "parent_id": lead_id, "level": "specialist", "team": member.team,
         "role": member.name, "autonomy": member.autonomy, "status": status, "usage": dict(usage or {})},
    ]
    return rows


def find_events(clients: Callable[[str], Any], *, today: date, inbox_exists: Callable[[str, str, str], bool],
                seen: Iterable[str]) -> list[Event]:
    """Every job's new events, each read as that job. ``clients(job)`` opens the job's tool client.

    Each account's dates are read once, by the first job that has it.
    """
    seen = set(seen)
    found: list[Event] = []
    freshness: dict[str, Mapping[str, Any]] = {}
    for wake in WAKES:
        client = clients(wake.job)
        try:
            accounts = [a["account_id"] for a in client.call("list_accounts", {})["data"]["accounts"]]
            for account_id in accounts:
                if account_id not in freshness:
                    try:
                        freshness[account_id] = settled_days.read_dates(client.call, account_id)
                    except Exception:
                        LOGGER.warning("dispatcher could not read freshness for one account")
                        continue
                event = pending(wake, account_id, freshness[account_id], today=today, inbox_exists=inbox_exists)
                if event is not None and event.key not in seen:
                    found.append(event)
        except Exception:
            LOGGER.warning("dispatcher could not read %s's accounts", wake.job)
        finally:
            try:
                client.close()
            except Exception:
                pass
    return found


def dispatch(store: Any, clients: Callable[[str], Any], *, mode_: str, jobs: frozenset[str],
             run_job: Callable[[Event], tuple[int, dict[str, int]]], now: Callable[[], datetime],
             budget_left: Callable[[], bool] = lambda: True) -> dict[str, int]:
    """One dispatcher run: find the new events, record each, and in live mode run the named jobs.

    Never raises for one event; returns counts by outcome.
    """
    counts = {"events": 0, "recorded": 0, "ran": 0, "failed": 0, "skipped_budget": 0}
    if mode_ == "off" or not store.tasks_ready():
        return counts
    started = now()
    events = find_events(clients, today=started.date(), inbox_exists=store.inbox_exists,
                         seen=store.event_keys(SEEN_DAYS))
    counts["events"] = len(events)
    for event in events:
        detected = now()
        runs_model = mode_ == "live" and event.wake.job in jobs and event.wake.model
        if (detected - started).total_seconds() > (MODEL_START_SECONDS if runs_model else RUN_SECONDS):
            counts["left_for_next_run"] = counts.get("left_for_next_run", 0) + 1
            continue
        status, written, usage, error_code = "shadow", None, None, None
        if mode_ == "live" and event.wake.job in jobs:
            if event.wake.model and not budget_left():
                counts["skipped_budget"] += 1
                status, error_code = "blocked", "budget_exhausted"
            else:
                try:
                    written, usage = run_job(event)
                    status = "done"
                    counts["ran"] += 1
                except Exception as error:
                    status, error_code = "failed", str(getattr(error, "code", None) or type(error).__name__)[:40]
                    counts["failed"] += 1
                    LOGGER.warning("dispatcher run of %s failed: %s", event.wake.job, error_code)
        try:
            if store.add_tasks(tree(event, mode_=mode_, detected_at=detected, status=status, written=written,
                                    usage=usage, error_code=error_code)):
                counts["recorded"] += 1
        except Exception:
            LOGGER.warning("dispatcher could not record an event", exc_info=True)
    return counts


def _run_job(store: Any, tools_for: Callable[[str], Any], agent_factory: Callable[[], Any],
             event: Event) -> tuple[int, dict[str, int]]:
    """Run one job for one account, exactly as its scheduled run would, as that job."""
    from .. import jobs

    usage: dict[str, int] = {}
    tools = tools_for(event.wake.job)
    try:
        if event.wake.job == "export_packs":
            written = jobs.export_packs(store, tools, [event.account_id])
        else:
            job = {"holdings_explainer": jobs.holdings_explainer, "risk_watch": jobs.risk_watch,
                   "weekly_brief": jobs.weekly_brief, "month_end_drafter": jobs.month_end_drafter}[event.wake.job]
            written = job(store, tools, agent_factory(), [event.account_id], usage)
    finally:
        tools.close()
    return written, usage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    current = mode()
    if current == "off":
        LOGGER.info("dispatcher is off")
        return 0
    from ..auth import TokenSource
    from ..config import DatabaseSettings, ModelSettings, RuntimeSettings
    from ..database import connect
    from ..runtime import Agent
    from ..store import Store
    from ..tool_client import ToolClient

    db, runtime = DatabaseSettings.from_env(), RuntimeSettings.from_env()
    correlation_id = str(uuid.uuid4())
    tokens = TokenSource(runtime.managed_identity_client_id, runtime.tool_server_audience)

    def tools_for(job: str) -> ToolClient:
        return ToolClient(runtime.tool_server_url, tokens, job=job, correlation_id=correlation_id)

    store = Store(connect(db, search_path="agent"), db.tenant_id)
    if store.run_in_progress("event_dispatcher", JOB_TIMEOUT_MINUTES):
        LOGGER.info("the last dispatcher run is still going; skipping this one")
        return 0
    run_id = store.start_run("event_dispatcher", correlation_id)
    usage: dict[str, int] = {}

    def run_job(event: Event) -> tuple[int, dict[str, int]]:
        written, used = _run_job(store, tools_for, lambda: Agent(ModelSettings.from_env()), event)
        for key, value in used.items():
            usage[key] = usage.get(key, 0) + int(value)
        return written, used

    try:
        probe = tools_for(WAKES[0].job)
        try:
            probe.wait_until_ready()
        finally:
            probe.close()
        counts = dispatch(store, tools_for, mode_=current, jobs=live_jobs(), run_job=run_job,
                          now=lambda: datetime.now(timezone.utc),
                          budget_left=lambda: store.tokens_used_today() < runtime.daily_token_budget)
        store.finish_run(run_id, status="succeeded", items=counts["recorded"], usage=usage)
        LOGGER.info("dispatcher (%s): %s", current, counts)
        return 0
    except Exception:
        store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="internal_error")
        LOGGER.exception("dispatcher failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
