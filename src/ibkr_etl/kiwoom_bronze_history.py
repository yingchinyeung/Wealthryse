"""Kiwoom's past days asked again into bronze, so every silver day can be rebuilt from bronze (Medallion).

Bronze capture (kiwoom_bronze) began with the daily run of 22 Oct 2026. The past days silver already
held from the history pass were stored before that, so their answers were never kept, and the daily
run never asks for a stored day again. This asks Kiwoom again for those days, for each Kiwoom account
being loaded, with bronze capture on and the same calls the collector makes (the calls
kiwoom_conform replays): the account's movement list and today's answers, then each past day.

It writes nothing to silver. The account's own reads go through a read-only session; only the bronze
recorder writes, through its own connection, one kiwoom_rest_bronze run per account. Days already
answered in a bronze run that finished are skipped, so a run picks up where the last one stopped.
kiwoom_conform.check_run rebuilds each run's silver rows from what it stored and compares them with silver.
"""
from __future__ import annotations

import time as clock
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from .kiwoom_bronze import RUN_TYPE, TABLE
from .kiwoom_client import KiwoomClient
from .kiwoom_collector import (LOADING_STAGES, SEOUL, Target, collect, known_identifiers, platform_account_id,
                               snapshot_day, targets, windows)
from .kiwoom_us_account import (US_HISTORY_RUN, UsHistoryPositions, collect_past_day, collect_today,
                                is_usd_account, us_snapshot_day)

KOREA_HISTORY_RUN = "kiwoom_rest_history"


@dataclass(frozen=True)
class Market:
    """Where one market's past days live in silver and in bronze."""

    name: str
    history_run: str  # the ingestion run type of the history pass that stored silver's past days
    api_id: str  # the per-day inquiry
    day_field: str  # its date field in the request


KRW = Market("KRW", KOREA_HISTORY_RUN, "ka01690", "qry_dt")
USD = Market("USD", US_HISTORY_RUN, "ust21132", "base_dt")


def market_of(target: Target) -> Market:
    return USD if is_usd_account(target.reporting_currency) else KRW


def loaded(target: Target) -> bool:
    """An account the daily run loads with a history (kiwoom_collector.run's own test)."""

    return target.case_id is not None and target.stage in LOADING_STAGES and target.history_start is not None


def silver_days(connection, account_id: str, market: Market) -> list[date]:
    """The past days silver holds for the account from the history pass, oldest first."""

    rows = connection.execute(
        "SELECT DISTINCT n.snapshot_date FROM nav_snapshots n JOIN ingestion_runs r ON r.run_id = n.last_seen_run_id "
        "AND r.run_type = ? WHERE n.account_id = ? AND n.asset_class = 'TOTAL'",
        (market.history_run, account_id)).fetchall()
    return sorted(date.fromisoformat(str(row[0])[:10]) for row in rows)


def bronze_days(connection, broker_account_id: str, market: Market) -> set[date]:
    """The days whose answer bronze already holds for the account, from runs that finished (answered pages only).

    A run that failed part-way can hold a day's first answer without the rest: a day the client could not
    value stops the account after its ka01690 and kt00016 answers, before that day's U.S. balances. Its days
    are asked again.
    """

    rows = connection.execute(
        f"SELECT DISTINCT k.request_key->>? FROM {TABLE} k JOIN ingestion_runs r ON r.run_id = k.run_id "
        "WHERE r.run_type = ? AND r.status = 'success' AND k.api_id = ? AND k.request_key->>'account' = ? "
        "AND k.http_status = 200 AND COALESCE(k.return_code, 0) = 0",
        (market.day_field, RUN_TYPE, market.api_id, broker_account_id)).fetchall()
    found = set()
    for row in rows:
        text = str(row[0] or "")
        if len(text) == 8 and text.isdigit():
            found.add(date(int(text[:4]), int(text[4:6]), int(text[6:])))
    return found


