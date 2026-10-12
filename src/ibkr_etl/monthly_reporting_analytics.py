from __future__ import annotations

import re
import calendar
import bisect
from collections import defaultdict
from datetime import date
from typing import Any


METHOD_VERSION = "monthly-reporting-attribution-v1"
PHYSICAL_METHOD_VERSION = "monthly-reporting-attribution-v2-full-nav-coverage"


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _month_start(period_end: str) -> str:
    parsed = date.fromisoformat(period_end)
    if parsed.day != calendar.monthrange(parsed.year, parsed.month)[1]:
        raise ValueError("period_end must be a calendar month end")
    return parsed.replace(day=1).isoformat()


def _external_cash_flow(transaction_type: Any, description: Any) -> bool:
    text = f"{transaction_type or ''} {description or ''}".upper()
    # Whole words only: "SWIRE PACIFIC" dividends are not wires.
    return re.search(
        r"\b(DEPOSITS?|WITHDRAW\w*|TRANSFERS?|WIRE|ACH|CONTRIBUTIONS?|DISTRIBUTIONS?)\b",
        text,
    ) is not None


def _cash_bucket(transaction_type: Any, description: Any, amount: float) -> str:
    text = f"{transaction_type or ''} {description or ''}".upper()
    if "DIVIDEND" in text:
        return "dividend"
    if "INTEREST" in text:
        return "interest_received" if amount >= 0 else "interest_paid"
    if any(token in text for token in ("WITHHOLD", "TAX", "FEE", "COMMISSION", "CUSTODY")):
        return "fees_and_withholding"
    return "other_internal_cash"


class _FxHistory:
    """Select an account's latest non-forward-looking direct or reciprocal FX rate."""

    def __init__(self, rows: Any) -> None:
        self.series: dict[tuple[str, str], list[tuple[str, Any]]] = defaultdict(list)
        for row in rows:
            key = (
                str(row["from_currency"] or "").upper(),
                str(row["to_currency"] or "").upper(),
            )
            self.series[key].append((str(row["rate_date"]), row["rate_text"]))
        for values in self.series.values():
            values.sort(key=lambda item: item[0])

    @staticmethod
    def _latest(values: list[tuple[str, Any]], on_date: str) -> float | None:
        if not values:
            return None
        from .fx_selection import latest_fx_quote
        quote = latest_fx_quote(values, on_date)
        return quote[0] if quote else None

    def rate(self, source: str, target: str, on_date: str) -> float | None:
        source = source.upper()
        target = target.upper()
        if source == target:
            return 1.0
        direct = self._latest(self.series.get((source, target), []), on_date)
        if direct is not None:
            return direct
        inverse = self._latest(self.series.get((target, source), []), on_date)
        return None if inverse in (None, 0.0) else 1.0 / inverse


