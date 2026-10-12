from __future__ import annotations

import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.db import DEFAULT_TENANT_ID, connect  # noqa: E402
from ibkr_etl.operating_registry import (  # noqa: E402
    enqueue_work_plan,
    load_active_flex_bindings,
    plan_flex_work,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Institutional dispatch is restricted to internal production")
    database_url = _required("DATABASE_URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    hkt_today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    run_date = date.fromisoformat(
        os.environ.get("WORK_RUN_DATE", hkt_today.isoformat())
    )
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        bindings = load_active_flex_bindings(
            connection, tenant_id=tenant_id, as_of=run_date
        )
        work_plan = plan_flex_work(bindings, run_date=run_date)
        work_item_ids = enqueue_work_plan(
            connection, tenant_id=tenant_id, work_plan=work_plan
        )
        summary = {
            "status": "queued",
            "run_date": run_date.isoformat(),
            "source_count": len(
                {
                    item.flex_source_id
                    for item in bindings
                }
            ),
            "subject_count": len(bindings),
            "work_item_count": len(work_plan),
            "work_item_ids": work_item_ids,
        }
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'service', 'institutional-daily-dispatcher',
                      'institutional_daily_work_queued', NULL, 'queued', ?)
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
