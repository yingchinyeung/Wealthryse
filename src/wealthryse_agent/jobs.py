"""Scheduled agents. They write only to the agent schema (the inbox and their own run
records); nothing is sent, published or released.

    python -m wealthryse_agent.jobs holdings_explainer   # daily, after the account sync
    python -m wealthryse_agent.jobs month_end_drafter    # 1st–7th of each month
    python -m wealthryse_agent.jobs data_quality         # daily; also applies chat retention
    python -m wealthryse_agent.jobs weekly_brief         # Saturdays: each account's weekly brief
    python -m wealthryse_agent.jobs live_market          # every 15 minutes in trading hours: alert on big
                                                         # live moves, with an explanation
    python -m wealthryse_agent.jobs risk_watch           # weekday mornings: alert on a VaR breach, a switch into
                                                         # the stressed regime or a volatility jump, with an
                                                         # explanation; once a month, check the risk models
                                                         # against what actually happened
    python -m wealthryse_agent.jobs export_packs         # daily: an inbox card for each saved export pack
                                                         # whose period has closed (no model, no files)
    python -m wealthryse_agent.jobs guide_check          # Mondays: recompute each account's statistics from
                                                         # the Metrics guide's formulas and flag any figure
                                                         # the website or the analyst shows differently
    python -m wealthryse_agent.jobs guide_keeper         # Mondays: find website figures and calculations the
                                                         # Metrics guide has fallen behind on, and draft the
                                                         # update for the owner (code only, no client data)
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
import hashlib
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
from . import certified_tools, guide_tools
from .prompts import (GUIDE_CHECKER, GUIDE_KEEPER, HOLDINGS_EXPLAINER, LIVE_ALERT, MONTH_END_CERTIFIED, MONTH_END_DRAFTER,
                      RISK_ALERT, WEEKLY_BRIEF)
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
    allowed = ["get_performance", "compare_to_benchmark", "get_holdings_changes", "get_sector_allocation", "get_cash_flows"]
    # AGENT_CERTIFIED_MONTH_END=on (here and on the tool server): the read tools too.
    certified = certified_tools.month_end_enabled() and certified_tools.enabled()
    if certified:
        allowed += [certified_tools.GET, certified_tools.TRACE]
    for account_id in accounts:
        if store.inbox_exists("month_end_draft", account_id, period):
            continue
        task = MONTH_END_DRAFTER.format(account_id=account_id, month_end=month_end.isoformat(),
                                        month_start=month_start.isoformat())
        if certified:
            task += MONTH_END_CERTIFIED.format(account_id=account_id, month_end=month_end.isoformat())
        answer, citations, used, ok = _run_agent(agent, tools, account_id, task, allowed)
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
             "optimise_portfolio", "get_market_regimes"])
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


VOLATILITY_JUMP = 1.5        # forecast volatility at least 1.5 times its long-run level
STRESSED_PROBABILITY = 0.6   # how sure the regime model must be before an alert


def risk_triggers(outlook: dict[str, Any], regimes: dict[str, Any] | None) -> list[tuple[str, str, str]]:
    """(key, title, trigger in words) for each risk alert the latest data warrants. Deterministic; no model."""
    out = []
    latest = outlook.get("latest_day")
    if latest and latest.get("breached"):
        out.append((f"var_breach:{latest['date']}",
                    f"A loss beyond the 95% VaR on {latest['date']}",
                    f"on {latest['date']} the account returned {latest['return']:.2%}, beyond its 95% one-day VaR "
                    f"of {latest['var_95']:.2%} forecast the day before"))
    if regimes:
        stressed = regimes["regimes"][-1]["regime"]
        if (regimes["current_regime"] == stressed and regimes["regime_a_week_ago"] != stressed
                and regimes["current_probability"] >= STRESSED_PROBABILITY):
            out.append((f"regime:{stressed.lower()}:{regimes['window_end']}",
                        f"The account has moved into its {stressed.lower()} regime",
                        f"the regime model now puts the account in its {stressed.lower()} regime "
                        f"({regimes['current_probability']:.0%} likely), up from {regimes['regime_a_week_ago'].lower()} "
                        "a week ago"))
    ratio = outlook.get("volatility_vs_long_run")
    if ratio is not None and ratio >= VOLATILITY_JUMP:
        year, week, _ = date.fromisoformat(outlook["window_end"]).isocalendar()
        out.append((f"volatility:{year}-W{week:02d}", "Forecast volatility is well above normal",
                    f"forecast volatility for the next month is {outlook['forecast_volatility_next_month_annual']:.1%} "
                    f"a year, {ratio:.1f} times its long-run level of {outlook['long_run_volatility_annual']:.1%}"))
    return out


def model_check_findings(outlook: dict[str, Any], regimes: dict[str, Any] | None) -> tuple[list[str], list[str]]:
    """(problems, passes) from the models' own backtests. Deterministic; no model."""
    problems, passes = [], []
    backtest = outlook.get("backtest") or {}
    if backtest.get("available"):
        line = backtest["verdict"]
        if backtest.get("kupiec_p_value") is not None and backtest["kupiec_p_value"] < 0.05:
            problems.append(line + " Treat the VaR and tail-loss figures with caution until it is back in line.")
        else:
            passes.append(line)
    else:
        passes.append(backtest.get("reason") or "The VaR backtest is not available yet.")
    score = (regimes or {}).get("score") or {}
    if score.get("available"):
        coverage = score["coverage_90"]
        line = (f"The outlook's 90% range held the next month's outcome {coverage:.0%} of the time over "
                f"{score['tested_months']} months.")
        (passes if 0.8 <= coverage <= 0.97 else problems).append(
            line if 0.8 <= coverage <= 0.97 else line + " That is outside 80% to 97%, so the ranges are off.")
        passes.append("The outlook shows directional skill." if score["directional_skill"] else
                      "The outlook shows no reliable directional skill; read its range, not its middle.")
    elif regimes is not None:
        passes.append(score.get("reason") or "The outlook cannot be scored yet.")
    return problems, passes


