"""Daily Kiwoom collector: loads a Kiwoom account into the platform tables, behind KIWOOM_COLLECTOR_ENABLED.

For each Kiwoom connection the collector:

  - checks the connection with one valuation call and records whether it
    works on the Broker connections page;
  - for an account with an open onboarding case (or already live), writes
    today's holdings, cash, Kiwoom's own total and the USD/KRW rate, and every
    trade and cash movement since the case's history start, into the same
    tables the IBKR Flex loader fills, under the platform account id
    ``KIWOOM-<account number>``;
  - records the case's request check and its four stage 2 checks itself, from
    the stored figures, because Kiwoom data needs no model to reconcile:
    holdings plus cash must equal Kiwoom's estimated assets to the won.

Securities are keyed through the security master: a KRX code already mapped
to an IBKR conid uses that conid, so the same stock held at both brokers adds
up; a new one gets ``KRX:<code>`` (``US:<ticker>`` for US stocks).

Kiwoom's movement labels are Korean. Only the labels in ``_KINDS`` are read;
any other label fails the transactions check with the label shown, instead of
being guessed into a trade or a cash amount.

From Load and reconcile on, each run also fills the trading days from the
history start that have no snapshot yet, from Kiwoom's per-day
balance (ka01690, and ust21132 for each US currency's value); once the case
is in Build history it records the case's history_built check. A long history is filled over several runs, at
most ``HISTORY_DAYS_PER_RUN`` days at a time. Kiwoom's past-day answer has no
per-holding US detail, so US stocks count in a past day's total and cash but
are listed as holdings only from the first collected day.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Mapping, Sequence

from .kiwoom_client import MAX_SELLING_COST_RATE, AccountValuation, CashMovement, Holding, KiwoomClient, \
    KiwoomError, UsValuation, reconcile_us_valuation, reconcile_valuation
from .security_master import SecurityIdentifier, new_security_key

SWITCH_NAME = "KIWOOM_COLLECTOR_ENABLED"
SOURCE = "kiwoom_rest"
BASE_CURRENCY = "KRW"
SECRET_PREFIX = "broker-kiwoom-rest-"
# Kiwoom's history inquiry is asked in windows no longer than this.
TRANSACTION_WINDOW_DAYS = 90
# Stages in which the collector loads an account. Live accounts keep loading every day.
LOADING_STAGES = ("request", "load_and_reconcile", "build_history", "first_month_end", "closed")
# Stages in which past days are filled. Filling starts with Load and reconcile, so the
# history is in place before approval 1; the history_built check is recorded only in Build history.
HISTORY_STAGES = ("load_and_reconcile", "build_history")
# Past days filled per run, so one run stays well inside the daily job's time limit.
HISTORY_DAYS_PER_RUN = 250


def collector_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """The collector stays off unless the switch is set explicitly."""

    source = os.environ if environ is None else environ
    return source.get(SWITCH_NAME, "").strip().lower() == "true"


def platform_account_id(broker_account_id: str) -> str:
    """The id the platform tables use; the same as the onboarding case's subject."""

    return f"KIWOOM-{broker_account_id}"


# Movement labels ------------------------------------------------------------------
# (label fragment, kind, direction). Checked in order, so the longer and more
# specific fragments come first. Direction is the sign of the cash effect.

_KINDS: tuple[tuple[str, str, int], ...] = (
    ("매수", "BUY", -1),
    ("매도", "SELL", 1),
    ("입고", "TRANSFER_IN", 0),
    ("출고", "TRANSFER_OUT", 0),
    ("배당", "DIVIDEND", 1),
    ("이자", "INTEREST", 1),
    ("세금", "TAX", -1),
    ("수수료", "FEE", -1),
    ("출금", "WITHDRAWAL", -1),
    ("입금", "DEPOSIT", 1),
    ("환전", "FX_EXCHANGE", 0),
)
TRADE_KINDS = {"BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT"}


def classify(movement: CashMovement) -> tuple[str, int] | None:
    """The movement's kind and cash direction, or None for a label the platform does not know."""

    text = f"{movement.kind} {movement.description}"
    for fragment, kind, direction in _KINDS:
        if fragment in text:
            return kind, direction
    return None


# Pure row building ----------------------------------------------------------------


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


@dataclass(frozen=True)
class NewSecurity:
    key: str
    identifier: SecurityIdentifier
    name: str
    currency: str
    exchange: str | None


