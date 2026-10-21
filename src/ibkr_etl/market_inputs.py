"""Phase 3 market inputs: readers for the four silver tables the phase 3 quant sections use.

Phase 3 data design (frontier-analytics/phase3-data-design.md) and the fixed schemas. Each table is
created by its own held migration and loader, on its own day:

| Table | Read by | Reader |
|---|---|---|
| `daily_volumes` | `liquidity`, the Desk's pre-trade market impact | `read_daily_volumes`, by a holding's IBKR conid (zeros skipped) |
| `factor_returns` | `factor_attribution` | `read_factor_returns` (Kenneth French rows, latest vintage per date) |
| `macro_series` | `macro` | `read_macro_series` (latest vintage per date) |
| `institutional_holdings` | `crowding` | `read_institutional_holdings` (each filer's latest filing) |

Readers query the unqualified name, which resolves to the public `security_invoker` view once the
table is in silver. Until a table's migration has run, its reader returns None ("not loaded
yet"): the query runs in a savepoint, so a missing relation, schema or grant leaves the caller's
transaction usable. Placeholders are ``%s``, so the same SQL runs on a psycopg connection (the
website) and on ``db.PostgresConnection`` (the jobs).

``QUANT_ANALYTICS_PHASE3`` switches each phase 3 section on, for example
``liquidity=shadow,macro=on``: ``off`` (the default for a section not listed), ``shadow`` (built,
and shown to owners only) or ``on``. Nothing in this module reads the setting on its own.
"""
from __future__ import annotations

import logging
import math
import os
from collections import defaultdict
from typing import Any, Iterable, Mapping, Sequence

LOGGER = logging.getLogger(__name__)

PHASE3_SETTING = "QUANT_ANALYTICS_PHASE3"
MODES = ("off", "shadow", "on")
# Each phase 3 section and the silver table it needs.
SECTION_TABLES = {
    "liquidity": "daily_volumes",
    "factor_attribution": "factor_returns",
    "macro": "macro_series",
    "crowding": "institutional_holdings",
}
PHASE3_SECTIONS = tuple(SECTION_TABLES)
MACRO_SERIES = ("DGS10", "DGS2", "DTWEXBGS", "DCOILBRENTEU", "VIXCLS", "USDCNH", "DEXKOUS")


# --- The setting -------------------------------------------------------------------------

