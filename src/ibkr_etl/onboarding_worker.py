"""The onboarding worker: the platform's part of every open onboarding case, without a backend run.

Run by the ibkrv2stg-onboard-worker job every 20 minutes and when the website asks (Run now on a
case, Check now on a Kiwoom connection; both write a 'requested' row in onboarding_worker_runs and
start the job). Each run does what the open cases are waiting for, in this order:

1. ``kiwoom``: a Kiwoom case's load, stage 2 checks and past-day history, through the daily
   collector's own code for that case only (kiwoom_collector.run with case_ids), every page stored
   in bronze first. A new Kiwoom connection still waiting for its first check is checked too, so
   its accounts appear on the onboarding page within minutes instead of the next morning.
2. ``reconcile``: an IBKR case's stage 2 checks from the reconciliation agent's newest result.
3. ``classify``: English names and sectors (Yahoo) for the case's holdings, once the owner has
   approved sending its ticker symbols and its history is built.
4. ``render``: the historical reports from the submitted design (designed_reports), as the
   month-end job renders them, recorded as the case's reports_rendered check.
5. ``review``: the agent review of those reports, recorded as agent_review_complete.

Each step is the same function the daily and month-end jobs call, so a case moves as it would have
overnight, only sooner. Nothing here approves anything: the owner's three approvals stay on the
case page.

Timing. Nothing runs during the daily chain (07:20-09:45 UTC), whose jobs write the same tables;
a request made then waits for the first run after it. Kiwoom's past days are asked for only outside
its rebuild hours (16:00-18:00 and 19:00-22:00 UTC), when its daily balance answers with empty
figures; the day's own load still runs then.

How often. A scheduled run calls Kiwoom for a case only while its stage still needs Kiwoom (the
first load and checks, then the history fill); once the history is built the daily job keeps it
fresh. Names and sectors are looked up at most once a day per case and reports rendered at most
every six hours, unless the owner presses Run now.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

JOB_NAME = "ibkrv2stg-onboard-worker"
SCHEDULE_MINUTES = 20
DAILY_CHAIN_UTC = (time(7, 20), time(9, 45))
KIWOOM_REBUILD_UTC = ((time(16, 0), time(18, 0)), (time(19, 0), time(22, 0)))
CLASSIFY_EVERY = timedelta(hours=24)
RENDER_EVERY = timedelta(hours=6)
# A run still marked running after this long died without saying so.
STALE_RUN = timedelta(hours=2)
OPEN_STAGES = ("request", "load_and_reconcile", "build_history")
STEPS = ("kiwoom", "reconcile", "classify", "render", "review")
STEP_LABELS = {
    "kiwoom": "Kiwoom load and history",
    "check_connection": "Kiwoom connection check",
    "reconcile": "IBKR reconciliation",
    "classify": "Names and sectors",
    "render": "Historical reports",
    "review": "Agent review",
    "design": "Built-in factsheet design",
}


def _between(moment: time, start: time, end: time) -> bool:
    return start <= moment < end


def windows(now: datetime) -> dict[str, bool]:
    """Whether ``now`` falls in the daily chain or in Kiwoom's rebuild hours (UTC)."""

    moment = now.astimezone(timezone.utc).time()
    return {
        "daily_chain": _between(moment, *DAILY_CHAIN_UTC),
        "kiwoom_rebuild": any(_between(moment, start, end) for start, end in KIWOOM_REBUILD_UTC),
    }


def window_note(now: datetime) -> str | None:
    """What the case page says about the time, or None when the worker runs as usual."""

    found = windows(now)
    if found["daily_chain"]:
        return ("The daily run is updating every account until 09:45 UTC; the worker starts again right after it.")
    if found["kiwoom_rebuild"]:
        return ("Kiwoom is rebuilding its figures (16:00-18:00 and 19:00-22:00 UTC), so past days are asked "
                "for once it has finished. Everything else runs as usual.")
    return None


