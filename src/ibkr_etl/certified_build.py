"""The certified-figures build (medallion upgrade U5, docs/MEDALLION_CERTIFIED_FIGURES.md).

scripts/build_certified_figures.py runs it; it does nothing unless CERTIFIED_FIGURES_BUILD=on.

Same numbers, now stored: every figure is read from the payload of the website API handler
that shows it today (ibkr_etl.web), called for one subject and one as-of date with the same
arguments the page passes. No figure is worked out here. page_rows() is the reader for one
subject and date, and can serve any tool that needs "the page's figures the old way".

A run: check its inputs are there; pick the dates; read each page's figures; compare them
with the published ones and keep only new or changed rows; check them; then write a build in
shadow with those rows and publish it, or hold it with the reason.

The dates: first each subject's new dates and its last RECHECK_DATES, oldest first, then the
history: every older date that has no published figures yet (every older date with --backfill),
newest first and one subject at a time in turn. Reading stops after a time limit (CERTIFIED_FIGURES_MINUTES,
--minutes, DEFAULT_MINUTES otherwise) and the run publishes what it read; the next run carries on
with the rest. Each subject's built dates so stay one unbroken run back from its newest date, so a
page asked for an older date finds either that date's figures or none (and shows today's).

The checks:

- rules: drawdowns at most zero, win rates and shares of months between 0 and 1, correlation
  between -1 and 1, RSI between 0 and 100, volatility not below zero (the contract rules of
  gold.certified_figures);
- same inputs, same answer: a figure that changes while its input fingerprint and
  calculation version are the same holds the build;
- the page agrees: for each subject's newest date, the page handlers are called again on
  their own connections and must give the same figures;
- readers: a page that fails for a reason the website would also show (an HTTP error) is
  counted; any other error holds the build.

The charts (page "charts", ibkr_etl.certified_charts) are read only when CERTIFIED_FIGURES_CHARTS
is fallback or on, or --page charts names them; in fallback a chart problem leaves the chart rows
out and never holds the build.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import re
import time as monotonic_time
import uuid
from itertools import zip_longest
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo

from . import certified_figures as cf

JOB_NAME = "ibkrv2stg-certified-figures"
RECHECK_DATES = 5
# How long one run reads before it publishes what it has: the job runs at 09:05 and 09:45 UTC
# with a 60-minute limit, so the first run ends before the second starts.
DEFAULT_MINUTES = 30.0
MINUTES_ENV = "CERTIFIED_FIGURES_MINUTES"
WRITE_BATCH = 2000
HONG_KONG = ZoneInfo("Asia/Hong_Kong")
# Who the build reads the pages as: an owner, as the website's database authorization sees one.
BUILD_USER = {"email": "certified-figures-build", "object_id": "certified-figures-build", "role": "owner",
              "roles": ["owner"], "authorization_source": "database"}
DRAWDOWNS = frozenset({
    "risk.expected_max_drawdown_1y", "risk.actual_max_drawdown_1y", "risk.tail_max_drawdown_1y",
    "risk.crisis_max_drawdown_2008", "risk.maximum_drawdown", "risk.crisis_scenario_max_drawdown",
    "stats.maximum_drawdown", "stats.current_drawdown", "table.drawdown_episodes"})
SHARES = frozenset({"risk.win_rate", "benchmark.relative_win_rate", "stats.positive_months"})
CORRELATIONS = frozenset({"benchmark.correlation", "series.rolling_correlation_126d", "matrix.holdings_correlation"})
BOUNDS = {"indicator.rsi_14": (0.0, 100.0), "series.rolling_volatility_63d": (0.0, math.inf)}
COMPARED = ("value", "value_text", "unit", "currency", "status", "reason", "detail", "period_start", "period_end",
            "observations")
KEY = ("subject_id", "figure_id", "period", "item", "benchmark_id", "as_of_date")


@dataclass
class Row:
    """One certified figure before it is stored (the columns of gold.certified_figures)."""
    subject_id: str
    figure_id: str
    as_of_date: str
    source_table: str
    source_key: dict
    input_sha256: str
    calculation_version: str
    period: str = ""
    item: str = ""
    benchmark_id: str = ""
    value: float | None = None
    value_text: str | None = None
    unit: str = "number"
    currency: str | None = None
    status: str = "ok"
    reason: str | None = None
    detail: dict = field(default_factory=dict)
    period_start: str | None = None
    period_end: str | None = None
    observations: int | None = None

    @property
    def key(self) -> tuple:
        return tuple(str(getattr(self, name)) for name in KEY)

    def compared(self) -> tuple:
        return tuple(_canonical(getattr(self, name)) for name in COMPARED)

    def stored(self) -> dict:
        figure = cf.figure(self.figure_id)
        return {**asdict(self), "definition_id": figure.definition_id,
                "definition_version": figure.definition_version}


class PageUnavailable(Exception):
    """The page itself answers with an error for this subject and date, as the website would."""


def enabled(environ: Mapping[str, str]) -> bool:
    return str(environ.get(cf.BUILD_SETTING, "")).strip().lower() == "on"


# The publish gate's verdict on the jobs whose gold tables the pages read (publish_gate.JOB_TABLES).
# off: not asked. report: asked and stored with the build. binding: a failed verdict also holds the build.
GATE_ENV = "CERTIFIED_FIGURES_GATE"
GATE_MODES = ("off", "report", "binding")
GATE_JOBS = ("benchmark-daily", "derived-daily", "risk-snapshots", "crisis-scenarios-daily")
GATE_REASONS = 5


def gate_mode(environ: Mapping[str, str] | None = None) -> str:
    """The setting's mode; anything else (a typo included) is off, so a mistake never holds a build."""
    text = str((os.environ if environ is None else environ).get(GATE_ENV, "")).strip().lower()
    return text if text in GATE_MODES else "off"


def gate_since(started: datetime) -> datetime:
    """The gate judges what the source jobs wrote since 00:00 UTC on the build's day: the daily
    cycle (07:20 to 09:45 UTC) and anything rerun since."""
    return started.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def gate_verdict(*, mode: str, database_url: str, tenant_id: str, started: datetime,
                 check: Callable[..., dict] | None = None) -> dict | None:
    """The publish gate's read-only verdict for each source job, as stored with the build. Never
    raises: a check that cannot run counts as unavailable, which warns and never holds."""
    if mode == "off":
        return None
    from . import publish_gate

    check = check or publish_gate.check
    since = gate_since(started)
    environ = {"DATABASE_URL": database_url, "PORTFOLIO_TENANT_ID": tenant_id}
    jobs: dict[str, dict] = {}
    for job in GATE_JOBS:
        try:
            result = check(job, since=since, environ=environ, now=started)
        except Exception as error:  # noqa: BLE001 - the gate never breaks the build
            result = {"verdict": publish_gate.UNAVAILABLE, "reasons": [f"check failed: {type(error).__name__}"]}
        reasons = [str(reason) for reason in result.get("reasons") or ()]
        jobs[job] = {"verdict": str(result.get("verdict") or publish_gate.UNAVAILABLE),
                     "reasons": reasons[:GATE_REASONS], "reasons_total": int(result.get("reasons_total") or len(reasons))}
    verdict = publish_gate._worst(job["verdict"] for job in jobs.values())
    return {"mode": mode, "since": since.isoformat(), "verdict": verdict, "jobs": jobs,
            "failed": [job for job, result in jobs.items() if result["verdict"] == publish_gate.FAIL],
            "policy_version": publish_gate.POLICY_VERSION}


def sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def mask(subject_id: str) -> str:
    """u… for accounts, s… for other subjects, as the logs show them."""
    text = str(subject_id)
    return "u…" + text[-2:] if re.fullmatch(r"[UF]\d+", text) else "s…" + hashlib.sha256(text.encode()).hexdigest()[:6]


# -- the website module and how the build calls it ---------------------------------------------

def load_web():
    """ibkr_etl.web, imported only when the build runs."""
    from . import web

    return web


@contextlib.contextmanager
def shared_web_database(web, database_url: str) -> Iterator[None]:
    """Run the page handlers on one read-only connection instead of one per call.

    The handlers' own _database() opens a connection, makes the transaction read-only and sets
    the tenant; this does exactly that on one connection, so the backfill does not open
    thousands. The SQL the handlers run is unchanged."""
    import psycopg
    from psycopg.rows import dict_row

    original = web._database
    _OWN_DATABASE[id(web)] = original
    connection = psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10,
                                 options="-c statement_timeout=30000 -c lock_timeout=5000")

    @contextlib.contextmanager
    def database():
        connection.rollback()
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (web.TENANT_ID,))
        try:
            yield connection
        finally:
            connection.rollback()

    web._database = database
    try:
        yield
    finally:
        web._database = original
        _OWN_DATABASE.pop(id(web), None)
        connection.close()


@contextlib.contextmanager
def own_connections(web) -> Iterator[None]:
    """Inside shared_web_database: let the handlers open their own connections again, as the
    website runs them."""
    shared = web._database
    web._database = _OWN_DATABASE.get(id(web), shared)
    try:
        yield
    finally:
        web._database = shared


_OWN_DATABASE: dict[int, Any] = {}


def _call(handler: Callable, *args: Any, **kwargs: Any) -> dict:
    from fastapi import HTTPException

    try:
        return handler(*args, user=BUILD_USER, **kwargs)
    except HTTPException as error:
        raise PageUnavailable(f"{error.status_code}: {error.detail}") from None


class _Reads:
    """Every row a page handler reads, in the order it reads them: the input fingerprint of a page
    whose engine reads its own tables (Attribution). The order counts, because the engine adds
    up in that order."""

    def __init__(self) -> None:
        self._sha = hashlib.sha256()
        self.queries = 0

    def add(self, query: Any, params: Any, rows: list) -> None:
        self.queries += 1
        plain = [dict(row) if isinstance(row, Mapping) else list(row) if isinstance(row, (list, tuple)) else row
                 for row in rows]
        self._sha.update(_canonical([str(query), params, plain]).encode("utf-8") + b"\n")

    def hexdigest(self) -> str:
        return self._sha.hexdigest()

    @contextlib.contextmanager
    def around(self, web) -> Iterator[None]:
        """Record the reads of the handlers called inside, on whatever connections they use."""
        original = web._database

        @contextlib.contextmanager
        def database():
            with original() as connection:
                yield _RecordingConnection(connection, self)

        web._database = database
        try:
            yield
        finally:
            web._database = original


class _RecordingConnection:
    def __init__(self, connection, reads: _Reads):
        self._connection, self._reads = connection, reads

    def execute(self, query, params=None, *args, **kwargs):
        cursor = self._connection.execute(query, params, *args, **kwargs)
        rows = cursor.fetchall() if cursor.description is not None else []
        self._reads.add(query, params, rows)
        return _RecordedCursor(rows, cursor.description, cursor.rowcount)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class _RecordedCursor:
    def __init__(self, rows: list, description: Any, rowcount: int):
        self._rows, self.description, self.rowcount = list(rows), description, rowcount

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows

    def __iter__(self):
        while self._rows:
            yield self._rows.pop(0)


class Pages:
    """The page payloads for one subject and date, each called once and only when needed."""

    def __init__(self, web, subject: "Subject", as_of: str):
        self.web, self.subject, self.as_of = web, subject, as_of
        self._cache: dict[str, Any] = {}

    def _get(self, name: str, make: Callable[[], Any]) -> Any:
        if name not in self._cache:
            try:
                self._cache[name] = make()
            except PageUnavailable as error:
                self._cache[name] = error
        found = self._cache[name]
        if isinstance(found, PageUnavailable):
            raise found
        return found

    def summary(self) -> dict:
        return self._get("summary", lambda: _call(self.web.portfolio_summary, self.subject.id, as_of=self.as_of))

    def periods(self) -> dict:
        # As the Overview asks: the as-of date, and a custom period of that one day (Day change).
        return self._get("periods", lambda: _call(self.web.performance_periods_view, self.subject.id,
                                                  as_of=self.as_of, start=self.as_of, end=self.as_of))

    def analytics(self) -> dict:
        return self._get("analytics", lambda: _call(self.web.portfolio_analytics_view, self.subject.id,
                                                    as_of=self.as_of))

    def holdings(self) -> dict:
        return self._get("holdings", lambda: _call(self.web.holdings, self.subject.id, as_of=self.as_of))

    def sectors(self) -> dict:
        request = SimpleNamespace(state=SimpleNamespace(correlation_id="certified-figures-build"))
        return self._get("sectors", lambda: _call(self.web.sector_allocation, self.subject.id, request,
                                                  as_of=self.as_of))

    def cash(self) -> dict:
        # The ledger below the balances is not a figure, so ask for one day of it.
        return self._get("cash", lambda: _call(self.web.cash_activity, self.subject.id, as_of=self.as_of,
                                               from_date=self.as_of, to_date=self.as_of))

    def comparison(self) -> dict:
        return self._get("comparison", lambda: _call(self.web.benchmark_comparison, self.subject.id,
                                                     as_of=self.as_of))

    def risk(self) -> dict:
        return self._get("risk", lambda: _call(self.web.risk_analytics, self.subject.id, as_of=self.as_of))

    def risk_details(self) -> dict:
        return self._get("risk_details", lambda: _call(self.web.risk_analytics_details, self.subject.id,
                                                       as_of=self.as_of))

    def attribution(self, month: str) -> tuple[dict, str]:
        """Performance › Attribution for one month, and sha256 of every row its handler read."""
        def make():
            reads = _Reads()
            with reads.around(self.web):
                payload = _call(self.web.monthly_attribution_view, self.subject.id, month=month, as_of=self.as_of)
            return payload, reads.hexdigest()
        return self._get(f"attribution:{month}", make)

    def nav_inputs(self) -> str:
        """sha256 of the NAV series and external flows through the as-of date, as the handlers read them."""
        def make():
            with self.web._database() as connection:
                nav = self.web._subject_nav_values(connection, self.subject.id, self.subject.access, self.as_of)
                try:
                    flows = self.web._subject_external_flows(connection, self.subject.id, self.subject.access)
                except Exception as error:  # noqa: BLE001 - the page reports it; the fingerprint notes it
                    flows = {"unavailable": type(error).__name__}
            through = {day: amount for day, amount in (flows or {}).items() if day <= self.as_of} \
                if isinstance(flows, dict) and "unavailable" not in flows else flows
            return sha256({"nav": nav, "flows": through, "currency": self.subject.currency})
        return self._get("nav_inputs", make)


