"""Scheduled agents. They write only to the agent schema (the inbox and their own run
records); nothing is sent, published or released.

    python -m wealthryse_agent.jobs holdings_explainer   # daily, after the account sync
    python -m wealthryse_agent.jobs month_end_drafter    # 1st–7th of each month
    python -m wealthryse_agent.jobs data_quality         # daily; also applies chat retention
    python -m wealthryse_agent.jobs weekly_brief         # Saturdays: each account's weekly brief
    python -m wealthryse_agent.jobs live_market          # every 15 minutes in trading hours: alert on big
                                                         # live moves, with an explanation
    python -m wealthryse_agent.jobs month_team           # hourly, 2nd–7th: the month-end team
    python -m wealthryse_agent.jobs month_team --replay --from 2026-01-31 --to 2026-08-31
    python -m wealthryse_agent.jobs month_team --replay --round 2 --from 2026-01-31
                                                         # re-check months already released
    python -m wealthryse_agent.jobs onboarding           # hourly: draft new cases' terms from their documents,
                                                         # reconcile new broker accounts, and review new
                                                         # composites' historical reports, and watch
                                                         # newly live accounts until their first month-end
    python -m wealthryse_agent.jobs operations           # every 5 minutes: diagnose the autonomous
                                                         # administrator's open incidents from Azure telemetry

Accounts come from agent.service_grants for the job, enforced by the tool server. The
onboarding job has no accounts: its cases are not live yet, and it sees only the case views
its own tools give it. Neither has the operations job: it reads only incidents, run records and
Azure telemetry, never client data.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .auth import TokenSource
from .config import DatabaseSettings, ModelSettings, RuntimeSettings
from .database import connect
from .month_team.agents import BudgetExhausted
from .prompts import HOLDINGS_EXPLAINER, LIVE_ALERT, MONTH_END_DRAFTER, WEEKLY_BRIEF
from .runtime import Agent, AgentError
from .store import Store
from .tool_client import ToolCallFailed, ToolClient

LOGGER = logging.getLogger("wealthryse_agent.jobs")
STALE_VALUATION_DAYS = 4
STALE_BENCHMARK_DAYS = 5


def _run_agent(agent: Agent, tools: ToolClient, account_id: str, task: str, allowed: list[str]) -> tuple[str, list, dict, bool]:
    answer, citations, usage, ok = "", [], {}, False
    for event in agent.run(question=task, account_id=account_id, tools=tools, allowed_tools=allowed, skip_router=True):
        if event["type"] in ("answer", "rejected"):
            turn = event["turn"]
            answer, citations, usage, ok = turn.answer, turn.citations, turn.usage, event["type"] == "answer"
    return answer, citations, usage, ok


def _sum_usage(total: dict[str, int], usage: dict[str, int]) -> None:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + int(value)


def holdings_explainer(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict) -> int:
    written = 0
    for account_id in accounts:
        fresh = tools.call("get_data_freshness", {"account_id": account_id})["data"]
        end = fresh.get("latest_holdings_date")
        if not end or store.inbox_exists("holdings_explainer", account_id, end):
            continue
        start = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        task = HOLDINGS_EXPLAINER.format(account_id=account_id, from_date=start, to_date=end)
        answer, citations, used, ok = _run_agent(agent, tools, account_id, task, ["get_holdings_changes", "get_holdings"])
        _sum_usage(usage, used)
        if ok and store.add_inbox(kind="holdings_explainer", account_id=account_id, period_key=end,
                                  title=f"What changed in {account_id} on {end}", body=answer,
                                  citations=citations, run_id=None):
            written += 1
    return written


def month_end_drafter(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict,
                      today: date | None = None) -> int:
    today = today or date.today()
    month_end = today.replace(day=1) - timedelta(days=1)
    month_start = month_end.replace(day=1)
    period = month_end.strftime("%Y-%m")
    written = 0
    for account_id in accounts:
        if store.inbox_exists("month_end_draft", account_id, period):
            continue
        task = MONTH_END_DRAFTER.format(account_id=account_id, month_end=month_end.isoformat(),
                                        month_start=month_start.isoformat())
        answer, citations, used, ok = _run_agent(
            agent, tools, account_id, task,
            ["get_performance", "compare_to_benchmark", "get_holdings_changes", "get_sector_allocation", "get_cash_flows"])
        _sum_usage(usage, used)
        if ok and store.add_inbox(kind="month_end_draft", account_id=account_id, period_key=period,
                                  title=f"Draft commentary for {account_id}, {period}", body=answer,
                                  citations=citations, run_id=None):
            written += 1
    return written


def weekly_brief(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict) -> int:
    """One brief per account per week of data, in the website inbox for the people who can see the account."""
    written = 0
    for account_id in accounts:
        end = tools.call("get_data_freshness", {"account_id": account_id})["data"].get("latest_holdings_date")
        if not end:
            continue
        year, week, _ = date.fromisoformat(end).isocalendar()
        period = f"{year}-W{week:02d}"
        if store.inbox_exists("weekly_brief", account_id, period):
            continue
        start = (date.fromisoformat(end) - timedelta(days=7)).isoformat()
        task = WEEKLY_BRIEF.format(account_id=account_id, from_date=start, to_date=end)
        answer, citations, used, ok = _run_agent(
            agent, tools, account_id, task,
            ["get_performance", "compare_to_benchmark", "get_holdings_changes", "get_cash_flows", "get_data_freshness",
             "optimise_portfolio"])
        _sum_usage(usage, used)
        if ok and store.add_inbox(kind="weekly_brief", account_id=account_id, period_key=period,
                                  title=f"Your week to {end}", body=answer, citations=citations, run_id=None):
            written += 1
    return written


LIVE_ACCOUNT_MOVE = 0.02     # the account up or down 2% today
LIVE_POSITION_PNL = 0.0075   # one position adding or taking 0.75% of the account's value today
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def live_triggers(snapshot: dict[str, Any], account_move: float = LIVE_ACCOUNT_MOVE,
                  position_pnl: float = LIVE_POSITION_PNL) -> list[tuple[str, str, str]]:
    """(key, title, trigger in words) for each alert today's live snapshot warrants. Deterministic; no model."""
    if not snapshot.get("available") or snapshot.get("state") not in ("live", "delayed"):
        return []
    out = []
    currency, daily_return = snapshot.get("base_currency"), snapshot.get("daily_return")
    if daily_return is not None and abs(daily_return) >= account_move:
        way = "up" if daily_return > 0 else "down"
        out.append((f"account:{way}", f"{snapshot['account_label']} is {way} {abs(daily_return):.1%} today",
                    f"the account is {way} {abs(daily_return):.1%} today ({snapshot.get('daily_pnl'):,.0f} {currency})"))
    start = None
    if snapshot.get("net_liquidation") is not None and snapshot.get("daily_pnl") is not None:
        start = float(snapshot["net_liquidation"]) - float(snapshot["daily_pnl"])
    for mover in snapshot.get("largest_movers") or []:
        pnl = mover.get("daily_pnl")
        if start and pnl is not None and abs(float(pnl)) / start >= position_pnl:
            way = "added" if float(pnl) > 0 else "took"
            share = abs(float(pnl)) / start
            out.append((f"position:{mover['symbol']}:{'up' if float(pnl) > 0 else 'down'}",
                        f"{mover['symbol']} {way} {share:.1%} {'to' if float(pnl) > 0 else 'from'} "
                        f"{snapshot['account_label']} today",
                        f"{mover['symbol']} {way} {abs(float(pnl)):,.0f} {currency} ({share:.1%} of the account) today"))
    return out


