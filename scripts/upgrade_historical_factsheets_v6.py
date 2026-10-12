from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.identity import DefaultAzureCredential  # noqa: E402
from azure.storage.blob import BlobServiceClient, ContentSettings  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.monthly_artifacts import (  # noqa: E402
    FACTSHEET_LAYOUT_VERSION,
    factsheet_layout_version,
    render_factsheet,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


EXPECTED_PERIODS = tuple(
    [f"2025-{month:02d}-{day:02d}" for month, day in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31), (9, 30), (10, 31), (11, 30), (12, 31))]
    + [f"2026-{month:02d}-{day:02d}" for month, day in ((1, 31), (2, 28), (3, 31), (4, 30), (5, 31), (6, 30), (7, 31), (8, 31))]
)

SUPPORTED_SUBJECTS = {
    "U16088571": {
        "template_path": "/app/templates/U16088571/approved-august-2026-factsheet-template.pdf",
        "template_sha256": "460064f7b7cd0650189e71949f31859e07f1fa7256f266c9138b9b9214ba705d",
    },
    "U16088606": {
        "template_path": "/app/templates/U16088606/intake/monthly factsheet template.pdf",
        "template_sha256": "7bd282ee4d1e40b3540008614e8bd997829022745e3ea036d6e019f09035d965",
    },
}


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _validate_pdf(content: bytes, *, period_end: str) -> None:
    with TemporaryDirectory(prefix="validate-v6-") as directory:
        path = Path(directory) / "factsheet.pdf"
        path.write_bytes(content)
        document = PdfReader(path)
        if len(document.pages) != 2:
            raise RuntimeError(f"Expected two factsheet pages for {period_end}")
        text = "\n".join(page.extract_text() or "" for page in document.pages)
    display_date = date.fromisoformat(period_end).strftime("%d %b %Y").lstrip("0")
    normalized_text = text.casefold()
    for required in (
        "Top 5 Products",
        "REGION",
        "SECTOR",
        "SUB-SECTOR",
        "Historical Performance",
        "Normalized NAV",
        display_date,
    ):
        if required.casefold() not in normalized_text:
            raise RuntimeError(f"{period_end} factsheet is missing {required!r}")


def _classification_index(connection) -> dict[str, dict[str, object]]:
    rows = connection.execute(
        """
        SELECT conid, symbol, sector, industry, status, quality_status
        FROM instrument_classifications
        WHERE provider = 'Yahoo Finance search profile'
          AND status = 'classified' AND quality_status <> 'fail'
        """
    ).fetchall()
    return {
        str(row["conid"]): {
            "conid": str(row["conid"]),
            "symbol": str(row["symbol"] or ""),
            "region": "United States",
            "sector": str(row["sector"] or ""),
            "sub_sector": str(row["industry"] or ""),
        }
        for row in rows
    }


def _enrich_snapshot(
    payload: dict[str, object], classifications: dict[str, dict[str, object]]
) -> dict[str, object]:
    holdings_block = payload.get("holdings")
    if not isinstance(holdings_block, dict):
        raise RuntimeError("Snapshot holdings block is unavailable")
    holdings = holdings_block.get("holdings")
    if not isinstance(holdings, list) or not holdings:
        raise RuntimeError("Snapshot holdings are unavailable")
    required_conids = {str(row.get("conid") or "") for row in holdings}
    missing = sorted(
        str(row.get("symbol") or row.get("conid") or "unknown")
        for row in holdings
        if str(row.get("conid") or "") not in classifications
    )
    if missing:
        raise RuntimeError(
            "Historical classifications are incomplete: " + ", ".join(missing)
        )
    sector_block = payload.get("sector_allocation")
    if not isinstance(sector_block, dict):
        sector_block = {}
        payload["sector_allocation"] = sector_block
    sector_block["classifications"] = [
        classifications[conid] for conid in sorted(required_conids)
    ]
    if not sector_block.get("gross_market_value"):
        sector_block["gross_market_value"] = sum(
            abs(float(row["market_value_text"])) for row in holdings
        )
    return payload


def _blob_service(storage_url: str) -> BlobServiceClient:
    return BlobServiceClient(
        account_url=storage_url,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )


