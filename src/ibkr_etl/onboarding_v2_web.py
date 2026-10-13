"""Website side of the new account onboarding, behind ONBOARDING_V2_ENABLED.

Pages: Account onboarding (cases in progress, accounts available to onboard,
live accounts), one page per case, and Broker connections. When the switch is
off every route here answers 404 and the existing pages are unchanged.

Database work goes through the functions below, which take a psycopg
connection with dict rows; the routes only check who is asking. Secrets reach
Key Vault through the existing write-only credential broker and are never
stored, logged or returned.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any, Callable, Iterable, Literal, Mapping

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .broker_connections import (
    BROKERS,
    HEALTH_LABELS,
    connection_health,
    display_account_id,
    ip_lock_state,
    new_connection_record,
    normalize_account_id,
    onboarding_v2_enabled,
    validate_expiry,
    validate_ip_lock,
)
from .flex_recipe import RECIPE_STEPS
from .onboarding_cases import (
    APPROVAL_LABELS,
    CHECK_LABELS,
    PRE_LIVE_STAGES,
    STAGE_CHECKS,
    STAGE_LABELS,
    OnboardingCase,
    advance,
    approve,
    assert_can_start,
    cancel,
    case_status,
    go_live_phrase,
    missing_approvals,
    next_owner_action,
    progress,
    record_check,
    stage_check_states,
    terms_digest,
)

# IBKR connection status as the existing Flex validation records it.
_FLEX_STATUS = {
    "pending_validation": "pending_check",
    "rotating": "pending_check",
    "active": "healthy",
    "suspended": "failing",
    "expired": "failing",
    "revoked": "retired",
}
# Approvals 2 and 3 arrive with the history build; until then they are refused.
AVAILABLE_APPROVALS = frozenset({"account_terms"})


class OnboardingError(ValueError):
    """A refusal the website shows to the person as it is."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def subject_id_for(broker: str, account_id: str) -> str:
    return account_id if broker == "ibkr_flex" else f"KIWOOM-{account_id}"


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# Cases ---------------------------------------------------------------------


def _case_from_rows(row: Mapping[str, Any], checks: Iterable[Mapping[str, Any]],
                    approvals: Iterable[Mapping[str, Any]]) -> OnboardingCase:
    check_rows = list(checks)
    return OnboardingCase(
        case_id=str(row["case_id"]),
        broker=str(row["broker"]),
        broker_account_id=str(row["broker_account_id"]),
        stage=str(row["stage"]),
        cancelled=row["status"] == "cancelled",
        checks={str(item["check_id"]): str(item["status"]) for item in check_rows},
        approvals=frozenset(str(item["approval_kind"]) for item in approvals),
        acknowledged_warnings=frozenset(
            str(item["check_id"]) for item in check_rows if item.get("warning_acknowledged_at")
        ),
    )


def _load_case(connection, tenant_id: str, case_id: str, *, lock: bool = False):
    row = connection.execute(
        "SELECT * FROM broker_onboarding_cases WHERE tenant_id = %s AND case_id = %s"
        + (" FOR UPDATE" if lock else ""),
        (tenant_id, case_id),
    ).fetchone()
    if row is None:
        raise OnboardingError("Onboarding case not found", 404)
    checks = connection.execute(
        "SELECT check_id, status, message, evidence_json, warning_acknowledged_at, checked_at "
        "FROM broker_onboarding_checks WHERE tenant_id = %s AND case_id = %s",
        (tenant_id, case_id),
    ).fetchall()
    approvals = connection.execute(
        "SELECT approval_kind, terms_sha256, approved_at FROM broker_onboarding_approvals "
        "WHERE tenant_id = %s AND case_id = %s ORDER BY approved_at",
        (tenant_id, case_id),
    ).fetchall()
    return row, checks, approvals, _case_from_rows(row, checks, approvals)


def _event(connection, tenant_id: str, case_id: str, event_type: str, from_stage: str,
           to_stage: str, actor: str | None, detail: Mapping[str, object] | None = None) -> None:
    connection.execute(
        "INSERT INTO broker_onboarding_events (event_id, tenant_id, case_id, event_type, from_stage, "
        "to_stage, actor_identity_sha256, detail_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
        (uuid.uuid4(), tenant_id, case_id, event_type, from_stage, to_stage, actor, _json(detail or {})),
    )


def _save_progress(connection, tenant_id: str, case: OnboardingCase, actor: str | None) -> OnboardingCase:
    """Advance as far as checks and approvals allow, then store stage and status."""

    while True:
        moved, event = advance(case)
        if event is None:
            break
        _event(connection, tenant_id, case.case_id, "advanced", event.from_stage, event.to_stage, actor)
        case = moved
    connection.execute(
        "UPDATE broker_onboarding_cases SET stage = %s, status = %s, updated_at = now(), "
        "went_live_at = CASE WHEN %s IN ('first_month_end', 'closed') THEN coalesce(went_live_at, now()) END, "
        "closed_at = CASE WHEN %s = 'closed' THEN coalesce(closed_at, now()) END "
        "WHERE tenant_id = %s AND case_id = %s",
        (case.stage, case_status(case), case.stage, case.stage, tenant_id, case.case_id),
    )
    return case


def _account_terms(row: Mapping[str, Any]) -> dict[str, object]:
    broker = str(row["broker"])
    return {
        "broker": broker,
        "broker_account_id": str(row["broker_account_id"]),
        "subject_id": subject_id_for(broker, str(row["broker_account_id"])),
        "display_name": str(row["display_name"]),
        "reporting_currency": str(row["reporting_currency"]),
        "history_start_date": str(row["history_start_date"]),
    }


def _already_onboarded(connection, tenant_id: str, broker: str, account_id: str) -> bool:
    subject = connection.execute(
        "SELECT 1 FROM platform_subjects WHERE tenant_id = %s AND (subject_id = %s OR source_account_id = %s)",
        (tenant_id, subject_id_for(broker, account_id), account_id),
    ).fetchone()
    if subject is not None:
        return True
    if broker != "ibkr_flex":
        return False
    legacy = connection.execute(
        "SELECT 1 FROM account_onboarding_cases WHERE tenant_id = %s AND source_account_id = %s "
        "AND status <> 'cancelled'",
        (tenant_id, account_id),
    ).fetchone()
    return legacy is not None