def risk_watch(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict,
               today: date | None = None) -> int:
    """Daily risk alerts with an explanation, and a monthly check of the risk models against what happened."""
    today = today or date.today()
    written = 0
    for account_id in accounts:
        try:
            outlook = tools.call("get_risk_outlook", {"account_id": account_id})["data"]
        except ToolCallFailed:
            continue  # too little history, or no valuations: nothing to watch yet
        try:
            regimes = tools.call("get_market_regimes", {"account_id": account_id})["data"]
        except ToolCallFailed:
            regimes = None
        end = outlook["window_end"]
        start = (date.fromisoformat(end) - timedelta(days=7)).isoformat()
        for key, title, trigger in risk_triggers(outlook, regimes):
            if store.inbox_exists("risk_alert", account_id, key):
                continue
            answer, citations, used, ok = _run_agent(
                agent, tools, account_id, RISK_ALERT.format(account_id=account_id, trigger=trigger, from_date=start,
                                                            to_date=end),
                ["get_risk_outlook", "get_market_regimes", "get_holdings_changes", "get_factor_exposures",
                 "get_sector_allocation"])
            _sum_usage(usage, used)
            body = answer if ok else (f"The risk watch found that {trigger}. The explanation could not be checked "
                                      "against the figures, so it is left out; open the Modelling tab for detail.")
            if store.add_inbox(kind="risk_alert", account_id=account_id, period_key=key, title=title, body=body,
                               citations=citations if ok else [{"source": "subject_nav_series",
                                                                "account_id": account_id}], run_id=None):
                written += 1
        month = today.strftime("%Y-%m")
        if store.inbox_exists("model_check", account_id, month):
            continue
        problems, passes = model_check_findings(outlook, regimes)
        title = (f"Risk model check for {month}: {len(problems)} issue(s)" if problems
                 else f"Risk model check for {month}: in line")
        body = "\n".join([f"- {p}" for p in problems] + [f"- {p}" for p in passes])
        if store.add_inbox(kind="model_check", account_id=account_id, period_key=month, title=title, body=body,
                           citations=[{"source": "subject_nav_series", "account_id": account_id, "to_date": end}],
                           run_id=None):
            written += 1
    return written


def guide_check_summary(data: dict[str, Any]) -> list[str]:
    """One line per mismatch, from the conformance facts alone (used when the explanation fails its check)."""
    return [f"{c['metric']} ({c['source']}): guide formula {c['guide_formula_value']}, shown {c['shown_value']}"
            for c in data["checks"] if c["status"] == "mismatch"]


def guide_check(store: Store, tools: ToolClient, agent: Agent, accounts: list[str], usage: dict,
                today: date | None = None) -> int:
    """Weekly: compare each account's figures with an independent implementation of the Metrics guide.

    A card each week that something differs, with the analyst's explanation; otherwise one all-clear a month.
    """
    today = today or date.today()
    year, week, _ = today.isocalendar()
    week_key, month_key = f"{year}-W{week:02d}", today.strftime("%Y-%m")
    guided = guide_tools.GuideTools(tools)
    written = 0
    for account_id in accounts:
        if store.inbox_exists("guide_check", account_id, week_key):
            continue
        try:
            result = tools.call("get_guide_conformance", {"account_id": account_id})
        except ToolCallFailed:
            continue  # too little history to check
        data = result["data"]
        citations = result.get("citations") or [{"source": "subject_nav_series", "account_id": account_id}]
        if not data["mismatches"]:
            if store.inbox_exists("guide_check", account_id, month_key):
                continue
            if store.add_inbox(kind="guide_check", account_id=account_id, period_key=month_key,
                               title=f"Metrics guide check: all {data['checked']} figures match",
                               body=(f"Every figure checked matched the Metrics guide's formulas, recomputed separately "
                                     f"over {data['observations']} daily returns from {data['period_start']} to "
                                     f"{data['period_end']}."), citations=citations, run_id=None):
                written += 1
            continue
        answer, cited, used, ok = _run_agent(
            agent, guided, account_id, GUIDE_CHECKER.format(account_id=account_id, count=data["mismatches"]),
            ["get_guide_conformance", "get_metric_definition", "calculate"])
        _sum_usage(usage, used)
        body = answer if ok else "\n".join(
            ["The explanation could not be checked against the figures, so only the facts are shown:"]
            + [f"- {line}" for line in guide_check_summary(data)])
        if store.add_inbox(kind="guide_check", account_id=account_id, period_key=week_key,
                           title=f"Metrics guide check: {data['mismatches']} of {data['checked']} figures differ",
                           body=body, citations=cited if ok else citations, run_id=None):
            written += 1
    return written