def _run_qa(
    connection,
    blob_service: BlobServiceClient,
    *,
    container: str,
    account_id: str,
) -> dict[str, object]:
    layout_version = factsheet_layout_version(account_id)
    rows = connection.execute(
        """
        SELECT snapshot_id, period_end, snapshot_payload_json, data_sha256
        FROM reporting_snapshots
        WHERE subject_id = ? AND period_end >= ? AND period_end <= ?
          AND status IN ('frozen', 'approved') AND quality_status <> 'fail'
        ORDER BY period_end
        """,
        (account_id, EXPECTED_PERIODS[0], EXPECTED_PERIODS[-1]),
    ).fetchall()
    by_period = {str(row["period_end"]): row for row in rows}
    if tuple(by_period) != EXPECTED_PERIODS:
        raise RuntimeError(
            f"Expected the 20 complete historical periods; found {tuple(by_period)}"
        )
    classifications = _classification_index(connection)
    subject_config = SUPPORTED_SUBJECTS[account_id]
    template_path = Path(str(subject_config["template_path"]))
    template_sha = _sha256(template_path.read_bytes())
    expected_template_sha = str(subject_config["template_sha256"])
    if template_sha != expected_template_sha:
        raise RuntimeError("Embedded base factsheet template SHA-256 does not match")

    artifacts: list[dict[str, object]] = []
    with TemporaryDirectory(prefix="historical-v6-") as directory:
        root = Path(directory)
        for period_end in EXPECTED_PERIODS:
            row = by_period[period_end]
            payload_text = str(row["snapshot_payload_json"])
            if _sha256(payload_text.encode("utf-8")) != str(row["data_sha256"]):
                raise RuntimeError(f"Frozen snapshot integrity failed for {period_end}")
            payload = _enrich_snapshot(json.loads(payload_text), classifications)
            output = root / f"{account_id}_Factsheet_{period_end}.pdf"
            result = render_factsheet(payload, template_path=template_path, output_path=output)
            content = output.read_bytes()
            digest = _sha256(content)
            _validate_pdf(content, period_end=period_end)
            blob_name = f"qa-return-panel-v7/{account_id}/{period_end}/{output.name}"
            blob_service.get_blob_client(container, blob_name).upload_blob(
                content,
                overwrite=True,
                metadata={
                    "sha256": digest,
                    "snapshot_id": str(row["snapshot_id"]),
                    "layout_version": layout_version,
                    "qa_only": "true",
                },
                content_settings=ContentSettings(content_type="application/pdf"),
            )
            artifacts.append(
                {
                    "account": account_id,
                    "period_end": period_end,
                    "snapshot_id": str(row["snapshot_id"]),
                    "blob_name": blob_name,
                    "sha256": digest,
                    "bytes": len(content),
                    "pages": int(result["pages"]),
                    "layout_version": str(result["layout_version"]),
                }
            )

    manifest = {
        "schema_version": 1,
        "account": account_id,
        "start": EXPECTED_PERIODS[0],
        "end": EXPECTED_PERIODS[-1],
        "period_count": len(artifacts),
        "layout_version": layout_version,
        "base_template_sha256": template_sha,
        "presentation_changed": False,
        "artifacts": artifacts,
    }
    content = canonical_json(manifest).encode("utf-8")
    manifest_blob = f"qa-return-panel-v7/{account_id}/manifest.json"
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
        "artifacts": artifacts,
        "presentation_changed": False,
    }


