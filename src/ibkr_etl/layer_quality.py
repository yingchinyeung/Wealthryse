"""Quality monitoring per medallion layer, with a target per layer (review item 7).

LAYER_QUALITY_MODE on the autonomous administrator says what happens:

- off (the default, also when unset): nothing runs. No query, no log line, no record.
- shadow: measures, records each result in quality_check_results (migration 072), logs one
  `LAYER_QUALITY {json}` line per run and raises only info findings, which the administrator
  records quietly and never announces.
- warn: the same, but a layer not ready by its target time, or a failed check of severity
  error, is a warning incident.

What is measured, and when (the administrator runs every five minutes):

- Readiness, on every run: has each layer's data for the last business day arrived, and was it
  in by the layer's target time? Judged from the freshness observations (layer_freshness), so it
  adds no query when the freshness check is on. Recorded at most twice a day per layer: when
  first seen ready, and as late if it is not ready at the target time.
- The daily pass, once a day after the daily chain (the first run from 10:30 UTC):
  row counts per business date against what is typical for the same kind of day (both
  markets open, one, or neither), schema drift against the data contracts, value checks (a NAV
  move beyond a band, positions dropping to zero), the contracts' quality rules that the
  publish gate has not already run that day, and every freshness result. Within a hard time
  limit, in one READ ONLY session.

TARGETS holds the target for each layer. `python -m ibkr_etl.layer_quality report` reads the
history and states each target met or missed, with the week's failed checks, as counts, check
ids and table names only. `python -m ibkr_etl.layer_quality` prints a dry run and records nothing.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import sys
import threading
import time as monotonic_time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import layer_freshness

MODE_ENV = "LAYER_QUALITY_MODE"
DATABASE_URL_ENV = "LAYER_QUALITY_DATABASE_URL"  # optional override for the checks' reads
CONTRACTS_DIR_ENV = "LAYER_QUALITY_CONTRACTS_DIR"  # optional; default <project>/infra/medallion/contracts
OFF, SHADOW, WARN = "off", "shadow", "warn"
OFF_VALUES = frozenset({"", "off", "false", "0", "no", "none", "disabled"})
LOG_PREFIX = "LAYER_QUALITY "
HISTORY_TABLE = "quality_check_results"
MONITOR, PUBLISH_GATE = "monitor", "publish_gate"
DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"

# Cadence and time limits. The administrator's replica timeout is 240 seconds.
DAILY_PASS_FROM = time(10, 30)  # UTC: after the daily chain (08:20 pull to the 09:20 crisis job)
DAILY_PASS_WINDOW = timedelta(minutes=5)  # without readable history, only the run in this window
DAILY_BUDGET_SECONDS = 30.0  # the daily pass's checks, connection included
TOTAL_BUDGET_SECONDS = 45.0  # everything one administrator run spends here
READINESS_BUDGET_SECONDS = 10.0  # measuring freshness when the freshness check is off
HISTORY_TIMEOUT_MS = 5000
READY_RESOLUTION = timedelta(minutes=10)  # two administrator runs

# Row counts. A day is compared only with earlier days of the same kind (both markets open, one,
# or neither), and only once it has MIN_BASELINE of them; until then it is 'learning'. A count
# fails only when it is outside the band of both the long and the short baseline, so a backfill
# that reaches only older days (an account in onboarding) does not move the judgement.
BUSINESS_MARKETS = ("XHKG", "XNYS")
LOOKBACK_DAYS = 42
BASELINE_SIZE = 20  # the long baseline: the newest earlier days of the same kind
SHORT_BASELINE = 5  # the short baseline
MIN_BASELINE = 10
LOW_RATIO = 0.5  # fewer than half the typical rows: rows are missing (error)
HIGH_RATIO = 2.0  # more than twice the typical rows (plus HIGH_SLACK): a jump (warning, report only)
HIGH_SLACK = 2

# Value checks. A subject is judged only once it has MIN_VALUE_HISTORY dates in the lookback.
NAV_BAND = Decimal("0.10")  # a NAV move beyond 10% between a subject's two newest dates
VALUE_LOOKBACK_DAYS = 42
MIN_VALUE_HISTORY = 10
DECIMAL_TEXT = r"^-?[0-9]+(\.[0-9]+)?$"
RLS_BYPASS_PHRASE = "bypasses row-level security"

KINDS = ("readiness", "freshness", "volume", "schema", "value", "rule")
STATES = frozenset({"pass", "fail", "ready", "late", "learning", "recorded", "skipped", "unavailable"})


def _flag_mode(value: str) -> tuple[str, str | None]:
    value = value.strip().lower()
    if value in OFF_VALUES:
        return OFF, None
    if value in (SHADOW, WARN):
        return value, None
    return SHADOW, f"unknown {MODE_ENV} value; ran as shadow"


def mode(environ: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """The mode, and a note when the value was not understood (then shadow)."""
    environ = os.environ if environ is None else environ
    return _flag_mode(str(environ.get(MODE_ENV, "")))


# ---------------------------------------------------------------- targets (the one place)

READY, CHECKS = "ready", "checks"


@dataclass(frozen=True)
class Target:
    target_id: str
    layer: str
    kind: str  # READY: data in by ready_by; CHECKS: no failed error check that day
    share: float  # of business days
    ready_by: time | None = None  # UTC, on the day after the business day
    checks: tuple[str, ...] = ()  # layer_freshness check ids that must show the business day's data
    calendar: str = "XNYS"  # whose business days count: IBKR statements follow US sessions

    def describe(self) -> str:
        days = f"{self.share:.0%} of business days"
        if self.kind == READY:
            return f"{self.layer} ready by {self.ready_by:%H:%M} UTC on {days}"
        return f"{self.layer} checks pass on {days}"


TARGETS: tuple[Target, ...] = (
    # The 08:20 UTC Flex pull stores the raw statement.
    Target("bronze.ready", "bronze", READY, 0.99, time(8, 45), ("bronze.raw_reports",)),
    # The pull loads NAV and positions; benchmark-daily (08:30) loads benchmark prices.
    Target("silver.ready", "silver", READY, 0.99, time(9, 0),
           ("silver.nav_snapshots", "silver.position_snapshots", "silver.benchmark_prices")),
    # Benchmark comparison 08:30, risk 08:55, crisis 09:20.
    Target("gold.ready", "gold", READY, 0.99, time(10, 0),
           ("gold.performance_comparison_daily", "gold.risk_analytics_snapshots",
            "gold.crisis_scenario_results", "gold.crisis_replay_daily")),
    Target("silver.checks", "silver", CHECKS, 0.95),
    Target("gold.checks", "gold", CHECKS, 0.95),
)
TARGET_WINDOW_DAYS = 91  # a target is judged over the last 13 weeks


def business_day_for(cycle: date, calendar: str = "XNYS") -> date | None:
    """The business day whose data the cycle on `cycle` brings (the pull asks up to yesterday),
    or None when yesterday was not a session. Raises CalendarCoverageError past the calendar."""
    from .market_calendar import CALENDARS

    day = cycle - timedelta(days=1)
    return day if CALENDARS[calendar].is_session(day) else None


def day_kind(day: date) -> str:
    """Which business markets were open: 'XHKG+XNYS', one of them, 'closed' or 'unknown'."""
    from .market_calendar import CALENDARS, CalendarCoverageError

    try:
        open_markets = [market for market in BUSINESS_MARKETS if CALENDARS[market].is_session(day)]
    except CalendarCoverageError:
        return "unknown"
    return "+".join(open_markets) or "closed"


# ---------------------------------------------------------------- what is checked


@dataclass(frozen=True)
class Volume:
    table: str  # catalog name without schema
    layer: str
    date_column: str | None = None  # the business date of each row; None: count the whole table
    judged: bool = True  # dated: against typical; whole table: must not shrink. False: recorded only
    count_column: str | None = None  # whole table: the one column the count reads
    qualified: bool = False  # created in its layer schema, with no public view: read as layer.table
    held: bool = False  # created by a held migration: skipped until that migration has run

    @property
    def check_id(self) -> str:
        return f"volume.{self.layer}.{self.table}"

    @property
    def target(self) -> str:
        return f"{self.layer}.{self.table}"

    @property
    def relation(self) -> str:
        """The name the count reads: the public view, or layer.table when there is none."""
        return self.target if self.qualified else self.table


# Every silver and gold table in infra/medallion/catalog.json (views are counted through their tables).
VOLUMES: tuple[Volume, ...] = (
    Volume("accounts", "silver", count_column="account_id"),
    Volume("benchmark_prices", "silver", "observation_date"),
    Volume("cash_snapshots", "silver", "snapshot_date"),
    # Events: the number per day varies by nature, so it is recorded, not judged.
    Volume("cash_transactions", "silver", "effective_date", judged=False),
    Volume("corporate_actions", "silver", "effective_date", judged=False),
    Volume("derived_portfolio_cash_balances", "silver", "snapshot_date"),
    Volume("derived_portfolio_cash_ledger", "silver", "event_date", judged=False),
    Volume("derived_portfolio_exclusions", "silver", "snapshot_date", judged=False),
    Volume("derived_portfolio_nav", "silver", "snapshot_date"),
    Volume("derived_portfolio_positions", "silver", "snapshot_date"),
    Volume("fx_rates", "silver", "rate_date"),
    Volume("fx_transactions", "silver", "report_date", judged=False),
    Volume("instrument_classifications", "silver", count_column="conid"),
    Volume("instruments", "silver", count_column="conid"),
    Volume("market_data_prices", "silver", "price_date"),
    Volume("nav_snapshots", "silver", "snapshot_date"),
    Volume("position_snapshots", "silver", "snapshot_date"),
    Volume("reference_fx_rates", "silver", "rate_date"),
    Volume("risk_free_rates", "silver", "rate_date"),
    Volume("synthetic_portfolio_components", "silver", "snapshot_date"),
    Volume("trades", "silver", "trade_date", judged=False),
    # Corrections history (U8, migration 074): append-only, so the whole table must not shrink.
    Volume("cash_transactions_history", "silver", count_column="version_id", qualified=True),
    Volume("fx_rates_history", "silver", count_column="version_id", qualified=True),
    Volume("fx_transactions_history", "silver", count_column="version_id", qualified=True),
    Volume("nav_snapshots_history", "silver", count_column="version_id", qualified=True),
    Volume("trades_history", "silver", count_column="version_id", qualified=True),
    # Certified figures (U5): builds and figures are append-only, so neither may shrink.
    Volume("certified_builds", "gold", count_column="build_id"),
    Volume("certified_figures", "gold", count_column="build_id"),
    Volume("crisis_replay_components", "gold", "holdings_as_of"),
    Volume("crisis_replay_daily", "gold", "holdings_as_of"),
    Volume("crisis_scenario_results", "gold", "holdings_as_of"),
    Volume("performance_comparison_daily", "gold", "observation_date"),
    Volume("reporting_snapshots", "gold", count_column="snapshot_id"),
    Volume("risk_analytics_snapshots", "gold", "as_of_date"),
)
# The columns each count reads, by relation, for logins that cannot read the whole table (bootstrap
# grants these to the approval login; a count needs SELECT on one column, row security needs none).
COUNT_COLUMNS = {v.relation: v.date_column or v.count_column for v in VOLUMES}

NAV_MOVE_SQL = f"""
WITH series AS (
    SELECT subject_id, currency, snapshot_date, total_text::numeric AS nav,
           row_number() OVER (PARTITION BY subject_id, currency ORDER BY snapshot_date DESC) AS position,
           count(*) OVER (PARTITION BY subject_id, currency) AS dates
    FROM subject_nav_series
    WHERE snapshot_date >= %s AND total_text ~ '{DECIMAL_TEXT}')
