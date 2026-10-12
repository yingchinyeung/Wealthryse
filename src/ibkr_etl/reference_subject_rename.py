from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from .subject_onboarding import canonical_json, configuration_sha256
from .template_upload import presentation_identity_scope


SUBJECT_ID = "hong-kong-china"
OLD_DISPLAY_NAME = "Hong Kong and China"
NEW_DISPLAY_NAME = "China & Hong Kong"
DEPENDENT_SUBJECT_ID = "alpha-world-ms"


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return dict(value or {})


def _replace_display_name(value: Any) -> Any:
    """Replace only the retired portfolio label, preserving IDs and free text."""

    if isinstance(value, dict):
        return {key: _replace_display_name(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_display_name(item) for item in value]
    if value == OLD_DISPLAY_NAME:
        return NEW_DISPLAY_NAME
    return value


def renamed_definition(configuration: Mapping[str, Any] | str) -> dict[str, Any]:
    """Return the same financial contract with only its display label amended."""

    return _replace_display_name(_mapping(configuration))


def _new_approval(
    connection: Any,
    *,
    old: Mapping[str, Any],
    scope: Mapping[str, Any],
    disclosure_version: str,
    changed_at: str,
    reason: str,
) -> tuple[str, str]:
    old_id = str(old["approval_id"])
    new_id = str(uuid.uuid4())
    scope_json = canonical_json(dict(scope))
    scope_sha256 = configuration_sha256(dict(scope))
    metadata = _mapping(old["metadata_json"])
    metadata.update(
        {
            "amends_approval_id": old_id,
            "amendment_reason": reason,
            "owner_instruction_date": changed_at[:10],
        }
    )
    connection.execute(
        """
        UPDATE account_onboarding_approvals
        SET approval_status = 'revoked', revoked_at = ?
        WHERE tenant_id = ? AND approval_id = ?
          AND approval_status = 'approved'
        """,
        (changed_at, old["tenant_id"], old_id),
    )
    connection.execute(
        """
        INSERT INTO account_onboarding_approvals(
            approval_id, tenant_id, case_id, subject_id, approval_type,
            approval_status, disclosure_version, destination, scope_json,
            scope_sha256, approved_by_identity_sha256, approved_at,
            metadata_json
        ) VALUES (
            ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            new_id,
            old["tenant_id"],
            old["case_id"],
            old["subject_id"],
            old["approval_type"],
            disclosure_version,
            old["destination"],
            scope_json,
            scope_sha256,
            old["approved_by_identity_sha256"],
            changed_at,
            canonical_json(metadata),
        ),
    )
    return new_id, scope_sha256


def _amend_calculation_definition(
    connection: Any,
    *,
    tenant_id: str,
    subject_id: str,
    changed_at: str,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT definition_id, tenant_id, subject_id, subject_type,
               calculation_kind, definition_version, configuration_json,
               configuration_sha256, approval_status, effective_from,
               created_by_identity_sha256, approved_by_identity_sha256,
               approved_at
        FROM subject_calculation_definitions
        WHERE tenant_id = ? AND subject_id = ? AND effective_to IS NULL
        ORDER BY definition_version DESC
        LIMIT 1
        """,
        (tenant_id, subject_id),
    ).fetchone()
    if row is None:
        return None
    current = _mapping(row["configuration_json"])
    amended = renamed_definition(current)
    if amended == current:
        return {
            "definition_id": str(row["definition_id"]),
            "configuration": current,
            "sha256": str(row["configuration_sha256"]),
            "version": int(row["definition_version"]),
        }
    digest = configuration_sha256(amended)
    definition_id = str(row["definition_id"])
    version = int(row["definition_version"])
    if str(row["approval_status"]) == "approved":
        new_definition_id = str(uuid.uuid4())
        connection.execute(
            """
            UPDATE subject_calculation_definitions
            SET approval_status = 'superseded', effective_to = ?
            WHERE tenant_id = ? AND definition_id = ?
              AND approval_status = 'approved' AND effective_to IS NULL
            """,
            (row["effective_from"], tenant_id, definition_id),
        )
        connection.execute(
            """
            INSERT INTO subject_calculation_definitions(
                definition_id, tenant_id, subject_id, subject_type,
                calculation_kind, definition_version, configuration_json,
                configuration_sha256, approval_status, effective_from,
                created_by_identity_sha256, approved_by_identity_sha256,
                created_at, approved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?)
            """,
            (
                new_definition_id,
                tenant_id,
                subject_id,
                row["subject_type"],
                row["calculation_kind"],
                version + 1,
                canonical_json(amended),
                digest,
                row["effective_from"],
                row["created_by_identity_sha256"],
                row["approved_by_identity_sha256"],
                changed_at,
                changed_at,
            ),
        )
        definition_id = new_definition_id
        version += 1
    else:
        connection.execute(
            """
            UPDATE subject_calculation_definitions
            SET configuration_json = ?, configuration_sha256 = ?
            WHERE tenant_id = ? AND definition_id = ?
              AND approval_status = 'validated' AND effective_to IS NULL
            """,
            (canonical_json(amended), digest, tenant_id, definition_id),
        )
    return {
        "definition_id": definition_id,
        "configuration": amended,
        "sha256": digest,
        "version": version,
    }


def _amend_calculation_approval(
    connection: Any,
    *,
    tenant_id: str,
    subject_id: str,
    definition: Mapping[str, Any],
    changed_at: str,
) -> str | None:
    old = connection.execute(
        """
        SELECT approval_id, tenant_id, case_id, subject_id, approval_type,
               disclosure_version, destination, scope_json, scope_sha256,
               approved_by_identity_sha256, metadata_json
        FROM account_onboarding_approvals
        WHERE tenant_id = ? AND subject_id = ?
          AND approval_type = 'calculation_contract'
          AND approval_status = 'approved'
        """,
        (tenant_id, subject_id),
    ).fetchone()
    if old is None:
        return None
    scope = _mapping(old["scope_json"])
    scope["definition"] = dict(definition["configuration"])
    scope["definition_sha256"] = definition["sha256"]
    if subject_id == SUBJECT_ID:
        scope["display_name"] = NEW_DISPLAY_NAME
    if (
        _mapping(old["scope_json"]) == scope
        and str(old["scope_sha256"] or "") == configuration_sha256(scope)
    ):
        return str(old["approval_id"])
    approval_id, scope_sha256 = _new_approval(
        connection,
        old=old,
        scope=scope,
        disclosure_version="typed-derived-calculation-v2-display-name",
        changed_at=changed_at,
        reason="Owner-approved portfolio display-name amendment; financial methodology unchanged.",
    )
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = 'pass',
            message = CASE check_id
                WHEN 'calculation_definition_approved' THEN
                    'The owner approved the versioned deterministic calculation with the amended portfolio display name.'
                ELSE
                    'The owner approved the derived reporting and benchmark contract with the amended portfolio display name.'
            END,
            evidence_json = ?, checked_at = ?
        WHERE tenant_id = ? AND case_id = ?
          AND check_id IN ('calculation_definition_approved', 'financial_contract_approved')
        """,
        (canonical_json(scope), changed_at, tenant_id, old["case_id"]),
    )
    case = connection.execute(
        """
        SELECT configuration_json FROM account_onboarding_cases
        WHERE tenant_id = ? AND case_id = ?
        """,
        (tenant_id, old["case_id"]),
    ).fetchone()
    if case is not None:
        configuration = _replace_display_name(_mapping(case["configuration_json"]))
        configuration["calculation_contract"] = {"scope_sha256": scope_sha256}
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET configuration_json = ?, updated_at = ?
            WHERE tenant_id = ? AND case_id = ?
            """,
            (
                canonical_json(configuration),
                changed_at,
                tenant_id,
                old["case_id"],
            ),
        )
    return approval_id