def _build_composite_monthly_reporting_analytics(
    connection: Any,
    *,
    subject_id: str,
    period_end: str,
    reporting_currency: str,
) -> dict[str, Any]:
    """Attribute a fixed-unit composite to its deterministic component sleeves."""

    month_start = _month_start(period_end)
    previous = connection.execute(
        """
        SELECT MAX(snapshot_date) AS snapshot_date
        FROM subject_nav_series
        WHERE subject_id = ? AND currency = ? AND snapshot_date < ?
        """,
        (subject_id, reporting_currency, month_start),
    ).fetchone()
    range_start = str(previous["snapshot_date"] or month_start) if previous else month_start
    nav_rows = connection.execute(
        """
        SELECT snapshot_date, total_text
        FROM subject_nav_series
        WHERE subject_id = ? AND currency = ? AND snapshot_date BETWEEN ? AND ?
        ORDER BY snapshot_date
        """,
        (subject_id, reporting_currency, range_start, period_end),
    ).fetchall()
    nav_by_date = {
        str(row["snapshot_date"]): _number(row["total_text"])
        for row in nav_rows
        if _number(row["total_text"]) not in (None, 0.0)
    }
    component_rows = connection.execute(
        """
        SELECT snapshot_date, component_id, component_name, component_type,
               direction, contribution
        FROM synthetic_portfolio_components
        WHERE portfolio_id = ? AND snapshot_date BETWEEN ? AND ?
          AND base_currency = ?
        ORDER BY snapshot_date, component_id
        """,
        (subject_id, range_start, period_end, reporting_currency),
    ).fetchall()
    components: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    metadata: dict[str, dict[str, str]] = {}
    for row in component_rows:
        component_id = str(row["component_id"])
        components[str(row["snapshot_date"])][component_id] = {
            "contribution": float(row["contribution"]),
        }
        metadata[component_id] = {
            "name": str(row["component_name"] or component_id),
            "type": str(row["component_type"] or "component"),
            "direction": str(row["direction"] or "long"),
        }
    dates = sorted(set(nav_by_date).intersection(components))
    daily: list[dict[str, Any]] = []
    for prior_day, current_day in zip(dates, dates[1:]):
        if current_day < month_start:
            continue
        prior_nav = nav_by_date[prior_day]
        current_nav = nav_by_date[current_day]
        if prior_nav is None or current_nav is None or prior_nav == 0:
            continue
        contributions: dict[str, float] = {}
        component_ids = set(components[prior_day]) | set(components[current_day])
        for component_id in component_ids:
            prior_value = components[prior_day].get(component_id, {}).get("contribution", 0.0)
            current_value = components[current_day].get(component_id, {}).get("contribution", 0.0)
            contributions[component_id] = (current_value - prior_value) / prior_nav
        portfolio_return = current_nav / prior_nav - 1.0
        daily.append(
            {
                "date": current_day,
                "portfolio_return": portfolio_return,
                "security_contributions": contributions,
                "residual_contribution": portfolio_return - sum(contributions.values()),
            }
        )
    if not daily:
        return {
            "tool_name": "get_monthly_reporting_analytics",
            "quality_status": "fail",
            "message": "Insufficient common composite NAV and component observations for monthly attribution.",
            "data": {
                "available": False,
                "period_start": month_start,
                "period_end": period_end,
                "method_version": METHOD_VERSION,
            },
            "citations": (
                {"table": "subject_nav_series", "subject_id": subject_id},
                {"table": "synthetic_portfolio_components", "subject_id": subject_id},
            ),
        }
    linked: dict[str, float] = defaultdict(float)
    linked_residual = 0.0
    future_growth = 1.0
    for item in reversed(daily):
        for component_id, value in item["security_contributions"].items():
            linked[component_id] += value * future_growth
        linked_residual += item["residual_contribution"] * future_growth
        future_growth *= 1.0 + item["portfolio_return"]
    rows = []
    type_values: dict[str, float] = defaultdict(float)
    for component_id, contribution in linked.items():
        meta = metadata.get(component_id, {"name": component_id, "type": "component", "direction": "long"})
        rows.append(
            {
                "conid": component_id,
                "symbol": meta["name"],
                "description": meta["name"],
                "sector": meta["type"].replace("_", " ").title(),
                "direction": meta["direction"],
                "contribution": contribution,
            }
        )
        type_values[meta["type"]] += contribution
    rows.sort(key=lambda row: float(row["contribution"]))
    return {
        "tool_name": "get_monthly_reporting_analytics",
        "quality_status": "pass",
        "message": "Monthly component contributions are linked from the approved fixed-unit composite calculation and reconcile to compounded NAV return.",
        "data": {
            "available": True,
            "period_start": daily[0]["date"],
            "period_end": daily[-1]["date"],
            "reporting_currency": reporting_currency,
            "method_version": METHOD_VERSION,
            "daily_observations": len(daily),
            "portfolio_monthly_return": future_growth - 1.0,
            "equity_contribution": sum(linked.values()),
            "residual_contribution": linked_residual,
            "fx_conversion": {
                "method": "component values are already converted by the approved composite engine",
                "converted_currencies": [],
                "missing_currencies": [],
            },
            "security_contributions": rows,
            "sector_contributions": [
                {"sector": key.replace("_", " ").title(), "contribution": value}
                for key, value in sorted(type_values.items(), key=lambda item: item[1])
            ],
            "cash_activity": {
                "dividends_by_symbol": [],
                "total_dividends": 0.0,
                "interest_received": 0.0,
                "interest_paid": 0.0,
                "fees_and_withholding": 0.0,
                "other_internal_cash": 0.0,
                "net_listed_cash_activity": 0.0,
            },
        },
        "citations": (
            {"table": "subject_nav_series", "subject_id": subject_id, "through": daily[-1]["date"]},
            {"table": "synthetic_portfolio_components", "subject_id": subject_id, "through": daily[-1]["date"]},
        ),
    }


