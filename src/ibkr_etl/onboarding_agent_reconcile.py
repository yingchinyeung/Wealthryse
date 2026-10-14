"""Stage 2's four checks for a broker account, recorded from the reconciliation agent's results.

The reconciliation agent (wealthryse_agent.onboarding_reconcile) checks a new
IBKR account's loaded holdings, cash, transactions, securities and NAV and
writes its result to agent.onboarding_reconciliations; the agent cannot write
any platform table. This module runs on the platform side and records the
newest result on its case through onboarding_v2_web.record_platform_check, as
every other stage check is recorded, with "recorded_by": "agent" and the
result's evidence.

A result counts only while it is recent: the account's data changes every
day, so a result older than MAX_AGE_HOURS is not recorded, and the checks keep
what they last showed until the agent runs again.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from .onboarding_v2_web import OnboardingError, record_platform_check

CHECK_IDS = ("holdings_and_cash_loaded", "transactions_loaded", "securities_mapped", "nav_reconciled")
MAX_AGE_HOURS = 36


def _json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    return json.loads(value) if isinstance(value, str) else value


def _cases(connection, tenant_id: str) -> list[dict]:
    rows = connection.execute(
        """
        SELECT c.case_id, r.reconciliation_id, r.checks, r.proposals, r.created_at
        FROM broker_onboarding_cases c
        JOIN LATERAL (
            SELECT reconciliation_id, checks, proposals, created_at FROM agent.onboarding_reconciliations a
            WHERE a.tenant_id = c.tenant_id AND a.case_id = c.case_id
              AND a.created_at > now() - make_interval(hours => ?)
            ORDER BY a.created_at DESC, a.reconciliation_id DESC LIMIT 1
        ) r ON TRUE
        WHERE c.tenant_id = ? AND c.source_kind = 'broker_account' AND c.broker = 'ibkr_flex'
          AND c.stage = 'load_and_reconcile' AND c.status <> 'cancelled'
        ORDER BY c.created_at
        """,
        (MAX_AGE_HOURS, tenant_id),
    ).fetchall()
    return [{**dict(row), "case_id": str(row["case_id"])} for row in rows]


def _recorded(connection, tenant_id: str, case_id: str) -> dict[str, tuple]:
    rows = connection.execute(
        "SELECT check_id, status, message, evidence_json FROM broker_onboarding_checks "
        "WHERE tenant_id = ? AND case_id = ?",
        (tenant_id, case_id),
    ).fetchall()
    return {str(r["check_id"]): (r["status"], r["message"], _json(r["evidence_json"], None)) for r in rows}


def check_records(result: Mapping[str, Any]) -> dict[str, tuple[str, str, dict]]:
    """Each check as the case will show it: status, message and the agent's evidence."""
    checks = _json(result["checks"], {})
    proposals = _json(result.get("proposals"), [])
    records = {}
    for check_id in CHECK_IDS:
        item = checks.get(check_id)
        if not item or item.get("status") not in ("pass", "warn", "fail"):
            continue
        evidence = {"recorded_by": "agent", "reconciliation_id": int(result["reconciliation_id"]),
                    "checked_at": str(result["created_at"]), **dict(item.get("evidence") or {})}
        if check_id == "securities_mapped":
            evidence["proposals"] = proposals[:200]
        records[check_id] = (str(item["status"]), str(item.get("message") or "")[:1000], evidence)
    return records


def record_agent_reconciliations(connection, tenant_id: str) -> list[dict]:
    """Record each stage 2 broker case's newest result. One case's failure never stops the others."""

    results = []
    for case in _cases(connection, tenant_id):
        case_id = case["case_id"]
        recorded = _recorded(connection, tenant_id, case_id)
        connection.rollback()
        changed = {k: v for k, v in check_records(case).items() if recorded.get(k) != v}
        try:
            # Checks that do not pass go first: once all four pass the case may move on, and a later check could
            # then no longer be recorded in this stage.
            for check_id, (status, message, evidence) in sorted(changed.items(), key=lambda i: i[1][0] == "pass"):
                record_platform_check(connection, tenant_id, case_id, check_id, status, message, evidence)
            connection.commit()
            results.append({"case_id": case_id, "recorded": sorted(changed)})
        except (OnboardingError, ValueError) as error:
            connection.rollback()
            results.append({"case_id": case_id, "error": str(error)[:400]})
    return results