@dataclass
class Securities:
    """Resolves Kiwoom identifiers to platform security keys, collecting new ones to register."""

    known: dict[SecurityIdentifier, str]
    new: dict[str, NewSecurity] = field(default_factory=dict)

    def key(self, identifier: SecurityIdentifier, *, name: str = "", currency: str = BASE_CURRENCY,
            exchange: str | None = None) -> str:
        found = self.known.get(identifier)
        if found is not None:
            return found
        key = new_security_key([identifier])
        existing = self.new.get(key)
        # A holding names the security; a bare movement only gives its code.
        if existing is None or (name and existing.name == identifier.value):
            self.new[key] = NewSecurity(key, identifier, name or identifier.value, currency, exchange)
        return key


def snapshot_rows(account_id: str, day: date, valuation: AccountValuation, us: UsValuation | None,
                  securities: Securities) -> dict[str, list[dict[str, Any]]]:
    """Today's positions, cash, Kiwoom's total and FX rates as platform rows (text values)."""

    snapshot = day.isoformat()
    positions: list[dict[str, Any]] = []
    instruments: list[dict[str, Any]] = []

    def add(holding: Holding, exchange: str) -> None:
        key = securities.key(holding.security, name=holding.name, currency=holding.currency, exchange=exchange)
        positions.append({
            "account_id": account_id, "snapshot_date": snapshot, "conid": key, "model_code": "",
            "currency": holding.currency, "quantity_text": _text(holding.quantity),
            "cost_basis_text": _text(holding.average_price * holding.quantity),
            "mark_price_text": _text(holding.price), "market_value_text": _text(holding.market_value),
            "unrealized_pnl_text": _text(holding.market_value - holding.average_price * holding.quantity),
        })
        instruments.append({
            "conid": key, "symbol": holding.security.value, "description": holding.name, "asset_category": "STK",
            "currency": holding.currency, "listing_exchange": exchange, "isin": None,
        })

    for holding in valuation.holdings:
        add(holding, "KRX")
    stock_krw = sum((item.market_value for item in valuation.holdings), Decimal("0"))
    cash = [{"account_id": account_id, "snapshot_date": snapshot, "currency": BASE_CURRENCY,
             "ending_cash_text": _text(valuation.deposit),
             "settled_cash_text": _text(valuation.deposit if valuation.settled_deposit is None
                                        else valuation.settled_deposit),
             "fx_rate_to_base_text": "1"}]
    fx_rates: list[dict[str, Any]] = []
    total_krw = valuation.estimated_assets
    cash_krw = valuation.deposit
    # Kiwoom's total is after its estimated selling cost; that cost is kept as a negative accrual,
    # so holdings + cash + accruals add up to Kiwoom's total.
    accruals_krw = -valuation.selling_cost
    if us is not None:
        for holding in us.holdings:
            add(holding, holding.exchange or "US")
        for balance in us.balances:
            if balance.currency == BASE_CURRENCY:
                continue
            rate = balance.krw_rate
            fx_rates.append({"account_id": account_id, "rate_date": snapshot, "from_currency": balance.currency,
                             "to_currency": BASE_CURRENCY, "rate_text": _text(rate)})
            cash.append({"account_id": account_id, "snapshot_date": snapshot, "currency": balance.currency,
                         "ending_cash_text": _text(balance.deposit), "settled_cash_text": _text(balance.deposit),
                         "fx_rate_to_base_text": _text(rate)})
            # Kiwoom's domestic total leaves out the US account, so its KRW value is added here.
            stock_krw += balance.securities_value * rate
            cash_krw += balance.deposit * rate
            total_krw += (balance.securities_value + balance.deposit) * rate
    nav = [{"account_id": account_id, "snapshot_date": snapshot, "currency": BASE_CURRENCY, "asset_class": "TOTAL",
            "total_text": _text(total_krw), "cash_text": _text(cash_krw), "stock_text": _text(stock_krw),
            "options_text": "0", "futures_text": "0", "bonds_text": "0", "accruals_text": _text(accruals_krw)}]
    return {"position_snapshots": positions, "cash_snapshots": cash, "nav_snapshots": nav,
            "fx_rates": fx_rates, "instruments": instruments}


