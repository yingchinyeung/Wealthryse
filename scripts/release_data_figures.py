"""The release data report's figures command: each page's figures the old way against the
certified figures, on a restored copy (certified figures, U5, build plan part 6).

For every active account and portfolio, for the latest NAV date, every month-end and a
fixed sample of other NAV dates, it takes the figures a page shows today (by calling the
website's own API functions with the certified pages switched off, exactly as the page
loads them) and the certified figures readers would see (gold.v_certified_figures), and
compares them figure by figure:

- ratio and number: equal within a relative 1e-9 (the release report's tolerance);
- money: to the cent;
- date and text: exactly.

A figure the page shows that has no certified value, and a certified value the page leaves
blank, are differences too (the second is often expected: a figure the page used to leave
blank that the build can now fill). Figures the page works out in the browser from the
day it is read (Data freshness) and figures this command does not read yet are listed as
not compared, with the reason.

The full report, with values, goes only to a private folder named on the command line;
accounts in it appear as u…#n and other subjects as s…#n. The public summary, for the PR,
holds counts, figure ids and page names only. docs/MEDALLION_RELEASE_REPORT.md has the steps.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ibkr_etl import certified_figures as register  # noqa: E402  always the register through core's module

REPORT = "certified-figures-report"
REL_TOL = 1e-9
ABS_TOL = 1e-12
CENT = 0.005
DEFAULT_SAMPLE = 12
ACCOUNT_ID = re.compile(r"[A-Z]{1,2}\d{5,}")
OK = ("fail", "empty")  # period statuses the page shows as a dash
# Figures this command does not compare, and why. Shown in both reports.
NOT_COMPARED = {
    "overview.data_freshness": "worked out in the browser from the day the page is read",
    "benchmark.rebased_level": "a chart series: compared with the charts after month-end",
    "risk.crisis_scenario_max_drawdown": "read from the crisis scenarios table: not compared yet",
}
# The pages whose figures are another page's, read when the page is opened.
BORROWED = {"combined-holdings": "each account's Holdings figures, compared under holdings"}


class FiguresError(RuntimeError):
    pass


# ---- the register (always through ibkr_etl.certified_figures) ----

def pages_for(batches: Iterable[str] = (), pages: Iterable[str] = ()) -> list[str]:
    known = register.pages()
    chosen = list(dict.fromkeys(pages))
    for batch in batches:
        found = register.register()["batches"].get(str(batch))
        if found is None:
            raise FiguresError(f"batch {batch} is not in the register")
        chosen += [page for page in found["pages"] if page not in chosen]
    unknown = [page for page in chosen if page not in known]
    if unknown:
        raise FiguresError("unknown page: " + ", ".join(unknown))
    if not chosen:
        raise FiguresError("name a --batch or a --page")
    return sorted(chosen, key=lambda page: known[page].order)


def own_figures(page: str) -> dict[str, "register.Figure"]:
    """The figures whose own page this is (borrowed ones are compared on their own page)."""
    return {item.id: item for item in register.figures().values() if item.page == page}


# ---- one figure value ----

Key = tuple  # (figure_id, period, item, benchmark_id)


def _number(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(str(value).replace(",", "")) if isinstance(value, str) else float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _text(value) -> str | None:
    if value is None or value == "":
        return None
    return str(value)[:10] if isinstance(value, date) else str(value)


def same(unit: str, left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if unit in ("date", "text"):
        return str(left) == str(right)
    if unit == "money":
        return abs(left - right) <= CENT + ABS_TOL
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


# ---- today's figures, from the website's own API functions ----

def _ok(period: Mapping | None) -> bool:
    return bool(period) and period.get("status") not in OK


def overview_today(api, subject: str, day: str) -> dict[Key, Any]:
    """The account page header and Overview, as web_ui.renderOverview shows them."""
    found: dict[Key, Any] = {}
    summary = api.call("summary", subject, day)
    if summary is not None:
        latest = summary.get("latest_nav") or {}
        found[("overview.as_of", "", "", "")] = _text(summary.get("effective_as_of"))
        found[("overview.nav", "", "", "")] = _number(latest.get("total_text"))
        found[("overview.reporting_currency", "", "", "")] = _text(latest.get("currency"))
        found[("overview.observations", "", "", "")] = _number((summary.get("nav_coverage") or {}).get("observations"))
        found[("overview.positions", "", "", "")] = _number(summary.get("current_positions"))
    periods = api.call("periods_with_day", subject, day)
    if periods is not None:
        custom = periods.get("custom")
        found[("overview.day_change", "", "", "")] = _number(custom.get("twr")) if _ok(custom) else None
        flows = periods.get("flows_applicable") is not False
        for period in periods.get("periods") or []:
            key, good = str(period["key"]), _ok(period)
            found[("overview.twr", key, "", "")] = _number(period.get("twr")) if good else None
            if key.startswith("year_") or key == "since_inception":
                found[("overview.twr_annualised", key, "", "")] = _number(period.get("twr_annualized")) if good else None
            found[("overview.irr_annualised", key, "", "")] = _number(period.get("irr_annualized")) if good else None
            found[("overview.net_external_flows", key, "", "")] = (
                _number(period.get("net_flows")) if good and flows else None)
    return found


def accounts_list_today(api) -> dict[str, tuple[str | None, dict[Key, Any]]]:
    """The accounts list: each account's latest NAV, its date, and MTD and YTD TWR."""
    listed = api.call("accounts_overview", None, None) or {"accounts": []}
    found = {}
    for entry in listed.get("accounts") or []:
        day = _text(entry.get("nav_date"))
        found[str(entry["account_id"])] = (day, {
            ("overview.as_of", "", "", ""): day,
            ("overview.nav", "", "", ""): _number(entry.get("nav")),
            ("overview.twr", "mtd", "", ""): _number(entry.get("mtd")),
            ("overview.twr", "ytd", "", ""): _number(entry.get("ytd")),
        })
    return found


