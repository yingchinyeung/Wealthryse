from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ibkr_etl.config import load_settings
from ibkr_etl.db import POSTGRES_MIGRATIONS_DIR, connect
from ibkr_etl.operating_registry import control_plane_summary


IDENTITY_MIGRATION = "044_identity_registry_and_worker_controls.sql"
SHADOW_MIGRATION = "045_institutional_shadow_reconciliation.sql"


def _expected_int(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if value < 0:
        raise RuntimeError(f"{name} cannot be negative")
    return value


def main() -> int:
    settings = load_settings()
    expected = {
        "users": _expected_int("EXPECTED_ACTIVE_USERS", 5),
        "owners": _expected_int("EXPECTED_ACTIVE_OWNERS", 1),
        "roles": _expected_int("EXPECTED_ACTIVE_ROLES", 5),
        "entitlements": _expected_int("EXPECTED_SUBJECT_ENTITLEMENTS", 6),
        "active_registrations": _expected_int("EXPECTED_ACTIVE_REGISTRATIONS", 6),
        "preview_registrations": _expected_int("EXPECTED_PREVIEW_REGISTRATIONS", 3),
    }
    expected_digests = {
        version: hashlib.sha256(
            (POSTGRES_MIGRATIONS_DIR / version).read_bytes()
        ).hexdigest()
        for version in (IDENTITY_MIGRATION, SHADOW_MIGRATION)
    }
    connection = connect(
        Path(settings.database_path), settings.database_url, settings.tenant_id
    )
    try:
        migrations = {}
        for version, expected_digest in expected_digests.items():
            migration = connection.execute(
                "SELECT sha256, applied_at FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if migration is None or str(migration["sha256"]) != expected_digest:
                raise RuntimeError(
                    f"Migration {version} is missing or its checksum does not match"
                )
            migrations[version] = migration
        summary = control_plane_summary(connection, tenant_id=settings.tenant_id)
    finally:
        connection.close()
    identity = summary["identity_registry"]
    actual = {
        "users": identity["users"],
        "owners": identity["owners"],
        "roles": identity["roles"],
        "entitlements": identity["entitlements"],
        "active_registrations": summary["registrations"].get("active", 0),
        "preview_registrations": summary["registrations"].get("preview", 0),
    }
    mismatches = {
        key: {"expected": expected[key], "actual": actual[key]}
        for key in expected
        if actual[key] != expected[key]
    }
    if summary["dead_letters"]:
        mismatches["dead_letters"] = {
            "expected": 0,
            "actual": summary["dead_letters"],
        }
    if not identity["database_authorization_ready"]:
        mismatches["database_authorization_ready"] = {
            "expected": True,
            "actual": False,
        }
    shadow = summary.get("shadow_reconciliation")
    if not shadow or shadow.get("status") != "pass":
        mismatches["shadow_reconciliation"] = {
            "expected": "pass",
            "actual": shadow.get("status") if shadow else "not_run",
        }
    result = {
        "status": "pass" if not mismatches else "fail",
        "migration": IDENTITY_MIGRATION,
        "migration_sha256": expected_digests[IDENTITY_MIGRATION],
        "migration_applied_at": migrations[IDENTITY_MIGRATION]["applied_at"],
        "shadow_migration": SHADOW_MIGRATION,
        "shadow_migration_sha256": expected_digests[SHADOW_MIGRATION],
        "shadow_migration_applied_at": migrations[SHADOW_MIGRATION]["applied_at"],
        "actual": actual,
        "work_items": summary["work_items"],
        "dead_letters": summary["dead_letters"],
        "shadow_reconciliation": shadow,
        "mismatches": mismatches,
    }
    print(json.dumps(result, sort_keys=True, default=str))
    if mismatches:
        raise RuntimeError("Institutional control-plane acceptance failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
