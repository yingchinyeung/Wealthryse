from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import content_type_for_path  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.internal_release import (  # noqa: E402
    INTERNAL_RELEASE_SCOPE,
    approve_internal_release,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


def _blob_name(reference: str, source_container: str) -> str:
    if reference.startswith("https://"):
        parts = PurePosixPath(unquote(urlparse(reference).path)).parts
        if len(parts) < 3 or parts[1] != source_container:
            raise RuntimeError("Report reference is outside the approved source container")
        return "/".join(parts[2:])
    value = reference.lstrip("/")
    prefix = f"{source_container}/"
    return value[len(prefix) :] if value.startswith(prefix) else value


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "staging":
        raise RuntimeError("The internal-release job may run only in the isolated environment")
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    snapshot_id = os.environ["REPORTING_SNAPSHOT_ID"]
    actor = os.environ["INTERNAL_RELEASE_APPROVED_BY"].strip().lower()
    account_url = os.environ["AZURE_STORAGE_ACCOUNT_URL"].rstrip("/")
    source_container = os.environ.get("REPORT_DRAFT_CONTAINER", "report-drafts")
    destination_container = os.environ.get(
        "INTERNAL_REPORT_CONTAINER", "internal-reports"
    )

    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient, ContentSettings

    credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    service = BlobServiceClient(account_url=account_url, credential=credential)
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        snapshot = connection.execute(
            """
            SELECT snapshot_id, subject_id, period_end, status, quality_status,
                   publication_allowed, publication_blockers_json, data_sha256
            FROM reporting_snapshots
            WHERE tenant_id = ? AND snapshot_id = ?
            """,
            (tenant_id, snapshot_id),
        ).fetchone()
        if snapshot is None:
            raise RuntimeError("The selected frozen snapshot does not exist")
        report_runs = connection.execute(
            """
            SELECT generation_run_id, snapshot_id, report_type, status,
                   output_reference, output_sha256
            FROM report_generation_runs
            WHERE tenant_id = ? AND snapshot_id = ?
              AND generation_run_id IN (39, 40)
            ORDER BY generation_run_id
            """,
            (tenant_id, snapshot_id),
        ).fetchall()
        accepted = connection.execute(
            """
            SELECT 1 FROM audit_events
            WHERE tenant_id = ? AND subject_id = ?
              AND event_type = 'month_end_package_user_accepted'
              AND outcome = 'accepted_internal_only'
            LIMIT 1
            """,
            (tenant_id, snapshot["subject_id"]),
        ).fetchone()
        approval = approve_internal_release(
            snapshot, report_runs, user_acceptance_recorded=accepted is not None
        )

        released_artifacts = []
        for row in report_runs:
            source_name = _blob_name(row["output_reference"], source_container)
            source_client = service.get_blob_client(source_container, source_name)
            content = source_client.download_blob().readall()
            digest = hashlib.sha256(content).hexdigest()
            if digest != row["output_sha256"]:
                raise RuntimeError(
                    f"Artifact digest mismatch for generation run {row['generation_run_id']}"
                )
            destination_name = (
                f"released/{approval.subject_id}/{approval.period_end}/"
                f"{PurePosixPath(source_name).name}"
            )
            destination_client = service.get_blob_client(
                destination_container, destination_name
            )
            metadata = {
                "sha256": digest,
                "release_scope": INTERNAL_RELEASE_SCOPE,
                "client_distribution": "prohibited",
                "market_data_provider": "yfinance",
                "snapshot_id": approval.snapshot_id,
            }
            if destination_client.exists():
                existing = destination_client.download_blob().readall()
                if hashlib.sha256(existing).hexdigest() != digest:
                    raise RuntimeError("Existing internal-release artifact has a different digest")
                destination_client.set_http_headers(
                    content_settings=ContentSettings(
                        content_type=content_type_for_path(Path(destination_name))
                    )
                )
                # Azure's Set Blob Properties operation can clear metadata on an
                # existing blob.  Apply the metadata last so an idempotent rerun
                # preserves both the corrected MIME type and the release guardrails.
                destination_client.set_blob_metadata(metadata)
            else:
                destination_client.upload_blob(
                    content,
                    overwrite=False,
                    metadata=metadata,
                    content_settings=ContentSettings(
                        content_type=content_type_for_path(Path(destination_name))
                    ),
                )
            released_artifacts.append(
                {
                    "generation_run_id": int(row["generation_run_id"]),
                    "report_type": row["report_type"],
                    "container_name": destination_container,
                    "blob_name": destination_name,
                    "sha256": digest,
                }
            )

        now = utc_now_iso()
        conditions = canonical_json(
            {
                "release_scope": INTERNAL_RELEASE_SCOPE,
                "audience": "company shareholders and authorized internal users",
                "market_data_provider": "yfinance",
                "client_distribution": "prohibited",
                "client_release_requires": "licensed provider such as Bloomberg and publication_allowed=true",
                "publication_blockers_retained": list(approval.blockers),
            }
        )
        decision = connection.execute(
            """
            INSERT INTO report_release_decisions(
                tenant_id, snapshot_id, release_scope, status,
                approved_by, approved_at, conditions_json
            ) VALUES (?, ?, 'internal_shareholder', 'approved', ?, ?, ?)
            ON CONFLICT (tenant_id, snapshot_id, release_scope, status)
            DO UPDATE SET approved_by = excluded.approved_by,
                          approved_at = excluded.approved_at,
                          conditions_json = excluded.conditions_json
            RETURNING release_decision_id
            """,
            (tenant_id, snapshot_id, actor, now, conditions),
        ).fetchone()
        release_decision_id = int(decision[0])
        for artifact in released_artifacts:
            connection.execute(
                """
                INSERT INTO report_artifact_releases(
                    tenant_id, release_decision_id, generation_run_id,
                    release_scope, container_name, blob_name,
                    artifact_sha256, released_at
                ) VALUES (?, ?, ?, 'internal_shareholder', ?, ?, ?, ?)
                ON CONFLICT (tenant_id, generation_run_id, release_scope)
                DO UPDATE SET release_decision_id = excluded.release_decision_id,
                              container_name = excluded.container_name,
                              blob_name = excluded.blob_name,
                              artifact_sha256 = excluded.artifact_sha256,
                              released_at = excluded.released_at
                """,
                (
                    tenant_id,
                    release_decision_id,
                    artifact["generation_run_id"],
                    artifact["container_name"],
                    artifact["blob_name"],
                    artifact["sha256"],
                    now,
                ),
            )
        connection.execute(
            """
            UPDATE report_generation_runs
            SET status = 'released_internal'
            WHERE tenant_id = ? AND snapshot_id = ?
              AND generation_run_id IN (39, 40)
              AND status IN ('awaiting_approval', 'released_internal')
            """,
            (tenant_id, snapshot_id),
        )
        event_details = canonical_json(
            {
                "release_decision_id": release_decision_id,
                "snapshot_id": snapshot_id,
                "release_scope": INTERNAL_RELEASE_SCOPE,
                "client_publication_allowed": False,
                "artifacts": released_artifacts,
            }
        )
        existing_event = connection.execute(
            """
            SELECT 1 FROM audit_events
            WHERE tenant_id = ? AND event_type = 'month_end_package_released_internal'
              AND subject_id = ? AND details_json = ?
            LIMIT 1
            """,
            (tenant_id, approval.subject_id, event_details),
        ).fetchone()
        if existing_event is None:
            connection.execute(
                """
                INSERT INTO audit_events(
                    tenant_id, occurred_at, actor_type, actor_id, event_type,
                    subject_id, outcome, details_json
                ) VALUES (?, ?, 'user', ?, 'month_end_package_released_internal',
                          ?, 'released_internal_client_blocked', ?)
                """,
                (tenant_id, now, actor, approval.subject_id, event_details),
            )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "release_decision_id": release_decision_id,
                    "release_scope": INTERNAL_RELEASE_SCOPE,
                    "snapshot_id": snapshot_id,
                    "artifacts": released_artifacts,
                    "client_publication_allowed": False,
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