def holdings_today(api, subject: str, day: str) -> dict[Key, Any]:
    """The Holdings page: per holding, the account totals, concentration and sectors."""
    found: dict[Key, Any] = {}
    view = api.call("holdings", subject, day)
    if view is not None:
        for row in view.get("holdings") or []:
            item = str(row.get("conid"))
            for figure, name in (("holdings.quantity", "quantity_text"), ("holdings.market_value", "market_value_text"),
                                 ("holdings.value_in_reporting_currency", "reporting_market_value_text"),
                                 ("holdings.cost_basis", "cost_basis_text"),
                                 ("holdings.unrealized_pnl", "unrealized_pnl_text"),
                                 ("holdings.pnl_pct", "unrealized_pnl_pct"), ("holdings.holding_weight", "weight")):
                if name in row:
                    found[(figure, "", item, "")] = _number(row.get(name))
        if "gross_holdings_text" in view:
            found[("holdings.gross_market_value", "", "", "")] = _number(view.get("gross_holdings_text"))
        concentration = view.get("concentration")
        if concentration is not None:
            available = bool(concentration.get("available"))
            pick = (lambda name: _number(concentration.get(name)) if available else None)
            found[("holdings.largest_holding", "", "", "")] = (
                _number((concentration.get("largest") or {}).get("weight")) if available else None)
            found[("holdings.top_5_weight", "", "", "")] = pick("top_5_weight")
            found[("holdings.top_10_weight", "", "", "")] = pick("top_10_weight")
            found[("holdings.effective_holdings", "", "", "")] = pick("effective_holdings")
            for row in concentration.get("currency_exposure") or [] if available else []:
                found[("holdings.currency_exposure", "", str(row["currency"]).upper(), "")] = _number(row.get("weight"))
    sectors = api.call("sectors", subject, day)
    if sectors is not None and sectors.get("available") is not False:
        for row in sectors.get("allocation") or []:
            found[("holdings.sector_allocation", "", str(row.get("sector")), "")] = _number(row.get("weight"))
    return found


def cash_today(api, subject: str, day: str) -> dict[Key, Any]:
    """The Cash page's balances, one per currency (and IBKR's base summary)."""
    found: dict[Key, Any] = {}
    cash = api.call("cash", subject, day)
    for row in (cash or {}).get("balances") or []:
        item = str(row.get("currency"))
        found[("cash.ending_cash", "", item, "")] = _number(row.get("ending_cash_text"))
        found[("cash.settled_cash", "", item, "")] = _number(row.get("settled_cash_text"))
    return found


