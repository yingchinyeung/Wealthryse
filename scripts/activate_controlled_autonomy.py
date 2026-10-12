from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.autonomy_review import next_review_due  # noqa: E402
from ibkr_etl.util import utc_now_iso  # noqa: E402


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    import psycopg

    database_url = required("DATABASE_URL")
    app_name = required("WEB_APP_NAME")
    revision_name = required("LAST_KNOWN_GOOD_REVISION")
    image_digest = required("LAST_KNOWN_GOOD_IMAGE_DIGEST")
    if not image_digest.startswith("sha256:") or len(image_digest) != 71:
        raise RuntimeError("LAST_KNOWN_GOOD_IMAGE_DIGEST must be a SHA-256 digest")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    now = datetime.now(timezone.utc)
    activated_at = utc_now_iso()
    due_at = next_review_due(now.date()).isoformat()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        connection.execute(
            """
            UPDATE autonomy_release_baselines SET status = 'retired', retired_at = %s
            WHERE app_name = %s AND status = 'last_known_good'
              AND revision_name <> %s
            """,
            (activated_at, app_name, revision_name),
        )
        connection.execute(
            """
            INSERT INTO autonomy_release_baselines(
                app_name, revision_name, image_digest, status, established_at, established_by
            ) VALUES (%s, %s, %s, 'last_known_good', %s, 'owner-approved-stage-3')
            ON CONFLICT (tenant_id, app_name, revision_name) DO UPDATE SET
                image_digest = excluded.image_digest, status = 'last_known_good',
                established_at = excluded.established_at,
                established_by = excluded.established_by, retired_at = NULL
            """,
            (app_name, revision_name, image_digest, activated_at),
        )
        connection.execute(
            """
            UPDATE autonomy_control SET enabled = true, kill_switch = false,
                production_active = true, production_activated_at = %s,
                automatic_rollback_enabled = true, rollback_failure_threshold = 3,
                monthly_review_due_at = %s, updated_at = %s,
                updated_by = 'owner-approved-stage-3-and-5'
            WHERE tenant_id = app_tenant_id()
            """,
            (activated_at, due_at, activated_at),
        )
        connection.commit()
    print(json.dumps({
        "status": "ACTIVE",
        "app_name": app_name,
        "last_known_good_revision": revision_name,
        "monthly_review_due_at": due_at,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
