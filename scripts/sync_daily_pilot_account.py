from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import _service, content_type_for_path  # noqa: E402
from ibkr_etl.daily_account_sync import (  # noqa: E402
    parse_account_sync_targets,
    validate_production_export_manifest,
)
from ibkr_etl.db import DEFAULT_TENANT_ID  # noqa: E402
from ibkr_etl.onboarding import (  # noqa: E402
    discover_v11_accounts,
    ensure_feature_states,
    ensure_physical_subject,
    ensure_wave0_case,
    mark_import_reconciled,
    register_source_discoveries,
)
from ibkr_etl.pilot_import import file_sha256, import_snapshot  # noqa: E402
from ibkr_etl.pilot_snapshot import build_pilot_snapshot, validate_pilot_snapshot  # noqa: E402


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _download(service, container: str, blob_name: str, destination: Path) -> None:
    client = service.get_blob_client(container=container, blob=blob_name)
    if not client.exists():
        raise RuntimeError(f"Required production export does not exist: {blob_name}")
    destination.write_bytes(client.download_blob().readall())


def _register_onboarding_foundation(
    *,
    database_url: str,
    tenant_id: str,
    discoveries: list[dict],
    source_export_sha256: str,
    active_account_id: str,
    wave0_account_id: str,
    start_dates: dict[str, str],
) -> None:
    import psycopg

    by_account = {item["account_id"]: item for item in discoveries}
    for required in (active_account_id, wave0_account_id):
        if required not in by_account:
            raise RuntimeError(f"Required onboarding account is absent: {required}")
    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        register_source_discoveries(
            connection,
            tenant_id=tenant_id,
            discoveries=discoveries,
            source_export_sha256=source_export_sha256,
        )
        ensure_physical_subject(
            connection,
            tenant_id=tenant_id,
            discovery=by_account[active_account_id],
            lifecycle_status="active",
            history_start_date=start_dates[active_account_id],
        )
        ensure_feature_states(
            connection,
            tenant_id=tenant_id,
            subject_id=active_account_id,
            active=True,
        )
        ensure_physical_subject(
            connection,
            tenant_id=tenant_id,
            discovery=by_account[wave0_account_id],
            lifecycle_status="onboarding",
            history_start_date=start_dates[wave0_account_id],
        )
        ensure_feature_states(
            connection,
            tenant_id=tenant_id,
            subject_id=wave0_account_id,
            active=False,
        )
        ensure_wave0_case(
            connection,
            tenant_id=tenant_id,
            discovery=by_account[wave0_account_id],
            start_date=start_dates[wave0_account_id],
        )
        connection.commit()


def _registry_account_targets(database_url: str, tenant_id: str) -> list[dict[str, str]]:
    """Load website-approved v1.1 imports from the operating registry."""

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        rows = connection.execute(
            """
            SELECT r.subject_id, r.configuration_json
            FROM subject_operating_registrations r
            JOIN platform_subjects s
              ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
            WHERE r.tenant_id = %s AND r.capability = 'daily_ingestion'
              AND r.operating_state IN ('preview', 'active')
              AND r.effective_to IS NULL
              AND s.subject_type = 'physical_account'
              AND r.configuration_json ->> 'source' = 'existing_v11'
            ORDER BY r.subject_id
            """,
            (tenant_id,),
        ).fetchall()
    return [
        {
            "account_id": str(row["subject_id"]),
            "start_date": str(row["configuration_json"]["start_date"]),
        }
        for row in rows
    ]


