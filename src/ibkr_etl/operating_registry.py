from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from .util import canonical_json


CAPABILITIES = frozenset(
    {
        "daily_ingestion",
        "benchmark_refresh",
        "sector_refresh",
        "month_end_reporting",
        "analyst_assurance",
        "viewer_directory",
    }
)
OPERATING_STATES = frozenset({"preview", "active", "paused", "disabled"})
WORK_KINDS = frozenset(
    {
        "flex_statement_request",
        "source_partition",
        "account_import",
        "benchmark_refresh",
        "sector_refresh",
        "analytics_rebuild",
        "historical_report",
        "month_end_report",
        "quality_assurance",
    }
)
REQUIRED_REPORT_ASSETS = frozenset(
    {
        ("factsheet", "template"),
        ("factsheet", "completed_example"),
        ("presentation", "template"),
    }
)


@dataclass(frozen=True)
class OperatingTarget:
    subject_id: str
    subject_type: str
    lifecycle_status: str
    capability: str
    operating_state: str
    effective_from: date
    configuration: dict[str, Any]
    configuration_sha256: str


@dataclass(frozen=True)
class FlexSubjectBinding:
    credential_id: str
    flex_source_id: str
    subject_id: str
    source_account_id: str
    start_date: date


@dataclass(frozen=True)
class WorkClaim:
    work_item_id: str
    work_kind: str
    ordering_key: str
    idempotency_key: str
    subject_id: str | None
    flex_source_id: str | None
    payload: dict[str, Any]
    attempt_count: int
    maximum_attempts: int


def configuration_sha256(configuration: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(dict(configuration)).encode("utf-8")).hexdigest()


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("Operating registration configuration must be a JSON object")


def validate_operating_target(row: Mapping[str, Any]) -> OperatingTarget:
    capability = str(row["capability"])
    state = str(row["operating_state"])
    if capability not in CAPABILITIES:
        raise RuntimeError(f"Unsupported operating capability: {capability}")
    if state not in OPERATING_STATES:
        raise RuntimeError(f"Unsupported operating state: {state}")
    subject_id = str(row["subject_id"]).strip().upper()
    if not subject_id or len(subject_id) > 80:
        raise RuntimeError("Operating registration has an invalid subject ID")
    lifecycle = str(row["lifecycle_status"])
    if state == "active" and lifecycle != "active":
        raise RuntimeError(
            f"Active capability {capability} is bound to non-active subject {subject_id}"
        )
    if state == "preview" and lifecycle not in {"onboarding", "preview"}:
        raise RuntimeError(
            f"Preview capability {capability} is bound to invalid subject {subject_id}"
        )
    effective_from = row["effective_from"]
    if isinstance(effective_from, str):
        effective_from = date.fromisoformat(effective_from)
    if not isinstance(effective_from, date):
        raise RuntimeError("Operating registration has an invalid effective date")
    configuration = _json_object(row["configuration_json"])
    expected_digest = configuration_sha256(configuration)
    stored_digest = str(row["configuration_sha256"]).lower()
    if stored_digest != expected_digest:
        raise RuntimeError(
            f"Operating registration integrity check failed for {subject_id}/{capability}"
        )
    if capability in {"daily_ingestion", "benchmark_refresh"}:
        start_date = configuration.get("start_date")
        if not isinstance(start_date, str):
            raise RuntimeError(f"{capability} requires an ISO start_date")
        date.fromisoformat(start_date)
    return OperatingTarget(
        subject_id=subject_id,
        subject_type=str(row["subject_type"]),
        lifecycle_status=lifecycle,
        capability=capability,
        operating_state=state,
        effective_from=effective_from,
        configuration=configuration,
        configuration_sha256=stored_digest,
    )


