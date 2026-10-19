"""The account Desk page: live P&L and where it came from, live limits, quote age and a pre-trade check.

Switched by ``DESK_PAGE``:

* ``off`` (the default, and any value not listed here): no Desk tab, no Metrics guide entries, and
  both routes answer 404 exactly as if they did not exist.
* ``owners``: the Desk for owners and platform admins, on accounts whose live view they can see.
* ``on``: the Desk for everyone who can see the account's live view.

Access is exactly the live view's rule (:func:`ibkr_etl.live_view.live_view_allowed`: a database
sign-in, a physical account, not an onboarding preview, and the account's ``live_view`` switch)
plus this setting. Composites have no live positions of their own and get a 409, as on the
Modelling tab.

Reads silver and gold only, inside the website's read-only transaction:

* live: ``subject_live_summary`` and ``subject_live_positions`` (migration 063), and
  ``subject_live_executions`` and ``benchmark_live_quotes`` from the held migration
  **desk_live_views** (``infra/medallion/drafts/desk_live_views.sql``, 077 on its day), which must
  run before the Desk is switched on. Until it has run, the Desk still works and says what is
  missing: no fills today, or no market factor. A view that is there but cannot be read is read
  in a savepoint, so the rest of the transaction is unaffected and nothing answers 500.
* history: a year of daily marks (``subject_position_series``), the primary benchmark's daily
  returns (``performance_comparison_daily``) and daily FX (``reference_fx_rates``).

The maths is :func:`ibkr_etl.quant.desk_risk.desk_snapshot` and
:func:`ibkr_etl.quant.pre_trade.pre_trade_check`, unchanged. Holdings are keyed by conid. Limits are
``DESK_LIMITS`` (JSON) over :data:`DEFAULT_LIMITS`. The pre-trade check is hypothetical: nothing is
placed or sent to IBKR, and its money is in the NAV's (reporting) currency.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape
from typing import Any, Callable, Literal, Mapping, Sequence

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .live_collector import HONG_KONG, NEW_YORK, markets_open
from .live_view import PREVIEW_ROLES, SNAPSHOT_LIMIT_CLOSED, SNAPSHOT_LIMIT_OPEN, live_view_allowed
from .metrics_guide import metric_anchor
from .modelling import ModellingInputError, holding_return_matrix
from .quant import desk_risk, pre_trade
from .quant.holdings_risk import MIN_OBSERVATIONS, MIN_RESIDUAL_DEGREES, factor_risk_model, ledoit_wolf_covariance
from .risk_forecasting import _tail

LOGGER = logging.getLogger("uvicorn.error.desk")

DESK_SETTING = "DESK_PAGE"
LIMITS_SETTING = "DESK_LIMITS"
MODES = ("off", "owners", "on")
# The documented defaults, as fractions of NAV. A number is the most a measure may be either way;
# a [lowest, highest] pair is a range. DESK_LIMITS replaces any of them, adds others, or removes
# one with null, and can set max_quote_age_seconds and amber_at too, e.g.
# {"gross_exposure": 1.3, "var_1d": 0.04, "stale_quote_share": null, "max_quote_age_seconds": 600}.
DEFAULT_LIMITS: dict[str, Any] = {
    "gross_exposure": 1.5,             # 150% of NAV
    "net_exposure": 1.2,               # 120% of NAV either way
    "largest_position_weight": 0.25,   # 25% of NAV in one name
    "var_1d": 0.05,                    # one-day historical VaR at 99%: 5% of NAV
    "pnl_return": [-0.03, 1.0],        # today's P&L: a 3% loss limit, gains unlimited in practice
    "stale_quote_share": 0.10,         # at most 10% of holdings stale or without a quote
}
DEFAULT_SETTINGS: dict[str, float] = {
    "max_quote_age_seconds": desk_risk.DEFAULT_MAX_QUOTE_AGE_SECONDS,   # 15 minutes
    "amber_at": desk_risk.DEFAULT_AMBER_AT,                             # amber from 80% of a limit
}
REFRESH_SECONDS = 60
HISTORY_DAYS = 366          # calendar days of daily marks: the latest 252 sessions
VAR_LEVEL = 0.99
FX_MAX_AGE_DAYS = 4         # daily reference rates; a long weekend is four days
MAX_FILLS = 2000
MAX_ORDERS = 20
MARKET_FACTOR = "Market"
EXECUTIONS_VIEW = "subject_live_executions"
BENCHMARK_VIEW = "benchmark_live_quotes"
# benchmark_master ids → the symbol of the index contract the live collector subscribes to
# (LIVE_BENCHMARKS, live_contracts.is_benchmark). The id and the symbol without "^" also match.
LIVE_BENCHMARK_SYMBOLS = {"GSPC": "SPX", "SP500TR": "SPX", "IXIC": "COMP", "XNDX": "NDX", "HSI": "HSI"}
# When "the prior close" is for fills: the regular close of the instrument's market, as the live
# collector's market hours (live_collector.markets_open). Holidays are not modelled.
_HONG_KONG_CLOSE = (HONG_KONG, time(16, 10))
_NEW_YORK_CLOSE = (NEW_YORK, time(16, 0))
MARKET_CLOSES = {"HKD": _HONG_KONG_CLOSE, "CNH": _HONG_KONG_CLOSE, "CNY": _HONG_KONG_CLOSE}
COMPOSITE_MESSAGE = "Composites have no live positions of their own, so the Desk covers physical accounts only."
NOT_AVAILABLE = "Desk not available"
NO_SNAPSHOT = "No live snapshot has arrived for this account yet."
NOTICE = pre_trade.NOTICE + " Nothing is placed or sent to IBKR."
COST_NOTE = ("Daily volumes are not in silver yet, so square-root market impact (default coefficient 1.0) is not "
             "estimated for any order: the cost shown is half the live bid-ask spread only.")
# Once silver daily_volumes exists (phase 3) and the liquidity section is switched on for this viewer
# (QUANT_ANALYTICS_PHASE3), the pre-trade check reads each holding's average daily volume over its last
# VOLUME_SESSIONS sessions with volume, so market impact is estimated too.
VOLUMES_TABLE = "daily_volumes"
VOLUME_SESSIONS = 20
VOLUME_WINDOW_DAYS = 45
VOLUME_COST_NOTE = ("Square-root market impact (default coefficient 1.0) uses each holding's average daily volume over "
                    f"its last {VOLUME_SESSIONS} sessions in silver (daily_volumes); an order in a holding without "
                    "volume data, or in a new instrument, costs half the live bid-ask spread only.")
STALE_STATUSES = ("stale", "no_quote", "no_quote_time", "quote_after_as_of")

# Every Desk figure links to its Metrics guide entry (metrics_guide.DESK_DEFINITIONS).
GUIDE_NAMES = (
    "Intraday P&L", "Intraday P&L share of NAV", "Trading P&L from today's fills", "Intraday FX P&L",
    "P&L explain", "Beta to the primary benchmark", "Gross exposure (live)", "Net exposure (live)",
    "Largest single name (live)", "One-day VaR 99% (live)", "Quote age", "Stale quote share",
    "Desk limit status", "Pre-trade check", "Pre-trade trading cost",
)
GUIDE = {name: f"/metrics-guide#{metric_anchor(name)}" for name in GUIDE_NAMES}
# Limit name → (label on the page, its guide entry).
LIMIT_LABELS = {
    "gross_exposure": ("Gross exposure", "Gross exposure (live)"),
    "net_exposure": ("Net exposure", "Net exposure (live)"),
    "largest_position_weight": ("Largest single name", "Largest single name (live)"),
    "var_1d": ("One-day VaR 99%", "One-day VaR 99% (live)"),
    "pnl_return": ("Today's P&L ÷ NAV", "Intraday P&L share of NAV"),
    "stale_quote_share": ("Stale quotes", "Stale quote share"),
}


class OrderError(ValueError):
    """An order the Desk cannot place in the book, such as a symbol two holdings share (400)."""


# Settings and access -------------------------------------------------------------------------


def _environ(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def desk_mode(environ: Mapping[str, str] | None = None) -> str:
    """DESK_PAGE as off, owners or on; anything else is off."""

    value = str(_environ(environ).get(DESK_SETTING, "")).strip().lower()
    return value if value in MODES else "off"


def desk_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return desk_mode(environ) != "off"


def desk_allowed(access: Mapping[str, Any], user: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> bool:
    """The live view's own rule plus DESK_PAGE: owners and platform admins in "owners" mode, all in "on"."""

    mode = desk_mode(environ)
    if mode == "off" or not live_view_allowed(access, user):
        return False
    if mode == "owners":
        roles = {str(role) for role in user.get("roles") or ()} | {str(user.get("role", ""))}
        return bool(roles & PREVIEW_ROLES)
    return True


