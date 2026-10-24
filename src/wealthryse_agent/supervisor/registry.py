"""Who is in the agent hierarchy, which tools each may use, and which Medallion layer each tool reads.

The registry lives in code for now. Today's agents keep the tool lists they already have
(``tools=None``); the new client analysis specialists get explicit lists. The tool server does
not read this yet: it still uses ``only_jobs`` and ``not_jobs`` in ``tools.py``. Tests keep the
two in step, so a new job or tool can't be added without a place in the hierarchy.
"""
from __future__ import annotations

from dataclasses import dataclass

from .. import calculator, certified_tools, guide_tools

TEAMS = ("client_analysis", "reporting", "onboarding", "operations")
KINDS = ("lead", "specialist", "checker", "plan")
# What a member may do: reply in chat, write an inbox card, or propose a change a person applies.
# "act" (restart jobs, roll back) stays with the autonomous administrator, which is not an agent.
AUTONOMY = ("answer", "draft", "propose")
# analyst: the Global Standard chat deployment. reports: the APAC Data Zone deployment. none: code only.
DEPLOYMENTS = ("analyst", "reports", "none")

# The layer each tool reads, from the tables behind its agent views (migrations 068 to 070 and 073):
#   gold     certified figures and builds, frozen month-end snapshots and packages
#   silver   accounts, NAV, positions, cash, trades, FX, prices, benchmarks
#   bronze   raw files and the live feed
#   staging  onboarding cases, loaded per case before an account goes live
#   monitor  import runs, the administrator's runs and incidents, broker login checks
#   code     no database: the Metrics guide and the calculator
#   external public market news the market news watch confirmed (migrations/agent/023); no client data
LAYERS = ("gold", "silver", "bronze", "staging", "monitor", "code", "external")
TOOL_LAYERS = {
    "list_accounts": "silver",
    "get_holdings": "silver",
    "get_holdings_changes": "silver",
    "get_performance": "silver",
    "compare_to_benchmark": "silver",
    "get_sector_allocation": "silver",
    "get_cash_flows": "silver",
    "get_data_freshness": "silver",
    # Silver dates, and the newest published certified build's as-of date once Medallion's view exists.
    "get_settled_dates": "silver",
    "get_nav_history": "silver",
    "get_risk_metrics": "silver",
    "get_month_end_package": "gold",
    "get_month_end_snapshot": "gold",
    "list_month_end_packages": "gold",
    "list_onboarding_report_cases": "staging",
    "list_onboarding_intake_cases": "staging",
    "list_onboarding_broker_cases": "staging",
    "get_onboarding_broker_overview": "staging",
    "get_onboarding_broker_day": "staging",
    "list_live_onboarding_cases": "staging",
    # The live feed's tables are bronze (069); silver has subject_live_summary for when it moves.
    "get_live_snapshot": "bronze",
    "run_stress_test": "silver",
    "project_wealth": "silver",
    "run_what_if": "silver",
    "optimise_portfolio": "silver",
    "get_factor_exposures": "silver",
    "get_risk_outlook": "silver",
    "get_market_regimes": "silver",
    "run_factor_stress": "silver",
    "get_guide_conformance": "silver",
    "list_broker_logins": "monitor",
    certified_tools.GET: "gold",
    certified_tools.TRACE: "gold",
    certified_tools.LIST: "code",
    "list_open_incidents": "monitor",
    "get_operations_history": "monitor",
    guide_tools.DEFINITION: "code",
    guide_tools.SEARCH: "code",
    guide_tools.DRIFT: "code",
    calculator.NAME: "code",
    "get_market_events": "external",
}

# Every client analysis specialist may also use these; they say nothing about which area a question is in.
SHARED_CLIENT_TOOLS = ("get_data_freshness", certified_tools.GET, certified_tools.TRACE, certified_tools.LIST,
                       calculator.NAME)
# Asked by the server, not the model, to check a person's accounts.
SERVER_TOOLS = ("list_accounts",)


@dataclass(frozen=True)
class Member:
    name: str
    team: str
    kind: str
    autonomy: str
    deployment: str
    # The tools it may call. None: it keeps the list it has in code today.
    tools: tuple[str, ...] | None = None
    # Today's scheduled job names that are this member.
    jobs: tuple[str, ...] = ()
    # Where it runs today; empty for a member that does not exist yet.
    today: str = ""


# Tools a specialist borrows from the one that owns them, to look something up before its own tools run:
# run_what_if and run_stress_test take holdings by symbol, so "my largest holding" needs get_holdings first.
BORROWED = {"modelling": ("get_holdings",)}


def _client(name: str, *tools: str, today: str = "") -> Member:
    return Member(name, "client_analysis", "specialist", "answer", "analyst",
                  tuple(tools) + BORROWED.get(name, ()) + SHARED_CLIENT_TOOLS, today=today)


