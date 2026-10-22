"""The website's read-only live view of IBKR data in the live_ tables (migration 063).

The TWS gateway that was meant to fill those tables was retired before it was
ever deployed (docs/LIVE_IBKR_DATA.md), so the view shows nothing until a new
source writes them through live_store.LiveStore.

Access follows the per-account ``live_view`` switch from migration 063:
'preview' shows live data to owners and platform admins only, and 'active'
to everyone who may already view the account. The Flex statement stays the
book of record; live figures are labelled as such and are never mixed into
the As-of pages.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Mapping

from .live_store import markets_open

LIVE_FEATURE = "live_view"
PREVIEW_ROLES = frozenset({"owner", "platform_admin"})
# The collector writes a status row every minute; two missed minutes and a
# bit more means it is not running.
HEARTBEAT_LIMIT = timedelta(minutes=5)
# Snapshots come every minute while a market is open and every five minutes
# otherwise; three missed snapshots is behind.
SNAPSHOT_LIMIT_OPEN = timedelta(minutes=3)
SNAPSHOT_LIMIT_CLOSED = timedelta(minutes=15)
POLL_SECONDS_OPEN = 30
POLL_SECONDS_CLOSED = 300
TRADE_WINDOW = timedelta(hours=24)
MAX_TRADES = 200


def live_view_allowed(access: Mapping[str, Any], user: Mapping[str, Any]) -> bool:
    """Whether this person sees live data for an account they may already view."""
    if user.get("authorization_source") != "database":
        return False  # the legacy allow-list and the outage fallback never get live data
    if access.get("preview_mode"):
        return False
    if (access.get("subject_type") or "physical_account") != "physical_account":
        return False
    state = (access.get("feature_states") or {}).get(LIVE_FEATURE)
    if state == "active":
        return True
    roles = {str(role) for role in user.get("roles") or ()} | {str(user.get("role", ""))}
    return state == "preview" and bool(roles & PREVIEW_ROLES)


def _number(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds") if value else None


def freshness(
    now: datetime,
    status: Mapping[str, Any] | None,
    snapshot_at: datetime | None,
    delayed_positions: int = 0,
) -> dict[str, Any]:
    """Live, delayed, behind or paused, and how often the page should ask again."""
    is_open = markets_open(now)
    poll_seconds = POLL_SECONDS_OPEN if is_open else POLL_SECONDS_CLOSED
    if status is None or snapshot_at is None:
        state = "unavailable"
    elif now - status["observed_at"] > HEARTBEAT_LIMIT or status["gateway_state"] != "connected":
        state = "paused"
    elif now - snapshot_at > (SNAPSHOT_LIMIT_OPEN if is_open else SNAPSHOT_LIMIT_CLOSED):
        state = "behind"
    elif delayed_positions:
        state = "delayed"
    else:
        state = "live"
    return {"state": state, "markets_open": is_open, "poll_seconds": poll_seconds}


def read_summary(connection, tenant_id: str, account_id: str, now: datetime) -> dict[str, Any]:
    status = connection.execute(
        """
        SELECT observed_at, gateway_state, market_data_type
        FROM live_gateway_status
        WHERE tenant_id = %s
        ORDER BY observed_at DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    account = connection.execute(
        """
        SELECT observed_at, base_currency, net_liquidation, total_cash, daily_pnl,
               unrealized_pnl, realized_pnl, cash_by_currency
        FROM subject_live_summary
        WHERE tenant_id = %s AND subject_id = %s
        """,
        (tenant_id, account_id),
    ).fetchone()
    delayed = connection.execute(
        """
        SELECT count(*) AS delayed
        FROM subject_live_positions
        WHERE tenant_id = %s AND subject_id = %s AND market_data_type IN (3, 4)
        """,
        (tenant_id, account_id),
    ).fetchone()
    delayed_positions = int(delayed["delayed"]) if delayed else 0
    snapshot_at = account["observed_at"] if account else None
    payload = {
        "account_id": account_id,
        **freshness(now, status, snapshot_at, delayed_positions),
        "observed_at": _iso(snapshot_at),
        "gateway_observed_at": _iso(status["observed_at"]) if status else None,
        "delayed_positions": delayed_positions,
        "account": None,
    }
    if account:
        cash = account["cash_by_currency"] or {}
        payload["account"] = {
            "base_currency": account["base_currency"],
            "net_liquidation": _number(account["net_liquidation"]),
            "total_cash": _number(account["total_cash"]),
            "daily_pnl": _number(account["daily_pnl"]),
            "unrealized_pnl": _number(account["unrealized_pnl"]),
            "realized_pnl": _number(account["realized_pnl"]),
            "cash_by_currency": [
                {"currency": currency, "balance": _number(cash[currency])} for currency in sorted(cash)
            ],
        }
    return payload


