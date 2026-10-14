from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from pypdf import PdfReader
from azure.core.exceptions import ResourceExistsError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.cloud_storage import download_blob_bytes, upload_bytes  # noqa: E402
from ibkr_etl.db import connect  # noqa: E402
from ibkr_etl.historical_reporting import queue_report_generation_run  # noqa: E402
from ibkr_etl.month_end_agent import (  # noqa: E402
    MonthEndAgentConfig,
    generate_month_end_commentary,
)
from ibkr_etl.monthly_artifacts import (  # noqa: E402
    ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256,
    ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION,
    factsheet_layout_version,
    render_factsheet,
)
from ibkr_etl.presentation_design import (  # noqa: E402
    presentation_content_contract_version,
    presentation_content_requirements,
)
from ibkr_etl.reporting_snapshot import (  # noqa: E402
    active_month_end_snapshot_id,
    freeze_month_end_snapshot,
)
from ibkr_etl.template_upload import (  # noqa: E402
    normalize_strategy_identity,
    presentation_identity_scope,
)
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402
from ibkr_etl.report_design_updates import active_design, qualified_design, decoded  # noqa: E402
from ms_review_upload import upload_ms_review  # noqa: E402
from validate_subject_presentation import validate_subject_presentation  # noqa: E402


HONG_KONG = ZoneInfo("Asia/Hong_Kong")


