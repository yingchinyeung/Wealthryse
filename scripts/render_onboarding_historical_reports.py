from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from azure.identity import DefaultAzureCredential  # noqa: E402
from azure.storage.blob import BlobServiceClient, ContentSettings  # noqa: E402
from azure.core.exceptions import ResourceExistsError  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import queue_report_generation_run  # noqa: E402
from ibkr_etl.monthly_artifacts import (  # noqa: E402
    factsheet_render_contract_sha256,
    factsheet_layout_version,
    render_factsheet,
)
from ibkr_etl.presentation_design import (  # noqa: E402
    presentation_content_contract_version,
    presentation_content_requirements,
)
from ibkr_etl.template_upload import presentation_identity_scope  # noqa: E402
from ibkr_etl.util import canonical_json, sha256_text, utc_now_iso  # noqa: E402
from validate_subject_presentation import validate_subject_presentation  # noqa: E402


PLATFORM_PRESENTATION_SHA256 = "4393eda47945c9df17167e8cbe5278afe90f20b16f52bd473ff3e7549a759191"
PLATFORM_PRESENTATION_DEFAULT_PATH = (
    "/app/templates/U16088571/approved-august-2026-20-slide-v24.pptx"
)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _immutable_review_blob(subject_id: str, period_end: str, path: Path, digest: str) -> str:
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('A valid artifact digest is required')
    return f'review/{subject_id}/{period_end}/{path.stem}_{digest}{path.suffix}'


def _upload_review(client, content: bytes, *, metadata: dict, content_type: str) -> None:
    """Create immutable content-addressed artifacts; never overwrite old evidence."""
    try:
        client.upload_blob(content, overwrite=False, metadata=metadata,
            content_settings=ContentSettings(content_type=content_type))
    except ResourceExistsError:
        if client.download_blob().readall() != content:
            raise RuntimeError('Existing immutable review artifact differs')
    if client.download_blob().readall() != content:
        raise RuntimeError('Review artifact readback integrity failed')


def _blob_service(storage_url: str) -> BlobServiceClient:
    return BlobServiceClient(
        account_url=storage_url,
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True),
    )


def _validate_source(path: Path, expected_sha256: str, label: str) -> str:
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {path}")
    digest = _sha256(path.read_bytes())
    if digest != expected_sha256.lower():
        raise RuntimeError(f"{label} SHA-256 does not match the locked contract")
    return digest


