from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import Settings
from .cloud_storage import upload_bytes
from .db import (
    connect,
    finish_run_failure,
    finish_run_success,
    initialize,
    start_run,
    transaction,
)
from .flex_client import FlexClient
from .flex_guard import guarded_flex_request
from .loader import load_report
from .parser import parse_flex_xml
from .reconcile import reconcile_run
from .util import canonical_json, sha256_bytes, utc_now_iso


LOGGER = logging.getLogger(__name__)


def _archive_report(
    settings: Settings,
    content: bytes,
    from_date: date | None,
    to_date: date | None,
) -> tuple[str, str]:
    digest = sha256_bytes(content)
    settings.raw_report_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(settings.timezone)
    date_part = f"{from_date or 'unknown'}_{to_date or 'unknown'}"
    path = settings.raw_report_dir / f"flex_{date_part}_{now:%Y%m%dT%H%M%S}_{digest[:12]}.xml"
    # Exclusive creation prevents two workers from overwriting the same source.
    # An existing name is not proof that its bytes match this received payload.
    try:
        with path.open("xb") as archive:
            archive.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise RuntimeError("IBKR source archive collision or integrity failure; original retained")
    location = str(path)
    if settings.azure_storage_account_url:
        blob_name = f"{settings.tenant_id}/{now:%Y/%m/%d}/{path.name}"
        uploaded = upload_bytes(
            settings.azure_storage_account_url,
            settings.azure_raw_container,
            blob_name,
            content,
            overwrite=False,
            content_type="application/xml",
        )
        if uploaded:
            location = uploaded
    return digest, location