def previous_calendar_month_end(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _require_factsheet_identity(
    path: Path, strategy_name: str, *, subject_id: str = ""
) -> None:
    """Fail closed when an uploaded account template carries another identity."""
    expected = normalize_strategy_identity(strategy_name.split(":", 1)[-1])
    if not expected:
        raise RuntimeError("The approved factsheet identity is missing")
    document = PdfReader(str(path))
    pages = document.pages
    visible = normalize_strategy_identity(
        " ".join(page.extract_text() or "" for page in pages)
    )
    if expected not in visible:
        # MS's owner-uploaded authority uses this product title, not the
        # presentation's strategy title. Never extend this alias to other
        # accounts or to a legacy/unbound PDF. Rendering already validates
        # the authority bytes and chart geometry before this identity check.
        metadata = document.metadata or {}
        if (
            subject_id == "alpha-world-ms"
            and expected == normalize_strategy_identity("Alpha World MS")
            and normalize_strategy_identity("Alpha World Enhanced Index") in visible
            and str(metadata.get("/Title", "")).startswith("alpha-world-ms Factsheet - ")
            and metadata.get("/LayoutAuthoritySHA256") == ALPHA_WORLD_MS_FACTSHEET_AUTHORITY_SHA256
            and metadata.get("/ChartStyleVersion") == ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION
            and metadata.get("/LayoutFamily") == "alpha-world-owner-template"
        ):
            return
        raise RuntimeError(
            "Rendered factsheet does not show the owner-approved account identity"
        )


def _completed_after_month_end(value: object, period_end: date) -> bool:
    if not value:
        return False
    completed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return completed.astimezone(HONG_KONG).date() > period_end


def _require_final_source_import(connection, account_id: str, period_end: date) -> None:
    """Prevent freezing a prior-day NAV before the post-month Flex import succeeds."""
    subject = connection.execute(
        "SELECT subject_type FROM platform_subjects WHERE subject_id = ?",
        (account_id,),
    ).fetchone()
    if subject is None:
        raise RuntimeError("Month-end subject is unavailable")
    kind = str(subject["subject_type"])
    if kind == "physical_account":
        nav = connection.execute(
            """
            SELECT n.snapshot_date, i.status, i.completed_at
            FROM nav_snapshots n JOIN ingestion_runs i ON i.run_id = n.last_seen_run_id
            WHERE n.account_id = ? AND n.asset_class = 'TOTAL'
              AND n.snapshot_date <= ?
            ORDER BY n.snapshot_date DESC LIMIT 1
            """,
            (account_id, period_end.isoformat()),
        ).fetchone()
        positions = connection.execute(
            """
            SELECT p.snapshot_date, i.status, i.completed_at
            FROM position_snapshots p
            JOIN ingestion_runs i ON i.run_id = p.last_seen_run_id
            WHERE p.account_id = ? AND p.snapshot_date <= ?
            ORDER BY p.snapshot_date DESC, p.last_seen_run_id DESC LIMIT 1
            """,
            (account_id, period_end.isoformat()),
        ).fetchone()
        if not nav or not positions or str(nav["snapshot_date"]) != str(positions["snapshot_date"]):
            raise RuntimeError("Month-end NAV and positions are not aligned")
        for row in (nav, positions):
            if str(row["status"]) != "success" or not _completed_after_month_end(row["completed_at"], period_end):
                raise RuntimeError("Awaiting a successful post-month IBKR Flex import")
    else:
        nav = connection.execute(
            "SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE subject_id = ? AND snapshot_date <= ?",
            (account_id, period_end.isoformat()),
        ).fetchone()
        day = str(nav["day"]) if nav and nav["day"] else ""
        if not day:
            raise RuntimeError("Derived month-end NAV is unavailable")
        positions_table = (
            "synthetic_portfolio_components" if kind == "composite_portfolio"
            else "subject_position_series"
        )
        key = "portfolio_id" if kind == "composite_portfolio" else "subject_id"
        position = connection.execute(
            f"SELECT MAX(snapshot_date) AS day FROM {positions_table} WHERE {key} = ? AND snapshot_date <= ?",
            (account_id, period_end.isoformat()),
        ).fetchone()
        if not position or str(position["day"]) != day:
            raise RuntimeError("Derived month-end NAV and components are not aligned")
        run = connection.execute(
            """
            SELECT status, completed_at FROM subject_calculation_runs
            WHERE subject_id = ? AND requested_to >= ?
              AND status IN ('validated', 'published')
            ORDER BY completed_at DESC LIMIT 1
            """,
            (account_id, day),
        ).fetchone()
        if not run or not _completed_after_month_end(run["completed_at"], period_end):
            raise RuntimeError("Awaiting a validated post-month derived calculation")
    if (period_end - date.fromisoformat(str(nav["snapshot_date"] if kind == "physical_account" else day))).days > 7:
        raise RuntimeError("Month-end portfolio observation is stale")


def _existing_package(
    connection,
    *,
    tenant_id: str,
    account_id: str,
    period_end: str,
    expected_versions: dict[str, str],
    allow_replacement: bool = False,
    snapshot_id: str | None = None,
) -> str | None:
    if allow_replacement and not snapshot_id:
        raise RuntimeError('Replacement requires an exact approved snapshot')
    if not snapshot_id:
        # No active frozen input means an older/superseded package cannot
        # establish completion for this run. Main must freeze a valid input.
        return None
    rows = connection.execute(
        """
        SELECT r.report_type, r.status, r.template_version,
               r.output_reference, r.output_sha256
        FROM report_generation_runs r
        JOIN reporting_snapshots s
          ON s.tenant_id = r.tenant_id AND s.snapshot_id = r.snapshot_id
        WHERE r.tenant_id = ? AND s.subject_id = ? AND s.period_end = ?
          AND r.report_type IN ('factsheet', 'presentation')
          AND s.snapshot_id = CAST(? AS uuid)
        ORDER BY r.generation_run_id DESC
        """,
        (tenant_id, account_id, period_end, snapshot_id),
    ).fetchall()
    latest: dict[str, object] = {}
    for row in rows:
        latest.setdefault(str(row["report_type"]), row)
    released = {
        kind
        for kind, row in latest.items()
        if str(row["status"]) in {"released_internal", "published"}
    }
    if released and released != {"factsheet", "presentation"} and not allow_replacement:
        raise RuntimeError("Month-end package has an inconsistent partial release")
    if released == {"factsheet", "presentation"} and (
        not allow_replacement or all(str(latest[k]['template_version']) == expected_versions[k] for k in released)
    ):
        return "already_released"
    complete = all(
        kind in latest
        and str(latest[kind]["status"]) == "awaiting_approval"
        and str(latest[kind]["template_version"]) == expected_versions[kind]
        and bool(latest[kind]["output_reference"])
        and bool(latest[kind]["output_sha256"])
        for kind in ("factsheet", "presentation")
    )
    return "already_generated" if complete else None


def _pending_design_update(connection, *, tenant_id, account_id, period_end):
    row=connection.execute('''SELECT plan_json,plan_sha256 FROM report_design_update_requests
        WHERE tenant_id=? AND subject_id=? AND status IN ('queued','running','awaiting_review')''',
        (tenant_id,account_id)).fetchone()
    if not row:
        return False
    from ibkr_etl.report_design_updates import digest
    plan=decoded(row['plan_json'])
    if digest(plan)!=row['plan_sha256']:
        raise RuntimeError('Pending report design plan integrity failed')
    return any(item['period_end']==period_end for item in plan['snapshots'])


def _load_frozen_snapshot(
    connection, *, tenant_id: str, account_id: str, snapshot_id: str
) -> tuple[dict[str, object], str]:
    row = connection.execute(
        """
        SELECT snapshot_payload_json, data_sha256
        FROM reporting_snapshots
        WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ?
          AND status IN ('frozen', 'approved')
        """,
        (tenant_id, snapshot_id, account_id),
    ).fetchone()
    if row is None:
        raise RuntimeError("Frozen month-end snapshot is unavailable")
    payload_text = str(row["snapshot_payload_json"])
    digest = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
    if digest != str(row["data_sha256"]):
        raise RuntimeError("Frozen month-end snapshot integrity check failed")
    return json.loads(payload_text), digest


def _acquire_package_lock(connection, tenant_id, account_id, period_end):
    # A snapshot freeze may commit internally. A session lock remains held
    # through that commit and is released by the existing finally/close path.
    row = connection.execute(
        'SELECT pg_try_advisory_lock(hashtextextended(?,0)) AS acquired',
        (f'report-release:{tenant_id}:{account_id}:{period_end}',),
    ).fetchone()
    if not row or row['acquired'] is not True:
        raise RuntimeError('Another report operation is active for this account and month')


def _verify_existing_artifacts(connection, *, tenant_id, account_id, snapshot_id,
                               storage_url, container):
    rows = connection.execute('''SELECT r.report_type,r.output_reference,r.output_sha256
        FROM report_generation_runs r JOIN reporting_snapshots s
          ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
        WHERE r.tenant_id=? AND s.subject_id=? AND s.snapshot_id=CAST(? AS uuid)
          AND r.report_type IN ('factsheet','presentation')
        ORDER BY r.generation_run_id DESC''',(tenant_id,account_id,snapshot_id)).fetchall()
    latest={}
    for row in rows:latest.setdefault(str(row['report_type']),row)
    if set(latest)!={'factsheet','presentation'}:
        raise RuntimeError('Existing report package is incomplete')
    for row in latest.values():
        bucket, separator, name=str(row['output_reference'] or '').partition('/')
        digest=str(row['output_sha256'] or '')
        if (bucket!=container or not separator or not name or name.startswith('/')
                or '\\' in name or any(part in ('..','.') for part in name.split('/'))
                or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest)):
            raise RuntimeError('Existing report artifact reference or hash is invalid')
        content=download_blob_bytes(storage_url,container,name,maximum_bytes=64*1024*1024)
        if _sha256(content)!=digest:
            raise RuntimeError('Existing report artifact failed integrity verification')