def desk_limits(environ: Mapping[str, str] | None = None) -> tuple[dict[str, Any], dict[str, float], str]:
    """The limits, the quote age and amber settings, and where they came from.

    A DESK_LIMITS that cannot be read is logged and ignored as a whole, so a typo never loosens
    one limit while keeping the others.
    """

    raw = str(_environ(environ).get(LIMITS_SETTING, "")).strip()
    limits, settings = dict(DEFAULT_LIMITS), dict(DEFAULT_SETTINGS)
    if not raw:
        return limits, settings, "defaults"
    try:
        given = json.loads(raw)
        if not isinstance(given, dict):
            raise ValueError("DESK_LIMITS must be a JSON object")
        for key, value in given.items():
            if key in settings:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{key} must be a number")
                settings[key] = float(value)
            elif value is None:
                limits.pop(key, None)
            else:
                limits[str(key)] = value
        if settings["max_quote_age_seconds"] <= 0:
            raise ValueError("max_quote_age_seconds must be positive")
        desk_risk.limit_check({}, limits, amber_at=settings["amber_at"])   # checks every limit's shape
    except (ValueError, TypeError, ModellingInputError) as error:
        LOGGER.warning("DESK_LIMITS could not be read, so the default limits apply: %s", error)
        return dict(DEFAULT_LIMITS), dict(DEFAULT_SETTINGS), "defaults: DESK_LIMITS could not be read"
    return limits, settings, "DESK_LIMITS"


def prior_close_time(currency: str, moment: datetime) -> datetime:
    """The regular close before ``moment``'s date in the instrument's market: fills after it are today's."""

    zone, close = MARKET_CLOSES.get(str(currency or "").upper(), _NEW_YORK_CLOSE)
    day = moment.astimezone(zone).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime.combine(day, close, tzinfo=zone)


def live_benchmark_symbols(benchmark: Mapping[str, Any]) -> list[str]:
    """Symbols the benchmark's live index quote may carry in benchmark_live_quotes."""

    benchmark_id = str(benchmark.get("benchmark_id") or "").upper()
    symbols = {benchmark_id, str(benchmark.get("symbol") or "").upper().lstrip("^")}
    if benchmark_id in LIVE_BENCHMARK_SYMBOLS:
        symbols.add(LIVE_BENCHMARK_SYMBOLS[benchmark_id])
    return sorted(symbol for symbol in symbols if symbol)


# Reading -------------------------------------------------------------------------------------


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds") if moment else None


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _gap(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _relation(connection, name: str) -> str | None:
    """silver.<name>, else public.<name>, whichever exists; None before desk_live_views has run.

    Asked of the catalog, which every role can read, rather than with to_regclass, which raises
    (and so ends the transaction) for a schema the role may not use.
    """

    for schema in ("silver", "public"):
        row = connection.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = %s AND c.relname = %s) AS present", (schema, name)).fetchone()
        if row and row["present"]:
            return f"{schema}.{name}"
    return None


def _optional_rows(connection, sql: str, parameters: Sequence[Any], what: str) -> list[Mapping[str, Any]] | None:
    """Rows of an optional view, read in a savepoint; None (and the transaction intact) if it cannot be read."""

    try:
        with connection.transaction():
            return list(connection.execute(sql, parameters).fetchall())
    except Exception as error:   # a missing grant or a view dropped since to_regclass: report it, never 500
        LOGGER.warning("The Desk could not read %s (%s)", what, type(error).__name__)
        return None


_SUMMARY_SQL = """
    SELECT observed_at, base_currency, net_liquidation, total_cash, daily_pnl, cash_by_currency
    FROM subject_live_summary
    WHERE tenant_id = %s AND subject_id = %s
"""
_POSITIONS_SQL = """
    SELECT conid, symbol, description, sec_type, currency, quantity, market_price, market_value,
           last_price, bid_price, ask_price, prior_close, market_data_type, quote_observed_at
    FROM subject_live_positions
    WHERE tenant_id = %s AND subject_id = %s
    ORDER BY abs(market_value) DESC NULLS LAST, symbol
"""
_EXECUTIONS_SQL = """
    SELECT exec_id, executed_at, conid, symbol, sec_type, currency, side, quantity, price
    FROM {relation}
    WHERE tenant_id = %s AND subject_id = %s AND executed_at > %s AND executed_at <= %s
    ORDER BY executed_at, exec_id
    LIMIT %s
"""
_BENCHMARK_SQL = """
    SELECT b.benchmark_id, b.symbol, b.name, m.reporting_currency
    FROM portfolio_benchmark_mapping m
    JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id AND b.tenant_id = m.tenant_id
    WHERE m.tenant_id = %s AND m.subject_id = %s AND b.active = true
      AND m.effective_from <= CURRENT_DATE::text
      AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)
    ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END, b.name
    LIMIT 1
"""
_BENCHMARK_RETURNS_SQL = """
    SELECT observation_date, benchmark_return FROM performance_comparison_daily
    WHERE tenant_id = %s AND subject_id = %s AND benchmark_id = %s
      AND calculation_version = %s AND reporting_currency = %s
      AND quality_status IN ('pass', 'warn') AND benchmark_return IS NOT NULL
      AND observation_date >= %s
    ORDER BY observation_date
"""
_BENCHMARK_QUOTE_SQL = """
    SELECT symbol, currency, last_price, bid_price, ask_price, prior_close, market_data_type, observed_at
    FROM {relation}
    WHERE tenant_id = %s AND upper(symbol) = ANY(%s)
    ORDER BY observed_at DESC
    LIMIT 1
"""
_MARKS_SQL = """
    SELECT snapshot_date, conid::text AS conid, mark_price_text
    FROM subject_position_series
    WHERE tenant_id = %s AND subject_id = %s AND conid::text = ANY(%s)
      AND snapshot_date BETWEEN %s AND %s
"""
_FX_SQL = """
    SELECT rate_date, from_currency, to_currency, rate, quality_status, provider
    FROM reference_fx_rates
    WHERE tenant_id = %s AND quality_status IN ('pass', 'warn') AND rate_date BETWEEN %s AND %s
      AND ((from_currency = ANY(%s) AND to_currency = %s) OR (from_currency = %s AND to_currency = ANY(%s)))
"""


def fx_table(rows: Sequence[Mapping[str, Any]], needed: Sequence[str], reporting: str, today: date) -> dict[str, dict]:
    """Currency → today's and the prior-close daily rate, in reporting currency per unit.

    The prior close is the latest rate dated before ``today``, now is the latest on or before it,
    and both must be at most FX_MAX_AGE_DAYS old. A direct quote beats an inverted one, a pass a
    warn, then the provider's name, so the choice is stable.
    """

    wanted = {str(currency).upper() for currency in needed}
    best: dict[str, dict[str, tuple[tuple, float]]] = {}
    for row in rows:
        rate = _float(row["rate"])
        if rate is None or rate <= 0:
            continue
        source, target = str(row["from_currency"]).upper(), str(row["to_currency"]).upper()
        if source in wanted and target == reporting:
            currency, value, direction = source, rate, 0
        elif source == reporting and target in wanted:
            currency, value, direction = target, 1.0 / rate, 1
        else:
            continue
        rank = (direction, 0 if row.get("quality_status") == "pass" else 1, str(row.get("provider") or ""))
        day = str(row["rate_date"])[:10]
        held = best.setdefault(currency, {}).get(day)
        if held is None or rank < held[0]:
            best[currency][day] = (rank, value)
    oldest, latest = (today - timedelta(days=FX_MAX_AGE_DAYS)).isoformat(), today.isoformat()
    table = {}
    for currency in sorted(wanted):
        days = sorted(day for day in best.get(currency, {}) if oldest <= day <= latest)
        before = [day for day in days if day < latest]
        if days and before:
            table[currency] = {"rate": best[currency][days[-1]][1], "prior_close": best[currency][before[-1]][1],
                               "rate_date": days[-1], "prior_date": before[-1]}
    return table