@dataclass
class Subject:
    id: str
    access: dict
    dates: list[str]  # NAV dates the pages let you pick, oldest first

    @property
    def currency(self) -> str:
        return str(self.access["reporting_currency"])

    @property
    def kind(self) -> str:
        return str(self.access.get("subject_type") or "physical_account")


def subjects(web, only: Iterable[str] = ()) -> tuple[list[Subject], dict[str, str]]:
    """Active subjects whose pages an owner sees, with their NAV dates; and those skipped, why."""
    wanted = set(only)
    found, skipped = [], {}
    for subject_id in web._authorized_subject_ids(BUILD_USER):
        if wanted and subject_id not in wanted:
            continue
        try:
            access = _call(web._portfolio_access_context, subject_id)
            dates = _call(web.available_dates, subject_id)["dates"]
        except PageUnavailable as error:
            skipped[subject_id] = str(error)
            continue
        if access.get("preview_mode"):
            skipped[subject_id] = "onboarding preview"
            continue
        found.append(Subject(subject_id, access, sorted(str(day) for day in dates)))
    return found, skipped


# -- the readers: one per page -------------------------------------------------------------------

def _row(subject: Subject, figure_id: str, as_of: str, value: Any, *, inputs: str, source_table: str,
         source_key: Mapping, source_version: str | None = None, reason: str | None = None,
         currency: str | None = None, **columns: Any) -> Row:
    figure = cf.figure(figure_id)
    row = Row(subject_id=subject.id, figure_id=figure_id, as_of_date=as_of, source_table=source_table,
              source_key=dict(source_key), input_sha256=inputs, unit=figure.unit,
              calculation_version=cf.calculation_version(figure, source_version), **columns)
    if figure.unit in ("date", "text"):
        row.value_text = None if value in (None, "") else str(value)
        available = row.value_text is not None
    else:
        row.value = _number(value)
        available = row.value is not None
    if figure.unit == "money":
        row.currency = currency or subject.currency
    elif currency:
        row.currency = currency
    if not available:
        row.value, row.value_text, row.status = None, None, "unavailable"
        row.reason = reason or "The page shows no value for this date."
    return row


def _nav_key(subject: Subject, as_of: str) -> dict:
    return {"subject_id": subject.id, "snapshot_date": as_of, "currency": subject.currency}


def overview_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    summary, periods = pages.summary(), pages.periods()
    inputs = pages.nav_inputs()
    if summary.get("effective_as_of") != as_of:
        return []  # no NAV on this date
    common = {"inputs": inputs, "source_table": "silver.subject_nav_series", "source_key": _nav_key(subject, as_of),
              "source_version": periods.get("calculation_version")}
    latest = summary.get("latest_nav") or {}
    rows = [
        _row(subject, "overview.as_of", as_of, summary["effective_as_of"], **common),
        _row(subject, "overview.nav", as_of, latest.get("total_text"), currency=latest.get("currency"), **common),
        _row(subject, "overview.observations", as_of, (summary.get("nav_coverage") or {}).get("observations"),
             **common),
        _row(subject, "overview.reporting_currency", as_of, periods.get("currency"), **common),
    ]
    positions_inputs = sha256({"nav": inputs, "positions": summary.get("current_positions")})
    rows.append(_row(subject, "overview.positions", as_of, summary.get("current_positions"),
                     **{**common, "inputs": positions_inputs,
                        "source_table": "silver.synthetic_portfolio_components" if subject.kind == "composite_portfolio"
                        else "silver.subject_position_series"}))

    def ok(period: Mapping | None) -> bool:
        return bool(period) and period.get("status") not in ("fail", "empty")

    def why(period: Mapping | None) -> str:
        issues = list((period or {}).get("issues") or [])
        return " ".join(issues) or "The period has no return for this date."

    day = periods.get("custom")
    rows.append(_row(subject, "overview.day_change", as_of, day.get("twr") if ok(day) else None,
                     reason=why(day), **_period_columns(day), **common))
    flows_applicable = periods.get("flows_applicable") is not False
    for period in periods.get("periods") or []:
        key = str(period["key"])
        columns = {"period": key, **_period_columns(period), **common}
        good = ok(period)
        rows.append(_row(subject, "overview.twr", as_of, period.get("twr") if good else None,
                         reason=why(period), **columns))
        if key.startswith("year_") or key == "since_inception":
            rows.append(_row(subject, "overview.twr_annualised", as_of,
                             period.get("twr_annualized") if good else None,
                             reason=why(period) if not good else "Periods under a year are not annualised.",
                             **columns))
        rows.append(_row(subject, "overview.irr_annualised", as_of, period.get("irr_annualized") if good else None,
                         reason=why(period) if not good else (period.get("irr_note") or why(period)), **columns))
        rows.append(_row(subject, "overview.net_external_flows", as_of,
                         period.get("net_flows") if good and flows_applicable else None,
                         reason=periods.get("flows_message") or why(period), **columns))
    return rows


def _period_columns(period: Mapping | None) -> dict:
    if not period:
        return {}
    detail = {"status": period.get("status")}
    if period.get("partial"):
        detail["partial"] = True
    return {"period_start": period.get("start_date"), "period_end": period.get("end_date"),
            "observations": period.get("observations"), "detail": detail}


def freshness_row(pages: Pages, today_hk: date) -> list[Row]:
    """Data freshness as the account header shows it on the build's day (Hong Kong), for the newest date."""
    subject, as_of = pages.subject, pages.as_of
    if not subject.dates or as_of != subject.dates[-1]:
        return []

    def back(day: date) -> date:
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    expected = back(today_hk)
    grace = back(expected)
    latest = date.fromisoformat(as_of)
    state = "current" if latest >= expected else "late" if latest >= grace else "stale"
    text = {"current": "current", "late": "one business day behind", "stale": "overdue"}[state]
    return [_row(subject, "overview.data_freshness", as_of, text, inputs=sha256([as_of, today_hk.isoformat()]),
                 source_table="silver.subject_nav_series", source_key=_nav_key(subject, as_of),
                 source_version=cf.figure("overview.data_freshness").method,
                 detail={"state": state, "expected": expected.isoformat(), "checked_on": today_hk.isoformat()})]


