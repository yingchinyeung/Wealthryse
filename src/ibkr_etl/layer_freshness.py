"""Freshness of each medallion layer after the daily cycle. Read-only.

Each rule names what it measures, when the daily cycle should have refreshed it (a
UTC time of day) and how old the newest record may be at that time. Rules are judged
against the most recent due time that has passed, so a check run before today's cycle
finishes is judged against yesterday's cycle.

- loaded_at rules: the newest record or file must have been written no earlier than
  `max_age` before the due time.
- as_of_date rules: the newest business date may be at most `max_age` days before the
  due date (weekends and market holidays need the slack).

The autonomous administrator raises late required checks as system incidents when
LAYER_FRESHNESS_ENABLED is on. Run `python -m ibkr_etl.layer_freshness` for a dry run.
"""
from __future__ import annotations

import json
import os
import time as monotonic_time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

ENABLED_ENV = "LAYER_FRESHNESS_ENABLED"
LOADED = "loaded_at"
AS_OF = "as_of_date"
LATE_STATES = frozenset({"late", "missing", "future"})
MARKET_DATA_TARGET = "ibkrv2stgauhecwc4itaoo/bronze-market-data"
FEED_LOOKBACK_DAYS = 3
FUTURE_TOLERANCE = timedelta(minutes=10)
DEFAULT_TIME_BUDGET_SECONDS = 15.0


@dataclass(frozen=True)
class Rule:
    check_id: str
    layer: str
    target: str  # key in infra/medallion/catalog.json
    measure: str  # LOADED or AS_OF
    due_utc: time
    max_age: timedelta
    query: str | None = None  # one row with an "observed" column
    feed: str | None = None  # bronze-market-data folder
    required: bool = True  # False: reported, never raised as an incident


def _feed(feed: str, due: time, *, required: bool = True) -> Rule:
    return Rule(f"bronze.market_data.{feed}", "bronze", MARKET_DATA_TARGET, LOADED, due,
                timedelta(hours=6), feed=feed, required=required)


