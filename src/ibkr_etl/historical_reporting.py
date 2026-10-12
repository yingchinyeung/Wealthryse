from __future__ import annotations

import calendar
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable

from .crisis_replay import refresh_crisis_replay
from .reporting_snapshot import ReportingSnapshotResult, freeze_month_end_snapshot
from .util import utc_now_iso


class HistoricalReportingError(RuntimeError):
    pass


@dataclass(frozen=True)
class HistoricalMonth:
    reporting_month: str
    period_end: str
    holdings_as_of: str


@dataclass(frozen=True)
class HistoricalBatchItem:
    reporting_month: str
    period_end: str
    data_as_of: str
    snapshot_id: str
    crisis_holdings_as_of: str
    factsheet_generation_run_id: int
    presentation_generation_run_id: int
    publication_allowed: bool
    publication_blockers: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_date(value: str | date) -> date:
    return value if isinstance(value, date) else date.fromisoformat(value)


def _month_key(value: date) -> str:
    return f"{value.year:04d}-{value.month:02d}"


def _calendar_month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def discover_historical_months(
    connection: Any,
    *,
    account_id: str,
    start_date: str | date,
    end_date: str | date,
) -> list[HistoricalMonth]:
    """Find every month-end holding date and reject incomplete month ranges."""

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise HistoricalReportingError("Historical reporting start date is after end date")
    replay_dates = discover_replay_dates(
        connection,
        account_id=account_id,
        start_date=start,
        end_date=end,
    )
    latest_by_month: dict[str, date] = {}
    for value in replay_dates:
        observed = _parse_date(value)
        latest_by_month[_month_key(observed)] = observed

    expected: list[str] = []
    cursor = start.replace(day=1)
    final_month = end.replace(day=1)
    while cursor <= final_month:
        expected.append(_month_key(cursor))
        cursor = _next_month(cursor)
    missing = [month for month in expected if month not in latest_by_month]
    if missing:
        raise HistoricalReportingError(
            "No common position and total-NAV observation exists for reporting "
            f"month(s): {', '.join(missing)}"
        )

    return [
        HistoricalMonth(
            reporting_month=month,
            period_end=_calendar_month_end(latest_by_month[month]).isoformat(),
            holdings_as_of=latest_by_month[month].isoformat(),
        )
        for month in expected
    ]