def live_market(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict,
                now: datetime | None = None) -> int:
    """Alert, once a day per rule, on big live moves of accounts whose live view is on, with an explanation."""
    from ibkr_etl.live_collector import markets_open

    now = now or datetime.now(timezone.utc)
    if not markets_open(now):
        return 0
    today = now.astimezone(HONG_KONG).date().isoformat()
    written = 0
    for account_id in accounts:
        snapshot = tools.call("get_live_snapshot", {"account_id": account_id})["data"]
        snapshot["account_label"] = account_id
        for key, title, trigger in live_triggers(snapshot):
            period = f"{today}:{key}"
            if store.inbox_exists("live_alert", account_id, period):
                continue
            answer, citations, used, ok = _run_agent(
                agent, tools, account_id, LIVE_ALERT.format(account_id=account_id, trigger=trigger),
                ["get_live_snapshot", "get_sector_allocation", "get_holdings"])
            _sum_usage(usage, used)
            body = answer if ok else (f"Live data shows {trigger}. The explanation could not be checked against "
                                      "the figures, so it is left out; open the account's Live now panel for detail. "
                                      "Live figures are not the book of record.")
            if store.add_inbox(kind="live_alert", account_id=account_id, period_key=period, title=title, body=body,
                               citations=citations if ok else [{"source": "live_account_values",
                                                                "account_id": account_id}], run_id=None):
                written += 1
    return written


