from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from psycopg.rows import dict_row


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from ibkr_etl.admin_actions import (  # noqa: E402
    AdminActionConfig,
    AdminActionError,
    _default_job_starter,
)
from ibkr_etl.admin_chat import SYSTEM_UPDATES_SOURCE_KEY  # noqa: E402
from ibkr_etl.ai_analyst import AIAnalystConfig, AIAnalystError  # noqa: E402
from ibkr_etl import layer_freshness  # noqa: E402
from ibkr_etl.autonomous_admin import (  # noqa: E402
    AutonomyControl,
    ROLLBACK_ACTION,
    RecoveryContext,
    authority_review_current,
    diagnose_findings,
    evaluate_supervisor_state,
    layer_freshness_findings,
    plan_safe_repairs,
)
from ibkr_etl.burn_in import now_hkt_date, portal_boundary_check  # noqa: E402
from ibkr_etl.ops_agent_posts import post_connection_reports, post_new_diagnoses  # noqa: E402
from ibkr_etl.util import canonical_json, utc_now_iso  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _portal_failure(url: str, tenant_id: str) -> str | None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "WealthRyse-Controlled-Autonomy/1.0"}
    )
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=30) as response:
            status = int(response.status)
            location = response.headers.get("Location")
    except urllib.error.HTTPError as error:
        status = int(error.code)
        location = error.headers.get("Location")
    except (TimeoutError, urllib.error.URLError, OSError):
        return "Portal did not respond within the controlled availability-check window"
    _, failure = portal_boundary_check(status, location, tenant_id=tenant_id)
    return failure


def _arm_token(identity_client_id: str) -> str:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential(
        managed_identity_client_id=identity_client_id
    ).get_token("https://management.azure.com/.default").token


def _arm_url(subscription_id: str, resource_group: str, app_name: str) -> str:
    return (
        "https://management.azure.com/subscriptions/"
        f"{quote(subscription_id, safe='')}/resourceGroups/{quote(resource_group, safe='')}"
        f"/providers/Microsoft.App/containerApps/{quote(app_name, safe='')}"
    )


def _active_web_revision(
    *, subscription_id: str, resource_group: str, app_name: str, token: str
) -> str | None:
    request = urllib.request.Request(
        f"{_arm_url(subscription_id, resource_group, app_name)}/revisions?api-version=2024-03-01",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read() or b"{}")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise RuntimeError("Azure revision inventory was unavailable") from error
    active = [
        str(item.get("name"))
        for item in payload.get("value", [])
        if item.get("properties", {}).get("active") is True and item.get("name")
    ]
    return active[0] if len(active) == 1 else None


def _activate_web_revision(
    *,
    subscription_id: str,
    resource_group: str,
    app_name: str,
    revision_name: str,
    token: str,
) -> str:
    url = (
        f"{_arm_url(subscription_id, resource_group, app_name)}/revisions/"
        f"{quote(revision_name, safe='')}/activate?api-version=2024-03-01"
    )
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response.read()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Azure rejected exact revision activation with HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError("Azure did not confirm exact revision activation") from error
    return f"revision-activated:{app_name}:{revision_name}"