def read_positions(connection, tenant_id: str, account_id: str) -> dict[str, Any]:
    rows = connection.execute(
        """
        SELECT observed_at, conid, symbol, description, sec_type, currency, quantity,
               average_cost, market_price, market_value, unrealized_pnl, daily_pnl,
               last_price, prior_close, market_data_type, quote_observed_at
        FROM subject_live_positions
        WHERE tenant_id = %s AND subject_id = %s
        ORDER BY abs(market_value) DESC NULLS LAST, symbol
        """,
        (tenant_id, account_id),
    ).fetchall()
    positions = []
    for row in rows:
        price = _number(row["market_price"])
        prior_close = _number(row["prior_close"])
        positions.append({
            "conid": row["conid"],
            "symbol": row["symbol"],
            "description": row["description"],
            "sec_type": row["sec_type"],
            "currency": row["currency"],
            "quantity": _number(row["quantity"]),
            "average_cost": _number(row["average_cost"]),
            "price": price,
            "change_pct": (
                price / prior_close - 1 if price is not None and prior_close else None
            ),
            "market_value": _number(row["market_value"]),
            "unrealized_pnl": _number(row["unrealized_pnl"]),
            "daily_pnl": _number(row["daily_pnl"]),
            "price_source": (
                "none" if price is None
                else "delayed" if row["market_data_type"] in (3, 4)
                else "frozen" if row["market_data_type"] == 2
                else "live"
            ),
            "quote_observed_at": _iso(row["quote_observed_at"]),
        })
    return {
        "account_id": account_id,
        "observed_at": _iso(rows[0]["observed_at"]) if rows else None,
        "positions": positions,
    }


def read_trades(connection, tenant_id: str, account_id: str, now: datetime) -> dict[str, Any]:
    """Executions from the last 24 hours; 'confirmed' once the Flex import has the same execution."""
    rows = connection.execute(
        """
        SELECT e.exec_id, e.executed_at, e.symbol, e.sec_type, e.currency, e.side, e.quantity,
               e.price, e.commission, e.commission_currency, e.realized_pnl,
               EXISTS (
                   SELECT 1 FROM trades t
                   WHERE t.account_id = e.account_id AND t.execution_id = e.exec_id
               ) AS confirmed
        FROM live_executions e
        WHERE e.tenant_id = %s AND e.account_id = %s AND e.executed_at >= %s
        ORDER BY e.executed_at DESC
        LIMIT %s
        """,
        (tenant_id, account_id, now - TRADE_WINDOW, MAX_TRADES),
    ).fetchall()
    return {
        "account_id": account_id,
        "since": _iso(now - TRADE_WINDOW),
        "trades": [
            {
                "exec_id": row["exec_id"],
                "executed_at": _iso(row["executed_at"]),
                "symbol": row["symbol"],
                "sec_type": row["sec_type"],
                "currency": row["currency"],
                "side": row["side"],
                "quantity": _number(row["quantity"]),
                "price": _number(row["price"]),
                "commission": _number(row["commission"]),
                "commission_currency": row["commission_currency"],
                "realized_pnl": _number(row["realized_pnl"]),
                "confirmed": bool(row["confirmed"]),
            }
            for row in rows
        ],
    }
