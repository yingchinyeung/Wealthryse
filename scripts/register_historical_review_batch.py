from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import content_type_for_path, download_blob_bytes  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.monthly_reports import (  # noqa: E402
    REPORT_TYPES,
    REQUIRED_REPORT_TYPES,
    validate_period_end,
    validate_review_artifact,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("Historical review registration may run only in staging")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    storage_url = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()
    manifest_blob = os.environ.get("REVIEW_MANIFEST_BLOB", "").strip()
    if not storage_url or not manifest_blob:
        raise RuntimeError("Review storage configuration is incomplete")
    if PurePosixPath(manifest_blob).parts[:2] != ("review", "U16088571"):
        raise RuntimeError("Review manifest is outside the approved account prefix")

    manifest_bytes = download_blob_bytes(storage_url, container, manifest_blob)
    manifest = json.loads(manifest_bytes)
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    if manifest.get("account") != account_id:
        raise RuntimeError("Review manifest account does not match the configured pilot")
    if int(manifest.get("period_count", 0)) != len(manifest.get("artifacts", [])):
        raise RuntimeError("Review manifest period count is inconsistent")
    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    allow_released_replacement = os.environ.get(
        "ALLOW_RELEASED_INTERNAL_REPLACEMENT", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    approved_by = os.environ.get("REPORT_REPLACEMENT_APPROVED_BY", "").strip().lower()
    upgrade_tag = os.environ.get("LAYOUT_UPGRADE_TAG", "review-layout-upgrade").strip()
    event_type = os.environ.get(
        "LAYOUT_UPGRADE_EVENT_TYPE", "historical_reports_layout_upgraded"
    ).strip()
    event_outcome = os.environ.get(
        "LAYOUT_UPGRADE_OUTCOME", "historical_and_future_layout_updated"
    ).strip()
    if not upgrade_tag or not event_type or not event_outcome:
        raise RuntimeError("Layout-upgrade audit configuration is incomplete")
    template_versions = {
        "factsheet": os.environ.get("FACTSHEET_TEMPLATE_VERSION", "").strip(),
        "presentation": os.environ.get("PRESENTATION_TEMPLATE_VERSION", "").strip(),
    }
    if any(not value for value in template_versions.values()):
        raise RuntimeError("Replacement template versions are required")
    if allow_released_replacement and not approved_by:
        raise RuntimeError("Released internal replacement requires an approving user")

    blob_service = None
    if allow_released_replacement:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient, ContentSettings

        blob_service = BlobServiceClient(
            account_url=storage_url,
            credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
        )
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        registered: list[dict[str, object]] = []
        for item in manifest["artifacts"]:
            period_end = validate_period_end(str(item["period_end"]))
            snapshot_id = str(item["snapshot_id"])
            snapshot = connection.execute(
                """
                SELECT snapshot_id FROM reporting_snapshots
                WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ?
                  AND period_end = ? AND status IN ('frozen', 'approved')
                  AND quality_status <> 'fail'
                """,
                (tenant_id, snapshot_id, account_id, period_end),
            ).fetchone()
            if snapshot is None:
                raise RuntimeError(f"Frozen snapshot is unavailable for {period_end}")
            files = item.get("files", [])
            kinds = [str(file["report_type"]) for file in files]
            if len(kinds) != len(set(kinds)) or not REQUIRED_REPORT_TYPES <= set(kinds) <= set(REPORT_TYPES):
                raise RuntimeError(f"Review package is incomplete for {period_end}")
            for file in files:
                report_type = str(file["report_type"])
                candidate = {
                    "generation_run_id": 1,
                    "report_type": report_type,
                    "output_reference": str(file["output_reference"]),
                    "output_sha256": str(file["sha256"]),
                }
                validated = validate_review_artifact(
                    candidate,
                    account_id=account_id,
                    period_end=period_end,
                    container=container,
                )
                content = download_blob_bytes(
                    storage_url,
                    validated["container_name"],
                    validated["blob_name"],
                )
                digest = hashlib.sha256(content).hexdigest()
                if digest != validated["sha256"] or len(content) != int(file["bytes"]):
                    raise RuntimeError(f"Uploaded artifact integrity failed for {period_end}")
                run = connection.execute(
                    """
                    SELECT r.generation_run_id, r.status,
                           a.artifact_release_id, a.release_decision_id
                    FROM report_generation_runs r
                    LEFT JOIN report_artifact_releases a
                      ON a.tenant_id = r.tenant_id
                     AND a.generation_run_id = r.generation_run_id
                     AND a.release_scope = 'internal_shareholder'
                    WHERE r.tenant_id = ? AND r.snapshot_id = ? AND r.report_type = ?
                    ORDER BY CASE WHEN a.artifact_release_id IS NOT NULL THEN 0 ELSE 1 END,
                             r.generation_run_id DESC
                    LIMIT 1
                    """,
                    (tenant_id, snapshot_id, report_type),
                ).fetchone()
                if run is None:
                    raise RuntimeError(f"Generation run is missing for {period_end} {report_type}")
                now = utc_now_iso()
                run_status = str(run["status"])
                if run_status == "published":
                    raise RuntimeError(f"Client-published run cannot be replaced: {period_end}")
                if run_status == "released_internal":
                    if not allow_released_replacement or blob_service is None:
                        raise RuntimeError(
                            f"Released generation run cannot be replaced without approval: {period_end}"
                        )
                    if run["artifact_release_id"] is None:
                        raise RuntimeError(
                            f"Released generation run lacks its release record: {period_end}"
                        )
                    filename = PurePosixPath(validated["blob_name"]).name
                    released_blob = f"released/{account_id}/{period_end}/{filename}"
                    released_client = blob_service.get_blob_client(container, released_blob)
                    released_client.upload_blob(
                        content,
                        overwrite=True,
                        metadata={
                            "sha256": digest,
                            "release_scope": "internal_shareholder",
                            "client_distribution": "prohibited",
                            "market_data_provider": "yfinance",
                            "snapshot_id": snapshot_id,
                            "layout_upgrade": upgrade_tag,
                        },
                        content_settings=ContentSettings(
                            content_type=content_type_for_path(Path(filename))
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE report_artifact_releases
                        SET container_name = ?, blob_name = ?, artifact_sha256 = ?,
                            released_at = ?
                        WHERE tenant_id = ? AND artifact_release_id = ?
                        """,
                        (
                            container,
                            released_blob,
                            digest,
                            now,
                            tenant_id,
                            int(run["artifact_release_id"]),
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE report_generation_runs
                        SET template_version = ?, output_reference = ?,
                            output_sha256 = ?, completed_at = ?, error_message = NULL
                        WHERE tenant_id = ? AND generation_run_id = ?
                          AND status = 'released_internal'
                        """,
                        (
                            template_versions[report_type],
                            str(file["output_reference"]),
                            digest,
                            now,
                            tenant_id,
                            int(run["generation_run_id"]),
                        ),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE report_generation_runs
                        SET status = 'awaiting_approval', template_version = ?,
                            output_reference = ?, output_sha256 = ?, completed_at = ?,
                            error_message = NULL
                        WHERE tenant_id = ? AND generation_run_id = ?
                          AND status NOT IN ('released_internal', 'published')
                        """,
                        (
                            template_versions[report_type],
                            str(file["output_reference"]),
                            digest,
                            now,
                            tenant_id,
                            int(run["generation_run_id"]),
                        ),
                    )
                registered.append(
                    {
                        "period_end": period_end,
                        "report_type": report_type,
                        "generation_run_id": int(run["generation_run_id"]),
                        "sha256": digest,
                        "status": run_status,
                    }
                )

        details = canonical_json(
            {
                "manifest_blob": manifest_blob,
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "period_count": len(manifest["artifacts"]),
                "artifact_count": len(registered),
                "released_internal_replacement_approved_by": (
                    approved_by if allow_released_replacement else None
                ),
                "template_versions": template_versions,
                "layout_upgrade_tag": upgrade_tag,
            }
        )
        exists = connection.execute(
            """
            SELECT 1 FROM audit_events
            WHERE tenant_id = ? AND event_type = ?
              AND subject_id = ? AND details_json = ?
            LIMIT 1
            """,
            (tenant_id, event_type, account_id, details),
        ).fetchone()
        if exists is None:
            connection.execute(
                """
                INSERT INTO audit_events(
                    tenant_id, occurred_at, actor_type, actor_id, event_type,
                    subject_id, outcome, details_json
                ) VALUES (?, ?, 'user', ?, ?, ?, ?, ?)
                """,
                (
                    tenant_id,
                    utc_now_iso(),
                    approved_by or "system",
                    event_type,
                    account_id,
                    event_outcome,
                    details,
                ),
            )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "period_count": len(manifest["artifacts"]),
                    "artifact_count": len(registered),
                    "artifacts": registered,
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