def data_quality_findings(freshness: dict[str, Any], today: date) -> list[str]:
    """Plain-language findings from the freshness tool. Deterministic; no model."""
    findings = []
    valuation = freshness.get("latest_valuation_date")
    if not valuation:
        findings.append("There is no valuation on record.")
    else:
        age = (today - date.fromisoformat(valuation)).days
        if age > STALE_VALUATION_DAYS:
            findings.append(f"The latest valuation is from {valuation}, {age} days ago.")
    holdings = freshness.get("latest_holdings_date")
    if valuation and holdings and holdings != valuation:
        findings.append(f"Holdings are dated {holdings} but the latest valuation is {valuation}.")
    for bench in freshness.get("benchmarks") or []:
        latest = bench.get("latest")
        if not latest:
            findings.append(f"Benchmark {bench['benchmark_id']} has no prices.")
        elif (today - date.fromisoformat(latest)).days > STALE_BENCHMARK_DAYS:
            findings.append(f"Benchmark {bench['benchmark_id']} was last priced on {latest}.")
    return findings


def data_quality(store: Store, tools: ToolClient, accounts: list[str], retention_days: int, today: date | None = None) -> int:
    today = today or date.today()
    written = 0
    for account_id in accounts:
        problems: list[str] = []
        try:
            problems += data_quality_findings(tools.call("get_data_freshness", {"account_id": account_id})["data"], today)
        except ToolCallFailed as error:
            problems.append(f"Freshness could not be checked: {error.message}")
        for name in ("get_holdings", "get_performance"):
            try:
                tools.call(name, {"account_id": account_id})
            except ToolCallFailed as error:
                problems.append(f"{name.replace('get_', '').capitalize()} could not be produced: {error.message}")
        if problems and store.add_inbox(kind="data_quality", account_id=account_id, period_key=today.isoformat(),
                                        title=f"Data check for {account_id}: {len(problems)} issue(s)",
                                        body="\n".join(f"- {p}" for p in problems), citations=[], run_id=None):
            written += 1
    removed = store.purge(retention_days)
    LOGGER.info("retention removed %s conversations", removed)
    return written


def replay_targets(tools: ToolClient, accounts: list[str], start: str, end: str) -> list[tuple[str, str]]:
    """Every account and month in the range whose factsheet was released."""
    from .month_team.coordinator import RELEASED

    targets = []
    for account_id in accounts:
        for month in tools.call("list_month_end_packages", {"account_id": account_id})["data"]["months"]:
            factsheet = month["files"].get("factsheet") or {}
            if start <= month["period_end"] <= end and factsheet.get("status") in RELEASED:
                targets.append((account_id, month["period_end"]))
    return targets