SELECT subject_id, currency, max(dates) AS dates,
       max(snapshot_date) FILTER (WHERE position = 1) AS newest_date,
       max(nav) FILTER (WHERE position = 1) AS newest,
       max(snapshot_date) FILTER (WHERE position = 2) AS previous_date,
       max(nav) FILTER (WHERE position = 2) AS previous
FROM series WHERE position <= 2 GROUP BY subject_id, currency ORDER BY subject_id, currency"""

# Per subject: its newest NAV date and the non-zero positions held that date, how many earlier
# NAV dates it has, on how many of them it held positions, and the last of those.
POSITIONS_SQL = f"""
WITH held AS (
    SELECT subject_id, snapshot_date,
           count(*) FILTER (WHERE quantity_text ~ '{DECIMAL_TEXT}' AND quantity_text::numeric <> 0) AS positions
    FROM subject_position_series WHERE snapshot_date >= %s GROUP BY subject_id, snapshot_date),
nav AS (SELECT DISTINCT subject_id, snapshot_date FROM subject_nav_series WHERE snapshot_date >= %s),
newest AS (SELECT subject_id, max(snapshot_date) AS nav_date FROM nav GROUP BY subject_id)
SELECT n.subject_id, n.nav_date,
       coalesce((SELECT h.positions FROM held h WHERE h.subject_id = n.subject_id
                   AND h.snapshot_date = n.nav_date), 0) AS positions,
       (SELECT count(*) FROM nav d WHERE d.subject_id = n.subject_id AND d.snapshot_date < n.nav_date)
           AS earlier_nav_dates,
       (SELECT count(*) FROM nav d JOIN held h ON h.subject_id = d.subject_id AND h.snapshot_date = d.snapshot_date
         WHERE d.subject_id = n.subject_id AND d.snapshot_date < n.nav_date AND h.positions > 0) AS held_nav_dates,
       (SELECT max(h.snapshot_date) FROM held h WHERE h.subject_id = n.subject_id AND h.positions > 0
          AND h.snapshot_date < n.nav_date) AS last_held,
       (SELECT max(d.snapshot_date) FROM nav d WHERE d.subject_id = n.subject_id AND d.snapshot_date < n.nav_date)
           AS previous_nav_date
FROM newest n ORDER BY n.subject_id"""
# An account's NAV rebuilt from silver on its newest date with cash (ibkr_etl.nav_rebuild): the days to
# rebuild, then that day's rows. Silver's tables, not the subject views, so only broker accounts.
NAV_REBUILD_DAYS = """
SELECT c.account_id, max(c.snapshot_date) AS snapshot_date
FROM cash_snapshots c
JOIN nav_snapshots n ON n.account_id = c.account_id AND n.snapshot_date = c.snapshot_date
                    AND UPPER(n.asset_class) IN ('TOTAL', 'ALL', '')
WHERE c.snapshot_date >= %s GROUP BY c.account_id"""
NAV_REBUILD_SQL = {
    "nav": f"""WITH days AS ({NAV_REBUILD_DAYS})
SELECT n.account_id, n.snapshot_date, n.currency, n.total_text, n.cash_text, n.stock_text, n.options_text,
       n.futures_text, n.bonds_text, n.accruals_text, a.base_currency
FROM days d JOIN nav_snapshots n ON n.account_id = d.account_id AND n.snapshot_date = d.snapshot_date
LEFT JOIN accounts a ON a.account_id = n.account_id
WHERE UPPER(n.asset_class) IN ('TOTAL', 'ALL', '')""",
    "positions": f"""WITH days AS ({NAV_REBUILD_DAYS})
SELECT p.account_id, p.conid, p.currency, p.quantity_text, p.mark_price_text, p.market_value_text
FROM days d JOIN position_snapshots p ON p.account_id = d.account_id AND p.snapshot_date = d.snapshot_date""",
    "cash": f"""WITH days AS ({NAV_REBUILD_DAYS})
