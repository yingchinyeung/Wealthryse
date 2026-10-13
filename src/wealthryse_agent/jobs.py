"""Scheduled agents. Each writes to the inbox only; nothing is sent or published.

    python -m wealthryse_agent.jobs holdings_explainer   # daily, after the account sync
    python -m wealthryse_agent.jobs month_end_drafter    # 1st–7th of each month
    python -m wealthryse_agent.jobs data_quality         # daily; also applies chat retention

Accounts come from agent.service_grants for the job, enforced by the tool server.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date, timedelta
from typing import Any

from .auth import TokenSource
from .config import DatabaseSettings, ModelSettings, RuntimeSettings
from .database import connect
from .prompts import HOLDINGS_EXPLAINER, MONTH_END_DRAFTER
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", choices=["holdings_explainer", "month_end_drafter", "data_quality"])
    args = parser.parse_args(argv)
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
        accounts = [a["account_id"] for a in tools.call("list_accounts", {})["data"]["accounts"]]
        if args.job == "data_quality":
            written = data_quality(store, tools, accounts, runtime.retention_days)
        else:
            if store.tokens_used_today() >= runtime.daily_token_budget:
                store.finish_run(run_id, status="failed", items=0, usage=usage, error_code="budget_exhausted")
                return 1
            agent = Agent(ModelSettings.from_env())
            job = holdings_explainer if args.job == "holdings_explainer" else month_end_drafter
            written = job(store, tools, agent, accounts, usage)
        store.finish_run(run_id, status="succeeded", items=written, usage=usage)
        LOGGER.info("%s wrote %s inbox items", args.job, written)
        return 0
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