def month_team(store: Store, tools: ToolClient, accounts: list[str], usage: dict, args: argparse.Namespace,
               correlation_id: str, today: date | None = None) -> int:
    from .month_team.agents import TeamModel
    from .month_team.coordinator import Coordinator
    from .month_team.settings import TeamSettings, validate_report_model
    from .month_team.store import MonthStore

    settings, model_settings = TeamSettings.from_env(), ModelSettings.from_env()
    settings.validate()
    validate_report_model(model_settings)
    team_store = MonthStore(store.db, store.tenant_id)
    tokens_left = settings.monthly_token_budget - team_store.tokens_used_this_month()
    if tokens_left <= 0:
        raise BudgetExhausted
    mode = "replay" if args.replay else "month"
    holder: dict[str, TeamModel] = {}

    def model() -> TeamModel:
        holder["model"] = TeamModel(model_settings, tokens_left=tokens_left)
        return holder["model"]

    coordinator = Coordinator(team_store, tools.call, model, settings, today=today, correlation_id=correlation_id)
    period = args.period or ((today or date.today()).replace(day=1) - timedelta(days=1)).isoformat()
    if args.retry:
        first, last = (args.from_period, args.to_period) if args.replay else (period, period)
        LOGGER.info("retrying %s stopped runs", team_store.retry_stopped(mode, first, last,
                                                                       args.round if args.replay else None))
    try:
        if mode == "month":
            counts = coordinator.run_month(accounts, period, "month")
        else:
            counts = {}
            for account_id, period in replay_targets(tools, accounts, args.from_period, args.to_period):
                stage = coordinator.advance(account_id, period, "replay", args.round)
                counts[stage] = counts.get(stage, 0) + 1
    finally:
        if "model" in holder:
            _sum_usage(usage, holder["model"].usage)
    LOGGER.info("month team stages: %s", counts)
    return counts.get("ready", 0)


def onboarding(store: Store, tools: ToolClient, usage: dict, correlation_id: str, run_id: int | None = None) -> int:
    from .month_team.agents import TeamModel
    from .month_team.settings import validate_report_model
    from .onboarding_intake import DraftStore, IntakeDrafter, IntakeSettings, draft_waiting_cases
    from .onboarding_reconcile import Reconciler, ReconciliationStore, reconcile_waiting_cases
    from .onboarding_review import HistoryReviewer, ReviewSettings, ReviewStore, review_waiting_cases
    from .connection_watch import watch_connections
    from .onboarding_watch import watch_live_cases

    # The connection agent and the first-month watch are plain code, so they run even when the month's
    # model budget is used up.
    watched = watch_connections(store, tools.call, run_id) + watch_live_cases(store, tools.call, run_id)
    settings, intake_settings, model_settings = ReviewSettings.from_env(), IntakeSettings.from_env(), ModelSettings.from_env()
    settings.validate()
    intake_settings.validate()
    validate_report_model(model_settings)
    review_store = ReviewStore(store.db, store.tenant_id)
    tokens_left = settings.monthly_token_budget - review_store.tokens_used_this_month()
    if tokens_left <= 0:
        raise BudgetExhausted
    # One model for both agents, so they share the month's remaining budget.
    shared: list[TeamModel] = []

    def model() -> TeamModel:
        if not shared:
            shared.append(TeamModel(model_settings, tokens_left=tokens_left))
        return shared[0]

    drafted = draft_waiting_cases(DraftStore(store.db, store.tenant_id), tools.call,
                                  IntakeDrafter(intake_settings, model), usage, correlation_id)
    reconciled = reconcile_waiting_cases(ReconciliationStore(store.db, store.tenant_id), tools.call, Reconciler(model),
                                         usage, correlation_id)
    reviewed = review_waiting_cases(review_store, tools.call, HistoryReviewer(settings, model), usage, correlation_id)
    return drafted + reconciled + reviewed + watched


