"""The only values a design spec can print: a closed catalogue built from the frozen snapshot.

A spec never carries a figure. It names a binding (``trailing_twelve_month_return``,
``top_holdings``...) and a format, and the value comes from here, computed by the
same input builders the hand-written renderers use (``monthly_artifacts.factsheet_inputs``
and the Alpha World metric readers), so a spec-rendered factsheet and the website show
the same numbers. Every figure binding names its Metrics guide definition, and the
module refuses to load if a name is not in the guide.

Formats and text templates are a fixed list too; ``render_text`` is the one place
a template like ``"Historical Performance ({history_start_date|date_short} to
{period_end|date_short})"`` is filled in.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Mapping

from .. import metrics_guide
from .. import monthly_artifacts as legacy


class BindingError(ValueError):
    """A spec names a binding, field or format that does not exist, or the snapshot lacks it."""


@dataclass(frozen=True)
class Binding:
    name: str
    kind: str  # "scalar" | "rows" | "series"
    label: str
    guide: str | None = None
    unit: str = "text"  # "ratio" (0.1234 = 12.34%), "number", "money", "date", "text", "flag"
    fields: tuple[str, ...] = ()
    field_units: Mapping[str, str] = field(default_factory=dict)


def _b(name: str, kind: str, label: str, guide: str | None = None, unit: str = "text",
       fields: tuple[str, ...] = (), **field_units: str) -> Binding:
    return Binding(name, kind, label, guide, unit, fields, dict(field_units))


CATALOGUE: dict[str, Binding] = {b.name: b for b in (
    _b("subject_id", "scalar", "Account or portfolio ID"),
    _b("display_name", "scalar", "Account or portfolio name"),
    _b("reporting_currency", "scalar", "Reporting currency", "Reporting currency"),
    _b("is_composite", "scalar", "Is a composite portfolio", unit="flag"),
    _b("history_start_date", "scalar", "First date of the history", unit="date"),
    _b("period_end", "scalar", "Reporting month-end", unit="date"),
    _b("data_as_of", "scalar", "Last valuation date", "As of", unit="date"),
    _b("display_date", "scalar", "Reporting month-end, as printed"),
    _b("nav", "scalar", "Net asset value", "Net asset value (NAV)", unit="money"),
    _b("positions", "scalar", "Number of positions", "Positions", unit="number"),
    _b("gross_market_value", "scalar", "Gross market value", "Market value", unit="money"),
    _b("trailing_twelve_month_return", "scalar", "Trailing 12-month return", "Trailing 12-month return", unit="ratio"),
    _b("ytd_return", "scalar", "Year-to-date return", "Time-weighted return (TWR)", unit="ratio"),
    _b("month_return", "scalar", "Return for the month", "Monthly return", unit="ratio"),
    _b("since_inception_return", "scalar", "Cumulative return since inception", "Time-weighted return (TWR)",
       unit="ratio"),
    _b("annualized_return", "scalar", "Annualized return", "Annualized return", unit="ratio"),
    _b("annualized_volatility", "scalar", "Annualized volatility", "Annualized volatility", unit="ratio"),
    _b("sharpe_ratio", "scalar", "Sharpe ratio", "Sharpe ratio", unit="number"),
    _b("maximum_drawdown", "scalar", "Maximum drawdown", "Maximum drawdown (since inception)", unit="ratio"),
    _b("var_95_daily", "scalar", "Daily VaR 95%", "VaR 95% — daily", unit="ratio"),
    _b("cvar_95_daily", "scalar", "Daily expected tail loss 95%", "Daily Expected Tail Loss (95%)", unit="ratio"),
    _b("win_rate", "scalar", "Win rate", "Win rate", unit="ratio"),
    _b("total_return", "scalar", "Total return", "Total return", unit="ratio"),
    _b("risk_free_annual_rate", "scalar", "Risk-free rate", "Risk-free rate", unit="ratio"),
    _b("history_heading", "scalar", "Historical Performance table heading"),
    _b("top_holdings", "rows", "Largest holdings by absolute market value", "Holding weight",
       fields=("company_name", "symbol", "region", "sector", "sub_sector", "weight"), weight="ratio"),
    _b("sectors", "rows", "Sector allocation", "Sector allocation",
       fields=("region", "sector", "sub_sector", "weight"), weight="ratio"),
    _b("history", "rows", "Month-end history from inception", "Monthly return",
       fields=("date", "normalized_nav", "monthly_return", "cumulative_return"),
       date="date", normalized_nav="number", monthly_return="ratio", cumulative_return="ratio"),
    _b("history_display", "rows", "Month-end history as printed (inception, ..., latest 36)", "Monthly return",
       fields=("date", "normalized_nav", "monthly_return", "cumulative_return")),
    _b("normalized_nav", "series", "Cash-flow-adjusted NAV index, start = 100",
       "Rebased portfolio or benchmark level", fields=("date", "value", "nav")),
)}

for _binding in CATALOGUE.values():
    if _binding.guide is not None:
        metrics_guide.definition(_binding.guide)  # raises if the guide does not define it


@dataclass
class Context:
    """The values a render may use, computed once per snapshot."""

    scalars: dict[str, Any]
    rows: dict[str, list[dict[str, Any]]]
    series: dict[str, list[dict[str, Any]]]
    inputs: Any = None
    snapshot: Mapping[str, Any] | None = None
    owner_text: dict[str, str] = field(default_factory=dict)

    def scalar(self, name: str) -> Any:
        if name in self.owner_text:
            return self.owner_text[name]
        if name not in CATALOGUE or CATALOGUE[name].kind != "scalar":
            raise BindingError(f"Unknown value: {name}")
        if name not in self.scalars:
            raise BindingError(f"This snapshot has no value for {name}")
        return self.scalars[name]

    def table(self, name: str) -> list[dict[str, Any]]:
        if name not in CATALOGUE or CATALOGUE[name].kind != "rows":
            raise BindingError(f"Unknown table: {name}")
        return self.rows[name]


def _returns(normalized: tuple[dict[str, Any], ...], period_end: str) -> dict[str, float | None]:
    end = date.fromisoformat(period_end)
    by_date = [(date.fromisoformat(r["date"]), float(r["value"])) for r in normalized]
    last = by_date[-1][1]

    def before(limit: date) -> float | None:
        eligible = [v for d, v in by_date if d <= limit]
        return eligible[-1] if eligible else None

    year_start = before(date(end.year - 1, 12, 31))
    month_start = before(date(end.year, end.month, 1) - timedelta(days=1))
    first = by_date[0][1]
    return {
        # A history that starts this year has its YTD from inception.
        "ytd_return": last / (year_start if year_start is not None else first) - 1.0,
        "month_return": None if month_start is None else last / month_start - 1.0,
        "since_inception_return": last / first - 1.0,
    }


def build_context(snapshot: Mapping[str, Any], *, owner_text: Mapping[str, str] | None = None,
                  inputs_builder: Callable[[dict[str, Any]], Any] | None = None) -> Context:
    """Every binding for one frozen snapshot. Never mutates the snapshot."""
    import copy
    frozen = copy.deepcopy(dict(snapshot))
    builder = inputs_builder or legacy.factsheet_inputs
    try:
        inputs = builder(frozen)
    except legacy.MonthlyArtifactError as error:
        raise BindingError(str(error)) from error
    scalars: dict[str, Any] = {
        "subject_id": inputs.subject_id,
        "display_name": str((frozen.get("subject") or {}).get("display_name") or frozen.get("display_name")
                            or inputs.subject_id),
        "reporting_currency": inputs.reporting_currency,
        "is_composite": inputs.is_composite,
        "history_start_date": inputs.history_start_date,
        "period_end": inputs.period_end,
        "data_as_of": inputs.data_as_of,
        "display_date": inputs.display_date,
        "nav": inputs.nav,
        "positions": inputs.positions,
        "gross_market_value": inputs.gross_market_value,
        "trailing_twelve_month_return": inputs.trailing_twelve_month_return,
    }
    scalars.update(_returns(inputs.normalized_nav, inputs.period_end))
    metrics: Mapping[str, Any] | None = inputs.chart_metrics
    if metrics is None and (frozen.get("risk") or {}).get("metrics"):
        try:
            metrics = legacy._alpha_world_metric_values(frozen)
        except legacy.MonthlyArtifactError:
            metrics = None
    for name in ("annualized_return", "annualized_volatility", "sharpe_ratio", "maximum_drawdown",
                 "var_95_daily", "cvar_95_daily", "win_rate", "total_return", "risk_free_annual_rate"):
        if metrics is not None and name in metrics:
            scalars[name] = metrics[name]
    heading, display = legacy._history_display(inputs) if inputs.history else ("", [])
    if inputs.history:
        scalars["history_heading"] = heading
    gross = inputs.gross_market_value
    top = []
    for holding in inputs.top_holdings:
        market_value = legacy._number(holding["market_value_text"], field="holding market value")
        top.append({
            "company_name": str(holding["company_name"]), "symbol": str(holding.get("symbol") or ""),
            "region": str(holding["region"]), "sector": str(holding["sector"]),
            "sub_sector": str(holding["sub_sector"]), "weight": abs(market_value) / gross,
        })
    sectors = [{"region": s["region"], "sector": s["sector"], "sub_sector": s["sub_sector"],
                "weight": float(s["weight"])} for s in inputs.sectors]
    history = [dict(row) for row in inputs.history]
    display_rows = [dict(zip(("date", "normalized_nav", "monthly_return", "cumulative_return"), row))
                    for row in display]
    return Context(
        scalars=scalars,
        rows={"top_holdings": top, "sectors": sectors, "history": history, "history_display": display_rows},
        series={"normalized_nav": [dict(r) for r in inputs.normalized_nav]},
        inputs=inputs, snapshot=frozen, owner_text=dict(owner_text or {}),
    )


# ---- formats -------------------------------------------------------------------------------

def _date(value: Any, style: str) -> str:
    parsed = date.fromisoformat(str(value))
    if style == "short":  # 30 Sep 2026
        return parsed.strftime("%d %b %Y").lstrip("0")
    if style == "long":  # 30 September 2026
        return parsed.strftime("%d %B %Y").lstrip("0")
    if style == "month_day_year":  # September 30 2026
        return parsed.strftime("%B %d %Y").replace(" 0", " ")
    if style == "month_year":  # Sep 2026
        return parsed.strftime("%b %Y")
    if style == "iso":
        return parsed.isoformat()
    raise BindingError(f"Unknown date style: {style}")


FORMAT_KINDS = ("text", "percent", "number", "money", "date", "upper")


def format_value(value: Any, fmt: Mapping[str, Any] | None) -> str:
    """Print one value. ``fmt`` is a spec format object; None prints text as is."""
    fmt = dict(fmt or {"kind": "text"})
    kind = fmt.get("kind", "text")
    if value is None:
        return str(fmt.get("none", "N/A"))
    if kind == "text":
        return legacy._ascii_label(value)
    if kind == "upper":
        return legacy._ascii_label(value).upper()
    if kind == "date":
        return _date(value, str(fmt.get("style", "short")))
    number = float(value)
    if not math.isfinite(number):
        raise BindingError("A figure is not finite")
    if fmt.get("negate_abs"):
        number = -abs(number)
    if fmt.get("abs"):
        number = abs(number)
    digits = int(fmt.get("digits", 2))
    if kind == "percent":
        sign = "+" if fmt.get("signed") else ""
        return f"{number * 100:{sign}.{digits}f}{fmt.get('suffix', '%')}"
    if kind == "number":
        sign = "+" if fmt.get("signed") else ""
        grouping = "," if fmt.get("thousands") else ""
        return f"{number:{sign}{grouping}.{digits}f}"
    if kind == "money":
        return f"{fmt.get('prefix', '')}{number:,.{digits}f}"
    raise BindingError(f"Unknown format: {kind}")


_PIPES: dict[str, dict[str, Any]] = {
    "date_short": {"kind": "date", "style": "short"},
    "date_long": {"kind": "date", "style": "long"},
    "date_mdy": {"kind": "date", "style": "month_day_year"},
    "month_year": {"kind": "date", "style": "month_year"},
    "date_iso": {"kind": "date", "style": "iso"},
    "percent2": {"kind": "percent", "digits": 2},
    "percent1": {"kind": "percent", "digits": 1},
    "signed_percent2": {"kind": "percent", "digits": 2, "signed": True},
    "number0": {"kind": "number", "digits": 0, "thousands": True},
    "number2": {"kind": "number", "digits": 2},
    "money0": {"kind": "money", "digits": 0},
    "money2": {"kind": "money", "digits": 2},
    "upper": {"kind": "upper"},
}
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)(?:\|([a-z0-9_]+))?\}")


def template_names(template: str) -> list[tuple[str, str | None]]:
    return [(m.group(1), m.group(2)) for m in _PLACEHOLDER.finditer(template)]


def render_text(template: str, context: Context, row: Mapping[str, Any] | None = None) -> str:
    """Fill ``{name}`` or ``{name|pipe}`` placeholders; a row's fields shadow scalars."""
    def replace(match: re.Match[str]) -> str:
        name, pipe = match.group(1), match.group(2)
        if row is not None and name in row:
            value = row[name]
        else:
            value = context.scalar(name)
        if pipe is not None and pipe not in _PIPES:
            raise BindingError(f"Unknown format: {pipe}")
        return format_value(value, _PIPES.get(pipe) if pipe else None)
    return _PLACEHOLDER.sub(replace, template)