def _amend_presentation_identity(
    connection: Any, *, tenant_id: str, changed_at: str
) -> str | None:
    old = connection.execute(
        """
        SELECT approval_id, tenant_id, case_id, subject_id, approval_type,
               disclosure_version, destination, scope_json, scope_sha256,
               approved_by_identity_sha256, metadata_json
        FROM account_onboarding_approvals
        WHERE tenant_id = ? AND subject_id = ?
          AND approval_type = 'presentation_identity'
          AND approval_status = 'approved'
        """,
        (tenant_id, SUBJECT_ID),
    ).fetchone()
    if old is None:
        return None
    previous_scope = _mapping(old["scope_json"])
    scope = presentation_identity_scope(
        subject_id=SUBJECT_ID,
        display_name=NEW_DISPLAY_NAME,
        layout_policy=str(previous_scope.get("layout_policy") or "platform_standard"),
    )
    if (
        previous_scope == scope
        and str(old["scope_sha256"] or "") == configuration_sha256(scope)
    ):
        return str(old["approval_id"])
    approval_id, scope_sha256 = _new_approval(
        connection,
        old=old,
        scope=scope,
        disclosure_version="presentation-identity-v2-display-name",
        changed_at=changed_at,
        reason="Owner-approved portfolio display-name amendment.",
    )
    connection.execute(
        """
        UPDATE account_onboarding_checks
        SET status = 'pass',
            message = 'The owner-approved presentation identity is locked to iStrategy: China & Hong Kong.',
            evidence_json = ?, checked_at = ?
        WHERE tenant_id = ? AND case_id = ?
          AND check_id = 'presentation_identity_approved'
        """,
        (
            canonical_json(
                {
                    "approval_id": approval_id,
                    "scope_sha256": scope_sha256,
                    "strategy_name": scope["strategy_name"],
                    "layout_policy": scope["layout_policy"],
                    "uploader_editable": False,
                }
            ),
            changed_at,
            tenant_id,
            old["case_id"],
        ),
    )
    return approval_id


