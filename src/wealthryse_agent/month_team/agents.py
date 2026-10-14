"""The data agent, analyst, writer, reviewer and visual QA.

The data agent and the analyst are deterministic: they call the read-only
tools and apply fixed rules. The writer, the reviewer's claim checks and
visual QA call the APAC report model with a strict JSON schema; their output
is checked again in code before anything is kept.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

from ibkr_etl.month_end_agent import MonthEndAgentError, _validate_output

from ..config import ModelSettings
from ..runtime import _model_error, default_model_client
from ..tool_client import ToolCallFailed
from . import figures as fig
from .prompts import REVIEWER_COMMENTARY, REVIEWER_DECK, VISUAL_QA, WRITER
from .settings import validate_report_model

ToolCall = Callable[[str, dict[str, Any]], dict[str, Any]]
GRACE_DAYS = 3  # a late price or valuation is "fixable" until the 3rd, then "blocked"
MAX_ONE_DAY_MOVE = 0.20
MAX_VALUATION_GAP_DAYS = 5
STALE_BENCHMARK_DAYS = 4
FIELDS = ("executive_summary", "performance_observations", "risk_observations")


class TeamModelError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BudgetExhausted(Exception):
    pass


class TeamModel:
    """Structured calls to the APAC report deployment, with usage counted against the team's budget."""

    def __init__(self, settings: ModelSettings, *, tokens_left: int, client: Any = None) -> None:
        validate_report_model(settings)
        self.settings = settings
        self.client = client or default_model_client(settings)
        self.tokens_left = tokens_left
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def used(self) -> int:
        return self.usage["input_tokens"] + self.usage["output_tokens"]

    def ask(self, *, name: str, instructions: str, payload: Mapping[str, Any] | str, schema: Mapping[str, Any],
            images: Sequence[bytes] = (), max_output_tokens: int = 4000) -> tuple[dict[str, Any], dict[str, int]]:
        if self.used() >= self.tokens_left:
            raise BudgetExhausted
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(i).decode(),
                     "detail": "high"} for i in images]
        try:
            response = self.client.responses.create(
                model=self.settings.deployment, instructions=instructions, store=False,
                input=[{"role": "user", "content": content}], max_output_tokens=max_output_tokens,
                reasoning={"effort": self.settings.reasoning_effort},
                text={"format": {"type": "json_schema", "name": name, "strict": True, "schema": schema}},
            )
        except Exception as error:
            status, code, _ = _model_error(error)
            if status != 400:
                raise
            raise TeamModelError("content_filtered" if code == "content_filter" else "model_rejected",
                                 "The report model did not accept the request.") from error
        used = {"input_tokens": 0, "output_tokens": 0}
        usage = getattr(response, "usage", None)
        if usage is not None:
            used = {"input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                    "output_tokens": int(getattr(usage, "output_tokens", 0) or 0)}
        for key, value in used.items():
            self.usage[key] += value
        if getattr(response, "status", "completed") == "incomplete":
            raise TeamModelError("model_incomplete", "The report model stopped before finishing.")
        try:
            return json.loads(response.output_text), used
        except (ValueError, TypeError, AttributeError) as error:
            raise TeamModelError("invalid_model_output", "The report model returned unusable output.") from error


@dataclass
class Outcome:
    """One agent step: what it concluded, the objections it raised and the evidence behind them."""
    outcome: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    objections: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)


# Data ------------------------------------------------------------------------


