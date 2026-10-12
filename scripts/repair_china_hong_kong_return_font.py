"""Re-render only unreleased China & Hong Kong factsheets with approved typography.

Run `qa` first, then `publish`. The August 2026 reference and presentations are
never replaced. Original review blobs and generation records remain for audit.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from calendar import monthrange
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import queue_report_generation_run  # noqa: E402
from ibkr_etl.monthly_artifacts import (  # noqa: E402
    MonthlyArtifactError,
    factsheet_layout_version,
    render_factsheet,
    validate_factsheet_return_typography,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


SUBJECT_ID = "hong-kong-china"
REFERENCE_PERIOD = "2026-08-31"
QA_PREFIX = "qa-return-font-v8/hong-kong-china"


def _periods() -> tuple[str, ...]:
    values = []
    for year, first, last in ((2025, 1, 12), (2026, 1, 8)):
        for month in range(first, last + 1):
            values.append(date(year, month, monthrange(year, month)[1]).isoformat())
    return tuple(values)


EXPECTED_PERIODS = _periods()
REPAIR_PERIODS = EXPECTED_PERIODS[:-1]


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _snapshot_and_run(connection, period: str):
    rows = connection.execute(
        """
        SELECT snapshot_id, snapshot_payload_json, data_sha256
        FROM reporting_snapshots
        WHERE subject_id = ? AND period_end = ?
          AND status IN ('frozen', 'approved') AND quality_status <> 'fail'
        ORDER BY created_at DESC
        """,
        (SUBJECT_ID, period),
    ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"Expected one frozen snapshot for {period}; found {len(rows)}")
    snapshot = rows[0]
    run = connection.execute(
        """
        SELECT generation_run_id, status, output_reference, output_sha256,
               template_version
        FROM report_generation_runs
        WHERE snapshot_id = ? AND report_type = 'factsheet'
        ORDER BY generation_run_id DESC LIMIT 1
        """,
        (str(snapshot["snapshot_id"]),),
    ).fetchone()
    if run is None:
        raise RuntimeError(f"Factsheet generation record is missing for {period}")
    return snapshot, run


def _current_asset(connection, storage_url: str) -> tuple[bytes, str, str]:
    rows = connection.execute(
        """
        SELECT asset_role, container_name, blob_name, sha256
        FROM subject_report_template_assets
        WHERE subject_id = ? AND report_type = 'factsheet'
          AND asset_role IN ('template', 'completed_example') AND is_current
          AND approval_status IN ('validated', 'active')
        ORDER BY updated_at DESC
        """,
        (SUBJECT_ID,),
    ).fetchall()
    assets = {}
    for row in rows:
        assets.setdefault(str(row["asset_role"]), row)
    if set(assets) != {"template", "completed_example"}:
        raise RuntimeError("Current factsheet template and completed example are required")
    template, example = assets["template"], assets["completed_example"]
    content = download_blob_bytes(
        storage_url, str(template["container_name"]), str(template["blob_name"]),
        maximum_bytes=10 * 1024 * 1024,
    )
    template_sha = str(template["sha256"]).lower()
    if _sha(content) != template_sha:
        raise RuntimeError("Approved factsheet template hash mismatch")
    version = (
        f"sha256:{str(example['sha256']).lower()}"
        f"+supporting-sha256:{template_sha}"
        f"+layout:{factsheet_layout_version(SUBJECT_ID)}"
    )
    return content, template_sha, version


def _read_review_file(storage_url: str, container: str, run) -> bytes:
    reference = str(run["output_reference"] or "")
    prefix = f"{container}/review/{SUBJECT_ID}/"
    if not reference.startswith(prefix):
        raise RuntimeError("Factsheet review reference is outside its account")
    content = download_blob_bytes(storage_url, container, reference[len(container) + 1:])
    if _sha(content) != str(run["output_sha256"] or "").lower():
        raise RuntimeError("Stored factsheet hash no longer matches its record")
    return content


def _preflight(connection, storage_url: str, container: str) -> list[dict[str, object]]:
    items = []
    layout = factsheet_layout_version(SUBJECT_ID)
    for period in EXPECTED_PERIODS:
        snapshot, run = _snapshot_and_run(connection, period)
        if str(run["status"]) not in {"awaiting_approval", "released_internal"}:
            raise RuntimeError(f"Factsheet {period} is not in a reviewable state")
        content = _read_review_file(storage_url, container, run)
        try:
            validate_factsheet_return_typography(content)
            typography = "approved"
        except MonthlyArtifactError:
            typography = "legacy"
        if period == REFERENCE_PERIOD and typography != "approved":
            raise RuntimeError("The approved August reference changed unexpectedly")
        if period != REFERENCE_PERIOD and typography == "legacy":
            if str(run["status"]) != "awaiting_approval":
                raise RuntimeError(f"Previously released factsheet requires separate review: {period}")
            released = connection.execute(
                "SELECT artifact_release_id FROM report_artifact_releases WHERE generation_run_id = ?",
                (int(run["generation_run_id"]),),
            ).fetchone()
            if released:
                raise RuntimeError(f"A release record exists for {period}; refusing to replace it")
        items.append({
            "period_end": period,
            "snapshot_id": str(snapshot["snapshot_id"]),
            "generation_run_id": int(run["generation_run_id"]),
            "old_sha256": str(run["output_sha256"]).lower(),
            "typography": typography,
            "current_layout": str(run["template_version"]).endswith(f"+layout:{layout}"),
        })
    return items


def _qa(connection, storage_url: str, container: str) -> dict[str, object]:
    items = _preflight(connection, storage_url, container)
    template, template_sha, version = _current_asset(connection, storage_url)
    replacements = []
    with TemporaryDirectory(prefix="return-font-qa-") as directory:
        root = Path(directory)
        template_path = root / "template.pdf"
        template_path.write_bytes(template)
        for item in items:
            if item["typography"] == "approved":
                continue
            period = str(item["period_end"])
            snapshot, run = _snapshot_and_run(connection, period)
            if int(run["generation_run_id"]) != item["generation_run_id"]:
                raise RuntimeError(f"Latest generation record changed for {period}")
            payload_text = str(snapshot["snapshot_payload_json"])
            if _sha(payload_text.encode("utf-8")) != str(snapshot["data_sha256"]):
                raise RuntimeError(f"Frozen snapshot integrity failed for {period}")
            output = root / f"{SUBJECT_ID}_Factsheet_{period}_Return_Font_v8.pdf"
            render_factsheet(json.loads(payload_text), template_path=template_path, output_path=output)
            content = output.read_bytes()
            validate_factsheet_return_typography(content)
            blob = f"{QA_PREFIX}/{period}/{output.name}"
            upload_bytes(
                storage_url, container, blob, content, overwrite=True,
                content_type="application/pdf",
                metadata={"sha256": _sha(content), "snapshot_id": item["snapshot_id"], "qa_only": "true"},
            )
            replacements.append({**item, "new_sha256": _sha(content), "bytes": len(content), "qa_blob": blob})
    manifest = {
        "schema_version": 1,
        "subject_id": SUBJECT_ID,
        "reference_period": REFERENCE_PERIOD,
        "expected_periods": list(EXPECTED_PERIODS),
        "replacement_count": len(replacements),
        "template_sha256": template_sha,
        "template_version": version,
        "presentation_changed": False,
        "replacements": replacements,
    }
    manifest_content = canonical_json(manifest).encode("utf-8")
    upload_bytes(
        storage_url, container, f"{QA_PREFIX}/manifest.json", manifest_content,
        overwrite=True, content_type="application/json",
        metadata={"sha256": _sha(manifest_content), "qa_only": "true"},
    )
    return {
        "status": "qa_ready", "scanned": len(items),
        "replacement_count": len(replacements),
        "manifest_sha256": _sha(manifest_content),
        "reference_unchanged": True,
    }


def _publish(connection, storage_url: str, container: str, tenant_id: str) -> dict[str, object]:
    manifest_content = download_blob_bytes(storage_url, container, f"{QA_PREFIX}/manifest.json")
    manifest = json.loads(manifest_content)
    template, template_sha, version = _current_asset(connection, storage_url)
    del template
    if (
        manifest.get("subject_id") != SUBJECT_ID
        or manifest.get("reference_period") != REFERENCE_PERIOD
        or tuple(manifest.get("expected_periods", ())) != EXPECTED_PERIODS
        or manifest.get("template_sha256") != template_sha
        or manifest.get("template_version") != version
        or manifest.get("presentation_changed") is not False
    ):
        raise RuntimeError("QA manifest is stale or outside the approved repair scope")
    replacements = manifest.get("replacements", [])
    if len(replacements) != len(REPAIR_PERIODS) or tuple(
        item.get("period_end") for item in replacements
    ) != REPAIR_PERIODS:
        raise RuntimeError("QA manifest does not cover the expected 19 historical months")
    published = []
    for item in replacements:
        period = str(item["period_end"])
        snapshot, run = _snapshot_and_run(connection, period)
        if str(snapshot["snapshot_id"]) != item["snapshot_id"]:
            raise RuntimeError(f"Frozen snapshot changed for {period}")
        if int(run["generation_run_id"]) != item["generation_run_id"]:
            if str(run["template_version"]) == version and str(run["output_sha256"]) == item["new_sha256"]:
                published.append(period)  # A prior attempt completed this month.
                continue
            raise RuntimeError(f"Review record changed for {period}")
        if str(run["status"]) != "awaiting_approval" or str(run["output_sha256"]) != item["old_sha256"]:
            raise RuntimeError(f"Review record is no longer eligible for repair: {period}")
        if connection.execute(
            "SELECT artifact_release_id FROM report_artifact_releases WHERE generation_run_id = ?",
            (int(run["generation_run_id"]),),
        ).fetchone():
            raise RuntimeError(f"A release record appeared for {period}")
        blob = str(item["qa_blob"])
        if not blob.startswith(f"{QA_PREFIX}/{period}/"):
            raise RuntimeError(f"QA blob is outside the approved account/month: {period}")
        content = download_blob_bytes(storage_url, container, blob)
        if _sha(content) != item["new_sha256"] or len(content) != int(item["bytes"]):
            raise RuntimeError(f"QA PDF integrity failed for {period}")
        validate_factsheet_return_typography(content)
        filename = f"{SUBJECT_ID}_Factsheet_{period}_Return_Font_v8.pdf"
        review_blob = f"review/{SUBJECT_ID}/{period}/{filename}"
        try:
            upload_bytes(
                storage_url, container, review_blob, content, overwrite=False,
                content_type="application/pdf",
                metadata={"sha256": item["new_sha256"], "snapshot_id": item["snapshot_id"], "layout_version": factsheet_layout_version(SUBJECT_ID)},
            )
        except Exception:
            existing = download_blob_bytes(storage_url, container, review_blob)
            if _sha(existing) != item["new_sha256"]:
                raise
        run_id = queue_report_generation_run(
            connection, tenant_id=tenant_id, snapshot_id=item["snapshot_id"],
            report_type="factsheet", template_id=f"{SUBJECT_ID}-monthly-factsheet",
            template_version=version,
        )
        updated = connection.execute(
            """
            UPDATE report_generation_runs
            SET status = 'awaiting_approval', output_reference = ?, output_sha256 = ?,
                completed_at = ?, error_message = NULL
            WHERE tenant_id = ? AND generation_run_id = ? AND status = 'queued'
            """,
            (f"{container}/{review_blob}", item["new_sha256"], utc_now_iso(), tenant_id, run_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError(f"New factsheet generation run was not queued for {period}")
        connection.commit()
        published.append(period)
    connection.execute(
        """
        INSERT INTO audit_events(
            tenant_id, occurred_at, actor_type, actor_id, event_type,
            subject_id, outcome, details_json
        ) VALUES (?, ?, 'system', 'return-font-repair',
                  'china_hong_kong_return_font_repair', ?, 'awaiting_owner_approval', ?)
        """,
        (tenant_id, utc_now_iso(), SUBJECT_ID, canonical_json({
            "manifest_sha256": _sha(manifest_content), "repaired_periods": published,
            "template_version": version, "presentations_changed": False,
            "reference_changed": False,
        })),
    )
    connection.commit()
    return {"status": "repaired", "periods": published, "reference_unchanged": True}


def main() -> int:
    if _required("DEPLOYMENT_ENVIRONMENT").lower() != "internal-production":
        raise RuntimeError("Return-font repair is restricted to internal production")
    mode = _required("RETURN_FONT_MODE").lower()
    if mode not in {"qa", "publish"}:
        raise RuntimeError("RETURN_FONT_MODE must be qa or publish")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("PostgreSQL connection is required")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        result = (
            _qa(connection, storage_url, container) if mode == "qa"
            else _publish(connection, storage_url, container, tenant_id)
        )
        print(json.dumps(result, sort_keys=True))
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
