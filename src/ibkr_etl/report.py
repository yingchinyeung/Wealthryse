from __future__ import annotations

import bisect
import html
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .reference_data import load_reference_data
from .fx_selection import latest_fx_quote
from .db import connect as database_connect, is_postgres


HKT = timezone(timedelta(hours=8), name="Asia/Hong_Kong")
REPORT_TITLE = "IBKR Portfolio Daily Analytics"


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _sum(values: Iterable[float | None]) -> float:
    return sum(value for value in values if value is not None)


def _account_label(row: sqlite3.Row) -> str:
    alias = (row["alias"] or "").strip()
    return f"{alias} ({row['account_id']})" if alias else row["account_id"]


class FxConverter:
    def __init__(self, rows: Iterable[sqlite3.Row], bases: dict[str, str]) -> None:
        self.bases = bases
        self.series: dict[tuple[str, str, str], list[tuple[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (row["account_id"], row["from_currency"], row["to_currency"])
            self.series[key].append((row["rate_date"], row["rate_text"]))
        for values in self.series.values():
            values.sort(key=lambda item: item[0])

    def rate(self, account_id: str, currency: str | None, on_date: str | None) -> float | None:
        base = self.bases.get(account_id)
        if not currency or not base:
            return None
        if currency == base:
            return 1.0
        values = self.series.get((account_id, currency, base), [])
        if not values:
            return None
        quote = latest_fx_quote(values, on_date)
        return quote[0] if quote else None


def _convert(value: Any, rate: float | None) -> float | None:
    number = _number(value)
    return None if number is None or rate is None else number * rate


def _flex_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    if len(text) >= 10 and text[4:5] == "-":
        return text[:10]
    return None


def _latest_run(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT run_id, run_type, requested_from, requested_to, completed_at, status,
               reconciliation_json, row_count
        FROM ingestion_runs
        ORDER BY run_id DESC LIMIT 1
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("The database has no ingestion history.")
    reconciliation = json.loads(row["reconciliation_json"] or "{}")
    return {
        "run_id": row["run_id"],
        "run_type": row["run_type"],
        "requested_from": row["requested_from"],
        "requested_to": row["requested_to"],
        "completed_at": row["completed_at"],
        "status": row["status"],
        "reconciliation": reconciliation.get("status", "UNKNOWN"),
        "row_count": row["row_count"],
    }


def _load_data(
    connection: sqlite3.Connection, reference_data: dict[str, Any]
) -> dict[str, Any]:
    account_rows = connection.execute(
        "SELECT account_id, alias, base_currency FROM accounts ORDER BY account_id"
    ).fetchall()
    if not account_rows:
        raise RuntimeError("The database contains no accounts.")

    accounts = {
        row["account_id"]: {
            "account_id": row["account_id"],
            "label": _account_label(row),
            "base_currency": row["base_currency"] or "BASE",
        }
        for row in account_rows
    }
    bases = {key: value["base_currency"] for key, value in accounts.items()}
    converter = FxConverter(
        connection.execute(
            """
            SELECT account_id, rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            ORDER BY account_id, from_currency, to_currency, rate_date
            """
        ),
        bases,
    )

    nav_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT account_id, snapshot_date, currency, total_nav, cash, stock, options,
               futures, bonds, accruals
        FROM v_daily_nav
        ORDER BY account_id, snapshot_date
        """
    ):
        nav_by_account[row["account_id"]].append(
            {
                "date": row["snapshot_date"],
                "currency": row["currency"],
                "total": _number(row["total_nav"]),
                "cash": _number(row["cash"]),
                "stock": _number(row["stock"]),
                "options": _number(row["options"]),
                "futures": _number(row["futures"]),
                "bonds": _number(row["bonds"]),
                "accruals": _number(row["accruals"]),
            }
        )

    positions_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    missing_fx: set[str] = set()
    for row in connection.execute(
        """
        SELECT p.account_id, p.snapshot_date, p.conid, p.currency, p.quantity_text,
               p.mark_price_text, p.market_value_text, p.cost_basis_text,
               p.unrealized_pnl_text, i.symbol, i.description, i.asset_category
        FROM v_latest_positions AS p
        LEFT JOIN instruments AS i ON i.conid = p.conid
        ORDER BY p.account_id, ABS(CAST(p.market_value_text AS REAL)) DESC
        """
    ):
        rate = converter.rate(row["account_id"], row["currency"], row["snapshot_date"])
        if rate is None:
            missing_fx.add(f"{row['account_id']}:{row['currency'] or 'UNKNOWN'}")
        sector_entry = reference_data.get("sectors", {}).get(row["conid"], {})
        positions_by_account[row["account_id"]].append(
            {
                "account": row["account_id"],
                "date": row["snapshot_date"],
                "conid": row["conid"],
                "symbol": row["symbol"] or row["description"] or row["conid"],
                "description": row["description"] or "",
                "asset": row["asset_category"] or "UNKNOWN",
                "sector": sector_entry.get("sector") or "Unclassified",
                "industry": sector_entry.get("industry") or "",
                "currency": row["currency"] or "",
                "quantity": _number(row["quantity_text"]),
                "price": _number(row["mark_price_text"]),
                "market_value": _number(row["market_value_text"]),
                "market_value_base": _convert(row["market_value_text"], rate),
                "cost_basis_base": _convert(row["cost_basis_text"], rate),
                "unrealized_pnl_base": _convert(row["unrealized_pnl_text"], rate),
                "fx_rate": rate,
            }
        )

    flows_by_account: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in connection.execute(
        "SELECT account_id, data_json FROM flex_rows WHERE section = 'Transfers'"
    ):
        values = json.loads(row["data_json"])
        flow_date = _flex_date(values.get("date") or values.get("reportDate"))
        if not flow_date:
            continue
        asset_category = str(values.get("assetCategory") or "").upper()
        if asset_category == "CASH":
            currency = values.get("currency")
            rate = converter.rate(row["account_id"], currency, flow_date)
            amount_base = _convert(values.get("cashTransfer"), rate)
        else:
            amount_base = _number(values.get("positionAmountInBase"))
            if amount_base is None:
                currency = values.get("currency")
                rate = converter.rate(row["account_id"], currency, flow_date)
                amount_base = _convert(values.get("positionAmount"), rate)
        if amount_base is not None:
            flows_by_account[row["account_id"]][flow_date] += amount_base

    twr_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        "SELECT account_id, data_json FROM flex_rows WHERE section = 'ChangeInNAV'"
    ):
        values = json.loads(row["data_json"])
        from_date = _flex_date(values.get("fromDate"))
        to_date = _flex_date(values.get("toDate"))
        twr_percent = _number(values.get("twr"))
        if from_date and to_date and twr_percent is not None and twr_percent > -100:
            twr_by_account[row["account_id"]].append(
                {"from": from_date, "to": to_date, "return": twr_percent / 100.0}
            )

    latest_nav_date = max(
        (item["date"] for values in nav_by_account.values() for item in values),
        default=date.today().isoformat(),
    )
    activity_start = (date.fromisoformat(latest_nav_date) - timedelta(days=29)).isoformat()

    trades_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT t.account_id, t.conid, t.trade_date, t.trade_time, t.buy_sell, t.quantity_text,
               t.trade_price_text, t.proceeds_text, t.commission_text,
               t.commission_currency, t.realized_pnl_text, t.currency,
               i.symbol, i.description, i.asset_category
        FROM trades AS t
        LEFT JOIN instruments AS i ON i.conid = t.conid
        WHERE t.trade_date >= ?
        ORDER BY t.trade_date DESC, t.trade_time DESC
        """,
        (activity_start,),
    ):
        proceeds_rate = converter.rate(row["account_id"], row["currency"], row["trade_date"])
        commission_currency = row["commission_currency"] or row["currency"]
        commission_rate = converter.rate(
            row["account_id"], commission_currency, row["trade_date"]
        )
        trades_by_account[row["account_id"]].append(
            {
                "account": row["account_id"],
                "conid": row["conid"] or "",
                "date": row["trade_date"],
                "time": row["trade_time"] or "",
                "symbol": row["symbol"] or row["description"] or "Unknown",
                "asset": row["asset_category"] or "UNKNOWN",
                "side": row["buy_sell"] or "",
                "quantity": _number(row["quantity_text"]),
                "price": _number(row["trade_price_text"]),
                "proceeds": _number(row["proceeds_text"]),
                "currency": row["currency"] or "",
                "commission": _number(row["commission_text"]),
                "commission_currency": commission_currency or "",
                "realized_pnl": _number(row["realized_pnl_text"]),
                "proceeds_base": _convert(row["proceeds_text"], proceeds_rate),
                "commission_base": _convert(row["commission_text"], commission_rate),
                "realized_pnl_base": _convert(row["realized_pnl_text"], proceeds_rate),
            }
        )

    cash_by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT account_id, conid, effective_date, event_datetime, transaction_type,
               description, amount_text, currency
        FROM cash_transactions
        WHERE effective_date >= ?
        ORDER BY effective_date DESC, event_datetime DESC
        """,
        (activity_start,),
    ):
        rate = converter.rate(row["account_id"], row["currency"], row["effective_date"])
        cash_by_account[row["account_id"]].append(
            {
                "account": row["account_id"],
                "conid": row["conid"] or "",
                "date": row["effective_date"],
                "datetime": row["event_datetime"] or "",
                "type": row["transaction_type"] or "",
                "description": row["description"] or "",
                "amount": _number(row["amount_text"]),
                "currency": row["currency"] or "",
                "amount_base": _convert(row["amount_text"], rate),
            }
        )

    derived_metadata: dict[str, dict[str, Any]] = {}
    for portfolio in connection.execute(
        """
        SELECT portfolio_id, name, source_account_id, base_currency,
               exclusion_rule_json, calculation_version, updated_at
        FROM derived_portfolios
        WHERE status = 'active'
        ORDER BY name
        """
    ):
        portfolio_id = portfolio["portfolio_id"]
        source_account_id = portfolio["source_account_id"]
        definition = json.loads(portfolio["exclusion_rule_json"] or "{}")
        portfolio_type = definition.get("type", "exclude_company_country")
        if definition.get('calculation_kind') == 'fixed_unit_normalized_composite':
            portfolio_type = 'fixed_unit_composite'
        accounts[portfolio_id] = {
            "account_id": portfolio_id,
            "label": f"{portfolio['name']} (derived)",
            "base_currency": portfolio["base_currency"] or "BASE",
            "is_derived": True,
            "source_account_id": source_account_id,
        }
        nav_rows = connection.execute(
            """
            SELECT snapshot_date, currency, total_nav, cash, stock, options,
                   futures, bonds, accruals, source_total_nav,
                   excluded_market_value_base, source_position_count,
                   included_position_count, excluded_position_count
            FROM derived_portfolio_nav
            WHERE portfolio_id = ? AND currency = ?
            ORDER BY snapshot_date, currency
            """,
            (portfolio_id,portfolio['base_currency']),
        ).fetchall()
        for row in nav_rows:
            nav_by_account[portfolio_id].append(
                {
                    "date": row["snapshot_date"],
                    "currency": row["currency"],
                    "total": _number(row["total_nav"]),
                    "cash": _number(row["cash"]),
                    "stock": _number(row["stock"]),
                    "options": _number(row["options"]),
                    "futures": _number(row["futures"]),
                    "bonds": _number(row["bonds"]),
                    "accruals": _number(row["accruals"]),
                }
            )

        for row in connection.execute(
            """
            SELECT p.source_account_id, p.snapshot_date, p.conid, p.currency,
                   p.quantity_text, p.mark_price_text, p.market_value_text,
                   p.cost_basis_text, p.unrealized_pnl_text,
                   i.symbol, i.description, i.asset_category
            FROM derived_portfolio_positions AS p
            LEFT JOIN instruments AS i ON i.conid = p.conid
            JOIN (
                SELECT portfolio_id, MAX(snapshot_date) AS snapshot_date
                FROM derived_portfolio_positions
                WHERE portfolio_id = ?
                GROUP BY portfolio_id
            ) AS latest
              ON latest.portfolio_id = p.portfolio_id
             AND latest.snapshot_date = p.snapshot_date
            WHERE p.portfolio_id = ?
            ORDER BY ABS(CAST(p.market_value_text AS REAL)) DESC
            """,
            (portfolio_id, portfolio_id),
        ):
            rate = converter.rate(
                row["source_account_id"], row["currency"], row["snapshot_date"]
            )
            if rate is None:
                missing_fx.add(f"{portfolio_id}:{row['currency'] or 'UNKNOWN'}")
            sector_entry = reference_data.get("sectors", {}).get(row["conid"], {})
            positions_by_account[portfolio_id].append(
                {
                    "account": portfolio_id,
                    "date": row["snapshot_date"],
                    "conid": row["conid"],
                    "symbol": row["symbol"] or row["description"] or row["conid"],
                    "description": row["description"] or "",
                    "asset": row["asset_category"] or "UNKNOWN",
                    "sector": sector_entry.get("sector") or "Unclassified",
                    "industry": sector_entry.get("industry") or "",
                    "currency": row["currency"] or "",
                    "quantity": _number(row["quantity_text"]),
                    "price": _number(row["mark_price_text"]),
                    "market_value": _number(row["market_value_text"]),
                    "market_value_base": _convert(row["market_value_text"], rate),
                    "cost_basis_base": _convert(row["cost_basis_text"], rate),
                    "unrealized_pnl_base": _convert(row["unrealized_pnl_text"], rate),
                    "fx_rate": rate,
                }
            )

        if portfolio_type == "fixed_unit_composite":
            for row in connection.execute(
                """
                SELECT snapshot_date, component_id, component_name,
                       component_type, direction, coefficient, base_currency,
                       normalized_value, contribution, observation_date
                FROM synthetic_portfolio_components
                WHERE portfolio_id = ?
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date)
                      FROM synthetic_portfolio_components
                      WHERE portfolio_id = ?
                  )
                  AND component_type <> 'cash'
                ORDER BY ABS(contribution) DESC
                """,
                (portfolio_id, portfolio_id),
            ):
                positions_by_account[portfolio_id].append(
                    {
                        "account": portfolio_id,
                        "date": row["snapshot_date"],
                        "conid": row["component_id"],
                        "symbol": row["component_name"],
                        "description": (
                            f"{row['direction'].title()} fixed-unit sleeve; "
                            f"source observation {row['observation_date']}"
                        ),
                        "asset": str(row["component_type"] or "sleeve").upper(),
                        "sector": "Strategy sleeve",
                        "industry": "",
                        "currency": row["base_currency"],
                        "quantity": None,
                        "price": None,
                        "holding_kind": "synthetic_exposure",
                        "coefficient": _number(row["coefficient"]),
                        "normalized_source_value": _number(row["normalized_value"]),
                        "market_value": _number(row["contribution"]),
                        "market_value_base": _number(row["contribution"]),
                        "cost_basis_base": None,
                        "unrealized_pnl_base": None,
                        "fx_rate": 1.0,
                    }
                )

        excluded_conids = {
            row["conid"]
            for row in connection.execute(
                """
                SELECT DISTINCT conid
                FROM derived_portfolio_exclusions
                WHERE portfolio_id = ?
                """,
                (portfolio_id,),
            )
        }
        excluded_conids.update(
            row["conid"]
            for row in connection.execute(
                """
                SELECT DISTINCT conid
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = ? AND included = 0 AND conid IS NOT NULL
                """,
                (portfolio_id,),
            )
        )
        if portfolio_type == "fixed_unit_composite":
            trades_by_account[portfolio_id] = []
            cash_by_account[portfolio_id] = []
            flows_by_account[portfolio_id] = defaultdict(float)
        else:
            trades_by_account[portfolio_id] = [
                {**row, "account": portfolio_id}
                for row in trades_by_account.get(source_account_id, [])
                if (not row.get("conid") or row["conid"] not in excluded_conids)
                and str(row.get("currency") or "").upper() in {"HKD", "USD"}
            ]
            cash_by_account[portfolio_id] = []
            for row in connection.execute(
                """
                SELECT conid, event_date, classification, description, amount,
                       currency, amount_base
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = ? AND included = 1
                  AND source_type = 'cash_transaction' AND event_date >= ?
                ORDER BY event_date DESC, event_key DESC
                """,
                (portfolio_id, activity_start),
            ):
                cash_by_account[portfolio_id].append(
                    {
                        "account": portfolio_id,
                        "conid": row["conid"] or "",
                        "date": row["event_date"],
                        "datetime": row["event_date"],
                        "type": row["classification"],
                        "description": row["description"] or "",
                        "amount": _number(row["amount"]),
                        "currency": row["currency"] or "",
                        "amount_base": _number(row["amount_base"]),
                    }
                )
            flows_by_account[portfolio_id] = defaultdict(float)
            for row in connection.execute(
                """
                SELECT event_date, SUM(amount_base) AS amount_base
                FROM derived_portfolio_cash_ledger
                WHERE portfolio_id = ? AND included = 1 AND external_flow = 1
                GROUP BY event_date
                ORDER BY event_date
                """,
                (portfolio_id,),
            ):
                amount = _number(row["amount_base"])
                if amount is not None:
                    flows_by_account[portfolio_id][row["event_date"]] += amount
        twr_by_account[portfolio_id] = []
        latest_derived = nav_rows[-1] if nav_rows else None
        derived_metadata[portfolio_id] = {
            "source_account_id": source_account_id,
            "calculation_version": portfolio["calculation_version"],
            "updated_at": portfolio["updated_at"],
            "portfolio_type": portfolio_type,
            "exclusion_rule": definition,
            "latest_excluded_market_value_base": (
                _number(latest_derived["excluded_market_value_base"])
                if latest_derived
                else None
            ),
            "latest_excluded_position_count": (
                latest_derived["excluded_position_count"] if latest_derived else 0
            ),
            "latest_cash_balances": {
                row["currency"]: _number(row["closing_balance"])
                for row in connection.execute(
                    """
                    SELECT currency, closing_balance
                    FROM derived_portfolio_cash_balances
                    WHERE portfolio_id = ?
                      AND snapshot_date = (
                          SELECT MAX(snapshot_date)
                          FROM derived_portfolio_cash_balances
                          WHERE portfolio_id = ?
                      )
                    ORDER BY currency
                    """,
                    (portfolio_id, portfolio_id),
                )
            },
        }

    return {
        "accounts": accounts,
        "nav": nav_by_account,
        "positions": positions_by_account,
        "trades": trades_by_account,
        "cash": cash_by_account,
        "flows": flows_by_account,
        "twr": twr_by_account,
        "risk_free": reference_data.get("risk_free", {}),
        "missing_fx": sorted(missing_fx),
        "latest_nav_date": latest_nav_date,
        "activity_start": activity_start,
        "derived_metadata": derived_metadata,
    }


def _aggregate_nav(series_by_account: dict[str, list[dict[str, Any]]], ids: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    fields = ("total", "cash", "stock", "options", "futures", "bonds", "accruals")
    for account_id in ids:
        for row in series_by_account.get(account_id, []):
            point = grouped.setdefault(
                row["date"], {"date": row["date"], "currency": row["currency"]}
            )
            for field in fields:
                value = row[field]
                if value is not None:
                    point[field] = point.get(field, 0.0) + value
    return [grouped[key] for key in sorted(grouped)]


def _aggregate_positions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["conid"], row["currency"], row["asset"])
        item = grouped.setdefault(
            key,
            {
                **row,
                "accounts": set(),
                "quantity": 0.0,
                "market_value_base": 0.0,
                "cost_basis_base": 0.0,
                "unrealized_pnl_base": 0.0,
            },
        )
        item["accounts"].add(row["account"])
        for field in ("quantity", "market_value_base", "cost_basis_base", "unrealized_pnl_base"):
            if row[field] is not None:
                item[field] += row[field]
    result = []
    for item in grouped.values():
        item["accounts"] = len(item["accounts"])
        result.append(item)
    return result


def _aggregate_flows(
    flows_by_account: dict[str, dict[str, float]], ids: list[str]
) -> dict[str, float]:
    combined: dict[str, float] = defaultdict(float)
    for account_id in ids:
        for flow_date, amount in flows_by_account.get(account_id, {}).items():
            combined[flow_date] += amount
    return dict(combined)


def _most_recent_continuous_nav(
    nav: list[dict[str, Any]], maximum_gap_days: int = 5
) -> list[dict[str, Any]]:
    """Return the latest daily-like segment, excluding historical month-end gaps."""
    ordered = sorted(nav, key=lambda row: row["date"])
    if not ordered:
        return []
    start = len(ordered) - 1
    while start > 0:
        current = date.fromisoformat(ordered[start]["date"])
        previous = date.fromisoformat(ordered[start - 1]["date"])
        if (current - previous).days > maximum_gap_days:
            break
        start -= 1
    return ordered[start:]


def _daily_modified_dietz_returns(
    nav: list[dict[str, Any]],
    flows: dict[str, float],
    maximum_absolute_return: float | None = 1.0,
) -> list[dict[str, Any]]:
    points = [row for row in nav if row.get("total") is not None]
    results: list[dict[str, Any]] = []
    for previous, current in zip(points, points[1:]):
        start_value = previous["total"]
        end_value = current["total"]
        if start_value is None or end_value is None or start_value <= 0:
            continue
        flow = sum(
            amount
            for flow_date, amount in flows.items()
            if previous["date"] < flow_date <= current["date"]
        )
        denominator = start_value + 0.5 * flow
        if denominator <= 0:
            continue
        daily_return = (end_value - start_value - flow) / denominator
        within_limit = (
            maximum_absolute_return is None
            or abs(daily_return) <= maximum_absolute_return
        )
        if math.isfinite(daily_return) and daily_return > -1 and within_limit:
            results.append({"date": current["date"], "return": daily_return, "flow": flow})
    return results


def _official_twr(intervals: list[dict[str, Any]]) -> dict[str, Any] | None:
    valid = sorted(
        (
            interval
            for interval in intervals
            if interval.get("from") and interval.get("to") and interval.get("return") is not None
        ),
        key=lambda interval: (interval["from"], interval["to"]),
    )
    if not valid:
        return None
    selected: list[dict[str, Any]] = []
    cursor: str | None = None
    remaining = valid[:]
    while remaining:
        if cursor is None:
            next_start = min(interval["from"] for interval in remaining)
        else:
            eligible_starts = [interval["from"] for interval in remaining if interval["from"] > cursor]
            if not eligible_starts:
                break
            next_start = min(eligible_starts)
        candidates = [interval for interval in remaining if interval["from"] == next_start]
        chosen = max(candidates, key=lambda interval: interval["to"])
        selected.append(chosen)
        cursor = chosen["to"]
        remaining = [interval for interval in remaining if interval["from"] > cursor]
    growth = math.prod(1.0 + interval["return"] for interval in selected)
    if growth <= 0:
        return None
    first_date = date.fromisoformat(selected[0]["from"])
    last_date = date.fromisoformat(selected[-1]["to"])
    calendar_days = max(1, (last_date - first_date).days)
    return {
        "total_return": growth - 1.0,
        "annualized_return": growth ** (365.25 / calendar_days) - 1.0,
        "from": selected[0]["from"],
        "to": selected[-1]["to"],
        "intervals": len(selected),
    }


def _quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _performance_metrics(
    nav: list[dict[str, Any]],
    flows: dict[str, float],
    risk_free: dict[str, Any] | None,
    official_twr: dict[str, Any] | None,
) -> dict[str, Any]:
    series = _daily_modified_dietz_returns(nav, flows)
    unfiltered_series = _daily_modified_dietz_returns(
        nav, flows, maximum_absolute_return=None
    )
    excluded_observations = len(unfiltered_series) - len(series)
    returns = [row["return"] for row in series]
    mean = statistics.fmean(returns) if returns else None
    volatility_daily = statistics.stdev(returns) if len(returns) >= 2 else None
    annual_rate = _number((risk_free or {}).get("annual_rate"))
    risk_free_daily = (
        (1.0 + annual_rate) ** (1.0 / 252.0) - 1.0
        if annual_rate is not None and annual_rate > -1
        else None
    )
    sharpe = (
        (mean - risk_free_daily) / volatility_daily * math.sqrt(252)
        if mean is not None
        and risk_free_daily is not None
        and volatility_daily not in (None, 0)
        else None
    )
    growth = 1.0
    peak = 1.0
    max_drawdown = 0.0
    for daily_return in returns:
        growth *= 1.0 + daily_return
        peak = max(peak, growth)
        max_drawdown = min(max_drawdown, growth / peak - 1.0)
    daily_total = growth - 1.0 if returns else None
    if official_twr:
        total_return = official_twr["total_return"]
        annualized_return = official_twr["annualized_return"]
        total_method = "IBKR time-weighted return"
        return_from = official_twr["from"]
        return_to = official_twr["to"]
    else:
        total_return = daily_total
        return_from = nav[0]["date"] if nav else None
        return_to = nav[-1]["date"] if nav else None
        if daily_total is not None and return_from and return_to and daily_total > -1:
            calendar_days = max(
                1, (date.fromisoformat(return_to) - date.fromisoformat(return_from)).days
            )
            annualized_return = (1.0 + daily_total) ** (365.25 / calendar_days) - 1.0
        else:
            annualized_return = None
        total_method = "consolidated daily Modified Dietz"
    fifth_percentile = _quantile(returns, 0.05)
    tail = (
        [value for value in returns if fifth_percentile is not None and value <= fifth_percentile]
        if returns
        else []
    )
    return {
        "total_return_pct": total_return * 100 if total_return is not None else None,
        "annualized_return_pct": annualized_return * 100 if annualized_return is not None else None,
        "annualized_volatility_pct": volatility_daily * math.sqrt(252) * 100 if volatility_daily is not None else None,
        "risk_free_rate_pct": annual_rate * 100 if annual_rate is not None else None,
        "risk_free_date": (risk_free or {}).get("observation_date"),
        "risk_free_source": (risk_free or {}).get("source"),
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_drawdown * 100 if returns else None,
        "var_95_daily_pct": max(0.0, -fifth_percentile * 100) if fifth_percentile is not None else None,
        "cvar_95_daily_pct": max(0.0, -statistics.fmean(tail) * 100) if tail else None,
        "win_rate_pct": 100.0 * sum(value > 0 for value in returns) / len(returns) if returns else None,
        "blue_mean_daily_pct": mean * 100 if mean is not None else None,
        "blue_standard_error_pct": volatility_daily / math.sqrt(len(returns)) * 100 if volatility_daily is not None and returns else None,
        "observations": len(returns),
        "excluded_observations": excluded_observations,
        "return_from": return_from,
        "return_to": return_to,
        "total_return_method": total_method,
        "risk_method": (
            "Daily Modified Dietz using Flex transfer records with a mid-day flow "
            "assumption; daily moves above 100% in absolute value are excluded as "
            "capital-flow discontinuities"
        ),
    }


def _build_view(
    key: str,
    label: str,
    base_currency: str,
    ids: list[str],
    data: dict[str, Any],
) -> dict[str, Any]:
    nav = _aggregate_nav(data["nav"], ids)
    positions = _aggregate_positions(
        [row for account_id in ids for row in data["positions"].get(account_id, [])]
    )
    positions.sort(key=lambda row: abs(row["market_value_base"] or 0), reverse=True)
    trades = [row for account_id in ids for row in data["trades"].get(account_id, [])]
    trades.sort(key=lambda row: (row["date"], row["time"]), reverse=True)
    cash = [row for account_id in ids for row in data["cash"].get(account_id, [])]
    cash.sort(key=lambda row: (row["date"], row["datetime"]), reverse=True)
    flows = _aggregate_flows(data["flows"], ids)
    base_risk_free = data["risk_free"].get(base_currency)
    account_twr = _official_twr(data["twr"].get(ids[0], [])) if len(ids) == 1 else None
    performance = _performance_metrics(nav, flows, base_risk_free, account_twr)

    latest = nav[-1] if nav else {}
    previous = nav[-2] if len(nav) > 1 else {}
    latest_total = latest.get("total")
    previous_total = previous.get("total")
    nav_change = (
        latest_total - previous_total
        if latest_total is not None and previous_total not in (None, 0)
        else None
    )
    nav_change_pct = (
        100 * nav_change / previous_total
        if nav_change is not None and previous_total not in (None, 0)
        else None
    )

    gross = _sum(abs(row["market_value_base"]) for row in positions if row["market_value_base"] is not None)
    net = _sum(row["market_value_base"] for row in positions)
    unrealized = _sum(row["unrealized_pnl_base"] for row in positions)
    top_five = _sum(abs(row["market_value_base"]) for row in positions[:5])
    allocation: dict[str, float] = defaultdict(float)
    currency_allocation: dict[str, float] = defaultdict(float)
    sector_allocation: dict[str, float] = defaultdict(float)
    for row in positions:
        value = abs(row["market_value_base"] or 0)
        allocation[row["asset"]] += value
        currency_allocation[row["currency"] or "UNKNOWN"] += value
        sector_allocation[row["sector"] or "Unclassified"] += value

    turnover = _sum(abs(row["proceeds_base"]) for row in trades if row["proceeds_base"] is not None)
    commission = -_sum(row["commission_base"] for row in trades)
    realized = _sum(row["realized_pnl_base"] for row in trades)
    net_cash_activity = _sum(row["amount_base"] for row in cash)

    warnings: list[str] = []
    missing_for_view = [value for value in data["missing_fx"] if value.split(":", 1)[0] in ids]
    if missing_for_view:
        warnings.append(
            "Some position values were excluded because no Flex FX rate was available: "
            + ", ".join(missing_for_view)
        )
    if not positions:
        warnings.append("No open positions were reported for this selection; it may be cash-only.")
    if performance["excluded_observations"]:
        warnings.append(
            f"{performance['excluded_observations']} daily return observation(s) above "
            "100% in absolute value were excluded as likely capital-flow discontinuities."
        )

    return {
        "key": key,
        "label": label,
        "base_currency": base_currency,
        "account_count": len(ids),
        "latest_nav_date": latest.get("date"),
        "previous_nav_date": previous.get("date"),
        "position_date": max((row["date"] for row in positions), default=None),
        "nav": nav,
        "metrics": {
            "nav": latest_total,
            "nav_change": nav_change,
            "nav_change_pct": nav_change_pct,
            "cash": latest.get("cash"),
            "gross_exposure": gross,
            "net_exposure": net,
            "unrealized_pnl": unrealized,
            "position_count": len(positions),
            "top_five_pct": (100 * top_five / gross) if gross else None,
            "turnover_30d": turnover,
            "commission_30d": commission,
            "realized_pnl_30d": realized,
            "net_cash_activity_30d": net_cash_activity,
            "trade_count_30d": len(trades),
        },
        "breakdown": {
            field: latest.get(field)
            for field in ("cash", "stock", "options", "futures", "bonds", "accruals")
        },
        "performance": performance,
        "allocation": [
            {"label": asset, "value": value}
            for asset, value in sorted(allocation.items(), key=lambda item: item[1], reverse=True)
        ],
        "currency_allocation": [
            {"label": currency, "value": value}
            for currency, value in sorted(
                currency_allocation.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "sector_allocation": [
            {"label": sector, "value": value}
            for sector, value in sorted(
                sector_allocation.items(), key=lambda item: item[1], reverse=True
            )
        ],
        "sector_coverage_pct": (
            max(
                0.0,
                min(
                    100.0,
                    100.0
                    * sum(
                        value
                        for sector, value in sector_allocation.items()
                        if sector != "Unclassified"
                    )
                    / gross,
                ),
            )
            if gross
            else None
        ),
        "positions": positions[:25],
        "trades": trades[:20],
        "cash_activity": cash[:15],
        "warnings": warnings,
    }


def _report_payload(
    connection: sqlite3.Connection,
    generated_at: datetime,
    reference_data: dict[str, Any],
) -> dict[str, Any]:
    data = _load_data(connection, reference_data)
    latest_run = _latest_run(connection)
    ids = [
        account_id
        for account_id, account in data["accounts"].items()
        if not account.get("is_derived")
    ]
    bases = {data["accounts"][account_id]["base_currency"] for account_id in ids}
    views: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    if len(bases) == 1:
        base = next(iter(bases))
        views["__all__"] = _build_view("__all__", "All accounts", base, ids, data)
        order.append("__all__")
    for account_id in ids:
        account = data["accounts"][account_id]
        views[account_id] = _build_view(
            account_id,
            account["label"],
            account["base_currency"],
            [account_id],
            data,
        )
        order.append(account_id)
    for portfolio_id, metadata in data["derived_metadata"].items():
        account = data["accounts"][portfolio_id]
        view = _build_view(
            portfolio_id,
            account["label"],
            account["base_currency"],
            [portfolio_id],
            data,
        )
        if metadata["portfolio_type"].startswith("exclude_company_country"):
            view["metrics"]["excluded_market_value_base"] = metadata[
                "latest_excluded_market_value_base"
            ]
            view["metrics"]["excluded_position_count"] = metadata[
                "latest_excluded_position_count"
            ]
        continuous_nav = _most_recent_continuous_nav(view["nav"])
        view["performance"] = _performance_metrics(
            continuous_nav,
            _aggregate_flows(data["flows"], [portfolio_id]),
            data["risk_free"].get(account["base_currency"]),
            None,
        )
        if metadata["portfolio_type"] == "fixed_unit_composite":
            view["performance"]["total_return_method"] = (
                "fixed-unit composite return from common-base-currency normalized sleeves"
            )
        else:
            view["performance"]["total_return_method"] = (
                "derived daily Modified Dietz (continuous position-snapshot period)"
            )
        view["derived_portfolio"] = metadata
        if metadata["portfolio_type"] == "fixed_unit_composite":
            definition = metadata["exclusion_rule"]
            view["warnings"].insert(
                0,
                "Fixed-unit derived portfolio: "
                f"{definition.get('formula', 'formula unavailable')}. "
                f"Normalized on {definition.get('normalization_date', 'the configured date')}; "
                "the 58 cash units earn no interest and sleeve weights are not rebalanced."
            )
        else:
            view["warnings"].insert(
                0,
                "Derived portfolio: mirrors account "
                f"{metadata['source_account_id']} but removes Taiwanese-company positions. "
                "Source cash is replaced by an HKD/USD ledger beginning with "
                "HKD 484,300 and USD 0 before activity on 20 January 2025; "
                "TWD and other currencies are excluded. Actual posted HKD/USD "
                "interest is included, but no assumed interest is applied."
            )
        if (
            metadata["portfolio_type"].startswith("exclude_company_country")
            and len(continuous_nav) < len(view["nav"])
        ):
            view["warnings"].insert(
                1,
                "Derived performance and daily risk statistics begin with the most "
                "recent continuous position-snapshot period; older month-end points "
                "remain visible in the NAV chart but are excluded from daily metrics."
            )
        views[portfolio_id] = view
        order.append(portfolio_id)
    return {
        "title": REPORT_TITLE,
        "generated_at": generated_at.astimezone(HKT).isoformat(timespec="seconds"),
        "latest_run": latest_run,
        "activity_start": data["activity_start"],
        "view_order": order,
        "views": views,
    }


def _html_document(payload: dict[str, Any]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = html.escape(payload["title"])
    return f'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>{title}</title>
  <style>
    :root {{ --bg:#f4f7fb; --surface:#ffffff; --surface-2:#eef3f9; --text:#142033; --muted:#607089; --line:#d9e2ee; --navy:#10233f; --blue:#246bfd; --cyan:#18a7b8; --green:#16815b; --red:#c43d4b; --amber:#aa6b00; --shadow:0 8px 28px rgba(19,42,76,.08); }}
    @media (prefers-color-scheme:dark) {{ :root {{ --bg:#0d1521; --surface:#152235; --surface-2:#1c2d44; --text:#e8eef7; --muted:#a6b4c8; --line:#2d405a; --navy:#0a1423; --blue:#74a0ff; --cyan:#65c8d3; --green:#55c996; --red:#ff8190; --amber:#f0b95b; --shadow:none; }} }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:var(--bg); color:var(--text); font:14px/1.5 Inter,Segoe UI,Arial,sans-serif; }}
    header {{ background:linear-gradient(135deg,var(--navy),#173a67); color:#fff; padding:28px max(20px,calc((100% - 1220px)/2)); }}
    header h1 {{ margin:0 0 4px; font-size:28px; font-weight:650; letter-spacing:-.02em; }}
    header p {{ margin:0; color:#c9d8eb; }}
    main {{ max-width:1220px; margin:0 auto; padding:22px 20px 48px; }}
    .toolbar {{ display:flex; gap:14px; align-items:end; justify-content:space-between; flex-wrap:wrap; margin-bottom:18px; }}
    label {{ display:block; color:var(--muted); font-size:12px; font-weight:650; margin-bottom:5px; text-transform:uppercase; letter-spacing:.06em; }}
    select,button {{ font:inherit; color:var(--text); background:var(--surface); border:1px solid var(--line); border-radius:9px; padding:9px 12px; }}
    select {{ min-width:260px; }} button {{ cursor:pointer; }} button:hover {{ border-color:var(--blue); }}
    .freshness {{ color:var(--muted); text-align:right; }} .freshness strong {{ color:var(--green); }}
    .grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; }}
    .analytics-grid {{ display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:12px; }}
    .card {{ background:var(--surface); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow); padding:16px; }}
    .kpi-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.05em; }}
    .kpi-value {{ font-size:25px; font-weight:650; letter-spacing:-.02em; margin-top:4px; font-variant-numeric:tabular-nums; }}
    .kpi-note {{ color:var(--muted); font-size:12px; margin-top:2px; }}
    .positive {{ color:var(--green); }} .negative {{ color:var(--red); }}
    section {{ margin-top:18px; }} .section-title {{ display:flex; align-items:baseline; justify-content:space-between; gap:10px; margin:0 0 10px; }}
    h2 {{ font-size:18px; margin:0; }} h3 {{ font-size:15px; margin:0 0 10px; }} .subtle {{ color:var(--muted); font-size:12px; }}
    .two-col {{ display:grid; grid-template-columns:minmax(0,1.65fr) minmax(280px,.85fr); gap:14px; }}
    #nav-chart {{ position:relative; min-height:310px; }} #nav-chart svg {{ width:100%; height:300px; display:block; }}
    .chart-tooltip {{ position:absolute; pointer-events:none; display:none; background:var(--navy); color:#fff; padding:7px 9px; border-radius:7px; font-size:12px; box-shadow:var(--shadow); white-space:nowrap; z-index:2; }}
    .bar-row {{ display:grid; grid-template-columns:82px 1fr 105px; gap:9px; align-items:center; margin:10px 0; }}
    .bar-label {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-weight:600; }} .bar-track {{ height:9px; background:var(--surface-2); border-radius:99px; overflow:hidden; }}
    .bar-fill {{ height:100%; background:linear-gradient(90deg,var(--blue),var(--cyan)); border-radius:99px; }} .bar-value {{ text-align:right; font-variant-numeric:tabular-nums; }}
    .sector-bars .bar-row {{ grid-template-columns:150px 1fr 170px; }}
    .mini-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:12px; }} .mini {{ background:var(--surface-2); border-radius:10px; padding:11px; }}
    .mini span {{ color:var(--muted); display:block; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }} .mini strong {{ font-size:16px; font-variant-numeric:tabular-nums; }}
    .table-wrap {{ overflow-x:auto; }} table {{ width:100%; border-collapse:collapse; min-width:760px; }} th,td {{ border-bottom:1px solid var(--line); padding:9px 10px; text-align:left; vertical-align:top; }}
    th {{ color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; font-weight:650; }} td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
    tbody tr:hover {{ background:var(--surface-2); }} .symbol {{ font-weight:650; }} .desc {{ display:block; color:var(--muted); font-size:11px; max-width:280px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }}
    .status-row {{ display:flex; flex-wrap:wrap; gap:10px 22px; }} .status-row div {{ min-width:140px; }} .status-row span {{ color:var(--muted); display:block; font-size:11px; text-transform:uppercase; }}
    .note {{ border-left:4px solid var(--amber); background:var(--surface); padding:12px 14px; border-radius:0 10px 10px 0; color:var(--muted); }}
    .warning {{ color:var(--amber); margin:6px 0; }} .empty {{ color:var(--muted); padding:18px 0; text-align:center; }}
    footer {{ color:var(--muted); font-size:12px; margin-top:22px; text-align:center; }}
    @media(max-width:1050px) {{ .analytics-grid {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} }}
    @media(max-width:900px) {{ .grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .two-col {{ grid-template-columns:1fr; }} .analytics-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media(max-width:540px) {{ header h1 {{ font-size:23px; }} main {{ padding-inline:12px; }} .grid,.analytics-grid {{ grid-template-columns:1fr; }} select {{ min-width:0; width:100%; }} .toolbar>div:first-child {{ width:100%; }} .freshness {{ text-align:left; }} .mini-grid {{ grid-template-columns:1fr; }} .sector-bars .bar-row {{ grid-template-columns:95px 1fr 115px; }} }}
    @media print {{ body {{ background:#fff; color:#111; }} header {{ background:#fff; color:#111; padding:0 0 12px; }} header p {{ color:#555; }} main {{ max-width:none; padding:0; }} button {{ display:none; }} .card {{ box-shadow:none; break-inside:avoid; }} section {{ break-inside:avoid; }} }}
  </style>
</head>
<body>
  <header><h1>{title}</h1><p>End-of-day portfolio oversight from Interactive Brokers Flex statements</p></header>
  <main>
    <div class="toolbar">
      <div><label for="account-select">Portfolio view</label><select id="account-select"></select></div>
      <div class="freshness" id="freshness"></div>
      <button type="button" onclick="window.print()">Print / save as PDF</button>
    </div>
    <div class="grid" id="kpis"></div>
    <section class="two-col">
      <div class="card"><div class="section-title"><h2>NAV history</h2><span class="subtle" id="nav-range"></span></div><div id="nav-chart"><div class="chart-tooltip" id="chart-tooltip"></div></div></div>
      <div class="card"><h2>Portfolio snapshot</h2><div class="mini-grid" id="risk-metrics"></div><h3 style="margin-top:18px">Gross exposure by asset</h3><div id="allocation"></div></div>
    </section>
    <section><div class="section-title"><h2>Performance & risk</h2><span class="subtle" id="performance-period"></span></div><div class="analytics-grid" id="performance-metrics"></div><p class="subtle" id="performance-method"></p></section>
    <section class="card"><div class="section-title"><h2>Sector allocation</h2><span class="subtle" id="sector-coverage"></span></div><div class="sector-bars" id="sector-allocation"></div></section>
    <section><div class="section-title"><h2>Largest holdings</h2><span class="subtle">Converted to account base currency</span></div><div class="card table-wrap" id="positions"></div></section>
    <section><div class="section-title"><h2>Trading activity — last 30 days</h2><span class="subtle" id="activity-period"></span></div><div class="grid" id="activity-kpis"></div><div class="card table-wrap" id="trades" style="margin-top:14px"></div></section>
    <section><div class="section-title"><h2>Recent cash activity</h2><span class="subtle">Broker classifications and original currencies</span></div><div class="card table-wrap" id="cash-activity"></div></section>
    <section class="card"><h2>Data quality & methodology</h2><div class="status-row" id="run-status"></div><div id="warnings"></div></section>
    <section class="note">Account total and annualized returns use IBKR time-weighted returns when available. Daily risk statistics use transfer-adjusted Modified Dietz returns with a mid-day flow assumption. VaR and CVaR are historical estimates, not loss limits or forecasts. Sector classifications are cached external reference data and unclassified holdings remain visible. This is an end-of-day accounting dashboard, not a live market monitor.</section>
    <footer id="footer"></footer>
  </main>
  <script id="report-data" type="application/json">{serialized}</script>
  <script>
    const report=JSON.parse(document.getElementById('report-data').textContent); let currentKey=report.view_order[0];
    const $=id=>document.getElementById(id); const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    const money=(v,c='USD')=>v==null?'—':new Intl.NumberFormat('en-HK',{{style:'currency',currency:c,maximumFractionDigits:2}}).format(v);
    const number=(v,d=2)=>v==null?'—':new Intl.NumberFormat('en-HK',{{maximumFractionDigits:d}}).format(v);
    const pct=v=>v==null?'—':`${{v>=0?'+':''}}${{number(v,2)}}%`; const dte=v=>v?new Date(v+'T00:00:00').toLocaleDateString('en-GB',{{day:'2-digit',month:'short',year:'numeric'}}):'—';
    const plainPct=v=>v==null?'—':`${{number(v,2)}}%`;
    const tone=v=>v>0?'positive':v<0?'negative':'';
    function card(label,value,note='',klass=''){{return `<div class="card"><div class="kpi-label">${{esc(label)}}</div><div class="kpi-value ${{klass}}">${{value}}</div><div class="kpi-note">${{esc(note)}}</div></div>`}}
    function populate(){{const sel=$('account-select'); report.view_order.forEach(k=>{{const o=document.createElement('option');o.value=k;o.textContent=report.views[k].label;sel.appendChild(o)}});sel.addEventListener('change',()=>{{currentKey=sel.value;render()}})}}
    function render(){{const v=report.views[currentKey],m=v.metrics,c=v.base_currency;
      $('kpis').innerHTML=card('Net liquidation value',money(m.nav,c),`As of ${{dte(v.latest_nav_date)}}`)+card('Change vs prior statement',money(m.nav_change,c),`${{pct(m.nav_change_pct)}} since ${{dte(v.previous_nav_date)}}`,tone(m.nav_change))+card('Cash',money(m.cash,c),'NAV cash component')+card('Unrealized P&L',money(m.unrealized_pnl,c),'Latest open positions',tone(m.unrealized_pnl));
      const snapshotMetrics=[["Gross exposure",money(m.gross_exposure,c)],["Net exposure",money(m.net_exposure,c)],["Positions",number(m.position_count,0)],["Top 5 concentration",m.top_five_pct==null?'—':number(m.top_five_pct,1)+'%']];if(v.derived_portfolio&&v.derived_portfolio.portfolio_type.startsWith('exclude_company_country')){{const b=v.derived_portfolio.latest_cash_balances||{{}};snapshotMetrics.push(["Taiwan positions removed",number(m.excluded_position_count,0)],["Excluded market value",money(m.excluded_market_value_base,c)],["HKD cash balance",money(b.HKD,'HKD')],["USD cash balance",money(b.USD,'USD')])}}$('risk-metrics').innerHTML=snapshotMetrics.map(x=>`<div class="mini"><span>${{x[0]}}</span><strong>${{x[1]}}</strong></div>`).join('');
      renderBars(v.allocation,c); renderPerformance(v); renderSector(v,c); renderPositions(v,c); renderTrades(v,c); renderCash(v,c); drawNav(v,c); renderStatus(v);
      $('activity-period').textContent=`${{dte(report.activity_start)}} to ${{dte(v.latest_nav_date)}}`;
      $('freshness').innerHTML=`<strong>${{esc(report.latest_run.reconciliation)}} / ${{esc(report.latest_run.status.toUpperCase())}}</strong><br>IBKR data requested through ${{dte(report.latest_run.requested_to)}}`;
      $('footer').textContent=`Generated ${{new Date(report.generated_at).toLocaleString('en-HK',{{dateStyle:'medium',timeStyle:'medium',timeZone:'Asia/Hong_Kong'}})}} HKT • ${{v.label}} • Base currency ${{c}}`;
    }}
    function renderBars(items,c){{if(!items.length){{$('allocation').innerHTML='<div class="empty">No open positions</div>';return}}const max=Math.max(...items.map(x=>x.value));$('allocation').innerHTML=items.map(x=>`<div class="bar-row"><div class="bar-label">${{esc(x.label)}}</div><div class="bar-track"><div class="bar-fill" style="width:${{max?100*x.value/max:0}}%"></div></div><div class="bar-value">${{money(x.value,c)}}</div></div>`).join('')}}
    function renderPerformance(v){{const p=v.performance,obs=`${{number(p.observations,0)}} daily observations`;$('performance-metrics').innerHTML=card('Total return',pct(p.total_return_pct),p.total_return_method,tone(p.total_return_pct))+card('Annualized return',pct(p.annualized_return_pct),`${{dte(p.return_from)}} – ${{dte(p.return_to)}}`,tone(p.annualized_return_pct))+card('Annualized volatility',plainPct(p.annualized_volatility_pct),obs)+card('Current risk-free rate',plainPct(p.risk_free_rate_pct),`3-month U.S. Treasury • ${{dte(p.risk_free_date)}}`)+card('Sharpe ratio',number(p.sharpe_ratio,2),'Annualized; current risk-free rate',tone(p.sharpe_ratio))+card('Maximum drawdown',pct(p.max_drawdown_pct),'From adjusted daily return path',tone(p.max_drawdown_pct))+card('VaR 95% — daily',plainPct(p.var_95_daily_pct),'Historical one-day loss threshold')+card('CVaR 95% — daily',plainPct(p.cvar_95_daily_pct),'Average loss beyond VaR')+card('Win rate',plainPct(p.win_rate_pct),'Positive adjusted daily returns')+card('BLUE mean daily return',pct(p.blue_mean_daily_pct),`SE ${{plainPct(p.blue_standard_error_pct)}} • ${{obs}}`,tone(p.blue_mean_daily_pct));$('performance-period').textContent=`${{dte(p.return_from)}} – ${{dte(p.return_to)}}`;$('performance-method').textContent=`${{p.total_return_method}} for total/annualized return. ${{p.risk_method}} for daily risk statistics. BLUE is the sample mean, best linear unbiased under independent equal-variance return assumptions.`}}
    function renderSector(v,c){{const items=v.sector_allocation,total=items.reduce((sum,item)=>sum+item.value,0);$('sector-coverage').textContent=v.sector_coverage_pct==null?'No invested positions':`${{number(v.sector_coverage_pct,1)}}% of gross exposure classified`;if(!items.length){{$('sector-allocation').innerHTML='<div class="empty">No open positions</div>';return}}const max=Math.max(...items.map(x=>x.value));$('sector-allocation').innerHTML=items.map(x=>`<div class="bar-row"><div class="bar-label">${{esc(x.label)}}</div><div class="bar-track"><div class="bar-fill" style="width:${{max?100*x.value/max:0}}%"></div></div><div class="bar-value">${{total?number(100*x.value/total,1)+'% • ':''}}${{money(x.value,c)}}</div></div>`).join('')}}
    function renderPositions(v,c){{if(!v.positions.length){{$('positions').innerHTML='<div class="empty">No open positions reported</div>';return}}const gross=v.metrics.gross_exposure||0;$('positions').innerHTML=`<table><thead><tr><th>Holding</th><th>Sector</th><th>Asset</th><th>Currency</th><th class="num">Quantity</th><th class="num">Market value (${{c}})</th><th class="num">Weight</th><th class="num">Unrealized P&L</th></tr></thead><tbody>${{v.positions.map(p=>`<tr><td><span class="symbol">${{esc(p.symbol)}}</span><span class="desc">${{esc(p.description)}}</span></td><td>${{esc(p.sector)}}</td><td>${{esc(p.asset)}}</td><td>${{esc(p.currency)}}</td><td class="num">${{number(p.quantity,4)}}</td><td class="num">${{money(p.market_value_base,c)}}</td><td class="num">${{gross?number(100*Math.abs(p.market_value_base||0)/gross,2)+'%':'—'}}</td><td class="num ${{tone(p.unrealized_pnl_base)}}">${{money(p.unrealized_pnl_base,c)}}</td></tr>`).join('')}}</tbody></table>`}}
    function renderTrades(v,c){{const m=v.metrics;$('activity-kpis').innerHTML=card('Trades',number(m.trade_count_30d,0),'Executions')+card('Gross turnover',money(m.turnover_30d,c),'Absolute proceeds')+card('Commissions',money(m.commission_30d,c),'Estimated cost in base currency')+card('Realized P&L',money(m.realized_pnl_30d,c),'Broker-reported',tone(m.realized_pnl_30d));if(!v.trades.length){{$('trades').innerHTML='<div class="empty">No trades in the last 30 days</div>';return}}$('trades').innerHTML=`<table><thead><tr><th>Date</th><th>Account</th><th>Symbol</th><th>Side</th><th class="num">Quantity</th><th class="num">Price</th><th class="num">Proceeds</th><th class="num">Commission</th><th class="num">Realized P&L</th></tr></thead><tbody>${{v.trades.map(t=>`<tr><td>${{dte(t.date)}}</td><td>${{esc(t.account)}}</td><td class="symbol">${{esc(t.symbol)}}</td><td>${{esc(t.side)}}</td><td class="num">${{number(t.quantity,4)}}</td><td class="num">${{money(t.price,t.currency)}}</td><td class="num">${{money(t.proceeds,t.currency)}}</td><td class="num">${{money(t.commission,t.commission_currency)}}</td><td class="num ${{tone(t.realized_pnl)}}">${{money(t.realized_pnl,t.currency)}}</td></tr>`).join('')}}</tbody></table>`}}
    function renderCash(v,c){{if(!v.cash_activity.length){{$('cash-activity').innerHTML='<div class="empty">No cash transactions in the last 30 days</div>';return}}$('cash-activity').innerHTML=`<table><thead><tr><th>Date</th><th>Account</th><th>Type</th><th>Description</th><th class="num">Amount</th></tr></thead><tbody>${{v.cash_activity.map(x=>`<tr><td>${{dte(x.date)}}</td><td>${{esc(x.account)}}</td><td>${{esc(x.type)}}</td><td>${{esc(x.description)}}</td><td class="num ${{tone(x.amount)}}">${{money(x.amount,x.currency)}}</td></tr>`).join('')}}</tbody></table>`}}
    function renderStatus(v){{const r=report.latest_run;$('run-status').innerHTML=`<div><span>Latest run</span><strong>#${{r.run_id}} ${{esc(r.run_type)}}</strong></div><div><span>Requested window</span><strong>${{dte(r.requested_from)}} – ${{dte(r.requested_to)}}</strong></div><div><span>Reconciliation</span><strong class="positive">${{esc(r.reconciliation)}}</strong></div><div><span>Rows processed</span><strong>${{number(r.row_count,0)}}</strong></div><div><span>Position date</span><strong>${{dte(v.position_date)}}</strong></div>`;$('warnings').innerHTML=v.warnings.map(x=>`<p class="warning">${{esc(x)}}</p>`).join('')}}
    function drawNav(v,c){{const host=$('nav-chart'),tip=$('chart-tooltip');host.querySelectorAll('svg').forEach(x=>x.remove());const values=v.nav.filter(x=>x.total!=null);$('nav-range').textContent=values.length?`${{dte(values[0].date)}} – ${{dte(values.at(-1).date)}}`:'No history';if(!values.length){{host.insertAdjacentHTML('beforeend','<div class="empty">No NAV history</div>');return}}const width=Math.max(300,host.clientWidth),height=300,p={{l:70,r:18,t:16,b:36}},iw=width-p.l-p.r,ih=height-p.t-p.b,min=Math.min(...values.map(x=>x.total)),max=Math.max(...values.map(x=>x.total)),pad=(max-min||Math.abs(max)||1)*.08,ymin=min-pad,ymax=max+pad,x=i=>p.l+(values.length===1?iw/2:i*iw/(values.length-1)),y=n=>p.t+(ymax-n)*ih/(ymax-ymin);const points=values.map((d,i)=>`${{x(i)}},${{y(d.total)}}`).join(' ');let grid='';for(let i=0;i<5;i++){{const val=ymax-i*(ymax-ymin)/4,yy=y(val);grid+=`<line x1="${{p.l}}" y1="${{yy}}" x2="${{width-p.r}}" y2="${{yy}}" stroke="var(--line)"/><text x="${{p.l-8}}" y="${{yy+4}}" text-anchor="end" fill="var(--muted)" font-size="11">${{number(val,0)}}</text>`}}const ticks=Math.min(width<500?3:5,values.length),idx=[...new Set(Array.from({{length:ticks}},(_,i)=>Math.round(i*(values.length-1)/Math.max(1,ticks-1))))];const labels=idx.map(i=>`<text x="${{x(i)}}" y="${{height-10}}" text-anchor="middle" fill="var(--muted)" font-size="11">${{dte(values[i].date).replace(/ \\d{{4}}$/,'')}}</text>`).join('');const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox',`0 0 ${{width}} ${{height}}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',`NAV history for ${{v.label}} in ${{c}}`);svg.innerHTML=`${{grid}}<polyline points="${{points}}" fill="none" stroke="var(--blue)" stroke-width="2.5" vector-effect="non-scaling-stroke"/><circle cx="${{x(values.length-1)}}" cy="${{y(values.at(-1).total)}}" r="4" fill="var(--blue)"/><line id="hover-line" x1="0" y1="${{p.t}}" x2="0" y2="${{height-p.b}}" stroke="var(--muted)" visibility="hidden"/><circle id="hover-dot" r="5" fill="var(--surface)" stroke="var(--blue)" stroke-width="2" visibility="hidden"/><rect x="${{p.l}}" y="${{p.t}}" width="${{iw}}" height="${{ih}}" fill="transparent" pointer-events="all"/>${{labels}}`;host.appendChild(svg);const overlay=svg.querySelector('rect'),line=svg.querySelector('#hover-line'),dot=svg.querySelector('#hover-dot');overlay.addEventListener('mousemove',e=>{{const rect=svg.getBoundingClientRect(),mx=(e.clientX-rect.left)*width/rect.width,i=Math.max(0,Math.min(values.length-1,Math.round((mx-p.l)/iw*(values.length-1)))),d=values[i],xx=x(i),yy=y(d.total);line.setAttribute('x1',xx);line.setAttribute('x2',xx);line.setAttribute('visibility','visible');dot.setAttribute('cx',xx);dot.setAttribute('cy',yy);dot.setAttribute('visibility','visible');tip.style.display='block';tip.textContent=`${{dte(d.date)}} • ${{money(d.total,c)}}`;tip.style.left=Math.min(host.clientWidth-tip.offsetWidth-5,Math.max(5,e.clientX-host.getBoundingClientRect().left+12))+'px';tip.style.top=Math.max(5,e.clientY-host.getBoundingClientRect().top-38)+'px'}});overlay.addEventListener('mouseleave',()=>{{line.setAttribute('visibility','hidden');dot.setAttribute('visibility','hidden');tip.style.display='none'}})}}
    let resizeTimer; window.addEventListener('resize',()=>{{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>drawNav(report.views[currentKey],report.views[currentKey].base_currency),120)}});populate();render();
  </script>
</body>
</html>'''


def generate_daily_report(
    database_path: Path,
    output_dir: Path,
    generated_at: datetime | None = None,
    database_url: str = "",
    tenant_id: str = "00000000-0000-0000-0000-000000000001",
) -> tuple[Path, Path]:
    generated = generated_at or datetime.now(HKT)
    reference_data = load_reference_data(
        database_path.resolve().parent / "reference_data.json"
    )
    connection = database_connect(database_path, database_url, tenant_id)
    try:
        if not is_postgres(connection) and connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite quick_check failed; report was not generated.")
        if is_postgres(connection):
            connection.execute("SELECT 1").fetchone()
        payload = _report_payload(connection, generated, reference_data)
    finally:
        connection.close()

    report_date = (
        payload["latest_run"].get("requested_to")
        or max(
            (view.get("latest_nav_date") or "" for view in payload["views"].values()),
            default=generated.date().isoformat(),
        )
        or generated.date().isoformat()
    )
    document = _html_document(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = output_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    latest_path = output_dir / "IBKR_Portfolio_Analytics_Latest.html"
    archive_path = archive_dir / f"IBKR_Portfolio_Analytics_{report_date}.html"

    for path in (latest_path, archive_path):
        temporary_path = path.with_suffix(path.suffix + ".tmp")
        temporary_path.write_text(document, encoding="utf-8")
        temporary_path.replace(path)
    return latest_path, archive_path