def holdings_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.holdings()
    snapshot = payload.get("snapshot_date")
    if snapshot is None or str(snapshot) != as_of:
        return []  # the page shows an earlier day's holdings, certified on that day
    holdings = payload.get("holdings") or []
    key = {"subject_id": subject.id, "snapshot_date": as_of}
    if subject.kind == "composite_portfolio":
        inputs = sha256(holdings)
        return [_row(subject, "holdings.market_value", as_of, row.get("market_value_text"), item=str(row["conid"]),
                     currency=row.get("currency"), inputs=inputs, source_table="silver.synthetic_portfolio_components",
                     source_key={**key, "component_id": str(row["conid"])},
                     detail={"basis": row.get("valuation_basis")}) for row in holdings]
    raw = [{name: row.get(name) for name in ("conid", "currency", "quantity_text", "market_value_text",
                                              "cost_basis_text", "unrealized_pnl_text", "sector", "reporting_fx")}
           for row in holdings]
    inputs = sha256({"positions": raw, "nav": payload.get("nav_text"), "currency": payload.get("reporting_currency")})
    version = payload.get("valuation_method")
    contract = _contract_version("silver", "subject_position_series")
    rows = []
    for row in holdings:
        item = str(row["conid"])
        source = {"inputs": inputs, "source_table": "silver.subject_position_series",
                  "source_key": {**key, "conid": item}}
        own = str(row.get("currency") or "") or None
        fx = row.get("reporting_fx") or None
        rows += [
            _row(subject, "holdings.quantity", as_of, row.get("quantity_text"), item=item, source_version=contract,
                 **source),
            _row(subject, "holdings.market_value", as_of, row.get("market_value_text"), item=item, currency=own,
                 source_version=contract, **source),
            _row(subject, "holdings.cost_basis", as_of, row.get("cost_basis_text"), item=item, currency=own,
                 source_version=contract, **source),
            _row(subject, "holdings.unrealized_pnl", as_of, row.get("unrealized_pnl_text"), item=item, currency=own,
                 detail={"source": row.get("unrealized_pnl_source")}, source_version=version, **source),
            _row(subject, "holdings.pnl_pct", as_of, row.get("unrealized_pnl_pct"), item=item, source_version=version,
                 **source),
            _row(subject, "holdings.value_in_reporting_currency", as_of, row.get("reporting_market_value_text"),
                 item=item, currency=row.get("reporting_currency"), source_version=version,
                 reason="No usable FX rate for this holding." if row.get("fx_missing") else None,
                 detail={"fx": fx} if fx else {}, **source),
            _row(subject, "holdings.holding_weight", as_of, row.get("weight"), item=item, source_version=version,
                 detail={"weight_basis": payload.get("weight_basis")}, **source),
        ]
    common = {"inputs": inputs, "source_table": "silver.subject_position_series", "source_key": key}
    rows.append(_row(subject, "holdings.gross_market_value", as_of, payload.get("gross_holdings_text"),
                     currency=payload.get("reporting_currency"), source_version=version, **common))
    concentration = payload.get("concentration") or {}
    reason = concentration.get("reason")
    analytics = cf.figure("holdings.largest_holding").method
    largest = concentration.get("largest") or {}
    rows += [
        _row(subject, "holdings.largest_holding", as_of, largest.get("weight"), reason=reason,
             detail={"symbol": largest.get("symbol")} if largest else {}, source_version=analytics, **common),
        _row(subject, "holdings.top_5_weight", as_of, concentration.get("top_5_weight"), reason=reason,
             source_version=analytics, **common),
        _row(subject, "holdings.top_10_weight", as_of, concentration.get("top_10_weight"), reason=reason,
             source_version=analytics, **common),
        _row(subject, "holdings.effective_holdings", as_of, concentration.get("effective_holdings"), reason=reason,
             source_version=analytics, **common),
    ]
    for exposure in concentration.get("currency_exposure") or []:
        rows.append(_row(subject, "holdings.currency_exposure", as_of, exposure.get("weight"),
                         item=str(exposure["currency"]), source_version=analytics, **common))
    return rows


def sector_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.sectors()
    if payload.get("as_of") != as_of or not payload.get("allocation"):
        return []
    inputs = sha256(payload.get("allocation_detail") or payload.get("allocation"))
    table = "silver.synthetic_portfolio_components" if subject.kind == "composite_portfolio" \
        else "silver.subject_position_series"
    key = {"subject_id": subject.id, "snapshot_date": str(payload.get("position_date") or as_of)}
    return [_row(subject, "holdings.sector_allocation", as_of, entry.get("weight"), item=str(entry["sector"]),
                 inputs=inputs, source_table=table, source_key=key,
                 detail={"quality_status": payload.get("quality_status")})
            for entry in payload["allocation"]]


def cash_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.cash()
    if payload.get("model_cash") is not None or payload.get("activity_applicable") is False:
        return []  # the composites' model cash is not a guide figure and stays as today
    if str(payload.get("balances_date") or "") != as_of:
        return []
    balances = [dict(row) for row in payload.get("balances") or []]
    inputs = sha256(balances)
    contract = _contract_version("silver", "subject_cash_balance_series")
    rows = []
    for balance in balances:
        item = str(balance["currency"])
        currency = item if re.fullmatch(r"[A-Z]{3}", item) else str(subject.access.get("base_currency")
                                                                     or subject.currency)
        source = {"inputs": inputs, "source_table": "silver.subject_cash_balance_series",
                  "source_key": {"subject_id": subject.id, "snapshot_date": as_of, "currency": item},
                  "source_version": contract, "item": item, "currency": currency}
        rows += [_row(subject, "cash.ending_cash", as_of, balance.get("ending_cash_text"), **source),
                 _row(subject, "cash.settled_cash", as_of, balance.get("settled_cash_text"), **source)]
    return rows