# Daily cycle (UTC): institutional-daily 08:00, benchmark 08:30, sector 08:45,
# derived 08:50, risk snapshots 08:55, crisis scenarios 09:20, risk-free 23:00.
RULES: tuple[Rule, ...] = (
    # Bronze: raw IBKR statements and records. Each Flex pull stores a new raw report
    # (the statement carries its generation time); new Flex records can pause over
    # weekends and holidays, so flex_rows gets the same slack as NAV.
    Rule("bronze.raw_reports", "bronze", "public.raw_reports", LOADED, time(9, 15),
         timedelta(hours=6), query="SELECT MAX(received_at) AS observed FROM raw_reports"),
    Rule("bronze.flex_rows", "bronze", "public.flex_rows", LOADED, time(9, 15), timedelta(days=4),
         query="SELECT r.started_at AS observed FROM flex_rows f "
               "JOIN ingestion_runs r ON r.run_id = f.first_seen_run_id "
               "ORDER BY f.flex_row_id DESC LIMIT 1"),
    # Bronze: newest raw market-data file per feed (written by S2 raw capture).
    _feed("yahoo-benchmark-history", time(9, 30)),
    _feed("yahoo-crisis-history", time(10, 0)),
    _feed("treasury-par-yield", time(23, 30)),
    # Fetched only when a subject needs them (FX, new holdings, licensed HIBOR, derived-daily).
    _feed("yahoo-fx-history", time(9, 30), required=False),
    _feed("yahoo-sector-search", time(9, 30), required=False),
    _feed("yahoo-sector-profile", time(9, 30), required=False),
    _feed("hkma-hibor", time(23, 30), required=False),
    _feed("hkab-hibor", time(23, 30), required=False),
    _feed("yahoo-chart", time(9, 30), required=False),
    # Silver.
    Rule("silver.nav_snapshots", "silver", "public.nav_snapshots", AS_OF, time(9, 30),
         timedelta(days=4), query="SELECT MAX(snapshot_date) AS observed FROM nav_snapshots"),
    Rule("silver.position_snapshots", "silver", "public.position_snapshots", AS_OF, time(9, 30),
         timedelta(days=4), query="SELECT MAX(snapshot_date) AS observed FROM position_snapshots"),
    # The benchmark and risk-free jobs re-stamp fetched_at on every run.
    Rule("silver.benchmark_prices", "silver", "public.benchmark_prices", LOADED, time(9, 30),
         timedelta(hours=6),
         query="SELECT MAX(fetched_at) AS observed FROM benchmark_prices WHERE quality_status <> 'fail'"),
    Rule("silver.market_data_prices", "silver", "public.market_data_prices", AS_OF, time(9, 30),
         timedelta(days=4), query="SELECT MAX(price_date) AS observed FROM market_data_prices"),
    Rule("silver.risk_free_rates", "silver", "public.risk_free_rates", LOADED, time(23, 30),
         timedelta(hours=6),
         query="SELECT MAX(fetched_at) AS observed FROM risk_free_rates "
               "WHERE currency = 'USD' AND quality_status <> 'fail'"),
    # Gold.
    Rule("gold.risk_analytics_snapshots", "gold", "public.risk_analytics_snapshots", AS_OF,
         time(10, 0), timedelta(days=4),
         query="SELECT MAX(as_of_date) AS observed FROM risk_analytics_snapshots"),
    Rule("gold.performance_comparison_daily", "gold", "public.performance_comparison_daily", AS_OF,
         time(9, 30), timedelta(days=4),
         query="SELECT MAX(observation_date) AS observed FROM performance_comparison_daily"),
    Rule("gold.crisis_scenario_results", "gold", "public.crisis_scenario_results", AS_OF,
         time(10, 0), timedelta(days=4),
         query="SELECT MAX(holdings_as_of) AS observed FROM crisis_scenario_results"),
    Rule("gold.crisis_replay_daily", "gold", "public.crisis_replay_daily", AS_OF, time(9, 30),
         timedelta(days=4),
         query="SELECT MAX(holdings_as_of) AS observed FROM crisis_replay_daily"),
)


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(ENABLED_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def last_due(now: datetime, due_utc: time) -> datetime:
    """The most recent occurrence of due_utc at or before now."""
    if now.tzinfo is None:
        raise ValueError("Freshness checks need a timezone-aware time")
    current = now.astimezone(timezone.utc)
    due = datetime.combine(current.date(), due_utc, tzinfo=timezone.utc)
    return due if due <= current else due - timedelta(days=1)


def _instant(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])


def evaluate(rule: Rule, observed: Any, *, now: datetime, error: str | None = None) -> dict[str, Any]:
    """Judge one observation; the result is plain JSON."""
    due = last_due(now, rule.due_utc)
    result: dict[str, Any] = {
        "check_id": rule.check_id, "layer": rule.layer, "target": rule.target,
        "measure": rule.measure, "required": rule.required, "due": due.isoformat(),
        "observed": None, "state": "unavailable",
    }
    if rule.feed:
        result["feed"] = rule.feed
    if error is not None:
        result["error"] = error
    elif observed is None:
        result["state"] = "missing"
    else:
        try:
            if rule.measure == LOADED:
                value = _instant(observed)
                earliest = due - rule.max_age
                result.update(observed=value.isoformat(), earliest_expected=earliest.isoformat(),
                              age_hours=round((due - value).total_seconds() / 3600, 1))
                result["state"] = ("future" if value > now.astimezone(timezone.utc) + FUTURE_TOLERANCE
                                   else "late" if value < earliest else "fresh")
            else:
                value = _date(observed)
                earliest_day = due.date() - timedelta(days=rule.max_age.days)
                result.update(observed=value.isoformat(), earliest_expected=earliest_day.isoformat(),
                              age_days=(due.date() - value).days)
                # Asian market dates can run a day ahead of the UTC date.
                result["state"] = ("future" if value > now.astimezone(timezone.utc).date() + timedelta(days=1)
                                   else "late" if value < earliest_day else "fresh")
        except (TypeError, ValueError):
            result.update(state="unavailable", error="unreadable value")
    result["late"] = bool(rule.required and result["state"] in LATE_STATES)
    return result