def reconcile_china_hong_kong_display_name(
    connection: Any, *, tenant_id: str, changed_at: str | None = None
) -> dict[str, Any]:
    """Apply the owner-approved display amendment without changing portfolio IDs.

    Approved calculation and presentation contracts are superseded by new
    auditable versions. Historical calculation runs keep their original foreign
    keys, while all current and future user-facing output uses the new name.
    """

    timestamp = changed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    subject = connection.execute(
        """
        SELECT display_name, metadata_json FROM platform_subjects
        WHERE tenant_id = ? AND subject_id = ?
        """,
        (tenant_id, SUBJECT_ID),
    ).fetchone()
    if subject is None:
        return {"status": "not_applicable", "subject_id": SUBJECT_ID}

    definitions: dict[str, dict[str, Any]] = {}
    for subject_id in (SUBJECT_ID, DEPENDENT_SUBJECT_ID):
        amended = _amend_calculation_definition(
            connection,
            tenant_id=tenant_id,
            subject_id=subject_id,
            changed_at=timestamp,
        )
        if amended is not None:
            definitions[subject_id] = amended
            _amend_calculation_approval(
                connection,
                tenant_id=tenant_id,
                subject_id=subject_id,
                definition=amended,
                changed_at=timestamp,
            )

    metadata = _mapping(subject["metadata_json"])
    previous_metadata = dict(metadata)
    metadata.setdefault("previous_display_name", OLD_DISPLAY_NAME)
    metadata.setdefault("display_name_amended_at", timestamp)
    metadata["display_name_amendment"] = "owner_approved"
    if str(subject["display_name"]) != NEW_DISPLAY_NAME or metadata != previous_metadata:
        connection.execute(
            """
            UPDATE platform_subjects
            SET display_name = ?, metadata_json = ?, updated_at = ?
            WHERE tenant_id = ? AND subject_id = ?
            """,
            (
                NEW_DISPLAY_NAME,
                canonical_json(metadata),
                timestamp,
                tenant_id,
                SUBJECT_ID,
            ),
        )
    connection.execute(
        """
        UPDATE derived_portfolios SET name = ?, updated_at = ?
        WHERE tenant_id = ? AND portfolio_id = ?
          AND name <> ?
        """,
        (NEW_DISPLAY_NAME, timestamp, tenant_id, SUBJECT_ID, NEW_DISPLAY_NAME),
    )
    connection.execute(
        """
        UPDATE synthetic_portfolio_components
        SET component_name = CASE component_name
            WHEN ? THEN ?
            WHEN ? THEN ?
            ELSE component_name
        END
        WHERE tenant_id = ? AND component_id = ?
          AND component_name IN (?, ?)
        """,
        (
            OLD_DISPLAY_NAME,
            NEW_DISPLAY_NAME,
            f"{OLD_DISPLAY_NAME} portfolio",
            f"{NEW_DISPLAY_NAME} portfolio",
            tenant_id,
            SUBJECT_ID,
            OLD_DISPLAY_NAME,
            f"{OLD_DISPLAY_NAME} portfolio",
        ),
    )
    presentation_approval_id = _amend_presentation_identity(
        connection, tenant_id=tenant_id, changed_at=timestamp
    )
    return {
        "status": "reconciled",
        "subject_id": SUBJECT_ID,
        "display_name": NEW_DISPLAY_NAME,
        "calculation_definitions": {
            key: {"version": value["version"], "sha256": value["sha256"]}
            for key, value in definitions.items()
        },
        "presentation_approval_id": presentation_approval_id,
    }