class DataAgent:
    """Reconciles the month: valuations, holdings, benchmark prices, NAV continuity and FX coverage."""

    def check(self, call: ToolCall, account_id: str, period_end: str, *, today: date, replay: bool = False) -> Outcome:
        end = date.fromisoformat(period_end)
        due = fig.last_business_day(end)
        late = "blocked" if replay or today > end + timedelta(days=GRACE_DAYS) else "fixable"
        findings: list[dict[str, str]] = []
        evidence: dict[str, Any] = {"period_end": period_end, "last_business_day": due.isoformat()}

        def finding(severity: str, message: str) -> None:
            findings.append({"severity": severity, "message": message})

        if not replay:
            fresh = call("get_data_freshness", {"account_id": account_id})["data"]
            evidence["freshness"] = {k: fresh.get(k) for k in ("latest_valuation_date", "latest_holdings_date",
                                                                 "last_successful_import")}
            for key, what in (("latest_valuation_date", "valuation"), ("latest_holdings_date", "holdings")):
                seen = fresh.get(key)
                if not seen or date.fromisoformat(seen) < due:
                    finding(late, f"The latest {what} is {seen or 'missing'}; the month needs {due.isoformat()}.")
            for bench in fresh.get("benchmarks") or []:
                seen = bench.get("latest")
                if not seen or date.fromisoformat(seen) < due - timedelta(days=STALE_BENCHMARK_DAYS):
                    finding(late, f"Benchmark {bench.get('benchmark_id')} is priced only to {seen or 'never'}.")

        try:
            performance = call("get_performance", {"account_id": account_id, "as_of": period_end})
            evidence["month_end_valuation"] = performance["as_of"]
            if date.fromisoformat(performance["as_of"]) < due:
                finding(late, f"The last valuation in the month is {performance['as_of']}, before {due.isoformat()}.")
        except ToolCallFailed as error:
            finding("blocked", f"Performance for the month could not be calculated: {error.message}")

        try:
            history = call("get_nav_history", {"account_id": account_id, "from_date": fig.previous_month_end(end).isoformat(),
                                               "to_date": period_end, "frequency": "daily"})["data"]["points"]
            evidence["valuations_in_month"] = len(history)
            for before, after in zip(history, history[1:]):
                gap = (date.fromisoformat(after["date"]) - date.fromisoformat(before["date"])).days
                if gap > MAX_VALUATION_GAP_DAYS:
                    finding("blocked", f"No valuation between {before['date']} and {after['date']}.")
                move = (1 + after["cumulative_return"]) / (1 + before["cumulative_return"]) - 1
                if abs(move) > MAX_ONE_DAY_MOVE:
                    finding("blocked", f"The value moved {'up' if move > 0 else 'down'} by more than a fifth on "
                                       f"{after['date']} after removing deposits and withdrawals; check prices and flows.")
        except ToolCallFailed as error:
            finding("blocked", f"The month's valuations could not be read: {error.message}")

        try:
            call("get_holdings_changes", {"account_id": account_id, "from_date": fig.previous_month_end(end).isoformat(),
                                          "to_date": period_end})
            evidence["fx_coverage"] = "complete"
        except ToolCallFailed as error:
            if error.code == "reporting_fx_unavailable":
                finding(late, f"Exchange rates are missing: {error.message}")
            elif error.code != "not_supported":
                finding("blocked", f"Holdings could not be compared across the month: {error.message}")

        evidence["findings"] = findings
        blocked = [f["message"] for f in findings if f["severity"] == "blocked"]
        if blocked:
            return Outcome("blocked", "Needs a decision: " + blocked[0], evidence)
        if findings:
            return Outcome("fixable", "Waiting for the next sync: " + findings[0]["message"], evidence)
        return Outcome("clean", "The month's data is complete and consistent.", evidence)


# Analyst ---------------------------------------------------------------------


class Analyst:
    def figures(self, call: ToolCall, account_id: str, period_end: str) -> Outcome:
        figure_set = fig.build_figure_set(call, account_id, period_end)
        count = len(figure_set["figures"])
        missing = figure_set["unavailable"]
        summary = f"Built {count} cited figures" + (f"; unavailable: {', '.join(missing)[:300]}." if missing else ".")
        return Outcome("pass" if count else "failed", summary, {"figure_set": figure_set})


# Writer ----------------------------------------------------------------------

_CITED = {"type": "array", "items": {"type": "string"}}
_SENTENCE = {"type": "object", "properties": {"text": {"type": "string", "maxLength": 180}, "figures": _CITED},
             "required": ["text", "figures"], "additionalProperties": False}
WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string", "maxLength": 420},
        "executive_summary_figures": _CITED,
        "performance_observations": {"type": "array", "items": _SENTENCE, "minItems": 2, "maxItems": 4},
        "risk_observations": {"type": "array", "items": _SENTENCE, "minItems": 2, "maxItems": 4},
    },
    "required": ["executive_summary", "executive_summary_figures", "performance_observations", "risk_observations"],
    "additionalProperties": False,
}


def commentary_items(draft: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The draft as numbered sentences with their cited figures, as the reviewer sees it."""
    items = [{"field": "executive_summary", "index": 0, "text": draft["executive_summary"],
              "figures": list(draft.get("executive_summary_figures") or [])}]
    for name in ("performance_observations", "risk_observations"):
        items += [{"field": name, "index": n, "text": s["text"], "figures": list(s["figures"])}
                  for n, s in enumerate(draft[name])]
    return items


def plain_commentary(draft: Mapping[str, Any]) -> dict[str, Any]:
    """The report's commentary shape (MONTH_END_OUTPUT_SCHEMA), as the factsheet job would print it."""
    return {"executive_summary": draft["executive_summary"],
            "performance_observations": [s["text"] for s in draft["performance_observations"]],
            "risk_observations": [s["text"] for s in draft["risk_observations"]]}


class Writer:
    def __init__(self, model: TeamModel) -> None:
        self.model = model

    def draft(self, figure_set: Mapping[str, Any], *, account_id: str, previous: Mapping[str, Any] | None = None,
              objections: Sequence[Mapping[str, Any]] = (), last_draft: Mapping[str, Any] | None = None) -> Outcome:
        payload = {"account": account_id, "period_end": figure_set["period_end"], "benchmark": figure_set.get("benchmark"),
                   "figures": [{k: f[k] for k in ("id", "label", "display")} for f in figure_set["figures"]],
                   "unavailable": figure_set.get("unavailable", []), "previous_month_text": previous,
                   "your_last_draft": last_draft, "reviewer_objections": list(objections)}
        usage = {"input_tokens": 0, "output_tokens": 0}
        feedback = None
        for attempt in (1, 2):
            if feedback:
                payload["format_problem"] = feedback
            draft, used = self.model.ask(name="month_end_commentary", instructions=WRITER, payload=payload,
                                         schema=WRITER_SCHEMA, max_output_tokens=3000)
            for key in usage:
                usage[key] += used[key]
            feedback = self._format_problem(draft, figure_set)
            if feedback is None:
                commentary = plain_commentary(draft)
                return Outcome("pass", "Drafted the commentary from the figure set.",
                               {"draft": draft, "commentary": commentary}, usage=usage)
        return Outcome("failed", "The writer's draft broke the commentary rules twice: " + feedback[:400],
                       {"draft": draft}, usage=usage)

    @staticmethod
    def _format_problem(draft: Mapping[str, Any], figure_set: Mapping[str, Any]) -> str | None:
        try:
            _validate_output(plain_commentary(draft))
        except (MonthEndAgentError, KeyError, TypeError) as error:
            return str(error)
        known = set(fig.figure_lookup(figure_set))
        for item in commentary_items(draft):
            unknown = [i for i in item["figures"] if i not in known]
            if not item["figures"] or unknown:
                return (f"{item['field']} {item['index']} cites no figure" if not item["figures"]
                        else f"{item['field']} {item['index']} cites figures that are not in the set: {', '.join(unknown)}")
        return None


# Reviewer --------------------------------------------------------------------

_OBJECTION = {"type": "object", "properties": {
    "field": {"type": "string", "enum": list(FIELDS)}, "index": {"type": "integer"}, "message": {"type": "string"}},
    "required": ["field", "index", "message"], "additionalProperties": False}
REVIEW_SCHEMA = {"type": "object", "properties": {"objections": {"type": "array", "items": _OBJECTION}},
                 "required": ["objections"], "additionalProperties": False}
_DECK_OBJECTION = {"type": "object", "properties": {"slide": {"type": "integer"}, "message": {"type": "string"}},
                   "required": ["slide", "message"], "additionalProperties": False}
DECK_REVIEW_SCHEMA = {"type": "object", "properties": {"objections": {"type": "array", "items": _DECK_OBJECTION}},
                      "required": ["objections"], "additionalProperties": False}


class Reviewer:
    def __init__(self, model: TeamModel) -> None:
        self.model = model

    def review_commentary(self, draft: Mapping[str, Any], figure_set: Mapping[str, Any], *, round: int) -> Outcome:
        """Cold check of the writer's output: the code's direction check, then the model's claim check."""
        items = commentary_items(draft)
        objections = fig.direction_conflicts(items, figure_set)
        lookup = fig.figure_lookup(figure_set)
        payload = {"period_end": figure_set["period_end"], "benchmark": figure_set.get("benchmark"),
                   "figures": [{k: f[k] for k in ("id", "label", "display")} for f in figure_set["figures"]],
                   "unavailable": figure_set.get("unavailable", []),
                   "commentary": [{**i, "cited": [lookup[f]["label"] + ": " + lookup[f]["display"]
                                                  for f in i["figures"] if f in lookup]} for i in items]}
        result, usage = self.model.ask(name="commentary_review", instructions=REVIEWER_COMMENTARY, payload=payload,
                                       schema=REVIEW_SCHEMA, max_output_tokens=2500)
        valid = {(i["field"], i["index"]) for i in items}
        seen = {(o["field"], o["index"]) for o in objections}
        for objection in result.get("objections", []):
            key = (objection.get("field"), objection.get("index"))
            if key in valid and key not in seen and str(objection.get("message") or "").strip():
                objections.append({"field": key[0], "index": key[1], "message": str(objection["message"]).strip()[:500]})
                seen.add(key)
        normalised = [{**o, "target": "commentary", "message": f"{o['field'].replace('_', ' ').capitalize()} "
                                                                f"{o['index'] + 1}: {o['message']}"} for o in objections]
        if normalised:
            return Outcome("objection", f"Round {round}: {len(normalised)} objection(s) to the commentary.",
                           {"objections": objections}, normalised, usage)
        return Outcome("pass", f"Round {round}: every sentence is supported by the figures it cites.", {}, [], usage)

    def check_files(self, *, account_id: str, period_end: str, snapshot: Mapping[str, Any] | None,
                    factsheet_pages: Sequence[str] | None, deck_slides: Sequence[str] | None,
                    figure_set: Mapping[str, Any], inception_date: str | None) -> Outcome:
        """Re-derive every printed figure from the frozen snapshot and check the deck's narrative."""
        objections: list[dict[str, Any]] = []
        evidence: dict[str, Any] = {}
        usage: dict[str, int] = {}
        if snapshot is not None and factsheet_pages is not None:
            expected = fig.factsheet_expectations(dict(snapshot))
            if "skipped" in expected:
                evidence["factsheet_figures"] = expected["skipped"]
            elif "error" in expected:
                objections.append({"target": "factsheet", "page": None, "message": expected["error"]})
            else:
                problems = fig.check_factsheet_text(expected, factsheet_pages)
                objections += [{"target": "factsheet", **p} for p in problems]
                checked = sum(len(v) for page in ("page_1", "page_2") for v in expected[page].values())
                evidence["factsheet_figures"] = f"{checked} printed values checked against the frozen snapshot."
                objections += [{"target": "data", "page": None, "message": m}
                               for m in fig.check_against_live(expected, figure_set, inception_date)]
        if deck_slides is not None:
            objections += [{"target": "presentation", **p} for p in fig.check_deck_text(account_id, period_end, deck_slides)]
            lines = fig.deck_commentary(deck_slides)
            evidence["deck_narrative_lines"] = len(lines)
            if lines:
                payload = {"period_end": period_end, "benchmark": figure_set.get("benchmark"),
                           "figures": [{k: f[k] for k in ("id", "label", "display")} for f in figure_set["figures"]],
                           "unavailable": figure_set.get("unavailable", []), "deck_lines": lines}
                result, usage = self.model.ask(name="deck_review", instructions=REVIEWER_DECK, payload=payload,
                                               schema=DECK_REVIEW_SCHEMA, max_output_tokens=2000)
                slides = {line["slide"] for line in lines}
                objections += [{"target": "presentation", "page": int(o["slide"]),
                                "message": "Narrative: " + str(o["message"]).strip()[:500]}
                               for o in result.get("objections", []) if o.get("slide") in slides and o.get("message")]
        if objections:
            return Outcome("objection", f"{len(objections)} objection(s) to the package files.", evidence, objections, usage)
        return Outcome("pass", "Every printed figure matches the frozen snapshot and the deck's narrative is supported.",
                       evidence, [], usage)


# Visual QA -------------------------------------------------------------------

_REGION = {"type": "object", "properties": {k: {"type": "number"} for k in ("x", "y", "w", "h")},
           "required": ["x", "y", "w", "h"], "additionalProperties": False}
_FINDING = {"type": "object", "properties": {
    "page": {"type": "integer"},
    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
    "kind": {"type": "string", "enum": ["blank_area", "clipped_text", "overlap", "missing_element", "wrong_period",
                                        "stale_date", "other"]},
    "description": {"type": "string"}, "region": _REGION},
    "required": ["page", "severity", "kind", "description", "region"], "additionalProperties": False}
VISUAL_SCHEMA = {"type": "object", "properties": {"findings": {"type": "array", "items": _FINDING}},
                 "required": ["findings"], "additionalProperties": False}


def _clamp(region: Mapping[str, Any]) -> dict[str, float]:
    x = min(max(float(region.get("x", 0)), 0.0), 1.0)
    y = min(max(float(region.get("y", 0)), 0.0), 1.0)
    return {"x": x, "y": y, "w": min(max(float(region.get("w", 0)), 0.0), 1.0 - x),
            "h": min(max(float(region.get("h", 0)), 0.0), 1.0 - y)}


class VisualQA:
    def __init__(self, model: TeamModel) -> None:
        self.model = model

    def inspect_factsheet(self, images: Sequence[bytes], *, display_date: str, period_label: str) -> Outcome:
        if not images:
            return Outcome("skipped", "No factsheet pages to look at.")
        note = {"report_date": display_date, "pages": [
            {"page": 1, "must_show": ["report date at the top and beside the return panel", "Top 5 table",
                                      "sector allocation table with a total row", f"historical performance table ({period_label})",
                                      "trailing twelve-month return panel"]},
            {"page": 2, "must_show": ["report date", f"historical performance chart ({period_label})",
                                      "axis labels and gridlines"]}][:len(images)]}
        try:
            result, usage = self.model.ask(name="visual_qa", instructions=VISUAL_QA, payload=note, schema=VISUAL_SCHEMA,
                                           images=images, max_output_tokens=3000)
        except TeamModelError as error:
            if error.code == "model_rejected":
                return Outcome("skipped", "The report model did not accept page images, so the pages were not "
                                          "looked at. Visual QA needs an APAC deployment that reads images.",
                               {"error": error.code})
            raise
        findings = [f for f in result.get("findings", []) if 1 <= int(f.get("page", 0)) <= len(images)]
        objections = [{"target": "factsheet", "page": int(f["page"]), "region": _clamp(f.get("region") or {}),
                       "message": f"{f['kind'].replace('_', ' ').capitalize()} ({f['severity']}): "
                                  f"{str(f['description']).strip()[:450]}"}
                      for f in findings if f.get("severity") in ("high", "medium")]
        notes = [{"page": int(f["page"]), "description": str(f["description"])[:300]}
                 for f in findings if f.get("severity") == "low"]
        evidence = {"pages": len(images), "cosmetic_notes": notes}
        if objections:
            return Outcome("objection", f"{len(objections)} layout fault(s) on the factsheet pages.", evidence,
                           objections, usage)
        return Outcome("pass", f"Looked at {len(images)} factsheet page(s); no layout faults.", evidence, [], usage)
