from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

from .util import canonical_json


REVIEW_CONFIRMATION_PREFIX = "APPROVE MONTHLY AUTHORITY REVIEW"


def previous_month(reference: date) -> tuple[date, date, str]:
    first = reference.replace(day=1)
    end = first.fromordinal(first.toordinal() - 1)
    start = end.replace(day=1)
    return start, end, start.strftime("%Y-%m")


def next_review_due(reference: date) -> datetime:
    year = reference.year + (1 if reference.month == 12 else 0)
    month = 1 if reference.month == 12 else reference.month + 1
    return datetime(year, month, 15, 23, 59, 59, tzinfo=timezone.utc)


def review_confirmation_phrase(review_month: str) -> str:
    return f"{REVIEW_CONFIRMATION_PREFIX} {review_month}"


def build_review_summary(rows: Mapping[str, Any], *, policy_version: str) -> dict[str, Any]:
    """Return a fixed-schema summary; no log bodies, credentials or portfolio data."""
    return {
        "policy_version": policy_version,
        "supervisor_runs": int(rows.get("supervisor_runs") or 0),
        "healthy_runs": int(rows.get("healthy_runs") or 0),
        "failed_runs": int(rows.get("failed_runs") or 0),
        "incidents_opened": int(rows.get("incidents_opened") or 0),
        "autonomous_actions": int(rows.get("autonomous_actions") or 0),
        "successful_actions": int(rows.get("successful_actions") or 0),
        "rollback_actions": int(rows.get("rollback_actions") or 0),
        "model_assisted_runs": int(rows.get("model_assisted_runs") or 0),
    }


def create_monthly_review(connection, *, today: date, created_at: str) -> dict[str, Any]:
    start, end, review_month = previous_month(today)
    stats = connection.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM autonomous_agent_runs
            WHERE started_at::timestamptz >= %s::date AND started_at::timestamptz < (%s::date + 1)) AS supervisor_runs,
          (SELECT COUNT(*) FROM autonomous_agent_runs
            WHERE status = 'healthy' AND started_at::timestamptz >= %s::date
              AND started_at::timestamptz < (%s::date + 1)) AS healthy_runs,
          (SELECT COUNT(*) FROM autonomous_agent_runs
            WHERE status = 'failed' AND started_at::timestamptz >= %s::date
              AND started_at::timestamptz < (%s::date + 1)) AS failed_runs,
          (SELECT COUNT(*) FROM system_incidents
            WHERE first_seen_at::timestamptz >= %s::date AND first_seen_at::timestamptz < (%s::date + 1)) AS incidents_opened,
          (SELECT COUNT(*) FROM autonomous_actions
            WHERE created_at::timestamptz >= %s::date AND created_at::timestamptz < (%s::date + 1)) AS autonomous_actions,
          (SELECT COUNT(*) FROM autonomous_actions
            WHERE status = 'executed' AND created_at::timestamptz >= %s::date
              AND created_at::timestamptz < (%s::date + 1)) AS successful_actions,
          (SELECT COUNT(*) FROM autonomous_actions
            WHERE action_type = 'rollback_web_to_last_known_good'
              AND created_at::timestamptz >= %s::date AND created_at::timestamptz < (%s::date + 1)) AS rollback_actions,
          (SELECT COUNT(*) FROM autonomous_agent_runs
            WHERE model_used AND started_at::timestamptz >= %s::date
              AND started_at::timestamptz < (%s::date + 1)) AS model_assisted_runs
        """,
        tuple(value for _ in range(8) for value in (start.isoformat(), end.isoformat())),
    ).fetchone()
    control = connection.execute(
        "SELECT policy_version FROM autonomy_control WHERE tenant_id = app_tenant_id()"
    ).fetchone()
    policy_version = str(control["policy_version"] if control else "unknown")
    summary = build_review_summary(stats or {}, policy_version=policy_version)
    due_at = datetime(today.year, today.month, 15, 23, 59, 59, tzinfo=timezone.utc)
    row = connection.execute(
        """
        INSERT INTO autonomy_authority_reviews(
            review_month, period_start, period_end, due_at, status, summary_json, created_at
        ) VALUES (%s, %s, %s, %s, 'awaiting_owner_review', %s, %s)
        ON CONFLICT (tenant_id, review_month) DO UPDATE SET
            summary_json = CASE WHEN autonomy_authority_reviews.status = 'approved'
                                THEN autonomy_authority_reviews.summary_json
                                ELSE excluded.summary_json END,
            due_at = CASE WHEN autonomy_authority_reviews.status = 'approved'
                          THEN autonomy_authority_reviews.due_at ELSE excluded.due_at END,
            created_at = CASE WHEN autonomy_authority_reviews.status = 'approved'
                              THEN autonomy_authority_reviews.created_at ELSE excluded.created_at END
        RETURNING review_id, review_month, status, due_at, summary_json
        """,
        (
            review_month,
            start.isoformat(),
            end.isoformat(),
            due_at.isoformat(),
            canonical_json(summary),
            created_at,
        ),
    ).fetchone()
    connection.execute(
        """
        UPDATE autonomy_authority_reviews SET status = 'overdue'
        WHERE status = 'awaiting_owner_review' AND due_at::timestamptz < %s::timestamptz
        """,
        (created_at,),
    )
    connection.execute(
        "UPDATE autonomy_control SET monthly_review_due_at = %s, updated_at = %s, updated_by = 'monthly-authority-review-job'",
        (due_at.isoformat(), created_at),
    )
    return dict(row)