def next_scheduled(now: datetime) -> datetime:
    """The next scheduled run, at the next multiple of 20 minutes after the daily chain if inside it."""

    moment = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    minutes = SCHEDULE_MINUTES - moment.minute % SCHEDULE_MINUTES
    candidate = moment + timedelta(minutes=minutes)
    while windows(candidate)["daily_chain"]:
        candidate += timedelta(minutes=SCHEDULE_MINUTES)
    return candidate


@dataclass(frozen=True)
class Case:
    case_id: str
    source_kind: str
    broker: str | None
    stage: str
    subject_id: str | None
    history_start_date: str
    checks: Mapping[str, str]
    yahoo_approved: bool
    last_steps: Mapping[str, datetime] = field(default_factory=dict)
    requested: bool = False


def plan(case: Case, *, now: datetime) -> list[str]:
    """The steps this run does for one case, in order (see the module's docstring)."""

    if windows(now)["daily_chain"] or case.stage not in OPEN_STAGES:
        return []
    checks, steps = case.checks, []
    history_done = checks.get("history_built") in ("pass", "warn")
    if case.broker == "kiwoom_rest":
        needs_kiwoom = case.stage in ("request", "load_and_reconcile") or (
            case.stage == "build_history" and not history_done)
        history_only = case.stage == "build_history"
        if case.requested or needs_kiwoom:
            if not (history_only and windows(now)["kiwoom_rebuild"]):
                steps.append("kiwoom")
    if case.broker == "ibkr_flex" and case.stage == "load_and_reconcile":
        steps.append("reconcile")
    built = case.stage == "build_history" and history_done and case.subject_id
    if built and case.source_kind == "broker_account" and case.yahoo_approved:
        if case.requested or _due(case, "classify", CLASSIFY_EVERY, now):
            steps.append("classify")
    if built:
        if case.requested or checks.get("reports_rendered") != "pass" or _due(case, "render", RENDER_EVERY, now):
            steps.append("render")
    if case.stage == "build_history" and checks.get("reports_rendered") == "pass" \
            and checks.get("agent_review_complete") != "pass":
        steps.append("review")
    return steps


def _due(case: Case, step: str, every: timedelta, now: datetime) -> bool:
    last = case.last_steps.get(step)
    return last is None or now - last >= every