def _mark_registered_import_reconciled(
    *, database_url: str, tenant_id: str, account_id: str, reconciliation: dict
) -> None:
    import psycopg

    with psycopg.connect(database_url) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        connection.execute(
            """
            UPDATE account_onboarding_checks checks
            SET status = 'pass',
                message = 'The registry-driven v1.1 import passed row-count and coverage reconciliation.',
                evidence_json = %s::jsonb, checked_at = now()
            FROM account_onboarding_cases cases
            WHERE checks.tenant_id = cases.tenant_id
              AND checks.case_id = cases.case_id
              AND checks.check_id = 'data_import_reconciled'
              AND cases.tenant_id = %s
              AND cases.source_system = 'existing_v11'
              AND cases.target_subject_id = %s
              AND cases.status NOT IN ('cancelled')
            """,
            (json.dumps(reconciliation, sort_keys=True, default=str), tenant_id, account_id),
        )
        connection.commit()


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Daily account sync is restricted to internal production")
    database_url = _required("DATABASE_URL")
    source_account_url = _required("PRODUCTION_STORAGE_ACCOUNT_URL")
    destination_account_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", DEFAULT_TENANT_ID)
    active_account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571").strip().upper()
    fallback_start_date = os.environ.get("PILOT_START_DATE", "2025-01-20")
    wave0_account_id = os.environ.get("ONBOARDING_WAVE0_ACCOUNT_ID", "U16088606").strip().upper()
    execution_mode = os.environ.get("ACCOUNT_SYNC_EXECUTION_MODE", "scheduled").strip().lower()
    allow_recent_previous_hkt_date = (
        os.environ.get(
            "MANUAL_ONBOARDING_ALLOW_RECENT_PREVIOUS_HKT_DATE", "false"
        ).strip().lower()
        == "true"
    )
    if execution_mode not in {"scheduled", "manual_onboarding"}:
        raise RuntimeError("Unsupported ACCOUNT_SYNC_EXECUTION_MODE")
    if allow_recent_previous_hkt_date and execution_mode != "manual_onboarding":
        raise RuntimeError(
            "The previous-HKT-date allowance is restricted to manual onboarding"
        )
    configured_targets = json.loads(os.environ.get("ACCOUNT_SYNC_TARGETS_JSON", "[]") or "[]")
    registry_targets = _registry_account_targets(database_url, tenant_id)
    merged_targets = {
        str(item["account_id"]).strip().upper(): item
        for item in [*configured_targets, *registry_targets]
    }
    targets = parse_account_sync_targets(
        json.dumps(list(merged_targets.values()), sort_keys=True),
        fallback_account_id=active_account_id,
        fallback_start_date=fallback_start_date,
    )
    start_dates = {target.account_id: target.start_date for target in targets}
    if active_account_id not in start_dates or wave0_account_id not in start_dates:
        raise RuntimeError("The active and Wave 0 accounts must both be sync targets")
    source_container = os.environ.get("PRODUCTION_EXPORT_CONTAINER", "analytics-reports")
    source_prefix = os.environ.get(
        "PRODUCTION_EXPORT_PREFIX",
        f"{tenant_id}/latest",
    ).strip("/")
    manifest_name = f"{source_prefix}/IBKR_Analyst_Export_Manifest_Latest.json"

    source_service = _service(source_account_url)
    destination_service = _service(destination_account_url)
    if source_service is None or destination_service is None:
        raise RuntimeError("Azure Blob Storage configuration is unavailable")

    with TemporaryDirectory(prefix="ibkr-account-sync-") as directory:
        temporary = Path(directory)
        manifest_path = temporary / "manifest.json"
        _download(source_service, source_container, manifest_name, manifest_path)
        manifest = validate_production_export_manifest(
            manifest_path.read_bytes(),
            expected_tenant_id=tenant_id,
            require_current_hkt_date=not allow_recent_previous_hkt_date,
        )
        source_blob_name = f"{source_prefix}/{manifest.sqlite_file}"
        source_path = temporary / "production.sqlite"
        _download(source_service, source_container, source_blob_name, source_path)
        if file_sha256(source_path) != manifest.sqlite_sha256:
            raise RuntimeError("Production SQLite SHA-256 does not match its manifest")

        discoveries = discover_v11_accounts(source_path)
        _register_onboarding_foundation(
            database_url=database_url,
            tenant_id=tenant_id,
            discoveries=discoveries,
            source_export_sha256=manifest.sqlite_sha256,
            active_account_id=active_account_id,
            wave0_account_id=wave0_account_id,
            start_dates=start_dates,
        )
        generated_date = manifest.generated_at.date().isoformat()
        destination_container = os.environ.get(
            "PILOT_SNAPSHOT_CONTAINER", "input-snapshots"
        )
        results: list[dict] = []
        for target in targets:
            account_id = target.account_id
            pilot_path = temporary / f"{account_id}.sqlite"
            build_manifest = build_pilot_snapshot(
                source_path,
                pilot_path,
                account_id=account_id,
                start_date=target.start_date,
                # Bind the filtered artifact to the production export, rather than
                # the time this job happened to run, so reruns are byte-idempotent.
                generated_at=manifest.generated_at.isoformat(),
            )
            validation = validate_pilot_snapshot(
                pilot_path, account_id=account_id, start_date=target.start_date
            )
            filtered_digest = validation["sha256"]
            destination_blob_name = (
                f"daily/{account_id}/{generated_date}/{filtered_digest}.sqlite"
            )
            destination = destination_service.get_blob_client(
                container=destination_container, blob=destination_blob_name
            )
            metadata = {
                "account_id": account_id,
                "source_sha256": manifest.sqlite_sha256,
                "filtered_sha256": filtered_digest,
                "source_generated_date": generated_date,
                "client_distribution": "prohibited",
            }
            if destination.exists():
                existing = destination.download_blob().readall()
                import hashlib

                if hashlib.sha256(existing).hexdigest() != filtered_digest:
                    raise RuntimeError("Existing filtered snapshot has a different SHA-256")
                from azure.storage.blob import ContentSettings

                destination.set_http_headers(
                    content_settings=ContentSettings(
                        content_type=content_type_for_path(pilot_path)
                    )
                )
                destination.set_blob_metadata(metadata)
            else:
                from azure.storage.blob import ContentSettings

                destination.upload_blob(
                    pilot_path.read_bytes(),
                    overwrite=False,
                    metadata=metadata,
                    content_settings=ContentSettings(
                        content_type=content_type_for_path(pilot_path)
                    ),
                )

            result = import_snapshot(
                database_url=database_url,
                local_path=pilot_path,
                account_id=account_id,
                start_date=target.start_date,
                tenant_id=tenant_id,
                source_reference=(
                    f"{source_container}/{source_blob_name}#sha256={manifest.sqlite_sha256}"
                ),
            )
            if account_id == wave0_account_id:
                import psycopg

                with psycopg.connect(database_url) as connection:
                    connection.execute(
                        "SELECT set_config('app.tenant_id', %s, false)", (tenant_id,)
                    )
                    mark_import_reconciled(
                        connection,
                        tenant_id=tenant_id,
                        account_id=account_id,
                        reconciliation=result,
                    )
                    connection.commit()
            _mark_registered_import_reconciled(
                database_url=database_url,
                tenant_id=tenant_id,
                account_id=account_id,
                reconciliation=result,
            )
            results.append(
                {
                    "account_id": account_id,
                    "filtered_snapshot": (
                        f"{destination_container}/{destination_blob_name}"
                    ),
                    "filtered_sha256": filtered_digest,
                    "build": build_manifest,
                    "import": result,
                }
            )
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "execution_mode": execution_mode,
                    "current_hkt_date_required": not allow_recent_previous_hkt_date,
                    "source_generated_at": manifest.generated_at.isoformat(),
                    "source_sha256": manifest.sqlite_sha256,
                    "account_count": len(results),
                    "accounts": results,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