BENCHMARK_FIGURES = ("portfolio_total_return", "benchmark_total_return", "excess_total_return", "tracking_error",
                     "information_ratio", "beta", "correlation", "relative_win_rate")


def performance_today(api, subject: str, day: str) -> dict[Key, Any]:
    """Performance: monthly and yearly returns, and the comparison with the primary benchmark.
    Its period returns and contributions are Overview's figures, compared under overview."""
    found: dict[Key, Any] = {}
    analytics = api.call("analytics", subject, day)
    if analytics is not None and analytics.get("available"):
        for row in analytics.get("monthly_calendar") or []:
            year = int(row["year"])
            for index, cell in enumerate(row.get("months") or []):
                if cell is not None:
                    found[("performance.monthly_return", f"month_{year}-{index + 1:02d}", "", "")] = _number(cell.get("twr"))
            if row.get("year_return") is not None:
                found[("performance.monthly_return", f"year_{year}", "", "")] = _number(row["year_return"].get("twr"))
    comparison = api.call("benchmark", subject, day)
    if comparison is not None and comparison.get("available"):
        benchmark = str((comparison.get("benchmark") or {}).get("benchmark_id") or "")
        metrics = comparison.get("metrics") or {}
        for name in BENCHMARK_FIGURES:
            found[(f"benchmark.{name}", "", "", benchmark)] = _number(metrics.get(name))
    return found


RISK_FIGURES = {
    "risk.total_return": "total_return", "risk.annualized_return": "annualized_return",
    "risk.annualized_volatility": "annualized_volatility", "risk.risk_free_rate": "risk_free_annual_rate",
    "risk.sharpe_ratio": "sharpe_ratio", "risk.expected_max_drawdown_1y": "expected_maximum_drawdown_1y",
    "risk.actual_max_drawdown_1y": "actual_maximum_drawdown_1y", "risk.tail_max_drawdown_1y": "tail_maximum_drawdown_1y",
    "risk.crisis_max_drawdown_2008": "crisis_maximum_drawdown", "risk.var_95_daily": "var_95_daily",
    "risk.etl_95_daily": "cvar_95_daily", "risk.win_rate": "win_rate",
    "risk.blue_mean_daily_return": "blue_mean_daily_return", "risk.maximum_drawdown": "maximum_drawdown",
}


def risk_snapshot_today(api, subject: str, day: str) -> dict[Key, Any]:
    """Risk › Risk snapshot: the stored snapshot's metrics and its tail-loss contributors."""
    found: dict[Key, Any] = {}
    risk = api.call("risk", subject, day)
    if risk is not None:
        metrics = risk.get("metrics") or {}
        for figure, name in RISK_FIGURES.items():
            found[(figure, "", "", "")] = _number(metrics.get(name))
    details = api.call("risk_details", subject, day)
    if details is not None:
        contributors = details.get("expected_tail_loss_contributors") or {}
        for row in (contributors.get("top") or []) + (contributors.get("bottom") or []):
            found[("risk.etl_contribution", "", str(row.get("conid")), "")] = _number(row.get("expected_tail_loss_contribution"))
        if "residual_contribution" in contributors:
            found[("risk.etl_residual_contribution", "", "", "")] = _number(contributors.get("residual_contribution"))
    return found


STATISTICS = {
    "stats.sortino_ratio": "sortino_ratio", "stats.calmar_ratio": "calmar_ratio",
    "stats.annualized_volatility": "annualized_volatility", "stats.downside_deviation": "downside_deviation",
    "stats.skewness": "skewness", "stats.excess_kurtosis": "excess_kurtosis",
    "stats.positive_months": "positive_month_ratio", "stats.average_gain_month": "average_gain_month",
    "stats.average_loss_month": "average_loss_month", "stats.gain_loss_ratio": "gain_loss_ratio",
}


