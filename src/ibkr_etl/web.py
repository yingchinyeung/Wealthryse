from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import PurePosixPath
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from psycopg.errors import InsufficientPrivilege
from psycopg import Error as PostgresError
from psycopg.rows import dict_row
from .ops_agent_posts import open_incidents
from .platform_health import aggregate_health
from .risk_display import RISK_DISPLAY_SCRIPT
from .subject_read_contract import ACTIVE_NAV_CURRENCY_SQL
from pypdf.errors import PdfReadError

from .ai_analyst import (
    AIAnalystConfig,
    AIAnalystError,
    analysis_types,
    generate_grounded_narrative,
)
from .ai_chat import (
    MAX_CONVERSATION_MESSAGES,
    analysis_json,
    assistant_plain_text,
    chart_csv,
    chart_svg,
    conversation_title,
    model_history,
    transcript_markdown,
    transcript_payload,
)
from .admin_actions import (
    AdminActionConfig,
    AdminActionError,
    action_catalog,
    claim_action_for_execution,
    create_action_preview,
    execute_claimed_action,
    _default_job_starter,
)
from .admin_chat import (
    MAX_ADMIN_CHAT_MESSAGES,
    SYSTEM_UPDATES_SOURCE_KEY,
    admin_conversation_title,
    admin_model_history,
    admin_transcript_markdown,
    admin_transcript_payload,
    generate_admin_chat_response,
)
from .autonomy_review import next_review_due, review_confirmation_phrase
from .agent_tools import AgentToolContext, AgentToolError, execute_tool, tool_catalog
from .benchmark_analytics import (
    ComparisonRow,
    comparison_metrics,
)
from .cloud_storage import download_blob_bytes, list_blobs, upload_bytes
from .db import PostgresConnection
from .subject_read_contract import require_ms_hkd_contract, synthetic_exposure
from .model_cash_display import load_model_cash
from .monthly_artifacts import MonthlyArtifactError, validate_factsheet_return_typography
from .monthly_reports import (
    MonthlyReportError,
    build_monthly_report_catalog,
    validate_artifact,
    validate_period_end,
    validate_review_artifact,
)
from .monthly_release import review_package
from .performance_periods import (
    PerformanceInputError,
    flow_statement,
    performance_periods as calculate_performance_periods,
)
from . import report_design_updates
from .report_design_ui import controls as report_design_controls, SCRIPT as REPORT_DESIGN_SCRIPT
from .internal_release import InternalReleaseError
from .risk_snapshots import RISK_ANALYTICS_VERSION, decoded_payload, snapshot_read_status
from .market_data import ALPHA_WORLD_MARKET_INSTRUMENTS
from .template_intake import parse_template_manifest, register_template_assets
from .template_upload import (
    MAX_TEMPLATE_BYTES,
    presentation_identity_scope,
    safe_blob_filename,
    presentation_strategy_contract,
    upload_version_label,
    validate_pdf_upload,
    validate_pptx_upload,
    validate_subject_id,
)
from .subject_activation import activate_subject
from .subject_onboarding import (
    POST_WAVE0_ONBOARDING_SEQUENCE,
    approved_reference_definitions,
    configuration_sha256 as subject_configuration_sha256,
    dependencies_for_definition,
    derived_portfolio_studio_templates,
    filtered_portfolio_definition,
    fixed_unit_composite_definition,
    master_account_definition,
    onboarding_branch_catalog,
    reference_definition_summary,
    reference_onboarding_programme,
)
from .onboarding_preview import build_physical_preview_validation
from .website_benchmarks import website_comparison_version, MS_HKD_COMPARISON_VERSION, T_HKD_COMPARISON_VERSION
from .onboarding_reports import (
    OnboardingReportError,
    build_historical_report_batch_validation,
    discover_completed_reporting_periods,
    source_period_evidence,
    template_evidence,
)
from .onboarding_queue import (
    QueueAccount,
    classify_connection_accounts,
    initial_queue_items,
    next_queue_state,
    normalize_account_order,
)
from .operating_registry import control_plane_summary
from .authorization import (
    may_view_subject,
    resolve_authorized_identity,
    subject_access_levels,
)
from .metrics_guide import METRIC_ANCHORS, METRICS_GUIDE_SCRIPT, render_metrics_guide
from . import onboarding_v2_web
from .broker_connections import onboarding_v2_enabled
from .util import canonical_json, sha256_text, utc_now_iso
from .web_theme import ACCOUNT_THEME_CSS, HOME_THEME_CSS, THEME_TOGGLE_HTML, WEB_FONT_FILES, web_font_bytes
from .web_ui import ACCOUNT_UI_SCRIPT, HOME_SCRIPT, TABLE_SCRIPT
from .web_analytics_ui import (
    ANALYTICS_SCRIPT,
    CORRELATION_CARD,
    HOLDINGS_CONCENTRATION_CARD,
    PERFORMANCE_ANALYTICS_CARDS,
    RISK_ANALYTICS_CARDS,
    TRADING_ACTIVITY_CARD,
)
from .web_modelling import (
    MODELLING_CARDS,
    MODELLING_CSS,
    MODELLING_SCRIPT,
    install as _install_modelling,
)
from .portfolio_analytics import (
    CORRELATION_HOLDINGS,
    holdings_concentration,
    RSI_LOOKBACK_DAYS,
    holding_rsi,
    holdings_correlation as calculate_holdings_correlation,
    portfolio_analytics as calculate_portfolio_analytics,
    rolling_benchmark_statistics,
    trading_activity as calculate_trading_activity,
)
from .holdings_view import combine_holdings, enrich_holdings
from .live_ui import live_assets
from .live_view import live_view_allowed, read_positions, read_summary, read_trades
from .sector_classification import YAHOO_PROVIDER as HOLDINGS_SECTOR_PROVIDER


TENANT_ID = os.environ.get(
    "PORTFOLIO_TENANT_ID", "00000000-0000-0000-0000-000000000001"
)
PILOT_ACCOUNT_ID = os.environ.get("PILOT_ACCOUNT_ID", "U16088571")


def _studio_price_convention(price_type: str) -> str:
    """Translate storage labels into the approved calculation vocabulary."""

    return (
        "unadjusted_official_close"
        if price_type == "unadjusted_close"
        else price_type
    )
PILOT_ACCOUNT_DISPLAY_NAME = os.environ.get(
    "PILOT_ACCOUNT_DISPLAY_NAME",
    "U16088571-U.S. Technology and Biotechnology",
)
PLATFORM_NAME = "WealthRyse Portfolio Data Platform"
ONBOARDING_STEP_PAGES = (
    ("connection", "Connect and discover"),
    ("account", "Select account and history"),
    ("financial-contract", "Set financial contract"),
    ("external-data", "Approve external data use"),
    ("documents", "Upload report documents"),
    ("reconciliation", "Reconcile account data"),
    ("historical-reports", "Render and approve historical reports"),
    ("private-preview", "Review private website preview"),
    ("activation", "Owner activation"),
)
RECONCILIATION_REQUIRED_CHECK_IDS = (
    "source_account_discovered",
    "connection_validated",
    "financial_contract_approved",
    "external_market_data_approved",
    "presentation_identity_approved",
    "report_documents_validated",
    "data_import_reconciled",
    "risk_analytics_validated",
)
RELEASE_CHANNEL = os.environ.get("RELEASE_CHANNEL", "internal-production").strip().lower()
AZURE_STORAGE_ACCOUNT_URL = os.environ.get("AZURE_STORAGE_ACCOUNT_URL", "").strip()
INTERNAL_REPORT_CONTAINER = os.environ.get(
    "INTERNAL_REPORT_CONTAINER", "internal-reports"
).strip()
ONBOARDING_INTAKE_CONTAINER = os.environ.get(
    "ONBOARDING_INTAKE_CONTAINER", "onboarding-intake"
).strip()
CREDENTIAL_BROKER_URL = os.environ.get("CREDENTIAL_BROKER_URL", "").strip().rstrip("/")
CREDENTIAL_BROKER_AUTH = os.environ.get("CREDENTIAL_BROKER_AUTH", "").strip()
DERIVED_REFRESH_JOB_NAME = os.environ.get(
    "DERIVED_REFRESH_JOB_NAME", "ibkrv2stg-derived-daily"
).strip()
ONBOARDING_REPORT_JOB_NAME = os.environ.get(
    "ONBOARDING_REPORT_JOB_NAME", "ibkrv2stg-onboard-render"
).strip()
ONBOARDING_ANALYTICS_JOB_NAME = os.environ.get(
    "ONBOARDING_ANALYTICS_JOB_NAME", "ibkrv2stg-onboard-analytics"
).strip()
YAHOO_ONBOARDING_DISCLOSURE_VERSION = "yahoo-ticker-disclosure-v1"
YAHOO_ONBOARDING_SCOPE = {
    "data": ["current_ticker_symbols", "historical_ticker_symbols"],
    "destination": "Yahoo Finance via yfinance",
    "excluded": [
        "raw_ibkr_files",
        "ibkr_account_identifiers",
        "flex_credentials",
        "database_credentials",
        "personal_information",
    ],
    "purposes": [
        "provisional_sector_classification",
        "crisis_period_price_retrieval",
    ],
}
YAHOO_ONBOARDING_SCOPE_SHA256 = sha256_text(canonical_json(YAHOO_ONBOARDING_SCOPE))
COMPANY_LOGO_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAHEAAABxCAMAAADVoLziAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAJVUExURQAAAAYGBiMjIyQkJCAgIBYWFgUFBSIiItnZ2d/f397e3t3d3dDQ0L29vZ+fn3Jycjg4OBMTEwICAigoKPv7+/////r6+sfHx4GBgTY2NicnJ/n5+ePj45qamjExMenp6YCAgLy8vDs7O1JSUvHx8WZmZgEBAe7u7mVlZe3t7UxMTNra2rGxsRISEnBwcJCQkOXl5SsrK/7+/vDw8Pf3939/f7u7u56envz8/Pj4+MvLy/b29qurq6mpqcnJyRoaGsXFxbS0tKioqB4eHpWVlc7OzvPz80lJSdbW1iEhISYmJtjY2BUVFba2tuLi4j8/Pw8PD8PDw4WFhTU1NU5OTubm5iwsLNPT02RkZBsbG9LS0g4ODkdHR3FxcfT09GJiYkpKSoqKii4uLuHh4dzc3FtbW5aWlv39/YaGhm5ubrKysg0NDREREQgICJOTk8zMzCUlJVNTU1VVVYeHhxcXF7m5uUVFRZ2dnUZGRjo6OlFRUW1tbZycnMjIyAMDA+jo6PX19fLy8kRERCkpKezs7OTk5EFBQW9vb1hYWJubm2NjY2lpaaWlpWFhYS0tLaOjo8DAwAkJCaSkpDAwMFdXV3V1dYKCggQEBO/v73Nzc9fX1yoqKjc3N+rq6qqqqjk5OYmJicrKyhkZGY6OjpGRkUtLSwcHB5eXl6ysrNvb21paWmpqagsLC7W1tYiIiD09Pevr60BAQAoKClxcXNXV1TMzM3p6esHBwWxsbGtra7+/vxQUFHR0dKCgoB8fH8/Pz5KSkl5eXqenp3l5eUNDQ3h4eF9fX+DjzmUAAAAJcEhZcwAAFxEAABcRAcom8z8AAAQjSURBVGhD7Zn5W1RlGIY/wI1EU4Z3XEJEBadEUhERxQgwdxTLNDfcQsmttE1LUwytzH1Hzdxya9G0tCzK3b+r65wZZr73uaR532/8wcvr3L8MZ97nPPd1hnPmLGNMQEBAQEBAwDNPWnpGEtI7dOyEa6VC5y6ZL/w/Xbtkdev+Yo+e2biqI6EcEhHu1bvP09nUvi9hd7vk9svDtV1QGIn65/fE9fWojEQDBmKBGqWRMgdhgxatkQoKB2OHDrWRIi9jhw69kV4ZgiUqHIxUlNJR4mKkocVYo8DJmPMq1ihwMlLXvtgjx81Iw7BHjqNx+AgsEmMbS0aW2q2McKSELY/CIjFsG8tGlycWxoytsEbjXrMWiKjydWySwoxVoerEQs34NxILE0bUJBY8Jk7CJinMOHnK1Gltf1dmmNrpbQsz6ma+GU9FeQubpDDjjFnm7f7RP2d7p6U570QXKuaaefOtnEfZAqwSwoyRhcbU+28sWuxPl0T895d2MsvetXIeDR2wSggzhpcbk7aCiBrfi13TrAwT0arVxtQVWDmPkXVYJYQfj/XGmNoyonFrYuO17xN9sM4Ys96O+XwITVK48SPvrbyirPT4/ONPIp96r4V2zGeu1aKBGzf4H+bGz6xA3uf+S74d89lkhTRwY3W7O+BmO+bzdIxfdMR5G/FDM04hRoRwY8N4nMfYgocj0XLMCOHGrctwHqM41455NPk7lAPcuO1LnMdo3m7HPHY0Y0YIN5a391F9tciOeVS6XutwI32N8xjfeN89jJ2ud1pgrP5215NYvJufj4loDzZJAWNjzpNpZClvr96LTVLAKGZfGjZJcTRWRL/7XHA07j+ARWLcjDnum+hoPLgWe+Q4GYsOYY0CF+O2w9iiwcEYPpLSfbneWHr0GJaoUBtbjjsf+1G0xhPrsUGLzti08yQWqNEYS4duTO1f6KMxzv4O13ZBY6TurmdhGzBWnGLAmX9MSod+DDB+f3qgRY8zcOo/ew7X1wPG83y6popNqcT99j8OGH+A8YWJbEy5pyGgJ4nx4iU2Jrp8ERJqkhjNyStsTuU/YkJLMqP5CS7jstq7NZGS1PjzLyxAdBUTSpIazbVfWYKGX8eEjuTG7DMsQbTqBkZUJDeaKWNZxP0JQBSB0fwGvzT93hkTGiTGY/BMjoZtwYgCidHczGQhOrUQEwpERnOLhYimz8SEHJnx3B8sRfQnJuTIjGYJ3JU3uD+aFxoH92MxohXOlwNCownFH/VGabmACSlSo7kNVyB//Y0JIWJjsfUI3aOkFRNCxEZzvYUlKdfxJw+50eA3+j9ut60K4793WJQK3G5BQnyHuItzm3tNLEtVtZiQEJJvo7lxn2WJemNCQnH9kdYEDx7inNHcaodbHz1O+cJOTUq35wEBAQEBAQHPF/8BnNCgQv/VCxcAAAAASUVORK5CYII="
)
ALLOWED_EMAILS = {
    value.strip().lower()
    for value in os.environ.get("ALLOWED_EMAILS", "").split(",")
    if value.strip()
}
ALLOWED_OBJECT_IDS = {
    value.strip().lower()
    for value in os.environ.get("ALLOWED_ENTRA_OBJECT_IDS", "").split(",")
    if value.strip()
}
VIEWER_EMAILS = {
    value.strip().lower()
    for value in os.environ.get("VIEWER_EMAILS", "").split(",")
    if value.strip()
}
VIEWER_OBJECT_IDS = {
    value.strip().lower()
    for value in os.environ.get("VIEWER_ENTRA_OBJECT_IDS", "").split(",")
    if value.strip()
}
ONBOARDING_OPERATOR_EMAILS = {
    value.strip().lower()
    for value in os.environ.get("ONBOARDING_OPERATOR_EMAILS", "").split(",")
    if value.strip()
}
ONBOARDING_OPERATOR_OBJECT_IDS = {
    value.strip().lower()
    for value in os.environ.get("ONBOARDING_OPERATOR_ENTRA_OBJECT_IDS", "").split(",")
    if value.strip()
}
AUTHORIZATION_MODE = os.environ.get("AUTHORIZATION_MODE", "legacy").strip().lower()
if AUTHORIZATION_MODE not in {"legacy", "hybrid", "database"}:
    raise RuntimeError("AUTHORIZATION_MODE must be legacy, hybrid, or database")
AI_ANALYST_CONFIG = AIAnalystConfig(
    enabled=os.environ.get("AI_ANALYST_ENABLED", "false").strip().lower() == "true",
    endpoint=os.environ.get("AZURE_AI_ENDPOINT", "").strip(),
    deployment=os.environ.get("AZURE_AI_DEPLOYMENT", "").strip(),
    region=os.environ.get("AZURE_AI_REGION", "southeastasia").strip().lower(),
    processing_scope=os.environ.get("AZURE_AI_PROCESSING_SCOPE", "disabled").strip().lower(),
    managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID", "").strip(),
)
ADMIN_ACTION_CONFIG = AdminActionConfig(
    enabled=os.environ.get("ADMIN_ACTIONS_ENABLED", "false").strip().lower() == "true",
    environment=os.environ.get("DEPLOYMENT_ENVIRONMENT", "disabled").strip().lower(),
    subscription_id=os.environ.get("AZURE_SUBSCRIPTION_ID", "").strip(),
    resource_group=os.environ.get("AZURE_RESOURCE_GROUP", "").strip(),
    managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID", "").strip(),
)

app = FastAPI(
    title=PLATFORM_NAME,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

SECURITY_AUDIT_LOGGER = logging.getLogger("uvicorn.error.ibkr_security")
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    # Easy Auth validates same-origin POST requests with the Referer header.
    # Keep it for this portal only while suppressing it on cross-origin requests.
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'"
    ),
}


def _identity_digest(value: str) -> str | None:
    candidate = value.strip().lower()
    return hashlib.sha256(candidate.encode("utf-8")).hexdigest() if candidate else None


def _security_audit_event(
    request: Request,
    *,
    status_code: int,
    duration_ms: float,
    error_type: str | None = None,
) -> dict[str, object]:
    object_id = request.headers.get("x-ms-client-principal-id", "")
    email = request.headers.get("x-ms-client-principal-name", "")
    if status_code < 400:
        outcome = "allowed"
    elif status_code in (401, 403):
        outcome = "denied"
    else:
        outcome = "failed"
    event: dict[str, object] = {
        "event_type": "portfolio_portal_request",
        "timestamp": utc_now_iso(),
        "correlation_id": request.state.correlation_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": status_code,
        "outcome": outcome,
        "identity_present": bool(object_id or email),
        "object_id_sha256": _identity_digest(object_id),
        "email_sha256": _identity_digest(email),
        "duration_ms": round(duration_ms, 2),
    }
    if error_type:
        event["error_type"] = error_type
    return event


@app.middleware("http")
async def security_audit_middleware(request: Request, call_next):
    request.state.correlation_id = str(uuid.uuid4())
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as error:
        event = _security_audit_event(
            request,
            status_code=500,
            duration_ms=(time.perf_counter() - started) * 1000,
            error_type=type(error).__name__,
        )
        SECURITY_AUDIT_LOGGER.exception(
            "SECURITY_AUDIT %s", json.dumps(event, sort_keys=True, separators=(",", ":"))
        )
        raise
    event = _security_audit_event(
        request,
        status_code=response.status_code,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    SECURITY_AUDIT_LOGGER.info(
        "SECURITY_AUDIT %s", json.dumps(event, sort_keys=True, separators=(",", ":"))
    )
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, error: Exception) -> JSONResponse:
    """Return a stable JSON envelope without exposing database or server details."""

    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    SECURITY_AUDIT_LOGGER.exception(
        "Unhandled portfolio portal request correlation_id=%s", correlation_id
    )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_server_error",
                "message": (
                    "The server could not complete this request. No approval was recorded."
                ),
                "correlation_id": correlation_id,
            }
        },
        headers={"X-Correlation-ID": correlation_id},
    )


def _identity_matches(
    object_id: str,
    email: str,
    *,
    object_ids: set[str],
    emails: set[str],
) -> bool:
    """Require every configured claim for one role to match the Entra principal."""
    if not object_ids and not emails:
        return False
    if object_ids and object_id not in object_ids:
        return False
    if emails and email not in emails:
        return False
    return True


def _legacy_authorized_user(request: Request) -> dict[str, str]:
    object_id = request.headers.get("x-ms-client-principal-id", "").strip().lower()
    email = request.headers.get("x-ms-client-principal-name", "").strip().lower()
    if not any(
        (
            ALLOWED_OBJECT_IDS,
            ALLOWED_EMAILS,
            ONBOARDING_OPERATOR_OBJECT_IDS,
            ONBOARDING_OPERATOR_EMAILS,
            VIEWER_OBJECT_IDS,
            VIEWER_EMAILS,
        )
    ):
        raise HTTPException(status_code=503, detail="Access allow-list is not configured")
    if _identity_matches(
        object_id,
        email,
        object_ids=ALLOWED_OBJECT_IDS,
        emails=ALLOWED_EMAILS,
    ):
        return {"object_id": object_id, "email": email, "role": "owner"}
    if _identity_matches(
        object_id,
        email,
        object_ids=ONBOARDING_OPERATOR_OBJECT_IDS,
        emails=ONBOARDING_OPERATOR_EMAILS,
    ):
        return {"object_id": object_id, "email": email, "role": "onboarding_operator"}
    if _identity_matches(
        object_id,
        email,
        object_ids=VIEWER_OBJECT_IDS,
        emails=VIEWER_EMAILS,
    ):
        return {"object_id": object_id, "email": email, "role": "viewer"}
    raise HTTPException(status_code=403, detail="This account is not authorized")


def _database_authorized_user(request: Request) -> dict[str, object] | None:
    object_id = request.headers.get("x-ms-client-principal-id", "").strip().lower()
    email = request.headers.get("x-ms-client-principal-name", "").strip().lower()
    if not object_id or not email:
        return None
    try:
        with _database() as connection:
            identity = resolve_authorized_identity(
                _portable_database_connection(connection),
                tenant_id=TENANT_ID,
                object_id=object_id,
                email=email,
            )
    except Exception as error:
        # Hybrid emergency access is restricted to the exact configured owner.
        # A database outage must never revive a removed colleague's legacy role.
        # Database-only mode remains fail-closed for everyone.
        if AUTHORIZATION_MODE == "hybrid":
            SECURITY_AUDIT_LOGGER.warning(
                "Institutional authorization fallback error_type=%s sqlstate=%s",
                error.__class__.__name__,
                getattr(error, "sqlstate", "unavailable"),
            )
            legacy_user = _legacy_authorized_user(request)
            if legacy_user["role"] == "owner":
                return legacy_user
        raise HTTPException(
            status_code=503, detail="Institutional authorization is unavailable"
        ) from error
    if identity is None:
        # A completed lookup is authoritative, including disabled users and
        # revoked roles. Do not undo that decision using the old allow-list.
        raise HTTPException(status_code=403, detail="This account is not authorized")
    return {
        "object_id": identity.object_id,
        "email": identity.email,
        "role": identity.primary_role,
        "roles": list(identity.roles),
        "authorization_source": "database",
    }


def _portable_database_connection(connection):
    """Return the placeholder-translating adapter used by shared modules.

    The website's request-scoped database context exposes a raw psycopg
    connection because most web queries use native ``%s`` placeholders. The
    shared service modules use portable ``?`` placeholders and therefore must
    run through :class:`PostgresConnection`.
    """
    if isinstance(connection, PostgresConnection):
        return connection
    return PostgresConnection(connection)


def _authorized_user(request: Request) -> dict[str, object]:
    if AUTHORIZATION_MODE in {"hybrid", "database"}:
        database_user = _database_authorized_user(request)
        if database_user is not None:
            return database_user
        if AUTHORIZATION_MODE == "database":
            raise HTTPException(status_code=403, detail="This account is not authorized")
    return _legacy_authorized_user(request)


def _owner_user(request: Request) -> dict[str, object]:
    user = _authorized_user(request)
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner access is required")
    return user


def _onboarding_user(request: Request) -> dict[str, object]:
    user = _authorized_user(request)
    if user["role"] not in {"owner", "platform_admin", "onboarding_operator"}:
        raise HTTPException(status_code=403, detail="Onboarding operator access is required")
    return user


class AgentToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str
    as_of: str | None = None
    benchmark_id: str | None = None
    limit: int | None = None


class AnalystNarrativeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str
    analysis_type: Literal[
        "portfolio_overview",
        "performance_review",
        "holdings_review",
        "risk_review",
        "interactive_question",
    ]
    as_of: str | None = None
    benchmark_id: str | None = None
    question: str | None = Field(default=None, max_length=1200)


class AnalystConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str
    title: str | None = Field(default=None, min_length=1, max_length=120)


class AnalystChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1200)
    as_of: str | None = None
    benchmark_id: str | None = None


class AdminActionPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Literal[
        "refresh_risk_free_rate",
        "refresh_provisional_benchmarks",
    ]


class AdminActionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str = Field(min_length=1, max_length=80)


class AdminConversationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=120)


class AdminChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=1200)


class AuthorityReviewApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=80)


class FlexSourceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential_id: uuid.UUID | None = None
    credential_name: str | None = Field(default=None, max_length=120)
    source_name: str = Field(min_length=1, max_length=120)
    query_id: str = Field(pattern=r"^[0-9]{1,20}$")
    token: SecretStr | None = None


class OnboardingQueueCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_ids: list[str] = Field(min_length=1, max_length=100)


class OnboardingPhysicalAccountStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=3, max_length=160)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    history_start_date: date


class OnboardingFinancialContractApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=3, max_length=160)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    history_start_date: date
    primary_benchmark_id: str = Field(min_length=1, max_length=80)
    secondary_benchmark_ids: list[str] = Field(default_factory=list, max_length=8)
    approved: Literal[True]


class OnboardingDerivedContractApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    primary_benchmark_id: str = Field(min_length=1, max_length=80)
    secondary_benchmark_ids: list[str] = Field(default_factory=list, max_length=8)
    approved: Literal[True]


class OnboardingMasterAccountStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=3, max_length=160)
    reporting_mode: Literal[
        "administrative_only", "consolidated_children", "standalone_investable"
    ]
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    history_start_date: date
    member_account_ids: list[str] = Field(default_factory=list, max_length=100)


class OnboardingMasterContractApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: Literal[True]


class DerivedStudioFilteredStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=3, max_length=160)
    source_account_id: str = Field(min_length=3, max_length=32)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    history_start_date: date
    excluded_countries: list[str] = Field(default_factory=list, max_length=32)
    excluded_exchanges: list[str] = Field(default_factory=list, max_length=64)
    excluded_currencies: list[str] = Field(default_factory=list, max_length=32)
    opening_cash: dict[str, float]
    allowed_cash_currencies: list[str] = Field(min_length=1, max_length=16)
    include_actual_interest: bool = True


class DerivedStudioCompositeSleeveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(min_length=3, max_length=80)
    coefficient: float


class DerivedStudioCompositeStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject_id: str = Field(min_length=3, max_length=64)
    display_name: str = Field(min_length=3, max_length=160)
    base_currency: str = Field(pattern=r"^[A-Z]{3}$")
    normalization_date: date
    constant_cash: float
    market_carry_days: int = Field(default=7, ge=0, le=14)
    components: list[DerivedStudioCompositeSleeveRequest] = Field(
        min_length=1, max_length=24
    )


class OnboardingExternalDataApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure_version: Literal["yahoo-ticker-disclosure-v1"]
    scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: Literal[True]


class OnboardingPreviewApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: Literal[True]


class OnboardingHistoricalReportApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: Literal[True]


class OnboardingPresentationIdentityApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    layout_policy: Literal["platform_standard"] = "platform_standard"
    approved: Literal[True]


class OnboardingActivationApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmation: str = Field(min_length=1, max_length=120)


@contextmanager
def _database():
    import psycopg

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The website database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row, connect_timeout=10,
                         options="-c statement_timeout=30000 -c lock_timeout=5000") as connection:
        connection.execute("SET TRANSACTION READ ONLY")
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, false)", (TENANT_ID,)
        )
        yield connection


@contextmanager
def _approval_database():
    import psycopg

    database_url = os.environ.get("APPROVAL_DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The approval database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, false)", (TENANT_ID,)
        )
        yield connection


@contextmanager
def _chat_database():
    """Use the narrowly granted write role for user-owned chat persistence."""

    import psycopg

    database_url = os.environ.get("APPROVAL_DATABASE_URL", "")
    if not database_url.startswith(("postgres://", "postgresql://")):
        raise RuntimeError("The AI conversation database connection is not configured")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        connection.execute(
            "SELECT set_config('app.tenant_id', %s, false)", (TENANT_ID,)
        )
        yield connection


def _chat_identity_sha256(user: dict[str, str]) -> str:
    value = user.get("object_id") or user.get("email") or ""
    digest = _identity_digest(value)
    if digest is None:
        raise HTTPException(status_code=403, detail="A stable signed-in identity is required")
    return digest


def _conversation_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation not found") from error


def _conversation_row(connection, conversation_id: str, identity_sha256: str):
    row = connection.execute(
        """
        SELECT conversation_id, subject_id, title, status, created_at, updated_at
        FROM ai_analyst_conversations
        WHERE tenant_id = %s AND conversation_id = %s
          AND owner_identity_sha256 = %s
        """,
        (TENANT_ID, conversation_id, identity_sha256),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return row


def _conversation_messages(connection, conversation_id: str) -> list[dict]:
    return connection.execute(
        """
        SELECT message_id, sequence_no, role, content_text, response_json,
               as_of_date, benchmark_id, correlation_id, created_at
        FROM ai_analyst_messages
        WHERE tenant_id = %s AND conversation_id = %s
        ORDER BY sequence_no
        """,
        (TENANT_ID, conversation_id),
    ).fetchall()


def _conversation_bundle(
    conversation_id: str, user: dict[str, str]
) -> tuple[dict, list[dict]]:
    normalized_id = _conversation_uuid(conversation_id)
    identity_sha256 = _chat_identity_sha256(user)
    with _database() as connection:
        conversation = _conversation_row(connection, normalized_id, identity_sha256)
        messages = _conversation_messages(connection, normalized_id)
    return dict(conversation), [dict(message) for message in messages]


def _assistant_response_for_download(
    conversation_id: str, message_id: str, user: dict[str, str]
) -> dict:
    normalized_conversation = _conversation_uuid(conversation_id)
    normalized_message = _conversation_uuid(message_id)
    identity_sha256 = _chat_identity_sha256(user)
    with _database() as connection:
        _conversation_row(connection, normalized_conversation, identity_sha256)
        row = connection.execute(
            """
            SELECT response_json
            FROM ai_analyst_messages
            WHERE tenant_id = %s AND conversation_id = %s AND message_id = %s
              AND role = 'assistant'
            """,
            (TENANT_ID, normalized_conversation, normalized_message),
        ).fetchone()
    if row is None or not isinstance(row["response_json"], dict):
        raise HTTPException(status_code=404, detail="Generated analysis not found")
    return dict(row["response_json"])


def _ensure_admin_updates_conversation(connection) -> str:
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
        "SELECT conversation_id FROM admin_chat_conversations WHERE source_key = %s",
        (SYSTEM_UPDATES_SOURCE_KEY,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail="Administrator updates channel is unavailable")
    conversation_id = str(row["conversation_id"])
    if created is not None:
        welcome = {
            "title": "Continuous monitoring is active",
            "message": (
                "I am online and monitoring deterministic system checks. I will start a message "
                "here when I detect a new incident or an owner decision is required."
            ),
            "status_points": [],
            "proposed_action_type": "none",
            "action_reason": "",
            "requires_owner_approval": False,
            "limitations": [
                "Sensitive and unsupported changes remain subject to explicit owner approval and implementation controls."
            ],
            "proactive": True,
        }
        connection.execute(
            """
            INSERT INTO admin_chat_messages(
                message_id, conversation_id, sequence_no, role, message_kind,
                content_text, response_json
            ) VALUES (%s, %s, 1, 'administrator', 'proactive_update', %s, %s)
            """,
            (str(uuid.uuid4()), conversation_id, welcome["message"], json.dumps(welcome)),
        )
        connection.commit()
    return conversation_id


def _admin_conversation_row(connection, conversation_id: str):
    normalized = _conversation_uuid(conversation_id)
    row = connection.execute(
        """
        SELECT conversation_id, title, initiated_by, source_key, status,
               owner_last_read_sequence, created_at, updated_at
        FROM admin_chat_conversations
        WHERE conversation_id = %s
        """,
        (normalized,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Administrator conversation not found")
    return row


def _admin_messages(connection, conversation_id: str) -> list[dict]:
    return connection.execute(
        """
        SELECT message_id, sequence_no, role, message_kind, content_text,
               response_json, approval_id, incident_id, correlation_id, created_at
        FROM admin_chat_messages WHERE conversation_id = %s ORDER BY sequence_no
        """,
        (conversation_id,),
    ).fetchall()


def _admin_structured_sources(connection) -> list[dict]:
    control = connection.execute(
        """
        SELECT enabled, kill_switch, policy_version, production_active,
               automatic_rollback_enabled, rollback_failure_threshold,
               monthly_review_due_at, updated_at
        FROM autonomy_control WHERE tenant_id = app_tenant_id()
        """
    ).fetchone()
    latest_run = connection.execute(
        """
        SELECT completed_at, status, finding_count, action_count, model_used,
               summary, error_code FROM autonomous_agent_runs
        ORDER BY agent_run_id DESC LIMIT 1
        """
    ).fetchone()
    incidents = connection.execute(
        """
        SELECT incident_key, check_id, severity, status, description,
               last_seen_at, occurrence_count FROM system_incidents
        WHERE status IN ('open', 'acknowledged')
        ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                 last_seen_at DESC LIMIT 20
        """
    ).fetchall()
    actions = connection.execute(
        """
        SELECT action_type, target_job, status, reason, created_at, executed_at, error_code
        FROM autonomous_actions ORDER BY action_id DESC LIMIT 10
        """
    ).fetchall()
    recovery = connection.execute(
        """
        SELECT app_name, consecutive_portal_failures, latest_checked_revision,
               last_failure_at, last_success_at, updated_at
        FROM autonomy_recovery_state ORDER BY updated_at DESC LIMIT 1
        """
    ).fetchone()
    review = connection.execute(
        """
        SELECT review_month, due_at, status, created_at, approved_at
        FROM autonomy_authority_reviews ORDER BY review_month DESC LIMIT 1
        """
    ).fetchone()
    return [
        {"source_id": "autonomy-control", "data": dict(control or {})},
        {"source_id": "latest-supervisor-run", "data": dict(latest_run or {})},
        {"source_id": "open-incidents", "data": [dict(item) for item in incidents]},
        {"source_id": "recent-autonomous-actions", "data": [dict(item) for item in actions]},
        {"source_id": "portal-recovery-state", "data": dict(recovery or {})},
        {"source_id": "latest-authority-review", "data": dict(review or {})},
        {
            "source_id": "controlled-action-catalog",
            "data": action_catalog(),
        },
    ]


@app.get("/assets/fonts/{name}", include_in_schema=False)
def web_font(name: str) -> Response:
    # Self-hosted so the CSP (font-src falls back to 'self') allows it.
    if name not in WEB_FONT_FILES:
        raise HTTPException(status_code=404, detail="Font not found")
    return Response(
        web_font_bytes(name),
        media_type="font/woff2",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readiness():
    from .platform_readiness import database_readiness
    return database_readiness()


def _parse_as_of(as_of: str | None) -> str | None:
    if as_of is None:
        return None
    try:
        return date.fromisoformat(as_of).isoformat()
    except ValueError as error:
        raise HTTPException(
            status_code=400, detail="as_of must be a valid date in YYYY-MM-DD format"
        ) from error


def _trade_filter_values(
    date_from: str | None,
    date_to: str,
    symbol: str | None,
    side: str | None,
) -> tuple[str, str, str | None, str | None]:
    start = _parse_as_of(date_from) or "2025-01-20"
    end = _parse_as_of(date_to)
    if end is None:
        raise HTTPException(status_code=400, detail="Trade end date is required")
    if start > end:
        raise HTTPException(
            status_code=400, detail="Trade start date cannot be after the end date"
        )
    normalized_symbol = (symbol or "").strip()
    if len(normalized_symbol) > 64 or any(ord(character) < 32 for character in normalized_symbol):
        raise HTTPException(status_code=400, detail="Invalid trade symbol filter")
    normalized_side = (side or "").strip().upper()
    if normalized_side and normalized_side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="Trade side must be BUY or SELL")
    return start, end, normalized_symbol or None, normalized_side or None


def _cash_filter_values(
    date_from: str | None,
    date_to: str,
) -> tuple[str, str]:
    start = _parse_as_of(date_from) or "2025-01-20"
    end = _parse_as_of(date_to)
    if end is None:
        raise HTTPException(status_code=400, detail="Cash activity end date is required")
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="Cash activity start date cannot be after the end date",
        )
    return start, end


def _comparison_rows(records: list[dict]) -> list[ComparisonRow]:
    return [
        ComparisonRow(
            observation_date=row["observation_date"],
            portfolio_value=float(row["portfolio_value"]),
            benchmark_level=float(row["benchmark_level"]),
            normalized_portfolio=float(row["normalized_portfolio"]),
            normalized_benchmark=float(row["normalized_benchmark"]),
            portfolio_return=(
                float(row["portfolio_return"])
                if row["portfolio_return"] is not None
                else None
            ),
            benchmark_return=(
                float(row["benchmark_return"])
                if row["benchmark_return"] is not None
                else None
            ),
            excess_return=(
                float(row["excess_return"])
                if row["excess_return"] is not None
                else None
            ),
        )
        for row in records
    ]


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _portfolio_access_context(
    account_id: str, user: dict[str, object]
) -> dict[str, object]:
    """Resolve account metadata and fail closed for onboarding previews.

    The original pilot remains available without an extra lookup so existing
    production requests keep their current failure surface. Every additional
    subject is governed by the onboarding registry and feature states.
    """
    database_authorization = user.get("authorization_source") == "database"
    if account_id == PILOT_ACCOUNT_ID and not database_authorization:
        return {
            "subject_id": account_id,
            "display_name": PILOT_ACCOUNT_DISPLAY_NAME,
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
            "lifecycle_status": "active",
            "preview_mode": False,
            "feature_states": {
                "data_ingestion": "active",
                "website_analytics": "active",
                "ai_analyst": "active",
                "factsheet": "active",
                "presentation": "active",
                "viewer_access": "active",
            },
        }

    role = str(user.get("role", "viewer"))
    # Do not reveal whether an unpublished subject exists to ordinary viewers.
    if not database_authorization and role not in {"owner", "onboarding_operator"}:
        raise HTTPException(status_code=404, detail="Portfolio not available")
    with _database() as connection:
        subject = connection.execute(
            """
            SELECT subject_id, display_name, base_currency, reporting_currency, subject_type,
                   history_start_date, lifecycle_status
            FROM platform_subjects
            WHERE tenant_id = %s AND subject_id = %s
            """,
            (TENANT_ID, account_id),
        ).fetchone()
        if subject is None:
            raise HTTPException(status_code=404, detail="Portfolio not available")
        missing_contract_fields = [
            field
            for field in ("display_name", "reporting_currency", "history_start_date")
            if subject[field] in (None, "")
        ]
        if missing_contract_fields:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Portfolio contract incomplete: "
                    + ", ".join(missing_contract_fields)
                ),
            )
        feature_rows = connection.execute(
            """
            SELECT feature_key, feature_state
            FROM subject_feature_states
            WHERE tenant_id = %s AND subject_id = %s
            """,
            (TENANT_ID, account_id),
        ).fetchall()
        if database_authorization:
            levels = subject_access_levels(
                _portable_database_connection(connection),
                tenant_id=TENANT_ID,
                object_id=str(user.get("object_id", "")),
                subject_id=account_id,
            )
            roles = tuple(str(value) for value in user.get("roles", []))
            if not may_view_subject(
                roles=roles,
                access_levels=levels,
                lifecycle_status=str(subject["lifecycle_status"]),
            ):
                raise HTTPException(status_code=404, detail="Portfolio not available")
    feature_states = {
        str(row["feature_key"]): str(row["feature_state"]) for row in feature_rows
    }
    lifecycle_status = str(subject["lifecycle_status"])
    preview_mode = lifecycle_status != "active"
    if preview_mode:
        if feature_states.get("data_ingestion") not in {"preview", "active"}:
            raise HTTPException(status_code=404, detail="Portfolio not available")
    elif feature_states.get("website_analytics") != "active":
        raise HTTPException(status_code=404, detail="Portfolio not available")
    if account_id == 'alpha-world-ms':
        with _database() as connection:
            try:
                require_ms_hkd_contract(connection,tenant_id=TENANT_ID)
            except ValueError as error:
                raise HTTPException(status_code=409,detail=str(error)) from error
    return {
        **dict(subject),
        "history_start_date": str(subject["history_start_date"]),
        "preview_mode": preview_mode,
        "feature_states": feature_states,
    }


def _authorized_subject_ids(
    user: dict[str, object], *, feature_key: str | None = None
) -> list[str]:
    """Return active subjects the signed-in identity may use.

    Legacy environment allow-lists deliberately retain pilot-only behaviour.
    Database authorization expands access only through an effective role or an
    explicit subject entitlement; unpublished onboarding previews are never
    returned by this reader-facing helper.
    """
    if user.get("authorization_source") != "database":
        return [PILOT_ACCOUNT_ID]

    roles = {str(value) for value in user.get("roles", [])}
    privileged = bool(roles & {"owner", "platform_admin"})
    parameters: list[object] = [TENANT_ID]
    feature_join = ""
    feature_predicate = ""
    if feature_key is not None:
        feature_join = """
            JOIN subject_feature_states feature
              ON feature.tenant_id = subject.tenant_id
             AND feature.subject_id = subject.subject_id
             AND feature.feature_key = %s
        """
        feature_predicate = "AND feature.feature_state = 'active'"
        parameters.append(feature_key)

    entitlement_join = ""
    entitlement_predicate = ""
    if not privileged:
        entitlement_join = """
            JOIN user_account_access access
              ON access.tenant_id = subject.tenant_id
             AND access.subject_id = subject.subject_id
             AND lower(access.entra_object_id) = %s
        """
        parameters.append(str(user.get("object_id", "")))
        entitlement_predicate = """
            AND access.access_level IN ('read', 'report', 'analyze', 'approve', 'admin')
        """

    with _database() as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT subject.subject_id
            FROM platform_subjects subject
            {feature_join}
            {entitlement_join}
            WHERE subject.tenant_id = %s
              AND subject.lifecycle_status = 'active'
              {feature_predicate}
              {entitlement_predicate}
            ORDER BY subject.subject_id
            """,
            tuple(parameters[1:] + parameters[:1]),
        ).fetchall()
    return [str(row["subject_id"]) for row in rows]


def _account_directory(user: dict[str, object]) -> list[dict[str, object]]:
    if user.get("authorization_source") != "database":
        return [
            {
                "subject_id": PILOT_ACCOUNT_ID,
                "display_name": PILOT_ACCOUNT_DISPLAY_NAME,
                "reporting_currency": "USD",
                "history_start_date": "2025-01-20",
            }
        ]

    subject_ids = _authorized_subject_ids(user, feature_key="viewer_access")
    if not subject_ids:
        return []
    placeholders = ", ".join(["%s"] * len(subject_ids))
    with _database() as connection:
        rows = connection.execute(
            f"""
            SELECT subject_id, display_name, reporting_currency, history_start_date
            FROM platform_subjects
            WHERE tenant_id = %s AND subject_id IN ({placeholders})
            ORDER BY display_name, subject_id
            """,
            (TENANT_ID, *subject_ids),
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/portfolio/{account_id}/available-dates")
def available_dates(
    account_id: str, user: dict[str, str] = Depends(_authorized_user)
) -> dict:
    access = _portfolio_access_context(account_id, user)
    with _database() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT snapshot_date
            FROM subject_nav_series
            WHERE tenant_id = %s AND subject_id = %s
              AND currency = %s
              AND snapshot_date >= %s
            ORDER BY snapshot_date DESC
            """,
            (TENANT_ID, account_id, access['reporting_currency'], access["history_start_date"]),
        ).fetchall()
    dates = [row["snapshot_date"] for row in rows]
    return {
        "account_id": account_id,
        "dates": dates,
        "latest": dates[0] if dates else None,
        "earliest": dates[-1] if dates else None,
        "count": len(dates),
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/summary")
def portfolio_summary(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        account = {
            "account_id": account_id,
            "base_currency": access.get("base_currency") or access.get("reporting_currency"),
            "account_type": access.get("account_type") or access.get("subject_type", "physical_account"),
            "last_seen_at": None,
        }
        nav = connection.execute(
            """
            SELECT MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS last_date,
                   COUNT(*) AS observations
            FROM subject_nav_series
            WHERE tenant_id = %s AND subject_id = %s
              AND currency = %s
              AND (%s::text IS NULL OR snapshot_date <= %s)
            """,
            (TENANT_ID, account_id, access['reporting_currency'], requested_as_of, requested_as_of),
        ).fetchone()
        latest = connection.execute(
            """
            SELECT snapshot_date, currency, total_text
            FROM subject_nav_series
            WHERE tenant_id = %s AND subject_id = %s
              AND currency = %s
              AND (%s::text IS NULL OR snapshot_date <= %s)
            ORDER BY snapshot_date DESC LIMIT 1
            """,
            (TENANT_ID, account_id, access['reporting_currency'], requested_as_of, requested_as_of),
        ).fetchone()
        if access.get("subject_type", "physical_account") == "composite_portfolio":
            holdings = connection.execute(
                """
                SELECT COUNT(*) AS positions
                FROM synthetic_portfolio_components
                WHERE tenant_id = %s AND portfolio_id = %s
                  AND base_currency = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM synthetic_portfolio_components
                      WHERE tenant_id = %s AND portfolio_id = %s
                        AND base_currency = %s
                        AND (%s::text IS NULL OR snapshot_date <= %s)
                  )
                """,
                (TENANT_ID, account_id, access['reporting_currency'], TENANT_ID, account_id, access['reporting_currency'], requested_as_of, requested_as_of),
            ).fetchone()
        else:
            holdings = connection.execute(
                """
                SELECT COUNT(*) AS positions
                FROM subject_position_series
                WHERE tenant_id = %s AND subject_id = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM subject_position_series
                      WHERE tenant_id = %s AND subject_id = %s
                        AND (%s::text IS NULL OR snapshot_date <= %s)
                  )
                  AND COALESCE(NULLIF(quantity_text, ''), '0')::numeric <> 0
                """,
                (TENANT_ID, account_id, TENANT_ID, account_id, requested_as_of, requested_as_of),
            ).fetchone()
    return {
        "account": account,
        "nav_coverage": nav,
        "latest_nav": latest,
        "current_positions": holdings["positions"],
        "requested_as_of": requested_as_of,
        "effective_as_of": latest["snapshot_date"] if latest else None,
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/performance")
def performance(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        rows = connection.execute(
            """
            SELECT snapshot_date, total_text
            FROM subject_nav_series
            WHERE tenant_id = %s AND subject_id = %s AND snapshot_date >= %s
              AND currency = %s
              AND (%s::text IS NULL OR snapshot_date <= %s)
            ORDER BY snapshot_date
            """,
            (
                TENANT_ID,
                account_id,
                access["history_start_date"],
                access['reporting_currency'],
                requested_as_of,
                requested_as_of,
            ),
        ).fetchall()
    values = [
        {"date": row["snapshot_date"], "nav": float(row["total_text"])}
        for row in rows
        if row["total_text"] not in (None, "")
    ]
    return {
        "account_id": account_id,
        "currency": access["reporting_currency"],
        "as_of": requested_as_of,
        "series": values,
    }


COMPOSITE_FLOWS_REASON = (
    "Composite portfolios are synthetic model sleeves without investor "
    "contributions or withdrawals; returns use NAV changes only."
)


def _subject_external_flows(connection, account_id: str, access: dict) -> dict[str, float] | None:
    """Net external flows per date in the NAV currency, or None for composites.

    Flows come from the same reader as the website comparison. Physical and
    filtered flows are in the account base currency, so a subject reporting
    in another currency fails closed instead of mixing currencies.
    """
    subject_type = str(access.get("subject_type") or "physical_account")
    if subject_type == "composite_portfolio":
        return None
    base_currency = access.get("base_currency")
    if base_currency and str(base_currency).upper() != str(access["reporting_currency"]).upper():
        raise HTTPException(
            status_code=409,
            detail="External flows are recorded in the base currency, which differs from the reporting currency.",
        )
    from .provisional_benchmark import _external_flows

    try:
        return _external_flows(
            _portable_database_connection(connection), account_id, subject_type=subject_type
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


def _subject_nav_values(connection, account_id: str, access: dict, through: str | None) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT snapshot_date, total_text
        FROM subject_nav_series
        WHERE tenant_id = %s AND subject_id = %s AND snapshot_date >= %s
          AND currency = """
        + ACTIVE_NAV_CURRENCY_SQL
        + """
          AND (%s::text IS NULL OR snapshot_date <= %s)
        ORDER BY snapshot_date
        """,
        (TENANT_ID, account_id, access["history_start_date"], through, through),
    ).fetchall()
    return [
        (str(row["snapshot_date"]), row["total_text"])
        for row in rows
        if row["total_text"] not in (None, "")
    ]


@app.get("/api/portfolio/{account_id}/performance/periods")
def performance_periods_view(
    account_id: str,
    as_of: str | None = None,
    start: str | None = None,
    end: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    custom_start, custom_end = _parse_as_of(start), _parse_as_of(end)
    if custom_start and custom_end and custom_start > custom_end:
        raise HTTPException(status_code=400, detail="The custom start date cannot be after the end date")
    with _database() as connection:
        nav = _subject_nav_values(connection, account_id, access, requested_as_of)
        flows = _subject_external_flows(connection, account_id, access)
    try:
        result = calculate_performance_periods(
            nav,
            flows or {},
            as_of=requested_as_of,
            custom_start=custom_start,
            custom_end=custom_end,
        )
    except PerformanceInputError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "account_id": account_id,
        "currency": access["reporting_currency"],
        **result,
        "requested_as_of": requested_as_of,
        "flows_applicable": flows is not None,
        "flows_message": COMPOSITE_FLOWS_REASON if flows is None else "",
        "methodology": (
            "Time-weighted returns chain-link daily Modified Dietz returns between NAV "
            "observations; flows dated on an observation day are inside that day's NAV. "
            "Each period is valued from the last NAV before it starts. IRR is an annualised "
            "XIRR on the opening value, external flows and closing value."
        ),
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/analytics")
def portfolio_analytics_view(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Monthly returns calendar, drawdowns, return statistics and rolling risk."""
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        nav = _subject_nav_values(connection, account_id, access, requested_as_of)
        flows = _subject_external_flows(connection, account_id, access)
    try:
        result = calculate_portfolio_analytics(nav, flows or {}, as_of=requested_as_of)
    except PerformanceInputError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "account_id": account_id,
        "currency": access["reporting_currency"],
        "requested_as_of": requested_as_of,
        "flows_applicable": flows is not None,
        **result,
    }


@app.get("/api/portfolio/{account_id}/attribution")
def monthly_attribution_view(
    account_id: str,
    month: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Contribution to return by holding and sector for one calendar month."""
    from .monthly_reporting_analytics import build_monthly_reporting_analytics

    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month or ""):
        raise HTTPException(status_code=400, detail="Month must be YYYY-MM")
    if requested_as_of and month > requested_as_of[:7]:
        raise HTTPException(status_code=400, detail="Month is after the as-of date")
    year, number = int(month[:4]), int(month[5:])
    next_month = date(year + number // 12, number % 12 + 1, 1)
    period_end = (next_month - timedelta(days=1)).isoformat()
    try:
        with _database() as connection:
            result = build_monthly_reporting_analytics(
                _portable_database_connection(connection), subject_id=account_id,
                period_end=period_end, reporting_currency=str(access["reporting_currency"]),
            )
    except (RuntimeError, ValueError) as error:
        return {"account_id": account_id, "month": month, "available": False,
                "quality_status": "fail", "message": str(error)}
    return {
        "account_id": account_id,
        "month": month,
        "quality_status": result["quality_status"],
        "message": result["message"],
        **result["data"],
    }


@app.get("/api/portfolio/{account_id}/trading-activity")
def trading_activity_view(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Monthly traded value, turnover, commissions and realised P&L in the reporting currency."""
    from .holdings_view import fx_history, reporting_fx
    from .subject_exports import subject_export

    access = _portfolio_access_context(account_id, user)
    subject_type = str(access.get("subject_type") or "physical_account")
    if subject_type == "composite_portfolio":
        return {"account_id": account_id, "available": False, "not_applicable": True,
                "message": "A composite portfolio has no independently executed trades."}
    requested_as_of = _parse_as_of(as_of)
    target = str(access["reporting_currency"]).upper()
    with _database() as connection:
        nav = _subject_nav_values(connection, account_id, access, requested_as_of)
        if not nav:
            return {"account_id": account_id, "available": False, "message": "No NAV history is available."}
        effective = requested_as_of or nav[-1][0]
        _, trades, _, _ = subject_export(
            connection, tenant_id=TENANT_ID, subject_id=account_id, access=access,
            dataset="trades", requested_as_of=effective, from_date=None, symbol=None, side=None,
            cash_filter=_cash_filter_values, trade_filter=_trade_filter_values,
        )
        fx_account = _holdings_fx_account(connection, account_id, subject_type)
        fx_rows = [] if fx_account is None else connection.execute(
            """
            SELECT rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            WHERE account_id = %s AND rate_date <= %s
            ORDER BY from_currency, to_currency, rate_date
            """,
            (fx_account, effective),
        ).fetchall()
    history = fx_history(fx_rows)

    def rate(currency: str, day: str) -> float | None:
        quote, _ = reporting_fx(history, currency, target, day)
        return float(quote["rate"]) if quote else None

    nav_values = {}
    for day, value in nav:
        try:
            nav_values[day] = float(value)
        except (TypeError, ValueError):
            continue
    result = calculate_trading_activity([dict(row) for row in trades], nav_values, rate, as_of=effective)
    return {"account_id": account_id, "currency": target, "effective_as_of": effective, **result}


@app.get("/api/portfolio/{account_id}/holdings-correlation")
def holdings_correlation_view(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Correlation of daily price returns between the ten largest holdings."""
    access = _portfolio_access_context(account_id, user)
    subject_type = str(access.get("subject_type") or "physical_account")
    if subject_type == "composite_portfolio":
        return {"account_id": account_id, "available": False, "not_applicable": True,
                "message": "Composite sleeves have no security-level price history."}
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        rows = _latest_subject_holdings(connection, account_id, requested_as_of)
        if not rows:
            return {"account_id": account_id, "available": False, "message": "No holdings are available."}
        view = _enrich_subject_holdings(
            connection, account_id, rows, subject_type=subject_type,
            reporting_currency=str(access["reporting_currency"]),
            nav_currency=str(access["reporting_currency"]),
        )
        ranked = sorted(
            (row for row in view["holdings"] if row.get("weight") is not None),
            key=lambda row: -abs(float(row["weight"])),
        )[:CORRELATION_HOLDINGS]
        labels = {str(row["conid"]): str(row.get("symbol") or row["conid"]) for row in ranked}
        snapshot = str(rows[0]["snapshot_date"])
        start = (date.fromisoformat(snapshot[:10]) - timedelta(days=550)).isoformat()
        price_rows = connection.execute(
            """
            SELECT snapshot_date, conid::text AS conid, mark_price_text
            FROM subject_position_series
            WHERE tenant_id = %s AND subject_id = %s AND conid::text = ANY(%s)
              AND snapshot_date BETWEEN %s AND %s
            ORDER BY snapshot_date
            """,
            (TENANT_ID, account_id, list(labels), start, snapshot),
        ).fetchall() if labels else []
    prices: dict[str, dict[str, float]] = {}
    for row in price_rows:
        try:
            value = float(row["mark_price_text"])
        except (TypeError, ValueError):
            continue
        prices.setdefault(str(row["conid"]), {})[str(row["snapshot_date"])] = value
    return {
        "account_id": account_id,
        "snapshot_date": snapshot,
        **calculate_holdings_correlation(prices, labels),
    }


@app.get("/api/portfolio/{account_id}/flows")
def external_flows_statement(
    account_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    start = _parse_as_of(from_date) or str(access["history_start_date"])
    end = _parse_as_of(to_date)
    if end is not None and start > end:
        raise HTTPException(status_code=400, detail="from_date cannot be after to_date")
    with _database() as connection:
        flows = _subject_external_flows(connection, account_id, access)
    statement = flow_statement(flows or {}, start, end)
    return {
        "account_id": account_id,
        "currency": access["reporting_currency"],
        **statement,
        "applicable": flows is not None,
        "message": COMPOSITE_FLOWS_REASON if flows is None else "",
        "viewer": user["email"],
    }


def _latest_subject_holdings(
    connection, subject_id: str, requested_as_of: str | None
) -> list[dict]:
    """Latest non-zero physical or filtered positions with sector labels."""
    return [
        dict(row)
        for row in connection.execute(
            """
            SELECT p.snapshot_date, p.conid, i.symbol, i.description, p.currency,
                   p.quantity_text, p.mark_price_text, p.market_value_text,
                   p.cost_basis_text, p.unrealized_pnl_text,
                   COALESCE(ic.sector, 'Unclassified') AS sector
            FROM subject_position_series p
            LEFT JOIN instruments i ON i.conid = p.conid
            LEFT JOIN instrument_classifications ic
              ON ic.conid = p.conid AND ic.provider = %s
            WHERE p.tenant_id = %s AND p.subject_id = %s
              AND p.snapshot_date = (
                  SELECT MAX(snapshot_date) FROM subject_position_series
                  WHERE tenant_id = %s AND subject_id = %s
                    AND (%s::text IS NULL OR snapshot_date <= %s)
              )
              AND COALESCE(NULLIF(p.quantity_text, ''), '0')::numeric <> 0
            ORDER BY ABS(COALESCE(NULLIF(p.market_value_text, ''), '0')::numeric) DESC
            """,
            (
                HOLDINGS_SECTOR_PROVIDER,
                TENANT_ID,
                subject_id,
                TENANT_ID,
                subject_id,
                requested_as_of,
                requested_as_of,
            ),
        ).fetchall()
    ]


def _holdings_fx_account(connection, subject_id: str, subject_type: str) -> str | None:
    """Apply the reporting FX source-account rule to a holdings subject.

    Physical accounts use their own FX history; filtered portfolios use their
    approved source account, matching normalize_collected_report_holdings.
    """
    if subject_type != "filtered_portfolio":
        return subject_id
    subject = connection.execute(
        "SELECT source_account_id FROM platform_subjects "
        "WHERE tenant_id = %s AND subject_id = %s",
        (TENANT_ID, subject_id),
    ).fetchone()
    if subject and subject["source_account_id"]:
        return str(subject["source_account_id"])
    definition = connection.execute(
        "SELECT configuration_json ->> 'source_account_id' AS source_account_id "
        "FROM subject_calculation_definitions WHERE subject_id = %s "
        "AND subject_type = 'filtered_portfolio' "
        "AND approval_status IN ('validated', 'approved') "
        "ORDER BY definition_version DESC, created_at DESC LIMIT 1",
        (subject_id,),
    ).fetchone()
    return str(definition["source_account_id"]) if definition and definition["source_account_id"] else None


def _enrich_subject_holdings(
    connection,
    subject_id: str,
    rows: list[dict],
    *,
    subject_type: str,
    reporting_currency: str,
    nav_currency: str | None,
) -> dict:
    """Add cost, P&L, reporting values, weights and sector to holdings rows.

    ``nav_currency`` selects the NAV used as the weight denominator; pass None
    to weight on gross holdings (the combined view, where NAV is not summed).
    """
    if not rows:
        return enrich_holdings([], reporting_currency=reporting_currency)
    snapshot_date = str(rows[0]["snapshot_date"])
    fx_account = _holdings_fx_account(connection, subject_id, subject_type)
    fx_rows = []
    if fx_account is not None:
        fx_rows = connection.execute(
            """
            SELECT rate_date, from_currency, to_currency, rate_text
            FROM fx_rates
            WHERE account_id = %s AND rate_date <= %s
            ORDER BY from_currency, to_currency, rate_date
            """,
            (fx_account, snapshot_date),
        ).fetchall()
    nav_total = None
    if nav_currency is not None:
        nav = connection.execute(
            """
            SELECT total_text FROM subject_nav_series
            WHERE tenant_id = %s AND subject_id = %s AND snapshot_date = %s
              AND currency = %s
            """,
            (TENANT_ID, subject_id, snapshot_date, nav_currency),
        ).fetchone()
        nav_total = nav["total_text"] if nav else None
    view = enrich_holdings(
        rows,
        reporting_currency=reporting_currency,
        fx_rows=fx_rows,
        nav_total=nav_total,
        fx_unavailable_reason=None if fx_account is not None else "fx_source_unavailable",
    )
    view["fx_account_id"] = fx_account
    return view


def _attach_holdings_rsi(connection, subject_id: str, rows: list[dict]) -> None:
    """Add 14-day Wilder RSI from each holding's stored daily marks, in place."""
    if not rows:
        return
    snapshot = str(rows[0]["snapshot_date"])[:10]
    start = (date.fromisoformat(snapshot) - timedelta(days=RSI_LOOKBACK_DAYS)).isoformat()
    held = {str(row["conid"]) for row in rows}
    prices: dict[str, dict[str, float]] = {}
    for mark in connection.execute(
        """
        SELECT snapshot_date, conid::text AS conid, mark_price_text
        FROM subject_position_series
        WHERE tenant_id = %s AND subject_id = %s
          AND snapshot_date BETWEEN %s AND %s
        """,
        (TENANT_ID, subject_id, start, snapshot),
    ).fetchall():
        conid = str(mark["conid"])
        if conid not in held:
            continue
        try:
            value = float(mark["mark_price_text"])
        except (TypeError, ValueError):
            continue
        prices.setdefault(conid, {})[str(mark["snapshot_date"])[:10]] = value
    for row in rows:
        row.update(holding_rsi(prices.get(str(row["conid"]), {}), snapshot))


@app.get("/api/portfolio/{account_id}/holdings")
def holdings(
    account_id: str,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        if access.get("subject_type", "physical_account") == "composite_portfolio":
            rows = connection.execute(
                """
                SELECT snapshot_date, component_id AS conid,
                       component_id AS symbol, component_name AS description,
                       base_currency AS currency,
                       coefficient, source_currency, source_value,
                       fx_rate_to_base, base_value, normalized_value,
                       contribution::text AS market_value_text,
                       component_type, direction, observation_date
                FROM synthetic_portfolio_components
                WHERE tenant_id = %s AND portfolio_id = %s
                  AND base_currency = %s
                  AND snapshot_date = (
                      SELECT MAX(snapshot_date) FROM synthetic_portfolio_components
                      WHERE tenant_id = %s AND portfolio_id = %s
                        AND base_currency = %s
                        AND (%s::text IS NULL OR snapshot_date <= %s)
                  )
                ORDER BY ABS(contribution) DESC
                """,
                (TENANT_ID, account_id, access['reporting_currency'], TENANT_ID, account_id, access['reporting_currency'], requested_as_of, requested_as_of),
            ).fetchall()
            try:
                rows=[synthetic_exposure(row) for row in rows]
            except ValueError as error:
                raise HTTPException(status_code=409,detail=str(error)) from error
        else:
            rows = _latest_subject_holdings(connection, account_id, requested_as_of)
            view = _enrich_subject_holdings(
                connection,
                account_id,
                rows,
                subject_type=str(access.get("subject_type") or "physical_account"),
                reporting_currency=str(access["reporting_currency"]),
                nav_currency=str(access["reporting_currency"]),
            )
            _attach_holdings_rsi(connection, account_id, view["holdings"])
            return {
                "account_id": account_id,
                "requested_as_of": requested_as_of,
                "snapshot_date": rows[0]["snapshot_date"] if rows else None,
                **view,
                "concentration": holdings_concentration(view),
            }
    return {
        "account_id": account_id,
        "requested_as_of": requested_as_of,
        "snapshot_date": rows[0]["snapshot_date"] if rows else None,
        "holdings": rows,
    }


def _live_access(account_id: str, user: dict[str, object]) -> dict[str, object]:
    """Live data needs view access to the account and its live_view switch."""
    access = _portfolio_access_context(account_id, user)
    if not live_view_allowed(access, user):
        raise HTTPException(status_code=404, detail="Live data not available")
    return access


@app.get("/api/portfolio/{account_id}/live")
def live_summary(
    account_id: str, user: dict[str, object] = Depends(_authorized_user)
) -> dict:
    _live_access(account_id, user)
    with _database() as connection:
        return read_summary(connection, TENANT_ID, account_id, datetime.now(timezone.utc))


@app.get("/api/portfolio/{account_id}/live/positions")
def live_positions(
    account_id: str, user: dict[str, object] = Depends(_authorized_user)
) -> dict:
    _live_access(account_id, user)
    with _database() as connection:
        return read_positions(connection, TENANT_ID, account_id)


@app.get("/api/portfolio/{account_id}/live/trades")
def live_trades(
    account_id: str, user: dict[str, object] = Depends(_authorized_user)
) -> dict:
    _live_access(account_id, user)
    with _database() as connection:
        return read_trades(connection, TENANT_ID, account_id, datetime.now(timezone.utc))


@app.get("/api/portfolio/{account_id}/sectors")
def sector_allocation(
    account_id: str,
    request: Request,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Sector weights from the same validated tool the AI analyst and factsheets use."""
    _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    context = AgentToolContext(
        tenant_id=TENANT_ID,
        actor_object_id=str(user.get("object_id") or user.get("email") or "viewer"),
        allowed_subject_ids=frozenset({account_id}),
        correlation_id=getattr(request.state, "correlation_id", str(uuid.uuid4())),
    )
    arguments = {"subject_id": account_id}
    if requested_as_of:
        arguments["as_of"] = requested_as_of
    try:
        with _database() as connection:
            result = execute_tool(
                _portable_database_connection(connection), context, "get_sector_allocation", arguments
            )
    except AgentToolError as error:
        if error.status_code in (401, 403):
            raise HTTPException(status_code=error.status_code, detail=error.code) from error
        return {
            "account_id": account_id,
            "requested_as_of": requested_as_of,
            "available": False,
            "allocation": [],
            "reason": str(error),
            "code": error.code,
        }
    return {
        "account_id": account_id,
        "requested_as_of": requested_as_of,
        "as_of": result["as_of"],
        "quality_status": result["quality_status"],
        **result["data"],
    }


@app.get("/api/accounts/overview")
def accounts_overview(user: dict[str, object] = Depends(_authorized_user)) -> dict:
    """Latest NAV, month-to-date and year-to-date return for each visible account."""
    accounts = []
    for subject in _account_directory(user):
        subject_id = str(subject["subject_id"])
        entry = {
            "account_id": subject_id,
            "currency": subject.get("reporting_currency"),
            "nav": None,
            "nav_date": None,
            "mtd": None,
            "ytd": None,
        }
        accounts.append(entry)
        try:
            access = _portfolio_access_context(subject_id, user)
            with _database() as connection:
                nav = _subject_nav_values(connection, subject_id, access, None)
                if not nav:
                    continue
                entry["currency"] = access["reporting_currency"]
                entry["nav_date"], latest = nav[-1]
                entry["nav"] = float(latest)
                flows = _subject_external_flows(connection, subject_id, access)
            periods = calculate_performance_periods(nav, flows or {})["periods"]
        except (HTTPException, PerformanceInputError, PostgresError, ValueError):
            continue
        for period in periods:
            if period["key"] in ("mtd", "ytd") and period["status"] not in ("fail", "empty"):
                entry[period["key"]] = period["twr"]
    return {"accounts": accounts}


@app.get("/api/holdings/combined")
def combined_holdings(
    currency: str = "USD",
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    """Aggregate the signed-in colleague's physical accounts by instrument.

    Only physical accounts are combined: filtered and composite portfolios are
    built from those accounts and would double count. Each account is
    converted with its own dated FX history, and an account is included only
    when the same access check as its own holdings page passes.
    """
    target = (currency or "").strip().upper()
    if len(target) != 3 or not target.isalpha():
        raise HTTPException(status_code=400, detail="currency must be a three-letter ISO code")
    requested_as_of = _parse_as_of(as_of)
    accounts = []
    skipped: list[dict[str, str]] = []
    for subject in _account_directory(user):
        subject_id = str(subject["subject_id"])
        try:
            access = _portfolio_access_context(subject_id, user)
        except HTTPException as error:
            if error.status_code == 404:
                continue
            if error.status_code == 409:
                skipped.append({"account_id": subject_id, "reason": str(error.detail)})
                continue
            raise
        if access.get("subject_type", "physical_account") != "physical_account":
            continue
        with _database() as connection:
            rows = _latest_subject_holdings(connection, subject_id, requested_as_of)
            view = _enrich_subject_holdings(
                connection,
                subject_id,
                rows,
                subject_type="physical_account",
                reporting_currency=target,
                nav_currency=None,
            )
        accounts.append(
            {
                "account_id": subject_id,
                "display_name": str(
                    subject.get("display_name") or access.get("display_name") or subject_id
                ),
                "snapshot_date": rows[0]["snapshot_date"] if rows else None,
                "holdings_count": len(rows),
                "fx_missing_conids": view["fx_missing_conids"],
                "holdings": view["holdings"],
            }
        )
    combined = combine_holdings(accounts, currency=target)
    return {
        "requested_as_of": requested_as_of,
        "accounts": [
            {key: value for key, value in account.items() if key != "holdings"}
            for account in accounts
        ],
        "skipped_accounts": skipped,
        **combined,
    }


@app.get("/api/portfolio/{account_id}/cash")
def cash_activity(
    account_id: str,
    as_of: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(to_date or as_of)
    with _database() as connection:
        effective_as_of = requested_as_of or connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s AND currency = " + ACTIVE_NAV_CURRENCY_SQL,
            (TENANT_ID, account_id),
        ).fetchone()["latest"]
        if effective_as_of is None:
            return {
                "account_id": account_id,
                "as_of": None,
                "balances_date": None,
                "balances": [],
                "activity_from": None,
                "activity_to": None,
                "total_count": 0,
                "transactions": [],
            }
        activity_from, activity_to = _cash_filter_values(
            from_date or str(access["history_start_date"]), effective_as_of
        )
        if account_id in ('alpha-world-ms','alpha-world-t'):
            if access.get('subject_type')!='composite_portfolio':
                raise HTTPException(status_code=409,detail='Model cash requires approved composite metadata')
            try:
                model=load_model_cash(connection,tenant_id=TENANT_ID,subject_id=account_id,
                    reporting_currency=access['reporting_currency'],as_of=effective_as_of)
            except (ValueError,TypeError,KeyError) as error:
                raise HTTPException(status_code=409,detail='Model cash is withheld because its approved inputs do not reconcile') from error
            return {'account_id':account_id,'as_of':effective_as_of,'balances_date':model['as_of'] if model else None,
                'balances':[],'activity_from':activity_from,'activity_to':activity_to,'total_count':0,'transactions':[],
                'viewer':user['email'],'model_cash':model,'activity_applicable':False,
                'message':model['message'] if model else 'No validated model cash observation is available on or before this date.'}
        balances_date = connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_cash_balance_series WHERE tenant_id = %s AND subject_id = %s AND snapshot_date <= %s",
            (TENANT_ID, account_id, effective_as_of),
        ).fetchone()["latest"]
        balances = connection.execute(
            """
            SELECT snapshot_date, currency, ending_cash_text, settled_cash_text,
                   fx_rate_to_base_text
            FROM subject_cash_balance_series
            WHERE tenant_id = %s AND subject_id = %s AND snapshot_date = %s
            ORDER BY currency
            """,
            (TENANT_ID, account_id, balances_date),
        ).fetchall()
        transactions = connection.execute(
            """
            SELECT effective_date, event_datetime, transaction_type, description,
                   amount_text, currency, conid
            FROM subject_cash_activity_series
            WHERE tenant_id = %s AND subject_id = %s
              AND included AND effective_date BETWEEN %s AND %s
            ORDER BY effective_date DESC, event_datetime DESC, source_key
            """,
            (TENANT_ID, account_id, activity_from, activity_to),
        ).fetchall()
    return {
        "account_id": account_id,
        "as_of": effective_as_of,
        "balances_date": balances_date,
        "balances": balances,
        "activity_from": activity_from,
        "activity_to": activity_to,
        "total_count": len(transactions),
        "transactions": transactions,
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/trades")
def trade_history(
    account_id: str,
    from_date: str | None = None,
    to_date: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    access = _portfolio_access_context(account_id, user)
    subject_type = access.get("subject_type", "physical_account")
    if subject_type == "composite_portfolio":
        return {
            "account_id": account_id,
            "date_from": from_date or str(access["history_start_date"]),
            "date_to": to_date,
            "symbol": symbol,
            "side": side,
            "total_count": 0,
            "returned_count": 0,
            "truncated": False,
            "trades": [],
            "not_applicable": True,
            "message": (
                "A composite portfolio has no independently executed trades. "
                "Use its component exposures and reconstructed cash activity."
            ),
            "viewer": user["email"],
        }
    requested_to = _parse_as_of(to_date)
    with _database() as connection:
        if subject_type == "filtered_portfolio":
            latest_sql, latest_params = (
                "SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s AND currency = %s",
                (TENANT_ID, account_id, access["reporting_currency"]),
            )
        else:
            latest_sql, latest_params = (
                "SELECT MAX(snapshot_date) AS latest FROM nav_snapshots WHERE account_id = %s AND asset_class = 'TOTAL'",
                (account_id,),
            )
        effective_to = requested_to or connection.execute(
            latest_sql, latest_params
        ).fetchone()["latest"]
        if effective_to is None:
            return {
                "account_id": account_id,
                "date_from": None,
                "date_to": None,
                "symbol": None,
                "side": None,
                "total_count": 0,
                "returned_count": 0,
                "truncated": False,
                "trades": [],
                "viewer": user["email"],
            }
        start, end, normalized_symbol, normalized_side = _trade_filter_values(
            from_date or str(access["history_start_date"]),
            effective_to,
            symbol,
            side,
        )
        if subject_type == "filtered_portfolio":
            from .subject_exports import filtered_trade_query

            sql, params = filtered_trade_query(
                tenant_id=TENANT_ID,
                subject_id=account_id,
                start=start,
                end=end,
                symbol=normalized_symbol,
                side=normalized_side,
                latest_first=True,
                limit=500,
            )
            records = connection.execute(sql, params).fetchall()
        else:
            records = connection.execute(
                """
                SELECT t.trade_date, t.trade_time, t.settle_date, t.execution_id,
                       t.trade_id, t.conid, i.symbol, i.description, i.asset_category,
                       t.buy_sell, t.quantity_text, t.trade_price_text, t.currency,
                       t.proceeds_text, t.commission_text, t.commission_currency,
                       t.net_cash_text, t.realized_pnl_text,
                       COUNT(*) OVER() AS total_count
                FROM trades AS t
                LEFT JOIN instruments AS i ON i.conid = t.conid
                WHERE t.account_id = %s
                  AND t.trade_date BETWEEN %s AND %s
                  AND (%s::text IS NULL OR POSITION(UPPER(%s) IN UPPER(COALESCE(i.symbol, ''))) > 0)
                  AND (%s::text IS NULL OR UPPER(COALESCE(t.buy_sell, '')) = %s)
                ORDER BY t.trade_date DESC, t.trade_time DESC, t.source_key DESC
                LIMIT 500
                """,
                (
                    account_id,
                    start,
                    end,
                    normalized_symbol,
                    normalized_symbol,
                    normalized_side,
                    normalized_side,
                ),
            ).fetchall()
    total_count = int(records[0]["total_count"]) if records else 0
    trades = [
        {key: value for key, value in dict(record).items() if key != "total_count"}
        for record in records
    ]
    return {
        "account_id": account_id,
        "date_from": start,
        "date_to": end,
        "symbol": normalized_symbol,
        "side": normalized_side,
        "total_count": total_count,
        "returned_count": len(trades),
        "truncated": total_count > len(trades),
        "trades": trades,
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/risk")
def risk_analytics(
    account_id: str,
    as_of: str | None = None,
    response: Response = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    started = time.perf_counter()
    with _database() as connection:
        effective_as_of = requested_as_of or connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s AND currency = " + ACTIVE_NAV_CURRENCY_SQL,
            (TENANT_ID, account_id),
        ).fetchone()["latest"]
        snapshot = connection.execute(
            """
            SELECT payload_json, quality_status, generated_at, duration_ms,
                   timings_json, payload_sha256, comparison_version,
                   (SELECT m.benchmark_id FROM portfolio_benchmark_mapping m
                    WHERE m.tenant_id = r.tenant_id AND m.subject_id = r.subject_id
                      AND m.mapping_type = 'primary' AND m.effective_from <= r.as_of_date
                      AND (m.effective_to IS NULL OR m.effective_to >= r.as_of_date)
                    ORDER BY m.effective_from DESC LIMIT 1) AS expected_benchmark_id
            FROM risk_analytics_snapshots r
            WHERE tenant_id = %s AND subject_id = %s AND as_of_date = %s
              AND analytics_version = %s
            LIMIT 1
            """,
            (TENANT_ID, account_id, effective_as_of, RISK_ANALYTICS_VERSION),
        ).fetchone()
    lookup_ms = round((time.perf_counter() - started) * 1000, 3)
    if response is not None:
        response.headers["Server-Timing"] = f"risk-cache;dur={lookup_ms:.3f}"
        response.headers["Cache-Control"] = "private, max-age=60"
    if snapshot is None:
        return {
            "account_id": account_id,
            "as_of": effective_as_of,
            "metrics": {},
            "risk_free": None,
            "quality_status": "pending",
            "cache_status": "miss",
            "message": (
                "The validated risk snapshot for this date is being prepared by the background analytics job."
            ),
            "viewer": user["email"],
        }
    read_status = (snapshot_read_status(snapshot, subject_id=account_id, as_of=str(effective_as_of),
                                       benchmark_id=None if account_id in ('alpha-world-ms','alpha-world-t') else snapshot.get("expected_benchmark_id"))
                   if account_id in ('alpha-world-ms','alpha-world-t') or snapshot.get("expected_benchmark_id") else "benchmark_mapping_missing")
    if read_status != "current":
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
        return {"account_id":account_id, "as_of":effective_as_of, "metrics":{},
                "risk_free":None, "quality_status":"fail", "cache_status":"invalid",
                "validation_status":read_status,
                "message":"This risk snapshot did not pass integrity checks. Its results are withheld until the background analytics job rebuilds and validates it.",
                "viewer":user["email"]}
    payload = decoded_payload(snapshot["payload_json"])
    payload.pop("expected_tail_loss_contributors", None)
    payload.pop("crisis_replay", None)
    payload.update(
        {
            "cache_status": "hit",
            "snapshot_generated_at": snapshot["generated_at"],
            "snapshot_duration_ms": float(snapshot["duration_ms"]),
            "snapshot_payload_sha256": snapshot["payload_sha256"],
            "request_duration_ms": lookup_ms,
            "viewer": user["email"],
        }
    )
    return payload


@app.get("/api/portfolio/{account_id}/risk/details")
def risk_analytics_details(
    account_id: str,
    as_of: str | None = None,
    response: Response = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    started = time.perf_counter()
    with _database() as connection:
        effective_as_of = requested_as_of or connection.execute(
            "SELECT MAX(snapshot_date) AS latest FROM subject_nav_series WHERE tenant_id = %s AND subject_id = %s AND currency = " + ACTIVE_NAV_CURRENCY_SQL,
            (TENANT_ID, account_id),
        ).fetchone()["latest"]
        snapshot = connection.execute(
            """
            SELECT payload_json, generated_at, quality_status, comparison_version, payload_sha256,
                   (SELECT m.benchmark_id FROM portfolio_benchmark_mapping m
                    WHERE m.tenant_id = r.tenant_id AND m.subject_id = r.subject_id
                      AND m.mapping_type = 'primary' AND m.effective_from <= r.as_of_date
                      AND (m.effective_to IS NULL OR m.effective_to >= r.as_of_date)
                    ORDER BY m.effective_from DESC LIMIT 1) AS expected_benchmark_id
            FROM risk_analytics_snapshots r
            WHERE tenant_id = %s AND subject_id = %s AND as_of_date = %s
              AND analytics_version = %s
            LIMIT 1
            """,
            (TENANT_ID, account_id, effective_as_of, RISK_ANALYTICS_VERSION),
        ).fetchone()
    lookup_ms = round((time.perf_counter() - started) * 1000, 3)
    if response is not None:
        response.headers["Server-Timing"] = f"risk-detail-cache;dur={lookup_ms:.3f}"
        response.headers["Cache-Control"] = "private, max-age=60"
    if snapshot is None:
        return {
            "account_id": account_id,
            "as_of": effective_as_of,
            "cache_status": "miss",
            "expected_tail_loss_contributors": {"top": [], "bottom": []},
            "crisis_replay": None,
            "message": "Detailed risk attribution is still being prepared.",
        }
    read_status = (snapshot_read_status(snapshot, subject_id=account_id, as_of=str(effective_as_of),
                                       benchmark_id=None if account_id in ('alpha-world-ms','alpha-world-t') else snapshot.get("expected_benchmark_id"))
                   if account_id in ('alpha-world-ms','alpha-world-t') or snapshot.get("expected_benchmark_id") else "benchmark_mapping_missing")
    if read_status != "current":
        if response is not None:
            response.headers["Cache-Control"] = "no-store"
        return {"account_id":account_id, "as_of":effective_as_of,
                "quality_status":"fail", "cache_status":"invalid", "validation_status":read_status,
                "expected_tail_loss_contributors":{"top":[], "bottom":[]}, "crisis_replay":None,
                "message":"Detailed risk results are withheld because the stored snapshot failed integrity checks."}
    payload = decoded_payload(snapshot["payload_json"])
    return {
        "account_id": account_id,
        "as_of": effective_as_of,
        "cache_status": "hit",
        "snapshot_generated_at": snapshot["generated_at"],
        "snapshot_payload_sha256": snapshot["payload_sha256"],
        "expected_tail_loss_contributors": payload.get(
            "expected_tail_loss_contributors", {"top": [], "bottom": []}
        ),
        "crisis_replay": payload.get("crisis_replay"),
        "crisis_scenarios": _crisis_scenarios(account_id, effective_as_of),
        "unavailable_capabilities": payload.get("unavailable_capabilities", {}),
        "request_duration_ms": lookup_ms,
    }


def _crisis_scenarios(subject_id: str, as_of) -> dict | None:
    """The stored crisis scenarios, apart from the risk snapshot; None until they exist or are switched on."""
    from .crisis_scenarios import load as load_scenarios, website_enabled
    if not website_enabled():
        return None
    try:
        with _database() as connection:
            return load_scenarios(connection, subject_id, as_of=str(as_of) if as_of else None, tenant_id=TENANT_ID)
    except Exception:  # never let the optional table withhold the rest of the risk page
        logging.getLogger(__name__).exception("Crisis scenarios could not be read for %s", subject_id)
        return None


@app.get("/api/system/health")
def system_health(user: dict[str, str] = Depends(_authorized_user)) -> dict:
    with _database() as connection:
        latest_run = connection.execute(
            """
            SELECT run_id, run_type, requested_from, requested_to, completed_at,
                   status, row_count
            FROM ingestion_runs
            ORDER BY run_id DESC LIMIT 1
            """
        ).fetchone()
        portfolio = connection.execute(
            """
            SELECT MIN(n.snapshot_date) AS first_date, MAX(n.snapshot_date) AS latest_date,
                   COUNT(*) AS observations, COUNT(DISTINCT n.subject_id) AS subjects
            FROM subject_nav_series n
            JOIN platform_subjects s
              ON s.tenant_id = n.tenant_id AND s.subject_id = n.subject_id
            WHERE s.lifecycle_status = 'active'
            """
        ).fetchone()
        benchmarks = connection.execute(
            """
            SELECT benchmark_id, MAX(observation_date) AS latest_date,
                   COUNT(*) AS observations, MAX(quality_status) AS quality_status
            FROM benchmark_prices
            WHERE observation_status = 'provisional_close'
              AND benchmark_id IN (
                  SELECT DISTINCT benchmark_id FROM portfolio_benchmark_mapping
              )
            GROUP BY benchmark_id ORDER BY benchmark_id
            """
        ).fetchall()
        internal_release = connection.execute(
            """
            SELECT d.release_decision_id, d.snapshot_id, d.status, d.approved_at,
                   d.approved_by, COUNT(a.artifact_release_id) AS artifacts
            FROM report_release_decisions d
            LEFT JOIN report_artifact_releases a
              ON a.release_decision_id = d.release_decision_id
            WHERE d.release_scope = 'internal_shareholder'
              AND d.status = 'approved'
              AND a.revoked_at IS NULL
            GROUP BY d.release_decision_id, d.snapshot_id, d.status,
                     d.approved_at, d.approved_by
            ORDER BY d.approved_at DESC LIMIT 1
            """
        ).fetchone()
        burn_in = connection.execute(
            """
            SELECT window_id, subject_id, start_date, earliest_completion_date,
                   required_consecutive_days, consecutive_pass_days, status,
                   started_at, last_result_date, last_failure_date, completed_at
            FROM burn_in_acceptance_windows
            ORDER BY window_id DESC LIMIT 1
            """
        ).fetchone()
        burn_in_results = []
        if burn_in:
            burn_in_results = connection.execute(
                """
                SELECT result_date, status, checked_at
                FROM burn_in_daily_results WHERE window_id = %s
                ORDER BY result_date DESC LIMIT 14
                """,
                (burn_in["window_id"],),
            ).fetchall()
        all_assurance = connection.execute(
            """
            SELECT s.subject_id, s.display_name, a.assurance_run_id, a.assurance_date, a.as_of_date,
                   a.policy_version, a.completed_at, a.status, a.checks_passed,
                   a.checks_warned, a.checks_failed, a.result_json, a.model_deployment
            FROM platform_subjects s
            LEFT JOIN LATERAL (
                SELECT * FROM portfolio_analyst_assurance_runs r
                WHERE r.tenant_id = s.tenant_id AND r.subject_id = s.subject_id
                ORDER BY r.assurance_date DESC, r.assurance_run_id DESC LIMIT 1
            ) a ON true
            WHERE s.lifecycle_status = 'active' AND s.subject_type <> 'master_account'
            ORDER BY s.subject_id
            """
        ).fetchall()
        incidents = open_incidents(connection)
    analyst_assurance = max(
        all_assurance, key=lambda row: str(row.get("completed_at") or ""), default=None
    )
    status = aggregate_health(latest_run, all_assurance,
                              today=datetime.now(ZoneInfo("Asia/Hong_Kong")).date())
    issues = []
    for item in all_assurance:
        result = item.get("result_json") or {}
        if isinstance(result, str):
            result = json.loads(result)
        for finding in result.get("findings", []):
            if finding.get("severity") not in {"error", "critical", "warning"}:
                continue
            issues.append({"subject_id":item["subject_id"],
                           "display_name":item.get("display_name") or item["subject_id"],
                           "severity":finding.get("severity"), "check_id":finding.get("check_id"),
                           "message":finding.get("message"),
                           "metrics":finding.get("evidence", {}).get("metrics", [])})
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "issues": issues,
        "incidents": incidents,
        "environment": "internal-production",
        "release_channel": RELEASE_CHANNEL,
        "portfolio": portfolio,
        "latest_run": latest_run,
        "benchmarks": benchmarks,
        "internal_release": internal_release,
        "internal_release_status": "approved" if internal_release else "not_released",
        "burn_in": burn_in,
        "burn_in_recent_results": burn_in_results,
        "portfolio_analyst_assurance": analyst_assurance,
        "portfolio_analyst_assurance_all": all_assurance,
        "client_publication_status": "blocked_pending_bloomberg",
        "message": ("One or more checks failed. Review the account-specific findings below; failed analytics must not be treated as validated. "
                    if status == "fail" else "Review any outstanding warnings and data dates below. ")
                   + "Client distribution remains blocked until Bloomberg data and final disclosure approval are installed.",
        "viewer": user["email"],
    }


@app.get("/api/system/autonomous-administrator")
def autonomous_administrator_status(
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    with _database() as connection:
        control = connection.execute(
            """
            SELECT enabled, kill_switch, max_actions_per_run, cooldown_minutes,
                   policy_version, updated_at, updated_by, production_active,
                   production_activated_at, automatic_rollback_enabled,
                   rollback_failure_threshold, monthly_review_due_at
            FROM autonomy_control WHERE tenant_id = app_tenant_id()
            """
        ).fetchone()
        latest_run = connection.execute(
            """
            SELECT agent_run_id, correlation_id, started_at, completed_at, status,
                   finding_count, action_count, model_used, summary, error_code
            FROM autonomous_agent_runs ORDER BY agent_run_id DESC LIMIT 1
            """
        ).fetchone()
        incidents = connection.execute(
            """
            SELECT incident_id, incident_key, check_id, severity, status, description,
                   first_seen_at, last_seen_at, occurrence_count
            FROM system_incidents WHERE status IN ('open', 'acknowledged')
            ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                     last_seen_at DESC LIMIT 20
            """
        ).fetchall()
        actions = connection.execute(
            """
            SELECT action_id, action_type, target_job, policy_version, status,
                   reason, created_at, executed_at, error_code
            FROM autonomous_actions ORDER BY action_id DESC LIMIT 10
            """
        ).fetchall()
        baseline = connection.execute(
            """
            SELECT app_name, revision_name, image_digest, established_at
            FROM autonomy_release_baselines WHERE status = 'last_known_good'
            ORDER BY established_at DESC LIMIT 1
            """
        ).fetchone()
        recovery = connection.execute(
            """
            SELECT app_name, consecutive_portal_failures, latest_checked_revision,
                   last_failure_at, last_success_at, updated_at
            FROM autonomy_recovery_state ORDER BY updated_at DESC LIMIT 1
            """
        ).fetchone()
        reviews = connection.execute(
            """
            SELECT review_id, review_month, period_start, period_end, due_at, status,
                   summary_json, created_at, approved_at
            FROM autonomy_authority_reviews ORDER BY review_month DESC LIMIT 12
            """
        ).fetchall()
    review_due = None if control is None else control["monthly_review_due_at"]
    try:
        parsed_due = (
            None
            if not review_due
            else datetime.fromisoformat(str(review_due).replace("Z", "+00:00"))
        )
        if parsed_due is not None and parsed_due.tzinfo is None:
            parsed_due = parsed_due.replace(tzinfo=timezone.utc)
    except ValueError:
        parsed_due = None
    enabled = bool(
        control
        and control["enabled"]
        and not control["kill_switch"]
        and control["production_active"]
        and parsed_due is not None
        and parsed_due >= datetime.now(timezone.utc)
    )
    return {
        "status": "active" if enabled else "paused_or_review_overdue",
        "control": control,
        "latest_run": latest_run,
        "open_incidents": incidents,
        "recent_actions": actions,
        "last_known_good": baseline,
        "recovery": recovery,
        "authority_reviews": reviews,
        "authority": {
            "autonomous": [
                "observe_system_health",
                "diagnose_new_incidents_from_structured_results",
                "refresh_risk_free_rate_with_cooldown",
                "refresh_provisional_benchmarks_with_cooldown",
                "activate_exact_last_known_good_revision_after_three_consecutive_portal_failures",
            ],
            "approval_required": [
                "ibkr_refresh_or_circuit_breaker_reset",
                "deployment_or_unbounded_rollback",
                "formula_or_schema_change",
                "permission_or_secret_change",
                "monthly_authority_review",
            ],
            "prohibited": [
                "trading",
                "arbitrary_sql_or_shell",
                "secret_retrieval",
                "delete_financial_history",
                "self_modify_authority",
            ],
        },
        "viewer": user["email"],
    }


@app.get("/api/system/institutional-control-plane")
def institutional_control_plane_status(
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    with _database() as connection:
        summary = control_plane_summary(
            _portable_database_connection(connection), tenant_id=TENANT_ID
        )
    identity = summary["identity_registry"]
    shadow = summary.get("shadow_reconciliation")
    shadow_ready = bool(shadow and shadow.get("status") == "pass")
    summary.update(
        {
            "authorization_mode": AUTHORIZATION_MODE,
            "authorization_cutover_ready": bool(
                identity["database_authorization_ready"]
                and summary["dead_letters"] == 0
                and shadow_ready
            ),
            "production_scheduler": "existing_v11_1600_hkt",
            "institutional_dispatcher": "schedule_managed_in_azure",
            "message": (
                "Database authorization and the read-only dispatcher shadow are reconciled. Scheduler configuration is managed separately in Azure."
                if identity["database_authorization_ready"] and shadow_ready
                else (
                    "Database authorization is reconciled; a passing read-only dispatcher shadow is still required."
                    if identity["database_authorization_ready"]
                    else "Database authorization remains fail-closed until one owner and the approved user entitlements are registered."
                )
            ),
            "viewer": user["email"],
        }
    )
    return summary


@app.post("/api/system/autonomous-administrator/reviews/{review_id}/approve")
def approve_authority_review(
    review_id: int,
    payload: AuthorityReviewApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    with _approval_database() as connection:
        row = connection.execute(
            """
            SELECT review_id, review_month, status FROM autonomy_authority_reviews
            WHERE review_id = %s FOR UPDATE
            """,
            (review_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Authority review not found")
        if row["status"] == "approved":
            raise HTTPException(status_code=409, detail="Authority review was already approved")
        expected = review_confirmation_phrase(str(row["review_month"]))
        if payload.confirmation != expected:
            raise HTTPException(status_code=400, detail="Exact authority-review confirmation is required")
        approved_at = utc_now_iso()
        due_at = next_review_due(date.today()).isoformat()
        updated = connection.execute(
            """
            UPDATE autonomy_authority_reviews SET status = 'approved', approved_at = %s,
                approved_by = %s WHERE review_id = %s AND status IN ('awaiting_owner_review', 'overdue')
            """,
            (approved_at, _identity_digest(user["object_id"]), review_id),
        )
        if updated.rowcount != 1:
            connection.rollback()
            raise HTTPException(status_code=409, detail="Authority review approval was not applied")
        connection.execute(
            """
            UPDATE autonomy_control SET monthly_review_due_at = %s, updated_at = %s,
                updated_by = 'owner-monthly-authority-review'
            WHERE tenant_id = app_tenant_id()
            """,
            (due_at, approved_at),
        )
        connection.commit()
    return {
        "status": "approved",
        "review_id": review_id,
        "review_month": row["review_month"],
        "next_review_due_at": due_at,
    }


@app.get("/api/agent/tools")
def agent_tool_catalog(
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    is_owner = user.get("role", "owner") == "owner"
    authorized_subjects = _authorized_subject_ids(user, feature_key="ai_analyst")
    return {
        "status": "deterministic_tools_ready",
        "ai_model_status": (
            "enabled_apac_data_zone"
            if AI_ANALYST_CONFIG.enabled
            else "regional_boundary_ready_no_approved_payg_model"
        ),
        "ai_model": {
            "enabled": AI_ANALYST_CONFIG.enabled,
            "approved_region": "southeastasia",
            "resource_region": AI_ANALYST_CONFIG.region,
            "processing_scope": AI_ANALYST_CONFIG.processing_scope,
            "authentication": "managed_identity_only",
            "local_keys_disabled": True,
            "analysis_types": analysis_types(),
            "message": (
                "Grounded narrative generation is enabled for approved structured U16088571 results. The resource is in Southeast Asia and inference uses the APAC Data Zone."
                if AI_ANALYST_CONFIG.enabled
                else "The keyless Southeast Asia resource boundary is ready, but inference remains disabled because no regional pay-as-you-go text model is available within the staging ceiling."
            ),
        },
        "admin_actions": {
            "enabled": ADMIN_ACTION_CONFIG.enabled and is_owner,
            "environment": ADMIN_ACTION_CONFIG.environment,
            "approval_ttl_minutes": 10,
            "actions": action_catalog() if is_owner else [],
            "message": (
                "Two fixed market-data maintenance actions are available through an expiring preview and explicit approval. The AI model cannot execute them."
                if ADMIN_ACTION_CONFIG.enabled and is_owner
                else (
                    "Administrative actions are restricted to the platform owner."
                    if not is_owner
                    else "Controlled administrative actions are disabled."
                )
            ),
        },
        "authorized_subjects": authorized_subjects,
        "tools": tool_catalog(),
        "prohibited_capabilities": [
            "arbitrary_sql",
            "shell_access",
            "trading",
            "secret_retrieval",
            "role_or_policy_changes",
            "self_modification",
        ],
        "viewer": user["email"],
    }


@app.post("/api/admin/actions/preview")
def preview_admin_action(
    payload: AdminActionPreviewRequest,
    request: Request,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    try:
        ADMIN_ACTION_CONFIG.validate()
        with _approval_database() as connection:
            return create_action_preview(
                connection,
                ADMIN_ACTION_CONFIG,
                action_type=payload.action_type,
                actor_object_id=user["object_id"],
                correlation_id=request.state.correlation_id,
            )
    except AdminActionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@app.get("/api/admin/conversations")
def list_admin_conversations(
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    with _approval_database() as connection:
        _ensure_admin_updates_conversation(connection)
    with _database() as connection:
        rows = connection.execute(
            """
            SELECT c.conversation_id, c.title, c.initiated_by, c.source_key, c.status,
                   c.created_at, c.updated_at, COUNT(m.message_id) AS message_count,
                   GREATEST(COALESCE(MAX(m.sequence_no), 0) - c.owner_last_read_sequence, 0)
                     AS unread_count
            FROM admin_chat_conversations c
            LEFT JOIN admin_chat_messages m
              ON m.tenant_id = c.tenant_id AND m.conversation_id = c.conversation_id
            WHERE c.status = 'active'
            GROUP BY c.conversation_id, c.title, c.initiated_by, c.source_key,
                     c.status, c.created_at, c.updated_at, c.owner_last_read_sequence
            ORDER BY CASE WHEN c.source_key = %s THEN 0 ELSE 1 END, c.updated_at DESC
            """,
            (SYSTEM_UPDATES_SOURCE_KEY,),
        ).fetchall()
    return {"conversations": [dict(row) for row in rows], "viewer": user["email"]}


@app.post("/api/admin/conversations")
def create_admin_conversation(
    payload: AdminConversationCreateRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    title = " ".join((payload.title or "New administrator conversation").split()).strip()
    with _approval_database() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM admin_chat_conversations
            WHERE status = 'active' AND source_key IS NULL
            """
        ).fetchone()
        if int(count["count"]) >= 50:
            raise HTTPException(status_code=409, detail="Archive an administrator chat before creating another one")
        conversation_id = str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO admin_chat_conversations(
                conversation_id, title, initiated_by, status
            ) VALUES (%s, %s, 'owner', 'active')
            """,
            (conversation_id, title),
        )
        connection.commit()
    return {
        "conversation_id": conversation_id,
        "title": title,
        "initiated_by": "owner",
        "status": "active",
    }


@app.get("/api/admin/conversations/{conversation_id}")
def get_admin_conversation(
    conversation_id: str,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    with _approval_database() as connection:
        conversation = _admin_conversation_row(connection, conversation_id)
        messages = _admin_messages(connection, str(conversation["conversation_id"]))
        last_sequence = max((int(message["sequence_no"]) for message in messages), default=0)
        connection.execute(
            """
            UPDATE admin_chat_conversations SET owner_last_read_sequence = %s
            WHERE conversation_id = %s
            """,
            (last_sequence, conversation["conversation_id"]),
        )
        connection.commit()
    return {
        "conversation": dict(conversation),
        "messages": [dict(message) for message in messages],
        "viewer": user["email"],
    }


@app.post("/api/admin/conversations/{conversation_id}/messages")
def send_admin_chat_message(
    conversation_id: str,
    payload: AdminChatMessageRequest,
    request: Request,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    normalized_id = _conversation_uuid(conversation_id)
    with _database() as connection:
        conversation = _admin_conversation_row(connection, normalized_id)
        if str(conversation["status"]) != "active":
            raise HTTPException(status_code=409, detail="Administrator conversation is archived")
        messages = _admin_messages(connection, normalized_id)
        sources = _admin_structured_sources(connection)
    if len(messages) + 2 > MAX_ADMIN_CHAT_MESSAGES:
        raise HTTPException(status_code=409, detail="This conversation reached its message limit; start a new chat")
    try:
        narrative, _ = generate_admin_chat_response(
            AI_ANALYST_CONFIG,
            owner_message=payload.message,
            structured_sources=sources,
            conversation_history=admin_model_history(messages),
        )
    except AIAnalystError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error
    action_preview = None
    action_type = str(narrative["proposed_action_type"])
    if action_type != "none":
        try:
            with _approval_database() as connection:
                action_preview = create_action_preview(
                    connection,
                    ADMIN_ACTION_CONFIG,
                    action_type=action_type,
                    actor_object_id=user["object_id"],
                    correlation_id=request.state.correlation_id,
                )
        except AdminActionError as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={"code": error.code, "message": str(error)},
            ) from error
    response_json = {
        **narrative,
        "action_preview": action_preview,
        "policy_version": "controlled-autonomy-v1",
        "generated_at": utc_now_iso(),
        "correlation_id": request.state.correlation_id,
    }
    with _approval_database() as connection:
        locked = connection.execute(
            """
            SELECT title, status FROM admin_chat_conversations
            WHERE conversation_id = %s FOR UPDATE
            """,
            (normalized_id,),
        ).fetchone()
        if locked is None or str(locked["status"]) != "active":
            raise HTTPException(status_code=409, detail="Administrator conversation is unavailable")
        sequence = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) AS last_sequence
            FROM admin_chat_messages WHERE conversation_id = %s
            """,
            (normalized_id,),
        ).fetchone()
        next_sequence = int(sequence["last_sequence"]) + 1
        owner_message_id, assistant_message_id = str(uuid.uuid4()), str(uuid.uuid4())
        connection.execute(
            """
            INSERT INTO admin_chat_messages(
                message_id, conversation_id, sequence_no, role, message_kind,
                content_text, correlation_id
            ) VALUES (%s, %s, %s, 'owner', 'chat', %s, %s)
            """,
            (
                owner_message_id,
                normalized_id,
                next_sequence,
                payload.message.strip(),
                request.state.correlation_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO admin_chat_messages(
                message_id, conversation_id, sequence_no, role, message_kind,
                content_text, response_json, approval_id, correlation_id
            ) VALUES (%s, %s, %s, 'administrator', %s, %s, %s, %s, %s)
            """,
            (
                assistant_message_id,
                normalized_id,
                next_sequence + 1,
                "action_preview" if action_preview else "chat",
                str(narrative["message"]),
                json.dumps(response_json),
                None if action_preview is None else action_preview["approval_id"],
                request.state.correlation_id,
            ),
        )
        title = (
            admin_conversation_title(payload.message)
            if str(locked["title"]) == "New administrator conversation"
            else str(locked["title"])
        )
        connection.execute(
            """
            UPDATE admin_chat_conversations SET title = %s, updated_at = now(),
                owner_last_read_sequence = %s WHERE conversation_id = %s
            """,
            (title, next_sequence + 1, normalized_id),
        )
        connection.commit()
    return {
        "conversation_id": normalized_id,
        "conversation_title": title,
        "owner_message_id": owner_message_id,
        "administrator_message_id": assistant_message_id,
        "response": response_json,
    }


@app.post("/api/admin/conversations/{conversation_id}/archive")
def archive_admin_conversation(
    conversation_id: str,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    normalized_id = _conversation_uuid(conversation_id)
    with _approval_database() as connection:
        conversation = _admin_conversation_row(connection, normalized_id)
        if conversation["source_key"] is not None:
            raise HTTPException(status_code=409, detail="The system updates channel cannot be archived")
        connection.execute(
            "UPDATE admin_chat_conversations SET status = 'archived', updated_at = now() WHERE conversation_id = %s",
            (normalized_id,),
        )
        connection.commit()
    return {"conversation_id": normalized_id, "status": "archived"}


@app.get("/api/admin/conversations/{conversation_id}/export.json")
def export_admin_conversation_json(
    conversation_id: str,
    user: dict[str, str] = Depends(_owner_user),
) -> Response:
    with _database() as connection:
        conversation = _admin_conversation_row(connection, conversation_id)
        messages = _admin_messages(connection, str(conversation["conversation_id"]))
    content = json.dumps(
        admin_transcript_payload(conversation, messages),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ) + "\n"
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="AI_System_Administrator_{conversation["conversation_id"]}.json"'},
    )


@app.get("/api/admin/conversations/{conversation_id}/export.md")
def export_admin_conversation_markdown(
    conversation_id: str,
    user: dict[str, str] = Depends(_owner_user),
) -> Response:
    with _database() as connection:
        conversation = _admin_conversation_row(connection, conversation_id)
        messages = _admin_messages(connection, str(conversation["conversation_id"]))
    return Response(
        content=admin_transcript_markdown(conversation, messages),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="AI_System_Administrator_{conversation["conversation_id"]}.md"'},
    )


@app.post("/api/admin/conversations/{conversation_id}/actions/{approval_id}/approve")
def approve_admin_chat_action(
    conversation_id: str,
    approval_id: int,
    payload: AdminActionApprovalRequest,
    request: Request,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    normalized_id = _conversation_uuid(conversation_id)
    try:
        with _approval_database() as connection:
            _admin_conversation_row(connection, normalized_id)
            linked = connection.execute(
                """
                SELECT 1 FROM admin_chat_messages
                WHERE conversation_id = %s AND approval_id = %s
                """,
                (normalized_id, approval_id),
            ).fetchone()
            if linked is None:
                raise HTTPException(status_code=404, detail="Action preview is not part of this conversation")
            definition, _ = claim_action_for_execution(
                connection,
                ADMIN_ACTION_CONFIG,
                approval_id=approval_id,
                expected_payload_sha256=payload.action_payload_sha256,
                confirmation=payload.confirmation,
                actor_object_id=user["object_id"],
                correlation_id=request.state.correlation_id,
            )
        result = execute_claimed_action(
            _approval_database,
            ADMIN_ACTION_CONFIG,
            approval_id=approval_id,
            definition=definition,
            actor_object_id=user["object_id"],
            correlation_id=request.state.correlation_id,
            payload_sha256=payload.action_payload_sha256,
        )
        with _approval_database() as connection:
            next_row = connection.execute(
                "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS n FROM admin_chat_messages WHERE conversation_id = %s",
                (normalized_id,),
            ).fetchone()
            content = f"The approved action was executed once: {result['action_type']}."
            connection.execute(
                """
                INSERT INTO admin_chat_messages(
                    message_id, conversation_id, sequence_no, role, message_kind,
                    content_text, response_json, approval_id, correlation_id
                ) VALUES (%s, %s, %s, 'administrator', 'action_result', %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    normalized_id,
                    int(next_row["n"]),
                    content,
                    json.dumps({"action_result": result, "title": "Controlled action executed"}),
                    approval_id,
                    request.state.correlation_id,
                ),
            )
            connection.execute(
                "UPDATE admin_chat_conversations SET updated_at = now() WHERE conversation_id = %s",
                (normalized_id,),
            )
            connection.commit()
        return result
    except AdminActionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@app.post("/api/admin/actions/{approval_id}/approve")
def approve_admin_action(
    approval_id: int,
    payload: AdminActionApprovalRequest,
    request: Request,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    try:
        ADMIN_ACTION_CONFIG.validate()
        with _approval_database() as connection:
            definition, _ = claim_action_for_execution(
                connection,
                ADMIN_ACTION_CONFIG,
                approval_id=approval_id,
                expected_payload_sha256=payload.action_payload_sha256,
                confirmation=payload.confirmation,
                actor_object_id=user["object_id"],
                correlation_id=request.state.correlation_id,
            )
        return execute_claimed_action(
            _approval_database,
            ADMIN_ACTION_CONFIG,
            approval_id=approval_id,
            definition=definition,
            actor_object_id=user["object_id"],
            correlation_id=request.state.correlation_id,
            payload_sha256=payload.action_payload_sha256,
        )
    except AdminActionError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@app.post("/api/agent/tools/{tool_name}")
def run_agent_tool(
    tool_name: str,
    payload: AgentToolRequest,
    request: Request,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    arguments = payload.model_dump(exclude_none=True)
    allowed_subjects = _authorized_subject_ids(user, feature_key="ai_analyst")
    context = AgentToolContext(
        tenant_id=TENANT_ID,
        actor_object_id=str(user["object_id"]),
        allowed_subject_ids=frozenset(allowed_subjects),
        correlation_id=request.state.correlation_id,
    )
    try:
        with _database() as connection:
            return execute_tool(_portable_database_connection(connection), context, tool_name, arguments)
    except AgentToolError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@app.get("/api/reporting/snapshots/{snapshot_id}.json")
def export_reporting_snapshot(
    snapshot_id: str,
    download: bool = False,
    user: dict[str, str] = Depends(_owner_user),
    subject_id: str = PILOT_ACCOUNT_ID,
) -> Response:
    # Frozen calculation inputs are not released viewer report artifacts.
    # Enforce this in the handler as well as the HTTP dependency.
    if user.get("role") != "owner":
        raise HTTPException(status_code=403, detail="Owner access is required")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", subject_id):
        raise HTTPException(status_code=400, detail="Invalid portfolio ID")
    _portfolio_access_context(subject_id, user)
    if snapshot_id == "latest":
        normalized_id = None
    else:
        try:
            normalized_id = str(uuid.UUID(snapshot_id))
        except ValueError as error:
            raise HTTPException(status_code=400, detail="Invalid snapshot ID") from error

    with _database() as connection:
        if normalized_id is None:
            row = connection.execute(
                """
                SELECT snapshot_payload_json, data_sha256, subject_id, period_end, status
                FROM reporting_snapshots
                WHERE tenant_id = %s AND subject_id = %s
                  AND status IN ('frozen', 'approved')
                ORDER BY period_end DESC, created_at DESC
                LIMIT 1
                """,
                (TENANT_ID, subject_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT snapshot_payload_json, data_sha256, subject_id, period_end, status
                FROM reporting_snapshots
                WHERE tenant_id = %s AND snapshot_id = %s AND subject_id = %s
                  AND status IN ('frozen', 'approved')
                """,
                (TENANT_ID, normalized_id, subject_id),
            ).fetchone()
    if row is None or str(row["subject_id"]) != subject_id:
        raise HTTPException(status_code=404, detail="Reporting snapshot not found")

    payload = row["snapshot_payload_json"]
    if hashlib.sha256(payload.encode("utf-8")).hexdigest() != row["data_sha256"]:
        raise HTTPException(status_code=503, detail="Reporting snapshot integrity check failed")

    filename = (
        f"{subject_id}-{row['period_end']}-internal-development-snapshot.json"
    )
    headers = {"X-Content-SHA256": row["data_sha256"]}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(
        content=payload,
        media_type="application/json",
        headers=headers,
    )


def _monthly_report_catalog(connection, subject_id: str) -> list[dict]:
    snapshots = connection.execute(
        """
        SELECT snapshot_id, subject_id, period_end, data_as_of, quality_status
        FROM reporting_snapshots
        WHERE tenant_id = %s AND subject_id = %s
          AND status IN ('frozen', 'approved')
        ORDER BY period_end DESC, created_at DESC
        """,
        (TENANT_ID, subject_id),
    ).fetchall()
    generation_runs = connection.execute(
        """
        SELECT DISTINCT ON (r.snapshot_id, r.report_type)
               r.generation_run_id, r.snapshot_id, r.report_type, r.status,
               r.output_reference, r.output_sha256, s.subject_id, s.period_end
        FROM report_generation_runs r
        JOIN reporting_snapshots s
          ON s.snapshot_id = r.snapshot_id AND s.tenant_id = r.tenant_id
        WHERE r.tenant_id = %s AND r.report_type IN ('factsheet', 'presentation')
          AND r.snapshot_id IN (
              SELECT snapshot_id FROM reporting_snapshots
              WHERE tenant_id = %s AND subject_id = %s
          )
        ORDER BY r.snapshot_id, r.report_type, r.generation_run_id DESC
        """,
        (TENANT_ID, TENANT_ID, subject_id),
    ).fetchall()
    released_artifacts = connection.execute(
        """
        SELECT a.artifact_release_id, a.container_name, a.blob_name,
               a.artifact_sha256, r.snapshot_id, r.report_type, s.subject_id,
               s.period_end
        FROM report_artifact_releases a
        JOIN report_release_decisions d
          ON d.release_decision_id = a.release_decision_id
         AND d.tenant_id = a.tenant_id
        JOIN report_generation_runs r
          ON r.generation_run_id = a.generation_run_id
         AND r.tenant_id = a.tenant_id
        JOIN reporting_snapshots s
          ON s.snapshot_id = r.snapshot_id
         AND s.tenant_id = r.tenant_id
        WHERE a.tenant_id = %s
          AND a.release_scope = 'internal_shareholder'
          AND a.revoked_at IS NULL
          AND a.superseded_by_artifact_release_id IS NULL
          AND d.release_scope = 'internal_shareholder'
          AND d.status = 'approved'
          AND d.snapshot_id = r.snapshot_id
          AND s.subject_id = %s
          AND s.status IN ('frozen', 'approved', 'superseded')
          AND s.quality_status <> 'fail'
          AND r.status = 'released_internal'
          AND r.report_type IN ('factsheet', 'presentation')
        ORDER BY s.period_end DESC, r.report_type
        """,
        (TENANT_ID, subject_id),
    ).fetchall()
    return build_monthly_report_catalog(
        snapshots,
        generation_runs,
        released_artifacts,
        account_id=subject_id,
        container=INTERNAL_REPORT_CONTAINER,
    )


def _monthly_report_payload(subject_id: str, user: dict[str, object]) -> dict:
    access = _portfolio_access_context(subject_id, user)
    if access["preview_mode"]:
        raise HTTPException(
            status_code=409,
            detail="Monthly reporting remains unavailable until activation",
        )
    feature_states = dict(access.get("feature_states") or {})
    if feature_states.get("factsheet") != "active" or feature_states.get(
        "presentation"
    ) != "active":
        raise HTTPException(
            status_code=409,
            detail="Monthly report downloads are not active for this portfolio",
        )
    with _database() as connection:
        months = _monthly_report_catalog(connection, subject_id)
    if user["role"] != "owner":
        months = [
            {
                **month,
                "downloadable": bool(month["released_artifacts"]),
                "review_ready": False,
                "review_artifacts": [],
                "artifacts": month["released_artifacts"],
            }
            for month in months
        ]
    return {
        "account_id": subject_id,
        "display_name": str(access["display_name"]),
        "reporting_currency": str(access["reporting_currency"]),
        "months": months,
        "ready_months": sum(1 for month in months if month["ready"]),
        "review_ready_months": sum(1 for month in months if month["review_ready"]),
        "message": (
            "Select a completed month, review each file, then tick and submit the "
            "files you approve. Untick and submit to revoke access."
            if user["role"] == "owner"
            else "Select a completed month to download its approved files."
        ),
        "viewer": user["email"],
    }


@app.get("/api/reporting/monthly")
def list_monthly_reports(
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    # Backward-compatible pilot endpoint. Account pages use the subject-scoped
    # route below so one portfolio can never inherit another portfolio's files.
    return _monthly_report_payload(PILOT_ACCOUNT_ID, user)


@app.get("/api/accounts/{subject_id}/reporting/monthly")
def list_subject_monthly_reports(
    subject_id: str,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    return _monthly_report_payload(subject_id, user)


def _prepare_monthly_report_downloads(
    *, subject_id: str, period_end: str, user: dict[str, object]
) -> dict:
    access = _portfolio_access_context(subject_id, user)
    if access["preview_mode"]:
        raise HTTPException(status_code=409, detail="Portfolio is not active")
    feature_states = dict(access.get("feature_states") or {})
    if feature_states.get("factsheet") != "active" or feature_states.get(
        "presentation"
    ) != "active":
        raise HTTPException(status_code=409, detail="Monthly report downloads are not active")
    try:
        normalized_period_end = validate_period_end(period_end)
    except MonthlyReportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    with _database() as connection:
        catalog = _monthly_report_catalog(connection, subject_id)
    month = next(
        (item for item in catalog if item["period_end"] == normalized_period_end),
        None,
    )
    if month is None:
        raise HTTPException(status_code=404, detail="Monthly reporting snapshot not found")
    if not month["downloadable"]:
        raise HTTPException(
            status_code=409,
            detail=(
                "The frozen month-end snapshot exists, but its factsheet "
                "is not available yet"
            ),
        )
    review_only = bool(month["review_ready"] and user["role"] == "owner")
    artifacts = month["review_artifacts"] if review_only else month["released_artifacts"]
    if not artifacts:
        raise HTTPException(status_code=403, detail="Owner review is required")
    return {
        "status": "review_ready" if review_only else "ready",
        "account_id": subject_id,
        "period_end": normalized_period_end,
        "artifacts": artifacts,
        "client_distribution": "prohibited",
        "review_only": review_only,
        "message": (
            "The available files passed generation checks and are ready for owner review."
            if review_only
            else "The approved files passed release-scope and database integrity validation."
        ),
        "viewer": user["email"],
    }


@app.get("/api/reporting/monthly/{period_end}/prepare")
def prepare_monthly_report_downloads(
    period_end: str,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    return _prepare_monthly_report_downloads(
        subject_id=PILOT_ACCOUNT_ID, period_end=period_end, user=user
    )


@app.get("/api/accounts/{subject_id}/reporting/monthly/{period_end}/prepare")
def prepare_subject_monthly_report_downloads(
    subject_id: str,
    period_end: str,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    return _prepare_monthly_report_downloads(
        subject_id=subject_id, period_end=period_end, user=user
    )


def _validate_release_factsheet(connection, content: bytes, *, subject_id: str,
                               period_end: str, snapshot_id: str) -> None:
    """Apply the subject's governed contract; never use another account's panel."""
    if subject_id not in ("alpha-world-ms", "alpha-world-t"):
        validate_factsheet_return_typography(content)
        return
    label = "MS" if subject_id == "alpha-world-ms" else "T"
    row = connection.execute(
        """
        SELECT snapshot_payload_json, data_sha256, subject_id, period_end
        FROM reporting_snapshots
        WHERE tenant_id = %s AND snapshot_id = %s AND subject_id = %s
          AND period_end = %s AND status IN ('frozen', 'approved')
        """,
        (TENANT_ID, snapshot_id, subject_id, period_end),
    ).fetchone()
    if row is None or row["subject_id"] != subject_id or str(row["period_end"]) != period_end:
        raise MonthlyArtifactError(label + " release snapshot identity is unavailable")
    raw = row["snapshot_payload_json"]
    if not isinstance(raw, str) or hashlib.sha256(raw.encode("utf-8")).hexdigest() != row["data_sha256"]:
        raise MonthlyArtifactError(label + " release snapshot integrity check failed")
    snapshot = json.loads(raw)
    if subject_id == "alpha-world-t":
        from .t_factsheet_release import validate_t_factsheet_release
        validate_t_factsheet_release(content, snapshot, period_end=period_end)
        return
    from .ms_factsheet_release import validate_ms_factsheet_release
    validate_ms_factsheet_release(content, snapshot, period_end=period_end)


class MonthlyReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = Field(min_length=36, max_length=36)
    package_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    selection_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_report_types: list[Literal["factsheet", "presentation"]] = Field(max_length=2)


class ReportDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    description: str = Field(min_length=10,max_length=4000)


class ReportDesignQueueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    design_version: str = Field(min_length=1,max_length=128)
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    replace_prior_releases: Literal[True]
    apply_to_future: Literal[True]


def _report_design_owner(subject_id,user):
    if user.get('role')!='owner':raise HTTPException(status_code=403,detail='Owner access is required')
    if _portfolio_access_context(subject_id,user)['preview_mode']:
        raise HTTPException(status_code=409,detail='Design updates are available after onboarding')


@app.get('/api/accounts/{subject_id}/reporting/design-updates')
def list_report_design_updates(subject_id: str,user: dict=Depends(_owner_user)):
    _report_design_owner(subject_id,user)
    image=os.environ.get('REPORT_RENDERER_IMAGE_SHA256','')
    try:
        with _database() as connection:
            requests=connection.execute('''SELECT request_id,description,status,design_version,requested_at
                FROM report_design_update_requests WHERE tenant_id=%s AND subject_id=%s
                ORDER BY requested_at DESC LIMIT 50''',(TENANT_ID,subject_id)).fetchall()
            versions=connection.execute('''SELECT * FROM qualified_report_designs
                WHERE tenant_id=%s AND subject_id=%s AND retired_at IS NULL ORDER BY qualified_at DESC''',
                (TENANT_ID,subject_id)).fetchall()
    except PostgresError as error:
        raise HTTPException(status_code=503,detail='Report design registry is not available') from error
    qualified=[]
    for row in versions:
        try:report_design_updates.qualified_design(row,installed_image_sha256=image)
        except (ValueError,TypeError,KeyError):continue
        qualified.append({'design_version':row['design_version'],'qualified_at':row['qualified_at']})
    return {'requests':requests,'designs':qualified,
        'regeneration_enabled':os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')=='true'}


@app.post('/api/accounts/{subject_id}/reporting/design-updates')
def request_report_design_update(subject_id: str,payload: ReportDesignRequest,user: dict=Depends(_owner_user)):
    _report_design_owner(subject_id,user)
    try:
        with _approval_database() as connection:
            return report_design_updates.create_request(_portable_database_connection(connection),TENANT_ID,
                subject_id,payload.request_id,payload.description,str(user['object_id']))
    except ValueError as error:raise HTTPException(status_code=409,detail=str(error)) from error
    except PostgresError as error:
        raise HTTPException(status_code=503,detail='Report design registry is not available') from error


@app.get('/api/accounts/{subject_id}/reporting/design-updates/preview')
def preview_report_design_update(subject_id: str,design_version: str,user: dict=Depends(_owner_user)):
    _report_design_owner(subject_id,user)
    if os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')!='true':
        raise HTTPException(status_code=503,detail='Historical regeneration worker is not enabled')
    try:
        with _database() as connection:
            plan=report_design_updates.load_plan_inputs(_portable_database_connection(connection),TENANT_ID,
                subject_id,design_version,installed_image_sha256=os.environ.get('REPORT_RENDERER_IMAGE_SHA256',''))
    except (ValueError,KeyError,TypeError) as error:raise HTTPException(status_code=409,detail=str(error)) from error
    except PostgresError as error:
        raise HTTPException(status_code=503,detail='Report design registry is not available') from error
    return {'design_version':design_version,'plan_sha256':report_design_updates.digest(plan),
        'months':len(plan['snapshots']),'first_period':plan['snapshots'][0]['period_end'],
        'last_period':plan['snapshots'][-1]['period_end']}


@app.post('/api/accounts/{subject_id}/reporting/design-updates/{request_id}/queue')
def queue_report_design_update(subject_id: str,request_id: uuid.UUID,payload: ReportDesignQueueRequest,
                              user: dict=Depends(_owner_user)):
    _report_design_owner(subject_id,user)
    if os.environ.get('REPORT_DESIGN_UPDATES_ENABLED')!='true':
        raise HTTPException(status_code=503,detail='Historical regeneration worker is not enabled')
    try:
        with _approval_database() as connection:
            return report_design_updates.queue_request(_portable_database_connection(connection),TENANT_ID,
                subject_id,request_id,payload.design_version,payload.plan_sha256,str(user['object_id']),
                installed_image_sha256=os.environ.get('REPORT_RENDERER_IMAGE_SHA256',''))
    except (ValueError,KeyError,TypeError) as error:raise HTTPException(status_code=409,detail=str(error)) from error
    except PostgresError as error:
        raise HTTPException(status_code=503,detail='Report design registry is not available') from error


def _supersede_prior_monthly_releases(connection, tenant_id, subject_id, period_end, report_type, new_id, actor, now):
    """Withdraw old links inside the owner approval transaction; retain files/history."""
    return connection.execute('''UPDATE report_artifact_releases AS a
        SET revoked_at=COALESCE(a.revoked_at,%s),revoked_by=COALESCE(a.revoked_by,%s),
            state_revision=a.state_revision+1,superseded_by_artifact_release_id=%s
        FROM report_generation_runs r,reporting_snapshots s
        WHERE a.tenant_id=%s AND r.tenant_id=a.tenant_id AND s.tenant_id=r.tenant_id
          AND r.generation_run_id=a.generation_run_id AND s.snapshot_id=r.snapshot_id
          AND s.subject_id=%s AND s.period_end=%s AND r.report_type=%s
          AND a.release_scope='internal_shareholder' AND a.artifact_release_id<>%s
          AND a.superseded_by_artifact_release_id IS NULL
        RETURNING artifact_release_id''',
        (now,actor,new_id,tenant_id,subject_id,period_end,report_type,new_id)).fetchall()


def _monthly_release_candidate(connection, subject_id: str, period_end: str, *, lock: bool) -> dict:
    if lock:
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
            (f'report-release:{TENANT_ID}:{subject_id}:{period_end}',))
    snapshot = connection.execute(
        """
        SELECT snapshot_id, subject_id, period_end, status, quality_status,
               publication_blockers_json, data_sha256
        FROM reporting_snapshots
        WHERE tenant_id = %s AND subject_id = %s AND period_end = %s
          AND status IN ('frozen', 'approved')
        ORDER BY created_at DESC LIMIT 1
        """,
        (TENANT_ID, subject_id, period_end),
    ).fetchone()
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Frozen monthly snapshot not found")
    lock_clause = " FOR UPDATE" if lock else ""
    runs = connection.execute(
        """
        SELECT generation_run_id, snapshot_id, report_type, status,
               output_reference, output_sha256
        FROM report_generation_runs
        WHERE tenant_id = %s AND snapshot_id = %s
          AND report_type IN ('factsheet', 'presentation')
        ORDER BY generation_run_id DESC
        """ + lock_clause,
        (TENANT_ID, snapshot["snapshot_id"]),
    ).fetchall()
    release_lock = " FOR UPDATE OF a" if lock else ""
    releases = connection.execute(
        """
        SELECT a.artifact_release_id, a.generation_run_id, a.container_name,
               a.blob_name, a.artifact_sha256, a.revoked_at, a.state_revision,
               a.superseded_by_artifact_release_id,
               r.report_type, s.subject_id, d.release_decision_id
        FROM report_artifact_releases a
        JOIN report_release_decisions d
          ON d.release_decision_id = a.release_decision_id AND d.tenant_id = a.tenant_id
        JOIN report_generation_runs r
          ON r.generation_run_id = a.generation_run_id AND r.tenant_id = a.tenant_id
        JOIN reporting_snapshots s
          ON s.snapshot_id = r.snapshot_id AND s.tenant_id = r.tenant_id
        WHERE a.tenant_id = %s AND s.snapshot_id = %s
          AND a.release_scope = 'internal_shareholder'
          AND d.release_scope = 'internal_shareholder' AND d.status = 'approved'
        ORDER BY a.generation_run_id
        """ + release_lock,
        (TENANT_ID, snapshot["snapshot_id"]),
    ).fetchall()
    try:
        preview = review_package(
            snapshot, runs, subject_id=subject_id,
            period_end=period_end, container=INTERNAL_REPORT_CONTAINER,
            releases=releases, enforce_eligibility=False,
        )
        try:
            review_package(
                snapshot, runs, subject_id=subject_id,
                period_end=period_end, container=INTERNAL_REPORT_CONTAINER,
                releases=releases,
            )
        except (InternalReleaseError, MonthlyReportError, ValueError) as error:
            preview["release_eligible"] = False
            preview["release_blocker"] = str(error)
        else:
            preview["release_eligible"] = True
            preview["release_blocker"] = None
        return preview
    except (InternalReleaseError, MonthlyReportError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/accounts/{subject_id}/reporting/monthly/{period_end}/release-preview")
def preview_subject_monthly_release(
    subject_id: str,
    period_end: str,
    user: dict[str, object] = Depends(_owner_user),
) -> dict:
    access = _portfolio_access_context(subject_id, user)
    if access["preview_mode"]:
        raise HTTPException(status_code=409, detail="Portfolio is not active")
    try:
        period_end = validate_period_end(period_end)
    except MonthlyReportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    with _database() as connection:
        preview = _monthly_release_candidate(connection, subject_id, period_end, lock=False)
    return {
        **preview,
        "message": "Tick the files to release, untick files to revoke, then submit the checklist.",
    }


@app.post("/api/accounts/{subject_id}/reporting/monthly/{period_end}/release")
def release_subject_monthly_reports(
    subject_id: str,
    period_end: str,
    payload: MonthlyReleaseRequest,
    user: dict[str, object] = Depends(_owner_user),
) -> dict:
    access = _portfolio_access_context(subject_id, user)
    if access["preview_mode"]:
        raise HTTPException(status_code=409, detail="Portfolio is not active")
    try:
        period_end = validate_period_end(period_end)
    except MonthlyReportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not AZURE_STORAGE_ACCOUNT_URL:
        raise HTTPException(status_code=503, detail="Internal report storage is unavailable")
    desired = set(payload.selected_report_types)
    if len(desired) != len(payload.selected_report_types):
        raise HTTPException(status_code=400, detail="Each report file may be selected only once")
    with _approval_database() as connection:
        preview = _monthly_release_candidate(connection, subject_id, period_end, lock=True)
        if (
            payload.snapshot_id != preview["snapshot_id"]
            or payload.package_sha256 != preview["package_sha256"]
            or payload.selection_version != preview["selection_version"]
        ):
            raise HTTPException(status_code=409, detail="The file list or approval status changed. Refresh and review it again.")
        current = set(preview["approved_types"])
        additions = desired - current
        revocations = current - desired
        if not additions and not revocations:
            return {
                "status": "unchanged", "subject_id": subject_id,
                "period_end": period_end, "approved_types": sorted(current),
                "client_distribution": "prohibited",
                "message": "No approval changes were needed.",
            }
        if additions and not preview["release_eligible"]:
            raise HTTPException(
                status_code=409,
                detail="New approval is blocked: " + str(preview["release_blocker"]),
            )
        existing = connection.execute(
            """
            SELECT release_decision_id FROM report_release_decisions
            WHERE tenant_id = %s AND snapshot_id = %s
              AND release_scope = 'internal_shareholder' AND status = 'approved'
            """,
            (TENANT_ID, preview["snapshot_id"]),
        ).fetchone()
        if revocations and existing is None:
            raise HTTPException(status_code=409, detail="The prior approval record is missing")
        by_type = {artifact["report_type"]: artifact for artifact in preview["artifacts"]}
        contents = {}
        for report_type in additions:
            artifact = by_type[report_type]
            if artifact.get('superseded'):
                raise HTTPException(status_code=409,detail='A superseded report cannot be restored as the current download')
            try:
                content = download_blob_bytes(
                    AZURE_STORAGE_ACCOUNT_URL, artifact["container_name"], artifact["blob_name"]
                )
            except RuntimeError as error:
                raise HTTPException(status_code=503, detail="A private review file could not be verified") from error
            if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
                raise HTTPException(status_code=409, detail="A review file changed after validation")
            if report_type == "factsheet":
                try:
                    _validate_release_factsheet(connection, content, subject_id=subject_id,
                                               period_end=period_end, snapshot_id=preview["snapshot_id"])
                except (MonthlyArtifactError, PdfReadError, ValueError) as error:
                    raise HTTPException(
                        status_code=409,
                        detail="The factsheet return panel does not match the approved typography; regenerate it before release",
                    ) from error
            contents[report_type] = content
        for report_type in additions:
            artifact = by_type[report_type]
            if artifact["source_kind"] == "released":
                continue  # A previously revoked, integrity-checked file already exists.
            content = contents[report_type]
            released_name = f"released/{subject_id}/{period_end}/{artifact['sha256']}-{artifact['filename']}"
            try:
                upload_bytes(
                    AZURE_STORAGE_ACCOUNT_URL, INTERNAL_REPORT_CONTAINER,
                    released_name, content, overwrite=False,
                    content_type=artifact["media_type"],
                    metadata={
                        "sha256": artifact["sha256"],
                        "release_scope": "internal_shareholder",
                        "client_distribution": "prohibited",
                        "snapshot_id": preview["snapshot_id"],
                    },
                )
            except Exception as error:
                # An earlier interrupted attempt may have uploaded the exact file.
                try:
                    existing_content = download_blob_bytes(
                        AZURE_STORAGE_ACCOUNT_URL, INTERNAL_REPORT_CONTAINER, released_name
                    )
                except Exception:
                    raise HTTPException(status_code=503, detail="Internal release storage failed") from error
                if hashlib.sha256(existing_content).hexdigest() != artifact["sha256"]:
                    raise HTTPException(status_code=409, detail="A conflicting released file exists") from error
            artifact["released_blob_name"] = released_name
        now = utc_now_iso()
        if existing is None:
            conditions = canonical_json({
                "release_scope": "internal_shareholder",
                "audience": "authorized internal users and company shareholders",
                "client_distribution": "prohibited",
                "publication_blockers_retained": preview["publication_blockers"],
                "package_sha256": preview["package_sha256"],
                "approval_mode": "individual_file_checklist",
            })
            existing = connection.execute(
                """
                INSERT INTO report_release_decisions(
                    tenant_id, snapshot_id, release_scope, status,
                    approved_by, approved_at, conditions_json
                ) VALUES (%s, %s, 'internal_shareholder', 'approved', %s, %s, %s)
                RETURNING release_decision_id
                """,
                (TENANT_ID, preview["snapshot_id"], str(user["email"]), now, conditions),
            ).fetchone()
        for report_type in sorted(additions):
            artifact = by_type[report_type]
            if artifact["source_kind"] == "released":
                updated = connection.execute(
                    """
                    UPDATE report_artifact_releases
                    SET revoked_at = NULL, revoked_by = NULL,
                        state_revision = state_revision + 1
                    WHERE tenant_id = %s AND artifact_release_id = %s
                      AND revoked_at IS NOT NULL
                    """,
                    (TENANT_ID, artifact["artifact_release_id"]),
                )
            else:
                inserted = connection.execute(
                    """
                    INSERT INTO report_artifact_releases(
                        tenant_id, release_decision_id, generation_run_id, release_scope,
                        container_name, blob_name, artifact_sha256, released_at
                    ) VALUES (%s, %s, %s, 'internal_shareholder', %s, %s, %s, %s)
                    RETURNING artifact_release_id
                    """,
                    (TENANT_ID, existing["release_decision_id"], artifact["generation_run_id"],
                     INTERNAL_REPORT_CONTAINER, artifact["released_blob_name"], artifact["sha256"], now),
                ).fetchone()
                if inserted is None:
                    raise HTTPException(status_code=409,detail='Replacement release was not recorded')
                artifact['artifact_release_id']=inserted['artifact_release_id']
                updated = connection.execute(
                    """
                    UPDATE report_generation_runs SET status = 'released_internal'
                    WHERE tenant_id = %s AND generation_run_id = %s
                      AND snapshot_id = %s AND status = 'awaiting_approval'
                    """,
                    (TENANT_ID, artifact["generation_run_id"], preview["snapshot_id"]),
                )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Report status changed during approval")
            retired=_supersede_prior_monthly_releases(
                connection,TENANT_ID,subject_id,period_end,
                report_type,artifact['artifact_release_id'],str(user['email']),now)
            if report_type=='factsheet' and 'presentation' not in by_type:
                # Decks are no longer built: a new month-end factsheet also withdraws the deck an
                # earlier snapshot released for this month, as its figures no longer match.
                retired+=_supersede_prior_monthly_releases(
                    connection,TENANT_ID,subject_id,period_end,
                    'presentation',artifact['artifact_release_id'],str(user['email']),now)
            artifact['superseded_release_ids']=[r['artifact_release_id'] for r in retired]
        for report_type in sorted(revocations):
            artifact = by_type[report_type]
            updated = connection.execute(
                """
                UPDATE report_artifact_releases
                SET revoked_at = %s, revoked_by = %s,
                    state_revision = state_revision + 1
                WHERE tenant_id = %s AND artifact_release_id = %s
                  AND revoked_at IS NULL
                """,
                (now, str(user["email"]), TENANT_ID, artifact["artifact_release_id"]),
            )
            if updated.rowcount != 1:
                raise HTTPException(status_code=409, detail="Report approval changed during revocation")
        connection.execute(
            """
            INSERT INTO audit_events(
                tenant_id, occurred_at, actor_type, actor_id, event_type,
                subject_id, outcome, details_json
            ) VALUES (%s, %s, 'user', %s, 'month_end_file_approvals_changed',
                      %s, 'internal_scope_client_blocked', %s)
            """,
            (TENANT_ID, now, str(user["object_id"]), subject_id,
             canonical_json({
                 "snapshot_id": preview["snapshot_id"],
                 "period_end": period_end,
                 "package_sha256": preview["package_sha256"],
                 "release_decision_id": existing["release_decision_id"],
                 "approved_types": sorted(additions),
                 "revoked_types": sorted(revocations),
                 "resulting_approved_types": sorted(desired),
                 "superseded_release_ids": {kind:by_type[kind].get('superseded_release_ids',[]) for kind in sorted(additions)},
             })),
        )
    return {
        "status": "approval_updated",
        "subject_id": subject_id,
        "period_end": period_end,
        "release_decision_id": existing["release_decision_id"],
        "approved_types": sorted(desired),
        "newly_approved_types": sorted(additions),
        "revoked_types": sorted(revocations),
        "client_distribution": "prohibited",
        "message": "File approvals were updated. Checked files are available to authorized users; unchecked files are no longer downloadable.",
    }


def _monthly_download_filename(
    artifact: dict[str, object], *, subject_id: str, period_end: str
) -> str:
    """Keep content-addressed storage private; name MS and T downloads for readers."""
    prefixes = {"alpha-world-ms": "AlphaWorld_MS", "alpha-world-t": "AlphaWorld_T"}
    if subject_id in prefixes:
        period = validate_period_end(period_end)
        kind = str(artifact["report_type"])
        definitions = {
            "factsheet": ("Factsheet", ".pdf"),
            "presentation": ("Presentation", ".pptx"),
        }
        if kind not in definitions:
            raise MonthlyReportError("Unsupported monthly report type")
        label, extension = definitions[kind]
        return f"{prefixes[subject_id]}_{label}_{period}{extension}"
    name = PurePosixPath(str(artifact["filename"])).name
    # Stored files are content-addressed as "<sha256>-<name>" (twice once released);
    # readers get the plain name.
    readable = re.sub(r"^(?:[0-9a-f]{64}-)+", "", name)
    return readable if readable and not readable.startswith(".") else name


def _download_monthly_report(
    *, subject_id: str, artifact_release_id: int, user: dict[str, object]
) -> Response:
    _portfolio_access_context(subject_id, user)
    if artifact_release_id <= 0:
        raise HTTPException(status_code=404, detail="Monthly report not found")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT a.artifact_release_id, a.container_name, a.blob_name,
                   a.artifact_sha256, r.report_type, r.snapshot_id,
                   s.period_end, s.subject_id
            FROM report_artifact_releases a
            JOIN report_release_decisions d
              ON d.release_decision_id = a.release_decision_id
             AND d.tenant_id = a.tenant_id
            JOIN report_generation_runs r
              ON r.generation_run_id = a.generation_run_id
             AND r.tenant_id = a.tenant_id
            JOIN reporting_snapshots s
              ON s.snapshot_id = r.snapshot_id
             AND s.tenant_id = r.tenant_id
            WHERE a.tenant_id = %s AND a.artifact_release_id = %s
              AND a.release_scope = 'internal_shareholder'
              AND a.revoked_at IS NULL
              AND a.superseded_by_artifact_release_id IS NULL
              AND d.release_scope = 'internal_shareholder'
              AND d.status = 'approved'
              AND d.snapshot_id = r.snapshot_id
              AND s.subject_id = %s
              AND s.status IN ('frozen', 'approved', 'superseded')
              AND s.quality_status <> 'fail'
              AND r.status = 'released_internal'
            """,
            (TENANT_ID, artifact_release_id, subject_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Monthly report not found")
    try:
        period_end = validate_period_end(str(row["period_end"]))
        artifact = validate_artifact(
            row,
            account_id=subject_id,
            period_end=period_end,
            container=INTERNAL_REPORT_CONTAINER,
        )
        content = download_blob_bytes(
            AZURE_STORAGE_ACCOUNT_URL,
            artifact["container_name"],
            artifact["blob_name"],
        )
    except (MonthlyReportError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact["sha256"]:
        raise HTTPException(
            status_code=503,
            detail="Monthly report integrity check failed",
        )
    return Response(
        content=content,
        media_type=str(artifact["media_type"]),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_monthly_download_filename(artifact, subject_id=subject_id, period_end=period_end)}"'
            ),
            "X-Content-SHA256": digest,
            "X-Report-Subject-ID": subject_id,
            "X-Report-Period-End": period_end,
            "X-Report-Scope": "internal-shareholder",
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/api/reporting/artifacts/{artifact_release_id}/download")
def download_monthly_report(
    artifact_release_id: int,
    user: dict[str, object] = Depends(_authorized_user),
) -> Response:
    return _download_monthly_report(
        subject_id=PILOT_ACCOUNT_ID,
        artifact_release_id=artifact_release_id,
        user=user,
    )


@app.get("/api/accounts/{subject_id}/reporting/artifacts/{artifact_release_id}/download")
def download_subject_monthly_report(
    subject_id: str,
    artifact_release_id: int,
    user: dict[str, object] = Depends(_authorized_user),
) -> Response:
    return _download_monthly_report(
        subject_id=subject_id,
        artifact_release_id=artifact_release_id,
        user=user,
    )


def _download_monthly_review_report(
    *, subject_id: str, generation_run_id: int, user: dict[str, object]
) -> Response:
    if user["role"] != "owner":
        raise HTTPException(status_code=403, detail="Owner review is required")
    _portfolio_access_context(subject_id, user)
    if generation_run_id <= 0:
        raise HTTPException(status_code=404, detail="Monthly review report not found")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT r.generation_run_id, r.report_type, r.status,
                   r.output_reference, r.output_sha256,
                   s.period_end, s.subject_id,
                   a.artifact_release_id, a.container_name, a.blob_name,
                   a.artifact_sha256
            FROM report_generation_runs r
            JOIN reporting_snapshots s
              ON s.snapshot_id = r.snapshot_id AND s.tenant_id = r.tenant_id
            LEFT JOIN report_artifact_releases a
              ON a.generation_run_id = r.generation_run_id
             AND a.tenant_id = r.tenant_id
             AND a.release_scope = 'internal_shareholder'
            WHERE r.tenant_id = %s AND r.generation_run_id = %s
              AND r.status IN ('awaiting_approval', 'released_internal')
              AND s.subject_id = %s
              AND s.status IN ('frozen', 'approved')
            """,
            (TENANT_ID, generation_run_id, subject_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Monthly review report not found")
    try:
        period_end = validate_period_end(str(row["period_end"]))
        artifact = (
            validate_artifact(
                row, account_id=subject_id, period_end=period_end,
                container=INTERNAL_REPORT_CONTAINER,
            )
            if row["artifact_release_id"] is not None
            else validate_review_artifact(
                row, account_id=subject_id, period_end=period_end,
                container=INTERNAL_REPORT_CONTAINER,
            )
        )
        if artifact["sha256"] != str(row["output_sha256"]).lower():
            raise MonthlyReportError("Review file differs from its generation record")
        content = download_blob_bytes(
            AZURE_STORAGE_ACCOUNT_URL,
            artifact["container_name"],
            artifact["blob_name"],
        )
    except (MonthlyReportError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact["sha256"]:
        raise HTTPException(status_code=503, detail="Monthly review report integrity check failed")
    return Response(
        content=content,
        media_type=str(artifact["media_type"]),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_monthly_download_filename(artifact, subject_id=subject_id, period_end=period_end)}"'
            ),
            "X-Content-SHA256": digest,
            "X-Report-Subject-ID": subject_id,
            "X-Report-Period-End": period_end,
            "X-Report-Scope": "owner-review",
            "Cache-Control": "private, no-store",
        },
    )


@app.get("/api/reporting/review-artifacts/{generation_run_id}/download")
def download_monthly_review_report(
    generation_run_id: int,
    user: dict[str, object] = Depends(_authorized_user),
) -> Response:
    return _download_monthly_review_report(
        subject_id=PILOT_ACCOUNT_ID,
        generation_run_id=generation_run_id,
        user=user,
    )


@app.get(
    "/api/accounts/{subject_id}/reporting/review-artifacts/"
    "{generation_run_id}/download"
)
def download_subject_monthly_review_report(
    subject_id: str,
    generation_run_id: int,
    user: dict[str, object] = Depends(_authorized_user),
) -> Response:
    return _download_monthly_review_report(
        subject_id=subject_id,
        generation_run_id=generation_run_id,
        user=user,
    )


@app.post("/api/agent/analyze")
def run_grounded_analysis(
    payload: AnalystNarrativeRequest,
    request: Request,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    requested_as_of = _parse_as_of(payload.as_of)
    allowed_subjects = _authorized_subject_ids(user, feature_key="ai_analyst")
    context = AgentToolContext(
        tenant_id=TENANT_ID,
        actor_object_id=str(user["object_id"]),
        allowed_subject_ids=frozenset(allowed_subjects),
        correlation_id=request.state.correlation_id,
    )
    try:
        AI_ANALYST_CONFIG.validate()
        with _database() as connection:
            return generate_grounded_narrative(
                connection,
                context,
                AI_ANALYST_CONFIG,
                subject_id=payload.subject_id,
                analysis_type=payload.analysis_type,
                as_of=requested_as_of,
                benchmark_id=payload.benchmark_id,
                question=payload.question,
            )
    except (AIAnalystError, AgentToolError) as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error


@app.get("/api/agent/conversations")
def list_analyst_conversations(
    subject_id: str = PILOT_ACCOUNT_ID,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    if subject_id not in _authorized_subject_ids(user, feature_key="ai_analyst"):
        raise HTTPException(status_code=404, detail="Portfolio not available")
    identity_sha256 = _chat_identity_sha256(user)
    with _database() as connection:
        rows = connection.execute(
            """
            SELECT c.conversation_id, c.subject_id, c.title, c.status,
                   c.created_at, c.updated_at, COUNT(m.message_id) AS message_count
            FROM ai_analyst_conversations c
            LEFT JOIN ai_analyst_messages m
              ON m.tenant_id = c.tenant_id
             AND m.conversation_id = c.conversation_id
            WHERE c.tenant_id = %s AND c.subject_id = %s
              AND c.owner_identity_sha256 = %s AND c.status = 'active'
            GROUP BY c.conversation_id, c.subject_id, c.title, c.status,
                     c.created_at, c.updated_at
            ORDER BY c.updated_at DESC
            LIMIT 50
            """,
            (TENANT_ID, subject_id, identity_sha256),
        ).fetchall()
    return {
        "subject_id": subject_id,
        "conversations": [dict(row) for row in rows],
        "privacy": "Conversations are visible only to the signed-in user who created them.",
    }


@app.post("/api/agent/conversations")
def create_analyst_conversation(
    payload: AnalystConversationCreateRequest,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    if payload.subject_id not in _authorized_subject_ids(
        user, feature_key="ai_analyst"
    ):
        raise HTTPException(status_code=404, detail="Portfolio not available")
    conversation_id = str(uuid.uuid4())
    identity_sha256 = _chat_identity_sha256(user)
    title = " ".join((payload.title or "New conversation").split()).strip()
    if not title:
        title = "New conversation"
    now = datetime.now(timezone.utc)
    with _chat_database() as connection:
        existing_count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM ai_analyst_conversations
            WHERE tenant_id = %s AND owner_identity_sha256 = %s
              AND subject_id = %s AND status = 'active'
            """,
            (TENANT_ID, identity_sha256, payload.subject_id),
        ).fetchone()
        if int(existing_count["count"] or 0) >= 50:
            raise HTTPException(
                status_code=409,
                detail="Archive an existing conversation before creating another one",
            )
        connection.execute(
            """
            INSERT INTO ai_analyst_conversations(
                conversation_id, tenant_id, subject_id, owner_identity_sha256,
                title, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)
            """,
            (
                conversation_id,
                TENANT_ID,
                payload.subject_id,
                identity_sha256,
                title,
                now,
                now,
            ),
        )
        connection.commit()
    return {
        "conversation_id": conversation_id,
        "subject_id": payload.subject_id,
        "title": title,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "messages": [],
    }


@app.get("/api/agent/conversations/{conversation_id}")
def get_analyst_conversation(
    conversation_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    conversation, messages = _conversation_bundle(conversation_id, user)
    return {"conversation": conversation, "messages": messages}


@app.post("/api/agent/conversations/{conversation_id}/messages")
def send_analyst_chat_message(
    conversation_id: str,
    payload: AnalystChatMessageRequest,
    request: Request,
    user: dict[str, object] = Depends(_authorized_user),
) -> dict:
    try:
        AI_ANALYST_CONFIG.validate()
    except AIAnalystError as error:
        raise HTTPException(
            status_code=error.status_code,
            detail={"code": error.code, "message": str(error)},
        ) from error
    normalized_id = _conversation_uuid(conversation_id)
    requested_as_of = _parse_as_of(payload.as_of)
    identity_sha256 = _chat_identity_sha256(user)
    allowed_subjects = _authorized_subject_ids(user, feature_key="ai_analyst")
    with _database() as connection:
        conversation = _conversation_row(connection, normalized_id, identity_sha256)
        if str(conversation["status"]) != "active":
            raise HTTPException(status_code=409, detail="Conversation is archived")
        if str(conversation["subject_id"]) not in allowed_subjects:
            raise HTTPException(status_code=404, detail="Portfolio not available")
        messages = _conversation_messages(connection, normalized_id)
        context = AgentToolContext(
            tenant_id=TENANT_ID,
            actor_object_id=user.get("object_id") or user.get("email") or "unknown",
            allowed_subject_ids=frozenset(allowed_subjects),
            correlation_id=request.state.correlation_id,
        )
        try:
            response = generate_grounded_narrative(
                connection,
                context,
                AI_ANALYST_CONFIG,
                subject_id=str(conversation["subject_id"]),
                analysis_type="interactive_question",
                as_of=requested_as_of,
                benchmark_id=payload.benchmark_id,
                question=payload.question,
                conversation_history=model_history(messages),
            )
        except (AIAnalystError, AgentToolError) as error:
            raise HTTPException(
                status_code=error.status_code,
                detail={"code": error.code, "message": str(error)},
            ) from error

    user_message_id = str(uuid.uuid4())
    assistant_message_id = str(uuid.uuid4())
    assistant_text = assistant_plain_text(response)
    now = datetime.now(timezone.utc)
    with _chat_database() as connection:
        locked = connection.execute(
            """
            SELECT title, status FROM ai_analyst_conversations
            WHERE tenant_id = %s AND conversation_id = %s
              AND owner_identity_sha256 = %s
            FOR UPDATE
            """,
            (TENANT_ID, normalized_id, identity_sha256),
        ).fetchone()
        if locked is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if str(locked["status"]) != "active":
            raise HTTPException(status_code=409, detail="Conversation is archived")
        state = connection.execute(
            """
            SELECT COALESCE(MAX(sequence_no), 0) AS maximum,
                   COUNT(*) AS message_count
            FROM ai_analyst_messages
            WHERE tenant_id = %s AND conversation_id = %s
            """,
            (TENANT_ID, normalized_id),
        ).fetchone()
        if int(state["message_count"] or 0) + 2 > MAX_CONVERSATION_MESSAGES:
            raise HTTPException(
                status_code=409,
                detail="This conversation reached its message limit; start a new chat",
            )
        sequence = int(state["maximum"] or 0) + 1
        connection.execute(
            """
            INSERT INTO ai_analyst_messages(
                message_id, tenant_id, conversation_id, sequence_no, role,
                content_text, response_json, as_of_date, benchmark_id,
                correlation_id, created_at
            ) VALUES
                (%s, %s, %s, %s, 'user', %s, NULL, %s, %s, %s, %s),
                (%s, %s, %s, %s, 'assistant', %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                user_message_id,
                TENANT_ID,
                normalized_id,
                sequence,
                payload.question.strip(),
                requested_as_of,
                payload.benchmark_id,
                request.state.correlation_id,
                now,
                assistant_message_id,
                TENANT_ID,
                normalized_id,
                sequence + 1,
                assistant_text,
                json.dumps(response, sort_keys=True, separators=(",", ":")),
                response["as_of"],
                payload.benchmark_id,
                request.state.correlation_id,
                now,
            ),
        )
        title = (
            conversation_title(payload.question)
            if str(locked["title"]) == "New conversation"
            else str(locked["title"])
        )
        connection.execute(
            """
            UPDATE ai_analyst_conversations
            SET title = %s, updated_at = %s
            WHERE tenant_id = %s AND conversation_id = %s
            """,
            (title, now, TENANT_ID, normalized_id),
        )
        connection.commit()
    return {
        **response,
        "conversation_id": normalized_id,
        "conversation_title": title,
        "user_message_id": user_message_id,
        "assistant_message_id": assistant_message_id,
        "saved": True,
    }


@app.post("/api/agent/conversations/{conversation_id}/archive")
def archive_analyst_conversation(
    conversation_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    normalized_id = _conversation_uuid(conversation_id)
    identity_sha256 = _chat_identity_sha256(user)
    with _chat_database() as connection:
        result = connection.execute(
            """
            UPDATE ai_analyst_conversations
            SET status = 'archived', updated_at = now()
            WHERE tenant_id = %s AND conversation_id = %s
              AND owner_identity_sha256 = %s AND status = 'active'
            """,
            (TENANT_ID, normalized_id, identity_sha256),
        )
        if result.rowcount != 1:
            raise HTTPException(status_code=404, detail="Conversation not found")
        connection.commit()
    return {"conversation_id": normalized_id, "status": "archived"}


@app.get("/api/agent/conversations/{conversation_id}/export.json")
def export_analyst_conversation_json(
    conversation_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    conversation, messages = _conversation_bundle(conversation_id, user)
    content = json.dumps(
        transcript_payload(conversation, messages),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    ) + "\n"
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{conversation["subject_id"]}_AI_Conversation_'
                f'{conversation["conversation_id"]}.json"'
            )
        },
    )


@app.get("/api/agent/conversations/{conversation_id}/export.md")
def export_analyst_conversation_markdown(
    conversation_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    conversation, messages = _conversation_bundle(conversation_id, user)
    return Response(
        content=transcript_markdown(conversation, messages),
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{conversation["subject_id"]}_AI_Conversation_'
                f'{conversation["conversation_id"]}.md"'
            )
        },
    )


@app.get(
    "/api/agent/conversations/{conversation_id}/messages/{message_id}/analysis.json"
)
def download_analyst_response_json(
    conversation_id: str,
    message_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    response = _assistant_response_for_download(conversation_id, message_id, user)
    return Response(
        content=analysis_json(response),
        media_type="application/json",
        headers={
            "Content-Disposition": (
                f'attachment; filename="U16088571_AI_Analysis_{message_id}.json"'
            )
        },
    )


def _saved_chart(
    conversation_id: str,
    message_id: str,
    chart_id: str,
    user: dict[str, str],
) -> dict:
    response = _assistant_response_for_download(conversation_id, message_id, user)
    chart = next(
        (
            item
            for item in response.get("visualizations") or []
            if str(item.get("id")) == chart_id
        ),
        None,
    )
    if chart is None:
        raise HTTPException(status_code=404, detail="Generated chart not found")
    return chart


@app.get(
    "/api/agent/conversations/{conversation_id}/messages/{message_id}/charts/{chart_id}.csv"
)
def download_analyst_chart_csv(
    conversation_id: str,
    message_id: str,
    chart_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    chart = _saved_chart(conversation_id, message_id, chart_id, user)
    return Response(
        content=chart_csv(chart),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{chart_id}.csv"'
        },
    )


@app.get(
    "/api/agent/conversations/{conversation_id}/messages/{message_id}/charts/{chart_id}.svg"
)
def download_analyst_chart_svg(
    conversation_id: str,
    message_id: str,
    chart_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    chart = _saved_chart(conversation_id, message_id, chart_id, user)
    try:
        content = chart_svg(chart)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(
        content=content,
        media_type="image/svg+xml",
        headers={
            "Content-Disposition": f'attachment; filename="{chart_id}.svg"'
        },
    )


@app.get("/api/portfolio/{account_id}/export/{dataset}.csv")
def export_csv(
    account_id: str,
    dataset: str,
    as_of: str | None = None,
    from_date: str | None = None,
    symbol: str | None = None,
    side: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> Response:
    from .subject_exports import subject_export
    access = _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        columns, rows, effective_as_of, start = subject_export(
            connection, tenant_id=TENANT_ID, subject_id=account_id, access=access,
            dataset=dataset, requested_as_of=requested_as_of, from_date=from_date,
            symbol=symbol, side=side, cash_filter=_cash_filter_values,
            trade_filter=_trade_filter_values,
        )
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_safe(row[column]) for column in columns])
    filename_period = (
        f"{start}_{effective_as_of}" if dataset == "cash-activity" else effective_as_of
    )
    filename = f"{account_id}_{dataset}_{filename_period}.csv"
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


BENCHMARK_RETURN_BASIS = {
    "price": ("price_return", "Price return (excludes dividends)"),
    "gross_total_return": ("total_return", "Gross total return (dividends reinvested)"),
    "net_total_return": ("total_return", "Net total return (dividends reinvested after withholding)"),
    "fund_total_return": ("total_return", "Fund total return"),
}


def _benchmark_return_basis(return_convention: object) -> dict[str, str]:
    """Label the registered benchmark convention; unknown conventions stay explicit."""
    basis, label = BENCHMARK_RETURN_BASIS.get(
        str(return_convention or ""), ("unknown", "Return convention not registered")
    )
    return {"return_basis": basis, "return_basis_label": label}


@app.get("/api/portfolio/{account_id}/benchmark-status")
def benchmark_status(
    account_id: str, user: dict[str, str] = Depends(_authorized_user)
) -> dict:
    _portfolio_access_context(account_id, user)
    with _database() as connection:
        benchmarks = connection.execute(
            """
            SELECT b.benchmark_id, b.symbol, b.name, b.provider, b.currency,
                   b.return_convention, b.license_status, m.mapping_type,
                   m.effective_from,
                   m.reporting_currency,
                   (SELECT COUNT(*) FROM benchmark_prices p
                    WHERE p.benchmark_id = b.benchmark_id
                      AND p.observation_status = 'official_close'
                      AND p.quality_status IN ('pass', 'warn')) AS observations,
                   (SELECT MAX(p.observation_date) FROM benchmark_prices p
                    WHERE p.benchmark_id = b.benchmark_id
                      AND p.observation_status = 'official_close'
                      AND p.quality_status IN ('pass', 'warn')) AS latest_observation,
                   (SELECT COUNT(*) FROM benchmark_prices p
                    WHERE p.benchmark_id = b.benchmark_id
                      AND p.observation_status = 'provisional_close'
                      AND p.quality_status IN ('pass', 'warn')) AS provisional_observations,
                   (SELECT MAX(p.observation_date) FROM benchmark_prices p
                    WHERE p.benchmark_id = b.benchmark_id
                      AND p.observation_status = 'provisional_close'
                      AND p.quality_status IN ('pass', 'warn')) AS latest_provisional_observation
            FROM portfolio_benchmark_mapping m
            JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id
            WHERE m.subject_id = %s AND b.active = true
              AND m.effective_from <= CURRENT_DATE::text
              AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)
            ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END,
                     b.name
            """,
            (account_id,),
        ).fetchall()
    if not benchmarks:
        return {
            "account_id": account_id,
            "configured": False,
            "publishable": False,
            "message": "No primary benchmark is configured.",
        }
    items = []
    for row in benchmarks:
        item = dict(row)
        item.update(_benchmark_return_basis(item.get("return_convention")))
        item["publishable"] = (
            item["license_status"] == "approved"
            and int(item["observations"] or 0) >= 31
            and item["latest_observation"] is not None
        )
        item["internal_ready"] = (
            item["license_status"] == "restricted"
            and int(item["provisional_observations"] or 0) >= 31
            and item["latest_provisional_observation"] is not None
        )
        item["development_ready"] = item["internal_ready"]
        item["message"] = (
            "Validated official-close benchmark data are ready for comparison."
            if item["publishable"]
            else (
                "The comparison is approved for authorized internal and shareholder use. Client distribution remains prohibited until Bloomberg replaces yfinance."
                if item["internal_ready"]
                else "Comparison publication is blocked until a licensed provider is approved and validated official closes are loaded."
            )
        )
        items.append(item)
    primary = next(
        (item for item in items if item["mapping_type"] == "primary"), items[0]
    )
    return {
        "account_id": account_id,
        "configured": True,
        "publishable": primary["publishable"],
        "internal_ready": primary["internal_ready"],
        "development_ready": primary["development_ready"],
        "benchmark": primary,
        "benchmarks": items,
        "message": primary["message"],
        "viewer": user["email"],
    }


@app.get("/api/portfolio/{account_id}/benchmark-comparison")
def benchmark_comparison(
    account_id: str,
    benchmark_id: str | None = None,
    as_of: str | None = None,
    user: dict[str, str] = Depends(_authorized_user),
) -> dict:
    _portfolio_access_context(account_id, user)
    requested_as_of = _parse_as_of(as_of)
    with _database() as connection:
        benchmark = connection.execute(
            """
            SELECT b.benchmark_id, b.symbol, b.name, b.provider,
                   b.license_status, b.return_convention, m.mapping_type,
                   m.reporting_currency
            FROM portfolio_benchmark_mapping m
            JOIN benchmark_master b ON b.benchmark_id = m.benchmark_id
              AND b.tenant_id = m.tenant_id
            WHERE m.tenant_id = %s AND m.subject_id = %s
              AND (%s::text IS NULL OR b.benchmark_id = %s)
              AND b.active = true
              AND m.effective_from <= CURRENT_DATE::text
              AND (m.effective_to IS NULL OR m.effective_to >= CURRENT_DATE::text)
            ORDER BY CASE m.mapping_type WHEN 'primary' THEN 0 ELSE 1 END,
                     b.name
            LIMIT 1
            """
            ,
            (TENANT_ID, account_id, benchmark_id, benchmark_id),
        ).fetchone()
        if benchmark is None:
            records = []
        else:
            records = connection.execute(
                """
                SELECT observation_date, portfolio_value, benchmark_level,
                       normalized_portfolio, normalized_benchmark, portfolio_return,
                       benchmark_return, excess_return, quality_status
                FROM performance_comparison_daily
                WHERE tenant_id = %s AND subject_id = %s AND benchmark_id = %s
                  AND calculation_version = %s
                  AND reporting_currency = %s
                  AND quality_status IN ('pass', 'warn')
                  AND (%s::text IS NULL OR observation_date <= %s)
                ORDER BY observation_date
                """,
                (
                    TENANT_ID,
                    account_id,
                    benchmark["benchmark_id"],
                    website_comparison_version(account_id, benchmark["reporting_currency"]),
                    benchmark["reporting_currency"],
                    requested_as_of,
                    requested_as_of,
                ),
            ).fetchall()
    if benchmark is None or not records:
        return {
            "account_id": account_id,
            "as_of": requested_as_of,
            "available": False,
            "publishable": False,
            "message": "No validated provisional comparison is available.",
        }
    rows = _comparison_rows(records)
    return {
        "account_id": account_id,
        "as_of": requested_as_of,
        "available": True,
        "development_only": False,
        "internal_use_only": True,
        "internal_release_allowed": True,
        "publishable": False,
        "benchmark": benchmark,
        "benchmark_return_convention": benchmark["return_convention"],
        **{
            f"benchmark_{key}": value
            for key, value in _benchmark_return_basis(benchmark["return_convention"]).items()
        },
        "reporting_currency": benchmark["reporting_currency"],
        "calculation_version": website_comparison_version(account_id, benchmark["reporting_currency"]),
        "metrics": comparison_metrics(rows),
        "rolling": rolling_benchmark_statistics(rows),
        "series": [
            {
                "date": row.observation_date,
                "portfolio": row.normalized_portfolio,
                "benchmark": row.normalized_benchmark,
            }
            for row in rows
        ],
        "message": "",
        "viewer": user["email"],
    }


ACCOUNT_SECTIONS = {
    "overview": "Overview",
    "performance": "Performance",
    "holdings": "Holdings",
    "risk": "Risk analytics",
    "modelling": "Modelling",
    "cash": "Cash",
    "trades": "Trade history",
    "reports": "Monthly reports",
    "exports": "Data exports",
    "ai-analyst": "AI analyst",
}

# Shorter names for the account tab bar; page headings keep the full names.
ACCOUNT_TAB_LABELS = {
    "risk": "Risk",
    "trades": "Trades",
    "reports": "Reports",
    "exports": "Exports",
}

ONBOARDING_PREVIEW_SECTIONS = {
    "overview",
    "performance",
    "risk",
    "cash",
    "trades",
    "holdings",
    "exports",
}

PLATFORM_SECTIONS = {
    "combined-holdings": "Combined holdings",
    "system-health": "System health",
    "metrics-guide": "Metrics guide",
    "ibkr-connections": "IBKR connections",
    "account-onboarding": "Account onboarding",
    "administration": "Administration",
}

ALL_PAGE_SECTIONS = ACCOUNT_SECTIONS | PLATFORM_SECTIONS


def _platform_navigation(user: dict[str, object], *, active: str | None = None) -> str:
    from . import agent_web, month_team_web

    links = [
        ("accounts", "Accounts", "/"),
        ("combined-holdings", "Combined holdings", "/combined-holdings"),
        ("metrics-guide", "Metrics guide", "/metrics-guide"),
    ]
    # The agents' outputs (weekly briefs, alerts, drafts, data checks) sit one click from every page.
    if agent_web.ui_enabled_for(user):
        links.append(("agent-inbox", "Agent inbox", "/agent-inbox"))
    operations = [("system-health", "System health", "/system-health")]
    if user.get("role", "owner") in {
        "owner",
        "platform_admin",
        "onboarding_operator",
    }:
        operations.append(
            ("broker-connections", "Broker connections", "/broker-connections")
            if onboarding_v2_enabled()
            else ("ibkr-connections", "IBKR connections", "/ibkr-connections")
        )
        operations.append(
            ("account-onboarding", "Account onboarding", "/account-onboarding")
        )
    if month_team_web.is_month_owner(user):
        operations.append(("month-end", "Month-end", "/month-end"))
    if user.get("role", "owner") == "owner":
        operations.append(("administration", "Administration", "/administration"))

    def link(key: str, label: str, href: str, css: str) -> str:
        return (
            f'<a class="{css}{" active" if key == active else ""}" href="{href}"'
            f'{" data-agent-inbox" if key == "agent-inbox" else ""}'
            f'{" aria-current=\"page\"" if key == active else ""}>{escape(label)}</a>'
        )

    in_operations = any(key == active for key, _, _ in operations)
    return (
        '<nav class="platform-nav" aria-label="Platform navigation">'
        + "".join(link(key, label, href, "platform-nav-link") for key, label, href in links)
        + '<details class="platform-menu"><summary class="platform-nav-link'
        + (" active" if in_operations else "")
        + '">Operations</summary><div class="platform-menu-list">'
        + "".join(link(key, label, href, "platform-menu-link") for key, label, href in operations)
        + "</div></details></nav>"
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, user: dict[str, object] = Depends(_authorized_user)) -> str:
    viewer = escape(str(user["email"] or "authorized user"))
    role_label = escape(user.get("role", "owner").title())
    account_cards = []
    for subject in _account_directory(user):
        account = escape(str(subject["subject_id"]))
        from .viewer_labels import account_card_name
        account_display_name = escape(account_card_name(subject))
        currency = escape(str(subject["reporting_currency"]))
        start_value = subject["history_start_date"]
        try:
            parsed_start = date.fromisoformat(str(start_value))
            start_label = f"{parsed_start.day} {parsed_start.strftime('%B %Y')}"
        except (TypeError, ValueError):
            start_label = str(start_value)
        account_cards.append(
            f'<a class="account-card" href="/accounts/{account}/overview">'
            f'<div class="account-badge">{currency} reporting</div>'
            f'<div class="account-id">{account_display_name}</div>'
            f'<div class="account-stats" data-account-stats="{account}">'
            '<div class="account-nav-value" data-nav>…</div>'
            '<div class="account-returns" data-returns></div>'
            '<div class="muted" data-asof></div></div>'
            f'<div class="muted account-since">Data from {escape(start_label)}</div>'
            '<div class="open">Open account <span aria-hidden="true">→</span></div></a>'
        )
    cards_html = "".join(account_cards) or (
        '<div class="empty">No active portfolio has been assigned to this account. '
        'Contact the platform owner if access is expected.</div>'
    )
    platform_navigation = _platform_navigation(user, active="accounts")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Accounts · {PLATFORM_NAME}</title>
<style>{HOME_THEME_CSS}</style></head><body>
<header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{platform_navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header>
<main><section class="home-hero"><div class="eyebrow">Portfolio intelligence</div><h1>Accounts</h1><div class="lead">Select an account to open its portfolio workspace.</div><div id="accounts-summary" class="accounts-summary" aria-live="polite"></div></section><div class="accounts">{cards_html}</div></main>
{HOME_SCRIPT}</body></html>"""


COMBINED_HOLDINGS_PAGE_SCRIPT = r"""
<script>
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json=async response=>{const payload=await response.json();if(!response.ok)throw new Error(payload.detail||`http_${response.status}`);return payload};
const fmt=n=>n==null||n===''?'—':new Intl.NumberFormat('en-US',{maximumFractionDigits:2}).format(Number(n));
const pct=n=>n==null?'—':new Intl.NumberFormat('en-US',{style:'percent',maximumFractionDigits:2}).format(Number(n));
const money=n=>n==null||n===''||!Number.isFinite(Number(n))?'—':new Intl.NumberFormat('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}).format(Number(n)).replace(/^-/,'−');
const toned=(text,n)=>!Number.isFinite(Number(n))||n==null||Math.abs(Number(n))<1e-12?text:`<span class="${Number(n)>0?'gain':'loss'}">${Number(n)>0?'+':''}${text.replace(/^-/,'−')}</span>`;
const combinedForm=document.querySelector('#combined-form');
const loadCombined=()=>{
  const currency=document.querySelector('#combined-currency').value.trim().toUpperCase()||'USD',asOf=document.querySelector('#combined-asof').value,params=new URLSearchParams({currency});
  if(asOf)params.set('as_of',asOf);
  document.querySelector('#combined-note').textContent='Loading…';
  fetch(`/api/holdings/combined?${params}`).then(json).then(data=>{
    document.querySelector('#combined-currency-label').textContent=data.currency;
    document.querySelector('#combined-accounts').innerHTML=data.accounts.map(item=>`<div class="status-item"><span class="label">${esc(item.display_name)}</span><strong>${esc(item.snapshot_date||'No snapshot')}</strong><span class="muted">${esc(item.holdings_count)} holdings${item.fx_missing_conids.length?` · ${esc(item.fx_missing_conids.length)} without FX`:''}</span></div>`).join('');
    document.querySelector('#combined-holdings').innerHTML=data.holdings.map(x=>`<tr><td>${esc(x.symbol||x.conid)}</td><td>${esc(x.description||'')}</td><td>${esc(x.sector||'')}</td><td><details><summary>${esc(x.account_count)}</summary>${x.accounts.map(a=>`<div class="muted">${esc(a.display_name)}: ${fmt(a.quantity_text)} · ${a.fx_missing?'FX unavailable':esc(fmt(a.reporting_market_value_text))}</div>`).join('')}</details></td><td>${fmt(x.quantity_text)}</td><td>${money(x.reporting_market_value_text)}${x.incomplete?' <span class="muted" title="At least one account has no usable FX rate for this holding">(partial)</span>':''}</td><td>${money(x.reporting_cost_basis_text)}</td><td>${toned(money(x.reporting_unrealized_pnl_text),x.reporting_unrealized_pnl_text)}</td><td>${toned(pct(x.unrealized_pnl_pct),x.unrealized_pnl_pct)}</td><td data-sort="${x.weight??''}">${pct(x.weight)}</td></tr>`).join('')||'<tr><td colspan="10">No holdings are available for your accounts on or before this date.</td></tr>';
    document.querySelector('#combined-note').textContent=`${data.accounts.length} physical account(s) combined in ${data.currency}. Filtered and composite portfolios are excluded because they are built from these accounts. Each account uses its own latest snapshot and dated FX history. Weights are shares of gross combined holdings value (cash excluded).${data.incomplete?' Some holdings have no usable FX rate; their values are partial.':''}${data.skipped_accounts.length?` ${data.skipped_accounts.length} account(s) could not be included.`:''}`;
  }).catch(error=>{document.querySelector('#combined-note').textContent=`Combined holdings are unavailable: ${error.message}`;document.querySelector('#combined-holdings').innerHTML=''});
};
combinedForm.addEventListener('submit',event=>{event.preventDefault();loadCombined()});
wrTable('#combined-holdings',{filter:'#combined-filter',count:'#combined-filter-count'});
loadCombined();
</script>
"""


SYSTEM_HEALTH_PAGE_SCRIPT = r"""
<script>
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json=async response=>{const payload=await response.json();if(!response.ok)throw new Error(`http_${response.status}`);return payload};
const when=value=>{if(!value)return'—';const parsed=new Date(value);return isNaN(parsed)||!/T|:/.test(String(value))?String(value):parsed.toLocaleString(undefined,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})};
const words=value=>{const text=String(value||'').replaceAll('_',' ').trim();return text?text[0].toUpperCase()+text.slice(1):''};
const pill=(value,label)=>`<span class="ops-pill is-${esc(String(value||'').toLowerCase())}">${esc(label||words(value)||'—')}</span>`;
const healthView=document.querySelector('[data-section="system-health"]'),adminChat=healthView?.dataset.adminChat==='1';
const githubLink=url=>url&&/^https:\/\/github\.com\//.test(url)?url:'';
const askAdministrator=text=>'/administration?ask='+encodeURIComponent(text);
const headline={pass:'Everything is running normally',warn:'A few things need a look',fail:'Something needs attention'};
const incidentCard=item=>{const d=item.diagnosis,severity=String(item.severity||'').toLowerCase(),name=words(item.check_id),fix=githubLink(d?.pull_request_url);
  const ask=adminChat?`<a class="button" href="${esc(askAdministrator(`Tell me about the open ${name} incident and what I should do about it.`))}">Ask the administrator about this</a>`:'';
  const diagnosis=!d?`<div class="agent-diagnosis"><span class="ops-pill is-agent">Operations agent</span><p class="muted">The operations agent has not diagnosed this incident yet. Its findings will appear here.</p>${ask?'<div class="actions">'+ask+'</div>':''}</div>`
    :`<div class="agent-diagnosis"><span class="ops-pill is-agent">Operations agent</span> <strong>${d.status==='diagnosed'?'Diagnosis':'Inconclusive'}</strong><span class="muted">${d.confidence?' · '+esc(d.confidence)+' confidence':''}${d.diagnosed_at?' · '+esc(when(d.diagnosed_at)):''}</span><p>${esc(d.summary||'')}</p>${d.likely_cause?'<p><strong>Likely cause:</strong> '+esc(d.likely_cause)+'</p>':''}${(d.owner_steps||[]).length?'<strong>What to do</strong><ol>'+d.owner_steps.map(step=>`<li>${esc(step)}</li>`).join('')+'</ol>':''}${(d.evidence||[]).length?'<details><summary>Evidence the agent used</summary>'+d.evidence.map(e=>`<div class="muted">${esc(e.finding)} <code>${esc(e.source_id)}</code></div>`).join('')+'</details>':''}${fix||ask?`<div class="actions">${fix?`<a class="button" href="${esc(fix)}" target="_blank" rel="noopener">Review the draft fix</a>`:''}${ask}</div>`:''}${fix?'<p class="muted">The draft fix has not been run or reviewed. Nothing changes until a person merges it.</p>':''}</div>`;
  return `<article class="health-incident is-${esc(severity)}">${pill(severity)} <span class="muted">${esc(item.status==='acknowledged'?'Acknowledged':'Open')} · first seen ${esc(when(item.first_seen_at))} · latest ${esc(when(item.last_seen_at))} · ${esc(item.occurrence_count)} checks</span><h3>${esc(name)}</h3><p>${esc(item.description)}</p>${diagnosis}</article>`};
let healthPollTimer=null,healthLastChecked=null;
function refreshHealth(){
fetch('/api/system/health',{cache:'no-store'}).then(json).then(health=>{
  const run=health.latest_run||{},portfolio=health.portfolio||{},assurance=health.portfolio_analyst_assurance||{},assuranceResult=assurance.result_json||{},burnIn=health.burn_in||{},burnResults=health.burn_in_recent_results||[],release=health.internal_release||{},incidents=health.incidents||[],issues=health.issues||[];
  const critical=incidents.some(item=>item.severity==='critical'),overall=critical?'fail':(incidents.length&&health.status==='pass'?'warn':(health.status||'warn')),diagnosed=incidents.filter(item=>item.diagnosis).length,fixes=incidents.filter(item=>githubLink(item.diagnosis?.pull_request_url)).length;
  healthLastChecked=health.checked_at||new Date().toISOString();
  document.querySelector('#health-headline').textContent=headline[overall]||'Status unknown';
  document.querySelector('#health-pill').innerHTML=pill(overall,{pass:'Pass',warn:'Warning',fail:'Attention'}[overall]);
  document.querySelector('#health-overview').innerHTML=[['Open incidents',incidents.length],['Diagnosed by the operations agent',`${diagnosed} of ${incidents.length}`],['Draft fixes to review',fixes],['Accounts with findings',issues.length],['Latest portfolio date',portfolio.latest_date||'—'],['Last checked',when(healthLastChecked)]].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  document.querySelector('#health-status').innerHTML=[['Environment',words(health.environment)],['Status',health.status],['Tracked NAV subjects',portfolio.subjects??0],['Latest portfolio date',portfolio.latest_date||'—'],['Latest ingestion run',when(run.completed_at)],['Latest internal release',when(release.approved_at)]].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${item[0]==='Status'?pill(item[1]):esc(item[1]||'—')}</strong></div>`).join('');
  document.querySelector('#health-note').textContent=(health.message||'')+' Last checked: '+when(healthLastChecked)+'. Updates automatically every 60 seconds while this page is visible.';
  document.querySelector('#health-findings').innerHTML=issues.map(item=>`<p>${pill(item.severity)} <strong>${esc(item.display_name||item.subject_id)}</strong><br>${esc(item.message)}${item.metrics?.length?'<br>Metrics needing reconciliation: '+esc(item.metrics.join(', ')):''}</p>`).join('')||'<p>No account assurance findings were returned. Data dates and missing assurance still need to be checked.</p>';
  document.querySelector('#health-incidents').innerHTML=incidents.map(incidentCard).join('')||'<p>No open incidents.</p>';
  document.querySelector('#benchmark-health').innerHTML=(health.benchmarks||[]).map(item=>`<div class="status-item"><span class="label">${esc(String(item.benchmark_id||'').replaceAll('_',' '))}</span><strong>${esc(item.latest_date||'—')}</strong><span class="muted">${esc(item.observations??0)} observations · ${pill(item.quality_status)}</span></div>`).join('')||'<div class="empty">No benchmark observations are available.</div>';
  const assuranceRows=health.portfolio_analyst_assurance_all||[];
  document.querySelector('#assurance-status').textContent=`${assuranceRows.length} active portfolios checked for coverage`;
  document.querySelector('#assurance-summary').innerHTML=assuranceRows.map(item=>`<div class="status-item"><span class="label">${esc(item.display_name||item.subject_id)}</span><strong>${item.status?pill(item.status,String(item.status).toUpperCase()):'Awaiting first run'}</strong><span class="muted">Assurance: ${esc(item.assurance_date||'—')} · Data: ${esc(item.as_of_date||'—')}<br>Passed: ${esc(item.checks_passed??0)} · Warnings: ${esc(item.checks_warned??0)} · Failures: ${esc(item.checks_failed??0)}</span></div>`).join('')||'<div class="warning">No active portfolio assurance is available.</div>';
  document.querySelector('#assurance-note').textContent='Each active physical or derived portfolio is evaluated separately. Missing or stale assurance prevents an overall pass; any failed account makes the overall status fail.';
  document.querySelector('#burn-in-status').textContent=burnIn.status?burnIn.status.toUpperCase():'Not started';
  document.querySelector('#burn-in-summary').innerHTML=[['Subject',burnIn.subject_id||'—'],['Start date',burnIn.start_date||'—'],['Consecutive passing days',`${burnIn.consecutive_pass_days??0} / ${burnIn.required_consecutive_days??14}`],['Original earliest completion',burnIn.earliest_completion_date||'—'],['Last result',burnIn.last_result_date||'Awaiting Day 1'],['Last failure',burnIn.last_failure_date||'None']].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  document.querySelector('#burn-in-results').innerHTML=burnResults.length?'<h3>Recent daily acceptance results</h3>'+burnResults.map(item=>`<p>${pill(item.status,String(item.status||'').toUpperCase())} <strong>${esc(item.result_date)}</strong><br><span class="muted">Checked ${esc(when(item.checked_at))}</span></p>`).join(''):'<p>No formal acceptance result has been recorded yet.</p>';
}).catch(()=>{document.querySelector('#health-headline').textContent='Live status could not be refreshed';document.querySelector('#health-note').textContent='Live status could not be refreshed. Displayed results may be stale.'+(healthLastChecked?' Last successful check: '+when(healthLastChecked):'')+' Retrying automatically; this is not a health pass.';})
.finally(()=>{clearTimeout(healthPollTimer);healthPollTimer=setTimeout(()=>{if(!document.hidden)refreshHealth();},60000);});
}
document.addEventListener('visibilitychange',()=>{clearTimeout(healthPollTimer);if(!document.hidden)refreshHealth();});
refreshHealth();
</script>
"""


ADMINISTRATION_PAGE_SCRIPT = r"""
<script>
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json=async response=>{const payload=await response.json();if(!response.ok)throw new Error(`http_${response.status}`);return payload};
const when=value=>{if(!value)return'';const parsed=new Date(value);return isNaN(parsed)||!/T|:/.test(String(value))?String(value):parsed.toLocaleString(undefined,{day:'numeric',month:'short',year:'numeric',hour:'2-digit',minute:'2-digit'})};
const words=value=>{const text=String(value||'').replaceAll('_',' ').trim();return text?text[0].toUpperCase()+text.slice(1):''};
Promise.all([fetch('/api/system/autonomous-administrator').then(json),fetch('/api/agent/tools').then(json),fetch('/api/system/institutional-control-plane').then(json)]).then(([autonomy,tools,controlPlane])=>{
  const latest=autonomy.latest_run||{},control=autonomy.control||{},recovery=autonomy.recovery||{},baseline=autonomy.last_known_good||{};
  document.querySelector('#autonomy-status').textContent=autonomy.status==='active'?'Active — controlled-autonomy-v1':'Paused';
  document.querySelector('#autonomy-note').textContent='Platform-wide administrator monitoring runs in Azure every five minutes. Its authority remains constrained by deterministic policy, cooldowns and owner approval gates.';
  document.querySelector('#autonomy-summary').innerHTML=[['Latest run',when(latest.completed_at)||'Awaiting first run'],['Run status',words(latest.status)||'—'],['Production authority',control.production_active?'Active':'Not active'],['Open incidents',(autonomy.open_incidents||[]).length],['Portal failure count',recovery.consecutive_portal_failures??0],['Rollback threshold',control.rollback_failure_threshold??3],['Last-known-good revision',baseline.revision_name||'Not registered'],['Monthly review due',control.monthly_review_due_at||'Not scheduled']].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  document.querySelector('#autonomy-incidents').innerHTML=(autonomy.open_incidents||[]).length?'<h3>Open incidents</h3>'+autonomy.open_incidents.map(item=>`<p><span class="ops-pill is-${esc(String(item.severity||'').toLowerCase())}">${esc(words(item.severity))}</span> <strong>${esc(words(item.check_id))}</strong><br>${esc(item.description)}<br><span class="muted">Last observed ${esc(when(item.last_seen_at))} · ${esc(item.occurrence_count)} checks</span></p>`).join('')+'<p><a href="/system-health">See the operations agent\'s diagnoses and draft fixes on System health</a></p>':'<p>No open incidents.</p>';
  const registry=controlPlane.identity_registry||{},registrations=controlPlane.registrations||{},workItems=controlPlane.work_items||{},shadow=controlPlane.shadow_reconciliation||{};
  document.querySelector('#control-plane-status').textContent=controlPlane.authorization_cutover_ready?'Reconciled — cutover eligible':'Compatibility mode — reconciliation required';
  document.querySelector('#control-plane-note').textContent=controlPlane.message+' The proven 16:00 HKT production refresh remains authoritative. Reconciliation readiness does not itself activate or pause Azure schedules.';
  document.querySelector('#control-plane-summary').innerHTML=[['Authorization mode',controlPlane.authorization_mode],['Registered users',registry.users??0],['Active roles',registry.roles??0],['Subject entitlements',registry.entitlements??0],['Active capabilities',registrations.active??0],['Preview capabilities',registrations.preview??0],['Shadow dispatcher',shadow.status?shadow.status.toUpperCase():'Not run'],['Shadow run date',shadow.run_date||'—'],['Planned capabilities',shadow.planned_capability_count??0],['Shadow mismatches',shadow.mismatch_count??0],['Queued work',workItems.queued??0],['Dead letters',controlPlane.dead_letters??0],['Last identity revision',registry.last_applied_at||'Not applied']].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  const reviews=autonomy.authority_reviews||[],pendingReview=reviews.find(item=>item.status!=='approved');
  document.querySelector('#autonomy-reviews').innerHTML='<h3>Monthly authority reviews</h3>'+(reviews.length?reviews.map(item=>`<p><strong>${esc(item.review_month)} — ${esc(item.status)}</strong><br>Due ${esc(when(item.due_at))}${item.status==='approved'?' · Approved '+esc(when(item.approved_at)):''}</p>`).join(''):'<p>The first monthly review will be created on the first day of the next month.</p>')+(pendingReview?`<div class="actions"><input id="authority-review-confirmation" maxlength="80" aria-label="Monthly authority review confirmation" placeholder="APPROVE MONTHLY AUTHORITY REVIEW ${esc(pendingReview.review_month)}"><button id="authority-review-approve" class="button" type="button">Approve ${esc(pendingReview.review_month)} review</button></div><div id="authority-review-result" class="muted"></div>`:'');
  if(pendingReview){document.querySelector('#authority-review-approve').addEventListener('click',async()=>{const button=document.querySelector('#authority-review-approve'),result=document.querySelector('#authority-review-result');button.disabled=true;try{const approved=await fetch(`/api/system/autonomous-administrator/reviews/${pendingReview.review_id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation:document.querySelector('#authority-review-confirmation').value})}).then(json);result.textContent=`Approved ${approved.review_month}. Next review due ${approved.next_review_due_at}.`}catch(error){result.textContent='Review was not approved. Type the exact confirmation phrase.';button.disabled=false}})}
  const admin=tools.admin_actions,adminAction=document.querySelector('#admin-action'),adminPreview=document.querySelector('#admin-preview'),adminExecute=document.querySelector('#admin-execute'),adminConfirmation=document.querySelector('#admin-confirmation');let currentApproval=null;
  document.querySelector('#admin-status').textContent=admin.enabled?'Approval workflow ready':'Disabled';
  document.querySelector('#admin-note').textContent=admin.message+' Every preview expires after '+admin.approval_ttl_minutes+' minutes and can be executed only once.';
  adminAction.innerHTML=admin.actions.map(item=>`<option value="${esc(item.action_type)}">${esc(item.label)}</option>`).join('');adminPreview.disabled=!admin.enabled;
  adminPreview.addEventListener('click',async()=>{adminPreview.disabled=true;adminExecute.disabled=true;adminConfirmation.disabled=true;document.querySelector('#admin-result').textContent='';const result=document.querySelector('#admin-preview-result');result.textContent='Creating an integrity-bound preview…';try{currentApproval=await fetch('/api/admin/actions/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action_type:adminAction.value})}).then(json);const preview=currentApproval.preview;result.innerHTML=`<strong>${esc(preview.label)}</strong><p>${esc(preview.description)}</p><p>Target: <code>${esc(preview.target)}</code> · Environment: <strong>${esc(preview.environment)}</strong><br>Expires: ${esc(currentApproval.expires_at)}<br>Payload digest: <code>${esc(currentApproval.action_payload_sha256)}</code></p><p><strong>Will not allow:</strong> ${preview.does_not_allow.map(esc).join(', ')}</p><p>Type exactly: <code>${esc(currentApproval.confirmation_phrase)}</code></p>`;adminConfirmation.value='';adminConfirmation.placeholder=currentApproval.confirmation_phrase;adminConfirmation.disabled=false;adminExecute.disabled=false}catch(error){currentApproval=null;result.textContent='Unable to create an approval preview.'}finally{adminPreview.disabled=!admin.enabled}});
  adminExecute.addEventListener('click',async()=>{if(!currentApproval)return;adminExecute.disabled=true;adminPreview.disabled=true;const result=document.querySelector('#admin-result');result.textContent='Executing the single approved action…';try{const response=await fetch(`/api/admin/actions/${currentApproval.approval_id}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action_payload_sha256:currentApproval.action_payload_sha256,confirmation:adminConfirmation.value})}).then(json);result.innerHTML=`<strong>Executed once.</strong> Action: ${esc(response.action_type)} · Approval ID: ${esc(response.approval_id)} · Correlation ID: ${esc(response.correlation_id)}`;currentApproval=null;adminConfirmation.value='';adminConfirmation.disabled=true}catch(error){result.textContent='Action was not executed. Check the exact approval phrase or create a new preview.';adminExecute.disabled=false}finally{adminPreview.disabled=!admin.enabled}});
}).catch(()=>{document.querySelector('#autonomy-status').textContent='Administration data unavailable';document.querySelector('#admin-status').textContent='Fail-closed'});
</script>
"""


ADMIN_LIVE_CHAT_SCRIPT = r"""
<script>
(()=>{
const escAdmin=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const jsonAdmin=async response=>{const payload=await response.json();if(!response.ok)throw new Error(payload.detail?.message||payload.detail||`http_${response.status}`);return payload};
const list=document.querySelector('#admin-conversation-list'),thread=document.querySelector('#admin-chat-thread'),title=document.querySelector('#admin-chat-title'),input=document.querySelector('#admin-chat-input'),send=document.querySelector('#admin-chat-send'),archive=document.querySelector('#admin-chat-archive'),exportMd=document.querySelector('#admin-chat-export-md'),exportJson=document.querySelector('#admin-chat-export-json');
let active=null,busy=false,activeSourceKey=null;
const empty=()=>{thread.innerHTML='<div class="ai-chat-empty"><div class="label">Controlled system administration</div><div class="value">Ask the administrator</div><div class="muted">Ask about system health or give an order. Supported changes become an expiring preview and still require your exact approval before execution.</div><div class="ai-prompt-chips"><button class="admin-prompt-chip ai-prompt-chip" type="button">Give me a system health summary</button><button class="admin-prompt-chip ai-prompt-chip" type="button">Show all open incidents</button><button class="admin-prompt-chip ai-prompt-chip" type="button">Refresh the Treasury risk-free rate</button></div></div>';bindPrompts()};
const updateHeader=conversation=>{title.textContent=conversation?.title||'New administrator conversation';activeSourceKey=conversation?.source_key||null;const enabled=Boolean(conversation?.conversation_id);exportMd.hidden=!enabled;exportJson.hidden=!enabled;archive.hidden=!enabled||Boolean(activeSourceKey);if(enabled){exportMd.href=`/api/admin/conversations/${conversation.conversation_id}/export.md`;exportJson.href=`/api/admin/conversations/${conversation.conversation_id}/export.json`}};
const actionCard=(preview)=>{if(!preview)return'';const p=preview.preview||{};return `<div class="admin-action-preview" data-approval-id="${escAdmin(preview.approval_id)}" data-digest="${escAdmin(preview.action_payload_sha256)}"><div class="label">Owner approval required</div><strong>${escAdmin(p.label||preview.action_type)}</strong><p>${escAdmin(p.description||'')}</p><p>Target: <code>${escAdmin(p.target||'')}</code><br>Expires: ${escAdmin(preview.expires_at)}<br>Type exactly: <code>${escAdmin(preview.confirmation_phrase)}</code></p><div class="actions"><input class="admin-chat-confirmation" type="text" maxlength="80" placeholder="${escAdmin(preview.confirmation_phrase)}" aria-label="Exact action approval phrase"><button class="button admin-chat-approve" type="button">Approve and execute once</button></div><div class="muted admin-chat-action-result"></div></div>`};
const whenAdmin=value=>{if(!value)return'Saved';const parsed=new Date(value);return isNaN(parsed)||!/T|:/.test(String(value))?String(value):parsed.toLocaleString(undefined,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})};
const authorOf=(message,response)=>response.investigation_id?'Operations agent':response.connection_report_id?'Connection agent':message.message_kind==='incident'?'Administrator initiated':'AI System Administrator';
const bodyOf=(message,response)=>{const steps=(response.owner_steps||[]).filter(Boolean),fix=response.pull_request?.url&&/^https:\/\/github\.com\//.test(response.pull_request.url)?response.pull_request.url:'';const paragraphs=String(response.message||message.content_text||'').split(/\n\s*\n/).filter(text=>text.trim()&&!(steps.length&&/^What to do:/.test(text))&&!(fix&&/^I opened a draft pull request/.test(text)));return paragraphs.map(text=>`<p>${escAdmin(text)}</p>`).join('')+(steps.length?'<strong>What to do</strong><ol>'+steps.map(step=>`<li>${escAdmin(step)}</li>`).join('')+'</ol>':'')+(fix?`<div class="actions"><a class="button" href="${escAdmin(fix)}" target="_blank" rel="noopener">Review the draft fix</a></div>`:'')};
const append=(message)=>{const row=document.createElement('div'),owner=message.role==='owner',response=message.response_json||{};row.className=`ai-message ${owner?'user':'assistant'}`;if(owner){row.innerHTML=`<div class="ai-message-bubble"><div>${escAdmin(message.content_text)}</div><div class="ai-message-meta">${escAdmin(whenAdmin(message.created_at))}</div></div>`}else{const points=(response.status_points||[]).map(point=>`<p>• ${escAdmin(point.text)} <span class="admin-message-source">[${escAdmin(point.source_id)}]</span></p>`).join(''),limits=(response.limitations||[]).map(value=>`<p><strong>Control:</strong> ${escAdmin(value)}</p>`).join(''),result=response.action_result?`<div class="admin-action-preview"><strong>Controlled action completed</strong><p>${escAdmin(message.content_text)}</p><p class="muted">Approval ${escAdmin(response.action_result.approval_id)} · ${escAdmin(response.action_result.correlation_id)}</p></div>`:'';const author=authorOf(message,response);row.innerHTML=`<div class="ai-message-bubble ai-answer"><div class="label">${author.endsWith(' agent')?`<span class="ops-pill is-agent">${escAdmin(author)}</span>`:escAdmin(author)}${response.confidence?' · '+escAdmin(response.confidence)+' confidence':''}</div><h3>${escAdmin(response.title||'System administrator')}</h3>${bodyOf(message,response)}${points}${limits}${actionCard(response.action_preview)}${result}<div class="ai-message-meta">${escAdmin(whenAdmin(message.created_at))} ${response.correlation_id?'· '+escAdmin(response.correlation_id):''}</div></div>`}thread.appendChild(row);row.querySelectorAll('.admin-chat-approve').forEach(button=>button.addEventListener('click',()=>approveAction(button)));thread.scrollTop=thread.scrollHeight};
const approveAction=async button=>{const card=button.closest('.admin-action-preview'),confirmation=card.querySelector('.admin-chat-confirmation'),result=card.querySelector('.admin-chat-action-result');button.disabled=true;result.textContent='Executing the single approved action…';try{await fetch(`/api/admin/conversations/${active}/actions/${card.dataset.approvalId}/approve`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action_payload_sha256:card.dataset.digest,confirmation:confirmation.value})}).then(jsonAdmin);await openConversation(active)}catch(error){result.textContent=`Action was not executed: ${error.message}`;button.disabled=false}};
const renderList=(items,selected)=>{list.innerHTML=items.length?items.map(item=>`<button class="ai-conversation-item${item.conversation_id===selected?' active':''}" type="button" data-admin-conversation="${escAdmin(item.conversation_id)}"><strong>${escAdmin(item.title)}${Number(item.unread_count)>0?`<span class="admin-chat-badge">${escAdmin(item.unread_count)}</span>`:''}</strong><span>${escAdmin(item.initiated_by==='administrator'?'Administrator initiated':'Owner chat')} · ${escAdmin(item.message_count)} messages</span></button>`).join(''):'<div class="muted">No administrator conversations yet.</div>';list.querySelectorAll('[data-admin-conversation]').forEach(button=>button.addEventListener('click',()=>openConversation(button.dataset.adminConversation)))};
const refresh=async selected=>{const result=await fetch('/api/admin/conversations').then(jsonAdmin);renderList(result.conversations,selected);return result.conversations};
const openConversation=async id=>{const result=await fetch(`/api/admin/conversations/${id}`).then(jsonAdmin);active=result.conversation.conversation_id;updateHeader(result.conversation);thread.innerHTML='';if(!result.messages.length)empty();else result.messages.forEach(append);await refresh(active)};
const createConversation=async()=>{const result=await fetch('/api/admin/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).then(jsonAdmin);active=result.conversation_id;updateHeader(result);empty();await refresh(active);input.focus()};
const showTyping=()=>{const row=document.createElement('div');row.className='ai-message assistant';row.id='admin-typing';row.innerHTML='<div class="ai-typing" aria-label="AI System Administrator is responding"><i></i><i></i><i></i></div>';thread.appendChild(row);thread.scrollTop=thread.scrollHeight};
const sendMessage=async()=>{const message=input.value.trim();if(!message||busy)return;busy=true;send.disabled=true;try{if(!active)await createConversation();if(thread.querySelector('.ai-chat-empty'))thread.innerHTML='';append({role:'owner',content_text:message,created_at:'Sending…'});input.value='';showTyping();await fetch(`/api/admin/conversations/${active}/messages`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})}).then(jsonAdmin);document.querySelector('#admin-typing')?.remove();await openConversation(active)}catch(error){document.querySelector('#admin-typing')?.remove();append({role:'administrator',message_kind:'chat',content_text:`The request was not applied. ${error.message}`,response_json:{title:'Request stopped safely',message:`The request was not applied. ${error.message}`,status_points:[],limitations:['No system change was executed.']}})}finally{busy=false;send.disabled=false;input.focus()}};
const bindPrompts=()=>document.querySelectorAll('.admin-prompt-chip').forEach(button=>button.addEventListener('click',()=>{input.value=button.textContent;input.focus()}));
document.querySelector('#admin-new-chat').addEventListener('click',()=>createConversation().catch(()=>{}));send.addEventListener('click',sendMessage);input.addEventListener('keydown',event=>{if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage()}});archive.addEventListener('click',async()=>{if(!active||activeSourceKey)return;await fetch(`/api/admin/conversations/${active}/archive`,{method:'POST'}).then(jsonAdmin);active=null;updateHeader(null);empty();const items=await refresh(null);if(items.length)await openConversation(items[0].conversation_id)});
const asked=new URLSearchParams(location.search).get('ask');
refresh(null).then(async items=>{if(asked){active=null;updateHeader(null);empty();input.value=asked.slice(0,1200);input.focus();return}if(items.length)await openConversation(items[0].conversation_id);else empty()}).catch(()=>{thread.innerHTML='<div class="warning">The administrator chat is unavailable and remains fail-closed.</div>'});
setInterval(async()=>{if(document.hidden)return;const items=await refresh(active).catch(()=>[]);const selected=items.find(item=>item.conversation_id===active);if(selected&&Number(selected.unread_count)>0)await openConversation(active)},30000);
})();
</script>
"""


ACCOUNT_ONBOARDING_PAGE_SCRIPT = r"""
<script>
(()=>{
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json=async response=>{const payload=await response.json();if(!response.ok){const detail=typeof payload.detail==='string'?payload.detail:JSON.stringify(payload.detail||{});throw new Error(detail||`http_${response.status}`)}return payload};
const counts=value=>{if(!value)return{};if(typeof value==='object')return value;try{return JSON.parse(value)}catch{return{}}};
const label=value=>String(value||'').replaceAll('_',' ').replace(/\b\w/g,character=>character.toUpperCase());
const uploadForm=document.querySelector('#onboarding-template-upload');
const uploadSubject=document.querySelector('#onboarding-upload-subject');
const uploadStatus=document.querySelector('#onboarding-upload-status');
const wizardStageIndex=stage=>({discovered:0,data_import:1,financial_contract:2,analytics_configuration:3,template_intake:4,reconciliation:5,historical_reporting:6,website_preview:7,activation_ready:8,active:9}[stage]??0);
const referenceDefinitionCard=item=>{const d=item.definition_summary;if(!d)return'';if(item.subject_type==='filtered_portfolio'){const cash=Object.entries(d.opening_cash||{}).map(([ccy,value])=>`${ccy} ${Number(value).toLocaleString('en-US')}`).join(' · ');return `<div class="onboarding-definition"><div class="label">Locked calculation preview</div><p><strong>Source:</strong> ${esc(d.source)} · <strong>Effective:</strong> ${esc(d.effective_from)}</p><p>${esc(d.position_policy)}</p><p><strong>Opening cash:</strong> ${esc(cash)} · <strong>Allowed cash:</strong> ${esc((d.allowed_cash_currencies||[]).join(', '))}</p><p>${esc(d.cash_policy)}</p><p class="muted">Definition SHA-256 ${esc(item.definition_sha256)}</p></div>`}const exposure=d.initial_exposure||{};return `<div class="onboarding-definition"><div class="label">Locked calculation preview</div><p><strong>Formula:</strong> ${esc(d.formula)}</p><p><strong>Normalization:</strong> ${esc(d.effective_from)} · ${esc(d.base_currency)} · base ${esc(d.normalization_base)} · fixed units/no daily rebalancing</p><p><strong>Inception exposure:</strong> long ${esc(exposure.long_risky)} · short ${esc(exposure.short_risky)} · gross ${esc(exposure.gross_risky)} · net ${esc(exposure.net_risky)} · cash ${esc(exposure.cash)}</p><p class="muted">Definition SHA-256 ${esc(item.definition_sha256)}</p></div>`};
const externalApprovalCard=(item,wizard)=>{const approval=(item.approvals||[]).find(value=>value.approval_type==='external_market_data'&&value.approval_status==='approved'),disclosure=wizard.external_data_disclosure,canApprove=wizard.permissions.may_approve_external_data;if(approval)return `<div class="onboarding-check pass"><strong>External market-data consent</strong><span>Approved ${esc(approval.approved_at)} · ${esc(approval.destination)} · scope ${esc(approval.scope_sha256.slice(0,12))}…</span></div>`;return `<div class="onboarding-check pending"><strong>External market-data consent</strong><span>${esc(disclosure.message)}</span>${canApprove?`<label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input class="external-data-consent" type="checkbox"> <span>I approve this exact bounded transmission for <strong>${esc(item.target_subject_id)}</strong>.</span></label><div class="actions"><button class="button external-data-approve" type="button" data-subject="${esc(item.target_subject_id)}" data-version="${esc(disclosure.disclosure_version)}" data-scope="${esc(disclosure.scope_sha256)}" disabled>Approve external data use</button></div><div class="muted external-data-status" role="status"></div>`:'<span class="muted">Owner approval required. An onboarding operator cannot approve external transmission.</span>'}</div>`};
const presentationIdentityCard=(item,wizard)=>{const approval=(item.approvals||[]).find(value=>value.approval_type==='presentation_identity'&&value.approval_status==='approved'),candidate=item.presentation_identity_candidate,canApprove=wizard.permissions.may_approve_presentation_identity;if(approval){const scope=approval.scope_json||{},layoutDescription=scope.layout_policy==='subject_template'?'Platform standard output enforced · legacy identity approval':'Platform standard presentation layout';return `<div class="onboarding-check pass"><strong>Owner-controlled presentation identity</strong><span>${esc(scope.strategy_name||'Approved title')} · ${esc(layoutDescription)} · scope ${esc(approval.scope_sha256.slice(0,12))}…</span><span class="muted">Locked independently of document upload. Operators cannot edit the identity or replace the approved platform chart and table system.</span></div>`}if(!candidate)return `<div class="onboarding-check fail"><strong>Owner-controlled presentation identity</strong><span>The approved account name cannot produce a valid strategy title. Correct the financial contract first.</span></div>`;return `<div class="onboarding-check pending"><strong>Owner-controlled presentation identity</strong><span>Derived from the approved account name: <strong>${esc(candidate.strategy_name)}</strong></span><span class="muted">All generated presentations use the approved U16088571 platform layout. The uploaded account deck supplies identity, branding and disclosure evidence only.</span>${canApprove?`<label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input class="presentation-identity-consent" type="checkbox"> <span>I approve this server-derived title and the mandatory platform-standard presentation layout. The document uploader cannot change either value.</span></label><div class="actions"><button class="button presentation-identity-approve" type="button" data-subject="${esc(item.target_subject_id)}" data-display-digest="${esc(candidate.display_name_sha256)}" disabled>Lock presentation identity</button></div><div class="muted presentation-identity-status" role="status"></div>`:'<span class="muted">Owner approval required. An onboarding operator cannot set or approve a strategy title.</span>'}</div>`};
const csvList=value=>String(value||'').split(',').map(item=>item.trim()).filter(Boolean);
const parseOpeningCash=value=>{const result={};csvList(value).forEach(item=>{const [currency,amount]=item.split('=').map(part=>part.trim());const number=Number(amount);if(!/^[A-Z]{3}$/.test(currency||'')||!Number.isFinite(number))throw new Error(`Invalid opening-cash entry: ${item}`);result[currency]=number});return result};
const setupDerivedStudio=data=>{const studio=data.derived_studio||{},templates=studio.templates||[],subjects=studio.available_subjects||[],markets=studio.available_market_components||[],template=document.querySelector('#studio-template'),filtered=document.querySelector('#studio-filtered-form'),composite=document.querySelector('#studio-composite-form'),rows=document.querySelector('#studio-component-rows'),exposure=document.querySelector('#studio-exposure-preview');document.querySelector('#studio-template-summary').innerHTML=templates.map(item=>`<div class="status-item"><span class="label">${esc(item.modelled_on)}</span><strong>${esc(item.title)}</strong><span class="muted">${(item.supports||[]).map(label).map(esc).join(' · ')}</span></div>`).join('');const physical=subjects.filter(item=>item.subject_type==='physical_account');filtered.querySelector('[name="source_account_id"]').innerHTML=physical.map(item=>`<option value="${esc(item.subject_id)}">${esc(item.display_name)} · ${esc(item.currency)}</option>`).join('');const componentCatalog=[...subjects.map(item=>({component_id:item.subject_id,component_name:item.display_name,component_type:item.subject_type==='physical_account'?'account':'portfolio',source_currency:item.currency,available:true})),...markets.map(item=>({...item,available:Number(item.observations)>0}))];const options=()=>componentCatalog.map(item=>`<option value="${esc(item.component_id)}" ${item.available?'':'disabled'}>${esc(item.component_name)} · ${esc(label(item.component_type))} · ${esc(item.source_currency)}${item.available?'':' · data unavailable'}</option>`).join('');const updateExposure=()=>{const coefficients=[...rows.querySelectorAll('[name="coefficient"]')].map(input=>Number(input.value)||0),cash=Number(composite.querySelector('[name="constant_cash"]').value)||0,long=100*coefficients.filter(value=>value>0).reduce((a,b)=>a+b,0),short=-100*coefficients.filter(value=>value<0).reduce((a,b)=>a+b,0),nav=cash+100*coefficients.reduce((a,b)=>a+b,0);exposure.textContent=`Inception preview · NAV ${nav.toFixed(2)} · long ${long.toFixed(2)} · short ${short.toFixed(2)} · gross ${(long+short).toFixed(2)} · net ${(long-short).toFixed(2)} · cash ${cash.toFixed(2)}`};const addRow=()=>{const row=document.createElement('div');row.className='status-item';row.innerHTML=`<label><span class="label">Component</span><select name="component_id" required>${options()}</select></label><label><span class="label">Coefficient</span><input name="coefficient" type="number" step="any" required value="1"></label><button class="button remove-studio-component" type="button">Remove</button>`;row.querySelector('.remove-studio-component').addEventListener('click',()=>{row.remove();updateExposure()});row.querySelector('[name="coefficient"]').addEventListener('input',updateExposure);rows.appendChild(row);updateExposure()};template.addEventListener('change',()=>{const showFiltered=template.value==='filtered_portfolio';filtered.hidden=!showFiltered;composite.hidden=showFiltered});document.querySelector('#studio-add-component').addEventListener('click',addRow);composite.querySelector('[name="constant_cash"]').addEventListener('input',updateExposure);addRow();addRow();filtered.addEventListener('submit',async event=>{event.preventDefault();const button=filtered.querySelector('button[type="submit"]'),note=filtered.querySelector('.studio-result'),form=new FormData(filtered);button.disabled=true;note.textContent='Validating the filter, cash ledger and source history…';try{const result=await fetch('/api/onboarding/derived-studio/filtered/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject_id:form.get('subject_id'),display_name:form.get('display_name'),source_account_id:form.get('source_account_id'),base_currency:String(form.get('base_currency')).toUpperCase(),history_start_date:form.get('history_start_date'),excluded_countries:csvList(form.get('excluded_countries')),excluded_exchanges:csvList(form.get('excluded_exchanges')).map(value=>value.toUpperCase()),excluded_currencies:csvList(form.get('excluded_currencies')).map(value=>value.toUpperCase()),opening_cash:parseOpeningCash(form.get('opening_cash')),allowed_cash_currencies:csvList(form.get('allowed_cash_currencies')).map(value=>value.toUpperCase()),include_actual_interest:Boolean(form.get('include_actual_interest'))})}).then(json);window.location.href=result.next_page}catch(error){note.textContent=`Portfolio was not created: ${error.message}`;button.disabled=false}});composite.addEventListener('submit',async event=>{event.preventDefault();const button=composite.querySelector('button[type="submit"]'),note=composite.querySelector('.studio-result'),form=new FormData(composite),componentRows=[...rows.querySelectorAll('.status-item')],components=componentRows.map(row=>({component_id:row.querySelector('[name="component_id"]').value,coefficient:Number(row.querySelector('[name="coefficient"]').value)}));button.disabled=true;note.textContent='Validating components, currencies, market history and inception exposure…';try{const result=await fetch('/api/onboarding/derived-studio/composite/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({subject_id:form.get('subject_id'),display_name:form.get('display_name'),base_currency:String(form.get('base_currency')).toUpperCase(),normalization_date:form.get('normalization_date'),constant_cash:Number(form.get('constant_cash')),market_carry_days:Number(form.get('market_carry_days')),components})}).then(json);window.location.href=result.next_page}catch(error){note.textContent=`Portfolio was not created: ${error.message}`;button.disabled=false}});updateExposure()};
fetch('/api/onboarding/summary').then(json).then(data=>{
  document.querySelector('#onboarding-status').textContent=data.cases.length?'Wave 0 in progress':'Foundation ready';
  document.querySelector('#onboarding-summary').innerHTML=[['Reference account',data.active_reference_subject],['Wave 0 account',data.wave0_subject],['Discovered v1.1 accounts',data.discovered_accounts.length],['Active onboarding cases',data.cases.filter(item=>!['active','cancelled'].includes(item.status)).length]].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  document.querySelector('#onboarding-wizard-steps').innerHTML=data.wizard.steps.map((step,index)=>`<div class="status-item"><span class="label">Step ${index+1}</span><strong>${esc(step)}</strong></div>`).join('');
  document.querySelector('#onboarding-subject-types').innerHTML=data.wizard.subject_types.map(item=>`<article class="status-item"><span class="label">${esc(label(item.status))}</span><strong>${esc(item.title)}</strong><span class="muted">${esc(item.description)}</span><span class="muted">Required: ${item.requires.map(label).map(esc).join(' · ')}</span></article>`).join('');
  setupDerivedStudio(data);
  const sources=data.flex_sources||[],sourceAccounts=data.flex_source_accounts||[];
  const readiness=sourceAccounts.length?'Ready for account selection':sources.length?'Validation required':'Connection required';
  document.querySelector('#connection-prerequisite-summary').innerHTML=[['Connection readiness',readiness],['Registered connections',sources.length],['Discovered Flex accounts',sourceAccounts.length]].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
  document.querySelector('#connection-prerequisite-note').textContent=sourceAccounts.length?'Validated IBKR accounts are available for onboarding. Connection credentials and validation remain managed on the dedicated IBKR connections page.':sources.length?'A connection is registered but has not discovered any accounts yet. Validate it on the dedicated IBKR connections page before starting a new onboarding case.':'Register and validate an IBKR Flex connection before onboarding a new live account. Existing v1.1 account candidates remain available below.';
  document.querySelector('#onboarding-sequence').innerHTML=data.planned_sequence.map((item,index)=>`<div class="status-item"><span class="label">Stage ${index+1}</span><strong>${esc(item)}</strong></div>`).join('');
  document.querySelector('#reference-programme').innerHTML=data.reference_programme.map(item=>`<article class="onboarding-case"><div class="onboarding-case-head"><div><div class="label">Stage ${esc(item.position)} · ${esc(label(item.subject_type))}</div><h3>${esc(item.display_name)}</h3></div><span class="onboarding-pill ${esc(item.state)}">${esc(label(item.state))}</span></div><p class="muted">${esc(item.reason)}</p>${referenceDefinitionCard(item)}${item.state==='ready'&&item.source_system==='master_definition'?`<form class="start-master-subject"><input type="hidden" name="subject_id" value="${esc(item.subject_id)}"><div class="status-row"><label class="status-item"><span class="label">Display name</span><input name="display_name" required value="${esc(item.display_name)}"></label><label class="status-item"><span class="label">Reporting mode</span><select name="reporting_mode"><option value="administrative_only">Administrative directory only</option></select></label><label class="status-item"><span class="label">Reporting currency</span><input name="reporting_currency" required pattern="[A-Z]{3}" value="USD"></label><label class="status-item"><span class="label">History start</span><input name="history_start_date" type="date" required value="2025-01-20"></label></div><p class="muted">This reference master is intentionally administrative-only. Investment totals remain at child-account level, preventing double counting.</p><div class="actions"><button class="button" type="submit">Start master-account test</button></div></form>`:`<div class="actions">${item.state==='ready'&&item.source_system==='derived_definition'?`<button class="button start-reference-subject" type="button" data-subject="${esc(item.subject_id)}">Start private onboarding</button>`:item.state==='in_progress'?`<a class="button" href="/account-onboarding/${encodeURIComponent(item.subject_id)}/steps/connection">Continue onboarding</a>`:''}</div>`}<div class="muted reference-start-status" role="status"></div></article>`).join('');
  document.querySelectorAll('.start-reference-subject').forEach(button=>button.addEventListener('click',async()=>{const note=button.closest('.onboarding-case').querySelector('.reference-start-status');button.disabled=true;note.textContent='Creating a private, definition-bound onboarding case…';try{const result=await fetch(`/api/onboarding/reference-programme/${encodeURIComponent(button.dataset.subject)}/start`,{method:'POST'}).then(json);window.location.href=result.next_page}catch(error){note.textContent=`Subject was not started: ${error.message}`;button.disabled=false}}));
  document.querySelectorAll('.start-master-subject').forEach(form=>form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button'),note=form.closest('.onboarding-case').querySelector('.reference-start-status'),values=new FormData(form);button.disabled=true;note.textContent='Creating the governed master-account contract…';try{const result=await fetch('/api/onboarding/master-programme/U15606638/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:values.get('display_name'),reporting_mode:values.get('reporting_mode'),reporting_currency:String(values.get('reporting_currency')).toUpperCase(),history_start_date:values.get('history_start_date'),member_account_ids:String(values.get('member_account_ids')||'').split(',').map(value=>value.trim().toUpperCase()).filter(Boolean)})}).then(json);window.location.href=result.next_page}catch(error){note.textContent=`Master account was not started: ${error.message}`;button.disabled=false}}));
  document.querySelector('#onboarding-cases').innerHTML=data.cases.length?data.cases.map(item=>{const passed=item.checks.filter(check=>check.status==='pass').length,total=item.checks.length,templates=item.templates||[],current=wizardStageIndex(item.stage),sourceLabel=({existing_v11:'Existing v1.1 account',ibkr_flex:'IBKR Flex account',derived_definition:'Governed derived portfolio',master_definition:'Governed master account'})[item.source_system]||label(item.source_system);return `<article class="onboarding-case"><div class="onboarding-case-head"><div><div class="label">${esc(sourceLabel)}</div><h3>${esc(item.display_name||item.target_subject_id)}</h3></div><span class="onboarding-pill ${esc(item.status)}">${esc(label(item.stage))}</span></div><p class="muted">Subject ID: ${esc(item.target_subject_id)} · Lifecycle: ${esc(label(item.lifecycle_status))} · Reporting currency: ${esc(item.reporting_currency||'Awaiting confirmation')} · ${passed}/${total} gates passed</p><div class="actions"><a class="button" href="/account-onboarding/${encodeURIComponent(item.target_subject_id)}/steps/connection">Continue page-by-page onboarding</a></div><div class="status-row">${data.wizard.steps.map((step,index)=>`<div class="status-item"><span class="label">Step ${index+1}</span><strong>${index<current?'Complete':index===current?'Current':'Locked'}</strong><span class="muted">${esc(step)}</span></div>`).join('')}</div><h4>Quality and approval gates</h4><div class="onboarding-checks">${item.checks.map(check=>`<div class="onboarding-check ${esc(check.status)}"><strong>${esc(label(check.check_id))}</strong><span>${esc(check.message)}</span></div>`).join('')}${externalApprovalCard(item,data.wizard)}${presentationIdentityCard(item,data.wizard)}</div><h4>Feature controls</h4><div class="status-row">${item.features.map(feature=>`<div class="status-item"><span class="label">${esc(label(feature.feature_key))}</span><strong>${esc(label(feature.feature_state))}</strong></div>`).join('')}</div>${templates.length?`<h4>Validated report assets</h4><div class="status-row">${templates.map(asset=>`<div class="status-item"><span class="label">${esc(label(asset.report_type))} · ${esc(label(asset.asset_role))}</span><strong>${esc(asset.version_label)}</strong><span class="muted">${esc(asset.approval_status)} · SHA-256 ${esc(asset.sha256.slice(0,12))}…</span></div>`).join('')}</div><div class="actions"><a class="button" href="/account-onboarding/${encodeURIComponent(item.target_subject_id)}/preview">Open private reconciliation preview</a></div>`:''}</article>`}).join(''):'<p class="muted">No subject has entered onboarding yet.</p>';
  document.querySelectorAll('.external-data-consent').forEach(box=>{const button=box.closest('.onboarding-check').querySelector('.external-data-approve');box.addEventListener('change',()=>{button.disabled=!box.checked})});
  document.querySelectorAll('.external-data-approve').forEach(button=>button.addEventListener('click',async()=>{const card=button.closest('.onboarding-check'),status=card.querySelector('.external-data-status');button.disabled=true;status.textContent='Recording the integrity-bound owner approval…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(button.dataset.subject)}/approvals/external-market-data`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({disclosure_version:button.dataset.version,scope_sha256:button.dataset.scope,approved:true})}).then(json);status.textContent=`Approval ${result.approval_id} recorded. No ticker was transmitted by this button.`;setTimeout(()=>window.location.reload(),900)}catch(error){status.textContent=`Approval not recorded: ${error.message}`;button.disabled=false}}));
  document.querySelectorAll('.presentation-identity-consent').forEach(box=>{const button=box.closest('.onboarding-check').querySelector('.presentation-identity-approve');box.addEventListener('change',()=>{button.disabled=!box.checked})});
  document.querySelectorAll('.presentation-identity-approve').forEach(button=>button.addEventListener('click',async()=>{const card=button.closest('.onboarding-check'),status=card.querySelector('.presentation-identity-status');button.disabled=true;status.textContent='Locking the server-derived title and platform-standard layout under owner approval…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(button.dataset.subject)}/approvals/presentation-identity`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name_sha256:button.dataset.displayDigest,layout_policy:'platform_standard',approved:true})}).then(json);status.textContent=`${result.scope.strategy_name} is locked to the approved platform presentation layout.`;setTimeout(()=>window.location.reload(),900)}catch(error){status.textContent=`Identity not approved: ${error.message}`;button.disabled=false}}));
  const eligible=data.cases.filter(item=>['template_intake','reconciliation'].includes(item.stage)&&(item.approvals||[]).some(value=>value.approval_type==='presentation_identity'&&value.approval_status==='approved'));
  uploadSubject.innerHTML=eligible.map(item=>`<option value="${esc(item.target_subject_id)}">${esc(item.display_name||item.target_subject_id)}</option>`).join('');
  uploadForm.querySelector('button').disabled=!eligible.length;
  if(!eligible.length)uploadStatus.textContent='No account is currently eligible for template intake.';
  const masterComplete=(data.reference_programme||[]).some(item=>item.subject_id==='U15606638'&&item.state==='complete'),registered=new Set(data.cases.map(item=>item.target_subject_id));
  document.querySelector('#onboarding-discoveries').innerHTML=`<div class="table-wrap"><table><thead><tr><th>Account</th><th>Type</th><th>Currency</th><th>NAV coverage</th><th>NAV days</th><th>Positions</th><th>Trades</th><th>Cash</th><th>Eligibility</th><th>Action</th></tr></thead><tbody>${data.discovered_accounts.map(item=>{const c=counts(item.row_counts_json),canStart=masterComplete&&item.eligibility_status==='eligible'&&!registered.has(item.account_id);return `<tr><td><strong>${esc(item.account_id)}</strong></td><td>${esc(item.account_type||'—')}</td><td>${esc(item.base_currency||'—')}</td><td>${esc(item.nav_start_date||'—')} – ${esc(item.nav_end_date||'—')}</td><td>${esc(c.nav_days??0)}</td><td>${esc(c.position_rows??0)}</td><td>${esc(c.trade_rows??0)}</td><td>${esc(c.cash_rows??0)}</td><td><strong>${esc(label(item.eligibility_status))}</strong><br><span class="muted">${esc(item.eligibility_reason)}</span></td><td>${canStart?`<button class="button start-v11-account" type="button" data-account="${esc(item.account_id)}" data-currency="${esc(item.base_currency||'USD')}" data-start="${esc(item.nav_start_date||'2025-01-20')}">Start onboarding</button>`:registered.has(item.account_id)?'<span class="muted">Registered</span>':!masterComplete?'<span class="muted">Locked until master test</span>':'<span class="muted">Not eligible</span>'}</td></tr>`}).join('')}</tbody></table></div><div id="v11-start-status" class="muted" role="status"></div>`;
  document.querySelectorAll('.start-v11-account').forEach(button=>button.addEventListener('click',async()=>{const displayName=window.prompt('Confirm the website display name',button.dataset.account);if(!displayName)return;button.disabled=true;const note=document.querySelector('#v11-start-status');note.textContent=`Registering ${button.dataset.account} for private import…`;try{const result=await fetch(`/api/onboarding/existing-v11/${encodeURIComponent(button.dataset.account)}/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:displayName,reporting_currency:button.dataset.currency,history_start_date:button.dataset.start})}).then(json);window.location.href=result.next_page}catch(error){note.textContent=`Account was not started: ${error.message}`;button.disabled=false}}));
}).catch(error=>{
  document.querySelector('#onboarding-status').textContent='Onboarding data unavailable';
  document.querySelector('#onboarding-note').textContent=`The workflow remains fail-closed. ${error.message}`;
});
uploadForm.addEventListener('submit',async event=>{
  event.preventDefault();
  const button=uploadForm.querySelector('button');
  button.disabled=true;
  uploadStatus.textContent='Validating and storing all three documents…';
  try{
    const result=await fetch(`/api/onboarding/${encodeURIComponent(uploadSubject.value)}/template-submissions`,{method:'POST',body:new FormData(uploadForm)}).then(json);
    uploadStatus.textContent=`${result.asset_count} documents validated. The account is ready for reconciliation.`;
    setTimeout(()=>window.location.reload(),900);
  }catch(error){
    uploadStatus.textContent=`Submission rejected: ${error.message}`;
    button.disabled=false;
  }
});
})();
</script>
"""


IBKR_CONNECTIONS_PAGE_SCRIPT = r"""
<script>
(()=>{
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const json=async response=>{const payload=await response.json();if(!response.ok)throw new Error(payload.detail||`http_${response.status}`);return payload};
const label=value=>String(value||'').replaceAll('_',' ').replace(/\b\w/g,character=>character.toUpperCase());
const form=document.querySelector('#flex-source-form'),status=document.querySelector('#flex-source-status'),credentialSelect=document.querySelector('#flex-credential-select'),list=document.querySelector('#flex-source-list');
const render=async()=>{const data=await fetch('/api/onboarding/summary').then(json),sources=data.flex_sources||[],requests=data.flex_validation_requests||[],accounts=data.flex_source_accounts||[],credentials=[...new Map(sources.map(source=>[source.credential_id,source])).values()];
credentialSelect.innerHTML='<option value="">Create a new credential</option>'+credentials.map(source=>`<option value="${esc(source.credential_id)}">Reuse ${esc(source.credential_name)} · ${esc(source.token_fingerprint||'pending')}</option>`).join('');
document.querySelector('#connections-summary').innerHTML=[['Connections',sources.length],['Discovered accounts',accounts.length],['Token storage',data.flex_security.token_storage],['IBKR attempt policy',data.flex_security.request_scope]].map(item=>`<div class="status-item"><span class="label">${esc(item[0])}</span><strong>${esc(item[1])}</strong></div>`).join('');
list.innerHTML=sources.length?sources.map(source=>{const latest=requests.find(item=>String(item.flex_source_id)===String(source.flex_source_id)),found=accounts.filter(item=>String(item.flex_source_id)===String(source.flex_source_id)),busy=latest&&['pending','running'].includes(latest.status);return `<article class="onboarding-case"><div class="onboarding-case-head"><div><div class="label">${esc(source.credential_name)}</div><h3>${esc(source.display_name)}</h3></div><span class="onboarding-pill ${esc(source.status)}">${esc(label(source.status))}</span></div><p class="muted">Query ID ${esc(source.query_id)} · ${esc(found.length)} accounts · ${source.schedule_enabled?'Daily schedule active':'Schedule disabled'}</p><p class="muted">Key Vault reference ${esc(source.key_vault_secret_name)} · token fingerprint ${esc(source.token_fingerprint||'pending')}</p>${latest?`<p class="muted"><strong>Latest validation:</strong> ${esc(label(latest.status))} · ${esc(latest.requested_from)} to ${esc(latest.requested_to)} · ${esc(latest.attempt_count)}/1 attempt${latest.error_code?` · ${esc(latest.error_code)}`:''}</p>`:''}${found.length?`<div class="table-wrap"><table><thead><tr><th>Account</th><th>Name</th><th>Type</th><th>Currency</th><th>Selection</th></tr></thead><tbody>${found.map(account=>`<tr><td><strong>${esc(account.source_account_id)}</strong></td><td>${esc(account.account_name||'—')}</td><td>${esc(account.account_type||'—')}</td><td>${esc(account.base_currency||'—')}</td><td>${esc(label(account.discovery_status))}</td></tr>`).join('')}</tbody></table></div>`:''}<div class="actions"><button class="button flex-validate" type="button" data-source="${esc(source.flex_source_id)}" ${busy?'disabled':''}>${busy?'Validation queued':'Validate and discover accounts'}</button>${found.length?`<a class="button" href="/account-onboarding/connections/${encodeURIComponent(source.flex_source_id)}/accounts">Choose accounts and order</a>`:''}</div></article>`}).join(''):'<div class="muted">No direct Flex connection is registered. Add one below; the existing v1.1 production path remains unchanged.</div>';
list.querySelectorAll('.flex-validate').forEach(button=>button.addEventListener('click',async()=>{button.disabled=true;button.textContent='Queueing one controlled attempt…';try{const result=await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(button.dataset.source)}/validate`,{method:'POST'}).then(json);status.textContent=`Validation ${result.validation_request_id} queued. It is limited to one SendRequest and cannot retry automatically.`;button.textContent='Validation queued'}catch(error){button.disabled=false;button.textContent='Validate and discover accounts';status.textContent=`Validation not queued: ${error.message}`}}));};
form.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button'),payload=new FormData(form);button.disabled=true;status.textContent='Securing the token and registering a disabled connection…';try{const result=await fetch('/api/onboarding/flex-sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({credential_id:String(payload.get('credential_id')||'').trim()||null,credential_name:String(payload.get('credential_name')||'').trim()||null,source_name:payload.get('source_name'),query_id:payload.get('query_id'),token:String(payload.get('token')||'')||null})}).then(json);form.reset();status.textContent=`Connection registered with token fingerprint ${result.token_fingerprint}. Validate it explicitly to discover accounts.`;await render()}catch(error){form.querySelector('[name="token"]').value='';status.textContent=`Connection rejected: ${error.message}`}finally{button.disabled=false}});
render().catch(error=>{list.innerHTML='<div class="warning">Connections could not be loaded. No IBKR request was made.</div>';status.textContent=error.message});
})();
</script>
"""


@app.get("/combined-holdings", response_class=HTMLResponse)
def combined_holdings_page(
    request: Request, user: dict[str, str] = Depends(_authorized_user)
) -> str:
    return _render_platform_page(user, section="combined-holdings")


@app.get("/system-health", response_class=HTMLResponse)
def system_health_page(
    request: Request, user: dict[str, str] = Depends(_authorized_user)
) -> str:
    return _render_platform_page(user, section="system-health")


@app.get("/ibkr-connections", response_class=HTMLResponse)
def ibkr_connections_page(
    request: Request, user: dict[str, str] = Depends(_onboarding_user)
):
    # With the new onboarding on, Broker connections replaces this page (it lists the
    # IBKR logins already on the platform too), so old links and bookmarks go there.
    if onboarding_v2_enabled():
        return RedirectResponse(url="/broker-connections", status_code=307)
    return _render_platform_page(user, section="ibkr-connections")


@app.get("/api/onboarding/summary")
def account_onboarding_summary(
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Return operator onboarding state without exposing source files or secrets."""
    with _database() as connection:
        discoveries = connection.execute(
            """
            SELECT source_account_id AS account_id, account_type, base_currency,
                   nav_start_date, nav_end_date, row_counts_json,
                   eligibility_status, eligibility_reason, last_discovered_at
            FROM onboarding_source_accounts
            WHERE tenant_id = %s AND source_system = 'existing_v11'
            ORDER BY source_account_id
            """,
            (TENANT_ID,),
        ).fetchall()
        cases = connection.execute(
            """
            SELECT c.case_id, c.target_subject_id, c.source_system,
                   c.source_account_id, c.stage, c.status,
                   c.requested_history_start, c.reporting_currency,
                   c.configuration_json, c.created_at, c.updated_at,
                   s.subject_type, s.display_name, s.lifecycle_status
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id
             AND s.subject_id = c.target_subject_id
            WHERE c.tenant_id = %s
            ORDER BY c.updated_at DESC, c.target_subject_id
            """,
            (TENANT_ID,),
        ).fetchall()
        checks = connection.execute(
            """
            SELECT case_id, check_id, status, message, checked_at
            FROM account_onboarding_checks
            WHERE tenant_id = %s
            ORDER BY case_id, check_id
            """,
            (TENANT_ID,),
        ).fetchall()
        features = connection.execute(
            """
            SELECT subject_id, feature_key, feature_state, reason, updated_at
            FROM subject_feature_states
            WHERE tenant_id = %s
            ORDER BY subject_id, feature_key
            """,
            (TENANT_ID,),
        ).fetchall()
        template_assets = connection.execute(
            """
            SELECT subject_id, report_type, asset_role, version_label,
                   approval_status, sha256, byte_count, metadata_json, updated_at
            FROM subject_report_template_assets
            WHERE tenant_id = %s AND is_current
            ORDER BY subject_id, report_type, asset_role
            """,
            (TENANT_ID,),
        ).fetchall()
        approvals = connection.execute(
            """
            SELECT case_id, subject_id, approval_type, approval_status,
                   disclosure_version, destination, scope_json, scope_sha256,
                   approved_at
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND approval_status = 'approved'
            ORDER BY case_id, approval_type, approved_at DESC
            """,
            (TENANT_ID,),
        ).fetchall()
        flex_sources = connection.execute(
            """
            SELECT q.flex_source_id, q.display_name, q.query_id, q.status,
                   q.schedule_enabled, q.schedule_time_zone, q.schedule_hour,
                   q.schedule_minute, q.last_success_at,
                   c.credential_id, c.display_name AS credential_name,
                   c.key_vault_secret_name, c.token_fingerprint,
                   c.status AS credential_status,
                   COUNT(DISTINCT a.source_account_id) AS discovered_accounts,
                   COUNT(DISTINCT CASE WHEN b.status = 'active' THEN b.subject_id END)
                       AS active_subjects
            FROM ibkr_flex_queries q
            JOIN ibkr_flex_credentials c
              ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
            LEFT JOIN ibkr_flex_source_accounts a
              ON a.tenant_id = q.tenant_id AND a.flex_source_id = q.flex_source_id
            LEFT JOIN subject_flex_source_bindings b
              ON b.tenant_id = q.tenant_id AND b.flex_source_id = q.flex_source_id
            WHERE q.tenant_id = %s
            GROUP BY q.flex_source_id, q.display_name, q.query_id, q.status,
                     q.schedule_enabled, q.schedule_time_zone, q.schedule_hour,
                     q.schedule_minute, q.last_success_at,
                     c.credential_id, c.display_name, c.key_vault_secret_name,
                     c.token_fingerprint, c.status
            ORDER BY q.display_name, q.query_id
            """,
            (TENANT_ID,),
        ).fetchall()
        flex_validation_requests = connection.execute(
            """
            SELECT validation_request_id, flex_source_id, status,
                   requested_from, requested_to, attempt_count,
                   result_json, error_phase, error_code,
                   created_at, started_at, completed_at
            FROM ibkr_flex_source_validation_requests
            WHERE tenant_id = %s
            ORDER BY created_at DESC
            """,
            (TENANT_ID,),
        ).fetchall()
        flex_source_accounts = connection.execute(
            """
            SELECT flex_source_id, source_account_id, account_type,
                   base_currency, account_name, discovery_status,
                   row_counts_json, first_seen_at, last_seen_at
            FROM ibkr_flex_source_accounts
            WHERE tenant_id = %s
            ORDER BY flex_source_id, source_account_id
            """,
            (TENANT_ID,),
        ).fetchall()
        programme_subjects = connection.execute(
            """
            SELECT subject_id, lifecycle_status
            FROM platform_subjects
            WHERE tenant_id = %s
            """,
            (TENANT_ID,),
        ).fetchall()
        studio_subjects = connection.execute(
            """
            SELECT subject_id, subject_type, display_name,
                   COALESCE(reporting_currency, base_currency) AS currency,
                   history_start_date
            FROM platform_subjects
            WHERE tenant_id = %s AND lifecycle_status = 'active'
              AND subject_type IN (
                  'physical_account', 'filtered_portfolio', 'composite_portfolio'
              )
            ORDER BY display_name, subject_id
            """,
            (TENANT_ID,),
        ).fetchall()
        studio_market_rows = connection.execute(
            """
            SELECT instrument_id, MAX(price_date) AS latest_date, COUNT(*) AS observations
            FROM market_data_prices
            WHERE instrument_id = ANY(%s)
            GROUP BY instrument_id
            """,
            ([item.instrument_id for item in ALPHA_WORLD_MARKET_INSTRUMENTS],),
        ).fetchall()
    checks_by_case: dict[str, list[dict]] = {}
    for row in checks:
        checks_by_case.setdefault(str(row["case_id"]), []).append(dict(row))
    features_by_subject: dict[str, list[dict]] = {}
    for row in features:
        features_by_subject.setdefault(str(row["subject_id"]), []).append(dict(row))
    templates_by_subject: dict[str, list[dict]] = {}
    for row in template_assets:
        templates_by_subject.setdefault(str(row["subject_id"]), []).append(dict(row))
    approvals_by_case: dict[str, list[dict]] = {}
    for row in approvals:
        approvals_by_case.setdefault(str(row["case_id"]), []).append(dict(row))
    case_payload = []
    for row in cases:
        item = dict(row)
        item["checks"] = checks_by_case.get(str(row["case_id"]), [])
        item["features"] = features_by_subject.get(str(row["target_subject_id"]), [])
        item["templates"] = templates_by_subject.get(
            str(row["target_subject_id"]), []
        )
        item["approvals"] = approvals_by_case.get(str(row["case_id"]), [])
        try:
            identity_scope = presentation_identity_scope(
                subject_id=str(row["target_subject_id"]),
                display_name=str(row["display_name"]),
                layout_policy="platform_standard",
            )
            item["presentation_identity_candidate"] = {
                **identity_scope,
                "display_name_sha256": sha256_text(
                    str(identity_scope["source_display_name"])
                ),
                "scope_sha256": sha256_text(canonical_json(identity_scope)),
            }
        except RuntimeError:
            item["presentation_identity_candidate"] = None
        case_payload.append(item)
    programme = reference_onboarding_programme(
        subject_states={
            str(row["subject_id"]): str(row["lifecycle_status"])
            for row in programme_subjects
        },
        onboarding_states={
            str(row["target_subject_id"]): str(row["status"])
            for row in cases
        },
        discovered_account_ids=[
            str(row["account_id"])
            for row in discoveries
        ] + [
            str(row["source_account_id"])
            for row in flex_source_accounts
        ],
    )
    reference_definitions = approved_reference_definitions()
    for item in programme:
        definition = reference_definitions.get(str(item["subject_id"]))
        if definition is not None:
            item["definition_sha256"] = subject_configuration_sha256(definition)
            item["definition_summary"] = reference_definition_summary(definition)
    return {
        "source_system": "existing_v11",
        "source_description": "Existing validated v1.1 production export",
        "active_reference_subject": PILOT_ACCOUNT_ID,
        "wave0_subject": "U16088606",
        "discovered_accounts": [dict(row) for row in discoveries],
        "cases": case_payload,
        "flex_sources": [dict(row) for row in flex_sources],
        "flex_validation_requests": [dict(row) for row in flex_validation_requests],
        "flex_source_accounts": [dict(row) for row in flex_source_accounts],
        "flex_security": {
            "token_storage": "Azure Key Vault reference only",
            "request_scope": "One SendRequest per query source run",
            "partitioning": "One downloaded statement is partitioned by IBKR account ID",
            "circuit_breaker": "Shared by every query that uses the same credential",
            "credential_submission": "Secure write-only credential broker required",
        },
        "wizard": {
            "version": "subject-onboarding-wizard-v6",
            "queue_policy": {
                "scope": "one_queue_per_flex_connection",
                "execution": "one_current_account_at_a_time",
                "unlock_condition": "previous_account_fully_active",
                "unselected_accounts": "retained_for_future_onboarding",
            },
            "subject_types": onboarding_branch_catalog(),
            "steps": [
                "Connect and discover",
                "Select account and history",
                "Set financial contract",
                "Approve external data use",
                "Upload report documents",
                "Reconcile account data",
                "Render and approve historical reports",
                "Review private website preview",
                "Owner activation",
            ],
            "permissions": {
                "may_operate": user["role"] in {"owner", "onboarding_operator"},
                "may_approve_external_data": user["role"] == "owner",
                "may_approve_presentation_identity": user["role"] == "owner",
                "may_activate": user["role"] == "owner",
            },
            "external_data_disclosure": {
                "approval_type": "external_market_data",
                "disclosure_version": YAHOO_ONBOARDING_DISCLOSURE_VERSION,
                "scope": YAHOO_ONBOARDING_SCOPE,
                "scope_sha256": YAHOO_ONBOARDING_SCOPE_SHA256,
                "message": (
                    "Only current and historical ticker symbols will be sent to "
                    "Yahoo Finance via yfinance for provisional sector classification "
                    "and crisis-period price retrieval. Raw IBKR files, account IDs, "
                    "credentials, database credentials and personal information are excluded."
                ),
            },
        },
        "derived_studio": {
            "version": "derived-portfolio-studio-v1",
            "templates": derived_portfolio_studio_templates(),
            "available_subjects": [dict(row) for row in studio_subjects],
            "available_market_components": [
                {
                    "component_id": item.instrument_id,
                    "component_name": item.name,
                    "component_type": "benchmark",
                    "source_currency": item.currency,
                    "price_convention": _studio_price_convention(item.price_type),
                    "latest_date": next(
                        (
                            str(row["latest_date"])
                            for row in studio_market_rows
                            if str(row["instrument_id"]) == item.instrument_id
                        ),
                        None,
                    ),
                    "observations": next(
                        (
                            int(row["observations"])
                            for row in studio_market_rows
                            if str(row["instrument_id"]) == item.instrument_id
                        ),
                        0,
                    ),
                }
                for item in ALPHA_WORLD_MARKET_INSTRUMENTS
            ],
            "safety": {
                "executable_code": "prohibited",
                "definition_changes": "new version and owner approval required",
                "dependency_cycles": "rejected",
                "activation": "owner_only_after_all_gates",
            },
        },
        "planned_sequence": list(POST_WAVE0_ONBOARDING_SEQUENCE),
        "reference_programme": programme,
        "viewer": user["email"],
    }


def _refuse_classic_studio() -> None:
    """With the new onboarding on, portfolios start from the portfolio builder instead."""

    if onboarding_v2_enabled():
        raise HTTPException(
            status_code=409,
            detail="The Derived Portfolio Studio has been replaced by the portfolio builder: "
            "Account onboarding, New portfolio.",
        )


def _create_derived_studio_case(
    connection,
    *,
    definition: dict,
    actor: str,
) -> tuple[str, str]:
    """Persist one validated Studio definition and its private wizard case."""

    subject = str(definition["subject_id"])
    existing = connection.execute(
        """
        SELECT 1 FROM platform_subjects
        WHERE tenant_id = %s AND subject_id = %s
        """,
        (TENANT_ID, subject),
    ).fetchone()
    if existing is not None:
        raise HTTPException(status_code=409, detail="This subject ID is already registered")
    dependencies = dependencies_for_definition(definition)
    dependency_ids = sorted({item.depends_on_subject_id for item in dependencies})
    subject_rows = connection.execute(
        """
        SELECT subject_id, subject_type, lifecycle_status
        FROM platform_subjects
        WHERE tenant_id = %s AND subject_id = ANY(%s)
        FOR SHARE
        """,
        (TENANT_ID, dependency_ids),
    ).fetchall()
    states = {str(row["subject_id"]): dict(row) for row in subject_rows}
    benchmark_ids = {
        item.depends_on_subject_id
        for item in dependencies
        if item.dependency_type == "benchmark"
    }
    required_subjects = set(dependency_ids) - benchmark_ids
    missing = sorted(required_subjects - set(states))
    inactive = sorted(
        item for item in required_subjects
        if item in states and str(states[item]["lifecycle_status"]) != "active"
    )
    if missing or inactive:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "derived_dependency_not_active",
                "missing": missing,
                "inactive": inactive,
            },
        )
    effective_from = date.fromisoformat(
        str(definition.get("history_start_date") or definition["normalization_date"])
    )
    definition_digest = subject_configuration_sha256(definition)
    case_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    connection.execute(
        """
        INSERT INTO platform_subjects(
            tenant_id, subject_id, subject_type, display_name,
            source_account_id, base_currency, reporting_currency, history_start_date,
            inception_date, lifecycle_status, metadata_json,
            created_at, updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, 'onboarding',
            %s::jsonb, now(), now()
        )
        """,
        (
            TENANT_ID, subject, definition["subject_type"], definition["display_name"],
            definition.get("source_account_id"),
            definition["base_currency"], definition["base_currency"],
            effective_from, effective_from,
            canonical_json(
                {
                    "source_system": "derived_definition",
                    "definition_origin": "website_derived_portfolio_studio",
                    "studio_version": "derived-portfolio-studio-v1",
                    "definition_sha256": definition_digest,
                }
            ),
        ),
    )
    connection.execute(
        """
        INSERT INTO account_onboarding_cases(
            case_id, tenant_id, target_subject_id, source_system,
            source_account_id, stage, status, requested_history_start,
            reporting_currency, configuration_json,
            created_by_identity_sha256, created_at, updated_at
        ) VALUES (
            %s, %s, %s, 'derived_definition', %s,
            'financial_contract', 'in_progress', %s, %s,
            %s::jsonb, %s, now(), now()
        )
        """,
        (
            case_id, TENANT_ID, subject, subject, effective_from,
            definition["base_currency"],
            canonical_json(
                {
                    "wizard_version": "subject-onboarding-wizard-v6",
                    "definition_origin": "website_derived_portfolio_studio",
                    "definition_sha256": definition_digest,
                }
            ),
            actor,
        ),
    )
    connection.execute(
        """
        INSERT INTO subject_calculation_definitions(
            definition_id, tenant_id, subject_id, subject_type,
            calculation_kind, definition_version, configuration_json,
            configuration_sha256, approval_status, effective_from,
            created_by_identity_sha256, created_at
        ) VALUES (
            %s, %s, %s, %s, %s, 1, %s::jsonb, %s, 'validated',
            %s, %s, now()
        )
        """,
        (
            definition_id, TENANT_ID, subject, definition["subject_type"],
            definition["calculation_kind"], canonical_json(definition),
            definition_digest, effective_from, actor,
        ),
    )
    for dependency in dependencies:
        connection.execute(
            """
            INSERT INTO subject_dependencies(
                tenant_id, subject_id, depends_on_subject_id,
                dependency_type, effective_from, configuration_json, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
            """,
            (
                TENANT_ID, subject, dependency.depends_on_subject_id,
                dependency.dependency_type,
                date.fromisoformat(dependency.effective_from),
                canonical_json(dict(dependency.configuration)),
            ),
        )
    checks = (
        ("source_account_discovered", "pass", "Every selected account and portfolio dependency is active."),
        ("connection_validated", "pass", "The typed portfolio uses governed internal dependencies and no new Flex credential."),
        ("calculation_definition_approved", "pending", "Owner approval of the exact typed calculation is required."),
        ("financial_contract_approved", "pending", "Owner approval of the calculation and benchmark contract is required."),
        ("data_import_reconciled", "pending", "Waiting for the historical dry run and source reconciliation."),
        ("external_market_data_approved", "pending", "Owner approval is required before ticker symbols leave the platform."),
        ("presentation_identity_approved", "pending", "Owner approval of the server-derived presentation identity is required."),
        ("report_documents_validated", "pending", "The mandatory three-document set has not been validated."),
        ("risk_analytics_validated", "pending", "Risk analytics will be tested after calculation reconciliation."),
        ("historical_reports_backfilled", "pending", "Historical monthly reports must be rendered and approved."),
        ("website_preview_validated", "pending", "The private website preview has not been approved."),
        ("owner_activation", "pending", "Explicit owner activation is required after every other gate passes."),
    )
    for check_id, status, message in checks:
        connection.execute(
            """
            INSERT INTO account_onboarding_checks(
                tenant_id, case_id, check_id, status, message,
                evidence_json, checked_at
            ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, now())
            """,
            (TENANT_ID, case_id, check_id, status, message),
        )
    for feature_key in (
        "data_ingestion", "website_analytics", "ai_analyst",
        "factsheet", "presentation", "viewer_access",
    ):
        connection.execute(
            """
            INSERT INTO subject_feature_states(
                tenant_id, subject_id, feature_key, feature_state, reason, updated_at
            ) VALUES (%s, %s, %s, 'preview', %s, now())
            """,
            (
                TENANT_ID, subject, feature_key,
                "Private Studio onboarding only; owner activation remains blocked.",
            ),
        )
    return str(case_id), definition_digest


def _studio_filtered_definition(
    connection,
    submission: DerivedStudioFilteredStartRequest,
    subject: str,
    source: str,
) -> dict:
    """Validate a filtered-portfolio form against its live source account."""

    source_row = connection.execute(
        """
        SELECT subject_id, subject_type, lifecycle_status,
               COALESCE(reporting_currency, base_currency) AS currency,
               history_start_date
        FROM platform_subjects
        WHERE tenant_id = %s AND subject_id = %s
        FOR SHARE
        """,
        (TENANT_ID, source),
    ).fetchone()
    if (
        source_row is None
        or str(source_row["subject_type"]) != "physical_account"
        or str(source_row["lifecycle_status"]) != "active"
    ):
        raise HTTPException(
            status_code=409,
            detail="A filtered portfolio requires an active physical source account",
        )
    if (
        source_row["history_start_date"] is not None
        and submission.history_start_date < source_row["history_start_date"]
    ):
        raise HTTPException(
            status_code=409,
            detail="The requested history starts before the source account coverage",
        )
    if str(source_row["currency"] or "").upper() != submission.base_currency:
        raise HTTPException(
            status_code=409,
            detail="The filtered portfolio base currency must match its source account",
        )
    try:
        definition = filtered_portfolio_definition(
            subject_id=subject,
            display_name=submission.display_name,
            source_account_id=source,
            base_currency=submission.base_currency,
            history_start_date=submission.history_start_date.isoformat(),
            excluded_countries=submission.excluded_countries,
            excluded_exchanges=submission.excluded_exchanges,
            excluded_currencies=submission.excluded_currencies,
            opening_cash=submission.opening_cash,
            allowed_cash_currencies=submission.allowed_cash_currencies,
            include_actual_interest=submission.include_actual_interest,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return definition


def _studio_composite_definition(
    connection,
    submission: DerivedStudioCompositeStartRequest,
    subject: str,
) -> dict:
    """Validate a composite form against live sleeves and approved market history."""

    market_catalog = {
        item.instrument_id: item for item in ALPHA_WORLD_MARKET_INSTRUMENTS
    }
    component_ids = [item.component_id for item in submission.components]
    rows = connection.execute(
        """
        SELECT subject_id, subject_type, display_name,
               COALESCE(reporting_currency, base_currency) AS currency,
               lifecycle_status
        FROM platform_subjects
        WHERE tenant_id = %s AND subject_id = ANY(%s)
        FOR SHARE
        """,
        (TENANT_ID, component_ids),
    ).fetchall()
    registered = {str(row["subject_id"]): dict(row) for row in rows}
    components = []
    for request in submission.components:
        component_id = request.component_id
        row = registered.get(component_id)
        if row is not None:
            if str(row["lifecycle_status"]) != "active":
                raise HTTPException(
                    status_code=409,
                    detail=f"Component {component_id} is not active",
                )
            source_type = str(row["subject_type"])
            if source_type == "physical_account":
                component_type = "account"
            elif source_type in {"filtered_portfolio", "composite_portfolio"}:
                component_type = "portfolio"
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Component {component_id} is not an investable sleeve",
                )
            components.append(
                {
                    "component_id": component_id,
                    "component_name": str(row["display_name"]),
                    "component_type": component_type,
                    "coefficient": request.coefficient,
                    "source_currency": str(row["currency"]),
                    "price_convention": "nav",
                }
            )
            continue
        market = market_catalog.get(component_id)
        if market is None:
            raise HTTPException(
                status_code=409,
                detail=f"Component {component_id} is not in the approved catalogue",
            )
        coverage = connection.execute(
            """
            SELECT MAX(price_date) FILTER (WHERE price_date <= %s) AS base_date,
                   MAX(price_date) AS latest_date
            FROM market_data_prices WHERE instrument_id = %s
            """,
            (submission.normalization_date.isoformat(), component_id),
        ).fetchone()
        if coverage is None or coverage["base_date"] is None:
            raise HTTPException(
                status_code=409,
                detail=f"Component {component_id} has no normalization-date market history",
            )
        components.append(
            {
                "component_id": component_id,
                "component_name": market.name,
                "component_type": "benchmark",
                "coefficient": request.coefficient,
                "source_currency": market.currency,
                "price_convention": _studio_price_convention(market.price_type),
            }
        )
    try:
        definition = fixed_unit_composite_definition(
            subject_id=subject,
            display_name=submission.display_name,
            base_currency=submission.base_currency,
            normalization_date=submission.normalization_date.isoformat(),
            constant_cash=submission.constant_cash,
            components=components,
            market_carry_days=submission.market_carry_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return definition


_BUILDER_FIELD_LABELS = {
    "subject_id": "the portfolio ID",
    "display_name": "the name",
    "source_account_id": "the account",
    "base_currency": "the reporting currency",
    "allowed_cash_currencies": "the cash currencies",
    "opening_cash": "the opening cash",
    "components": "the sleeves",
}


def _builder_definition(connection, kind: str, fields: dict) -> dict:
    """The new portfolio builder runs exactly the Studio's validation."""

    try:
        if kind == "filtered_portfolio":
            filtered = DerivedStudioFilteredStartRequest.model_validate(fields)
            return _studio_filtered_definition(
                connection, filtered,
                validate_subject_id(filtered.subject_id),
                validate_subject_id(filtered.source_account_id),
            )
        composite = DerivedStudioCompositeStartRequest.model_validate(fields)
        return _studio_composite_definition(
            connection, composite, validate_subject_id(composite.subject_id)
        )
    except ValidationError as error:
        first = error.errors()[0] if error.errors() else {}
        field = str((first.get("loc") or ("",))[0])
        raise HTTPException(
            status_code=400,
            detail=f"Check {_BUILDER_FIELD_LABELS.get(field, 'the portfolio fields')}: "
            f"{first.get('msg', 'invalid value')}",
        ) from error
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.post("/api/onboarding/derived-studio/filtered/start")
def start_studio_filtered_portfolio(
    submission: DerivedStudioFilteredStartRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Create a private filtered-portfolio case from typed website fields."""

    _refuse_classic_studio()
    try:
        subject = validate_subject_id(submission.subject_id)
        source = validate_subject_id(submission.source_account_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        definition = _studio_filtered_definition(connection, submission, subject, source)
        case_id, digest = _create_derived_studio_case(
            connection, definition=definition, actor=actor
        )
    return {
        "status": "created",
        "case_id": case_id,
        "subject_id": subject,
        "definition_sha256": digest,
        "next_page": f"/account-onboarding/{subject}/steps/financial-contract",
    }


@app.post("/api/onboarding/derived-studio/composite/start")
def start_studio_fixed_unit_composite(
    submission: DerivedStudioCompositeStartRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Create a private fixed-unit composite from allow-listed components."""

    _refuse_classic_studio()
    try:
        subject = validate_subject_id(submission.subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        definition = _studio_composite_definition(connection, submission, subject)
        case_id, digest = _create_derived_studio_case(
            connection, definition=definition, actor=actor
        )
    return {
        "status": "created",
        "case_id": case_id,
        "subject_id": subject,
        "definition_sha256": digest,
        "next_page": f"/account-onboarding/{subject}/steps/financial-contract",
    }


@app.post("/api/onboarding/reference-programme/{subject_id}/start")
def start_reference_subject_onboarding(
    subject_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Start one approved derived subject after its ordered gates pass.

    The endpoint accepts only server-owned reference definitions.  It does not
    accept formulas, SQL, Python, coefficients or filters from the browser.
    """

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    definitions = approved_reference_definitions()
    definition = definitions.get(subject)
    if definition is None:
        raise HTTPException(
            status_code=409,
            detail="This subject requires its dedicated master or physical-account workflow",
        )
    actor = _identity_digest(user["object_id"] or user["email"])
    definition_digest = subject_configuration_sha256(definition)
    effective_from = date.fromisoformat(
        str(definition.get("history_start_date") or definition["normalization_date"])
    )
    with _approval_database() as connection:
        subjects = connection.execute(
            """
            SELECT subject_id, lifecycle_status FROM platform_subjects
            WHERE tenant_id = %s
            FOR UPDATE
            """,
            (TENANT_ID,),
        ).fetchall()
        cases = connection.execute(
            """
            SELECT target_subject_id, status FROM account_onboarding_cases
            WHERE tenant_id = %s
            FOR UPDATE
            """,
            (TENANT_ID,),
        ).fetchall()
        discovered = connection.execute(
            """
            SELECT source_account_id FROM onboarding_source_accounts
            WHERE tenant_id = %s
            UNION
            SELECT source_account_id FROM ibkr_flex_source_accounts
            WHERE tenant_id = %s
            """,
            (TENANT_ID, TENANT_ID),
        ).fetchall()
        programme = reference_onboarding_programme(
            subject_states={
                str(row["subject_id"]): str(row["lifecycle_status"])
                for row in subjects
            },
            onboarding_states={
                str(row["target_subject_id"]): str(row["status"])
                for row in cases
            },
            discovered_account_ids=[str(row["source_account_id"]) for row in discovered],
        )
        programme_item = next(
            (item for item in programme if item["subject_id"] == subject), None
        )
        if programme_item is None or not programme_item["can_start"]:
            detail = programme_item["reason"] if programme_item else "Subject is not in the approved programme"
            raise HTTPException(status_code=409, detail=detail)
        benchmark_ids = sorted(
            dependency.depends_on_subject_id
            for dependency in dependencies_for_definition(definition)
            if dependency.dependency_type == "benchmark"
        )
        if benchmark_ids:
            rows = connection.execute(
                """
                SELECT instrument_id,
                       MAX(price_date) FILTER (WHERE price_date <= %s) AS base_date,
                       MAX(price_date) AS latest_date
                FROM market_data_prices
                WHERE tenant_id = %s AND instrument_id = ANY(%s)
                GROUP BY instrument_id
                """,
                (effective_from.isoformat(), TENANT_ID, benchmark_ids),
            ).fetchall()
            covered = {
                str(row["instrument_id"])
                for row in rows
                if row["base_date"] is not None and row["latest_date"] is not None
            }
            missing = sorted(set(benchmark_ids) - covered)
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reference_market_data_missing",
                        "benchmark_ids": missing,
                        "required_on_or_before": effective_from.isoformat(),
                    },
                )
        case_id = uuid.uuid4()
        definition_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO platform_subjects(
                tenant_id, subject_id, subject_type, display_name,
                base_currency, reporting_currency, history_start_date,
                inception_date, lifecycle_status, metadata_json,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, 'onboarding',
                %s::jsonb, now(), now()
            )
            """,
            (
                TENANT_ID, subject, definition["subject_type"],
                definition["display_name"], definition["base_currency"],
                definition["base_currency"], effective_from, effective_from,
                canonical_json(
                    {
                        "source_system": "derived_definition",
                        "programme_position": programme_item["position"],
                        "definition_sha256": definition_digest,
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_cases(
                case_id, tenant_id, target_subject_id, source_system,
                source_account_id, stage, status, requested_history_start,
                reporting_currency, configuration_json,
                created_by_identity_sha256, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'derived_definition', %s,
                'financial_contract', 'in_progress', %s, %s,
                %s::jsonb, %s, now(), now()
            )
            """,
            (
                case_id, TENANT_ID, subject, subject, effective_from,
                definition["base_currency"],
                canonical_json(
                    {
                        "wizard_version": "subject-onboarding-wizard-v5",
                        "programme_position": programme_item["position"],
                        "definition_sha256": definition_digest,
                    }
                ),
                actor,
            ),
        )
        connection.execute(
            """
            INSERT INTO subject_calculation_definitions(
                definition_id, tenant_id, subject_id, subject_type,
                calculation_kind, definition_version, configuration_json,
                configuration_sha256, approval_status, effective_from,
                created_by_identity_sha256, created_at
            ) VALUES (
                %s, %s, %s, %s, %s, 1, %s::jsonb, %s, 'validated',
                %s, %s, now()
            )
            """,
            (
                definition_id, TENANT_ID, subject, definition["subject_type"],
                definition["calculation_kind"], canonical_json(definition),
                definition_digest, effective_from, actor,
            ),
        )
        for dependency in dependencies_for_definition(definition):
            connection.execute(
                """
                INSERT INTO subject_dependencies(
                    tenant_id, subject_id, depends_on_subject_id,
                    dependency_type, effective_from, configuration_json,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, now())
                """,
                (
                    TENANT_ID, subject, dependency.depends_on_subject_id,
                    dependency.dependency_type,
                    date.fromisoformat(dependency.effective_from),
                    canonical_json(dict(dependency.configuration)),
                ),
            )
        checks = (
            ("source_account_discovered", "pass", "Every required source account or portfolio is active."),
            ("connection_validated", "pass", "This calculation uses governed internal dependencies and no additional credential."),
            ("calculation_definition_approved", "pending", "Owner approval of the exact versioned calculation definition is required."),
            ("financial_contract_approved", "pending", "Owner approval of the calculation and benchmark contract is required."),
            ("data_import_reconciled", "pending", "Waiting for a complete historical calculation and source reconciliation."),
            ("external_market_data_approved", "pending", "Owner approval is required before ticker symbols leave the platform."),
            ("presentation_identity_approved", "pending", "Owner approval of the server-derived presentation identity is required."),
            ("report_documents_validated", "pending", "The mandatory three-document set has not been validated."),
            ("risk_analytics_validated", "pending", "Risk analytics will be tested after calculation reconciliation."),
            ("historical_reports_backfilled", "pending", "Historical monthly reports must be rendered and approved."),
            ("website_preview_validated", "pending", "The private website preview has not been approved."),
            ("owner_activation", "pending", "Explicit owner activation is required after every other gate passes."),
        )
        for check_id, status, message in checks:
            connection.execute(
                """
                INSERT INTO account_onboarding_checks(
                    tenant_id, case_id, check_id, status, message,
                    evidence_json, checked_at
                ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, now())
                """,
                (TENANT_ID, case_id, check_id, status, message),
            )
        for feature_key in (
            "data_ingestion", "website_analytics", "ai_analyst",
            "factsheet", "presentation", "viewer_access",
        ):
            connection.execute(
                """
                INSERT INTO subject_feature_states(
                    tenant_id, subject_id, feature_key, feature_state,
                    reason, updated_at
                ) VALUES (%s, %s, %s, %s, %s, now())
                """,
                (
                    TENANT_ID, subject, feature_key,
                    "preview" if feature_key == "data_ingestion" else "disabled",
                    "The derived subject remains private until every onboarding gate passes.",
                ),
            )
    return {
        "status": "in_progress",
        "subject_id": subject,
        "case_id": str(case_id),
        "definition_sha256": definition_digest,
        "next_page": f"/account-onboarding/{subject}/steps/financial-contract",
    }


@app.post("/api/onboarding/master-programme/U15606638/start")
def start_reference_master_onboarding(
    submission: OnboardingMasterAccountStartRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Create the master test with an explicit anti-double-counting contract."""

    subject = "U15606638"
    if submission.reporting_mode != "administrative_only":
        raise HTTPException(
            status_code=400,
            detail="The self-service reference master supports the approved administrative-only mode; investment aggregation is intentionally disabled to prevent double counting.",
        )
    if submission.history_start_date > date.today():
        raise HTTPException(status_code=400, detail="History start date cannot be in the future")
    try:
        definition = master_account_definition(
            master_account_id=subject,
            display_name=submission.display_name,
            reporting_mode=submission.reporting_mode,
            reporting_currency=submission.reporting_currency,
            history_start_date=submission.history_start_date.isoformat(),
            member_account_ids=submission.member_account_ids,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    digest = subject_configuration_sha256(definition)
    with _approval_database() as connection:
        subjects = connection.execute(
            "SELECT subject_id, lifecycle_status FROM platform_subjects WHERE tenant_id = %s FOR UPDATE",
            (TENANT_ID,),
        ).fetchall()
        cases = connection.execute(
            "SELECT target_subject_id, status FROM account_onboarding_cases WHERE tenant_id = %s FOR UPDATE",
            (TENANT_ID,),
        ).fetchall()
        discoveries = connection.execute(
            """
            SELECT source_account_id FROM onboarding_source_accounts
            WHERE tenant_id = %s
            UNION SELECT source_account_id FROM ibkr_flex_source_accounts
            WHERE tenant_id = %s
            """,
            (TENANT_ID, TENANT_ID),
        ).fetchall()
        programme = reference_onboarding_programme(
            subject_states={str(row["subject_id"]): str(row["lifecycle_status"]) for row in subjects},
            onboarding_states={str(row["target_subject_id"]): str(row["status"]) for row in cases},
            discovered_account_ids=[str(row["source_account_id"]) for row in discoveries],
        )
        item = next(value for value in programme if value["subject_id"] == subject)
        if not item["can_start"]:
            raise HTTPException(status_code=409, detail=item["reason"])
        active_ids = {
            str(row["subject_id"])
            for row in subjects
            if str(row["lifecycle_status"]) == "active"
        }
        missing_members = sorted(set(definition["member_account_ids"]) - active_ids)
        if missing_members:
            raise HTTPException(
                status_code=409,
                detail={"code": "inactive_master_members", "subject_ids": missing_members},
            )
        source = connection.execute(
            """
            SELECT account_type, base_currency FROM onboarding_source_accounts
            WHERE tenant_id = %s AND source_account_id = %s
            ORDER BY last_discovered_at DESC LIMIT 1
            """,
            (TENANT_ID, subject),
        ).fetchone()
        case_id = uuid.uuid4()
        definition_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO platform_subjects(
                tenant_id, subject_id, subject_type, display_name,
                source_account_id, account_type, base_currency,
                reporting_currency, history_start_date, inception_date,
                lifecycle_status, metadata_json, created_at, updated_at
            ) VALUES (
                %s, %s, 'master_account', %s, %s, %s, %s, %s, %s, %s,
                'onboarding', %s::jsonb, now(), now()
            )
            """,
            (
                TENANT_ID, subject, definition["display_name"], subject,
                str(source["account_type"] or "MASTER") if source else "MASTER",
                str(source["base_currency"] or submission.reporting_currency) if source else submission.reporting_currency,
                submission.reporting_currency, submission.history_start_date,
                submission.history_start_date,
                canonical_json(
                    {
                        "source_system": "master_definition",
                        "reporting_mode": submission.reporting_mode,
                        "definition_sha256": digest,
                    }
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_cases(
                case_id, tenant_id, target_subject_id, source_system,
                source_account_id, stage, status, requested_history_start,
                reporting_currency, configuration_json,
                created_by_identity_sha256, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'master_definition', %s,
                'financial_contract', 'in_progress', %s, %s, %s::jsonb,
                %s, now(), now()
            )
            """,
            (
                case_id, TENANT_ID, subject, subject,
                submission.history_start_date, submission.reporting_currency,
                canonical_json(
                    {
                        "wizard_version": "subject-onboarding-wizard-v5",
                        "definition_sha256": digest,
                        "reporting_mode": submission.reporting_mode,
                    }
                ),
                actor,
            ),
        )
        connection.execute(
            """
            INSERT INTO master_account_definitions(
                definition_id, tenant_id, subject_id, definition_version,
                configuration_json, configuration_sha256, approval_status,
                effective_from, created_by_identity_sha256, created_at
            ) VALUES (%s, %s, %s, 1, %s::jsonb, %s, 'validated', %s, %s, now())
            """,
            (
                definition_id, TENANT_ID, subject, canonical_json(definition),
                digest, submission.history_start_date, actor,
            ),
        )
        for member in definition["member_account_ids"]:
            connection.execute(
                """
                INSERT INTO master_account_memberships(
                    tenant_id, master_subject_id, member_subject_id,
                    reporting_mode, eliminate_internal_transfers,
                    effective_from, created_at
                ) VALUES (%s, %s, %s, 'consolidated_children', true, %s, now())
                """,
                (TENANT_ID, subject, member, submission.history_start_date),
            )
        administrative = submission.reporting_mode == "administrative_only"
        checks = (
            ("source_account_discovered", "pass", "The master account is present in a validated source inventory."),
            ("connection_validated", "pass", "The master source inventory and member identities are validated."),
            ("calculation_definition_approved", "pending", "Owner approval of the master reporting mode and anti-double-counting policy is required."),
            ("financial_contract_approved", "pending", "Owner approval of the master reporting contract is required."),
            ("data_import_reconciled", "pass" if administrative else "pending", "Administrative-only masters do not publish investment totals." if administrative else "Waiting for master aggregation reconciliation."),
            ("external_market_data_approved", "pass" if administrative else "pending", "No external market data is used by an administrative-only master." if administrative else "Owner external-data approval is required."),
            ("presentation_identity_approved", "pass" if administrative else "pending", "No presentation is produced for an administrative-only master." if administrative else "Owner presentation identity approval is required."),
            ("report_documents_validated", "pass" if administrative else "pending", "Reports are not applicable to an administrative-only master." if administrative else "Report documents are required."),
            ("risk_analytics_validated", "pass" if administrative else "pending", "Risk metrics are not applicable to an administrative-only master." if administrative else "Risk analytics must be validated."),
            ("historical_reports_backfilled", "pass" if administrative else "pending", "Historical reports are not applicable to an administrative-only master." if administrative else "Historical reports must be rendered."),
            ("website_preview_validated", "pending", "The private master preview has not been approved."),
            ("owner_activation", "pending", "Explicit owner activation is required."),
        )
        for check_id, status, message in checks:
            connection.execute(
                """
                INSERT INTO account_onboarding_checks(
                    tenant_id, case_id, check_id, status, message,
                    evidence_json, checked_at
                ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, now())
                """,
                (TENANT_ID, case_id, check_id, status, message),
            )
        for feature_key in (
            "data_ingestion", "website_analytics", "ai_analyst",
            "factsheet", "presentation", "viewer_access",
        ):
            connection.execute(
                """
                INSERT INTO subject_feature_states(
                    tenant_id, subject_id, feature_key, feature_state, reason, updated_at
                ) VALUES (%s, %s, %s, 'disabled', %s, now())
                """,
                (
                    TENANT_ID, subject, feature_key,
                    "The master remains private until its reporting mode and preview are approved.",
                ),
            )
    return {
        "status": "in_progress",
        "subject_id": subject,
        "case_id": str(case_id),
        "definition_sha256": digest,
        "next_page": f"/account-onboarding/{subject}/steps/financial-contract",
    }


@app.post("/api/onboarding/existing-v11/{account_id}/start")
def start_existing_v11_account_onboarding(
    account_id: str,
    submission: OnboardingPhysicalAccountStartRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Start one remaining discovered v1.1 account after the master test."""

    try:
        subject = validate_subject_id(account_id.strip().upper())
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if submission.history_start_date > date.today():
        raise HTTPException(status_code=400, detail="History start date cannot be in the future")
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        master = connection.execute(
            """
            SELECT lifecycle_status FROM platform_subjects
            WHERE tenant_id = %s AND subject_id = 'U15606638'
            """,
            (TENANT_ID,),
        ).fetchone()
        if master is None or str(master["lifecycle_status"]) != "active":
            raise HTTPException(
                status_code=409,
                detail="Complete and activate the separate master-account test first",
            )
        discovery = connection.execute(
            """
            SELECT source_account_id, account_type, base_currency,
                   eligibility_status, nav_start_date, nav_end_date,
                   row_counts_json, source_export_sha256
            FROM onboarding_source_accounts
            WHERE tenant_id = %s AND source_system = 'existing_v11'
              AND source_account_id = %s
            FOR UPDATE
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if discovery is None:
            raise HTTPException(status_code=404, detail="Discovered v1.1 account was not found")
        if str(discovery["eligibility_status"]) != "eligible":
            raise HTTPException(
                status_code=409,
                detail="This source is not eligible for physical-account onboarding",
            )
        existing = connection.execute(
            """
            SELECT lifecycle_status FROM platform_subjects
            WHERE tenant_id = %s AND subject_id = %s FOR UPDATE
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            raise HTTPException(status_code=409, detail="This account is already registered")
        case_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO platform_subjects(
                tenant_id, subject_id, subject_type, display_name,
                source_account_id, account_type, base_currency,
                reporting_currency, history_start_date, lifecycle_status,
                metadata_json, created_at, updated_at
            ) VALUES (
                %s, %s, 'physical_account', %s, %s, %s, %s, %s, %s,
                'onboarding', %s::jsonb, now(), now()
            )
            """,
            (
                TENANT_ID, subject, submission.display_name.strip(), subject,
                discovery["account_type"], discovery["base_currency"],
                submission.reporting_currency, submission.history_start_date,
                canonical_json({"source_system": "existing_v11"}),
            ),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_cases(
                case_id, tenant_id, target_subject_id, source_system,
                source_account_id, stage, status, requested_history_start,
                reporting_currency, configuration_json,
                created_by_identity_sha256, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'existing_v11', %s, 'data_import',
                'in_progress', %s, %s, %s::jsonb, %s, now(), now()
            )
            """,
            (
                case_id, TENANT_ID, subject, subject,
                submission.history_start_date, submission.reporting_currency,
                canonical_json(
                    {
                        "wizard_version": "subject-onboarding-wizard-v5",
                        "source_export_sha256": discovery["source_export_sha256"],
                    }
                ),
                actor,
            ),
        )
        checks = (
            ("source_account_discovered", "pass", "The account is present in the integrity-checked v1.1 source export."),
            ("connection_validated", "pass", "The governed v1.1 production-export connection is validated."),
            ("data_import_reconciled", "pending", "The registry-driven daily sync will import and reconcile this account."),
            ("financial_contract_approved", "pending", "Owner approval of the reporting contract is required."),
            ("external_market_data_approved", "pending", "Owner approval is required before ticker symbols leave the platform."),
            ("presentation_identity_approved", "pending", "Owner approval of the server-derived presentation identity is required."),
            ("report_documents_validated", "pending", "The mandatory three-document set has not been validated."),
            ("risk_analytics_validated", "pending", "Risk analytics will be tested after import."),
            ("historical_reports_backfilled", "pending", "Historical monthly reports must be rendered and approved."),
            ("website_preview_validated", "pending", "The private website preview has not been approved."),
            ("owner_activation", "pending", "Explicit owner activation is required."),
        )
        for check_id, status, message in checks:
            connection.execute(
                """
                INSERT INTO account_onboarding_checks(
                    tenant_id, case_id, check_id, status, message,
                    evidence_json, checked_at
                ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, now())
                """,
                (TENANT_ID, case_id, check_id, status, message),
            )
        for feature_key in (
            "data_ingestion", "website_analytics", "ai_analyst",
            "factsheet", "presentation", "viewer_access",
        ):
            connection.execute(
                """
                INSERT INTO subject_feature_states(
                    tenant_id, subject_id, feature_key, feature_state,
                    reason, updated_at
                ) VALUES (%s, %s, %s, %s, %s, now())
                """,
                (
                    TENANT_ID, subject, feature_key,
                    "preview" if feature_key == "data_ingestion" else "disabled",
                    "Onboarding is fail-closed until every account-specific gate passes.",
                ),
            )
        configuration = {
            "start_date": submission.history_start_date.isoformat(),
            "source": "existing_v11",
        }
        configuration_json = canonical_json(configuration)
        connection.execute(
            """
            INSERT INTO subject_operating_registrations(
                registration_id, tenant_id, subject_id, capability,
                operating_state, effective_from, configuration_json,
                configuration_sha256, changed_by_identity_sha256,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'daily_ingestion', 'preview', %s,
                %s::jsonb, %s, %s, now(), now()
            )
            """,
            (
                uuid.uuid4(), TENANT_ID, subject,
                submission.history_start_date, configuration_json,
                sha256_text(configuration_json), actor,
            ),
        )
    return {
        "status": "in_progress",
        "subject_id": subject,
        "case_id": str(case_id),
        "next_page": f"/account-onboarding/{subject}/steps/connection",
        "message": "The account is registered for the next daily import and remains private.",
    }


def _flex_source_uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError as error:
        raise HTTPException(status_code=404, detail="IBKR connection not found") from error


def _connection_queue_payload(connection, flex_source_id: str) -> dict:
    source = connection.execute(
        """
        SELECT q.flex_source_id, q.display_name, q.query_id, q.status,
               q.schedule_enabled, c.display_name AS credential_name
        FROM ibkr_flex_queries q
        JOIN ibkr_flex_credentials c
          ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
        WHERE q.tenant_id = %s AND q.flex_source_id = %s
        """,
        (TENANT_ID, flex_source_id),
    ).fetchone()
    if source is None:
        raise HTTPException(status_code=404, detail="IBKR connection not found")
    accounts = connection.execute(
        """
        SELECT source_account_id, account_type, base_currency, account_name,
               discovery_status, row_counts_json, first_seen_at, last_seen_at
        FROM ibkr_flex_source_accounts
        WHERE tenant_id = %s AND flex_source_id = %s
        ORDER BY source_account_id
        """,
        (TENANT_ID, flex_source_id),
    ).fetchall()
    queue = connection.execute(
        """
        SELECT queue_id, queue_status, created_at, updated_at, completed_at
        FROM account_onboarding_queues
        WHERE tenant_id = %s AND flex_source_id = %s
          AND queue_status IN ('draft', 'active')
        ORDER BY created_at DESC LIMIT 1
        """,
        (TENANT_ID, flex_source_id),
    ).fetchone()
    items = []
    if queue is not None:
        items = connection.execute(
            """
            SELECT i.queue_item_id, i.source_account_id, i.sequence_number,
                   i.item_status, i.onboarding_case_id, i.started_at,
                   i.completed_at, c.stage AS onboarding_stage,
                   c.status AS onboarding_case_status, c.target_subject_id
            FROM account_onboarding_queue_items i
            LEFT JOIN account_onboarding_cases c
              ON c.case_id = i.onboarding_case_id AND c.tenant_id = i.tenant_id
            WHERE i.tenant_id = %s AND i.queue_id = %s
            ORDER BY i.sequence_number
            """,
            (TENANT_ID, queue["queue_id"]),
        ).fetchall()
    active_accounts = connection.execute(
        """
        SELECT DISTINCT b.source_account_id
        FROM subject_flex_source_bindings b
        JOIN platform_subjects s
          ON s.tenant_id = b.tenant_id AND s.subject_id = b.subject_id
        WHERE b.tenant_id = %s AND b.flex_source_id = %s
          AND b.status = 'active' AND s.lifecycle_status = 'active'
        """,
        (TENANT_ID, flex_source_id),
    ).fetchall()
    classified = classify_connection_accounts(
        [dict(row) for row in accounts],
        [dict(row) for row in items],
        [row["source_account_id"] for row in active_accounts],
    )
    groups = {
        key: [row for row in classified if row["onboarding_group"] == key]
        for key in ("active", "onboarding_now", "queued", "available_later")
    }
    return {
        "connection": dict(source),
        "queue": dict(queue) if queue else None,
        "queue_items": [dict(row) for row in items],
        "accounts": classified,
        "groups": groups,
        "policy": {
            "execution": "strictly_sequential",
            "maximum_current_accounts": 1,
            "unlock_condition": "current_onboarding_case_status_active",
            "unselected_account_policy": "retain_as_available_later",
        },
    }


@app.get("/api/onboarding/flex-sources/{flex_source_id}/queue")
def connection_onboarding_queue(
    flex_source_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    source_id = _flex_source_uuid(flex_source_id)
    with _database() as connection:
        return _connection_queue_payload(connection, source_id)


@app.post("/api/onboarding/flex-sources/{flex_source_id}/queue")
def create_connection_onboarding_queue(
    flex_source_id: str,
    submission: OnboardingQueueCreateRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Create the immutable starting order for one connection's sequential queue."""

    source_id = _flex_source_uuid(flex_source_id)
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        source = connection.execute(
            """
            SELECT status FROM ibkr_flex_queries
            WHERE tenant_id = %s AND flex_source_id = %s
            FOR UPDATE
            """,
            (TENANT_ID, source_id),
        ).fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="IBKR connection not found")
        if source["status"] not in {"validated", "active"}:
            raise HTTPException(
                status_code=409,
                detail="Validate the connection and discover its accounts before creating a queue",
            )
        discovered = connection.execute(
            """
            SELECT source_account_id, discovery_status
            FROM ibkr_flex_source_accounts
            WHERE tenant_id = %s AND flex_source_id = %s
            ORDER BY source_account_id
            """,
            (TENANT_ID, source_id),
        ).fetchall()
        try:
            ordered = normalize_account_order(
                submission.account_ids,
                discovered_account_ids=[row["source_account_id"] for row in discovered],
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        prohibited = {
            row["source_account_id"]
            for row in discovered
            if row["discovery_status"] in {"ignored", "not_portfolio"}
        }
        if prohibited.intersection(ordered):
            raise HTTPException(
                status_code=409,
                detail="Ignored and non-portfolio accounts require review before selection",
            )
        existing = connection.execute(
            """
            SELECT queue_id FROM account_onboarding_queues
            WHERE tenant_id = %s AND flex_source_id = %s
              AND queue_status IN ('draft', 'active')
            FOR UPDATE
            """,
            (TENANT_ID, source_id),
        ).fetchone()
        if existing is not None:
            current_order = connection.execute(
                """
                SELECT source_account_id
                FROM account_onboarding_queue_items
                WHERE tenant_id = %s AND queue_id = %s
                ORDER BY sequence_number
                """,
                (TENANT_ID, existing["queue_id"]),
            ).fetchall()
            if tuple(row["source_account_id"] for row in current_order) == ordered:
                return {
                    "status": "unchanged",
                    "queue_id": str(existing["queue_id"]),
                    "ordered_accounts": list(ordered),
                }
            raise HTTPException(
                status_code=409,
                detail="This connection already has an open queue; finish it before creating another",
            )
        queue_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO account_onboarding_queues(
                queue_id, tenant_id, flex_source_id, queue_status,
                created_by_identity_sha256, created_at, updated_at
            ) VALUES (%s, %s, %s, 'active', %s, now(), now())
            """,
            (queue_id, TENANT_ID, source_id, actor),
        )
        for item in initial_queue_items(ordered):
            case = connection.execute(
                """
                SELECT case_id FROM account_onboarding_cases
                WHERE tenant_id = %s AND source_account_id = %s
                ORDER BY updated_at DESC LIMIT 1
                """,
                (TENANT_ID, item.source_account_id),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO account_onboarding_queue_items(
                    queue_item_id, tenant_id, queue_id, flex_source_id,
                    source_account_id, sequence_number, item_status,
                    onboarding_case_id, started_at, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    CASE WHEN %s = 'current' THEN now() ELSE NULL END,
                    now(), now()
                )
                """,
                (
                    uuid.uuid4(), TENANT_ID, queue_id, source_id,
                    item.source_account_id, item.position, item.state,
                    case["case_id"] if case else None, item.state,
                ),
            )
        connection.execute(
            """
            UPDATE ibkr_flex_source_accounts
            SET discovery_status = 'selected', last_seen_at = last_seen_at
            WHERE tenant_id = %s AND flex_source_id = %s
              AND source_account_id = ANY(%s)
            """,
            (TENANT_ID, source_id, list(ordered)),
        )
    return {
        "status": "active",
        "queue_id": str(queue_id),
        "ordered_accounts": list(ordered),
        "current_account": ordered[0],
        "queued_accounts": list(ordered[1:]),
    }


@app.post("/api/onboarding/flex-sources/{flex_source_id}/queue/current/start")
def start_current_physical_account_onboarding(
    flex_source_id: str,
    submission: OnboardingPhysicalAccountStartRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Create the governed case for the one current physical account."""

    source_id = _flex_source_uuid(flex_source_id)
    if submission.history_start_date > date.today():
        raise HTTPException(status_code=400, detail="History start date cannot be in the future")
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        current = connection.execute(
            """
            SELECT i.queue_item_id, i.queue_id, i.source_account_id,
                   i.onboarding_case_id, a.account_type, a.base_currency,
                   q.status AS source_status
            FROM account_onboarding_queue_items i
            JOIN account_onboarding_queues oq
              ON oq.tenant_id = i.tenant_id AND oq.queue_id = i.queue_id
            JOIN ibkr_flex_source_accounts a
              ON a.tenant_id = i.tenant_id
             AND a.flex_source_id = i.flex_source_id
             AND a.source_account_id = i.source_account_id
            JOIN ibkr_flex_queries q
              ON q.tenant_id = i.tenant_id AND q.flex_source_id = i.flex_source_id
            WHERE i.tenant_id = %s AND i.flex_source_id = %s
              AND oq.queue_status = 'active' AND i.item_status = 'current'
            FOR UPDATE OF i, oq
            """,
            (TENANT_ID, source_id),
        ).fetchone()
        if current is None:
            raise HTTPException(status_code=409, detail="No current account is ready to start")
        if current["source_status"] not in {"validated", "active"}:
            raise HTTPException(status_code=409, detail="The IBKR connection is not validated")
        if current["onboarding_case_id"] is not None:
            existing = connection.execute(
                """
                SELECT target_subject_id FROM account_onboarding_cases
                WHERE tenant_id = %s AND case_id = %s
                """,
                (TENANT_ID, current["onboarding_case_id"]),
            ).fetchone()
            return {
                "status": "already_started",
                "subject_id": existing["target_subject_id"],
            }
        subject_id = str(current["source_account_id"])
        existing_subject = connection.execute(
            """
            SELECT lifecycle_status FROM platform_subjects
            WHERE tenant_id = %s AND subject_id = %s
            FOR UPDATE
            """,
            (TENANT_ID, subject_id),
        ).fetchone()
        if existing_subject is not None:
            raise HTTPException(
                status_code=409,
                detail="This account already exists outside the current onboarding queue",
            )
        case_id = uuid.uuid4()
        connection.execute(
            """
            INSERT INTO platform_subjects(
                tenant_id, subject_id, subject_type, display_name,
                source_account_id, account_type, base_currency,
                reporting_currency, history_start_date, lifecycle_status,
                metadata_json, created_at, updated_at
            ) VALUES (
                %s, %s, 'physical_account', %s, %s, %s, %s, %s, %s,
                'onboarding', %s::jsonb, now(), now()
            )
            """,
            (
                TENANT_ID, subject_id, submission.display_name.strip(), subject_id,
                current["account_type"], current["base_currency"],
                submission.reporting_currency, submission.history_start_date,
                json.dumps(
                    {"source_system": "ibkr_flex", "flex_source_id": source_id},
                    sort_keys=True,
                ),
            ),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_cases(
                case_id, tenant_id, target_subject_id, source_system,
                source_account_id, stage, status, requested_history_start,
                reporting_currency, configuration_json,
                created_by_identity_sha256, created_at, updated_at
            ) VALUES (
                %s, %s, %s, 'ibkr_flex', %s, 'financial_contract',
                'in_progress', %s, %s, %s::jsonb, %s, now(), now()
            )
            """,
            (
                case_id, TENANT_ID, subject_id, subject_id,
                submission.history_start_date, submission.reporting_currency,
                json.dumps(
                    {
                        "wizard_version": "subject-onboarding-wizard-v4",
                        "queue_id": str(current["queue_id"]),
                        "financial_contract_status": "owner_confirmation_required",
                    },
                    sort_keys=True,
                ),
                actor,
            ),
        )
        checks = (
            ("source_account_discovered", "pass", "The account was discovered by the selected validated Flex connection."),
            ("connection_validated", "pass", "The selected IBKR Flex connection passed controlled validation."),
            ("data_import_reconciled", "pending", "Waiting for historical import and row-count reconciliation."),
            ("financial_contract_approved", "pending", "Owner approval of the account reporting contract is required."),
            ("external_market_data_approved", "pending", "Owner approval is required before ticker symbols leave the platform."),
            ("presentation_identity_approved", "pending", "Owner approval of the server-derived presentation identity is required."),
            ("report_documents_validated", "pending", "The mandatory three-document set has not been validated."),
            ("risk_analytics_validated", "pending", "Risk analytics will be tested after data reconciliation."),
            ("historical_reports_backfilled", "pending", "Historical monthly reports must be rendered and approved."),
            ("website_preview_validated", "pending", "The private website preview has not been approved."),
            ("owner_activation", "pending", "Explicit owner activation is required after every other gate passes."),
        )
        for check_id, status, message in checks:
            connection.execute(
                """
                INSERT INTO account_onboarding_checks(
                    tenant_id, case_id, check_id, status, message,
                    evidence_json, checked_at
                ) VALUES (%s, %s, %s, %s, %s, '{}'::jsonb, now())
                """,
                (TENANT_ID, case_id, check_id, status, message),
            )
        imported = connection.execute(
            """
            SELECT MIN(snapshot_date) AS first_date,
                   MAX(snapshot_date) AS latest_date,
                   COUNT(DISTINCT snapshot_date) AS observations,
                   (SELECT COUNT(*) FROM position_snapshots WHERE account_id = %s) AS position_rows
            FROM nav_snapshots
            WHERE account_id = %s AND asset_class = 'TOTAL'
            """,
            (subject_id, subject_id),
        ).fetchone()
        if imported is not None and imported["latest_date"] is not None:
            connection.execute(
                """
                UPDATE account_onboarding_checks
                SET status = 'pass',
                    message = 'The controlled Flex validation imported and reconciled the account history.',
                    evidence_json = %s::jsonb, checked_at = now()
                WHERE tenant_id = %s AND case_id = %s
                  AND check_id = 'data_import_reconciled'
                """,
                (canonical_json(dict(imported)), TENANT_ID, case_id),
            )
        for feature_key in (
            "data_ingestion", "website_analytics", "ai_analyst",
            "factsheet", "presentation", "viewer_access",
        ):
            state = "preview" if feature_key == "data_ingestion" else "disabled"
            connection.execute(
                """
                INSERT INTO subject_feature_states(
                    tenant_id, subject_id, feature_key, feature_state,
                    reason, updated_at
                ) VALUES (%s, %s, %s, %s, %s, now())
                """,
                (
                    TENANT_ID, subject_id, feature_key, state,
                    "Onboarding is fail-closed until every account-specific gate passes.",
                ),
            )
        connection.execute(
            """
            INSERT INTO subject_flex_source_bindings(
                tenant_id, subject_id, flex_source_id, source_account_id,
                effective_from, is_primary, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, true, 'pending', now(), now())
            """,
            (
                TENANT_ID, subject_id, source_id, subject_id,
                submission.history_start_date,
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_queue_items
            SET onboarding_case_id = %s, updated_at = now()
            WHERE tenant_id = %s AND queue_item_id = %s
            """,
            (case_id, TENANT_ID, current["queue_item_id"]),
        )
    return {
        "status": "in_progress",
        "subject_id": subject_id,
        "case_id": str(case_id),
        "next_page": f"/account-onboarding/{subject_id}/steps/financial-contract",
    }


@app.post("/api/onboarding/flex-sources/{flex_source_id}/queue/advance")
def advance_connection_onboarding_queue(
    flex_source_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Unlock one next account only after the current case is fully active."""

    source_id = _flex_source_uuid(flex_source_id)
    with _approval_database() as connection:
        queue = connection.execute(
            """
            SELECT queue_id FROM account_onboarding_queues
            WHERE tenant_id = %s AND flex_source_id = %s AND queue_status = 'active'
            FOR UPDATE
            """,
            (TENANT_ID, source_id),
        ).fetchone()
        if queue is None:
            raise HTTPException(status_code=404, detail="No active onboarding queue exists")
        rows = connection.execute(
            """
            SELECT i.source_account_id, i.sequence_number, i.item_status,
                   c.status AS onboarding_case_status
            FROM account_onboarding_queue_items i
            LEFT JOIN account_onboarding_cases c
              ON c.case_id = i.onboarding_case_id AND c.tenant_id = i.tenant_id
            WHERE i.tenant_id = %s AND i.queue_id = %s
            ORDER BY i.sequence_number
            FOR UPDATE OF i
            """,
            (TENANT_ID, queue["queue_id"]),
        ).fetchall()
        try:
            advanced = next_queue_state(
                tuple(
                    QueueAccount(
                        source_account_id=row["source_account_id"],
                        position=row["sequence_number"],
                        state=row["item_status"],
                        onboarding_case_status=row["onboarding_case_status"],
                    )
                    for row in rows
                )
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        for item in advanced:
            connection.execute(
                """
                UPDATE account_onboarding_queue_items
                SET item_status = %s,
                    started_at = CASE WHEN %s = 'current' THEN now() ELSE started_at END,
                    completed_at = CASE WHEN %s = 'completed' THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE tenant_id = %s AND queue_id = %s AND source_account_id = %s
                """,
                (
                    item.state, item.state, item.state, TENANT_ID,
                    queue["queue_id"], item.source_account_id,
                ),
            )
        current = next((item for item in advanced if item.state == "current"), None)
        if current is None:
            connection.execute(
                """
                UPDATE account_onboarding_queues
                SET queue_status = 'completed', completed_at = now(), updated_at = now()
                WHERE tenant_id = %s AND queue_id = %s
                """,
                (TENANT_ID, queue["queue_id"]),
            )
    return {
        "status": "active" if current else "completed",
        "current_account": current.source_account_id if current else None,
    }


def _advance_to_template_intake_if_ready(connection, case_id: uuid.UUID) -> None:
    """Advance only when both owner-controlled configuration gates passed."""

    connection.execute(
        """
        UPDATE account_onboarding_cases c
        SET stage = 'template_intake', status = 'in_progress', updated_at = now()
        WHERE c.tenant_id = %s AND c.case_id = %s
          AND c.stage IN ('analytics_configuration', 'template_intake')
          AND EXISTS (
              SELECT 1 FROM account_onboarding_checks k
              WHERE k.tenant_id = c.tenant_id AND k.case_id = c.case_id
                AND k.check_id = 'financial_contract_approved' AND k.status = 'pass'
          )
          AND EXISTS (
              SELECT 1 FROM account_onboarding_checks k
              WHERE k.tenant_id = c.tenant_id AND k.case_id = c.case_id
                AND k.check_id = 'external_market_data_approved' AND k.status = 'pass'
          )
          AND EXISTS (
              SELECT 1 FROM account_onboarding_checks k
              WHERE k.tenant_id = c.tenant_id AND k.case_id = c.case_id
                AND k.check_id = 'presentation_identity_approved' AND k.status = 'pass'
          )
        """,
        (TENANT_ID, case_id),
    )


@app.post("/api/onboarding/{subject_id}/approvals/financial-contract")
def approve_onboarding_financial_contract(
    subject_id: str,
    submission: OnboardingFinancialContractApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Approve and lock the account reporting and benchmark contract."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    secondary_ids = list(dict.fromkeys(submission.secondary_benchmark_ids))
    if submission.primary_benchmark_id in secondary_ids:
        raise HTTPException(
            status_code=400,
            detail="The primary benchmark cannot also be a secondary benchmark",
        )
    benchmark_ids = [submission.primary_benchmark_id, *secondary_ids]
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    with _approval_database() as connection:
        case = connection.execute(
            """
            SELECT c.case_id, c.stage, c.status, c.source_account_id,
                   s.base_currency
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            FOR UPDATE OF c, s
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Onboarding case was not found")
        if str(case["status"]) in {"active", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail="Financial-contract approval is unavailable at the current stage",
            )
        benchmark_rows = connection.execute(
            """
            SELECT benchmark_id, symbol, name, currency, return_convention, provider
            FROM benchmark_master
            WHERE tenant_id = %s AND active AND benchmark_id = ANY(%s)
            ORDER BY benchmark_id
            """,
            (TENANT_ID, benchmark_ids),
        ).fetchall()
        found = {str(row["benchmark_id"]): dict(row) for row in benchmark_rows}
        missing = [benchmark_id for benchmark_id in benchmark_ids if benchmark_id not in found]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"code": "benchmark_not_available", "benchmark_ids": missing},
            )
        scope = {
            "schema_version": 1,
            "subject_id": subject,
            "source_account_id": str(case["source_account_id"]),
            "display_name": submission.display_name.strip(),
            "base_currency": str(case["base_currency"] or ""),
            "reporting_currency": submission.reporting_currency,
            "history_start_date": submission.history_start_date.isoformat(),
            "primary_benchmark": found[submission.primary_benchmark_id],
            "secondary_benchmarks": [found[item] for item in secondary_ids],
        }
        scope_json = canonical_json(scope)
        scope_sha256 = sha256_text(scope_json)
        existing = connection.execute(
            """
            SELECT approval_id, scope_json, scope_sha256, approved_at
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND subject_id = %s
              AND approval_type = 'financial_contract'
              AND approval_status = 'approved'
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            if str(existing["scope_sha256"]) != scope_sha256:
                raise HTTPException(
                    status_code=409,
                    detail="A different financial contract is already approved and cannot be silently replaced",
                )
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "scope": existing["scope_json"],
                "scope_sha256": str(existing["scope_sha256"]),
                "approved_at": existing["approved_at"],
            }
        connection.execute(
            """
            UPDATE platform_subjects
            SET display_name = %s, reporting_currency = %s,
                history_start_date = %s,
                inception_date = COALESCE(inception_date, %s), updated_at = now()
            WHERE tenant_id = %s AND subject_id = %s
            """,
            (
                submission.display_name.strip(), submission.reporting_currency,
                submission.history_start_date, submission.history_start_date,
                TENANT_ID, subject,
            ),
        )
        connection.execute(
            """
            UPDATE portfolio_benchmark_mapping
            SET effective_to = %s - 1
            WHERE tenant_id = %s AND subject_id = %s AND effective_to IS NULL
              AND effective_from < %s
            """,
            (
                submission.history_start_date, TENANT_ID, subject,
                submission.history_start_date,
            ),
        )
        for mapping_type, benchmark_id in [
            ("primary", submission.primary_benchmark_id),
            *[("secondary", item) for item in secondary_ids],
        ]:
            connection.execute(
                """
                INSERT INTO portfolio_benchmark_mapping(
                    tenant_id, subject_id, benchmark_id, mapping_type,
                    effective_from, effective_to, reporting_currency, created_at
                ) VALUES (%s, %s, %s, %s, %s, NULL, %s, now())
                ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from)
                DO UPDATE SET mapping_type = EXCLUDED.mapping_type,
                              effective_to = NULL,
                              reporting_currency = EXCLUDED.reporting_currency
                """,
                (
                    TENANT_ID, subject, benchmark_id, mapping_type,
                    submission.history_start_date, submission.reporting_currency,
                ),
            )
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'financial_contract', 'approved',
                'physical-account-financial-contract-v1',
                'WealthRyse portfolio analytics platform',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id, TENANT_ID, case["case_id"], subject, scope_json,
                scope_sha256, actor,
                canonical_json({"channel": "website_onboarding_wizard"}),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner-approved reporting and benchmark contract is locked.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'financial_contract_approved'
            """,
            (scope_json, TENANT_ID, case["case_id"]),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET reporting_currency = %s, requested_history_start = %s,
                stage = 'analytics_configuration', status = 'in_progress',
                configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
            """,
            (
                submission.reporting_currency, submission.history_start_date,
                canonical_json(
                    {"financial_contract": {"scope_sha256": scope_sha256, **scope}}
                ),
                TENANT_ID, case["case_id"],
            ),
        )
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "scope": scope,
        "scope_sha256": scope_sha256,
        "next_stage": "analytics_configuration",
    }


@app.post("/api/onboarding/{subject_id}/approvals/derived-contract")
def approve_onboarding_derived_contract(
    subject_id: str,
    submission: OnboardingDerivedContractApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Approve an immutable typed calculation plus its comparison benchmarks."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    secondary_ids = list(dict.fromkeys(submission.secondary_benchmark_ids))
    if submission.primary_benchmark_id in secondary_ids:
        raise HTTPException(
            status_code=400,
            detail="The primary benchmark cannot also be a secondary benchmark",
        )
    benchmark_ids = [submission.primary_benchmark_id, *secondary_ids]
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        row = connection.execute(
            """
            SELECT c.case_id, c.status, c.requested_history_start,
                   s.subject_type, s.display_name, s.base_currency,
                   d.definition_id, d.configuration_json,
                   d.configuration_sha256, d.approval_status
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            JOIN subject_calculation_definitions d
              ON d.tenant_id = c.tenant_id AND d.subject_id = c.target_subject_id
             AND d.effective_to IS NULL
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
              AND c.source_system = 'derived_definition'
            FOR UPDATE OF c, s, d
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Derived onboarding case was not found")
        if str(row["configuration_sha256"]) != submission.definition_sha256:
            raise HTTPException(
                status_code=409,
                detail="The calculation definition changed; reload and review it again",
            )
        benchmark_rows = connection.execute(
            """
            SELECT benchmark_id, symbol, name, currency, return_convention, provider
            FROM benchmark_master
            WHERE tenant_id = %s AND active AND benchmark_id = ANY(%s)
            ORDER BY benchmark_id
            """,
            (TENANT_ID, benchmark_ids),
        ).fetchall()
        found = {str(item["benchmark_id"]): dict(item) for item in benchmark_rows}
        missing = [value for value in benchmark_ids if value not in found]
        if missing:
            raise HTTPException(
                status_code=409,
                detail={"code": "benchmark_not_available", "benchmark_ids": missing},
            )
        definition = row["configuration_json"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        scope = {
            "schema_version": 1,
            "subject_id": subject,
            "subject_type": str(row["subject_type"]),
            "display_name": str(row["display_name"]),
            "reporting_currency": str(row["base_currency"]),
            "history_start_date": str(row["requested_history_start"]),
            "definition_sha256": submission.definition_sha256,
            "definition": definition,
            "primary_benchmark": found[submission.primary_benchmark_id],
            "secondary_benchmarks": [found[value] for value in secondary_ids],
        }
        scope_json = canonical_json(scope)
        scope_digest = sha256_text(scope_json)
        existing = connection.execute(
            """
            SELECT approval_id, scope_sha256, approved_at
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND subject_id = %s
              AND approval_type = 'calculation_contract'
              AND approval_status = 'approved'
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            if str(existing["scope_sha256"]) != scope_digest:
                raise HTTPException(
                    status_code=409,
                    detail="A different calculation contract is already approved",
                )
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "scope_sha256": scope_digest,
            }
        approval_id = uuid.uuid4()
        connection.execute(
            """
            UPDATE subject_calculation_definitions
            SET approval_status = 'approved',
                approved_by_identity_sha256 = %s,
                approved_at = now()
            WHERE tenant_id = %s AND definition_id = %s
              AND approval_status = 'validated'
            """,
            (actor, TENANT_ID, row["definition_id"]),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'calculation_contract', 'approved',
                'typed-derived-calculation-v1',
                'WealthRyse deterministic analytics engine',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id, TENANT_ID, row["case_id"], subject, scope_json,
                scope_digest, actor,
                canonical_json({"channel": "website_onboarding_wizard"}),
            ),
        )
        for mapping_type, benchmark_id in [
            ("primary", submission.primary_benchmark_id),
            *[("secondary", value) for value in secondary_ids],
        ]:
            connection.execute(
                """
                INSERT INTO portfolio_benchmark_mapping(
                    tenant_id, subject_id, benchmark_id, mapping_type,
                    effective_from, reporting_currency, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from)
                DO UPDATE SET mapping_type = EXCLUDED.mapping_type,
                              effective_to = NULL,
                              reporting_currency = EXCLUDED.reporting_currency
                """,
                (
                    TENANT_ID, subject, benchmark_id, mapping_type,
                    row["requested_history_start"], row["base_currency"],
                ),
            )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = CASE check_id
                    WHEN 'calculation_definition_approved' THEN
                        'The owner approved the exact versioned deterministic calculation.'
                    ELSE 'The owner approved the derived reporting and benchmark contract.'
                END,
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id IN (
                  'calculation_definition_approved', 'financial_contract_approved'
              )
            """,
            (scope_json, TENANT_ID, row["case_id"]),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = 'analytics_configuration', status = 'in_progress',
                configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
            """,
            (
                canonical_json(
                    {"calculation_contract": {"scope_sha256": scope_digest}}
                ),
                TENANT_ID, row["case_id"],
            ),
        )
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "scope_sha256": scope_digest,
        "next_stage": "analytics_configuration",
    }


@app.post("/api/onboarding/{subject_id}/approvals/master-contract")
def approve_onboarding_master_contract(
    subject_id: str,
    submission: OnboardingMasterContractApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Approve the exact master mode and its anti-double-counting policy."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not ADMIN_ACTION_CONFIG.subscription_id or not ADMIN_ACTION_CONFIG.resource_group:
        raise HTTPException(status_code=503, detail="The Azure analytics runner is unavailable")
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        row = connection.execute(
            """
            SELECT c.case_id, c.status, s.display_name, s.reporting_currency,
                   d.definition_id, d.configuration_json,
                   d.configuration_sha256, d.approval_status
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            JOIN master_account_definitions d
              ON d.tenant_id = c.tenant_id AND d.subject_id = c.target_subject_id
             AND d.effective_to IS NULL
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
              AND c.source_system = 'master_definition'
            FOR UPDATE OF c, s, d
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Master onboarding case was not found")
        if str(row["configuration_sha256"]) != submission.definition_sha256:
            raise HTTPException(
                status_code=409,
                detail="The master definition changed; reload and review it again",
            )
        definition = row["configuration_json"]
        if isinstance(definition, str):
            definition = json.loads(definition)
        scope = {
            "schema_version": 1,
            "subject_id": subject,
            "subject_type": "master_account",
            "display_name": str(row["display_name"]),
            "reporting_currency": str(row["reporting_currency"]),
            "definition_sha256": submission.definition_sha256,
            "definition": definition,
        }
        scope_json = canonical_json(scope)
        scope_digest = sha256_text(scope_json)
        existing = connection.execute(
            """
            SELECT approval_id, scope_sha256
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND subject_id = %s
              AND approval_type = 'calculation_contract'
              AND approval_status = 'approved'
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            if str(existing["scope_sha256"]) != scope_digest:
                raise HTTPException(
                    status_code=409,
                    detail="A different master contract is already approved",
                )
            return {
                "status": "already_approved",
                "subject_id": subject,
                "approval_id": str(existing["approval_id"]),
                "scope_sha256": scope_digest,
            }
        approval_id = uuid.uuid4()
        connection.execute(
            """
            UPDATE master_account_definitions
            SET approval_status = 'approved', approved_by_identity_sha256 = %s,
                approved_at = now()
            WHERE tenant_id = %s AND definition_id = %s
              AND approval_status = 'validated'
            """,
            (actor, TENANT_ID, row["definition_id"]),
        )
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'calculation_contract', 'approved',
                'typed-master-account-v1', 'WealthRyse deterministic control plane',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id, TENANT_ID, row["case_id"], subject, scope_json,
                scope_digest, actor,
                canonical_json({"channel": "website_onboarding_wizard"}),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = CASE check_id
                    WHEN 'calculation_definition_approved' THEN
                        'The owner approved the exact master definition and aggregation mode.'
                    ELSE
                        'The owner approved the master reporting and anti-double-counting contract.'
                END,
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id IN ('calculation_definition_approved', 'financial_contract_approved')
            """,
            (scope_json, TENANT_ID, row["case_id"]),
        )
        administrative = definition.get("reporting_mode") == "administrative_only"
        next_stage = "website_preview" if administrative else "reconciliation"
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = %s, updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND stage = 'financial_contract'
            """,
            (next_stage, TENANT_ID, row["case_id"]),
        )
        if administrative:
            connection.execute(
                """
                UPDATE platform_subjects
                SET lifecycle_status = 'preview', updated_at = now()
                WHERE tenant_id = %s AND subject_id = %s
                  AND lifecycle_status = 'onboarding'
                """,
                (TENANT_ID, subject),
            )
    return {
        "status": "approved",
        "subject_id": subject,
        "approval_id": str(approval_id),
        "scope_sha256": scope_digest,
        "next_stage": next_stage,
    }


@app.post("/api/onboarding/{subject_id}/approvals/presentation-identity")
def approve_onboarding_presentation_identity(
    subject_id: str,
    submission: OnboardingPresentationIdentityApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Lock a server-derived strategy identity before any document upload."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    with _approval_database() as connection:
        case = connection.execute(
            """
            SELECT c.case_id, c.stage, c.status, s.display_name
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id
             AND s.subject_id = c.target_subject_id
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            FOR UPDATE OF c, s
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Onboarding case was not found")
        if str(case["status"]) in {"cancelled", "active"}:
            raise HTTPException(
                status_code=409,
                detail="Presentation identity approval is unavailable at the current stage",
            )
        financial_contract = connection.execute(
            """
            SELECT status
            FROM account_onboarding_checks
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'financial_contract_approved'
            """,
            (TENANT_ID, case["case_id"]),
        ).fetchone()
        if financial_contract is None or financial_contract["status"] != "pass":
            raise HTTPException(
                status_code=409,
                detail="The owner-approved financial contract must pass first",
            )
        try:
            scope = presentation_identity_scope(
                subject_id=subject,
                display_name=str(case["display_name"]),
                layout_policy=submission.layout_policy,
            )
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        display_digest = sha256_text(str(scope["source_display_name"]))
        if submission.display_name_sha256 != display_digest:
            raise HTTPException(
                status_code=409,
                detail="The account name changed; reload and review the derived title",
            )
        scope_json = canonical_json(scope)
        scope_sha256 = sha256_text(scope_json)
        existing = connection.execute(
            """
            SELECT approval_id, scope_json, scope_sha256, approved_at
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND subject_id = %s
              AND approval_type = 'presentation_identity'
              AND approval_status = 'approved'
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            if str(existing["scope_sha256"]) != scope_sha256:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A different owner-approved presentation identity already exists. "
                        "It cannot be silently replaced after approval."
                    ),
                )
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "scope": existing["scope_json"],
                "scope_sha256": str(existing["scope_sha256"]),
                "approved_at": existing["approved_at"],
            }
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'presentation_identity', 'approved',
                'presentation-identity-v1', 'Internal report renderer',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id,
                TENANT_ID,
                case["case_id"],
                subject,
                scope_json,
                scope_sha256,
                actor,
                canonical_json(
                    {
                        "channel": "website_onboarding_wizard",
                        "title_source": "owner_approved_subject_display_name",
                        "uploader_editable": False,
                    }
                ),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
            """,
            (
                canonical_json(
                    {
                        "presentation_identity_approval": {
                            "approval_id": str(approval_id),
                            "scope_sha256": scope_sha256,
                            "strategy_identity_sha256": scope[
                                "strategy_identity_sha256"
                            ],
                        }
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner-approved presentation identity is locked and cannot be edited by document uploaders.',
                evidence_json = %s::jsonb,
                checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'presentation_identity_approved'
            """,
            (
                canonical_json(
                    {
                        "approval_id": str(approval_id),
                        "scope_sha256": scope_sha256,
                        "strategy_name": scope["strategy_name"],
                        "layout_policy": scope["layout_policy"],
                        "uploader_editable": False,
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
        _advance_to_template_intake_if_ready(connection, case["case_id"])
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "scope": scope,
        "scope_sha256": scope_sha256,
        "message": (
            "The owner-controlled presentation identity is locked. Document "
            "uploaders cannot edit the strategy title or layout policy."
        ),
    }


@app.post("/api/onboarding/{subject_id}/approvals/external-market-data")
def approve_onboarding_external_market_data(
    subject_id: str,
    submission: OnboardingExternalDataApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Record an integrity-bound owner approval without transmitting any ticker yet."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if (
        submission.disclosure_version != YAHOO_ONBOARDING_DISCLOSURE_VERSION
        or submission.scope_sha256 != YAHOO_ONBOARDING_SCOPE_SHA256
    ):
        raise HTTPException(
            status_code=409,
            detail="The disclosure changed; reload and review the current scope",
        )
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    scope_json = canonical_json(YAHOO_ONBOARDING_SCOPE)
    with _approval_database() as connection:
        case = connection.execute(
            """
            SELECT case_id, stage, status
            FROM account_onboarding_cases
            WHERE tenant_id = %s AND target_subject_id = %s
            FOR UPDATE
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Onboarding case was not found")
        if case["status"] in {"cancelled", "active"}:
            raise HTTPException(
                status_code=409,
                detail="External-data approval is unavailable at the current stage",
            )
        existing = connection.execute(
            """
            SELECT approval_id, disclosure_version, scope_sha256, approved_at
            FROM account_onboarding_approvals
            WHERE tenant_id = %s AND subject_id = %s
              AND approval_type = 'external_market_data'
              AND approval_status = 'approved'
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if existing is not None:
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "disclosure_version": str(existing["disclosure_version"]),
                "scope_sha256": str(existing["scope_sha256"]),
                "approved_at": existing["approved_at"],
                "transmission_started": False,
            }
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'external_market_data', 'approved', %s,
                'Yahoo Finance via yfinance', %s::jsonb, %s, %s, now(),
                %s::jsonb
            )
            """,
            (
                approval_id,
                TENANT_ID,
                case["case_id"],
                subject,
                YAHOO_ONBOARDING_DISCLOSURE_VERSION,
                scope_json,
                YAHOO_ONBOARDING_SCOPE_SHA256,
                actor,
                canonical_json({"channel": "website_onboarding_wizard"}),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner approved the bounded Yahoo Finance ticker-symbol transmission.',
                evidence_json = %s::jsonb,
                checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'external_market_data_approved'
            """,
            (
                canonical_json(
                    {
                        "approval_id": str(approval_id),
                        "disclosure_version": YAHOO_ONBOARDING_DISCLOSURE_VERSION,
                        "destination": "Yahoo Finance via yfinance",
                        "scope_sha256": YAHOO_ONBOARDING_SCOPE_SHA256,
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
            """,
            (
                canonical_json(
                    {
                        "external_market_data_approval": {
                            "approval_id": str(approval_id),
                            "disclosure_version": YAHOO_ONBOARDING_DISCLOSURE_VERSION,
                            "scope_sha256": YAHOO_ONBOARDING_SCOPE_SHA256,
                        }
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
        _advance_to_template_intake_if_ready(connection, case["case_id"])
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "disclosure_version": YAHOO_ONBOARDING_DISCLOSURE_VERSION,
        "scope_sha256": YAHOO_ONBOARDING_SCOPE_SHA256,
        "transmission_started": False,
        "message": (
            "Approval recorded. The controlled market-data jobs may now process "
            "this account; no ticker was transmitted by the approval request itself."
        ),
    }


def _store_flex_token_via_broker(secret_name: str, token: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(CREDENTIAL_BROKER_URL)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".azurecontainerapps.io")
        or len(CREDENTIAL_BROKER_AUTH) < 32
    ):
        raise RuntimeError("Credential broker is not configured")
    request = urllib.request.Request(
        f"{CREDENTIAL_BROKER_URL}/v1/credentials",
        data=json.dumps(
            {"secret_name": secret_name, "token": token}, separators=(",", ":")
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {CREDENTIAL_BROKER_AUTH}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.load(response)
    if (
        payload.get("status") != "stored"
        or payload.get("secret_name") != secret_name
        or not re.fullmatch(r"[0-9a-f]{16}", str(payload.get("token_fingerprint") or ""))
    ):
        raise RuntimeError("Credential broker response failed validation")
    return payload


@app.post("/api/onboarding/flex-sources")
def create_flex_source(
    submission: FlexSourceCreateRequest,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Register one query source while keeping the submitted token out of PostgreSQL."""

    credential_name = " ".join((submission.credential_name or "").split())
    source_name = " ".join(submission.source_name.split())
    token = submission.token.get_secret_value().strip() if submission.token else ""
    existing_credential = None
    with _database() as connection:
        if submission.credential_id:
            existing_credential = connection.execute(
                """
                SELECT credential_id, display_name, key_vault_secret_name,
                       token_fingerprint, status
                FROM ibkr_flex_credentials
                WHERE tenant_id = %s AND credential_id = %s
                  AND status NOT IN ('revoked', 'expired')
                """,
                (TENANT_ID, submission.credential_id),
            ).fetchone()
            if existing_credential is None:
                raise HTTPException(status_code=404, detail="Reusable credential not found")
        duplicate = connection.execute(
            """
            SELECT 1
            FROM ibkr_flex_queries q
            WHERE q.tenant_id = %s
              AND (lower(q.display_name) = lower(%s)
                   OR (q.credential_id = %s AND q.query_id = %s))
            LIMIT 1
            """,
            (
                TENANT_ID,
                source_name,
                submission.credential_id,
                submission.query_id,
            ),
        ).fetchone()
    if duplicate:
        raise HTTPException(status_code=409, detail="Flex source already exists")

    credential_id = submission.credential_id or uuid.uuid4()
    flex_source_id = uuid.uuid4()
    if existing_credential:
        if token:
            raise HTTPException(
                status_code=400,
                detail="Do not submit a token when reusing a secured credential",
            )
        if not re.fullmatch(
            r"[0-9a-f]{16}", str(existing_credential["token_fingerprint"] or "")
        ):
            raise HTTPException(
                status_code=409,
                detail="Reusable credential has not passed secure registration",
            )
        credential_name = str(existing_credential["display_name"])
        stored = {
            "secret_name": str(existing_credential["key_vault_secret_name"]),
            "token_fingerprint": str(existing_credential["token_fingerprint"]),
        }
    else:
        if not credential_name or not token or len(token) > 1024:
            raise HTTPException(
                status_code=400,
                detail="A name and current Flex token are required for a new credential",
            )
        secret_name = f"ibkr-flex-source-{credential_id.hex}"
        try:
            stored = _store_flex_token_via_broker(secret_name, token)
        except Exception as error:
            SECURITY_AUDIT_LOGGER.warning(
                "Flex credential broker submission failed correlation_id=%s",
                uuid.uuid4().hex,
            )
            raise HTTPException(
                status_code=503,
                detail="The Flex credential was not registered; no source was activated",
            ) from error
        finally:
            token = ""

    actor = _identity_digest(user["object_id"] or user["email"])
    try:
        with _approval_database() as connection:
            if not existing_credential:
                connection.execute(
                    """
                    INSERT INTO ibkr_flex_credentials(
                        credential_id, tenant_id, display_name, key_vault_secret_name,
                        token_fingerprint, status, created_by_identity_sha256,
                        metadata_json, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, 'pending_validation', %s, %s::jsonb, now(), now())
                    """,
                    (
                        credential_id,
                        TENANT_ID,
                        credential_name,
                        stored["secret_name"],
                        stored["token_fingerprint"],
                        actor,
                        json.dumps({"submission_channel": "secure_credential_broker"}),
                    ),
                )
            connection.execute(
                """
                INSERT INTO ibkr_flex_queries(
                    flex_source_id, tenant_id, credential_id, query_id,
                    display_name, schedule_enabled, status, metadata_json,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, false, 'pending_validation', %s::jsonb, now(), now())
                """,
                (
                    flex_source_id,
                    TENANT_ID,
                    credential_id,
                    submission.query_id,
                    source_name,
                    json.dumps({"activation_blocked": True}),
                ),
            )
    except Exception as error:
        SECURITY_AUDIT_LOGGER.exception(
            "Flex source metadata registration failed credential_id=%s", credential_id
        )
        raise HTTPException(
            status_code=503,
            detail="The token was secured, but source metadata requires administrator reconciliation",
        ) from error
    return {
        "status": "pending_validation",
        "credential_id": str(credential_id),
        "flex_source_id": str(flex_source_id),
        "token_fingerprint": stored["token_fingerprint"],
        "schedule_enabled": False,
        "message": "Credential secured. Account discovery and scheduling remain blocked until one controlled validation succeeds.",
    }


@app.post("/api/onboarding/flex-sources/{flex_source_id}/validate")
def queue_flex_source_validation(
    flex_source_id: uuid.UUID,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Queue one explicit, non-retrying Flex validation and account-discovery run."""

    requested_to = datetime.now(ZoneInfo("Asia/Hong_Kong")).date() - timedelta(days=1)
    requested_from = requested_to - timedelta(days=6)
    validation_request_id = uuid.uuid4()
    actor = _identity_digest(user["object_id"] or user["email"])
    with _approval_database() as connection:
        source = connection.execute(
            """
            SELECT q.status, q.schedule_enabled, q.credential_id,
                   c.status AS credential_status,
                   s.blocked_until, s.requires_manual_reset
            FROM ibkr_flex_queries q
            JOIN ibkr_flex_credentials c
              ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
            LEFT JOIN ibkr_credential_circuit_state s
              ON s.tenant_id = q.tenant_id AND s.credential_id = q.credential_id
            WHERE q.tenant_id = %s AND q.flex_source_id = %s
            FOR UPDATE OF q
            """,
            (TENANT_ID, flex_source_id),
        ).fetchone()
        if source is None:
            raise HTTPException(status_code=404, detail="Flex source was not found")
        if source["status"] not in {"pending_validation", "validated"}:
            raise HTTPException(status_code=409, detail="Flex source cannot be validated in its current state")
        if source["credential_status"] in {"suspended", "expired", "revoked"}:
            raise HTTPException(status_code=409, detail="Flex credential is not available for validation")
        if source["requires_manual_reset"]:
            raise HTTPException(status_code=409, detail="Flex credential requires an owner-reviewed circuit reset")
        now = datetime.now(timezone.utc)
        if source["blocked_until"] and source["blocked_until"] > now:
            raise HTTPException(status_code=409, detail="Flex credential is in a safety cooldown")
        inserted = connection.execute(
            """
            INSERT INTO ibkr_flex_source_validation_requests(
                validation_request_id, tenant_id, flex_source_id, status,
                requested_from, requested_to, requested_by_identity_sha256,
                attempt_count, created_at
            )
            SELECT %s, %s, %s, 'pending', %s, %s, %s, 0, now()
            WHERE NOT EXISTS (
                SELECT 1
                FROM ibkr_flex_source_validation_requests
                WHERE tenant_id = %s AND flex_source_id = %s
                  AND status IN ('pending', 'running')
            )
            ON CONFLICT DO NOTHING
            RETURNING validation_request_id
            """,
            (
                validation_request_id,
                TENANT_ID,
                flex_source_id,
                requested_from,
                requested_to,
                actor,
                TENANT_ID,
                flex_source_id,
            ),
        ).fetchone()
        if inserted is None:
            raise HTTPException(status_code=409, detail="A validation request is already pending or running")
    return {
        "status": "pending",
        "validation_request_id": str(validation_request_id),
        "flex_source_id": str(flex_source_id),
        "requested_from": requested_from,
        "requested_to": requested_to,
        "attempt_limit": 1,
        "schedule_enabled": False,
        "message": "One controlled validation was queued. It cannot retry automatically.",
    }


async def _read_required_upload(upload: UploadFile, label: str) -> bytes:
    content = await upload.read(MAX_TEMPLATE_BYTES + 1)
    if len(content) > MAX_TEMPLATE_BYTES:
        raise HTTPException(status_code=413, detail=f"{label} exceeds 50 MiB")
    if not content:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    return content


@app.post("/api/onboarding/{subject_id}/template-submissions")
async def submit_account_onboarding_templates(
    subject_id: str,
    factsheet_template: UploadFile = File(...),
    presentation_template: UploadFile = File(...),
    completed_factsheet: UploadFile = File(...),
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Validate and register the mandatory three-document onboarding set."""
    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    with _database() as connection:
        case = connection.execute(
            """
            SELECT c.stage, c.status, s.display_name,
                   a.approval_id AS presentation_identity_approval_id,
                   a.scope_json AS presentation_identity_scope,
                   a.scope_sha256 AS presentation_identity_scope_sha256
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id
             AND s.subject_id = c.target_subject_id
            LEFT JOIN account_onboarding_approvals a
              ON a.tenant_id = c.tenant_id
             AND a.subject_id = c.target_subject_id
             AND a.approval_type = 'presentation_identity'
             AND a.approval_status = 'approved'
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            """,
            (TENANT_ID, subject),
        ).fetchone()
    if case is None:
        raise HTTPException(status_code=404, detail="Onboarding case was not found")
    if case["stage"] not in {"template_intake", "reconciliation"}:
        raise HTTPException(
            status_code=409,
            detail="Documents cannot be replaced at the current onboarding stage",
        )
    approved_scope = case["presentation_identity_scope"]
    if isinstance(approved_scope, str):
        try:
            approved_scope = json.loads(approved_scope)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=409,
                detail="The owner-approved presentation identity is invalid",
            ) from error
    if not isinstance(approved_scope, dict):
        raise HTTPException(
            status_code=409,
            detail=(
                "The owner must approve the server-derived presentation identity "
                "before an operator can upload report documents"
            ),
        )
    try:
        expected_scope = presentation_identity_scope(
            subject_id=subject,
            display_name=str(case["display_name"]),
            layout_policy=str(approved_scope.get("layout_policy") or ""),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    expected_scope_sha256 = sha256_text(canonical_json(expected_scope))
    if (
        approved_scope != expected_scope
        or str(case["presentation_identity_scope_sha256"])
        != expected_scope_sha256
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "The account identity changed after owner approval. Reload the "
                "onboarding case and obtain a new audited approval."
            ),
        )

    try:
        factsheet = validate_pdf_upload(
            factsheet_template.filename or "",
            await _read_required_upload(factsheet_template, "Factsheet template"),
        )
        presentation = validate_pptx_upload(
            presentation_template.filename or "",
            await _read_required_upload(
                presentation_template, "Presentation template"
            ),
        )
        presentation_contract = presentation_strategy_contract(
            presentation.content,
            expected_strategy=str(expected_scope["strategy_name"]),
            layout_policy=str(expected_scope["layout_policy"]),
            subject_id=subject,
        )
        presentation_contract["owner_approval_id"] = str(
            case["presentation_identity_approval_id"]
        )
        presentation_contract["owner_approval_scope_sha256"] = (
            expected_scope_sha256
        )
        presentation_contract["identity_source"] = (
            "owner_approved_subject_display_name"
        )
        example = validate_pdf_upload(
            completed_factsheet.filename or "",
            await _read_required_upload(
                completed_factsheet, "Previously completed factsheet"
            ),
        )
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    version_label = upload_version_label()
    prefix = f"templates/{subject}/intake/{version_label}"
    uploader_sha256 = _identity_digest(user["object_id"] or user["email"])
    specifications = (
        ("factsheet", "template", "factsheet-template", factsheet, {}),
        (
            "presentation",
            "template",
            "presentation-template",
            presentation,
            {"presentation_contract": presentation_contract},
        ),
        ("factsheet", "completed_example", "completed-factsheet", example, {}),
    )
    assets = []
    for report_type, asset_role, label, upload, specification_metadata in specifications:
        blob_name = f"{prefix}/{safe_blob_filename(upload, label=label)}"
        assets.append(
            {
                "report_type": report_type,
                "asset_role": asset_role,
                "version_label": version_label,
                "container_name": ONBOARDING_INTAKE_CONTAINER,
                "blob_name": blob_name,
                "content_type": upload.content_type,
                "sha256": upload.sha256,
                "byte_count": upload.byte_count,
                "metadata": {
                    "source": "authenticated_portal_upload",
                    "original_filename": upload.filename,
                    "uploader_sha256": uploader_sha256,
                    **specification_metadata,
                },
            }
        )
    manifest_content = json.dumps(
        {"subject_id": subject, "assets": assets},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    parsed_assets = parse_template_manifest(
        manifest_content,
        subject_id=subject,
        container_name=ONBOARDING_INTAKE_CONTAINER,
    )

    if not AZURE_STORAGE_ACCOUNT_URL:
        raise HTTPException(status_code=503, detail="Private intake storage is unavailable")
    try:
        for specification, asset in zip(specifications, parsed_assets, strict=True):
            upload_bytes(
                AZURE_STORAGE_ACCOUNT_URL,
                asset.container_name,
                asset.blob_name,
                specification[3].content,
                overwrite=False,
                content_type=asset.content_type,
            )
        manifest_blob = f"{prefix}/manifest-{hashlib.sha256(manifest_content).hexdigest()}.json"
        upload_bytes(
            AZURE_STORAGE_ACCOUNT_URL,
            ONBOARDING_INTAKE_CONTAINER,
            manifest_blob,
            manifest_content,
            overwrite=False,
            content_type="application/json; charset=utf-8",
        )
    except Exception as error:
        SECURITY_AUDIT_LOGGER.exception(
            "Onboarding template upload failed for %s", subject
        )
        raise HTTPException(
            status_code=503, detail="The private document upload did not complete"
        ) from error

    try:
        with _approval_database() as raw_connection:
            evidence = register_template_assets(
                PostgresConnection(raw_connection),
                tenant_id=TENANT_ID,
                subject_id=subject,
                assets=parsed_assets,
            )
    except Exception as error:
        SECURITY_AUDIT_LOGGER.exception(
            "Onboarding template registration failed for %s", subject
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Documents were preserved in private intake storage, but registration "
                "did not complete; an administrator can safely retry"
            ),
        ) from error
    return {
        "status": "validated",
        "subject_id": subject,
        "version_label": version_label,
        "asset_count": evidence["asset_count"],
        "next_stage": "reconciliation",
    }


CONNECTION_QUEUE_PAGE_SCRIPT = r"""
<script>
(()=>{
const sourceId=__SOURCE_ID__;
const esc=value=>String(value??'').replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const label=value=>String(value||'').replaceAll('_',' ').replace(/\b\w/g,character=>character.toUpperCase());
const json=async response=>{const payload=await response.json();if(!response.ok)throw new Error(payload.detail||`http_${response.status}`);return payload};
const root=document.querySelector('#queue-workspace'),status=document.querySelector('#queue-status');
let selected=[];
const accountName=item=>item.account_name?`${item.source_account_id} · ${item.account_name}`:item.source_account_id;
const renderSelected=()=>{const target=document.querySelector('#selected-order');if(!target)return;target.innerHTML=selected.length?selected.map((item,index)=>`<div class="status-item"><span class="label">Position ${index+1}</span><strong>${esc(accountName(item))}</strong><div class="actions"><button type="button" class="button move-up" data-id="${esc(item.source_account_id)}" ${index===0?'disabled':''}>Move up</button><button type="button" class="button move-down" data-id="${esc(item.source_account_id)}" ${index===selected.length-1?'disabled':''}>Move down</button></div></div>`).join(''):'<div class="muted">Select at least one eligible account. The first selected account starts immediately; every later account remains locked.</div>';target.querySelectorAll('.move-up,.move-down').forEach(button=>button.addEventListener('click',()=>{const index=selected.findIndex(item=>item.source_account_id===button.dataset.id),change=button.classList.contains('move-up')?-1:1,next=index+change;if(index<0||next<0||next>=selected.length)return;[selected[index],selected[next]]=[selected[next],selected[index]];renderSelected()}))};
const render=async()=>{const data=await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(sourceId)}/queue`).then(json),connection=data.connection,queue=data.queue,items=data.queue_items||[],groups=data.groups;
document.querySelector('#queue-title').textContent=connection.display_name;
document.querySelector('#queue-summary').innerHTML=[['Query ID',connection.query_id],['Discovered accounts',data.accounts.length],['Execution','Strictly sequential'],['Open queue',queue?label(queue.queue_status):'Not created']].map(value=>`<div class="status-item"><span class="label">${esc(value[0])}</span><strong>${esc(value[1])}</strong></div>`).join('');
if(queue){const current=items.find(item=>item.item_status==='current');root.innerHTML=`<div class="card wide"><div class="label">Account sequence</div><h2>One account at a time</h2><p class="muted">The next account cannot start until the current account has passed every gate and is active. This is enforced in PostgreSQL as well as in the user interface.</p><div class="status-row">${items.map(item=>`<div class="status-item"><span class="label">Position ${esc(item.sequence_number)} · ${esc(label(item.item_status))}</span><strong>${esc(item.source_account_id)}</strong><span class="muted">${esc(item.onboarding_stage?label(item.onboarding_stage):item.item_status==='current'?'Ready to begin its page-by-page contract':'Locked until its predecessor is active')}</span>${item.item_status==='current'&&item.target_subject_id?`<a href="/account-onboarding/${encodeURIComponent(item.target_subject_id)}/steps/connection">Continue onboarding</a>`:''}</div>`).join('')}</div>${current&&!current.target_subject_id?`<form id="start-current-account"><h3>Start ${esc(current.source_account_id)}</h3><p class="muted">Confirm its public display name, reporting currency and reporting-history start date. This creates only a private onboarding case; it does not activate the account.</p><div class="status-row"><label class="status-item"><span class="label">Display name</span><input name="display_name" required minlength="3" maxlength="160" value="${esc(current.source_account_id)}"></label><label class="status-item"><span class="label">Reporting currency</span><input name="reporting_currency" required pattern="[A-Z]{3}" maxlength="3" value="USD"></label><label class="status-item"><span class="label">History start date</span><input name="history_start_date" type="date" required value="2025-01-20"></label></div><div class="actions"><button class="button" type="submit">Create private onboarding case</button></div><div id="start-current-status" class="muted" role="status"></div></form>`:''}<div class="actions"><button id="advance-queue" class="button" type="button">Verify activation and start next</button></div><div id="advance-status" class="muted" role="status"></div></div><div class="card wide"><div class="label">Available later</div><h2>Discovered accounts retained for a future queue</h2><div class="status-row">${groups.available_later.length?groups.available_later.map(item=>`<div class="status-item"><strong>${esc(accountName(item))}</strong><span class="muted">Not selected now; discovery evidence is retained.</span></div>`).join(''):'<div class="muted">Every eligible discovered account is already active or in this queue.</div>'}</div></div>`;const startForm=document.querySelector('#start-current-account');if(startForm)startForm.addEventListener('submit',async event=>{event.preventDefault();const button=startForm.querySelector('button'),note=document.querySelector('#start-current-status'),form=new FormData(startForm);button.disabled=true;note.textContent='Creating the private, fail-closed onboarding case…';try{const result=await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(sourceId)}/queue/current/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:form.get('display_name'),reporting_currency:String(form.get('reporting_currency')).toUpperCase(),history_start_date:form.get('history_start_date')})}).then(json);window.location.href=result.next_page||`/account-onboarding/${encodeURIComponent(result.subject_id)}/steps/financial-contract`}catch(error){note.textContent=`Account was not started: ${error.message}`;button.disabled=false}});document.querySelector('#advance-queue').addEventListener('click',async()=>{const button=document.querySelector('#advance-queue'),note=document.querySelector('#advance-status');button.disabled=true;note.textContent='Checking the current account activation gate…';try{const result=await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(sourceId)}/queue/advance`,{method:'POST'}).then(json);note.textContent=result.current_account?`${result.current_account} is now the only current account.`:'The connection queue is complete.';setTimeout(()=>window.location.reload(),800)}catch(error){note.textContent=`Queue remains locked: ${error.message}`;button.disabled=false}});return}
const eligible=data.accounts.filter(item=>!['active'].includes(item.onboarding_group)&&!['ignored','not_portfolio'].includes(item.discovery_status));root.innerHTML=`<div class="card wide"><div class="label">Page 1 of onboarding</div><h2>Choose accounts and set their order</h2><p class="muted">Select only the accounts you want to onboard now. Unselected accounts remain attached to this IBKR connection as Available later and can be placed in a future queue without rediscovery.</p><div class="table-wrap"><table><thead><tr><th>Select</th><th>Account</th><th>Name</th><th>Type</th><th>Currency</th><th>Discovery state</th></tr></thead><tbody>${eligible.map(item=>`<tr><td><input class="queue-select" type="checkbox" data-account="${esc(item.source_account_id)}" aria-label="Select ${esc(item.source_account_id)}"></td><td><strong>${esc(item.source_account_id)}</strong></td><td>${esc(item.account_name||'—')}</td><td>${esc(item.account_type||'—')}</td><td>${esc(item.base_currency||'—')}</td><td>${esc(label(item.discovery_status))}</td></tr>`).join('')}</tbody></table></div><h3>Onboarding order</h3><div id="selected-order" class="status-row"></div><div class="actions"><button id="create-queue" class="button" type="button">Create sequential onboarding queue</button></div><div id="create-status" class="muted" role="status"></div></div>`;renderSelected();document.querySelectorAll('.queue-select').forEach(box=>box.addEventListener('change',()=>{const item=eligible.find(row=>row.source_account_id===box.dataset.account);if(box.checked)selected.push(item);else selected=selected.filter(row=>row.source_account_id!==box.dataset.account);renderSelected()}));document.querySelector('#create-queue').addEventListener('click',async()=>{const button=document.querySelector('#create-queue'),note=document.querySelector('#create-status');if(!selected.length){note.textContent='Select at least one account.';return}button.disabled=true;note.textContent='Creating the database-enforced sequence…';try{const result=await fetch(`/api/onboarding/flex-sources/${encodeURIComponent(sourceId)}/queue`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account_ids:selected.map(item=>item.source_account_id)})}).then(json);note.textContent=`${result.current_account} is current; ${result.queued_accounts.length} later account(s) are locked.`;setTimeout(()=>window.location.reload(),800)}catch(error){note.textContent=`Queue not created: ${error.message}`;button.disabled=false}})};
render().catch(error=>{status.textContent='Unavailable';root.innerHTML=`<div class="warning">The queue remains fail-closed. ${esc(error.message)}</div>`});
})();
</script>
"""


@app.get(
    "/account-onboarding/connections/{flex_source_id}/accounts",
    response_class=HTMLResponse,
)
def connection_onboarding_queue_page(
    flex_source_id: str,
    request: Request,
    user: dict[str, str] = Depends(_onboarding_user),
) -> str:
    source_id = _flex_source_uuid(flex_source_id)
    viewer = escape(user["email"] or "authorized user")
    role_label = escape(user.get("role", "owner").title())
    navigation = _platform_navigation(user, active="account-onboarding")
    script = CONNECTION_QUEUE_PAGE_SCRIPT.replace(
        "__SOURCE_ID__", json.dumps(source_id)
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Choose accounts · {PLATFORM_NAME}</title><style>{ACCOUNT_THEME_CSS}</style></head><body><header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header><main><div class="breadcrumbs"><a href="/">Accounts</a><span>/</span><a href="/ibkr-connections">IBKR connections</a><span>/</span><span>Choose accounts</span></div><div class="account-hero"><div class="account-hero-copy"><div class="label">Connection-scoped onboarding</div><h1 id="queue-title">Loading connection…</h1><div id="queue-status" class="muted">Choose which discovered accounts to onboard and the exact order.</div></div></div><section class="grid"><div class="card wide"><div class="label">Connection policy</div><div id="queue-summary" class="status-row"></div></div><div id="queue-workspace" class="grid" style="display:contents"><div class="card wide"><div class="muted">Loading discovered accounts…</div></div></div></section></main>{script}</body></html>"""


ONBOARDING_STEP_PAGE_SCRIPT = r"""
<script>
(()=>{
const subject=__SUBJECT__,step=__STEP__;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const label=value=>String(value??'').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const errorText=detail=>typeof detail==='string'?detail:detail?.message?`${detail.message}${detail.correlation_id?' Reference: '+detail.correlation_id:''}`:detail?.code?`${detail.code}${detail.failed_checks?.length?': '+detail.failed_checks.join(', '):''}${detail.failed_validation?.length?' · '+detail.failed_validation.join(', '):''}`:JSON.stringify(detail||'request_failed');
const json=async response=>{const raw=await response.text();let payload={};if(raw){try{payload=JSON.parse(raw)}catch{payload={}}}if(!response.ok){const correlation=response.headers.get('X-Correlation-ID'),fallback=response.status>=500?`The server could not complete this request. No approval was recorded.${correlation?' Reference: '+correlation:''}`:`Request failed (HTTP ${response.status}).`;throw new Error(payload?.detail?errorText(payload.detail):fallback)}return payload};
const root=document.querySelector('#step-workspace');
const statusRows=rows=>`<div class="status-row">${rows.map(row=>`<div class="status-item"><span class="label">${esc(row[0])}</span><strong>${esc(row[1]??'—')}</strong>${row[2]?`<span class="muted">${esc(row[2])}</span>`:''}</div>`).join('')}</div>`;
const checks=rows=>`<div class="onboarding-checks">${rows.map(row=>`<div class="onboarding-check ${esc(row.status)}"><strong>${esc(label(row.check_id))}</strong><span>${esc(row.message)}</span></div>`).join('')}</div>`;
const approval=(data,type)=>(data.approvals||[]).find(row=>row.approval_type===type);
const actionResult=id=>`<div id="${id}" class="muted" role="status"></div>`;
const reloadSoon=()=>setTimeout(()=>window.location.reload(),850);
let historyPollTimer=null,historyPollFailures=0,historySignature='';
const upgradeSecondaryBenchmarkPicker=form=>{
  const original=form?.querySelector('select[name="secondary_benchmark_ids"][multiple]');
  if(!original)return;
  const primary=form.querySelector('select[name="primary_benchmark_id"]');
  primary?.closest('label.status-item')?.classList.add('benchmark-primary');
  const fieldset=document.createElement('fieldset');
  fieldset.className='status-item benchmark-picker';
  fieldset.dataset.upgraded='true';
  const choices=[...original.options].filter(option=>option.value);
  fieldset.innerHTML=`<legend class="label">Secondary comparison benchmarks</legend><p class="benchmark-help">Choose any additional benchmarks by ticking the boxes. No Ctrl key is needed. The primary benchmark is disabled here automatically.</p><div class="benchmark-choice-grid">${choices.map(option=>`<label class="benchmark-choice"><input type="checkbox" name="secondary_benchmark_ids" value="${esc(option.value)}" ${option.selected?'checked':''} ${option.disabled?'disabled':''}><span>${esc(option.textContent)}</span></label>`).join('')}</div><div class="benchmark-picker-footer"><span class="benchmark-selection-summary" role="status" aria-live="polite">No secondary benchmarks selected</span><button class="benchmark-clear" type="button">Clear selection</button></div>`;
  const update=()=>{
    const primaryValue=primary?.value||'';
    const boxes=[...fieldset.querySelectorAll('input[name="secondary_benchmark_ids"]')];
    boxes.forEach(box=>{
      const sameAsPrimary=Boolean(primaryValue)&&box.value===primaryValue;
      if(sameAsPrimary)box.checked=false;
      box.disabled=sameAsPrimary;
      box.closest('.benchmark-choice')?.classList.toggle('disabled',sameAsPrimary);
    });
    const selected=boxes.filter(box=>box.checked);
    const summary=fieldset.querySelector('.benchmark-selection-summary');
    summary.textContent=selected.length?`${selected.length} selected: ${selected.map(box=>box.nextElementSibling.textContent.split(' · ')[0]).join(', ')}`:'No secondary benchmarks selected';
    fieldset.querySelector('.benchmark-clear').disabled=!selected.length;
  };
  fieldset.addEventListener('change',update);
  fieldset.querySelector('.benchmark-clear').addEventListener('click',()=>{fieldset.querySelectorAll('input[name="secondary_benchmark_ids"]').forEach(box=>box.checked=false);update()});
  primary?.addEventListener('change',update);
  original.closest('label.status-item')?.replaceWith(fieldset);
  update();
};
const benchmarkPickerObserver=new MutationObserver(()=>root.querySelectorAll('form').forEach(upgradeSecondaryBenchmarkPicker));
benchmarkPickerObserver.observe(root,{childList:true,subtree:true});
const renderConnection=data=>{const c=data.connection||{},s=data.subject,isDerived=['filtered_portfolio','composite_portfolio'].includes(s.subject_type);root.innerHTML=isDerived?`<h2>Governed internal source</h2><p class="muted">This portfolio is calculated from active platform subjects and versioned market-data dependencies. It does not require another Flex token.</p>${statusRows([['Source system',label(s.source_system)],['Calculation',label(data.calculation_definition?.calculation_kind)],['Definition version',data.calculation_definition?.definition_version],['Definition state',label(data.calculation_definition?.approval_status)],['Definition SHA-256',data.calculation_definition?.configuration_sha256]])}<div class="onboarding-check pass"><strong>Dependency source gate passed</strong><span>Every required source was checked before this private onboarding case was created.</span></div>`:`<h2>Secured source connection</h2><p class="muted">This page shows connection metadata only. Flex tokens and credentials are never returned to the browser.</p>${statusRows([['Source system',label(s.source_system)],['Connection',c.connection_name||'Imported v1.1 source'],['Query ID',c.query_id||'Existing v1.1 import'],['Connection state',label(c.connection_status||'validated')],['Binding',label(c.binding_status||'active')],['Token fingerprint',c.token_fingerprint||'Not exposed for imported source']])}<div class="onboarding-check pass"><strong>Connection gate passed</strong><span>The selected source is bound to this onboarding case. No credential value is exposed.</span></div>`};
const renderAccount=data=>{const s=data.subject,isDerived=['filtered_portfolio','composite_portfolio'].includes(s.subject_type);root.innerHTML=`<h2>${isDerived?'Selected governed dependencies':'Selected source account'}</h2><p class="muted">${isDerived?'The server registered the approved typed calculation and dependency graph. No executable formula came from the browser.':'This account is the only current item in its connection queue. Later selected accounts remain locked.'}</p>${statusRows([['Source',s.source_account_id],['Subject type',label(s.subject_type)],['Display name',s.display_name],['Base currency',s.base_currency],['Reporting currency',s.reporting_currency],['Reporting start',s.history_start_date]])}<div class="onboarding-check pass"><strong>Subject selection locked</strong><span>The page-by-page process is scoped only to ${esc(s.subject_id)}.</span></div>`};
const renderDerivedFinancial=data=>{const s=data.subject,d=data.calculation_definition,approved=approval(data,'calculation_contract'),catalog=data.benchmark_catalog||[],definition=d?.configuration_json||{};if(approved){const scope=approved.scope_json||{};root.innerHTML=`<h2>Approved deterministic calculation contract</h2>${statusRows([['Portfolio',s.display_name],['Calculation',label(d.calculation_kind)],['Definition version',d.definition_version],['Reporting currency',s.reporting_currency],['Primary benchmark',scope.primary_benchmark?.name||'Registered'],['Definition SHA-256',d.configuration_sha256]])}<div class="onboarding-check pass"><strong>Owner approval recorded</strong><span>The calculation, dependencies, currency policy and comparison benchmarks cannot be silently replaced.</span></div>`;return}if(!data.may_approve_financial_contract){root.innerHTML='<div class="warning">Owner approval is required to lock this calculation contract.</div>';return}const options=catalog.map(x=>`<option value="${esc(x.benchmark_id)}">${esc(x.name)} (${esc(x.symbol)}) · ${esc(x.return_convention)} · ${esc(x.currency)}</option>`).join(''),components=(definition.components||[]).map(x=>`${x.coefficient} × ${x.component_name||x.component_id}`).join(' · '),policy=definition.cash_policy?`Opening cash ${Object.entries(definition.cash_policy.opening_cash||{}).map(([ccy,value])=>`${ccy} ${value}`).join(', ')} · allowed ${definition.cash_policy.allowed_currencies.join(', ')}`:`Constant cash ${definition.constant_cash} · ${label(definition.rebalancing_policy)}`;root.innerHTML=`<h2>Review and lock the deterministic calculation</h2><p class="muted">This definition is server-owned, versioned and hash-bound. The browser cannot edit filters, coefficients or calculation code.</p>${statusRows([['Portfolio',s.display_name],['Calculation',label(d.calculation_kind)],['Components',components||definition.source_account_id],['Cash policy',policy],['Effective from',d.effective_from],['Definition SHA-256',d.configuration_sha256]])}<form id="derived-contract-form"><div class="status-row"><label class="status-item"><span class="label">Primary comparison benchmark</span><select name="primary_benchmark_id" required><option value="">Choose one</option>${options}</select></label><label class="status-item"><span class="label">Secondary comparison benchmarks</span><select name="secondary_benchmark_ids" multiple size="6">${options}</select></label></div><label style="display:flex;gap:10px;align-items:flex-start;margin-top:16px"><input name="approved" type="checkbox" required> <span>I approve this exact deterministic calculation and benchmark contract. A future change must create a new governed version.</span></label><div class="actions"><button class="button" type="submit">Approve typed calculation</button></div>${actionResult('derived-contract-result')}</form>`;document.querySelector('#derived-contract-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),note=document.querySelector('#derived-contract-result'),values=new FormData(form);button.disabled=true;note.textContent='Verifying the definition hash and dependencies…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/derived-contract`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({definition_sha256:d.configuration_sha256,primary_benchmark_id:values.get('primary_benchmark_id'),secondary_benchmark_ids:values.getAll('secondary_benchmark_ids'),approved:true})}).then(json);note.textContent='Calculation contract approved and locked.';reloadSoon()}catch(error){note.textContent=`Contract not approved: ${error.message}`;button.disabled=false}})};
const renderMasterFinancial=data=>{const s=data.subject,d=data.master_definition,approved=approval(data,'calculation_contract'),definition=d?.configuration_json||{};if(approved){root.innerHTML=`<h2>Approved master-account contract</h2>${statusRows([['Master account',s.display_name],['Reporting mode',label(definition.reporting_mode)],['Reporting currency',s.reporting_currency],['Member accounts',(definition.member_account_ids||[]).join(', ')||'Not applicable'],['Definition SHA-256',d.configuration_sha256]])}<div class="onboarding-check pass"><strong>Owner approval recorded</strong><span>The reporting mode and anti-double-counting policy are locked.</span></div>`;return}root.innerHTML=`<h2>Review and lock the master-account contract</h2><p class="muted">The selected mode explicitly controls whether investment totals are published and prevents master and child values from being counted together.</p>${statusRows([['Master account',s.display_name],['Reporting mode',label(definition.reporting_mode)],['Member accounts',(definition.member_account_ids||[]).join(', ')||'None'],['Count master totals',definition.aggregation?.count_master_totals],['Count child totals',definition.aggregation?.count_child_totals],['Eliminate internal transfers',definition.aggregation?.eliminate_internal_transfers],['Definition SHA-256',d.configuration_sha256]])}<label style="display:flex;gap:10px;align-items:flex-start"><input id="master-contract-consent" type="checkbox"> <span>I approve this exact reporting mode and anti-double-counting policy.</span></label><div class="actions"><button id="master-contract-approve" class="button" type="button" disabled>Approve master contract</button></div>${actionResult('master-contract-result')}`;const consent=document.querySelector('#master-contract-consent'),button=document.querySelector('#master-contract-approve');consent.addEventListener('change',()=>button.disabled=!consent.checked);button.addEventListener('click',async()=>{const note=document.querySelector('#master-contract-result');button.disabled=true;note.textContent='Locking the master-account definition…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/master-contract`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({definition_sha256:d.configuration_sha256,approved:true})}).then(json);note.textContent='Master-account contract approved.';reloadSoon()}catch(error){note.textContent=`Contract not approved: ${error.message}`;button.disabled=false}})};
const renderFinancial=data=>{if(data.master_definition){renderMasterFinancial(data);return}if(data.calculation_definition){renderDerivedFinancial(data);return}const s=data.subject,approved=approval(data,'financial_contract'),catalog=data.benchmark_catalog||[];if(approved){const scope=approved.scope_json||{};root.innerHTML=`<h2>Approved financial contract</h2>${statusRows([['Display name',scope.display_name||s.display_name],['Reporting currency',scope.reporting_currency||s.reporting_currency],['Reporting start',scope.history_start_date||s.history_start_date],['Primary benchmark',scope.primary_benchmark?.name||'Registered'],['Secondary benchmarks',(scope.secondary_benchmarks||[]).map(x=>x.name).join(', ')||'None'],['Evidence SHA-256',approved.scope_sha256]])}<div class="onboarding-check pass"><strong>Owner approval recorded</strong><span>The reporting contract cannot be silently replaced.</span></div>`;return}if(!data.may_approve_financial_contract){root.innerHTML='<div class="warning">Owner approval is required to lock this financial contract.</div>';return}const options=catalog.map(x=>`<option value="${esc(x.benchmark_id)}">${esc(x.name)} (${esc(x.symbol)}) · ${esc(x.return_convention)} · ${esc(x.currency)}</option>`).join('');root.innerHTML=`<h2>Review and lock the financial contract</h2><p class="muted">Choose exactly one primary benchmark and any secondary benchmarks. Their registered return convention and currency remain explicit.</p><form id="financial-form"><div class="status-row"><label class="status-item"><span class="label">Display name</span><input name="display_name" required minlength="3" maxlength="160" value="${esc(s.display_name)}"></label><label class="status-item"><span class="label">Reporting currency</span><input name="reporting_currency" required pattern="[A-Z]{3}" maxlength="3" value="${esc(s.reporting_currency||'USD')}"></label><label class="status-item"><span class="label">Reporting start</span><input name="history_start_date" type="date" required value="${esc(s.history_start_date||s.requested_history_start||'')}"></label><label class="status-item"><span class="label">Primary benchmark</span><select name="primary_benchmark_id" required><option value="">Choose one</option>${options}</select></label><label class="status-item"><span class="label">Secondary benchmarks</span><select name="secondary_benchmark_ids" multiple size="6">${options}</select></label></div><label style="display:flex;gap:10px;align-items:flex-start;margin-top:16px"><input name="approved" type="checkbox" required> <span>I approve this reporting and benchmark contract. I understand that changing it later requires a separately governed amendment.</span></label><div class="actions"><button class="button" type="submit">Approve and lock contract</button></div>${actionResult('financial-result')}</form>`;document.querySelector('#financial-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),note=document.querySelector('#financial-result'),values=new FormData(form);button.disabled=true;note.textContent='Validating and recording the financial contract…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/financial-contract`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name:values.get('display_name'),reporting_currency:String(values.get('reporting_currency')).toUpperCase(),history_start_date:values.get('history_start_date'),primary_benchmark_id:values.get('primary_benchmark_id'),secondary_benchmark_ids:values.getAll('secondary_benchmark_ids'),approved:true})}).then(json);note.textContent='Financial contract approved and locked.';reloadSoon()}catch(error){note.textContent=`Contract not approved: ${error.message}`;button.disabled=false}})};
const renderExternal=data=>{
  const ext=approval(data,'external_market_data'),identity=approval(data,'presentation_identity'),candidate=data.presentation_identity_candidate,disclosure=data.external_market_data_disclosure;
  const analytics=(data.checks||[]).find(row=>row.check_id==='risk_analytics_validated')||{};
  const analyticsEvidence=analytics.evidence_json||{};
  const analyticsState=analyticsEvidence.state||'';
  const completedAt=analytics.checked_at?new Date(analytics.checked_at).toLocaleString('en-HK',{timeZone:'Asia/Hong_Kong',dateStyle:'medium',timeStyle:'medium'}):'';
  const analyticsMessage=analytics.message||'Deterministic analytics have not been run for this portfolio.';
  root.innerHTML=`<h2>External data and presentation identity</h2><p class="muted">These are separate owner-controlled gates. Document upload remains locked until both pass.</p><div id="external-block"></div><div id="identity-block" style="margin-top:18px"></div>`;
  const extBlock=document.querySelector('#external-block');
  if(ext){
    let analyticsMarkup='';
    if(analytics.status==='pass')analyticsMarkup=`<div class="onboarding-check pass" style="margin-top:18px"><strong>Deterministic analytics completed successfully</strong><span>${esc(analyticsMessage)}${completedAt?` Completed ${esc(completedAt)} HKT.`:''}</span></div>`;
    else if(analytics.status==='pending'&&['dispatching','queued'].includes(analyticsState))analyticsMarkup=`<div class="onboarding-check pending" style="margin-top:18px"><strong>Deterministic analytics are running</strong><span>${esc(analyticsMessage)}${completedAt?` Requested ${esc(completedAt)} HKT.`:''}</span></div>${actionResult('analytics-result')}`;
    else analyticsMarkup=`${analytics.status==='fail'?`<div class="warning" style="margin-top:18px"><strong>Deterministic analytics need attention.</strong> ${esc(analyticsMessage)}</div>`:''}<div class="actions"><button id="run-onboarding-analytics" class="button" type="button">${analytics.status==='fail'?'Run deterministic analytics again':'Run deterministic analytics now'}</button></div>${actionResult('analytics-result')}`;
    extBlock.innerHTML=`<div class="onboarding-check pass"><strong>External-data scope approved</strong><span>Yahoo Finance ticker-symbol scope ${esc(ext.scope_sha256)}. Raw IBKR files, credentials and personal information remain excluded.</span></div>${analyticsMarkup}`;
  }else if(data.may_approve_financial_contract)extBlock.innerHTML=`<div class="onboarding-check pending"><strong>Yahoo Finance provisional market data</strong><span>Only ticker symbols may be sent for benchmark prices, sector classification and crisis-period price retrieval. Raw IBKR files, account identifiers, Flex credentials, database credentials and personal information are excluded.</span><label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input id="external-consent" type="checkbox"> <span>I approve disclosure ${esc(disclosure.version)} with evidence ${esc(disclosure.scope_sha256)}.</span></label><div class="actions"><button id="external-approve" class="button" type="button" disabled>Approve bounded transmission</button></div>${actionResult('external-result')}</div>`;
  else extBlock.innerHTML='<div class="warning">Owner approval is required for external market data.</div>';
  const identityBlock=document.querySelector('#identity-block');
  if(identity)identityBlock.innerHTML=`<div class="onboarding-check pass"><strong>Presentation identity locked</strong><span>${esc(identity.scope_json?.strategy_name||'Approved strategy title')} · platform-standard presentation layout.</span></div>`;
  else if(candidate&&data.may_approve_financial_contract)identityBlock.innerHTML=`<div class="onboarding-check pending"><strong>Server-derived presentation identity</strong><span>${esc(candidate.strategy_name)} · platform-standard presentation layout. Uploaders cannot edit either value.</span><label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input id="identity-consent" type="checkbox"> <span>I approve the derived strategy title and the mandatory platform layout.</span></label><div class="actions"><button id="identity-approve" class="button" type="button" disabled>Lock presentation identity</button></div>${actionResult('identity-result')}</div>`;
  else identityBlock.innerHTML='<div class="warning">Owner approval is required for the presentation identity.</div>';
  const pollAnalytics=async remaining=>{if(remaining<=0){const note=document.querySelector('#analytics-result');if(note)note.textContent='The run is taking longer than expected. You can safely refresh this page later.';return}await new Promise(resolve=>setTimeout(resolve,5000));try{const latest=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/preview`).then(json),check=(latest.checks||[]).find(row=>row.check_id==='risk_analytics_validated');if(check&&['pass','fail'].includes(check.status)){window.location.reload();return}pollAnalytics(remaining-1)}catch{pollAnalytics(remaining-1)}};
  const analyticsButton=document.querySelector('#run-onboarding-analytics');
  analyticsButton?.addEventListener('click',async()=>{const note=document.querySelector('#analytics-result');analyticsButton.disabled=true;note.textContent='Starting the governed calculation and deterministic analytics…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/analytics/queue`,{method:'POST'}).then(json);note.textContent=result.message;if(result.status==='already_complete'){window.location.reload();return}pollAnalytics(120)}catch(error){note.textContent=`Analytics were not queued: ${error.message}`;analyticsButton.disabled=false}});
  if(analytics.status==='pending'&&['dispatching','queued'].includes(analyticsState))pollAnalytics(120);
  const extConsent=document.querySelector('#external-consent'),extButton=document.querySelector('#external-approve');
  if(extConsent){extConsent.addEventListener('change',()=>extButton.disabled=!extConsent.checked);extButton.addEventListener('click',async()=>{const note=document.querySelector('#external-result');extButton.disabled=true;note.textContent='Recording approval…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/external-market-data`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({disclosure_version:disclosure.version,scope_sha256:disclosure.scope_sha256,approved:true})}).then(json);note.textContent='External-data approval recorded.';reloadSoon()}catch(error){note.textContent=`Approval failed: ${error.message}`;extButton.disabled=false}})}
  const idConsent=document.querySelector('#identity-consent'),idButton=document.querySelector('#identity-approve');
  if(idConsent){idConsent.addEventListener('change',()=>idButton.disabled=!idConsent.checked);idButton.addEventListener('click',async()=>{const note=document.querySelector('#identity-result');idButton.disabled=true;note.textContent='Locking identity…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/presentation-identity`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({display_name_sha256:candidate.display_name_sha256,layout_policy:'platform_standard',approved:true})}).then(json);note.textContent='Presentation identity locked.';reloadSoon()}catch(error){note.textContent=`Approval failed: ${error.message}`;idButton.disabled=false}})}
};
const renderDocuments=data=>{
  const templates=data.templates||[];
  const identities=new Set(templates.map(x=>`${x.report_type}:${x.asset_role}`));
  const complete=['factsheet:template','presentation:template','factsheet:completed_example'].every(value=>identities.has(value));
  const assetRows=templates.length?statusRows(templates.map(x=>[`${label(x.report_type)} · ${label(x.asset_role)}`,x.version_label,`${label(x.approval_status)} · ${x.byte_count} bytes · ${String(x.sha256).slice(0,16)}…`])):'';
  const formMarkup=replacement=>`<form id="documents-form"><p class="${replacement?'warning':'muted'}">${replacement?'Replacing the report package creates a new governed version and requires all three documents again. Existing versions remain in the audit history.':'Select the complete three-document package. Partial submissions are not accepted.'}</p><div class="status-row"><label class="status-item"><span class="label">Factsheet template PDF</span><input name="factsheet_template" type="file" accept="application/pdf,.pdf" required></label><label class="status-item"><span class="label">Presentation template PPTX</span><input name="presentation_template" type="file" accept="application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx" required></label><label class="status-item"><span class="label">Completed factsheet PDF</span><input name="completed_factsheet" type="file" accept="application/pdf,.pdf" required></label></div><div class="actions"><button class="button" type="submit">${replacement?'Validate and replace all documents':'Validate and store documents'}</button>${replacement?'<button id="cancel-document-replacement" class="button" type="button">Cancel replacement</button>':''}</div>${actionResult('documents-result')}</form>`;
  const bindForm=replacement=>{
    const form=document.querySelector('#documents-form');
    document.querySelector('#cancel-document-replacement')?.addEventListener('click',()=>window.location.reload());
    form?.addEventListener('submit',async event=>{event.preventDefault();const button=form.querySelector('button[type="submit"]'),note=document.querySelector('#documents-result');button.disabled=true;note.textContent='Validating identity, formats and file integrity…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/template-submissions`,{method:'POST',body:new FormData(form)}).then(json);note.textContent=`${result.asset_count} files validated and registered.`;reloadSoon()}catch(error){note.textContent=`Documents not accepted: ${error.message}`;button.disabled=false}})
  };
  root.innerHTML=`<h2>Mandatory three-document intake</h2><p class="muted">Upload one factsheet template PDF, one presentation template PPTX and one previously completed factsheet PDF. Files are validated, hashed and stored in private intake storage.</p>${assetRows}${complete?`<div class="onboarding-check pass"><strong>All three mandatory documents validated</strong><span>The governed document package is stored and Step 5 is complete. Continue to reconciliation, or replace the complete package deliberately.</span></div><div class="actions"><a class="button" href="/account-onboarding/${encodeURIComponent(subject)}/steps/reconciliation">Continue to reconciliation</a><button id="replace-documents" class="button" type="button" aria-expanded="false" aria-controls="document-replacement-slot">Replace documents</button></div><div id="document-replacement-slot"></div>`:formMarkup(false)}`;
  if(!complete){bindForm(false);return}
  document.querySelector('#replace-documents')?.addEventListener('click',event=>{const button=event.currentTarget,slot=document.querySelector('#document-replacement-slot');slot.innerHTML=formMarkup(true);button.hidden=true;button.setAttribute('aria-expanded','true');bindForm(true)})
};
const renderReconciliation=data=>{
  const v=data.validation,s=data.subject,n=data.nav,p=data.positions,isDerived=['filtered_portfolio','composite_portfolio'].includes(s.subject_type);
  const calculation=(data.checks||[]).find(row=>row.check_id==='data_import_reconciled')||{};
  const calculationEvidence=calculation.evidence_json||{};
  const calculationState=calculationEvidence.state||'';
  const calculationTime=calculation.checked_at?new Date(calculation.checked_at).toLocaleString('en-HK',{timeZone:'Asia/Hong_Kong',dateStyle:'medium',timeStyle:'medium'}):'';
  const calculationMessage=calculation.message||'The governed calculation has not been run for this portfolio.';
  const reconciliationResults=[...(v.results||[]).filter(row=>row.check_id!=='prior_onboarding_gates_passed'),data.reconciliation_prerequisites].filter(Boolean);
  let calculationMarkup='';
  if(isDerived&&calculation.status==='pass')calculationMarkup=`<div class="onboarding-check pass" style="margin-top:18px"><strong>Governed calculation completed successfully</strong><span>${esc(calculationMessage)}${calculationTime?` Completed ${esc(calculationTime)} HKT.`:''}</span></div>`;
  else if(isDerived&&calculation.status==='pending'&&['dispatching','queued'].includes(calculationState))calculationMarkup=`<div class="onboarding-check pending" style="margin-top:18px"><strong>Governed calculation is running</strong><span>${esc(calculationMessage)}${calculationTime?` Requested ${esc(calculationTime)} HKT.`:''}</span></div>${actionResult('calculation-result')}`;
  else if(isDerived)calculationMarkup=`${calculation.status==='fail'?`<div class="warning" style="margin-top:18px"><strong>Governed calculation needs attention.</strong> ${esc(calculationMessage)}</div>`:''}<div class="actions"><button id="run-calculation" class="button" type="button">${calculation.status==='fail'?'Run governed calculation again':'Run governed calculation'}</button></div>${actionResult('calculation-result')}`;
  root.innerHTML=`<h2>Deterministic financial reconciliation</h2><p class="muted">The language model does not calculate or waive these gates. The system verifies imported history, positions or components, benchmark/FX alignment, risk analytics and report assets.</p>${statusRows([['NAV coverage',`${n.first_date||'—'} – ${n.latest_date||'—'}`],['NAV observations',n.observations||0],['Latest positions',p.latest_date||'Not applicable'],['Position rows',p.rows||0],['Evidence SHA-256',v.evidence_sha256]])}${checks(reconciliationResults)}${calculationMarkup}<div class="actions"><button id="reconcile" class="button" type="button">Confirm reconciliation and continue</button></div>${actionResult('reconcile-result')}`;
  const pollCalculation=async remaining=>{if(remaining<=0){const note=document.querySelector('#calculation-result');if(note)note.textContent='The calculation is taking longer than expected. Its final result will remain available here after a later refresh or sign-in.';return}await new Promise(resolve=>setTimeout(resolve,5000));try{const latest=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/preview`).then(json),check=(latest.checks||[]).find(row=>row.check_id==='data_import_reconciled'),state=check?.evidence_json?.state||'';if(check&&check.status==='pass'){window.location.reload();return}if(check&&check.status==='fail'){window.location.reload();return}if(check&&check.status==='pending'&&['dispatching','queued'].includes(state)){pollCalculation(remaining-1);return}pollCalculation(remaining-1)}catch{pollCalculation(remaining-1)}};
  const calculationButton=document.querySelector('#run-calculation');
  calculationButton?.addEventListener('click',async()=>{const note=document.querySelector('#calculation-result');calculationButton.disabled=true;note.textContent='Starting the allow-listed Azure calculation job…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/calculations/queue`,{method:'POST'}).then(json);note.textContent=result.message;if(result.status==='already_complete'){window.location.reload();return}pollCalculation(120)}catch(error){note.textContent=`Calculation was not queued: ${error.message}`;calculationButton.disabled=false}});
  if(isDerived&&calculation.status==='pending'&&['dispatching','queued'].includes(calculationState))pollCalculation(120);
  document.querySelector('#reconcile').addEventListener('click',async event=>{const button=event.currentTarget,note=document.querySelector('#reconcile-result');button.disabled=true;note.textContent='Confirming reconciliation and advancing the onboarding case…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/reconciliation/confirm`,{method:'POST'}).then(json);note.textContent='Reconciliation complete. Opening historical reporting…';setTimeout(()=>window.location.href=result.next_page,900)}catch(error){note.textContent=`Reconciliation remains locked: ${error.message}`;button.disabled=false}})
};
const historyProcessing=status=>['queued','freezing_snapshots','rendering','validating'].includes(String(status||''));
const historyHkt=value=>{if(!value)return'';const parsed=new Date(value);return Number.isNaN(parsed.getTime())?'':parsed.toLocaleString('en-HK',{timeZone:'Asia/Hong_Kong',dateStyle:'medium',timeStyle:'medium'})};
const historyLatestUpdate=h=>{const b=h.batch||{},values=[b.completed_at,b.started_at,b.requested_at,...(h.items||[]).map(item=>item.updated_at)].filter(Boolean).map(value=>new Date(value)).filter(value=>!Number.isNaN(value.getTime())).sort((a,b)=>b-a);return values.length?historyHkt(values[0]):''};
const historySignatureFor=data=>{const h=data.historical_reports||{},b=h.batch||{};return JSON.stringify([b.batch_id||'',b.status||'',b.rendered_artifact_count||0,b.validated_artifact_count||0,b.manifest_sha256||'',b.error_code||'',b.error_message||'',h.approved||false,(h.items||[]).map(item=>[item.generation_run_id,item.status,item.updated_at])])};
const historyState=h=>{const b=h.batch;if(!b)return{tone:'pending',title:'Historical report generation has not started',message:'Queue all completed months when you are ready. No report files have been created yet.',poll:false};const expected=Number(b.expected_artifact_count||0),rendered=Number(b.rendered_artifact_count||0),validated=Number(b.validated_artifact_count||0),progress=`${validated} of ${expected} files validated${rendered?` · ${rendered} rendered`:''}`;if(b.status==='failed')return{tone:'fail',title:'Historical report generation needs attention',message:`${b.error_message||'The private renderer did not complete the batch.'} No batch approval was recorded.`,poll:false};if(b.status==='approved'||h.approved)return{tone:'pass',title:'Historical report batch approved',message:`All ${expected} private files remain validated and the owner approval is recorded.`,poll:false};if(b.status==='awaiting_owner_approval')return{tone:'pass',title:'Historical report generation completed successfully',message:`${progress}. The immutable manifest is ready for your review and approval.`,poll:false};if(b.status==='queued')return{tone:'pending',title:'Historical report generation is queued',message:`The private Azure renderer has accepted the batch. ${progress}. This page checks for progress automatically.`,poll:true};if(historyProcessing(b.status))return{tone:'pending',title:'Historical reports are being generated',message:`The private renderer is ${label(b.status).toLowerCase()}. ${progress}. This page checks for progress automatically.`,poll:true};return{tone:'pending',title:`Historical batch status: ${label(b.status)}`,message:`${progress}. This page will continue checking for a final result.`,poll:true}};
const historyDisplayChecks=h=>{const processing=historyProcessing(h.batch?.status),messages={all_artifacts_validated:'Private PDF and PPTX files are still being rendered and validated.',batch_manifest_finalized:'The immutable batch manifest will be finalized after every file validates.'};return(h.validation.results||[]).map(row=>processing&&row.status!=='pass'&&messages[row.check_id]?{...row,status:'pending',message:messages[row.check_id]}:row)};
const historyMarkup=data=>{const h=data.historical_reports,b=h.batch,items=h.items||[],state=historyState(h),updated=historyLatestUpdate(h),expected=Number(b?.expected_artifact_count||0),validated=Number(b?.validated_artifact_count||0),percent=expected?Math.min(100,Math.round(validated/expected*100)):0;return `<h2>Historical monthly report backfill</h2><p class="muted">Every completed month must have one validated factsheet and one validated presentation before approval.</p>${b?statusRows([['Batch status',label(b.status)],['Completed months',b.expected_month_count],['Validated files',`${validated} / ${expected}`],['Manifest',String(b.manifest_sha256||'Pending').slice(0,16)] ]):'<p class="muted">No batch has been queued.</p>'}<div id="history-live-status" class="onboarding-check ${esc(state.tone)}" role="status" aria-live="polite" aria-atomic="true"><strong>${esc(state.title)}</strong><span>${esc(state.message)}${updated?` Last server update: ${esc(updated)} HKT.`:''} <span id="history-last-checked">Status checked just now.</span></span>${b?`<progress value="${validated}" max="${Math.max(1,expected)}" aria-label="Historical report validation progress">${percent}%</progress>`:''}</div>${checks(historyDisplayChecks(h))}${items.filter(x=>x.status==='validated').map(x=>`<div class="status-item"><span class="label">${esc(x.period_end)} · ${esc(label(x.report_type))}</span><strong>Validated</strong><a href="/api/onboarding/${encodeURIComponent(subject)}/historical-reports/${encodeURIComponent(x.generation_run_id)}/download">Download private review file</a></div>`).join('')}<div id="history-action"></div>`};
const scheduleHistoryPoll=delay=>{if(historyPollTimer)clearTimeout(historyPollTimer);historyPollTimer=setTimeout(refreshHistory,delay)};
const refreshHistory=async()=>{if(document.hidden){scheduleHistoryPoll(15000);return}try{const latest=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/preview`).then(json),nextSignature=historySignatureFor(latest);historyPollFailures=0;if(nextSignature!==historySignature){renderHistory(latest);return}const checked=document.querySelector('#history-last-checked');if(checked)checked.textContent=`Status checked ${new Date().toLocaleTimeString('en-HK',{timeZone:'Asia/Hong_Kong'})} HKT.`;if(historyState(latest.historical_reports).poll)scheduleHistoryPoll(5000)}catch(error){historyPollFailures+=1;const checked=document.querySelector('#history-last-checked');if(checked)checked.textContent=`Automatic status check failed: ${error.message}. Retrying automatically.`;scheduleHistoryPoll(Math.min(30000,5000*historyPollFailures))}};
const renderHistory=data=>{if(historyPollTimer){clearTimeout(historyPollTimer);historyPollTimer=null}historySignature=historySignatureFor(data);root.innerHTML=historyMarkup(data);const h=data.historical_reports,state=historyState(h),target=document.querySelector('#history-action'),templateStale=(h.validation?.results||[]).some(row=>row.check_id==='template_versions_locked'&&row.status!=='pass');if(!h.batch&&data.may_queue_historical_reports)target.innerHTML=`<div class="actions"><button id="history-queue" class="button" type="button">Queue all completed months</button></div>${actionResult('history-result')}`;else if((h.batch?.status==='failed'||templateStale)&&data.may_queue_historical_reports)target.innerHTML=`<div class="warning">${templateStale?'The report design contract changed. The existing files cannot be approved and must be regenerated from the current subject-specific template and completed example.':'The previous private render did not complete.'}</div><div class="actions"><button id="history-queue" class="button" type="button">${templateStale?'Regenerate with current approved design':'Retry historical report generation'}</button></div>${actionResult('history-result')}`;else if(h.approved)target.innerHTML='<div class="onboarding-check pass"><strong>Historical batch approved</strong><span>Every expected artifact passed validation and remains private until activation.</span></div>';else if(h.validation.ready_for_owner_approval&&data.may_approve_historical_reports)target.innerHTML=`<label style="display:flex;gap:10px;align-items:flex-start"><input id="history-consent" type="checkbox"> <span>I reviewed the complete batch and approve evidence ${esc(h.validation.evidence_sha256)}.</span></label><div class="actions"><button id="history-approve" class="button" type="button" disabled>Approve historical batch</button></div>${actionResult('history-result')}`;else if(!state.poll)target.innerHTML='<div class="warning">Approval remains locked until all expected monthly files validate.</div>';document.querySelector('#history-queue')?.addEventListener('click',async event=>{const button=event.currentTarget,note=document.querySelector('#history-result');button.disabled=true;note.textContent=templateStale?'Superseding the stale batch and binding a new batch to the current approved design…':h.batch?.status==='failed'?'Starting a clean retry of the private report batch…':'Queuing completed months…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/historical-reports/queue`,{method:'POST'}).then(json);note.textContent=`Historical report generation ${result.status}. Progress will update automatically.`;scheduleHistoryPoll(800)}catch(error){note.textContent=`Queue failed: ${error.message}`;button.disabled=false}});const consent=document.querySelector('#history-consent'),button=document.querySelector('#history-approve');consent?.addEventListener('change',()=>button.disabled=!consent.checked);button?.addEventListener('click',async()=>{const note=document.querySelector('#history-result');button.disabled=true;note.textContent='Revalidating the complete batch…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/historical-reports`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({evidence_sha256:h.validation.evidence_sha256,approved:true})}).then(json);note.textContent='Historical batch approved.';scheduleHistoryPoll(800)}catch(error){note.textContent=`Approval failed: ${error.message}`;button.disabled=false}});if(state.poll)scheduleHistoryPoll(5000)};
const renderPreview=data=>{const v=data.validation;root.innerHTML=`<h2>Private website preview</h2><p class="muted">Review the same data contract, history, benchmark alignment, templates and feature states that will be exposed after activation.</p>${statusRows([['Subject',data.subject.subject_id],['Lifecycle',label(data.subject.lifecycle_status)],['Reporting currency',data.subject.reporting_currency],['Latest NAV',data.nav.latest_date],['Evidence SHA-256',v.evidence_sha256]])}${checks(v.results)}<div id="preview-action"></div>`;const target=document.querySelector('#preview-action');if(data.preview_approved)target.innerHTML='<div class="onboarding-check pass"><strong>Private preview approved</strong><span>Final activation remains a separate owner action.</span></div>';else if(v.ready_for_owner_preview_approval&&data.may_approve_preview)target.innerHTML=`<label style="display:flex;gap:10px;align-items:flex-start"><input id="preview-consent" type="checkbox"> <span>I reviewed this private preview and approve the current evidence hash.</span></label><div class="actions"><button id="preview-approve" class="button" type="button" disabled>Approve private preview</button></div>${actionResult('preview-result')}`;else target.innerHTML='<div class="warning">Preview approval remains locked until every validation result passes.</div>';const consent=document.querySelector('#preview-consent'),button=document.querySelector('#preview-approve');consent?.addEventListener('change',()=>button.disabled=!consent.checked);button?.addEventListener('click',async()=>{const note=document.querySelector('#preview-result');button.disabled=true;note.textContent='Revalidating and recording approval…';try{await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/website-preview`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({evidence_sha256:v.evidence_sha256,approved:true})}).then(json);note.textContent='Private preview approved.';reloadSoon()}catch(error){note.textContent=`Approval failed: ${error.message}`;button.disabled=false}})};
const renderActivation=data=>{const a=data.activation;root.innerHTML=`<h2>Final owner activation</h2><p class="muted">Activation publishes this subject to authorized users, enables its scheduled ingestion and unlocks the next account in the connection queue only after you return there and advance it.</p>${statusRows([['Subject',data.subject.subject_id],['Lifecycle',label(data.subject.lifecycle_status)],['Validation evidence',a.scope.validation_sha256],['Preview approval',a.scope.preview_approval_sha256||'Missing'],['Historical approval',a.scope.historical_approval_sha256||'Missing'],['Activation evidence',a.evidence_sha256]])}<div id="activation-action"></div>`;const target=document.querySelector('#activation-action');if(data.subject.status==='active')target.innerHTML='<div class="onboarding-check pass"><strong>Account active</strong><span>The final owner activation is recorded.</span></div>';else if(a.ready&&data.may_activate)target.innerHTML=`<label class="status-item"><span class="label">Type ${esc(a.confirmation)} exactly</span><input id="activation-confirmation" autocomplete="off"></label><div class="actions"><button id="activate" class="button" type="button" disabled>Activate account</button></div>${actionResult('activation-result')}`;else target.innerHTML='<div class="warning">Activation remains blocked until the historical batch and private preview are approved and every earlier check passes.</div>';const input=document.querySelector('#activation-confirmation'),button=document.querySelector('#activate');input?.addEventListener('input',()=>button.disabled=input.value!==a.confirmation);button?.addEventListener('click',async()=>{const note=document.querySelector('#activation-result');button.disabled=true;note.textContent='Revalidating every gate and activating…';try{const result=await fetch(`/api/onboarding/${encodeURIComponent(subject)}/approvals/activation`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({evidence_sha256:a.evidence_sha256,confirmation:input.value})}).then(json);note.textContent='Account activated. Opening its account page…';setTimeout(()=>window.location.href=result.next_page,900)}catch(error){note.textContent=`Activation failed: ${error.message}`;button.disabled=false}})};
fetch(`/api/onboarding/${encodeURIComponent(subject)}/preview`).then(json).then(data=>({connection:renderConnection,account:renderAccount,'financial-contract':renderFinancial,'external-data':renderExternal,documents:renderDocuments,reconciliation:renderReconciliation,'historical-reports':renderHistory,'private-preview':renderPreview,activation:renderActivation}[step]||renderConnection)(data)).catch(error=>{root.innerHTML=`<div class="warning">This page remains fail-closed. ${esc(error.message)}</div>`});
})();
</script>
"""


@app.get(
    "/account-onboarding/{subject_id}/steps/{step_slug}",
    response_class=HTMLResponse,
)
def account_onboarding_step_page(
    subject_id: str,
    step_slug: str,
    request: Request,
    user: dict[str, str] = Depends(_onboarding_user),
) -> str:
    steps = [slug for slug, _ in ONBOARDING_STEP_PAGES]
    if step_slug not in steps:
        raise HTTPException(status_code=404, detail="Onboarding step not found")
    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=404, detail="Onboarding subject not found") from error
    with _database() as connection:
        case = connection.execute(
            """
            SELECT c.target_subject_id, c.stage, c.status, c.requested_history_start,
                   c.reporting_currency, s.display_name, s.lifecycle_status
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if case is None:
            raise HTTPException(status_code=404, detail="Onboarding subject not found")
        checks = connection.execute(
            """
            SELECT check_id, status, message FROM account_onboarding_checks
            WHERE tenant_id = %s AND case_id = (
                SELECT case_id FROM account_onboarding_cases
                WHERE tenant_id = %s AND target_subject_id = %s
            ) ORDER BY check_id
            """,
            (TENANT_ID, TENANT_ID, subject),
        ).fetchall()
    index = steps.index(step_slug)
    current_by_stage = {
        "discovered": 0, "data_import": 1, "financial_contract": 2,
        "analytics_configuration": 3, "template_intake": 4,
        "reconciliation": 5, "historical_reporting": 6,
        "website_preview": 7, "activation_ready": 8, "active": 9,
    }.get(str(case["stage"]), 0)
    if index > current_by_stage:
        raise HTTPException(
            status_code=409,
            detail="This onboarding page remains locked until every earlier stage is complete",
        )
    title = dict(ONBOARDING_STEP_PAGES)[step_slug]
    navigation = _platform_navigation(user, active="account-onboarding")
    viewer = escape(user["email"] or "authorized user")
    role_label = escape(user.get("role", "owner").title())
    step_cards = "".join(
        (
            f'<a class="status-item" href="/account-onboarding/{escape(subject, quote=True)}/steps/{slug}"><span class="label">Step {position + 1}</span><strong>{escape("Complete" if position < current_by_stage or current_by_stage >= len(steps) else "Current")}</strong><span class="muted">{escape(label)}</span></a>'
            if position <= current_by_stage
            else f'<div class="status-item" aria-disabled="true"><span class="label">Step {position + 1}</span><strong>Locked</strong><span class="muted">{escape(label)}</span></div>'
        )
        for position, (slug, label) in enumerate(ONBOARDING_STEP_PAGES)
    )
    check_cards = "".join(
        f'<div class="onboarding-check {escape(str(row["status"]))}"><strong>{escape(str(row["check_id"]).replace("_", " ").title())}</strong><span>{escape(str(row["message"]))}</span></div>'
        for row in checks
    )
    previous = steps[index - 1] if index > 0 else None
    next_step = steps[index + 1] if index + 1 < len(steps) else None
    pager = ""
    if previous:
        pager += f'<a class="button" href="/account-onboarding/{escape(subject, quote=True)}/steps/{previous}">Previous page</a>'
    if next_step and index + 1 <= current_by_stage:
        pager += f'<a class="button" href="/account-onboarding/{escape(subject, quote=True)}/steps/{next_step}">Next page</a>'
    script = ONBOARDING_STEP_PAGE_SCRIPT.replace(
        "__SUBJECT__", json.dumps(subject)
    ).replace("__STEP__", json.dumps(step_slug))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · {PLATFORM_NAME}</title><style>{ACCOUNT_THEME_CSS}</style></head><body><header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header><main><div class="breadcrumbs"><a href="/">Accounts</a><span>/</span><a href="/account-onboarding">Account onboarding</a><span>/</span><span>{escape(subject)}</span><span>/</span><span>{escape(title)}</span></div><div class="account-hero"><div class="account-hero-copy"><div class="label">Page {index + 1} of {len(steps)}</div><h1>{escape(title)}</h1><div class="muted">{escape(str(case["display_name"]))} · {escape(str(case["status"]).replace("_", " ").title())}</div></div></div><section class="grid"><div class="card wide"><div class="label">Page-by-page workflow</div><div class="status-row">{step_cards}</div></div><div class="card wide"><div class="label">Current page</div><div id="step-workspace"><div class="muted">Loading current evidence and permitted actions…</div></div></div><div class="card wide"><div class="label">All evidence and approval gates</div><div class="onboarding-checks">{check_cards}</div></div><div class="card wide"><div class="actions">{pager}</div></div></section></main>{script}</body></html>"""


@app.get("/account-onboarding", response_class=HTMLResponse)
def account_onboarding_page(
    request: Request, user: dict[str, str] = Depends(_onboarding_user)
) -> str:
    if onboarding_v2_enabled():
        return _platform_shell(
            user, active="account-onboarding", title="Account onboarding",
            description=onboarding_v2_web.ONBOARDING_DESCRIPTION,
            content=onboarding_v2_web.ONBOARDING_CONTENT, script=onboarding_v2_web.ONBOARDING_SCRIPT,
        )
    return _render_platform_page(user, section="account-onboarding")


@app.get("/account-onboarding/classic", response_class=HTMLResponse)
def account_onboarding_classic_page(
    request: Request, user: dict[str, str] = Depends(_onboarding_user)
) -> Response:
    """The earlier onboarding page, kept for master accounts and cases started before the new onboarding."""

    if not onboarding_v2_enabled():
        return RedirectResponse(url="/account-onboarding", status_code=307)
    page = _render_platform_page(user, section="account-onboarding")
    for old, new in _CLASSIC_PAGE_REPLACEMENTS:
        page = page.replace(old, new, 1)
    for opening in _CLASSIC_HIDDEN_CARDS:
        page = page.replace(opening, opening.replace('<div class="card wide"', _HIDDEN_CARD, 1), 1)
    return HTMLResponse(page)


# With the new onboarding on, the earlier page keeps only what the new one does not
# cover: starting a master account and finishing cases started on the earlier wizard.
# The new Account onboarding, Broker connections and portfolio builder pages replace
# everything else, so those cards stay in the page (its script expects them) but hidden.
_HIDDEN_CARD = '<div class="card wide" style="display:none" aria-hidden="true"'
_CLASSIC_HIDDEN_CARDS = (
    '<div class="card wide"><div class="label">Guided onboarding wizard</div>',
    '<div class="card wide"><div class="label">Choose what you are onboarding</div>',
    '<div class="card wide" id="derived-portfolio-studio">',
    '<div class="card wide"><div class="label">IBKR connection prerequisite</div>',
    '<div class="card wide"><div class="label">Platform-wide presentation requirements</div>',
    '<div class="card wide"><div class="label">Approved implementation sequence</div>',
    '<div class="card wide"><div class="label">Existing v1.1 source accounts</div>',
)
_CLASSIC_TITLE = "Master accounts and earlier cases"
_CLASSIC_PAGE_REPLACEMENTS = (
    ("<title>Account onboarding ·", f"<title>{_CLASSIC_TITLE} ·"),
    ("<span>Account onboarding</span></div>",
     '<a href="/account-onboarding">Account onboarding</a><span aria-hidden="true">/</span>'
     f"<span>{_CLASSIC_TITLE}</span></div>"),
    ("<h1>Account onboarding</h1>", f"<h1>{_CLASSIC_TITLE}</h1>"),
    ("Controlled account discovery, source configuration, reconciliation gates and feature activation for "
     "safely scaling the platform beyond the U16088571 reference account.",
     "Starting a master account, and cases started on the earlier onboarding wizard."),
    ('<div class="card wide"><div class="label">Scalable account onboarding</div>',
     '<div class="card wide"><div class="label">Where to start</div>'
     "<h2>New accounts and portfolios start on Account onboarding</h2>"
     '<p class="muted">Use this page only to start a master account or to finish a case started '
     "before the new onboarding. Broker logins are on Broker connections, and new portfolios start "
     "in the portfolio builder.</p><div class=\"actions\">"
     '<a class="button" href="/account-onboarding">Account onboarding</a>'
     '<a class="button" href="/broker-connections">Broker connections</a>'
     '<a class="button" href="/account-onboarding/new-portfolio">Portfolio builder</a></div></div>'
     '<div class="card wide"><div class="label">Scalable account onboarding</div>'),
)


def _reconciliation_prerequisite_status(checks: list[dict]) -> dict:
    """Return the Step-6 gate without including future onboarding stages."""

    by_id = {str(row.get("check_id")): row for row in checks}
    results = []
    for check_id in RECONCILIATION_REQUIRED_CHECK_IDS:
        row = by_id.get(check_id)
        results.append(
            {
                "check_id": check_id,
                "status": str(row.get("status")) if row is not None else "missing",
                "message": (
                    str(row.get("message") or "")
                    if row is not None
                    else "Required onboarding evidence is missing."
                ),
            }
        )
    failed = [row["check_id"] for row in results if row["status"] != "pass"]
    return {
        "check_id": "reconciliation_prerequisites",
        "status": "pass" if not failed else "fail",
        "message": (
            "All Step 1–6 reconciliation prerequisites passed."
            if not failed
            else "Reconciliation is blocked by: "
            + ", ".join(value.replace("_", " ") for value in failed)
            + "."
        ),
        "failed_check_ids": failed,
        "results": results,
    }


def _onboarding_preview_payload(connection, subject_id: str) -> dict:
    subject = connection.execute(
        """
        SELECT s.subject_id, s.subject_type, s.display_name, s.base_currency,
               s.reporting_currency, s.history_start_date, s.lifecycle_status,
               s.metadata_json,
               c.case_id, c.stage, c.status, c.source_system,
               c.source_account_id, c.requested_history_start,
               c.configuration_json
        FROM platform_subjects s
        JOIN account_onboarding_cases c
          ON c.tenant_id = s.tenant_id AND c.target_subject_id = s.subject_id
        WHERE s.tenant_id = %s AND s.subject_id = %s
        """,
        (TENANT_ID, subject_id),
    ).fetchone()
    if subject is None:
        raise HTTPException(status_code=404, detail="Onboarding subject not found")
    connection_row = connection.execute(
        """
        SELECT q.flex_source_id, q.display_name AS connection_name, q.query_id,
               q.status AS connection_status, q.schedule_enabled,
               c.display_name AS credential_name, c.token_fingerprint,
               b.status AS binding_status
        FROM subject_flex_source_bindings b
        JOIN ibkr_flex_queries q
          ON q.tenant_id = b.tenant_id AND q.flex_source_id = b.flex_source_id
        JOIN ibkr_flex_credentials c
          ON c.tenant_id = q.tenant_id AND c.credential_id = q.credential_id
        WHERE b.tenant_id = %s AND b.subject_id = %s AND b.is_primary
        ORDER BY b.updated_at DESC LIMIT 1
        """,
        (TENANT_ID, subject_id),
    ).fetchone()
    benchmark_catalog = connection.execute(
        """
        SELECT benchmark_id, symbol, name, provider, currency, return_convention
        FROM benchmark_master
        WHERE tenant_id = %s AND active
        ORDER BY name, benchmark_id
        """,
        (TENANT_ID,),
    ).fetchall()
    if str(subject["subject_type"]) in {"filtered_portfolio", "composite_portfolio"}:
        nav = connection.execute(
            """
            SELECT MIN(snapshot_date) AS first_date,
                   MAX(snapshot_date) AS latest_date,
                   COUNT(DISTINCT snapshot_date) AS observations
            FROM derived_portfolio_nav
            WHERE portfolio_id = %s
            """,
            (subject_id,),
        ).fetchone()
        if str(subject["subject_type"]) == "filtered_portfolio":
            positions = connection.execute(
                """
                SELECT MAX(snapshot_date) AS latest_date, COUNT(*) AS rows
                FROM derived_portfolio_positions WHERE portfolio_id = %s
                """,
                (subject_id,),
            ).fetchone()
        else:
            positions = {"latest_date": None, "rows": 0}
    else:
        nav = connection.execute(
            """
            SELECT MIN(snapshot_date) AS first_date,
                   MAX(snapshot_date) AS latest_date,
                   COUNT(DISTINCT snapshot_date) AS observations
            FROM nav_snapshots
            WHERE account_id = %s AND asset_class = 'TOTAL'
            """,
            (subject_id,),
        ).fetchone()
        positions = connection.execute(
            """
            SELECT MAX(snapshot_date) AS latest_date, COUNT(*) AS rows
            FROM position_snapshots WHERE account_id = %s
            """,
            (subject_id,),
        ).fetchone()
    calculation_definition = connection.execute(
        """
        SELECT definition_id, calculation_kind, definition_version,
               configuration_json, configuration_sha256, approval_status,
               effective_from, approved_at
        FROM subject_calculation_definitions
        WHERE tenant_id = %s AND subject_id = %s AND effective_to IS NULL
        ORDER BY definition_version DESC LIMIT 1
        """,
        (TENANT_ID, subject_id),
    ).fetchone()
    master_definition_row = connection.execute(
        """
        SELECT definition_id, definition_version, configuration_json,
               configuration_sha256, approval_status, effective_from,
               approved_at
        FROM master_account_definitions
        WHERE tenant_id = %s AND subject_id = %s AND effective_to IS NULL
        ORDER BY definition_version DESC LIMIT 1
        """,
        (TENANT_ID, subject_id),
    ).fetchone()
    benchmarks = connection.execute(
        """
        SELECT m.benchmark_id, b.name, b.symbol, b.currency,
               b.return_convention, m.mapping_type, m.reporting_currency,
               MAX(p.observation_date) AS latest_comparison_date,
               COUNT(p.observation_date) AS comparison_rows
        FROM portfolio_benchmark_mapping m
        JOIN benchmark_master b
          ON b.tenant_id = m.tenant_id AND b.benchmark_id = m.benchmark_id
        LEFT JOIN performance_comparison_daily p
          ON p.tenant_id = m.tenant_id AND p.subject_id = m.subject_id
         AND p.benchmark_id = m.benchmark_id AND p.quality_status <> 'fail'
         AND p.reporting_currency = m.reporting_currency
         AND p.calculation_version = CASE WHEN m.subject_id = 'alpha-world-ms'
             THEN %s WHEN m.subject_id = 'alpha-world-t' THEN %s ELSE 'benchmark-comparison-v1' END
        WHERE m.tenant_id = %s AND m.subject_id = %s
          AND m.effective_to IS NULL
        GROUP BY m.benchmark_id, b.name, b.symbol, b.currency,
                 b.return_convention, m.mapping_type, m.reporting_currency
        ORDER BY CASE WHEN m.mapping_type = 'primary' THEN 0 ELSE 1 END,
                 m.benchmark_id
        """,
        (MS_HKD_COMPARISON_VERSION, T_HKD_COMPARISON_VERSION, TENANT_ID, subject_id),
    ).fetchall()
    templates = connection.execute(
        """
        SELECT report_type, asset_role, version_label, approval_status,
               sha256, byte_count, metadata_json, updated_at
        FROM subject_report_template_assets
        WHERE tenant_id = %s AND subject_id = %s AND is_current
        ORDER BY report_type, asset_role
        """,
        (TENANT_ID, subject_id),
    ).fetchall()
    features = connection.execute(
        """
        SELECT feature_key, feature_state, reason
        FROM subject_feature_states
        WHERE tenant_id = %s AND subject_id = %s
        ORDER BY feature_key
        """,
        (TENANT_ID, subject_id),
    ).fetchall()
    checks = connection.execute(
        """
        SELECT check_id, status, message, evidence_json, checked_at
        FROM account_onboarding_checks
        WHERE tenant_id = %s AND case_id = %s
        ORDER BY check_id
        """,
        (TENANT_ID, subject["case_id"]),
    ).fetchall()
    approvals = connection.execute(
        """
        SELECT approval_id, approval_type, disclosure_version, destination,
               scope_json, scope_sha256, approved_at
        FROM account_onboarding_approvals
        WHERE tenant_id = %s AND subject_id = %s
          AND approval_status = 'approved'
        ORDER BY approval_type, approved_at DESC
        """,
        (TENANT_ID, subject_id),
    ).fetchall()
    historical_batch = connection.execute(
        """
        SELECT batch_id, reporting_start, reporting_end,
               expected_month_count, expected_artifact_count,
               template_evidence_sha256, source_evidence_sha256,
               status, manifest_sha256, rendered_artifact_count,
               validated_artifact_count, error_code, error_message,
               requested_at, started_at, completed_at, approved_at
        FROM onboarding_historical_report_batches
        WHERE tenant_id = %s AND subject_id = %s
          AND status <> 'superseded'
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (TENANT_ID, subject_id),
    ).fetchone()
    historical_items = []
    if historical_batch is not None:
        historical_items = connection.execute(
            """
            SELECT period_end, holdings_as_of, report_type, snapshot_id,
                   generation_run_id, status, container_name, blob_name,
                   artifact_sha256, byte_count, template_sha256,
                   validation_json, error_message, updated_at
            FROM onboarding_historical_report_items
            WHERE tenant_id = %s AND batch_id = %s
            ORDER BY period_end, report_type
            """,
            (TENANT_ID, historical_batch["batch_id"]),
        ).fetchall()
    subject_payload = dict(subject)
    subject_metadata = subject_payload.get("metadata_json") or {}
    if isinstance(subject_metadata, str):
        try:
            subject_metadata = json.loads(subject_metadata)
        except json.JSONDecodeError:
            subject_metadata = {}
    administrative_master = bool(
        str(subject_payload.get("subject_type")) == "master_account"
        and isinstance(subject_metadata, dict)
        and subject_metadata.get("reporting_mode") == "administrative_only"
    )
    nav_payload = dict(nav)
    position_payload = dict(positions)
    benchmark_payload = [dict(row) for row in benchmarks]
    template_payload = [dict(row) for row in templates]
    check_payload = [dict(row) for row in checks]
    approval_payload = [dict(row) for row in approvals]
    identity_approval = next(
        (
            row
            for row in approval_payload
            if row["approval_type"] == "presentation_identity"
        ),
        None,
    )
    identity_contract_valid = False
    if identity_approval is not None:
        identity_scope = identity_approval.get("scope_json")
        if isinstance(identity_scope, str):
            try:
                identity_scope = json.loads(identity_scope)
            except json.JSONDecodeError:
                identity_scope = None
        if isinstance(identity_scope, dict):
            try:
                expected_identity_scope = presentation_identity_scope(
                    subject_id=subject_id,
                    display_name=str(subject["display_name"]),
                    layout_policy=str(identity_scope.get("layout_policy") or ""),
                )
                identity_contract_valid = bool(
                    identity_scope == expected_identity_scope
                    and str(identity_approval["scope_sha256"])
                    == sha256_text(canonical_json(expected_identity_scope))
                )
            except RuntimeError:
                identity_contract_valid = False
    for row in check_payload:
        if row["check_id"] == "presentation_identity_approved":
            row["status"] = "pass" if administrative_master or identity_contract_valid else "fail"
            row["message"] = (
                "Presentation identity is not applicable to an administrative-only master."
                if administrative_master
                else "The owner-approved presentation identity matches the current account name."
                if identity_contract_valid
                else "Presentation identity approval is missing or stale after an account-name change."
            )
    try:
        historical_periods = discover_completed_reporting_periods(
            PostgresConnection(connection),
            subject_id=subject_id,
            reporting_start=str(subject["history_start_date"]),
            today=datetime.now(ZoneInfo("Asia/Hong_Kong")).date(),
            subject_type=str(subject["subject_type"]),
        )
        historical_validation = build_historical_report_batch_validation(
            subject=subject_payload,
            templates=template_payload,
            periods=historical_periods,
            batch=dict(historical_batch) if historical_batch is not None else None,
            items=[dict(row) for row in historical_items],
            container=INTERNAL_REPORT_CONTAINER,
        )
        historical_error = None
    except OnboardingReportError as error:
        historical_periods = []
        historical_validation = {
            "schema_version": 1,
            "subject_id": subject_id,
            "results": [
                {
                    "check_id": "historical_reporting_data_ready",
                    "status": "fail",
                    "message": str(error),
                    "evidence": {},
                }
            ],
            "ready_for_owner_approval": False,
            "evidence_sha256": sha256_text(str(error)),
        }
        historical_error = str(error)
    historical_approved = any(
        row["approval_type"] == "historical_report_batch" for row in approvals
    )
    historical_gate_passed = bool(administrative_master or (
        historical_approved
        and historical_batch is not None
        and str(historical_batch["status"]) == "approved"
        and historical_validation["ready_for_owner_approval"]
    ))
    for row in check_payload:
        if row["check_id"] == "historical_reports_backfilled":
            row["status"] = "pass" if historical_gate_passed else "pending"
            row["message"] = (
                "Historical reports are not applicable to an administrative-only master."
                if administrative_master
                else "The owner approved the complete integrity-bound historical report batch."
                if historical_gate_passed
                else "All completed reporting months must have a validated private factsheet and presentation before website-preview approval."
            )
    reconciliation_prerequisites = _reconciliation_prerequisite_status(check_payload)
    validation = build_physical_preview_validation(
        subject=subject_payload,
        nav=nav_payload,
        positions=position_payload,
        benchmarks=benchmark_payload,
        templates=template_payload,
        checks=check_payload,
    )
    return {
        "subject": subject_payload,
        "calculation_definition": (
            dict(calculation_definition) if calculation_definition is not None else None
        ),
        "master_definition": (
            dict(master_definition_row) if master_definition_row is not None else None
        ),
        "connection": dict(connection_row) if connection_row is not None else {
            "connection_name": "Imported v1.1 source",
            "connection_status": "validated",
            "schedule_enabled": True,
            "binding_status": "active",
        },
        "benchmark_catalog": [dict(row) for row in benchmark_catalog],
        "nav": nav_payload,
        "positions": position_payload,
        "benchmarks": benchmark_payload,
        "templates": template_payload,
        "features": [dict(row) for row in features],
        "checks": check_payload,
        "reconciliation_prerequisites": reconciliation_prerequisites,
        "approvals": approval_payload,
        "historical_reports": {
            "batch": dict(historical_batch) if historical_batch is not None else None,
            "items": [dict(row) for row in historical_items],
            "periods": historical_periods,
            "validation": historical_validation,
            "approved": historical_approved,
            "gate_passed": historical_gate_passed,
            "error": historical_error,
        },
        "validation": validation,
    }


@app.get("/api/onboarding/{subject_id}/preview")
def account_onboarding_preview(
    subject_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Return integrity-bound reconciliation evidence for one onboarding subject."""
    with _database() as connection:
        payload = _onboarding_preview_payload(connection, subject_id)
    preview_approved = any(
        row["approval_type"] == "website_preview" for row in payload["approvals"]
    )
    try:
        identity_candidate = presentation_identity_scope(
            subject_id=subject_id,
            display_name=str(payload["subject"]["display_name"]),
            layout_policy="platform_standard",
        )
        identity_candidate["display_name_sha256"] = sha256_text(
            str(payload["subject"]["display_name"])
        )
    except RuntimeError:
        identity_candidate = None
    activation_scope = _onboarding_activation_scope(payload)
    payload.update(
        {
            "activation_blocked": True,
            "preview_approved": preview_approved,
            "may_approve_preview": user["role"] == "owner",
            "may_queue_historical_reports": user["role"]
            in {"owner", "onboarding_operator"},
            "may_approve_historical_reports": user["role"] == "owner",
            "may_approve_financial_contract": user["role"] == "owner",
            "may_activate": user["role"] == "owner",
            "activation": activation_scope,
            "presentation_identity_candidate": identity_candidate,
            "external_market_data_disclosure": {
                "version": YAHOO_ONBOARDING_DISCLOSURE_VERSION,
                "scope": YAHOO_ONBOARDING_SCOPE,
                "scope_sha256": YAHOO_ONBOARDING_SCOPE_SHA256,
            },
            "message": (
                "The private preview passed deterministic validation and is awaiting "
                "owner sign-off. Activation remains separately blocked."
                if payload["validation"]["ready_for_owner_preview_approval"]
                and not preview_approved
                else "This reconciliation preview does not activate the account."
            ),
            "viewer": user["email"],
        }
    )
    return payload


def _onboarding_activation_scope(payload: dict) -> dict:
    """Return the immutable evidence an owner must review before activation."""

    approvals = {
        str(row["approval_type"]): row for row in payload.get("approvals", [])
    }
    preview = approvals.get("website_preview")
    history = approvals.get("historical_report_batch")
    subject = payload["subject"]
    metadata = subject.get("metadata_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    administrative_master = bool(
        str(subject.get("subject_type")) == "master_account"
        and isinstance(metadata, dict)
        and metadata.get("reporting_mode") == "administrative_only"
    )
    scope = {
        "schema_version": 1,
        "subject_id": str(subject["subject_id"]),
        "case_id": str(subject["case_id"]),
        "preview_approval_sha256": str(preview["scope_sha256"]) if preview else None,
        "historical_approval_sha256": str(history["scope_sha256"]) if history else None,
        "validation_sha256": str(payload["validation"]["evidence_sha256"]),
        "feature_states": {
            str(row["feature_key"]): str(row["feature_state"])
            for row in payload.get("features", [])
        },
    }
    ready = bool(
        str(subject["stage"]) == "activation_ready"
        and preview
        and (history or administrative_master)
        and all(
            row["status"] == "pass"
            for row in payload.get("checks", [])
            if row["check_id"] != "owner_activation"
        )
    )
    return {
        "ready": ready,
        "scope": scope,
        "evidence_sha256": sha256_text(canonical_json(scope)),
        "confirmation": f"ACTIVATE {subject['subject_id']}",
    }


@app.post("/api/onboarding/{subject_id}/calculations/queue")
def queue_onboarding_calculation(
    subject_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Start the single allow-listed derived refresh job after contract approval."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    request_id = str(uuid.uuid4())
    with _approval_database() as connection:
        row = connection.execute(
            """
            SELECT c.case_id, c.status, s.subject_type, d.approval_status,
                   r.status AS calculation_status,
                   r.message AS calculation_message,
                   r.evidence_json AS calculation_evidence,
                   r.checked_at AS calculation_checked_at
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            JOIN subject_calculation_definitions d
              ON d.tenant_id = c.tenant_id AND d.subject_id = c.target_subject_id
             AND d.effective_to IS NULL
            LEFT JOIN account_onboarding_checks r
              ON r.tenant_id = c.tenant_id AND r.case_id = c.case_id
             AND r.check_id = 'data_import_reconciled'
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            FOR UPDATE OF c
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if (
            row is None
            or str(row["status"]) in {"cancelled", "active"}
            or str(row["subject_type"])
            not in {"filtered_portfolio", "composite_portfolio"}
        ):
            raise HTTPException(
                status_code=409, detail="No derived calculation is available"
            )
        if str(row["approval_status"]) != "approved":
            raise HTTPException(
                status_code=409,
                detail="Approve the exact calculation contract before running it",
            )
        if (
            not ADMIN_ACTION_CONFIG.subscription_id
            or not ADMIN_ACTION_CONFIG.resource_group
        ):
            raise HTTPException(
                status_code=503, detail="The Azure calculation runner is unavailable"
            )
        calculation_evidence = row["calculation_evidence"] or {}
        if isinstance(calculation_evidence, str):
            try:
                calculation_evidence = json.loads(calculation_evidence)
            except json.JSONDecodeError:
                calculation_evidence = {}
        if (
            str(row["calculation_status"]) == "pending"
            and isinstance(calculation_evidence, dict)
            and calculation_evidence.get("state") in {"dispatching", "queued"}
            and row["calculation_checked_at"] is not None
            and datetime.now(timezone.utc) - row["calculation_checked_at"]
            < timedelta(hours=2)
        ):
            return {
                "status": "already_queued",
                "subject_id": subject,
                "execution_reference": calculation_evidence.get(
                    "execution_reference"
                ),
                "message": row["calculation_message"],
            }
        queued_at = utc_now_iso()
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pending',
                message = 'The governed calculation is being started.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'data_import_reconciled'
            """,
            (
                canonical_json(
                    {
                        "state": "dispatching",
                        "request_id": request_id,
                        "job_name": DERIVED_REFRESH_JOB_NAME,
                        "requested_at": queued_at,
                        "requested_by_identity_sha256": actor,
                    }
                ),
                TENANT_ID,
                row["case_id"],
            ),
        )
    try:
        reference = _default_job_starter(
            ADMIN_ACTION_CONFIG, DERIVED_REFRESH_JOB_NAME
        )
    except AdminActionError as error:
        with _approval_database() as connection:
            connection.execute(
                """
                UPDATE account_onboarding_checks
                SET status = 'fail',
                    message = 'The governed calculation job could not be started.',
                    evidence_json = %s::jsonb, checked_at = now()
                WHERE tenant_id = %s AND case_id = %s
                  AND check_id = 'data_import_reconciled'
                """,
                (
                    canonical_json(
                        {
                            "state": "dispatch_failed",
                            "request_id": request_id,
                            "job_name": DERIVED_REFRESH_JOB_NAME,
                            "error_code": error.code,
                        }
                    ),
                    TENANT_ID,
                    row["case_id"],
                ),
            )
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    with _approval_database() as connection:
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pending',
                message = 'The governed calculation is running. This page will show completion automatically.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'data_import_reconciled'
            """,
            (
                canonical_json(
                    {
                        "state": "queued",
                        "request_id": request_id,
                        "job_name": DERIVED_REFRESH_JOB_NAME,
                        "execution_reference": reference,
                        "requested_at": queued_at,
                        "requested_by_identity_sha256": actor,
                    }
                ),
                TENANT_ID,
                row["case_id"],
            ),
        )
    return {
        "status": "queued",
        "subject_id": subject,
        "job_name": DERIVED_REFRESH_JOB_NAME,
        "execution_reference": reference,
        "message": (
            "The governed calculation is running. This page will show completion "
            "automatically."
        ),
    }


@app.post("/api/onboarding/{subject_id}/analytics/queue")
def queue_onboarding_analytics(
    subject_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Run the allow-listed deterministic analytics gate for pending subjects."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    request_id = str(uuid.uuid4())
    with _approval_database() as connection:
        row = connection.execute(
            """
            SELECT c.case_id, c.status, s.lifecycle_status, s.subject_type,
                   EXISTS (
                       SELECT 1 FROM account_onboarding_approvals a
                       WHERE a.subject_id = c.target_subject_id
                         AND a.approval_type = 'external_market_data'
                         AND a.approval_status = 'approved'
                         AND a.revoked_at IS NULL
                   ) AS external_approved,
                   r.status AS analytics_status,
                   r.message AS analytics_message,
                   r.evidence_json AS analytics_evidence,
                   r.checked_at AS analytics_checked_at
            FROM account_onboarding_cases c
            JOIN platform_subjects s
              ON s.tenant_id = c.tenant_id AND s.subject_id = c.target_subject_id
            LEFT JOIN account_onboarding_checks r
              ON r.tenant_id = c.tenant_id AND r.case_id = c.case_id
             AND r.check_id = 'risk_analytics_validated'
            WHERE c.tenant_id = %s AND c.target_subject_id = %s
            FOR UPDATE OF c
            """,
            (TENANT_ID, subject),
        ).fetchone()
        if row is None or str(row["status"]) in {"cancelled", "active"}:
            raise HTTPException(status_code=409, detail="Onboarding analytics are unavailable")
        if not bool(row["external_approved"]):
            raise HTTPException(
                status_code=409,
                detail="Approve the bounded external market-data scope first",
            )
        if str(row["analytics_status"]) == "pass":
            return {
                "status": "already_complete",
                "subject_id": subject,
                "completed_at": row["analytics_checked_at"],
                "message": row["analytics_message"],
            }
        analytics_evidence = row["analytics_evidence"] or {}
        if isinstance(analytics_evidence, str):
            try:
                analytics_evidence = json.loads(analytics_evidence)
            except json.JSONDecodeError:
                analytics_evidence = {}
        if (
            str(row["analytics_status"]) == "pending"
            and isinstance(analytics_evidence, dict)
            and analytics_evidence.get("state") in {"dispatching", "queued"}
            and row["analytics_checked_at"] is not None
            and datetime.now(timezone.utc) - row["analytics_checked_at"]
            < timedelta(hours=2)
        ):
            return {
                "status": "already_queued",
                "subject_id": subject,
                "execution_reference": analytics_evidence.get("execution_reference"),
                "message": row["analytics_message"],
            }
        job_name = (
            DERIVED_REFRESH_JOB_NAME
            if str(row["subject_type"]) in {"filtered_portfolio", "composite_portfolio"}
            else ONBOARDING_ANALYTICS_JOB_NAME
        )
        queued_at = utc_now_iso()
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pending',
                message = 'Deterministic analytics are being started.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'risk_analytics_validated'
            """,
            (
                canonical_json(
                    {
                        "state": "dispatching",
                        "request_id": request_id,
                        "job_name": job_name,
                        "requested_at": queued_at,
                        "requested_by_identity_sha256": actor,
                    }
                ),
                TENANT_ID,
                row["case_id"],
            ),
        )
    try:
        reference = _default_job_starter(
            ADMIN_ACTION_CONFIG, job_name
        )
    except AdminActionError as error:
        with _approval_database() as connection:
            connection.execute(
                """
                UPDATE account_onboarding_checks
                SET status = 'fail',
                    message = 'The deterministic analytics job could not be started.',
                    evidence_json = %s::jsonb, checked_at = now()
                WHERE tenant_id = %s AND case_id = %s
                  AND check_id = 'risk_analytics_validated'
                """,
                (
                    canonical_json(
                        {
                            "state": "dispatch_failed",
                            "request_id": request_id,
                            "job_name": job_name,
                            "error_code": error.code,
                        }
                    ),
                    TENANT_ID,
                    row["case_id"],
                ),
            )
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    with _approval_database() as connection:
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pending',
                message = 'Deterministic analytics are running. This page will show completion automatically.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'risk_analytics_validated'
            """,
            (
                canonical_json(
                    {
                        "state": "queued",
                        "request_id": request_id,
                        "job_name": job_name,
                        "execution_reference": reference,
                        "requested_at": queued_at,
                        "requested_by_identity_sha256": actor,
                    }
                ),
                TENANT_ID,
                row["case_id"],
            ),
        )
    return {
        "status": "queued",
        "subject_id": subject,
        "job_name": job_name,
        "execution_reference": reference,
        "message": (
            "Deterministic calculation, benchmark, sector and crisis-risk validation "
            "is running. This page will show completion automatically."
        ),
    }


@app.post("/api/onboarding/{subject_id}/reconciliation/confirm")
def confirm_onboarding_reconciliation(
    subject_id: str,
    request: Request,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Recompute deterministic evidence and unlock historical rendering only on pass."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    try:
        with _approval_database() as connection:
            payload = _onboarding_preview_payload(connection, subject)
            case = payload["subject"]
            if str(case["stage"]) not in {"reconciliation", "historical_reporting"}:
                raise HTTPException(
                    status_code=409,
                    detail="Reconciliation cannot be confirmed at the current stage",
                )
            validation_by_id = {
                str(row["check_id"]): row
                for row in payload["validation"]["results"]
            }
            document_validation = validation_by_id.get("report_documents_validated")
            if document_validation and document_validation["status"] == "pass":
                connection.execute(
                    """
                    UPDATE account_onboarding_checks
                    SET status = 'pass', message = %s, evidence_json = %s::jsonb,
                        checked_at = now()
                    WHERE tenant_id = %s AND case_id = %s
                      AND check_id = 'report_documents_validated'
                    """,
                    (
                        document_validation["message"],
                        canonical_json(document_validation.get("evidence") or {}),
                        TENANT_ID,
                        case["case_id"],
                    ),
                )
            payload = _onboarding_preview_payload(connection, subject)
            prerequisites = payload["reconciliation_prerequisites"]
            failed_checks = list(prerequisites["failed_check_ids"])
            required_validation_ids = {
                "subject_registered",
                "nav_history_coverage",
                "positions_aligned",
                "benchmarks_reconciled",
                "report_documents_validated",
            }
            failed_validation = [
                str(row["check_id"])
                for row in payload["validation"]["results"]
                if row["check_id"] in required_validation_ids
                and row["status"] != "pass"
            ]
            if failed_checks or failed_validation:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "reconciliation_incomplete",
                        "failed_checks": sorted(set(failed_checks)),
                        "failed_validation": sorted(set(failed_validation)),
                    },
                )
            connection.execute(
                """
                UPDATE account_onboarding_cases
                SET stage = 'historical_reporting', status = 'in_progress',
                    configuration_json = configuration_json || %s::jsonb,
                    updated_at = now()
                WHERE tenant_id = %s AND case_id = %s AND stage = 'reconciliation'
                """,
                (
                    canonical_json(
                        {
                            "reconciliation_confirmation": {
                                "validation_sha256": payload["validation"][
                                    "evidence_sha256"
                                ],
                                "confirmed_at": datetime.now(timezone.utc).isoformat(),
                            }
                        }
                    ),
                    TENANT_ID,
                    case["case_id"],
                ),
            )
    except InsufficientPrivilege as error:
        correlation_id = str(getattr(request.state, "correlation_id", "unavailable"))
        now_iso = utc_now_iso()
        SECURITY_AUDIT_LOGGER.error(
            "Onboarding reconciliation privilege configuration failed subject=%s correlation_id=%s",
            subject,
            correlation_id,
        )
        try:
            with _approval_database() as incident_connection:
                incident_connection.execute(
                    """
                    INSERT INTO system_incidents(
                        incident_key, check_id, severity, status, description,
                        evidence_json, first_seen_at, last_seen_at, occurrence_count
                    ) VALUES (%s, %s, 'critical', 'open', %s, %s, %s, %s, 1)
                    ON CONFLICT (tenant_id, incident_key) DO UPDATE SET
                        severity = 'critical', status = 'open',
                        description = excluded.description,
                        evidence_json = excluded.evidence_json,
                        last_seen_at = excluded.last_seen_at,
                        resolved_at = NULL,
                        occurrence_count = system_incidents.occurrence_count + 1
                    """,
                    (
                        f"onboarding-reconciliation-authorization:{subject}",
                        "onboarding_reconciliation_authorization",
                        "The onboarding service cannot read the governed subject data required for atomic reconciliation.",
                        canonical_json(
                            {
                                "subject_id": subject,
                                "correlation_id": correlation_id,
                                "sqlstate": getattr(error, "sqlstate", None),
                            }
                        ),
                        now_iso,
                        now_iso,
                    ),
                )
        except Exception:
            SECURITY_AUDIT_LOGGER.exception(
                "Unable to record onboarding reconciliation configuration incident correlation_id=%s",
                correlation_id,
            )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "onboarding_configuration_error",
                "message": (
                    "Portfolio data passed validation, but the onboarding service "
                    "requires an administrator configuration correction. No "
                    "onboarding state was changed."
                ),
                "correlation_id": correlation_id,
            },
        ) from error
    return {
        "status": "passed",
        "subject_id": subject,
        "next_stage": "historical_reporting",
        "next_page": f"/account-onboarding/{subject}/steps/historical-reports",
        "validation_sha256": payload["validation"]["evidence_sha256"],
    }


@app.post("/api/onboarding/{subject_id}/approvals/activation")
def approve_onboarding_activation(
    subject_id: str,
    submission: OnboardingActivationApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Perform the final fail-closed owner activation for one reviewed subject."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    with _approval_database() as connection:
        payload = _onboarding_preview_payload(connection, subject)
        activation = _onboarding_activation_scope(payload)
        expected_confirmation = str(activation["confirmation"])
        if submission.confirmation.strip() != expected_confirmation:
            raise HTTPException(
                status_code=409,
                detail=f"Type {expected_confirmation} exactly to activate this account",
            )
        if submission.evidence_sha256 != activation["evidence_sha256"]:
            raise HTTPException(
                status_code=409,
                detail="Activation evidence changed; reload and review the final page",
            )
        existing = next(
            (
                row for row in payload["approvals"]
                if row["approval_type"] == "owner_activation"
            ),
            None,
        )
        if existing is not None:
            return {
                "status": "already_active",
                "subject_id": subject,
                "approval_id": str(existing["approval_id"]),
                "next_page": f"/accounts/{subject}/overview",
                "queue_message": "Return to the connection queue to unlock the next selected account.",
            }
        if not activation["ready"]:
            raise HTTPException(
                status_code=409,
                detail="Final activation remains blocked by one or more onboarding gates",
            )
        case = payload["subject"]
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'owner_activation', 'approved',
                'owner-subject-activation-v1', 'WealthRyse production portal',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id, TENANT_ID, case["case_id"], subject,
                canonical_json(activation["scope"]), activation["evidence_sha256"],
                actor, canonical_json({"channel": "website_onboarding_wizard"}),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner approved final activation after reviewing every gate.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s AND check_id = 'owner_activation'
            """,
            (
                canonical_json(
                    {
                        "approval_id": str(approval_id),
                        "evidence_sha256": activation["evidence_sha256"],
                    }
                ),
                TENANT_ID, case["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = 'active', status = 'active', activated_at = now(),
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s AND stage = 'activation_ready'
            """,
            (TENANT_ID, case["case_id"]),
        )
        connection.execute(
            """
            UPDATE platform_subjects
            SET lifecycle_status = 'active', updated_at = now()
            WHERE tenant_id = %s AND subject_id = %s AND lifecycle_status = 'preview'
            """,
            (TENANT_ID, subject),
        )
        metadata = case.get("metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        subject_type = str(case["subject_type"])
        activate_subject(
            connection,
            tenant_id=TENANT_ID,
            subject_id=subject,
            subject_type=subject_type,
            source_name=str(case["source_system"]),
            history_start_date=str(case["history_start_date"]),
            administrative_master=bool(
                subject_type == "master_account"
                and isinstance(metadata, dict)
                and metadata.get("reporting_mode") == "administrative_only"
            ),
            approval_id=approval_id,
            actor=actor,
        )
    return {
        "status": "active",
        "subject_id": subject,
        "approval_id": str(approval_id),
        "next_page": f"/accounts/{subject}/overview",
        "queue_message": "Return to the connection queue to unlock the next selected account.",
    }


@app.post("/api/onboarding/{subject_id}/historical-reports/queue")
def queue_onboarding_historical_reports(
    subject_id: str,
    user: dict[str, str] = Depends(_onboarding_user),
) -> dict:
    """Queue a private factsheet and presentation pair for every completed month."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    batch_id = uuid.uuid4()
    with _approval_database() as connection:
        payload = _onboarding_preview_payload(connection, subject)
        case = payload["subject"]
        if str(case["status"]) in {"active", "cancelled"}:
            raise HTTPException(
                status_code=409,
                detail="Historical backfill is unavailable at the current stage",
            )
        preceding_failures = [
            row["check_id"]
            for row in payload["checks"]
            if row["check_id"]
            not in {
                "historical_reports_backfilled",
                "website_preview_validated",
                "owner_activation",
            }
            and row["status"] != "pass"
        ]
        if preceding_failures:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preceding_onboarding_gates_incomplete",
                    "failed_checks": preceding_failures,
                },
            )
        existing = payload["historical_reports"]["batch"]
        template_state = template_evidence(payload["templates"], subject_id=subject)
        if (
            existing
            and str(existing["status"]) not in {"failed", "superseded", "approved"}
            and str(existing.get("template_evidence_sha256") or "")
            != template_state["evidence_sha256"]
        ):
            connection.execute(
                """
                UPDATE onboarding_historical_report_batches
                SET status = 'superseded', completed_at = now(),
                    error_code = 'render_contract_superseded',
                    error_message = 'The subject-specific factsheet or presentation render contract changed.'
                WHERE tenant_id = %s AND batch_id = %s
                """,
                (TENANT_ID, existing["batch_id"]),
            )
            existing = None
        if existing and str(existing["status"]) not in {"failed", "superseded"}:
            execution_reference = None
            if str(existing["status"]) in {"queued", "freezing_snapshots"}:
                if not ADMIN_ACTION_CONFIG.subscription_id or not ADMIN_ACTION_CONFIG.resource_group:
                    raise HTTPException(status_code=503, detail="The Azure report renderer is unavailable")
                try:
                    execution_reference = _default_job_starter(
                        ADMIN_ACTION_CONFIG, ONBOARDING_REPORT_JOB_NAME
                    )
                except AdminActionError as error:
                    raise HTTPException(status_code=error.status_code, detail=error.code) from error
            return {
                "status": str(existing["status"]),
                "batch_id": str(existing["batch_id"]),
                "subject_id": subject,
                "expected_month_count": int(existing["expected_month_count"]),
                "expected_artifact_count": int(existing["expected_artifact_count"]),
                "execution_reference": execution_reference,
            }
        periods = payload["historical_reports"]["periods"]
        if not periods:
            raise HTTPException(
                status_code=409,
                detail="No completed reporting month is available",
            )
        source_state = source_period_evidence(
            subject_id=subject,
            reporting_start=str(case["history_start_date"]),
            periods=periods,
        )
        connection.execute(
            """
            INSERT INTO onboarding_historical_report_batches(
                batch_id, tenant_id, case_id, subject_id,
                reporting_start, reporting_end, expected_month_count,
                expected_artifact_count, template_evidence_sha256,
                source_evidence_sha256, status,
                requested_by_identity_sha256, requested_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                'queued', %s, now()
            )
            """,
            (
                batch_id,
                TENANT_ID,
                case["case_id"],
                subject,
                case["history_start_date"],
                periods[-1]["period_end"],
                len(periods),
                len(periods) * 2,
                template_state["evidence_sha256"],
                source_state["evidence_sha256"],
                actor,
            ),
        )
        for period in periods:
            for report_type in ("factsheet", "presentation"):
                connection.execute(
                    """
                    INSERT INTO onboarding_historical_report_items(
                        tenant_id, batch_id, period_end, holdings_as_of,
                        report_type, status, template_sha256,
                        validation_json, created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, 'queued', %s,
                        '{}'::jsonb, now(), now()
                    )
                    """,
                    (
                        TENANT_ID,
                        batch_id,
                        period["period_end"],
                        period["holdings_as_of"],
                        report_type,
                        (
                            template_state[report_type]["render_contract_sha256"]
                            if report_type == "factsheet"
                            else template_state[report_type]["sha256"]
                        ),
                    ),
                )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = 'historical_reporting', status = 'in_progress',
                configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
            """,
            (
                canonical_json(
                    {
                        "historical_report_batch": {
                            "batch_id": str(batch_id),
                            "source_evidence_sha256": source_state[
                                "evidence_sha256"
                            ],
                            "template_evidence_sha256": template_state[
                                "evidence_sha256"
                            ],
                        }
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pending',
                message = 'The private historical factsheet and presentation batch is queued.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'historical_reports_backfilled'
            """,
            (
                canonical_json(
                    {
                        "batch_id": str(batch_id),
                        "expected_month_count": len(periods),
                        "expected_artifact_count": len(periods) * 2,
                    }
                ),
                TENANT_ID,
                case["case_id"],
            ),
        )
    if not ADMIN_ACTION_CONFIG.subscription_id or not ADMIN_ACTION_CONFIG.resource_group:
        raise HTTPException(status_code=503, detail="The Azure report renderer is unavailable")
    try:
        execution_reference = _default_job_starter(
            ADMIN_ACTION_CONFIG, ONBOARDING_REPORT_JOB_NAME
        )
    except AdminActionError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    return {
        "status": "queued",
        "batch_id": str(batch_id),
        "subject_id": subject,
        "expected_month_count": len(periods),
        "expected_artifact_count": len(periods) * 2,
        "publication_allowed": False,
        "execution_reference": execution_reference,
    }


@app.post("/api/onboarding/{subject_id}/approvals/historical-reports")
def approve_onboarding_historical_reports(
    subject_id: str,
    submission: OnboardingHistoricalReportApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Approve a complete private historical batch without publishing the account."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    with _approval_database() as connection:
        payload = _onboarding_preview_payload(connection, subject)
        historical = payload["historical_reports"]
        validation = historical["validation"]
        if not validation["ready_for_owner_approval"]:
            failures = [
                row["check_id"]
                for row in validation["results"]
                if row["status"] != "pass"
            ]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "historical_report_validation_failed",
                    "failed_checks": failures,
                },
            )
        if submission.evidence_sha256 != validation["evidence_sha256"]:
            raise HTTPException(
                status_code=409,
                detail="Historical report evidence changed; reload and review it again",
            )
        existing = next(
            (
                row
                for row in payload["approvals"]
                if row["approval_type"] == "historical_report_batch"
            ),
            None,
        )
        if existing is not None:
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "evidence_sha256": str(existing["scope_sha256"]),
                "activation_blocked": True,
            }
        batch = historical["batch"]
        if batch is None:
            raise HTTPException(status_code=409, detail="Historical batch is missing")
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'historical_report_batch', 'approved',
                'historical-report-backfill-v1',
                'WealthRyse private onboarding report storage',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id,
                TENANT_ID,
                payload["subject"]["case_id"],
                subject,
                canonical_json(validation),
                validation["evidence_sha256"],
                actor,
                canonical_json({"channel": "website_onboarding_historical_reports"}),
            ),
        )
        connection.execute(
            """
            UPDATE onboarding_historical_report_batches
            SET status = 'approved', approved_at = now(),
                approved_by_identity_sha256 = %s
            WHERE tenant_id = %s AND batch_id = %s
              AND status = 'awaiting_owner_approval'
            """,
            (actor, TENANT_ID, batch["batch_id"]),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner approved the complete integrity-bound historical report batch.',
                evidence_json = %s::jsonb, checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'historical_reports_backfilled'
            """,
            (
                canonical_json(validation),
                TENANT_ID,
                payload["subject"]["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = 'website_preview', status = 'in_progress', updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND status NOT IN ('active', 'cancelled')
            """,
            (TENANT_ID, payload["subject"]["case_id"]),
        )
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "evidence_sha256": submission.evidence_sha256,
        "next_stage": "website_preview",
        "activation_blocked": True,
    }


@app.get(
    "/api/onboarding/{subject_id}/historical-reports/{generation_run_id}/download"
)
def download_onboarding_historical_report(
    subject_id: str,
    generation_run_id: int,
    user: dict[str, str] = Depends(_onboarding_user),
) -> Response:
    """Download one validated private onboarding artifact by exact subject and run."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if generation_run_id <= 0:
        raise HTTPException(status_code=404, detail="Historical report not found")
    with _database() as connection:
        row = connection.execute(
            """
            SELECT i.generation_run_id, i.report_type, i.status,
                   i.container_name, i.blob_name, i.artifact_sha256,
                   i.period_end, b.subject_id, b.status AS batch_status
            FROM onboarding_historical_report_items i
            JOIN onboarding_historical_report_batches b
              ON b.tenant_id = i.tenant_id AND b.batch_id = i.batch_id
            WHERE i.tenant_id = %s AND b.subject_id = %s
              AND i.generation_run_id = %s AND i.status = 'validated'
              AND b.status IN ('awaiting_owner_approval', 'approved')
            """,
            (TENANT_ID, subject, generation_run_id),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Historical report not found")
    validation_row = {
        "generation_run_id": row["generation_run_id"],
        "report_type": row["report_type"],
        "output_reference": f"{row['container_name']}/{row['blob_name']}",
        "output_sha256": row["artifact_sha256"],
    }
    try:
        period_end = validate_period_end(str(row["period_end"]))
        artifact = validate_review_artifact(
            validation_row,
            account_id=subject,
            period_end=period_end,
            container=INTERNAL_REPORT_CONTAINER,
        )
        content = download_blob_bytes(
            AZURE_STORAGE_ACCOUNT_URL,
            artifact["container_name"],
            artifact["blob_name"],
        )
    except (MonthlyReportError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact["sha256"]:
        raise HTTPException(status_code=503, detail="Historical report integrity check failed")
    return Response(
        content=content,
        media_type=str(artifact["media_type"]),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_monthly_download_filename(artifact, subject_id=subject, period_end=period_end)}"'
            ),
            "X-Content-SHA256": digest,
            "X-Report-Period-End": period_end,
            "X-Report-Scope": "private-onboarding-review",
        },
    )


@app.post("/api/onboarding/{subject_id}/approvals/website-preview")
def approve_onboarding_website_preview(
    subject_id: str,
    submission: OnboardingPreviewApprovalRequest,
    user: dict[str, str] = Depends(_owner_user),
) -> dict:
    """Bind owner preview approval to the current deterministic evidence snapshot."""

    try:
        subject = validate_subject_id(subject_id)
    except RuntimeError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    actor = _identity_digest(user["object_id"] or user["email"])
    approval_id = uuid.uuid4()
    with _approval_database() as connection:
        payload = _onboarding_preview_payload(connection, subject)
        validation = payload["validation"]
        if not validation["ready_for_owner_preview_approval"]:
            failures = [
                row["check_id"]
                for row in validation["results"]
                if row["status"] != "pass"
            ]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preview_validation_failed",
                    "failed_checks": failures,
                },
            )
        if submission.evidence_sha256 != validation["evidence_sha256"]:
            raise HTTPException(
                status_code=409,
                detail="The preview evidence changed; reload and review it again",
            )
        existing = next(
            (
                row
                for row in payload["approvals"]
                if row["approval_type"] == "website_preview"
            ),
            None,
        )
        if existing is not None:
            return {
                "status": "already_approved",
                "approval_id": str(existing["approval_id"]),
                "subject_id": subject,
                "evidence_sha256": str(existing["scope_sha256"]),
                "activation_blocked": True,
            }
        connection.execute(
            """
            INSERT INTO account_onboarding_approvals(
                approval_id, tenant_id, case_id, subject_id, approval_type,
                approval_status, disclosure_version, destination, scope_json,
                scope_sha256, approved_by_identity_sha256, approved_at,
                metadata_json
            ) VALUES (
                %s, %s, %s, %s, 'website_preview', 'approved',
                'physical-account-preview-v2-market-calendar-alignment',
                'WealthRyse private staging portal',
                %s::jsonb, %s, %s, now(), %s::jsonb
            )
            """,
            (
                approval_id,
                TENANT_ID,
                payload["subject"]["case_id"],
                subject,
                canonical_json(validation),
                validation["evidence_sha256"],
                actor,
                canonical_json({"channel": "website_onboarding_preview"}),
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_checks
            SET status = 'pass',
                message = 'The owner approved the integrity-bound private website preview.',
                evidence_json = %s::jsonb,
                checked_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND check_id = 'website_preview_validated'
            """,
            (
                canonical_json(validation),
                TENANT_ID,
                payload["subject"]["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE account_onboarding_cases
            SET stage = 'activation_ready', status = 'ready',
                configuration_json = configuration_json || %s::jsonb,
                updated_at = now()
            WHERE tenant_id = %s AND case_id = %s
              AND status NOT IN ('active', 'cancelled')
            """,
            (
                canonical_json(
                    {
                        "website_preview_approval": {
                            "approval_id": str(approval_id),
                            "evidence_sha256": validation["evidence_sha256"],
                        }
                    }
                ),
                TENANT_ID,
                payload["subject"]["case_id"],
            ),
        )
        connection.execute(
            """
            UPDATE platform_subjects
            SET lifecycle_status = 'preview', updated_at = now()
            WHERE tenant_id = %s AND subject_id = %s
              AND lifecycle_status = 'onboarding'
            """,
            (TENANT_ID, subject),
        )
    return {
        "status": "approved",
        "approval_id": str(approval_id),
        "subject_id": subject,
        "evidence_sha256": submission.evidence_sha256,
        "next_stage": "activation_ready",
        "activation_blocked": True,
        "message": "Preview approved. Final activation remains a separate owner action.",
    }


@app.get("/account-onboarding/{subject_id}/preview", response_class=HTMLResponse)
def account_onboarding_preview_page(
    subject_id: str,
    request: Request,
    user: dict[str, str] = Depends(_onboarding_user),
) -> str:
    viewer = escape(user["email"] or "authorized user")
    role_label = escape(user.get("role", "owner").title())
    subject = escape(subject_id)
    navigation = _platform_navigation(user, active="account-onboarding")
    script = f"""
<script>
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const label=value=>String(value??'').replaceAll('_',' ').replace(/\\b\\w/g,c=>c.toUpperCase());
fetch('/api/onboarding/{subject}/preview').then(async response=>{{const payload=await response.json();if(!response.ok)throw new Error('preview_unavailable');return payload}}).then(data=>{{
 const s=data.subject,n=data.nav,p=data.positions;
 document.querySelector('#preview-benchmarks').previousElementSibling.textContent=`${{s.reporting_currency}} benchmark reconciliation`;
 document.querySelector('#preview-title').textContent=s.display_name;
 document.querySelector('#preview-stage').textContent=label(s.stage);
 document.querySelector('#preview-note').textContent=data.message;
 document.querySelector('#preview-contract').innerHTML=[['Subject',s.subject_id],['Type',label(s.subject_type)],['Reporting currency',s.reporting_currency],['Reporting start',s.history_start_date],['Lifecycle',label(s.lifecycle_status)],['Activation','Blocked pending owner approval']].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1]||'—')}}</strong></div>`).join('');
 document.querySelector('#preview-data').innerHTML=[['NAV coverage',`${{n.first_date||'—'}} – ${{n.latest_date||'—'}}`],['NAV observations',n.observations||0],['Latest position date',p.latest_date||'—'],['Position rows',p.rows||0]].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1])}}</strong></div>`).join('');
 document.querySelector('#preview-benchmarks').innerHTML=data.benchmarks.map(x=>`<div class="status-item"><span class="label">${{esc(label(x.mapping_type))}}</span><strong>${{esc(x.name)}} (${{esc(x.symbol)}})</strong><span class="muted">${{esc(x.return_convention)}} · native ${{esc(x.currency)}} → ${{esc(x.reporting_currency)}} · through ${{esc(x.latest_comparison_date||'—')}} · ${{esc(x.comparison_rows)}} rows</span></div>`).join('')||'<div class="warning">No benchmark comparison passed reconciliation.</div>';
 document.querySelector('#preview-templates').innerHTML=data.templates.map(x=>`<div class="status-item"><span class="label">${{esc(label(x.report_type))}} · ${{esc(label(x.asset_role))}}</span><strong>${{esc(x.version_label)}}</strong><span class="muted">${{esc(label(x.approval_status))}} · ${{esc(x.byte_count)}} bytes · SHA-256 ${{esc(x.sha256.slice(0,16))}}…</span></div>`).join('');
 const history=data.historical_reports,hBatch=history.batch,hValidation=history.validation,hItems=history.items||[],historyRoot=document.querySelector('#preview-historical');
 const itemRows=hItems.filter(x=>x.status==='validated').map(x=>`<div class="status-item"><span class="label">${{esc(x.period_end)}} · ${{esc(label(x.report_type))}}</span><strong>Validated</strong><span class="muted">SHA-256 ${{esc(String(x.artifact_sha256||'').slice(0,16))}}…</span><a href="/api/onboarding/{subject}/historical-reports/${{encodeURIComponent(x.generation_run_id)}}/download">Download private review file</a></div>`).join('');
 const batchSummary=hBatch?`<div class="status-row"><div class="status-item"><span class="label">Batch status</span><strong>${{esc(label(hBatch.status))}}</strong></div><div class="status-item"><span class="label">Completed months</span><strong>${{esc(hBatch.expected_month_count)}}</strong></div><div class="status-item"><span class="label">Validated files</span><strong>${{esc(hBatch.validated_artifact_count)}} / ${{esc(hBatch.expected_artifact_count)}}</strong></div><div class="status-item"><span class="label">Manifest</span><strong>${{esc(String(hBatch.manifest_sha256||'Pending').slice(0,16))}}</strong></div></div>`:'<p class="muted">No historical report batch has been queued.</p>';
 const validationRows=(hValidation.results||[]).map(x=>`<div class="onboarding-check ${{esc(x.status)}}"><strong>${{esc(label(x.check_id))}}</strong><span>${{esc(x.message)}}</span></div>`).join('');
 historyRoot.innerHTML=batchSummary+`<div class="onboarding-checks">${{validationRows}}</div><div class="status-row">${{itemRows}}</div><div id="historical-actions"></div>`;
 const historicalActions=document.querySelector('#historical-actions');
 if(!hBatch&&data.may_queue_historical_reports){{historicalActions.innerHTML='<div class="actions"><button class="button" id="historical-queue" type="button">Queue all completed months</button></div><div class="muted" id="historical-result" role="status"></div>';const button=document.querySelector('#historical-queue'),result=document.querySelector('#historical-result');button.addEventListener('click',async()=>{{button.disabled=true;result.textContent='Creating the private historical report batch…';try{{const response=await fetch('/api/onboarding/{subject}/historical-reports/queue',{{method:'POST'}}),payload=await response.json();if(!response.ok)throw new Error(payload.detail?.code||payload.detail||'queue_failed');result.textContent=`Queued ${{payload.expected_artifact_count}} files across ${{payload.expected_month_count}} completed months.`;setTimeout(()=>window.location.reload(),900)}}catch(error){{result.textContent=`Batch not queued: ${{error.message}}`;button.disabled=false}}}})}}
 else if((hValidation.results||[]).some(x=>x.check_id==='template_versions_locked'&&x.status!=='pass')&&data.may_queue_historical_reports){{historicalActions.innerHTML='<div class="warning">The report design contract changed. The existing files cannot be approved.</div><div class="actions"><button class="button" id="historical-regenerate" type="button">Regenerate with current approved design</button></div><div class="muted" id="historical-result" role="status"></div>';const button=document.querySelector('#historical-regenerate'),result=document.querySelector('#historical-result');button.addEventListener('click',async()=>{{button.disabled=true;result.textContent='Superseding the stale batch and binding a new batch to the current subject-specific design…';try{{const response=await fetch('/api/onboarding/{subject}/historical-reports/queue',{{method:'POST'}}),payload=await response.json();if(!response.ok)throw new Error(payload.detail?.code||payload.detail||'queue_failed');result.textContent=`Queued ${{payload.expected_artifact_count}} files across ${{payload.expected_month_count}} completed months.`;setTimeout(()=>window.location.reload(),900)}}catch(error){{result.textContent=`Batch not queued: ${{error.message}}`;button.disabled=false}}}})}}
 else if(history.approved){{historicalActions.innerHTML=`<div class="onboarding-check pass"><strong>Historical batch approved</strong><span>Evidence remains private until final subject activation.</span></div>`}}
 else if(hValidation.ready_for_owner_approval&&data.may_approve_historical_reports){{historicalActions.innerHTML=`<label style="display:flex;gap:10px;align-items:flex-start"><input id="historical-consent" type="checkbox"> <span>I reviewed all private historical factsheets and presentations and approve evidence <code>${{esc(hValidation.evidence_sha256)}}</code>.</span></label><div class="actions"><button id="historical-approve" class="button" type="button" disabled>Approve historical report batch</button></div><div id="historical-result" class="muted" role="status"></div>`;const consent=document.querySelector('#historical-consent'),button=document.querySelector('#historical-approve'),result=document.querySelector('#historical-result');consent.addEventListener('change',()=>{{button.disabled=!consent.checked}});button.addEventListener('click',async()=>{{button.disabled=true;result.textContent='Revalidating the complete private batch…';try{{const response=await fetch('/api/onboarding/{subject}/approvals/historical-reports',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{evidence_sha256:hValidation.evidence_sha256,approved:true}})}}),payload=await response.json();if(!response.ok)throw new Error(payload.detail?.code||payload.detail||'approval_failed');result.textContent='Historical batch approved. Website preview remains a separate gate.';setTimeout(()=>window.location.reload(),900)}}catch(error){{result.textContent=`Approval not recorded: ${{error.message}}`;button.disabled=false}}}})}}
 else{{historicalActions.innerHTML='<div class="warning">Historical batch approval remains unavailable until every completed month has both validated files.</div>'}}
 document.querySelector('#preview-features').innerHTML=data.features.map(x=>`<div class="status-item"><span class="label">${{esc(label(x.feature_key))}}</span><strong>${{esc(label(x.feature_state))}}</strong><span class="muted">${{esc(x.reason)}}</span></div>`).join('');
 const validation=data.validation;
 document.querySelector('#preview-validation').innerHTML=validation.results.map(x=>`<div class="onboarding-check ${{esc(x.status)}}"><strong>${{esc(label(x.check_id))}}</strong><span>${{esc(x.message)}}</span></div>`).join('');
 const approval=document.querySelector('#preview-approval');
 if(data.preview_approved){{approval.innerHTML=`<div class="onboarding-check pass"><strong>Owner preview sign-off recorded</strong><span>Evidence SHA-256 ${{esc(validation.evidence_sha256)}}. Final activation remains blocked.</span></div>`}}
 else if(validation.ready_for_owner_preview_approval&&data.may_approve_preview){{approval.innerHTML=`<label style="display:flex;gap:10px;align-items:flex-start"><input id="preview-consent" type="checkbox"> <span>I have reviewed this private preview and approve evidence <code>${{esc(validation.evidence_sha256)}}</code>.</span></label><div class="actions"><button id="preview-approve" class="button" type="button" disabled>Approve private website preview</button></div><div id="preview-approval-result" class="muted" role="status"></div>`;const consent=document.querySelector('#preview-consent'),button=document.querySelector('#preview-approve'),result=document.querySelector('#preview-approval-result');consent.addEventListener('change',()=>{{button.disabled=!consent.checked}});button.addEventListener('click',async()=>{{button.disabled=true;result.textContent='Revalidating and recording the integrity-bound approval…';try{{const response=await fetch('/api/onboarding/{subject}/approvals/website-preview',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{evidence_sha256:validation.evidence_sha256,approved:true}})}}),payload=await response.json();if(!response.ok)throw new Error(payload.detail?.code||payload.detail||'approval_failed');result.textContent='Preview approved. Final activation remains separately blocked.';setTimeout(()=>window.location.reload(),900)}}catch(error){{result.textContent=`Approval not recorded: ${{error.message}}`;button.disabled=false}}}})}}
 else if(validation.ready_for_owner_preview_approval){{approval.innerHTML='<div class="muted">Only the owner can approve this validated preview.</div>'}}
 else{{approval.innerHTML='<div class="warning">Preview approval is unavailable until every deterministic validation passes.</div>'}}
}}).catch(()=>{{document.querySelector('#preview-stage').textContent='Preview unavailable';document.querySelector('#preview-note').textContent='The preview failed closed and no feature was activated.'}});
</script>"""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Private onboarding preview · {PLATFORM_NAME}</title><style>{ACCOUNT_THEME_CSS}</style></head><body>
<header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header>
<main><div class="breadcrumbs"><a href="/">Accounts</a><span aria-hidden="true">/</span><a href="/account-onboarding">Account onboarding</a><span aria-hidden="true">/</span><span>{subject}</span></div><div class="account-hero"><div class="account-hero-copy"><div class="label">Owner-only reconciliation preview</div><h1 id="preview-title">{subject}</h1><div id="preview-note" class="muted">Loading validated onboarding evidence…</div></div></div><section class="grid"><div class="card wide"><div class="label">Current onboarding stage</div><div id="preview-stage" class="value">Loading…</div><div id="preview-contract" class="status-row"></div><div class="actions"><a class="button" href="/accounts/{subject}/overview">Open account analytics preview</a></div></div><div class="card wide"><div class="label">Imported portfolio coverage</div><div id="preview-data" class="status-row"></div></div><div class="card wide"><div class="label">USD benchmark reconciliation</div><div id="preview-benchmarks" class="status-row"></div></div><div class="card wide"><div class="label">Validated report templates and examples</div><div id="preview-templates" class="status-row"></div></div><div class="card wide"><div class="label">Private historical factsheets and presentations</div><div id="preview-historical"><div class="muted">Loading the historical report batch…</div></div></div><div class="card wide"><div class="label">Deterministic website-preview validation</div><div id="preview-validation" class="onboarding-checks"><div class="muted">Validating current evidence…</div></div><div id="preview-approval"></div></div><div class="card wide"><div class="label">Fail-closed feature controls</div><div id="preview-features" class="status-row"></div></div></section></main>{script}</body></html>"""


def _require_codex_owner(user: dict[str, object]) -> None:
    # Legacy/emergency fallback must not grant new Codex authority. This helper
    # also protects internal rendering calls, not just HTTP dependencies.
    if (user.get("role") != "owner"
            or user.get("authorization_source") != "database"
            or not user.get("object_id")):
        raise HTTPException(status_code=403, detail="Database-authorized owner access is required")


@app.get("/ai-operations", response_class=HTMLResponse)
def codex_workspace_page(
    request: Request, user: dict[str, object] = Depends(_authorized_user)
) -> HTMLResponse:
    _require_codex_owner(user)
    # Retain a safe response for bookmarked URLs, not deployment/setup UI.
    navigation = _platform_navigation(user)
    return HTMLResponse(f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Feature retired · {PLATFORM_NAME}</title><style>{ACCOUNT_THEME_CSS}</style></head>
<body><header>{navigation}</header><main><h1>This feature has been retired</h1>
<p>The deployment programme was cancelled. Its setup controls and activity inbox are no longer available.</p>
<p><a href="/">Return to Accounts</a></p></main></body></html>""",
                        status_code=410, headers={"Cache-Control": "no-store"})


def _retired_deployment_response() -> JSONResponse:
    return JSONResponse({"code": "feature_retired", "detail": "The deployment programme was cancelled."},
                        status_code=410, headers={"Cache-Control": "no-store"})


@app.get("/api/codex/status")
def codex_workspace_status(
    request: Request, user: dict[str, object] = Depends(_authorized_user)
) -> JSONResponse:
    _require_codex_owner(user)
    return _retired_deployment_response()


@app.get("/api/ai-operations/daily-reports")
def daily_report_inbox(request: Request, user: dict[str, object] = Depends(_authorized_user)) -> JSONResponse:
    _require_codex_owner(user)
    # Historical evidence remains stored; the cancelled UI no longer reads it.
    return _retired_deployment_response()


@app.get("/api/ai-operations/daily-reports/{report_date}")
def daily_report_detail(report_date: str, request: Request,
                        user: dict[str, object] = Depends(_authorized_user)) -> JSONResponse:
    _require_codex_owner(user)
    return _retired_deployment_response()


@app.get("/administration", response_class=HTMLResponse)
def administration_page(
    request: Request, user: dict[str, str] = Depends(_owner_user)
) -> str:
    return _render_platform_page(user, section="administration")


@app.get("/metrics-guide", response_class=HTMLResponse)
def metrics_guide_page(
    request: Request, user: dict[str, str] = Depends(_authorized_user)
) -> str:
    return _render_platform_page(user, section="metrics-guide")


def _render_platform_page(user: dict[str, str], *, section: str) -> str:
    if section not in PLATFORM_SECTIONS:
        raise HTTPException(status_code=404, detail="Platform page not found")
    if section == "combined-holdings":
        description = "Latest holdings across every physical account you can view, converted into one currency with each account's own dated FX history and aggregated by instrument."
        content = """<div class="page-view active" data-section="combined-holdings"><div class="card wide"><div class="label">Combined holdings</div><form id="combined-form" class="actions"><label><span class="label">Currency</span> <input id="combined-currency" name="currency" value="USD" maxlength="3" pattern="[A-Za-z]{3}" required size="4"></label><label><span class="label">As of</span> <input id="combined-asof" name="as_of" type="date"></label><button class="button" type="submit">Update</button></form><div id="combined-note" class="muted" aria-live="polite">Loading…</div><div id="combined-accounts" class="status-row"></div><div class="table-tools"><input id="combined-filter" type="search" placeholder="Filter by symbol, name or sector" aria-label="Filter combined holdings"><span id="combined-filter-count" class="muted"></span></div><div class="table-scroll"><table><thead><tr><th>Symbol</th><th>Description</th><th>Sector</th><th>Accounts</th><th>Quantity</th><th>Value (<span id="combined-currency-label">USD</span>)</th><th>Cost basis</th><th>Unrealized P&amp;L</th><th>P&amp;L %</th><th>Weight</th></tr></thead><tbody id="combined-holdings"></tbody></table></div></div></div>"""
        script = TABLE_SCRIPT + COMBINED_HOLDINGS_PAGE_SCRIPT
    elif section == "system-health":
        description = "Platform-wide data freshness, ingestion, benchmark, assurance and reporting status across every account and portfolio."
        content = """<div class="page-view active" data-section="system-health"><div class="card wide"><div class="label">Platform health</div><div id="health-status" class="status-row"></div><div id="health-note" class="muted"></div></div><div class="card wide"><div class="label">Open incidents</div><div id="health-incidents"></div></div><div class="card wide"><div class="label">Benchmark coverage</div><div id="benchmark-health" class="status-row"></div></div><div class="card wide"><div class="label">Latest portfolio analyst assurance</div><div id="assurance-status" class="value">Loading…</div><div id="assurance-summary" class="status-row"></div><div id="assurance-note" class="muted"></div></div><div class="card wide"><div class="label">Platform acceptance burn-in</div><div id="burn-in-status" class="value">Loading…</div><div id="burn-in-summary" class="status-row"></div><div id="burn-in-results" class="muted"></div></div></div>"""
        content = content.replace('<div id="health-note" class="muted"></div>',
                                  '<div id="health-note" class="muted" aria-live="polite"></div><div id="health-findings"></div>')
        content = content.replace('Platform acceptance burn-in', 'Registered account acceptance burn-in')
        content = _system_health_agent_content(user, content)
        script = SYSTEM_HEALTH_PAGE_SCRIPT
    elif section == "metrics-guide":
        description = "Plain-language definitions, formulas, interpretation guidance and limitations for every metric and KPI in the current platform."
        content = render_metrics_guide()
        script = METRICS_GUIDE_SCRIPT
    elif section == "ibkr-connections":
        description = "Secure Flex credential, query and multi-account discovery management for every physical IBKR account in the platform."
        content = """<div class="page-view active" data-section="ibkr-connections"><div class="card wide"><div class="label">Connection registry</div><h2>One token can support multiple queries; one query can discover multiple accounts</h2><p class="muted">Tokens are sent once to an internal write-only broker and stored in a dedicated Azure Key Vault. PostgreSQL contains only the secret reference and a non-secret fingerprint. Validation is always explicit, limited to one IBKR SendRequest and never retried automatically.</p><div id="connections-summary" class="status-row"></div><div id="flex-source-list"><div class="muted">Loading secure connections…</div></div></div><div class="card wide"><div class="label">Add connection</div><h2>Register a new token or reuse an existing secured token</h2><form id="flex-source-form" autocomplete="off"><div class="status-row"><label class="status-item"><span class="label">Credential</span><select name="credential_id" id="flex-credential-select"><option value="">Create a new credential</option></select></label><label class="status-item"><span class="label">New credential name</span><input name="credential_name" maxlength="120" placeholder="Required only for a new token"></label><label class="status-item"><span class="label">Connection / query name</span><input name="source_name" maxlength="120" placeholder="e.g. Daily portfolio query" required></label><label class="status-item"><span class="label">Flex Query ID</span><input name="query_id" inputmode="numeric" pattern="[0-9]{1,20}" maxlength="20" required></label><label class="status-item"><span class="label">Current Flex Web Service token</span><input name="token" type="password" maxlength="1024" autocomplete="new-password" placeholder="Required only for a new credential"></label></div><div class="actions"><button class="button" type="submit">Secure and register connection</button></div><div id="flex-source-status" class="muted" role="status" aria-live="polite">No connection is scheduled until discovery, reconciliation and owner approval have all passed.</div></form><div class="warning">Never paste a Flex token into chat, logs, account metadata or a support message. Use only this secured form.</div></div></div>"""
        script = IBKR_CONNECTIONS_PAGE_SCRIPT
    elif section == "account-onboarding":
        description = "Controlled account discovery, source configuration, reconciliation gates and feature activation for safely scaling the platform beyond the U16088571 reference account."
        content = """<div class="page-view active" data-section="account-onboarding">
<div class="card wide"><div class="label">Scalable account onboarding</div><div id="onboarding-status" class="value">Loading…</div><div id="onboarding-note" class="muted">Existing v1.1 accounts are discovered from the integrity-checked production export. No additional Flex request is made by this workflow.</div><div id="onboarding-summary" class="status-row"></div></div>
<div class="card wide"><div class="label">Guided onboarding wizard</div><h2>One subject, nine controlled steps</h2><p class="muted">The wizard keeps connection setup, source selection, financial configuration, external-data consent, report templates, reconciliation, private historical reporting, website preview and activation in one auditable workflow. Operators may prepare a subject; only the owner can approve external transmission, historical reports, the website preview and final activation.</p><div id="onboarding-wizard-steps" class="status-row"><div class="muted">Loading workflow…</div></div></div>
<div class="card wide"><div class="label">Choose what you are onboarding</div><h2>Four governed branches</h2><p class="muted">Physical accounts, master accounts, filtered derived portfolios and fixed-unit composites follow different validation paths. The platform never interprets a master account as a client portfolio or accepts executable calculation code.</p><div id="onboarding-subject-types" class="status-row"><div class="muted">Loading subject types…</div></div></div>
<div class="card wide" id="derived-portfolio-studio"><div class="label">Derived Portfolio Studio</div><h2>Build a governed portfolio without code</h2><p class="muted">Create another portfolio using the approved China &amp; Hong Kong filtered-account pattern or the Alpha World fixed-unit pattern. Every field becomes a typed, versioned contract; arbitrary formulas, SQL and Python are prohibited.</p><div id="studio-template-summary" class="status-row"><div class="muted">Loading approved templates…</div></div><label class="status-item" style="margin-top:18px"><span class="label">Portfolio structure</span><select id="studio-template"><option value="filtered_portfolio">Filtered account with reconstructed cash</option><option value="composite_portfolio">Fixed-unit normalized composite</option></select></label><form id="studio-filtered-form"><div class="status-row"><label class="status-item"><span class="label">Portfolio ID</span><input name="subject_id" required pattern="[A-Za-z0-9][A-Za-z0-9-]{2,63}" placeholder="asia-ex-japan"></label><label class="status-item"><span class="label">Display name</span><input name="display_name" required maxlength="160"></label><label class="status-item"><span class="label">Active physical source account</span><select name="source_account_id" required></select></label><label class="status-item"><span class="label">Reporting currency</span><input name="base_currency" required pattern="[A-Z]{3}" value="USD"></label><label class="status-item"><span class="label">History start</span><input name="history_start_date" type="date" required value="2025-01-20"></label><label class="status-item"><span class="label">Excluded countries</span><input name="excluded_countries" placeholder="Taiwan, Japan"></label><label class="status-item"><span class="label">Excluded exchanges</span><input name="excluded_exchanges" placeholder="TWSE, TPEX"></label><label class="status-item"><span class="label">Excluded currencies</span><input name="excluded_currencies" placeholder="TWD"></label><label class="status-item"><span class="label">Allowed cash currencies</span><input name="allowed_cash_currencies" required placeholder="HKD, USD"></label><label class="status-item"><span class="label">Opening cash</span><input name="opening_cash" required placeholder="HKD=484300, USD=0"></label></div><label style="display:flex;gap:10px;align-items:flex-start;margin-top:14px"><input name="include_actual_interest" type="checkbox" checked> <span>Include actual interest posted by IBKR in an allowed cash currency. No assumed interest is applied.</span></label><div class="actions"><button class="button" type="submit">Validate and create private case</button></div><div class="muted studio-result" role="status"></div></form><form id="studio-composite-form" hidden><div class="status-row"><label class="status-item"><span class="label">Portfolio ID</span><input name="subject_id" required pattern="[A-Za-z0-9][A-Za-z0-9-]{2,63}" placeholder="global-long-short"></label><label class="status-item"><span class="label">Display name</span><input name="display_name" required maxlength="160"></label><label class="status-item"><span class="label">Reporting currency</span><input name="base_currency" required pattern="[A-Z]{3}" value="USD"></label><label class="status-item"><span class="label">Normalization date</span><input name="normalization_date" type="date" required value="2025-01-20"></label><label class="status-item"><span class="label">Constant non-interest-bearing cash</span><input name="constant_cash" type="number" step="any" required value="0"></label><label class="status-item"><span class="label">Maximum market-price carry days</span><input name="market_carry_days" type="number" min="0" max="14" required value="7"></label></div><div class="label" style="margin-top:18px">Portfolio sleeves</div><p class="muted">Positive coefficients are long; negative coefficients are short. Values are converted to the reporting currency and normalized to 100 on the selected date.</p><div id="studio-component-rows" class="status-row"></div><div class="actions"><button id="studio-add-component" class="button" type="button">Add sleeve</button><button class="button" type="submit">Validate exposure and create private case</button></div><div id="studio-exposure-preview" class="muted"></div><div class="muted studio-result" role="status"></div></form><div class="onboarding-check pass" style="margin-top:18px"><strong>Fail-closed workflow</strong><span>Creation starts only a private nine-page onboarding case. The owner must separately approve the immutable definition, external-data scope, reports, preview and activation.</span></div></div>
<div class="card wide"><div class="label">Owner-approved rollout programme</div><h2>Start and complete one governed subject at a time</h2><p class="muted">The website enforces the approved order. A later portfolio remains locked until every prerequisite is active, and all formulas are loaded from server-owned versioned definitions rather than entered as executable text.</p><div id="reference-programme"><div class="muted">Checking programme gates…</div></div></div>
<div class="card wide"><div class="label">IBKR connection prerequisite</div><h2>Connection readiness</h2><p class="muted">Account onboarding uses accounts discovered through the dedicated IBKR connections workspace. Credentials, tokens, Flex queries and validation attempts are managed only there.</p><div id="connection-prerequisite-summary" class="status-row"><div class="muted">Checking connection readiness…</div></div><div id="connection-prerequisite-note" class="muted"></div><div class="actions"><a class="button" href="/ibkr-connections">Manage IBKR connections</a></div></div>
<div class="card wide" id="report-document-intake"><div class="label">Mandatory report-document intake</div><h2>Upload the complete three-document set</h2><p class="muted">The previously completed factsheet is the authoritative model for the new account's factsheet layout, format and content structure. Before this form becomes available, the owner must approve the strategy identity derived by the server from the approved account name. Document uploaders cannot type or alter the strategy title. The uploaded deck must visibly match that locked identity and supplies account branding and disclosure evidence, but it cannot replace the approved U16088571 chart, table or slide-layout system. Account-specific figures, holdings, benchmarks and commentary always come from validated data.</p><form id="onboarding-template-upload" enctype="multipart/form-data"><div class="status-row"><label class="status-item"><span class="label">Onboarding account with locked identity</span><select id="onboarding-upload-subject" required></select><span class="muted">Only accounts with a separate owner-approved presentation identity appear here.</span></label><label class="status-item"><span class="label">1 · Supporting factsheet template (PDF)</span><input name="factsheet_template" type="file" accept="application/pdf,.pdf" required></label><label class="status-item"><span class="label">2 · Account presentation reference (PPTX)</span><input name="presentation_template" type="file" accept="application/vnd.openxmlformats-officedocument.presentationml.presentation,.pptx" required></label><label class="status-item"><span class="label">3 · Authoritative completed factsheet model (PDF)</span><input name="completed_factsheet" type="file" accept="application/pdf,.pdf" required></label></div><div class="actions"><button class="button" type="submit">Validate against locked identity and submit</button></div><div id="onboarding-upload-status" class="muted" role="status" aria-live="polite">Maximum 50 MiB per document. Files are stored privately with recorded hashes; a copied or renamed deck that does not match the owner-controlled identity fails closed. Every generated presentation must also pass the platform-native chart, table and colour validation gate.</div></form></div>
<div class="card wide"><div class="label">Platform-wide presentation requirements</div><h2>Applied automatically to every future account</h2><p class="muted">Historical backfill and future month-end decks are generated from frozen account data and must pass the same output-level contract before they can be approved or downloaded.</p><div class="onboarding-checks"><div class="onboarding-check pass"><strong>Security-name labels</strong><span>Slides 6 and 9 use security names—not tickers—on chart axes and in largest holding, contributor and detractor fields.</span></div><div class="onboarding-check pass"><strong>Exact NAV</strong><span>Slide 6 shows the exact reporting-currency net asset value instead of a gross-holdings-to-NAV percentage.</span></div><div class="onboarding-check pass"><strong>Snapshot-derived narrative</strong><span>Slide 6 commentary names the largest holding and highest-weight sector from that month’s frozen data.</span></div><div class="onboarding-check pass"><strong>Fail-closed validation</strong><span>A missing, reordered, stale or ticker-based value blocks the report batch and prevents onboarding approval.</span></div></div></div>
<div class="card wide"><div class="label">Approved implementation sequence</div><div id="onboarding-sequence" class="status-row"></div></div><div class="card wide"><div class="label">Onboarding cases</div><div id="onboarding-cases"><div class="muted">Loading Wave 0 controls…</div></div></div><div class="card wide"><div class="label">Existing v1.1 source accounts</div><div class="muted">Discovery metadata only. Accounts remain private and inactive until their individual quality gates and owner approval pass.</div><div id="onboarding-discoveries"><div class="muted">Loading source-account inventory…</div></div></div></div>"""
        script = ACCOUNT_ONBOARDING_PAGE_SCRIPT
    else:
        description = "Live owner communication with the controlled AI system administrator, plus platform-wide autonomy, incidents, reviews and audited maintenance actions."
        content = """<div class="page-view active" data-section="administration"><div class="card wide ai-chat-card admin-live-chat"><div class="ai-chat-shell"><aside class="ai-chat-sidebar"><div class="ai-chat-sidebar-head"><div><div class="label">Administrator chats</div><strong>System operations</strong></div><button id="admin-new-chat" class="button" type="button">New chat</button></div><div id="admin-conversation-list" class="ai-conversation-list"><div class="muted">Loading conversations…</div></div><p class="muted admin-chat-who">The administrator, the operations agent and the connection agent post their updates in System administrator updates. Ask the administrator about any of them here.</p></aside><section class="ai-chat-main"><div class="ai-chat-header"><div class="ai-chat-header-title"><strong id="admin-chat-title">System administrator updates</strong><div class="muted">Owner-only · Monitored continuously · Changes remain approval-controlled</div></div><div class="actions"><a id="admin-chat-export-md" class="button" href="#" hidden>Save Markdown</a><a id="admin-chat-export-json" class="button" href="#" hidden>Save JSON</a><button id="admin-chat-archive" class="button" type="button" hidden>Archive</button></div></div><div id="admin-chat-thread" class="ai-chat-thread"><div class="ai-chat-empty"><div class="label">Controlled system administration</div><div class="value">Connecting to the administrator…</div></div></div><div class="ai-chat-composer"><textarea id="admin-chat-input" maxlength="1200" aria-label="Message the AI System Administrator" placeholder="Ask about health or give a maintenance order…"></textarea><div class="ai-composer-footer"><div class="muted">Enter to send · Shift+Enter for a new line · Supported changes require an exact approval</div><button id="admin-chat-send" class="button" type="button">Send</button></div></div></section></div></div><div class="admin-layout"><div class="card admin-overview"><div class="label">24/7 controlled autonomy</div><div id="autonomy-status" class="value">Loading…</div><div id="autonomy-note" class="muted"></div><div id="autonomy-summary" class="status-row"></div><div id="autonomy-incidents" class="muted"></div><div id="autonomy-reviews" class="muted"></div></div><div class="card admin-actions-panel"><div class="label">Direct owner-approved actions</div><div id="admin-status" class="value">Loading…</div><div id="admin-note" class="muted"></div><div class="actions"><select id="admin-action" aria-label="Administrative action"></select><button id="admin-preview" class="button" type="button" disabled>Preview action</button></div><div id="admin-preview-result" class="muted" style="margin-top:14px"></div><div class="actions"><input id="admin-confirmation" type="text" maxlength="80" aria-label="Type the exact approval phrase" placeholder="Preview an action to obtain the approval phrase" disabled><button id="admin-execute" class="button" type="button" disabled>Approve and execute once</button></div><div id="admin-result" class="muted" style="margin-top:14px"></div></div></div><div class="card wide"><div class="label">Institutional onboarding control plane</div><div id="control-plane-status" class="value">Loading…</div><div id="control-plane-note" class="muted"></div><div id="control-plane-summary" class="status-row"></div></div></div>"""
        script = ADMINISTRATION_PAGE_SCRIPT + ADMIN_LIVE_CHAT_SCRIPT
    return _platform_shell(
        user, active=section, title=PLATFORM_SECTIONS[section], description=description,
        content=content, script=script,
    )


def _system_health_agent_content(user: dict[str, str], content: str) -> str:
    """Leads System health with what needs attention: a plain summary, then the open incidents
    with the operations agent's diagnosis and draft fix, and links to where the agents talk."""
    from .agent_web import ui_enabled_for

    owner = user.get("role", "owner") == "owner"
    links = []
    if owner:
        links.append('<a class="button" href="/administration">Open the administrator chat</a>')
    if ui_enabled_for(user):
        links.append('<a class="button" href="/agent-inbox">Agent inbox</a>')
    overview = (
        '<div class="card wide"><div class="label">At a glance</div><div class="health-overview-head">'
        '<span id="health-headline" class="value" aria-live="polite">Checking…</span><span id="health-pill"></span></div>'
        '<div id="health-overview" class="status-row"></div>'
        + (f'<div class="actions">{"".join(links)}</div>' if links else "")
        + "</div>"
    )
    incidents = ('<div class="card wide"><div class="label">Open incidents</div><div id="health-incidents"></div></div>')
    explained = (
        '<div class="card wide"><div class="label">Open incidents</div><p class="muted">The operations agent '
        "investigates each incident from the platform's telemetry and writes up what it finds here. It changes "
        'nothing itself; when it can, it opens a draft fix for a person to review.</p><div id="health-incidents"></div></div>'
    )
    head = '<div class="page-view active" data-section="system-health">'
    content = content.replace(incidents, "")
    return content.replace(
        head,
        '<div class="page-view active" data-section="system-health"'
        + (' data-admin-chat="1"' if owner else "") + ">"
        + overview + explained,
    )


def _platform_shell(
    user: dict[str, str], *, active: str, title: str, description: str, content: str, script: str
) -> str:
    viewer = escape(user["email"] or "authorized user")
    role_label = escape(user.get("role", "owner").title())
    page_title = escape(title)
    platform_navigation = _platform_navigation(user, active=active)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title} · {PLATFORM_NAME}</title><style>{ACCOUNT_THEME_CSS}</style></head><body>
<header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{platform_navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header>
<main><div class="breadcrumbs"><a href="/">Accounts</a><span aria-hidden="true">/</span><span>Platform</span><span aria-hidden="true">/</span><span>{page_title}</span></div><div class="account-hero"><div class="account-hero-copy"><h1>{page_title}</h1><div class="muted">{escape(description)}</div></div></div><section class="grid">{content}</section></main>{script}</body></html>"""


def _agent_analyst_replaces_tab(user: dict[str, object]) -> bool:
    """The "Ask about this account" analyst is on every tab, so while the older analyst's
    model is off its AI analyst tab is left out and old links open Overview with the chat."""
    from .agent_web import ui_enabled_for

    return not AI_ANALYST_CONFIG.enabled and ui_enabled_for(user)


@app.get("/accounts/{account_id}", response_class=RedirectResponse)
def account_root(
    account_id: str,
    user: dict[str, str] = Depends(_authorized_user),
) -> RedirectResponse:
    """Redirect legacy and post-activation account links to the canonical overview."""

    try:
        subject = validate_subject_id(account_id)
    except RuntimeError as error:
        raise HTTPException(status_code=404, detail="Account page not found") from error
    _portfolio_access_context(subject, user)
    return RedirectResponse(url=f"/accounts/{subject}/overview", status_code=307)


@app.get("/accounts/{account_id}/{section}", response_class=HTMLResponse)
def account_page(
    account_id: str,
    section: str,
    request: Request,
    user: dict[str, str] = Depends(_authorized_user),
):
    if section not in ALL_PAGE_SECTIONS:
        raise HTTPException(status_code=404, detail="Account page not found")
    if section == "system-health":
        return RedirectResponse(url="/system-health", status_code=307)
    if section == "combined-holdings":
        return RedirectResponse(url="/combined-holdings", status_code=307)
    if section == "metrics-guide":
        return RedirectResponse(url="/metrics-guide", status_code=307)
    if section == "ibkr-connections":
        if user.get("role", "owner") not in {"owner", "onboarding_operator"}:
            raise HTTPException(status_code=403, detail="Onboarding operator access is required")
        return RedirectResponse(url="/ibkr-connections", status_code=307)
    if section == "account-onboarding":
        if user.get("role", "owner") not in {"owner", "onboarding_operator"}:
            raise HTTPException(status_code=403, detail="Onboarding operator access is required")
        return RedirectResponse(url="/account-onboarding", status_code=307)
    if section == "administration":
        if user.get("role", "owner") != "owner":
            raise HTTPException(status_code=403, detail="Owner access is required")
        return RedirectResponse(url="/administration", status_code=307)
    access = _portfolio_access_context(account_id, user)
    if access["preview_mode"] and section not in ONBOARDING_PREVIEW_SECTIONS:
        raise HTTPException(status_code=404, detail="Account page not found")
    if section == "ai-analyst" and _agent_analyst_replaces_tab(user):
        return RedirectResponse(url=f"/accounts/{urllib.parse.quote(account_id, safe='')}/overview#ask", status_code=307)
    return _render_account_page(
        user, account_id=account_id, section=section, access=access
    )


def _render_account_page(
    user: dict[str, str],
    *,
    account_id: str,
    section: str,
    access: dict[str, object] | None = None,
) -> str:
    access = access or _portfolio_access_context(account_id, user)
    viewer = escape(user["email"] or "authorized user")
    role = user.get("role", "owner")
    role_label = escape(role.title())
    is_owner = role == "owner"
    account = escape(account_id)
    account_display_name_value = str(access["display_name"])
    account_display_name = escape(account_display_name_value)
    reporting_currency_value = str(access["reporting_currency"] or "USD")
    reporting_currency = escape(reporting_currency_value)
    history_start_date_value = str(access["history_start_date"] or "2025-01-20")
    history_start_date = escape(history_start_date_value)
    preview_mode = bool(access["preview_mode"])
    design_controls_html = report_design_controls(account_id) if is_owner and not preview_mode else ''
    design_controls_script = REPORT_DESIGN_SCRIPT if design_controls_html else ''
    live_page_assets = (
        live_assets(account_id, section) if live_view_allowed(access, user) else ""
    )
    visible_sections = (
        ONBOARDING_PREVIEW_SECTIONS if preview_mode else set(ACCOUNT_SECTIONS)
    )
    if _agent_analyst_replaces_tab(user):
        visible_sections = visible_sections - {"ai-analyst"}
    page_title = escape(ACCOUNT_SECTIONS[section])
    active = {key: " active" if key == section else "" for key in ALL_PAGE_SECTIONS}
    navigation = "".join(
        f'<a class="account-nav-link{active[key]}" href="/accounts/{account}/{key}"'
        f'{" aria-current=\"page\"" if key == section else ""}>{escape(ACCOUNT_TAB_LABELS.get(key, label))}</a>'
        for key, label in ACCOUNT_SECTIONS.items()
        if key in visible_sections
    )
    platform_navigation = _platform_navigation(user)
    try:
        directory = [] if preview_mode else _account_directory(user)
    except Exception:  # the switcher is optional; never fail the page for it
        directory = []
    account_switcher = (
        '<label class="account-switcher"><span class="label">Switch account</span>'
        '<select id="account-switcher" aria-label="Switch account" '
        f'onchange="location.href=\'/accounts/\'+encodeURIComponent(this.value)+\'/{escape(section)}\'">'
        + "".join(
            f'<option value="{escape(str(item["subject_id"]))}"'
            f'{" selected" if str(item["subject_id"]) == account_id else ""}>'
            f'{escape(str(item.get("display_name") or item["subject_id"]))}</option>'
            for item in directory
        )
        + "</select></label>"
        if len(directory) > 1
        else ""
    )
    section_jump_links = {
        "performance": (
            ("perf-chart", "NAV chart"),
            ("perf-returns", "Period returns"),
            ("perf-calendar", "Monthly returns"),
            ("perf-attribution", "Attribution"),
            ("perf-benchmark", "Vs benchmark"),
            ("perf-flows", "Contributions"),
        ),
        "risk": (
            ("risk-summary", "Risk snapshot"),
            ("analytics-statistics-card", "Statistics"),
            ("risk-drawdowns", "Drawdowns"),
            ("risk-rolling", "Rolling risk"),
            ("risk-correlation", "Correlation"),
        ),
        "modelling": (
            ("model-projection", "Wealth projection"),
            ("model-risk", "Risk outlook"),
            ("model-regimes", "Market regimes"),
            ("model-whatif", "What-if"),
            ("model-optimise", "Optimisation"),
            ("model-factors", "Factor model"),
            ("model-stress", "Stress test"),
        ),
    }.get(section, ())
    section_jumps = (
        '<nav class="section-jumps" aria-label="Jump to a section">'
        + "".join(f'<a href="#{target}">{escape(label)}</a>' for target, label in section_jump_links)
        + "</nav>"
        if section_jump_links
        else ""
    )
    metric_anchors_json = json.dumps(METRIC_ANCHORS, ensure_ascii=False)
    etl_contribution_href = escape(
        f"/metrics-guide#{METRIC_ANCHORS['Expected Tail Loss contribution']}",
        quote=True,
    )
    etl_top_href = escape(
        f"/metrics-guide#{METRIC_ANCHORS['Top 5 ETL contributors']}", quote=True
    )
    etl_bottom_href = escape(
        f"/metrics-guide#{METRIC_ANCHORS['Bottom 5 ETL contributors']}", quote=True
    )
    preview_notice = (
        '<div class="card wide"><div class="label">Owner-only onboarding preview</div>'
        '<div class="muted">This workspace uses reconciled staging data. It remains '
        'hidden from viewers and cannot publish monthly reports or use the AI analyst '
        f'until activation gates pass. <a href="/account-onboarding/{account}/preview">'
        "Return to reconciliation evidence</a>.</div></div>"
        if preview_mode
        else ""
    )
    reporting_snapshot_link = (
        ""
        if preview_mode or not is_owner
        else '<a id="export-reporting-snapshot" class="button" '
        f'href="/api/reporting/snapshots/latest.json?download=true&amp;subject_id={account}">Owner-only frozen snapshot JSON</a>'
    )
    trade_export_link = (
        ""
        if access.get("subject_type") == "composite_portfolio"
        else '<a id="export-trades-csv" class="button" href="#">Trade history CSV</a>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{page_title} · {account_display_name} · {PLATFORM_NAME}</title>
<style>{ACCOUNT_THEME_CSS}{MODELLING_CSS}</style></head>
<body><header><div class="brand-lockup"><a class="company-logo-link" href="/" aria-label="Go to Accounts home"><img class="company-logo" src="{COMPANY_LOGO_DATA_URI}" width="52" height="52" alt="Sprout Asset Management (Asia) Limited company logo"></a></div>{platform_navigation}<div class="session">{viewer} · {role_label} · <a href="/.auth/logout">Sign out</a>{THEME_TOGGLE_HTML}</div></header>
<main><div class="breadcrumbs"><a href="/">Accounts</a><span aria-hidden="true">/</span><span>{account_display_name}</span></div><div class="account-hero"><div class="account-hero-copy"><h1>{account_display_name}</h1><div class="muted">{reporting_currency} reporting · Data from {history_start_date}</div><div id="freshness" class="freshness" hidden></div></div><div class="account-hero-controls"><label class="asof-control{' report-date-hidden' if section == 'reports' else ''}"><span class="label">As of</span><select id="asof-select" class="asof-select" aria-label="Portfolio as of date"><option>Loading…</option></select></label>{account_switcher}</div></div><div class="account-sticky"><nav class="account-nav" aria-label="{account} sections">{navigation}</nav>{section_jumps}</div><h2 class="sr-only">{page_title}</h2>
<section class="grid" id="portfolio-grid">{preview_notice}<div class="page-view{active['overview']}" data-section="overview"><div class="card wide overview-nav"><div class="overview-nav-head"><div class="overview-nav-copy"><div class="label">Net asset value</div><div class="overview-nav-line"><span id="nav" class="value">—</span><span id="overview-day" class="overview-day"></span></div><div id="overview-asof" class="muted">{reporting_currency} reporting</div></div><svg id="overview-spark" class="overview-spark" viewBox="0 0 320 64" preserveAspectRatio="none" role="img" aria-label="NAV trend over the last year"></svg></div><div id="overview-returns" class="metrics overview-returns"><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div><div class="metric"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div></div></div><div class="card overview-half"><div class="label">Top holdings · <span id="positions">—</span> positions</div><div id="overview-holdings" class="sector-bars"></div><a class="overview-more" href="/accounts/{account}/holdings">All holdings <span aria-hidden="true">→</span></a></div><div class="card overview-half"><div class="label">Sector allocation</div><div id="overview-sectors" class="sector-bars"></div><a class="overview-more" href="/accounts/{account}/holdings">Sector detail <span aria-hidden="true">→</span></a></div></div>
<div class="page-view{active['performance']}" data-section="performance">
<div class="card wide" id="perf-chart"><div class="chart-head"><div class="label chart-title">NAV history</div><div class="chart-mode" role="group" aria-label="Chart view"><button type="button" data-mode="value" data-title="NAV history" class="active" aria-pressed="true">Value</button><button type="button" data-mode="benchmark" data-title="Rebased performance comparison" aria-pressed="false">Vs benchmark</button></div></div><div class="chart-view" data-view="value"><div class="range-buttons" data-chart="#chart" role="group" aria-label="Chart period"><button type="button" data-range="1m">1M</button><button type="button" data-range="3m">3M</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1Y</button><button type="button" data-range="all" class="active" aria-pressed="true">All</button></div><div class="chart-wrap"><canvas id="chart" width="1100" height="320" aria-label="NAV history chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div></div><div class="chart-view" data-view="benchmark" hidden><div class="legend"><span><i class="swatch portfolio"></i>{account_display_name}</span><span><i class="swatch index"></i><span id="benchmark-legend">Primary benchmark</span></span></div><div class="range-buttons" data-chart="#benchmark-chart" role="group" aria-label="Chart period"><button type="button" data-range="1m">1M</button><button type="button" data-range="3m">3M</button><button type="button" data-range="ytd">YTD</button><button type="button" data-range="1y">1Y</button><button type="button" data-range="all" class="active" aria-pressed="true">All</button></div><div class="chart-wrap"><canvas id="benchmark-chart" width="1100" height="320" aria-label="Rebased performance comparison chart"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div></div></div>
<div class="card wide" id="perf-returns"><div class="label">Period returns</div><div class="muted">Time-weighted returns (TWR) chain-link daily cash-flow-adjusted returns, so contributions and withdrawals do not count as performance. IRR is the annualised money-weighted return including the timing of those flows.</div><div class="table-scroll"><table><thead><tr><th>Period</th><th>From NAV date</th><th>To NAV date</th><th>TWR</th><th>TWR annualised</th><th>IRR annualised</th><th>Net flows ({reporting_currency})</th></tr></thead><tbody id="period-returns"></tbody></table></div><div class="label" style="margin-top:18px">Custom period</div><div class="filter-grid"><label><span class="label">From</span><input id="period-from" type="date" min="{history_start_date}" aria-label="Custom period start date"></label><label><span class="label">To</span><input id="period-to" type="date" min="{history_start_date}" aria-label="Custom period end date"></label></div><div class="actions"><button id="period-apply" class="button" type="button">Calculate custom period</button></div><div class="table-scroll"><table><thead><tr><th>Period</th><th>From NAV date</th><th>To NAV date</th><th>TWR</th><th>TWR annualised</th><th>IRR annualised</th><th>Net flows ({reporting_currency})</th></tr></thead><tbody id="period-custom"></tbody></table></div><div id="period-note" class="muted" style="margin-top:12px">Loading period returns…</div></div>
{PERFORMANCE_ANALYTICS_CARDS}
<div class="card wide" id="perf-benchmark"><div class="label">Comparison benchmark</div><select id="benchmark-select" aria-label="Comparison benchmark"></select><div id="benchmark" class="value">Loading…</div><div id="benchmark-note" class="muted"></div><div id="benchmark-metrics" class="metrics"></div></div>
<div class="card wide" id="perf-flows"><div class="label">Contributions and withdrawals</div><div id="flows-totals" class="metrics"></div><div class="table-scroll"><table><thead><tr><th>Date</th><th>Type</th><th>Amount ({reporting_currency})</th></tr></thead><tbody id="flows-statement"></tbody></table></div><div id="flows-note" class="muted" style="margin-top:12px">Loading contributions and withdrawals…</div></div></div>
<div class="page-view{active['risk']}" data-section="risk"><div class="card wide" id="risk-summary"><div class="label">Risk analytics through <span id="risk-asof">—</span></div><div id="risk-metrics" class="metrics"></div><div class="contributor-grid"><div><h3><a class="metric-guide-heading-link" href="{etl_top_href}">Top 5 Expected Tail Loss contributors <span aria-hidden="true">↗</span></a></h3><table><thead><tr><th>Holding</th><th><a class="metric-guide-table-link" href="{etl_contribution_href}">ETL contribution <span aria-hidden="true">↗</span></a></th></tr></thead><tbody id="etl-top"></tbody></table></div><div><h3><a class="metric-guide-heading-link" href="{etl_bottom_href}">Bottom 5 Expected Tail Loss contributors <span aria-hidden="true">↗</span></a></h3><table><thead><tr><th>Holding</th><th><a class="metric-guide-table-link" href="{etl_contribution_href}">ETL contribution <span aria-hidden="true">↗</span></a></th></tr></thead><tbody id="etl-bottom"></tbody></table></div></div><div id="etl-note" class="muted"></div><div id="risk-note" class="muted"></div></div><div class="card wide" id="crisis-scenarios-card" hidden><div class="label">Crisis scenarios</div><table><thead><tr><th>Crisis</th><th>Window</th><th>Maximum drawdown</th><th>Stand-ins (scaled)</th></tr></thead><tbody id="crisis-scenarios"></tbody></table><div id="crisis-scenarios-note" class="muted"></div></div>{RISK_ANALYTICS_CARDS}{CORRELATION_CARD}</div>
<div class="page-view{active['modelling']}" data-section="modelling">{MODELLING_CARDS}</div>
<div class="page-view{active['cash']}" data-section="cash"><div class="card wide"><div class="label"><span id="cash-kind">Cash balances</span> as of <span id="cash-asof">—</span></div><div id="cash-balances" class="metrics"></div><div id="cash-ledger-controls"><div class="label" style="margin-top:18px">Cash activity history</div><div class="muted">Choose a custom date range to view every matching IBKR cash activity. The CSV download contains the same complete filtered result.</div><div class="filter-grid"><label><span class="label">From</span><input id="cash-from" type="date" min="{history_start_date}" aria-label="Cash activity start date"></label><label><span class="label">To</span><input id="cash-to" type="date" min="{history_start_date}" aria-label="Cash activity end date"></label></div><div class="actions"><button id="cash-apply" class="button" type="button">Apply cash filters</button><a id="cash-export" class="button" href="#">Download cash activity CSV</a></div></div><div id="cash-note" class="muted" style="margin-top:12px">Loading cash activity…</div><div id="cash-ledger-table" class="table-scroll"><table><thead><tr><th>Date</th><th>Time</th><th>Type</th><th>Description</th><th>Currency</th><th>Amount</th></tr></thead><tbody id="cash-activity"></tbody></table></div></div></div>
<div class="page-view{active['trades']}" data-section="trades">{TRADING_ACTIVITY_CARD}<div class="card wide"><div class="label">Trade history</div><div id="trade-source-title" class="value">Trade records</div><div id="trade-source-note" class="muted">Read-only executed trades imported from the daily IBKR account refresh. Filter the history or download the complete filtered result as CSV.</div><div id="trade-filter-controls" class="filter-grid"><label><span class="label">From</span><input id="trade-from" type="date" min="{history_start_date}" aria-label="Trade history start date"></label><label><span class="label">To</span><input id="trade-to" type="date" min="{history_start_date}" aria-label="Trade history end date"></label><label><span class="label">Symbol contains</span><input id="trade-symbol" type="text" maxlength="64" autocomplete="off" aria-label="Trade history symbol filter" placeholder="All symbols"></label><label><span class="label">Side</span><select id="trade-side" aria-label="Trade history side filter"><option value="">All sides</option><option value="BUY">Buy</option><option value="SELL">Sell</option></select></label></div><div class="actions"><button id="trade-apply" class="button" type="button">Apply trade filters</button><a id="export-trades" class="button" href="#">Download trade history CSV</a></div><div id="trade-note" class="muted" style="margin-top:12px">Loading trade history…</div><div class="table-scroll"><table class="trade-table"><thead><tr><th>Date</th><th>Time</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Currency</th><th>Proceeds</th><th>Commission</th><th>Net cash</th><th>Realized P&amp;L</th></tr></thead><tbody id="trade-history"></tbody></table></div></div></div>
<div class="page-view{active['holdings']}" data-section="holdings"><div class="card wide"><div class="label">Sector allocation</div><div id="sector-summary" class="muted"></div><div id="sector-bars" class="sector-bars"></div><details class="sector-detail"><summary>Industries within each sector</summary><div class="table-scroll"><table><thead><tr><th class="text-col">Sector</th><th class="text-col">Industry</th><th class="text-col">Region</th><th>Holdings</th><th>Value (<span>{reporting_currency}</span>)</th><th>Weight</th></tr></thead><tbody id="sector-detail"></tbody></table></div></details><div id="sector-note" class="muted sector-note"></div></div>{HOLDINGS_CONCENTRATION_CARD}<div class="card wide"><div class="label">Holdings as of <span id="holdings-asof">—</span></div><div class="table-tools"><input id="holdings-filter" type="search" placeholder="Filter by symbol, name or sector" aria-label="Filter holdings"><span id="holdings-filter-count" class="muted"></span></div><div class="table-scroll"><table><thead><tr><th>Symbol</th><th>Description</th><th>Sector</th><th>Currency</th><th id="holdings-quantity-label">Quantity</th><th>Market value</th><th>Value (<span id="holdings-reporting-currency">reporting</span>)</th><th>Cost basis</th><th>Unrealized P&amp;L</th><th>P&amp;L %</th><th title="14-day relative strength index from daily closing marks. Above 70 is overbought, below 30 oversold.">RSI (14)</th><th>Weight</th></tr></thead><tbody id="holdings"></tbody></table></div><div id="holdings-note" class="muted"></div></div></div>
<div class="page-view{active['reports']}" data-section="reports"><div class="card wide"><div class="label">Monthly factsheets and presentations</div><div class="value">Report centre</div><div id="report-note" class="muted">Loading frozen month-end reports…</div><div class="actions"><select id="report-month" aria-label="Monthly report period"></select><button id="report-prepare" class="button" type="button" disabled>Generate secure downloads</button></div><div id="report-status" class="muted" style="margin-top:14px"></div><div id="report-downloads" class="actions"></div><div id="report-owner-review" style="display:none;margin-top:22px"><h3>Review and release files</h3><p class="muted">Tick a file to make it available to authorized users. Untick it and submit to revoke its download access. A newly approved replacement becomes the current download and withdraws the older version. The original file and approval history are retained. Review files before approving; client distribution remains prohibited.</p><div id="report-release-details" class="muted" style="margin-top:12px"></div><div id="report-release-list" style="display:grid;gap:12px;margin:16px 0"></div><button id="report-release" class="button" type="button" disabled>Submit approval changes</button><div id="report-release-status" class="muted" role="status" aria-live="polite"></div></div>{design_controls_html}</div></div>
<div class="page-view{active['exports']}" data-section="exports"><div class="card wide"><div class="label">Data exports</div><div class="muted">Downloads are account-scoped. CSV files use the selected as-of date.</div><div class="actions"><a id="export-performance" class="button" href="#">Performance CSV</a><a id="export-holdings" class="button" href="#">Holdings CSV</a><a id="export-cash" class="button" href="#">Cash activity CSV</a>{trade_export_link}<a class="button" href="/accounts/{account}/trades">Trade history controls</a>{reporting_snapshot_link}</div></div></div>
<div class="page-view{active['ai-analyst']}" data-section="ai-analyst"><div class="card wide ai-chat-card"><div class="ai-chat-shell"><aside class="ai-chat-sidebar"><div class="ai-chat-sidebar-head"><div><div class="label">Saved chats</div><strong>AI Analyst</strong></div><button id="ai-new-chat" class="button" type="button">New chat</button></div><div id="ai-conversation-list" class="ai-conversation-list"><div class="muted">Loading conversations…</div></div></aside><section class="ai-chat-main"><div class="ai-chat-header"><div class="ai-chat-header-title"><strong id="ai-chat-title">New conversation</strong><div id="agent-status" class="muted">Loading analyst…</div></div><div class="actions"><a id="ai-export-md" class="button" href="#" hidden>Save Markdown</a><a id="ai-export-json" class="button" href="#" hidden>Save JSON</a><button id="ai-archive-chat" class="button" type="button" hidden>Archive</button></div></div><div id="ai-chat-thread" class="ai-chat-thread"><div class="ai-chat-empty"><div class="label">Live grounded analysis</div><div class="value">Ask about this portfolio</div><div id="agent-note" class="muted">The analyst uses validated portfolio tools for every figure and chart.</div><div class="ai-prompt-chips"><button class="ai-prompt-chip" type="button">Compare performance with the selected benchmark</button><button class="ai-prompt-chip" type="button">Explain the portfolio's main risks</button><button class="ai-prompt-chip" type="button">Show NAV history and key performance metrics</button></div></div></div><div class="ai-chat-composer"><textarea id="analysis-question" maxlength="1200" aria-label="Message the AI Portfolio Analyst" placeholder="Message the AI Portfolio Analyst…"></textarea><div class="ai-composer-footer"><div class="muted">Enter to send · Shift+Enter for a new line · Conversations save automatically</div><button id="analysis-run" class="button" type="button" disabled>Send</button></div></div></section></div></div></div>
<div class="page-view{active['system-health']}" data-section="system-health"><div class="card wide"><div class="label">System health</div><div id="health-status" class="status-row"></div><div id="health-note" class="muted"></div></div><div class="card wide"><div class="label">Daily portfolio analyst assurance</div><div id="assurance-status" class="value">Loading…</div><div id="assurance-summary" class="status-row"></div><div id="assurance-note" class="muted"></div></div><div class="card wide"><div class="label">Stage 4 acceptance burn-in</div><div id="burn-in-status" class="value">Loading…</div><div id="burn-in-summary" class="status-row"></div><div id="burn-in-results" class="muted"></div></div></div>
<div class="page-view{active['administration']}" data-section="administration"><div class="admin-layout"><div class="card admin-overview"><div class="label">24/7 controlled autonomy</div><div id="autonomy-status" class="value">Loading…</div><div id="autonomy-note" class="muted"></div><div id="autonomy-summary" class="status-row"></div><div id="autonomy-incidents" class="muted"></div><div id="autonomy-reviews" class="muted"></div></div><div class="card admin-actions-panel"><div class="label">Owner-approved administrator actions</div><div id="admin-status" class="value">Loading…</div><div id="admin-note" class="muted"></div><div class="actions"><select id="admin-action" aria-label="Staging administrative action"></select><button id="admin-preview" class="button" type="button" disabled>Preview action</button></div><div id="admin-preview-result" class="muted" style="margin-top:14px"></div><div class="actions"><input id="admin-confirmation" type="text" maxlength="80" aria-label="Type the exact approval phrase" placeholder="Preview an action to obtain the approval phrase" disabled><button id="admin-execute" class="button" type="button" disabled>Approve and execute once</button></div><div id="admin-result" class="muted" style="margin-top:14px"></div></div></div></div></section></main>
{TABLE_SCRIPT}<script>
const account='{account}'; const accountDisplayName={json.dumps(account_display_name_value, ensure_ascii=False)}; const reportingCurrency={json.dumps(reporting_currency_value)}; const historyStartDate={json.dumps(history_start_date_value)}; const activeSection={json.dumps(section)}; const previewMode={str(preview_mode).lower()}; const portalOwner={str(is_owner).lower()}; const fmt=n=>n==null||n===''?'—':new Intl.NumberFormat('en-US',{{maximumFractionDigits:2}}).format(Number(n));
const pct=n=>{{if(n==null||!Number.isFinite(Number(n)))return '—';const formatter=new Intl.NumberFormat('en-US',{{style:'percent',maximumFractionDigits:2}}),parts=formatter.formatToParts(Number(n)),zero=parts.filter(p=>p.type==='integer'||p.type==='fraction').every(p=>Number(p.value)===0);return (zero?parts.filter(p=>p.type!=='minusSign'):parts).map(p=>p.value).join('');}};
const axis=n=>new Intl.NumberFormat('en-US',{{maximumFractionDigits:2}}).format(Number(n));
const exact=n=>new Intl.NumberFormat('en-US',{{minimumFractionDigits:2,maximumFractionDigits:6}}).format(Number(n));
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const metricGuideAnchors={metric_anchors_json};
const metricGuideHref=label=>`/metrics-guide#${{metricGuideAnchors[label]||''}}`;
const metricCard=x=>`<a class="metric metric-guide-card" href="${{esc(metricGuideHref(x[0]))}}" aria-label="Read the Metrics Guide explanation for ${{esc(x[0])}}"><div class="label">${{esc(x[2]||x[0])}} <span aria-hidden="true">↗</span></div><div class="value">${{esc(x[1])}}</div></a>`;
const json=async r=>{{const payload=await r.json();if(!r.ok){{const code=payload&&payload.detail&&payload.detail.code;throw new Error(code||`http_${{r.status}}`)}}return payload}};
const rootStyle=getComputedStyle(document.documentElement);
const chartColors={{portfolio:rootStyle.getPropertyValue('--chart-portfolio').trim()||'#2646a6',benchmark:rootStyle.getPropertyValue('--chart-benchmark').trim()||'#d97706'}};
const chartState=new Map();
const niceTickStep=(lo,hi,targetIntervals=8)=>{{const span=Math.abs(hi-lo)||Math.max(Math.abs(hi),1),rough=span/Math.max(1,targetIntervals),magnitude=10**Math.floor(Math.log10(rough)),normalized=rough/magnitude,multiplier=normalized<=1?1:normalized<=2?2:normalized<=5?5:10;return multiplier*magnitude}};
const chartBounds=(values,options)=>{{let lo=Math.min(...values),hi=Math.max(...values);const requestedStep=Number(options.yTickStep),tickStep=Number.isFinite(requestedStep)&&requestedStep>0?requestedStep:options.niceYTicks?niceTickStep(lo,hi,Number(options.targetYIntervals)||8):null;if(tickStep){{lo=Math.floor(lo/tickStep)*tickStep;hi=Math.ceil(hi/tickStep)*tickStep;if(lo===hi){{lo-=tickStep;hi+=tickStep}}return {{lo,hi,tickStep}}}}let range=hi-lo;if(!range)range=Math.max(Math.abs(hi)*.02,1);lo-=range*.05;hi+=range*.05;return {{lo,hi,tickStep:null}}}};
const renderChart=(id,hoverIndex=null)=>{{const state=chartState.get(id),c=document.querySelector(id),ctx=c.getContext('2d'),tip=c.parentElement.querySelector('.chart-tooltip'),dpr=window.devicePixelRatio||1,cw=c.clientWidth||1100,ch=c.clientHeight||320;if(c.width!==Math.round(cw*dpr)||c.height!==Math.round(ch*dpr)){{c.width=Math.round(cw*dpr);c.height=Math.round(ch*dpr)}}ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,cw,ch);if(!state||!state.series.length){{tip.style.display='none';const empty=state&&state.options.emptyMessage;if(empty){{ctx.fillStyle=chartInk('--chart-text','#667085');ctx.font='13px Segoe UI';ctx.textAlign='center';ctx.fillText(empty,cw/2,ch/2);ctx.textAlign='left'}}return}}const {{series,lines,options}}=state,m={{left:100,right:22,top:18,bottom:48}},w=cw-m.left-m.right,h=ch-m.top-m.bottom,values=lines.flatMap(line=>series.map(row=>Number(row[line.key]))).filter(Number.isFinite),bounds=chartBounds(values,options),lo=bounds.lo,hi=bounds.hi,xFor=i=>m.left+i*w/Math.max(1,series.length-1),yFor=n=>m.top+(hi-n)*h/(hi-lo);ctx.font='12px Segoe UI';ctx.lineWidth=1;const yTicks=bounds.tickStep?Array.from({{length:Math.round((hi-lo)/bounds.tickStep)+1}},(_,i)=>hi-i*bounds.tickStep):Array.from({{length:5}},(_,i)=>hi-i*(hi-lo)/4);yTicks.forEach(value=>{{const y=yFor(value);ctx.strokeStyle=chartInk('--chart-grid','#e7ebf2');ctx.beginPath();ctx.moveTo(m.left,y);ctx.lineTo(cw-m.right,y);ctx.stroke();ctx.fillStyle=chartInk('--chart-text','#667085');ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillText(axis(value),m.left-9,y)}});for(let i=0;i<5;i++){{const index=Math.round(i*(series.length-1)/4),x=xFor(index);ctx.strokeStyle=chartInk('--chart-grid','#e7ebf2');ctx.beginPath();ctx.moveTo(x,m.top);ctx.lineTo(x,ch-m.bottom);ctx.stroke();ctx.fillStyle=chartInk('--chart-text','#667085');ctx.textAlign=i===0?'left':i===4?'right':'center';ctx.textBaseline='top';ctx.fillText(series[index].date,x,ch-m.bottom+9)}}ctx.strokeStyle=chartInk('--chart-text','#667085');ctx.lineWidth=1.2;ctx.beginPath();ctx.moveTo(m.left,m.top);ctx.lineTo(m.left,ch-m.bottom);ctx.lineTo(cw-m.right,ch-m.bottom);ctx.stroke();ctx.fillStyle=chartInk('--chart-text','#667085');ctx.textAlign='center';ctx.textBaseline='bottom';ctx.fillText('Date',m.left+w/2,ch-2);ctx.save();ctx.translate(16,m.top+h/2);ctx.rotate(-Math.PI/2);ctx.textBaseline='middle';ctx.fillText(options.yLabel,0,0);ctx.restore();lines.forEach(line=>{{ctx.strokeStyle=line.color;ctx.lineWidth=2;ctx.beginPath();series.forEach((row,i)=>{{const x=xFor(i),y=yFor(Number(row[line.key]));i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});ctx.stroke()}});if(hoverIndex!==null){{const row=series[hoverIndex],x=xFor(hoverIndex);ctx.strokeStyle=chartInk('--chart-axis','#98a2b3');ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(x,m.top);ctx.lineTo(x,ch-m.bottom);ctx.stroke();ctx.setLineDash([]);lines.forEach(line=>{{ctx.fillStyle=line.color;ctx.beginPath();ctx.arc(x,yFor(Number(row[line.key])),4,0,Math.PI*2);ctx.fill();ctx.strokeStyle=chartInk('--chart-point-ring','#fff');ctx.lineWidth=1.5;ctx.stroke()}});tip.innerHTML=`<strong>${{esc(row.date)}}</strong>`+lines.map(line=>`<div><span style="color:${{line.color}}">●</span> ${{esc(line.label)}}: <strong>${{exact(row[line.key])}}${{options.unit?' '+esc(options.unit):''}}</strong></div>`).join('');const cssX=x*c.clientWidth/cw;tip.style.left=(cssX>c.clientWidth*.72?cssX-205:cssX+12)+'px';tip.style.top='12px';tip.style.display='block'}}else tip.style.display='none'}};
const draw=(id,series,lines,options={{}})=>{{chartSource.set(id,{{series,lines,options}});series=rangeSeries(id,series,options);const c=document.querySelector(id);chartState.set(id,{{series,lines,options:{{yLabel:'Value',unit:'',...options}}}});c.onmousemove=e=>{{if(!series.length)return;const r=c.getBoundingClientRect(),x=(e.clientX-r.left)*c.clientWidth/r.width,left=100,right=22,index=Math.max(0,Math.min(series.length-1,Math.round((x-left)/(c.clientWidth-left-right)*Math.max(1,series.length-1))));renderChart(id,index)}};c.onmouseleave=()=>renderChart(id);renderChart(id)}};
let benchmarks=[];
const loadCashActivity=async()=>{{
document.querySelector('#cash-balances').innerHTML='';document.querySelector('#cash-asof').textContent='Loading…';document.querySelector('#cash-activity').innerHTML='';document.querySelector('#cash-export').hidden=true;
const start=document.querySelector('#cash-from').value||historyStartDate,end=document.querySelector('#cash-to').value||document.querySelector('#asof-select').value;
const params=new URLSearchParams({{from_date:start,to_date:end}}),result=await fetch(`/api/portfolio/${{account}}/cash?${{params}}`).then(json);
document.querySelector('#cash-asof').textContent=result.balances_date||'No snapshot';
document.querySelector('#cash-export').hidden=result.activity_applicable===false;document.querySelector('#cash-ledger-controls').hidden=result.activity_applicable===false;document.querySelector('#cash-ledger-table').hidden=result.activity_applicable===false;document.querySelector('#cash-kind').textContent=result.activity_applicable===false?'Model cash':'Cash balances';
if(result.activity_applicable===false){{
const model=result.model_cash;
document.querySelector('#cash-balances').innerHTML=model?`<div class="metric"><div class="label">Model cash (normalized NAV units)</div><div class="value">${{fmt(model.normalized_value)}}</div><div class="muted">${{esc(model.currency_context)}} reporting context; not a currency cash balance</div></div><div class="metric"><div class="label">Share of current model NAV</div><div class="value">${{pct(model.nav_weight)}}</div></div>`:'<div class="empty">Validated model cash is unavailable.</div>';
document.querySelector('#cash-activity').innerHTML='<tr><td colspan="6">Not applicable: this model has no independently executed cash transactions.</td></tr>';
document.querySelector('#cash-note').textContent=result.message;return;
}}
document.querySelector('#cash-balances').innerHTML=result.balances.map(x=>`<div class="metric"><div class="label">${{x.currency==='BASE_SUMMARY'?'Base-currency cash summary':esc(x.currency)+' ending cash'}}</div><div class="value">${{fmt(x.ending_cash_text)}}${{x.currency==='BASE_SUMMARY'?'':' '+esc(x.currency)}}</div><div class="muted">Settled: ${{fmt(x.settled_cash_text)}}${{x.currency==='BASE_SUMMARY'?'<br>Broker base-currency aggregate; currency code not supplied.':''}}</div></div>`).join('')||'<div class="empty">No cash balance snapshot is available.</div>';
const cashTime=x=>{{const value=String(x.event_datetime||'');return value.includes('T')?value.split('T')[1].slice(0,8):value.includes(' ')?value.split(' ')[1].slice(0,8):''}};
document.querySelector('#cash-activity').innerHTML=result.transactions.map(x=>`<tr><td>${{esc(x.effective_date)}}</td><td>${{esc(cashTime(x))}}</td><td>${{esc(x.transaction_type||'')}}</td><td>${{esc(x.description||'')}}</td><td>${{esc(x.currency||'')}}</td><td>${{fmt(x.amount_text)}}</td></tr>`).join('')||'<tr><td colspan="6">No cash activity occurred in this date range.</td></tr>';
document.querySelector('#cash-note').textContent=`Showing all ${{result.total_count}} cash activities from ${{result.activity_from}} through ${{result.activity_to}}.`;
document.querySelector('#cash-export').href=`/api/portfolio/${{account}}/export/cash-activity.csv?${{new URLSearchParams({{as_of:end,from_date:start}})}}`;
}};
document.querySelector('#cash-apply').addEventListener('click',()=>loadCashActivity().catch(()=>{{document.querySelector('#cash-note').textContent='Cash data is unavailable or its inputs did not pass validation. Check the selected dates or contact the platform owner.'}}));
const loadTradeHistory=async()=>{{
const start=document.querySelector('#trade-from').value||historyStartDate,end=document.querySelector('#trade-to').value||document.querySelector('#asof-select').value,symbol=document.querySelector('#trade-symbol').value.trim(),side=document.querySelector('#trade-side').value;
const params=new URLSearchParams({{from_date:start,to_date:end}});if(symbol)params.set('symbol',symbol);if(side)params.set('side',side);
const result=await fetch(`/api/portfolio/${{account}}/trades?${{params}}`).then(json);
document.querySelector('#trade-history').innerHTML=result.trades.map(x=>`<tr><td>${{esc(x.trade_date)}}</td><td>${{esc(x.trade_time||'')}}</td><td><strong>${{esc(x.symbol||x.conid||'Unknown')}}</strong><div class="muted">${{esc(x.description||x.asset_category||'')}}</div></td><td>${{esc(x.buy_sell||'')}}</td><td>${{fmt(x.quantity_text)}}</td><td>${{fmt(x.trade_price_text)}}</td><td>${{esc(x.currency||'')}}</td><td>${{fmt(x.proceeds_text)}}</td><td>${{fmt(x.commission_text)}} ${{esc(x.commission_currency||x.currency||'')}}</td><td>${{fmt(x.net_cash_text)}}</td><td>${{fmt(x.realized_pnl_text)}}</td></tr>`).join('')||'<tr><td colspan="11">No executed trades match these filters.</td></tr>';
document.querySelector('#trade-note').textContent=result.not_applicable?result.message:`Showing ${{result.returned_count}} of ${{result.total_count}} trades from ${{result.date_from}} through ${{result.date_to}}${{result.truncated?' (screen limited to the latest 500; CSV contains the complete filtered result)':''}}.`;
if(result.not_applicable){{document.querySelector('#trade-source-title').textContent='Derived portfolio — no independently executed trades';document.querySelector('#trade-source-note').textContent=result.message;document.querySelector('#trade-filter-controls').hidden=true;document.querySelector('#trade-history').innerHTML='<tr><td colspan="11">No independently executed trades for this derived portfolio.</td></tr>';document.querySelector('#export-trades').hidden=true;document.querySelector('#trade-apply').disabled=true;}}
const exportParams=new URLSearchParams({{as_of:end,from_date:start}});if(symbol)exportParams.set('symbol',symbol);if(side)exportParams.set('side',side);document.querySelector('#export-trades').href=`/api/portfolio/${{account}}/export/trades.csv?${{exportParams}}`;
}};
document.querySelector('#trade-apply').addEventListener('click',()=>loadTradeHistory().catch(()=>{{document.querySelector('#trade-note').textContent='Unable to load trade history for these filters.'}}));
const periodRow=x=>{{const failed=x.status==='fail'||x.status==='empty',shortIrr=x.irr_annualized!=null&&Boolean(x.irr_note);return `<tr><td>${{esc(x.label)}}${{x.partial?' <span class="muted">(partial: history starts '+esc(x.start_date)+')</span>':''}}</td><td>${{esc(x.start_date||'—')}}</td><td>${{esc(x.end_date||'—')}}</td><td title="${{esc((x.issues||[]).join(' '))}}">${{failed?'Unavailable':pct(x.twr)}}</td><td>${{pct(x.twr_annualized)}}</td><td title="${{esc(x.irr_note||'')}}">${{failed?'—':pct(x.irr_annualized)}}${{shortIrr?' *':''}}</td><td>${{fmt(x.net_flows)}}</td></tr>`}};
const loadPerformancePeriods=async()=>{{
const asOf=document.querySelector('#asof-select').value,start=document.querySelector('#period-from').value,end=document.querySelector('#period-to').value;
const params=new URLSearchParams({{as_of:asOf}});if(start)params.set('start',start);if(end)params.set('end',end);
const flowParams=new URLSearchParams({{from_date:start||historyStartDate,to_date:end||asOf}});
const [r,f]=await Promise.all([fetch(`/api/portfolio/${{account}}/performance/periods?${{params}}`).then(json),fetch(`/api/portfolio/${{account}}/flows?${{flowParams}}`).then(json)]);
document.querySelector('#period-returns').innerHTML=r.periods.map(periodRow).join('')||'<tr><td colspan="7">No NAV history is available on or before this date.</td></tr>';
document.querySelector('#period-custom').innerHTML=r.custom?periodRow(r.custom):'<tr><td colspan="7">Choose a start and end date, then calculate a custom period.</td></tr>';
document.querySelector('#period-note').textContent=`Returns through ${{r.effective_as_of||'—'}} in ${{r.currency}}. Each period is valued from the last NAV before it starts to the last NAV on or before it ends. * Period under one year: the annualised IRR extrapolates a short-period result. ${{r.flows_message||''}}`;
document.querySelector('#flows-totals').innerHTML=f.applicable?[['Contributions',f.totals.flows_in],['Withdrawals',f.totals.flows_out],['Net external flow',f.totals.net]].map(x=>`<div class="metric"><div class="label">${{esc(x[0])}}</div><div class="value">${{fmt(x[1])}} ${{esc(f.currency)}}</div></div>`).join(''):'';
document.querySelector('#flows-statement').innerHTML=f.flows.map(x=>`<tr><td>${{esc(x.date)}}</td><td>${{x.direction==='contribution'?'Contribution':'Withdrawal'}}</td><td>${{fmt(x.amount)}}</td></tr>`).join('')||`<tr><td colspan="3">${{f.applicable?'No contributions or withdrawals in this date range.':'Not applicable.'}}</td></tr>`;
document.querySelector('#flows-note').textContent=f.applicable?`Showing ${{f.totals.count}} dated external flows from ${{f.from_date}} through ${{f.to_date||asOf}}, net per day in ${{f.currency}}. Deposits, withdrawals and transfers in or out are external flows; dividends, interest and fees are not.`:f.message;
}};
document.querySelector('#period-apply').addEventListener('click',()=>loadPerformancePeriods().catch(()=>{{document.querySelector('#period-note').textContent='Unable to calculate this period. Check that the start date is not after the end date.'}}));
const renderBenchmark=async(id,meta,asOf)=>{{
const query=`benchmark_id=${{encodeURIComponent(id)}}&as_of=${{encodeURIComponent(asOf)}}`;
const bc=await fetch(`/api/portfolio/${{account}}/benchmark-comparison?${{query}}`).then(json);
document.querySelector('#benchmark').textContent=`${{meta.name}} (${{meta.symbol}}) · ${{meta.mapping_type==='primary'?'Primary':'Secondary'}} · ${{meta.return_basis_label||meta.return_convention}}`;
document.querySelector('#benchmark-legend').textContent=`${{meta.name}} (${{meta.symbol}}) · ${{meta.return_basis==='price_return'?'price return':meta.return_basis==='total_return'?'total return':meta.return_convention}}`;
document.querySelector('#benchmark-note').textContent=meta.message+`${{meta.return_basis==='price_return'?' The benchmark is a price index: its returns exclude dividends, while portfolio returns include income, so the comparison favours the portfolio by the index dividend yield.':''}} Provider: ${{meta.provider||'—'}} · Convention: ${{meta.return_convention}} · License: ${{meta.license_status}} · Provisional observations: ${{meta.provisional_observations}} · Latest feed observation: ${{meta.latest_provisional_observation||'—'}}`;
document.querySelector('#benchmark-metrics').innerHTML='';
draw('#benchmark-chart',[],[],{{emptyMessage:bc.available?'':'No validated comparison is available for this benchmark and date.'}});renderRollingBenchmark(null);
if(bc.available){{
const m=bc.metrics,items=[['Portfolio total return',pct(m.portfolio_total_return)],['Benchmark total return',pct(m.benchmark_total_return),bc.benchmark_return_basis==='price_return'?'Benchmark price return (excludes dividends)':undefined],['Excess total return',pct(m.excess_total_return),bc.benchmark_return_basis==='price_return'?'Excess return vs price index':undefined],['Tracking error',pct(m.tracking_error)],['Information ratio',m.information_ratio==null?'—':fmt(m.information_ratio)],['Beta',m.beta==null?'—':fmt(m.beta)],['Correlation',m.correlation==null?'—':fmt(m.correlation)],['Relative win rate',pct(m.relative_win_rate)]];
document.querySelector('#benchmark-note').textContent+=` Aligned comparison: ${{m.period_start}} through ${{m.period_end}} · Reporting currency: ${{bc.reporting_currency}} · ${{m.observations}} common-date observations. Rebased to 100 at the first common observation; unmatched market dates are not filled.`;
document.querySelector('#benchmark-metrics').innerHTML=items.map(metricCard).join('');
renderRollingBenchmark(bc);
draw('#benchmark-chart',bc.series,[{{key:'portfolio',color:chartColors.portfolio,label:accountDisplayName}},{{key:'benchmark',color:chartColors.benchmark,label:bc.benchmark.name}}],{{yLabel:'Rebased value',yTickStep:10}})
}}
}};
{RISK_DISPLAY_SCRIPT}
const mergeRiskResponses=(core,details)=>{{const risk={{...core,...details}};if(core.cache_status!=='hit'||details.cache_status!=='hit'||core.snapshot_payload_sha256!==details.snapshot_payload_sha256){{risk.metrics={{}};risk.analytics_evidence={{}};risk.risk_free=null;risk.expected_tail_loss_contributors={{top:[],bottom:[]}};risk.crisis_replay=null;risk.cache_status='unavailable';risk.message=core.message||details.message||'Risk cache changed during this request; refresh to load one validated version.';}}return risk;}};
{ACCOUNT_UI_SCRIPT}
{ANALYTICS_SCRIPT}
{MODELLING_SCRIPT}
const renderAsOf=async asOf=>{{
const grid=document.querySelector('#portfolio-grid');grid.classList.add('loading');
try{{
const suffix=`?as_of=${{encodeURIComponent(asOf)}}`;
pageError('');
const performanceRequest=activeSection==='performance'?fetch(`/api/portfolio/${{account}}/performance${{suffix}}`).then(json).catch(()=>({{series:[],failed:true}})):Promise.resolve({{series:[]}});
const holdingsRequest=activeSection==='holdings'?fetch(`/api/portfolio/${{account}}/holdings${{suffix}}`).then(json).catch(()=>({{snapshot_date:null,holdings:[],failed:true}})):Promise.resolve({{snapshot_date:null,holdings:[]}});
const riskRequest=activeSection==='risk'?fetch(`/api/portfolio/${{account}}/risk${{suffix}}`).then(json).catch(()=>({{as_of:asOf,metrics:{{}},risk_free:null,message:'The risk snapshot could not be loaded for this date. Refresh the page or pick another As of date.'}})):Promise.resolve({{as_of:asOf,metrics:{{}},risk_free:null,message:'Open Risk analytics to view the validated risk snapshot.'}});
const riskDetailsRequest=activeSection==='risk'?fetch(`/api/portfolio/${{account}}/risk/details${{suffix}}`).then(json).catch(()=>({{expected_tail_loss_contributors:{{top:[],bottom:[]}},crisis_replay:null,crisis_scenarios:null}})):Promise.resolve({{expected_tail_loss_contributors:{{top:[],bottom:[]}},crisis_replay:null,crisis_scenarios:null}});
const healthRequest=Promise.resolve({{environment:'staging',status:'available',message:'Open System health for platform-wide diagnostics.',latest_run:{{}},portfolio:{{}},portfolio_analyst_assurance:{{}},burn_in:{{}},burn_in_recent_results:[]}});
const [s,p,h,riskCore,riskDetails,health]=await Promise.all([fetch(`/api/portfolio/${{account}}/summary${{suffix}}`).then(json),performanceRequest,holdingsRequest,riskRequest,riskDetailsRequest,healthRequest]);
const risk=activeSection==='risk'?mergeRiskResponses(riskCore,riskDetails):{{...riskCore,...riskDetails}};
if(!s.latest_nav)throw new Error('No NAV is available on or before this date');
document.querySelector('#nav').textContent=money(s.latest_nav.total_text)+' '+s.latest_nav.currency;
document.querySelector('#positions').textContent=s.current_positions;
if(activeSection==='overview')await renderOverview(asOf,s);
if(activeSection==='holdings'){{void renderSectors(asOf);renderConcentration(h)}}
if(activeSection==='performance'||activeSection==='risk')void renderAnalytics(asOf);
if(activeSection==='trades')void renderTradingActivity(asOf);
if(activeSection==='risk')void renderCorrelation(asOf);
if(activeSection==='modelling')renderModelling(asOf);
document.querySelector('#holdings-asof').textContent=h.snapshot_date||'No snapshot';
document.querySelector('#holdings-quantity-label').textContent=h.holdings.some(x=>x.holding_kind==='synthetic_exposure')?'Sleeve coefficient (not shares)':'Quantity';
document.querySelector('#holdings-reporting-currency').textContent=h.reporting_currency||reportingCurrency;
document.querySelector('#holdings').innerHTML=holdingsRows(h);
if(h.failed)document.querySelector('#holdings-asof').textContent='unavailable';
document.querySelector('#holdings-note').textContent=h.weight_basis?`Cost basis and unrealized P&L are in each holding's own currency. Value is converted to ${{h.reporting_currency}} with the dated FX rate on or before the holdings date. Weight basis: ${{h.weight_basis==='nav'?'net asset value':'gross holdings value (NAV unavailable for this date)'}}.${{(h.fx_missing_conids||[]).length?` ${{h.fx_missing_conids.length}} holding(s) have no usable FX rate and are excluded from weights.`:''}} RSI (14) is Wilder's 14-day relative strength index from each holding's daily closing marks; above 70 is marked overbought and below 30 oversold.`:h.failed?'Holdings could not be loaded for this date. Refresh the page or pick another As of date.':'';
const rm=risk.metrics||{{}},riskItems=[['Total return',pct(rm.total_return)],['Annualized return',pct(rm.annualized_return)],['Annualized volatility',pct(rm.annualized_volatility)],['Risk-free rate',rm.risk_free_annual_rate==null?'Unavailable':pct(rm.risk_free_annual_rate)],['Sharpe ratio',rm.sharpe_ratio==null?'Unavailable':fmt(rm.sharpe_ratio)],['Expected maximum drawdown (1 year)',rm.expected_maximum_drawdown_1y==null?'Unavailable':pct(rm.expected_maximum_drawdown_1y)],['Actual maximum drawdown (1 year)',rm.actual_maximum_drawdown_1y==null?'Unavailable':pct(rm.actual_maximum_drawdown_1y)],['Tail maximum drawdown (1 year)',rm.tail_maximum_drawdown_1y==null?'Unavailable':pct(rm.tail_maximum_drawdown_1y)],['Crisis maximum drawdown (15/9/2008–9/3/2009)',rm.crisis_maximum_drawdown==null?'Unavailable':pct(rm.crisis_maximum_drawdown)],['VaR 95% — daily',pct(rm.var_95_daily)],['Daily Expected Tail Loss (95%)',pct(rm.daily_expected_tail_loss)],['Win rate',pct(rm.win_rate)],['BLUE mean daily return',pct(rm.blue_mean_daily_return)]];
document.querySelector('#risk-asof').textContent=rm.period_end||risk.as_of||'—';document.querySelector('#risk-metrics').innerHTML=riskItems.map(metricCard).join('');const etl=risk.expected_tail_loss_contributors||{{}},contributorRows=items=>items.map(x=>`<tr><td><strong>${{esc(x.symbol||x.conid)}}</strong><div class="muted">${{esc(x.description||'')}}</div></td><td>${{pct(x.expected_tail_loss_contribution)}}</td></tr>`).join('')||'<tr><td colspan="2">Unavailable</td></tr>';document.querySelector('#etl-top').innerHTML=contributorRows(etl.top||[]);document.querySelector('#etl-bottom').innerHTML=contributorRows(etl.bottom||[]);const displayNotes=riskDisplayNotes(risk,pct);document.querySelector('#etl-note').textContent=displayNotes.attribution;document.querySelector('#risk-note').textContent=displayNotes.risk;
const cs=risk.crisis_scenarios;document.querySelector('#crisis-scenarios-card').hidden=!cs;document.querySelector('#crisis-scenarios').innerHTML=cs&&(cs.scenarios||[]).length?cs.scenarios.map(x=>`<tr><td><strong>${{esc(x.label)}}</strong></td><td>${{esc(x.crisis_start)}} to ${{esc(x.crisis_end)}}</td><td>${{x.available?pct(x.maximum_drawdown):'Unavailable'}}${{x.available?'':`<div class="muted">${{esc(x.reason||'')}}</div>`}}</td><td>${{x.available?`${{x.proxy_components||0}} (${{x.scaled_components||0}})`:'—'}}</td></tr>`).join(''):'<tr><td colspan="4">Not calculated yet</td></tr>';document.querySelector('#crisis-scenarios-note').textContent=cs?`Holdings as of ${{cs.holdings_as_of}}, in ${{cs.reporting_currency||'—'}}. ${{cs.limitations||''}}`:'Crisis scenarios are not available for this portfolio yet.';
if(displayNotes.attributionAvailable){{const guide=document.createElement('a');guide.className='metric-guide-inline';guide.href=metricGuideHref('ETL residual contribution');guide.textContent=' Read the residual-contribution explanation ↗';document.querySelector('#etl-note').appendChild(guide);}}
const run=health.latest_run||{{}},portfolio=health.portfolio||{{}},assurance=health.portfolio_analyst_assurance||{{}},assuranceResult=assurance.result_json||{{}},burnIn=health.burn_in||{{}},burnResults=health.burn_in_recent_results||[];document.querySelector('#health-status').innerHTML=[['Environment',health.environment],['Status',health.status],['Latest portfolio date',portfolio.latest_date||'—'],['Latest successful run',run.completed_at||'—']].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1]||'—')}}</strong></div>`).join('');document.querySelector('#health-note').textContent=health.message;document.querySelector('#assurance-status').textContent=assurance.status?assurance.status.toUpperCase():'Awaiting first run';document.querySelector('#assurance-summary').innerHTML=[['Assurance date',assurance.assurance_date||'—'],['Portfolio as of',assurance.as_of_date||'—'],['Checks passed',assurance.checks_passed??0],['Warnings / failures',`${{assurance.checks_warned??0}} / ${{assurance.checks_failed??0}}`]].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1])}}</strong></div>`).join('');document.querySelector('#assurance-note').textContent=assuranceResult.model_review?.summary||'The daily assurance job independently reconciles portfolio data, analytics, report integrity and the approved August layouts.';document.querySelector('#burn-in-status').textContent=burnIn.status?burnIn.status.toUpperCase():'Not started';document.querySelector('#burn-in-summary').innerHTML=[['Start date',burnIn.start_date||'—'],['Consecutive passing days',`${{burnIn.consecutive_pass_days??0}} / ${{burnIn.required_consecutive_days??14}}`],['Original earliest completion',burnIn.earliest_completion_date||'—'],['Last result',burnIn.last_result_date||'Awaiting Day 1'],['Last failure',burnIn.last_failure_date||'None']].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1])}}</strong></div>`).join('');document.querySelector('#burn-in-results').innerHTML=burnResults.length?'<h3>Recent daily acceptance results</h3>'+burnResults.map(x=>`<p><strong>${{esc(x.result_date)}} — ${{esc(x.status.toUpperCase())}}</strong><br><span class="muted">Checked ${{esc(x.checked_at)}}</span></p>`).join(''):'<p>No formal Stage 4 result has been recorded yet.</p>';
if(account==='alpha-world-ms'&&!risk.runtime_version){{
 document.querySelector('#etl-note').textContent='Security-level tail attribution is not qualified for this synthetic composite; no USD-position attribution is substituted.';
 const evidence=risk.analytics_evidence||{{}},rate=risk.risk_free;
 document.querySelector('#risk-note').textContent=(risk.message||'MS risk results are unavailable.')+(risk.cache_status==='hit'?` HKD NAV period: ${{evidence.start}} to ${{evidence.end}}. Valid daily intervals: ${{evidence.daily_return_observations}}. Risk-free rate: 1M HIBOR, inception-to-valuation average of ${{rate?.observation_count||0}} published fixings. One-year drawdown, crisis replay and HIBOR + Spread benchmark comparison remain unqualified. Methodology: ${{evidence.version}}.`:'');
 if(risk.cache_status==='hit'){{
  const msItems=riskItems.filter(x=>!['Expected maximum drawdown (1 year)','Actual maximum drawdown (1 year)','Tail maximum drawdown (1 year)','Crisis maximum drawdown (15/9/2008–9/3/2009)','BLUE mean daily return','Daily Expected Tail Loss (95%)'].includes(x[0]));
  msItems.push(['Maximum drawdown (since inception)',pct(rm.maximum_drawdown)],['Daily Expected Tail Loss (95%)',pct(rm.cvar_95_daily)]);
  document.querySelector('#risk-metrics').innerHTML=msItems.map(metricCard).join('');
 }}
}}
[['#export-performance','performance'],['#export-holdings','holdings'],['#export-cash','cash-activity']].forEach(x=>document.querySelector(x[0]).href=`/api/portfolio/${{account}}/export/${{x[1]}}.csv?as_of=${{encodeURIComponent(asOf)}}`);const tradeCsv=document.querySelector('#export-trades-csv');if(tradeCsv)tradeCsv.href=`/api/portfolio/${{account}}/export/trades.csv?as_of=${{encodeURIComponent(asOf)}}`;
const cashFrom=document.querySelector('#cash-from'),cashTo=document.querySelector('#cash-to');if(!cashFrom.value)cashFrom.value=historyStartDate;cashFrom.max=asOf;cashTo.max=asOf;cashTo.value=asOf;if(cashFrom.value>asOf)cashFrom.value=historyStartDate;if(activeSection==='cash')await loadCashActivity().catch(()=>{{document.querySelector('#cash-note').textContent='Cash activity could not be loaded for this date. Refresh the page or pick another As of date.'}});
const tradeFrom=document.querySelector('#trade-from'),tradeTo=document.querySelector('#trade-to');if(!tradeFrom.value)tradeFrom.value=historyStartDate;tradeTo.max=asOf;tradeTo.value=asOf;if(tradeFrom.value>asOf)tradeFrom.value=historyStartDate;if(activeSection==='trades')await loadTradeHistory().catch(()=>{{document.querySelector('#trade-note').textContent='Trade history could not be loaded for this date. Refresh the page or pick another As of date.'}});
draw('#chart',p.series,[{{key:'nav',color:chartColors.portfolio,label:`${{accountDisplayName}} NAV`}}],{{yLabel:`NAV (${{reportingCurrency}})`,unit:reportingCurrency,niceYTicks:true,targetYIntervals:8,emptyMessage:p.failed?'NAV history could not be loaded for this date.':'No NAV history up to this date.'}});
const benchmarkSelect=document.querySelector('#benchmark-select'),selected=benchmarks.find(x=>x.benchmark_id===benchmarkSelect.value);
if(activeSection==='performance'&&selected)await renderBenchmark(selected.benchmark_id,selected,asOf).catch(()=>{{document.querySelector('#benchmark-note').textContent='This benchmark comparison could not be loaded for this date.';draw('#benchmark-chart',[],[],{{emptyMessage:'The benchmark comparison could not be loaded.'}})}});
if(activeSection==='risk'){{if(selected)fetch(`/api/portfolio/${{account}}/benchmark-comparison?benchmark_id=${{encodeURIComponent(selected.benchmark_id)}}&as_of=${{encodeURIComponent(asOf)}}`).then(json).then(bc=>renderRollingBenchmark(bc.available?bc:null)).catch(()=>renderRollingBenchmark(null));else renderRollingBenchmark(null)}}
const periodFrom=document.querySelector('#period-from'),periodTo=document.querySelector('#period-to');periodFrom.max=asOf;periodTo.max=asOf;if(periodTo.value>asOf)periodTo.value=asOf;if(periodFrom.value>asOf)periodFrom.value='';
if(activeSection==='performance')await loadPerformancePeriods().catch(()=>{{document.querySelector('#period-note').textContent='Period returns are unavailable for this date.'}})
}}finally{{grid.classList.remove('loading')}}
}};
const autonomyFallback={{status:'viewer_restricted',control:{{}},latest_run:{{}},open_incidents:[],recent_actions:[],last_known_good:{{}},recovery:{{}},authority_reviews:[]}};
const autonomyRequest=portalOwner?fetch('/api/system/autonomous-administrator').then(json).catch(()=>autonomyFallback):Promise.resolve({{status:'viewer_restricted',control:{{}},latest_run:{{}},open_incidents:[],recent_actions:[],last_known_good:{{}},recovery:{{}},authority_reviews:[]}});
const agentRequest=previewMode?Promise.resolve({{tools:[],ai_model:{{enabled:false,message:'AI analysis remains disabled until this account passes activation.'}},admin_actions:{{enabled:false,message:'Use the platform Administration page for approved actions.',approval_ttl_minutes:10,actions:[]}}}}):fetch('/api/agent/tools').then(json).catch(()=>({{tools:[],ai_model:{{enabled:false,message:'The AI analyst could not be reached. Refresh the page to try again.'}},admin_actions:{{enabled:false,message:'Approved actions could not be loaded. Refresh the page to try again.',approval_ttl_minutes:10,actions:[]}}}}));
const reportsRequest=previewMode?Promise.resolve({{message:'Monthly reporting remains disabled until account-specific templates and calculations pass validation.',ready_months:0,review_ready_months:0,months:[]}}):fetch(`/api/accounts/${{encodeURIComponent(account)}}/reporting/monthly`).then(json).catch(()=>({{message:'Monthly reports could not be loaded. Refresh the page to try again.',ready_months:0,review_ready_months:0,months:[]}}));
Promise.all([fetch(`/api/portfolio/${{account}}/available-dates`).then(json),fetch(`/api/portfolio/${{account}}/benchmark-status`).then(json).catch(()=>({{benchmarks:[],failed:true}})),agentRequest,reportsRequest,autonomyRequest]).then(async([d,b,a,reports,autonomy])=>{{
const asOfSelect=document.querySelector('#asof-select'),benchmarkSelect=document.querySelector('#benchmark-select');
if(!d.dates.length)throw new Error('No portfolio dates are available');
const reportMonth=document.querySelector('#report-month'),reportPrepare=document.querySelector('#report-prepare'),reportStatus=document.querySelector('#report-status'),reportDownloads=document.querySelector('#report-downloads');
const reportOwnerReview=document.querySelector('#report-owner-review'),reportReleaseDetails=document.querySelector('#report-release-details'),reportReleaseList=document.querySelector('#report-release-list'),reportRelease=document.querySelector('#report-release'),reportReleaseStatus=document.querySelector('#report-release-status');let releasePreview=null,reportReviewRequest=0;
if(portalOwner)reportPrepare.style.display='none';
const renderReportCounts=()=>{{document.querySelector('#report-note').textContent=reports.message+` Internally released: ${{reports.ready_months}}. Ready for owner review: ${{reports.review_ready_months}}. Total month-end snapshots: ${{reports.months.length}}.`}};
const renderReportOptions=()=>{{reportMonth.innerHTML=reports.months.map(x=>`<option value="${{esc(x.period_end)}}">${{esc(x.period_end)}} — ${{x.replacement_pending?'Previously approved files available; replacement pending':x.ready?'Ready':x.partially_released?'Partially released':x.review_ready?'Ready for review':'Generation pending'}}</option>`).join('')}};
renderReportCounts();renderReportOptions();
const selectedReportTypes=()=>Array.from(reportReleaseList.querySelectorAll('input[data-report-type]')).filter(input=>input.checked).map(input=>input.dataset.reportType);
const updateReleaseButton=()=>{{if(!releasePreview){{reportRelease.disabled=true;return}}const selected=new Set(selectedReportTypes()),current=new Set(releasePreview.approved_types),changed=selected.size!==current.size||Array.from(selected).some(kind=>!current.has(kind)),adds=Array.from(selected).some(kind=>!current.has(kind));reportRelease.disabled=!changed||(adds&&!releasePreview.release_eligible);reportReleaseStatus.textContent=!changed?'No changes selected.':adds&&!releasePreview.release_eligible?'New approval is blocked: '+String(releasePreview.release_blocker||'validation unavailable').slice(0,220):'Ready to submit the selected file approvals.'}};
reportReleaseList.addEventListener('change',updateReleaseButton);
const loadReportChecklist=async()=>{{const request=++reportReviewRequest,period=reportMonth.value;releasePreview=null;reportRelease.disabled=true;reportReleaseList.innerHTML='';reportReleaseDetails.textContent='';reportReleaseStatus.textContent='Loading file approval status…';try{{const response=await fetch(`/api/accounts/${{encodeURIComponent(account)}}/reporting/monthly/${{encodeURIComponent(period)}}/release-preview`),payload=await response.json();if(!response.ok)throw new Error(typeof payload.detail==='string'?payload.detail:`HTTP ${{response.status}}`);if(request!==reportReviewRequest)return;releasePreview=payload;reportReleaseDetails.textContent=`${{payload.subject_id}} · ${{payload.period_end}} · Internal/shareholder use only`;reportReleaseList.innerHTML=payload.artifacts.map(item=>{{const url=`/api/accounts/${{encodeURIComponent(account)}}/reporting/review-artifacts/${{item.generation_run_id}}/download`;return `<div style="display:flex;align-items:center;justify-content:space-between;gap:16px;padding:14px;border:1px solid #dce2eb;border-radius:14px;flex-wrap:wrap"><label style="display:flex;align-items:center;gap:10px"><input type="checkbox" data-report-type="${{esc(item.report_type)}}" ${{item.approved?'checked':''}}><span><strong>${{esc(item.label)}}</strong><br><small>${{item.approved?'Released to authorized users':'Not released'}}</small></span></label><a href="${{esc(url)}}">Review / download file</a></div>`}}).join('');updateReleaseButton()}}catch(error){{if(request===reportReviewRequest)reportReleaseStatus.textContent='File checklist unavailable: '+String(error.message||'validation unavailable').slice(0,220)}}}};
const updateReportMonth=async()=>{{const selected=reports.months.find(x=>x.period_end===reportMonth.value);reportDownloads.innerHTML='';releasePreview=null;reportReleaseList.innerHTML='';reportReleaseDetails.textContent='';reportReleaseStatus.textContent='';reportRelease.disabled=true;reportOwnerReview.style.display=portalOwner&&selected?.downloadable?'block':'none';if(!selected){{reportPrepare.disabled=true;reportStatus.textContent='No frozen month-end snapshot is available.';return}}reportPrepare.disabled=!selected.downloadable;reportStatus.textContent=selected.replacement_pending?`Previously approved ${{selected.period_end}} files remain available. Corrected inputs do not approve replacement files; replacements require a separate owner decision.`:selected.ready?`The ${{selected.period_end}} files are internally released.`:selected.partially_released?`Some ${{selected.period_end}} files are released; the owner can change each file below.`:selected.review_ready?`The ${{selected.period_end}} files are ready for owner review.`:`The ${{selected.period_end}} frozen snapshot exists, but its factsheet is not available yet.`;if(portalOwner&&selected.downloadable)await loadReportChecklist()}};
reportMonth.addEventListener('change',()=>{{void updateReportMonth()}});void updateReportMonth();
reportPrepare.addEventListener('click',async()=>{{reportPrepare.disabled=true;reportDownloads.innerHTML='';reportStatus.textContent='Validating available files for this portfolio…';try{{const prepared=await fetch(`/api/accounts/${{encodeURIComponent(account)}}/reporting/monthly/${{encodeURIComponent(reportMonth.value)}}/prepare`).then(json);if(prepared.account_id!==account)throw new Error('report_subject_mismatch');reportStatus.textContent=prepared.message;reportDownloads.innerHTML=prepared.artifacts.map(x=>`<a class="button" href="${{esc(x.download_url)}}">Download ${{esc(x.label)}}</a>`).join('')}}catch(error){{reportStatus.textContent='The files could not be prepared for this portfolio. Please check system health.'}}finally{{const selected=reports.months.find(x=>x.period_end===reportMonth.value);reportPrepare.disabled=!selected?.downloadable}}}});
reportRelease.addEventListener('click',async()=>{{if(!releasePreview)return;const period=reportMonth.value,selected=selectedReportTypes();reportRelease.disabled=true;reportReleaseStatus.textContent='Applying file approval changes…';try{{const response=await fetch(`/api/accounts/${{encodeURIComponent(account)}}/reporting/monthly/${{encodeURIComponent(period)}}/release`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{snapshot_id:releasePreview.snapshot_id,package_sha256:releasePreview.package_sha256,selection_version:releasePreview.selection_version,selected_report_types:selected}})}}),result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:`HTTP ${{response.status}}`);const fresh=await fetch(`/api/accounts/${{encodeURIComponent(account)}}/reporting/monthly`).then(json);reports.months=fresh.months;reports.ready_months=fresh.ready_months;reports.review_ready_months=fresh.review_ready_months;renderReportCounts();renderReportOptions();reportMonth.value=period;await updateReportMonth();reportReleaseStatus.textContent=result.message}}catch(error){{await loadReportChecklist();reportReleaseStatus.textContent='Could not confirm the changes: '+String(error.message||'request failed').slice(0,220)+'. Current file status is shown above.'}}}});
document.querySelector('#agent-status').textContent=`Interactive analyst ready · ${{a.tools.length}} deterministic tools`;
document.querySelector('#agent-note').textContent=a.ai_model.message+(previewMode?'':' Exact figures, requested metrics and chart series are produced by deterministic read-only tools; GPT-5.6 Sol supplies grounded qualitative explanation only. Approved inputs: NAV, performance, holdings, benchmark, risk, freshness and sector results for '+account+'. Excluded: raw IBKR files, Flex credentials, database credentials and personal information. Prohibited: arbitrary SQL, shell access, trading, secret retrieval, permission changes and self-modification.');
const autonomyRun=autonomy.latest_run||{{}},autonomyControl=autonomy.control||{{}},recovery=autonomy.recovery||{{}},baseline=autonomy.last_known_good||{{}};document.querySelector('#autonomy-status').textContent=autonomy.status==='active'?'Active — controlled-autonomy-v1':'Paused';document.querySelector('#autonomy-note').textContent='Runs in Azure every five minutes. Deterministic policy permits one cooled-down market-data repair or activation of the exact last-known-good website revision after three consecutive portal failures. IBKR retries, formulas, permissions and secrets remain excluded.';document.querySelector('#autonomy-summary').innerHTML=[['Latest run',autonomyRun.completed_at||'Awaiting first run'],['Run status',autonomyRun.status||'—'],['Production authority',autonomyControl.production_active?'Active':'Not active'],['Open incidents',autonomy.open_incidents.length],['Portal failure count',recovery.consecutive_portal_failures??0],['Rollback threshold',autonomyControl.rollback_failure_threshold??3],['Last-known-good revision',baseline.revision_name||'Not registered'],['Monthly review due',autonomyControl.monthly_review_due_at||'Not scheduled']].map(x=>`<div class="status-item"><span class="label">${{esc(x[0])}}</span><strong>${{esc(x[1])}}</strong></div>`).join('');document.querySelector('#autonomy-incidents').innerHTML=autonomy.open_incidents.length?'<h3>Open incidents</h3>'+autonomy.open_incidents.map(x=>`<p><strong>${{esc(x.severity.toUpperCase())}} — ${{esc(x.check_id)}}</strong><br>${{esc(x.description)}}<br><span class="muted">Last observed ${{esc(x.last_seen_at)}} · ${{esc(x.occurrence_count)}} checks</span></p>`).join(''):'<p>No open incidents.</p>';const authorityReviews=autonomy.authority_reviews||[],pendingReview=authorityReviews.find(x=>x.status!=='approved');document.querySelector('#autonomy-reviews').innerHTML='<h3>Monthly authority reviews</h3>'+(authorityReviews.length?authorityReviews.map(x=>`<p><strong>${{esc(x.review_month)}} — ${{esc(x.status)}}</strong><br>Due ${{esc(x.due_at)}}${{x.status==='approved'?' · Approved '+esc(x.approved_at||''):''}}</p>`).join(''):'<p>The first monthly review will be created on the first day of the next month.</p>')+(pendingReview?`<div class="actions"><input id="authority-review-confirmation" maxlength="80" aria-label="Monthly authority review confirmation" placeholder="APPROVE MONTHLY AUTHORITY REVIEW ${{esc(pendingReview.review_month)}}"><button id="authority-review-approve" class="button" type="button">Approve ${{esc(pendingReview.review_month)}} review</button></div><div id="authority-review-result" class="muted"></div>`:'');if(pendingReview){{document.querySelector('#authority-review-approve').addEventListener('click',async()=>{{const button=document.querySelector('#authority-review-approve'),result=document.querySelector('#authority-review-result');button.disabled=true;try{{const approved=await fetch(`/api/system/autonomous-administrator/reviews/${{pendingReview.review_id}}/approve`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{confirmation:document.querySelector('#authority-review-confirmation').value}})}}).then(json);result.textContent=`Approved ${{approved.review_month}}. Next review due ${{approved.next_review_due_at}}.`}}catch(error){{result.textContent='Review was not approved. Type the exact confirmation phrase.';button.disabled=false}}}})}};
const analysisRun=document.querySelector('#analysis-run'),analysisQuestion=document.querySelector('#analysis-question'),chatThread=document.querySelector('#ai-chat-thread'),conversationList=document.querySelector('#ai-conversation-list'),chatTitle=document.querySelector('#ai-chat-title'),exportMd=document.querySelector('#ai-export-md'),exportJson=document.querySelector('#ai-export-json'),archiveChat=document.querySelector('#ai-archive-chat');let activeConversation=null,chatBusy=false;
const factValue=f=>f.format==='percent'?pct(f.value):f.format==='currency'?new Intl.NumberFormat('en-US',{{style:'currency',currency:f.currency||'USD',maximumFractionDigits:2}}).format(Number(f.value)):fmt(f.value);
const emptyChat=()=>{{chatThread.innerHTML=`<div class="ai-chat-empty"><div class="label">Live grounded analysis</div><div class="value">Ask about this portfolio</div><div class="muted">The analyst uses validated portfolio tools for every figure and chart. Follow-up questions use this saved conversation as context while current validated data remain authoritative.</div><div class="ai-prompt-chips"><button class="ai-prompt-chip" type="button">Compare performance with the selected benchmark</button><button class="ai-prompt-chip" type="button">Explain the portfolio's main risks</button><button class="ai-prompt-chip" type="button">Show NAV history and key performance metrics</button></div></div>`;bindPromptChips()}};
const updateChatHeader=conversation=>{{chatTitle.textContent=conversation?.title||'New conversation';const enabled=Boolean(conversation?.conversation_id);[exportMd,exportJson,archiveChat].forEach(element=>element.hidden=!enabled);if(enabled){{exportMd.href=`/api/agent/conversations/${{conversation.conversation_id}}/export.md`;exportJson.href=`/api/agent/conversations/${{conversation.conversation_id}}/export.json`}}}};
const downloadCanvas=(canvas,filename)=>{{canvas.toBlob(blob=>{{if(!blob)return;const url=URL.createObjectURL(blob),anchor=document.createElement('a');anchor.href=url;anchor.download=filename;document.body.appendChild(anchor);anchor.click();anchor.remove();URL.revokeObjectURL(url)}},'image/png')}};
const appendChatMessage=(role,content,response=null,messageId=null,createdAt=null)=>{{const row=document.createElement('div');row.className=`ai-message ${{role}}`;if(role==='user'){{row.innerHTML=`<div class="ai-message-bubble"><div>${{esc(content)}}</div><div class="ai-message-meta">${{esc(createdAt||'Saved with this conversation')}}</div></div>`;chatThread.appendChild(row);chatThread.scrollTop=chatThread.scrollHeight;return}}const n=response?.narrative||{{title:'Portfolio analysis',summary:content,key_points:[],risks_and_caveats:[]}},sources=(response?.citations||[]).map(x=>`${{esc(x.citation_id)}} — ${{esc(x.dataset||x.source)}}${{x.through?' through '+esc(x.through):''}}`).join('<br>'),facts=(response?.deterministic_facts||[]).map(f=>`<div class="metric"><div class="label">${{esc(f.label)}}</div><div class="value">${{esc(factValue(f))}}</div><div class="muted">As of ${{esc(f.as_of||response.as_of)}}</div></div>`).join(''),base=activeConversation&&messageId?`/api/agent/conversations/${{activeConversation}}/messages/${{messageId}}`:null,charts=(response?.visualizations||[]).map((chart,index)=>{{const canvasId=`ai-chart-${{String(messageId||Date.now()).replace(/[^a-zA-Z0-9_-]/g,'')}}-${{index}}`,downloads=base?`<div class="actions"><button class="button ai-png-download" data-canvas="${{canvasId}}" data-filename="${{esc(chart.id)}}.png" type="button">PNG</button><a class="button" href="${{base}}/charts/${{encodeURIComponent(chart.id)}}.svg">SVG</a><a class="button" href="${{base}}/charts/${{encodeURIComponent(chart.id)}}.csv">CSV data</a></div>`:'';return `<div class="ai-artifact"><div class="ai-artifact-heading"><div><div class="label">Generated chart</div><strong>${{esc(chart.title)}}</strong></div>${{downloads}}</div><div class="chart-wrap"><canvas id="${{canvasId}}" width="1100" height="320" aria-label="${{esc(chart.title)}}"></canvas><div class="chart-tooltip" role="status" aria-live="polite"></div></div></div>`}}).join(''),analysisDownload=base?`<div class="actions ai-answer-downloads"><a class="button" href="${{base}}/analysis.json">Download analysis JSON</a></div>`:'';row.innerHTML=`<div class="ai-message-bubble ai-answer"><h3>${{esc(n.title)}}</h3><p>${{esc(n.summary)}}</p>${{(n.key_points||[]).map(x=>`<p>• ${{esc(x.text)}} <span class="muted">[${{x.citation_ids.map(esc).join(', ')}}]</span></p>`).join('')}}${{(n.risks_and_caveats||[]).map(x=>`<p><strong>Caveat:</strong> ${{esc(x.text)}} <span class="muted">[${{x.citation_ids.map(esc).join(', ')}}]</span></p>`).join('')}}${{facts?`<div class="metrics">${{facts}}</div>`:''}}${{charts}}${{analysisDownload}}<p class="ai-message-meta"><strong>Sources:</strong><br>${{sources||'Validated portfolio database'}}<br>${{esc(response?.disclaimer||'')}}${{response?.correlation_id?' · Correlation ID: '+esc(response.correlation_id):''}}</p></div>`;chatThread.appendChild(row);(response?.visualizations||[]).forEach((chart,index)=>{{const canvas=row.querySelectorAll('canvas')[index];draw(`#${{canvas.id}}`,chart.series,chart.lines,{{yLabel:chart.y_label,unit:chart.unit||'',niceYTicks:chart.id==='nav-history',yTickStep:chart.id==='benchmark-comparison'?10:null}})}});row.querySelectorAll('.ai-png-download').forEach(button=>button.addEventListener('click',()=>downloadCanvas(document.getElementById(button.dataset.canvas),button.dataset.filename)));chatThread.scrollTop=chatThread.scrollHeight}};
const renderConversationList=(items,selected=null)=>{{conversationList.innerHTML=items.length?items.map(item=>`<button class="ai-conversation-item${{item.conversation_id===selected?' active':''}}" type="button" data-conversation-id="${{esc(item.conversation_id)}}"><strong>${{esc(item.title)}}</strong><span>${{esc(item.message_count)}} messages · ${{esc(String(item.updated_at).slice(0,16).replace('T',' '))}}</span></button>`).join(''):'<div class="muted">No saved conversations yet.</div>';conversationList.querySelectorAll('[data-conversation-id]').forEach(button=>button.addEventListener('click',()=>openConversation(button.dataset.conversationId)))}};
const refreshConversations=async selected=>{{const result=await fetch(`/api/agent/conversations?subject_id=${{encodeURIComponent(account)}}`).then(json);renderConversationList(result.conversations,selected);return result.conversations}};
const openConversation=async id=>{{const result=await fetch(`/api/agent/conversations/${{id}}`).then(json);activeConversation=result.conversation.conversation_id;updateChatHeader(result.conversation);chatThread.innerHTML='';if(!result.messages.length)emptyChat();else result.messages.forEach(message=>appendChatMessage(message.role,message.content_text,message.response_json,message.message_id,message.created_at));await refreshConversations(activeConversation)}};
const createConversation=async()=>{{const result=await fetch('/api/agent/conversations',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{subject_id:account}})}}).then(json);activeConversation=result.conversation_id;updateChatHeader(result);emptyChat();await refreshConversations(activeConversation);analysisQuestion.focus();return result}};
const showTyping=()=>{{const row=document.createElement('div');row.className='ai-message assistant';row.id='ai-typing-row';row.innerHTML='<div class="ai-typing" aria-label="AI Portfolio Analyst is responding"><i></i><i></i><i></i></div>';chatThread.appendChild(row);chatThread.scrollTop=chatThread.scrollHeight}};
const bindPromptChips=()=>document.querySelectorAll('.ai-prompt-chip').forEach(button=>button.addEventListener('click',()=>{{analysisQuestion.value=button.textContent;analysisQuestion.focus()}}));
const sendChatMessage=async()=>{{const question=analysisQuestion.value.trim();if(!question||chatBusy||!a.ai_model.enabled)return;chatBusy=true;analysisRun.disabled=true;try{{if(!activeConversation)await createConversation();if(chatThread.querySelector('.ai-chat-empty'))chatThread.innerHTML='';appendChatMessage('user',question,null,null,'Sending…');analysisQuestion.value='';showTyping();const response=await fetch(`/api/agent/conversations/${{activeConversation}}/messages`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question,as_of:asOfSelect.value,benchmark_id:benchmarkSelect.value||null}})}}).then(json);document.querySelector('#ai-typing-row')?.remove();const temporary=chatThread.querySelector('.ai-message.user:last-of-type .ai-message-meta');if(temporary)temporary.textContent='Saved';appendChatMessage('assistant','',response,response.assistant_message_id,response.generated_at);chatTitle.textContent=response.conversation_title;await refreshConversations(activeConversation)}}catch(error){{document.querySelector('#ai-typing-row')?.remove();appendChatMessage('assistant',`The grounded analysis was not saved because a data, policy or model validation gate did not pass. Reference: ${{error.message||'unknown'}}`)}}finally{{chatBusy=false;analysisRun.disabled=!a.ai_model.enabled;analysisQuestion.focus()}}}};
document.querySelector('#ai-new-chat').addEventListener('click',()=>createConversation().catch(()=>{{}}));analysisRun.addEventListener('click',sendChatMessage);analysisQuestion.addEventListener('keydown',event=>{{if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();sendChatMessage()}}}});archiveChat.addEventListener('click',async()=>{{if(!activeConversation)return;await fetch(`/api/agent/conversations/${{activeConversation}}/archive`,{{method:'POST'}}).then(json);activeConversation=null;updateChatHeader(null);emptyChat();const items=await refreshConversations(null);if(items.length)await openConversation(items[0].conversation_id)}});
analysisRun.disabled=true;
const admin=a.admin_actions,adminAction=document.querySelector('#admin-action'),adminPreview=document.querySelector('#admin-preview'),adminExecute=document.querySelector('#admin-execute'),adminConfirmation=document.querySelector('#admin-confirmation');let currentApproval=null;document.querySelector('#admin-status').textContent=admin.enabled?'Approval workflow ready':'Disabled';document.querySelector('#admin-note').textContent=admin.message+' Every preview expires after '+admin.approval_ttl_minutes+' minutes and can be executed only once.';adminAction.innerHTML=admin.actions.map(x=>`<option value="${{esc(x.action_type)}}">${{esc(x.label)}}</option>`).join('');adminPreview.disabled=!admin.enabled;
adminPreview.addEventListener('click',async()=>{{adminPreview.disabled=true;adminExecute.disabled=true;adminConfirmation.disabled=true;document.querySelector('#admin-result').textContent='';const result=document.querySelector('#admin-preview-result');result.textContent='Creating an integrity-bound preview…';try{{currentApproval=await fetch('/api/admin/actions/preview',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action_type:adminAction.value}})}}).then(json);const p=currentApproval.preview;result.innerHTML=`<strong>${{esc(p.label)}}</strong><p>${{esc(p.description)}}</p><p>Target: <code>${{esc(p.target)}}</code> · Environment: <strong>${{esc(p.environment)}}</strong><br>Expires: ${{esc(currentApproval.expires_at)}}<br>Payload digest: <code>${{esc(currentApproval.action_payload_sha256)}}</code></p><p><strong>Will not allow:</strong> ${{p.does_not_allow.map(esc).join(', ')}}</p><p>Type exactly: <code>${{esc(currentApproval.confirmation_phrase)}}</code></p>`;adminConfirmation.value='';adminConfirmation.placeholder=currentApproval.confirmation_phrase;adminConfirmation.disabled=false;adminExecute.disabled=false}}catch(error){{currentApproval=null;result.textContent='Unable to create an approval preview.'}}finally{{adminPreview.disabled=!admin.enabled}}}});
adminExecute.addEventListener('click',async()=>{{if(!currentApproval)return;adminExecute.disabled=true;adminPreview.disabled=true;const result=document.querySelector('#admin-result');result.textContent='Executing the single approved staging action…';try{{const response=await fetch(`/api/admin/actions/${{currentApproval.approval_id}}/approve`,{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action_payload_sha256:currentApproval.action_payload_sha256,confirmation:adminConfirmation.value}})}}).then(json);result.innerHTML=`<strong>Executed once.</strong> Action: ${{esc(response.action_type)}} · Approval ID: ${{esc(response.approval_id)}} · Correlation ID: ${{esc(response.correlation_id)}}`;currentApproval=null;adminConfirmation.value='';adminConfirmation.disabled=true}}catch(error){{result.textContent='Action was not executed. Check the exact approval phrase or create a new preview.';adminExecute.disabled=false}}finally{{adminPreview.disabled=!admin.enabled}}}});
asOfSelect.innerHTML=d.dates.map(x=>`<option value="${{esc(x)}}">${{esc(asOfLabel(x))}}</option>`).join('');asOfSelect.value=storedAsOf(d.dates)||d.latest;showFreshness(d.latest);
benchmarks=b.benchmarks||[];benchmarkSelect.innerHTML=benchmarks.map(x=>`<option value="${{esc(x.benchmark_id)}}">${{x.mapping_type==='primary'?'Primary':'Secondary'}} — ${{esc(x.name)}} (${{esc(x.symbol)}})</option>`).join('');
if(benchmarks.length){{const primary=benchmarks.find(x=>x.mapping_type==='primary')||benchmarks[0];benchmarkSelect.value=primary.benchmark_id;benchmarkSelect.addEventListener('change',()=>{{const selected=benchmarks.find(x=>x.benchmark_id===benchmarkSelect.value);renderBenchmark(selected.benchmark_id,selected,asOfSelect.value).catch(()=>{{document.querySelector('#benchmark-note').textContent='Unable to load this comparison.'}})}})}}else{{document.querySelector('#benchmark').textContent=b.failed?'Unavailable':'Not configured';if(b.failed)document.querySelector('#benchmark-note').textContent='The benchmark list could not be loaded. Refresh the page to try again.';benchmarkSelect.disabled=true}}
asOfSelect.addEventListener('change',()=>(rememberAsOf(asOfSelect.value),renderAsOf(asOfSelect.value)).catch(error=>loadFailure(error,'The figures for this date could not be loaded. Pick another date or refresh the page.')));
if(!previewMode){{const savedConversations=await refreshConversations(null);if(savedConversations.length)await openConversation(savedConversations[0].conversation_id);else{{updateChatHeader(null);emptyChat()}}}}else{{updateChatHeader(null);emptyChat()}}analysisRun.disabled=!a.ai_model.enabled;
await renderAsOf(asOfSelect.value)
}}).catch(error=>loadFailure(error,'The figures for this account could not be loaded. Refresh the page. If this keeps happening, the daily data refresh may have failed.'));
</script>{live_page_assets}{design_controls_script}</body></html>"""


# WealthRyse agents: chat panel, Explain buttons and inbox. Off unless
# AGENT_UI_MODE is "owner" or "all"; see agent_web.py.
from .agent_web import case_question_stream as _case_question_stream
from .agent_web import install as _install_agent_web

def _agent_account_names(user: dict[str, object]) -> dict[str, str]:
    from .viewer_labels import account_card_title

    return {str(item["subject_id"]): account_card_title(item) for item in _account_directory(user)}


_install_agent_web(
    app,
    _authorized_user,
    lambda user: _authorized_subject_ids(user, feature_key="viewer_access"),
    ACCOUNT_THEME_CSS,
    render_page=_platform_shell,
    account_names=_agent_account_names,
)


def _upload_intake_document(container: str, blob: str, content: bytes, content_type: str) -> None:
    if not AZURE_STORAGE_ACCOUNT_URL:
        raise RuntimeError("Private intake storage is unavailable")
    upload_bytes(AZURE_STORAGE_ACCOUNT_URL, container, blob, content, overwrite=False, content_type=content_type)


# New account onboarding (broker connections and four-stage cases). Every
# route answers 404 unless ONBOARDING_V2_ENABLED is true; see onboarding_v2_web.py.
onboarding_v2_web.install(
    app,
    onboarding_user=_onboarding_user,
    owner_user=_owner_user,
    read_database=lambda: _database(),
    write_database=lambda: _approval_database(),
    render_page=_platform_shell,
    identity_digest=_identity_digest,
    store_secret=lambda name, value: _store_flex_token_via_broker(name, value),
    tenant_id=TENANT_ID,
    platform_ip=lambda: os.environ.get("BROKER_EGRESS_IP", "").strip() or None,
    derived_definition=_builder_definition,
    download_report=lambda container, blob: download_blob_bytes(AZURE_STORAGE_ACCOUNT_URL, container, blob),
    report_container=INTERNAL_REPORT_CONTAINER,
    # Case documents for the intake agent; only while ONBOARDING_AGENTS_ENABLED is on (onboarding_agents.py).
    upload_document=_upload_intake_document,
    list_documents=lambda container, prefix: list_blobs(AZURE_STORAGE_ACCOUNT_URL, container, prefix),
    intake_container=ONBOARDING_INTAKE_CONTAINER,
    # "Ask about this case", answered by the agent service from the case's facts (also behind that switch).
    ask_case=lambda: _case_question_stream(),
)


# Account Modelling tab: wealth projection, what-if, optimisation and factor model (web_modelling.py).
_install_modelling(
    app,
    authorized_user=_authorized_user,
    access_context=_portfolio_access_context,
    parse_as_of=_parse_as_of,
    read_database=lambda: _database(),
    nav_values=_subject_nav_values,
    external_flows=_subject_external_flows,
    latest_holdings=_latest_subject_holdings,
    enrich_holdings=_enrich_subject_holdings,
    comparison_version=website_comparison_version,
    tenant_id=TENANT_ID,
    risk_analytics_version=RISK_ANALYTICS_VERSION,
)