def _database_state(connection, account_id: str) -> dict:
    dates = connection.execute(
        """
        SELECT
          (SELECT MAX(snapshot_date) FROM nav_snapshots WHERE account_id = %s) AS latest_nav_date,
          (SELECT MAX(observation_date) FROM benchmark_prices
            WHERE benchmark_id = 'GSPC' AND quality_status <> 'fail') AS latest_gspc_date,
          (SELECT MAX(observation_date) FROM benchmark_prices
            WHERE benchmark_id = 'IXIC' AND quality_status <> 'fail') AS latest_ixic_date,
          (SELECT MAX(rate_date) FROM risk_free_rates
            WHERE currency = 'USD' AND quality_status <> 'fail') AS latest_risk_free_date
        """,
        (account_id,),
    ).fetchone()
    latest_import = connection.execute(
        """
        SELECT run_id, status, requested_to, completed_at
        FROM ingestion_runs
        WHERE run_type = 'staging-snapshot-import'
        ORDER BY run_id DESC LIMIT 1
        """
    ).fetchone()
    latest_snapshot = connection.execute(
        """
        SELECT snapshot_id, period_end, data_as_of, status, quality_status
        FROM reporting_snapshots
        WHERE subject_id = %s AND status IN ('frozen', 'approved')
        ORDER BY period_end DESC, created_at DESC LIMIT 1
        """,
        (account_id,),
    ).fetchone()
    ready_report_types: list[str] = []
    if latest_snapshot:
        ready_report_types = [
            str(row["report_type"])
            for row in connection.execute(
                """
                SELECT DISTINCT report_type
                FROM report_generation_runs
                WHERE snapshot_id = %s
                  AND report_type IN ('factsheet', 'presentation')
                  AND status IN ('awaiting_approval', 'released_internal', 'published')
                  AND output_reference IS NOT NULL AND output_sha256 IS NOT NULL
                ORDER BY report_type
                """,
                (latest_snapshot["snapshot_id"],),
            ).fetchall()
        ]
    client_approvals = connection.execute(
        """
        SELECT COUNT(*) AS n FROM report_release_decisions
        WHERE release_scope = 'client' AND status = 'approved'
        """
    ).fetchone()
    return {
        **dict(dates),
        "latest_import": None if latest_import is None else dict(latest_import),
        "latest_snapshot": None if latest_snapshot is None else dict(latest_snapshot),
        "ready_report_types": ready_report_types,
        "client_approval_count": int(client_approvals["n"]),
    }


def _control(connection) -> AutonomyControl:
    row = connection.execute(
        """
        SELECT enabled, kill_switch, max_actions_per_run, cooldown_minutes, policy_version,
               production_active, automatic_rollback_enabled, rollback_failure_threshold,
               monthly_review_due_at
        FROM autonomy_control WHERE tenant_id = app_tenant_id()
        """
    ).fetchone()
    if row is None:
        return AutonomyControl(False, True, 0)
    return AutonomyControl(
        bool(row["enabled"]),
        bool(row["kill_switch"]),
        int(row["max_actions_per_run"]),
        int(row["cooldown_minutes"]),
        str(row["policy_version"]),
        bool(row["production_active"]),
        bool(row["automatic_rollback_enabled"]),
        int(row["rollback_failure_threshold"]),
        row["monthly_review_due_at"],
    )


def _recovery_context(
    connection,
    *,
    portal_failure: str | None,
    control: AutonomyControl,
    app_name: str,
    identity_client_id: str,
    subscription_id: str,
    resource_group: str,
) -> RecoveryContext:
    now_iso = utc_now_iso()
    row = connection.execute(
        """
        INSERT INTO autonomy_recovery_state(
            app_name, consecutive_portal_failures, last_failure_at, last_success_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (tenant_id, app_name) DO UPDATE SET
            consecutive_portal_failures = CASE
                WHEN %s THEN autonomy_recovery_state.consecutive_portal_failures + 1 ELSE 0 END,
            last_failure_at = CASE WHEN %s THEN excluded.last_failure_at
                                   ELSE autonomy_recovery_state.last_failure_at END,
            last_success_at = CASE WHEN %s THEN autonomy_recovery_state.last_success_at
                                   ELSE excluded.last_success_at END,
            updated_at = excluded.updated_at
        RETURNING consecutive_portal_failures
        """,
        (
            app_name,
            1 if portal_failure else 0,
            now_iso if portal_failure else None,
            None if portal_failure else now_iso,
            now_iso,
            bool(portal_failure),
            bool(portal_failure),
            bool(portal_failure),
        ),
    ).fetchone()
    failure_count = int(row["consecutive_portal_failures"])
    baseline = connection.execute(
        """
        SELECT revision_name FROM autonomy_release_baselines
        WHERE app_name = %s AND status = 'last_known_good'
        """,
        (app_name,),
    ).fetchone()
    active_revision = None
    if (
        portal_failure
        and control.automatic_rollback_enabled
        and failure_count >= control.rollback_failure_threshold
        and baseline is not None
    ):
        active_revision = _active_web_revision(
            subscription_id=subscription_id,
            resource_group=resource_group,
            app_name=app_name,
            token=_arm_token(identity_client_id),
        )
        connection.execute(
            """
            UPDATE autonomy_recovery_state SET latest_checked_revision = %s, updated_at = %s
            WHERE app_name = %s
            """,
            (active_revision, now_iso, app_name),
        )
    return RecoveryContext(
        app_name=app_name,
        consecutive_portal_failures=failure_count,
        active_revision=active_revision,
        last_known_good_revision=None if baseline is None else str(baseline["revision_name"]),
    )