def start_case(connection, tenant_id: str, *, connection_id: str, broker_account_id: str, display_name: str,
               reporting_currency: str, history_start_date: date, actor: str | None, today: date) -> dict:
    """Open a case for one discovered account. One account at a time."""

    source = connection.execute(
        "SELECT connection_id, broker, status FROM broker_connections WHERE tenant_id = %s AND connection_id = %s",
        (tenant_id, connection_id),
    ).fetchone()
    if source is None:
        raise OnboardingError("Broker connection not found", 404)
    broker = str(source["broker"])
    if source["status"] in {"suspended", "retired"}:
        raise OnboardingError("This connection is paused; renew or replace it first")
    try:
        account_id = normalize_account_id(broker, broker_account_id)
    except ValueError as error:
        raise OnboardingError(str(error), 400) from error
    name = " ".join(str(display_name or "").split())
    if not 3 <= len(name) <= 160:
        raise OnboardingError("Display name must contain 3 to 160 characters", 400)
    currency = str(reporting_currency or "").strip().upper()
    if len(currency) != 3 or not currency.isalpha():
        raise OnboardingError("Reporting currency must be a three-letter code", 400)
    if history_start_date > today:
        raise OnboardingError("History cannot start in the future", 400)

    seen = _connection_account(connection, tenant_id, source, account_id)
    if seen is None:
        raise OnboardingError("This connection has not found that account", 404)
    open_rows = connection.execute(
        "SELECT case_id, broker, broker_account_id, stage, status FROM broker_onboarding_cases "
        "WHERE tenant_id = %s AND status <> 'cancelled' FOR UPDATE",
        (tenant_id,),
    ).fetchall()
    open_cases = [_case_from_rows(row, (), ()) for row in open_rows]
    try:
        assert_can_start(broker=broker, broker_account_id=account_id, open_cases=open_cases)
    except ValueError as error:
        raise OnboardingError(str(error)) from error
    if _already_onboarded(connection, tenant_id, broker, account_id):
        raise OnboardingError("This account is already on the platform")

    case_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO broker_onboarding_cases (case_id, tenant_id, connection_id, broker, broker_account_id, "
        "display_name, reporting_currency, history_start_date, stage, status, created_by_identity_sha256) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'request', 'platform_working', %s)",
        (case_id, tenant_id, connection_id, broker, account_id, name, currency, history_start_date, actor),
    )
    _event(connection, tenant_id, case_id, "created", "request", "request", actor,
           {"broker": broker, "connection_id": str(connection_id)})
    connection.execute(
        "UPDATE broker_connection_accounts SET discovery_status = 'case_started' "
        "WHERE tenant_id = %s AND connection_id = %s AND broker_account_id = %s",
        (tenant_id, connection_id, account_id),
    )
    case = OnboardingCase(case_id=case_id, broker=broker, broker_account_id=account_id)
    # IBKR accounts come from a statement the connection already downloaded;
    # a Kiwoom account is confirmed by the collector's first call.
    seen_message = ("Found in this login's Flex statement." if broker == "ibkr_flex"
                    else "Waiting for the first call from the platform's fixed address.")
    case = _write_check(connection, tenant_id, case, "connection_sees_account",
                        "pass" if broker == "ibkr_flex" else "pending", seen_message, {}, actor)
    case = _write_check(connection, tenant_id, case, "account_not_already_onboarded", "pass",
                        "No live account or earlier case uses this account.", {}, actor)
    case = _save_progress(connection, tenant_id, case, actor)
    return {"case_id": case_id, "stage": case.stage, "status": case_status(case),
            "next_page": f"/account-onboarding/cases/{case_id}"}


def _write_check(connection, tenant_id: str, case: OnboardingCase, check_id: str, status: str, message: str,
                 evidence: Mapping[str, object], actor: str | None) -> OnboardingCase:
    case = record_check(case, check_id, status)
    connection.execute(
        "INSERT INTO broker_onboarding_checks (tenant_id, case_id, check_id, status, message, evidence_json) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb) ON CONFLICT (tenant_id, case_id, check_id) DO UPDATE SET "
        "status = EXCLUDED.status, message = EXCLUDED.message, evidence_json = EXCLUDED.evidence_json, "
        "warning_acknowledged_at = NULL, checked_at = now()",
        (tenant_id, case.case_id, check_id, status, message[:1000], _json(evidence)),
    )
    _event(connection, tenant_id, case.case_id, "check_recorded", case.stage, case.stage, actor,
           {"check_id": check_id, "status": status})
    # A re-recorded check needs a fresh acknowledgement if it warns again.
    return OnboardingCase(case.case_id, case.broker, case.broker_account_id, case.stage, case.cancelled,
                          case.checks, case.approvals, case.acknowledged_warnings - {check_id})


def record_platform_check(connection, tenant_id: str, case_id: str, check_id: str, status: str, message: str,
                          evidence: Mapping[str, object] | None = None) -> dict:
    """What the collectors and history build call to report a stage check."""

    _, _, _, case = _load_case(connection, tenant_id, case_id, lock=True)
    try:
        case = _write_check(connection, tenant_id, case, check_id, status, message, evidence or {}, None)
    except ValueError as error:
        raise OnboardingError(str(error)) from error
    case = _save_progress(connection, tenant_id, case, None)
    return {"case_id": case_id, "stage": case.stage, "status": case_status(case)}


def approve_case(connection, tenant_id: str, case_id: str, *, approval: str, terms_sha256: str,
                 acknowledge_warnings: Iterable[str], confirmation: str | None, actor: str) -> dict:
    if approval not in AVAILABLE_APPROVALS:
        raise OnboardingError(f"{APPROVAL_LABELS.get(approval, approval)} arrives with the history build")
    row, _, _, case = _load_case(connection, tenant_id, case_id, lock=True)
    terms = _account_terms(row)
    if terms_digest(terms) != terms_sha256:
        raise OnboardingError("The terms changed after this page loaded; reload and review them again")
    acknowledged = [item for item in acknowledge_warnings if item in STAGE_CHECKS[case.stage]]
    try:
        case, event = approve(case, approval, acknowledge_warnings=acknowledged, confirmation=confirmation,
                              broker_account_display=display_account_id(case.broker, case.broker_account_id))
    except ValueError as error:
        raise OnboardingError(str(error)) from error
    for check_id in acknowledged:
        connection.execute(
            "UPDATE broker_onboarding_checks SET warning_acknowledged_at = now() "
            "WHERE tenant_id = %s AND case_id = %s AND check_id = %s AND status = 'warn'",
            (tenant_id, case_id, check_id),
        )
        _event(connection, tenant_id, case_id, "warning_acknowledged", case.stage, case.stage, actor,
               {"check_id": check_id})
    if approval == "account_terms":
        _register_subject(connection, tenant_id, row, terms)
    connection.execute(
        "INSERT INTO broker_onboarding_approvals (tenant_id, case_id, approval_kind, terms_json, terms_sha256, "
        "approved_by_identity_sha256) VALUES (%s, %s, %s, %s::jsonb, %s, %s)",
        (tenant_id, case_id, approval, _json(terms), terms_sha256, actor),
    )
    _event(connection, tenant_id, case_id, "approved", event.from_stage, event.to_stage, actor,
           {"approval": approval, "terms_sha256": terms_sha256})
    case = _save_progress(connection, tenant_id, case, actor)
    return {"case_id": case_id, "stage": case.stage, "status": case_status(case)}