def movement_rows(account_id: str, movements: Iterable[CashMovement],
                  securities: Securities) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """Trades and cash movements as platform rows, and the labels the platform could not read."""

    trades: list[dict[str, Any]] = []
    cash: list[dict[str, Any]] = []
    unknown: list[str] = []
    seen: Counter = Counter()
    for movement in movements:
        fields = [movement.trade_date, movement.kind, movement.description,
                  movement.security.value if movement.security else "", str(movement.quantity),
                  str(movement.amount), movement.currency]
        # Two identical movements on one day are both real; the occurrence keeps both.
        seen[tuple(fields)] += 1
        source_key = _sha([account_id, *fields, seen[tuple(fields)]])[:40]
        found = classify(movement)
        if found is None:
            unknown.append(f"{movement.trade_date} {movement.kind or movement.description or 'no label'}")
            continue
        kind, direction = found
        amount = abs(movement.amount) * direction if direction else movement.amount
        key = (securities.key(movement.security, currency=movement.currency if movement.security.scheme != "krx_code"
                              else BASE_CURRENCY)
               if movement.security is not None else None)
        if kind in TRADE_KINDS and key is not None:
            quantity = abs(movement.quantity) * (-1 if kind in {"SELL", "TRANSFER_OUT"} else 1)
            trades.append({
                "account_id": account_id, "source_key": source_key, "execution_id": None, "trade_id": source_key,
                "conid": key, "trade_date": movement.trade_date, "trade_time": None,
                "settle_date": None, "buy_sell": kind, "quantity_text": _text(quantity),
                "trade_price_text": _text(abs(movement.amount) / abs(movement.quantity)) if movement.quantity else None,
                "proceeds_text": _text(amount), "commission_text": None, "commission_currency": movement.currency,
                "net_cash_text": _text(amount), "realized_pnl_text": None, "currency": movement.currency,
                "order_reference": movement.description[:200] or None, "model_code": "",
            })
        else:
            cash.append({
                "account_id": account_id, "source_key": source_key, "transaction_id": source_key,
                "event_datetime": movement.trade_date, "model_code": "", "effective_date": movement.trade_date,
                "settle_date": None, "transaction_type": kind, "description": movement.description[:500] or kind,
                "amount_text": _text(amount), "currency": movement.currency, "conid": key,
            })
    return {"trades": trades, "cash_transactions": cash}, unknown


def windows(start: date, end: date, days: int = TRANSACTION_WINDOW_DAYS) -> list[tuple[date, date]]:
    """Consecutive date windows covering start to end, each at most ``days`` long."""

    spans = []
    while start <= end:
        stop = min(end, start + timedelta(days=days - 1))
        spans.append((start, stop))
        start = stop + timedelta(days=1)
    return spans


# Stage checks ----------------------------------------------------------------------


@dataclass(frozen=True)
class Collected:
    account_id: str
    day: date
    valuation: AccountValuation
    us: UsValuation | None
    movements: tuple[CashMovement, ...]
    rows: Mapping[str, list[dict[str, Any]]]
    unknown_labels: tuple[str, ...]
    new_securities: tuple[NewSecurity, ...]


def stage_two_checks(collected: Collected, *, history_start: date) -> dict[str, dict[str, Any]]:
    """The four load-and-reconcile checks, worked out from what was just collected."""

    holdings = len(collected.rows["position_snapshots"])
    checks: dict[str, dict[str, Any]] = {
        "holdings_and_cash_loaded": {
            "status": "pass",
            "message": f"{holdings} holdings and cash loaded for {collected.day.isoformat()}.",
            "evidence": {"date": collected.day.isoformat(), "holdings": holdings,
                         "currencies": sorted({row["currency"] for row in collected.rows["cash_snapshots"]})},
        },
    }
    trades, cash = len(collected.rows["trades"]), len(collected.rows["cash_transactions"])
    evidence = {"from": history_start.isoformat(), "to": collected.day.isoformat(), "trades": trades,
                "cash_movements": cash, "unknown_labels": list(collected.unknown_labels[:20])}
    if collected.unknown_labels:
        checks["transactions_loaded"] = {
            "status": "fail", "evidence": evidence,
            "message": f"{len(collected.unknown_labels)} Kiwoom movements have a label the platform does not read "
                       f"yet, for example {collected.unknown_labels[0]}. Nothing was guessed.",
        }
    elif trades + cash == 0 and holdings:
        checks["transactions_loaded"] = {
            "status": "warn", "evidence": evidence,
            "message": "Kiwoom returned no trades or cash movements since the history start, but the account "
                       "holds securities. Check the history start date.",
        }
    else:
        checks["transactions_loaded"] = {
            "status": "pass", "evidence": evidence,
            "message": f"{trades} trades and {cash} cash movements loaded from {history_start.isoformat()}.",
        }
    new = [item.key for item in collected.new_securities]
    checks["securities_mapped"] = {
        "status": "pass", "evidence": {"new_securities": new},
        "message": "Every security is identified by its KRX code or US ticker"
                   + (f"; {len(new)} were new to the security master and are added." if new else "."),
    }
    domestic = reconcile_valuation(collected.valuation)
    if collected.us is not None:
        us = reconcile_us_valuation(collected.us)
        failed = [item for item in (domestic, us) if item["status"] != "pass"]
        checks["nav_reconciled"] = {
            "status": "fail" if failed else "pass",
            "message": " ".join(item["message"] for item in (failed or [domestic, us])),
            "evidence": {"korea": domestic["evidence"], "us": us["evidence"]},
        }
    else:
        checks["nav_reconciled"] = dict(domestic)
    return checks


