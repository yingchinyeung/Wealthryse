"""Switching a reviewed subject on for viewers and the scheduled jobs.

Both the original onboarding wizard's final activation and the new onboarding's
go-live approval call activate_subject once their own approvals are recorded and
the subject's lifecycle is active. It turns on the subject's website features,
activates its pending Flex source binding, registers its operating capabilities
(daily ingestion, benchmarks, sectors, month-end reports, analyst assurance and
the viewer directory) and enables the Flex schedule that feeds it.
"""

from __future__ import annotations

import uuid

from .util import canonical_json, sha256_text

DERIVED_SUBJECT_TYPES = frozenset({"filtered_portfolio", "composite_portfolio"})


def operating_capabilities(*, subject_type: str, source_name: str, history_start_date: str,
                           administrative_master: bool) -> tuple[tuple[str, dict], ...]:
    """The capabilities a subject runs with once it is active."""

    if administrative_master:
        return (("viewer_directory", {}),)
    source = "derived_calculation" if subject_type in DERIVED_SUBJECT_TYPES else source_name
    return (
        ("daily_ingestion", {"start_date": history_start_date, "source": source}),
        ("benchmark_refresh", {"start_date": history_start_date}),
        ("sector_refresh", {"external_approval_required": True}),
        ("month_end_reporting", {"approval_required": True}),
        ("analyst_assurance", {}),
        ("viewer_directory", {}),
    )


def activate_subject(connection, *, tenant_id: str, subject_id: str, subject_type: str, source_name: str,
                     history_start_date: str, administrative_master: bool, approval_id: uuid.UUID | str | None,
                     actor: str) -> None:
    """Turn an approved subject's features, sources and capabilities on.

    approval_id is the wizard's account_onboarding_approvals row. The new
    onboarding keeps its go-live approval in broker_onboarding_approvals and
    passes None.
    """

    connection.execute(
        """
        UPDATE subject_feature_states
        SET feature_state = CASE
                WHEN %s AND feature_key <> 'viewer_access' THEN 'disabled'
                ELSE 'active'
            END,
            reason = CASE
                WHEN %s AND feature_key <> 'viewer_access'
                THEN 'This administrative-only master does not publish investment analytics or reports.'
                ELSE 'Every account-specific onboarding gate passed and the owner activated the subject.'
            END,
            updated_at = now()
        WHERE tenant_id = %s AND subject_id = %s
        """,
        (administrative_master, administrative_master, tenant_id, subject_id),
    )
    connection.execute(
        """
        UPDATE subject_flex_source_bindings
        SET status = 'active', updated_at = now()
        WHERE tenant_id = %s AND subject_id = %s AND status = 'pending'
        """,
        (tenant_id, subject_id),
    )
    for capability, configuration in operating_capabilities(
        subject_type=subject_type, source_name=source_name, history_start_date=history_start_date,
        administrative_master=administrative_master,
    ):
        configuration_json = canonical_json(configuration)
        connection.execute(
            """
            INSERT INTO subject_operating_registrations(
                registration_id, tenant_id, subject_id, capability,
                operating_state, effective_from, configuration_json,
                configuration_sha256, activation_approval_id,
                changed_by_identity_sha256, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, 'active', %s, %s::jsonb, %s,
                %s, %s, now(), now()
            )
            ON CONFLICT (tenant_id, subject_id, capability, effective_from)
            DO UPDATE SET operating_state = 'active', effective_to = NULL,
                          configuration_json = EXCLUDED.configuration_json,
                          configuration_sha256 = EXCLUDED.configuration_sha256,
                          activation_approval_id = EXCLUDED.activation_approval_id,
                          changed_by_identity_sha256 = EXCLUDED.changed_by_identity_sha256,
                          updated_at = now()
            """,
            (
                uuid.uuid4(), tenant_id, subject_id, capability,
                history_start_date, configuration_json,
                sha256_text(configuration_json), approval_id, actor,
            ),
        )
    connection.execute(
        """
        UPDATE ibkr_flex_queries q
        SET schedule_enabled = true, status = 'active', updated_at = now()
        WHERE q.tenant_id = %s
          AND EXISTS (
              SELECT 1 FROM subject_flex_source_bindings b
              WHERE b.tenant_id = q.tenant_id
                AND b.flex_source_id = q.flex_source_id
                AND b.subject_id = %s AND b.status = 'active'
          )
        """,
        (tenant_id, subject_id),
    )