def read_inputs(connection, tenant_id: str, account_id: str, *, reporting_currency: str,
                comparison_version: Callable[[str, str], str], now: datetime) -> dict[str, Any]:
    """Everything the Desk needs, read from silver and gold in the caller's read-only transaction."""

    reporting = str(reporting_currency or "USD").strip().upper()
    inputs: dict[str, Any] = {
        "account_id": account_id, "reporting_currency": reporting, "now": now, "summary": None,
        "positions": [], "quotes": [], "labels": {}, "executions": None, "fills_since": {},
        "benchmark": None, "benchmark_returns": {}, "benchmark_quote": None, "marks": {}, "fx": {},
        "fx_needed": [], "missing": [],
    }
    missing = inputs["missing"]
    summary = connection.execute(_SUMMARY_SQL, (tenant_id, account_id)).fetchone()
    if summary is None:
        return inputs
    cash = summary.get("cash_by_currency") or {}
    if isinstance(cash, str):
        cash = json.loads(cash)
    snapshot_at = _utc(summary["observed_at"])
    inputs["summary"] = {
        "observed_at": snapshot_at,
        "base_currency": str(summary["base_currency"] or reporting).upper(),
        "net_liquidation": _float(summary["net_liquidation"]),
        "total_cash": _float(summary.get("total_cash")),
        "daily_pnl": _float(summary.get("daily_pnl")),
        "cash": {str(c).upper(): _float(v) for c, v in sorted(cash.items()) if _float(v) is not None},
    }
    for row in connection.execute(_POSITIONS_SQL, (tenant_id, account_id)).fetchall():
        conid = str(row["conid"])
        currency = str(row["currency"] or "").upper()
        inputs["positions"].append({
            "conid": conid, "symbol": row["symbol"] or conid, "currency": currency,
            "quantity": _float(row["quantity"]) or 0.0, "market_price": _float(row.get("market_price")),
            "market_value": _float(row.get("market_value")), "account_id": account_id,
        })
        inputs["labels"][conid] = {"symbol": row["symbol"] or conid, "description": row.get("description"),
                                   "sec_type": row.get("sec_type"), "currency": currency}
        if row.get("quote_observed_at") is not None:
            inputs["quotes"].append({
                "conid": conid, "observed_at": _utc(row["quote_observed_at"]),
                "last_price": _float(row.get("last_price")), "bid_price": _float(row.get("bid_price")),
                "ask_price": _float(row.get("ask_price")), "prior_close": _float(row.get("prior_close")),
                "market_data_type": row.get("market_data_type"),
            })
    # The engine reads as of the newest quote, so the prior close is the one before that moment.
    reference = max((quote["observed_at"] for quote in inputs["quotes"]), default=snapshot_at)
    today = reference.astimezone(timezone.utc).date()

    # Today's fills (desk_live_views).
    markets = {position["currency"] for position in inputs["positions"]} | {"USD", "HKD"}
    cutoffs = {currency: prior_close_time(currency, reference) for currency in sorted(markets)}
    relation = _relation(connection, EXECUTIONS_VIEW)
    if relation is None:
        missing.append(_gap("executions_view_missing",
                            "No fills today: the live fills view (subject_live_executions, from desk_live_views) is "
                            "not there yet, so a holding traded today is measured from the prior close."))
    else:
        rows = _optional_rows(connection, _EXECUTIONS_SQL.format(relation=relation),
                              (tenant_id, account_id, min(cutoffs.values()), snapshot_at, MAX_FILLS), relation)
        if rows is None:
            missing.append(_gap("executions_view_unreadable",
                                "No fills today: the live fills view could not be read, so a holding traded today "
                                "is measured from the prior close."))
        else:
            fills = []
            for row in rows:
                currency = str(row["currency"] or "").upper()
                if str(row.get("sec_type") or "").upper() == "CASH":
                    continue   # a currency conversion moves cash, not a holding
                cutoff = cutoffs.get(currency) or prior_close_time(currency, reference)
                executed_at = _utc(row["executed_at"])
                if executed_at <= cutoff or executed_at > snapshot_at:
                    continue   # before the prior close, or not yet in the snapshot's quantities
                fills.append({"exec_id": row.get("exec_id"), "executed_at": _utc(row["executed_at"]),
                              "conid": str(row["conid"]), "symbol": row.get("symbol"), "currency": currency,
                              "side": row["side"], "quantity": _float(row["quantity"]), "price": _float(row["price"])})
            inputs["executions"] = fills
            inputs["fills_since"] = {currency: _iso(moment) for currency, moment in cutoffs.items()}
            if len(rows) >= MAX_FILLS:
                missing.append(_gap("fills_truncated", f"Only the first {MAX_FILLS} fills today are counted."))

    # The primary benchmark: its history for the betas and its live quote for today's market move.
    start = (today - timedelta(days=HISTORY_DAYS)).isoformat()
    mapping = connection.execute(_BENCHMARK_SQL, (tenant_id, account_id)).fetchone()
    if mapping is None:
        missing.append(_gap("no_primary_benchmark",
                            "No market factor: the account has no primary benchmark, so the market's move stays "
                            "in stock-specific."))
    else:
        benchmark = {"benchmark_id": str(mapping["benchmark_id"]), "symbol": str(mapping["symbol"] or ""),
                     "name": str(mapping["name"] or mapping["benchmark_id"]),
                     "reporting_currency": str(mapping["reporting_currency"] or reporting)}
        inputs["benchmark"] = benchmark
        try:
            version = comparison_version(account_id, benchmark["reporting_currency"])
        except ValueError as error:
            version = None
            missing.append(_gap("benchmark_history_unavailable", f"No betas: {error}."))
        if version:
            for row in connection.execute(_BENCHMARK_RETURNS_SQL, (
                    tenant_id, account_id, benchmark["benchmark_id"], version, benchmark["reporting_currency"],
                    start)).fetchall():
                value = _float(row["benchmark_return"])
                if value is not None:
                    inputs["benchmark_returns"][str(row["observation_date"])[:10]] = value
        relation = _relation(connection, BENCHMARK_VIEW)
        if relation is None:
            missing.append(_gap("benchmark_view_missing",
                                "No market factor: the live benchmark quotes view (benchmark_live_quotes, from "
                                "desk_live_views) is not there yet, so the market's move stays in stock-specific."))
        else:
            rows = _optional_rows(connection, _BENCHMARK_QUOTE_SQL.format(relation=relation),
                                  (tenant_id, live_benchmark_symbols(benchmark)), relation)
            if rows is None:
                missing.append(_gap("benchmark_view_unreadable",
                                    "No market factor: the live benchmark quotes view could not be read."))
            elif not rows:
                missing.append(_gap("benchmark_quote_missing",
                                    f"No market factor: there is no live quote for {benchmark['name']}, so the "
                                    "market's move stays in stock-specific."))
            else:
                row = rows[0]
                inputs["benchmark_quote"] = {
                    "symbol": row["symbol"], "currency": row.get("currency"), "last_price": _float(row["last_price"]),
                    "bid_price": _float(row.get("bid_price")), "ask_price": _float(row.get("ask_price")),
                    "prior_close": _float(row["prior_close"]), "market_data_type": row.get("market_data_type"),
                    "observed_at": _utc(row["observed_at"]) if row["observed_at"] else None,
                }

    # A year of daily marks of today's holdings.
    held = sorted({position["conid"] for position in inputs["positions"] if position["quantity"]})
    if held:
        for mark in connection.execute(_MARKS_SQL, (tenant_id, account_id, held, start, today.isoformat())).fetchall():
            value = _float(mark["mark_price_text"])
            if value is not None and value > 0:
                inputs["marks"].setdefault(str(mark["conid"]), {})[str(mark["snapshot_date"])[:10]] = value

    # Daily FX for every currency that is not the reporting currency.
    needed = sorted(({position["currency"] for position in inputs["positions"]} | set(inputs["summary"]["cash"])
                     | {inputs["summary"]["base_currency"]}) - {reporting, ""})
    inputs["fx_needed"] = needed
    if needed:
        rows = connection.execute(_FX_SQL, (tenant_id, (today - timedelta(days=FX_MAX_AGE_DAYS)).isoformat(),
                                            today.isoformat(), needed, reporting, reporting, needed)).fetchall()
        inputs["fx"] = fx_table(rows, needed, reporting, today)
    return inputs


def read_volumes(connection, tenant_id: str, inputs: Mapping[str, Any]) -> dict[str, float] | None:
    """Each live holding's average daily volume (shares, its last VOLUME_SESSIONS sessions with
    volume in silver daily_volumes), by conid. None until that table exists, and then the
    pre-trade check keeps today's half-spread cost alone."""

    if _relation(connection, VOLUMES_TABLE) is None:
        return None
    from . import market_inputs
    from .quant.liquidity import average_daily_volume

    today = inputs["now"].astimezone(timezone.utc).date()
    # A holding's volumes are stored under its IBKR conid (daily_volumes.fetch_and_load).
    conids = {str(position["conid"]): position["conid"] for position in inputs["positions"] if position["quantity"]}
    found = market_inputs.read_daily_volumes(
        connection, tenant_id=tenant_id, instrument_ids=conids,
        start=(today - timedelta(days=VOLUME_WINDOW_DAYS)).isoformat(), end=today.isoformat())
    if found is None:
        return None
    volumes = {}
    for key, series in sorted(found.items()):
        if key not in conids or not series:
            continue
        average = average_daily_volume(series, window=VOLUME_SESSIONS)["average_daily_volume"]
        if average:
            volumes[conids[key]] = average
    return volumes


# Analysis ------------------------------------------------------------------------------------


def _quote_price(quote: Mapping[str, Any]) -> float | None:
    last = quote.get("last_price")
    if last is not None and last > 0:
        return last
    bid, ask = quote.get("bid_price"), quote.get("ask_price")
    if bid is not None and ask is not None and 0 < bid <= ask:
        return (bid + ask) / 2.0
    return None