def _render_presentation(
    *,
    template: Path,
    snapshot: Path,
    output: Path,
    period_end: str,
    subject_id: str,
    expected_strategy: str,
) -> dict[str, object]:
    renderer = "render_alpha_t_presentation.mjs" if subject_id == 'alpha-world-t' else "render_monthly_presentation.mjs"
    completed = subprocess.run(
        [
            os.environ.get("NODE_BINARY", "node"),
            str(PROJECT_ROOT / "scripts" / renderer),
            str(template),
            str(snapshot),
            str(output),
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "PRESENTATION_STRATEGY_NAME": expected_strategy},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr or completed.stdout or "unknown renderer error"
        raise RuntimeError("Presentation rendering failed: " + message[-6000:])
    return validate_subject_presentation(
        output, snapshot, subject_id=subject_id, period_end=period_end,
        expected_strategy=expected_strategy,
    )


def _expected_strategy(connection, subject_id: str, presentation_sha: str) -> str:
    row = connection.execute(
        """
        SELECT t.sha256, t.metadata_json, s.display_name,
               a.approval_id, a.scope_json, a.scope_sha256
        FROM subject_report_template_assets t
        JOIN platform_subjects s
          ON s.tenant_id = t.tenant_id AND s.subject_id = t.subject_id
        LEFT JOIN account_onboarding_approvals a
          ON a.tenant_id = t.tenant_id AND a.subject_id = t.subject_id
         AND a.approval_type = 'presentation_identity'
         AND a.approval_status = 'approved'
        WHERE t.subject_id = ? AND t.report_type = 'presentation'
          AND t.asset_role = 'template' AND t.is_current
        """,
        (subject_id,),
    ).fetchone()
    if row is None or str(row["sha256"]) != presentation_sha:
        raise RuntimeError("The current presentation template contract is missing or stale")
    approval_scope = row["scope_json"] or {}
    if isinstance(approval_scope, str):
        approval_scope = json.loads(approval_scope)
    if not isinstance(approval_scope, dict) or row["approval_id"] is None:
        raise RuntimeError("The owner-approved presentation identity is missing")
    expected_scope = presentation_identity_scope(
        subject_id=subject_id,
        display_name=str(row["display_name"]),
        layout_policy=str(approval_scope.get("layout_policy") or ""),
    )
    expected_scope_sha256 = sha256_text(canonical_json(expected_scope))
    if (
        approval_scope != expected_scope
        or str(row["scope_sha256"]) != expected_scope_sha256
    ):
        raise RuntimeError("The owner-approved presentation identity is stale")
    metadata = row["metadata_json"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    contract = metadata.get("presentation_contract") if isinstance(metadata, dict) else None
    if isinstance(contract, dict) and str(contract.get("strategy_name") or "").strip():
        if str(contract.get("source_template_sha256") or "") != presentation_sha:
            raise RuntimeError("The presentation identity contract is bound to another template")
        if str(contract["strategy_name"]).strip() != expected_scope["strategy_name"]:
            raise RuntimeError("The template strategy differs from the owner-approved identity")
        if subject_id != "U16088606" and (
            str(contract.get("owner_approval_id") or "") != str(row["approval_id"])
            or str(contract.get("owner_approval_scope_sha256") or "")
            != expected_scope_sha256
        ):
            raise RuntimeError("The template is not bound to the current owner approval")
        return str(expected_scope["strategy_name"])
    # U16088606's documents were accepted before the template-intake workflow
    # embedded the owner approval into the uploaded asset metadata.  Its
    # identity is now independently locked by migration 032, so the dedicated
    # approval record is the authoritative evidence as long as the current
    # asset hash and the server-derived scope both match.  Do not extend this
    # compatibility path to future subjects: their intake must remain bound to
    # the current owner approval in the asset metadata.
    if subject_id == "U16088606":
        return str(expected_scope["strategy_name"])
    raise RuntimeError("The presentation template has no strategy identity contract")


def _active_batch(connection, subject_id: str):
    batch = connection.execute(
        """
        SELECT *
        FROM onboarding_historical_report_batches
        WHERE subject_id = ? AND status <> 'superseded'
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (subject_id,),
    ).fetchone()
    if batch is None:
        raise RuntimeError("No active historical onboarding batch exists")
    allowed = {"queued", "freezing_snapshots", "rendering"}
    if str(batch["status"]) == "failed" and str(batch["error_code"] or "") == "rendering_failed":
        allowed.add("failed")
    if str(batch["status"]) not in allowed:
        raise RuntimeError(f"Historical batch cannot render from {batch['status']}")
    return batch


def _items(connection, batch_id: str) -> list[object]:
    rows = connection.execute(
        """
        SELECT *
        FROM onboarding_historical_report_items
        WHERE batch_id = ?
        ORDER BY period_end, report_type
        """,
        (batch_id,),
    ).fetchall()
    if not rows or len(rows) % 2:
        raise RuntimeError("Historical batch item inventory is incomplete")
    by_period: dict[str, set[str]] = {}
    for row in rows:
        by_period.setdefault(str(row["period_end"]), set()).add(str(row["report_type"]))
    if any(types != {"factsheet", "presentation"} for types in by_period.values()):
        raise RuntimeError("Every completed month must have one factsheet and presentation")
    return list(rows)


def _snapshots(
    connection, tenant_id: str, subject_id: str, periods: list[str]
) -> dict[str, object]:
    rows = connection.execute(
        """
        SELECT snapshot_id, period_end, snapshot_payload_json, data_sha256
        FROM reporting_snapshots
        WHERE tenant_id = ? AND subject_id = ?
          AND period_end >= ? AND period_end <= ?
          AND status IN ('frozen', 'approved') AND quality_status <> 'fail'
        ORDER BY period_end, snapshot_id
        """,
        (tenant_id, subject_id, periods[0], periods[-1]),
    ).fetchall()
    result: dict[str, object] = {}
    for row in rows:
        period = str(row["period_end"])
        if period not in periods:
            continue
        if period in result:
            raise RuntimeError(f"Multiple active reporting snapshots exist for {period}")
        payload = str(row["snapshot_payload_json"])
        if _sha256(payload.encode("utf-8")) != str(row["data_sha256"]):
            raise RuntimeError(f"Frozen snapshot integrity failed for {period}")
        result[period] = row
    missing = [period for period in periods if period not in result]
    if missing:
        raise RuntimeError("Active frozen snapshots are missing: " + ", ".join(missing))
    return result


def _update_item(
    connection,
    *,
    batch_id: str,
    period_end: str,
    report_type: str,
    snapshot_id: str,
    generation_run_id: int,
    container: str,
    blob_name: str,
    artifact_sha256: str,
    byte_count: int,
    validation: dict[str, object],
) -> None:
    connection.execute(
        """
        UPDATE onboarding_historical_report_items
        SET snapshot_id = ?, generation_run_id = ?, status = 'validated',
            container_name = ?, blob_name = ?, artifact_sha256 = ?,
            byte_count = ?, validation_json = ?, error_message = NULL,
            updated_at = now()
        WHERE batch_id = ? AND period_end = ? AND report_type = ?
        """,
        (
            snapshot_id,
            generation_run_id,
            container,
            blob_name,
            artifact_sha256,
            byte_count,
            canonical_json(validation),
            batch_id,
            period_end,
            report_type,
        ),
    )
    connection.execute(
        """
        UPDATE report_generation_runs
        SET status = 'awaiting_approval', output_reference = ?,
            output_sha256 = ?, completed_at = ?, error_message = NULL
        WHERE generation_run_id = ?
        """,
        (f"{container}/{blob_name}", artifact_sha256, utc_now_iso(), generation_run_id),
    )


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("Onboarding report rendering may run only in staging")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    subject_id = _required("ONBOARDING_SUBJECT_ID")
    presentation_layout = presentation_content_contract_version(subject_id)
    presentation_requirements = presentation_content_requirements(subject_id)
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports").strip()

    factsheet_template = Path(_required("FACTSHEET_TEMPLATE_PATH"))
    factsheet_example = Path(_required("FACTSHEET_COMPLETED_EXAMPLE_PATH"))
    presentation_template = Path(_required("PRESENTATION_TEMPLATE_PATH"))
    platform_presentation_template = Path(
        os.environ.get(
            "PLATFORM_PRESENTATION_TEMPLATE_PATH",
            PLATFORM_PRESENTATION_DEFAULT_PATH,
        ).strip()
    )
    factsheet_example_sha = _validate_source(
        factsheet_example,
        _required("FACTSHEET_COMPLETED_EXAMPLE_SHA256"),
        "Completed factsheet design model",
    )
    _validate_source(
        factsheet_template,
        _required("FACTSHEET_SUPPORTING_TEMPLATE_SHA256"),
        "Supporting factsheet template",
    )
    presentation_sha = _validate_source(
        presentation_template,
        _required("PRESENTATION_TEMPLATE_SHA256"),
        "Owner-approved presentation identity evidence",
    )
    platform_presentation_sha = _validate_source(
        platform_presentation_template,
        os.environ.get(
            "PLATFORM_PRESENTATION_TEMPLATE_SHA256",
            PLATFORM_PRESENTATION_SHA256,
        ).strip(),
        "Approved U16088571 presentation layout model",
    )
    if len(PdfReader(factsheet_example).pages) != 2 or len(PdfReader(factsheet_template).pages) != 2:
        raise RuntimeError("The factsheet model and supporting template must have two pages")

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    batch_id = ""
    try:
        batch = _active_batch(connection, subject_id)
        expected_strategy = _expected_strategy(connection, subject_id, presentation_sha)
        batch_id = str(batch["batch_id"])
        items = _items(connection, batch_id)
        periods = sorted({str(row["period_end"]) for row in items})
        if len(periods) != int(batch["expected_month_count"]):
            raise RuntimeError("Historical period count does not match the batch contract")
        factsheet_contract_sha = factsheet_render_contract_sha256(
            subject_id=subject_id,
            supporting_template_sha256=_required("FACTSHEET_SUPPORTING_TEMPLATE_SHA256"),
            completed_example_sha256=factsheet_example_sha,
        )
        for row in items:
            expected = (
                factsheet_contract_sha
                if row["report_type"] == "factsheet"
                else platform_presentation_sha
            )
            if str(row["template_sha256"]) != expected:
                raise RuntimeError("A batch item is bound to a stale design model")

        connection.execute(
            """
            UPDATE onboarding_historical_report_batches
            SET status = 'rendering', started_at = COALESCE(started_at, now()),
                error_code = NULL, error_message = NULL
            WHERE batch_id = ?
            """,
            (batch_id,),
        )
        connection.commit()
        snapshots = _snapshots(connection, tenant_id, subject_id, periods)
        service = _blob_service(storage_url)
        artifact_records: list[dict[str, object]] = []

        with TemporaryDirectory(prefix=f"{subject_id}-historical-reports-") as directory:
            root = Path(directory)
            for period_end in periods:
                row = snapshots[period_end]
                snapshot_id = str(row["snapshot_id"])
                payload_text = str(row["snapshot_payload_json"])
                snapshot_path = root / f"{period_end}.json"
                snapshot_path.write_text(payload_text, encoding="utf-8")

                factsheet_path = root / f"{subject_id}_Factsheet_{period_end}.pdf"
                factsheet_result = render_factsheet(
                    json.loads(payload_text),
                    template_path=factsheet_template,
                    design_model_path=factsheet_example,
                    output_path=factsheet_path,
                )
                if factsheet_result.get("render_contract_sha256") != factsheet_contract_sha:
                    raise RuntimeError(
                        "Generated factsheet is not bound to the approved subject design pair"
                    )
                factsheet_content = factsheet_path.read_bytes()
                factsheet_digest = _sha256(factsheet_content)
                factsheet_blob = _immutable_review_blob(subject_id, period_end, factsheet_path, factsheet_digest)
                _upload_review(
                    service.get_blob_client(container, factsheet_blob), factsheet_content,
                    metadata={
                        "sha256": factsheet_digest,
                        "snapshot_id": snapshot_id,
                        "design_model_sha256": factsheet_example_sha,
                        "layout_version": str(factsheet_result["layout_version"]),
                    },
                    content_type="application/pdf",
                )
                factsheet_run = queue_report_generation_run(
                    connection,
                    tenant_id=tenant_id,
                    snapshot_id=snapshot_id,
                    report_type="factsheet",
                    template_id=f"{subject_id}-completed-factsheet-model",
                    template_version=f"sha256:{factsheet_example_sha}+layout:{factsheet_layout_version(subject_id)}",
                )
                factsheet_validation = {
                    "passed": True,
                    "pages": int(factsheet_result["pages"]),
                    "layout_version": str(factsheet_result["layout_version"]),
                    "design_source": "subject_completed_factsheet",
                    "design_model_sha256": factsheet_example_sha,
                    "supporting_template_sha256": factsheet_result[
                        "supporting_template_sha256"
                    ],
                    "render_contract_sha256": factsheet_contract_sha,
                    "render_contract_version": factsheet_result[
                        "render_contract_version"
                    ],
                    "snapshot_sha256": str(row["data_sha256"]),
                }
                _update_item(
                    connection,
                    batch_id=batch_id,
                    period_end=period_end,
                    report_type="factsheet",
                    snapshot_id=snapshot_id,
                    generation_run_id=factsheet_run,
                    container=container,
                    blob_name=factsheet_blob,
                    artifact_sha256=factsheet_digest,
                    byte_count=len(factsheet_content),
                    validation=factsheet_validation,
                )
                artifact_records.append(
                    {
                        "period_end": period_end,
                        "report_type": "factsheet",
                        "snapshot_id": snapshot_id,
                        "generation_run_id": factsheet_run,
                        "blob_name": factsheet_blob,
                        "sha256": factsheet_digest,
                        "bytes": len(factsheet_content),
                        "validation": factsheet_validation,
                    }
                )

                presentation_path = root / f"{subject_id}_Monthly_Presentation_{period_end}.pptx"
                presentation_validation = _render_presentation(
                    template=platform_presentation_template,
                    snapshot=snapshot_path,
                    output=presentation_path,
                    period_end=period_end,
                    subject_id=subject_id,
                    expected_strategy=expected_strategy,
                )
                presentation_content = presentation_path.read_bytes()
                presentation_digest = _sha256(presentation_content)
                if (presentation_validation["output_sha256"] != presentation_digest
                        or presentation_validation["snapshot_sha256"] != str(row["data_sha256"])
                        or presentation_validation["content_contract_version"] != presentation_layout):
                    raise RuntimeError("Validated presentation no longer matches the registered artifact/input contract")
                presentation_blob = _immutable_review_blob(subject_id, period_end, presentation_path, presentation_digest)
                _upload_review(
                    service.get_blob_client(container, presentation_blob), presentation_content,
                    metadata={
                        "sha256": presentation_digest,
                        "snapshot_id": snapshot_id,
                        "design_model_sha256": platform_presentation_sha,
                        "identity_evidence_sha256": presentation_sha,
                        "layout_version": presentation_layout,
                    },
                    content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                )
                presentation_run = queue_report_generation_run(
                    connection,
                    tenant_id=tenant_id,
                    snapshot_id=snapshot_id,
                    report_type="presentation",
                    template_id="approved-u16088571-platform-presentation-layout",
                    template_version=f"sha256:{platform_presentation_sha}+layout:{presentation_layout}",
                )
                deck_validation = {
                    "passed": True,
                    "slides": int(presentation_validation["slides"]),
                    "layout_version": presentation_layout,
                    "design_source": "approved_u16088571_platform_layout_with_subject_identity",
                    "design_model_sha256": platform_presentation_sha,
                    "identity_evidence_sha256": presentation_sha,
                    "snapshot_sha256": str(row["data_sha256"]),
                    "performance_axis": presentation_validation["performance_axis"],
                    "strategy_name": expected_strategy,
                    "content_contract_version": presentation_validation["content_contract_version"],
                    "content_requirements": list(presentation_requirements),
                    "output_validation": presentation_validation,
                }
                _update_item(
                    connection,
                    batch_id=batch_id,
                    period_end=period_end,
                    report_type="presentation",
                    snapshot_id=snapshot_id,
                    generation_run_id=presentation_run,
                    container=container,
                    blob_name=presentation_blob,
                    artifact_sha256=presentation_digest,
                    byte_count=len(presentation_content),
                    validation=deck_validation,
                )
                artifact_records.append(
                    {
                        "period_end": period_end,
                        "report_type": "presentation",
                        "snapshot_id": snapshot_id,
                        "generation_run_id": presentation_run,
                        "blob_name": presentation_blob,
                        "sha256": presentation_digest,
                        "bytes": len(presentation_content),
                        "validation": deck_validation,
                    }
                )
                connection.execute(
                    """
                    UPDATE onboarding_historical_report_batches
                    SET rendered_artifact_count = ?, validated_artifact_count = ?
                    WHERE batch_id = ?
                    """,
                    (len(artifact_records), len(artifact_records), batch_id),
                )
                connection.commit()

        manifest = {
            "schema_version": 2,
            "subject_id": subject_id,
            "batch_id": batch_id,
            "period_count": len(periods),
            "artifact_count": len(artifact_records),
            "factsheet_design_source": "subject_completed_factsheet",
            "factsheet_design_model_sha256": factsheet_example_sha,
            "presentation_design_source": "approved_u16088571_platform_layout_with_subject_identity",
            "presentation_design_model_sha256": platform_presentation_sha,
            "presentation_identity_evidence_sha256": presentation_sha,
            "presentation_strategy_name": expected_strategy,
            "presentation_content_contract_version": presentation_layout,
            "presentation_content_requirements": list(presentation_requirements),
            "artifacts": artifact_records,
        }
        manifest_json = canonical_json(manifest)
        manifest_sha = _sha256(manifest_json.encode("utf-8"))
        connection.execute(
            """
            UPDATE onboarding_historical_report_batches
            SET status = 'awaiting_owner_approval', manifest_json = ?,
                manifest_sha256 = ?, rendered_artifact_count = ?,
                validated_artifact_count = ?, completed_at = now(),
                error_code = NULL, error_message = NULL
            WHERE batch_id = ?
            """,
            (
                manifest_json,
                manifest_sha,
                len(artifact_records),
                len(artifact_records),
                batch_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'system', 'onboarding-report-renderer',
                      'onboarding_historical_reports_rendered', ?,
                      'awaiting_owner_approval', ?)
            """,
            (
                tenant_id,
                utc_now_iso(),
                subject_id,
                canonical_json(
                    {
                        "batch_id": batch_id,
                        "manifest_sha256": manifest_sha,
                        "period_count": len(periods),
                        "artifact_count": len(artifact_records),
                    }
                ),
            ),
        )
        connection.commit()
        print(
            canonical_json(
                {
                    "status": "AWAITING_OWNER_APPROVAL",
                    "subject_id": subject_id,
                    "batch_id": batch_id,
                    "period_count": len(periods),
                    "artifact_count": len(artifact_records),
                    "manifest_sha256": manifest_sha,
                }
            )
        )
        return 0
    except Exception as error:
        connection.rollback()
        if batch_id:
            connection.execute(
                """
                UPDATE onboarding_historical_report_batches
                SET status = 'failed', error_code = 'rendering_failed',
                    error_message = ?, completed_at = now()
                WHERE batch_id = ?
                """,
                (str(error)[:2000], batch_id),
            )
            connection.commit()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
