from __future__ import annotations

import hashlib
import json
import logging
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote

from .util import canonical_json, utc_now_iso


ACTION_AUDIT_LOGGER = logging.getLogger("uvicorn.error.ibkr_admin_action")
APPROVAL_TTL_MINUTES = 10
CONFIRMATION_PREFIX = "APPROVE"


class AdminActionError(ValueError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class AdminActionConfig:
    enabled: bool
    environment: str
    subscription_id: str
    resource_group: str
    managed_identity_client_id: str

    def validate(self) -> None:
        if not self.enabled:
            raise AdminActionError(503, "admin_actions_disabled", "Controlled administrative actions are disabled.")
        if self.environment.strip().lower() != "staging":
            raise AdminActionError(503, "production_actions_prohibited", "Administrative actions are restricted to staging.")
        if not self.subscription_id or not self.resource_group or not self.managed_identity_client_id:
            raise AdminActionError(503, "admin_action_configuration_incomplete", "The staging action broker is not fully configured.")


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    description: str
    target_job: str
    confirmation_phrase: str


ACTION_REGISTRY: dict[str, ActionDefinition] = {
    "refresh_risk_free_rate": ActionDefinition(
        "refresh_risk_free_rate",
        "Refresh Treasury risk-free rate",
        "Start the existing staging-only U.S. Treasury risk-free-rate job once. It never calls IBKR Flex.",
        "ibkrv2stg-risk-free-daily",
        "APPROVE REFRESH TREASURY RISK-FREE",
    ),
    "refresh_provisional_benchmarks": ActionDefinition(
        "refresh_provisional_benchmarks",
        "Refresh provisional benchmarks",
        "Start the existing yfinance benchmark job once. Results are approved for internal/shareholder analysis but prohibited from client distribution.",
        "ibkrv2stg-benchmark-daily",
        "APPROVE REFRESH PROVISIONAL BENCHMARKS",
    ),
}


def action_catalog() -> list[dict[str, str]]:
    return [asdict(definition) for definition in ACTION_REGISTRY.values()]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _audit(
    *,
    correlation_id: str,
    actor_object_id: str,
    action_type: str,
    approval_id: int | None,
    payload_sha256: str | None,
    status: str,
    error_code: str | None = None,
) -> None:
    event = {
        "event_type": "controlled_admin_action",
        "timestamp": utc_now_iso(),
        "correlation_id": correlation_id,
        "actor_object_id_sha256": hashlib.sha256(actor_object_id.encode("utf-8")).hexdigest(),
        "action_type": action_type,
        "approval_id": approval_id,
        "payload_sha256": payload_sha256,
        "status": status,
        "error_code": error_code,
    }
    ACTION_AUDIT_LOGGER.info("ADMIN_ACTION_AUDIT %s", canonical_json(event))


def _payload(config: AdminActionConfig, definition: ActionDefinition) -> dict[str, str]:
    return {
        "action_type": definition.action_type,
        "environment": "staging",
        "resource_group": config.resource_group,
        "target_job": definition.target_job,
    }


def create_action_preview(
    connection,
    config: AdminActionConfig,
    *,
    action_type: str,
    actor_object_id: str,
    correlation_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    config.validate()
    definition = ACTION_REGISTRY.get(action_type)
    if definition is None:
        raise AdminActionError(400, "action_not_allowed", "The requested administrative action is not allowed.")
    current = now or _utc_now()
    expires = current + timedelta(minutes=APPROVAL_TTL_MINUTES)
    payload = _payload(config, definition)
    payload_json = canonical_json(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    preview = {
        "label": definition.label,
        "description": definition.description,
        "target": definition.target_job,
        "environment": "staging",
        "effects": ["start one existing staging job execution"],
        "does_not_allow": [
            "production changes",
            "IBKR Flex access",
            "arbitrary job names",
            "SQL or shell access",
            "trading",
            "permission changes",
        ],
    }
    row = connection.execute(
        """
        INSERT INTO approved_actions(
            correlation_id, action_type, action_payload_sha256,
            action_payload_json, preview_json, requested_by, requested_at,
            expires_at, status
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
        RETURNING approval_id
        """,
        (
            correlation_id,
            action_type,
            payload_hash,
            payload_json,
            canonical_json(preview),
            actor_object_id,
            current.isoformat(),
            expires.isoformat(),
        ),
    ).fetchone()
    connection.commit()
    approval_id = int(row["approval_id"])
    _audit(
        correlation_id=correlation_id,
        actor_object_id=actor_object_id,
        action_type=action_type,
        approval_id=approval_id,
        payload_sha256=payload_hash,
        status="previewed",
    )
    return {
        "approval_id": approval_id,
        "status": "pending",
        "action_type": action_type,
        "action_payload_sha256": payload_hash,
        "preview": preview,
        "confirmation_phrase": definition.confirmation_phrase,
        "expires_at": expires.isoformat(),
        "correlation_id": correlation_id,
    }


def claim_action_for_execution(
    connection,
    config: AdminActionConfig,
    *,
    approval_id: int,
    expected_payload_sha256: str,
    confirmation: str,
    actor_object_id: str,
    correlation_id: str,
    now: datetime | None = None,
) -> tuple[ActionDefinition, Mapping[str, Any]]:
    config.validate()
    current = now or _utc_now()
    row = connection.execute(
        """
        SELECT approval_id, correlation_id, action_type, action_payload_sha256,
               action_payload_json, requested_by, requested_at, expires_at, status
        FROM approved_actions WHERE approval_id = %s FOR UPDATE
        """,
        (approval_id,),
    ).fetchone()
    if row is None:
        raise AdminActionError(404, "approval_not_found", "The approval request was not found.")
    action_type = str(row["action_type"])
    definition = ACTION_REGISTRY.get(action_type)
    if definition is None:
        raise AdminActionError(409, "stored_action_not_allowed", "The stored action is no longer allowed.")
    if row["status"] != "pending":
        raise AdminActionError(409, "approval_not_pending", "The approval request is no longer pending.")
    if row["requested_by"] != actor_object_id:
        raise AdminActionError(403, "approval_actor_mismatch", "The approval is bound to the requesting user.")
    if _parse_utc(str(row["expires_at"])) <= current:
        connection.execute(
            "UPDATE approved_actions SET status = 'expired', decided_at = %s WHERE approval_id = %s",
            (current.isoformat(), approval_id),
        )
        connection.commit()
        raise AdminActionError(409, "approval_expired", "The approval request has expired.")
    stored_hash = str(row["action_payload_sha256"])
    if expected_payload_sha256 != stored_hash:
        raise AdminActionError(409, "approval_digest_mismatch", "The approved payload digest does not match the preview.")
    if confirmation.strip() != definition.confirmation_phrase:
        raise AdminActionError(400, "confirmation_mismatch", "The typed confirmation does not match the preview.")
    payload_json = str(row["action_payload_json"])
    if hashlib.sha256(payload_json.encode("utf-8")).hexdigest() != stored_hash:
        raise AdminActionError(409, "stored_payload_tampered", "The stored approval payload failed integrity validation.")
    payload = json.loads(payload_json)
    if payload != _payload(config, definition):
        raise AdminActionError(409, "stored_payload_not_allowed", "The stored approval payload is outside the current allow-list.")
    updated = connection.execute(
        """
        UPDATE approved_actions
        SET status = 'executing', decided_by = %s, decided_at = %s
        WHERE approval_id = %s AND status = 'pending'
        """,
        (actor_object_id, current.isoformat(), approval_id),
    )
    if updated.rowcount != 1:
        connection.rollback()
        raise AdminActionError(409, "approval_race_lost", "The approval was already claimed.")
    connection.commit()
    _audit(
        correlation_id=correlation_id,
        actor_object_id=actor_object_id,
        action_type=action_type,
        approval_id=approval_id,
        payload_sha256=stored_hash,
        status="approved",
    )
    return definition, payload


def _default_job_starter(config: AdminActionConfig, job_name: str) -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential(managed_identity_client_id=config.managed_identity_client_id)
    token = credential.get_token("https://management.azure.com/.default").token
    url = (
        "https://management.azure.com/subscriptions/"
        f"{quote(config.subscription_id, safe='')}/resourceGroups/{quote(config.resource_group, safe='')}"
        f"/providers/Microsoft.App/jobs/{quote(job_name, safe='')}/start?api-version=2024-03-01"
    )
    request = urllib.request.Request(
        url,
        data=b"{}",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            operation_reference = response.headers.get("Operation-Location") or response.headers.get("Location")
    except urllib.error.HTTPError as error:
        raise AdminActionError(502, "azure_job_start_failed", f"Azure rejected the staging job start with HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise AdminActionError(502, "azure_job_start_unavailable", "Azure did not confirm the staging job start.") from error
    try:
        payload = json.loads(body or b"{}")
    except json.JSONDecodeError:
        payload = {}
    return str(
        payload.get("id")
        or payload.get("name")
        or operation_reference
        or f"start-accepted:{job_name}"
    )


def execute_claimed_action(
    connection_factory: Callable[[], Any],
    config: AdminActionConfig,
    *,
    approval_id: int,
    definition: ActionDefinition,
    actor_object_id: str,
    correlation_id: str,
    payload_sha256: str,
    job_starter: Callable[[AdminActionConfig, str], str] = _default_job_starter,
) -> dict[str, Any]:
    try:
        result_reference = job_starter(config, definition.target_job)
    except AdminActionError as error:
        with connection_factory() as connection:
            connection.execute(
                "UPDATE approved_actions SET status = 'failed', failure_code = %s WHERE approval_id = %s AND status = 'executing'",
                (error.code, approval_id),
            )
            connection.commit()
        _audit(
            correlation_id=correlation_id,
            actor_object_id=actor_object_id,
            action_type=definition.action_type,
            approval_id=approval_id,
            payload_sha256=payload_sha256,
            status="failed",
            error_code=error.code,
        )
        raise
    with connection_factory() as connection:
        connection.execute(
            """
            UPDATE approved_actions
            SET status = 'executed', executed_at = %s, result_reference = %s
            WHERE approval_id = %s AND status = 'executing'
            """,
            (utc_now_iso(), result_reference, approval_id),
        )
        connection.commit()
    _audit(
        correlation_id=correlation_id,
        actor_object_id=actor_object_id,
        action_type=definition.action_type,
        approval_id=approval_id,
        payload_sha256=payload_sha256,
        status="executed",
    )
    return {
        "approval_id": approval_id,
        "status": "executed",
        "action_type": definition.action_type,
        "result_reference": result_reference,
        "correlation_id": correlation_id,
    }
