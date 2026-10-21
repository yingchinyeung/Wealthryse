"""A Kiwoom account reported in USD: U.S. stocks only, loaded from Kiwoom's U.S. account calls.

The Korea collector (kiwoom_collector) values an account in KRW from the domestic
calls. An account whose onboarding case reports in USD and holds only U.S. stocks
is loaded here instead, in USD, from the U.S. account calls:

  - now: holdings (ust21070) and each currency's cash and securities value
    (ust21120), for the onboarding checks only. Kiwoom prices them at the moment
    of asking, after-hours and overnight trading included, so they are never
    stored as a day's figures;
  - each closed U.S. trading day, the latest one included: USD cash and securities value (ust21132), which
    is Kiwoom's own daily total for the account. Kiwoom's past-day answer has no
    per-holding rows, so a past day's positions are rebuilt from the movements,
    Kiwoom's per-stock daily holding (usa21730) and daily closes (usa06012)
    (UsHistoryPositions), and must add up to that total before the day counts as
    stored;
  - every movement since the history start (ust21100), with the U.S. labels in
    ``US_KINDS``. Anything else fails the transactions check with the label
    shown.

The Korea path is untouched: ``is_usd_account`` decides per account, and an
account in KRW never reaches this module.

Kiwoom dates a past day by its U.S. trading day (ust21132 base_dt); its movement
list (ust21100 deal_dt) is the Korean posting date, one or two U.S. trading days
after the trade. Cash and securities in Kiwoom's daily total are on the trade
date, as is its per-stock daily holding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from .kiwoom_client import CashMovement, CurrencyBalance, Holding, KiwoomClient, KiwoomError, UsValuation, \
    reconcile_us_valuation
from .security_master import SecurityIdentifier

BASE_CURRENCY = "USD"
KRW = "KRW"

# (label fragment, kind, cash direction), checked in order. U.S. labels from Kiwoom's ust21100 on the live
# account: 매수/매도 trades, 배당금(외화)입금 dividends, 감자액면병합 (reverse split or merger, shares out and in),
# 액면분할병합 (split), 유상소각 (paid cancellation: shares out, cash in), 합병단주대금 (merger cash in lieu).
# A corporate action's cash is income to the account, never a deposit: only a flow from outside the account
# may read as one (see derived_portfolio._EXTERNAL_FLOW_PATTERN).
US_KINDS: tuple[tuple[str, str, int], ...] = (
    ("매수", "BUY", -1),
    ("매도", "SELL", 1),
    ("배당", "DIVIDEND", 1),
    ("합병단주대금", "CORPORATE_ACTION_CASH", 1),
    ("유상소각(해외)입금", "CORPORATE_ACTION_CASH", 1),
    ("유상소각출고", "CORPORATE_ACTION_OUT", 0),
    ("감자액면병합출고", "CORPORATE_ACTION_OUT", 0),
    ("감자액면병합입고", "CORPORATE_ACTION_IN", 0),
    ("액면분할병합출고", "CORPORATE_ACTION_OUT", 0),
    ("액면분할병합입고", "CORPORATE_ACTION_IN", 0),
    ("세금", "TAX", -1),
    ("수수료", "FEE", -1),
)
# Movements that change a share count. A corporate action moves shares without cash.
SHARE_KINDS = {"BUY": 1, "SELL": -1, "CORPORATE_ACTION_IN": 1, "CORPORATE_ACTION_OUT": -1}


def is_usd_account(reporting_currency: str | None) -> bool:
    """An onboarding case in USD is loaded from Kiwoom's U.S. account calls."""

    return str(reporting_currency or "").strip().upper() == BASE_CURRENCY


def us_classify(movement: CashMovement) -> tuple[str, int] | None:
    text = f"{movement.kind} {movement.description}"
    for fragment, kind, direction in US_KINDS:
        if fragment in text:
            return kind, direction
    return None


def us_snapshot_day(now: datetime) -> date:
    """The latest U.S. trading day that has closed: today in New York from 17:00 New York time, an hour
    after the 16:00 close (summer or winter), else the last NYSE session before it."""

    from zoneinfo import ZoneInfo

    from .market_calendar import CALENDARS

    local = now.astimezone(ZoneInfo("America/New_York"))
    day = local.date() if local.hour >= 17 else local.date() - timedelta(days=1)
    while not CALENDARS["XNYS"].is_session(day):
        day -= timedelta(days=1)
    return day