SELECT c.account_id, c.currency, c.ending_cash_text, c.fx_rate_to_base_text
FROM days d JOIN cash_snapshots c ON c.account_id = d.account_id AND c.snapshot_date = d.snapshot_date""",
    "fx_rates": f"""WITH days AS ({NAV_REBUILD_DAYS})
SELECT f.account_id, f.from_currency, f.to_currency, f.rate_text
FROM days d JOIN fx_rates f ON f.account_id = d.account_id AND f.rate_date = d.snapshot_date""",
}
# Within one cent of IBKR's total reconciles. A warning while the check is new (until 30 days of clean
# history); then an error, as positions_to_zero is.
NAV_REBUILD_TOLERANCE = Decimal("0.01")
NAV_REBUILD_SEVERITY = "warning"

HELD_SHARE = 0.8  # positions normally exist on at least this share of a subject's NAV dates
# A subject with no NAV for a week (a closed account) is not judged, so an old move or drop
# is not reported again every day.
STALE_AFTER_DAYS = 7


@dataclass(frozen=True)
class ValueCheck:
    check_id: str
    layer: str
    target: str
    description: str


VALUE_CHECKS = (
    ValueCheck("value.nav_move", "silver", "silver.subject_nav_series",
               f"NAV moved more than {NAV_BAND:.0%} between a subject's two newest dates"),
    ValueCheck("value.positions_to_zero", "silver", "silver.subject_position_series",
               "A subject that held positions holds none on its newest dates"),
    ValueCheck("value.nav_rebuild", "silver", "silver.nav_snapshots",
               "A broker account's NAV rebuilt from its silver positions, cash and FX differs from the broker's "
               "own total on its newest date"),
)


def rule_check_id(physical_name: str, rule_name: str) -> str:
    """The history id of a contract quality rule; the publish gate records under the same id."""
    return f"rule.{physical_name}.{rule_name}"


@dataclass(frozen=True)
class ContractRule:
    check_id: str
    layer: str
    target: str  # physical name
    name: str
    query: str
    must_be: int
    severity: str
    column: str | None
    needs_rls_bypass: bool


def contracts_dir(environ: Mapping[str, str] | None = None) -> Path | None:
    """Where the data contracts are, or None. The job image needs infra/medallion copied in."""
    environ = os.environ if environ is None else environ
    configured = str(environ.get(CONTRACTS_DIR_ENV, "")).strip()
    if configured:
        candidates = [Path(configured)]
    else:
        roots = [environ.get("IBKR_PROJECT_ROOT"), Path(__file__).resolve().parents[2]]
        candidates = [Path(root) / "infra" / "medallion" / "contracts" for root in roots if root]
        # The job image copies infra/medallion to MEDALLION_DIR (/app/medallion).
        medallion = str(environ.get("MEDALLION_DIR", "")).strip()
        if medallion:
            candidates.insert(0, Path(medallion) / "contracts")
    return next((path for path in candidates if (path / "silver").is_dir()), None)


def held_relations(directory: Path) -> frozenset[str]:
    """layer.table of the catalog objects a held migration creates (catalog.json beside the contracts)."""
    catalog = Path(directory).parent / "catalog.json"
    if not catalog.is_file():
        return frozenset()
    entries = json.loads(catalog.read_text(encoding="utf-8")).get("postgres", {})
    return frozenset(f"{value.get('target_schema') or key.split('.', 1)[0]}.{key.split('.', 1)[1]}"
                     for key, value in entries.items() if value.get("held"))


def load_contracts(directory: Path) -> list[dict]:
    from . import medallion_contracts

    return [contract for _, contract in sorted(medallion_contracts.load_all(directory).items(),
                                               key=lambda item: str(item[0]))]


def contract_rules(contracts: Iterable[Mapping]) -> list[ContractRule]:
    from . import medallion_contracts

    rules = []
    for contract in contracts:
        obj = medallion_contracts.table_object(contract)
        physical = str(obj.get("physicalName"))
        found = [(None, rule) for rule in obj.get("quality") or ()]
        found += [(prop.get("name"), rule) for prop in obj.get("properties") or () for rule in prop.get("quality") or ()]
        for column, rule in found:
            if rule.get("type") != "sql" or "mustBe" not in rule:
                continue
            rules.append(ContractRule(
                rule_check_id(physical, str(rule["name"])), str(contract.get("domain")), physical,
                str(rule["name"]), str(rule["query"]), int(rule["mustBe"]), str(rule.get("severity")),
                column, RLS_BYPASS_PHRASE in str(rule.get("description", ""))))
    return rules


# ---------------------------------------------------------------- judging


def _result(check_id: str, kind: str, layer: str, target: str | None, state: str, *,
            severity: str | None = None, observed: Any = None, **detail: Any) -> dict[str, Any]:
    return {"check_id": check_id, "kind": kind, "layer": layer, "target": target, "state": state,
            "severity": severity, "observed": observed, "detail": detail}


def judge_volume(rule: Volume, counts: Mapping[str, int] | None, *, today: date,
                 total: int | None = None, previous_total: int | None = None) -> dict[str, Any]:
    """One table's row counts. `counts` maps ISO business dates to rows (dated tables)."""
    if rule.date_column is None:
        detail: dict[str, Any] = {"scope": "table"}
        if previous_total is None or not rule.judged:
            return _result(rule.check_id, "volume", rule.layer, rule.target, "recorded", observed=total, **detail)
        detail["previous_total"] = previous_total
        if total is not None and total < previous_total:
            return _result(rule.check_id, "volume", rule.layer, rule.target, "fail", severity="error",
                           observed=total, reason="the table shrank", **detail)
        return _result(rule.check_id, "volume", rule.layer, rule.target, "pass", observed=total, **detail)
    # Only complete days: the cycle loads data up to yesterday, and a day still in progress
    # (Asian closes before the US one) would look short.
    days: dict[date, int] = {}
    for key, rows in (counts or {}).items():
        try:
            day = date.fromisoformat(str(key)[:10])
        except ValueError:
            continue
        if day < today:
            days[day] = days.get(day, 0) + int(rows)
    if not days:
        return _result(rule.check_id, "volume", rule.layer, rule.target, "recorded", observed=0,
                       reason=f"no rows dated in the {LOOKBACK_DAYS} days before today")
    newest = max(days)
    kind = day_kind(newest)
    detail: dict[str, Any] = {"date": newest.isoformat(), "day_kind": kind}
    rows = days[newest]
    if not rule.judged:
        return _result(rule.check_id, "volume", rule.layer, rule.target, "recorded", observed=rows, **detail)
    if kind == "unknown":
        return _result(rule.check_id, "volume", rule.layer, rule.target, "skipped", observed=rows,
                       reason="the market calendar does not cover the date", **detail)
    earlier = [days[day] for day in sorted(days) if day < newest and day_kind(day) == kind]
    detail["baseline_days"] = min(len(earlier), BASELINE_SIZE)
    if len(earlier) < MIN_BASELINE:
        return _result(rule.check_id, "volume", rule.layer, rule.target, "learning", observed=rows, **detail)
    typical = statistics.median(earlier[-BASELINE_SIZE:])
    recent = statistics.median(earlier[-SHORT_BASELINE:])
    low = min(typical, recent) * LOW_RATIO
    high = max(typical, recent) * HIGH_RATIO + HIGH_SLACK
    detail.update(typical=typical, recent=recent, low=round(low, 1), high=round(high, 1))
    if rows < low:
        return _result(rule.check_id, "volume", rule.layer, rule.target, "fail", severity="error",
                       observed=rows, direction="low", **detail)
    if rows > high:
        return _result(rule.check_id, "volume", rule.layer, rule.target, "fail", severity="warning",
                       observed=rows, direction="high", **detail)
    return _result(rule.check_id, "volume", rule.layer, rule.target, "pass", observed=rows, **detail)