def _error_label(error: Exception) -> str:
    name = type(error).__name__
    sqlstate = getattr(error, "sqlstate", None)
    return f"{name} ({sqlstate})" if sqlstate else name


def newest_feed_blob(newest_blob_time: Callable[[str], datetime | None], feed: str, *,
                     now: datetime, lookback_days: int = FEED_LOOKBACK_DAYS) -> datetime | None:
    """Newest blob in the feed's most recent day folder, looking back a few days."""
    today = now.astimezone(timezone.utc).date()
    for offset in range(lookback_days):
        day = today - timedelta(days=offset)
        newest = newest_blob_time(f"{feed}/{day:%Y/%m/%d}/")
        if newest is not None:
            return newest
    return None


def collect(
    connection: Any,
    *,
    now: datetime,
    newest_blob_time: Callable[[str], datetime | None] | None = None,
    rules: Iterable[Rule] = RULES,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = monotonic_time.monotonic,
) -> list[dict[str, Any]]:
    """Measure and judge every rule. Never raises; problems become 'unavailable'.

    `connection` should be an autocommit, read-only connection so one failed query
    does not affect the next. `newest_blob_time(prefix)` lists bronze-market-data.
    """
    results = []
    started = clock()
    for rule in rules:
        if clock() - started > time_budget_seconds:
            results.append(evaluate(rule, None, now=now, error="time budget used"))
            continue
        try:
            if rule.feed:
                if newest_blob_time is None:
                    results.append(evaluate(rule, None, now=now, error="bronze-market-data not configured"))
                    continue
                observed = newest_feed_blob(newest_blob_time, rule.feed, now=now)
            else:
                if connection is None:
                    results.append(evaluate(rule, None, now=now, error="database not configured"))
                    continue
                row = connection.execute(rule.query).fetchone()
                observed = None if row is None else (row["observed"] if isinstance(row, Mapping) else row[0])
            results.append(evaluate(rule, observed, now=now))
        except Exception as error:  # noqa: BLE001 - report, never fail the caller
            results.append(evaluate(rule, None, now=now, error=_error_label(error)))
    return results


def summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "checked": len(results),
        "late": sorted(r["check_id"] for r in results if r["late"]),
        "unavailable": sorted(r["check_id"] for r in results if r["state"] == "unavailable"),
        "fresh": sum(1 for r in results if r["state"] == "fresh"),
    }


def run_read_only(database_url: str, tenant_id: str, *, now: datetime | None = None,
                  environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """Open read-only database and blob clients, collect, close. Never raises."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    connection = None
    blob_lister = None
    connection_error = None
    try:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(
            database_url, autocommit=True, connect_timeout=5, row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=2000",
        )
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
    except Exception as error:  # noqa: BLE001
        connection_error = _error_label(error)
        connection = None
    account_url = str(environ.get("AZURE_STORAGE_ACCOUNT_URL", "")).strip()
    container = str(environ.get("MARKET_DATA_RAW_CONTAINER", "")).strip()
    if account_url and container:
        try:
            from .cloud_storage import container_client, newest_blob_time

            client = container_client(account_url, container, connection_timeout=3,
                                      read_timeout=5, retry_total=0)
            blob_lister = lambda prefix: newest_blob_time(client, prefix)  # noqa: E731
        except Exception:  # noqa: BLE001
            blob_lister = None
    try:
        results = collect(connection, now=now, newest_blob_time=blob_lister)
        if connection_error:
            for result in results:
                if result.get("error") == "database not configured":
                    result["error"] = connection_error
        return results
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    database_url = os.environ.get("LAYER_FRESHNESS_DATABASE_URL") or os.environ.get("DATABASE_URL", "")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    results = run_read_only(database_url, tenant_id)
    print(json.dumps({"summary": summary(results), "checks": results}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