def _register_subject(connection, tenant_id: str, row: Mapping[str, Any], terms: Mapping[str, object]) -> None:
    """Approval 1 registers the account privately; viewers see nothing until go-live."""

    account = connection.execute(
        "SELECT base_currency, account_name FROM broker_connection_accounts "
        "WHERE tenant_id = %s AND connection_id = %s AND broker_account_id = %s",
        (tenant_id, row["connection_id"], row["broker_account_id"]),
    ).fetchone() or {}
    subject_id = str(terms["subject_id"])
    connection.execute(
        "INSERT INTO platform_subjects (tenant_id, subject_id, subject_type, display_name, source_account_id, "
        "base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status, metadata_json) "
        "VALUES (%s, %s, 'physical_account', %s, %s, %s, %s, %s, %s, 'onboarding', %s::jsonb)",
        (tenant_id, subject_id, terms["display_name"], row["broker_account_id"],
         account.get("base_currency") or terms["reporting_currency"], terms["reporting_currency"],
         terms["history_start_date"], terms["history_start_date"],
         _json({"onboarding": "broker_onboarding_v2", "broker": row["broker"], "case_id": str(row["case_id"])})),
    )
    connection.execute(
        "UPDATE broker_onboarding_cases SET subject_id = %s WHERE tenant_id = %s AND case_id = %s",
        (subject_id, tenant_id, row["case_id"]),
    )


def cancel_case(connection, tenant_id: str, case_id: str, *, reason: str, actor: str) -> dict:
    row, _, _, case = _load_case(connection, tenant_id, case_id, lock=True)
    try:
        case, event = cancel(case, reason=reason)
    except ValueError as error:
        raise OnboardingError(str(error)) from error
    connection.execute(
        "UPDATE broker_onboarding_cases SET status = 'cancelled', updated_at = now() "
        "WHERE tenant_id = %s AND case_id = %s",
        (tenant_id, case_id),
    )
    connection.execute(
        "UPDATE broker_connection_accounts SET discovery_status = 'discovered' "
        "WHERE tenant_id = %s AND connection_id = %s AND broker_account_id = %s",
        (tenant_id, row["connection_id"], row["broker_account_id"]),
    )
    if row["subject_id"]:
        connection.execute(
            "UPDATE platform_subjects SET lifecycle_status = 'archived', updated_at = now() "
            "WHERE tenant_id = %s AND subject_id = %s AND lifecycle_status = 'onboarding'",
            (tenant_id, row["subject_id"]),
        )
    _event(connection, tenant_id, case_id, "cancelled", event.from_stage, event.to_stage, actor, event.detail)
    return {"case_id": case_id, "status": "cancelled"}


def case_detail(connection, tenant_id: str, case_id: str) -> dict:
    row, checks, approvals, case = _load_case(connection, tenant_id, case_id)
    by_id = {str(item["check_id"]): item for item in checks}
    account_display = display_account_id(case.broker, case.broker_account_id)
    terms = _account_terms(row)
    stage_number = min(list(STAGE_LABELS).index(case.stage) + 1, 4)
    return {
        "case_id": case.case_id,
        "broker": case.broker,
        "broker_label": BROKERS[case.broker].label,
        "account": account_display,
        "display_name": row["display_name"],
        "reporting_currency": row["reporting_currency"],
        "history_start_date": str(row["history_start_date"]),
        "subject_id": row["subject_id"],
        "stage": case.stage,
        "stage_label": STAGE_LABELS[case.stage],
        "stage_number": stage_number,
        "status": case_status(case),
        "next_owner_action": next_owner_action(case),
        "awaiting_approval": missing_approvals(case)[0] if case_status(case) == "waiting_on_owner" else None,
        "progress": progress(case),
        "checks": [
            {
                "check_id": check_id,
                "label": CHECK_LABELS[check_id],
                "status": state,
                "message": str(by_id[check_id]["message"]) if check_id in by_id else "Not run yet.",
                "acknowledged": check_id in case.acknowledged_warnings,
            }
            for check_id, state in stage_check_states(case).items()
        ],
        "approvals": [
            {
                "kind": kind,
                "label": label,
                "approved": kind in case.approvals,
                "available": kind in AVAILABLE_APPROVALS,
            }
            for kind, label in APPROVAL_LABELS.items()
        ],
        "account_terms": terms,
        "account_terms_sha256": terms_digest(terms),
        "go_live_phrase": go_live_phrase(account_display),
        "can_cancel": case.stage in PRE_LIVE_STAGES and not case.cancelled,
    }


# Connections and accounts --------------------------------------------------


def _connection_account(connection, tenant_id: str, source: Mapping[str, Any], account_id: str):
    """The account row for a connection, copied from Flex discovery for IBKR when first needed."""

    row = connection.execute(
        "SELECT * FROM broker_connection_accounts WHERE tenant_id = %s AND connection_id = %s "
        "AND broker_account_id = %s",
        (tenant_id, source["connection_id"], account_id),
    ).fetchone()
    if row is not None or source["broker"] != "ibkr_flex":
        return row
    found = connection.execute(
        "SELECT a.account_name, a.base_currency FROM ibkr_flex_source_accounts a "
        "JOIN ibkr_flex_queries q ON q.tenant_id = a.tenant_id AND q.flex_source_id = a.flex_source_id "
        "WHERE a.tenant_id = %s AND q.credential_id = %s AND a.source_account_id = %s "
        "AND a.discovery_status NOT IN ('not_portfolio', 'ignored') LIMIT 1",
        (tenant_id, source["connection_id"], account_id),
    ).fetchone()
    if found is None:
        return None
    return connection.execute(
        "INSERT INTO broker_connection_accounts (tenant_id, connection_id, broker_account_id, account_name, "
        "base_currency, discovery_status) VALUES (%s, %s, %s, %s, %s, 'discovered') RETURNING *",
        (tenant_id, source["connection_id"], account_id, found["account_name"], found["base_currency"]),
    ).fetchone()


def _health(row: Mapping[str, Any], *, today: date, platform_ip: str | None) -> str:
    stored = str(row["status"])
    if row["broker"] == "ibkr_flex" and row.get("flex_status"):
        stored = _FLEX_STATUS.get(str(row["flex_status"]), stored) if stored not in {"suspended", "retired"} else stored
    locked = str(row["ip_locked_to"]) if row.get("ip_locked_to") else None
    return connection_health(stored_status=stored, expires_on=row.get("credential_expires_on"), today=today,
                             ip_lock=ip_lock_state(locked, platform_ip))


