"""The export agent, agent side: the planner and the checker's cover note.

The planner turns a person's request into an export plan. It sees only the
account's shapes and names (datasets, columns, cash activity types,
currencies, dates), never balances or rows, and the website validates its
plan before building anything.

The checker reads the facts the website computed about the built files (row
counts, totals, and the result of every fixed check) through one tool, and
writes the README's cover note. The usual validator rejects any figure that
is not in those facts. It can add concerns, which turn a passing export into
one the person must review, but it can never clear a failed check.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Mapping

from . import calculator
from .prompts import EXPORT_CHECKER, EXPORT_PLANNER
from .runtime import Agent, AgentError, _add_usage, Turn
from .tool_client import ToolCallFailed

EXPORT_TOOL = "get_export_check"
DATASETS = ["performance", "holdings", "cash-activity", "trades"]
GROUPS = ["transaction_type", "currency", "conid", "month", "year", "symbol", "buy_sell", "asset_category",
          "component_type", "base_currency"]
PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "unsupported": {"type": "string"},
        "refusal": {"type": "string"},
        "datasets": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "dataset": {"type": "string", "enum": DATASETS},
                "from_date": {"type": "string", "description": "YYYY-MM-DD, or empty for the start of the history."},
                "to_date": {"type": "string", "description": "YYYY-MM-DD, or empty for the latest data."},
                "columns": {"type": "array", "items": {"type": "string"}},
                "transaction_types": {"type": "array", "items": {"type": "string"}},
                "currencies": {"type": "array", "items": {"type": "string"}},
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["", "BUY", "SELL"]},
                "group_by": {"type": "array", "items": {"type": "string", "enum": GROUPS}},
            },
            "required": ["dataset", "from_date", "to_date", "columns", "transaction_types", "currencies", "symbol",
                         "side", "group_by"],
            "additionalProperties": False,
        }},
    },
    "required": ["title", "summary", "unsupported", "refusal", "datasets"],
    "additionalProperties": False,
}
CONCERNS = {"concerns": {"type": "array", "items": {"type": "string"},
                         "description": "Short sentences; empty when nothing beyond the checks gives you pause."}}
NOT_AN_EXPORT = ("I can prepare exports of this account's value history, holdings, cash activity and trades, "
                 "with totals. Tell me which data, which dates and how to total it.")
MAX_FACTS_BYTES = 200_000


class ExportFacts:
    """A one-tool client over the check facts the website sent, so the validator checks every figure."""

    def __init__(self, account_id: str, facts: Mapping[str, Any]) -> None:
        self.account_id, self.facts = account_id, facts

    def list_tools(self) -> list[dict[str, Any]]:
        return [{"name": EXPORT_TOOL, "description": "The built export's files, totals and check results.",
                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name != EXPORT_TOOL:
            raise ToolCallFailed("unknown_tool", "That tool is not available here.")
        return {"tool": EXPORT_TOOL, "account_id": self.account_id, "as_of": None, "data": self.facts,
                "citations": [{"source": "export_check", "account_id": self.account_id}]}

    def close(self) -> None:
        pass


def plan_export(agent: Agent, *, account_id: str, request: str, facts: Mapping[str, Any]) -> tuple[dict[str, Any], Turn]:
    """The model's plan for the request, unvalidated (the website validates it against the account)."""
    turn = Turn()
    instructions = EXPORT_PLANNER.format(account_id=account_id, facts=json.dumps(facts, default=str),
                                         today=str(facts.get("today") or date.today().isoformat()))
    response = agent._create(
        model=agent.settings.deployment, instructions=instructions, input=request, store=False,
        reasoning={"effort": agent.settings.reasoning_effort}, max_output_tokens=agent.settings.max_output_tokens,
        text={"format": {"type": "json_schema", "name": "export_plan", "strict": True, "schema": PLAN_SCHEMA}},
    )
    _add_usage(turn, response)
    if getattr(response, "status", "completed") == "incomplete":
        raise AgentError("model_incomplete", "The planner stopped before finishing. Try a shorter request.")
    if any(getattr(c, "type", None) == "refusal" for o in response.output for c in getattr(o, "content", []) or []):
        return {"refusal": NOT_AN_EXPORT, "datasets": []}, turn
    try:
        plan = json.loads(response.output_text)
        if not isinstance(plan, dict):
            raise ValueError
    except (ValueError, TypeError, AttributeError) as error:
        raise AgentError("invalid_model_output", "The planner did not return a usable plan.") from error
    return plan, turn


def check_note(agent: Agent, *, account_id: str, facts: Mapping[str, Any]) -> tuple[str, list[str], bool, Turn]:
    """The checker's cover note and concerns; ("", [], False) when its note fails the figure check."""
    run = agent.run(question="Check this export and write its cover note.", account_id=None,
                    tools=ExportFacts(account_id, facts), allowed_tools=[EXPORT_TOOL, calculator.NAME],
                    instructions=EXPORT_CHECKER.format(account_id=account_id), skip_router=True,
                    extra_fields=CONCERNS)
    for event in run:
        if event["type"] == "answer":
            turn = event["turn"]
            return turn.answer, list(turn.extra.get("concerns") or []), True, turn
        if event["type"] == "rejected":
            return "", [], False, event["turn"]
    return "", [], False, Turn()