def load_operating_targets(
    connection,
    *,
    tenant_id: str,
    capability: str,
    as_of: date,
    include_preview: bool = False,
) -> list[OperatingTarget]:
    if capability not in CAPABILITIES:
        raise RuntimeError(f"Unsupported operating capability: {capability}")
    allowed_states = ("active", "preview") if include_preview else ("active",)
    placeholders = ", ".join("?" for _ in allowed_states)
    rows = connection.execute(
        f"""
        SELECT r.subject_id, s.subject_type, s.lifecycle_status,
               r.capability, r.operating_state, r.effective_from,
               r.configuration_json, r.configuration_sha256
        FROM subject_operating_registrations r
        JOIN platform_subjects s
          ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
        WHERE r.tenant_id = ? AND r.capability = ?
          AND r.operating_state IN ({placeholders})
          AND r.effective_from <= ?
          AND (r.effective_to IS NULL OR r.effective_to >= ?)
        ORDER BY r.subject_id
        """,
        (tenant_id, capability, *allowed_states, as_of.isoformat(), as_of.isoformat()),
    ).fetchall()
    targets = [validate_operating_target(row) for row in rows]
    subject_ids = [target.subject_id for target in targets]
    if len(subject_ids) != len(set(subject_ids)):
        raise RuntimeError(f"Duplicate current operating registration for {capability}")
    return targets


def plan_flex_work(bindings: Iterable[FlexSubjectBinding], *, run_date: date) -> list[dict[str, Any]]:
    """Plan one statement request per source and independent downstream work.

    Requests sharing a credential receive the same ordering key, so a queue with
    ordered sessions cannot send them concurrently.  Partition/import work is
    subject-keyed and can scale independently after the statement is available.
    """

    normalized = sorted(
        list(bindings),
        key=lambda item: (
            item.credential_id,
            item.flex_source_id,
            item.subject_id,
            item.source_account_id,
        ),
    )
    seen_subjects: set[str] = set()
    sources: dict[tuple[str, str], list[FlexSubjectBinding]] = {}
    for binding in normalized:
        if binding.subject_id in seen_subjects:
            raise RuntimeError(f"Subject {binding.subject_id} has multiple active Flex bindings")
        seen_subjects.add(binding.subject_id)
        sources.setdefault((binding.credential_id, binding.flex_source_id), []).append(binding)

    work: list[dict[str, Any]] = []
    for (credential_id, flex_source_id), source_bindings in sources.items():
        source_key = f"flex:{flex_source_id}:{run_date.isoformat()}"
        work.append(
            {
                "work_kind": "flex_statement_request",
                "ordering_key": f"credential:{credential_id}",
                "idempotency_key": source_key,
                "maximum_attempts": 1,
                "flex_source_id": flex_source_id,
                "subject_id": None,
                "source_account_id": None,
                "depends_on_idempotency_key": None,
            }
        )
        for binding in source_bindings:
            subject_key = f"{source_key}:subject:{binding.subject_id}"
            work.extend(
                [
                    {
                        "work_kind": "source_partition",
                        "ordering_key": f"subject:{binding.subject_id}",
                        "idempotency_key": f"{subject_key}:partition",
                        "maximum_attempts": 3,
                        "flex_source_id": flex_source_id,
                        "subject_id": binding.subject_id,
                        "source_account_id": binding.source_account_id,
                        "depends_on_idempotency_key": source_key,
                    },
                    {
                        "work_kind": "account_import",
                        "ordering_key": f"subject:{binding.subject_id}",
                        "idempotency_key": f"{subject_key}:import",
                        "maximum_attempts": 3,
                        "flex_source_id": flex_source_id,
                        "subject_id": binding.subject_id,
                        "source_account_id": binding.source_account_id,
                        "depends_on_idempotency_key": f"{subject_key}:partition",
                    },
                    {
                        "work_kind": "analytics_rebuild",
                        "ordering_key": f"subject:{binding.subject_id}",
                        "idempotency_key": f"{subject_key}:analytics",
                        "maximum_attempts": 3,
                        "flex_source_id": flex_source_id,
                        "subject_id": binding.subject_id,
                        "source_account_id": binding.source_account_id,
                        "depends_on_idempotency_key": f"{subject_key}:import",
                    },
                ]
            )
    return work