def performance_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    rows = []
    analytics = pages.analytics()
    if analytics.get("available") and analytics.get("effective_as_of") == as_of:
        inputs = pages.nav_inputs()
        common = {"inputs": inputs, "source_table": "silver.subject_nav_series",
                  "source_key": _nav_key(subject, as_of), "source_version": analytics.get("calculation_version")}
        for year in analytics.get("monthly_calendar") or []:
            cells = [(f"month_{year['year']}-{index + 1:02d}", cell) for index, cell in enumerate(year["months"])]
            cells.append((f"year_{year['year']}", year.get("year_return")))
            for period, cell in cells:
                if cell is None:
                    continue
                good = cell.get("status") in ("pass", "partial")
                rows.append(_row(subject, "performance.monthly_return", as_of, cell.get("twr") if good else None,
                                 period=period, period_start=cell.get("start_date"), period_end=cell.get("end_date"),
                                 reason=" ".join(cell.get("issues") or []) or None,
                                 detail={"status": cell.get("status"), **({"to_date": True} if cell.get("to_date")
                                                                          else {})}, **common))
    comparison = pages.comparison()
    if not comparison.get("available"):
        return rows
    series = comparison.get("series") or []
    if not series:
        return rows
    # On a day the benchmark's market was shut the page still shows the comparison through its last
    # common date, so the figures for this date are those (source_key names that date).
    benchmark = comparison["benchmark"]
    benchmark_id = str(benchmark["benchmark_id"])
    version = str(comparison.get("calculation_version"))
    with pages.web._database() as connection:
        levels = connection.execute(
            "SELECT observation_date, portfolio_value, benchmark_level FROM performance_comparison_daily "
            "WHERE tenant_id = %s AND subject_id = %s AND benchmark_id = %s AND calculation_version = %s "
            "AND reporting_currency = %s AND quality_status IN ('pass', 'warn') AND observation_date <= %s "
            "ORDER BY observation_date",
            (pages.web.TENANT_ID, subject.id, benchmark_id, version, comparison.get("reporting_currency"), as_of),
        ).fetchall()
    inputs = sha256([[str(r["observation_date"]), str(r["portfolio_value"]), str(r["benchmark_level"])]
                     for r in levels])
    key = {"subject_id": subject.id, "benchmark_id": benchmark_id, "observation_date": str(series[-1]["date"]),
           "calculation_version": version}
    common = {"inputs": inputs, "source_table": "gold.performance_comparison_daily", "source_key": key,
              "source_version": version, "benchmark_id": benchmark_id}
    basis = {"return_basis": comparison.get("benchmark_return_convention")}
    metrics = comparison.get("metrics") or {}
    for figure in cf.figures().values():
        if figure.page == "performance" and (figure.field or "").startswith("comparison_metrics:"):
            name = figure.field.split(":", 1)[1]
            rows.append(_row(subject, figure.id, as_of, metrics.get(name), detail=basis, **common))
    last = series[-1]
    rows += [_row(subject, "benchmark.rebased_level", as_of, last.get("portfolio"), item="portfolio", **common),
             _row(subject, "benchmark.rebased_level", as_of, last.get("benchmark"), item="benchmark", detail=basis,
                  **common)]
    return rows


def risk_snapshot_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    payload = pages.risk()
    if payload.get("cache_status") == "miss":
        return []  # not built yet: the next run certifies it
    version = pages.web.RISK_ANALYTICS_VERSION
    key = {"subject_id": subject.id, "as_of_date": as_of, "analytics_version": version}
    withheld = payload.get("message") if payload.get("cache_status") != "hit" else None
    inputs = sha256({"payload": payload.get("snapshot_payload_sha256"), "status": payload.get("cache_status")})
    common = {"inputs": inputs, "source_table": "gold.risk_analytics_snapshots", "source_key": key,
              "source_version": version}
    metrics = payload.get("metrics") or {}
    rows = []
    for figure in cf.figures().values():
        if figure.page == "risk-snapshot" and (figure.field or "").startswith("payload_json.metrics."):
            name = figure.field.rsplit(".", 1)[1]
            rows.append(_row(subject, figure.id, as_of, None if withheld else metrics.get(name),
                             reason=withheld or "The risk snapshot has no value for this figure.", **common))
    details = pages.risk_details()
    if details.get("cache_status") == "hit":
        contributors = details.get("expected_tail_loss_contributors") or {}
        seen = {}
        for entry in [*(contributors.get("top") or []), *(contributors.get("bottom") or [])]:
            seen.setdefault(str(entry["conid"]), entry)
        detail_common = {**common, "inputs": sha256({"payload": details.get("snapshot_payload_sha256")})}
        for conid, entry in seen.items():
            rows.append(_row(subject, "risk.etl_contribution", as_of, entry.get("expected_tail_loss_contribution"),
                             item=conid, detail={"symbol": entry.get("symbol")}, **detail_common))
        if contributors:
            rows.append(_row(subject, "risk.etl_residual_contribution", as_of,
                             contributors.get("residual_contribution"),
                             detail={"tail_observations": contributors.get("tail_observations")}, **detail_common))
        scenarios = details.get("crisis_scenarios") or {}
        for scenario in scenarios.get("scenarios") or []:
            scenario_id = str(scenario.get("scenario_id"))
            source_key = {"subject_id": subject.id, "holdings_as_of": scenarios.get("holdings_as_of"),
                          "scenario_id": scenario_id, "calculation_version": scenarios.get("calculation_version")}
            rows.append(_row(subject, "risk.crisis_scenario_max_drawdown", as_of, scenario.get("maximum_drawdown"),
                             item=scenario_id, inputs=sha256(scenario), source_table="gold.crisis_scenario_results",
                             source_key=source_key, source_version=scenarios.get("calculation_version"),
                             reason=scenario.get("reason") or scenario.get("message"),
                             detail={"holdings_as_of": scenarios.get("holdings_as_of")}))
    return rows


STATISTICS = {
    "stats.sortino_ratio": "sortino_ratio", "stats.calmar_ratio": "calmar_ratio",
    "stats.annualized_volatility": "annualized_volatility", "stats.downside_deviation": "downside_deviation",
    "stats.skewness": "skewness", "stats.excess_kurtosis": "excess_kurtosis",
    "stats.average_gain_month": "average_gain_month", "stats.average_loss_month": "average_loss_month",
    "stats.gain_loss_ratio": "gain_loss_ratio",
}


def risk_statistics_rows(pages: Pages) -> list[Row]:
    subject, as_of = pages.subject, pages.as_of
    analytics = pages.analytics()
    if analytics.get("effective_as_of") != as_of:
        return []
    common = {"inputs": pages.nav_inputs(), "source_table": "silver.subject_nav_series",
              "source_key": _nav_key(subject, as_of), "source_version": analytics.get("calculation_version")}
    if not analytics.get("available"):
        reason = " ".join(analytics.get("issues") or []) or "Statistics are unavailable for this date."
        return [_row(subject, figure.id, as_of, None, reason=reason, **common)
                for figure in cf.figures().values() if figure.page == "risk-statistics"]
    stats = analytics.get("statistics") or {}
    span = {"period_start": stats.get("period_start"), "period_end": stats.get("period_end"),
            "observations": stats.get("return_observations")}
    rows = [_row(subject, figure_id, as_of, stats.get(name), **span, **common)
            for figure_id, name in STATISTICS.items()]
    drawdown = analytics.get("drawdown") or {}
    rows += [_row(subject, "stats.maximum_drawdown", as_of, drawdown.get("maximum_drawdown"), **span, **common),
             _row(subject, "stats.current_drawdown", as_of, drawdown.get("current_drawdown"), **span, **common)]
    for figure_id, name, when in (("stats.best_day", "best_day", "date"), ("stats.worst_day", "worst_day", "date"),
                                  ("stats.best_month", "best_month", "month"),
                                  ("stats.worst_month", "worst_month", "month")):
        entry = stats.get(name) or {}
        rows.append(_row(subject, figure_id, as_of, entry.get("return"), detail={when: entry.get(when)} if entry else {},
                         **span, **common))
    rows.append(_row(subject, "stats.positive_months", as_of, stats.get("positive_month_ratio"),
                     detail={"positive_months": stats.get("positive_months"),
                             "measured_months": stats.get("measured_months")}, **span, **common))
    trailing = next((point for point in reversed(analytics.get("rolling") or []) if point["date"] == as_of), None)
    rows.append(_row(subject, "stats.trailing_12m_return", as_of, (trailing or {}).get("trailing_return"),
                     reason="Less than twelve months of history on this date.", **common))
    return rows


