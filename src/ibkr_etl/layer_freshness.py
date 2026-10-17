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
LAYER_FRESHNESS_ENABLED is on. The bronze-market-data file checks also need
LAYER_FRESHNESS_BLOB_CHECKS, so they can wait until raw capture is switched on.
Run `python -m ibkr_etl.layer_freshness` for a dry run.
"""
from __future__ import annotations

import json
import os
import threading
import time as monotonic_time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

ENABLED_ENV = "LAYER_FRESHNESS_ENABLED"
BLOB_CHECKS_ENV = "LAYER_FRESHNESS_BLOB_CHECKS"
DATABASE_URL_ENV = "LAYER_FRESHNESS_DATABASE_URL"  # optional override of DATABASE_URL
LOADED = "loaded_at"
AS_OF = "as_of_date"
LATE_STATES = frozenset({"late", "missing", "future"})
MARKET_DATA_TARGET = "ibkrv2stgauhecwc4itaoo/bronze-market-data"
FEED_LOOKBACK_DAYS = 3
FUTURE_TOLERANCE = timedelta(minutes=10)
DEFAULT_TIME_BUDGET_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 5000
LOCK_TIMEOUT_MS = 2000
# error_scope of an unavailable result: one check failed, or the whole session did
# (no database connection, storage not configured, time budget used).
CHECK_SCOPE = "check"
SESSION_SCOPE = "session"


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
    # Bronze: newest raw market-data file per feed in its dated folders (S2 raw capture).
    # yahoo-crisis-history has no rule: crisis windows are finished history, filed
    # once under history/, so no new file is expected each day.
    _feed("yahoo-benchmark-history", time(9, 30)),
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


def _flag(environ: Mapping[str, str] | None, name: str) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    return _flag(environ, ENABLED_ENV)


def blob_checks_enabled(environ: Mapping[str, str] | None = None) -> bool:
    return _flag(environ, BLOB_CHECKS_ENV)


def active_rules(environ: Mapping[str, str] | None = None) -> tuple[Rule, ...]:
    """Every database rule; the bronze-market-data rules only with blob checks on."""
    blobs = blob_checks_enabled(environ)
    return tuple(rule for rule in RULES if blobs or not rule.feed)


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


def evaluate(rule: Rule, observed: Any, *, now: datetime, error: str | None = None,
             scope: str = CHECK_SCOPE) -> dict[str, Any]:
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
        result.update(error=error, error_scope=scope)
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
            result.update(state="unavailable", error="unreadable value", error_scope=CHECK_SCOPE)
    result["late"] = bool(rule.required and result["state"] in LATE_STATES)
    return result


def _error_label(error: BaseException) -> str:
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


def _query(connection: Any, sql: str) -> Any:
    """One rule's query. Inside a transaction each runs under a savepoint, so a denied
    or cancelled query does not abort the ones after it."""
    transaction = getattr(connection, "transaction", None)
    if transaction is None:
        row = connection.execute(sql).fetchone()
    else:
        with transaction():
            row = connection.execute(sql).fetchone()
    return None if row is None else (row["observed"] if isinstance(row, Mapping) else row[0])


def collect(
    connection: Any,
    *,
    now: datetime,
    newest_blob_time: Callable[[str], datetime | None] | None = None,
    rules: Iterable[Rule] = RULES,
    time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS,
    clock: Callable[[], float] = monotonic_time.monotonic,
    results: list[dict[str, Any]] | None = None,
    database_error: str | None = None,
    storage_error: str | None = None,
) -> list[dict[str, Any]]:
    """Measure and judge every rule. Never raises; problems become 'unavailable'.

    `connection` is a read-only session (see open_read_only). `newest_blob_time(prefix)`
    lists bronze-market-data. Results are appended to `results` as they are made, so a
    caller that stops waiting still has the finished ones.
    """
    results = [] if results is None else results
    started = clock()
    for rule in rules:
        if clock() - started > time_budget_seconds:
            results.append(evaluate(rule, None, now=now, error="time budget used", scope=SESSION_SCOPE))
            continue
        try:
            if rule.feed:
                if newest_blob_time is None:
                    results.append(evaluate(rule, None, now=now, scope=SESSION_SCOPE,
                                            error=storage_error or "bronze-market-data not configured"))
                    continue
                observed = newest_feed_blob(newest_blob_time, rule.feed, now=now)
            else:
                if connection is None:
                    results.append(evaluate(rule, None, now=now, scope=SESSION_SCOPE,
                                            error=database_error or "database not configured"))
                    continue
                observed = _query(connection, rule.query)
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


def open_read_only(database_url: str, tenant_id: str) -> Any:
    """A session inside one READ ONLY transaction with short statement and lock
    timeouts. The caller rolls back and closes it."""
    import psycopg
    from psycopg.rows import dict_row

    connection = psycopg.connect(database_url, connect_timeout=CONNECT_TIMEOUT_SECONDS,
                                 row_factory=dict_row)
    try:
        connection.read_only = True  # psycopg starts each transaction with BEGIN READ ONLY
        connection.execute(
            "SELECT set_config('statement_timeout', %s, true), set_config('lock_timeout', %s, true), "
            "set_config('app.tenant_id', %s, true)",
            (str(STATEMENT_TIMEOUT_MS), str(LOCK_TIMEOUT_MS), tenant_id),
        )
    except BaseException:
        connection.close()
        raise
    return connection


def _blob_lister(environ: Mapping[str, str]) -> tuple[Callable[[str], datetime | None] | None, str | None]:
    account_url = str(environ.get("AZURE_STORAGE_ACCOUNT_URL", "")).strip()
    container = str(environ.get("MARKET_DATA_RAW_CONTAINER", "")).strip()
    if not (account_url and container):
        return None, "bronze-market-data not configured"
    try:
        from .cloud_storage import best_effort_credential, container_client, newest_blob_time

        client = container_client(
            account_url, container,
            credential=best_effort_credential(connection_timeout=3, read_timeout=5),
            connection_timeout=3, read_timeout=5, retry_total=0,
        )
        return (lambda prefix: newest_blob_time(client, prefix)), None
    except Exception as error:  # noqa: BLE001
        return None, _error_label(error)


def _measure(database_url: str, tenant_id: str, *, now: datetime, environ: Mapping[str, str],
             rules: tuple[Rule, ...], results: list[dict[str, Any]], budget: float) -> None:
    connection = None
    database_error = None
    try:
        try:
            connection = open_read_only(database_url, tenant_id)
        except Exception as error:  # noqa: BLE001
            database_error = _error_label(error)
        lister, storage_error = (None, None)
        if any(rule.feed for rule in rules):
            lister, storage_error = _blob_lister(environ)
        collect(connection, now=now, newest_blob_time=lister, rules=rules, time_budget_seconds=budget,
                results=results, database_error=database_error, storage_error=storage_error)
    finally:
        if connection is not None:
            try:
                connection.rollback()
                connection.close()
            except Exception:  # noqa: BLE001
                pass


def run_read_only(database_url: str, tenant_id: str, *, now: datetime | None = None,
                  environ: Mapping[str, str] | None = None,
                  time_budget_seconds: float = DEFAULT_TIME_BUDGET_SECONDS) -> list[dict[str, Any]]:
    """Collect every active rule within a hard time limit, connection included. Never
    raises. The work runs in a daemon thread; whatever is unfinished when the budget
    runs out is reported unavailable and the thread is left behind."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    rules = active_rules(environ)
    results: list[dict[str, Any]] = []
    failure: list[str] = []
    done = threading.Event()

    def work() -> None:
        try:
            _measure(database_url, tenant_id, now=now, environ=environ, rules=rules,
                     results=results, budget=time_budget_seconds)
        except BaseException as error:  # noqa: BLE001 - reported below
            failure.append(_error_label(error))
        finally:
            done.set()

    try:
        threading.Thread(target=work, name="layer-freshness", daemon=True).start()
        finished = done.wait(time_budget_seconds)
    except Exception as error:  # noqa: BLE001 - for example no thread could start
        finished, failure = True, [_error_label(error)]
    snapshot = list(results)
    if finished and not failure:
        return snapshot
    reason = failure[0] if failure else f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"
    measured = {result["check_id"] for result in snapshot}
    return snapshot + [evaluate(rule, None, now=now, error=reason, scope=SESSION_SCOPE)
                       for rule in rules if rule.check_id not in measured]


def main() -> int:
    database_url = os.environ.get(DATABASE_URL_ENV) or os.environ.get("DATABASE_URL", "")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    results = run_read_only(database_url, tenant_id)
    print(json.dumps({"summary": summary(results), "checks": results}, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