def load_active_flex_bindings(
    connection, *, tenant_id: str, as_of: date
) -> list[FlexSubjectBinding]:
    rows = connection.execute(
        """
        SELECT r.subject_id, r.configuration_json, r.configuration_sha256,
               s.subject_type, s.lifecycle_status, r.capability,
               r.operating_state, r.effective_from,
               b.flex_source_id, b.source_account_id,
               q.credential_id
        FROM subject_operating_registrations r
        JOIN platform_subjects s
          ON s.tenant_id = r.tenant_id AND s.subject_id = r.subject_id
        JOIN subject_flex_source_bindings b
          ON b.tenant_id = r.tenant_id AND b.subject_id = r.subject_id
         AND b.status = 'active' AND b.is_primary
         AND b.effective_from <= ?
         AND (b.effective_to IS NULL OR b.effective_to >= ?)
        JOIN ibkr_flex_queries q
          ON q.tenant_id = b.tenant_id AND q.flex_source_id = b.flex_source_id
         AND q.status = 'active' AND q.schedule_enabled
        JOIN ibkr_flex_credentials c
          ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
         AND c.status = 'active'
        WHERE r.tenant_id = ? AND r.capability = 'daily_ingestion'
          AND r.operating_state = 'active'
          AND r.effective_from <= ?
          AND (r.effective_to IS NULL OR r.effective_to >= ?)
        ORDER BY q.credential_id, b.flex_source_id, r.subject_id
        """,
        (
            as_of.isoformat(),
            as_of.isoformat(),
            tenant_id,
            as_of.isoformat(),
            as_of.isoformat(),
        ),
    ).fetchall()
    bindings: list[FlexSubjectBinding] = []
    for row in rows:
        target = validate_operating_target(row)
        start_date = date.fromisoformat(str(target.configuration["start_date"]))
        bindings.append(
            FlexSubjectBinding(
                credential_id=str(row["credential_id"]),
                flex_source_id=str(row["flex_source_id"]),
                subject_id=target.subject_id,
                source_account_id=str(row["source_account_id"]),
                start_date=start_date,
            )
        )
    return bindings


def enqueue_work_plan(
    connection,
    *,
    tenant_id: str,
    work_plan: Iterable[Mapping[str, Any]],
) -> list[str]:
    """Persist a deterministic work graph without duplicating prior work."""

    inserted: list[str] = []
    resolved: dict[str, str] = {}
    for item in work_plan:
        work_kind = str(item["work_kind"])
        if work_kind not in WORK_KINDS:
            raise RuntimeError(f"Unsupported institutional work kind: {work_kind}")
        idempotency_key = str(item["idempotency_key"])
        depends_on_key = item.get("depends_on_idempotency_key")
        dependency_id = None
        if depends_on_key:
            dependency_id = resolved.get(str(depends_on_key))
            if dependency_id is None:
                row = connection.execute(
                    """
                    SELECT work_item_id FROM institutional_work_items
                    WHERE tenant_id = ? AND idempotency_key = ?
                    """,
                    (tenant_id, str(depends_on_key)),
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        f"Work dependency is unavailable: {depends_on_key}"
                    )
                dependency_id = str(row["work_item_id"])
        work_item_id = str(
            uuid.uuid5(
                uuid.UUID("a8f73575-67b4-48e8-afc4-d354e10a6b3e"),
                f"{tenant_id}:{idempotency_key}",
            )
        )
        payload = {
            "run_date": idempotency_key.split(":")[2]
            if idempotency_key.startswith("flex:")
            else None,
            "source_account_id": item.get("source_account_id"),
        }
        connection.execute(
            """
            INSERT INTO institutional_work_items(
                work_item_id, tenant_id, subject_id, flex_source_id,
                depends_on_work_item_id, work_kind, ordering_key,
                idempotency_key, maximum_attempts, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?::jsonb)
            ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
            """,
            (
                work_item_id,
                tenant_id,
                item.get("subject_id"),
                item.get("flex_source_id"),
                dependency_id,
                work_kind,
                str(item["ordering_key"]),
                idempotency_key,
                int(item["maximum_attempts"]),
                canonical_json(payload),
            ),
        )
        resolved[idempotency_key] = work_item_id
        inserted.append(work_item_id)
    return inserted