def _analysis(inputs: Mapping[str, Any], settings: Mapping[str, float]) -> dict[str, Any]:
    """The NAV, live weights, history, VaR, betas and today's market move, shared by both routes."""

    reporting, summary = inputs["reporting_currency"], inputs["summary"]
    max_age = settings["max_quote_age_seconds"]
    missing: list[dict[str, str]] = []
    fx = inputs["fx"]
    for currency in inputs["fx_needed"]:
        if currency not in fx:
            missing.append(_gap(f"no_fx_rate_{currency}",
                                f"There is no daily {currency}/{reporting} rate within {FX_MAX_AGE_DAYS} days, so "
                                f"{currency} figures cannot be converted into {reporting}."))
    fx_rates = {currency: {"rate": row["rate"], "prior_close": row["prior_close"]} for currency, row in fx.items()}
    base = summary["base_currency"]
    rates = (1.0, 1.0) if base == reporting else (fx[base]["prior_close"], fx[base]["rate"]) if base in fx else None
    net, daily = summary["net_liquidation"], summary["daily_pnl"]
    nav_prior = nav_now = None
    if rates is not None and net is not None:
        nav_now = net * rates[1] if net * rates[1] > 0 else None
        prior = (net - (daily or 0.0)) * rates[0]
        nav_prior = prior if prior > 0 else None
    if nav_prior is None:
        missing.append(_gap("nav_unavailable",
                            "The NAV at the prior close is not available, so exposures, VaR and today's P&L as a share "
                            "of NAV cannot be measured."))
    nav_source = ("live net liquidation less IBKR's P&L for today" if daily is not None
                  else "live net liquidation (IBKR gave no P&L for today)")
    cash = summary["cash"]
    pre = desk_risk.intraday_pnl(inputs["positions"], inputs["quotes"], fx_rates=fx_rates,
                                 reporting_currency=reporting, max_quote_age_seconds=max_age,
                                 executions=inputs["executions"], cash=cash)
    held = [row for row in pre["holdings"] if row["quantity"]]
    weights = ({row["key"]: row["market_value_reporting"] / nav_prior for row in held
                if row["market_value_reporting"] is not None} if nav_prior else {})
    stale = [row for row in held if row["status"] in STALE_STATUSES]

    history = holding_return_matrix(inputs["marks"]) if inputs["marks"] else {"dates": [], "returns": {}}
    dates, columns = history["dates"], history["returns"]
    var = etl = None
    in_history = [key for key in weights if key in columns]
    if nav_prior is not None and not held:
        var = etl = 0.0   # all cash
    elif in_history and len(dates) >= MIN_OBSERVATIONS:
        series = [math.fsum(weights[key] * columns[key][t] for key in in_history) for t in range(len(dates))]
        var, etl = _tail(sorted(series), VAR_LEVEL)
    elif held and nav_prior is not None:
        missing.append(_gap("history_short",
                            f"One-day VaR needs at least {MIN_OBSERVATIONS} days of daily marks for today's holdings; "
                            f"there are {len(dates) if in_history else 0}."))

    bench = inputs["benchmark_returns"]
    common = [index for index, day in enumerate(dates) if day in bench]
    model = None
    if inputs["benchmark"] is not None:
        if columns and len(common) >= max(MIN_OBSERVATIONS, 2 + MIN_RESIDUAL_DEGREES):
            try:
                model = factor_risk_model({key: [column[i] for i in common] for key, column in columns.items()},
                                          {MARKET_FACTOR: [bench[dates[i]] for i in common]})
            except ModellingInputError as error:
                missing.append(_gap("betas_unavailable", f"No betas, so no market part in the explain: {error}"))
        else:
            missing.append(_gap("betas_unavailable",
                                f"No betas, so no market part in the explain: they need at least {MIN_OBSERVATIONS} "
                                f"days with both daily marks and benchmark returns; there are {len(common)}."))
    betas = {name: row[0] for name, row in zip(model["names"], model["betas"])} if model else {}
    portfolio_beta = math.fsum(w * betas[key] for key, w in weights.items() if key in betas) if betas else None

    market = None
    quote = inputs["benchmark_quote"]
    as_of = datetime.fromisoformat(pre["as_of"]) if pre["as_of"] else None
    if quote is not None:
        price, close, observed = _quote_price(quote), quote.get("prior_close"), quote.get("observed_at")
        age = abs((as_of - observed).total_seconds()) if as_of and observed else None
        if price is None or close is None or close <= 0 or observed is None:
            missing.append(_gap("benchmark_quote_unusable",
                                "No market factor: the benchmark's live quote has no price or prior close."))
        elif age is None or age > max_age:
            missing.append(_gap("benchmark_quote_stale",
                                "No market factor: the benchmark's live quote is not within the quote age limit of "
                                "the Desk's as-of time, so the market's move stays in stock-specific."))
        else:
            market = price / close - 1.0
    return {"fx_rates": fx_rates, "cash": cash, "nav_prior": nav_prior, "nav_now": nav_now, "nav_source": nav_source,
            "pre": pre, "held": held, "weights": weights, "stale": stale,
            "stale_share": len(stale) / len(held) if held else None,
            "dates": dates, "columns": columns, "var": var, "etl": etl,
            "no_history": sorted(key for key in weights if key not in columns),
            "model": model, "betas": betas, "portfolio_beta": portfolio_beta, "common_days": len(common),
            "market_return": market, "missing": missing}


def _finite(value: Any) -> Any:
    """JSON-safe: non-finite floats become null and times ISO text."""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        return _float(value)
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Mapping):
        return {str(key): _finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(item) for item in value]
    return value