def _layer_freshness_findings(database_url: str, tenant_id: str) -> list:
    """Late medallion layers as findings, off unless LAYER_FRESHNESS_ENABLED. Read-only
    on its own connection; any problem is printed and skipped, never failing the run."""
    if not layer_freshness.enabled():
        return []
    try:
        results = layer_freshness.run_read_only(
            os.environ.get("LAYER_FRESHNESS_DATABASE_URL") or database_url, tenant_id
        )
        print("LAYER_FRESHNESS " + canonical_json(layer_freshness.summary(results)), flush=True)
        return layer_freshness_findings(results)
    except Exception as error:  # noqa: BLE001
        print("LAYER_FRESHNESS " + canonical_json({"status": "failed", "error_code": type(error).__name__}),
              flush=True)
        return []


def _sync_incidents(connection, findings, now_iso: str) -> tuple[dict[str, int], set[str]]:
    existing = {
        str(row["incident_key"]): int(row["incident_id"])
        for row in connection.execute(
            "SELECT incident_id, incident_key FROM system_incidents WHERE status IN ('open', 'acknowledged')"
        ).fetchall()
    }
    active: dict[str, int] = {}
    for finding in findings:
        row = connection.execute(
            """
            INSERT INTO system_incidents(
                incident_key, check_id, severity, status, description,
                evidence_json, first_seen_at, last_seen_at, occurrence_count
            ) VALUES (%s, %s, %s, 'open', %s, %s, %s, %s, 1)
            ON CONFLICT (tenant_id, incident_key) DO UPDATE SET
                check_id = excluded.check_id,
                severity = excluded.severity,
                status = 'open',
                description = excluded.description,
                evidence_json = excluded.evidence_json,
                last_seen_at = excluded.last_seen_at,
                resolved_at = NULL,
                occurrence_count = system_incidents.occurrence_count + 1
            RETURNING incident_id
            """,
            (
                finding.incident_key,
                finding.check_id,
                finding.severity,
                finding.description,
                canonical_json(dict(finding.evidence)),
                now_iso,
                now_iso,
            ),
        ).fetchone()
        active[finding.incident_key] = int(row["incident_id"])
    if active:
        connection.execute(
            """
            UPDATE system_incidents SET status = 'resolved', resolved_at = %s
            WHERE status IN ('open', 'acknowledged') AND NOT (incident_key = ANY(%s))
            """,
            (now_iso, list(active)),
        )
    else:
        connection.execute(
            """
            UPDATE system_incidents SET status = 'resolved', resolved_at = %s
            WHERE status IN ('open', 'acknowledged')
            """,
            (now_iso,),
        )
    return active, set(active) - set(existing)


def _recent_actions(connection) -> dict[str, datetime | None]:
    return {
        str(row["action_type"]): row["last_action_at"]
        for row in connection.execute(
            """
            SELECT action_type, MAX(COALESCE(executed_at, created_at)) AS last_action_at
            FROM autonomous_actions
            WHERE status IN ('executing', 'executed')
            GROUP BY action_type
            """
        ).fetchall()
    }