ATTRIBUTION_TOTALS = (("attribution.portfolio_return", "portfolio_monthly_return"),
                      ("attribution.holdings_contribution", "equity_contribution"),
                      ("attribution.residual_contribution", "residual_contribution"))


def attribution_rows(pages: Pages) -> list[Row]:
    """Performance › Attribution: the as-of date's month, from the page's own handler (the monthly
    reports' engine). The page works a month out to its calendar end whatever the as-of date, so
    a month is read only on its last date and, for the month in progress, on the newest date.
    The composites keep today's figures: their engine adds the sleeves in set order, which
    changes from one run to the next, so its totals can move in the last digit with the same
    inputs and would hold the build."""
    subject, as_of = pages.subject, pages.as_of
    month = as_of[:7]
    later = [day for day in subject.dates if day > as_of]
    if subject.kind == "composite_portfolio" or (later and later[0][:7] == month):
        return []
    payload, inputs = pages.attribution(month)
    if payload.get("available") and "position_coverage" not in payload:
        return []  # a composite's answer
    period = f"month_{month}"
    common = {"inputs": inputs, "source_table": "silver.subject_position_series",
              "source_key": {"subject_id": subject.id, "month": month, "as_of_date": as_of},
              "source_version": payload.get("method_version"), "period": period}
    if not payload.get("available"):
        reason = str(payload.get("message") or "") or "Contribution to return is unavailable for this month."
        return [_row(subject, figure_id, as_of, None, reason=reason, **common) for figure_id, _ in ATTRIBUTION_TOTALS]
    span = {"period_start": payload.get("period_start"), "period_end": payload.get("period_end"),
            "observations": payload.get("daily_observations")}
    detail = {"quality_status": payload.get("quality_status")}
    rows = [_row(subject, figure_id, as_of, payload.get(name), detail=detail, **span, **common)
            for figure_id, name in ATTRIBUTION_TOTALS]
    for entry in payload.get("security_contributions") or []:
        rows.append(_row(subject, "attribution.holding_contribution", as_of, entry.get("contribution"),
                         item=str(entry["conid"]), detail={"symbol": entry.get("symbol"), "sector": entry.get("sector")},
                         **span, **common))
    for entry in payload.get("sector_contributions") or []:
        rows.append(_row(subject, "attribution.sector_contribution", as_of, entry.get("contribution"),
                         item=str(entry["sector"]), **span, **common))
    return rows


READERS: dict[str, tuple[Callable[[Pages], list[Row]], ...]] = {
    "overview": (overview_rows,),
    "holdings": (holdings_rows, sector_rows),
    "cash": (cash_rows,),
    "performance": (performance_rows,),
    "risk-snapshot": (risk_snapshot_rows,),
    "risk-statistics": (risk_statistics_rows,),
    "combined-holdings": (),  # the Holdings page's records, added up when read
    "attribution": (attribution_rows,),
}


def page_rows(web, subject: Subject, as_of: str, pages: Iterable[str], *, today_hk: date | None = None,
              notes: dict | None = None) -> list[Row]:
    """Today's figures for one subject and date, read from the pages' own handlers.

    A page that answers with an HTTP error, as the website would show it, is counted in
    notes["page_unavailable"] and gives no rows; any other error is raised."""
    payloads = Pages(web, subject, as_of)
    rows: list[Row] = []
    for page in pages:
        for reader in _readers(page):
            try:
                rows += reader(payloads)
            except PageUnavailable:
                if notes is not None:
                    notes.setdefault("page_unavailable", {}).setdefault(page, 0)
                    notes["page_unavailable"][page] += 1
            except Exception as error:  # a chart reader's error is counted; the charts' mode decides
                if page in READERS or notes is None:
                    raise
                kinds = notes.setdefault("chart_errors", {}).setdefault(reader.__name__, {})
                kinds[type(error).__name__] = kinds.get(type(error).__name__, 0) + 1
        if page == "overview" and today_hk is not None:
            rows += freshness_row(payloads, today_hk)
    return rows


def _readers(page: str) -> tuple[Callable[[Pages], list[Row]], ...]:
    if page in READERS:
        return READERS[page]
    return _charts().READERS[page]


def _charts():
    """ibkr_etl.certified_charts, imported when a run reads the charts (it imports this module)."""
    from . import certified_charts

    return certified_charts


# The data contract versions of the silver tables whose values are copied unchanged (the
# "imported" figures). Kept here so the build needs no contract files at run time; a test checks
# them against infra/medallion/contracts.
SOURCE_CONTRACT_VERSIONS = {
    "silver.subject_position_series": "1.0.0",
    "silver.subject_cash_balance_series": "1.0.0",
}


def _contract_version(layer: str, table: str) -> str:
    """'silver.<table>@<contract version>'."""
    name = f"{layer}.{table}"
    return f"{name}@{SOURCE_CONTRACT_VERSIONS[name]}"


# -- checks --------------------------------------------------------------------------------------

def rule_failures(rows: Iterable[Row]) -> list[str]:
    """The contract rules for gold.certified_figures, on rows not yet stored."""
    failures = []
    for row in rows:
        if row.status != "ok" or row.value is None:
            continue
        if not math.isfinite(row.value):
            failures.append(row.figure_id)
        elif row.figure_id in DRAWDOWNS and row.value > 0:
            failures.append(row.figure_id)
        elif row.figure_id in SHARES and not 0 <= row.value <= 1:
            failures.append(row.figure_id)
        elif row.figure_id in CORRELATIONS and abs(row.value) > 1 + 1e-12:
            failures.append(row.figure_id)
        elif row.figure_id in BOUNDS and not BOUNDS[row.figure_id][0] <= row.value <= BOUNDS[row.figure_id][1]:
            failures.append(row.figure_id)
    return failures


def compare(rows: Iterable[Row], published: Mapping[tuple, Mapping]) -> dict:
    """Keep only new and changed rows; a change with the same inputs and version is a violation."""
    new, changed, unchanged, violations = [], [], 0, []
    for row in rows:
        old = published.get(row.key)
        if old is None:
            new.append(row)
            continue
        before = tuple(_canonical(old.get(name)) for name in COMPARED)
        if before == row.compared():
            unchanged += 1
            continue
        changed.append(row)
        if old.get("input_sha256") == row.input_sha256 and old.get("calculation_version") == row.calculation_version:
            violations.append(row)
    return {"new": new, "changed": changed, "unchanged": unchanged, "violations": violations}