def ingest_content(
    settings: Settings,
    content: bytes,
    *,
    run_type: str,
    requested_from: date | None = None,
    requested_to: date | None = None,
    reference_code: str | None = None,
    content_type: str = "application/xml",
    existing_raw_path: Path | None = None,
) -> dict:
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    run_id = start_run(
        connection,
        run_type,
        requested_from.isoformat() if requested_from else None,
        requested_to.isoformat() if requested_to else None,
    )
    try:
        if existing_raw_path is None:
            digest, raw_path = _archive_report(settings, content, requested_from, requested_to)
        else:
            resolved_source = existing_raw_path.resolve(strict=True)
            if resolved_source.read_bytes() != content:
                raise RuntimeError("Replay content does not match the retained IBKR source")
            digest, raw_path = sha256_bytes(content), str(resolved_source)
        report = parse_flex_xml(content)
        with transaction(connection):
            connection.execute(
                """
                INSERT INTO raw_reports(
                    run_id, received_at, content_sha256, content_type, byte_count, file_path
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_sha256) DO NOTHING
                """,
                (run_id, utc_now_iso(), digest, content_type, len(content), str(raw_path)),
            )
            section_counts = load_report(connection, report, run_id)
        reconciliation = reconcile_run(connection, run_id)
        reconciliation["record_types"] = section_counts
        finish_run_success(
            connection,
            run_id,
            reference_code=reference_code,
            raw_sha256=digest,
            raw_path=str(raw_path),
            row_count=len(report.rows),
            reconciliation_json=canonical_json(reconciliation),
        )
        return {"run_id": run_id, "raw_path": str(raw_path), "raw_sha256": digest,
                "reconciliation": reconciliation}
    except Exception as exc:
        finish_run_failure(connection, run_id, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        connection.close()


def fetch_and_ingest(settings: Settings, from_date: date, to_date: date, run_type: str) -> dict:
    client = FlexClient(settings)
    with guarded_flex_request(settings):
        report = client.request_report(from_date, to_date)
    return ingest_content(
        settings,
        report.content,
        run_type=run_type,
        requested_from=from_date,
        requested_to=to_date,
        reference_code=report.reference_code,
        content_type=report.content_type,
    )


def daily_window(settings: Settings) -> tuple[date, date]:
    today = datetime.now(settings.timezone).date()
    to_date = today - timedelta(days=1)
    from_date = to_date - timedelta(days=settings.daily_lookback_days - 1)
    return from_date, to_date


def backfill_windows(from_date: date, to_date: date, max_days: int = 365) -> list[tuple[date, date]]:
    if from_date > to_date:
        raise ValueError("from date must not be after to date")
    windows: list[tuple[date, date]] = []
    cursor = from_date
    while cursor <= to_date:
        end = min(cursor + timedelta(days=max_days - 1), to_date)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return windows


def monthly_windows(from_date: date, to_date: date) -> list[tuple[date, date]]:
    if from_date > to_date:
        raise ValueError("from date must not be after to date")
    windows: list[tuple[date, date]] = []
    cursor = from_date
    while cursor <= to_date:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        end = min(next_month - timedelta(days=1), to_date)
        windows.append((cursor, end))
        cursor = next_month
    return windows


def missing_position_snapshot_dates(
    settings: Settings,
    from_date: date,
    to_date: date,
    source_account_id: str,
) -> list[date]:
    """Return source NAV dates that do not yet have a position snapshot.

    Flex Open Positions are reported as of the statement date. A multi-day Flex
    query therefore cannot reconstruct every historical day's holdings. Exact
    one-day queries are required for the dates identified here.
    """

    if from_date > to_date:
        raise ValueError("from date must not be after to date")
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    connection = connect(
        settings.database_path, settings.database_url, settings.tenant_id
    )
    try:
        account = connection.execute(
            "SELECT account_id FROM accounts WHERE account_id = ?",
            (source_account_id,),
        ).fetchone()
        if account is None:
            raise ValueError(f"Source account does not exist: {source_account_id}")
        rows = connection.execute(
            """
            SELECT DISTINCT nav.snapshot_date
            FROM v_daily_nav AS nav
            WHERE nav.account_id = ?
              AND nav.snapshot_date >= ?
              AND nav.snapshot_date <= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM position_snapshots AS positions
                  WHERE positions.account_id = nav.account_id
                    AND positions.snapshot_date = nav.snapshot_date
              )
            ORDER BY nav.snapshot_date
            """,
            (source_account_id, from_date.isoformat(), to_date.isoformat()),
        ).fetchall()
        return [date.fromisoformat(row[0]) for row in rows]
    finally:
        connection.close()


def backfill_daily_position_snapshots(
    settings: Settings,
    from_date: date,
    to_date: date,
    source_account_id: str,
    *,
    pause_seconds: float = 10.0,
    max_dates: int | None = None,
) -> dict:
    """Fetch one Flex statement for every missing source-account NAV date.

    Completed dates are committed independently, making this operation safely
    resumable. The default delay respects IBKR's limit of 10 Flex requests per
    minute per token.
    """

    if pause_seconds < settings.flex_min_send_interval_seconds:
        raise ValueError(
            "pause seconds must be at least the configured Flex minimum send interval"
        )
    if max_dates is not None and max_dates < 1:
        raise ValueError("max dates must be at least 1")

    missing_before = missing_position_snapshot_dates(
        settings, from_date, to_date, source_account_id
    )
    selected = missing_before[:max_dates] if max_dates is not None else missing_before
    completed: list[dict[str, object]] = []
    previous_request_started: float | None = None
    for index, snapshot_date in enumerate(selected, start=1):
        if previous_request_started is not None:
            elapsed = time.monotonic() - previous_request_started
            remaining_pause = pause_seconds - elapsed
            if remaining_pause > 0:
                time.sleep(remaining_pause)
        LOGGER.info(
            "Daily position backfill %s/%s for %s",
            index,
            len(selected),
            snapshot_date,
        )
        previous_request_started = time.monotonic()
        result = fetch_and_ingest(
            settings,
            snapshot_date,
            snapshot_date,
            "daily-position-snapshot",
        )
        connection = connect(
            settings.database_path, settings.database_url, settings.tenant_id
        )
        try:
            row = connection.execute(
                """
                SELECT COUNT(*)
                FROM position_snapshots
                WHERE account_id = ? AND snapshot_date = ?
                """,
                (source_account_id, snapshot_date.isoformat()),
            ).fetchone()
            position_count = int(row[0])
        finally:
            connection.close()
        if position_count == 0:
            raise RuntimeError(
                "IBKR returned no source-account positions for exact statement date "
                f"{snapshot_date}; the backfill stopped without estimating data"
            )
        completed.append(
            {
                "snapshot_date": snapshot_date.isoformat(),
                "run_id": result["run_id"],
                "position_count": position_count,
            }
        )

    remaining = missing_position_snapshot_dates(
        settings, from_date, to_date, source_account_id
    )
    return {
        "source_account_id": source_account_id,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "missing_before": len(missing_before),
        "attempted": len(selected),
        "completed": len(completed),
        "remaining": len(remaining),
        "completed_dates": completed,
        "status": "complete" if not remaining else "partial",
    }


def status(settings: Settings, limit: int = 10) -> list[dict]:
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        rows = connection.execute(
            """
            SELECT run_id, run_type, requested_from, requested_to, started_at,
                   completed_at, status, row_count, error_message
            FROM ingestion_runs
            ORDER BY run_id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        connection.close()