def _upload_immutable_artifact(storage_url, container, name, content, content_type):
    try:
        uploaded=upload_bytes(storage_url,container,name,content,
                              overwrite=False,content_type=content_type)
    except ResourceExistsError:
        if download_blob_bytes(storage_url,container,name)!=content:
            raise RuntimeError('Existing immutable report differs')
        uploaded=True
    if not uploaded or download_blob_bytes(storage_url,container,name)!=content:
        raise RuntimeError('Immutable report upload/readback failed')
    return uploaded


def _factsheet_template_version(template_sha, design_sha, layout):
    # An identical PDF occupying both approved asset roles is one authority,
    # not a second supporting file. Preserve T's existing canonical version.
    if design_sha and design_sha != template_sha:
        return f'sha256:{design_sha}+supporting-sha256:{template_sha}+layout:{layout}'
    return f'sha256:{template_sha}+layout:{layout}'


def main() -> int:
    if os.environ.get("DEPLOYMENT_ENVIRONMENT", "").lower() != "internal-production":
        raise RuntimeError("Month-end package automation is restricted to internal production")
    database_url = _required("DATABASE_URL")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL")

    period_override = os.environ.get("REPORT_PERIOD_END", "").strip()
    period_end = (
        date.fromisoformat(period_override)
        if period_override
        else previous_calendar_month_end(datetime.now(HONG_KONG).date())
    )
    if period_end != previous_calendar_month_end(period_end + timedelta(days=1)):
        raise RuntimeError("REPORT_PERIOD_END must be a calendar month end")

    tenant_id = os.environ.get(
        "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
    )
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    reporting_start = os.environ.get("REPORTING_PERIOD_START", "").strip()
    storage_url = _required("AZURE_STORAGE_ACCOUNT_URL")
    container = os.environ.get("INTERNAL_REPORT_CONTAINER", "internal-reports")

    factsheet_blob = os.environ.get("FACTSHEET_TEMPLATE_BLOB", "").strip()
    factsheet_sha = os.environ.get("FACTSHEET_TEMPLATE_SHA256", "").strip().lower()
    factsheet_design_sha = ""
    factsheet_container = container
    presentation_path = Path(_required("PRESENTATION_TEMPLATE_PATH"))
    presentation_sha = _required("PRESENTATION_TEMPLATE_SHA256").lower()
    if not presentation_path.is_file():
        raise RuntimeError("Approved presentation template is not present in the image")
    presentation_content = presentation_path.read_bytes()
    if _sha256(presentation_content) != presentation_sha:
        raise RuntimeError("Presentation template SHA-256 does not match the approved version")

    agent_config = MonthEndAgentConfig(
        endpoint=_required("AZURE_OPENAI_ENDPOINT"),
        deployment=_required("AZURE_OPENAI_DEPLOYMENT"),
        region=_required("AI_RESOURCE_REGION"),
        processing_scope=_required("AI_PROCESSING_SCOPE"),
        managed_identity_client_id=_required("AZURE_CLIENT_ID"),
    )

    connection = connect(Path("/tmp/unused.sqlite3"), database_url, tenant_id)
    try:
        _acquire_package_lock(connection,tenant_id,account_id,period_end.isoformat())
        if (os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')=='true'
                and not os.environ.get('REPORT_DESIGN_VERSION')
                and _pending_design_update(connection,tenant_id=tenant_id,account_id=account_id,
                    period_end=period_end.isoformat())):
            print(canonical_json({'status':'design_update_in_progress','period_end':period_end.isoformat(),
                'existing_downloads_unchanged':True}))
            return 0
        subject = connection.execute(
            """
            SELECT reporting_currency, history_start_date, lifecycle_status, display_name
            FROM platform_subjects WHERE subject_id = ?
            """,
            (account_id,),
        ).fetchone()
        if subject is None or str(subject["lifecycle_status"]) != "active":
            raise RuntimeError("The month-end subject is not active")
        reporting_start = reporting_start or str(subject["history_start_date"])
        reporting_currency = str(subject["reporting_currency"])
        strategy_name = ""
        if account_id != "U16088571":
            identity = connection.execute(
                """
                SELECT scope_json, scope_sha256
                FROM account_onboarding_approvals
                WHERE subject_id = ? AND approval_type = 'presentation_identity'
                  AND approval_status = 'approved'
                """,
                (account_id,),
            ).fetchone()
            if identity is None:
                raise RuntimeError("The subject has no approved presentation identity")
            scope = identity["scope_json"]
            if isinstance(scope, str):
                scope = json.loads(scope)
            if _sha256(canonical_json(scope).encode("utf-8")) != str(identity["scope_sha256"]):
                raise RuntimeError("Approved presentation identity integrity check failed")
            expected_scope = presentation_identity_scope(
                subject_id=account_id,
                display_name=str(subject["display_name"]),
                layout_policy=str(scope["layout_policy"]),
            )
            if scope != expected_scope:
                raise RuntimeError("Presentation identity no longer matches the approved subject")
            strategy_name = str(scope["strategy_name"])
        if not factsheet_blob or not factsheet_sha:
            asset_rows = connection.execute(
                """
                SELECT asset_role, container_name, blob_name, sha256
                FROM subject_report_template_assets
                WHERE subject_id = ? AND report_type = 'factsheet'
                  AND asset_role IN ('template', 'completed_example') AND is_current
                  AND approval_status IN ('validated', 'active')
                ORDER BY updated_at DESC
                """,
                (account_id,),
            ).fetchall()
            assets: dict[str, object] = {}
            for asset in asset_rows:
                assets.setdefault(str(asset["asset_role"]), asset)
            if set(assets) != {"template", "completed_example"}:
                raise RuntimeError(
                    "The active subject requires both a validated factsheet template "
                    "and completed design example"
                )
            render_asset = assets["template"]
            design_asset = assets["completed_example"]
            factsheet_container = str(render_asset["container_name"])
            factsheet_blob = str(render_asset["blob_name"])
            factsheet_sha = str(render_asset["sha256"])
            factsheet_design_sha = str(design_asset["sha256"])
            factsheet_design_container = str(design_asset["container_name"])
            factsheet_design_blob = str(design_asset["blob_name"])
        else:
            factsheet_design_container = ""
            factsheet_design_blob = ""
        active_factsheet_layout = factsheet_layout_version(account_id)
        factsheet_version = _factsheet_template_version(
            factsheet_sha,factsheet_design_sha,active_factsheet_layout)
        presentation_layout = presentation_content_contract_version(account_id)
        template_versions = {
            "factsheet": factsheet_version,
            "presentation": f"sha256:{presentation_sha}+layout:{presentation_layout}",
        }
        design=None
        if os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')=='true':
            image=os.environ.get('REPORT_RENDERER_IMAGE_SHA256','')
            requested_design=os.environ.get('REPORT_DESIGN_VERSION','')
            if requested_design:
                design=connection.execute('''SELECT * FROM qualified_report_designs
                    WHERE tenant_id=? AND subject_id=? AND design_version=?''',
                    (tenant_id,account_id,requested_design)).fetchone()
                qualified_design(design,installed_image_sha256=image)
            else:
                design=active_design(connection,tenant_id,account_id,installed_image_sha256=image)
            if design:
                if decoded(design['template_versions_json'])!=template_versions:
                    raise RuntimeError('Installed templates differ from the qualified design')
                template_versions={k:v+'+design:'+design['design_version'] for k,v in template_versions.items()}
        elif os.environ.get('REPORT_DESIGN_VERSION'):
            raise RuntimeError('Report design regeneration is not enabled')
        bound_id=os.environ.get('REPORT_BOUND_SNAPSHOT_ID','')
        bound_sha=os.environ.get('REPORT_BOUND_SNAPSHOT_SHA256','')
        if bound_id or bound_sha:
            if not design or not bound_id or not bound_sha:
                raise RuntimeError('Historical regeneration requires a qualified design and exact frozen input')
            current_id=active_month_end_snapshot_id(connection,tenant_id=tenant_id,
                subject_id=account_id,period_end=period_end.isoformat())
            if str(current_id)!=bound_id:raise RuntimeError('Current frozen snapshot changed after owner preview')
            bound_payload,verified_sha=_load_frozen_snapshot(connection,tenant_id=tenant_id,account_id=account_id,snapshot_id=bound_id)
            if verified_sha!=bound_sha or bound_payload.get('period_end')!=period_end.isoformat():
                raise RuntimeError('Frozen historical input no longer matches the approved plan')
        snapshot_id = bound_id or active_month_end_snapshot_id(
            connection, tenant_id=tenant_id, subject_id=account_id,
            period_end=period_end.isoformat(),
        )
        package_status = _existing_package(
            connection,
            tenant_id=tenant_id,
            account_id=account_id,
            period_end=period_end.isoformat(),
            expected_versions=template_versions,
            allow_replacement=bool(design),
            snapshot_id=snapshot_id,
        )
        if package_status:
            _verify_existing_artifacts(connection,tenant_id=tenant_id,account_id=account_id,
                snapshot_id=snapshot_id,storage_url=storage_url,container=container)
            print(canonical_json({"status": package_status, "period_end": period_end.isoformat()}))
            return 0

        factsheet_content = download_blob_bytes(
            storage_url, factsheet_container, factsheet_blob, maximum_bytes=10 * 1024 * 1024
        )
        if _sha256(factsheet_content) != factsheet_sha:
            raise RuntimeError("Factsheet template SHA-256 does not match the approved version")
        factsheet_design_content = None
        if factsheet_design_sha:
            if not factsheet_design_container or not factsheet_design_blob:
                raise RuntimeError("Factsheet completed design model location is missing")
            factsheet_design_content = download_blob_bytes(
                storage_url,
                factsheet_design_container,
                factsheet_design_blob,
                maximum_bytes=10 * 1024 * 1024,
            )
            if _sha256(factsheet_design_content) != factsheet_design_sha:
                raise RuntimeError(
                    "Factsheet completed design model SHA-256 does not match the approved version"
                )

        snapshot_action = "reused_existing"
        if bound_id and str(snapshot_id)!=bound_id:
            raise RuntimeError('Current historical snapshot changed during regeneration')
        if snapshot_id is None:
            if bound_id:raise RuntimeError('Historical regeneration cannot create a new snapshot')
            _require_final_source_import(connection, account_id, period_end)
            snapshot = freeze_month_end_snapshot(
                connection,
                tenant_id=tenant_id,
                subject_id=account_id,
                period_start=reporting_start,
                period_end=period_end.isoformat(),
                reporting_currency=reporting_currency,
                reject_quality_fail=True,
            )
            snapshot_id = snapshot.snapshot_id
            snapshot_action = "created_new"
        payload, snapshot_sha = _load_frozen_snapshot(
            connection,
            tenant_id=tenant_id,
            account_id=account_id,
            snapshot_id=snapshot_id,
        )
        if bound_id and snapshot_sha!=bound_sha:
            raise RuntimeError('Bound historical input changed during regeneration')
        if payload.get("quality_status") == "fail" or not (
            payload.get("monthly_reporting") or {}
        ).get("available"):
            raise RuntimeError("Frozen month-end snapshot failed data-quality checks")

        agent_result = generate_month_end_commentary(payload, agent_config)
        commentary = agent_result["commentary"]
        with TemporaryDirectory(prefix="month-end-package-") as directory:
            root = Path(directory)
            factsheet_template = root / "approved-factsheet-template.pdf"
            factsheet_design_model = root / "approved-completed-factsheet-model.pdf"
            snapshot_file = root / "snapshot.json"
            commentary_file = root / "commentary.json"
            factsheet_output = root / f"{account_id}_Factsheet_{period_end.isoformat()}.pdf"
            presentation_output = root / (
                f"{account_id}_Monthly_Presentation_{period_end.isoformat()}.pptx"
            )
            factsheet_template.write_bytes(factsheet_content)
            if factsheet_design_content is not None:
                factsheet_design_model.write_bytes(factsheet_design_content)
            snapshot_file.write_text(canonical_json(payload), encoding="utf-8")
            commentary_file.write_text(canonical_json(commentary), encoding="utf-8")

            factsheet_result = render_factsheet(
                payload,
                template_path=factsheet_template,
                design_model_path=(
                    factsheet_design_model
                    if factsheet_design_content is not None
                    else None
                ),
                output_path=factsheet_output,
            )
            if factsheet_design_sha and factsheet_result.get(
                "completed_example_sha256"
            ) != factsheet_design_sha:
                raise RuntimeError(
                    "Generated factsheet is not bound to the approved completed example"
                )
            if strategy_name:
                _require_factsheet_identity(
                    factsheet_output, strategy_name, subject_id=account_id
                )
            presentation_environment = dict(os.environ)
            if strategy_name:
                presentation_environment["PRESENTATION_STRATEGY_NAME"] = strategy_name
            subprocess.run(
                [
                    os.environ.get("NODE_EXECUTABLE", "node"),
                    str(PROJECT_ROOT / "scripts" / ("render_alpha_t_presentation.mjs" if payload.get('subject_id') == 'alpha-world-t' else "render_monthly_presentation.mjs")),
                    str(presentation_path),
                    str(snapshot_file),
                    str(presentation_output),
                    str(commentary_file),
                ],
                check=True,
                cwd=PROJECT_ROOT,
                env=presentation_environment,
            )
            presentation_validation = validate_subject_presentation(
                presentation_output, snapshot_file,
                subject_id=account_id, period_end=period_end.isoformat(),
                expected_strategy=strategy_name or None,
            )
            if (presentation_validation["snapshot_sha256"] != snapshot_sha
                    or presentation_validation["content_contract_version"] != presentation_layout):
                raise RuntimeError("Presentation validation differs from the frozen input/content contract")

            artifacts = {
                "factsheet": {
                    "path": factsheet_output,
                    "content_type": "application/pdf",
                    "template_id": f"{account_id}-monthly-factsheet",
                },
                "presentation": {
                    "path": presentation_output,
                    "content_type": (
                        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
                    ),
                    "template_id": f"{account_id}-monthly-presentation",
                },
            }
            for kind, artifact in artifacts.items():
                path = artifact["path"]
                content = path.read_bytes()
                artifact["sha256"] = _sha256(content)
                if kind == "presentation" and artifact["sha256"] != presentation_validation["output_sha256"]:
                    raise RuntimeError("Presentation changed after output validation")
                artifact["blob_name"] = (
                    f"review/{account_id}/{period_end.isoformat()}/{artifact['sha256']}-{path.name}"
                )
                if account_id == "alpha-world-ms" and not design:
                    uploaded = upload_ms_review(storage_url, container, artifact["blob_name"],
                        content, content_type=artifact["content_type"])
                else:
                    uploaded = _upload_immutable_artifact(storage_url,container,
                        artifact['blob_name'],content,artifact['content_type'])
                if not uploaded:
                    raise RuntimeError(f"Private {kind} storage is unavailable")

        now = utc_now_iso()
        run_ids: dict[str, int] = {}
        for kind, artifact in artifacts.items():
            run_id = queue_report_generation_run(
                connection,
                tenant_id=tenant_id,
                snapshot_id=snapshot_id,
                report_type=kind,
                template_id=str(artifact["template_id"]),
                template_version=template_versions[kind],
            )
            run_ids[kind] = run_id
            output_reference = f"{container}/{artifact['blob_name']}"
            connection.execute(
                """
                UPDATE report_generation_runs
                SET status = 'awaiting_approval', output_reference = ?,
                    output_sha256 = ?, completed_at = ?, error_message = NULL
                WHERE tenant_id = ? AND generation_run_id = ?
                  AND status NOT IN ('released_internal', 'published')
                """,
                (
                    output_reference,
                    artifact["sha256"],
                    now,
                    tenant_id,
                    run_id,
                ),
            )

        details = canonical_json(
            {
                "period_end": period_end.isoformat(),
                "snapshot_id": snapshot_id,
                "snapshot_sha256": snapshot_sha,
                "snapshot_action": snapshot_action,
                "generation_run_ids": run_ids,
                "design_version": design['design_version'] if design else None,
                "factsheet_template_sha256": factsheet_sha,
                "factsheet_design_model_sha256": factsheet_design_sha or factsheet_sha,
                "presentation_template_sha256": presentation_sha,
                "factsheet_layout_version": active_factsheet_layout,
                "presentation_layout_version": presentation_layout,
                "presentation_content_requirements": list(
                    presentation_content_requirements(account_id)
                ),
                "presentation_validation": presentation_validation,
                "model_role": agent_result["model_role"],
                "model_deployment": agent_result["model_deployment"],
                "model_request_sha256": agent_result["request_sha256"],
                "model_response_sha256": agent_result["response_sha256"],
                "model_usage": agent_result["usage"],
                "layout_authority": "deterministic-renderers",
                "publication_state": "awaiting_owner_approval",
            }
        )
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (?, ?, 'system', 'gpt-5.6-sol-portfolio-analyst',
                      'month_end_report_package_generated', ?,
                      'awaiting_owner_approval', ?)
            """,
            (tenant_id, now, account_id, details),
        )
        connection.commit()
        print(
            canonical_json(
                {
                    "status": "generated",
                    "period_end": period_end.isoformat(),
                    "snapshot_id": snapshot_id,
                    "snapshot_action": snapshot_action,
                    "generation_run_ids": run_ids,
                    "factsheet_layout_version": factsheet_result["layout_version"],
                    "presentation_layout_version": presentation_layout,
                    "website_download_state": "ready_for_review",
                }
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