# Collection ------------------------------------------------------------------------


def collect(client: KiwoomClient, *, broker_account_id: str, history_start: date, today: date,
            known: Mapping[SecurityIdentifier, str], include_us: bool = True) -> Collected:
    """Every Kiwoom call for one account, turned into platform rows. Writes nothing."""

    account_id = platform_account_id(broker_account_id)
    valuation = client.valuation()
    us = client.us_valuation() if include_us else None
    movements: list[CashMovement] = []
    for start, end in windows(history_start, today):
        movements.extend(client.transactions(start, end))
        if include_us:
            movements.extend(client.us_transactions(start, end))
    securities = Securities(known=dict(known))
    rows = snapshot_rows(account_id, today, valuation, us, securities)
    moved, unknown = movement_rows(account_id, movements, securities)
    rows.update(moved)
    return Collected(account_id=account_id, day=today, valuation=valuation, us=us, movements=tuple(movements),
                     rows=rows, unknown_labels=tuple(unknown),
                     new_securities=tuple(securities.new[key] for key in sorted(securities.new)))


# Database --------------------------------------------------------------------------

_KEYS = {
    "position_snapshots": ("account_id", "snapshot_date", "conid", "model_code"),
    "cash_snapshots": ("account_id", "snapshot_date", "currency"),
    "nav_snapshots": ("account_id", "snapshot_date", "currency", "asset_class"),
    "fx_rates": ("account_id", "rate_date", "from_currency", "to_currency"),
    "trades": ("account_id", "source_key"),
    "cash_transactions": ("account_id", "source_key"),
}


def known_identifiers(connection) -> dict[SecurityIdentifier, str]:
    rows = connection.execute(
        "SELECT scheme, identifier, security_key FROM security_identifiers WHERE scheme IN ('krx_code', 'us_ticker')"
    ).fetchall()
    return {SecurityIdentifier(str(row["scheme"]), str(row["identifier"])): str(row["security_key"]) for row in rows}


def _upsert(connection, table: str, rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> int:
    for row in rows:
        columns = list(row)
        updates = [column for column in columns if column not in keys]
        connection.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT ({', '.join(keys)}) DO UPDATE SET "
            + ", ".join(f"{column} = EXCLUDED.{column}" for column in updates),
            [row[column] for column in columns],
        )
    return len(rows)


def _register(connection, new_securities: Iterable[NewSecurity], instruments: Iterable[Mapping[str, Any]],
              stamp: str) -> None:
    for item in new_securities:
        connection.execute(
            "INSERT INTO security_master (security_key, display_name, asset_class, currency, primary_exchange, "
            "status) VALUES (?, ?, 'STK', ?, ?, 'active') ON CONFLICT (security_key) DO NOTHING",
            (item.key, item.name[:200], item.currency, item.exchange),
        )
        connection.execute(
            "INSERT INTO security_identifiers (scheme, identifier, security_key, source) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (scheme, identifier) DO NOTHING",
            (item.identifier.scheme, item.identifier.value, item.key, SOURCE),
        )
    for row in instruments:
        connection.execute(
            "INSERT INTO instruments (conid, symbol, description, asset_category, currency, listing_exchange, isin, "
            "first_seen_at, last_seen_at, source_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (conid) "
            "DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at",
            (row["conid"], row["symbol"], row["description"], row["asset_category"], row["currency"],
             row["listing_exchange"], row["isin"], stamp, stamp, json.dumps({"source": SOURCE})),
        )


