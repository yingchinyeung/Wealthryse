"""The agent loop: the model picks tools, the tool server answers, the validator checks.

Uses the Responses API on the Foundry endpoint with ``store=False`` so no
conversation state is kept by Azure; reasoning items are passed back
encrypted between steps. The loop yields progress events and ends with one
``answer`` event that has already passed validation, or a ``rejected`` event.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterator, Mapping, Sequence

from . import calculator
from .config import ModelSettings
from .prompts import ANALYST, ROUTER
from .tool_client import ToolCallFailed, ToolClient
from .validator import validate_answer

VISUAL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["table", "bar", "line", "pie"]},
        "title": {"type": "string"},
        "x_label": {"type": "string", "description": "Axis or first-column heading; empty if none."},
        "currency": {"type": "string", "description": "ISO code for currency series, else empty."},
        "labels": {"type": "array", "items": {"type": "string"},
                   "description": "Categories, dates (YYYY-MM-DD) or table row names."},
        "series": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "unit": {"type": "string", "enum": ["percent", "currency", "number"],
                         "description": "percent values are fractions as tools return them (0.05 = 5%)."},
                "values": {"type": "array", "items": {"type": ["number", "null"]},
                           "description": "One per label, copied exactly from tool or calculate results."},
            },
            "required": ["name", "unit", "values"],
            "additionalProperties": False,
        }},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "Citation ids, e.g. T1."},
    },
    "required": ["kind", "title", "x_label", "currency", "labels", "series", "sources"],
    "additionalProperties": False,
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "description": "Markdown answer with inline [T#] citations."},
        "citations": {"type": "array", "items": {"type": "string"}},
        "visuals": {"type": "array", "items": VISUAL_SCHEMA,
                    "description": "Charts and tables, only when asked for or clearly helpful; else empty."},
    },
    "required": ["answer", "citations", "visuals"],
    "additionalProperties": False,
}
ROUTER_SCHEMA = {
    "type": "object",
    "properties": {"in_scope": {"type": "boolean"}, "reason": {"type": "string"}},
    "required": ["in_scope", "reason"],
    "additionalProperties": False,
}
TOOL_LABELS = {
    "list_accounts": "Checking your accounts",
    "get_holdings": "Reading holdings",
    "get_holdings_changes": "Comparing holdings between dates",
    "get_performance": "Calculating returns",
    "compare_to_benchmark": "Comparing with the benchmark",
    "get_sector_allocation": "Reading sector allocation",
    "get_cash_flows": "Reading deposits and withdrawals",
    "get_data_freshness": "Checking how recent the data is",
    "get_nav_history": "Reading the value history",
    "get_risk_metrics": "Measuring risk",
    calculator.NAME: "Calculating",
}
OUT_OF_SCOPE = ("I can explain your holdings and performance, but I can't recommend trades, rebalancing "
                "or personal investment advice. Ask me what changed or how an account has performed.")
UNVERIFIED = ("I couldn't produce an answer whose figures all match the data, so I haven't shown one. "
              "Try asking about one account and one period.")
MAX_QUESTION_CHARS = 2000


LOGGER = logging.getLogger("wealthryse_agent.runtime")


def _model_error(error: Exception) -> tuple[int | None, str | None, str | None]:
    """Status, code and parameter of a model API error; never its message, which can quote the prompt."""
    body = getattr(error, "body", None)
    body = body.get("error", body) if isinstance(body, dict) else {}
    body = body if isinstance(body, dict) else {}
    return getattr(error, "status_code", None), body.get("code"), body.get("param")


class AgentError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Turn:
    """Everything the store needs after a run."""
    answer: str = ""
    citations: list[dict[str, Any]] = field(default_factory=list)
    visuals: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


def default_model_client(settings: ModelSettings):
    from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
    from openai import OpenAI

    credential = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    return OpenAI(
        base_url=settings.endpoint.rstrip("/") + "/openai/v1/",
        api_key=get_bearer_token_provider(credential, "https://ai.azure.com/.default"),
        max_retries=1,
        timeout=float(settings.timeout_seconds),
    )


def _add_usage(turn: Turn, response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is not None:
        turn.usage["input_tokens"] += int(getattr(usage, "input_tokens", 0) or 0)
        turn.usage["output_tokens"] += int(getattr(usage, "output_tokens", 0) or 0)


def _item(obj: Any) -> dict[str, Any]:
    return obj.model_dump(exclude_none=True) if hasattr(obj, "model_dump") else dict(obj)


class Agent:
    def __init__(self, settings: ModelSettings, model_client: Any = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        settings.validate()
        self.settings = settings
        self.client = model_client or default_model_client(settings)
        self.clock = clock

    def _create(self, **request: Any) -> Any:
        try:
            return self.client.responses.create(**request)
        except Exception as error:
            status, code, param = _model_error(error)
            if status != 400:
                raise
            # Azure's content filter (including prompt shields) refuses some prompts outright.
            if code == "content_filter":
                raise AgentError("content_filtered", OUT_OF_SCOPE) from error
            LOGGER.warning("model rejected the request: code=%s param=%s", code, param)
            raise AgentError("model_rejected", "The analyst couldn't process that question. Try rephrasing it.") from error

    def in_scope(self, question: str, turn: Turn) -> bool:
        if not self.settings.router_deployment:
            return True
        response = self._create(
            model=self.settings.router_deployment, instructions=ROUTER, input=question, store=False,
            max_output_tokens=200,
            text={"format": {"type": "json_schema", "name": "scope", "strict": True, "schema": ROUTER_SCHEMA}},
        )
        _add_usage(turn, response)
        try:
            return bool(json.loads(response.output_text)["in_scope"])
        except (ValueError, KeyError, TypeError):
            return True  # the analyst's own rules still apply

    def run(self, *, question: str, account_id: str, tools: ToolClient, history: Sequence[Mapping[str, str]] = (),
            allowed_tools: Sequence[str] | None = None, instructions: str | None = None,
            skip_router: bool = False) -> Iterator[dict[str, Any]]:
        question = (question or "").strip()
        if not question or len(question) > MAX_QUESTION_CHARS:
            raise AgentError("invalid_question", f"Questions must be 1 to {MAX_QUESTION_CHARS} characters.")
        turn = Turn()
        started = self.clock()
        if not skip_router and not self.in_scope(question, turn):
            turn.answer, turn.status = OUT_OF_SCOPE, "ok"
            yield {"type": "answer", "answer": turn.answer, "citations": [], "turn": turn}
            return

        catalog = tools.list_tools()
        if allowed_tools is not None:
            catalog = [t for t in catalog if t["name"] in set(allowed_tools)]
        function_tools = [{"type": "function", "name": t["name"], "description": t["description"],
                           "parameters": t["inputSchema"], "strict": False} for t in catalog]
        if allowed_tools is None or calculator.NAME in allowed_tools:
            function_tools.append({"type": "function", "name": calculator.NAME, "description": calculator.DESCRIPTION,
                                   "parameters": calculator.SCHEMA, "strict": False})
        allowed_names = {t["name"] for t in function_tools}
        system = instructions or ANALYST.format(account_id=account_id, today=date.today().isoformat())
        conversation: list[dict[str, Any]] = [
            {"role": m["role"], "content": str(m["content"])[:4000]}
            for m in list(history)[-10:] if m.get("role") in ("user", "assistant")
        ]
        conversation.append({"role": "user", "content": question})
        results: dict[str, Any] = {}
        retried = False

        while True:
            if self.clock() - started > self.settings.timeout_seconds:
                raise AgentError("timeout", "The analysis took too long. Try a narrower question.")
            response = self._create(
                model=self.settings.deployment, instructions=system, input=conversation, tools=function_tools,
                store=False, include=["reasoning.encrypted_content"], reasoning={"effort": self.settings.reasoning_effort},
                max_output_tokens=self.settings.max_output_tokens, parallel_tool_calls=True,
                text={"format": {"type": "json_schema", "name": "answer", "strict": True, "schema": ANSWER_SCHEMA}},
            )
            _add_usage(turn, response)
            if getattr(response, "status", "completed") == "incomplete":
                raise AgentError("model_incomplete", "The model stopped before finishing. Try a narrower question.")
            calls = [o for o in response.output if getattr(o, "type", None) == "function_call"]
            if calls:
                conversation.extend(_item(o) for o in response.output)
                for call in calls:
                    turn.tool_calls += 1
                    if turn.tool_calls > self.settings.max_tool_calls:
                        raise AgentError("too_many_steps", "That needed too many lookups. Try a narrower question.")
                    yield {"type": "status", "text": TOOL_LABELS.get(call.name, "Looking up data")}
                    output = self._call_tool(tools, call.name, call.arguments, allowed_names, results)
                    conversation.append({"type": "function_call_output", "call_id": call.call_id,
                                         "output": json.dumps(output)})
                continue
            if any(getattr(c, "type", None) == "refusal" for o in response.output for c in getattr(o, "content", []) or []):
                turn.answer, turn.status = OUT_OF_SCOPE, "ok"
                yield {"type": "answer", "answer": turn.answer, "citations": [], "turn": turn}
                return
            try:
                parsed = json.loads(response.output_text)
                answer, cited = str(parsed["answer"]).strip(), [str(c) for c in parsed["citations"]]
                visuals = [v for v in parsed.get("visuals") or [] if isinstance(v, dict)]
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise AgentError("invalid_model_output", "The model did not return a usable answer.") from error
            check = validate_answer(answer, cited, results, question, visuals)
            if check.ok:
                turn.answer, turn.visuals = answer, visuals
                turn.citations = [{"id": c, "tool": results[c]["tool"], "account_id": results[c].get("account_id"),
                                   "as_of": results[c].get("as_of"), "sources": results[c].get("citations", [])}
                                  for c in dict.fromkeys(cited) if c in results]
                yield {"type": "answer", "answer": answer, "citations": turn.citations, "visuals": visuals,
                       "turn": turn}
                return
            if retried:
                turn.answer, turn.status = UNVERIFIED, "rejected"
                yield {"type": "rejected", "answer": UNVERIFIED, "reason": check.feedback(), "turn": turn}
                return
            retried = True
            yield {"type": "status", "text": "Double-checking figures"}
            conversation.extend(_item(o) for o in response.output)
            conversation.append({"role": "user", "content": "Your draft failed the figure check. " + check.feedback()
                                 + ". Use only numbers exactly as tools returned them, cite them, and try again."})

    @staticmethod
    def _call_tool(tools: ToolClient, name: str, raw_arguments: str, allowed: set[str],
                   results: dict[str, Any]) -> dict[str, Any]:
        if name not in allowed:
            return {"error": {"code": "unknown_tool", "message": "That tool is not available here."}}
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except ValueError:
            return {"error": {"code": "invalid_arguments", "message": "Arguments must be a JSON object."}}
        try:
            if name == calculator.NAME:
                result = calculator.calculate(arguments, results)
            else:
                result = tools.call(name, arguments)
        except calculator.CalculationError as error:
            return {"error": {"code": "invalid_calculation", "message": str(error)}}
        except ToolCallFailed as error:
            return {"error": {"code": error.code, "message": error.message}}
        citation_id = f"T{len(results) + 1}"
        results[citation_id] = result
        return {"citation_id": citation_id, **result}