def _snapshot_state(summary: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    is_open = markets_open(now)
    age = (now - summary["observed_at"]).total_seconds()
    limit = SNAPSHOT_LIMIT_OPEN if is_open else SNAPSHOT_LIMIT_CLOSED
    return {"observed_at": _iso(summary["observed_at"]), "age_seconds": age,
            "behind": age > limit.total_seconds(), "markets_open": is_open}


def desk_payload(inputs: Mapping[str, Any], *, limits: Mapping[str, Any], settings: Mapping[str, float],
                 now: datetime, limits_source: str) -> dict[str, Any]:
    """GET /desk: desk_risk.desk_snapshot on the live rows, with what was used and what is missing."""

    summary = inputs["summary"]
    payload: dict[str, Any] = {
        "account_id": inputs["account_id"], "model_id": "desk-risk", "model_version": desk_risk.MODEL_VERSION,
        "available": False, "reason": None, "as_of": None, "reporting_currency": inputs["reporting_currency"],
        "refresh_seconds": REFRESH_SECONDS, "labels": inputs["labels"],
        "inputs": {"limits_source": limits_source, "limits": dict(limits),
                   "max_quote_age_seconds": settings["max_quote_age_seconds"], "amber_at": settings["amber_at"],
                   "var_level": VAR_LEVEL},
        "missing": list(inputs["missing"]),
        "notes": ["Live figures from the IBKR connection, against the prior close; the daily IBKR statement stays the "
                  "book of record. Commissions are left out."],
        "results": {},
    }
    if summary is None:
        payload["reason"] = NO_SNAPSHOT
        payload["missing"].append(_gap("no_live_snapshot", NO_SNAPSHOT))
        return payload
    payload["inputs"]["snapshot"] = _snapshot_state(summary, now)
    try:
        analysis = _analysis(inputs, settings)
        payload["missing"] += analysis["missing"]
        pre, nav = analysis["pre"], analysis["nav_prior"]
        metrics = {"var_1d": analysis["var"], "expected_tail_loss_1d": analysis["etl"],
                   "pnl_return": pre["total"]["pnl"] / nav if nav else None,
                   "stale_quote_share": analysis["stale_share"]}
        market = analysis["market_return"]
        if market is not None and analysis["model"] is not None:
            exposures, factor_returns, market_factor = analysis["model"], {MARKET_FACTOR: market}, MARKET_FACTOR
        else:   # no market part: the explain still splits FX from the stock-specific rest
            exposures, factor_returns, market_factor = {}, {}, None
        snapshot = desk_risk.desk_snapshot(
            inputs["positions"], inputs["quotes"], reporting_currency=inputs["reporting_currency"], nav=nav,
            fx_rates=analysis["fx_rates"], executions=inputs["executions"], cash=analysis["cash"],
            max_quote_age_seconds=settings["max_quote_age_seconds"], exposures=exposures,
            factor_returns=factor_returns, market_factor=market_factor, metrics=metrics, limits=limits,
            amber_at=settings["amber_at"])
    except ModellingInputError as error:
        payload["reason"] = str(error)
        return payload
    benchmark, quote = inputs["benchmark"], inputs["benchmark_quote"]
    payload.update({"available": True, "as_of": snapshot["as_of"]})
    payload["inputs"].update({
        "positions": len(analysis["held"]),
        "fills_today": len(inputs["executions"]) if inputs["executions"] is not None else None,
        "fills_since": inputs["fills_since"],
        "history_days": len(analysis["dates"]), "history_holdings": len(analysis["columns"]),
        "benchmark": None if benchmark is None else {
            **benchmark, "live_symbol": quote["symbol"] if quote else None,
            "quote_observed_at": quote["observed_at"] if quote else None,
            "return_today": analysis["market_return"], "common_days": analysis["common_days"]},
        "betas": {"holdings": len(analysis["betas"]), "by_holding": analysis["betas"],
                  "portfolio_beta": analysis["portfolio_beta"],
                  "observations": analysis["model"]["observations"] if analysis["model"] else 0},
        "var": {"value": analysis["var"], "expected_tail_loss": analysis["etl"], "level": VAR_LEVEL,
                "observations": len(analysis["dates"]), "no_history": analysis["no_history"]},
        "nav": {"prior_close": analysis["nav_prior"], "now": analysis["nav_now"],
                "currency": inputs["reporting_currency"], "source": analysis["nav_source"]},
        "fx": inputs["fx"],
    })
    if pre["excluded"]:
        payload["notes"].append(f"{len(pre['excluded'])} holding(s) left out of today's P&L: stale, missing or "
                                "unusable quotes are listed in the positions table, never filled in.")
    payload["notes"].append("Quote age is the time since a quote last changed, against the newest quote on the account; "
                            "outside a market's hours its quotes read as stale.")
    payload["results"] = {"desk_snapshot": snapshot}
    return payload


def pre_trade_payload(inputs: Mapping[str, Any], orders: Sequence[Mapping[str, Any]], *,
                      limits: Mapping[str, Any], settings: Mapping[str, float]) -> dict[str, Any]:
    """POST /desk/pre-trade: pre_trade.pre_trade_check on today's live positions; nothing is placed."""

    if inputs["summary"] is None:
        raise ModellingInputError(NO_SNAPSHOT)
    analysis = _analysis(inputs, settings)
    nav = analysis["nav_now"]
    if nav is None:
        raise ModellingInputError("The live NAV is not available in the reporting currency, so orders cannot be "
                                  "weighed against it.")
    book, rates, by_symbol, spreads = {}, {}, {}, {}
    quotes = {quote["conid"]: quote for quote in inputs["quotes"]}
    for row in analysis["held"]:
        key, rate = row["key"], row["fx_rate"]
        rates[key] = rate
        book[key] = {"quantity": row["quantity"], "multiplier": row["multiplier"],
                     "price": row["price"] * rate if row["price"] is not None and rate is not None else None}
        by_symbol.setdefault(str(row["symbol"]).strip().upper(), []).append(key)
        quote = quotes.get(key) or {}
        bid, ask = quote.get("bid_price"), quote.get("ask_price")
        if bid is not None and ask is not None and 0 < bid <= ask:
            spreads[key] = {"bid": bid, "ask": ask}
    labels = {key: inputs["labels"].get(key, {}).get("symbol") or key for key in book}
    checked = []
    for index, order in enumerate(orders, start=1):
        conid = str(order.get("conid") or "").strip()
        symbol = str(order.get("symbol") or "").strip().upper()
        if conid:
            key = conid if conid in book else f"new:{conid}"
        else:
            matches = by_symbol.get(symbol, [])
            if len(matches) > 1:
                raise OrderError(f"Order {index}: {symbol} matches {len(matches)} holdings; give its conid instead.")
            key = matches[0] if matches else f"new:{symbol}"
        labels.setdefault(key, symbol or conid)
        engine_order = {"holding": key, "side": order["side"], "quantity": order["quantity"]}
        limit_price = order.get("limit_price")
        if limit_price is not None:
            if key in book:   # a held instrument's limit is in its own currency
                if rates.get(key) is not None:
                    engine_order["price"] = limit_price * rates[key]
            else:             # a new instrument's limit is read in the NAV's currency
                engine_order["price"] = limit_price
        checked.append(engine_order)
    columns = analysis["columns"] if len(analysis["dates"]) >= MIN_OBSERVATIONS else {}
    volumes = inputs.get("average_daily_volumes")   # None unless silver daily_volumes exists (read_volumes)
    covariance = None
    if columns:
        try:
            covariance = ledoit_wolf_covariance(columns)
        except ModellingInputError:
            covariance = None
    result = pre_trade.pre_trade_check(
        book, checked, returns_history={"dates": analysis["dates"], "returns": columns} if columns else {},
        covariance=covariance, factor_model=analysis["model"],
        limits={key: value for key, value in limits.items() if key in pre_trade.LIMIT_METRICS},
        average_daily_volumes=None if volumes is None else {key: volumes[key] for key in book if key in volumes},
        spreads=spreads, nav=nav, var_level=VAR_LEVEL, amber_at=settings["amber_at"])
    notes = [COST_NOTE if volumes is None else
             VOLUME_COST_NOTE + f" {sum(key in volumes for key in book)} of {len(book)} holdings have volume data.",
             "Positions, prices and spreads are today's live ones, in the reporting currency at today's daily FX rate."]
    if covariance is not None:
        notes.append(f"Ex-ante volatility uses a Ledoit-Wolf covariance of {len(analysis['dates'])} days of daily "
                     "marks; VaR and expected tail loss at 99% replay the same days.")
    else:
        notes.append(f"Risk model: {result['risk_model']}.")
    if analysis["model"] is None:
        notes.append("No betas could be fitted, so the check has no beta to the primary benchmark.")
    return {
        "account_id": inputs["account_id"], "model_id": "pre-trade", "model_version": pre_trade.MODEL_VERSION,
        "hypothetical": True, "notice": NOTICE, "as_of": analysis["pre"]["as_of"],
        "reporting_currency": inputs["reporting_currency"], "nav": nav, "labels": labels,
        "missing": list(inputs["missing"]) + analysis["missing"], "notes": notes,
        "results": {"pre_trade_check": result},
    }


# Request bodies --------------------------------------------------------------------------------


class DeskOrder(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str | None = Field(default=None, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9 .\-/^=&]*$")
    conid: str | None = Field(default=None, pattern=r"^[0-9]{1,12}$")
    side: Literal["BUY", "SELL"]
    quantity: float = Field(gt=0, le=1e9, allow_inf_nan=False)
    limit_price: float | None = Field(default=None, gt=0, le=1e9, allow_inf_nan=False)

    @model_validator(mode="after")
    def _names_an_instrument(self) -> "DeskOrder":
        if not self.symbol and not self.conid:
            raise ValueError("Give each order a symbol or a conid")
        return self


class PreTradeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: list[DeskOrder] = Field(min_length=1, max_length=MAX_ORDERS)


# Page ------------------------------------------------------------------------------------------


def _guide_link(name: str, text: str, css: str = "metric-guide-heading-link") -> str:
    return (f'<a class="{css}" href="{escape(GUIDE[name], quote=True)}">{escape(text)} '
            '<span aria-hidden="true">↗</span></a>')


DESK_CSS = """
[data-section="desk"] .desk-status{margin-top:10px}
[data-section="desk"] .desk-subhead{margin-top:22px}
.desk-bars{display:grid;gap:9px;margin:16px 0}
.desk-bar{display:grid;grid-template-columns:minmax(120px,1.3fr) 3fr minmax(96px,auto);align-items:center;gap:12px}
.desk-bar-track{position:relative;height:12px;background:var(--surface-soft,#f2f4f7)}
.desk-bar-track::after{position:absolute;top:-3px;bottom:-3px;left:50%;width:1px;content:"";background:var(--line-strong,#98a2b3)}
.desk-bar-fill{position:absolute;top:0;bottom:0}
.desk-bar-fill.gain{background:var(--gain,#0f7a4a)}.desk-bar-fill.loss{background:var(--loss,#b42318)}
.desk-bar-value{text-align:right;font-variant-numeric:tabular-nums}
.desk-bar.total{padding-top:8px;border-top:1px solid var(--line,#d0d5dd);font-weight:650}
.desk-table td,.desk-table th{white-space:nowrap}
.desk-table tr.desk-stale td{background:rgba(194,137,0,.10)}
.desk-table tr.desk-stale td:last-child{color:var(--warn-ink,#7a5000);font-weight:650}
[data-section="desk"] .desk-orders input,[data-section="desk"] .desk-orders select{min-width:0;width:100%;max-width:11em;padding:7px 10px;color:var(--ink);background:var(--surface);border:1px solid var(--line-strong);font:inherit}
[data-section="desk"] .desk-orders input[type=number]{text-align:right}
@media (max-width:640px){.desk-bar{grid-template-columns:1fr auto}.desk-bar-track{grid-column:1/-1;grid-row:2}}
"""

DESK_CARDS = f"""
<div class="card wide desk-card" id="desk-pnl"><div class="label">{_guide_link("Intraday P&L", "Intraday P&L")}</div>
<div class="muted">Today's profit or loss against the prior close, from the live IBKR positions and quotes. The daily IBKR statement stays the book of record. Refreshes every minute while this page is open.</div>
<div id="desk-status" class="freshness desk-status" role="status" aria-live="polite">Loading the live desk…</div>
<div id="desk-pnl-metrics" class="metrics"></div><div id="desk-missing" class="warning" hidden></div><div id="desk-pnl-note" class="muted"></div></div>
<div class="card wide desk-card" id="desk-explain"><div class="label">{_guide_link("P&L explain", "Where today's P&L came from")}</div>
<div class="muted">Today's P&amp;L split into the market's move (each holding's beta × the primary benchmark's move today), currency moves, the stock-specific rest, today's fills and the revaluation of foreign cash. The parts add up to the total.</div>
<div id="desk-explain-metrics" class="metrics"></div><div id="desk-explain-bars" class="desk-bars"></div><div id="desk-explain-note" class="muted"></div></div>
<div class="card wide desk-card" id="desk-limits"><div class="label">{_guide_link("Desk limit status", "Live limits")}</div>
<div class="muted">Each live measure against its limit: green below 80% of it, amber from 80%, red outside it. <span id="desk-limits-source"></span></div>
<div class="table-scroll"><table class="desk-table"><thead><tr><th class="text-col">Measure</th><th>Value</th><th>Limit</th><th>Used</th><th class="text-col">Status</th></tr></thead><tbody id="desk-limits-table"></tbody></table></div><div id="desk-limits-note" class="muted"></div></div>
<div class="card wide desk-card" id="desk-positions"><div class="label">{_guide_link("Quote age", "Positions and quote age")}</div>
<div class="muted">Each holding at its live price, with how long ago its quote last changed. Highlighted rows have a stale, missing or unusable quote and are left out of today's P&amp;L.</div>
<div class="table-scroll"><table class="desk-table"><thead><tr><th class="text-col">Instrument</th><th>Quantity</th><th>Live price</th><th>Prior close</th><th>Today</th><th>{_guide_link("Intraday P&L", "P&L", "metric-guide-table-link")}</th><th>{_guide_link("Gross exposure (live)", "Weight", "metric-guide-table-link")}</th><th>{_guide_link("Beta to the primary benchmark", "Beta", "metric-guide-table-link")}</th><th>{_guide_link("Quote age", "Quote age", "metric-guide-table-link")}</th><th class="text-col">Quote</th></tr></thead><tbody id="desk-positions-table"></tbody></table></div><div id="desk-positions-note" class="muted"></div></div>
<div class="card wide desk-card" id="desk-pretrade"><div class="label">{_guide_link("Pre-trade check", "Pre-trade check")}</div>
<div class="muted">What these orders would do to exposure, risk and the limits, on today's live positions and prices.</div>
<div class="warning"><strong>Hypothetical.</strong> This check is on paper only: nothing is placed or sent to IBKR, and it is not advice.</div>
<div class="table-scroll"><table class="desk-table desk-orders"><thead><tr><th class="text-col">Symbol or conid</th><th class="text-col">Side</th><th>Quantity</th><th>Limit price (optional)</th><th></th></tr></thead><tbody id="desk-orders"></tbody></table></div>
<div class="actions"><button id="desk-order-add" class="button" type="button">Add an order</button><button id="desk-check" class="button" type="button">Check these orders</button></div>
<div id="desk-pt-status" class="muted" role="status" aria-live="polite">A limit price on a holding is in its own currency; for an instrument you do not hold, it is read in the reporting currency.</div>
<div id="desk-pt-result" hidden><div id="desk-pt-notice" class="warning"></div>
<div class="table-scroll"><table class="desk-table"><thead><tr><th class="text-col">Measure</th><th>Before</th><th>After</th><th>Change</th></tr></thead><tbody id="desk-pt-summary"></tbody></table></div>
<div class="label desk-subhead">{_guide_link("Desk limit status", "Limits after the orders")}</div><div id="desk-pt-limits" class="muted"></div>
<div class="label desk-subhead">{_guide_link("Pre-trade trading cost", "Estimated trading cost")}</div>
<div class="table-scroll"><table class="desk-table"><thead><tr><th class="text-col">Order</th><th>Value</th><th>Spread</th><th>Cost</th><th>Cost (bps)</th><th class="text-col">Notes</th></tr></thead><tbody id="desk-pt-costs"></tbody></table></div>
<div id="desk-pt-notes" class="muted"></div></div></div>
"""

_SCRIPT = r"""<script>
(() => {
  const config = __CONFIG__;
  const view = document.querySelector('.page-view[data-section="desk"]');
  if (!view) return;
  const base = `/api/portfolio/${encodeURIComponent(config.account)}/desk`;
  const $ = selector => view.querySelector(selector);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const ok = n => n != null && Number.isFinite(Number(n));
  const num = (n, digits = 2) => ok(n) ? new Intl.NumberFormat('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}).format(Number(n)) : '—';
  const qty = n => ok(n) ? new Intl.NumberFormat('en-US', {maximumFractionDigits: 4}).format(Number(n)) : '—';
  const amount = n => ok(n) ? num(n, Math.abs(Number(n)) >= 10000 ? 0 : 2) : '—';
  const tone = n => ok(n) ? (Number(n) > 0 ? 'gain' : Number(n) < 0 ? 'loss' : '') : '';
  const signedAmount = n => ok(n) ? `<span class="${tone(n)}">${Number(n) > 0 ? '+' : ''}${amount(n)}</span>` : '—';
  const pct = (n, digits = 2) => ok(n) ? `${num(Number(n) * 100, digits)}%` : '—';
  const signedPct = (n, digits = 2) => ok(n) ? `<span class="${tone(n)}">${Number(n) > 0 ? '+' : ''}${pct(n, digits)}</span>` : '—';
  const age = s => !ok(s) ? '—' : s < 90 ? `${Math.round(s)} s` : s < 5400 ? `${Math.round(s / 60)} min` : s < 172800 ? `${(s / 3600).toFixed(1)} h` : `${Math.round(s / 86400)} d`;
  const hkt = iso => iso ? new Intl.DateTimeFormat('en-GB', {day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Hong_Kong'}).format(new Date(iso)) + ' HKT' : '—';
  const guide = name => config.guide[name] || '/metrics-guide';
  const link = (name, text, css = 'metric-guide-table-link') => `<a class="${css}" href="${esc(guide(name))}">${esc(text)} <span aria-hidden="true">↗</span></a>`;
  const tile = (name, value, label) => `<a class="metric metric-guide-card" href="${esc(guide(name))}" aria-label="Read the Metrics Guide explanation for ${esc(name)}"><div class="label">${esc(label || name)} <span aria-hidden="true">↗</span></div><div class="value">${value}</div></a>`;
  const pill = status => `<span class="ops-pill ${status === 'red' ? 'is-fail' : status === 'amber' ? 'is-warning' : status === 'green' ? 'is-pass' : ''}">${esc(status ? status[0].toUpperCase() + status.slice(1) : 'Not available')}</span>`;
  const detail = async response => { try { const body = await response.json(); if (typeof body.detail === 'string') return body.detail; if (Array.isArray(body.detail)) return body.detail.map(d => d.msg).join('; '); } catch (error) {} return `The request failed (error ${response.status}).`; };
  const ccy = () => config.currency;
  const symbolOf = (labels, key) => (labels && labels[key] && (labels[key].symbol || labels[key])) || String(key).replace(/^new:/, '');

  function showMissing(body) {
    const box = $('#desk-missing');
    const items = (body.missing || []).map(m => `<li>${esc(m.message)}</li>`).join('');
    box.hidden = !items;
    box.innerHTML = items ? `<strong>Not everything is available yet</strong><ul>${items}</ul>` : '';
  }
  function render(body) {
    const status = $('#desk-status');
    showMissing(body);
    if (!body.available) {
      status.className = 'freshness late desk-status';
      status.textContent = body.reason || 'The live desk is not available.';
      ['#desk-pnl-metrics', '#desk-explain-metrics', '#desk-explain-bars', '#desk-limits-table', '#desk-positions-table'].forEach(id => { $(id).innerHTML = ''; });
      return;
    }
    const snapshot = body.results.desk_snapshot, inputs = body.inputs, total = snapshot.pnl.total, state = inputs.snapshot || {};
    status.className = `freshness desk-status${state.behind ? ' late' : ''}`;
    status.textContent = `As of ${hkt(body.as_of)} · live snapshot ${age(state.age_seconds)} old${state.behind ? ', behind' : ''} · markets ${state.markets_open ? 'open' : 'closed'}`;
    $('#desk-pnl-metrics').innerHTML = [
      tile('Intraday P&L', `${signedAmount(total.pnl)} ${esc(ccy())}`),
      tile('Intraday P&L share of NAV', signedPct(snapshot.pnl_return), "Today's P&L ÷ NAV"),
      tile("Trading P&L from today's fills", inputs.fills_today == null ? '—' : `${signedAmount(total.trading_pnl)} ${esc(ccy())}`, `Today's fills (${inputs.fills_today == null ? 'not available' : inputs.fills_today})`),
      tile('Intraday FX P&L', `${signedAmount((total.fx_pnl || 0) + (total.cash_fx_pnl || 0))} ${esc(ccy())}`, 'FX P&L'),
      tile('Stale quote share', pct(snapshot.metrics.stale_quote_share, 0), 'Stale quotes'),
    ].join('');
    $('#desk-pnl-note').textContent = `${snapshot.pnl.counted} of ${inputs.positions} holdings counted in today's P&L. NAV at the prior close ${amount(inputs.nav.prior_close)} ${ccy()} (${inputs.nav.source}). ${(body.notes || []).join(' ')}`;
    renderExplain(body, snapshot, inputs);
    renderLimits(body, snapshot, inputs);
    renderPositions(body, snapshot, inputs);
  }
  function renderExplain(body, snapshot, inputs) {
    const explain = snapshot.explain, bench = inputs.benchmark, betas = inputs.betas || {};
    $('#desk-explain-metrics').innerHTML = [
      tile('Beta to the primary benchmark', ok(betas.portfolio_beta) ? num(betas.portfolio_beta) : '—', bench ? `Beta to ${bench.name}` : 'Beta to the primary benchmark'),
      tile('P&L explain', signedPct(bench && bench.return_today), bench ? `${bench.name} today` : 'Benchmark today'),
    ].join('');
    if (!explain || !explain.available) {
      $('#desk-explain-bars').innerHTML = '';
      $('#desk-explain-note').textContent = (explain && explain.reason) || 'The explain is not available.';
      return;
    }
    const amounts = explain.amounts || {}, rec = explain.reconciliation || {};
    const parts = [
      ['Market (beta × benchmark)', explain.market_factor ? amounts[explain.market_factor] : null, explain.market_factor ? '' : 'no market factor'],
      ['Currency on holdings', amounts.FX],
      ['Stock-specific', amounts['Stock-specific']],
      ["Today's fills", inputs.fills_today == null ? null : rec.trading_pnl, inputs.fills_today == null ? 'not counted yet' : ''],
      ['Currency on cash', rec.cash_fx_pnl],
    ];
    const largest = Math.max(1e-9, ...parts.map(p => Math.abs(Number(p[1]) || 0)));
    const bar = (label, value, note, css = '') => {
      const width = ok(value) ? Math.abs(value) / largest * 50 : 0;
      const fill = ok(value) && value !== 0 ? `<span class="desk-bar-fill ${value > 0 ? 'gain' : 'loss'}" style="${value > 0 ? 'left:50%' : `left:${50 - width}%`};width:${width}%"></span>` : '';
      return `<div class="desk-bar${css}"><div>${esc(label)}${note ? ` <span class="muted">(${esc(note)})</span>` : ''}</div><div class="desk-bar-track" aria-hidden="true">${fill}</div><div class="desk-bar-value">${ok(value) ? signedAmount(value) : '—'}</div></div>`;
    };
    $('#desk-explain-bars').innerHTML = parts.map(p => bar(p[0], p[1], p[2])).join('') + bar(`Total (${ccy()})`, rec.total_pnl, '', ' total');
    const difference = ok(rec.difference) && Math.abs(rec.difference) < 0.005 ? 0 : rec.difference;
    $('#desk-explain-note').textContent = `Weighted by prior-close value over ${explain.holdings.length} holdings with fresh quotes. Betas from ${betas.observations || 0} days of history. Difference from the total: ${amount(difference)} ${ccy()}.`;
  }
  function renderLimits(body, snapshot, inputs) {
    const limits = snapshot.limits;
    if (!limits) { $('#desk-limits-table').innerHTML = '<tr><td colspan="5" class="muted">No limits are set.</td></tr>'; return; }
    const label = name => (config.limits[name] || [name.replace(/_/g, ' '), 'Desk limit status']);
    const bound = c => c.limit_type === 'range' ? `${pct(c.lower)} to ${pct(c.upper)}` : `≤ ${pct(c.upper)}`;
    const rows = limits.checks.map(c => `<tr><td class="text-col">${link(label(c.metric)[1], label(c.metric)[0])}</td><td>${c.metric === 'pnl_return' ? signedPct(c.value) : pct(c.value)}</td><td>${bound(c)}</td><td>${pct(c.usage, 0)}</td><td class="text-col">${pill(c.status)}</td></tr>`);
    const gaps = limits.missing_metrics.map(m => `<tr><td class="text-col">${link(label(m)[1], label(m)[0])}</td><td>—</td><td></td><td></td><td class="text-col">${pill(null)}</td></tr>`);
    $('#desk-limits-table').innerHTML = rows.concat(gaps).join('') || '<tr><td colspan="5" class="muted">No limits are set.</td></tr>';
    $('#desk-limits-source').textContent = `Limits: ${inputs.limits_source}. Quote age limit ${age(inputs.max_quote_age_seconds)}.`;
    $('#desk-limits-note').textContent = limits.breaches.length ? `${limits.breaches.length} limit(s) breached. A prompt to look, not an instruction to trade.` : 'No limit is breached.';
  }
  function renderPositions(body, snapshot, inputs) {
    const weights = (snapshot.exposures && snapshot.exposures.weights) || {}, betas = (inputs.betas && inputs.betas.by_holding) || {};
    const rows = snapshot.pnl.holdings.map(h => {
      const stale = h.status !== 'ok';
      const label = body.labels[h.key] || {};
      const quoteState = h.status === 'ok' ? 'Fresh' : h.status === 'stale' ? 'Stale' : (h.reason || h.status);
      return `<tr class="${stale ? 'desk-stale' : ''}"><td class="text-col"><strong>${esc(h.symbol)}</strong>${label.description ? `<div class="muted">${esc(label.description)}</div>` : ''}</td>
        <td>${qty(h.quantity)}</td><td>${num(h.price, 4)} ${esc(h.currency)}</td><td>${num(h.prior_close, 4)}</td><td>${signedPct(h.return)}</td>
        <td>${signedAmount(h.pnl_reporting)}</td><td>${pct(weights[h.key])}</td><td>${ok(betas[h.key]) ? num(betas[h.key]) : '—'}</td>
        <td>${age(h.quote_age_seconds)}</td><td class="text-col">${esc(quoteState)}${h.delayed ? ' · delayed' : ''}</td></tr>`;
    });
    $('#desk-positions-table').innerHTML = rows.join('') || '<tr><td colspan="10" class="muted">No live positions.</td></tr>';
    const fresh = snapshot.freshness;
    $('#desk-positions-note').textContent = `P&L in ${ccy()}. ${fresh.fresh} of ${fresh.holdings} quotes fresh, ${fresh.stale} stale, ${fresh.missing} missing, ${fresh.delayed} delayed. Oldest quote ${age(fresh.oldest_age_seconds)}.`;
  }

  let timer = null, failures = 0, busy = false, stopped = false, controller = null;
  async function load() {
    if (stopped || busy || document.hidden) return;
    clearTimeout(timer); busy = true; controller = new AbortController();
    const timeout = setTimeout(() => controller && controller.abort(), 20000);
    try {
      const response = await fetch(base, {credentials: 'same-origin', cache: 'no-store', signal: controller.signal});
      if ([401, 403, 404].includes(response.status)) {
        stopped = true;
        $('#desk-status').className = 'freshness stale desk-status';
        $('#desk-status').textContent = await detail(response);
        return;
      }
      if (!response.ok) throw new Error(await detail(response));
      render(await response.json());
      failures = 0;
    } catch (error) {
      if (stopped) return;
      failures++;
      $('#desk-status').className = 'freshness late desk-status';
      $('#desk-status').textContent = 'The live desk is unavailable · retrying';
    } finally {
      clearTimeout(timeout); controller = null; busy = false;
      if (!stopped) timer = setTimeout(load, failures ? Math.min(600000, config.refresh * 1000 * 2 ** failures) : config.refresh * 1000);
    }
  }
  document.addEventListener('visibilitychange', () => { if (document.hidden) clearTimeout(timer); else load(); });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); if (controller) controller.abort(); });

  const orders = $('#desk-orders');
  const addOrder = () => {
    if (orders.children.length >= config.max_orders) return;
    const row = document.createElement('tr');
    row.innerHTML = `<td class="text-col"><input type="text" maxlength="32" autocomplete="off" placeholder="AAPL or 265598" aria-label="Symbol or conid"></td>
      <td class="text-col"><select aria-label="Side"><option value="BUY">Buy</option><option value="SELL">Sell</option></select></td>
      <td><input type="number" min="0" step="any" aria-label="Quantity"></td><td><input type="number" min="0" step="any" aria-label="Limit price"></td>
      <td><button class="button" type="button" aria-label="Remove this order">Remove</button></td>`;
    row.querySelector('button').addEventListener('click', () => { row.remove(); if (!orders.children.length) addOrder(); });
    orders.appendChild(row);
  };
  addOrder();
  $('#desk-order-add').addEventListener('click', addOrder);
  const measures = [
    ['Gross exposure', 'gross_exposure', 'Gross exposure (live)'], ['Net exposure', 'net_exposure', 'Net exposure (live)'],
    ['Cash', 'cash_weight', 'Pre-trade check'], ['Largest holding', 'largest_position_weight', 'Largest single name (live)'],
    ['Top 5 weight', 'top_5_weight', 'Pre-trade check'], ['Ex-ante volatility', 'ex_ante_volatility', 'Pre-trade check'],
    ['One-day VaR 99%', 'var_1d', 'One-day VaR 99% (live)'], ['Expected tail loss 99%', 'expected_tail_loss_1d', 'Pre-trade check'],
  ];
  function renderCheck(body) {
    const check = body.results.pre_trade_check, labels = body.labels || {};
    $('#desk-pt-notice').textContent = body.notice;
    const rows = measures.map(([label, key, entry]) => `<tr><td class="text-col">${link(entry, label)}</td><td>${pct(check.before[key])}</td><td>${pct(check.after[key])}</td><td>${signedPct(check.change[key])}</td></tr>`);
    const beforeBeta = (check.before.factor_exposures || {}).Market, afterBeta = (check.after.factor_exposures || {}).Market;
    if (ok(beforeBeta) || ok(afterBeta)) rows.push(`<tr><td class="text-col">${link('Beta to the primary benchmark', 'Beta to the benchmark')}</td><td>${num(beforeBeta)}</td><td>${num(afterBeta)}</td><td>${ok(beforeBeta) && ok(afterBeta) ? num(afterBeta - beforeBeta) : '—'}</td></tr>`);
    $('#desk-pt-summary').innerHTML = rows.join('');
    const limits = check.limits;
    $('#desk-pt-limits').innerHTML = !limits ? 'No limits are set.' : `Before ${pill(limits.status_before)} After ${pill(limits.status_after)}. ` +
      (limits.new_breaches.length ? `New breaches: ${limits.new_breaches.map(b => esc((config.limits[b.metric] || [b.metric])[0])).join(', ')}.` : 'The orders add no breach.') +
      (limits.breaches_cleared.length ? ` Cleared: ${limits.breaches_cleared.map(m => esc((config.limits[m] || [m])[0])).join(', ')}.` : '');
    const cost = check.trading_cost;
    const costRows = check.orders.map(o => `<tr><td class="text-col">${esc(o.side === 'BUY' ? 'Buy' : 'Sell')} ${qty(Math.abs(o.quantity))} ${esc(symbolOf(labels, o.holding))}</td><td>${amount(o.value)} ${esc(ccy())}</td>
      <td>${ok(o.relative_spread) ? `${num(o.relative_spread * 10000, 1)} bps` : '—'}</td><td>${ok(o.cost) ? `${amount(o.cost)} ${esc(ccy())}` : '—'}</td><td>${ok(o.cost_bps) ? num(o.cost_bps, 1) : '—'}</td>
      <td class="text-col">${o.missing_cost_data.includes('spread') ? 'No live bid and ask. ' : ''}${o.missing_cost_data.includes('impact') ? 'Impact not estimated.' : ''}</td></tr>`);
    const refused = check.refused_orders.map(o => `<tr><td class="text-col">${esc(o.side === 'BUY' ? 'Buy' : 'Sell')} ${qty(Math.abs(o.quantity))} ${esc(symbolOf(labels, o.holding))}</td><td colspan="4">Not checked</td><td class="text-col">${esc(String(o.reason || '').replace(/new:/g, ''))}</td></tr>`);
    $('#desk-pt-costs').innerHTML = costRows.concat(refused).join('') + `<tr><td class="text-col"><strong>Total</strong></td><td>${amount(cost.traded_value)} ${esc(ccy())}</td><td></td><td>${amount(cost.total)} ${esc(ccy())}</td><td>${ok(cost.bps_of_traded_value) ? num(cost.bps_of_traded_value, 1) : '—'}</td><td></td></tr>`;
    const missing = (body.missing || []).map(m => m.message);
    $('#desk-pt-notes').textContent = [...(body.notes || []), ...missing].join(' ');
    $('#desk-pt-result').hidden = false;
  }
  $('#desk-check').addEventListener('click', async () => {
    const button = $('#desk-check'), status = $('#desk-pt-status');
    const list = [...orders.children].map(row => {
      const [instrument, quantity, price] = row.querySelectorAll('input');
      const text = instrument.value.trim(), order = {side: row.querySelector('select').value, quantity: Number(quantity.value)};
      if (/^[0-9]+$/.test(text)) order.conid = text; else order.symbol = text;
      if (price.value !== '') order.limit_price = Number(price.value);
      return text || quantity.value ? order : null;
    }).filter(Boolean);
    if (!list.length) { status.textContent = 'Enter at least one order.'; return; }
    button.disabled = true; status.textContent = 'Checking… nothing is sent to IBKR.';
    try {
      const response = await fetch(`${base}/pre-trade`, {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({orders: list})});
      if (!response.ok) throw new Error(await detail(response));
      renderCheck(await response.json());
      status.textContent = 'Checked on paper. Nothing was placed or sent to IBKR.';
    } catch (error) {
      status.textContent = error.message || 'The check could not run.';
    } finally { button.disabled = false; }
  });
  load();
})();
</script>"""


def page_view(account_id: str, reporting_currency: str) -> str:
    """The Desk section of the account page: its cards, style and script, JSON-encoded so no value breaks out."""

    config = json.dumps({
        "account": account_id, "currency": str(reporting_currency or "USD"), "refresh": REFRESH_SECONDS,
        "max_orders": MAX_ORDERS, "guide": GUIDE, "limits": {key: list(value) for key, value in LIMIT_LABELS.items()},
    }, ensure_ascii=False).replace("<", "\\u003c")
    return (f'<div class="page-view active" data-section="desk"><style>{DESK_CSS}</style>{DESK_CARDS}'
            + _SCRIPT.replace("__CONFIG__", config) + "</div>")


# Routes ----------------------------------------------------------------------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def install(
    app: FastAPI,
    *,
    authorized_user: Callable[..., dict],
    access_context: Callable[[str, dict], dict],
    read_database: Callable[[], Any],
    comparison_version: Callable[[str, str], str],
    tenant_id: str,
    now: Callable[[], datetime] = _utc_now,
    environ: Mapping[str, str] | None = None,
) -> None:
    """Add GET /api/portfolio/{account_id}/desk and POST .../desk/pre-trade.

    With DESK_PAGE off both answer 404 Not Found before sign-in is checked, as if they were not
    there. The POST takes a JSON body under the same sign-in and same-origin protection as the
    website's other POST endpoints, and places nothing.
    """

    def require_switched_on() -> None:
        if not desk_enabled(environ):
            raise HTTPException(status_code=404, detail="Not Found")

    def desk_access(account_id: str, user: dict) -> dict:
        access = access_context(account_id, user)
        if str(access.get("subject_type") or "physical_account") != "physical_account":
            raise HTTPException(status_code=409, detail=COMPOSITE_MESSAGE)
        if not desk_allowed(access, user, environ):
            raise HTTPException(status_code=404, detail=NOT_AVAILABLE)
        return access

    def inputs_for(account_id: str, access: Mapping[str, Any], moment: datetime, *,
                   volumes: bool = False) -> dict[str, Any]:
        with read_database() as connection:
            inputs = read_inputs(connection, tenant_id, account_id,
                                 reporting_currency=str(access.get("reporting_currency") or "USD"),
                                 comparison_version=comparison_version, now=moment)
            if volumes and inputs["summary"] is not None:
                inputs["average_daily_volumes"] = read_volumes(connection, tenant_id, inputs)
            return inputs

    @app.get("/api/portfolio/{account_id}/desk")
    def desk_live(
        account_id: str,
        _switch: None = Depends(require_switched_on),
        user: dict[str, object] = Depends(authorized_user),
    ) -> dict:
        """Today's live P&L, its explain, live limits and quote age (desk_risk.desk_snapshot)."""
        access = desk_access(account_id, user)
        limits, settings, source = desk_limits(environ)
        moment = now()
        inputs = inputs_for(account_id, access, moment)
        return _finite(desk_payload(inputs, limits=limits, settings=settings, now=moment, limits_source=source))

    @app.post("/api/portfolio/{account_id}/desk/pre-trade")
    def desk_pre_trade(
        account_id: str,
        payload: PreTradeRequest,
        _switch: None = Depends(require_switched_on),
        user: dict[str, object] = Depends(authorized_user),
    ) -> dict:
        """A hypothetical pre-trade check of proposed orders on today's live positions; nothing is placed."""
        access = desk_access(account_id, user)
        limits, settings, _source = desk_limits(environ)
        # Market impact from daily volumes follows the liquidity section's own switch
        # (QUANT_ANALYTICS_PHASE3), so the Desk's costs change only on that section's day.
        from . import market_inputs

        volumes = market_inputs.section_allowed("liquidity", user, environ)
        inputs = inputs_for(account_id, access, now(), volumes=volumes)
        if inputs["summary"] is None:
            raise HTTPException(status_code=409, detail=NO_SNAPSHOT)
        try:
            result = pre_trade_payload(inputs, [order.model_dump() for order in payload.orders],
                                       limits=limits, settings=settings)
        except OrderError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except ModellingInputError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return _finite(result)