def _clear_day(connection, account_id: str, day: date, fx_rates: Sequence[Mapping[str, Any]] = ()) -> None:
    # A day's snapshot is replaced whole, so a holding sold since the last run of the day disappears.
    for table in ("position_snapshots", "cash_snapshots"):
        connection.execute(f"DELETE FROM {table} WHERE account_id = ? AND snapshot_date = ?",
                           (account_id, day.isoformat()))
    # FX rates are upserted, and only a rate no longer returned is removed, so an unchanged
    # rate keeps its row (and its silver history) when a day is collected again.
    kept = sorted({str(row["from_currency"]) for row in fx_rates})
    if kept:
        marks = ", ".join("?" for _ in kept)
        connection.execute(f"DELETE FROM fx_rates WHERE account_id = ? AND rate_date = ? "
                           f"AND from_currency NOT IN ({marks})", (account_id, day.isoformat(), *kept))
    else:
        connection.execute("DELETE FROM fx_rates WHERE account_id = ? AND rate_date = ?",
                           (account_id, day.isoformat()))


def store(connection, collected: Collected, *, display_name: str, now: datetime) -> dict[str, int]:
    """Write one collection in the caller's transaction. Re-running a day replaces that day's rows."""

    stamp = now.astimezone(timezone.utc).isoformat()
    payload = {table: rows for table, rows in collected.rows.items()}
    run_id = connection.execute(
        "INSERT INTO ingestion_runs (run_type, requested_from, requested_to, started_at, completed_at, status, "
        "raw_sha256, row_count) VALUES (?, ?, ?, ?, ?, 'success', ?, ?) RETURNING run_id",
        ("kiwoom_rest", min((m.trade_date for m in collected.movements), default=collected.day.isoformat()),
         collected.day.isoformat(), stamp, stamp, _sha(payload), sum(len(rows) for rows in payload.values())),
    ).fetchone()["run_id"]
    connection.execute(
        "INSERT INTO accounts (account_id, alias, base_currency, account_type, ib_entity, first_seen_at, "
        "last_seen_at, source_json) VALUES (?, ?, ?, 'kiwoom', NULL, ?, ?, ?) ON CONFLICT (account_id) "
        "DO UPDATE SET alias = EXCLUDED.alias, last_seen_at = EXCLUDED.last_seen_at",
        (collected.account_id, display_name, BASE_CURRENCY, stamp, stamp,
         json.dumps({"source": SOURCE, "account_name": collected.valuation.account_name}, ensure_ascii=False)),
    )
    _register(connection, collected.new_securities, collected.rows["instruments"], stamp)
    _clear_day(connection, collected.account_id, collected.day, collected.rows["fx_rates"])
    counts: dict[str, int] = {}
    for table, keys in _KEYS.items():
        rows = [{**row, "row_sha256": _sha(row), "last_seen_run_id": run_id} for row in collected.rows[table]]
        counts[table] = _upsert(connection, table, rows, keys)
    return counts


# History build ---------------------------------------------------------------------


