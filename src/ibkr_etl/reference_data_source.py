"""Reference data for derived-daily: v1.1's published file, or v2's own tables.

derived-daily reads reference_data.json (sectors by conid and the USD risk-free
rate), which the legacy v1.1 job publishes. REFERENCE_DATA_SOURCE=database
builds the same shape from v2's silver tables instead: instrument_classifications
and risk_free_rates. v2 does not record issuer country, so every country is
empty. The filtered-portfolio calculation reads only country from this data.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .derived_portfolio import (
    TAIWAN_COUNTRY_NAMES,
    TAIWAN_EXCHANGES,
    _instrument_exclusion_reason,
    portfolio_exclusion_reason,
)
from .reference_snapshot import inspect_reference_bytes
from .risk_free import SERIES_ID as USD_RISK_FREE_SERIES
from .risk_free import TREASURY_FEED_BASE_URL
from .sector_classification import YAHOO_PROVIDER


ENV_VAR = "REFERENCE_DATA_SOURCE"
BLOB = "blob"
DATABASE = "database"
SOURCES = (BLOB, DATABASE)
DATABASE_TABLES = ("instrument_classifications", "risk_free_rates")
# Per-conid fields compared between the two sources.
SECTOR_FIELDS = ("symbol", "sector", "industry", "exchange", "status", "country")
DEFAULT_RATE_TOLERANCE = 1e-6
DEFAULT_EXAMPLES = 50


def reference_data_source(environ: Mapping[str, str] | None = None) -> str:
    """'blob' (today's behaviour) unless REFERENCE_DATA_SOURCE says 'database'."""

    value = (os.environ if environ is None else environ).get(ENV_VAR, "")
    value = value.strip().lower()
    if not value:
        return BLOB
    if value not in SOURCES:
        raise ValueError(f"{ENV_VAR} must be one of: {', '.join(SOURCES)}")
    return value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def sector_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """One instrument_classifications row in the file's per-conid shape."""

    status = _text(row["status"])
    entry = {
        "conid": _text(row["conid"]),
        "symbol": _text(row["symbol"]),
        "lookup_symbol": _text(row["lookup_symbol"]),
        "sector": _text(row["sector"]),
        "industry": _text(row["industry"]),
        # v2 does not record issuer country, and checked none.
        "country": "",
        "country_checked": False,
        "exchange": _text(row["exchange"]),
        "status": status,
        "source": _text(row["provider"]),
        "source_url": _text(row["source_url"]),
        "checked_at": _text(row["checked_at"]),
    }
    if status != "classified" and row["error_code"]:
        entry["error"] = _text(row["error_code"])
    return entry


def risk_free_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """One risk_free_rates row in the file's risk_free.USD shape."""

    annual_rate = float(row["annual_rate"])
    return {
        "currency": _text(row["currency"]),
        "tenor": _text(row["tenor"]),
        "annual_rate": annual_rate,
        "annual_percent": round(annual_rate * 100.0, 8),
        "observation_date": _text(row["rate_date"]),
        "series_id": _text(row["series_id"]),
        "quality_status": _text(row["quality_status"]),
        "source": _text(row["provider"]),
        "source_url": TREASURY_FEED_BASE_URL,
        "fetched_at": _text(row["fetched_at"]),
    }


def usd_risk_free_row(connection, tenant_id: str, on_date: str | None = None):
    date_clause = "AND rate_date = ?" if on_date else ""
    parameters = (tenant_id, USD_RISK_FREE_SERIES) + ((on_date,) if on_date else ())
    return connection.execute(
        f"""
        SELECT rate_date, series_id, currency, tenor, annual_rate, provider,
               fetched_at, quality_status
        FROM risk_free_rates
        WHERE tenant_id = ? AND currency = 'USD' AND series_id = ?
          AND quality_status IN ('pass', 'warn') {date_clause}
        ORDER BY rate_date DESC LIMIT 1
        """,
        parameters,
    ).fetchone()


def build_reference_data_from_database(connection, tenant_id: str) -> dict[str, Any]:
    """Read v2's own tables into the shape of v1.1's reference_data.json."""

    classifications = connection.execute(
        """
        SELECT conid, provider, symbol, lookup_symbol, sector, industry,
               exchange, status, source_url, error_code, checked_at
        FROM instrument_classifications
        WHERE tenant_id = ? AND provider = ?
        ORDER BY conid
        """,
        (tenant_id, YAHOO_PROVIDER),
    ).fetchall()
    risk_free = usd_risk_free_row(connection, tenant_id)
    if not classifications and risk_free is None:
        raise RuntimeError("v2 reference tables hold no rows for this tenant")
    return {
        "version": 2,
        "built_from": "v2 database tables: " + ", ".join(DATABASE_TABLES),
        "sectors": {entry["conid"]: entry for entry in map(sector_entry, classifications)},
        "risk_free": {} if risk_free is None else {"USD": risk_free_entry(risk_free)},
    }


def reference_bytes(data: Mapping[str, Any]) -> bytes:
    """Deterministic bytes, formatted as v1.1 saves the file."""

    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    return (text + "\n").encode("utf-8")


def materialize_database_reference(connection, tenant_id: str, destination: Path) -> dict[str, Any]:
    """Write the database-built reference file and return its receipt.

    The receipt has the same keys as the blob download's, so the run watermark
    and the hash check in the calculation work unchanged.
    """

    data = build_reference_data_from_database(connection, tenant_id)
    content = reference_bytes(data)
    _, manifest = inspect_reference_bytes(content)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    usd = data["risk_free"].get("USD") or {}
    return {
        "source": DATABASE,
        "storage": {
            "tables": list(DATABASE_TABLES),
            "content_sha256": manifest["content_sha256"],
            "bytes": len(content),
            "latest_classification_checked_at": max(
                (entry["checked_at"] for entry in data["sectors"].values()), default=None
            ),
            "risk_free_observation_date": usd.get("observation_date"),
        },
        **manifest,
    }


# Comparison (read only)

def _examples(items: list, limit: int) -> dict[str, Any]:
    return {"count": len(items), "examples": items[:limit]}


def compare_sectors(file_sectors: Mapping[str, Any], database_sectors: Mapping[str, Any], *,
                    examples: int = DEFAULT_EXAMPLES) -> dict[str, Any]:
    file_keys, database_keys = set(file_sectors), set(database_sectors)
    common = sorted(file_keys & database_keys)
    differences: dict[str, list] = {field: [] for field in SECTOR_FIELDS}
    for conid in common:
        for field in SECTOR_FIELDS:
            in_file = _text(file_sectors[conid].get(field))
            in_database = _text(database_sectors[conid].get(field))
            if in_file != in_database:
                differences[field].append(
                    {"conid": conid, "file": in_file, "database": in_database}
                )
    countries = [
        {"conid": conid, "symbol": _text(entry.get("symbol")), "country": _text(entry.get("country"))}
        for conid, entry in sorted(file_sectors.items())
        if _text(entry.get("country"))
    ]
    return {
        "file_rows": len(file_keys),
        "database_rows": len(database_keys),
        "file_classified": sum(bool(_text(e.get("sector"))) for e in file_sectors.values()),
        "database_classified": sum(bool(_text(e.get("sector"))) for e in database_sectors.values()),
        "common_rows": len(common),
        "only_in_file": _examples(sorted(file_keys - database_keys), examples),
        "only_in_database": _examples(sorted(database_keys - file_keys), examples),
        "field_differences": {
            field: _examples(rows, examples) for field, rows in differences.items() if rows
        },
        # The database source has no country, so each of these is lost on the switch.
        "file_countries": _examples(countries, examples),
    }


def compare_risk_free(file_risk_free: Mapping[str, Any], database_risk_free: Mapping[str, Any], *,
                      database_rate_on_file_date: float | None,
                      rate_tolerance: float = DEFAULT_RATE_TOLERANCE) -> dict[str, Any]:
    file_usd = dict(file_risk_free.get("USD") or {})
    database_usd = dict(database_risk_free.get("USD") or {})
    file_date = _text(file_usd.get("observation_date"))
    database_date = _text(database_usd.get("observation_date"))
    differences: list[str] = []
    other = sorted(set(file_risk_free) - {"USD"})
    if other:
        differences.append("file has risk-free currencies the database source omits: " + ", ".join(other))
    if not file_usd:
        differences.append("file has no USD risk-free rate")
    if not database_usd:
        differences.append("database has no USD risk-free rate")
    if file_usd and database_usd and file_date != database_date:
        differences.append(f"latest observation dates differ: file {file_date}, database {database_date}")
    file_rate = file_usd.get("annual_rate")
    gap = None
    if file_usd:
        if file_rate is None:
            differences.append("file's USD risk-free rate has no annual_rate")
        elif database_rate_on_file_date is None:
            differences.append(f"database has no USD rate on the file's date {file_date}")
        else:
            gap = abs(float(file_rate) - float(database_rate_on_file_date))
            if gap > rate_tolerance:
                differences.append(
                    f"USD rate on {file_date} differs by {gap:.10f} (tolerance {rate_tolerance})"
                )
    return {
        "file": {"observation_date": file_date or None, "annual_rate": file_rate},
        "database_latest": {
            "observation_date": database_date or None,
            "annual_rate": database_usd.get("annual_rate"),
        },
        "database_rate_on_file_date": database_rate_on_file_date,
        "rate_difference_on_file_date": gap,
        "rate_tolerance": rate_tolerance,
        "differences": differences,
    }


def filtered_portfolio_inputs(connection, definition: Mapping[str, Any]) -> dict[str, Any]:
    """The rows whose exclusion depends on reference country, for one filtered portfolio."""

    account = str(definition["source_account_id"])
    start = str(definition["history_start_date"])
    positions = [
        dict(row)
        for row in connection.execute(
            """
            SELECT DISTINCT p.conid, i.listing_exchange, p.currency,
                   i.currency AS instrument_currency
            FROM position_snapshots AS p
            LEFT JOIN instruments AS i ON i.conid = p.conid
            WHERE p.account_id = ? AND p.snapshot_date >= ?
            ORDER BY p.conid
            """,
            (account, start),
        )
    ]
    activity_conids = sorted(
        {
            str(row["conid"])
            for row in connection.execute(
                """
                SELECT conid FROM cash_transactions
                WHERE account_id = ? AND effective_date >= ? AND COALESCE(conid, '') <> ''
                UNION
                SELECT conid FROM trades
                WHERE account_id = ? AND trade_date >= ? AND COALESCE(conid, '') <> ''
                """,
                (account, start, account, start),
            )
        }
    )
    wanted = set(activity_conids)
    instruments = {
        str(row["conid"]): dict(row)
        for row in connection.execute(
            "SELECT conid, symbol, currency, listing_exchange FROM instruments"
        )
        if str(row["conid"]) in wanted
    }
    return {"positions": positions, "activity_conids": activity_conids, "instruments": instruments}


def exclusion_impact(file_data: Mapping[str, Any], database_data: Mapping[str, Any], *,
                     definition: Mapping[str, Any], positions: Iterable[Mapping[str, Any]],
                     activity_conids: Iterable[str], instruments: Mapping[str, Any],
                     examples: int = DEFAULT_EXAMPLES) -> dict[str, Any]:
    """Which holdings or cash/trade rows the switch would include or exclude differently."""

    positions = list(positions)
    activity_conids = list(activity_conids)
    policy = dict(definition.get("position_filter") or {})
    # The same defaults refresh_filtered_portfolio applies.
    rules = {
        "excluded_countries": policy.get("excluded_countries") or TAIWAN_COUNTRY_NAMES,
        "excluded_exchanges": policy.get("excluded_exchanges") or TAIWAN_EXCHANGES,
        "excluded_currencies": policy.get("excluded_currencies") or ("TWD",),
    }
    status_changes: dict[str, dict] = {}
    reason_changes: dict[str, dict] = {}

    def record(conid: str, kind: str, in_file: str | None, in_database: str | None) -> None:
        if (in_file is None) != (in_database is None):
            target = status_changes
        elif in_file != in_database:
            target = reason_changes
        else:
            return
        target.setdefault(f"{kind}:{conid}", {
            "conid": conid, "rows": kind,
            "file_reason": in_file, "database_reason": in_database,
        })

    for row in positions:
        record(
            str(row["conid"]), "positions",
            portfolio_exclusion_reason(row, file_data, **rules),
            portfolio_exclusion_reason(row, database_data, **rules),
        )
    for conid in activity_conids:
        record(
            conid, "cash_and_trades",
            _instrument_exclusion_reason(conid, instruments, file_data, **rules),
            _instrument_exclusion_reason(conid, instruments, database_data, **rules),
        )
    return {
        "subject_id": str(definition.get("subject_id") or ""),
        "position_conids_checked": len({str(row["conid"]) for row in positions}),
        "activity_conids_checked": len(activity_conids),
        # A change here moves a holding or cash row in or out of the NAV.
        "excluded_status_changes": _examples(list(status_changes.values()), examples),
        # Same inclusion, different stored reason text.
        "reason_text_changes": _examples(list(reason_changes.values()), examples),
    }