def page_disagreements(web, subject: Subject, as_of: str, pages: Iterable[str], rows: Iterable[Row],
                       today_hk: date) -> list[str]:
    """Call the handlers again, each on its own connections as the website does, and compare."""
    with own_connections(web):
        again = {row.key: row.compared() for row in page_rows(web, subject, as_of, pages, today_hk=today_hk,
                                                              notes={})}
    built = {row.key: row.compared() for row in rows if row.subject_id == subject.id and row.as_of_date == as_of}
    return sorted({key[1] for key in set(again) | set(built) if again.get(key) != built.get(key)})


# -- reading and writing the tables ---------------------------------------------------------------

def published_rows(connection, subject_ids: list[str]) -> dict[tuple, dict]:
    if not subject_ids:
        return {}
    cursor = connection.execute(
        "SELECT subject_id, figure_id, period, item, benchmark_id, as_of_date::text AS as_of_date, "
        + ", ".join(COMPARED) + ", input_sha256, calculation_version, period_start::text AS period_start_text, "
        "period_end::text AS period_end_text FROM gold.v_certified_figures WHERE subject_id = ANY(%s)",
        (subject_ids,))
    names = [column.name for column in cursor.description]
    found = {}
    for values in cursor.fetchall():
        row = dict(zip(names, values))
        row["period_start"], row["period_end"] = row.pop("period_start_text"), row.pop("period_end_text")
        found[tuple(str(row[name]) for name in KEY)] = row
    return found


INSERT_FIGURES = """
INSERT INTO gold.certified_figures(build_id, subject_id, figure_id, period, item, benchmark_id, as_of_date, value,
    value_text, unit, currency, status, reason, detail, period_start, period_end, observations, definition_id,
    definition_version, calculation_version, input_sha256, source_table, source_key)
SELECT %s, r.subject_id, r.figure_id, r.period, r.item, r.benchmark_id, r.as_of_date, r.value, r.value_text, r.unit,
       r.currency, r.status, r.reason, COALESCE(r.detail, '{}'::jsonb), r.period_start, r.period_end, r.observations,
       r.definition_id, r.definition_version, r.calculation_version, r.input_sha256, r.source_table, r.source_key
FROM jsonb_to_recordset(%s::jsonb) AS r(subject_id text, figure_id text, period text, item text, benchmark_id text,
    as_of_date date, value double precision, value_text text, unit text, currency text, status text, reason text,
    detail jsonb, period_start date, period_end date, observations integer, definition_id text,
    definition_version text, calculation_version text, input_sha256 text, source_table text, source_key jsonb)
"""


def write_build(connection, *, build_id: str, run_kind: str, started_at: datetime, rows: list[Row],
                checks: dict, held_reason: str | None) -> str:
    """Write the build in shadow with its rows, then publish or hold it. Returns its status."""
    connection.execute("INSERT INTO gold.certified_builds(build_id, run_kind, started_at, status, checks) "
                       "VALUES (%s, %s, %s, 'shadow', %s::jsonb)", (build_id, run_kind, started_at, _canonical(checks)))
    for start in range(0, len(rows), WRITE_BATCH):
        batch = [row.stored() for row in rows[start:start + WRITE_BATCH]]
        connection.execute(INSERT_FIGURES, (build_id, json.dumps(batch, default=str)))
    connection.commit()
    status = "held" if held_reason else "published"
    connection.execute(
        "UPDATE gold.certified_builds SET status = %s, published_at = CASE WHEN %s = 'published' THEN now() END, "
        "finished_at = now(), reason = %s, checks = %s::jsonb WHERE build_id = %s",
        (status, status, held_reason, _canonical(checks), build_id))
    connection.commit()
    return status


# -- one run --------------------------------------------------------------------------------------

def hong_kong_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(HONG_KONG).date()


def expected_date(today_hk: date) -> date:
    """The previous weekday in Hong Kong: the day whose data should be in (Data freshness)."""
    day = today_hk - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def due_dates(subject: Subject, published: Iterable[str]) -> list[str]:
    """The dates every run reads: the last RECHECK_DATES and each date after the newest published
    one. With nothing published yet, only the last RECHECK_DATES; the rest is history."""
    latest = max(published, default=None)
    recent = subject.dates[-RECHECK_DATES:]
    newer = [day for day in subject.dates if latest is not None and day > latest]
    return sorted(set(recent) | set(newer))


def history_dates(subject: Subject, published: Iterable[str], due: Iterable[str], *, backfill: bool) -> list[str]:
    """The older dates, newest first: each one with no published figures, or every one with --backfill."""
    published, due = set(published), set(due)
    return sorted((day for day in subject.dates if day not in due and (backfill or day not in published)),
                  reverse=True)


def work_plan(found: Iterable[Subject], published_dates: Mapping[str, Iterable[str]], *,
              backfill: bool) -> list[tuple[Subject, str]]:
    """What a run reads, in order: every subject's due dates, oldest first, so a run that stops
    among them leaves no gap after the published dates; then the history, newest first and one
    date of each subject in turn."""
    plan: list[tuple[Subject, str]] = []
    histories = []
    for subject in found:
        published = set(published_dates.get(subject.id, ()))
        due = due_dates(subject, published)
        plan += [(subject, day) for day in sorted(due)]
        histories.append([(subject, day) for day in history_dates(subject, published, due, backfill=backfill)])
    for turn in zip_longest(*histories):
        plan += [item for item in turn if item is not None]
    return plan


def time_limit(minutes: float | None, environ: Mapping[str, str] | None = None) -> float:
    """Minutes a run may read: the argument, else CERTIFIED_FIGURES_MINUTES, else DEFAULT_MINUTES."""
    if minutes is None:
        text = str((os.environ if environ is None else environ).get(MINUTES_ENV, "")).strip()
        try:
            minutes = float(text) if text else DEFAULT_MINUTES
        except ValueError:
            minutes = DEFAULT_MINUTES
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("the time limit must be a positive number of minutes")
    return minutes