# Reading and writing the run records -------------------------------------------------------------


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _moment(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def open_cases(connection, tenant_id: str, *, requested: Iterable[str] = ()) -> list[Case]:
    """Every case before go-live, with its checks, Yahoo consent and when each step last ran."""

    wanted = set(requested)
    rows = connection.execute(
        "SELECT case_id, source_kind, broker, stage, subject_id, history_start_date FROM broker_onboarding_cases "
        "WHERE tenant_id = ? AND status <> 'cancelled' AND stage IN ('request', 'load_and_reconcile', "
        "'build_history') ORDER BY created_at",
        (tenant_id,),
    ).fetchall()
    found = []
    for row in rows:
        case_id = str(row["case_id"])
        checks = {str(item["check_id"]): str(item["status"]) for item in connection.execute(
            "SELECT check_id, status FROM broker_onboarding_checks WHERE tenant_id = ? AND case_id = ?",
            (tenant_id, case_id)).fetchall()}
        approved = bool(row["subject_id"]) and connection.execute(
            "SELECT 1 FROM account_onboarding_approvals WHERE tenant_id = ? AND subject_id = ? "
            "AND approval_type = 'external_market_data' AND approval_status = 'approved' AND revoked_at IS NULL "
            "LIMIT 1", (tenant_id, row["subject_id"])).fetchone() is not None
        found.append(Case(
            case_id=case_id, source_kind=str(row["source_kind"]), broker=row["broker"], stage=str(row["stage"]),
            subject_id=str(row["subject_id"]) if row["subject_id"] else None,
            history_start_date=str(row["history_start_date"])[:10], checks=checks, yahoo_approved=approved,
            last_steps=last_steps(connection, tenant_id, case_id), requested=case_id in wanted))
    return found


def last_steps(connection, tenant_id: str, case_id: str) -> dict[str, datetime]:
    """When each step last finished without failing for this case, from the recent run records."""

    rows = connection.execute(
        "SELECT status, steps_json, finished_at FROM onboarding_worker_runs WHERE tenant_id = ? "
        "AND finished_at IS NOT NULL AND steps_json::text LIKE ? ORDER BY finished_at DESC LIMIT 40",
        (tenant_id, f"%{case_id}%"),
    ).fetchall()
    return last_steps_from(rows, case_id)


def last_steps_from(rows: Iterable[Mapping[str, Any]], case_id: str) -> dict[str, datetime]:
    """From run records, newest first: when each step last finished without failing for the case."""

    found: dict[str, datetime] = {}
    for row in rows:
        finished = _moment(row["finished_at"])
        if finished is None:
            continue
        for item in _json(row["steps_json"], []):
            if item.get("case_id") == case_id and item.get("outcome") in ("done", "unchanged"):
                found.setdefault(str(item.get("step")), finished)
    return found


def reap_stale(connection, tenant_id: str, *, now: datetime) -> int:
    """Mark runs that died while running as failed, so a new request is not blocked behind them."""

    rows = connection.execute(
        "UPDATE onboarding_worker_runs SET status = 'failed', finished_at = ?, "
        "message = 'The worker stopped before finishing this run; the next run starts again.' "
        "WHERE tenant_id = ? AND status = 'running' AND started_at < ? RETURNING run_id",
        (now, tenant_id, now - STALE_RUN),
    ).fetchall()
    return len(rows)


def claim_requests(connection, tenant_id: str, *, now: datetime) -> list[dict]:
    """Take every waiting request (Run now, Check now) for this run."""

    rows = connection.execute(
        "UPDATE onboarding_worker_runs SET status = 'running', started_at = ? "
        "WHERE tenant_id = ? AND status = 'requested' "
        "RETURNING run_id, case_id, connection_id, trigger, steps_json",
        (now, tenant_id),
    ).fetchall()
    # A request may carry what the owner asked for besides the usual steps (a built-in design).
    return [{"run_id": str(row["run_id"]), "case_id": str(row["case_id"]) if row["case_id"] else None,
             "connection_id": str(row["connection_id"]) if row["connection_id"] else None,
             "trigger": str(row["trigger"]),
             "asks": [item for item in _json(row["steps_json"], []) if isinstance(item, Mapping) and item.get("ask")]}
            for row in rows]


def unchecked_kiwoom_connections(connection, tenant_id: str) -> list[str]:
    """Kiwoom connections saved on the website that no run has checked yet."""

    rows = connection.execute(
        "SELECT connection_id FROM broker_connections WHERE tenant_id = ? AND broker = 'kiwoom_rest' "
        "AND status = 'pending_check' ORDER BY created_at",
        (tenant_id,),
    ).fetchall()
    return [str(row["connection_id"]) for row in rows]


def _message(steps: Sequence[Mapping[str, Any]]) -> str:
    if not steps:
        return "Nothing was waiting on the platform."
    failed = [item for item in steps if item.get("outcome") == "failed"]
    done = [item for item in steps if item.get("outcome") != "failed"]
    parts = []
    if done:
        parts.append("Done: " + ", ".join(dict.fromkeys(STEP_LABELS.get(str(i["step"]), str(i["step"])) for i in done)))
    if failed:
        parts.append("Failed: " + "; ".join(
            f"{STEP_LABELS.get(str(i['step']), i['step'])} ({str(i.get('detail') or '')[:200]})" for i in failed))
    return ". ".join(parts)[:1000] + "."


def _finish_one(connection, tenant_id: str, run_id: str, steps: Sequence[Mapping[str, Any]], *,
                now: datetime) -> None:
    status = "failed" if any(item.get("outcome") == "failed" for item in steps) else "done"
    connection.execute(
        "UPDATE onboarding_worker_runs SET status = ?, finished_at = ?, message = ?, steps_json = ?::jsonb "
        "WHERE tenant_id = ? AND run_id = ?",
        (status, now, _message(steps), json.dumps(list(steps), default=str), tenant_id, run_id),
    )


# Running ------------------------------------------------------------------------------------------


@dataclass
class Steps:
    """What each step calls; the script gives the real ones and tests give fakes.

    kiwoom(case_ids, connection_ids, history) -> list of the collector's per-account results
    reconcile() -> list of per-case results; design(case_id, design_id) -> what was submitted;
    classify(case) -> summary; render(case_ids) -> list of
    per-case results; review() -> list of per-case results.
    """

    kiwoom: Callable[[set[str], set[str], bool], list[dict]] | None = None
    # design(case_id, design_id) -> what builtin_design_submission did; only when the owner asked.
    design: Callable[[str, str], dict] | None = None
    reconcile: Callable[[], list[dict]] | None = None
    classify: Callable[[Case], dict] | None = None
    render: Callable[[set[str]], list[dict]] | None = None
    review: Callable[[], list[dict]] | None = None


def _outcome(results: Iterable[Mapping[str, Any]], case_id: str | None) -> tuple[str, str]:
    mine = [item for item in results if case_id is None or str(item.get("case_id")) == case_id]
    if not mine:
        return "unchanged", "nothing to record"
    errors = [str(item.get("error")) for item in mine if item.get("error") or item.get("status") == "failed"]
    if errors:
        return "failed", errors[0][:300]
    recorded = [item for item in mine if item.get("recorded") or item.get("status") in ("loaded", "checked")]
    return ("done" if recorded else "unchanged"), ", ".join(
        str(item.get("check") or item.get("status") or "") for item in mine if item.get("check") or item.get("status"))[:300]


def run_worker(connection, tenant_id: str, *, now: datetime, steps: Steps, trigger: str = "schedule") -> dict:
    """One worker run: claim requests, do each open case's due steps, write the run records."""

    if windows(now)["daily_chain"]:
        # Requests stay waiting; the first run after 09:45 UTC takes them.
        return {"status": "skipped", "reason": "daily chain", "next": next_scheduled(now).isoformat()}
    reaped = reap_stale(connection, tenant_id, now=now)
    requests = claim_requests(connection, tenant_id, now=now)
    connection.commit()
    requested_cases = {item["case_id"] for item in requests if item["case_id"]}
    connections = {item["connection_id"] for item in requests if item["trigger"] == "check_connection"}
    connections |= set(unchecked_kiwoom_connections(connection, tenant_id))
    cases = open_cases(connection, tenant_id, requested=requested_cases)
    connection.rollback()
    planned = {case.case_id: plan(case, now=now) for case in cases}
    done: list[dict[str, Any]] = []

    def record(step: str, case_id: str | None, outcome: str, detail: str = "", **extra: Any) -> None:
        done.append({"step": step, "case_id": case_id, "outcome": outcome, "detail": detail,
                     "at": now.isoformat(), **extra})

    def attempt(step: str, call: Callable[[], Any], case_ids: Sequence[str | None]) -> Any:
        try:
            results = call()
        except Exception as error:  # noqa: BLE001 - one step's failure never stops the others
            connection.rollback()
            for case_id in case_ids:
                record(step, case_id, "failed", f"{type(error).__name__}: {str(error)[:250]}")
            return None
        return results

    kiwoom_cases = {case_id for case_id, todo in planned.items() if "kiwoom" in todo}
    if (kiwoom_cases or connections) and steps.kiwoom is not None:
        history = not windows(now)["kiwoom_rebuild"]
        results = attempt("kiwoom", lambda: steps.kiwoom(kiwoom_cases, connections, history),
                          sorted(kiwoom_cases) or [None])
        if results is not None:
            for case_id in sorted(kiwoom_cases):
                outcome, detail = _outcome(results, case_id)
                mine = next((item for item in results if str(item.get("case_id")) == case_id), {})
                record("kiwoom", case_id, outcome, detail, history=(mine.get("history") or {}).get("check"))
            for connection_id in sorted(connections):
                mine = [item for item in results if str(item.get("connection_id")) == connection_id
                        and str(item.get("case_id")) not in kiwoom_cases]
                if mine:
                    outcome, detail = _outcome(mine, None)
                    record("check_connection", None, outcome, detail, connection_id=connection_id)
    if any("reconcile" in todo for todo in planned.values()) and steps.reconcile is not None:
        results = attempt("reconcile", steps.reconcile, [None])
        if results is not None:
            for case_id, todo in planned.items():
                if "reconcile" in todo:
                    record("reconcile", case_id, *_outcome(results, case_id))
    for case in cases:
        if "classify" in planned[case.case_id] and steps.classify is not None:
            summary = attempt("classify", lambda case=case: steps.classify(case), [case.case_id])
            if summary is not None:
                record("classify", case.case_id, "done",
                       f"{summary.get('classified', 0)} looked up, {summary.get('unclassified', 0)} not found")
    for request in requests:
        for ask in request["asks"]:
            if ask.get("ask") != "built_in_design" or not request["case_id"] or steps.design is None:
                continue
            case_id, design_id = request["case_id"], str(ask.get("design_id") or "")
            submitted = attempt("design", lambda: steps.design(case_id, design_id), [case_id])
            if submitted is not None:
                record("design", case_id, "done" if submitted.get("status") == "submitted" else "unchanged",
                       f"{design_id}: {submitted.get('status')}")
                if case_id in planned and "render" not in planned[case_id] and \
                        next((c for c in cases if c.case_id == case_id and c.checks.get("history_built")
                              in ("pass", "warn")), None):
                    planned[case_id].append("render")
    render_cases = {case_id for case_id, todo in planned.items() if "render" in todo}
    if render_cases and steps.render is not None:
        results = attempt("render", lambda: steps.render(render_cases), sorted(render_cases))
        if results is not None:
            for case_id in sorted(render_cases):
                record("render", case_id, *_outcome(results, case_id))
    if any("review" in todo for todo in planned.values()) and steps.review is not None:
        results = attempt("review", steps.review, [None])
        if results is not None:
            for case_id, todo in planned.items():
                if "review" in todo:
                    record("review", case_id, *_outcome(results, case_id))

    covered: set[int] = set()
    for request in requests:
        if request["trigger"] == "check_connection":
            own = [i for i, item in enumerate(done) if item.get("connection_id") == request["connection_id"]
                   or (item["step"] == "kiwoom" and item.get("case_id") is None)]
        else:
            own = [i for i, item in enumerate(done) if item.get("case_id") in (request["case_id"], None)
                   and item["step"] != "check_connection"]
        covered.update(own)
        _finish_one(connection, tenant_id, request["run_id"], [done[i] for i in own], now=now)
    leftover = [item for i, item in enumerate(done) if i not in covered]
    if leftover:
        # What a scheduled run did keeps its own record, so the case page shows it.
        connection.execute(
            "INSERT INTO onboarding_worker_runs (run_id, tenant_id, trigger, status, started_at, finished_at, "
            "message, steps_json) VALUES (?, ?, 'schedule', ?, ?, ?, ?, ?::jsonb)",
            (str(uuid.uuid4()), tenant_id, "failed" if any(i["outcome"] == "failed" for i in leftover) else "done",
             now, now, _message(leftover), json.dumps(leftover, default=str)),
        )
    connection.commit()
    return {"status": "ran", "trigger": trigger, "requests": len(requests), "reaped": reaped,
            "cases": {case_id: todo for case_id, todo in planned.items()}, "steps": done}