def plan(connection, target: Target, *, limit: int) -> tuple[Market, list[date], int]:
    """The account's market, the past days to ask for this run (oldest first) and how many are left after it."""

    market = market_of(target)
    have = bronze_days(connection, target.broker_account_id, market)
    wanted = [day for day in silver_days(connection, platform_account_id(target.broker_account_id), market)
              if day not in have]
    return market, wanted[:limit], max(0, len(wanted) - limit)


def ask_korea(client: KiwoomClient, connection, target: Target, days: list[date], *, now: datetime,
              include_us: bool, more: Callable[[], bool]) -> int:
    """kiwoom_collector.run's calls for a KRW account, then build_history's for each past day."""

    collect(client, broker_account_id=target.broker_account_id, history_start=target.history_start,
            today=snapshot_day(now), known=known_identifiers(connection), include_us=include_us)
    asked = 0
    for day in days:
        if not more():
            break
        if client.balance_on(day) is not None and include_us:
            client.us_balances_on(day)
        asked += 1
    return asked


def ask_usa(client: KiwoomClient, target: Target, days: list[date], *, now: datetime,
            more: Callable[[], bool]) -> int:
    """kiwoom_us_account.run_target's calls, then build_us_history's for each past day."""

    day = us_snapshot_day(now)
    valuation, _ = collect_today(client, day=day)
    movements = [item for first, last in windows(target.history_start, now.astimezone(SEOUL).date())
                 for item in client.us_transactions(first, last)]
    positions = UsHistoryPositions(client, movements, since=target.history_start, through=day,
                                   held_today=[holding.code for holding in valuation.holdings])
    asked = 0
    for past in days:
        if not more():
            break
        positions(past, collect_past_day(client, past))
        asked += 1
    return asked


def run(connection, tenant_id: str, *, client_for: Callable[[Target], KiwoomClient], bronze: Any,
        now: Callable[[], datetime], limit: int, minutes: float, include_us: bool = True,
        markets: tuple[str, ...] = ("KRW", "USD"),
        monotonic: Callable[[], float] = clock.monotonic) -> list[dict[str, Any]]:
    """Ask again for each loaded account's past days that bronze does not hold. Counts only, never an account.

    ``connection`` is only read (the caller makes its session read-only). Asking stops at ``limit`` days an
    account or after ``minutes``; the run is still closed and counted. ``now`` is read as each account's
    bronze run opens, so its days are the ones kiwoom_conform replays from that run's start.
    """

    deadline = monotonic() + minutes * 60
    results = []
    for target in targets(connection, tenant_id):
        if not loaded(target) or market_of(target).name not in markets:
            continue
        market, days, left = plan(connection, target, limit=limit)
        connection.rollback()  # end the read; the next account starts a fresh one
        result: dict[str, Any] = {"market": market.name, "days_planned": len(days), "days_asked": 0,
                                  "days_left": left + len(days)}
        if not days:
            results.append({**result, "status": "nothing to ask"})
            continue
        if monotonic() >= deadline:
            results.append({**result, "status": "out of time"})  # left for the next run
            continue
        client = capture = None
        ok = False
        try:
            client = client_for(target)
            capture = bronze.for_target(target)
            if capture is None:
                results.append({**result, "status": "failed", "error": "bronze capture is off"})
                continue
            client.set_capture(capture)
            started = now()
            more = lambda: monotonic() < deadline  # noqa: E731
            if market is USD:
                asked = ask_usa(client, target, days, now=started, more=more)
            else:
                asked = ask_korea(client, connection, target, days, now=started, include_us=include_us, more=more)
            connection.rollback()
            ok = bronze.enabled
            result.update(days_asked=asked, days_left=left + len(days) - asked,
                          status="asked" if ok else "failed")
            if not ok:
                result["error"] = "a bronze write failed"
        except Exception as error:  # noqa: BLE001 - only the type: a message could carry an account number
            connection.rollback()
            result.update(status="failed", error=type(error).__name__)
        finally:
            if client is not None:
                try:
                    client.revoke()
                except Exception:  # noqa: BLE001 - the token expires on its own
                    pass
            if capture is not None:
                capture.finish(ok)
                result["run_id"] = capture.run_id
        results.append(result)
    return results