def _work_payload(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise RuntimeError("Institutional work payload must be a JSON object")


def claim_next_work(
    connection,
    *,
    tenant_id: str,
    worker_id: str,
    lease_seconds: int = 300,
    allowed_kinds: Iterable[str] | None = None,
) -> WorkClaim | None:
    """Atomically claim one dependency-ready item.

    The ordering-key exclusion prevents concurrent requests on the same IBKR
    credential and serializes work for a single subject. Expired leases are
    reclaimed only when the attempt budget permits it.
    """

    normalized_worker = worker_id.strip()
    if not normalized_worker or len(normalized_worker) > 120:
        raise RuntimeError("Worker ID must contain 1-120 characters")
    if lease_seconds < 30 or lease_seconds > 3600:
        raise RuntimeError("Worker lease must be between 30 and 3600 seconds")
    kinds = tuple(sorted(set(allowed_kinds or WORK_KINDS)))
    if not kinds or any(kind not in WORK_KINDS for kind in kinds):
        raise RuntimeError("Worker contains an unsupported work kind")
    placeholders = ", ".join("?" for _ in kinds)
    row = connection.execute(
        f"""
        WITH candidate AS (
            SELECT item.work_item_id
            FROM institutional_work_items item
            LEFT JOIN institutional_work_items dependency
              ON dependency.work_item_id = item.depends_on_work_item_id
            WHERE item.tenant_id = ?
              AND item.work_kind IN ({placeholders})
              AND item.available_at <= now()
              AND item.attempt_count < item.maximum_attempts
              AND (
                    item.status = 'queued'
                    OR (
                        item.status = 'running'
                        AND item.lease_expires_at < now()
                    )
              )
              AND (
                    item.depends_on_work_item_id IS NULL
                    OR dependency.status = 'succeeded'
              )
              AND NOT EXISTS (
                    SELECT 1 FROM institutional_work_items active
                    WHERE active.tenant_id = item.tenant_id
                      AND active.ordering_key = item.ordering_key
                      AND active.status = 'running'
                      AND active.lease_expires_at >= now()
                      AND active.work_item_id <> item.work_item_id
              )
            ORDER BY item.priority DESC, item.available_at, item.created_at
            FOR UPDATE OF item SKIP LOCKED
            LIMIT 1
        )
        UPDATE institutional_work_items item
        SET status = 'running', lease_owner = ?,
            lease_expires_at = now() + (? * interval '1 second'),
            attempt_count = item.attempt_count + 1,
            started_at = COALESCE(item.started_at, now()),
            completed_at = NULL, updated_at = now(),
            error_code = NULL, error_message = NULL
        FROM candidate
        WHERE item.work_item_id = candidate.work_item_id
        RETURNING item.work_item_id, item.work_kind, item.ordering_key,
                  item.idempotency_key, item.subject_id, item.flex_source_id,
                  item.payload_json, item.attempt_count, item.maximum_attempts
        """,
        (tenant_id, *kinds, normalized_worker, lease_seconds),
    ).fetchone()
    if row is None:
        return None
    return WorkClaim(
        work_item_id=str(row["work_item_id"]),
        work_kind=str(row["work_kind"]),
        ordering_key=str(row["ordering_key"]),
        idempotency_key=str(row["idempotency_key"]),
        subject_id=(str(row["subject_id"]) if row["subject_id"] else None),
        flex_source_id=(str(row["flex_source_id"]) if row["flex_source_id"] else None),
        payload=_work_payload(row["payload_json"]),
        attempt_count=int(row["attempt_count"]),
        maximum_attempts=int(row["maximum_attempts"]),
    )


def complete_work(
    connection, *, tenant_id: str, work_item_id: str, worker_id: str
) -> None:
    cursor = connection.execute(
        """
        UPDATE institutional_work_items
        SET status = 'succeeded', completed_at = now(), updated_at = now(),
            lease_owner = NULL, lease_expires_at = NULL,
            error_code = NULL, error_message = NULL
        WHERE tenant_id = ? AND work_item_id = ? AND status = 'running'
          AND lease_owner = ? AND lease_expires_at >= now()
        """,
        (tenant_id, work_item_id, worker_id),
    )
    if cursor.rowcount != 1:
        raise RuntimeError("Work completion rejected because the lease is not current")


def fail_work(
    connection,
    *,
    tenant_id: str,
    claim: WorkClaim,
    worker_id: str,
    error_code: str,
    error_message: str,
    retry_after_seconds: int = 300,
) -> str:
    """Fail closed for Flex and retry bounded non-broker work."""

    terminal = (
        claim.work_kind == "flex_statement_request"
        or claim.attempt_count >= claim.maximum_attempts
    )
    status = "dead_letter" if terminal else "queued"
    if retry_after_seconds < 30 or retry_after_seconds > 86400:
        raise RuntimeError("Retry delay must be between 30 seconds and 24 hours")
    if status == "dead_letter":
        cursor = connection.execute(
            """
            UPDATE institutional_work_items
            SET status = 'dead_letter', completed_at = now(), updated_at = now(),
                lease_owner = NULL, lease_expires_at = NULL,
                error_code = ?, error_message = ?
            WHERE tenant_id = ? AND work_item_id = ? AND status = 'running'
              AND lease_owner = ?
            """,
            (error_code[:100], error_message[:4000], tenant_id, claim.work_item_id, worker_id),
        )
    else:
        cursor = connection.execute(
            """
            UPDATE institutional_work_items
            SET status = 'queued', available_at = now() + (? * interval '1 second'),
                completed_at = NULL, updated_at = now(),
                lease_owner = NULL, lease_expires_at = NULL,
                error_code = ?, error_message = ?
            WHERE tenant_id = ? AND work_item_id = ? AND status = 'running'
              AND lease_owner = ?
            """,
            (
                retry_after_seconds,
                error_code[:100],
                error_message[:4000],
                tenant_id,
                claim.work_item_id,
                worker_id,
            ),
        )
    if cursor.rowcount != 1:
        raise RuntimeError("Work failure rejected because the lease is not current")
    return status


def dead_letter_exhausted_and_blocked_work(connection, *, tenant_id: str) -> int:
    """Move unrecoverable items and dependency descendants to dead letter."""

    exhausted = connection.execute(
        """
        UPDATE institutional_work_items
        SET status = 'dead_letter', completed_at = now(), updated_at = now(),
            lease_owner = NULL, lease_expires_at = NULL,
            error_code = COALESCE(error_code, 'lease_exhausted'),
            error_message = COALESCE(error_message, 'Attempt budget exhausted before completion')
        WHERE tenant_id = ? AND status = 'running' AND lease_expires_at < now()
          AND attempt_count >= maximum_attempts
        """,
        (tenant_id,),
    ).rowcount
    blocked = connection.execute(
        """
        UPDATE institutional_work_items item
        SET status = 'dead_letter', completed_at = now(), updated_at = now(),
            error_code = 'dependency_failed',
            error_message = 'Required predecessor did not complete successfully'
        FROM institutional_work_items dependency
        WHERE item.tenant_id = ? AND item.status = 'queued'
          AND item.depends_on_work_item_id = dependency.work_item_id
          AND dependency.status IN ('dead_letter', 'failed', 'cancelled')
        """,
        (tenant_id,),
    ).rowcount
    return int(exhausted) + int(blocked)


def control_plane_summary(connection, *, tenant_id: str) -> dict[str, Any]:
    registrations = connection.execute(
        """
        SELECT operating_state, COUNT(*) AS count
        FROM subject_operating_registrations
        WHERE tenant_id = ? AND (effective_to IS NULL OR effective_to >= current_date)
        GROUP BY operating_state ORDER BY operating_state
        """,
        (tenant_id,),
    ).fetchall()
    work = connection.execute(
        """
        SELECT status, COUNT(*) AS count
        FROM institutional_work_items WHERE tenant_id = ?
        GROUP BY status ORDER BY status
        """,
        (tenant_id,),
    ).fetchall()
    identity = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM app_users WHERE tenant_id = ? AND status = 'active') AS users,
          (SELECT COUNT(*) FROM app_user_roles WHERE tenant_id = ? AND role_status = 'active'
             AND effective_to IS NULL) AS roles,
          (SELECT COUNT(*) FROM user_account_access WHERE tenant_id = ?) AS entitlements,
          (SELECT COUNT(*) FROM app_user_roles WHERE tenant_id = ? AND role_status = 'active'
             AND effective_to IS NULL AND role_name = 'owner') AS owners,
          (SELECT MAX(applied_at) FROM identity_registry_revisions
             WHERE tenant_id = ? AND status = 'applied') AS last_applied_at
        """,
        (tenant_id, tenant_id, tenant_id, tenant_id, tenant_id),
    ).fetchone()
    shadow = connection.execute(
        """
        SELECT reconciliation_id, run_date, status, active_subject_count,
               preview_subject_count, planned_capability_count, mismatch_count,
               authoritative_manifest_sha256, registry_sha256, created_at
        FROM institutional_shadow_reconciliations
        WHERE tenant_id = ?
        ORDER BY run_date DESC, created_at DESC
        LIMIT 1
        """,
        (tenant_id,),
    ).fetchone()
    dead_letters = next(
        (int(row["count"]) for row in work if str(row["status"]) == "dead_letter"), 0
    )
    ready = bool(identity and int(identity["owners"] or 0) == 1 and int(identity["users"] or 0) > 0)
    return {
        "registrations": {str(row["operating_state"]): int(row["count"]) for row in registrations},
        "work_items": {str(row["status"]): int(row["count"]) for row in work},
        "dead_letters": dead_letters,
        "identity_registry": {
            "users": int(identity["users"] or 0) if identity else 0,
            "roles": int(identity["roles"] or 0) if identity else 0,
            "entitlements": int(identity["entitlements"] or 0) if identity else 0,
            "owners": int(identity["owners"] or 0) if identity else 0,
            "last_applied_at": identity["last_applied_at"] if identity else None,
            "database_authorization_ready": ready,
        },
        "shadow_reconciliation": (
            {
                key: shadow[key]
                for key in (
                    "reconciliation_id",
                    "run_date",
                    "status",
                    "active_subject_count",
                    "preview_subject_count",
                    "planned_capability_count",
                    "mismatch_count",
                    "authoritative_manifest_sha256",
                    "registry_sha256",
                    "created_at",
                )
            }
            if shadow
            else None
        ),
    }


def plan_month_end_work(
    targets: Iterable[OperatingTarget], *, period_end: date
) -> list[dict[str, Any]]:
    planned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for target in sorted(targets, key=lambda item: item.subject_id):
        if target.capability != "month_end_reporting" or target.operating_state != "active":
            raise RuntimeError("Month-end work requires an active month_end_reporting target")
        if target.subject_id in seen:
            raise RuntimeError(f"Duplicate month-end target: {target.subject_id}")
        seen.add(target.subject_id)
        planned.append(
            {
                "work_kind": "month_end_report",
                "ordering_key": f"subject:{target.subject_id}",
                "idempotency_key": (
                    f"month-end:{target.subject_id}:{period_end.isoformat()}:"
                    f"{target.configuration_sha256[:16]}"
                ),
                "maximum_attempts": 3,
                "flex_source_id": None,
                "subject_id": target.subject_id,
                "source_account_id": None,
                "depends_on_idempotency_key": None,
            }
        )
    return planned


def require_current_report_assets(
    connection, *, tenant_id: str, subject_id: str
) -> dict[tuple[str, str], dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT report_type, asset_role, version_label, container_name, blob_name,
               content_type, sha256, byte_count, approval_status, metadata_json
        FROM subject_report_template_assets
        WHERE tenant_id = ? AND subject_id = ? AND is_current
          AND approval_status IN ('validated', 'active')
        ORDER BY report_type, asset_role
        """,
        (tenant_id, subject_id),
    ).fetchall()
    assets: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["report_type"]), str(row["asset_role"]))
        if key in assets:
            raise RuntimeError(f"Duplicate current report asset for {subject_id}: {key}")
        assets[key] = {
            field: row[field]
            for field in (
                "report_type",
                "asset_role",
                "version_label",
                "container_name",
                "blob_name",
                "content_type",
                "sha256",
                "byte_count",
                "approval_status",
                "metadata_json",
            )
        }
    missing = sorted(REQUIRED_REPORT_ASSETS - set(assets))
    if missing:
        labels = ", ".join(f"{kind}/{role}" for kind, role in missing)
        raise RuntimeError(f"Subject {subject_id} lacks current report assets: {labels}")
    return assets
