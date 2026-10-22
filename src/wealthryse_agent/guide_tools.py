"""Metrics guide tools for the agents: look up a definition, or search the guide in plain words.

They read the guide in ``ibkr_etl.metrics_guide`` (no database, no client data), so they are added
beside the account tools by wrapping the tool client rather than through the tool server. Their
results go through the same figure check as everything else: an answer may quote the guide's own
numbers (252 sessions, 95%) because ``figures_in_text`` carries them.
"""
from __future__ import annotations

import difflib
from typing import Any

from ibkr_etl import metrics_guide as guide

from . import certified_tools
from .tool_client import ToolCallFailed

# Inbox cards about the whole website (not one account) use this subject; only owners see them.
PLATFORM_SUBJECT = "platform"
DRIFT = "get_guide_drift"
DEFINITION = "get_metric_definition"
SEARCH = "search_metrics_guide"
NAMES = (DEFINITION, SEARCH)
MAX_QUERY_CHARS = 200

# The account tools whose results feed each guide section's figures. Explain-with-your-numbers and
# Ask-the-guide offer only these (plus the calculator) for a definition in that section.
SECTION_TOOLS = {
    "overview": ("get_performance", "get_nav_history", "get_cash_flows", "get_data_freshness"),
    "benchmark": ("compare_to_benchmark", "get_risk_metrics", "get_nav_history", "get_performance"),
    "holdings": ("get_holdings", "get_sector_allocation"),
    "risk": ("get_risk_metrics", "get_nav_history", "run_stress_test"),
    "statistics": ("get_risk_metrics", "get_nav_history"),
    "modelling-outlook": ("project_wealth", "get_risk_outlook", "get_market_regimes"),
    "modelling-portfolio": ("run_what_if", "optimise_portfolio", "get_factor_exposures", "run_factor_stress"),
    "cash-trades": ("get_cash_flows", "get_holdings_changes", "get_holdings"),
    "reports": ("get_data_freshness",),
    "exports": ("get_data_freshness",),
    "agents": ("get_risk_outlook", "get_market_regimes", "get_risk_metrics"),
    "system": ("get_data_freshness",),
}
_SECTION_KEY = {metric.name: section.key for section in guide.METRIC_SECTIONS for metric in section.metrics}

SPECS = (
    {"name": DEFINITION,
     "description": "One Metrics guide definition by its exact name: what it tells you, how it is worked out "
                    "(plain and technical), how to explain it, where the website shows it, its link, other "
                    "names and related definitions.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["name"],
                     "properties": {"name": {"type": "string", "description": "exact definition name, e.g. "
                                             "Sharpe ratio; use search_metrics_guide to find it"}}}},
    {"name": SEARCH,
     "description": "Search the Metrics guide in plain words (e.g. 'why did my risk go up', 'CVaR'). Returns "
                    "up to five matching definitions with where each is shown and a one-line explanation.",
     "inputSchema": {"type": "object", "additionalProperties": False, "required": ["query"],
                     "properties": {"query": {"type": "string", "description": "words or a question"},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 10}}}},
)


def tools_for_metric(name: str) -> tuple[str, ...]:
    return SECTION_TOOLS.get(_SECTION_KEY.get(name, ""), ())


def call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == DEFINITION:
        metric = str(arguments.get("name") or "").strip()
        if metric not in guide.METRIC_ANCHORS:
            close = difflib.get_close_matches(metric, list(guide.METRIC_ANCHORS), n=3, cutoff=0.6)
            close += [m["name"] for m in (guide.search(metric, 3) if metric else []) if m["name"] not in close]
            hint = ("Closest: " + ", ".join(close[:3]) + ".") if close else ""
            raise ToolCallFailed("unknown_metric", f"The guide has no definition called that. {hint}".strip())
        data = guide.definition_payload(metric)
        data["account_tools"] = list(tools_for_metric(metric))
        return {"tool": DEFINITION, "account_id": None, "as_of": None, "data": data,
                "citations": [{"source": "metrics_guide", "metric": metric, "link": data["link"]}]}
    if name == SEARCH:
        query = str(arguments.get("query") or "").strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            raise ToolCallFailed("invalid_arguments", f"query must be 1 to {MAX_QUERY_CHARS} characters.")
        limit = arguments.get("limit") or 5
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ToolCallFailed("invalid_arguments", "limit must be a whole number.")
        results = guide.search(query, limit)
        return {"tool": SEARCH, "account_id": None, "as_of": None,
                "data": {"query": query, "definitions": results},
                "citations": [{"source": "metrics_guide", "metric": r["name"], "link": r["link"]} for r in results]}
    raise ToolCallFailed("unknown_tool", "That tool is not available here.")


class GuideTools:
    """A tool client with the guide tools added; account tools pass through to ``inner``.

    With no ``inner`` (a question asked without an account) only the guide tools exist.
    """

    def __init__(self, inner: Any = None) -> None:
        self.inner = inner

    def list_tools(self) -> list[dict[str, Any]]:
        listed = list(self.inner.list_tools()) if self.inner is not None else []
        extra = [certified_tools.LIST_SPEC] if certified_tools.enabled() else []
        return ([t for t in listed if t["name"] not in NAMES and t["name"] != certified_tools.LIST]
                + [dict(spec) for spec in (*SPECS, *extra)])

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in NAMES:
            return call(name, arguments)
        if name == certified_tools.LIST and certified_tools.enabled():
            # The register only (no client data), so it needs no account.
            try:
                return certified_tools.list_certified_figures(arguments)
            except ValueError as problem:
                raise ToolCallFailed("invalid_arguments", str(problem)) from None
        if self.inner is None:
            raise ToolCallFailed("unknown_tool", "That tool is not available here.")
        return self.inner.call(name, arguments)

    def close(self) -> None:
        if self.inner is not None:
            self.inner.close()


def guide_links(citations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The definitions an answer cited, as {name, link}, for the reader to open."""

    seen: dict[str, str] = {}
    for citation in citations:
        if citation.get("tool") not in NAMES:
            continue
        for source in citation.get("sources") or []:
            if source.get("source") == "metrics_guide" and source.get("metric") in guide.METRIC_ANCHORS:
                seen.setdefault(source["metric"], f"/metrics-guide#{guide.METRIC_ANCHORS[source['metric']]}")
    return [{"name": name, "link": link} for name, link in seen.items()]


class DriftFacts:
    """A one-tool client over the guide keeper's audit facts, so the usual validator checks the draft."""

    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = facts

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": DRIFT, "description": "Where the Metrics guide has fallen behind the deployed website: "
                 "figure labels with no definition, and changed calculation code with the definitions that "
                 "describe it.", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != DRIFT:
            raise ToolCallFailed("unknown_tool", "That tool is not available here.")
        return {"tool": DRIFT, "account_id": None, "as_of": None, "data": self.facts,
                "citations": [{"source": "deployed_source_code"}]}

    def close(self) -> None:
        pass