def connections_overview(connection, tenant_id: str, *, today: date, platform_ip: str | None) -> dict:
    rows = connection.execute(
        "SELECT c.*, f.status AS flex_status, "
        "(SELECT q.flex_source_id FROM ibkr_flex_queries q WHERE q.tenant_id = c.tenant_id "
        " AND q.credential_id = c.connection_id ORDER BY q.created_at LIMIT 1) AS flex_source_id "
        "FROM broker_connections c LEFT JOIN ibkr_flex_credentials f "
        "ON f.tenant_id = c.tenant_id AND f.credential_id = c.connection_id "
        "WHERE c.tenant_id = %s AND c.status <> 'retired' ORDER BY c.created_at",
        (tenant_id,),
    ).fetchall()
    connections = []
    for row in rows:
        health = _health(row, today=today, platform_ip=platform_ip)
        locked = str(row["ip_locked_to"]) if row.get("ip_locked_to") else None
        connections.append({
            "connection_id": str(row["connection_id"]),
            "broker": row["broker"],
            "broker_label": BROKERS[row["broker"]].label,
            "display_name": row["display_name"],
            "environment": row["environment"],
            "expires_on": str(row["credential_expires_on"]) if row.get("credential_expires_on") else None,
            "ip_lock": ip_lock_state(locked, platform_ip),
            "health": health,
            "health_label": HEALTH_LABELS[health],
            "flex_source_id": str(row["flex_source_id"]) if row.get("flex_source_id") else None,
        })
    tracked = {item["connection_id"] for item in connections}
    untracked = [
        {"credential_id": str(row["credential_id"]), "display_name": row["display_name"]}
        for row in connection.execute(
            "SELECT credential_id, display_name FROM ibkr_flex_credentials WHERE tenant_id = %s "
            "AND status NOT IN ('revoked', 'expired') ORDER BY created_at",
            (tenant_id,),
        ).fetchall()
        if str(row["credential_id"]) not in tracked
    ]
    return {
        "connections": connections,
        "existing_ibkr_logins": untracked,
        "platform_ip": platform_ip,
        "recipe_steps": list(RECIPE_STEPS),
        "brokers": [{"key": spec.key, "label": spec.label, "account_hint": spec.account_hint,
                     "environments": list(spec.environments)} for spec in BROKERS.values()],
    }


def onboarding_overview(connection, tenant_id: str, *, today: date, platform_ip: str | None) -> dict:
    cases = []
    for row in connection.execute(
        "SELECT case_id FROM broker_onboarding_cases WHERE tenant_id = %s AND status NOT IN ('cancelled', 'closed') "
        "ORDER BY created_at",
        (tenant_id,),
    ).fetchall():
        detail = case_detail(connection, tenant_id, str(row["case_id"]))
        cases.append({key: detail[key] for key in (
            "case_id", "display_name", "broker_label", "account", "stage_label", "stage_number", "status",
            "next_owner_action")})
    taken = {
        (str(row["broker"]), str(row["broker_account_id"]))
        for row in connection.execute(
            "SELECT broker, broker_account_id FROM broker_onboarding_cases WHERE tenant_id = %s "
            "AND status <> 'cancelled'",
            (tenant_id,),
        ).fetchall()
    }
    live_sources = {
        str(row["source_account_id"])
        for row in connection.execute(
            "SELECT source_account_id FROM platform_subjects WHERE tenant_id = %s AND source_account_id IS NOT NULL",
            (tenant_id,),
        ).fetchall()
    }
    available = []
    for row in connection.execute(
        "SELECT c.connection_id, c.broker, c.display_name AS connection_name, a.broker_account_id, "
        "a.account_name, a.base_currency FROM broker_connection_accounts a "
        "JOIN broker_connections c ON c.tenant_id = a.tenant_id AND c.connection_id = a.connection_id "
        "WHERE a.tenant_id = %s AND a.discovery_status = 'discovered' AND c.status NOT IN ('suspended', 'retired') "
        "UNION "
        "SELECT c.connection_id, c.broker, c.display_name, s.source_account_id, s.account_name, s.base_currency "
        "FROM ibkr_flex_source_accounts s "
        "JOIN ibkr_flex_queries q ON q.tenant_id = s.tenant_id AND q.flex_source_id = s.flex_source_id "
        "JOIN broker_connections c ON c.tenant_id = q.tenant_id AND c.connection_id = q.credential_id "
        "WHERE s.tenant_id = %s AND s.discovery_status NOT IN ('not_portfolio', 'ignored') "
        "AND c.status NOT IN ('suspended', 'retired') "
        "ORDER BY 3, 4",
        (tenant_id, tenant_id),
    ).fetchall():
        key = (str(row["broker"]), str(row["broker_account_id"]))
        if key in taken or key[1] in live_sources:
            continue
        taken.add(key)
        available.append({
            "connection_id": str(row["connection_id"]),
            "broker": row["broker"],
            "broker_label": BROKERS[row["broker"]].label,
            "connection_name": row["connection_name"],
            "broker_account_id": row["broker_account_id"],
            "account": display_account_id(row["broker"], row["broker_account_id"]),
            "account_name": row["account_name"],
            "base_currency": row["base_currency"],
        })
    live = [
        {"subject_id": row["subject_id"], "display_name": row["display_name"]}
        for row in connection.execute(
            "SELECT subject_id, display_name FROM platform_subjects WHERE tenant_id = %s "
            "AND lifecycle_status = 'active' ORDER BY display_name",
            (tenant_id,),
        ).fetchall()
    ]
    starting_allowed = not any(item["stage_number"] <= 3 for item in cases)
    return {"cases": cases, "available": available, "live": live, "can_start": starting_allowed}


