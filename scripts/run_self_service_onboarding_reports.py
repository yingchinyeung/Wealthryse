from __future__ import annotations

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

from ibkr_etl.cloud_storage import download_blob_bytes  # noqa: E402
from ibkr_etl.crisis_replay import (  # noqa: E402
    refresh_composite_crisis_replay,
    refresh_crisis_replay,
)
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import run_historical_month_end_batch  # noqa: E402
from ibkr_etl.monthly_artifacts import factsheet_layout_version  # noqa: E402
from ibkr_etl.onboarding_reports import template_evidence  # noqa: E402
from ibkr_etl.presentation_design import (  # noqa: E402
    presentation_content_contract_version,
)
from ibkr_etl.sector_classification import refresh_sector_classifications_range  # noqa: E402
from ibkr_etl.util import canonical_json  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _asset_rows(connection, subject_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT report_type, asset_role, version_label, container_name,
               blob_name, content_type, sha256, byte_count,
               approval_status, metadata_json, is_current
        FROM subject_report_template_assets
        WHERE subject_id = ? AND is_current
          AND (report_type, asset_role) IN (
              ('factsheet', 'template'),
              ('factsheet', 'completed_example'),
              ('presentation', 'template')
          )
        ORDER BY report_type, asset_role
        """,
        (subject_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def _next_batch(connection):
    return connection.execute(
        """
        SELECT b.batch_id, b.subject_id, b.reporting_start, b.reporting_end,
               s.reporting_currency, s.subject_type
        FROM onboarding_historical_report_batches b
        JOIN platform_subjects s
          ON s.tenant_id = b.tenant_id AND s.subject_id = b.subject_id
        WHERE b.status IN ('queued', 'freezing_snapshots')
        ORDER BY b.requested_at
        FOR UPDATE OF b SKIP LOCKED
        LIMIT 1
        """
    ).fetchone()


def _existing_ms_snapshots(connection, batch, tenant_id: str) -> dict:
    """Render qualified MS snapshots without refreshing unqualified crisis data."""
    from ibkr_etl.subject_read_contract import require_ms_hkd_contract
    from ibkr_etl.alpha_world_ms_analytics import analyze
    from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot
    from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id
    from generate_month_end_package import _load_frozen_snapshot

    if str(batch['subject_id']) != 'alpha-world-ms' or str(batch['reporting_currency']) != 'HKD':
        raise ValueError('Existing MS snapshot rendering requires the HKD contract')
    require_ms_hkd_contract(connection, tenant_id=tenant_id)
    periods = [str(row['period_end']) for row in connection.execute(
        'SELECT DISTINCT period_end FROM onboarding_historical_report_items WHERE batch_id = ? ORDER BY period_end',
        (str(batch['batch_id']),),
    ).fetchall()]
    if not periods or periods[-1] != str(batch['reporting_end']):
        raise ValueError('MS registered reporting periods are incomplete')
    records = []
    for period in periods:
        snapshot_id = active_month_end_snapshot_id(connection, tenant_id=tenant_id,
            subject_id='alpha-world-ms', period_end=period)
        if not snapshot_id:
            raise ValueError('MS requires a qualified frozen HKD snapshot for ' + period)
        state = connection.execute(
            'SELECT quality_status, publication_allowed, reporting_currency FROM reporting_snapshots WHERE snapshot_id = ?',
            (snapshot_id,),
        ).fetchone()
        if (not state or state['quality_status'] == 'fail'
                or state['publication_allowed'] or state['reporting_currency'] != 'HKD'):
            raise ValueError('MS snapshot quality or private-publication boundary differs')
        payload, digest = _load_frozen_snapshot(connection, tenant_id=tenant_id,
            account_id='alpha-world-ms', snapshot_id=snapshot_id)
        if (payload['reporting_currency'] != 'HKD'
                or payload['period_start'] != str(batch['reporting_start'])
                or payload['period_end'] != period):
            raise ValueError('MS frozen snapshot identity differs')
        metrics, _, _ = analyze(payload)
        _, current_metrics, _ = load_risk_snapshot(connection, payload['data_as_of'], tenant_id=tenant_id)
        if metrics != current_metrics or payload['risk']['metrics'] != metrics:
            raise ValueError('MS frozen snapshot has stale calculation inputs')
        records.append({'snapshot_id':snapshot_id,'period_end':period,'data_sha256':digest})
    return {'items':records,'snapshot_source':'existing validated immutable HKD snapshots',
        'unqualified_crisis_replay_refreshed':False,'publication_approval_changed':False}


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").strip().lower() != "staging":
        raise RuntimeError("Self-service onboarding reports may run only in staging")
    database_url = _required("DATABASE_URL")
    tenant_id = _required("PORTFOLIO_TENANT_ID")
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    batch_id = ""
    try:
        batch = _next_batch(connection)
        if batch is None:
            print(canonical_json({"status": "idle", "message": "No queued onboarding report batch"}))
            return 0
        batch_id = str(batch["batch_id"])
        subject_id = str(batch["subject_id"])
        subject_type = str(batch["subject_type"])
        if subject_type not in {"physical_account", "filtered_portfolio", "composite_portfolio"}:
            raise RuntimeError(
                "The standard holdings-based report pack is unavailable for this subject type"
            )
        approved = connection.execute(
            """
            SELECT 1 FROM account_onboarding_approvals
            WHERE subject_id = ? AND approval_type = 'external_market_data'
              AND approval_status = 'approved' AND revoked_at IS NULL
            LIMIT 1
            """,
            (subject_id,),
        ).fetchone()
        if approved is None:
            raise RuntimeError("External market-data approval is missing")
        assets = _asset_rows(connection, subject_id)
        evidence = template_evidence(assets, subject_id=subject_id)
        connection.execute(
            """
            UPDATE onboarding_historical_report_batches
            SET status = 'freezing_snapshots',
                started_at = COALESCE(started_at, now()),
                error_code = NULL, error_message = NULL
            WHERE batch_id = ?
            """,
            (batch_id,),
        )
        connection.commit()

        if subject_type == "composite_portfolio":
            classifications = {
                "status": "not_applicable",
                "classification_basis": "approved composite sleeve types",
                "unclassified": 0,
            }
        else:
            classifications = refresh_sector_classifications_range(
                connection,
                subject_id=subject_id,
                start_date=str(batch["reporting_start"]),
                end_date=str(batch["reporting_end"]),
                workers=int(os.environ.get("SECTOR_CLASSIFICATION_WORKERS", "4")),
            )
            if classifications["unclassified"]:
                raise RuntimeError(
                    "Historical sector classification is incomplete: "
                    + ", ".join(classifications["unclassified_symbols"])
                )
        if subject_id == 'alpha-world-ms':
            result = _existing_ms_snapshots(connection, batch, tenant_id)
        else:
            result = run_historical_month_end_batch(
                connection,
                tenant_id=tenant_id,
                account_id=subject_id,
                period_start=str(batch["reporting_start"]),
                start_date=str(batch["reporting_start"]),
                end_date=str(batch["reporting_end"]),
                reporting_currency=str(batch["reporting_currency"]),
                factsheet_template_id=f"{subject_id}-completed-factsheet-model",
                factsheet_template_version=(
                    f"sha256:{evidence['factsheet']['sha256']}+layout:"
                    f"{factsheet_layout_version(subject_id)}"
                ),
                presentation_template_id="approved-u16088571-platform-presentation-layout",
                presentation_template_version=(
                    f"sha256:{evidence['presentation']['sha256']}+layout:"
                    f"{presentation_content_contract_version(subject_id)}"
                ),
                replay_refresher=(
                    refresh_composite_crisis_replay
                    if subject_type == "composite_portfolio"
                    else refresh_crisis_replay
                ),
            )
        by_key = {(row["report_type"], row["asset_role"]): row for row in assets}
        with TemporaryDirectory(prefix=f"{subject_id}-onboarding-assets-") as directory:
            root = Path(directory)
            local: dict[tuple[str, str], Path] = {}
            for key, row in by_key.items():
                suffix = ".pptx" if key[0] == "presentation" else ".pdf"
                path = root / f"{key[0]}-{key[1]}{suffix}"
                content = download_blob_bytes(
                    storage_url,
                    str(row["container_name"]),
                    str(row["blob_name"]),
                    maximum_bytes=50 * 1024 * 1024,
                )
                path.write_bytes(content)
                local[key] = path
            environment = dict(os.environ)
            environment.update(
                {
                    "ONBOARDING_SUBJECT_ID": subject_id,
                    "FACTSHEET_TEMPLATE_PATH": str(local[("factsheet", "template")]),
                    "FACTSHEET_COMPLETED_EXAMPLE_PATH": str(local[("factsheet", "completed_example")]),
                    "PRESENTATION_TEMPLATE_PATH": str(local[("presentation", "template")]),
                    "FACTSHEET_SUPPORTING_TEMPLATE_SHA256": str(by_key[("factsheet", "template")]["sha256"]),
                    "FACTSHEET_COMPLETED_EXAMPLE_SHA256": str(by_key[("factsheet", "completed_example")]["sha256"]),
                    "PRESENTATION_TEMPLATE_SHA256": str(by_key[("presentation", "template")]["sha256"]),
                }
            )
            subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "render_onboarding_historical_reports.py")],
                cwd=PROJECT_ROOT,
                env=environment,
                check=True,
                timeout=7200,
            )
        print(
            canonical_json(
                {
                    "status": "success",
                    "subject_id": subject_id,
                    "batch_id": batch_id,
                    "snapshots": len(result["items"]),
                    "classification": classifications,
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
                SET status = 'failed',
                    error_code = COALESCE(error_code, 'pipeline_failed'),
                    error_message = CASE
                        WHEN error_message IS NULL OR error_message = '' THEN ?
                        ELSE error_message
                    END,
                    completed_at = now()
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
