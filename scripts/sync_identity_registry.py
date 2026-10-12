from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

from ibkr_etl.config import load_settings
from ibkr_etl.db import connect, transaction
from ibkr_etl.identity_registry import apply_identity_manifest, validate_identity_manifest


def main() -> int:
    settings = load_settings()
    encoded = os.environ.get("IDENTITY_REGISTRY_B64", "").strip()
    raw = os.environ.get("IDENTITY_REGISTRY_JSON", "").strip()
    if encoded:
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("IDENTITY_REGISTRY_B64 is not valid UTF-8 Base64") from error
    expected_sha = os.environ.get("IDENTITY_REGISTRY_SHA256", "").strip().lower()
    apply = os.environ.get("IDENTITY_REGISTRY_APPLY", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    actor = os.environ.get("IDENTITY_REGISTRY_ACTOR", "").strip().lower()
    if not raw:
        raise RuntimeError("IDENTITY_REGISTRY_B64 or IDENTITY_REGISTRY_JSON is required")
    manifest = validate_identity_manifest(json.loads(raw))
    if expected_sha and expected_sha != manifest.sha256:
        raise RuntimeError("Identity registry digest does not match the approved manifest")
    print(
        json.dumps(
            {
                "status": "validated",
                "manifest_sha256": manifest.sha256,
                "mode": manifest.mode,
                "users": len(manifest.users),
                "roles": manifest.role_count,
                "entitlements": manifest.entitlement_count,
                "apply_requested": apply,
            },
            sort_keys=True,
        )
    )
    if not apply:
        return 0
    if not actor:
        raise RuntimeError("IDENTITY_REGISTRY_ACTOR is required when applying a manifest")
    actor_digest = hashlib.sha256(actor.encode("utf-8")).hexdigest()
    connection = connect(Path(settings.database_path), settings.database_url, settings.tenant_id)
    try:
        with transaction(connection):
            revision_id = apply_identity_manifest(
                connection,
                tenant_id=settings.tenant_id,
                manifest=manifest,
                applied_by_identity_sha256=actor_digest,
            )
    finally:
        connection.close()
    print(json.dumps({"status": "applied", "revision_id": revision_id, "manifest_sha256": manifest.sha256}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
