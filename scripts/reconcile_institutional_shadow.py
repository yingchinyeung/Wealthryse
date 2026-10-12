from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ibkr_etl.config import load_settings
from ibkr_etl.db import connect
from ibkr_etl.shadow_reconciliation import (
    load_shadow_targets,
    reconcile_shadow_plan,
    record_shadow_reconciliation,
)


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Shadow reconciliation is restricted to internal production")
    manifest_text = os.environ.get("AUTHORITATIVE_JOB_MANIFEST_JSON", "").strip()
    if not manifest_text:
        raise RuntimeError("AUTHORITATIVE_JOB_MANIFEST_JSON is required")
    manifest = json.loads(manifest_text)
    if not isinstance(manifest, dict):
        raise RuntimeError("Authoritative job manifest must be a JSON object")
    settings = load_settings()
    hkt_today = datetime.now(ZoneInfo("Asia/Hong_Kong")).date()
    run_date = date.fromisoformat(
        os.environ.get("SHADOW_RUN_DATE", hkt_today.isoformat())
    )
    connection = connect(
        Path(settings.database_path), settings.database_url, settings.tenant_id
    )
    try:
        work_before = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM institutional_work_items "
                "WHERE tenant_id = ?",
                (settings.tenant_id,),
            ).fetchone()["count"]
        )
        targets = load_shadow_targets(
            connection, tenant_id=settings.tenant_id, as_of=run_date
        )
        evidence = reconcile_shadow_plan(targets, manifest)
        work_after = int(
            connection.execute(
                "SELECT COUNT(*) AS count FROM institutional_work_items "
                "WHERE tenant_id = ?",
                (settings.tenant_id,),
            ).fetchone()["count"]
        )
        if work_before != work_after:
            evidence["mismatches"].append(
                {
                    "code": "shadow_mutated_work_queue",
                    "before": work_before,
                    "after": work_after,
                }
            )
            evidence["status"] = "fail"
        evidence["institutional_work_items_before"] = work_before
        evidence["institutional_work_items_after"] = work_after
        reconciliation_id = record_shadow_reconciliation(
            connection,
            tenant_id=settings.tenant_id,
            run_date=run_date,
            evidence=evidence,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    output = {
        "status": evidence["status"],
        "reconciliation_id": reconciliation_id,
        "run_date": run_date.isoformat(),
        "manifest_sha256": evidence["manifest_sha256"],
        "registry_sha256": evidence["registry_sha256"],
        "active_subjects": evidence["active_subjects"],
        "preview_subjects": evidence["preview_subjects"],
        "planned_capability_count": evidence["planned_capability_count"],
        "mismatches": evidence["mismatches"],
        "work_queue_unchanged": work_before == work_after,
    }
    print(json.dumps(output, sort_keys=True))
    if evidence["status"] != "pass":
        raise RuntimeError("Institutional shadow reconciliation failed closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