def guide_keeper(store: Store, agent: Agent, usage: dict, report: Any = None) -> int:
    """Weekly: audit the deployed code against the Metrics guide and post one drafted update per drift."""
    from ibkr_etl import metrics_guide_audit as audit

    report = report or audit.audit()
    if report.clean:
        return 0
    key = audit.drift_key(report)
    if store.inbox_exists("guide_keeper", guide_tools.PLATFORM_SUBJECT, key):
        return 0
    count = (len(report.uncovered_labels) + len(report.changed_sources) + len(report.unrecorded_sources)
             + len(report.missing_sources))
    answer, cited, used, ok = "", [], {}, False
    for event in agent.run(question="Draft the Metrics guide update.", account_id=None,
                           tools=guide_tools.DriftFacts(audit.drift_facts(report)),
                           allowed_tools=[guide_tools.DRIFT], instructions=GUIDE_KEEPER, skip_router=True):
        if event["type"] in ("answer", "rejected"):
            turn = event["turn"]
            answer, cited, used, ok = turn.answer, turn.citations, turn.usage, event["type"] == "answer"
    _sum_usage(usage, used)
    body = answer if ok else ("The draft could not be checked against the code, so only what drifted is "
                              "shown:\n\n" + audit.brief(report).split("## When the guide is right")[0])
    added = store.add_inbox(kind="guide_keeper", account_id=guide_tools.PLATFORM_SUBJECT, period_key=key,
                            title=f"Metrics guide keeper: {count} item(s) to update", body=body,
                            citations=cited if ok else [{"source": "deployed_source_code"}], run_id=None)
    return 1 if added else 0


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


def export_packs(store: Store, tools: ToolClient, accounts: list[str]) -> int:
    """One card per saved pack and closed period, for the person who saved it.

    A period is closed once the account has data on or after its last day. A new pack starts with the
    last period closed when it was saved, never earlier ones. The card only says the pack is ready: the
    person builds, checks and releases the files on the website.
    """
    from ibkr_etl.export_agent import period_for

    latest: dict[str, str | None] = {}
    written = 0
    for pack in store.active_export_packs():
        account_id = pack["subject_id"]
        if account_id not in accounts:
            continue
        if account_id not in latest:
            latest[account_id] = tools.call("get_data_freshness", {"account_id": account_id})["data"].get(
                "latest_valuation_date")
        if not latest[account_id]:
            continue
        period = period_for(pack["schedule"], latest[account_id])
        created = pack["created_at"]
        created_day = (created.date() if isinstance(created, datetime) else date.fromisoformat(str(created)[:10]))
        first = period_for(pack["schedule"], created_day.isoformat())
        if period is None or first is None or period[1] < first[1]:
            continue
        from_day, to_day, label = period
        owner = hashlib.sha256(str(pack["user_object_id"]).lower().encode()).hexdigest()
        if store.add_inbox(
                kind="export_pack", account_id=account_id, period_key=f"{pack['pack_id']}:{to_day}",
                title=f"{pack['name']}: {label} is ready to check",
                body=(f"The {pack['schedule']} export pack {pack['name']} can now be built for {from_day} to {to_day}. "
                      "Open it to build the files, see the checks and the cover note, and download them."),
                citations=[{"export_pack": pack["pack_id"], "owner_sha256": owner, "from_date": from_day,
                            "to_date": to_day, "label": label}],
                run_id=None):
            written += 1
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
                                                  "onboarding", "operations", "weekly_brief", "live_market",
                                                  "risk_watch", "export_packs", "guide_check", "guide_keeper"])
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
        if args.job == "guide_keeper":
            if store.tokens_used_today() >= runtime.daily_token_budget:
                store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="budget_exhausted")
                return 1
            written = guide_keeper(store, Agent(ModelSettings.from_env()), usage)
            store.finish_run(run_id, status="succeeded", items=written, usage=usage)
            LOGGER.info("guide_keeper wrote %s inbox items", written)
            return 0
        accounts = [a["account_id"] for a in tools.call("list_accounts", {})["data"]["accounts"]]
        if args.job == "data_quality":
            written = data_quality(store, tools, accounts, runtime.retention_days)
        elif args.job == "export_packs":
            written = export_packs(store, tools, accounts)
        elif args.job == "month_team":
            written = month_team(store, tools, accounts, usage, args, correlation_id)
        else:
            if store.tokens_used_today() >= runtime.daily_token_budget:
                store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="budget_exhausted")
                return 1
            agent = Agent(ModelSettings.from_env())
            job = {"holdings_explainer": holdings_explainer, "weekly_brief": weekly_brief,
                   "live_market": live_market, "risk_watch": risk_watch,
                   "guide_check": guide_check}.get(args.job, month_end_drafter)
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