def _text(value: Decimal) -> str:
    return format(value.normalize(), "f") if value == value.to_integral() else format(value, "f")


@dataclass(frozen=True)
class UsDay:
    """Kiwoom's own figures for one U.S. trading day, in USD."""

    day: date
    cash: Decimal
    securities_value: Decimal
    krw_rate: Decimal
    krw_cash: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        krw = self.krw_cash / self.krw_rate if self.krw_cash and self.krw_rate else Decimal("0")
        return self.cash + self.securities_value + krw


def usd_balance(balances: Sequence[CurrencyBalance]) -> CurrencyBalance:
    found = [item for item in balances if item.currency == BASE_CURRENCY]
    others = [item.currency for item in balances if item.currency != BASE_CURRENCY
              and (item.deposit or item.securities_value)]
    if len(found) != 1:
        raise KiwoomError("Kiwoom gave no single USD balance for the U.S. account")
    if others:
        raise KiwoomError("The U.S. account holds currencies other than USD: " + ", ".join(sorted(others)))
    return found[0]


def day_from_balances(day: date, balances: Sequence[CurrencyBalance], krw_cash: Decimal = Decimal("0")) -> UsDay:
    usd = usd_balance(balances)
    return UsDay(day=day, cash=usd.deposit, securities_value=usd.securities_value, krw_rate=usd.krw_rate,
                 krw_cash=krw_cash)


def us_rows(account_id: str, figures: UsDay, holdings: Iterable[Holding],
            securities) -> dict[str, list[dict[str, Any]]]:
    """One day's positions, cash, total and the KRW rate as platform rows, in USD.

    ``securities`` is the collector's Securities resolver. Kiwoom's own
    securities value is the stock line, so holdings + cash = Kiwoom's total; the
    positions are checked against it separately (position_gap).
    """

    snapshot = figures.day.isoformat()
    positions, instruments = [], []
    for holding in holdings:
        exchange = holding.exchange or "US"
        key = securities.key(holding.security, name=holding.name, currency=holding.currency, exchange=exchange)
        cost = holding.average_price * holding.quantity
        positions.append({
            "account_id": account_id, "snapshot_date": snapshot, "conid": key, "model_code": "",
            "currency": holding.currency, "quantity_text": _text(holding.quantity),
            "cost_basis_text": _text(cost), "mark_price_text": _text(holding.price),
            "market_value_text": _text(holding.market_value),
            "unrealized_pnl_text": _text(holding.market_value - cost),
        })
        instruments.append({
            "conid": key, "symbol": holding.security.value, "description": holding.name, "asset_category": "STK",
            "currency": holding.currency, "listing_exchange": exchange, "isin": None,
        })
    cash = [{"account_id": account_id, "snapshot_date": snapshot, "currency": BASE_CURRENCY,
             "ending_cash_text": _text(figures.cash), "settled_cash_text": _text(figures.cash),
             "fx_rate_to_base_text": "1"}]
    fx_rates = []
    krw_in_usd = Decimal("0")
    if figures.krw_rate:
        # Kiwoom quotes KRW per USD; the platform stores the rate into the base currency.
        to_usd = Decimal(1) / figures.krw_rate
        fx_rates.append({"account_id": account_id, "rate_date": snapshot, "from_currency": KRW,
                         "to_currency": BASE_CURRENCY, "rate_text": format(to_usd, ".12f")})
        if figures.krw_cash:
            krw_in_usd = figures.krw_cash * to_usd
            cash.append({"account_id": account_id, "snapshot_date": snapshot, "currency": KRW,
                         "ending_cash_text": _text(figures.krw_cash), "settled_cash_text": _text(figures.krw_cash),
                         "fx_rate_to_base_text": format(to_usd, ".12f")})
    nav = [{"account_id": account_id, "snapshot_date": snapshot, "currency": BASE_CURRENCY, "asset_class": "TOTAL",
            "total_text": _text(figures.cash + figures.securities_value + krw_in_usd),
            "cash_text": _text(figures.cash + krw_in_usd), "stock_text": _text(figures.securities_value),
            "options_text": "0", "futures_text": "0", "bonds_text": "0", "accruals_text": "0"}]
    return {"position_snapshots": positions, "cash_snapshots": cash, "nav_snapshots": nav,
            "fx_rates": fx_rates, "instruments": instruments}


