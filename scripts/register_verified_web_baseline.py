"""Register an operator-verified rollback target; never enable or expand autonomy.

Run only after live checks of the exact supplied Azure revision and image.
Does not change schedules, approval gates, kill switches or authority reviews.
"""
import json
import os
import re
from datetime import datetime, timezone

import psycopg


def main():
    app = os.environ["WEB_APP_NAME"]
    revision = os.environ["LAST_KNOWN_GOOD_REVISION"]
    digest = os.environ["LAST_KNOWN_GOOD_IMAGE_DIGEST"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or not revision.startswith(app + "--"):
        raise RuntimeError("Invalid verified release identity")
    tenant = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    now = datetime.now(timezone.utc).isoformat()
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (tenant + ':release-baseline',))
        connection.execute("""
            UPDATE autonomy_release_baselines SET status = 'retired', retired_at = %s
            WHERE tenant_id = %s AND app_name = %s AND status = 'last_known_good'
              AND revision_name <> %s
        """, (now, tenant, app, revision))
        connection.execute("""
            INSERT INTO autonomy_release_baselines
                (tenant_id, app_name, revision_name, image_digest, status, established_at, established_by)
            VALUES (%s, %s, %s, %s, 'last_known_good', %s, 'owner-authorized-system-audit-20261001')
            ON CONFLICT (tenant_id, app_name, revision_name) DO UPDATE SET
                image_digest = excluded.image_digest, status = 'last_known_good',
                established_at = excluded.established_at, established_by = excluded.established_by,
                retired_at = NULL
        """, (tenant, app, revision, digest, now))
    print(json.dumps({'status': 'verified_baseline_registered', 'revision': revision, 'image_digest': digest}))


if __name__ == '__main__':
    main()