def build_monthly_reporting_analytics(
    connection: Any,
    *,
    subject_id: str,
    period_end: str,
    reporting_currency: str,
) -> dict[str, Any]:
    """Build deterministic monthly contribution and cash pages for the v23 deck.

    Security effects use the prior day's units and mark-price change divided by
    prior NAV. Daily effects, including the residual, are Frongello-linked so
    that they add exactly to the compounded monthly NAV return. The residual
    captures cash, fees, FX, trading effects and unmatched position movement.
    """

    month_start = _month_start(period_end)
    currency = reporting_currency.upper()
    legacy_sqlite = connection.__class__.__module__.startswith("sqlite3") and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_subjects'"
    ).fetchone() is None
    subject = None if legacy_sqlite else connection.execute(
        "SELECT subject_type, source_account_id FROM platform_subjects WHERE subject_id = ?",
        (subject_id,),
    ).fetchone()
    subject_type = str(subject["subject_type"]) if subject is not None else "physical_account"
    if subject_type == "composite_portfolio":
        return _build_composite_monthly_reporting_analytics(
            connection,
            subject_id=subject_id,
            period_end=period_end,
            reporting_currency=currency,
        )
    source_account_id = None if subject is None else subject["source_account_id"]
    if subject_type == "filtered_portfolio" and not source_account_id:
        definition = connection.execute(
            """
            SELECT configuration_json ->> 'source_account_id' AS source_account_id
            FROM subject_calculation_definitions
            WHERE subject_id = ? AND subject_type = 'filtered_portfolio'
              AND approval_status IN ('validated', 'approved')
            ORDER BY definition_version DESC, created_at DESC
            LIMIT 1
            """,
            (subject_id,),
        ).fetchone()
        source_account_id = None if definition is None else definition["source_account_id"]
    fx_account_id = str(source_account_id or subject_id)
    nav_relation = "nav_snapshots" if legacy_sqlite else "subject_nav_series"
    nav_subject_column = "account_id" if legacy_sqlite else "subject_id"
    nav_total_filter = "AND n.asset_class = 'TOTAL'" if legacy_sqlite else ""
    nav_total_filter_plain = "AND asset_class = 'TOTAL'" if legacy_sqlite else ""
    position_relation = "position_snapshots" if legacy_sqlite else "subject_position_series"
    position_subject_column = "account_id" if legacy_sqlite else "subject_id"
    previous = connection.execute(
        """
        SELECT MAX(n.snapshot_date) AS snapshot_date
        FROM {nav_relation} n
        WHERE n.{nav_subject_column} = ? AND n.currency = ?
          {nav_total_filter}
          AND n.snapshot_date < ?
        """.format(
            nav_relation=nav_relation,
            nav_subject_column=nav_subject_column,
            nav_total_filter=nav_total_filter,
            position_relation=position_relation,
            position_subject_column=position_subject_column,
        ),
        (subject_id, currency, month_start),
    ).fetchone()
    prior_date = None if previous is None else previous["snapshot_date"]
    range_start = str(prior_date or month_start)

    nav_rows = connection.execute(
        """
        SELECT snapshot_date, total_text
        FROM {nav_relation}
        WHERE {nav_subject_column} = ? AND currency = ?
          {nav_total_filter_plain}
          AND snapshot_date BETWEEN ? AND ?
        ORDER BY snapshot_date
        """.format(
            nav_relation=nav_relation,
            nav_subject_column=nav_subject_column,
            nav_total_filter_plain=nav_total_filter_plain,
        ),
        (subject_id, currency, range_start, period_end),
    ).fetchall()
    nav_by_date = {
        str(row["snapshot_date"]): _number(row["total_text"])
        for row in nav_rows
        if _number(row["total_text"]) not in (None, 0.0)
    }

    fx = _FxHistory(
        connection.execute(
            """
            SELECT rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            WHERE account_id = ? AND rate_date <= ?
            ORDER BY from_currency, to_currency, rate_date
            """,
            (fx_account_id, period_end),
        ).fetchall()
    )

    position_rows = connection.execute(
        """
        SELECT p.snapshot_date, p.conid, p.currency, p.quantity_text,
               p.mark_price_text, i.symbol, i.description,
               COALESCE(ic.sector, 'Unclassified') AS sector
        FROM {position_relation} p
        LEFT JOIN instruments i ON i.conid = p.conid
        LEFT JOIN instrument_classifications ic
          ON ic.conid = p.conid AND ic.provider = 'Yahoo Finance search profile'
        WHERE p.{position_subject_column} = ? AND p.snapshot_date BETWEEN ? AND ?
        ORDER BY p.snapshot_date, p.conid
        """.format(
            position_relation=position_relation,
            position_subject_column=position_subject_column,
        ),
        (subject_id, range_start, period_end),
    ).fetchall()
    positions: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    instrument_meta: dict[str, dict[str, str]] = {}
    converted_currencies: set[str] = set()
    missing_position_fx: set[str] = set()
    for row in position_rows:
        row_currency = str(row["currency"] or currency).upper()
        quantity = _number(row["quantity_text"])
        price = _number(row["mark_price_text"])
        if quantity is None or price is None:
            continue
        day = str(row["snapshot_date"])
        rate = fx.rate(row_currency, currency, day)
        if rate is None:
            missing_position_fx.add(row_currency)
            continue
        if row_currency != currency:
            converted_currencies.add(row_currency)
        base_price = price * rate
        conid = str(row["conid"])
        existing = positions[day].get(conid)
        if existing is None:
            positions[day][conid] = {"quantity": quantity, "price": base_price}
        else:
            existing["quantity"] += quantity
            existing["price"] = base_price
        instrument_meta[conid] = {
            "symbol": str(row["symbol"] or conid),
            "description": str(row["description"] or ""),
            "sector": str(row["sector"] or "Unclassified"),
        }

    # Portfolio return must cover every validated NAV interval, even when a
    # corresponding position snapshot is absent. Never infer those positions:
    # unmatched effects remain explicitly in the reconciliation residual.
    dates = sorted(nav_by_date)
    missing_position_dates = [day for day in dates if not positions.get(day)]
    # Deposits and withdrawals are not performance: use the website's daily
    # Modified Dietz return and weight contributions on the same denominator.
    from .provisional_benchmark import _external_flows

    flows = _external_flows(
        connection,
        subject_id,
        subject_type="account" if subject_type == "physical_account" else subject_type,
    )
    daily: list[dict[str, Any]] = []
    for prior_day, current_day in zip(dates, dates[1:]):
        if current_day < month_start:
            continue
        prior_nav = nav_by_date[prior_day]
        current_nav = nav_by_date[current_day]
        if prior_nav is None or current_nav is None or prior_nav <= 0:
            continue
        external_flow = sum(
            amount for day, amount in flows.items() if prior_day < day <= current_day
        )
        denominator = prior_nav + 0.5 * external_flow
        if denominator <= 0:
            continue
        portfolio_return = (current_nav - prior_nav - external_flow) / denominator
        contributions: dict[str, float] = {}
        for conid, prior_position in positions[prior_day].items():
            current_position = positions[current_day].get(conid)
            if current_position is None:
                continue
            contributions[conid] = (
                prior_position["quantity"]
                * (current_position["price"] - prior_position["price"])
                / denominator
            )
        residual = portfolio_return - sum(contributions.values())
        daily.append(
            {
                "date": current_day,
                "portfolio_return": portfolio_return,
                "security_contributions": contributions,
                "residual_contribution": residual,
            }
        )

    if not daily or not any(item["security_contributions"] for item in daily):
        return {
            "tool_name": "get_monthly_reporting_analytics",
            "quality_status": "fail",
            "message": "Insufficient common daily NAV and position observations for monthly attribution.",
            "data": {
                "available": False,
                "period_start": month_start,
                "period_end": period_end,
                "method_version": PHYSICAL_METHOD_VERSION,
            },
            "citations": (
                {"table": "subject_nav_series", "subject_id": subject_id},
                {"table": "subject_position_series", "subject_id": subject_id},
            ),
        }

    linked_security: dict[str, float] = defaultdict(float)
    linked_residual = 0.0
    future_growth = 1.0
    for item in reversed(daily):
        for conid, value in item["security_contributions"].items():
            linked_security[conid] += value * future_growth
        linked_residual += item["residual_contribution"] * future_growth
        future_growth *= 1.0 + item["portfolio_return"]
    portfolio_monthly_return = future_growth - 1.0

    security_rows = []
    sector_values: dict[str, float] = defaultdict(float)
    for conid, contribution in linked_security.items():
        meta = instrument_meta.get(
            conid,
            {"symbol": conid, "description": "", "sector": "Unclassified"},
        )
        security_rows.append(
            {
                "conid": conid,
                "symbol": meta["symbol"],
                "description": meta["description"],
                "sector": meta["sector"],
                "contribution": contribution,
            }
        )
        sector_values[meta["sector"]] += contribution
    security_rows.sort(key=lambda row: float(row["contribution"]))
    sector_rows = [
        {"sector": sector, "contribution": contribution}
        for sector, contribution in sorted(
            sector_values.items(), key=lambda item: item[1]
        )
    ]

    cash_relation = "cash_transactions" if legacy_sqlite else "subject_cash_activity_series"
    cash_subject_column = "account_id" if legacy_sqlite else "subject_id"
    cash_included_filter = "" if legacy_sqlite else "AND c.included"
    cash_rows = connection.execute(
        """
        SELECT c.effective_date, c.transaction_type, c.description,
               c.amount_text, c.currency, c.conid, i.symbol
        FROM {cash_relation} c
        LEFT JOIN instruments i ON i.conid = c.conid
        WHERE c.{cash_subject_column} = ? {cash_included_filter}
          AND c.effective_date BETWEEN ? AND ?
        ORDER BY c.effective_date, c.source_key
        """.format(
            cash_relation=cash_relation,
            cash_subject_column=cash_subject_column,
            cash_included_filter=cash_included_filter,
        ),
        (subject_id, month_start, period_end),
    ).fetchall()
    cash_totals: dict[str, float] = defaultdict(float)
    dividends: dict[str, float] = defaultdict(float)
    missing_cash_fx: set[str] = set()
    for row in cash_rows:
        amount = _number(row["amount_text"])
        if amount is None or _external_cash_flow(row["transaction_type"], row["description"]):
            continue
        row_currency = str(row["currency"] or currency).upper()
        rate = fx.rate(row_currency, currency, str(row["effective_date"]))
        if rate is None:
            missing_cash_fx.add(row_currency)
            continue
        if row_currency != currency:
            converted_currencies.add(row_currency)
        amount *= rate
        bucket = _cash_bucket(row["transaction_type"], row["description"], amount)
        cash_totals[bucket] += amount
        if bucket == "dividend":
            label = str(row["symbol"] or row["description"] or "Dividend")
            dividends[label] += amount
    dividend_rows = [
        {"symbol": symbol, "amount": amount}
        for symbol, amount in sorted(
            dividends.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    net_listed_cash = sum(cash_totals.values())

    message = (
        "Monthly security and sector contributions use prior-position mark-to-market "
        "effects linked across daily observations. Residual contribution reconciles "
        "the result to compounded NAV return. Every validated NAV interval is included; "
        "intervals without matched positions contribute only to the residual."
    )
    if converted_currencies:
        message += " Converted to reporting currency using non-forward-looking Flex FX rates: " + ", ".join(sorted(converted_currencies)) + "."
    missing_fx = sorted(missing_position_fx | missing_cash_fx)
    if missing_fx:
        message += " Missing required FX rates: " + ", ".join(missing_fx) + "."
    return {
        "tool_name": "get_monthly_reporting_analytics",
        "quality_status": "fail" if missing_fx else "pass",
        "message": message,
        "data": {
            "available": True,
            "period_start": daily[0]["date"],
            "period_end": daily[-1]["date"],
            "reporting_currency": currency,
            "method_version": PHYSICAL_METHOD_VERSION,
            "daily_observations": len(daily),
            "position_coverage": {
                "missing_position_dates": missing_position_dates,
                "residual_only_intervals": [
                    item["date"] for item in daily if not item["security_contributions"]
                ],
                "matched_position_intervals": sum(
                    bool(item["security_contributions"]) for item in daily
                ),
                "nav_intervals": len(daily),
            },
            "portfolio_monthly_return": portfolio_monthly_return,
            "equity_contribution": sum(linked_security.values()),
            "residual_contribution": linked_residual,
            "fx_conversion": {
                "method": "latest non-forward-looking direct or reciprocal Flex FX rate",
                "converted_currencies": sorted(converted_currencies),
                "missing_currencies": missing_fx,
            },
            "security_contributions": security_rows,
            "sector_contributions": sector_rows,
            "cash_activity": {
                "dividends_by_symbol": dividend_rows,
                "total_dividends": cash_totals["dividend"],
                "interest_received": cash_totals["interest_received"],
                "interest_paid": cash_totals["interest_paid"],
                "fees_and_withholding": cash_totals["fees_and_withholding"],
                "other_internal_cash": cash_totals["other_internal_cash"],
                "net_listed_cash_activity": net_listed_cash,
            },
        },
        "citations": (
            {
                "table": "subject_nav_series",
                "subject_id": subject_id,
                "through": daily[-1]["date"],
            },
            {
                "table": "subject_position_series",
                "subject_id": subject_id,
                "through": daily[-1]["date"],
            },
            {
                "table": "subject_cash_activity_series",
                "subject_id": subject_id,
                "period_start": month_start,
                "period_end": period_end,
            },
            {"table": "instrument_classifications"},
        ),
    }