def _decimal(value: Any) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def mask(subject: Any) -> str:
    """Keep the last three characters of an account or portfolio id."""
    text = str(subject or "")
    return "***" + text[-3:]


def _stale(newest: Any, today: date | None) -> bool:
    if today is None or newest is None:
        return False
    try:
        return date.fromisoformat(str(newest)[:10]) < today - timedelta(days=STALE_AFTER_DAYS)
    except ValueError:
        return True


def judge_nav_moves(rows: Iterable[Mapping[str, Any]], *, band: Decimal = NAV_BAND,
                    today: date | None = None) -> dict[str, Any]:
    """Day-on-day NAV moves beyond the band. Deposits and withdrawals move NAV too, so this is a
    warning for the weekly report, never an error."""
    check = VALUE_CHECKS[0]
    failed, checked, learning, stale = [], 0, 0, 0
    for row in rows:
        newest, previous = _decimal(row.get("newest")), _decimal(row.get("previous"))
        if newest is None or previous is None or row.get("previous_date") is None:
            continue
        if _stale(row.get("newest_date"), today):
            stale += 1
            continue
        if int(row.get("dates") or 0) < MIN_VALUE_HISTORY:
            learning += 1
            continue
        checked += 1
        if previous <= 0:
            moved = newest != previous
            move = None
        else:
            move = newest / previous - 1
            moved = abs(move) > band
        if newest <= 0 < previous:
            moved = True
        if moved:
            failed.append({"subject": mask(row.get("subject_id")), "currency": row.get("currency"),
                           "from": str(row.get("previous_date")), "to": str(row.get("newest_date")),
                           "move_pct": None if move is None else float(round(move * 100, 1))})
    state = "fail" if failed else "pass" if checked else "learning"
    return _result(check.check_id, "value", check.layer, check.target, state,
                   severity="warning" if failed else None, observed=len(failed), subjects_checked=checked,
                   subjects_learning=learning, subjects_stale=stale, band=float(band), failed=failed)


