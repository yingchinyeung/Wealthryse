"""The event dispatcher: scheduled agents wake when their data is in, not at a fixed time.

    python -m wealthryse_agent.supervisor.dispatcher      # every 5 minutes, when deployed

Each run asks the tool server, as each job and with that job's own accounts, what the job is
waiting for: a new holdings date (holdings explainer), a new valuation date (risk watch) or a
finished week (weekly brief). These come from the silver layer through the agent views, the same
reads the jobs make themselves. Code only; it never calls a model to decide.

``AGENT_DISPATCHER=watch`` records in ``agent.tasks`` (source 'event') which job it would have
started, for which account and data date, and how long before that job's fixed time. Nothing
runs. ``live`` also runs the jobs named in ``AGENT_DISPATCHER_JOBS`` for exactly that account,
through the same job code and access as their scheduled run; the scheduled jobs stay as a
backup, and whichever comes first writes the inbox card while the other finds it and skips.
Off (the default, and any other value) does nothing at all.

Held builds (the gate investigator) and finished month-end packages (the month-end team) join
once Medallion's contracted build view and the gate investigator's tools exist, and the month-end
team only after December's month-end, as decided.
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


@dataclass(frozen=True)
class Wake:
    job: str  # the scheduled job, and the job name the tool server knows it by
    event: str  # what wakes it
    inbox_kind: str  # the card it writes, so a card already there means nothing to do
    cron: str  # its fixed time today (UTC), from infra/agent-apps.bicep


WAKES = (
    Wake("holdings_explainer", "new_holdings_date", "holdings_explainer", "30 0 * * *"),
    Wake("risk_watch", "new_valuation_date", "risk_alert", "15 1 * * 1-5"),
    Wake("weekly_brief", "week_closed", "weekly_brief", "30 1 * * 6"),
)
BY_JOB = {wake.job: wake for wake in WAKES}


@dataclass(frozen=True)
class Event:
    wake: Wake
    account_id: str
    data_date: str  # the holdings or valuation date the job will work on
    period_key: str  # the job's own key for it (the date, or the ISO week)

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


def next_fixed_run(cron: str, after: datetime) -> datetime:
    """The next time a "M H * * D" schedule (D: *, one day or a range; 0 is Sunday) fires after ``after``."""
    minute, hour, _, _, dow = cron.split()
    if dow == "*":
        days = set(range(7))
    elif "-" in dow:
        first, last = (int(x) for x in dow.split("-"))
        days = set(range(first, last + 1))
    else:
        days = {int(dow)}
    candidate = after.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
    if candidate <= after:
        candidate += timedelta(days=1)
    while candidate.isoweekday() % 7 not in days:
        candidate += timedelta(days=1)
    return candidate


def _iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def pending(wake: Wake, account_id: str, freshness: Mapping[str, Any], *, today: date,
            inbox_exists: Callable[[str, str, str], bool]) -> Event | None:
    """What the job is waiting for on this account, if anything, from the account's freshness."""
    if wake.event == "new_valuation_date":
        data_date = freshness.get("latest_valuation_date")
    else:
        data_date = freshness.get("latest_holdings_date")
    if not data_date:
        return None
    day = date.fromisoformat(str(data_date))
    if wake.event == "week_closed":
        # The week is done once its Friday is in, or once a later week has started.
        if day.weekday() < 4 and today.isocalendar()[:2] <= day.isocalendar()[:2]:
            return None
        period = _iso_week(day)
    else:
        period = day.isoformat()
    # Risk alerts are keyed by their trigger, so only the dispatcher's own record says a date was handled.
    if wake.event != "new_valuation_date" and inbox_exists(wake.inbox_kind, account_id, period):
        return None
    return Event(wake, account_id, day.isoformat(), period)


def tree(event: Event, *, mode_: str, detected_at: datetime, status: str = "shadow", written: int | None = None,
         usage: Mapping[str, int] | None = None, error_code: str | None = None) -> list[dict[str, Any]]:
    """The rows for one event: the supervisor, the dispatcher that decided, and the job it woke."""
    member = registry.BY_NAME[registry.JOB_MEMBER[event.wake.job]]
    fixed = next_fixed_run(event.wake.cron, detected_at)
    root_id, lead_id = str(uuid.uuid4()), str(uuid.uuid4())
    common = {"root_id": root_id, "mode": mode_, "source": "event",
              "subject_id": event.account_id if len(event.account_id) <= 64 else None,
              "correlation_id": event.key, "conversation_id": None, "pinned_as_of": event.data_date}
    detail = {"event": event.wake.event, "job": event.wake.job, "layer": "silver", "period": event.period_key,
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
    """Every job's new events, each read as that job. ``clients(job)`` opens the job's tool client."""
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
                        freshness[account_id] = client.call("get_data_freshness", {"account_id": account_id})["data"]
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
        if (detected - started).total_seconds() > RUN_SECONDS:
            counts["left_for_next_run"] = counts.get("left_for_next_run", 0) + 1
            continue
        status, written, usage, error_code = "shadow", None, None, None
        if mode_ == "live" and event.wake.job in jobs:
            if not budget_left():
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

    job = {"holdings_explainer": jobs.holdings_explainer, "risk_watch": jobs.risk_watch,
           "weekly_brief": jobs.weekly_brief}[event.wake.job]
    usage: dict[str, int] = {}
    tools = tools_for(event.wake.job)
    try:
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
