from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from psycopg.rows import dict_row

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.autonomy_review import create_monthly_review  # noqa: E402
from ibkr_etl.burn_in import now_hkt_date  # noqa: E402
from ibkr_etl.util import utc_now_iso  # noqa: E402


def main() -> int:
    import psycopg

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL is required")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtext('wealthryse-monthly-authority-review')) AS locked"
        ).fetchone()
        if not locked or not locked["locked"]:
            print(json.dumps({"status": "SKIPPED", "reason": "review_already_running"}))
            return 0
        result = create_monthly_review(
            connection, today=now_hkt_date(), created_at=utc_now_iso()
        )
        connection.commit()
    print(json.dumps({"status": "AWAITING_OWNER_REVIEW", **result}, default=str, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