def _publish_admin_chat_updates(
    connection,
    *,
    findings,
    new_keys: set[str],
    incident_ids: dict[str, int],
    diagnosis: dict | None,
    correlation_id: str,
) -> None:
    """Create the administrator-owned updates thread and announce new incidents once."""
    conversation_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"wealthryse:{SYSTEM_UPDATES_SOURCE_KEY}")
    )
    created = connection.execute(
        """
        INSERT INTO admin_chat_conversations(
            conversation_id, title, initiated_by, source_key, status
        ) VALUES (%s, 'System administrator updates', 'administrator', %s, 'active')
        ON CONFLICT (tenant_id, source_key) WHERE source_key IS NOT NULL DO NOTHING
        RETURNING conversation_id
        """,
        (conversation_id, SYSTEM_UPDATES_SOURCE_KEY),
    ).fetchone()
    row = connection.execute(
        """
        SELECT conversation_id FROM admin_chat_conversations
        WHERE source_key = %s
        """,
        (SYSTEM_UPDATES_SOURCE_KEY,),
    ).fetchone()
    if row is None:
        raise RuntimeError("System-administrator updates conversation was not available")
    conversation_id = str(row["conversation_id"])

    def append(content: str, kind: str, response: dict, incident_id: int | None = None) -> None:
        next_sequence = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence
            FROM admin_chat_messages WHERE conversation_id = %s
            """,
            (conversation_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO admin_chat_messages(
                message_id, conversation_id, sequence_no, role, message_kind,
                content_text, response_json, incident_id, correlation_id
            ) VALUES (%s, %s, %s, 'administrator', %s, %s, %s, %s, %s)
            """,
            (
                str(uuid.uuid4()),
                conversation_id,
                int(next_sequence["next_sequence"]),
                kind,
                content,
                canonical_json(response),
                incident_id,
                correlation_id,
            ),
        )
        connection.execute(
            "UPDATE admin_chat_conversations SET updated_at = now() WHERE conversation_id = %s",
            (conversation_id,),
        )

    if created is not None:
        append(
            "I am online and monitoring the portfolio platform continuously. I will start a message here when I detect a new incident or an owner decision is required.",
            "proactive_update",
            {
                "title": "Continuous monitoring is active",
                "message": "The controlled administrator is monitoring deterministic system checks every five minutes.",
                "status_points": [],
                "proposed_action_type": "none",
                "action_reason": "",
                "requires_owner_approval": False,
                "limitations": [
                    "Sensitive and unsupported changes remain subject to explicit owner approval and implementation controls."
                ],
                "proactive": True,
            },
        )
    assessments = {
        str(item.get("incident_key")): str(item.get("assessment") or "")
        for item in (diagnosis or {}).get("priorities", [])
    }
    for finding in findings:
        if finding.incident_key not in new_keys:
            continue
        assessment = assessments.get(finding.incident_key) or finding.description
        append(
            f"I detected a new {finding.severity} incident: {finding.description} {assessment}".strip(),
            "incident",
            {
                "title": f"New {finding.severity} system incident",
                "message": assessment,
                "status_points": [
                    {
                        "text": finding.description,
                        "source_id": f"incident:{finding.incident_key}",
                    }
                ],
                "proposed_action_type": finding.autonomous_action or "none",
                "action_reason": finding.description,
                "requires_owner_approval": False,
                "limitations": [],
                "proactive": True,
            },
            incident_ids.get(finding.incident_key),
        )
    # The operations agent's diagnoses of open incidents and the connection agent's
    # reports, posted here because only the administrator writes this conversation.
    post_new_diagnoses(connection, conversation_id, append)
    post_connection_reports(connection, conversation_id, append)


def _ai_config(identity_client_id: str) -> AIAnalystConfig:
    return AIAnalystConfig(
        enabled=os.environ.get("AI_ADMIN_ENABLED", "false").strip().lower() == "true",
        endpoint=os.environ.get("AZURE_AI_ENDPOINT", "").strip(),
        deployment=os.environ.get("AZURE_AI_DEPLOYMENT", "").strip(),
        region=os.environ.get("AZURE_AI_REGION", "southeastasia").strip().lower(),
        processing_scope=os.environ.get("AZURE_AI_PROCESSING_SCOPE", "disabled").strip().lower(),
        managed_identity_client_id=identity_client_id,
    )