def _run_publish(
    connection,
    blob_service: BlobServiceClient,
    *,
    container: str,
    tenant_id: str,
    account_id: str,
) -> dict[str, object]:
    layout_version = factsheet_layout_version(account_id)
    manifest_blob = f"qa-return-panel-v7/{account_id}/manifest.json"
    manifest_content = blob_service.get_blob_client(container, manifest_blob).download_blob().readall()
    manifest = json.loads(manifest_content)
    if (
        manifest.get("account") != account_id
        or manifest.get("layout_version") != layout_version
        or int(manifest.get("period_count", 0)) != len(EXPECTED_PERIODS)
        or tuple(item.get("period_end") for item in manifest.get("artifacts", []))
        != EXPECTED_PERIODS
        or manifest.get("presentation_changed") is not False
    ):
        raise RuntimeError("QA manifest is incomplete or outside the approved scope")

    template_version = (
        f"sha256:{SUPPORTED_SUBJECTS[account_id]['template_sha256']}"
        f"+layout:{layout_version}"
    )
    approved_by = _required("REPORT_REPLACEMENT_APPROVED_BY").lower()
    published: list[dict[str, object]] = []
    for item in manifest["artifacts"]:
        period_end = str(item["period_end"])
        source_blob = str(item["blob_name"])
        if PurePosixPath(source_blob).parts[:2] != ("qa-return-panel-v7", account_id):
            raise RuntimeError(f"QA artifact is outside the approved prefix: {source_blob}")
        content = blob_service.get_blob_client(container, source_blob).download_blob().readall()
        digest = _sha256(content)
        if digest != str(item["sha256"]) or len(content) != int(item["bytes"]):
            raise RuntimeError(f"QA artifact integrity failed for {period_end}")
        _validate_pdf(content, period_end=period_end)

        run = connection.execute(
            """
            SELECT r.generation_run_id, r.status,
                   a.artifact_release_id
            FROM report_generation_runs r
            LEFT JOIN report_artifact_releases a
              ON a.tenant_id = r.tenant_id
             AND a.generation_run_id = r.generation_run_id
             AND a.release_scope = 'internal_shareholder'
            WHERE r.snapshot_id = ? AND r.report_type = 'factsheet'
            ORDER BY CASE WHEN a.artifact_release_id IS NOT NULL THEN 0 ELSE 1 END,
                     r.generation_run_id DESC
            LIMIT 1
            """,
            (str(item["snapshot_id"]),),
        ).fetchone()
        if run is None:
            raise RuntimeError(f"Factsheet generation run is missing for {period_end}")
        status = str(run["status"])
        if status == "published":
            raise RuntimeError(f"Client-published factsheet cannot be replaced: {period_end}")

        filename = f"{account_id}_Factsheet_{period_end}.pdf"
        review_blob = f"review/{account_id}/{period_end}/{filename}"
        review_reference = f"{container}/{review_blob}"
        metadata = {
            "sha256": digest,
            "snapshot_id": str(item["snapshot_id"]),
            "layout_upgrade": layout_version,
            "presentation_changed": "false",
        }
        blob_service.get_blob_client(container, review_blob).upload_blob(
            content,
            overwrite=True,
            metadata=metadata,
            content_settings=ContentSettings(content_type="application/pdf"),
        )
        now = utc_now_iso()
        if status == "released_internal":
            if run["artifact_release_id"] is None:
                raise RuntimeError(f"Released factsheet lacks release record: {period_end}")
            released_blob = f"released/{account_id}/{period_end}/{filename}"
            blob_service.get_blob_client(container, released_blob).upload_blob(
                content,
                overwrite=True,
                metadata={
                    **metadata,
                    "release_scope": "internal_shareholder",
                    "client_distribution": "prohibited",
                    "market_data_provider": "yfinance",
                },
                content_settings=ContentSettings(content_type="application/pdf"),
            )
            connection.execute(
                """
                UPDATE report_artifact_releases
                SET container_name = ?, blob_name = ?, artifact_sha256 = ?, released_at = ?
                WHERE artifact_release_id = ?
                """,
                (
                    container,
                    released_blob,
                    digest,
                    now,
                    int(run["artifact_release_id"]),
                ),
            )
        connection.execute(
            """
            UPDATE report_generation_runs
            SET status = ?, template_version = ?, output_reference = ?,
                output_sha256 = ?, completed_at = ?, error_message = NULL
            WHERE generation_run_id = ? AND report_type = 'factsheet'
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
            "layout_version": layout_version,
            "template_version": template_version,
            "presentation_changed": False,
        }
    )
    connection.execute(
        """
        INSERT INTO audit_events(
            tenant_id, occurred_at, actor_type, actor_id, event_type,
            subject_id, outcome, details_json
        ) VALUES (?, ?, 'user', ?, 'historical_factsheet_return_panel_v7_applied',
                  ?, 'historical_and_future_layout_updated', ?)
        """,
        (tenant_id, utc_now_iso(), approved_by, account_id, details),
    )
    connection.commit()
    return {
        "status": "PASS",
        "period_count": len(published),
        "layout_version": layout_version,
        "artifacts": published,
        "presentation_changed": False,
    }


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("Historical v6 upgrade may run only in staging")
    account_id = _required("UPGRADE_SUBJECT_ID")
    if account_id not in SUPPORTED_SUBJECTS:
        raise RuntimeError("UPGRADE_SUBJECT_ID is outside the approved replacement scope")
    if "return-panel" not in FACTSHEET_LAYOUT_VERSION:
        raise RuntimeError("The image does not contain the approved return-panel renderer")
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
            _run_qa(
                connection,
                service,
                container=container,
                account_id=account_id,
            )
            if mode == "qa"
            else _run_publish(
                connection,
                service,
                container=container,
                tenant_id=tenant_id,
                account_id=account_id,
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
