from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.identity import DefaultAzureCredential  # noqa: E402
from azure.storage.blob import BlobServiceClient, ContentSettings  # noqa: E402

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.monthly_reporting_analytics import (  # noqa: E402
    build_monthly_reporting_analytics,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402
from validate_monthly_presentation import validate  # noqa: E402


ACCOUNT_ID = "U16088571"
LAYOUT_VERSION = "approved-august-2026-20-slide-v24"
TEMPLATE_SHA256 = "4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191"
EXPECTED_PERIODS = tuple(
    [
        f"2025-{month:02d}-{day:02d}"
        for month, day in (
            (1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30),
            (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31),
        )
    ]
    + [
        f"2026-{month:02d}-{day:02d}"
        for month, day in (
            (1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30),
            (7, 31), (8, 31),
        )
    ]
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _blob_service(storage_url: str) -> BlobServiceClient:
    return BlobServiceClient(
        account_url=storage_url,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )


def _active_snapshots(connection) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT snapshot_id, period_end, snapshot_payload_json, data_sha256
        FROM reporting_snapshots
        WHERE subject_id = ? AND period_end >= ? AND period_end <= ?
          AND status IN ('frozen', 'approved') AND quality_status <> 'fail'
        ORDER BY period_end, snapshot_id
        """,
        (ACCOUNT_ID, EXPECTED_PERIODS[0], EXPECTED_PERIODS[-1]),
    ).fetchall()
    by_period: dict[str, object] = {}
    for row in rows:
        period = str(row["period_end"])
        if period in by_period:
            raise RuntimeError(f"Multiple active reporting snapshots exist for {period}")
        by_period[period] = row
    if tuple(by_period) != EXPECTED_PERIODS:
        raise RuntimeError(
            f"Expected all {len(EXPECTED_PERIODS)} historical periods; found {tuple(by_period)}"
        )
    return by_period


def _augment_snapshot(connection, row) -> tuple[dict[str, object], str]:
    payload_text = str(row["snapshot_payload_json"])
    if _sha256(payload_text.encode("utf-8")) != str(row["data_sha256"]):
        raise RuntimeError(f"Frozen snapshot integrity failed for {row['period_end']}")
    payload = json.loads(payload_text)
    analytics = build_monthly_reporting_analytics(
        connection,
        subject_id=ACCOUNT_ID,
        period_end=str(row["period_end"]),
        reporting_currency="USD",
    )
    if analytics.get("quality_status") == "fail" or not analytics.get("data", {}).get("available"):
        raise RuntimeError(
            f"Monthly reporting analytics are unavailable for {row['period_end']}: "
            f"{analytics.get('message', 'unknown error')}"
        )
    payload["monthly_reporting"] = analytics["data"]
    augmentation = canonical_json(
        {
            "snapshot_id": str(row["snapshot_id"]),
            "snapshot_sha256": str(row["data_sha256"]),
            "monthly_reporting": analytics["data"],
        }
    ).encode("utf-8")
    return payload, _sha256(augmentation)


def _render(template_path: Path, snapshot_path: Path, output_path: Path) -> None:
    command = [
        os.environ.get("NODE_BINARY", "node"),
        str(PROJECT_ROOT / "scripts" / "render_monthly_presentation.mjs"),
        str(template_path),
        str(snapshot_path),
        str(output_path),
    ]
    result = subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=420,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "PowerPoint rendering failed: "
            + (result.stderr or result.stdout or "unknown renderer error")[-4000:]
        )


def _run_qa(connection, blob_service: BlobServiceClient, *, container: str) -> dict[str, object]:
    snapshots = _active_snapshots(connection)
    template_path = Path(
        os.environ.get(
            "PRESENTATION_TEMPLATE_PATH",
            "/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx",
        )
    )
    template_content = template_path.read_bytes()
    template_sha = _sha256(template_content)
    expected_sha = _required("PRESENTATION_TEMPLATE_SHA256").lower()
    if template_sha != expected_sha or template_sha != TEMPLATE_SHA256:
        raise RuntimeError("Embedded v24 template SHA-256 does not match the approved file")

    artifacts: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="historical-v24-") as directory:
        root = Path(directory)
        for period_end in EXPECTED_PERIODS:
            row = snapshots[period_end]
            payload, augmentation_sha = _augment_snapshot(connection, row)
            snapshot_path = root / f"{period_end}.json"
            snapshot_path.write_text(canonical_json(payload), encoding="utf-8")
            output = root / f"{ACCOUNT_ID}_Monthly_Presentation_{period_end}.pptx"
            _render(template_path, snapshot_path, output)
            validation = validate(output, expected_period=period_end)
            content = output.read_bytes()
            digest = _sha256(content)
            blob_name = f"qa-v24/{ACCOUNT_ID}/{period_end}/{output.name}"
            blob_service.get_blob_client(container, blob_name).upload_blob(
                content,
                overwrite=True,
                metadata={
                    "sha256": digest,
                    "snapshot_id": str(row["snapshot_id"]),
                    "layout_version": LAYOUT_VERSION,
                    "augmentation_sha256": augmentation_sha,
                    "qa_only": "true",
                },
                content_settings=ContentSettings(
                    content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
                ),
            )
            artifacts.append(
                {
                    "account": ACCOUNT_ID,
                    "period_end": period_end,
                    "snapshot_id": str(row["snapshot_id"]),
                    "blob_name": blob_name,
                    "sha256": digest,
                    "bytes": len(content),
                    "slides": int(validation["slides"]),
                    "augmentation_sha256": augmentation_sha,
                }
            )

    manifest = {
        "schema_version": 1,
        "account": ACCOUNT_ID,
        "period_count": len(artifacts),
        "start": EXPECTED_PERIODS[0],
        "end": EXPECTED_PERIODS[-1],
        "layout_version": LAYOUT_VERSION,
        "template_sha256": template_sha,
        "factsheet_changed": False,
        "artifacts": artifacts,
    }
    content = canonical_json(manifest).encode("utf-8")
    manifest_blob = _required("QA_MANIFEST_BLOB")
    blob_service.get_blob_client(container, manifest_blob).upload_blob(
        content,
        overwrite=True,
        metadata={"sha256": _sha256(content), "qa_only": "true"},
        content_settings=ContentSettings(content_type="application/json"),
    )
    return {
        "status": "QA_READY",
        "period_count": len(artifacts),
        "manifest_blob": manifest_blob,
        "manifest_sha256": _sha256(content),
    }


def _run_publish(
    connection,
    blob_service: BlobServiceClient,
    *,
    container: str,
    tenant_id: str,
) -> dict[str, object]:
    manifest_blob = _required("QA_MANIFEST_BLOB")
    manifest_content = blob_service.get_blob_client(container, manifest_blob).download_blob().readall()
    manifest = json.loads(manifest_content)
    if (
        manifest.get("account") != ACCOUNT_ID
        or manifest.get("layout_version") != LAYOUT_VERSION
        or manifest.get("template_sha256") != TEMPLATE_SHA256
        or int(manifest.get("period_count", 0)) != len(EXPECTED_PERIODS)
        or tuple(item.get("period_end") for item in manifest.get("artifacts", []))
        != EXPECTED_PERIODS
        or manifest.get("factsheet_changed") is not False
    ):
        raise RuntimeError("QA manifest is incomplete or outside the approved scope")

    template_version = _required("PRESENTATION_TEMPLATE_VERSION")
    approved_by = _required("REPORT_REPLACEMENT_APPROVED_BY").lower()
    published: list[dict[str, object]] = []
    for item in manifest["artifacts"]:
        period_end = str(item["period_end"])
        source_blob = str(item["blob_name"])
        if PurePosixPath(source_blob).parts[:2] != ("qa-v24", ACCOUNT_ID):
            raise RuntimeError(f"QA artifact is outside the approved prefix: {source_blob}")
        content = blob_service.get_blob_client(container, source_blob).download_blob().readall()
        digest = _sha256(content)
        if digest != str(item["sha256"]) or len(content) != int(item["bytes"]):
            raise RuntimeError(f"QA artifact integrity failed for {period_end}")
        with TemporaryDirectory(prefix="validate-v24-") as directory:
            path = Path(directory) / "presentation.pptx"
            path.write_bytes(content)
            validate(path, expected_period=period_end)

        run = connection.execute(
            """
            SELECT r.generation_run_id, r.status, a.artifact_release_id
            FROM report_generation_runs r
            LEFT JOIN report_artifact_releases a
              ON a.tenant_id = r.tenant_id
             AND a.generation_run_id = r.generation_run_id
             AND a.release_scope = 'internal_shareholder'
            WHERE r.snapshot_id = ? AND r.report_type = 'presentation'
            ORDER BY CASE WHEN a.artifact_release_id IS NOT NULL THEN 0 ELSE 1 END,
                     r.generation_run_id DESC
            LIMIT 1
            """,
            (str(item["snapshot_id"]),),
        ).fetchone()
        if run is None:
            raise RuntimeError(f"Presentation generation run is missing for {period_end}")
        status = str(run["status"])
        if status == "published":
            raise RuntimeError(f"Client-published presentation cannot be replaced: {period_end}")

        filename = f"{ACCOUNT_ID}_Monthly_Presentation_{period_end}.pptx"
        review_blob = f"review/{ACCOUNT_ID}/{period_end}/{filename}"
        review_reference = f"{container}/{review_blob}"
        metadata = {
            "sha256": digest,
            "snapshot_id": str(item["snapshot_id"]),
            "layout_upgrade": LAYOUT_VERSION,
            "augmentation_sha256": str(item["augmentation_sha256"]),
            "factsheet_changed": "false",
        }
        content_settings = ContentSettings(
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        blob_service.get_blob_client(container, review_blob).upload_blob(
            content, overwrite=True, metadata=metadata, content_settings=content_settings
        )
        now = utc_now_iso()
        if status == "released_internal":
            if run["artifact_release_id"] is None:
                raise RuntimeError(f"Released presentation lacks release record: {period_end}")
            released_blob = f"released/{ACCOUNT_ID}/{period_end}/{filename}"
            blob_service.get_blob_client(container, released_blob).upload_blob(
                content,
                overwrite=True,
                metadata={
                    **metadata,
                    "release_scope": "internal_shareholder",
                    "client_distribution": "prohibited",
                    "market_data_provider": "yfinance",
                },
                content_settings=content_settings,
            )
            connection.execute(
                """
                UPDATE report_artifact_releases
                SET container_name = ?, blob_name = ?, artifact_sha256 = ?, released_at = ?
                WHERE artifact_release_id = ?
                """,
                (container, released_blob, digest, now, int(run["artifact_release_id"])),
            )
        connection.execute(
            """
            UPDATE report_generation_runs
            SET status = ?, template_version = ?, output_reference = ?,
                output_sha256 = ?, completed_at = ?, error_message = NULL
            WHERE generation_run_id = ? AND report_type = 'presentation'
              AND status <> 'published'
            """,
            (
                "released_internal" if status == "released_internal" else "awaiting_approval",
                template_version,
                review_reference,
                digest,
                now,
                int(run["generation_run_id"]),
            ),
        )
        published.append(
            {
                "period_end": period_end,
                "generation_run_id": int(run["generation_run_id"]),
                "status": status,
                "sha256": digest,
                "bytes": len(content),
            }
        )

    details = canonical_json(
        {
            "manifest_blob": manifest_blob,
            "manifest_sha256": _sha256(manifest_content),
            "period_count": len(published),
            "layout_version": LAYOUT_VERSION,
            "template_version": template_version,
            "factsheet_changed": False,
        }
    )
    connection.execute(
        """
        INSERT INTO audit_events(
            tenant_id, occurred_at, actor_type, actor_id, event_type,
            subject_id, outcome, details_json
        ) VALUES (?, ?, 'user', ?, 'historical_presentations_v24_applied',
                  ?, 'historical_and_future_layout_updated', ?)
        """,
        (tenant_id, utc_now_iso(), approved_by, ACCOUNT_ID, details),
    )
    connection.commit()
    return {
        "status": "PASS",
        "period_count": len(published),
        "layout_version": LAYOUT_VERSION,
        "artifacts": published,
        "factsheet_changed": False,
    }


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("Historical v24 upgrade may run only in staging")
    mode = _required("UPGRADE_MODE").lower()
    if mode not in {"qa", "publish"}:
        raise RuntimeError("UPGRADE_MODE must be qa or publish")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        service = _blob_service(storage_url)
        result = (
            _run_qa(connection, service, container=container)
            if mode == "qa"
            else _run_publish(
                connection,
                service,
                container=container,
                tenant_id=tenant_id,
            )
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