def resolve(value: Any, context: Context, row: Mapping[str, Any] | None = None) -> str:
    """A spec value: a literal string, a template, ``{"bind": ..., "format": ...}`` or ``{"choose": [...]}``."""
    if isinstance(value, str):
        return render_text(value, context, row)
    if not isinstance(value, Mapping):
        raise BindingError("A value must be text, a binding or a choice")
    if "choose" in value:
        for option in value["choose"]:
            condition = option.get("when")
            if condition is None or _truthy(condition, context):
                return resolve(option["value"], context, row)
        return ""
    if "bind" in value:
        name = value["bind"]
        raw = row[name] if row is not None and name in row else context.scalar(name)
        return format_value(raw, value.get("format"))
    if "field" in value:
        if row is None or value["field"] not in row:
            raise BindingError(f"Unknown column: {value['field']}")
        return format_value(row[value["field"]], value.get("format"))
    raise BindingError("A value must be text, a binding or a choice")


CONDITION_PREFIXES = ("not ", "has ", "missing ")


def condition_name(condition: str) -> str:
    for prefix in CONDITION_PREFIXES:
        if condition.startswith(prefix):
            return condition[len(prefix):]
    return condition


def _truthy(condition: str, context: Context) -> bool:
    """``name`` (true), ``not name`` (false), ``has name`` (a value) or ``missing name`` (no value)."""
    name = condition_name(condition)
    if condition.startswith(("has ", "missing ")):
        present = name in context.scalars and context.scalars[name] is not None
        return present if condition.startswith("has ") else not present
    result = bool(context.scalar(name))
    return not result if condition.startswith("not ") else result