def trading_weekdays(start: date, end: date) -> list[date]:
    """Monday to Friday from start to end inclusive; KRX holidays are found by asking Kiwoom."""

    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def days_with_snapshots(connection, account_id: str, start: date, end: date) -> set[date]:
    """Stored days that reconcile on the current basis (holdings + cash + accruals = Kiwoom's total).

    A day stored on an earlier basis (cash as the total less the holdings, the
    selling cost not kept apart) has its KRW cash differ from the settled cash;
    leaving it out here has the history fill it again.
    """

    rows = connection.execute(
        "SELECT n.snapshot_date, n.total_text, n.cash_text, n.stock_text, n.accruals_text, "
        "c.ending_cash_text, c.settled_cash_text FROM nav_snapshots n LEFT JOIN cash_snapshots c "
        "ON c.account_id = n.account_id AND c.snapshot_date = n.snapshot_date AND c.currency = ? "
        "WHERE n.account_id = ? AND n.asset_class = 'TOTAL' AND n.snapshot_date BETWEEN ? AND ?",
        (BASE_CURRENCY, account_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    return {date.fromisoformat(str(row["snapshot_date"])[:10]) for row in rows
            if abs(_nav_gap(row)) <= Decimal("1") and row["ending_cash_text"] is not None
            and row["ending_cash_text"] == row["settled_cash_text"]}


def _number(value) -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal("0")


def _nav_gap(row) -> Decimal:
    return (_number(row["total_text"]) - _number(row["cash_text"]) - _number(row["stock_text"])
            - _number(row["accruals_text"]))


def history_issues(connection, account_id: str, start: date, end: date) -> list[dict[str, str]]:
    """Stored days that do not reconcile, read from the stored rows so earlier runs' days count too.

    Two checks a day:
    - total: holdings + cash + accruals equal Kiwoom's total, to 1 KRW;
    - selling_cost: the cost Kiwoom's total leaves out (the negative accrual)
      is between nothing and MAX_SELLING_COST_RATE of the holdings. Anything
      else in or missing from Kiwoom's total shows here.
    """

    rows = connection.execute(
        "SELECT snapshot_date, total_text, cash_text, stock_text, accruals_text FROM nav_snapshots "
        "WHERE account_id = ? AND asset_class = 'TOTAL' AND snapshot_date BETWEEN ? AND ? ORDER BY snapshot_date",
        (account_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    issues: list[dict[str, str]] = []
    for row in rows:
        day = str(row["snapshot_date"])[:10]
        gap = _nav_gap(row)
        if abs(gap) > Decimal("1"):
            issues.append({"date": day, "check": "total", "gap": _text(gap)})
            continue
        stock = _number(row["stock_text"])
        cost = -_number(row["accruals_text"])
        allowed = stock * MAX_SELLING_COST_RATE
        if cost < Decimal("-1") or cost > allowed + Decimal("1"):
            issues.append({"date": day, "check": "selling_cost", "gap": _text(cost),
                           "share_of_holdings": f"{(cost / stock * 100) if stock else Decimal(0):.4f}%"})
    return issues


def build_history(connection, client: KiwoomClient, *, broker_account_id: str, history_start: date, today: date,
                  include_us: bool, now: datetime, limit: int = HISTORY_DAYS_PER_RUN) -> dict[str, Any]:
    """Fill past trading days that have no snapshot, in the caller's transaction, and say what is left.

    Returns the history_built check: pending while days remain, then pass, or
    warn when some stored day does not reconcile (see history_issues).
    """

    account_id = platform_account_id(broker_account_id)
    end = today - timedelta(days=1)
    have = days_with_snapshots(connection, account_id, history_start, end)
    missing = [day for day in trading_weekdays(history_start, end) if day not in have]
    batch, remaining = missing[:limit], missing[limit:]
    stamp = now.astimezone(timezone.utc).isoformat()
    securities = Securities(known=known_identifiers(connection))
    loaded: list[date] = []
    closed: list[date] = []
    written: list[dict[str, list[dict[str, Any]]]] = []
    for day in batch:
        valuation = client.balance_on(day)
        if valuation is None:
            closed.append(day)
            continue
        us = UsValuation(holdings=(), balances=client.us_balances_on(day)) if include_us else None
        rows = snapshot_rows(account_id, day, valuation, us, securities)
        written.append(rows)
        loaded.append(day)
    if loaded:
        payload = {"days": [day.isoformat() for day in loaded], "rows": written}
        run_id = connection.execute(
            "INSERT INTO ingestion_runs (run_type, requested_from, requested_to, started_at, completed_at, status, "
            "raw_sha256, row_count) VALUES (?, ?, ?, ?, ?, 'success', ?, ?) RETURNING run_id",
            ("kiwoom_rest_history", loaded[0].isoformat(), loaded[-1].isoformat(), stamp, stamp, _sha(payload),
             sum(len(items) for rows in written for items in rows.values())),
        ).fetchone()["run_id"]
        _register(connection, (securities.new[key] for key in sorted(securities.new)),
                  [row for rows in written for row in rows["instruments"]], stamp)
        for day, rows in zip(loaded, written):
            _clear_day(connection, account_id, day, rows["fx_rates"])
            for table in ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates"):
                _upsert(connection, table,
                        [{**row, "row_sha256": _sha(row), "last_seen_run_id": run_id} for row in rows[table]],
                        _KEYS[table])
    gaps = history_issues(connection, account_id, history_start, end)
    evidence: dict[str, Any] = {
        "from": history_start.isoformat(), "to": end.isoformat(), "loaded_this_run": len(loaded),
        "days_with_history": len(have) + len(loaded), "closed_days": [day.isoformat() for day in closed][:40],
        "days_left": len(remaining), "gap_days": len({gap["date"] for gap in gaps}), "gaps": gaps[:20],
        "cash_basis": "trade date (ka01690 dbst_bal); Kiwoom's estimated selling cost kept as accruals",
        "us_cash_basis": "settled" if include_us else "not collected",
        "us_holdings_listed_from_first_collected_day": include_us,
    }
    if remaining:
        return {"status": "pending", "evidence": evidence,
                "message": f"{len(have) + len(loaded)} past days loaded; {len(remaining)} more are filled on the "
                           "next runs."}
    if gaps:
        return {"status": "warn", "evidence": evidence,
                "message": f"Daily history from {history_start.isoformat()} is complete, but "
                           f"{evidence['gap_days']} days do not reconcile, for example {gaps[0]['date']} "
                           f"({gaps[0]['check']}, {gaps[0]['gap']} KRW). Check those days before approving "
                           "the reports."}
    return {"status": "pass", "evidence": evidence,
            "message": f"Daily history from {history_start.isoformat()} is complete: "
                       f"{len(have) + len(loaded)} trading days, each matching Kiwoom's total after its estimated selling cost."}


# The daily run ---------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    connection_id: str
    environment: str
    secret_name: str
    broker_account_id: str
    case_id: str | None
    stage: str | None
    display_name: str
    history_start: date | None


def targets(connection, tenant_id: str) -> list[Target]:
    """Every Kiwoom connection that is not paused, with its account and any open or live case."""

    rows = connection.execute(
        "SELECT b.connection_id, b.environment, b.key_vault_secret_name, a.broker_account_id, c.case_id, c.stage, "
        "COALESCE(c.display_name, a.account_name, b.display_name) AS display_name, c.history_start_date "
        "FROM broker_connections b JOIN broker_connection_accounts a "
        "ON a.tenant_id = b.tenant_id AND a.connection_id = b.connection_id "
        "LEFT JOIN broker_onboarding_cases c ON c.tenant_id = b.tenant_id AND c.connection_id = b.connection_id "
        "AND c.broker_account_id = a.broker_account_id AND c.status <> 'cancelled' "
        "WHERE b.tenant_id = ? AND b.broker = 'kiwoom_rest' AND b.status NOT IN ('suspended', 'retired') "
        "AND a.discovery_status NOT IN ('ignored', 'no_longer_seen') "
        "ORDER BY b.connection_id, a.broker_account_id",
        (tenant_id,),
    ).fetchall()
    found = []
    for row in rows:
        start = row["history_start_date"]
        found.append(Target(
            connection_id=str(row["connection_id"]), environment=str(row["environment"]),
            secret_name=str(row["key_vault_secret_name"]), broker_account_id=str(row["broker_account_id"]),
            case_id=str(row["case_id"]) if row["case_id"] else None, stage=row["stage"],
            display_name=str(row["display_name"]),
            history_start=date.fromisoformat(str(start)[:10]) if start else None,
        ))
    return found


def _connection_result(connection, tenant_id: str, target: Target, *, ok: bool, message: str,
                       account_name: str | None = None) -> None:
    connection.execute(
        "UPDATE broker_connections SET status = ?, last_checked_at = now(), last_check_json = ?::jsonb, "
        "updated_at = now() WHERE tenant_id = ? AND connection_id = ?",
        ("healthy" if ok else "failing", json.dumps({"ok": ok, "message": message[:300], "source": "kiwoom_collector"}),
         tenant_id, target.connection_id),
    )
    if ok:
        connection.execute(
            "UPDATE broker_connection_accounts SET account_name = COALESCE(?, account_name), base_currency = ?, "
            "last_seen_at = now() WHERE tenant_id = ? AND connection_id = ? AND broker_account_id = ?",
            (account_name or None, BASE_CURRENCY, tenant_id, target.connection_id, target.broker_account_id),
        )


def _record(connection, tenant_id: str, case_id: str, check_id: str, check: Mapping[str, Any]) -> str:
    from .onboarding_v2_web import record_platform_check

    outcome = record_platform_check(connection, tenant_id, case_id, check_id, str(check["status"]),
                                    str(check["message"]), dict(check.get("evidence") or {}))
    return str(outcome["stage"])


def _history(connection, tenant_id: str, client: KiwoomClient, target: Target, *, today: date, now: datetime,
             include_us: bool, record: bool = True) -> dict[str, Any]:
    """The history build for one account; a Kiwoom refusal fails the history check, not the day's load.

    Before Build history (``record`` false) the days are filled but no check is recorded.
    """

    assert target.case_id is not None and target.history_start is not None
    previous = connection.execute(
        "SELECT status FROM broker_onboarding_checks WHERE tenant_id = ? AND case_id = ? AND check_id = 'history_built'",
        (tenant_id, target.case_id),
    ).fetchone()
    try:
        check = build_history(connection, client, broker_account_id=target.broker_account_id,
                              history_start=target.history_start, today=today, include_us=include_us, now=now)
    except (KiwoomError, ValueError) as error:
        connection.rollback()
        check = {"status": "fail", "message": f"Kiwoom refused a past day's balance: {str(error)[:300]}",
                 "evidence": {"from": target.history_start.isoformat()}}
    if not record:
        connection.commit()
        return {"check": check["status"], "recorded": False, "loaded": check["evidence"].get("loaded_this_run", 0)}
    # A finished history stays as recorded until something new is loaded.
    if check["status"] == "pass" and previous is not None and previous["status"] == "pass" \
            and not check["evidence"].get("loaded_this_run"):
        connection.commit()
        return {"check": "pass", "recorded": False}
    _record(connection, tenant_id, target.case_id, "history_built", check)
    connection.commit()
    return {"check": check["status"], "recorded": True, "loaded": check["evidence"].get("loaded_this_run", 0)}


def run(connection, tenant_id: str, *, client_for: Callable[[Target], KiwoomClient], today: date,
        now: datetime, include_us: bool = True) -> list[dict[str, Any]]:
    """Collect every Kiwoom account. One account's failure never stops the others."""

    results = []
    for target in targets(connection, tenant_id):
        result: dict[str, Any] = {"connection_id": target.connection_id, "account": target.broker_account_id,
                                  "case_id": target.case_id}
        client = None
        try:
            client = client_for(target)
            if target.case_id is None or target.stage not in LOADING_STAGES or target.history_start is None:
                # Not being onboarded: a connection check only, so the account is never copied in unasked.
                valuation = client.valuation()
                _connection_result(connection, tenant_id, target, ok=True, message="Kiwoom answered.",
                                   account_name=valuation.account_name)
                connection.commit()
                results.append({**result, "status": "checked"})
                continue
            collected = collect(client, broker_account_id=target.broker_account_id,
                                history_start=target.history_start, today=today,
                                known=known_identifiers(connection), include_us=include_us)
            counts = store(connection, collected, display_name=target.display_name, now=now)
            _connection_result(connection, tenant_id, target, ok=True, message="Kiwoom answered and the account loaded.",
                               account_name=collected.valuation.account_name)
            stage = str(target.stage)
            if stage == "request":
                stage = _record(connection, tenant_id, target.case_id, "connection_sees_account", {
                    "status": "pass", "message": "Kiwoom answered from the platform's fixed address for this account.",
                    "evidence": {"date": today.isoformat(), "environment": target.environment},
                })
            if stage == "load_and_reconcile":
                for check_id, check in stage_two_checks(collected, history_start=target.history_start).items():
                    stage = _record(connection, tenant_id, target.case_id, check_id, check)
                    if stage != "load_and_reconcile":
                        break
            connection.commit()
            if stage in HISTORY_STAGES:
                result["history"] = _history(connection, tenant_id, client, target, today=today, now=now,
                                             include_us=include_us, record=stage == "build_history")
            results.append({**result, "status": "loaded", "stage": stage, "rows": counts})
        except Exception as error:  # noqa: BLE001 - one account's failure, of any kind, never stops the run
            connection.rollback()
            message = str(error)[:300] if isinstance(error, (KiwoomError, ValueError)) \
                else f"{type(error).__name__}: {str(error)[:250]}"
            try:
                _connection_result(connection, tenant_id, target, ok=False, message=message)
                connection.commit()
            except Exception:  # noqa: BLE001 - the database itself may be the failure
                connection.rollback()
            results.append({**result, "status": "failed", "error": message})
        finally:
            if client is not None:
                try:
                    client.revoke()
                except KiwoomError:
                    pass
    return results