def main() -> int:
    import psycopg

    database_url = _required("DATABASE_URL")
    tenant_id = os.environ.get("PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001")
    account_id = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")
    portal_url = _required("PORTAL_URL")
    entra_tenant_id = _required("ENTRA_TENANT_ID")
    identity_client_id = _required("AZURE_CLIENT_ID")
    subscription_id = _required("AZURE_SUBSCRIPTION_ID")
    resource_group = _required("AZURE_RESOURCE_GROUP")
    web_app_name = _required("WEB_APP_NAME")
    correlation_id = str(uuid.uuid4())
    started_at = utc_now_iso()
    agent_run_id: int | None = None
    stage = "connect_database"
    connection = psycopg.connect(database_url, row_factory=dict_row)
    try:
        stage = "set_tenant_context"
        connection.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
        stage = "acquire_supervisor_lock"
        locked = connection.execute(
            "SELECT pg_try_advisory_lock(hashtext('wealthryse-controlled-autonomy')) AS locked"
        ).fetchone()
        if not locked or not locked["locked"]:
            print(canonical_json({"status": "SKIPPED", "reason": "supervisor_already_running"}))
            return 0
        stage = "load_autonomy_control"
        control = _control(connection)
        stage = "create_agent_run"
        row = connection.execute(
            """
            INSERT INTO autonomous_agent_runs(correlation_id, started_at, status)
            VALUES (%s, %s, 'running') RETURNING agent_run_id
            """,
            (correlation_id, started_at),
        ).fetchone()
        agent_run_id = int(row["agent_run_id"])
        connection.commit()

        stage = "collect_database_state"
        state = _database_state(connection, account_id)
        stage = "check_portal_boundary"
        portal_failure = _portal_failure(portal_url, entra_tenant_id)
        stage = "update_recovery_state"
        recovery = _recovery_context(
            connection,
            portal_failure=portal_failure,
            control=control,
            app_name=web_app_name,
            identity_client_id=identity_client_id,
            subscription_id=subscription_id,
            resource_group=resource_group,
        )
        connection.commit()
        findings = evaluate_supervisor_state(
            state, today_hkt=now_hkt_date(), portal_failure=portal_failure
        )
        stage = "check_layer_freshness"
        layer_findings = _layer_freshness_findings(database_url, tenant_id)
        if layer_findings:
            findings = sorted(
                [*findings, *layer_findings],
                key=lambda item: (item.severity != "critical", item.incident_key),
            )
        now_iso = utc_now_iso()
        stage = "synchronize_incidents"
        incident_ids, new_keys = _sync_incidents(connection, findings, now_iso)
        connection.commit()

        diagnosis: dict | None = None
        model_used = False
        if new_keys and control.enabled and not control.kill_switch:
            stage = "diagnose_new_incidents"
            new_findings = [item for item in findings if item.incident_key in new_keys]
            try:
                diagnosis, _ = diagnose_findings(_ai_config(identity_client_id), new_findings)
                model_used = True
                for finding in new_findings:
                    connection.execute(
                        "UPDATE system_incidents SET diagnosis_json = %s WHERE incident_id = %s",
                        (canonical_json(diagnosis), incident_ids[finding.incident_key]),
                    )
                connection.commit()
            except AIAnalystError as error:
                diagnosis = {"status": "unavailable", "error_code": error.code}

        stage = "publish_admin_chat_updates"
        _publish_admin_chat_updates(
            connection,
            findings=findings,
            new_keys=new_keys,
            incident_ids=incident_ids,
            diagnosis=diagnosis,
            correlation_id=correlation_id,
        )
        connection.commit()

        stage = "plan_safe_repairs"
        plans = plan_safe_repairs(
            findings,
            control,
            recent_action_at=_recent_actions(connection),
            now=datetime.now(timezone.utc),
            recovery=recovery,
        )
        action_results: list[dict] = []
        action_config = AdminActionConfig(
            enabled=True,
            environment="staging",
            subscription_id=subscription_id,
            resource_group=resource_group,
            managed_identity_client_id=identity_client_id,
        )
        for plan in plans:
            stage = "record_autonomous_action"
            row = connection.execute(
                """
                INSERT INTO autonomous_actions(
                    agent_run_id, incident_id, action_type, target_job,
                    policy_version, status, reason, created_at
                ) VALUES (%s, %s, %s, %s, %s, 'executing', %s, %s)
                RETURNING action_id
                """,
                (
                    agent_run_id,
                    incident_ids.get(plan.incident_key),
                    plan.action_type,
                    plan.target_job,
                    control.policy_version,
                    plan.reason,
                    utc_now_iso(),
                ),
            ).fetchone()
            action_id = int(row["action_id"])
            connection.commit()
            try:
                if plan.action_type == ROLLBACK_ACTION:
                    stage = "activate_last_known_good_revision"
                    if not plan.target_revision:
                        raise RuntimeError("Last-known-good revision was not resolved")
                    reference = _activate_web_revision(
                        subscription_id=subscription_id,
                        resource_group=resource_group,
                        app_name=plan.target_job,
                        revision_name=plan.target_revision,
                        token=_arm_token(identity_client_id),
                    )
                else:
                    stage = "start_allowlisted_job"
                    reference = _default_job_starter(action_config, plan.target_job)
                connection.execute(
                    """
                    UPDATE autonomous_actions SET status = 'executed', executed_at = %s,
                        result_reference = %s WHERE action_id = %s
                    """,
                    (utc_now_iso(), reference, action_id),
                )
                action_results.append({"action_type": plan.action_type, "status": "executed"})
            except (AdminActionError, RuntimeError) as error:
                connection.execute(
                    """
                    UPDATE autonomous_actions SET status = 'failed', executed_at = %s,
                        error_code = %s WHERE action_id = %s
                    """,
                    (utc_now_iso(), getattr(error, "code", type(error).__name__), action_id),
                )
                action_results.append(
                    {
                        "action_type": plan.action_type,
                        "status": "failed",
                        "error_code": getattr(error, "code", type(error).__name__),
                    }
                )
            connection.commit()

        stage = "complete_agent_run"
        current_authority = (
            control.enabled
            and not control.kill_switch
            and control.production_active
            and authority_review_current(control, now=datetime.now(timezone.utc))
        )
        status = "paused" if not current_authority else ("degraded" if findings else "healthy")
        summary = (
            diagnosis.get("summary")
            if diagnosis and diagnosis.get("summary")
            else (f"{len(findings)} active finding(s); {len(action_results)} autonomous action(s).")
        )
        connection.execute(
            """
            UPDATE autonomous_agent_runs SET completed_at = %s, status = %s,
                finding_count = %s, action_count = %s, model_used = %s, summary = %s
            WHERE agent_run_id = %s
            """,
            (
                utc_now_iso(),
                status,
                len(findings),
                len(action_results),
                model_used,
                summary,
                agent_run_id,
            ),
        )
        connection.commit()
        print(
            json.dumps(
                {
                    "status": status.upper(),
                    "correlation_id": correlation_id,
                    "finding_count": len(findings),
                    "new_incident_count": len(new_keys),
                    "model_used": model_used,
                    "actions": action_results,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception as error:
        connection.rollback()
        if agent_run_id is not None:
            try:
                connection.execute(
                    """
                    UPDATE autonomous_agent_runs SET completed_at = %s, status = 'failed',
                        error_code = %s, summary = %s WHERE agent_run_id = %s
                    """,
                    (utc_now_iso(), type(error).__name__, "Supervisor execution failed closed.", agent_run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
        detail = {
            "status": "FAILED",
            "error_code": type(error).__name__,
            "stage": stage,
        }
        sqlstate = getattr(error, "sqlstate", None)
        if sqlstate:
            detail["sqlstate"] = sqlstate
        diagnostic = getattr(error, "diag", None)
        if diagnostic is not None:
            if getattr(diagnostic, "table_name", None):
                detail["table"] = diagnostic.table_name
            if getattr(diagnostic, "message_primary", None):
                detail["message"] = diagnostic.message_primary
        print(canonical_json(detail))
        return 1
    finally:
        try:
            connection.execute("SELECT pg_advisory_unlock(hashtext('wealthryse-controlled-autonomy'))")
        except Exception:
            pass
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