def register_connection(connection, tenant_id: str, *, record, stored_fingerprint: str, query_id: str | None,
                        account_id: str | None, actor: str | None) -> None:
    """Write a new connection after its secret is in Key Vault. One transaction."""

    if record.broker == "ibkr_flex":
        connection.execute(
            "INSERT INTO ibkr_flex_credentials (credential_id, tenant_id, display_name, key_vault_secret_name, "
            "token_fingerprint, status, created_by_identity_sha256, metadata_json) "
            "VALUES (%s, %s, %s, %s, %s, 'pending_validation', %s, %s::jsonb)",
            (record.connection_id, tenant_id, record.display_name, record.key_vault_secret_name,
             stored_fingerprint, actor, _json({"submission_channel": "broker_connections"})),
        )
        connection.execute(
            "INSERT INTO ibkr_flex_queries (flex_source_id, tenant_id, credential_id, query_id, display_name, "
            "schedule_enabled, status, metadata_json) VALUES (%s, %s, %s, %s, %s, false, 'pending_validation', "
            "%s::jsonb)",
            (uuid.uuid4(), tenant_id, record.connection_id, query_id, f"{record.display_name} daily",
             _json({"activation_blocked": True, "recipe": "wealthryse-daily-v1"})),
        )
    connection.execute(
        "INSERT INTO broker_connections (connection_id, tenant_id, broker, environment, display_name, "
        "key_vault_secret_name, credential_fingerprint, credential_expires_on, ip_locked_to, status, "
        "created_by_identity_sha256) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending_check', %s)",
        (record.connection_id, tenant_id, record.broker, record.environment, record.display_name,
         record.key_vault_secret_name, stored_fingerprint, record.credential_expires_on, record.ip_locked_to, actor),
    )
    if account_id:
        connection.execute(
            "INSERT INTO broker_connection_accounts (tenant_id, connection_id, broker_account_id, discovery_status) "
            "VALUES (%s, %s, %s, 'discovered')",
            (tenant_id, record.connection_id, account_id),
        )


def track_existing_login(connection, tenant_id: str, *, credential_id: str, expires_on: date | None,
                         ip_locked_to: str | None, actor: str | None, today: date) -> None:
    """Bring an IBKR login already on the platform under expiry and IP tracking; no new token."""

    row = connection.execute(
        "SELECT credential_id, display_name, key_vault_secret_name, token_fingerprint FROM ibkr_flex_credentials "
        "WHERE tenant_id = %s AND credential_id = %s AND status NOT IN ('revoked', 'expired')",
        (tenant_id, credential_id),
    ).fetchone()
    if row is None:
        raise OnboardingError("IBKR login not found", 404)
    fingerprint = str(row["token_fingerprint"] or "")[:16]
    if len(fingerprint) != 16 or not str(row["key_vault_secret_name"]).startswith("ibkr-flex-source-"):
        raise OnboardingError("This login's token was not stored through the secure form; replace it instead")
    try:
        expiry = validate_expiry("ibkr_flex", expires_on, today=today)
        locked = validate_ip_lock(ip_locked_to)
    except ValueError as error:
        raise OnboardingError(str(error), 400) from error
    connection.execute(
        "INSERT INTO broker_connections (connection_id, tenant_id, broker, environment, display_name, "
        "key_vault_secret_name, credential_fingerprint, credential_expires_on, ip_locked_to, status, "
        "created_by_identity_sha256) VALUES (%s, %s, 'ibkr_flex', 'live', %s, %s, %s, %s, %s, 'pending_check', %s)",
        (row["credential_id"], tenant_id, row["display_name"], row["key_vault_secret_name"], fingerprint, expiry,
         locked, actor),
    )


# Requests ------------------------------------------------------------------


class StartCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_id: uuid.UUID
    broker_account_id: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=3, max_length=160)
    reporting_currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    history_start_date: date


class ApproveCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledge_warnings: list[str] = Field(default_factory=list, max_length=20)
    confirmation: str | None = Field(default=None, max_length=120)


class CancelCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=500)


class NewConnectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    broker: Literal["ibkr_flex", "kiwoom_rest"]
    display_name: str = Field(min_length=1, max_length=120)
    environment: Literal["live", "mock"] = "live"
    expires_on: date | None = None
    ip_locked_to: str | None = Field(default=None, max_length=64)
    query_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")
    account_id: str | None = Field(default=None, max_length=32)
    flex_token: SecretStr | None = None
    app_key: SecretStr | None = None
    app_secret: SecretStr | None = None


class TrackLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: uuid.UUID
    expires_on: date
    ip_locked_to: str | None = Field(default=None, max_length=64)


