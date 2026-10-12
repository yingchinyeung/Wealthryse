from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.operating_registry import (  # noqa: E402
    enqueue_work_plan,
    load_operating_targets,
    plan_month_end_work,
    require_current_report_assets,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def previous_calendar_month_end(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Month-end dispatch is restricted to internal production")
    database_url = _required("DATABASE_URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    override = os.environ.get("REPORT_PERIOD_END", "").strip()
    period_end = (
        date.fromisoformat(override)
        if override
        else previous_calendar_month_end(datetime.now(HONG_KONG).date())
    )
    if period_end != previous_calendar_month_end(period_end + timedelta(days=1)):
        raise RuntimeError("REPORT_PERIOD_END must be a calendar month end")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        targets = load_operating_targets(
            connection,
            tenant_id=tenant_id,
            capability="month_end_reporting",
            as_of=period_end,
            include_preview=False,
        )
        if not targets:
            raise RuntimeError("No active month-end reporting subject is registered")
        asset_versions: dict[str, list[dict[str, str]]] = {}
        for target in targets:
            assets = require_current_report_assets(
                connection, tenant_id=tenant_id, subject_id=target.subject_id
            )
            asset_versions[target.subject_id] = [
                {
                    "report_type": key[0],
                    "asset_role": key[1],
                    "version_label": str(value["version_label"]),
                    "sha256": str(value["sha256"]),
                }
                for key, value in sorted(assets.items())
            ]
        plan = plan_month_end_work(targets, period_end=period_end)
        work_item_ids = enqueue_work_plan(
            connection, tenant_id=tenant_id, work_plan=plan
        )
        summary = {
            "status": "queued",
            "period_end": period_end.isoformat(),
            "subject_count": len(targets),
            "subjects": [target.subject_id for target in targets],
            "asset_versions": asset_versions,
            "work_item_ids": work_item_ids,
        }
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'service', 'institutional-month-end-dispatcher',
                      'month_end_work_queued', NULL, 'queued', ?)
            """,
            (tenant_id, utc_now_iso(), canonical_json(summary)),
        )
        connection.commit()
        print(canonical_json(summary))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