def position_gap(figures: UsDay, holdings: Iterable[Holding]) -> Decimal:
    """Holdings at their prices less Kiwoom's own securities value for the day."""

    return sum((item.market_value for item in holdings), Decimal("0")) - figures.securities_value


def us_movement_rows(account_id: str, movements: Iterable[CashMovement],
                     securities) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """U.S. trades, corporate actions and cash movements as platform rows, and the labels not read."""

    from .kiwoom_collector import _sha  # one source-key rule for every Kiwoom account

    trades, cash, unknown = [], [], []
    seen: dict[tuple, int] = {}
    for movement in movements:
        fields = (movement.trade_date, movement.kind, movement.description,
                  movement.security.value if movement.security else "", str(movement.quantity),
                  str(movement.amount), movement.currency)
        seen[fields] = seen.get(fields, 0) + 1
        source_key = _sha([account_id, *fields, seen[fields]])[:40]
        found = us_classify(movement)
        if found is None:
            unknown.append(f"{movement.trade_date} {movement.kind or movement.description or 'no label'}")
            continue
        kind, direction = found
        amount = abs(movement.amount) * direction if direction else Decimal("0")
        key = (securities.key(movement.security, currency=movement.currency)
               if movement.security is not None else None)
        if kind in SHARE_KINDS and key is not None:
            quantity = abs(movement.quantity) * SHARE_KINDS[kind]
            trades.append({
                "account_id": account_id, "source_key": source_key, "execution_id": None, "trade_id": source_key,
                "conid": key, "trade_date": movement.trade_date, "trade_time": None,
                "settle_date": None, "buy_sell": kind, "quantity_text": _text(quantity),
                "trade_price_text": (_text(abs(movement.amount) / abs(movement.quantity))
                                     if movement.quantity and movement.amount else None),
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


def share_counts(movements: Iterable[CashMovement]) -> dict[str, Decimal]:
    """Shares held after every movement in the list, by ticker, starting from none."""

    held: dict[str, Decimal] = {}
    for movement in movements:
        found = us_classify(movement)
        if found is None or found[0] not in SHARE_KINDS or movement.security is None:
            continue
        ticker = movement.security.value
        held[ticker] = held.get(ticker, Decimal("0")) + abs(movement.quantity) * SHARE_KINDS[found[0]]
    return {ticker: count for ticker, count in held.items() if count}


def us_checks(*, day: date, history_start: date, valuation: UsValuation, figures: UsDay,
              rows: Mapping[str, list], unknown: Sequence[str], new_securities: Sequence[str],
              movements: Sequence[CashMovement]) -> dict[str, dict[str, Any]]:
    """The four load-and-reconcile checks for a USD account, from what was just collected."""

    holdings = len(rows["position_snapshots"])
    checks: dict[str, dict[str, Any]] = {
        "holdings_and_cash_loaded": {
            "status": "pass",
            "message": f"{holdings} U.S. holdings and USD cash loaded for {day.isoformat()}.",
            "evidence": {"date": day.isoformat(), "holdings": holdings,
                         "currencies": sorted({row["currency"] for row in rows["cash_snapshots"]})},
        },
    }
    trades, cash = len(rows["trades"]), len(rows["cash_transactions"])
    evidence = {"from": history_start.isoformat(), "to": day.isoformat(), "trades": trades, "cash_movements": cash,
                "unknown_labels": list(unknown[:20])}
    if unknown:
        checks["transactions_loaded"] = {
            "status": "fail", "evidence": evidence,
            "message": f"{len(unknown)} Kiwoom movements have a label the platform does not read yet, for example "
                       f"{unknown[0]}. Nothing was guessed."}
    else:
        replayed = share_counts(movements)
        held = {item.security.value: item.quantity for item in valuation.holdings}
        mismatched = sorted(t for t in set(replayed) | set(held) if replayed.get(t, Decimal(0)) != held.get(t, Decimal(0)))
        evidence["share_count_mismatches"] = mismatched[:20]
        if mismatched:
            checks["transactions_loaded"] = {
                "status": "fail", "evidence": evidence,
                "message": f"Kiwoom's movements since {history_start.isoformat()} do not add up to today's share "
                           f"count for {len(mismatched)} stocks, for example {mismatched[0]}. Check the history start."}
        else:
            checks["transactions_loaded"] = {
                "status": "pass", "evidence": evidence,
                "message": f"{trades} trades and corporate actions and {cash} cash movements loaded from "
                           f"{history_start.isoformat()}; they add up to every holding's share count today."}
    checks["securities_mapped"] = {
        "status": "pass", "evidence": {"new_securities": list(new_securities)},
        "message": "Every security is identified by its U.S. ticker"
                   + (f"; {len(new_securities)} were new to the security master and are added." if new_securities
                      else "."),
    }
    us = reconcile_us_valuation(valuation)
    checks["nav_reconciled"] = {
        "status": us["status"], "evidence": {"us": us["evidence"], "total_usd": _text(figures.total)},
        "message": us["message"] + f" Kiwoom's total is {figures.total:,.2f} USD.",
    }
    return checks


def collect_today(client: KiwoomClient, *, day: date) -> tuple[UsValuation, UsDay]:
    """Today's U.S. holdings and USD figures. ust21120 also gives the KRW cash left on the account."""

    valuation = client.us_valuation()
    figures = day_from_balances(day, valuation.balances)
    return valuation, figures


def collect_past_day(client: KiwoomClient, day: date) -> UsDay:
    return day_from_balances(day, client.us_balances_on(day))


# Loading ---------------------------------------------------------------------------

US_HISTORY_RUN = "kiwoom_rest_us_history"
# Past days filled per run (one ust21132 call each), so a run stays inside the daily job's time limit.
US_HISTORY_DAYS_PER_RUN = 250


def us_trading_days(start: date, end: date) -> list[date]:
    """New York Stock Exchange sessions from start to end inclusive."""

    from .market_calendar import CALENDARS

    calendar = CALENDARS["XNYS"]
    days, day = [], start
    while day <= end:
        if calendar.is_session(day):
            days.append(day)
        day += timedelta(days=1)
    return days


def _store_rows(connection, account_id: str, display_name: str, rows_by_day: Sequence[tuple[date, Mapping]],
                *, run_type: str, raw: Any, new_securities, now: datetime) -> int:
    from .kiwoom_collector import SOURCE, _KEYS, _clear_day, _register, _sha, _upsert

    stamp = now.astimezone(timezone.utc).isoformat()
    run_id = connection.execute(
        "INSERT INTO ingestion_runs (run_type, requested_from, requested_to, started_at, completed_at, status, "
        "raw_sha256, row_count) VALUES (?, ?, ?, ?, ?, 'success', ?, ?) RETURNING run_id",
        (run_type, rows_by_day[0][0].isoformat(), rows_by_day[-1][0].isoformat(), stamp, stamp, _sha(raw),
         sum(len(items) for _, rows in rows_by_day for items in rows.values())),
    ).fetchone()["run_id"]
    connection.execute(
        "INSERT INTO accounts (account_id, alias, base_currency, account_type, ib_entity, first_seen_at, "
        "last_seen_at, source_json) VALUES (?, ?, ?, 'kiwoom', NULL, ?, ?, ?) ON CONFLICT (account_id) "
        "DO UPDATE SET alias = EXCLUDED.alias, last_seen_at = EXCLUDED.last_seen_at",
        (account_id, display_name, BASE_CURRENCY, stamp, stamp, '{"source": "%s", "market": "US"}' % SOURCE),
    )
    _register(connection, new_securities, [row for _, rows in rows_by_day for row in rows.get("instruments", [])],
              stamp)
    for day, rows in rows_by_day:
        if "nav_snapshots" in rows:
            _clear_day(connection, account_id, day, rows.get("fx_rates", ()))
        for table in ("position_snapshots", "cash_snapshots", "nav_snapshots", "fx_rates", "trades",
                      "cash_transactions"):
            if rows.get(table):
                _upsert(connection, table,
                        [{**row, "row_sha256": _sha(row), "last_seen_run_id": run_id} for row in rows[table]],
                        _KEYS[table])
    return run_id


def us_days_stored(connection, account_id: str, start: date, end: date) -> set[date]:
    """Past days stored by the U.S. history pass whose positions add up to Kiwoom's securities value, to 1 cent
    a position. A day stored without positions (prices not yet known) is filled again."""

    rows = connection.execute(
        "SELECT n.snapshot_date, n.stock_text, COUNT(p.conid) AS positions, "
        "COALESCE(SUM(CAST(p.market_value_text AS NUMERIC)), 0) AS held FROM nav_snapshots n "
        "JOIN ingestion_runs r ON r.run_id = n.last_seen_run_id AND r.run_type = ? "
        "LEFT JOIN position_snapshots p ON p.account_id = n.account_id AND p.snapshot_date = n.snapshot_date "
        "WHERE n.account_id = ? AND n.asset_class = 'TOTAL' AND n.snapshot_date BETWEEN ? AND ? "
        "GROUP BY n.snapshot_date, n.stock_text",
        (US_HISTORY_RUN, account_id, start.isoformat(), end.isoformat()),
    ).fetchall()
    stored = set()
    for row in rows:
        stock = Decimal(str(row["stock_text"] or "0"))
        positions = int(row["positions"] or 0)
        if (positions or not stock) and abs(Decimal(str(row["held"])) - stock) <= Decimal("0.01") * max(1, positions):
            stored.add(date.fromisoformat(str(row["snapshot_date"])[:10]))
    return stored


def stored_holdings(connection, account_id: str, day: date) -> list[Holding]:
    """The positions already stored for a day, as holdings (ticker from the instrument)."""

    rows = connection.execute(
        "SELECT i.symbol, i.description, i.listing_exchange, p.currency, p.quantity_text, p.cost_basis_text, "
        "p.mark_price_text, p.market_value_text FROM position_snapshots p JOIN instruments i ON i.conid = p.conid "
        "WHERE p.account_id = ? AND p.snapshot_date = ?", (account_id, day.isoformat())).fetchall()
    held = []
    for row in rows:
        quantity = Decimal(str(row["quantity_text"]))
        cost = Decimal(str(row["cost_basis_text"] or "0"))
        held.append(Holding(SecurityIdentifier("us_ticker", str(row["symbol"])), str(row["description"] or ""),
                            quantity, cost / quantity if quantity else Decimal("0"),
                            Decimal(str(row["mark_price_text"])), Decimal(str(row["market_value_text"])),
                            str(row["currency"]), row["listing_exchange"]))
    return held


class UsHistoryPositions:
    """Each past U.S. trading day's holdings at that day's close, from Kiwoom's own answers only.

    - Shares: the movement list (ust21100) replayed, each trade counted from the Korean date on which
      Kiwoom's per-stock daily holding (usa21730) changes by that trade. Kiwoom posts a trade a day or two
      after the U.S. trade date, so the posting date alone is not used. A corporate action counts from its
      posting date. A trade in a stock Kiwoom no longer lists counts from the day the other trades posted
      with it count from, else two U.S. trading days before its posting date.
    - Prices: each stock's unadjusted close for the day (usa06012), on the first exchange Kiwoom knows it on.
    - A stock Kiwoom no longer lists (delisted after a merger or a paid cancellation) has no close. Its value
      is what is left of Kiwoom's own securities value for the day (ust21132) after every listed stock. When
      several are held, each takes its last known price except the one leaving the account soonest, which
      takes the rest. Those prices are derived, not quoted, and the evidence says so.

    Kiwoom is asked once per run, on the first day asked for.
    """

    def __init__(self, client: KiwoomClient, movements: Sequence[CashMovement], *, since: date, through: date,
                 held_today: Iterable[str] = ()):
        self._client = client
        self._since, self._through = since, through
        self._held_today = set(held_today)
        self._moves: list[tuple[date, str, Decimal, bool, Decimal]] = []  # posted, ticker, shares, trade, cash
        for movement in movements:
            found = us_classify(movement)
            if found is None or found[0] not in SHARE_KINDS or movement.security is None or not movement.quantity:
                continue
            self._moves.append((date.fromisoformat(movement.trade_date[:10]), movement.security.value,
                                abs(movement.quantity) * SHARE_KINDS[found[0]], found[0] in ("BUY", "SELL"),
                                abs(movement.amount)))
        self._ready = False
        self.closes: dict[str, dict[date, Decimal]] = {}
        self.exchanges: dict[str, str] = {}
        self.unlisted: set[str] = set()
        self.counted_from: dict[tuple[date, str], date] = {}
        self.derived: list[dict[str, str]] = []
        self._last_price: dict[str, Decimal] = {}

    def _prepare(self) -> None:
        from .kiwoom_client import US_EXCHANGES, KiwoomUnknownStock

        for ticker in sorted({ticker for _, ticker, *_ in self._moves} | self._held_today):
            for exchange in US_EXCHANGES:
                try:
                    self.closes[ticker] = self._client.us_daily_closes(ticker, exchange, self._since, self._through)
                except KiwoomUnknownStock:
                    continue
                self.exchanges[ticker] = exchange
                break
            else:
                self.unlisted.add(ticker)
        # Net shares each posting date moved, per stock, and Kiwoom's own day for it.
        groups: dict[tuple[date, str], tuple[Decimal, bool]] = {}
        for posted, ticker, shares, trade, _ in self._moves:
            total, was_trade = groups.get((posted, ticker), (Decimal("0"), False))
            groups[(posted, ticker)] = (total + shares, was_trade or trade)
        for ticker, exchange in sorted(self.exchanges.items()):
            posted_days = sorted(posted for posted, item in groups if item == ticker)
            quantities: dict[date, Decimal] = {}
            for start, end in _windows_around(posted_days, floor=self._since - timedelta(days=1)):
                quantities.update(self._client.us_stock_quantities(ticker, exchange, start, end))
            for posted in posted_days:
                shares = groups[(posted, ticker)][0]
                for back in range(8):
                    day = posted - timedelta(days=back)
                    before, after = quantities.get(day - timedelta(days=1)), quantities.get(day)
                    if before is not None and after is not None and after - before == shares:
                        self.counted_from[(posted, ticker)] = day
                        break
        for (posted, ticker), (_, trade) in sorted(groups.items()):
            if (posted, ticker) in self.counted_from:
                continue
            if not trade:
                self.counted_from[(posted, ticker)] = posted
                continue
            alongside = [day for (other_posted, _), day in self.counted_from.items() if other_posted == posted]
            self.counted_from[(posted, ticker)] = (max(set(alongside), key=alongside.count) if alongside
                                                   else _sessions_before(posted, 2))
        self._ready = True

    def __call__(self, day: date, figures: UsDay) -> list[Holding] | None:
        if not self._ready:
            self._prepare()
        held: dict[str, Decimal] = {}
        cost: dict[str, Decimal] = {}
        for posted, ticker, shares, trade, cash in sorted(self._moves, key=lambda item: item[0]):
            if self.counted_from[(posted, ticker)] > day:
                continue
            before = held.get(ticker, Decimal("0"))
            if trade and shares > 0:
                cost[ticker] = cost.get(ticker, Decimal("0")) + cash
                self._last_price.setdefault(ticker, cash / shares)
            elif before:
                cost[ticker] = cost.get(ticker, Decimal("0")) * (before + shares) / before
            held[ticker] = before + shares
        holdings, unlisted = [], []
        listed_value = Decimal("0")
        for ticker, quantity in sorted(held.items()):
            if not quantity:
                continue
            if ticker in self.unlisted:
                if ticker in self._held_today:
                    return None  # a stock still held must have Kiwoom's close
                unlisted.append(ticker)
                continue
            close = self.closes.get(ticker, {}).get(day)
            if close is None:
                return None
            value = quantity * close
            listed_value += value
            holdings.append(self._holding(ticker, quantity, cost.get(ticker, Decimal("0")), close, value))
        if unlisted:
            rest = figures.securities_value - listed_value
            leaving = min(unlisted, key=lambda ticker: (self._next_exit(ticker, day), ticker))
            values = {ticker: held[ticker] * self._last_price.get(ticker, Decimal("0"))
                      for ticker in unlisted if ticker != leaving}
            values[leaving] = rest - sum(values.values(), Decimal("0"))
            if any(value < 0 for value in values.values()):
                values = {ticker: held[ticker] * self._last_price.get(ticker, Decimal("0")) for ticker in unlisted}
            for ticker in unlisted:
                price = (values[ticker] / held[ticker]).quantize(Decimal("0.0001"))
                if ticker == leaving or len(unlisted) == 1:
                    self._last_price[ticker] = price
                self.derived.append({"date": day.isoformat(), "ticker": ticker, "price": _text(price)})
                holdings.append(self._holding(ticker, held[ticker], cost.get(ticker, Decimal("0")), price,
                                              values[ticker]))
        return holdings

    def _next_exit(self, ticker: str, day: date) -> date:
        exits = [self.counted_from[(posted, item)] for posted, item, shares, *_ in self._moves
                 if item == ticker and shares < 0 and self.counted_from[(posted, item)] > day]
        return min(exits) if exits else date.max

    def _holding(self, ticker: str, quantity: Decimal, cost: Decimal, price: Decimal, value: Decimal) -> Holding:
        return Holding(SecurityIdentifier("us_ticker", ticker), ticker, quantity, cost / quantity if quantity else
                       Decimal("0"), price, value, BASE_CURRENCY, self.exchanges.get(ticker) and "US")


def _sessions_before(day: date, count: int) -> date:
    from .market_calendar import CALENDARS

    for _ in range(count):
        day -= timedelta(days=1)
        while not CALENDARS["XNYS"].is_session(day):
            day -= timedelta(days=1)
    return day


def _windows_around(days: Sequence[date], *, floor: date) -> list[tuple[date, date]]:
    """Date ranges covering a week before to a day after each posting date, merged where they touch."""

    windows: list[list[date]] = []
    for day in sorted(days):
        start, end = max(day - timedelta(days=6), floor), day + timedelta(days=1)
        if windows and start <= windows[-1][1] + timedelta(days=1):
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return [(start, end) for start, end in windows]


def build_us_history(connection, client: KiwoomClient, *, account_id: str, display_name: str, history_start: date,
                     through: date, now: datetime, positions_on=None,
                     limit: int = US_HISTORY_DAYS_PER_RUN) -> dict[str, Any]:
    """Fill closed U.S. trading days up to ``through`` from Kiwoom's daily total (ust21132), say what is left.

    Every stored day is Kiwoom's close for that day, the latest one included: the holdings answer
    (ust21070) prices at the moment of asking, after-hours and overnight trading included, so it is
    never stored as a day's close.

    ``positions_on(day, figures)`` gives a past day's holdings at that day's close
    (UsHistoryPositions), or None when a stock's close is missing. A day is stored either way
    (its total and cash are Kiwoom's), but only counts as done once its positions
    add up to Kiwoom's securities value; until then the check stays pending.
    """

    from .kiwoom_collector import Securities, known_identifiers

    end = through
    have = us_days_stored(connection, account_id, history_start, end)
    missing = [day for day in us_trading_days(history_start, end) if day not in have]
    batch, remaining = missing[:limit], missing[limit:]
    securities = Securities(known=known_identifiers(connection))
    written: list[tuple[date, Mapping]] = []
    without_positions, gaps = [], []
    for day in batch:
        figures = collect_past_day(client, day)
        holdings = positions_on(day, figures) if positions_on is not None else None
        if holdings is None or abs(position_gap(figures, holdings)) > Decimal("0.01") * max(1, len(holdings)):
            # The daily run's positions for the day stay when they add up to Kiwoom's total for it.
            kept = stored_holdings(connection, account_id, day)
            if kept and abs(position_gap(figures, kept)) <= Decimal("0.01") * len(kept):
                holdings = kept
        if holdings is None:
            without_positions.append(day.isoformat())
            holdings = ()
        elif abs(position_gap(figures, holdings)) > Decimal("0.01") * max(1, len(holdings)):
            gaps.append({"date": day.isoformat(), "gap": _text(position_gap(figures, holdings))})
        written.append((day, us_rows(account_id, figures, holdings, securities)))
    if written:
        _store_rows(connection, account_id, display_name, written, run_type=US_HISTORY_RUN,
                    raw={"days": [day.isoformat() for day, _ in written],
                         "rows": [rows for _, rows in written]},
                    new_securities=[securities.new[key] for key in sorted(securities.new)], now=now)
    done = len(us_days_stored(connection, account_id, history_start, end))
    evidence = {"from": history_start.isoformat(), "to": end.isoformat(), "loaded_this_run": len(written),
                "days_done": done, "days_left": len(remaining),
                "days_without_positions_this_run": len(without_positions),
                "without_positions": without_positions[:20], "gaps": gaps[:20],
                "basis": "Kiwoom's daily USD cash and securities value (ust21132), U.S. trading days; each "
                         "stock's shares (ust21100 dated by usa21730) at its close (usa06012)"}
    derived = getattr(positions_on, "derived", None)
    if derived:
        evidence["derived_prices"] = {"days": len({item["date"] for item in derived}),
                                      "tickers": sorted({item["ticker"] for item in derived}),
                                      "basis": "Kiwoom's securities value less every listed stock at its close, "
                                               "for stocks Kiwoom no longer lists"}
    if remaining or without_positions or gaps:
        reason = (f"{len(remaining)} more days are filled on the next runs" if remaining else
                  f"{len(gaps)} days' positions do not add up to Kiwoom's securities value" if gaps else
                  "some days have a stock without Kiwoom's close for the day")
        return {"status": "pending", "evidence": evidence,
                "message": f"{done} past days complete; {reason}."}
    return {"status": "pass", "evidence": evidence,
            "message": f"Daily history from {history_start.isoformat()} is complete: {done} U.S. trading days, "
                       "each adding up to Kiwoom's own daily total."}


def run_target(connection, tenant_id: str, client: KiwoomClient, target, *, now: datetime) -> dict[str, Any]:
    """Load one USD Kiwoom account for its onboarding stage. Mirrors the Korea path in kiwoom_collector.run."""

    from .kiwoom_collector import HISTORY_STAGES, REFRESH_STAGES, Securities, _connection_result, _history_record, \
        _record, known_identifiers, platform_account_id, windows

    assert target.case_id is not None and target.history_start is not None
    account_id = platform_account_id(target.broker_account_id)
    day = us_snapshot_day(now)
    valuation, figures = collect_today(client, day=day)
    movements: list[CashMovement] = []
    # Kiwoom posts a U.S. trade on a later Korean date, so the list runs to today in Seoul.
    from zoneinfo import ZoneInfo

    for start, end in windows(target.history_start, now.astimezone(ZoneInfo("Asia/Seoul")).date()):
        movements.extend(client.us_transactions(start, end))
    securities = Securities(known=known_identifiers(connection))
    rows = us_rows(account_id, figures, valuation.holdings, securities)
    moved, unknown = us_movement_rows(account_id, movements, securities)
    new = [securities.new[key] for key in sorted(securities.new)]
    # Today's holdings are priced at this moment, not at a close: only the securities and the movements
    # are stored from them. The day itself is stored from Kiwoom's close below (build_us_history).
    _store_rows(connection, account_id, target.display_name,
                [(day, {"instruments": rows["instruments"], **moved})], run_type="kiwoom_rest_us",
                raw={"day": day.isoformat(), "rows": rows, **moved}, new_securities=new, now=now)
    _connection_result(connection, tenant_id, target, ok=True, message="Kiwoom answered and the U.S. account loaded.",
                       base_currency=BASE_CURRENCY)
    stage = str(target.stage)
    if stage == "request":
        stage = _record(connection, tenant_id, target.case_id, "connection_sees_account", {
            "status": "pass", "message": "Kiwoom answered from the platform's fixed address for this account.",
            "evidence": {"date": day.isoformat(), "environment": target.environment, "market": "US"}})
    if stage == "load_and_reconcile":
        for check_id, check in us_checks(day=day, history_start=target.history_start, valuation=valuation,
                                         figures=figures, rows={**rows, **moved}, unknown=unknown,
                                         new_securities=[item.key for item in new], movements=movements).items():
            stage = _record(connection, tenant_id, target.case_id, check_id, check)
            if stage != "load_and_reconcile":
                break
    connection.commit()
    result: dict[str, Any] = {"status": "loaded", "stage": stage, "market": "US",
                              "rows": {table: len(items) for table, items in {**rows, **moved}.items()}}
    if stage in HISTORY_STAGES or stage in REFRESH_STAGES:
        positions = UsHistoryPositions(client, movements, since=target.history_start, through=day,
                                       held_today=[holding.code for holding in valuation.holdings])

        def build():
            return build_us_history(connection, client, account_id=account_id, display_name=target.display_name,
                                    history_start=target.history_start, through=day, now=now,
                                    positions_on=positions)

        result["history"] = _history_record(connection, tenant_id, target, build, record=stage == "build_history")
    return result