def risk_statistics_today(api, subject: str, day: str) -> dict[Key, Any]:
    """Risk › Statistics and Drawdowns, from the same analytics answer the page reads."""
    found: dict[Key, Any] = {}
    analytics = api.call("analytics", subject, day)
    if analytics is None:
        return found
    available = bool(analytics.get("available"))
    stats = analytics.get("statistics") or {}
    for figure, name in STATISTICS.items():
        found[(figure, "", "", "")] = _number(stats.get(name)) if available else None
    for figure, name in (("stats.best_day", "best_day"), ("stats.worst_day", "worst_day"),
                         ("stats.best_month", "best_month"), ("stats.worst_month", "worst_month")):
        found[(figure, "", "", "")] = _number((stats.get(name) or {}).get("return")) if available else None
    drawdown = analytics.get("drawdown") or {}
    found[("stats.maximum_drawdown", "", "", "")] = _number(drawdown.get("maximum_drawdown")) if available else None
    found[("stats.current_drawdown", "", "", "")] = _number(drawdown.get("current_drawdown")) if available else None
    rolling = [row for row in analytics.get("rolling") or [] if row.get("trailing_return") is not None]
    last = rolling[-1] if rolling and rolling[-1].get("date") == analytics.get("effective_as_of") else None
    found[("stats.trailing_12m_return", "", "", "")] = _number(last.get("trailing_return")) if last else None
    return found


TODAY: dict[str, Callable] = {
    "overview": overview_today, "holdings": holdings_today, "cash": cash_today, "performance": performance_today,
    "risk-snapshot": risk_snapshot_today, "risk-statistics": risk_statistics_today,
}


class WebPages:
    """Today's page answers, from the website's own API functions on the copy.

    The certified pages setting is cleared first, so every page answers the old way. The
    calls are read-only (the website opens each connection read-only) and run as an owner,
    who may see every active account, as the person approving the release does."""

    USER = {"authorization_source": "database", "roles": ["owner"], "object_id": "release-data-report",
            "email": "release-data-report"}

    def __init__(self, url: str, tenant: str) -> None:
        os.environ.pop(PAGES_SETTING, None)
        os.environ["DATABASE_URL"] = url
        from ibkr_etl import web

        web.TENANT_ID = tenant
        self.web = web
        self.cache: dict[tuple, Any] = {}
        self.unavailable: Counter = Counter()

    def _request(self):
        from types import SimpleNamespace

        return SimpleNamespace(state=SimpleNamespace(correlation_id="release-data-report"))

    def _answer(self, name: str, subject: str | None, day: str | None):
        web, user = self.web, self.USER
        calls = {
            "summary": lambda: web.portfolio_summary(subject, as_of=day, user=user),
            "periods_with_day": lambda: web.performance_periods_view(subject, as_of=day, start=day, end=day, user=user),
            "accounts_overview": lambda: web.accounts_overview(user=user),
            "holdings": lambda: web.holdings(subject, as_of=day, user=user),
            "sectors": lambda: web.sector_allocation(subject, self._request(), as_of=day, user=user),
            "cash": lambda: web.cash_activity(subject, as_of=day, user=user),
            "analytics": lambda: web.portfolio_analytics_view(subject, as_of=day, user=user),
            "benchmark": lambda: web.benchmark_comparison(subject, as_of=day, user=user),
            "risk": lambda: web.risk_analytics(subject, as_of=day, user=user),
            "risk_details": lambda: web.risk_analytics_details(subject, as_of=day, user=user),
        }
        return json.loads(json.dumps(calls[name](), default=str))

    def call(self, name: str, subject: str | None, day: str | None):
        """The API answer, or None when the page shows an error (counted by status)."""
        key = (name, subject, day)
        if key not in self.cache:
            from fastapi import HTTPException

            try:
                self.cache[key] = self._answer(name, subject, day)
            except HTTPException as error:
                self.unavailable[f"{name}: HTTP {error.status_code}"] += 1
                self.cache[key] = None
            except Exception as error:  # noqa: BLE001 - the page would show an error; count it, never its text
                self.unavailable[f"{name}: {type(error).__name__}"] += 1
                self.cache[key] = None
        return self.cache[key]


# ---- the certified figures, as readers see them ----

CERTIFIED_SQL = """
    SELECT subject_id, figure_id, period, item, benchmark_id, as_of_date::text AS as_of_date,
           value, value_text, unit, status
      FROM gold.v_certified_figures
     WHERE subject_id = ANY(%s) AND as_of_date::text = ANY(%s) AND figure_id = ANY(%s)
"""