def install(
    app: FastAPI,
    *,
    onboarding_user: Callable[..., dict[str, Any]],
    owner_user: Callable[..., dict[str, Any]],
    read_database: Callable[[], Any],
    write_database: Callable[[], Any],
    render_page: Callable[..., str],
    identity_digest: Callable[[str], str | None],
    store_secret: Callable[[str, str], Mapping[str, str]],
    tenant_id: str,
    platform_ip: Callable[[], str | None],
    today: Callable[[], date] = date.today,
) -> None:
    """Add the new onboarding routes. Each answers 404 while the switch is off."""

    def switched_on() -> None:
        if not onboarding_v2_enabled():
            raise HTTPException(status_code=404, detail="Not found")

    def v2_onboarding_user(user: dict[str, Any] = Depends(onboarding_user)) -> dict[str, Any]:
        switched_on()
        return user

    def v2_owner(user: dict[str, Any] = Depends(owner_user)) -> dict[str, Any]:
        switched_on()
        return user

    def actor(user: Mapping[str, Any]) -> str | None:
        return identity_digest(str(user.get("object_id") or user.get("email") or ""))

    def run(database: Callable[[], Any], work: Callable[[Any], dict]) -> dict:
        from psycopg.errors import UniqueViolation

        try:
            with database() as connection:
                return work(connection)
        except OnboardingError as error:
            raise HTTPException(status_code=error.status_code, detail=str(error)) from error
        except UniqueViolation as error:
            raise HTTPException(status_code=409, detail="Someone else changed this at the same time; reload the page") from error

    @app.get("/broker-connections", response_class=HTMLResponse)
    def broker_connections_page(user: dict[str, Any] = Depends(v2_onboarding_user)) -> str:
        return render_page(user, active="broker-connections", title="Broker connections",
                           description=CONNECTIONS_DESCRIPTION, content=CONNECTIONS_CONTENT,
                           script=CONNECTIONS_SCRIPT)

    @app.get("/account-onboarding/cases/{case_id}", response_class=HTMLResponse)
    def onboarding_case_page(case_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> str:
        return render_page(user, active="account-onboarding", title="Account onboarding",
                           description=CASE_DESCRIPTION,
                           content=CASE_CONTENT.replace("{case_id}", str(case_id)), script=CASE_SCRIPT)

    @app.get("/api/onboarding-v2/overview")
    def overview(user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(read_database, lambda c: onboarding_overview(c, tenant_id, today=today(),
                                                                platform_ip=platform_ip()))

    @app.get("/api/onboarding-v2/cases/{case_id}")
    def case(case_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        detail = run(read_database, lambda c: case_detail(c, tenant_id, str(case_id)))
        return {**detail, "may_approve": user.get("role") == "owner"}

    @app.post("/api/onboarding-v2/cases")
    def start(body: StartCaseRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(write_database, lambda c: start_case(
            c, tenant_id, connection_id=str(body.connection_id), broker_account_id=body.broker_account_id,
            display_name=body.display_name, reporting_currency=body.reporting_currency,
            history_start_date=body.history_start_date, actor=actor(user), today=today()))

    @app.post("/api/onboarding-v2/cases/{case_id}/approvals/{approval}")
    def approve_route(case_id: uuid.UUID, approval: str, body: ApproveCaseRequest,
                      user: dict[str, Any] = Depends(v2_owner)) -> dict:
        return run(write_database, lambda c: approve_case(
            c, tenant_id, str(case_id), approval=approval, terms_sha256=body.terms_sha256,
            acknowledge_warnings=body.acknowledge_warnings, confirmation=body.confirmation,
            actor=actor(user) or ""))

    @app.post("/api/onboarding-v2/cases/{case_id}/cancel")
    def cancel_route(case_id: uuid.UUID, body: CancelCaseRequest, user: dict[str, Any] = Depends(v2_owner)) -> dict:
        return run(write_database, lambda c: cancel_case(c, tenant_id, str(case_id), reason=body.reason,
                                                         actor=actor(user) or ""))

    @app.get("/api/broker-connections")
    def list_connections(user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(read_database, lambda c: connections_overview(c, tenant_id, today=today(),
                                                                 platform_ip=platform_ip()))

    @app.post("/api/broker-connections/track-existing")
    def track_existing(body: TrackLoginRequest, user: dict[str, Any] = Depends(v2_owner)) -> dict:
        def work(connection) -> dict:
            track_existing_login(connection, tenant_id, credential_id=str(body.credential_id),
                                 expires_on=body.expires_on, ip_locked_to=body.ip_locked_to,
                                 actor=actor(user), today=today())
            return {"status": "tracked", "connection_id": str(body.credential_id)}
        return run(write_database, work)

    @app.post("/api/broker-connections")
    def add_connection(body: NewConnectionRequest, user: dict[str, Any] = Depends(v2_owner)) -> dict:
        if body.broker == "ibkr_flex":
            secrets = {"flex_token": body.flex_token.get_secret_value() if body.flex_token else ""}
            if not body.query_id:
                raise HTTPException(status_code=400, detail="Enter the Flex query ID from the recipe")
            account_id = None
        else:
            secrets = {"app_key": body.app_key.get_secret_value() if body.app_key else "",
                       "app_secret": body.app_secret.get_secret_value() if body.app_secret else ""}
            try:
                account_id = normalize_account_id("kiwoom_rest", body.account_id or "")
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
        try:
            record = new_connection_record(
                broker=body.broker, display_name=body.display_name, environment=body.environment,
                credentials=secrets, expires_on=body.expires_on, ip_locked_to=body.ip_locked_to, today=today())
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        with read_database() as connection:
            if connection.execute(
                "SELECT 1 FROM broker_connections WHERE tenant_id = %s AND lower(display_name) = lower(%s)",
                (tenant_id, record.display_name),
            ).fetchone():
                raise HTTPException(status_code=409, detail="A connection with this name already exists")
        value = secrets["flex_token"] if body.broker == "ibkr_flex" else json.dumps(secrets, separators=(",", ":"))
        try:
            stored = store_secret(record.key_vault_secret_name, value)
        except Exception as error:
            raise HTTPException(status_code=503, detail="The key was not stored; nothing was registered") from error
        finally:
            secrets, value = {}, ""
        run(write_database, lambda c: register_connection(
            c, tenant_id, record=record, stored_fingerprint=str(stored["token_fingerprint"]),
            query_id=body.query_id, account_id=account_id, actor=actor(user)) or {})
        return {"status": "pending_check", "connection_id": record.connection_id,
                "message": "Stored. The platform checks the connection before any account can be onboarded."}


# Pages ---------------------------------------------------------------------

ONBOARDING_DESCRIPTION = "Pick one account at a time. Nothing is imported or shown to viewers until you start its case."
CASE_DESCRIPTION = "One account, four stages, three decisions from you."
CONNECTIONS_DESCRIPTION = "One connection per broker login. Keys go straight to Key Vault and are never shown again."

ONBOARDING_CONTENT = """<div class="page-view active" data-section="account-onboarding">
<div class="card wide"><div class="label">Cases in progress</div><div id="v2-cases"><div class="muted">Loading…</div></div></div>
<div class="card wide"><div class="label">Available to onboard</div><div class="muted">Found through your broker connections. Each account is onboarded only when you choose it.</div><div id="v2-available"><div class="muted">Loading…</div></div><div id="v2-start" hidden></div><div class="actions"><a class="button" href="/broker-connections">Broker connections</a><a class="button" href="/account-onboarding/classic#derived-portfolio-studio">New derived portfolio</a><a class="button" href="/account-onboarding/classic">Master accounts and earlier cases</a></div></div>
<div class="card wide"><div class="label">Live accounts</div><div id="v2-live" class="status-row"></div></div>
</div>"""

CASE_CONTENT = """<div class="page-view active" data-section="account-onboarding" data-case-id="{case_id}">
<div class="card wide"><div class="label" id="case-kicker">Loading…</div><h2 id="case-title"></h2><div id="case-next" class="muted"></div><div id="case-progress" class="status-row"></div></div>
<div class="card wide"><div class="label">Checks for this stage</div><div id="case-checks" class="onboarding-checks"></div></div>
<div class="card wide"><div class="label">Your three approvals</div><div id="case-approvals" class="status-row"></div><div id="case-approval-form"></div><div id="case-result" class="muted" role="status" aria-live="polite"></div></div>
<div class="card wide" id="case-cancel-card" hidden><div class="label">Stop this case</div><p class="muted">Cancelling frees the account to be added again later. Nothing reached viewers.</p><div class="actions"><input id="case-cancel-reason" type="text" maxlength="500" placeholder="Reason" aria-label="Reason for cancelling"><button id="case-cancel" class="button" type="button">Cancel this case</button></div></div>
</div>"""

CONNECTIONS_CONTENT = """<div class="page-view active" data-section="broker-connections">
<div class="card wide"><div class="label">Connections</div><div id="bc-list"><div class="muted">Loading…</div></div><div id="bc-existing"></div></div>
<div class="card wide"><div class="label">Add a connection</div>
<form id="bc-form" autocomplete="off"><div class="status-row">
<label class="status-item"><span class="label">Broker</span><select name="broker" id="bc-broker"><option value="ibkr_flex">IBKR (Flex)</option><option value="kiwoom_rest">Kiwoom (REST API)</option></select></label>
<label class="status-item"><span class="label">Login name</span><input type="text" name="display_name" maxlength="120" required placeholder="e.g. Second IBKR login"></label>
<label class="status-item" data-broker="kiwoom_rest" hidden><span class="label">Environment</span><select name="environment"><option value="mock">Mock (test)</option><option value="live">Live</option></select></label>
<label class="status-item" data-broker="kiwoom_rest" hidden><span class="label">Account number</span><input type="text" name="account_id" maxlength="32" placeholder="0000-0000-00"></label>
<label class="status-item" data-broker="ibkr_flex"><span class="label">Flex query ID</span><input type="text" name="query_id" inputmode="numeric" pattern="[0-9]{1,20}" maxlength="20" placeholder="From the recipe below"></label>
<label class="status-item" data-broker="ibkr_flex"><span class="label">Flex token</span><input type="password" class="secret-field" name="flex_token" maxlength="1024" autocomplete="new-password" placeholder="Pasted once, never shown again"></label>
<label class="status-item" data-broker="kiwoom_rest" hidden><span class="label">App key</span><input type="password" class="secret-field" name="app_key" maxlength="1024" autocomplete="new-password"></label>
<label class="status-item" data-broker="kiwoom_rest" hidden><span class="label">App secret</span><input type="password" class="secret-field" name="app_secret" maxlength="1024" autocomplete="new-password"></label>
<label class="status-item"><span class="label">Key expiry</span><input type="date" name="expires_on"></label>
<label class="status-item"><span class="label">IP lock</span><input type="text" name="ip_locked_to" id="bc-ip" maxlength="64" placeholder="The platform address"></label>
</div>
<details id="bc-recipe" style="margin-top:16px"><summary>Show the Flex query recipe</summary><ol id="bc-recipe-steps"></ol></details>
<div class="actions"><button class="button" type="submit">Save and check</button></div><div id="bc-status" class="muted" role="status" aria-live="polite"></div></form>
<div class="warning">Never paste a token or key into chat, email or a support message. Use only this form.</div></div>
</div>"""

_COMMON_SCRIPT = r"""
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json=async response=>{const payload=await response.json().catch(()=>({}));if(!response.ok)throw new Error(payload.detail||`http_${response.status}`);return payload};
const post=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(json);
const pill=(text,cls)=>`<span class="onboarding-pill ${cls||''}">${esc(text)}</span>`;
const row=(name,note,right)=>`<div class="onboarding-row"><div><strong>${esc(name)}</strong><div class="muted">${esc(note)}</div></div><div class="actions">${right||''}</div></div>`;
const statusPill={platform_working:['Waiting on platform','blocked'],waiting_on_owner:['Waiting on you',''],blocked:['Check failed','blocked'],live:['Live','active'],closed:['Closed','active'],cancelled:['Cancelled','']};
document.addEventListener('click',e=>document.querySelectorAll('details.platform-menu[open]').forEach(d=>{if(!d.contains(e.target))d.removeAttribute('open')}));
"""

ONBOARDING_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const load=()=>fetch('/api/onboarding-v2/overview').then(json).then(data=>{
  document.querySelector('#v2-cases').innerHTML=data.cases.length?data.cases.map(c=>{const [text,cls]=statusPill[c.status]||[c.status,''];return row(c.display_name,`${c.broker_label} · ${c.account} · Stage ${c.stage_number} of 4: ${c.stage_label}`,pill(text,cls)+`<a class="button" href="/account-onboarding/cases/${encodeURIComponent(c.case_id)}">Open case</a>`)}).join(''):'<p class="muted">No account is being onboarded.</p>';
  document.querySelector('#v2-available').innerHTML=data.available.length?data.available.map((a,i)=>row(`${a.broker_label.split(' ')[0]} account ${a.account}${a.account_name?' · '+a.account_name:''}`,`${a.connection_name}${a.base_currency?' · '+a.base_currency:''}`,data.can_start?`<button class="button" type="button" data-pick="${i}">Add account</button>`:pill('Finish the current case first','blocked'))).join(''):'<p class="muted">No new accounts found yet. Add or check a connection on the Broker connections page.</p>';
  document.querySelector('#v2-live').innerHTML=data.live.map(s=>`<div class="status-item"><div class="label">${esc(s.subject_id)}</div><strong>${esc(s.display_name)}</strong></div>`).join('')||'<div class="muted">None yet.</div>';
  document.querySelectorAll('[data-pick]').forEach(button=>button.addEventListener('click',()=>pick(data.available[Number(button.dataset.pick)])));
}).catch(error=>{document.querySelector('#v2-cases').innerHTML=`<div class="warning">This page could not load: ${esc(error.message)}</div>`});
const pick=a=>{const box=document.querySelector('#v2-start');box.hidden=false;box.innerHTML=`<form id="v2-start-form" class="onboarding-row" style="display:block"><strong>Add ${esc(a.broker_label)} account ${esc(a.account)}</strong><div class="status-row"><label class="status-item"><span class="label">Display name</span><input type="text" name="display_name" required minlength="3" maxlength="160" value="${esc(a.account_name||a.account)}"></label><label class="status-item"><span class="label">Reporting currency</span><input type="text" name="reporting_currency" required pattern="[A-Za-z]{3}" maxlength="3" value="${esc(a.base_currency||(a.broker==='kiwoom_rest'?'KRW':'USD'))}"></label><label class="status-item"><span class="label">History from</span><input type="date" name="history_start_date" required></label></div><div class="actions"><button class="button" type="submit">Start this case</button></div><div class="muted" role="status"></div></form>`;
  const form=box.querySelector('form');form.addEventListener('submit',async event=>{event.preventDefault();const values=new FormData(form),note=form.querySelector('[role=status]'),button=form.querySelector('button');button.disabled=true;note.textContent='Starting the case…';try{const result=await post('/api/onboarding-v2/cases',{connection_id:a.connection_id,broker_account_id:a.broker_account_id,display_name:values.get('display_name'),reporting_currency:String(values.get('reporting_currency')).toUpperCase(),history_start_date:values.get('history_start_date')});window.location.href=result.next_page}catch(error){note.textContent=`Not started: ${error.message}`;button.disabled=false}});box.scrollIntoView({behavior:'smooth',block:'nearest'})};
load();
})();
</script>"""

CASE_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const root=document.querySelector('[data-case-id]'),caseId=root.dataset.caseId,result=document.querySelector('#case-result');
const tileState={done:['Done','active'],current:['In progress',''],not_started:['Not started','blocked'],cancelled:['Cancelled','blocked']};
const render=c=>{
  document.querySelector('#case-kicker').textContent=`Stage ${c.stage_number} of 4 · ${c.broker_label} · ${c.reporting_currency} reporting`;
  document.querySelector('#case-title').textContent=`${c.display_name} (${c.account})`;
  document.querySelector('#case-next').textContent=c.next_owner_action||(c.status==='cancelled'?'This case was cancelled.':'Nothing needs you right now.');
  document.querySelector('#case-progress').innerHTML=c.progress.map(t=>{const [text,cls]=tileState[t.state];return `<div class="status-item"><div class="label">${esc(t.number)} · ${esc(t.label)}</div>${pill(text,cls)}</div>`}).join('');
  document.querySelector('#case-checks').innerHTML=c.checks.map(k=>`<div class="onboarding-check ${esc(k.status==='pending'?'':k.status)}"><strong>${esc(k.label)}</strong><span>${esc(k.message)}${k.status==='warn'&&k.acknowledged?' Acknowledged.':''}</span></div>`).join('')||'<div class="muted">No checks in this stage.</div>';
  document.querySelector('#case-approvals').innerHTML=c.approvals.map((a,i)=>`<div class="status-item"><div class="label">Approval ${i+1}</div><strong>${esc(a.label)}</strong>${a.approved?pill('Approved','active'):pill(a.available?'Not yet':'Comes with the history build','blocked')}</div>`).join('');
  const ready=c.may_approve&&c.awaiting_approval==='account_terms';
  const form=document.querySelector('#case-approval-form');
  if(ready){const t=c.account_terms,warns=c.checks.filter(k=>k.status==='warn');form.innerHTML=`<form id="terms-form"><h3>Approve the account terms</h3><div class="status-row">${[['Account',c.account],['Display name',t.display_name],['Reporting currency',t.reporting_currency],['History from',t.history_start_date]].map(([k,v])=>`<div class="status-item"><div class="label">${esc(k)}</div><strong>${esc(v)}</strong></div>`).join('')}</div>${warns.map(k=>`<label style="display:flex;gap:10px;margin-top:12px"><input type="checkbox" name="ack" value="${esc(k.check_id)}" required> <span>I have read the warning on ${esc(k.label)}.</span></label>`).join('')}<div class="actions"><button class="button" type="submit">Approve account terms</button></div></form>`;
    form.querySelector('form').addEventListener('submit',async event=>{event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;result.textContent='Recording your approval…';try{await post(`/api/onboarding-v2/cases/${caseId}/approvals/account_terms`,{terms_sha256:c.account_terms_sha256,acknowledge_warnings:[...event.currentTarget.querySelectorAll('input[name=ack]:checked')].map(x=>x.value)});result.textContent='Approved.';load()}catch(error){result.textContent=`Not approved: ${error.message}`;button.disabled=false}})}
  else form.innerHTML='';
  document.querySelector('#case-cancel-card').hidden=!(c.can_cancel&&c.may_approve);
};
const load=()=>fetch(`/api/onboarding-v2/cases/${caseId}`).then(json).then(render).catch(error=>{document.querySelector('#case-kicker').textContent='Unavailable';document.querySelector('#case-next').textContent=error.message});
document.querySelector('#case-cancel').addEventListener('click',async()=>{const reason=document.querySelector('#case-cancel-reason').value;try{await post(`/api/onboarding-v2/cases/${caseId}/cancel`,{reason});window.location.href='/account-onboarding'}catch(error){result.textContent=`Not cancelled: ${error.message}`}});
load();
})();
</script>"""

CONNECTIONS_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const form=document.querySelector('#bc-form'),status=document.querySelector('#bc-status'),broker=document.querySelector('#bc-broker');
const healthClass={healthy:'active',renew_soon:'blocked',expired:'blocked',failing:'blocked',not_checked:'',suspended:'blocked'};
const lockText={locked:'locked to the platform address',not_locked:'not IP-locked',mismatch:'locked to a different address',unknown_platform_ip:'IP lock recorded'};
const showFields=()=>form.querySelectorAll('[data-broker]').forEach(node=>{node.hidden=node.dataset.broker!==broker.value});
broker.addEventListener('change',showFields);showFields();
const load=()=>fetch('/api/broker-connections').then(json).then(data=>{
  document.querySelector('#bc-list').innerHTML=data.connections.length?data.connections.map(c=>row(`${c.broker_label} · ${c.display_name}`,[c.environment==='mock'?'Mock environment':null,c.expires_on?`expires ${c.expires_on}`:null,lockText[c.ip_lock]].filter(Boolean).join(' · '),pill(c.health_label,healthClass[c.health])+(c.flex_source_id?`<button class="button" type="button" data-check="${esc(c.flex_source_id)}">Check now</button>`:''))).join(''):'<p class="muted">No connections yet.</p>';
  document.querySelector('#bc-existing').innerHTML=data.existing_ibkr_logins.length?`<h3>IBKR logins already on the platform</h3><p class="muted">Add each token's expiry date so the platform can warn you before it lapses. The token itself stays where it is.</p>`+data.existing_ibkr_logins.map(l=>`<form class="onboarding-row" data-track="${esc(l.credential_id)}"><div><strong>${esc(l.display_name)}</strong></div><div class="actions"><input type="date" name="expires_on" required aria-label="Token expiry"><button class="button" type="submit">Track this login</button></div></form>`).join(''):'';
  document.querySelector('#bc-recipe-steps').innerHTML=data.recipe_steps.map(s=>`<li>${esc(s)}</li>`).join('');
  const ip=document.querySelector('#bc-ip');if(data.platform_ip){ip.value=data.platform_ip;ip.placeholder=data.platform_ip}
  document.querySelectorAll('[data-check]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(b.dataset.check)}/validate`,{method:'POST'}).then(json);b.textContent='Check queued'}catch(error){b.textContent='Not queued';b.title=error.message}}));
  document.querySelectorAll('[data-track]').forEach(f=>f.addEventListener('submit',async event=>{event.preventDefault();try{await post('/api/broker-connections/track-existing',{credential_id:f.dataset.track,expires_on:new FormData(f).get('expires_on'),ip_locked_to:data.platform_ip||null});load()}catch(error){f.querySelector('button').textContent=error.message}}));
}).catch(error=>{document.querySelector('#bc-list').innerHTML=`<div class="warning">This page could not load: ${esc(error.message)}</div>`});
form.addEventListener('submit',async event=>{event.preventDefault();const values=new FormData(form),button=form.querySelector('button[type=submit]'),body={broker:broker.value,display_name:values.get('display_name'),expires_on:values.get('expires_on')||null,ip_locked_to:values.get('ip_locked_to')||null};
  if(broker.value==='ibkr_flex'){body.query_id=values.get('query_id');body.flex_token=values.get('flex_token')}else{body.environment=values.get('environment');body.account_id=values.get('account_id');body.app_key=values.get('app_key');body.app_secret=values.get('app_secret')}
  button.disabled=true;status.textContent='Storing the key in Key Vault…';
  try{const result=await post('/api/broker-connections',body);form.reset();showFields();status.textContent=result.message;load()}catch(error){status.textContent=`Not saved: ${error.message}`}finally{form.querySelectorAll('input[type=password]').forEach(x=>{x.value=''});button.disabled=false}});
load();
})();
</script>"""