def phase3_modes(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """``{section: mode}`` from QUANT_ANALYTICS_PHASE3; a section missing, misspelt or with an
    unknown mode is off."""

    text = (os.environ if environ is None else environ).get(PHASE3_SETTING, "") or ""
    modes = {name: "off" for name in PHASE3_SECTIONS}
    for part in str(text).split(","):
        name, separator, mode = part.partition("=")
        name, mode = name.strip().lower(), mode.strip().lower()
        if separator and name in modes and mode in MODES:
            modes[name] = mode
    return modes


def enabled_sections(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The phase 3 sections the build computes: shadow or on, in a fixed order."""

    modes = phase3_modes(environ)
    return tuple(name for name in PHASE3_SECTIONS if modes[name] in ("shadow", "on"))


def section_allowed(section: str, user: Mapping[str, Any] | None, environ: Mapping[str, str] | None = None) -> bool:
    """On for everyone with access, or shadow and this viewer is an owner (as QUANT_ANALYTICS_PAGES)."""

    mode = phase3_modes(environ).get(section, "off")
    return mode == "on" or (mode == "shadow" and str((user or {}).get("role") or "") == "owner")


# --- Identifiers -------------------------------------------------------------------------
# A holding's daily volumes are stored under its IBKR conid as instrument_id (the holdings' own
# fetch, ibkr_etl.daily_volumes.fetch_and_load); only the benchmarks' rows (SPY, ^HSI) use
# market_data_prices' instrument ids. So a holding's volumes are read by its conid, never by symbol.
#
# A holding's CUSIP is ibkr_etl.sec_13f.cusip_from_isin, the 13F loader's own rule, so holdings and
# filings always match the same way. The crowding section imports it when it runs; nothing here
# does, so the website and the v1 build never load the loader.

# --- Reading ---------------------------------------------------------------------------

def _missing_errors() -> tuple[type[BaseException], ...]:
    import psycopg

    return (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName,
            psycopg.errors.InsufficientPrivilege, psycopg.errors.UndefinedColumn)


def _rows(connection: Any, sql: str, parameters: Sequence[Any], what: str) -> list[Mapping[str, Any]] | None:
    """The rows of one query in a savepoint; None when its relation (or schema, grant or column)
    is not there yet, with the caller's transaction left usable."""

    try:
        with connection.transaction():
            return list(connection.execute(sql, parameters).fetchall())
    except _missing_errors() as error:
        LOGGER.info("%s is not loaded yet (%s)", what, type(error).__name__)
        return None


TABLE_EXISTS_SQL = """
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class AS c JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname IN ('silver', 'public') AND c.relname = %s
) AS present
"""


def table_exists(connection: Any, name: str) -> bool:
    """Whether silver.<name> or its public view is there yet. Asked of the catalog, which every
    role can read, so it never ends the caller's transaction."""

    row = connection.execute(TABLE_EXISTS_SQL, (name,)).fetchone()
    return bool(row and row["present"])


def _day(value: Any) -> str:
    return str(value)[:10]


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# A zero is stored as Yahoo reports it (an exchange holiday) and is skipped here, as a day without volume.
DAILY_VOLUMES_SQL = """
SELECT instrument_id, price_date, volume
FROM daily_volumes
WHERE tenant_id = %s AND instrument_id = ANY(%s) AND price_date BETWEEN %s AND %s AND volume > 0
ORDER BY instrument_id, price_date
"""


def read_daily_volumes(connection: Any, *, tenant_id: str, instrument_ids: Iterable[str], start: str,
                       end: str) -> dict[str, dict[str, float]] | None:
    """``{instrument_id: {date: volume}}`` from start to end (inclusive), days with volume only; None
    before the table exists. For holdings, pass their IBKR conids: that is the instrument_id their
    rows are stored under. An instrument with no volume in the window is left out (today most
    holdings have none until the holdings' fetch has run for them)."""

    ids = sorted({str(value) for value in instrument_ids if value})
    if not ids:
        return {}
    rows = _rows(connection, DAILY_VOLUMES_SQL, (tenant_id, ids, start, end), "daily_volumes")
    if rows is None:
        return None
    found: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        volume = _float(row["volume"])
        if volume is not None and volume > 0:
            found[str(row["instrument_id"])][_day(row["price_date"])] = volume
    return dict(found)


# Regions are fama_french's keys, lower case (us, developed, asia_pacific_ex_japan: a CHECK keeps
# them so). A vintage holds only the rows it added or changed, so the latest vintage of each factor
# and date is the series as last published. The ETF stand-ins (source etf-proxy) are left out.
FACTOR_SOURCE = "kenneth-french"
FACTOR_RETURNS_SQL = """
SELECT DISTINCT ON (factor, return_date) factor, return_date, value, source, vintage
FROM factor_returns
WHERE tenant_id = %s AND region = %s AND source = %s AND return_date BETWEEN %s AND %s
ORDER BY factor, return_date, vintage DESC, fetched_at DESC
"""


def read_factor_returns(connection: Any, *, tenant_id: str, region: str, start: str,
                        end: str) -> dict[str, Any] | None:
    """One region's daily Kenneth French factor returns, the latest vintage of each factor and date
    (``region`` is fama_french's key, such as ``developed``).

    Returns ``{"region", "factors": {date: {factor: value}}, "sources": {source: rows},
    "vintages": [oldest, newest]}`` (values are fractions, RF included), or None before
    factor_returns exists.
    """

    rows = _rows(connection, FACTOR_RETURNS_SQL, (tenant_id, region, FACTOR_SOURCE, start, end), "factor_returns")
    if rows is None:
        return None
    factors: dict[str, dict[str, float]] = defaultdict(dict)
    sources: dict[str, int] = defaultdict(int)
    vintages = []
    for row in rows:
        value = _float(row["value"])
        if value is None:
            continue
        factors[_day(row["return_date"])][str(row["factor"])] = value
        sources[str(row["source"])] += 1
        vintages.append(_day(row["vintage"]))
    return {"region": region, "factors": {day: dict(sorted(values.items())) for day, values in sorted(factors.items())},
            "sources": dict(sorted(sources.items())),
            "vintages": [min(vintages), max(vintages)] if vintages else None}


MACRO_SERIES_SQL = """
SELECT DISTINCT ON (series_id, observation_date) series_id, observation_date, value, units, source, vintage
FROM macro_series
WHERE tenant_id = %s AND series_id = ANY(%s) AND observation_date BETWEEN %s AND %s
ORDER BY series_id, observation_date, vintage DESC, fetched_at DESC
"""


def read_macro_series(connection: Any, *, tenant_id: str, series_ids: Iterable[str] = MACRO_SERIES, start: str,
                      end: str) -> dict[str, Any] | None:
    """Macro levels, the latest vintage of each series and date.

    Returns ``{"series": {series_id: {date: value}}, "units": {series_id: units},
    "sources": {series_id: source}}``, or None before macro_series exists.
    """

    wanted = sorted({str(value) for value in series_ids})
    rows = _rows(connection, MACRO_SERIES_SQL, (tenant_id, wanted, start, end), "macro_series")
    if rows is None:
        return None
    series: dict[str, dict[str, float]] = defaultdict(dict)
    units: dict[str, str] = {}
    sources: dict[str, str] = {}
    for row in rows:
        value = _float(row["value"])
        if value is None:
            continue
        name = str(row["series_id"])
        series[name][_day(row["observation_date"])] = value
        units[name] = str(row["units"])
        sources[name] = str(row["source"])
    return {"series": {name: dict(sorted(values.items())) for name, values in sorted(series.items())},
            "units": dict(sorted(units.items())), "sources": dict(sorted(sources.items()))}


# Each filer's latest quarter filed by the date, and that quarter's latest accession filed by then:
# the newest filed_at, then the highest accession, as ibkr_etl.sec_13f.LATEST_SQL takes it (an
# accession starts with the filing agent's id, so the highest alone is not the newest filing). The
# loader stores a new-holdings amendment as the earlier report plus its new lines, so the latest
# accession is the whole quarter's holdings on its own.
FILINGS_SQL = """
SELECT DISTINCT ON (filer_cik) filer_cik, filer_name, period, accession, filed_at
FROM (
    SELECT filer_cik, period, accession, MAX(filer_name) AS filer_name, MAX(filed_at) AS filed_at
    FROM institutional_holdings
    WHERE tenant_id = %s AND period <= %s AND filed_at <= %s
    GROUP BY filer_cik, period, accession
) AS filings
ORDER BY filer_cik, period DESC, filed_at DESC, accession DESC
"""
# Only common shares (SH) held outright count: options (PUT, CALL) and principal amounts (PRN)
# are not stakes in the company.
_CHOSEN = """
FROM institutional_holdings AS h
JOIN unnest(%s::text[], %s::text[], %s::text[]) AS c(filer_cik, period, accession)
  ON h.filer_cik = c.filer_cik AND h.period = c.period AND h.accession = c.accession
WHERE h.tenant_id = %s AND h.put_call = '' AND h.shares_type = 'SH' AND h.value_usd > 0
"""
FILER_TOTALS_SQL = """
SELECT h.filer_cik, COUNT(*) AS positions, SUM(h.value_usd) AS total_value,
       SUM(CASE WHEN h.cusip = ANY(%s) THEN 0 ELSE h.value_usd END) AS rest_value,
       SUM(CASE WHEN h.cusip = ANY(%s) THEN 0 ELSE h.value_usd * h.value_usd END) AS rest_squares,
       SUM(CASE WHEN h.cusip = ANY(%s) THEN 0 ELSE 1 END) AS rest_positions
""" + _CHOSEN + """
GROUP BY h.filer_cik
ORDER BY h.filer_cik
"""
STAKES_SQL = """
SELECT h.filer_cik, h.cusip, SUM(h.shares) AS shares, SUM(h.value_usd) AS value_usd
""" + _CHOSEN + """
  AND h.cusip = ANY(%s)
GROUP BY h.filer_cik, h.cusip
ORDER BY h.filer_cik, h.cusip
"""


def read_institutional_holdings(connection: Any, *, tenant_id: str, as_of: str,
                                cusips: Iterable[str]) -> dict[str, Any] | None:
    """The 13F filers' latest filings known on ``as_of``, for comparing with one portfolio.

    For each filer: the latest quarter (period) on or before ``as_of`` filed by then, and that
    quarter's latest accession filed by then (the newest ``filed_at``, then the highest accession,
    as ``sec_13f.LATEST_SQL``). Only shares held outright count (``shares_type`` SH, no put or
    call). Returns ``{"filers": [{filer_cik, filer_name, period, accession, filed_at, positions,
    total_value, rest_value, rest_squares, rest_positions}], "stakes": [{filer_cik, cusip, shares,
    value_usd}]}``: the stakes in ``cusips`` row by row, and the rest of each filing as its total
    value, sum of squared values and count, which is all a cosine similarity needs. None before
    institutional_holdings exists.
    """

    wanted = sorted({str(value).strip().upper() for value in cusips if value})
    try:
        with connection.transaction():
            filings = list(connection.execute(FILINGS_SQL, (tenant_id, as_of, as_of)).fetchall())
            if not filings:
                return {"filers": [], "stakes": []}
            arrays = ([str(row["filer_cik"]) for row in filings], [_day(row["period"]) for row in filings],
                      [str(row["accession"]) for row in filings])
            totals = {str(row["filer_cik"]): row for row in connection.execute(
                FILER_TOTALS_SQL, (wanted, wanted, wanted, *arrays, tenant_id)).fetchall()}
            stakes = list(connection.execute(STAKES_SQL, (*arrays, tenant_id, wanted)).fetchall()) if wanted else []
    except _missing_errors() as error:
        LOGGER.info("institutional_holdings is not loaded yet (%s)", type(error).__name__)
        return None
    filers = []
    for row in filings:
        cik = str(row["filer_cik"])
        total = totals.get(cik) or {}
        filers.append({
            "filer_cik": cik, "filer_name": str(row["filer_name"] or cik), "period": _day(row["period"]),
            "accession": str(row["accession"]), "filed_at": _day(row["filed_at"]),
            "positions": int(total.get("positions") or 0), "total_value": _float(total.get("total_value")) or 0.0,
            "rest_value": _float(total.get("rest_value")) or 0.0,
            "rest_squares": _float(total.get("rest_squares")) or 0.0,
            "rest_positions": int(total.get("rest_positions") or 0),
        })
    return {"filers": filers,
            "stakes": [{"filer_cik": str(row["filer_cik"]), "cusip": str(row["cusip"]).upper(),
                        "shares": _float(row["shares"]), "value_usd": _float(row["value_usd"])} for row in stakes]}


INSTRUMENTS_SQL = """
SELECT conid, symbol, listing_exchange, currency, isin, asset_category
FROM instruments
WHERE conid = ANY(%s)
ORDER BY conid
"""


def read_instruments(connection: Any, conids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """``{conid: {symbol, listing_exchange, currency, isin, asset_category}}`` from silver instruments."""

    wanted = sorted({str(value) for value in conids if value})
    if not wanted:
        return {}
    return {str(row["conid"]): {key: row[key] for key in ("symbol", "listing_exchange", "currency", "isin",
                                                         "asset_category")}
            for row in connection.execute(INSTRUMENTS_SQL, (wanted,)).fetchall()}