def judge_positions(rows: Iterable[Mapping[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    """A subject that held positions on its previous NAV date and holds none on its newest one:
    reported once, the day it happens. Judged only for subjects with MIN_VALUE_HISTORY such dates
    (not new or in onboarding) whose positions exist on most NAV dates (not NAV-only dates such
    as weekends)."""
    check = VALUE_CHECKS[1]
    failed, checked, learning, stale = [], 0, 0, 0
    for row in rows:
        if row.get("nav_date") is None:
            continue
        if _stale(row.get("nav_date"), today):
            stale += 1
            continue
        held, earlier = int(row.get("held_nav_dates") or 0), int(row.get("earlier_nav_dates") or 0)
        if held < MIN_VALUE_HISTORY or held < HELD_SHARE * earlier:
            learning += 1
            continue
        checked += 1
        dropped = row.get("last_held") is not None and str(row.get("last_held")) == str(row.get("previous_nav_date"))
        if int(row.get("positions") or 0) == 0 and dropped:
            failed.append({"subject": mask(row.get("subject_id")), "nav_date": str(row["nav_date"]),
                           "last_held": None if row.get("last_held") is None else str(row["last_held"])})
    state = "fail" if failed else "pass" if checked else "learning"
    return _result(check.check_id, "value", check.layer, check.target, state,
                   severity="error" if failed else None, observed=len(failed), subjects_checked=checked,
                   subjects_learning=learning, subjects_stale=stale, failed=failed)


def judge_nav_rebuild(rows: Mapping[str, Iterable[Mapping[str, Any]]], *, today: date | None = None,
                      tolerance: Decimal = NAV_REBUILD_TOLERANCE) -> dict[str, Any]:
    """Each broker account's NAV on its newest date with cash, rebuilt from silver positions, cash and FX
    (ibkr_etl.nav_rebuild), against the broker's own total. Gaps are reported in basis points of the
    total, never as amounts."""
    from . import nav_rebuild

    check = VALUE_CHECKS[2]
    by_account: dict[str, dict[str, list]] = {}
    for part in ("nav", "positions", "cash", "fx_rates"):
        for row in rows.get(part) or ():
            by_account.setdefault(str(row["account_id"]), {"nav": [], "positions": [], "cash": [],
                                                           "fx_rates": []})[part].append(row)
    failed, checked, stale, sources = [], 0, 0, {}
    for account, parts in sorted(by_account.items()):
        totals = parts["nav"]
        base = next((str(r["base_currency"]) for r in totals if r.get("base_currency")), None)
        total = next((r for r in totals if base and r.get("currency") == base), None)
        if total is None and len(totals) == 1:
            total, base = totals[0], str(totals[0].get("currency"))
        if total is None:
            continue
        if _stale(total.get("snapshot_date"), today):
            stale += 1
            continue
        found = nav_rebuild.rebuild(total, parts["positions"], parts["cash"], parts["fx_rates"], base)
        if found is None:
            continue
        checked += 1
        sources[found["cash_source"]] = sources.get(found["cash_source"], 0) + 1
        if abs(found["gap"]) > tolerance:
            broker = found["broker_nav"]
            cash_gap = (found["cash_value"] - found["broker_cash"]) if found["broker_cash"] is not None else None
            failed.append({"subject": mask(account), "date": str(total.get("snapshot_date")),
                           "gap_bp": nav_rebuild.basis_points(found["gap"], broker),
                           "positions_gap_bp": nav_rebuild.basis_points(
                               found["positions_value"] - found["broker_positions_value"], broker),
                           "cash_gap_bp": None if cash_gap is None else nav_rebuild.basis_points(cash_gap, broker),
                           "cash_source": found["cash_source"], "missing_fx": found["missing_fx"],
                           "missing_prices": len(found["missing_price"])})
    state = "fail" if failed else "pass" if checked else "learning"
    return _result(check.check_id, "value", check.layer, check.target, state,
                   severity=NAV_REBUILD_SEVERITY if failed else None, observed=len(failed),
                   subjects_checked=checked, subjects_stale=stale, cash_sources=sources,
                   tolerance=float(tolerance), failed=failed)


def judge_schema(contract: Mapping, actual: Mapping | None) -> dict[str, Any]:
    from . import medallion_contracts

    obj = medallion_contracts.table_object(contract)
    physical = str(obj.get("physicalName"))
    diffs = medallion_contracts.compare(contract, actual)
    major = any(d.level == medallion_contracts.MAJOR for d in diffs)
    return _result(f"schema.{physical}", "schema", str(contract.get("domain")), physical,
                   "fail" if diffs else "pass", severity=("error" if major else "warning") if diffs else None,
                   observed=len(diffs), contract_version=str(contract.get("version")),
                   differences=[f"{d.message} ({d.level})" for d in diffs])


def judge_rule(rule: ContractRule, count: Any) -> dict[str, Any]:
    value = int(count)
    state = "pass" if value == rule.must_be else "fail"
    return _result(rule.check_id, "rule", rule.layer, rule.target, state,
                   severity=rule.severity if state == "fail" else None, observed=value,
                   rule=rule.name, column=rule.column, must_be=rule.must_be)


def _freshness_state(state: str) -> str:
    return {"fresh": "pass", "unavailable": "unavailable"}.get(state, "fail")


def freshness_results(results: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Freshness results as history rows (the bronze-market-data file checks included)."""
    rows = []
    for result in results:
        # "state" and "observed" are this row's own fields, so their freshness values are renamed.
        names = {"state": "freshness", "observed": "latest"}
        detail = {names.get(key, key): result.get(key) for key in (
            "state", "measure", "due", "observed", "earliest_expected", "required", "feed", "error")
            if result.get(key) is not None}
        state = _freshness_state(str(result.get("state")))
        rows.append(_result(str(result["check_id"]), "freshness", str(result.get("layer")), result.get("target"),
                            state, severity=("error" if result.get("required") else "warning")
                            if state == "fail" else None, **detail))
    return rows


# ---------------------------------------------------------------- readiness


def _check_ready(result: Mapping[str, Any] | None, cycle: date, business: date) -> dict[str, Any]:
    if result is None or result.get("state") == "unavailable":
        return {"ready": None, "error": (result or {}).get("error") or "not measured"}
    observed = result.get("observed")
    if observed is None:
        return {"ready": False, "observed": None}
    try:
        if result.get("measure") == layer_freshness.LOADED:
            start = datetime.combine(cycle, time(0), tzinfo=timezone.utc)
            ready = datetime.fromisoformat(str(observed)) >= start
        else:
            ready = date.fromisoformat(str(observed)[:10]) >= business
    except ValueError:
        return {"ready": None, "error": "unreadable value"}
    return {"ready": ready, "observed": str(observed)}


def readiness(results: Iterable[Mapping[str, Any]] | None, *, now: datetime) -> list[dict[str, Any]]:
    """Each READY target's state on this run: ready, waiting (before its time), late, not_due
    (no business day's data due this cycle) or unavailable (a check could not be read)."""
    from .market_calendar import CalendarCoverageError

    current = now.astimezone(timezone.utc)
    cycle = current.date()
    by_check = {str(r.get("check_id")): r for r in results or ()}
    items = []
    for target in TARGETS:
        if target.kind != READY:
            continue
        due_at = datetime.combine(cycle, target.ready_by, tzinfo=timezone.utc)
        item: dict[str, Any] = {"check_id": target.target_id, "layer": target.layer, "cycle_date": cycle.isoformat(),
                                "ready_by": due_at.isoformat(), "due": current >= due_at}
        try:
            business = business_day_for(cycle, target.calendar)
        except CalendarCoverageError:
            item.update(state="unavailable", error="the market calendar does not cover the date")
            items.append(item)
            continue
        if business is None:
            item["state"] = "not_due"
            items.append(item)
            continue
        checks = {check_id: _check_ready(by_check.get(check_id), cycle, business) for check_id in target.checks}
        values = [c["ready"] for c in checks.values()]
        item.update(business_day=business.isoformat(), checks=checks)
        if all(value is True for value in values):
            item["state"] = "ready"
        elif any(value is None for value in values):
            item["state"] = "unavailable"
        else:
            item["state"] = "late" if item["due"] else "waiting"
        items.append(item)
    return items


def readiness_records(items: Iterable[Mapping[str, Any]], recorded: Mapping[str, set[str]]) -> list[dict[str, Any]]:
    """Rows to record this run: a layer's first sighting ready, and one 'late' (or 'unavailable')
    row at its target time. `recorded` maps each target id to the states already recorded today."""
    rows = []
    for item in items:
        seen = recorded.get(item["check_id"], set())
        state = item["state"]
        detail = {key: item.get(key) for key in ("business_day", "ready_by", "checks", "error") if item.get(key)}
        if state == "ready" and "ready" not in seen:
            rows.append(_result(item["check_id"], "readiness", item["layer"], None, "ready", **detail))
        elif state in ("late", "unavailable") and item.get("due") and not seen & {"ready", "late", "unavailable"}:
            rows.append(_result(item["check_id"], "readiness", item["layer"], None, state,
                                severity="error" if state == "late" else None, **detail))
    return rows


# ---------------------------------------------------------------- measuring


class _Tuples:
    """medallion_contracts.introspect reads rows by position; the read-only session gives dicts."""

    def __init__(self, connection: Any):
        self.connection = connection

    def execute(self, query: str, params: Any = None) -> Any:
        from psycopg.rows import tuple_row

        return self.connection.cursor(row_factory=tuple_row).execute(query, params)


def _rows(connection: Any, query: str, params: Any = None) -> list[dict[str, Any]]:
    cursor = connection.execute(query, params)
    rows = cursor.fetchall()
    if rows and not isinstance(rows[0], Mapping):
        names = [column.name for column in cursor.description]
        return [dict(zip(names, row)) for row in rows]
    return [dict(row) for row in rows]


def _volume_sql(rule: Volume) -> str:
    if rule.date_column is None:
        return f"SELECT count(*) AS total FROM {rule.relation}"
    return (f"SELECT left({rule.date_column}, 10) AS day, count(*) AS total FROM {rule.relation} "
            f"WHERE {rule.date_column} >= %s AND {rule.date_column} <= %s GROUP BY 1")


@dataclass
class Plan:
    """What one daily pass measures, known before it starts."""

    volumes: tuple[Volume, ...] = VOLUMES
    contracts: tuple[dict, ...] = ()
    rules: tuple[ContractRule, ...] = ()
    contracts_error: str | None = None
    previous_totals: Mapping[str, int] | None = None
    gate_rules: frozenset[str] = frozenset()
    held: frozenset[str] = frozenset(v.target for v in VOLUMES if v.held)  # layer.table, held migrations

    def planned(self) -> list[tuple[str, str, str, str | None]]:
        """(check_id, kind, layer, target) of every check this pass makes."""
        from . import medallion_contracts

        items = [(v.check_id, "volume", v.layer, v.target) for v in self.volumes]
        items += [(c.check_id, "value", c.layer, c.target) for c in VALUE_CHECKS]
        for contract in self.contracts:
            physical = str(medallion_contracts.table_object(contract).get("physicalName"))
            items.append((f"schema.{physical}", "schema", str(contract.get("domain")), physical))
        items += [(r.check_id, "rule", r.layer, r.target) for r in self.rules if r.check_id not in self.gate_rules]
        return items


def plan(environ: Mapping[str, str] | None = None, *, previous_totals: Mapping[str, int] | None = None,
         gate_rules: Iterable[str] = ()) -> Plan:
    directory = contracts_dir(environ)
    if directory is None:
        return Plan(contracts_error="the data contracts are not in this image", previous_totals=previous_totals,
                    gate_rules=frozenset(gate_rules))
    try:
        contracts = load_contracts(directory)
        return Plan(contracts=tuple(contracts), rules=tuple(contract_rules(contracts)),
                    previous_totals=previous_totals, gate_rules=frozenset(gate_rules),
                    held=Plan.held | held_relations(directory))
    except Exception as error:  # noqa: BLE001 - reported as unavailable
        return Plan(contracts_error=f"the data contracts could not be read ({type(error).__name__})",
                    previous_totals=previous_totals, gate_rules=frozenset(gate_rules))


def _savepoint(connection: Any):
    transaction = getattr(connection, "transaction", None)
    if transaction is None:
        import contextlib
        return contextlib.nullcontext()
    return transaction()


def _bypasses_row_security(connection: Any) -> bool:
    try:
        with _savepoint(connection):
            rows = _rows(connection, "SELECT rolbypassrls OR rolsuper AS bypass FROM pg_roles "
                                     "WHERE rolname = current_user")
        return bool(rows and rows[0].get("bypass"))
    except Exception:  # noqa: BLE001
        return False


def measure_daily(connection: Any, pass_plan: Plan, *, now: datetime, results: list[dict[str, Any]],
                  time_budget_seconds: float = DAILY_BUDGET_SECONDS,
                  clock: Callable[[], float] = monotonic_time.monotonic) -> list[dict[str, Any]]:
    """Run every planned check on a read-only session. Never raises; a check that cannot be read,
    or that the time budget leaves unmeasured, is 'unavailable'. Results are appended as made."""
    from . import medallion_contracts

    started = clock()
    today = now.astimezone(timezone.utc).date()
    since = (today - timedelta(days=LOOKBACK_DAYS)).isoformat()
    until = (today + timedelta(days=1)).isoformat()
    value_since = (today - timedelta(days=VALUE_LOOKBACK_DAYS)).isoformat()

    def spent() -> bool:
        return clock() - started > time_budget_seconds

    def attempt(check_id: str, kind: str, layer: str, target: str | None, work: Callable[[], dict]) -> None:
        if spent():
            results.append(_result(check_id, kind, layer, target, "unavailable", error="time budget used",
                                   error_scope=layer_freshness.SESSION_SCOPE))
            return
        try:
            with _savepoint(connection):
                results.append(work())
        except Exception as error:  # noqa: BLE001 - reported, never raised
            results.append(_result(check_id, kind, layer, target, "unavailable",
                                   error=layer_freshness._error_label(error)))

    # Tables a held migration creates are skipped, not failed, until that migration has run.
    absent: set[str] = set()
    if pass_plan.held:
        try:
            with _savepoint(connection):
                absent = {str(row["relation"]) for row in _rows(
                    connection, "SELECT r AS relation FROM unnest(%s::text[]) AS r WHERE to_regclass(r) IS NULL",
                    (sorted(pass_plan.held),))}
        except Exception:  # noqa: BLE001 - each check then reports for itself
            absent = set()

    def not_yet(check_id: str, kind: str, layer: str, target: str | None) -> bool:
        if target not in absent:
            return False
        results.append(_result(check_id, kind, layer, target, "skipped",
                               reason="created by a held migration that has not run yet"))
        return True

    previous = pass_plan.previous_totals or {}
    for rule in pass_plan.volumes:
        if not_yet(rule.check_id, "volume", rule.layer, rule.target):
            continue

        def count(rule: Volume = rule) -> dict:
            if rule.date_column is None:
                total = _rows(connection, _volume_sql(rule))[0]["total"]
                return judge_volume(rule, None, today=today, total=int(total),
                                    previous_total=previous.get(rule.check_id))
            rows = _rows(connection, _volume_sql(rule), (since, until))
            return judge_volume(rule, {str(r["day"]): int(r["total"]) for r in rows}, today=today)
        attempt(rule.check_id, "volume", rule.layer, rule.target, count)

    nav, positions, rebuild = VALUE_CHECKS
    attempt(nav.check_id, "value", nav.layer, nav.target,
            lambda: judge_nav_moves(_rows(connection, NAV_MOVE_SQL, (value_since,)), today=today))
    attempt(positions.check_id, "value", positions.layer, positions.target,
            lambda: judge_positions(_rows(connection, POSITIONS_SQL, (value_since, value_since)), today=today))
    attempt(rebuild.check_id, "value", rebuild.layer, rebuild.target,
            lambda: judge_nav_rebuild({part: _rows(connection, query, (value_since,))
                                       for part, query in NAV_REBUILD_SQL.items()}, today=today))

    if pass_plan.contracts_error:
        for kind in ("schema", "rule"):
            results.append(_result(f"{kind}.contracts", kind, "silver", None, "unavailable",
                                   error=pass_plan.contracts_error, error_scope=layer_freshness.SESSION_SCOPE))
    tuples = _Tuples(connection)
    for contract in pass_plan.contracts:
        physical = str(medallion_contracts.table_object(contract).get("physicalName"))
        if not_yet(f"schema.{physical}", "schema", str(contract.get("domain")), physical):
            continue
        attempt(f"schema.{physical}", "schema", str(contract.get("domain")), physical,
                lambda contract=contract, physical=physical: judge_schema(
                    contract, medallion_contracts.introspect(tuples, physical)))

    bypass = _bypasses_row_security(connection) if pass_plan.rules else False
    for rule in pass_plan.rules:
        if rule.check_id in pass_plan.gate_rules:
            continue
        if not_yet(rule.check_id, "rule", rule.layer, rule.target):
            continue
        if rule.needs_rls_bypass and not bypass:
            results.append(_result(rule.check_id, "rule", rule.layer, rule.target, "skipped",
                                   rule=rule.name, reason="needs a login that bypasses row-level security"))
            continue
        attempt(rule.check_id, "rule", rule.layer, rule.target,
                lambda rule=rule: judge_rule(rule, tuples.execute(rule.query).fetchone()[0]))
    return results


def run_daily(database_url: str, tenant_id: str, pass_plan: Plan, *, now: datetime,
              time_budget_seconds: float = DAILY_BUDGET_SECONDS,
              connect: Callable[[str, str], Any] | None = None) -> list[dict[str, Any]]:
    """The daily pass within a hard time limit, connection included. Never raises. The work runs
    in a daemon thread; whatever is unfinished when the budget runs out is 'unavailable'."""
    connect = connect or layer_freshness.open_read_only
    results: list[dict[str, Any]] = []
    failure: list[str] = []
    done = threading.Event()

    def work() -> None:
        session = None
        try:
            session = connect(database_url, tenant_id)
            measure_daily(session, pass_plan, now=now, results=results, time_budget_seconds=time_budget_seconds)
        except BaseException as error:  # noqa: BLE001 - reported below
            failure.append(layer_freshness._error_label(error))
        finally:
            if session is not None:
                try:
                    session.rollback()
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
            done.set()

    try:
        threading.Thread(target=work, name="layer-quality", daemon=True).start()
        finished = done.wait(time_budget_seconds)
    except Exception as error:  # noqa: BLE001
        finished, failure = True, [layer_freshness._error_label(error)]
    snapshot = list(results)
    if finished and not failure:
        return snapshot
    reason = failure[0] if failure else f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"
    measured = {result["check_id"] for result in snapshot}
    return snapshot + [_result(check_id, kind, layer, target, "unavailable", error=reason,
                               error_scope=layer_freshness.SESSION_SCOPE)
                       for check_id, kind, layer, target in pass_plan.planned() if check_id not in measured]


# ---------------------------------------------------------------- history


INSERT_SQL = (f"INSERT INTO {HISTORY_TABLE}(checked_at, cycle_date, source, mode, kind, check_id, layer, target, "
              "state, severity, observed, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)")
TODAY_SQL = f"""
SELECT kind, check_id, state, source FROM {HISTORY_TABLE}
WHERE cycle_date = %s AND (kind = 'readiness' OR (source = 'publish_gate' AND kind = 'rule'))
UNION ALL
SELECT 'daily', 'daily', 'ran', 'monitor' FROM {HISTORY_TABLE}
WHERE cycle_date = %s AND source = 'monitor' AND kind <> 'readiness' HAVING count(*) > 0"""
PREVIOUS_TOTALS_SQL = f"""
SELECT DISTINCT ON (check_id) check_id, observed FROM {HISTORY_TABLE}
WHERE source = 'monitor' AND kind = 'volume' AND check_id = ANY(%s) AND cycle_date < %s
  AND cycle_date >= %s AND observed IS NOT NULL AND state <> 'unavailable'
ORDER BY check_id, checked_at DESC"""


def open_history(database_url: str, tenant_id: str) -> Any:
    """A read-write session for the history alone, with short timeouts, apart from the
    administrator's own transaction. The caller commits and closes it."""
    import psycopg
    from psycopg.rows import dict_row

    connection = psycopg.connect(database_url, connect_timeout=layer_freshness.CONNECT_TIMEOUT_SECONDS,
                                 row_factory=dict_row)
    try:
        connection.execute(
            "SELECT set_config('statement_timeout', %s, false), set_config('lock_timeout', %s, false), "
            "set_config('app.tenant_id', %s, false)",
            (str(HISTORY_TIMEOUT_MS), str(layer_freshness.LOCK_TIMEOUT_MS), tenant_id))
        connection.commit()
    except BaseException:
        connection.close()
        raise
    return connection


def read_today(connection: Any, cycle: date) -> dict[str, Any]:
    """Readiness states already recorded for the cycle, whether the daily pass ran, and the
    contract rules the publish gate ran."""
    rows = _rows(connection, TODAY_SQL, (cycle, cycle))
    recorded: dict[str, set[str]] = {}
    for row in rows:
        if row["kind"] == "readiness":
            recorded.setdefault(str(row["check_id"]), set()).add(str(row["state"]))
    return {"readiness": recorded,
            "daily_ran": any(row["kind"] == "daily" for row in rows),
            "gate_rules": frozenset(str(r["check_id"]) for r in rows if r["kind"] == "rule")}


def read_previous_totals(connection: Any, cycle: date) -> dict[str, int]:
    ids = [v.check_id for v in VOLUMES if v.date_column is None]
    rows = _rows(connection, PREVIOUS_TOTALS_SQL, (ids, cycle, cycle - timedelta(days=VALUE_LOOKBACK_DAYS)))
    return {str(row["check_id"]): int(row["observed"]) for row in rows}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def record(connection: Any, results: Iterable[Mapping[str, Any]], *, now: datetime, mode_name: str,
           source: str = MONITOR) -> int:
    """Append results to the history; the caller commits. A writer that runs contract rules
    (the publish gate, once wired) records them with source='publish_gate' and rule_check_id()
    ids, and the monitor then skips those rules that day."""
    current = now.astimezone(timezone.utc)
    rows = []
    for result in results:
        if result.get("state") not in STATES or result.get("kind") not in KINDS:
            raise ValueError(f"not a quality result: {result.get('check_id')}")
        observed = result.get("observed")
        rows.append((current, current.date(), source, mode_name, result["kind"], str(result["check_id"])[:300],
                     result["layer"], result.get("target"), result["state"], result.get("severity"),
                     None if observed is None else Decimal(str(observed)),
                     json.dumps(result.get("detail") or {}, sort_keys=True, default=_jsonable)))
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(INSERT_SQL, rows)
    return len(rows)


# ---------------------------------------------------------------- one administrator run


def summary(outcome: Mapping[str, Any]) -> dict[str, Any]:
    """The log line: states and check ids only, no subjects and no figures."""
    line: dict[str, Any] = {"mode": outcome.get("mode"), "cycle_date": outcome.get("cycle_date"),
                            "readiness": {i["check_id"]: i["state"] for i in outcome.get("readiness") or ()}}
    if outcome.get("mode_note"):
        line["mode_note"] = outcome["mode_note"]
    daily = outcome.get("daily")
    if daily is not None:
        counts: dict[str, int] = {}
        for result in daily:
            counts[result["state"]] = counts.get(result["state"], 0) + 1
        line["daily"] = {"checked": len(daily), "states": counts,
                         "failed": sorted(r["check_id"] for r in daily if r["state"] == "fail"),
                         "rules_run_by_gate": outcome.get("rules_run_by_gate", 0)}
    line["history"] = outcome.get("history")
    line["elapsed_ms"] = outcome.get("elapsed_ms")
    if outcome.get("error"):
        line["error"] = outcome["error"]
    return line


def run_in_administrator(database_url: str, tenant_id: str, *, now: datetime, mode_name: str,
                         freshness: Sequence[Mapping[str, Any]] | None = None,
                         environ: Mapping[str, str] | None = None, mode_note: str | None = None,
                         time_budget_seconds: float = TOTAL_BUDGET_SECONDS,
                         history: Callable[[str, str], Any] = open_history,
                         daily: Callable[..., list[dict[str, Any]]] = run_daily,
                         measure_freshness: Callable[..., list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """One administrator run: readiness every time, the daily pass when due, then the record.
    `database_url` is the administrator's own login, which writes the history; the checks read
    through LAYER_QUALITY_DATABASE_URL when it is set. `freshness` is this run's freshness
    results, measured here when the freshness check is off. Never raises; within a hard limit."""
    environ = os.environ if environ is None else environ
    started = monotonic_time.monotonic()
    cycle = now.astimezone(timezone.utc).date()
    outcome: dict[str, Any] = {"mode": mode_name, "cycle_date": cycle.isoformat(), "readiness": [], "daily": None,
                               "history": {"recorded": 0}}
    if mode_note:
        outcome["mode_note"] = mode_note
    reader_url = str(environ.get(DATABASE_URL_ENV) or database_url or "").strip()
    done = threading.Event()

    def work() -> None:
        session = None
        try:
            today = None
            try:
                session = history(database_url, tenant_id)
                today = read_today(session, cycle)
            except Exception as error:  # noqa: BLE001 - the checks still run; the log carries them
                outcome["history"] = {"error": layer_freshness._error_label(error)}
                if session is not None:
                    try:
                        session.rollback()
                    except Exception:  # noqa: BLE001
                        session = None
            observed = freshness
            if observed is None:
                measure = measure_freshness or layer_freshness.run_read_only
                observed = measure(reader_url, tenant_id, now=now, environ={},
                                   time_budget_seconds=READINESS_BUDGET_SECONDS)
            items = readiness(observed, now=now)
            outcome["readiness"] = items
            rows = readiness_records(items, (today or {}).get("readiness", {}))
            start = datetime.combine(cycle, DAILY_PASS_FROM, tzinfo=timezone.utc)
            current = now.astimezone(timezone.utc)
            due = current >= start and (current < start + DAILY_PASS_WINDOW if today is None
                                        else not today["daily_ran"])
            if due:
                previous = None
                if session is not None and today is not None:
                    try:
                        previous = read_previous_totals(session, cycle)
                    except Exception:  # noqa: BLE001
                        session.rollback()
                gate_rules = (today or {}).get("gate_rules", frozenset())
                pass_plan = plan(environ, previous_totals=previous, gate_rules=gate_rules)
                outcome["rules_run_by_gate"] = len([r for r in pass_plan.rules if r.check_id in gate_rules])
                results = daily(reader_url, tenant_id, pass_plan, now=now)
                outcome["daily"] = results
                rows += freshness_results(observed) + [r for r in results]
            if session is not None and today is not None and rows:
                try:
                    outcome["history"] = {"recorded": record(session, rows, now=now, mode_name=mode_name)}
                    session.commit()
                except Exception as error:  # noqa: BLE001
                    outcome["history"] = {"error": layer_freshness._error_label(error), "recorded": 0}
        except BaseException as error:  # noqa: BLE001 - reported in the outcome
            outcome["error"] = layer_freshness._error_label(error)
        finally:
            if session is not None:
                try:
                    session.rollback()
                    session.close()
                except Exception:  # noqa: BLE001
                    pass
            done.set()

    try:
        threading.Thread(target=work, name="layer-quality-run", daemon=True).start()
        if not done.wait(time_budget_seconds):
            outcome["error"] = f"TimeoutError (time budget of {time_budget_seconds:g} seconds)"
    except Exception as error:  # noqa: BLE001
        outcome["error"] = layer_freshness._error_label(error)
    outcome["elapsed_ms"] = int((monotonic_time.monotonic() - started) * 1000)
    return dict(outcome)


# ---------------------------------------------------------------- the weekly report


REPORT_SQL = f"""
SELECT cycle_date, kind, check_id, layer, target, state, severity, checked_at, source
FROM {HISTORY_TABLE}
WHERE cycle_date BETWEEN %s AND %s AND (kind = 'readiness' OR state IN ('fail', 'unavailable'))
UNION ALL
SELECT cycle_date, 'daily', 'daily', layer, NULL, 'ran', NULL, min(checked_at), 'monitor'
FROM {HISTORY_TABLE}
WHERE cycle_date BETWEEN %s AND %s AND source = 'monitor' AND kind NOT IN ('readiness', 'freshness')
GROUP BY cycle_date, layer
ORDER BY 1, 2, 3"""


def _week_ending(today: date) -> date:
    return today - timedelta(days=(today.weekday() + 1) % 7)  # the last Sunday on or before today


def _business_days(start: date, end: date, calendar: str) -> list[date]:
    from .market_calendar import CALENDARS, CalendarCoverageError

    days, day = [], start
    while day <= end:
        try:
            if CALENDARS[calendar].is_session(day):
                days.append(day)
        except CalendarCoverageError:
            pass
        day += timedelta(days=1)
    return days


def judge_days(target: Target, rows: Sequence[Mapping[str, Any]], days: Iterable[date]) -> dict[str, str]:
    """met, missed or unmeasured for each business day (the cycle the day after judges it)."""
    by_cycle: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_cycle.setdefault(str(row["cycle_date"])[:10], []).append(row)
    verdicts = {}
    for day in days:
        cycle = day + timedelta(days=1)
        found = by_cycle.get(cycle.isoformat(), [])
        if target.kind == READY:
            mine = [r for r in found if r["kind"] == "readiness" and r["check_id"] == target.target_id]
            due = datetime.combine(cycle, target.ready_by, tzinfo=timezone.utc) + READY_RESOLUTION
            ready = [r for r in mine if r["state"] == "ready"]
            if any(r["state"] == "late" for r in mine):
                verdict = "missed"
            elif any(_instant(r["checked_at"]) <= due for r in ready):
                verdict = "met"
            else:
                verdict = "unmeasured"
        else:
            ran = any(r["kind"] == "daily" and r["layer"] == target.layer for r in found)
            failed = any(r["state"] == "fail" and r["severity"] == "error" and r["layer"] == target.layer
                         and r["kind"] not in ("readiness", "freshness") for r in found)
            verdict = "unmeasured" if not ran else "missed" if failed else "met"
        verdicts[day.isoformat()] = verdict
    return verdicts


def _instant(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _tally(verdicts: Iterable[str]) -> dict[str, Any]:
    values = list(verdicts)
    met, missed = values.count("met"), values.count("missed")
    tally: dict[str, Any] = {"business_days": len(values), "met": met, "missed": missed,
                             "unmeasured": values.count("unmeasured")}
    tally["share"] = round(met / (met + missed), 4) if met + missed else None
    return tally


def report(rows: Sequence[Mapping[str, Any]], *, week_ending: date) -> dict[str, Any]:
    """The weekly report from history rows: counts, check ids and table names only."""
    week_start = week_ending - timedelta(days=6)
    window_start = week_ending - timedelta(days=TARGET_WINDOW_DAYS - 1)
    targets = []
    for target in TARGETS:
        week = judge_days(target, rows, _business_days(week_start, week_ending, target.calendar))
        window = judge_days(target, rows, _business_days(window_start, week_ending, target.calendar))
        week_tally, window_tally = _tally(week.values()), _tally(window.values())
        share = window_tally["share"]
        status = "not measured" if share is None else "met" if share >= target.share else "missed"
        targets.append({"target_id": target.target_id, "target": target.describe(), "layer": target.layer,
                        "status": status, "week": week_tally, "last_13_weeks": window_tally})
    in_week = [r for r in rows if week_start + timedelta(days=1) <= date.fromisoformat(str(r["cycle_date"])[:10])
               <= week_ending + timedelta(days=1)]
    failed: dict[tuple, set[str]] = {}
    unavailable: set[str] = set()
    for row in in_week:
        key = (row["layer"], row["kind"], row["check_id"], row.get("target"), row.get("severity"), row["source"])
        if row["state"] == "fail" and row["kind"] != "readiness":
            failed.setdefault(key, set()).add(str(row["cycle_date"])[:10])
        elif row["state"] == "unavailable" and row["kind"] != "readiness":
            unavailable.add(str(row["check_id"]))
    return {
        "week": {"from": week_start.isoformat(), "to": week_ending.isoformat()},
        "targets": targets,
        "failed_checks": [{"layer": k[0], "kind": k[1], "check_id": k[2], "table": k[3], "severity": k[4],
                           "source": k[5], "days": len(days)} for k, days in sorted(failed.items(), key=str)],
        "unavailable_checks": {"count": len(unavailable), "check_ids": sorted(unavailable)},
    }


def render(weekly: Mapping[str, Any]) -> str:
    """The weekly report as plain text, ready to paste."""
    week = weekly["week"]
    lines = [f"Layer quality, week {week['from']} to {week['to']}", "", "Targets (last 13 weeks; this week)"]
    for item in weekly["targets"]:
        window, this = item["last_13_weeks"], item["week"]
        share = "no measured days" if window["share"] is None else f"{window['share']:.1%}"
        lines.append(f"- {item['target']}: {item['status'].upper()} ({window['met']} of "
                     f"{window['met'] + window['missed']} measured days, {share}; {window['unmeasured']} unmeasured. "
                     f"This week {this['met']} met, {this['missed']} missed, {this['unmeasured']} unmeasured)")
    lines += ["", "Failed checks this week"]
    lines += [f"- {f['layer']} {f['kind']} {f['check_id']} ({f['severity'] or 'n/a'}, {f['source']}): "
              f"{f['days']} day(s)" for f in weekly["failed_checks"]] or ["- none"]
    unavailable = weekly["unavailable_checks"]
    lines += ["", f"Checks that could not be read this week: {unavailable['count']}"]
    lines += [f"- {check_id}" for check_id in unavailable["check_ids"]]
    return "\n".join(lines)


def weekly_report(connection: Any, *, week_ending: date) -> dict[str, Any]:
    start = week_ending - timedelta(days=TARGET_WINDOW_DAYS - 1)
    end = week_ending + timedelta(days=1)
    return report(_rows(connection, REPORT_SQL, (start, end, start, end)), week_ending=week_ending)


# ---------------------------------------------------------------- command line


def _environment() -> tuple[str, str, str]:
    database_url = os.environ.get("DATABASE_URL", "")
    reader = os.environ.get(DATABASE_URL_ENV) or database_url
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    return database_url, reader, tenant_id


def dry_run(*, now: datetime | None = None, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Readiness and the daily pass, now, whatever the time and mode. Records nothing."""
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc)
    reader = str(environ.get(DATABASE_URL_ENV) or environ.get("DATABASE_URL") or "")
    tenant_id = str(environ.get("PORTFOLIO_TENANT_ID") or DEFAULT_TENANT_ID)
    observed = layer_freshness.run_read_only(reader, tenant_id, now=now, environ={})
    daily = run_daily(reader, tenant_id, plan(environ), now=now)
    outcome = {"mode": mode(environ)[0], "cycle_date": now.astimezone(timezone.utc).date().isoformat(),
               "readiness": readiness(observed, now=now), "daily": daily, "history": "dry run: nothing recorded"}
    return {"summary": summary(outcome), "readiness": outcome["readiness"], "checks": daily}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "report":
        import argparse

        parser = argparse.ArgumentParser(prog="python -m ibkr_etl.layer_quality report")
        parser.add_argument("--week-ending", type=date.fromisoformat,
                            help="the Sunday the week ends on (default: the last Sunday)")
        parser.add_argument("--json", action="store_true", help="print JSON instead of text")
        args = parser.parse_args(argv[1:])
        week_ending = args.week_ending or _week_ending(datetime.now(timezone.utc).date())
        _, reader, tenant_id = _environment()
        session = layer_freshness.open_read_only(reader, tenant_id)
        try:
            weekly = weekly_report(session, week_ending=week_ending)
        finally:
            session.rollback()
            session.close()
        print(json.dumps(weekly, indent=1, sort_keys=True) if args.json else render(weekly))
        return 0
    if argv:
        print("usage: python -m ibkr_etl.layer_quality [report [--week-ending YYYY-MM-DD] [--json]]",
              file=sys.stderr)
        return 2
    print(json.dumps(dry_run(), indent=1, sort_keys=True, default=_jsonable))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
