"""Stage 3's agent_review_complete check, recorded from the history reviewer's findings.

The history reviewer (wealthryse_agent.onboarding_review) reads a composite
case's rendered historical reports and writes its review to
agent.onboarding_reviews; the agent cannot write any platform table. This
module runs on the platform side, in the month-end report job after the
composite reports, and records each review on its case through
onboarding_v2_web.record_platform_check, as every other stage 3 check is
recorded.

A review counts only for the exact reports it read: it carries the digest of
the reports_rendered evidence, and when the reports are rendered again the
check goes back to pending until the reviewer has read the new ones. Approval
2 is bound to this check's evidence, so the owner always approves reports
that were reviewed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .onboarding_v2_web import OnboardingError, record_platform_check

CHECK_ID = "agent_review_complete"
_MAX_FINDINGS = 50


def reports_digest(evidence: Mapping[str, Any]) -> str:
    """The digest a review is bound to: the reports_rendered evidence, canonical JSON."""

    text = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _cases(connection, tenant_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT c.case_id, r.evidence_json AS reports_evidence,
               a.status AS recorded, a.message AS recorded_message, a.evidence_json AS recorded_evidence
        FROM broker_onboarding_cases c
        JOIN broker_onboarding_checks r
          ON r.tenant_id = c.tenant_id AND r.case_id = c.case_id AND r.check_id = 'reports_rendered'
        LEFT JOIN broker_onboarding_checks a
          ON a.tenant_id = c.tenant_id AND a.case_id = c.case_id AND a.check_id = 'agent_review_complete'
        WHERE c.tenant_id = ? AND c.source_kind = 'composite_portfolio' AND c.report_style IS NOT NULL
          AND c.stage = 'build_history' AND c.status <> 'cancelled' AND r.status = 'pass'
        ORDER BY c.created_at
        """,
        (tenant_id,),
    ).fetchall()
    return [{**dict(row), "case_id": str(row["case_id"])} for row in rows]


def _review(connection, tenant_id: str, case_id: str, digest: str) -> Mapping[str, Any] | None:
    return connection.execute(
        "SELECT review_id, outcome, summary, findings, evidence, created_at FROM agent.onboarding_reviews "
        "WHERE tenant_id = ? AND case_id = CAST(? AS uuid) AND reports_sha256 = ?",
        (tenant_id, case_id, digest),
    ).fetchone()


def check_for(review: Mapping[str, Any] | None, digest: str) -> tuple[str, str, dict]:
    """The check a review gives, or pending while the reviewer has not read these reports yet."""

    if review is None:
        return ("pending", "Waiting for the agent review of the rendered reports.",
                {"reviewed_reports_sha256": digest, "review_id": None})
    findings = list(_json(review["findings"], []))
    evidence = {
        "reviewed_reports_sha256": digest,
        "review_id": int(review["review_id"]),
        "reviewed_at": str(review["created_at"]),
        "checked": _json(review["evidence"], {}),
        "findings": findings[:_MAX_FINDINGS],
        "more_findings": max(0, len(findings) - _MAX_FINDINGS),
    }
    return str(review["outcome"]), str(review["summary"])[:1000], evidence


def record_agent_reviews(connection, tenant_id: str) -> list[dict]:
    """Record each waiting case's review, or pending. One case's failure never stops the others."""

    results = []
    for case in _cases(connection, tenant_id):
        case_id = case["case_id"]
        digest = reports_digest(_json(case["reports_evidence"], {}))
        status, message, evidence = check_for(_review(connection, tenant_id, case_id, digest), digest)
        recorded = (case["recorded"], case["recorded_message"], _json(case["recorded_evidence"], None))
        # An unchanged result stays as recorded, so approval 2 is not reset every day.
        if (status, message, evidence) == recorded:
            connection.rollback()
            results.append({"case_id": case_id, "check": status, "recorded": False})
            continue
        try:
            record_platform_check(connection, tenant_id, case_id, CHECK_ID, status, message, evidence)
            connection.commit()
            results.append({"case_id": case_id, "check": status, "recorded": True})
        except (OnboardingError, ValueError) as error:
            connection.rollback()
            results.append({"case_id": case_id, "error": str(error)[:400]})
    return results
