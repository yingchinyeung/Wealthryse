"""The agent loop: the model picks tools, the tool server answers, the validator checks.

Uses the Responses API on the Foundry endpoint with ``store=False`` so no
conversation state is kept by Azure; reasoning items are passed back
encrypted between steps. The loop yields progress events and ends with one
``answer`` event that has already passed validation, or a ``rejected`` event.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterator, Mapping, Sequence

from . import calculator, certified_tools
from .config import REASONING_MODES, ModelSettings
from .memory import MEMORY_SCHEMA
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
        "remember": MEMORY_SCHEMA,
    },
    "required": ["answer", "citations", "visuals", "remember"],
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
    "get_case": "Reading the case",
    "get_export_check": "Reading the export and its checks",
    "project_wealth": "Simulating future values",
    "run_what_if": "Comparing a different mix",
    "optimise_portfolio": "Measuring where the risk sits",
    "get_factor_exposures": "Fitting the factor model",
    "get_risk_outlook": "Forecasting risk",
    "get_market_regimes": "Reading the market regime",
    "run_factor_stress": "Running a factor stress test",
    "get_metric_definition": "Reading the Metrics guide",
    "search_metrics_guide": "Searching the Metrics guide",
    "get_guide_conformance": "Checking figures against the Metrics guide",
    "get_guide_drift": "Reading what changed in the code",
    certified_tools.GET: "Reading certified figures",
    certified_tools.TRACE: "Tracing where a figure came from",
    certified_tools.LIST: "Listing certified figures",
    calculator.NAME: "Calculating",
}
OUT_OF_SCOPE = ("I can explain your holdings and performance, but I can't recommend trades, rebalancing "
                "or personal investment advice. Ask me what changed or how an account has performed.")
UNVERIFIED = ("I couldn't produce an answer whose figures all match the data, so I haven't shown one. "
              "Try asking about one account and one period.")
MAX_QUESTION_CHARS = 2000
TOO_LONG = "The analysis took too long. Try a narrower question."


LOGGER = logging.getLogger("wealthryse_agent.runtime")
CERTIFIED_NOTE = ("\n\nCertified figures: get_certified_figures returns the stored, checked figures the website "
                  "shows. Prefer it for any figure it serves, and cite each figure you quote by its ref right after "
                  "the number, for example \"Your Sharpe ratio was 1.12 [F1]\". Hypothetical model results keep "
                  "[T#] citations.")


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
    # Preferences the person asked to be remembered; the server checks and saves them.
    remember: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    # Fields a caller added to the answer schema (extra_fields), already through the figure check.
    extra: dict[str, Any] = field(default_factory=dict)
    # Certified figures cited [F#] whose number did not match (warn mode, the default); refs only.
    figure_warnings: list[str] = field(default_factory=list)
    # The reasoning mode the model last answered in: pro drops to standard if Azure refuses it.
    reasoning_mode: str = "standard"


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
        self.pro_refused = False  # set once Azure refuses pro mode; standard mode from then on

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
            reasoning = request.get("reasoning") or {}
            if "mode" in reasoning:
                # Pro is only refused if the same request goes through in standard mode.
                response = self._create(**{**request, "reasoning": {k: v for k, v in reasoning.items() if k != "mode"}})
                LOGGER.warning("pro reasoning mode refused, answering in standard mode: code=%s param=%s", code, param)
                self.pro_refused = True
                return response
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
            skip_router: bool = False, extra_fields: Mapping[str, Any] | None = None,
            reasoning_mode: str = "standard") -> Iterator[dict[str, Any]]:
        """``extra_fields`` adds lists of strings to the answer (for example a checker's concerns); their
        figures are checked with the answer's, and they come back in ``turn.extra``.

        ``reasoning_mode`` ``pro`` asks the model to work longer on every call of this run (website chat)."""
        if reasoning_mode not in REASONING_MODES:
            raise ValueError("The reasoning mode is not one the model accepts")
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
        # AGENT_CERTIFIED_FIGURE_CHECK: warn (the default) or enforce, for the whole turn.
        enforce = certified_tools.figure_check() == "enforce"
        if certified_tools.GET in allowed_names and certified_tools.enabled():
            system += CERTIFIED_NOTE
        conversation: list[dict[str, Any]] = [
            {"role": m["role"], "content": str(m["content"])[:4000]}
            for m in list(history)[-10:] if m.get("role") in ("user", "assistant")
        ]
        conversation.append({"role": "user", "content": question})
        results: dict[str, Any] = {}
        figures: dict[str, dict[str, Any]] = {}  # F1, F2… across the turn
        retried = False
        schema = ANSWER_SCHEMA
        if extra_fields:
            schema = {**ANSWER_SCHEMA, "properties": {**ANSWER_SCHEMA["properties"], **extra_fields},
                      "required": [*ANSWER_SCHEMA["required"], *extra_fields]}

        while True:
            remaining = self.settings.timeout_seconds - (self.clock() - started)
            if remaining < 0:
                raise AgentError("timeout", TOO_LONG)
            reasoning = {"effort": self.settings.reasoning_effort}
            if reasoning_mode == "pro" and not self.pro_refused:
                reasoning["mode"] = "pro"
            # Each call gets only what is left of the question's time, so a slow one ends cleanly.
            try:
                response = self._create(
                    model=self.settings.deployment, instructions=system, input=conversation, tools=function_tools,
                    store=False, include=["reasoning.encrypted_content"], reasoning=reasoning,
                    max_output_tokens=self.settings.max_output_tokens, parallel_tool_calls=True,
                    text={"format": {"type": "json_schema", "name": "answer", "strict": True, "schema": schema}},
                    timeout=max(remaining, 1.0),
                )
            except Exception as error:
                if type(error).__name__ == "APITimeoutError":  # the OpenAI client's own time limit
                    raise AgentError("timeout", TOO_LONG) from error
                raise
            turn.reasoning_mode = "pro" if reasoning_mode == "pro" and not self.pro_refused else "standard"
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
                    output = self._call_tool(tools, call.name, call.arguments, allowed_names, results, account_id,
                                             figures)
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
                remember = [m for m in parsed.get("remember") or [] if isinstance(m, dict)][:4]
                extra = {k: [str(x).strip() for x in (parsed.get(k) if isinstance(parsed.get(k), list) else [])
                             if str(x).strip()][:10]
                         for k in (extra_fields or {})}
            except (ValueError, KeyError, TypeError, AttributeError) as error:
                raise AgentError("invalid_model_output", "The model did not return a usable answer.") from error
            checked = "\n".join([answer, *(x for values in extra.values() for x in values)])
            check = (validate_answer(checked, cited, results, question, visuals, figures or None, figure_check="enforce")
                     if enforce else validate_answer(checked, cited, results, question, visuals, figures or None))
            if check.ok:
                turn.answer, turn.visuals, turn.remember, turn.extra = answer, visuals, remember, extra
                turn.citations = [{"id": c, "tool": results[c]["tool"], "account_id": results[c].get("account_id"),
                                   "as_of": results[c].get("as_of"), "sources": results[c].get("citations", [])}
                                  for c in dict.fromkeys(cited) if c in results]
                if figures:
                    turn.citations += figure_citations(checked, cited, figures, check.figure_checks)
                    turn.figure_warnings = sorted(ref for ref, state in check.figure_checks.items() if state != "ok")
                    if check.figure_warnings:
                        # Warn mode (the default). Refs and figure ids only: never values or text.
                        LOGGER.warning("CERTIFIED_FIGURE_CHECK %s", json.dumps({
                            "warnings": len(check.figure_warnings), "refs": turn.figure_warnings,
                            "figure_ids": sorted({figures[r]["figure_id"] for r in turn.figure_warnings if r in figures})}))
                yield {"type": "answer", "answer": answer, "citations": turn.citations, "visuals": visuals,
                       "turn": turn}
                return
            if check.figure_mode == "enforce" and (check.figure_warnings or check.quoted_elsewhere):
                # Enforce mode: a failed [F#] check is sent back once, then the answer is not shown.
                # Counts, refs and figure ids only: never values or text.
                refs = sorted({ref for ref, state in check.figure_checks.items() if state != "ok"}
                              | set(check.quoted_refs), key=lambda ref: int(ref[1:]))
                LOGGER.warning("CERTIFIED_FIGURE_CHECK %s", json.dumps({
                    "mode": "enforce", "outcome": "rejected" if retried else "retry",
                    "warnings": len(check.figure_warnings), "quoted_elsewhere": len(check.quoted_elsewhere),
                    "refs": refs, "figure_ids": sorted({figures[r]["figure_id"] for r in refs if r in figures})}))
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
                   results: dict[str, Any], account_id: str | None = None,
                   figures: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        if name not in allowed:
            return {"error": {"code": "unknown_tool", "message": "That tool is not available here."}}
        try:
            arguments = json.loads(raw_arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError
        except ValueError:
            return {"error": {"code": "invalid_arguments", "message": "Arguments must be a JSON object."}}
        # A conversation is about one account, even when the person may see others.
        if account_id and "account_id" in arguments and arguments["account_id"] != account_id:
            return {"error": {"code": "other_account", "message": (
                f"This conversation is about account {account_id} only. Tell the person to open the other "
                "account's page and ask there.")}}
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
        if name == certified_tools.GET and figures is not None:
            result = number_figures(result, figures, citation_id)
        elif figures is not None and isinstance(result, dict) and isinstance(result.get("certified"), dict):
            result = number_attached(result, figures, citation_id, name)
        results[citation_id] = result
        return {"citation_id": citation_id, **result}


def number_figures(result: dict[str, Any], figures: dict[str, dict[str, Any]], citation_id: str) -> dict[str, Any]:
    """Give each certified figure in a result the next F# of the turn, as tool calls get T#."""
    numbered = []
    for figure in result.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        ref = f"F{len(figures) + 1}"
        figure = {**figure, "ref": ref}
        figures[ref] = {**figure, "subject": result.get("account_id"), "tool_call": citation_id}
        numbered.append(figure)
    return {**result, "figures": numbered}


def number_attached(result: dict[str, Any], figures: dict[str, dict[str, Any]], citation_id: str,
                    tool: str) -> dict[str, Any]:
    """A figure tool's certified block (AGENT_CERTIFIED_FIGURE_TOOLS): its figures get the next F# too."""
    block = result["certified"]
    numbered = []
    for figure in block.get("figures") or []:
        if not isinstance(figure, dict):
            continue
        ref = f"F{len(figures) + 1}"
        figure = {**figure, "ref": ref}
        figures[ref] = {**figure, "subject": result.get("account_id"), "tool_call": citation_id, "tool": tool}
        numbered.append(figure)
    return {**result, "certified": {**block, "figures": numbered}}


def figure_citations(answer: str, cited: list[str], figures: Mapping[str, Mapping[str, Any]],
                     checks: Mapping[str, str]) -> list[dict[str, Any]]:
    """What the message keeps for each F# the answer cites: enough to fetch and trace that exact figure."""
    refs = [c for c in cited if c in figures]
    refs += [ref for ref in re.findall(r"\[(F\d+)\]", answer) if ref in figures]
    entries = []
    for ref in dict.fromkeys(refs):
        figure = figures[ref]
        lineage, definition = figure.get("lineage") or {}, figure.get("definition") or {}
        entries.append({
            "id": ref, "tool": figure.get("tool") or certified_tools.GET, "account_id": figure.get("subject"),
            "as_of": figure.get("as_of_date"), "certified": True, "check": checks.get(ref, "not_checked"),
            "figure": {"figure_id": figure.get("figure_id"), "label": figure.get("label"),
                       "subject": figure.get("subject"), "as_of_date": figure.get("as_of_date"),
                       "period": figure.get("period_key"), "item": figure.get("item"),
                       "benchmark_id": figure.get("benchmark_id"), "build_id": lineage.get("build_id"),
                       "definition_id": definition.get("id"), "definition_version": definition.get("version"),
                       "link": definition.get("link"), "value": figure.get("value"), "unit": figure.get("unit"),
                       "display": figure.get("display")},
            "sources": [{"source": "certified_figures", "tool_call": figure.get("tool_call")}],
        })
    return entries