def discover_replay_dates(
    connection: Any,
    *,
    account_id: str,
    start_date: str | date,
    end_date: str | date,
) -> list[str]:
    """Return all valid position dates with a same-day total NAV in a range."""

    start = _parse_date(start_date)
    end = _parse_date(end_date)
    if start > end:
        raise HistoricalReportingError("Crisis replay start date is after end date")
    legacy_sqlite = connection.__class__.__module__.startswith("sqlite3") and connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_subjects'"
    ).fetchone() is None
    subject = None if legacy_sqlite else connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
        (account_id,),
    ).fetchone()
    if subject is not None and str(subject["subject_type"]) == "composite_portfolio":
        rows = connection.execute(
            """
            SELECT DISTINCT c.snapshot_date
            FROM synthetic_portfolio_components c
            WHERE c.portfolio_id = ? AND c.snapshot_date BETWEEN ? AND ?
              AND EXISTS (
                  SELECT 1 FROM subject_nav_series n
                  WHERE n.subject_id = c.portfolio_id
                    AND n.snapshot_date = c.snapshot_date
              )
            ORDER BY c.snapshot_date
            """,
            (account_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    elif legacy_sqlite:
        rows = connection.execute(
            """
            SELECT DISTINCT p.snapshot_date
            FROM position_snapshots p
            WHERE p.account_id = ? AND p.snapshot_date BETWEEN ? AND ?
              AND EXISTS (
                  SELECT 1 FROM nav_snapshots n
                  WHERE n.account_id = p.account_id
                    AND n.snapshot_date = p.snapshot_date
                    AND n.asset_class = 'TOTAL'
              )
            ORDER BY p.snapshot_date
            """,
            (account_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT DISTINCT p.snapshot_date
            FROM subject_position_series p
            WHERE p.subject_id = ? AND p.snapshot_date BETWEEN ? AND ?
              AND EXISTS (
                  SELECT 1 FROM subject_nav_series n
                  WHERE n.subject_id = p.subject_id
                    AND n.snapshot_date = p.snapshot_date
              )
            ORDER BY p.snapshot_date
            """,
            (account_id, start.isoformat(), end.isoformat()),
        ).fetchall()
    return [str(row["snapshot_date"]) for row in rows]


def queue_report_generation_run(
    connection: Any,
    *,
    tenant_id: str,
    snapshot_id: str,
    report_type: str,
    template_id: str,
    template_version: str,
) -> int:
    """Queue one idempotent report run, reviving only a previously failed run."""

    if report_type not in {"factsheet", "presentation"}:
        raise HistoricalReportingError(f"Unsupported historical report type: {report_type}")
    if not template_id.strip() or not template_version.strip():
        raise HistoricalReportingError("Template id and version must be non-empty")
    existing = connection.execute(
        """
        SELECT generation_run_id, status
        FROM report_generation_runs
        WHERE tenant_id = ? AND snapshot_id = ? AND report_type = ?
          AND template_id = ? AND template_version = ?
        ORDER BY generation_run_id DESC
        LIMIT 1
        """,
        (tenant_id, snapshot_id, report_type, template_id, template_version),
    ).fetchone()
    now = utc_now_iso()
    if existing is not None:
        run_id = int(existing["generation_run_id"])
        if str(existing["status"]) == "failed":
            connection.execute(
                """
                UPDATE report_generation_runs
                SET status = 'queued', output_reference = NULL,
                    output_sha256 = NULL, started_at = ?, completed_at = NULL,
                    error_message = NULL
                WHERE generation_run_id = ?
                """,
                (now, run_id),
            )
        return run_id
    row = connection.execute(
        """
        INSERT INTO report_generation_runs(
            tenant_id, snapshot_id, report_type, template_id,
            template_version, status, started_at
        ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
        RETURNING generation_run_id
        """,
        (tenant_id, snapshot_id, report_type, template_id, template_version, now),
    ).fetchone()
    if row is None:
        raise HistoricalReportingError("The report generation run was not queued")
    return int(row["generation_run_id"])


def run_historical_month_end_batch(
    connection: Any,
    *,
    tenant_id: str,
    account_id: str,
    period_start: str,
    start_date: str,
    end_date: str,
    reporting_currency: str,
    factsheet_template_id: str,
    factsheet_template_version: str,
    presentation_template_id: str,
    presentation_template_version: str,
    replay_refresher: Callable[..., dict[str, Any]] = refresh_crisis_replay,
    snapshot_freezer: Callable[..., ReportingSnapshotResult] = freeze_month_end_snapshot,
) -> dict[str, Any]:
    """Create date-specific replays, immutable snapshots, and report work items."""

    months = discover_historical_months(
        connection,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
    )
    # A historical report batch publishes one package per completed month, so
    # only its selected month-end holdings snapshots belong in this job.  The
    # former all-daily loop was both needlessly expensive and could make an
    # otherwise valid month-end batch fail on an unused inception-day snapshot
    # whose underlying sleeve did not yet have holdings.  Date-specific crisis
    # replays remain available through the separate analytics workflow.
    replay_dates = [month.holdings_as_of for month in months]
    shared_price_cache: dict[str, dict[str, float]] = {}
    persisted_price_symbols: set[str] = set()
    replays: dict[str, dict[str, Any]] = {}
    for replay_date in replay_dates:
        if account_id == 'alpha-world-t':
            if reporting_currency != 'HKD':
                raise HistoricalReportingError('T historical reports require HKD')
            from .alpha_world_t_analytics import load_risk_snapshot
            from .alpha_world_t_crisis import load as load_t_crisis
            core,_,_=load_risk_snapshot(connection,replay_date,tenant_id=tenant_id)
            replay=load_t_crisis(connection,core,tenant_id=tenant_id)
        else:
            replay = replay_refresher(
                connection,
                account_id=account_id,
                holdings_as_of=replay_date,
                price_cache=shared_price_cache,
                persisted_price_symbols=persisted_price_symbols,
            )
        if replay.get("holdings_as_of") != replay_date:
            raise HistoricalReportingError(
                f"Crisis replay requested {replay_date} but used "
                f"{replay.get('holdings_as_of')}"
            )
        replays[replay_date] = replay
    completed: list[HistoricalBatchItem] = []
    for month in months:
        try:
            replay = replays[month.holdings_as_of]
            if replay.get("holdings_as_of") != month.holdings_as_of:
                raise HistoricalReportingError(
                    f"Crisis replay for {month.reporting_month} used "
                    f"{replay.get('holdings_as_of')}, expected {month.holdings_as_of}"
                )
            snapshot = snapshot_freezer(
                connection,
                tenant_id=tenant_id,
                subject_id=account_id,
                period_start=period_start,
                period_end=month.period_end,
                reporting_currency=reporting_currency,
            )
            factsheet_id = queue_report_generation_run(
                connection,
                tenant_id=tenant_id,
                snapshot_id=snapshot.snapshot_id,
                report_type="factsheet",
                template_id=factsheet_template_id,
                template_version=factsheet_template_version,
            )
            presentation_id = queue_report_generation_run(
                connection,
                tenant_id=tenant_id,
                snapshot_id=snapshot.snapshot_id,
                report_type="presentation",
                template_id=presentation_template_id,
                template_version=presentation_template_version,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        completed.append(
            HistoricalBatchItem(
                reporting_month=month.reporting_month,
                period_end=month.period_end,
                data_as_of=snapshot.data_as_of,
                snapshot_id=snapshot.snapshot_id,
                crisis_holdings_as_of=month.holdings_as_of,
                factsheet_generation_run_id=factsheet_id,
                presentation_generation_run_id=presentation_id,
                publication_allowed=snapshot.publication_allowed,
                publication_blockers=snapshot.publication_blockers,
            )
        )
    return {
        "status": "success",
        "account_id": account_id,
        "requested_start": _parse_date(start_date).isoformat(),
        "requested_end": _parse_date(end_date).isoformat(),
        "crisis_dates_completed": len(replays),
        "months_completed": len(completed),
        "report_runs_queued": len(completed) * 2,
        "items": [item.as_dict() for item in completed],
    }
