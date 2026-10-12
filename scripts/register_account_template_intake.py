from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import (  # noqa: E402
    download_blob_bytes,
    upload_bytes,
    upload_file,
)
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.template_intake import (  # noqa: E402
    parse_template_manifest,
    register_template_assets,
    verify_template_assets,
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() not in {
        "staging",
        "internal-production",
    }:
        raise RuntimeError("Template intake may run only in controlled environments")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    subject_id = _required("TEMPLATE_SUBJECT_ID")
    account_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()
    manifest_blob = _required("TEMPLATE_MANIFEST_BLOB")
    expected_manifest_sha256 = _required("EXPECTED_TEMPLATE_MANIFEST_SHA256").lower()

    bundle_dir_value = os.environ.get("TEMPLATE_BUNDLE_DIR", "").strip()
    if bundle_dir_value:
        bundle_dir = Path(bundle_dir_value).resolve()
        manifest_path = bundle_dir / "template-intake-manifest.json"
        if not manifest_path.is_file():
            raise RuntimeError("Bundled template intake manifest is missing")
        manifest_content = manifest_path.read_bytes()
    else:
        bundle_dir = None
        manifest_content = download_blob_bytes(
            account_url, container, manifest_blob, maximum_bytes=256 * 1024
        )
    if hashlib.sha256(manifest_content).hexdigest() != expected_manifest_sha256:
        raise RuntimeError("Template intake manifest SHA-256 does not match")
    assets = parse_template_manifest(
        manifest_content, subject_id=subject_id, container_name=container
    )
    if bundle_dir is not None:
        for asset in assets:
            bundle_file = str(asset.metadata.get("bundle_file") or "").strip()
            if not bundle_file or Path(bundle_file).name != bundle_file:
                raise RuntimeError("Bundled template asset filename is invalid")
            asset_path = bundle_dir / bundle_file
            if not asset_path.is_file():
                raise RuntimeError(f"Bundled template asset is missing: {bundle_file}")
            upload_file(
                account_url,
                asset.container_name,
                asset.blob_name,
                asset_path,
                overwrite=True,
                content_type=asset.content_type,
            )
        upload_bytes(
            account_url,
            container,
            manifest_blob,
            manifest_content,
            overwrite=True,
            content_type="application/json; charset=utf-8",
        )
    verify_template_assets(
        assets,
        download=lambda asset_container, blob_name: download_blob_bytes(
            account_url,
            asset_container,
            blob_name,
            maximum_bytes=50 * 1024 * 1024,
        ),
    )

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        evidence = register_template_assets(
            connection,
            tenant_id=tenant_id,
            subject_id=subject_id,
            assets=assets,
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(
        f"Registered {evidence['asset_count']} verified template assets "
        f"for {subject_id}; onboarding advanced to reconciliation."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
