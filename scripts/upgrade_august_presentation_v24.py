from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.identity import DefaultAzureCredential  # noqa: E402
from azure.storage.blob import BlobServiceClient, ContentSettings  # noqa: E402

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


ACCOUNT_ID = "U16088571"
PERIOD_END = "2026-08-31"
LAYOUT_VERSION = "approved-august-2026-20-slide-v24"


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_presentation(path: Path) -> None:
    with zipfile.ZipFile(path) as package:
        names = set(package.namelist())
        slides = sorted(
            name
            for name in names
            if name.startswith("ppt/slides/slide")
            and name.endswith(".xml")
            and "/_rels/" not in name
        )
        if len(slides) != 20:
            raise RuntimeError("Approved presentation must contain exactly 20 slides")
        if any(name.startswith("ppt/comments/") for name in names):
            raise RuntimeError("Approved presentation must not contain PowerPoint comments")
        slide_18 = package.read("ppt/slides/slide18.xml").decode("utf-8")
        if "Aug Results" in slide_18 or not re.search(r">Results\s*</a:t>", slide_18):
            raise RuntimeError("Slide 18 must use the exact navigation label Results")


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("The August presentation upgrade may run only in staging")

    tenant_id = _required("PORTFOLIO_TENANT_ID")
    account_id = os.environ.get("PILOT_ACCOUNT_ID", ACCOUNT_ID).strip()
    period_end = os.environ.get("REPORT_PERIOD_END", PERIOD_END).strip()
    approved_by = _required("REPORT_REPLACEMENT_APPROVED_BY").lower()
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    database_url = _required("DATABASE_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()
    source = Path(
        os.environ.get(
            "APPROVED_PRESENTATION_PATH",
            "/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx",
        )
    )
    expected_sha256 = _required("EXPECTED_PRESENTATION_SHA256").lower()
    template_version = _required("PRESENTATION_TEMPLATE_VERSION")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    if account_id != ACCOUNT_ID or period_end != PERIOD_END:
        raise RuntimeError("The one-time upgrade is restricted to August 2026 U16088571")
    if LAYOUT_VERSION not in template_version:
        raise RuntimeError("The presentation template version is not the approved v24 layout")

    content = source.read_bytes()
    digest = _sha256(content)
    if digest != expected_sha256:
        raise RuntimeError("Embedded approved presentation SHA-256 does not match")
    _validate_presentation(source)

    filename = f"{account_id}_Monthly_Presentation_{period_end}.pptx"
    review_blob = f"review/{account_id}/{period_end}/{filename}"
    released_blob = f"released/{account_id}/{period_end}/{filename}"
    output_reference = f"{container}/{review_blob}"
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    blob_service = BlobServiceClient(account_url=storage_url, credential=credential)
    metadata = {
        "sha256": digest,
        "release_scope": "internal_shareholder",
        "client_distribution": "prohibited",
        "market_data_provider": "yfinance",
        "layout_upgrade": LAYOUT_VERSION,
    }

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        row = connection.execute(
            """
            SELECT r.generation_run_id, r.status, r.output_sha256,
                   a.artifact_release_id
            FROM report_generation_runs r
            JOIN reporting_snapshots s
              ON s.tenant_id = r.tenant_id AND s.snapshot_id = r.snapshot_id
            LEFT JOIN report_artifact_releases a
              ON a.tenant_id = r.tenant_id
             AND a.generation_run_id = r.generation_run_id
             AND a.release_scope = 'internal_shareholder'
            WHERE r.tenant_id = ? AND s.subject_id = ? AND s.period_end = ?
              AND r.report_type = 'presentation'
            ORDER BY CASE WHEN a.artifact_release_id IS NOT NULL THEN 0 ELSE 1 END,
                     r.generation_run_id DESC
            LIMIT 1
            """,
            (tenant_id, account_id, period_end),
        ).fetchone()
        if row is None or str(row["status"]) != "released_internal":
            raise RuntimeError("The approved August presentation run is not released_internal")
        if row["artifact_release_id"] is None:
            raise RuntimeError("The approved August presentation has no internal release record")

        for blob_name in (review_blob, released_blob):
            blob_service.get_blob_client(container, blob_name).upload_blob(
                content,
                overwrite=True,
                metadata=metadata,
                content_settings=ContentSettings(
                    content_type=(
                        "application/vnd.openxmlformats-officedocument."
                        "presentationml.presentation"
                    )
                ),
            )

        now = utc_now_iso()
        connection.execute(
            """
            UPDATE report_generation_runs
            SET template_version = ?, output_reference = ?, output_sha256 = ?,
                completed_at = ?, error_message = NULL
            WHERE tenant_id = ? AND generation_run_id = ?
              AND status = 'released_internal'
            """,
            (
                template_version,
                output_reference,
                digest,
                now,
                tenant_id,
                int(row["generation_run_id"]),
            ),
        )
        connection.execute(
            """
            UPDATE report_artifact_releases
            SET container_name = ?, blob_name = ?, artifact_sha256 = ?, released_at = ?
            WHERE tenant_id = ? AND artifact_release_id = ?
            """,
            (
                container,
                released_blob,
                digest,
                now,
                tenant_id,
                int(row["artifact_release_id"]),
            ),
        )
        details = canonical_json(
            {
                "period_end": period_end,
                "layout_version": LAYOUT_VERSION,
                "previous_sha256": str(row["output_sha256"] or ""),
                "replacement_sha256": digest,
                "factsheet_changed": False,
                "slide": 18,
                "label": "Results",
            }
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'user', ?, 'august_presentation_results_label_applied',
                      ?, 'released_internal_replaced', ?)
            """,
            (tenant_id, now, approved_by, account_id, details),
        )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "account_id": account_id,
                    "period_end": period_end,
                    "layout_version": LAYOUT_VERSION,
                    "sha256": digest,
                    "bytes": len(content),
                    "generation_run_id": int(row["generation_run_id"]),
                    "factsheet_changed": False,
                },
                sort_keys=True,
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
