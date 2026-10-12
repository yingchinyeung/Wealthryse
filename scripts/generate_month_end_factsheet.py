from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import queue_report_generation_run  # noqa: E402
from ibkr_etl.monthly_artifacts import (  # noqa: E402
    FACTSHEET_LAYOUT_VERSION,
    render_factsheet,
)
from ibkr_etl.reporting_snapshot import (  # noqa: E402
    active_month_end_snapshot_id,
    freeze_month_end_snapshot,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def previous_calendar_month_end(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Month-end factsheet automation is restricted to internal production")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    period_override = os.environ.get("REPORT_PERIOD_END", "").strip()
    period_end = (
        date.fromisoformat(period_override)
        if period_override
        else previous_calendar_month_end(datetime.now(HONG_KONG).date())
    )
    if period_end != previous_calendar_month_end(period_end + timedelta(days=1)):
        raise RuntimeError("REPORT_PERIOD_END must be a calendar month end")

    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    reporting_start = os.environ.get("REPORTING_PERIOD_START", "2025-01-20")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")
    template_blob = _required("FACTSHEET_TEMPLATE_BLOB")
    expected_template_sha = _required("FACTSHEET_TEMPLATE_SHA256").lower()
    template_version = f"sha256:{expected_template_sha}+layout:{FACTSHEET_LAYOUT_VERSION}"

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        released = connection.execute(
            """
            SELECT r.generation_run_id
            FROM report_generation_runs r
            JOIN reporting_snapshots s
              ON s.tenant_id = r.tenant_id AND s.snapshot_id = r.snapshot_id
            WHERE r.tenant_id = ? AND s.subject_id = ? AND s.period_end = ?
              AND r.report_type = 'factsheet'
              AND r.status IN ('released_internal', 'published')
            LIMIT 1
            """,
            (tenant_id, account_id, period_end.isoformat()),
        ).fetchone()
        if released is not None:
            print(
                canonical_json(
                    {
                        "status": "already_released",
                        "period_end": period_end.isoformat(),
                        "generation_run_id": int(released["generation_run_id"]),
                    }
                )
            )
            return 0

        template_content = download_blob_bytes(
            storage_url, container, template_blob, maximum_bytes=10 * 1024 * 1024
        )
        actual_template_sha = hashlib.sha256(template_content).hexdigest()
        if actual_template_sha != expected_template_sha:
            raise RuntimeError("Factsheet template SHA-256 does not match the approved version")

        snapshot_id = active_month_end_snapshot_id(
            connection,
            tenant_id=tenant_id,
            subject_id=account_id,
            period_end=period_end.isoformat(),
        )
        snapshot_action = "reused_existing"
        if snapshot_id is None:
            snapshot = freeze_month_end_snapshot(
                connection,
                tenant_id=tenant_id,
                subject_id=account_id,
                period_start=reporting_start,
                period_end=period_end.isoformat(),
                reporting_currency="USD",
            )
            snapshot_id = snapshot.snapshot_id
            snapshot_action = "created_new"
        row = connection.execute(
            """
            SELECT snapshot_payload_json, data_sha256
            FROM reporting_snapshots
            WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ?
              AND status IN ('frozen', 'approved')
            """,
            (tenant_id, snapshot_id, account_id),
        ).fetchone()
        if row is None:
            raise RuntimeError("Frozen month-end snapshot is unavailable")
        payload_text = str(row["snapshot_payload_json"])
        if hashlib.sha256(payload_text.encode("utf-8")).hexdigest() != row["data_sha256"]:
            raise RuntimeError("Frozen month-end snapshot integrity check failed")
        payload = json.loads(payload_text)

        with TemporaryDirectory(prefix="month-end-factsheet-") as directory:
            root = Path(directory)
            template_path = root / "approved-template.pdf"
            output_path = root / f"{account_id}_Factsheet_{period_end.isoformat()}.pdf"
            template_path.write_bytes(template_content)
            result = render_factsheet(
                payload,
                template_path=template_path,
                output_path=output_path,
            )
            content = output_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            blob_name = (
                f"review/{account_id}/{period_end.isoformat()}/{output_path.name}"
            )
            uploaded = upload_bytes(
                storage_url,
                container,
                blob_name,
                content,
                overwrite=True,
                content_type="application/pdf",
            )
            if not uploaded:
                raise RuntimeError("Private report storage is unavailable")

        run_id = queue_report_generation_run(
            connection,
            tenant_id=tenant_id,
            snapshot_id=snapshot_id,
            report_type="factsheet",
            template_id="U16088571-monthly-factsheet",
            template_version=template_version,
        )
        now = utc_now_iso()
        output_reference = f"{container}/{blob_name}"
        connection.execute(
            """
            UPDATE report_generation_runs
            SET status = 'awaiting_approval', output_reference = ?,
                output_sha256 = ?, completed_at = ?, error_message = NULL
            WHERE tenant_id = ? AND generation_run_id = ?
              AND status NOT IN ('released_internal', 'published')
            """,
            (output_reference, digest, now, tenant_id, run_id),
        )
        details = canonical_json(
            {
                "period_end": period_end.isoformat(),
                "snapshot_id": snapshot_id,
                "snapshot_action": snapshot_action,
                "generation_run_id": run_id,
                "output_reference": output_reference,
                "output_sha256": digest,
                "factsheet_layout_version": FACTSHEET_LAYOUT_VERSION,
                "template_sha256": expected_template_sha,
                "model_role": "gpt-5.6-sol-portfolio-analyst",
                "layout_authority": "deterministic-renderer",
            }
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'system', 'month-end-portfolio-analyst',
                      'month_end_factsheet_generated', ?, 'awaiting_owner_approval', ?)
            """,
            (tenant_id, now, account_id, details),
        )
        connection.commit()
        print(
            canonical_json(
                {
                    "status": "generated",
                    "period_end": period_end.isoformat(),
                    "snapshot_id": snapshot_id,
                    "snapshot_action": snapshot_action,
                    "generation_run_id": run_id,
                    "output_sha256": digest,
                    "layout_version": result["layout_version"],
                }
            )
        )
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