def certified_rows(connection, subjects: list[str], days: list[str], figures: list[str]) -> list[dict]:
    if connection.execute("SELECT to_regclass('gold.v_certified_figures')").fetchone()[0] is None:
        raise FiguresError("gold.v_certified_figures is not in this database: apply migration 072 and run the build")
    cursor = connection.execute(CERTIFIED_SQL, (subjects, days, figures))
    names = [column.name for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def certified_value(row: Mapping) -> Any:
    if row.get("status") != "ok":
        return None
    if row["unit"] in ("date", "text"):
        return _text(row.get("value_text"))
    return _number(row.get("value"))


# ---- which subjects and dates ----

def subjects_and_dates(connection, *, only: list[str] | None, sample: int) -> dict[str, list[str]]:
    """Active accounts and portfolios, each with its latest NAV date, every month-end NAV date
    and a fixed sample of its other NAV dates (the same sample every run)."""
    rows = connection.execute("""
        SELECT s.subject_id, n.snapshot_date::text
          FROM platform_subjects s
          JOIN subject_nav_series n ON n.tenant_id = s.tenant_id AND n.subject_id = s.subject_id
               AND n.currency = COALESCE(s.reporting_currency, s.base_currency)
         WHERE s.lifecycle_status = 'active' AND s.subject_type <> 'benchmark'
         ORDER BY 1, 2""").fetchall()
    dates: dict[str, list[str]] = defaultdict(list)
    for subject, day in rows:
        if not only or subject in only:
            dates[subject].append(day)
    return {subject: pick_dates(days, sample, subject) for subject, days in sorted(dates.items())}


def pick_dates(days: list[str], sample: int, salt: str = "") -> list[str]:
    days = sorted(set(days))
    if not days:
        return []
    chosen = {days[-1]}
    for previous, current in zip(days, days[1:]):
        if previous[:7] != current[:7]:
            chosen.add(previous)  # the last NAV date of each month
    rest = sorted((day for day in days if day not in chosen),
                  key=lambda day: hashlib.sha256(f"{salt}|{day}".encode()).hexdigest())
    chosen.update(rest[:max(sample, 0)])
    return sorted(chosen)


# ---- comparing ----

@dataclass
class Comparison:
    pages: list[str]
    compared: Counter = field(default_factory=Counter)        # page -> figures compared
    matched: Counter = field(default_factory=Counter)         # page -> matching
    blank: Counter = field(default_factory=Counter)           # page -> blank on both
    differences: list[dict] = field(default_factory=list)
    by_figure: dict = field(default_factory=lambda: defaultdict(Counter))  # figure -> kind -> count
    subjects: set = field(default_factory=set)
    dates: set = field(default_factory=set)
    not_compared: dict = field(default_factory=dict)
    unavailable: Counter = field(default_factory=Counter)

    def add(self, page: str, subject: str, day: str, key: Key, unit: str, today, certified, source: str) -> None:
        self.compared[page] += 1
        self.subjects.add(subject)
        self.dates.add(day)
        figure = key[0]
        if same(unit, today, certified):
            if today is None:
                self.blank[page] += 1
            else:
                self.matched[page] += 1
            self.by_figure[figure]["matched"] += 1
            return
        kind = ("page_only" if certified is None else "certified_only" if today is None else "different")
        self.by_figure[figure][kind] += 1
        self.differences.append({"page": page, "source": source, "subject": subject, "as_of_date": day,
                                 "figure_id": figure, "period": key[1], "item": key[2], "benchmark_id": key[3],
                                 "unit": unit, "kind": kind, "page_value": today, "certified_value": certified})


def _stored(rows: Iterable[Mapping]) -> dict[tuple, dict[Key, Any]]:
    stored: dict[tuple, dict[Key, Any]] = defaultdict(dict)
    for row in rows:
        stored[(row["subject_id"], row["as_of_date"])][
            (row["figure_id"], row["period"] or "", row["item"] or "", row["benchmark_id"] or "")] = certified_value(row)
    return stored


def compare(pages: list[str], api, connection, subject_dates: Mapping[str, list[str]]) -> Comparison:
    """Every page's figures today against the certified ones, subject by subject and date by date."""
    result = Comparison(pages)
    figures = {figure_id: item for page in pages for figure_id, item in own_figures(page).items()}
    result.not_compared = {figure_id: why for figure_id, why in NOT_COMPARED.items() if figure_id in figures}
    result.not_compared.update({f"page {page}": BORROWED[page] for page in pages if page in BORROWED})
    readers = {page: TODAY[page] for page in pages if page in TODAY}
    days = sorted({day for values in subject_dates.values() for day in values})
    stored = _stored(certified_rows(connection, list(subject_dates), days, sorted(figures))
                     if subject_dates and days else [])
    for subject, subject_days in subject_dates.items():
        for day in subject_days:
            certified = stored.get((subject, day), {})
            for page, read in readers.items():
                today = read(api, subject, day)
                own = {key for key in certified if key[0] in figures and figures[key[0]].page == page}
                for key in sorted(set(today) | own):
                    if key[0] in figures and key[0] not in NOT_COMPARED:
                        result.add(page, subject, day, key, figures[key[0]].unit, today.get(key),
                                   certified.get(key), "page")
    if "overview" in readers:
        for subject, (day, today) in accounts_list_today(api).items():
            if subject not in subject_dates or day is None:
                continue
            if (subject, day) not in stored:
                stored.update(_stored(certified_rows(connection, [subject], [day], sorted(figures))))
            certified = stored.get((subject, day), {})
            for key, value in today.items():
                result.add("overview", subject, day, key, figures[key[0]].unit, value, certified.get(key),
                           "accounts list")
    result.unavailable = Counter(getattr(api, "unavailable", {}))
    return result


# ---- reports ----

def _labels(subjects: Iterable[str]) -> dict[str, str]:
    labels = {}
    accounts = sorted(subject for subject in subjects if ACCOUNT_ID.fullmatch(subject))
    others = sorted(subject for subject in subjects if not ACCOUNT_ID.fullmatch(subject))
    for prefix, group in (("u", accounts), ("s", others)):
        for number, subject in enumerate(group, 1):
            labels[subject] = f"{prefix}…#{number}"
    return labels


def build_report(comparison: Comparison, *, source: str, taken_at: str | None = None) -> dict:
    labels = _labels(comparison.subjects)
    differences = [dict(item, subject=labels[item["subject"]]) for item in comparison.differences]
    differences.sort(key=lambda item: (item["page"], item["figure_id"], item["subject"], item["as_of_date"],
                                       item["period"], item["item"], item["benchmark_id"]))
    pages = {}
    for page in comparison.pages:
        found = [item for item in comparison.differences if item["page"] == page]
        pages[page] = {"title": register.page(page).title, "batch": register.page(page).batch,
                       "compared": comparison.compared[page], "matched": comparison.matched[page],
                       "blank_on_both": comparison.blank[page], "differences": len(found),
                       "kinds": dict(Counter(item["kind"] for item in found))}
    figures = {figure: dict(counts) for figure, counts in sorted(comparison.by_figure.items())}
    return {
        "report": REPORT, "version": 1, "source": source, "taken_at": taken_at,
        "pages": pages, "figures": figures,
        "subjects": len(comparison.subjects), "dates": len(comparison.dates),
        "compared": sum(comparison.compared.values()), "differences": differences,
        "not_compared": dict(sorted(comparison.not_compared.items())),
        "page_errors": dict(sorted(comparison.unavailable.items())),
        "tolerance": {"ratio_and_number_relative": REL_TOL, "money_absolute": CENT, "date_and_text": "exact"},
    }


def has_differences(report: Mapping) -> bool:
    return bool(report["differences"])


KIND_TEXT = {"different": "values differ", "page_only": "page shows a value the certified figures lack",
             "certified_only": "certified value where the page shows a dash"}


def _shown(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit in ("date", "text"):
        return f"`{value}`"
    return f"{value:.10g}"


def render_markdown(report: Mapping, *, limit: int = 500) -> str:
    lines = ["## Certified figures: today's pages against the certified figures", "",
             "Private: keep this report in the project's private folder. Post only the public summary on the PR.", "",
             f"{report['compared']:,} figures compared for {report['subjects']:,} accounts and portfolios on "
             f"{report['dates']:,} dates; {len(report['differences']):,} differ.", "",
             "| Page | Batch | Compared | Match | Blank on both | Differ |", "|---|---|---|---|---|---|"]
    for page, item in report["pages"].items():
        lines.append(f"| {page} | {item['batch']} | {item['compared']:,} | {item['matched']:,} | "
                     f"{item['blank_on_both']:,} | {item['differences']:,} |")
    lines.append("")
    if report["not_compared"]:
        lines += ["**Not compared:**", ""] + [f"- `{name}`: {why}" for name, why in report["not_compared"].items()] + [""]
    if report["page_errors"]:
        lines += ["**Page answers that were errors** (counted, not compared):", ""] + \
            [f"- {name}: {count:,}" for name, count in report["page_errors"].items()] + [""]
    if report["differences"]:
        lines += ["### Differences", "",
                  "| Page | Figure | Period | Item | Benchmark | Subject | As of | Page | Certified | Kind |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for item in report["differences"][:limit]:
            lines.append(f"| {item['page']}{' (accounts list)' if item['source'] != 'page' else ''} | "
                         f"`{item['figure_id']}` | {item['period'] or '—'} | {item['item'] or '—'} | "
                         f"{item['benchmark_id'] or '—'} | {item['subject']} | {item['as_of_date']} | "
                         f"{_shown(item['page_value'], item['unit'])} | {_shown(item['certified_value'], item['unit'])} | "
                         f"{KIND_TEXT[item['kind']]} |")
        if len(report["differences"]) > limit:
            lines.append(f"\nAnd {len(report['differences']) - limit:,} more in the JSON report.")
        lines.append("")
    lines.append("Ratios and numbers must match within a relative 1e-9, money to the cent, dates and text exactly. "
                 "u…#n (IBKR accounts) and s…#n (other subjects) are labels for this report only. "
                 "Made by `scripts/release_data_report.py figures`.")
    return mask("\n".join(lines) + "\n")


def render_public_summary(report: Mapping) -> str:
    """What may go on a public PR: counts, figure ids and page names. No values, dates,
    subjects, items or benchmarks."""
    lines = ["## Certified figures: public summary", "",
             f"Figures compared: {report['compared']:,}, for {report['subjects']:,} accounts and portfolios "
             f"on {report['dates']:,} dates. Differences: {len(report['differences']):,}.", "",
             "| Page | Batch | Compared | Match | Blank on both | Differ |", "|---|---|---|---|---|---|"]
    for page, item in report["pages"].items():
        lines.append(f"| {page} | {item['batch']} | {item['compared']:,} | {item['matched']:,} | "
                     f"{item['blank_on_both']:,} | {item['differences']:,} |")
    lines.append("")
    differing = {figure: counts for figure, counts in report["figures"].items()
                 if any(counts.get(kind) for kind in KIND_TEXT)}
    if differing:
        lines += ["### Figures that differ", ""]
        for figure, counts in differing.items():
            parts = [f"{KIND_TEXT[kind]} {counts[kind]:,}" for kind in KIND_TEXT if counts.get(kind)]
            lines.append(f"- `{figure}`: " + "; ".join(parts) + ".")
        lines.append("")
    if report["not_compared"]:
        lines += ["Not compared: " + ", ".join(f"`{name}`" for name in report["not_compared"]) + ".", ""]
    if report["page_errors"]:
        lines += [f"Page answers that were errors: {sum(report['page_errors'].values()):,}.", ""]
    lines.append("Counts, figure ids and page names only. The full report, with the figures, is kept privately "
                 "for the approver. Made by `scripts/release_data_report.py figures`.")
    return mask("\n".join(lines) + "\n")


def mask(text: str) -> str:
    from ibkr_etl.switch_dry_run import ACCOUNT_ID_IN_TEXT

    return ACCOUNT_ID_IN_TEXT.sub("u…", text)


def private_folder(path: str) -> Path:
    """The private folder for the full report: never inside the repository checkout."""
    folder = Path(path).expanduser().resolve()
    if folder == ROOT or ROOT in folder.parents:
        raise FiguresError("the private folder must be outside the repository checkout")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def write_private(folder: Path, name: str, text: str) -> Path:
    path = folder / name
    os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600))
    os.chmod(path, 0o600)
    path.write_text(text, encoding="utf-8")
    return path