MEMBERS = (
    # Client analysis: a planner model for open questions; fixed plans for the scheduled briefs.
    Member("planner", "client_analysis", "lead", "answer", "analyst", ()),
    _client("holdings_performance", "get_holdings", "get_holdings_changes", "get_performance",
            "compare_to_benchmark", "get_sector_allocation", "get_cash_flows", "get_nav_history",
            "get_live_snapshot", "get_market_events", today="website chat (one generalist)"),
    _client("risk", "get_risk_metrics", "get_risk_outlook", "get_market_regimes", "get_factor_exposures",
            today="website chat (one generalist)"),
    _client("modelling", "project_wealth", "run_what_if", "optimise_portfolio", "run_stress_test",
            "run_factor_stress", today="website chat (one generalist)"),
    _client("metrics_guide", guide_tools.DEFINITION, guide_tools.SEARCH,
            today="Explain buttons and Ask the guide"),
    Member("answer_checker", "client_analysis", "checker", "answer", "analyst", ()),
    Member("holdings_explainer", "client_analysis", "plan", "draft", "analyst", jobs=("holdings_explainer",),
           today="job, 00:30 UTC"),
    Member("weekly_brief", "client_analysis", "plan", "draft", "analyst", jobs=("weekly_brief",),
           today="job, Saturdays"),
    Member("risk_watch", "client_analysis", "plan", "draft", "analyst", jobs=("risk_watch",),
           today="job, weekdays 01:15 UTC"),
    Member("live_market", "client_analysis", "plan", "draft", "analyst", jobs=("live_market",),
           today="job, every 15 minutes in market hours"),
    # Reporting: today's month-end coordinator leads, unchanged inside.
    Member("month_end_coordinator", "reporting", "lead", "draft", "reports", jobs=("month_team",),
           today="month_team job"),
    Member("month_end_data", "reporting", "specialist", "draft", "reports", today="month_team job"),
    Member("month_end_analyst", "reporting", "specialist", "draft", "reports", today="month_team job"),
    Member("month_end_writer", "reporting", "specialist", "draft", "reports", today="month_team job"),
    Member("month_end_reviewer", "reporting", "checker", "draft", "reports", today="month_team job"),
    Member("month_end_visual_qa", "reporting", "checker", "draft", "reports", today="month_team job"),
    Member("commentary_drafter", "reporting", "specialist", "draft", "analyst", jobs=("month_end_drafter",),
           today="month_end_drafter job"),
    Member("export_planner", "reporting", "specialist", "propose", "analyst", (), today="/v1/exports/plan"),
    Member("export_checker", "reporting", "checker", "answer", "analyst", today="/v1/exports/checks"),
    Member("export_packs", "reporting", "plan", "draft", "none", jobs=("export_packs",), today="export_packs job"),
    Member("factsheet_builder", "reporting", "specialist", "propose", "analyst", (),
           today="/v1/factsheet-designs/build-step"),
    Member("factsheet_checker", "reporting", "checker", "answer", "analyst", (),
           today="/v1/factsheet-designs/check"),
    Member("factsheet_editor", "reporting", "specialist", "propose", "analyst", (),
           today="/v1/factsheet-designs/edit"),
    # Onboarding: today's hourly job leads.
    Member("onboarding_lead", "onboarding", "lead", "draft", "none", jobs=("onboarding",), today="onboarding job"),
    Member("intake", "onboarding", "specialist", "draft", "reports", today="onboarding job"),
    Member("connection_check", "onboarding", "specialist", "draft", "reports", today="onboarding job"),
    Member("reconciliation", "onboarding", "specialist", "draft", "reports", today="onboarding job"),
    Member("history_reviewer", "onboarding", "checker", "draft", "reports", today="onboarding job"),
    Member("first_month_watch", "onboarding", "specialist", "draft", "reports", today="onboarding job"),
    # Data and operations: the event dispatcher (supervisor/dispatcher.py; code, no model) wakes jobs on new data.
    Member("event_dispatcher", "operations", "lead", "answer", "none", ()),
    Member("data_quality", "operations", "specialist", "draft", "none", jobs=("data_quality",),
           today="data_quality job"),
    Member("guide_check", "operations", "specialist", "draft", "analyst", jobs=("guide_check",),
           today="guide_check job"),
    Member("guide_keeper", "operations", "specialist", "draft", "analyst", jobs=("guide_keeper",),
           today="guide_keeper job"),
    Member("incident_diagnosis", "operations", "specialist", "propose", "reports", jobs=("operations",),
           today="operations job"),
    # Reads world stock market news hourly and keeps the market changes it can confirm (supervisor/market_news.py);
    # news_link.py relates them to numbers that don't add up, for the onboarding and operations agents and chat.
    Member("market_news", "operations", "specialist", "draft", "reports", ("get_market_events",),
           jobs=("market_news",)),
    # New: says why a gold build was held. Its tools come with Medallion's monitor views.
    Member("gate_investigator", "operations", "specialist", "draft", "reports", ()),
)
BY_NAME = {member.name: member for member in MEMBERS}
CLIENT_SPECIALISTS = tuple(m.name for m in MEMBERS if m.team == "client_analysis" and m.kind == "specialist")
JOB_MEMBER = {job: member.name for member in MEMBERS for job in member.jobs}


# Which specialist answers for each website page's certified figures (ibkr_etl.certified_figures pages).
PAGE_OWNERS = {"overview": "holdings_performance", "holdings": "holdings_performance", "cash": "holdings_performance",
               "performance": "holdings_performance", "combined-holdings": "holdings_performance",
               "risk-snapshot": "risk", "risk-statistics": "risk"}


def figure_owner(figure_id: str) -> str | None:
    """The specialist a certified figure belongs to, from its website page."""
    item = certified_tools.register.figures().get(figure_id)
    return PAGE_OWNERS.get(item.page) if item is not None else None


def owner(tool: str) -> str | None:
    """The client analysis specialist a tool belongs to; None for shared and server tools."""
    if tool in SHARED_CLIENT_TOOLS or tool in SERVER_TOOLS:
        return None
    for name in CLIENT_SPECIALISTS:
        if tool in (BY_NAME[name].tools or ()) and tool not in BORROWED.get(name, ()):
            return name
    return None


def layers(tools: list[str] | tuple[str, ...]) -> dict[str, int]:
    """How many calls read each layer; a tool missing from the map counts as unknown."""
    counted: dict[str, int] = {}
    for tool in tools:
        layer = TOOL_LAYERS.get(tool, "unknown")
        counted[layer] = counted.get(layer, 0) + 1
    return counted