def operations(store: Store, tools: ToolClient, usage: dict, correlation_id: str, client_id: str) -> int:
    from .month_team.agents import TeamModel
    from .month_team.settings import validate_report_model
    from .operations import Investigator, OpsSettings, OpsStore, investigate_open_incidents

    settings, model_settings = OpsSettings.from_env(), ModelSettings.from_env()
    settings.validate()
    validate_report_model(model_settings)
    ops_store = OpsStore(store.db, store.tenant_id)
    tokens_left = settings.monthly_token_budget - ops_store.tokens_used_this_month()
    if tokens_left <= 0:
        raise BudgetExhausted
    shared: list[TeamModel] = []

    def model() -> TeamModel:
        if not shared:
            shared.append(TeamModel(model_settings, tokens_left=tokens_left))
        return shared[0]

    return investigate_open_incidents(ops_store, tools.call, Investigator(settings, model, client_id=client_id),
                                      usage, correlation_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job", choices=["holdings_explainer", "month_end_drafter", "data_quality", "month_team",
                                                  "onboarding", "operations", "weekly_brief", "live_market"])
    parser.add_argument("--period", help="month_team: the month end to work on (default: last month)")
    parser.add_argument("--replay", action="store_true", help="month_team: re-check months already released")
    parser.add_argument("--from", dest="from_period", default="1900-01-01", help="month_team --replay: first month end")
    parser.add_argument("--to", dest="to_period", default="2999-12-31", help="month_team --replay: last month end")
    parser.add_argument("--round", type=int, default=1,
                        help="month_team --replay: the replay round; a new round checks every released month afresh")
    parser.add_argument("--retry", action="store_true", help="month_team: retry runs stopped after repeated failures")
    args = parser.parse_args(argv)
    if args.job != "month_team" and (args.period or args.replay or args.retry):
        parser.error("--period, --replay and --retry apply to month_team only")
    if args.round != 1 and not args.replay:
        parser.error("--round applies to month_team --replay only")
    if args.round < 1:
        parser.error("--round starts at 1")
    logging.basicConfig(level=logging.INFO)
    db, runtime = DatabaseSettings.from_env(), RuntimeSettings.from_env()
    correlation_id = str(uuid.uuid4())
    store = Store(connect(db, search_path="agent"), db.tenant_id)
    tools = ToolClient(runtime.tool_server_url, TokenSource(runtime.managed_identity_client_id, runtime.tool_server_audience),
                       job=args.job, correlation_id=correlation_id)
    run_id = store.start_run(args.job, correlation_id)
    usage: dict[str, int] = {}
    try:
        tools.wait_until_ready()
        if args.job == "onboarding":
            written = onboarding(store, tools, usage, correlation_id, run_id)
            store.finish_run(run_id, status="succeeded", items=written, usage=usage)
            LOGGER.info("onboarding reviewed %s case(s)", written)
            return 0
        if args.job == "operations":
            written = operations(store, tools, usage, correlation_id, runtime.managed_identity_client_id)
            store.finish_run(run_id, status="succeeded", items=written, usage=usage)
            LOGGER.info("operations investigated %s incident(s)", written)
            return 0
        accounts = [a["account_id"] for a in tools.call("list_accounts", {})["data"]["accounts"]]
        if args.job == "data_quality":
            written = data_quality(store, tools, accounts, runtime.retention_days)
        elif args.job == "month_team":
            written = month_team(store, tools, accounts, usage, args, correlation_id)
        else:
            if store.tokens_used_today() >= runtime.daily_token_budget:
                store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="budget_exhausted")
                return 1
            agent = Agent(ModelSettings.from_env())
            job = {"holdings_explainer": holdings_explainer, "weekly_brief": weekly_brief,
                   "live_market": live_market}.get(args.job, month_end_drafter)
            written = job(store, tools, agent, accounts, usage)
        store.finish_run(run_id, status="succeeded", items=written, usage=usage)
        LOGGER.info("%s wrote %s inbox items", args.job, written)
        return 0
    except BudgetExhausted:
        store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="budget_exhausted")
        LOGGER.error("%s paused: the monthly token budget is used up", args.job)
        return 1
    except (AgentError, ToolCallFailed) as error:
        store.finish_run(run_id, status="failed", items=0, usage=usage, error_code=error.code)
        LOGGER.error("%s failed: %s", args.job, error.code)
        return 1
    except Exception:
        store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="internal_error")
        LOGGER.exception("%s failed", args.job)
        return 1
    finally:
        tools.close()


if __name__ == "__main__":
    sys.exit(main())