def run(*, database_url: str, backfill: bool = False, dry_run: bool = False, only_subjects: Iterable[str] = (),
        pages: Iterable[str] | None = None, now: datetime | None = None, web=None, record=None,
        on_connect: Callable[[Any], None] | None = None, out: Callable[[str], None] = print,
        minutes: float | None = None, clock: Callable[[], float] = monotonic_time.monotonic,
        gate_check: Callable[..., dict] | None = None) -> int:
    """One build. Returns 0 when published, held for review or not due (inputs not in yet).

    on_connect gets the writing connection first (the job binds its lineage run there); record,
    a lineage run, is told what was written. Reading stops after `minutes` (time_limit()). The publish
    gate is asked as CERTIFIED_FIGURES_GATE says (gate_mode(); gate_check replaces publish_gate.check
    in tests)."""
    import psycopg

    limit = time_limit(minutes)
    gate = gate_mode()
    begun = clock()
    web = web or load_web()
    pages, charts_mode = _charts().run_pages(pages, READERS)
    started = now or datetime.now(timezone.utc)
    today_hk = hong_kong_today(started)
    with shared_web_database(web, database_url):
        found, skipped = subjects(web, only_subjects)
        expected = expected_date(today_hk).isoformat()
        ready = [subject for subject in found if subject.dates and subject.dates[-1] >= expected]
        out(f"Certified figures: {len(found)} subjects, {len(ready)} with data for {expected}, "
            f"{len(skipped)} skipped.")
        if not ready and not backfill:
            out(f"Inputs for {expected} are not in yet, so nothing new is built or published.")
            return 0
        with psycopg.connect(database_url, options=f"-c app.tenant_id={web.TENANT_ID}") as connection:
            if on_connect is not None:
                on_connect(connection)
            published = published_rows(connection, [subject.id for subject in found])
            connection.rollback()
            published_dates: dict[str, set[str]] = {}
            for key in published:
                if charts_mode and pages == [_charts().PAGE] and key[1] not in _charts().FIGURES:
                    continue  # a run of the charts alone fills in the charts' own history
                published_dates.setdefault(key[0], set()).add(key[5])
            plan = work_plan(found, published_dates, backfill=backfill)
            # Each subject's pages are asked again on its newest date right after that date is read, so
            # a figure that moves with the clock reads the same both times.
            newest_due = {subject.id: max(due_dates(subject, published_dates.get(subject.id, ())), default=None)
                          for subject in found}
            notes: dict = {}
            rows: list[Row] = []
            disagreements: dict[str, list[str]] = {}
            errors: list[str] = []
            read: dict[str, list[str]] = {}
            checked: set[str] = set()

            def check(subject: Subject, day: str) -> None:
                checked.add(subject.id)
                try:
                    with_own_connections = page_disagreements(web, subject, day, pages, rows, today_hk)
                except Exception as error:  # noqa: BLE001
                    with_own_connections = [f"error: {type(error).__name__}"]
                if with_own_connections:
                    disagreements[mask(subject.id)] = with_own_connections

            done = 0
            for subject, day in plan:
                if clock() - begun >= limit * 60:
                    break
                done += 1
                read.setdefault(subject.id, []).append(day)
                try:
                    rows += page_rows(web, subject, day, pages, today_hk=today_hk, notes=notes)
                except Exception as error:  # noqa: BLE001 - a bug in a reader holds the build
                    errors.append(f"{mask(subject.id)} {day}: {type(error).__name__}")
                if day == newest_due[subject.id]:
                    check(subject, day)
            left = len(plan) - done
            for subject in found:   # the time limit came before a subject's newest date
                if read.get(subject.id) and subject.id not in checked:
                    check(subject, max(read[subject.id]))
            result = compare(rows, published)
            charts = _charts().settle(charts_mode, rows, result, disagreements, notes, errors) if charts_mode else None
            to_write = result["new"] + result["changed"]
            failures = rule_failures(to_write)
            verdict = gate_verdict(mode=gate, database_url=database_url, tenant_id=str(web.TENANT_ID),
                                   started=started, check=gate_check)
            checks = {
                "subjects": len(found), "subjects_ready": len(ready), "subjects_skipped": len(skipped),
                "expected_date": expected, "run_kind": "backfill" if backfill else "daily",
                "subject_dates_planned": len(plan), "subject_dates_read": done, "subject_dates_left": left,
                "minutes": limit, "figures_read": len(rows), "new": len(result["new"]), "changed": len(result["changed"]),
                "unchanged": result["unchanged"],
                "same_inputs_new_answer": sorted({row.figure_id for row in result["violations"]}),
                "same_inputs_new_answer_count": len(result["violations"]),
                "rule_failures": sorted(set(failures)), "rule_failure_count": len(failures),
                "page_disagreements": disagreements, "reader_errors": len(errors),
                "page_unavailable": notes.get("page_unavailable", {}),
                # publish_gate.check's verdict on the source jobs (gate_verdict); None when the setting is off.
                "publish_gate": verdict,
            }
            if charts is not None:
                checks["charts"] = charts
            outcomes = {"contract_rules": not failures, "same_inputs_same_answer": not result["violations"],
                        "pages_agree": not disagreements, "pages_read": not errors}
            if gate == "binding":
                outcomes["publish_gate"] = not verdict["failed"]
            checks.update(passed=sum(outcomes.values()), total=len(outcomes),
                          failed=[name for name, ok in outcomes.items() if not ok])
            reasons = []
            if result["violations"]:
                reasons.append(f"{len(result['violations'])} figures changed with the same inputs and version")
            if failures:
                reasons.append(f"{len(failures)} figures broke a contract rule")
            if disagreements:
                reasons.append(f"the pages disagree for {len(disagreements)} subjects")
            if errors:
                reasons.append(f"{len(errors)} subject dates could not be read")
            if gate == "binding" and verdict["failed"]:
                reasons.append(f"the publish gate failed for {', '.join(verdict['failed'])}")
            held_reason = "; ".join(reasons) or None
            out(f"Read {len(rows)} figures: {len(result['new'])} new, {len(result['changed'])} changed, "
                f"{result['unchanged']} unchanged. Checks: {len(result['violations'])} same-input changes, "
                f"{len(failures)} rule failures, {len(disagreements)} page disagreements, {len(errors)} read errors.")
            if verdict is not None:   # verdicts and counts only: the reasons name subjects
                out(f"Publish gate ({gate}, since {verdict['since'][:16]}Z): {verdict['verdict']}; "
                    + ", ".join(f"{job} {result['verdict']} ({result['reasons_total']} reasons)"
                                for job, result in verdict["jobs"].items()) + ".")
            if charts is not None:
                out(_charts().summary_line(charts))
            for line in errors[:20]:
                out(f"  read error {line}")
            for who, figure_ids in sorted(disagreements.items()):   # figure ids only, no figures
                out(f"  page disagreement {who}: {', '.join(figure_ids[:20])}")
            out(f"Read {done} of {len(plan)} subject dates."
                + (f" Stopped after {limit:g} minutes: the other {left} are left for the next run." if left else ""))
            if dry_run:
                out("Dry run: nothing written. " + ("Would hold: " + held_reason if held_reason
                                                    else "Would publish." if to_write else "Nothing to publish."))
                return 0
            if not to_write and not held_reason:
                out("Nothing changed, so no build is written.")
                return 0
            build_id = str(uuid.uuid4())
            status = write_build(connection, build_id=build_id, run_kind=checks["run_kind"], started_at=started,
                                 rows=to_write, checks=checks, held_reason=held_reason)
            if record is not None:
                record.output("gold.certified_builds", rows=1)
                record.output("gold.certified_figures", rows=len(to_write))
                record.facet(certified_build_id=build_id, certified_build_status=status)
            out(f"Build {build_id} {status}" + (f": {held_reason}." if held_reason else "."))
            return 0
