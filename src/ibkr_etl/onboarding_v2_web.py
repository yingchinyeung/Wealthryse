"""Website side of the new account onboarding, behind ONBOARDING_V2_ENABLED.

Pages: Account onboarding (cases in progress, accounts available to onboard,
live accounts), one page per case, and Broker connections. When the switch is
off every route here answers 404 and the existing pages are unchanged.

A derived portfolio (filtered or fixed-unit composite) is described in the
portfolio builder, which runs the Derived Portfolio Studio's validation and
replaces the Studio while the switch is on.

Database work goes through the functions below, which take a psycopg
connection with dict rows; the routes only check who is asking. Secrets reach
Key Vault through the existing write-only credential broker and are never
stored, logged or returned.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Literal, Mapping

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
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
from .composition_tree import composite_tree, filtered_tree, tree_for_definition, tree_svg
from .flex_recipe import RECIPE_STEPS
from .market_data import ALPHA_WORLD_MARKET_INSTRUMENTS
from . import onboarding_agents, yahoo_disclosure
from .monthly_reports import REPORT_TYPES, MonthlyReportError, download_prefix, validate_period_end, \
    validate_review_artifact
from .onboarding import FEATURE_KEYS
from .onboarding_cases import (
    APPROVAL_LABELS,
    APPROVALS,
    CHECK_LABELS,
    DERIVED_SOURCE_KINDS,
    MAX_OPEN_CASES,
    PRE_LIVE_STAGES,
    SOURCE_LABELS,
    STAGE_LABELS,
    OnboardingCase,
    advance,
    approve,
    assert_can_start,
    cancel,
    case_status,
    checks_for,
    go_live_phrase,
    missing_approvals,
    next_owner_action,
    progress,
    record_check,
    stage_check_states,
    terms_digest,
)
from .report_profiles import STYLES as REPORT_STYLES, ReportProfileError, build_profile, check_style
from . import onboarding_worker
from .subject_activation import activate_subject
from .subject_onboarding import configuration_sha256, dependencies_for_definition

# IBKR connection status as the existing Flex validation records it.
_FLEX_STATUS = {
    "pending_validation": "pending_check",
    "rotating": "pending_check",
    "active": "healthy",
    "suspended": "failing",
    "expired": "failing",
    "revoked": "retired",
}
# Stage 3's checks are recorded by the jobs that do the work: the daily refresh
# records history_built; the historical report run records reports_rendered with
# evidence {"reports": [{"period_end", "report_type", "file_name", "sha256",
# "download_path"}]}; the agent team records agent_review_complete. Approval 2 is
# bound to exactly that evidence, so a re-render or a new review needs a new look.
BUILD_HISTORY_EVIDENCE = ("history_built", "reports_rendered", "agent_review_complete")
# The same rule broker_onboarding_cases.target_subject_id enforces.
_PORTFOLIO_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,63}")
# A link within this website: no scheme, host, backslash or quote.
_LOCAL_PATH = re.compile(r"/(?![/])[A-Za-z0-9._~/%?=&+-]*")


class OnboardingError(ValueError):
    """A refusal the website shows to the person as it is."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def subject_id_for(broker: str, account_id: str) -> str:
    return account_id if broker == "ibkr_flex" else f"KIWOOM-{account_id}"


def _display(case: OnboardingCase) -> str:
    """The account number as the broker writes it, or the portfolio ID."""

    if case.source_kind in DERIVED_SOURCE_KINDS:
        return str(case.target_subject_id)
    return display_account_id(str(case.broker), str(case.broker_account_id))


def _definition(row: Mapping[str, Any]) -> dict[str, Any]:
    value = row["definition_json"]
    return json.loads(value) if isinstance(value, str) else dict(value)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# Cases ---------------------------------------------------------------------


def _case_from_rows(row: Mapping[str, Any], checks: Iterable[Mapping[str, Any]],
                    approvals: Iterable[Mapping[str, Any]]) -> OnboardingCase:
    check_rows = list(checks)
    return OnboardingCase(
        case_id=str(row["case_id"]),
        broker=row.get("broker"),
        broker_account_id=row.get("broker_account_id"),
        source_kind=str(row.get("source_kind") or "broker_account"),
        target_subject_id=row.get("target_subject_id"),
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
    common = {
        "subject_id": str(row["target_subject_id"]),
        "display_name": str(row["display_name"]),
        "reporting_currency": str(row["reporting_currency"]),
        "history_start_date": str(row["history_start_date"]),
    }
    kind = str(row.get("source_kind") or "broker_account")
    if kind in DERIVED_SOURCE_KINDS:
        # The owner approves the exact typed definition, not a description of it.
        terms = {"source_kind": kind, **common, "description": str(row["description"]),
                 "definition_sha256": str(row["definition_sha256"]), "definition": _definition(row)}
        if kind == "composite_portfolio":
            terms["report_style"] = row.get("report_style")
        return terms
    terms = {"broker": str(row["broker"]), "broker_account_id": str(row["broker_account_id"]), **common}
    # Asked for since migration 085; a case started before keeps the terms (and digest) it had.
    benchmarks = row.get("benchmarks_json")
    if benchmarks:
        benchmarks = json.loads(benchmarks) if isinstance(benchmarks, str) else dict(benchmarks)
        terms["benchmarks"] = {"primary": str(benchmarks["primary"]),
                               "secondary": [str(item) for item in benchmarks.get("secondary") or []]}
    if row.get("kiwoom_include_us") is not None:
        terms["kiwoom_us_account"] = bool(row["kiwoom_include_us"])
    return terms


def _definition_summary(definition: Mapping[str, Any]) -> list[dict[str, str]]:
    """The definition in plain rows for the approval form."""

    def listed(values: Iterable[object]) -> str:
        return ", ".join(str(value) for value in values) or "None"

    if definition.get("subject_type") == "composite_portfolio":
        rows = [("Constant cash", f"{float(definition.get('constant_cash') or 0):g}")]
        for item in definition.get("components", []):
            coefficient = float(item["coefficient"])
            rows.append((f"{'Long' if coefficient > 0 else 'Short'} sleeve",
                         f"{abs(coefficient):g} × {item['component_name']} ({item['source_currency']})"))
        exposure = dict(definition.get("initial_exposure") or {})
        rows += [("Normalised to 100 on", str(definition.get("normalization_date"))),
                 ("Starting exposure", f"long {float(exposure.get('long_risky') or 0):g}, "
                                       f"short {float(exposure.get('short_risky') or 0):g}, "
                                       f"NAV {float(exposure.get('nav') or 0):g}")]
    else:
        position_filter = dict(definition.get("position_filter") or {})
        cash_policy = dict(definition.get("cash_policy") or {})
        opening = dict(cash_policy.get("opening_cash") or {})
        rows = [("Source account", str(definition.get("source_account_id"))),
                ("Excluded countries", listed(position_filter.get("excluded_countries") or [])),
                ("Excluded exchanges", listed(position_filter.get("excluded_exchanges") or [])),
                ("Excluded currencies", listed(position_filter.get("excluded_currencies") or [])),
                ("Cash currencies", listed(cash_policy.get("allowed_currencies") or [])),
                ("Opening cash", listed(f"{key} {float(value):g}" for key, value in sorted(opening.items())))]
    return [{"label": label, "value": value} for label, value in rows]


def _subject_taken(connection, tenant_id: str, subject_id: str) -> bool:
    """A live or onboarding subject, or an earlier wizard case, already uses this ID."""

    if connection.execute(
        "SELECT 1 FROM platform_subjects WHERE tenant_id = %s AND subject_id = %s",
        (tenant_id, subject_id),
    ).fetchone() is not None:
        return True
    return connection.execute(
        "SELECT 1 FROM account_onboarding_cases WHERE tenant_id = %s AND target_subject_id = %s "
        "AND status <> 'cancelled'",
        (tenant_id, subject_id),
    ).fetchone() is not None


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
               reporting_currency: str, history_start_date: date, actor: str | None, today: date,
               benchmarks: Mapping[str, Any] | None = None, include_us: bool | None = None) -> dict:
    """Open a case for one discovered account. Up to MAX_OPEN_CASES at a time.

    ``benchmarks`` ({"primary": id, "secondary": [ids]}) are the account's benchmarks, written to
    portfolio_benchmark_mapping at approval 1; ``include_us`` says whether a Kiwoom account's U.S.
    holdings are read too (None leaves it to the collector's setting).
    """

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
    subject_id = subject_id_for(broker, account_id)
    _assert_can_start(connection, tenant_id, broker=broker, broker_account_id=account_id,
                      target_subject_id=subject_id)
    if _already_onboarded(connection, tenant_id, broker, account_id):
        raise OnboardingError("This account is already on the platform")
    picked = _benchmark_terms(connection, tenant_id, benchmarks) if benchmarks else None
    if include_us is not None and broker != "kiwoom_rest":
        raise OnboardingError("Only a Kiwoom account has a U.S. account to read", 400)

    case_id = str(uuid.uuid4())
    # The columns of migration 085 only when the form used them, so an older database still takes a case.
    extra = {"benchmarks_json": _json(picked) if picked else None, "kiwoom_include_us": include_us}
    extra = {key: value for key, value in extra.items() if value is not None}
    connection.execute(
        "INSERT INTO broker_onboarding_cases (case_id, tenant_id, source_kind, connection_id, broker, "
        "broker_account_id, target_subject_id, display_name, reporting_currency, history_start_date, stage, "
        "status, created_by_identity_sha256" + "".join(f", {key}" for key in extra) + ") "
        "VALUES (%s, %s, 'broker_account', %s, %s, %s, %s, %s, %s, %s, 'request', 'platform_working', %s"
        + "".join(", %s::jsonb" if key == "benchmarks_json" else ", %s" for key in extra) + ")",
        (case_id, tenant_id, connection_id, broker, account_id, subject_id, name, currency, history_start_date,
         actor, *extra.values()),
    )
    _event(connection, tenant_id, case_id, "created", "request", "request", actor,
           {"broker": broker, "connection_id": str(connection_id)})
    connection.execute(
        "UPDATE broker_connection_accounts SET discovery_status = 'case_started' "
        "WHERE tenant_id = %s AND connection_id = %s AND broker_account_id = %s",
        (tenant_id, connection_id, account_id),
    )
    case = OnboardingCase(case_id=case_id, broker=broker, broker_account_id=account_id,
                          target_subject_id=subject_id)
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


def _assert_can_start(connection, tenant_id: str, **candidate: str | None) -> None:
    # Two cases started at the same moment must not both see room for one more.
    connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"broker-onboarding-start:{tenant_id}",))
    open_rows = connection.execute(
        "SELECT case_id, source_kind, broker, broker_account_id, target_subject_id, stage, status "
        "FROM broker_onboarding_cases WHERE tenant_id = %s AND status <> 'cancelled' FOR UPDATE",
        (tenant_id,),
    ).fetchall()
    try:
        assert_can_start(open_cases=[_case_from_rows(row, (), ()) for row in open_rows], **candidate)
    except ValueError as error:
        raise OnboardingError(str(error)) from error


def benchmark_choices(connection, tenant_id: str) -> list[dict[str, str]]:
    """The active benchmarks an account can be compared with, for the start form."""

    return [{"benchmark_id": str(row["benchmark_id"]), "name": str(row["name"]), "currency": str(row["currency"])}
            for row in connection.execute(
                "SELECT benchmark_id, name, currency FROM benchmark_master WHERE tenant_id = %s AND active "
                "ORDER BY name, benchmark_id", (tenant_id,)).fetchall()]


def _benchmark_terms(connection, tenant_id: str, benchmarks: Mapping[str, Any]) -> dict[str, Any]:
    primary = str(benchmarks.get("primary") or "").strip()
    secondary = list(dict.fromkeys(str(item).strip() for item in benchmarks.get("secondary") or [] if str(item).strip()))
    if not primary:
        raise OnboardingError("Choose the main benchmark", 400)
    if primary in secondary:
        raise OnboardingError("The main benchmark cannot also be a second benchmark", 400)
    if len(secondary) > 4:
        raise OnboardingError("Choose at most four other benchmarks", 400)
    known = {item["benchmark_id"] for item in benchmark_choices(connection, tenant_id)}
    unknown = [item for item in [primary, *secondary] if item not in known]
    if unknown:
        raise OnboardingError("Not an active benchmark: " + ", ".join(unknown), 400)
    return {"primary": primary, "secondary": secondary}


def set_case_terms(connection, tenant_id: str, case_id: str, *, benchmarks: Mapping[str, Any] | None,
                   include_us: bool | None, actor: str | None) -> dict:
    """Change the benchmarks or the Kiwoom U.S. choice before the account terms are approved."""

    row, _checks, approvals, case = _load_case(connection, tenant_id, case_id, lock=True)
    if case.source_kind != "broker_account":
        raise OnboardingError("A portfolio's benchmarks come from its definition")
    if case.cancelled or "account_terms" in case.approvals:
        raise OnboardingError("The account terms are already approved; these can no longer change")
    if include_us is not None and case.broker != "kiwoom_rest":
        raise OnboardingError("Only a Kiwoom account has a U.S. account to read", 400)
    picked = _benchmark_terms(connection, tenant_id, benchmarks) if benchmarks else None
    connection.execute(
        "UPDATE broker_onboarding_cases SET benchmarks_json = %s::jsonb, kiwoom_include_us = %s, updated_at = now() "
        "WHERE tenant_id = %s AND case_id = %s",
        (_json(picked) if picked else None, include_us, tenant_id, case_id),
    )
    return {"case_id": case_id, "benchmarks": picked, "kiwoom_us_account": include_us}


def _map_benchmarks(connection, tenant_id: str, terms: Mapping[str, Any]) -> None:
    """Approval 1: the account's benchmarks, from its history start, as the earlier wizard writes them."""

    picked = terms.get("benchmarks")
    if not picked:
        return
    for mapping_type, benchmark_id in [("primary", picked["primary"]),
                                       *[("secondary", item) for item in picked["secondary"]]]:
        connection.execute(
            "INSERT INTO portfolio_benchmark_mapping (tenant_id, subject_id, benchmark_id, mapping_type, "
            "effective_from, effective_to, reporting_currency, created_at) VALUES (%s, %s, %s, %s, %s, NULL, %s, %s) "
            "ON CONFLICT (tenant_id, subject_id, benchmark_id, effective_from) DO UPDATE SET "
            "mapping_type = EXCLUDED.mapping_type, effective_to = NULL, reporting_currency = EXCLUDED.reporting_currency",
            (tenant_id, terms["subject_id"], benchmark_id, mapping_type, str(terms["history_start_date"]),
             terms["reporting_currency"], datetime.now(timezone.utc).isoformat()),
        )


def _sleeve_states(connection, tenant_id: str, definition: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """The live subjects a derived portfolio is built from, and any that are not live.

    Benchmark sleeves are market prices, not platform subjects; the Studio form
    already limits them to the approved catalogue with history on the start date.
    """

    try:
        dependencies = dependencies_for_definition(definition)
    except ValueError as error:
        raise OnboardingError(str(error), 400) from error
    required = sorted({item.depends_on_subject_id for item in dependencies if item.dependency_type != "benchmark"})
    rows = connection.execute(
        "SELECT subject_id, lifecycle_status FROM platform_subjects WHERE tenant_id = %s AND subject_id = ANY(%s) "
        "FOR SHARE",
        (tenant_id, required),
    ).fetchall()
    active = {str(row["subject_id"]) for row in rows if str(row["lifecycle_status"]) == "active"}
    return required, [item for item in required if item not in active]


def _sleeves_starting_late(connection, tenant_id: str, definition: Mapping[str, Any]) -> list[str]:
    """Sleeves whose own history begins after the portfolio's start date."""

    starts = str(definition.get("history_start_date") or definition.get("normalization_date") or "")
    required = sorted({item.depends_on_subject_id for item in dependencies_for_definition(definition)
                       if item.dependency_type != "benchmark"})
    rows = connection.execute(
        "SELECT subject_id, history_start_date FROM platform_subjects WHERE tenant_id = %s AND subject_id = ANY(%s)",
        (tenant_id, required),
    ).fetchall()
    return sorted(str(row["subject_id"]) for row in rows
                  if row["history_start_date"] is not None and str(row["history_start_date"]) > starts)


def _portfolio_id(definition: Mapping[str, Any]) -> str:
    subject_id = str(definition.get("subject_id") or "")
    if not _PORTFOLIO_ID.fullmatch(subject_id):
        raise OnboardingError("Use 3 to 64 letters, digits and hyphens for the portfolio ID, starting with a letter "
                              "or digit", 400)
    return subject_id


def _clean_description(description: str | None) -> str:
    text = " ".join(str(description or "").split())
    if not 10 <= len(text) <= 600:
        raise OnboardingError("Describe the portfolio in 10 to 600 characters; its reports show this text", 400)
    return text


def _report_style(definition: Mapping[str, Any], style: str | None) -> str | None:
    try:
        return check_style(definition, style)
    except ReportProfileError as error:
        raise OnboardingError(str(error), 400) from error


def _as_stored(value: Any) -> Any:
    """A definition exactly as PostgreSQL's jsonb returns it, so its fingerprint survives storage.

    jsonb has no negative zero: a composite with no short sleeve has a starting
    short exposure of -0.0, which comes back as 0 and would change the digest the
    daily calculation checks.
    """

    if isinstance(value, float) and value == 0.0:
        return 0.0
    if isinstance(value, Mapping):
        return {key: _as_stored(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_as_stored(item) for item in value]
    return value


def start_derived_case(connection, tenant_id: str, *, definition: Mapping[str, Any], description: str,
                       actor: str | None, today: date, report_style: str | None = None) -> dict:
    """Open a case for a filtered or composite portfolio the builder already validated."""

    definition = _as_stored(definition)
    kind = str(definition.get("subject_type") or "")
    if kind not in DERIVED_SOURCE_KINDS:
        raise OnboardingError("Only filtered portfolios and fixed-unit composites are supported", 400)
    text = _clean_description(description)
    subject_id = _portfolio_id(definition)
    style = _report_style(definition, report_style)
    starts = date.fromisoformat(str(definition.get("history_start_date") or definition["normalization_date"]))
    if starts > today:
        raise OnboardingError("History cannot start in the future", 400)
    _assert_can_start(connection, tenant_id, target_subject_id=subject_id)
    if _subject_taken(connection, tenant_id, subject_id):
        raise OnboardingError("This portfolio ID is already used on the platform")
    sleeves, not_live = _sleeve_states(connection, tenant_id, definition)
    if not_live:
        raise OnboardingError("Every sleeve must be live before this portfolio can start: " + ", ".join(not_live))
    late = _sleeves_starting_late(connection, tenant_id, definition)
    if late:
        raise OnboardingError("These sleeves have no history on the start date; start later: " + ", ".join(late))

    digest = configuration_sha256(definition)
    case_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO broker_onboarding_cases (case_id, tenant_id, source_kind, definition_json, definition_sha256, "
        "target_subject_id, display_name, description, reporting_currency, history_start_date, report_style, "
        "stage, status, created_by_identity_sha256) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, 'request', 'platform_working', %s)",
        (case_id, tenant_id, kind, _json(definition), digest, subject_id, str(definition["display_name"]), text,
         str(definition["base_currency"]), starts, style, actor),
    )
    _event(connection, tenant_id, case_id, "created", "request", "request", actor,
           {"source_kind": kind, "definition_sha256": digest, "report_style": style})
    case = OnboardingCase(case_id=case_id, broker=None, broker_account_id=None, source_kind=kind,
                          target_subject_id=subject_id)
    case = _write_check(connection, tenant_id, case, "sleeves_are_live", "pass",
                        ("Live: " + ", ".join(sleeves) + ".") if sleeves else "Built only from market indices.",
                        {"sleeves": sleeves}, actor)
    case = _write_check(connection, tenant_id, case, "subject_id_is_free", "pass",
                        "No live subject or earlier case uses this ID.", {}, actor)
    case = _save_progress(connection, tenant_id, case, actor)
    return {"case_id": case_id, "subject_id": subject_id, "definition_sha256": digest, "stage": case.stage,
            "status": case_status(case), "next_page": f"/account-onboarding/cases/{case_id}"}


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
    return replace(case, acknowledged_warnings=case.acknowledged_warnings - {check_id})


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


def record_first_month_end(connection, tenant_id: str, subject_id: str, period_end: str) -> dict | None:
    """The owner released a subject's month-end factsheet: a live case waiting on its first month-end closes.

    Called by the website's month-end release, in its transaction. Does nothing for a subject with no case in
    the First month-end stage, so later months and older onboardings are unaffected.
    """

    row = connection.execute(
        "SELECT case_id FROM broker_onboarding_cases WHERE tenant_id = ? AND target_subject_id = ? "
        "AND stage = 'first_month_end' AND status <> 'cancelled'",
        (tenant_id, subject_id),
    ).fetchone()
    if row is None:
        return None
    return record_platform_check(connection, tenant_id, str(row["case_id"]), "first_month_end_released", "pass",
                                 f"The {period_end} month-end factsheet was released.", {"period_end": period_end})


def _evidence(check: Mapping[str, Any]) -> dict:
    value = check.get("evidence_json") or {}
    return json.loads(value) if isinstance(value, str) else dict(value)


def _approval_terms(row: Mapping[str, Any], checks: Iterable[Mapping[str, Any]],
                    approvals: Iterable[Mapping[str, Any]], approval: str) -> dict[str, object]:
    """Exactly what the owner reviews for one approval; its digest is stored with it."""

    if approval == "account_terms":
        return _account_terms(row)
    common = {"subject_id": str(row["target_subject_id"]), "display_name": str(row["display_name"])}
    if approval == "historical_reports":
        by_id = {str(item["check_id"]): item for item in checks}
        return {**common, "checks": {
            check_id: {"status": str(by_id[check_id]["status"]), "message": str(by_id[check_id]["message"]),
                       "evidence": _evidence(by_id[check_id])}
            for check_id in BUILD_HISTORY_EVIDENCE if check_id in by_id
        }}
    return {**common, "reporting_currency": str(row["reporting_currency"]),
            "approved": {str(item["approval_kind"]): str(item["terms_sha256"]) for item in approvals}}


def _reports(checks: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The rendered historical reports listed for approval 2, newest month first."""

    rendered = next((item for item in checks if str(item["check_id"]) == "reports_rendered"), None)
    reports = _evidence(rendered).get("reports") if rendered else None
    listed = []
    for item in reports if isinstance(reports, list) else []:
        if not isinstance(item, Mapping):
            continue
        path = str(item.get("download_path") or "")
        listed.append({
            "period_end": str(item.get("period_end") or ""),
            "report_type": str(item.get("report_type") or ""),
            "file_name": str(item.get("file_name") or ""),
            # Only links within this website are shown.
            "download_path": path if _LOCAL_PATH.fullmatch(path) else "",
        })
    return sorted(listed, key=lambda item: (item["period_end"], item["report_type"]), reverse=True)


def case_report(connection, tenant_id: str, case_id: str, generation_run_id: int, *, container: str) -> dict:
    """One file the case's report run listed, located in private storage for the owner to open.

    Only a file named in the case's reports_rendered evidence, generated from
    this subject's frozen snapshot and still unchanged, is ever served.
    """

    row = connection.execute(
        "SELECT c.target_subject_id, k.evidence_json FROM broker_onboarding_cases c "
        "JOIN broker_onboarding_checks k ON k.tenant_id = c.tenant_id AND k.case_id = c.case_id "
        "AND k.check_id = 'reports_rendered' WHERE c.tenant_id = %s AND c.case_id = %s",
        (tenant_id, case_id),
    ).fetchone()
    reports = _evidence(row).get("reports") if row else None
    item = next((entry for entry in reports if isinstance(entry, Mapping)
                 and entry.get("generation_run_id") == generation_run_id), None) if isinstance(reports, list) else None
    if item is None:
        raise OnboardingError("This report is not listed on the case", 404)
    subject_id = str(row["target_subject_id"])
    run = connection.execute(
        "SELECT r.generation_run_id, r.report_type, r.output_reference, r.output_sha256, s.period_end, s.subject_id "
        "FROM report_generation_runs r JOIN reporting_snapshots s "
        "ON s.tenant_id = r.tenant_id AND s.snapshot_id = r.snapshot_id "
        "WHERE r.tenant_id = %s AND r.generation_run_id = %s AND s.subject_id = %s "
        "AND r.status IN ('awaiting_approval', 'released_internal') AND s.status IN ('frozen', 'approved')",
        (tenant_id, generation_run_id, subject_id),
    ).fetchone()
    if run is None:
        raise OnboardingError("This report is not listed on the case", 404)
    try:
        period_end = validate_period_end(str(run["period_end"])[:10])
        artifact = validate_review_artifact(dict(run), account_id=subject_id, period_end=period_end,
                                            container=container)
    except MonthlyReportError as error:
        raise OnboardingError(str(error), 503) from error
    if artifact["sha256"] != str(item.get("sha256") or ""):
        raise OnboardingError("The report changed after it was listed; the next report run lists it again")
    label = "Factsheet" if artifact["report_type"] == "factsheet" else "Presentation"
    extension = REPORT_TYPES[artifact["report_type"]]["extension"]
    name = download_prefix(connection, tenant_id, subject_id) or subject_id
    return {**artifact, "file_name": f"{name}_{label}_{period_end}{extension}"}


def _go_live(connection, tenant_id: str, row: Mapping[str, Any], case: OnboardingCase, actor: str) -> None:
    """Approval 3: publish the subject and start its daily jobs, as the wizard's activation does."""

    subject_id = str(row["subject_id"] or "")
    subject = connection.execute(
        "SELECT subject_type, history_start_date FROM platform_subjects "
        "WHERE tenant_id = %s AND subject_id = %s AND lifecycle_status = 'onboarding' FOR UPDATE",
        (tenant_id, subject_id),
    ).fetchone() if subject_id else None
    if subject is None:
        raise OnboardingError("The account is no longer waiting to go live; reload the case")
    connection.execute(
        "UPDATE platform_subjects SET lifecycle_status = 'active', updated_at = now() "
        "WHERE tenant_id = %s AND subject_id = %s AND lifecycle_status = 'onboarding'",
        (tenant_id, subject_id),
    )
    activate_subject(
        connection, tenant_id=tenant_id, subject_id=subject_id, subject_type=str(subject["subject_type"]),
        source_name=str(case.broker or "derived_calculation"),
        history_start_date=str(subject["history_start_date"]), administrative_master=False,
        approval_id=None, actor=actor,
    )
    if case.source_kind == "broker_account":
        connection.execute(
            "UPDATE broker_connection_accounts SET discovery_status = 'live' "
            "WHERE tenant_id = %s AND connection_id = %s AND broker_account_id = %s",
            (tenant_id, row["connection_id"], row["broker_account_id"]),
        )


def approve_case(connection, tenant_id: str, case_id: str, *, approval: str, terms_sha256: str,
                 acknowledge_warnings: Iterable[str], confirmation: str | None, actor: str,
                 viewers: Iterable[str] = ()) -> dict:
    """Record one of the owner's three approvals. At go-live, ``viewers`` (Entra object IDs of active
    viewers) are given read access to the account in the same transaction."""

    if approval not in APPROVALS:
        raise OnboardingError("Unknown approval", 404)
    viewers = sorted({str(item).strip().lower() for item in viewers if str(item).strip()})
    if viewers and approval != "go_live":
        raise OnboardingError("Viewers are given access when the account goes live", 400)
    row, checks, approvals, case = _load_case(connection, tenant_id, case_id, lock=True)
    terms = _approval_terms(row, checks, approvals, approval)
    if terms_digest(terms) != terms_sha256:
        raise OnboardingError("What you reviewed changed after this page loaded; reload and review it again")
    acknowledged = [item for item in acknowledge_warnings if item in checks_for(case.source_kind, case.stage)]
    try:
        case, event = approve(case, approval, acknowledge_warnings=acknowledged, confirmation=confirmation,
                              broker_account_display=_display(case))
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
    detail: dict[str, object] = {"approval": approval, "terms_sha256": terms_sha256}
    if approval == "account_terms" and case.source_kind in DERIVED_SOURCE_KINDS:
        _register_derived_subject(connection, tenant_id, row, terms, actor)
    elif approval == "account_terms":
        _register_subject(connection, tenant_id, row, terms)
        _map_benchmarks(connection, tenant_id, terms)
    elif approval == "go_live":
        _go_live(connection, tenant_id, row, case, actor)
        detail["viewers_given_access"] = _grant_viewers(connection, tenant_id, str(row["subject_id"]), viewers,
                                                        actor)
    connection.execute(
        "INSERT INTO broker_onboarding_approvals (tenant_id, case_id, approval_kind, terms_json, terms_sha256, "
        "approved_by_identity_sha256) VALUES (%s, %s, %s, %s::jsonb, %s, %s)",
        (tenant_id, case_id, approval, _json(terms), terms_sha256, actor),
    )
    _event(connection, tenant_id, case_id, "approved", event.from_stage, event.to_stage, actor, detail)
    case = _save_progress(connection, tenant_id, case, actor)
    result = {"case_id": case_id, "stage": case.stage, "status": case_status(case)}
    if approval == "go_live":
        result["next_page"] = f"/accounts/{row['subject_id']}/overview"
        result["viewers_given_access"] = detail.get("viewers_given_access", 0)
    return result


def viewer_choices(connection, tenant_id: str, subject_id: str | None = None) -> list[dict[str, Any]]:
    """Active viewers (an active, effective viewer role), and whether each already reads the account."""

    rows = connection.execute(
        "SELECT u.entra_object_id, u.email, u.display_name, "
        "EXISTS (SELECT 1 FROM user_account_access a WHERE a.tenant_id = u.tenant_id "
        "AND a.entra_object_id = u.entra_object_id AND a.subject_id = %s) AS has_access "
        "FROM app_users u WHERE u.tenant_id = %s AND u.status = 'active' AND EXISTS ("
        "SELECT 1 FROM app_user_roles r WHERE r.tenant_id = u.tenant_id AND lower(r.entra_object_id) = "
        "lower(u.entra_object_id) AND r.role_name = 'viewer' AND r.role_status = 'active' "
        "AND r.effective_from <= now() AND (r.effective_to IS NULL OR r.effective_to >= now())) "
        "ORDER BY lower(coalesce(u.display_name, u.email))",
        (subject_id or "", tenant_id),
    ).fetchall()
    return [{"object_id": str(row["entra_object_id"]).lower(), "name": str(row["display_name"] or row["email"]),
             "email": str(row["email"]), "has_access": bool(row["has_access"])} for row in rows]


def _grant_viewers(connection, tenant_id: str, subject_id: str, viewers: list[str], actor: str) -> int:
    """Read access for the ticked viewers; never a higher level, and an existing grant is left as it is."""

    if not viewers:
        return 0
    allowed = connection.execute(
        "SELECT has_table_privilege(current_user, 'public.user_account_access', 'INSERT') AS allowed").fetchone()
    if not allowed or not allowed["allowed"]:
        raise OnboardingError("The website cannot give viewers access yet; go live without ticking viewers, "
                              "or ask for the website's login to be allowed to add read access", 503)
    known = {item["object_id"]: item for item in viewer_choices(connection, tenant_id, subject_id)}
    unknown = [item for item in viewers if item not in known]
    if unknown:
        raise OnboardingError("Only active viewers can be given access; reload the page", 400)
    stamp = datetime.now(timezone.utc).isoformat()
    added = 0
    for object_id in viewers:
        if known[object_id]["has_access"]:
            continue
        connection.execute(
            "INSERT INTO user_account_access (tenant_id, entra_object_id, subject_id, access_level, granted_at, "
            "granted_by) SELECT %s, u.entra_object_id, %s, 'read', %s, %s FROM app_users u "
            "WHERE u.tenant_id = %s AND lower(u.entra_object_id) = %s "
            "ON CONFLICT (tenant_id, entra_object_id, subject_id) DO NOTHING",
            (tenant_id, subject_id, stamp, actor, tenant_id, object_id),
        )
        added += 1
    connection.execute(
        "INSERT INTO audit_events (tenant_id, occurred_at, actor_type, actor_id, event_type, subject_id, outcome, "
        "details_json) VALUES (%s, %s, 'user', %s, 'viewer_read_access_granted', %s, 'succeeded', %s)",
        (tenant_id, stamp, actor, subject_id, _json({"grants": added, "channel": "broker_onboarding_v2_go_live",
                                                      "access_level": "read"})),
    )
    return added


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
    _private_feature_states(connection, tenant_id, subject_id)
    connection.execute(
        "UPDATE broker_onboarding_cases SET subject_id = %s WHERE tenant_id = %s AND case_id = %s",
        (subject_id, tenant_id, row["case_id"]),
    )


def _private_feature_states(connection, tenant_id: str, subject_id: str) -> None:
    """Only the owner's private website preview is on until go-live."""

    for feature_key in FEATURE_KEYS:
        connection.execute(
            "INSERT INTO subject_feature_states (tenant_id, subject_id, feature_key, feature_state, reason, "
            "updated_at) VALUES (%s, %s, %s, %s, %s, now())",
            (tenant_id, subject_id, feature_key, "preview" if feature_key == "data_ingestion" else "disabled",
             "Private until the owner approves go-live."),
        )


def _register_derived_subject(connection, tenant_id: str, row: Mapping[str, Any], terms: Mapping[str, object],
                              actor: str) -> None:
    """Approval 1 registers the portfolio privately with the exact definition the owner approved."""

    definition = _definition(row)
    digest = configuration_sha256(definition)
    if digest != row["definition_sha256"]:
        raise OnboardingError("The stored definition no longer matches what was reviewed; cancel and start again")
    _, not_live = _sleeve_states(connection, tenant_id, definition)
    if not_live:
        raise OnboardingError("A sleeve stopped being live: " + ", ".join(not_live))
    subject_id = str(terms["subject_id"])
    metadata = {"onboarding": "broker_onboarding_v2", "case_id": str(row["case_id"]),
                "source_system": "derived_definition", "definition_origin": "website_portfolio_builder",
                "definition_sha256": digest, "description": terms["description"]}
    if terms.get("report_style"):
        # The reports' name, description and tree, exactly as approved.
        try:
            metadata["report_profile"] = build_profile(definition=definition, style=str(terms["report_style"]),
                                                       description=str(terms["description"]))
        except ReportProfileError as error:
            raise OnboardingError(str(error)) from error
    connection.execute(
        "INSERT INTO platform_subjects (tenant_id, subject_id, subject_type, display_name, source_account_id, "
        "base_currency, reporting_currency, history_start_date, inception_date, lifecycle_status, metadata_json) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'onboarding', %s::jsonb)",
        (tenant_id, subject_id, definition["subject_type"], terms["display_name"],
         definition.get("source_account_id"), definition["base_currency"], terms["reporting_currency"],
         terms["history_start_date"], terms["history_start_date"], _json(metadata)),
    )
    connection.execute(
        "INSERT INTO subject_calculation_definitions (definition_id, tenant_id, subject_id, subject_type, "
        "calculation_kind, definition_version, configuration_json, configuration_sha256, approval_status, "
        "effective_from, created_by_identity_sha256, approved_by_identity_sha256, approved_at) "
        "VALUES (%s, %s, %s, %s, %s, 1, %s::jsonb, %s, 'approved', %s, %s, %s, now())",
        (uuid.uuid4(), tenant_id, subject_id, definition["subject_type"], definition["calculation_kind"],
         _json(definition), digest, terms["history_start_date"], row.get("created_by_identity_sha256"), actor),
    )
    for dependency in dependencies_for_definition(definition):
        connection.execute(
            "INSERT INTO subject_dependencies (tenant_id, subject_id, depends_on_subject_id, dependency_type, "
            "effective_from, configuration_json) VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
            (tenant_id, subject_id, dependency.depends_on_subject_id, dependency.dependency_type,
             dependency.effective_from, _json(dict(dependency.configuration))),
        )
    _private_feature_states(connection, tenant_id, subject_id)
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
    if case.source_kind == "broker_account":
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
        # No calculation may run from a definition whose case was stopped.
        connection.execute(
            "UPDATE subject_calculation_definitions SET approval_status = 'rejected' "
            "WHERE tenant_id = %s AND subject_id = %s AND approval_status IN ('validated', 'approved')",
            (tenant_id, row["subject_id"]),
        )
    _event(connection, tenant_id, case_id, "cancelled", event.from_stage, event.to_stage, actor, event.detail)
    return {"case_id": case_id, "status": "cancelled"}


def case_detail(connection, tenant_id: str, case_id: str) -> dict:
    row, checks, approvals, case = _load_case(connection, tenant_id, case_id)
    by_id = {str(item["check_id"]): item for item in checks}
    account_display = _display(case)
    terms = _account_terms(row)
    awaiting = missing_approvals(case)[0] if case_status(case) == "waiting_on_owner" else None
    awaiting_terms = _approval_terms(row, checks, approvals, awaiting) if awaiting else None
    derived = case.source_kind in DERIVED_SOURCE_KINDS
    stage_number = min(list(STAGE_LABELS).index(case.stage) + 1, 4)
    style_label = (f"{REPORT_STYLES[row['report_style']]['label']} design"
                   if row.get("report_style") in REPORT_STYLES else None)
    summary = _definition_summary(terms["definition"]) if derived else []
    if style_label:
        summary.append({"label": "Report design", "value": style_label})
    detail = {
        "case_id": case.case_id,
        "source_kind": case.source_kind,
        "broker": case.broker,
        "broker_label": SOURCE_LABELS[case.source_kind] if derived else BROKERS[str(case.broker)].label,
        "account": account_display,
        "definition_summary": summary,
        "description": row.get("description"),
        "report_style_label": style_label,
        "composition_svg": _composition_svg(connection, tenant_id, terms["definition"]) if derived else None,
        "display_name": row["display_name"],
        "reporting_currency": row["reporting_currency"],
        "history_start_date": str(row["history_start_date"]),
        "subject_id": row["subject_id"],
        "stage": case.stage,
        "stage_label": STAGE_LABELS[case.stage],
        "stage_number": stage_number,
        "status": case_status(case),
        "next_owner_action": next_owner_action(case),
        "awaiting_approval": awaiting,
        "awaiting_terms_sha256": terms_digest(awaiting_terms) if awaiting_terms is not None else None,
        "reports": _reports(checks) if case.stage == "build_history" else [],
        "preview_page": f"/accounts/{row['subject_id']}/overview" if row["subject_id"] else None,
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
            {"kind": kind, "label": label, "approved": kind in case.approvals}
            for kind, label in APPROVAL_LABELS.items()
        ],
        "account_terms": terms,
        "account_terms_sha256": terms_digest(terms),
        "go_live_phrase": go_live_phrase(account_display),
        "can_cancel": case.stage in PRE_LIVE_STAGES and not case.cancelled,
        # The onboarding agents' part (None while ONBOARDING_AGENTS_ENABLED is off); see onboarding_agents.py.
        "agents": onboarding_agents.case_agent_view(connection, tenant_id, case.case_id, checks),
        "documents_open": onboarding_agents.agents_enabled() and onboarding_agents.documents_open(row, approvals),
        "readiness": onboarding_agents.case_readiness(connection, tenant_id, case.case_id, awaiting),
    }
    detail["history"] = _history_coverage(by_id.get("history_built"))
    detail["benchmarks"] = _benchmark_view(connection, tenant_id, terms.get("benchmarks"))
    detail["kiwoom_us_account"] = terms.get("kiwoom_us_account")
    detail["terms_open"] = case.source_kind == "broker_account" and not case.cancelled \
        and "account_terms" not in case.approvals
    detail["benchmark_choices"] = benchmark_choices(connection, tenant_id) if detail["terms_open"] else []
    detail["viewers"] = (viewer_choices(connection, tenant_id, str(row["subject_id"]))
                         if awaiting == "go_live" and row["subject_id"] else [])
    # Last, as it reads another feature's table: a failed read leaves only this part unavailable.
    design = _factsheet_design(connection, tenant_id, row, case)
    detail["factsheet_design"] = design
    detail["external_data"] = external = _external_data(connection, tenant_id, row, case)
    if external and external["open"] and not external["approved"] and detail["next_owner_action"] is None:
        detail["next_owner_action"] = "Approve sending this account's ticker symbols to Yahoo, below."
    if design and design.get("owner_action") and detail["next_owner_action"] is None:
        detail["next_owner_action"] = design["owner_action"]
    # The worker's run records come with migration 085; before it the panel says so and nothing else changes.
    detail["platform_work"] = _platform_work(connection, tenant_id, row, checks, case, external)
    return detail


def _history_coverage(check: Mapping[str, Any] | None) -> dict | None:
    """The history_built check in figures for the case page: days loaded, left and not reconciling."""

    if check is None:
        return None
    evidence = _evidence(check)
    return {
        "status": str(check["status"]),
        "message": str(check["message"]),
        "from": evidence.get("from"),
        "to": evidence.get("to"),
        "days_with_history": evidence.get("days_with_history"),
        "days_left": evidence.get("days_left"),
        "gap_days": evidence.get("gap_days"),
        "gaps": list(evidence.get("gaps") or [])[:10],
        "checked_at": str(check.get("checked_at") or ""),
    }


def _benchmark_view(connection, tenant_id: str, picked: Mapping[str, Any] | None) -> dict | None:
    if not picked:
        return None
    names = {item["benchmark_id"]: item["name"] for item in benchmark_choices(connection, tenant_id)}
    return {"primary": {"benchmark_id": picked["primary"], "name": names.get(picked["primary"], picked["primary"])},
            "secondary": [{"benchmark_id": item, "name": names.get(item, item)} for item in picked["secondary"]]}


def _worker_case(row: Mapping[str, Any], checks: Iterable[Mapping[str, Any]], case: OnboardingCase,
                 external: Mapping[str, Any] | None, runs: list[Mapping[str, Any]], *,
                 requested: bool = False) -> onboarding_worker.Case:
    return onboarding_worker.Case(
        case_id=case.case_id, source_kind=case.source_kind, broker=case.broker, stage=case.stage,
        subject_id=str(row["subject_id"]) if row.get("subject_id") else None,
        history_start_date=str(row["history_start_date"])[:10],
        checks={str(item["check_id"]): str(item["status"]) for item in checks},
        yahoo_approved=bool(external and external.get("approved")),
        last_steps=onboarding_worker.last_steps_from(runs, case.case_id), requested=requested)


def _run_view(run: Mapping[str, Any]) -> dict[str, Any]:
    steps = run.get("steps_json") or []
    steps = json.loads(steps) if isinstance(steps, str) else list(steps)
    return {
        "run_id": str(run["run_id"]),
        "trigger": str(run["trigger"]),
        "status": str(run["status"]),
        "message": str(run.get("message") or ""),
        "requested_at": str(run.get("requested_at") or ""),
        "finished_at": str(run["finished_at"]) if run.get("finished_at") else None,
        "steps": [{"label": onboarding_worker.STEP_LABELS.get(str(item.get("step")), str(item.get("step"))),
                   "outcome": str(item.get("outcome") or ""), "detail": str(item.get("detail") or "")[:300]}
                  for item in steps if isinstance(item, Mapping) and item.get("step")],
    }


def _platform_work(connection, tenant_id: str, row: Mapping[str, Any], checks: Iterable[Mapping[str, Any]],
                   case: OnboardingCase, external: Mapping[str, Any] | None, *,
                   now: datetime | None = None) -> dict | None:
    """What the onboarding worker did for this case lately and does next, for the Platform work panel."""

    if case.cancelled or case.stage not in PRE_LIVE_STAGES:
        return None
    now = now or datetime.now(timezone.utc)
    try:
        runs = connection.execute(
            "SELECT run_id, trigger, status, message, steps_json, requested_at, finished_at "
            "FROM onboarding_worker_runs WHERE tenant_id = %s AND (case_id = %s OR (case_id IS NULL "
            "AND steps_json::text LIKE %s)) ORDER BY requested_at DESC LIMIT 8",
            (tenant_id, case.case_id, f"%{case.case_id}%"),
        ).fetchall()
    except Exception:  # migration 085 not applied yet: the rest of the page stays as it was
        return {"unavailable": True}
    runs = [dict(item) for item in runs]
    waiting = next((item for item in runs if item["status"] in ("requested", "running")), None)
    next_steps = onboarding_worker.plan(_worker_case(row, checks, case, external, runs, requested=True), now=now)
    return {
        "unavailable": False,
        "waiting": _run_view(waiting) if waiting else None,
        "runs": [_run_view(item) for item in runs if item is not waiting][:5],
        "next_scheduled": onboarding_worker.next_scheduled(now).isoformat(),
        "window_note": onboarding_worker.window_note(now),
        "run_now_does": [onboarding_worker.STEP_LABELS[step] for step in next_steps],
        "built_in_designs": _built_in_designs(row, case),
    }


def _built_in_designs(row: Mapping[str, Any], case: OnboardingCase) -> list[dict[str, str]]:
    """Designs the platform ships with the owner's exact template, in this case's currency."""

    if case.stage != "build_history" or row.get("report_style") or not row.get("subject_id"):
        return []
    from importlib import resources

    from .factsheet_spec.spec import load_design

    found = []
    try:
        entries = sorted(resources.files("ibkr_etl.factsheet_spec").joinpath("templates").iterdir(),
                         key=lambda item: item.name)
    except (FileNotFoundError, NotADirectoryError):
        return []
    for entry in entries:
        if not entry.name.endswith(".pdf"):
            continue
        design_id = entry.name[:-4]
        try:
            design = load_design(design_id)
        except Exception:  # a template without a design is not offered
            continue
        if str(row["reporting_currency"]) in design.get("currencies", []) and design.get("template_sha256"):
            found.append({"design_id": design_id, "title": str(design.get("title") or design_id)})
    return found


def request_worker_run(connection, tenant_id: str, *, actor: str | None, case_id: str | None = None,
                       connection_id: str | None = None, asks: Iterable[Mapping[str, Any]] = ()) -> dict:
    """Run now (a case) or Check now (a Kiwoom connection): a request the next worker run takes.

    A request already waiting for the same case or connection is joined, not repeated.
    """

    if case_id:
        _, _, _, case = _load_case(connection, tenant_id, case_id, lock=True)
        if case.cancelled or case.stage not in PRE_LIVE_STAGES:
            raise OnboardingError("This case is past go-live; the daily jobs keep it up to date")
        waiting = connection.execute(
            "SELECT run_id, steps_json FROM onboarding_worker_runs WHERE tenant_id = %s AND case_id = %s "
            "AND status IN ('requested', 'running') LIMIT 1", (tenant_id, case_id)).fetchone()
        trigger = "run_now"
    else:
        source = connection.execute(
            "SELECT broker, status FROM broker_connections WHERE tenant_id = %s AND connection_id = %s FOR UPDATE",
            (tenant_id, connection_id)).fetchone()
        if source is None:
            raise OnboardingError("Broker connection not found", 404)
        if source["broker"] != "kiwoom_rest":
            raise OnboardingError("IBKR connections are checked by the Flex check")
        if source["status"] in {"suspended", "retired"}:
            raise OnboardingError("This connection is paused; renew or replace it first")
        waiting = connection.execute(
            "SELECT run_id, steps_json FROM onboarding_worker_runs WHERE tenant_id = %s AND connection_id = %s "
            "AND case_id IS NULL AND status IN ('requested', 'running') LIMIT 1",
            (tenant_id, connection_id)).fetchone()
        trigger = "check_connection"
    asks = [dict(item) for item in asks]
    if waiting is not None:
        if asks:
            known = waiting["steps_json"] or []
            known = json.loads(known) if isinstance(known, str) else list(known)
            connection.execute(
                "UPDATE onboarding_worker_runs SET steps_json = %s::jsonb WHERE tenant_id = %s AND run_id = %s "
                "AND status = 'requested'", (_json([*known, *asks]), tenant_id, waiting["run_id"]))
        return {"run_id": str(waiting["run_id"]), "status": "already_waiting"}
    run_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO onboarding_worker_runs (run_id, tenant_id, case_id, connection_id, trigger, status, "
        "requested_by_identity_sha256, steps_json) VALUES (%s, %s, %s, %s, %s, 'requested', %s, %s::jsonb)",
        (run_id, tenant_id, case_id, connection_id, trigger, actor, _json(asks)),
    )
    return {"run_id": run_id, "status": "requested"}


def note_worker_start(connection, tenant_id: str, run_id: str, reference: str | None, error: str | None) -> None:
    """Keep the job execution's name, or why it could not be started (the schedule still takes the request)."""

    connection.execute(
        "UPDATE onboarding_worker_runs SET execution_reference = %s, message = coalesce(message, %s) "
        "WHERE tenant_id = %s AND run_id = %s",
        ((reference or "")[:400] or None, error, tenant_id, run_id),
    )


def archive_design_draft(connection, tenant_id: str, case_id: str, draft_id: str) -> dict:
    """Hide an unsubmitted factsheet draft from the designer's lists; its versions are kept."""

    row, _, _, case = _load_case(connection, tenant_id, case_id, lock=True)
    if case.cancelled or case.stage not in PRE_LIVE_STAGES or not row.get("subject_id"):
        raise OnboardingError("Drafts are tidied while the case is before go-live")
    changed = connection.execute(
        "UPDATE factsheet_design_drafts SET status = 'archived', updated_at = %s WHERE tenant_id = %s "
        "AND draft_id = %s AND subject_id = %s AND status IN ('review', 'failed') RETURNING draft_id",
        (datetime.now(timezone.utc).isoformat(), tenant_id, draft_id, row["subject_id"]),
    ).fetchone()
    if changed is None:
        raise OnboardingError("Only a draft of this account that is not submitted can be archived", 404)
    return {"draft_id": draft_id, "status": "archived"}


EXTERNAL_DATA_MESSAGE = (
    "Only this account's current and historical ticker symbols are sent to Yahoo Finance via yfinance, to "
    "look up each company's English name, sector and industry for the factsheets and to read crisis-period "
    "prices. Raw broker files, account numbers, keys, database credentials and personal information are "
    "never sent."
)


def _external_data_approval(connection, tenant_id: str, subject_id: str):
    return connection.execute(
        "SELECT approval_id, approved_at FROM account_onboarding_approvals "
        "WHERE tenant_id = %s AND subject_id = %s AND approval_type = 'external_market_data' "
        "AND approval_status = 'approved' AND revoked_at IS NULL AND disclosure_version = %s LIMIT 1",
        (tenant_id, subject_id, yahoo_disclosure.VERSION),
    ).fetchone()


def _external_data(connection, tenant_id: str, row: Mapping[str, Any], case: OnboardingCase) -> dict | None:
    """The owner's Yahoo consent for a broker account, once approval 1 has registered it."""

    if case.source_kind != "broker_account" or not row.get("subject_id"):
        return None
    approval = _external_data_approval(connection, tenant_id, str(row["subject_id"]))
    return {
        "approved": approval is not None,
        "approved_at": str(approval["approved_at"]) if approval else None,
        "message": EXTERNAL_DATA_MESSAGE,
        "disclosure_version": yahoo_disclosure.VERSION,
        "scope_sha256": yahoo_disclosure.SCOPE_SHA256,
        "open": not case.cancelled and case.stage in PRE_LIVE_STAGES,
    }


def approve_external_data(connection, tenant_id: str, case_id: str, *, disclosure_version: str,
                          scope_sha256: str, actor: str) -> dict:
    """Record the owner's consent to send this account's ticker symbols to Yahoo; nothing is sent here.

    Stored where the sector job and the other Yahoo readers look for it. A new-onboarding case has no
    wizard case, so case_id stays empty (as for accounts onboarded before the wizard) and the metadata
    names this case.
    """

    row, _checks, _approvals, case = _load_case(connection, tenant_id, case_id, lock=True)
    if case.source_kind != "broker_account" or not row.get("subject_id"):
        raise OnboardingError("Yahoo consent opens once you approve the account terms")
    if case.cancelled or case.stage not in PRE_LIVE_STAGES:
        raise OnboardingError("Yahoo consent is recorded before go-live; this case is past that")
    if disclosure_version != yahoo_disclosure.VERSION or scope_sha256 != yahoo_disclosure.SCOPE_SHA256:
        raise OnboardingError("What you reviewed changed after this page loaded; reload and review it again")
    subject_id = str(row["subject_id"])
    existing = _external_data_approval(connection, tenant_id, subject_id)
    if existing is not None:
        return {"status": "already_approved", "approval_id": str(existing["approval_id"]), "transmission_started": False}
    approval_id = uuid.uuid4()
    connection.execute(
        "INSERT INTO account_onboarding_approvals (approval_id, tenant_id, case_id, subject_id, approval_type, "
        "approval_status, disclosure_version, destination, scope_json, scope_sha256, approved_by_identity_sha256, "
        "approved_at, metadata_json) VALUES (%s, %s, NULL, %s, 'external_market_data', 'approved', %s, %s, "
        "%s::jsonb, %s, %s, now(), %s::jsonb)",
        (approval_id, tenant_id, subject_id, yahoo_disclosure.VERSION, yahoo_disclosure.DESTINATION,
         _json(yahoo_disclosure.SCOPE), yahoo_disclosure.SCOPE_SHA256, actor,
         _json({"channel": "broker_onboarding_v2", "broker_case_id": case_id})),
    )
    return {"status": "approved", "approval_id": str(approval_id), "transmission_started": False}


def factsheet_design_enabled() -> bool:
    from .factsheet_design_web import enabled
    return enabled()


def _factsheet_design(connection, tenant_id: str, row: Mapping[str, Any], case: OnboardingCase) -> dict | None:
    """The factsheet design step of a case with no built-in design, or None.

    Such a case reports in the design its owner makes in the factsheet designer
    (designed_reports.py): its reports_rendered check waits for a submitted design.
    The designer opens once approval 1 has registered the subject.
    """

    if case.cancelled or row.get("report_style") in REPORT_STYLES or case.stage not in PRE_LIVE_STAGES:
        return None
    if not factsheet_design_enabled():
        return None
    subject_id = row.get("subject_id")
    step = {"open": bool(subject_id) and case.stage == "build_history", "designs": [], "submitted": None,
            "designer_page": f"/accounts/{row['target_subject_id']}/factsheet-designer", "owner_action": None}
    if not step["open"]:
        return step
    try:
        designs = connection.execute(
            "SELECT draft_id, title, status, current_version, updated_at FROM factsheet_design_drafts "
            "WHERE tenant_id = %s AND subject_id = %s AND status <> 'archived' ORDER BY updated_at DESC LIMIT 10",
            (tenant_id, subject_id),
        ).fetchall()
    except Exception:  # the designer's tables are another feature's; never break the case page for them
        return {**step, "unavailable": True}
    step["designs"] = [{"draft_id": str(d.get("draft_id") or ""), "title": str(d["title"]), "status": str(d["status"]),
                        "version": int(d["current_version"]), "updated_at": str(d["updated_at"])} for d in designs]
    step["submitted"] = next((d for d in step["designs"] if d["status"] == "submitted"), None)
    if step["submitted"] is None and case.checks.get("reports_rendered") != "pass":
        step["owner_action"] = ("Design the factsheet: open the factsheet designer, upload the template, "
                                "review the draft and submit it.")
    return step


def _subject_names(connection, tenant_id: str, subject_ids: Iterable[str]) -> dict[str, str]:
    wanted = sorted(set(subject_ids))
    if not wanted:
        return {}
    rows = connection.execute(
        "SELECT subject_id, display_name FROM platform_subjects WHERE tenant_id = %s AND subject_id = ANY(%s)",
        (tenant_id, wanted),
    ).fetchall()
    return {str(row["subject_id"]): str(row["display_name"]) for row in rows}


def _composition_svg(connection, tenant_id: str, definition: Mapping[str, Any]) -> str:
    source = definition.get("source_account_id")
    names = _subject_names(connection, tenant_id, [str(source)] if source else [])
    return tree_svg(tree_for_definition(definition, source_names=names))


# Portfolio builder ---------------------------------------------------------


def _market_indices() -> dict[str, Any]:
    return {item.instrument_id: item for item in ALPHA_WORLD_MARKET_INSTRUMENTS}


def builder_form_from_definition(definition: Mapping[str, Any], *, report_style: str | None = None
                                 ) -> dict[str, Any]:
    """A live portfolio's definition as builder fields, to copy as a starting point.

    Exposures are rescaled so the copy starts at a NAV of 100, as MS and T do. A
    copy of MS or T, or of a composite in their design, keeps that design.
    """

    if definition.get("subject_type") == "composite_portfolio":
        components = list(definition.get("components") or [])
        nav = float(definition.get("constant_cash") or 0) + 100.0 * sum(float(c["coefficient"]) for c in components)
        return {
            "kind": "composite_portfolio",
            "reporting_currency": definition.get("base_currency"),
            "start_date": definition.get("normalization_date"),
            "report_style": report_style if report_style in REPORT_STYLES else (
                definition.get("subject_id") if definition.get("subject_id") in REPORT_STYLES else None),
            "sleeves": [{"component_id": c["component_id"],
                         "direction": "long" if float(c["coefficient"]) > 0 else "short",
                         "exposure_percent": round(abs(float(c["coefficient"])) * 100.0 * 100.0 / nav, 6)}
                        for c in components],
        }
    position_filter = dict(definition.get("position_filter") or {})
    cash_policy = dict(definition.get("cash_policy") or {})
    return {
        "kind": "filtered_portfolio",
        "reporting_currency": definition.get("base_currency"),
        "start_date": definition.get("history_start_date"),
        "source_account_id": definition.get("source_account_id"),
        "excluded_countries": list(position_filter.get("excluded_countries") or []),
        "excluded_exchanges": list(position_filter.get("excluded_exchanges") or []),
        "excluded_currencies": list(position_filter.get("excluded_currencies") or []),
        "allowed_cash_currencies": list(cash_policy.get("allowed_currencies") or []),
        "opening_cash": dict(cash_policy.get("opening_cash") or {}),
        "include_actual_interest": bool(cash_policy.get("include_actual_posted_interest", True)),
    }


def portfolio_builder_catalogue(connection, tenant_id: str) -> dict:
    """Everything the builder offers: live sleeves, market indices and portfolios to copy."""

    subjects = connection.execute(
        "SELECT subject_id, subject_type, display_name, COALESCE(reporting_currency, base_currency) AS currency, "
        "history_start_date FROM platform_subjects WHERE tenant_id = %s AND lifecycle_status = 'active' "
        "AND subject_type IN ('physical_account', 'filtered_portfolio', 'composite_portfolio') "
        "ORDER BY display_name, subject_id",
        (tenant_id,),
    ).fetchall()
    indices = _market_indices()
    coverage = {
        str(row["instrument_id"]): row
        for row in connection.execute(
            "SELECT instrument_id, MIN(price_date) AS first_date, MAX(price_date) AS latest_date, "
            "COUNT(*) AS observations FROM market_data_prices WHERE instrument_id = ANY(%s) GROUP BY instrument_id",
            (sorted(indices),),
        ).fetchall()
    }
    copies = connection.execute(
        "SELECT d.subject_id, s.display_name, s.metadata_json, d.configuration_json FROM subject_calculation_definitions d "
        "JOIN platform_subjects s ON s.tenant_id = d.tenant_id AND s.subject_id = d.subject_id "
        "WHERE d.tenant_id = %s AND d.approval_status = 'approved' AND d.effective_to IS NULL "
        "AND s.lifecycle_status = 'active' ORDER BY s.display_name",
        (tenant_id,),
    ).fetchall()
    starting_points = []
    for row in copies:
        definition = row["configuration_json"]
        definition = json.loads(definition) if isinstance(definition, str) else dict(definition)
        metadata = row["metadata_json"] or {}
        metadata = json.loads(metadata) if isinstance(metadata, str) else dict(metadata)
        try:
            form = builder_form_from_definition(
                definition, report_style=(metadata.get("report_profile") or {}).get("style"))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
        starting_points.append({"subject_id": str(row["subject_id"]), "display_name": str(row["display_name"]),
                                "form": form})
    return {
        "sleeves": [{"component_id": str(row["subject_id"]), "name": str(row["display_name"]),
                     "kind": "account" if row["subject_type"] == "physical_account" else "portfolio",
                     "currency": str(row["currency"] or ""),
                     "history_start_date": str(row["history_start_date"]) if row["history_start_date"] else None}
                    for row in subjects],
        "indices": [{"component_id": key, "name": item.name, "kind": "benchmark", "currency": item.currency,
                     "first_date": str(coverage[key]["first_date"]) if key in coverage else None,
                     "latest_date": str(coverage[key]["latest_date"]) if key in coverage else None}
                    for key, item in indices.items()],
        "starting_points": starting_points,
        "report_styles": [{"style": key, "label": f"{value['label']} design"} for key, value in REPORT_STYLES.items()],
    }


def builder_fields(body: "PortfolioRequest") -> dict[str, Any]:
    """Translate the builder's form into the Studio's typed fields.

    A composite starts at 100: each sleeve's exposure in % of NAV becomes its
    coefficient, and the cash is whatever brings the total to 100.
    """

    _portfolio_id({"subject_id": body.subject_id.strip()})
    if body.kind == "composite_portfolio":
        if not body.sleeves:
            raise OnboardingError("Add at least one sleeve", 400)
        signed = [(item.component_id, item.exposure_percent if item.direction == "long" else -item.exposure_percent)
                  for item in body.sleeves]
        return {
            "subject_id": body.subject_id.strip(), "display_name": body.display_name,
            "base_currency": body.reporting_currency, "normalization_date": body.start_date.isoformat(),
            "constant_cash": round(100.0 - sum(value for _, value in signed), 10), "market_carry_days": 7,
            "components": [{"component_id": component, "coefficient": round(value / 100.0, 10)}
                           for component, value in signed],
        }
    if not body.source_account_id:
        raise OnboardingError("Choose the account to filter", 400)
    return {
        "subject_id": body.subject_id.strip(), "display_name": body.display_name,
        "source_account_id": body.source_account_id, "base_currency": body.reporting_currency,
        "history_start_date": body.start_date.isoformat(),
        "excluded_countries": body.excluded_countries, "excluded_exchanges": body.excluded_exchanges,
        "excluded_currencies": body.excluded_currencies, "opening_cash": body.opening_cash,
        "allowed_cash_currencies": body.allowed_cash_currencies,
        "include_actual_interest": body.include_actual_interest,
    }


def preview_tree(connection, tenant_id: str, body: "PortfolioRequest") -> dict:
    """The tree for the form as typed so far; validates nothing beyond the arithmetic."""

    title = body.display_name.strip() or "New portfolio"
    if body.kind == "composite_portfolio":
        indices = _market_indices()
        wanted = sorted({item.component_id for item in body.sleeves} - set(indices))
        subjects = {str(row["subject_id"]): row for row in connection.execute(
            "SELECT subject_id, subject_type, display_name, COALESCE(reporting_currency, base_currency) AS currency "
            "FROM platform_subjects WHERE tenant_id = %s AND subject_id = ANY(%s)",
            (tenant_id, wanted),
        ).fetchall()} if wanted else {}
        sleeves = []
        for item in body.sleeves:
            index, subject = indices.get(item.component_id), subjects.get(item.component_id)
            sleeves.append({
                "name": index.name if index else str(subject["display_name"]) if subject else item.component_id,
                "kind": "benchmark" if index else (
                    "account" if subject and subject["subject_type"] == "physical_account" else "portfolio"),
                "currency": index.currency if index else str(subject["currency"] or "") if subject else "",
                "coefficient": (1 if item.direction == "long" else -1) * item.exposure_percent / 100.0,
            })
        cash = 100.0 - sum(100.0 * sleeve["coefficient"] for sleeve in sleeves)
        try:
            tree = composite_tree(title=title, base_currency=body.reporting_currency, constant_cash=cash,
                                  sleeves=sleeves)
        except ValueError as error:
            raise OnboardingError(str(error), 400) from error
    else:
        names = _subject_names(connection, tenant_id, [body.source_account_id] if body.source_account_id else [])
        tree = filtered_tree(
            title=title, base_currency=body.reporting_currency,
            source_name=names.get(body.source_account_id or "", body.source_account_id or "Choose an account"),
            excluded_countries=body.excluded_countries, excluded_exchanges=body.excluded_exchanges,
            excluded_currencies=body.excluded_currencies, cash_currencies=body.allowed_cash_currencies,
            opening_cash=body.opening_cash)
    return {"svg": tree_svg(tree), "totals": tree["totals"]}


def check_portfolio(connection, tenant_id: str, *, definition: Mapping[str, Any], description: str,
                    today: date, report_style: str | None = None) -> dict:
    """Everything start_derived_case would refuse, as a list, without writing anything."""

    definition = _as_stored(definition)
    problems = []
    for rule in (lambda: _clean_description(description), lambda: _portfolio_id(definition),
                 lambda: _report_style(definition, report_style)):
        try:
            rule()
        except OnboardingError as error:
            problems.append(f"{error}.")
    subject_id = str(definition["subject_id"])
    starts = str(definition.get("history_start_date") or definition.get("normalization_date") or "")
    if starts > today.isoformat():
        problems.append("History cannot start in the future.")
    try:
        _assert_can_start(connection, tenant_id, target_subject_id=subject_id)
    except OnboardingError as error:
        problems.append(f"{error}.")
    if _subject_taken(connection, tenant_id, subject_id):
        problems.append("This portfolio ID is already used on the platform.")
    _, not_live = _sleeve_states(connection, tenant_id, definition)
    if not_live:
        problems.append("Not live yet: " + ", ".join(not_live) + ".")
    late = _sleeves_starting_late(connection, tenant_id, definition)
    if late:
        problems.append("No history on the start date: " + ", ".join(late) + ".")
    notes = []
    base = str(definition.get("base_currency"))
    converted = sorted({str(c.get("source_currency")) for c in definition.get("components") or []
                        if c.get("source_currency") and c.get("source_currency") != base})
    if converted:
        notes.append(f"Sleeves in {', '.join(converted)} are converted to {base} every day.")
    if definition.get("subject_type") == "composite_portfolio" and float(definition.get("constant_cash") or 0) < 0:
        notes.append("Exposure is above 100% of NAV, so the cash is negative: the portfolio borrows.")
    return {
        "ok": not problems, "problems": problems, "notes": notes,
        "definition_sha256": configuration_sha256(definition),
        "summary": _definition_summary(definition),
        "svg": _composition_svg(connection, tenant_id, definition),
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


def _last_check_message(value: Any) -> str | None:
    if not value:
        return None
    value = json.loads(value) if isinstance(value, str) else dict(value)
    return str(value.get("message") or "")[:300] or None


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
            # Where the key is: its Key Vault secret's name and fingerprint, never the key.
            "key_vault_secret": str(row["key_vault_secret_name"]),
            "key_fingerprint": str(row["credential_fingerprint"]),
            "key_stored_at": str(row["created_at"]) if row.get("created_at") else None,
            "last_checked_at": str(row["last_checked_at"]) if row.get("last_checked_at") else None,
            "last_check_message": _last_check_message(row.get("last_check_json")),
            "can_check_now": row["broker"] == "kiwoom_rest" and row["status"] not in {"suspended", "retired"},
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
        # What the connection agent found for each login (None while ONBOARDING_AGENTS_ENABLED is off).
        "agent_reports": onboarding_agents.connection_reports(connection, tenant_id),
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
    open_count = sum(1 for item in cases if item["stage_number"] <= 3)
    try:
        benchmarks = benchmark_choices(connection, tenant_id)
    except Exception:  # the benchmark list is another feature's; the start form then asks for none
        benchmarks = []
    return {"cases": cases, "available": available, "live": live, "can_start": open_count < MAX_OPEN_CASES,
            "open_cases": open_count, "max_open_cases": MAX_OPEN_CASES, "benchmarks": benchmarks}


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
    primary_benchmark_id: str | None = Field(default=None, max_length=80)
    secondary_benchmark_ids: list[str] = Field(default_factory=list, max_length=4)
    kiwoom_include_us: bool | None = None

    def benchmarks(self) -> dict[str, Any] | None:
        if not self.primary_benchmark_id:
            return None
        return {"primary": self.primary_benchmark_id, "secondary": self.secondary_benchmark_ids}


class CaseTermsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_benchmark_id: str | None = Field(default=None, max_length=80)
    secondary_benchmark_ids: list[str] = Field(default_factory=list, max_length=4)
    kiwoom_include_us: bool | None = None


class BuiltInDesignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    design_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")


class ApproveCaseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terms_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledge_warnings: list[str] = Field(default_factory=list, max_length=20)
    confirmation: str | None = Field(default=None, max_length=120)
    viewers: list[str] = Field(default_factory=list, max_length=50)


class ExternalDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disclosure_version: Literal["yahoo-ticker-disclosure-v1"]
    scope_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved: Literal[True]


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


class BuilderSleeve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(min_length=3, max_length=80)
    direction: Literal["long", "short"]
    exposure_percent: float = Field(gt=0, le=1000)


class PortfolioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["composite_portfolio", "filtered_portfolio"]
    subject_id: str = Field(default="", max_length=64)
    display_name: str = Field(default="", max_length=160)
    description: str = Field(default="", max_length=600)
    reporting_currency: str = Field(pattern=r"^[A-Z]{3}$")
    start_date: date
    sleeves: list[BuilderSleeve] = Field(default_factory=list, max_length=24)
    source_account_id: str | None = Field(default=None, max_length=32)
    excluded_countries: list[str] = Field(default_factory=list, max_length=32)
    excluded_exchanges: list[str] = Field(default_factory=list, max_length=64)
    excluded_currencies: list[str] = Field(default_factory=list, max_length=32)
    allowed_cash_currencies: list[str] = Field(default_factory=list, max_length=16)
    opening_cash: dict[str, float] = Field(default_factory=dict)
    include_actual_interest: bool = True
    report_style: Literal["alpha-world-ms", "alpha-world-t"] | None = None


class CaseQuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=36)


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
    derived_definition: Callable[[Any, str, dict], dict],
    today: Callable[[], date] = date.today,
    download_report: Callable[[str, str], bytes] | None = None,
    report_container: str = "internal-reports",
    upload_document: Callable[[str, str, bytes, str], None] | None = None,
    list_documents: Callable[[str, str], list[dict]] | None = None,
    intake_container: str = onboarding_agents.INTAKE_CONTAINER,
    ask_case: Callable[[], Callable[[dict[str, Any]], Iterable[bytes]] | None] = lambda: None,
    start_worker: Callable[[], str] | None = None,
) -> None:
    """Add the new onboarding routes. Each answers 404 while the switch is off.

    derived_definition(connection, kind, fields) runs the Derived Portfolio
    Studio's validation on the builder's fields and returns the definition.
    download_report(container, blob) reads a stage 3 report from private storage.
    upload_document(container, blob, content, content_type) stores a case document in the private intake
    container without overwriting, and list_documents(container, prefix) lists what is there; both are used
    only while ONBOARDING_AGENTS_ENABLED is on. ask_case() gives the function that streams the agent
    service's answer to an owner's question about a case, or None when that service is not configured.
    start_worker() starts one execution of the onboarding worker job and returns its name (Run now,
    Check now); without it, or when Azure refuses, the request waits for the worker's next scheduled run.
    """

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

    @app.get("/account-onboarding/new-portfolio", response_class=HTMLResponse)
    def portfolio_builder_page(user: dict[str, Any] = Depends(v2_onboarding_user)) -> str:
        return render_page(user, active="account-onboarding", title="New portfolio",
                           description=PORTFOLIO_DESCRIPTION, content=PORTFOLIO_CONTENT, script=PORTFOLIO_SCRIPT)

    @app.get("/api/onboarding-v2/portfolio-builder")
    def portfolio_builder(user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(read_database, lambda c: portfolio_builder_catalogue(c, tenant_id))

    @app.post("/api/onboarding-v2/portfolios/preview")
    def portfolio_preview(body: PortfolioRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(read_database, lambda c: preview_tree(c, tenant_id, body))

    def validated(connection, body: PortfolioRequest) -> dict:
        return derived_definition(connection, body.kind, builder_fields(body))

    @app.post("/api/onboarding-v2/portfolios/check")
    def portfolio_check(body: PortfolioRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(write_database, lambda c: check_portfolio(c, tenant_id, definition=validated(c, body),
                                                             description=body.description, today=today(),
                                                             report_style=body.report_style))

    @app.post("/api/onboarding-v2/portfolios")
    def portfolio_start(body: PortfolioRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(write_database, lambda c: start_derived_case(
            c, tenant_id, definition=validated(c, body), description=body.description, actor=actor(user),
            today=today(), report_style=body.report_style))

    @app.get("/api/onboarding-v2/overview")
    def overview(user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return run(read_database, lambda c: onboarding_overview(c, tenant_id, today=today(),
                                                                platform_ip=platform_ip()))

    @app.get("/api/onboarding-v2/cases/{case_id}")
    def case(case_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        detail = run(read_database, lambda c: case_detail(c, tenant_id, str(case_id)))
        owner = user.get("role") == "owner"
        return {**detail, "may_approve": owner,
                "may_ask": owner and onboarding_agents.agents_enabled() and ask_case() is not None}

    @app.post("/api/onboarding-v2/cases/{case_id}/ask")
    def ask_about_case(case_id: uuid.UUID, body: CaseQuestionRequest,
                       user: dict[str, Any] = Depends(v2_owner)) -> StreamingResponse:
        asker = ask_case() if onboarding_agents.agents_enabled() else None
        if asker is None:
            raise HTTPException(status_code=404, detail="Not found")
        object_id = str(user.get("object_id") or "").lower()
        if not object_id:
            raise HTTPException(status_code=403, detail="Your sign-in has no Entra object ID.")
        facts = run(read_database, lambda c: onboarding_agents.case_facts(c, tenant_id, str(case_id)))
        stream = asker({"user_object_id": object_id, "case_id": str(case_id), "question": body.question,
                           "conversation_id": body.conversation_id, "case_facts": facts})
        return StreamingResponse(stream, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.post("/api/onboarding-v2/cases")
    def start(body: StartCaseRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        started = run(write_database, lambda c: start_case(
            c, tenant_id, connection_id=str(body.connection_id), broker_account_id=body.broker_account_id,
            display_name=body.display_name, reporting_currency=body.reporting_currency,
            history_start_date=body.history_start_date, actor=actor(user), today=today(),
            benchmarks=body.benchmarks(), include_us=body.kiwoom_include_us))
        # The worker's first run checks the account at once instead of the next morning.
        try:
            started["worker"] = queue_worker(user, case_id=started["case_id"])
        except Exception:  # noqa: BLE001 - the case is started either way; the schedule or the daily job takes it
            started["worker"] = {"status": "not_queued"}
        return started

    def queue_worker(user: Mapping[str, Any], *, case_id: str | None = None, connection_id: str | None = None,
                     asks: Iterable[Mapping[str, Any]] = ()) -> dict:
        """Write the request, then start the worker job; a refused start leaves it for the schedule."""

        from psycopg.errors import UndefinedTable

        try:
            request = run(write_database, lambda c: request_worker_run(
                c, tenant_id, actor=actor(user), case_id=case_id, connection_id=connection_id, asks=asks))
        except UndefinedTable as error:  # migration 085 not applied yet
            raise HTTPException(status_code=503, detail="The onboarding worker is not set up yet") from error
        reference, problem = None, None
        if request["status"] == "requested":
            try:
                if start_worker is None:
                    raise RuntimeError("not configured")
                reference = start_worker()
            except Exception:  # the schedule takes the request within 20 minutes
                problem = "The worker could not be started now; its next scheduled run takes this."
            run(write_database, lambda c: note_worker_start(c, tenant_id, request["run_id"], reference, problem) or {})
        now = datetime.now(timezone.utc)
        return {**request, "started": reference is not None, "message": problem,
                "next_scheduled": onboarding_worker.next_scheduled(now).isoformat(),
                "window_note": onboarding_worker.window_note(now)}

    @app.post("/api/onboarding-v2/cases/{case_id}/run-now")
    def run_now(case_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return queue_worker(user, case_id=str(case_id))

    @app.post("/api/onboarding-v2/cases/{case_id}/built-in-design")
    def built_in_design(case_id: uuid.UUID, body: BuiltInDesignRequest, user: dict[str, Any] = Depends(v2_owner)) -> dict:
        def offered(connection) -> dict:
            row, _, _, case = _load_case(connection, tenant_id, str(case_id))
            if body.design_id not in {item["design_id"] for item in _built_in_designs(row, case)}:
                raise OnboardingError("This design is not offered for this account", 404)
            return {}

        run(read_database, offered)
        return queue_worker(user, case_id=str(case_id),
                            asks=[{"ask": "built_in_design", "design_id": body.design_id}])

    @app.post("/api/onboarding-v2/cases/{case_id}/terms")
    def case_terms(case_id: uuid.UUID, body: CaseTermsRequest, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        picked = ({"primary": body.primary_benchmark_id, "secondary": body.secondary_benchmark_ids}
                  if body.primary_benchmark_id else None)
        return run(write_database, lambda c: set_case_terms(c, tenant_id, str(case_id), benchmarks=picked,
                                                            include_us=body.kiwoom_include_us, actor=actor(user)))

    @app.post("/api/onboarding-v2/cases/{case_id}/designs/{draft_id}/archive")
    def archive_draft(case_id: uuid.UUID, draft_id: uuid.UUID, user: dict[str, Any] = Depends(v2_owner)) -> dict:
        return run(write_database, lambda c: archive_design_draft(c, tenant_id, str(case_id), str(draft_id)))

    @app.post("/api/broker-connections/{connection_id}/check")
    def check_connection(connection_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        return queue_worker(user, connection_id=str(connection_id))

    @app.post("/api/onboarding-v2/cases/{case_id}/external-market-data")
    def external_data_route(case_id: uuid.UUID, body: ExternalDataRequest,
                            user: dict[str, Any] = Depends(v2_owner)) -> dict:
        return run(write_database, lambda c: approve_external_data(
            c, tenant_id, str(case_id), disclosure_version=body.disclosure_version,
            scope_sha256=body.scope_sha256, actor=actor(user) or ""))

    @app.post("/api/onboarding-v2/cases/{case_id}/approvals/{approval}")
    def approve_route(case_id: uuid.UUID, approval: str, body: ApproveCaseRequest,
                      user: dict[str, Any] = Depends(v2_owner)) -> dict:
        return run(write_database, lambda c: approve_case(
            c, tenant_id, str(case_id), approval=approval, terms_sha256=body.terms_sha256,
            acknowledge_warnings=body.acknowledge_warnings, confirmation=body.confirmation,
            actor=actor(user) or "", viewers=body.viewers))

    @app.get("/api/onboarding-v2/cases/{case_id}/reports/{generation_run_id}")
    def case_report_file(case_id: uuid.UUID, generation_run_id: int,
                         user: dict[str, Any] = Depends(v2_owner)) -> Response:
        artifact = run(read_database, lambda c: case_report(c, tenant_id, str(case_id), generation_run_id,
                                                            container=report_container))
        try:
            if download_report is None:
                raise RuntimeError("Private report storage is not configured")
            content = download_report(artifact["container_name"], artifact["blob_name"])
        except Exception as error:
            raise HTTPException(status_code=503, detail="The report could not be read from private storage") from error
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact["sha256"]:
            raise HTTPException(status_code=503, detail="The report failed its integrity check")
        return Response(content=content, media_type=str(artifact["media_type"]), headers={
            "Content-Disposition": f'attachment; filename="{artifact["file_name"]}"',
            "X-Content-SHA256": digest, "X-Report-Scope": "owner-review", "Cache-Control": "private, no-store"})

    def agents_on() -> None:
        if not onboarding_agents.agents_enabled():
            raise HTTPException(status_code=404, detail="Not found")

    def case_documents(case_id: str) -> list[dict]:
        if list_documents is None:
            raise HTTPException(status_code=503, detail="Private document storage is not configured")
        prefix = onboarding_agents.document_prefix(case_id)
        try:
            listed = list_documents(intake_container, prefix)
        except Exception as error:
            raise HTTPException(status_code=503, detail="The case documents could not be listed") from error
        return [{"name": str(item["name"])[len(prefix):], "byte_count": int(item.get("size") or 0)}
                for item in sorted(listed, key=lambda item: str(item["name"]))
                if str(item["name"]).startswith(prefix) and not str(item["name"]).endswith("/")]

    @app.get("/api/onboarding-v2/cases/{case_id}/documents")
    def documents(case_id: uuid.UUID, user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        agents_on()

        def exists(connection) -> dict:
            _load_case(connection, tenant_id, str(case_id))
            return {}

        run(read_database, exists)
        return {"documents": case_documents(str(case_id)), "max_documents": onboarding_agents.MAX_DOCUMENTS}

    @app.post("/api/onboarding-v2/cases/{case_id}/documents")
    async def add_document(case_id: uuid.UUID, document: UploadFile = File(...),
                           user: dict[str, Any] = Depends(v2_onboarding_user)) -> dict:
        """One client document (mandate, fee terms, side letter) for the intake agent to draft the terms from."""

        agents_on()

        def still_open(connection) -> dict:
            row, _, approvals, _ = _load_case(connection, tenant_id, str(case_id))
            if not onboarding_agents.documents_open(row, approvals):
                raise OnboardingError("Documents can be added only before the account terms are approved", 409)
            return {}

        run(read_database, still_open)
        content = await document.read(onboarding_agents.MAX_DOCUMENT_BYTES + 1)
        try:
            name, content_type = onboarding_agents.validate_document(document.filename or "", content)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        existing = case_documents(str(case_id))
        if any(item["name"] == name for item in existing):
            raise HTTPException(status_code=409, detail=f"{name} is already on this case")
        if len(existing) >= onboarding_agents.MAX_DOCUMENTS:
            raise HTTPException(status_code=409, detail=(
                f"This case already has {onboarding_agents.MAX_DOCUMENTS} documents, the most the agent reads"))
        if upload_document is None:
            raise HTTPException(status_code=503, detail="Private document storage is not configured")
        try:
            upload_document(intake_container, onboarding_agents.document_blob_name(str(case_id), name), content,
                            content_type)
        except Exception as error:
            if type(error).__name__ == "ResourceExistsError":  # added by someone else a moment ago
                raise HTTPException(status_code=409, detail=f"{name} is already on this case") from error
            raise HTTPException(status_code=503, detail="The document was not stored") from error
        return {"status": "stored", "name": name, "byte_count": len(content),
                "sha256": hashlib.sha256(content).hexdigest()}

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

ONBOARDING_DESCRIPTION = ("Pick up to three accounts at a time. Nothing is imported or shown to viewers until you "
                          "start its case.")
CASE_DESCRIPTION = "One account or portfolio, four stages, three decisions from you."
CONNECTIONS_DESCRIPTION = "One connection per broker login. Keys go straight to Key Vault and are never shown again."

ONBOARDING_CONTENT = """<div class="page-view active" data-section="account-onboarding">
<div class="card wide"><div class="label">How it works</div><ol class="onboarding-steps" style="margin:10px 0 0 18px;padding:0;line-height:1.7">
<li><strong>Connect the broker login</strong> on Broker connections. The platform checks it and finds its accounts.</li>
<li><strong>Pick an account</strong> below, or build a portfolio from live accounts in the portfolio builder.</li>
<li><strong>Follow its case</strong> through four stages: request, load and reconcile, build history, first month-end. You make three decisions; nothing reaches viewers before you approve.</li></ol>
<div class="actions"><a class="button" href="/broker-connections">Broker connections</a><a class="button" href="/account-onboarding/new-portfolio">Portfolio builder</a><a class="button" href="/account-onboarding/classic">Master accounts and earlier cases</a></div></div>
<div class="card wide"><div class="label">Cases in progress</div><div id="v2-cases"><div class="muted">Loading…</div></div></div>
<div class="card wide"><div class="label">Available to onboard</div><div class="muted">Found through your broker connections. Each account is onboarded only when you choose it.</div><div id="v2-available"><div class="muted">Loading…</div></div><div id="v2-start" hidden></div></div>
<div class="card wide"><div class="label">Live accounts</div><div id="v2-live" class="status-row"></div></div>
</div>"""

CASE_CONTENT = """<div class="page-view active" data-section="account-onboarding" data-case-id="{case_id}">
<div class="card wide"><div class="label" id="case-kicker">Loading…</div><h2 id="case-title"></h2><div id="case-next" class="muted"></div><div id="case-progress" class="status-row"></div></div>
<div class="card wide" id="case-portfolio" hidden><div class="label">The portfolio</div><p id="case-description"></p><p class="muted" id="case-report-style" hidden></p><div id="case-tree" style="max-width:760px;margin-top:14px;overflow-x:auto"></div></div>
<div class="card wide"><div class="label">Checks for this stage</div><div id="case-checks" class="onboarding-checks"></div></div>
<div class="card wide" id="case-work" hidden><div class="label">Platform work</div><p class="muted">The onboarding worker does the platform's part of this case every 20 minutes: loading the account, building its history, looking up names and sectors, rendering the historical reports and recording the agent review. Run now starts it at once.</p><div id="case-work-body"></div><div id="case-work-result" class="muted" role="status" aria-live="polite"></div></div>
<div class="card wide" id="case-history" hidden><div class="label">History</div><div id="case-history-body"></div></div>
<div class="card wide" id="case-terms-edit" hidden><div class="label">Benchmarks and account options</div><p class="muted">Part of the account terms you approve in approval 1. They can change until then.</p><form id="case-terms-form"><div id="case-terms-fields" class="status-row"></div><div class="actions"><button class="button" type="submit">Save</button></div><div class="muted" role="status"></div></form></div>
<div class="card wide" id="case-external" hidden><div class="label">Yahoo Finance</div><div id="case-external-body"></div><div id="case-external-result" class="muted" role="status" aria-live="polite"></div></div>
<div class="card wide" id="case-design" hidden><div class="label">Factsheet design</div><p class="muted">The monthly factsheets use the design you make from your template in the factsheet designer. Upload the template and, if you have one, a completed example. The design agents draft it, you review it and ask for changes in plain words, then submit it. The historical factsheets are rendered from the submitted design for approval 2.</p><div id="case-design-body"></div></div>
<div class="card wide" id="case-proposals" hidden><div class="label">Proposed security master entries</div><p class="muted">Securities in this account that the security master does not have yet, as the agent proposes them. Nothing is added until you approve the account terms.</p><div id="case-proposal-list"></div></div>
<div class="card wide" id="case-documents" hidden><div class="label">Client documents</div><p class="muted">The mandate, fee terms and side letters. The agent drafts the account terms from them, quoting each clause, for you to compare before approval 1.</p><div id="case-document-list"></div><form id="case-document-form" hidden><div class="actions"><input type="file" name="document" accept=".pdf,.docx,.txt,.md" required aria-label="Client document"><button class="button" type="submit">Add document</button></div><div class="muted" role="status"></div></form></div>
<div class="card wide"><div class="label">Your three approvals</div><div id="case-approvals" class="status-row"></div><div id="case-term-draft" hidden></div><div id="case-readiness" hidden></div><div id="case-approval-form"></div><div id="case-result" class="muted" role="status" aria-live="polite"></div></div>
<div class="card wide" id="case-ask" hidden><div class="label">Ask about this case</div><p class="muted">The analyst answers from this case's checks, approvals, terms and the agents' findings, and every figure it gives is checked against them. It cannot approve or change anything.</p><div id="case-ask-thread" aria-live="polite"></div><form id="case-ask-form"><textarea name="question" rows="2" maxlength="2000" required placeholder="For example: what is still stopping go-live?" aria-label="Your question" style="display:block;width:100%;min-width:0"></textarea><div class="actions"><button class="button" type="submit">Ask</button></div></form></div>
<div class="card wide" id="case-cancel-card" hidden><div class="label">Stop this case</div><p class="muted">Cancelling frees the account or portfolio ID to be added again later. Nothing reached viewers.</p><div class="actions"><input id="case-cancel-reason" type="text" maxlength="500" placeholder="Reason" aria-label="Reason for cancelling"><button id="case-cancel" class="button" type="button">Cancel this case</button></div></div>
</div>"""

CONNECTIONS_CONTENT = """<div class="page-view active" data-section="broker-connections">
<div class="card wide" id="bc-address"><div class="label">The platform's fixed address</div><p class="muted">Every call to a broker comes from this one internet address. Register it in the broker's API settings (Kiwoom: the IP registration of its REST API page) before you save the keys here.</p><div class="actions"><strong id="bc-address-ip" style="font-size:1.3em">Not configured</strong><button class="button" type="button" id="bc-address-copy" hidden>Copy</button></div></div>
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
<div class="card wide"><div class="label">Next step</div><p class="muted">Once a connection is healthy, its accounts appear under Available to onboard.</p><div class="actions"><a class="button" href="/account-onboarding">Account onboarding</a></div></div>
</div>"""

_COMMON_SCRIPT = r"""
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const json=async response=>{const payload=await response.json().catch(()=>({}));if(!response.ok){const detail=Array.isArray(payload.detail)?payload.detail.map(d=>d.msg).join('; '):payload.detail;throw new Error((typeof detail==='string'?detail:detail&&detail.message)||`http_${response.status}`)}return payload};
const post=(url,body)=>fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(json);
const pill=(text,cls)=>`<span class="onboarding-pill ${cls||''}">${esc(text)}</span>`;
const row=(name,note,right)=>`<div class="onboarding-row"><div><strong>${esc(name)}</strong><div class="muted">${esc(note)}</div></div><div class="actions">${right||''}</div></div>`;
const statusPill={platform_working:['Waiting on platform','blocked'],waiting_on_owner:['Waiting on you',''],blocked:['Check failed','blocked'],live:['Live','active'],closed:['Closed','active'],cancelled:['Cancelled','']};
document.addEventListener('click',e=>document.querySelectorAll('details.platform-menu[open]').forEach(d=>{if(!d.contains(e.target))d.removeAttribute('open')}));
"""

ONBOARDING_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const load=()=>fetch('/api/onboarding-v2/overview').then(json).then(data=>{
  document.querySelector('#v2-cases').innerHTML=data.cases.length?data.cases.map(c=>{const [text,cls]=statusPill[c.status]||[c.status,''];return row(c.display_name,`${c.broker_label} · ${c.account} · Stage ${c.stage_number} of 4: ${c.stage_label}`,pill(text,cls)+`<a class="button" href="/account-onboarding/cases/${encodeURIComponent(c.case_id)}">Open case</a>`)}).join(''):'<p class="muted">No account is being onboarded.</p>';
  benchmarks=data.benchmarks||[];
  document.querySelector('#v2-available').innerHTML=(data.max_open_cases?`<p class="muted">${esc(data.open_cases)} of ${esc(data.max_open_cases)} cases open.</p>`:'')+(data.available.length?data.available.map((a,i)=>row(`${a.broker_label.split(' ')[0]} account ${a.account}${a.account_name?' · '+a.account_name:''}`,`${a.connection_name}${a.base_currency?' · '+a.base_currency:''}`,data.can_start?`<button class="button" type="button" data-pick="${i}">Add account</button>`:pill(`${data.max_open_cases||1} cases are open; finish one first`,'blocked'))).join(''):'<p class="muted">No new accounts found yet. Add or check a connection on the Broker connections page.</p>');
  document.querySelector('#v2-live').innerHTML=data.live.map(s=>`<div class="status-item"><div class="label">${esc(s.subject_id)}</div><strong>${esc(s.display_name)}</strong></div>`).join('')||'<div class="muted">None yet.</div>';
  document.querySelectorAll('[data-pick]').forEach(button=>button.addEventListener('click',()=>pick(data.available[Number(button.dataset.pick)])));
}).catch(error=>{document.querySelector('#v2-cases').innerHTML=`<div class="warning">This page could not load: ${esc(error.message)}</div>`});
let benchmarks=[];
const benchmarkFields=(primary,secondary)=>benchmarks.length?`<label class="status-item"><span class="label">Main benchmark</span><select name="primary_benchmark_id"><option value="">None yet</option>${benchmarks.map(b=>`<option value="${esc(b.benchmark_id)}"${b.benchmark_id===primary?' selected':''}>${esc(b.name)} (${esc(b.currency)})</option>`).join('')}</select></label><label class="status-item"><span class="label">Other benchmarks (up to 4)</span><select name="secondary_benchmark_ids" multiple size="4">${benchmarks.map(b=>`<option value="${esc(b.benchmark_id)}"${(secondary||[]).includes(b.benchmark_id)?' selected':''}>${esc(b.name)} (${esc(b.currency)})</option>`).join('')}</select></label>`:'';
const pick=a=>{const box=document.querySelector('#v2-start');box.hidden=false;const kiwoom=a.broker==='kiwoom_rest';box.innerHTML=`<form id="v2-start-form" class="onboarding-row" style="display:block"><strong>Add ${esc(a.broker_label)} account ${esc(a.account)}</strong><div class="status-row"><label class="status-item"><span class="label">Display name</span><input type="text" name="display_name" required minlength="3" maxlength="160" value="${esc(a.account_name||a.account)}"></label><label class="status-item"><span class="label">Reporting currency</span><input type="text" name="reporting_currency" required pattern="[A-Za-z]{3}" maxlength="3" value="${esc(a.base_currency||(kiwoom?'KRW':'USD'))}"></label><label class="status-item"><span class="label">History from</span><input type="date" name="history_start_date" required></label>${benchmarkFields('',[])}</div>${kiwoom?`<label style="display:flex;gap:10px;margin-top:12px"><input type="checkbox" name="kiwoom_include_us" checked> <span>Also read this account's U.S. stocks and dollar cash (Kiwoom's U.S. account). Untick for an account with Korean stocks only.</span></label>`:''}<div class="actions"><button class="button" type="submit">Start this case</button></div><div class="muted" role="status"></div></form>`;
  const form=box.querySelector('form');form.addEventListener('submit',async event=>{event.preventDefault();const values=new FormData(form),note=form.querySelector('[role=status]'),button=form.querySelector('button');button.disabled=true;note.textContent='Starting the case…';const body={connection_id:a.connection_id,broker_account_id:a.broker_account_id,display_name:values.get('display_name'),reporting_currency:String(values.get('reporting_currency')).toUpperCase(),history_start_date:values.get('history_start_date')};if(values.get('primary_benchmark_id')){body.primary_benchmark_id=values.get('primary_benchmark_id');body.secondary_benchmark_ids=values.getAll('secondary_benchmark_ids').filter(x=>x!==body.primary_benchmark_id)}if(kiwoom)body.kiwoom_include_us=form.querySelector('[name=kiwoom_include_us]').checked;try{const result=await post('/api/onboarding-v2/cases',body);window.location.href=result.next_page}catch(error){note.textContent=`Not started: ${error.message}`;button.disabled=false}});box.scrollIntoView({behavior:'smooth',block:'nearest'})};
load();
})();
</script>"""

CASE_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const root=document.querySelector('[data-case-id]'),caseId=root.dataset.caseId,result=document.querySelector('#case-result');
const tileState={done:['Done','active'],current:['In progress',''],not_started:['Not started','blocked'],cancelled:['Cancelled','blocked']};
const words=key=>String(key).replace(/_/g,' ').replace(/^./,ch=>ch.toUpperCase());
const shown=value=>value!==null&&typeof value==='object'?`<pre style="white-space:pre-wrap;margin:4px 0 0">${esc(JSON.stringify(value,null,2))}</pre>`:esc(value);
const evidenceDrawer=ev=>`<details class="agent-evidence" style="grid-column:1/-1;margin-top:8px"><summary>Evidence</summary><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px;margin-top:8px">${Object.entries(ev).filter(([key])=>key!=='recorded_by').map(([key,value])=>`<div class="status-item" style="min-width:0;overflow-wrap:anywhere"><div class="label">${esc(words(key))}</div>${shown(value)}</div>`).join('')}</div></details>`;
const outcomePill={pass:['Matches the documents','active'],warn:['Check the notes',''],fail:['Needs your attention','blocked']};
const renderProposals=list=>{const card=document.querySelector('#case-proposals');card.hidden=!list.length;document.querySelector('#case-proposal-list').innerHTML=list.map(p=>row(`${(p.evidence||{}).symbol||p.security_key} · ${p.display_name||''}`,[p.asset_class,p.currency,p.primary_exchange].filter(Boolean).join(' · ')+((p.identifiers||[]).length?' · '+p.identifiers.map(i=>`${i.scheme} ${i.identifier}`).join(', '):''),pill('Proposed',''))).join('')};
const renderDraft=(c,d)=>{const box=document.querySelector('#case-term-draft');const before=!c.approvals.find(a=>a.kind==='account_terms'&&a.approved);box.hidden=!(d&&before);if(!d||!before){box.innerHTML='';return}
  const [text,cls]=outcomePill[d.outcome]||[d.outcome,''];
  const terms=Object.values(d.terms||{}).filter(t=>t.status!=='not_found'||t.value);
  const source=s=>`${esc(s.document)} p.${esc(s.page)}: “${esc(s.quote)}”`;
  box.innerHTML=`<div class="onboarding-row" style="display:block;margin-top:14px"><strong>Terms drafted by the agent from the client's documents</strong> ${pill(text,cls)}<p class="muted">${esc(d.summary)} Compare them with the terms below before you approve; the agent does not change the case.</p>${terms.map(t=>`<div class="status-item" style="min-width:0;margin-top:8px"><div class="label">${esc(t.label)}${t.status==='conflict'?' · documents disagree':''}</div><strong>${esc(t.status==='conflict'?(t.values||[]).join(' / '):t.value)}</strong><details><summary>Sources</summary>${(t.sources||[]).map(s=>`<div class="muted">${source(s)}</div>`).join('')}</details></div>`).join('')}${(d.findings||[]).length?`<div class="warning" style="margin-top:10px">${d.findings.map(f=>`<div>${esc(f.message)}</div>`).join('')}</div>`:''}<div class="muted">Read from ${esc((d.documents||[]).map(x=>x.document).join(', ')||'no documents')} · drafted ${esc(d.drafted_at)}</div></div>`};
const designState={review:['In review',''],failed:['Needs changes','blocked'],submitted:['Submitted','active']};
let mayApprove=false,work=null;
const renderDesign=d=>{const card=document.querySelector('#case-design');card.hidden=!d;if(!d)return;const body=document.querySelector('#case-design-body');
  if(!d.open){body.innerHTML='<p class="muted">The designer opens once you approve the account terms.</p>';return}
  if(d.unavailable){body.innerHTML='<div class="warning">The designs could not be listed. Open the designer to see them.</div>'}
  else body.innerHTML=(d.designs.length?d.designs.map(x=>{const [text,cls]=designState[x.status]||[x.status,''];return row(x.title,`version ${x.version} · ${x.updated_at}`,pill(text,cls)+(mayApprove&&x.status!=='submitted'&&x.draft_id?`<button class="button" type="button" data-archive="${esc(x.draft_id)}">Archive draft</button>`:''))}).join(''):'<p class="muted">No design yet.</p>')
    +(d.submitted?`<p class="muted">The historical factsheets are rendered from ${esc(d.submitted.title)}. Until you approve them, submitting a newer design renders them again.</p>`:'');
  const builtIn=(work&&work.built_in_designs)||[];
  body.innerHTML+=`<div class="actions"><a class="button" href="${esc(d.designer_page)}">${d.designs.length?'Open the factsheet designer':'Design the factsheet'}</a>${mayApprove?builtIn.map(b=>`<button class="button" type="button" data-builtin="${esc(b.design_id)}">Use the built-in ${esc(b.title)} design</button>`).join(''):''}</div>${builtIn.length&&mayApprove?'<p class="muted">The platform ships the built-in designs measured from the owner\'s own template. Choosing one submits it as the designer would; the reports are then rendered from it.</p>':''}`;
  body.querySelectorAll('[data-archive]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;try{await post(`/api/onboarding-v2/cases/${caseId}/designs/${encodeURIComponent(b.dataset.archive)}/archive`,{});load()}catch(error){b.disabled=false;b.textContent=`Not archived: ${error.message}`}}));
  body.querySelectorAll('[data-builtin]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;b.textContent='Submitting…';try{const r=await post(`/api/onboarding-v2/cases/${caseId}/built-in-design`,{design_id:b.dataset.builtin});b.textContent=r.started?'Submitted to the worker; reports follow':'Queued for the next worker run';load()}catch(error){b.disabled=false;b.textContent=`Not submitted: ${error.message}`}}))};
const renderExternal=(c,x)=>{const card=document.querySelector('#case-external'),body=document.querySelector('#case-external-body');card.hidden=!x;if(!x)return;
  if(x.approved){body.innerHTML=`<div class="onboarding-check pass"><strong>You approved sending this account's ticker symbols to Yahoo</strong><span>Approved ${esc(x.approved_at)}. The sector job looks up names and sectors on its next run.</span></div>`;return}
  const can=c.may_approve&&x.open;
  body.innerHTML=`<div class="onboarding-check pending"><strong>Approve sending ticker symbols to Yahoo</strong><span>${esc(x.message)} The factsheets need these names and sectors.</span>${can?`<label style="display:flex;gap:10px;align-items:flex-start;margin-top:12px"><input id="external-consent" type="checkbox"> <span>I approve sending this account's ticker symbols to Yahoo Finance as described.</span></label><div class="actions"><button id="external-approve" class="button" type="button" disabled>Approve</button></div>`:`<span class="muted">${x.open?'Only the owner can approve this.':'This case is past go-live.'}</span>`}</div>`;
  if(!can)return;const box=body.querySelector('#external-consent'),button=body.querySelector('#external-approve'),note=document.querySelector('#case-external-result');
  box.addEventListener('change',()=>button.disabled=!box.checked);
  button.addEventListener('click',async()=>{button.disabled=true;note.textContent='Recording your approval…';try{await post(`/api/onboarding-v2/cases/${caseId}/external-market-data`,{disclosure_version:x.disclosure_version,scope_sha256:x.scope_sha256,approved:true});note.textContent='Approved. No ticker was sent by this button.';load()}catch(error){note.textContent=`Not approved: ${error.message}`;button.disabled=false}})};
const termState={matches:['Matches','active'],differs:['Differs','blocked'],not_in_documents:['Not in the documents',''],no_draft:['No draft yet','']};
const renderReadiness=r=>{const box=document.querySelector('#case-readiness');box.hidden=!r;if(!r){box.innerHTML='';return}
  if(r.unavailable){box.innerHTML='<div class="warning" style="margin-top:14px">The readiness summary could not be built. The checks and approvals above are still current.</div>';return}
  const quote=s=>s?`<div class="muted">${esc(s.document)} p.${esc(s.page)}: “${esc(s.quote)}”</div>`:'';
  const terms=r.terms_vs_documents.map(t=>{const [text,cls]=termState[t.state]||[t.state,''];return `<div class="status-item" style="min-width:0"><div class="label">${esc(t.label)}</div><strong>${esc(t.approved)}</strong> ${pill(text,cls)}${t.state==='differs'?`<div>Documents: ${esc(t.documents)}</div>`:''}${quote(t.source)}</div>`}).join('');
  const stages=r.stages.map(s=>`<div style="margin-top:8px"><div class="label">${esc(s.label)}</div>${s.checks.map(k=>`<div class="muted">${esc(k.label)}: ${esc(k.status)}${k.warning_acknowledged?' (acknowledged)':''} · ${k.recorded_by==='agent'?'checked by agent':'checked by platform'}</div>`).join('')||'<div class="muted">No checks.</div>'}</div>`).join('');
  box.innerHTML=`<div class="onboarding-row" style="display:block;margin-top:14px"><strong>Ready to go live?</strong> ${r.ready?pill('Nothing stands in the way','active'):pill('Not yet','blocked')}${r.blockers.length?`<div class="warning" style="margin-top:10px">${r.blockers.map(b=>`<div>${esc(b)}</div>`).join('')}</div>`:''}${r.notes.map(n=>`<div class="warning" style="margin-top:10px">${esc(n)}</div>`).join('')}<div class="label" style="margin-top:14px">Approved terms against the client's documents</div><div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-top:6px">${terms}</div><div class="label" style="margin-top:14px">Typing GO LIVE switches on</div><ul style="margin:6px 0 0 18px">${r.go_live_switches_on.map(e=>`<li>${esc(e)}</li>`).join('')}</ul><details style="margin-top:12px"><summary>Every check so far</summary>${stages}${r.acknowledged_warnings.length?`<div class="muted" style="margin-top:8px">Warnings acknowledged: ${esc(r.acknowledged_warnings.join(', '))}</div>`:''}</details><p class="muted">${esc(r.after_go_live)} Pack ${esc(String(r.pack_sha256).slice(0,12))}.</p></div>`};
const renderDocuments=c=>{const card=document.querySelector('#case-documents');card.hidden=!c.agents;if(!c.agents)return;const form=document.querySelector('#case-document-form');form.hidden=!c.documents_open;
  fetch(`/api/onboarding-v2/cases/${caseId}/documents`).then(json).then(data=>{document.querySelector('#case-document-list').innerHTML=data.documents.length?data.documents.map(d=>row(d.name,`${Math.max(1,Math.round(d.byte_count/1024))} KB`,'')).join(''):'<p class="muted">No documents yet.</p>'}).catch(error=>{document.querySelector('#case-document-list').innerHTML=`<div class="warning">The documents could not be listed: ${esc(error.message)}</div>`})};
document.querySelector('#case-document-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,note=form.querySelector('[role=status]'),button=form.querySelector('button');button.disabled=true;note.textContent='Uploading…';try{const done=await fetch(`/api/onboarding-v2/cases/${caseId}/documents`,{method:'POST',body:new FormData(form)}).then(json);note.textContent=`Added ${done.name}. The agent drafts the terms on its next run.`;form.reset();load()}catch(error){note.textContent=`Not added: ${error.message}`}finally{button.disabled=false}});
let askConversation=null;
const askBubble=(who,text)=>{const box=document.querySelector('#case-ask-thread');const node=document.createElement('div');node.className='onboarding-row';node.style.display='block';node.innerHTML=`<div class="label">${esc(who)}</div><div style="white-space:pre-wrap">${esc(text)}</div>`;box.appendChild(node);return node.lastElementChild};
document.querySelector('#case-ask-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),question=form.question.value.trim();if(!question)return;
  button.disabled=true;askBubble('You',question);const out=askBubble('Analyst','Reading the case…');form.reset();
  try{const response=await fetch(`/api/onboarding-v2/cases/${caseId}/ask`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question,conversation_id:askConversation})});
    if(!response.ok)await json(response);
    const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='',answered=false;
    for(;;){const {done,value}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let cut;
      while((cut=buffer.indexOf('\n\n'))>=0){const chunk=buffer.slice(0,cut);buffer=buffer.slice(cut+2);
        for(const line of chunk.split('\n')){if(!line.startsWith('data: '))continue;const e=JSON.parse(line.slice(6));
          if(e.type==='start')askConversation=e.conversation_id;
          else if(e.type==='status'&&!answered)out.textContent=e.text+'…';
          else if(e.type==='answer'||e.type==='rejected'){answered=true;out.textContent=String(e.answer).replace(/\s?\[T\d+\]/g,'')}
          else if(e.type==='error'){answered=true;out.textContent=e.message}}}}
    if(!answered)out.textContent='No answer came back. Try again.'}
  catch(error){out.textContent=`Not answered: ${error.message}`}finally{button.disabled=false}});
const runState={requested:['Waiting to start',''],running:['Running',''],done:['Done','active'],failed:['Failed','blocked'],skipped:['Skipped','']};
const when=t=>t?new Date(t).toLocaleString():'';
const renderWork=w=>{const card=document.querySelector('#case-work'),body=document.querySelector('#case-work-body');card.hidden=!w;if(!w)return;
  if(w.unavailable){body.innerHTML='<div class="warning">The onboarding worker is not set up yet. The daily jobs still move this case on overnight.</div>';return}
  const waiting=w.waiting?`<div class="onboarding-check pending"><strong>${esc((runState[w.waiting.status]||[w.waiting.status])[0])}</strong><span>Asked ${esc(when(w.waiting.requested_at))}.${w.waiting.message?' '+esc(w.waiting.message):''}</span></div>`:'';
  const does=w.run_now_does.length?`Run now does: ${esc(w.run_now_does.join(', '))}.`:'Nothing is waiting on the platform right now; Run now checks again.';
  const runs=w.runs.length?w.runs.map(r=>{const [text,cls]=runState[r.status]||[r.status,''];return `<div class="onboarding-row" style="display:block"><strong>${esc(r.trigger==='schedule'?'Scheduled run':r.trigger==='run_now'?'Run now':'Connection check')}</strong> ${pill(text,cls)} <span class="muted">${esc(when(r.finished_at||r.requested_at))}</span><div class="muted">${esc(r.message)}</div>${r.steps.length?`<ul style="margin:6px 0 0 18px">${r.steps.map(x=>`<li>${esc(x.label)}: ${esc(x.outcome)}${x.detail?' · '+esc(x.detail):''}</li>`).join('')}</ul>`:''}</div>`}).join(''):'<p class="muted">No run yet.</p>';
  body.innerHTML=`${w.window_note?`<div class="warning">${esc(w.window_note)}</div>`:''}${waiting}<p class="muted">${does} Next scheduled run ${esc(when(w.next_scheduled))}.</p><div class="actions"><button id="case-run-now" class="button" type="button"${w.waiting?' disabled':''}>Run now</button></div><h3>Recent runs</h3>${runs}`;
  const button=body.querySelector('#case-run-now'),note=document.querySelector('#case-work-result');
  button.addEventListener('click',async()=>{button.disabled=true;note.textContent='Asking the worker…';try{const r=await post(`/api/onboarding-v2/cases/${caseId}/run-now`,{});note.textContent=r.status==='already_waiting'?'A run is already waiting; it covers this.':r.started?'Started. This page shows the result when it finishes.':(r.message||'Queued for the next scheduled run.');setTimeout(load,1500)}catch(error){note.textContent=`Not started: ${error.message}`;button.disabled=false}})};
const renderHistory=h=>{const card=document.querySelector('#case-history');card.hidden=!h;if(!h)return;const [text,cls]={pass:['Complete','active'],warn:['Complete, with days to check',''],pending:['Filling',''],fail:['Failed','blocked']}[h.status]||[h.status,''];
  const facts=[['From',h.from],['To',h.to],['Trading days loaded',h.days_with_history],['Days still to fill',h.days_left],['Days that do not reconcile',h.gap_days]].filter(([,v])=>v!==undefined&&v!==null);
  document.querySelector('#case-history-body').innerHTML=`${pill(text,cls)}<p>${esc(h.message)}</p><div class="status-row">${facts.map(([k,v])=>`<div class="status-item"><div class="label">${esc(k)}</div><strong>${esc(v)}</strong></div>`).join('')}</div>${h.gaps.length?`<details style="margin-top:10px"><summary>Days to check</summary><ul style="margin:6px 0 0 18px">${h.gaps.map(g=>`<li>${esc(g.date)} · ${esc(g.check)}${g.gap?' · '+esc(g.gap):''}</li>`).join('')}</ul></details>`:''}<div class="muted">Checked ${esc(when(h.checked_at))}</div>`};
const renderTerms=c=>{const card=document.querySelector('#case-terms-edit');const open=c.terms_open&&(c.benchmark_choices||[]).length+(c.broker==='kiwoom_rest'?1:0)>0;card.hidden=!open;if(!open)return;
  benchmarks=c.benchmark_choices||[];const b=c.benchmarks||{};
  document.querySelector('#case-terms-fields').innerHTML=benchmarkFields(b.primary?b.primary.benchmark_id:'',(b.secondary||[]).map(x=>x.benchmark_id))+(c.broker==='kiwoom_rest'?`<label class="status-item"><span class="label">Kiwoom U.S. account</span><select name="kiwoom_include_us"><option value=""${c.kiwoom_us_account===null||c.kiwoom_us_account===undefined?' selected':''}>Platform default</option><option value="true"${c.kiwoom_us_account===true?' selected':''}>Read U.S. stocks and dollar cash too</option><option value="false"${c.kiwoom_us_account===false?' selected':''}>Korean stocks only</option></select></label>`:'')};
let benchmarks=[];
const benchmarkFields=(primary,secondary)=>benchmarks.length?`<label class="status-item"><span class="label">Main benchmark</span><select name="primary_benchmark_id"><option value="">None</option>${benchmarks.map(b=>`<option value="${esc(b.benchmark_id)}"${b.benchmark_id===primary?' selected':''}>${esc(b.name)} (${esc(b.currency)})</option>`).join('')}</select></label><label class="status-item"><span class="label">Other benchmarks (up to 4)</span><select name="secondary_benchmark_ids" multiple size="4">${benchmarks.map(b=>`<option value="${esc(b.benchmark_id)}"${(secondary||[]).includes(b.benchmark_id)?' selected':''}>${esc(b.name)} (${esc(b.currency)})</option>`).join('')}</select></label>`:'';
document.querySelector('#case-terms-form').addEventListener('submit',async event=>{event.preventDefault();const f=event.currentTarget,values=new FormData(f),note=f.querySelector('[role=status]');const body={};if(values.get('primary_benchmark_id')){body.primary_benchmark_id=values.get('primary_benchmark_id');body.secondary_benchmark_ids=values.getAll('secondary_benchmark_ids').filter(x=>x!==body.primary_benchmark_id)}const us=values.get('kiwoom_include_us');if(us)body.kiwoom_include_us=us==='true';note.textContent='Saving…';try{await post(`/api/onboarding-v2/cases/${caseId}/terms`,body);note.textContent='Saved. Review the account terms below before you approve them.';load()}catch(error){note.textContent=`Not saved: ${error.message}`}});
const render=c=>{
  mayApprove=!!c.may_approve;work=c.platform_work;
  renderWork(c.platform_work);
  renderHistory(c.history);
  renderTerms(c);
  document.querySelector('#case-ask').hidden=!c.may_ask;
  document.querySelector('#case-kicker').textContent=`Stage ${c.stage_number} of 4 · ${c.broker_label} · ${c.reporting_currency} reporting`;
  document.querySelector('#case-title').textContent=`${c.display_name} (${c.account})`;
  const portfolio=document.querySelector('#case-portfolio');portfolio.hidden=!c.composition_svg;
  if(c.composition_svg){document.querySelector('#case-description').textContent=c.description||'';const style=document.querySelector('#case-report-style');style.hidden=!c.report_style_label;style.textContent=c.report_style_label?`Reports use the ${c.report_style_label}, under this portfolio's name, with this description and tree.`:'';const box=document.querySelector('#case-tree');box.innerHTML=c.composition_svg;box.firstElementChild.style.minWidth='560px'}
  document.querySelector('#case-next').textContent=c.next_owner_action||(c.status==='cancelled'?'This case was cancelled.':'Nothing needs you right now.');
  document.querySelector('#case-progress').innerHTML=c.progress.map(t=>{const [text,cls]=tileState[t.state];return `<div class="status-item"><div class="label">${esc(t.number)} · ${esc(t.label)}</div>${pill(text,cls)}</div>`}).join('');
  const agent=c.agents||{checks:{}};
  document.querySelector('#case-checks').innerHTML=c.checks.map(k=>{const ev=agent.checks[k.check_id];return `<div class="onboarding-check ${esc(k.status==='pending'?'':k.status)}"><strong>${esc(k.label)}${ev?' '+pill('Checked by agent',''):''}</strong><span>${esc(k.message)}${k.status==='warn'&&k.acknowledged?' Acknowledged.':''}</span>${ev?evidenceDrawer(ev):''}</div>`}).join('')||'<div class="muted">No checks in this stage.</div>';
  renderProposals(agent.security_proposals||[]);
  renderExternal(c,c.external_data);
  renderDesign(c.factsheet_design);
  renderDraft(c,agent.term_draft);
  renderDocuments(c);
  renderReadiness(c.readiness);
  document.querySelector('#case-approvals').innerHTML=c.approvals.map((a,i)=>`<div class="status-item"><div class="label">Approval ${i+1}</div><strong>${esc(a.label)}</strong>${a.approved?pill('Approved','active'):c.awaiting_approval===a.kind?pill('Waiting on you',''):pill('Not yet','blocked')}</div>`).join('');
  const ready=c.may_approve&&c.awaiting_approval==='account_terms';
  const form=document.querySelector('#case-approval-form');
  const acks=()=>c.checks.filter(k=>k.status==='warn').map(k=>`<label style="display:flex;gap:10px;margin-top:12px"><input type="checkbox" name="ack" value="${esc(k.check_id)}" required> <span>I have read the warning on ${esc(k.label)}.</span></label>`).join('');
  const acked=f=>[...f.querySelectorAll('input[name=ack]:checked')].map(x=>x.value);
  if(c.may_approve&&c.awaiting_approval==='historical_reports'){
    const reports=c.reports.length?c.reports.map(r=>`<div class="onboarding-row"><div><strong>${esc(r.period_end)} · ${esc(r.report_type)}</strong><div class="muted">${esc(r.file_name)}</div></div><div class="actions">${r.download_path?`<a class="button" href="${esc(r.download_path)}">Open</a>`:''}</div></div>`).join(''):'<p class="muted">The report run listed no files.</p>';
    form.innerHTML=`<form id="reports-form"><h3>Approve the historical reports</h3><p class="muted">Open each month's reports. Nothing is shown to viewers until you go live.</p>${reports}${acks()}<label style="display:flex;gap:10px;margin-top:12px"><input type="checkbox" name="reviewed" required> <span>I opened and reviewed these reports.</span></label><div class="actions"><button class="button" type="submit">Approve historical reports</button></div></form>`;
    form.querySelector('form').addEventListener('submit',async event=>{event.preventDefault();const f=event.currentTarget,button=f.querySelector('button');button.disabled=true;result.textContent='Recording your approval…';try{await post(`/api/onboarding-v2/cases/${caseId}/approvals/historical_reports`,{terms_sha256:c.awaiting_terms_sha256,acknowledge_warnings:acked(f)});result.textContent='Approved.';load()}catch(error){result.textContent=`Not approved: ${error.message}`;button.disabled=false}})}
  else if(c.may_approve&&c.awaiting_approval==='go_live'){
    form.innerHTML=`<form id="live-form"><h3>Go live</h3><p class="muted">Going live shows ${esc(c.display_name)} to the viewers you give access to and starts its daily updates and month-end reports.</p><div class="actions"><a class="button" href="${esc(c.preview_page)}" target="_blank" rel="noopener">Preview it on the website</a></div>${(c.viewers||[]).length?`<fieldset style="margin-top:14px;border:0;padding:0"><legend class="label">Viewers who can see it</legend>${c.viewers.map(v=>`<label style="display:flex;gap:10px;margin-top:6px"><input type="checkbox" name="viewer" value="${esc(v.object_id)}"${v.has_access?' checked disabled':''}> <span>${esc(v.name)} <span class="muted">${esc(v.email)}${v.has_access?' · already has access':''}</span></span></label>`).join('')}<p class="muted">Ticked viewers get read access when it goes live. You can leave everyone unticked and add access later.</p></fieldset>`:''}${acks()}<label style="display:block;margin-top:16px"><span class="label">Type ${esc(c.go_live_phrase)} to confirm</span><input type="text" name="confirmation" autocomplete="off" spellcheck="false" style="display:block;width:100%;max-width:440px;min-width:0"></label><div class="actions"><button class="button" type="submit" disabled>Go live</button></div></form>`;
    const f=form.querySelector('form'),input=f.querySelector('input[name=confirmation]'),button=f.querySelector('button[type=submit]');
    input.addEventListener('input',()=>{button.disabled=input.value.trim()!==c.go_live_phrase});
    f.addEventListener('submit',async event=>{event.preventDefault();button.disabled=true;result.textContent='Going live…';try{const done=await post(`/api/onboarding-v2/cases/${caseId}/approvals/go_live`,{terms_sha256:c.awaiting_terms_sha256,acknowledge_warnings:acked(f),confirmation:input.value.trim(),viewers:[...f.querySelectorAll('input[name=viewer]:checked:not(:disabled)')].map(x=>x.value)});result.textContent='Live. Opening its page…';window.location.href=done.next_page}catch(error){result.textContent=`Not live: ${error.message}`;button.disabled=false}})}
  else if(ready){const t=c.account_terms,derived=c.source_kind!=='broker_account',warns=c.checks.filter(k=>k.status==='warn'),facts=[[derived?'Portfolio ID':'Account',c.account],['Display name',t.display_name],['Reporting currency',t.reporting_currency],['History from',t.history_start_date],...(c.definition_summary||[]).map(r=>[r.label,r.value]),...(c.benchmarks?[['Benchmarks',[c.benchmarks.primary.name,...c.benchmarks.secondary.map(x=>x.name)].join(', ')]]:[]),...(c.kiwoom_us_account===true||c.kiwoom_us_account===false?[['Kiwoom U.S. account',c.kiwoom_us_account?'Read too':'Not read']]:[])];form.innerHTML=`<form id="terms-form"><h3>${derived?'Approve the portfolio definition':'Approve the account terms'}</h3><div class="status-row">${facts.map(([k,v])=>`<div class="status-item"><div class="label">${esc(k)}</div><strong>${esc(v)}</strong></div>`).join('')}</div>${warns.map(k=>`<label style="display:flex;gap:10px;margin-top:12px"><input type="checkbox" name="ack" value="${esc(k.check_id)}" required> <span>I have read the warning on ${esc(k.label)}.</span></label>`).join('')}<div class="actions"><button class="button" type="submit">${derived?'Approve definition and terms':'Approve account terms'}</button></div></form>`;
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
const reportState={queued:['Check queued',''],found_accounts:['Accounts found','active'],failed:['Check failed','blocked'],blocked:['Check blocked','blocked'],stuck:['Check stuck','blocked'],waiting:['Waiting','']};
const agentReport=r=>{if(!r)return '';const [text,cls]=reportState[r.state]||[r.state,''];return `<div class="onboarding-row" style="display:block;margin:-6px 0 10px 16px"><span class="label">Checked by agent</span> ${pill(text,cls)} <strong>${esc(r.title)}</strong><div class="muted">${esc(r.body)}</div>${r.owner_steps.length?`<ol style="margin:6px 0 0 18px">${r.owner_steps.map(s=>`<li>${esc(s)}</li>`).join('')}</ol>`:''}<div class="muted">${esc(r.reported_at)}</div></div>`};
const keyLine=c=>`<div class="muted" style="margin:-6px 0 10px 16px">Key stored in Key Vault as ${esc(c.key_vault_secret)} (fingerprint ${esc(c.key_fingerprint)})${c.key_stored_at?', saved '+esc(new Date(c.key_stored_at).toLocaleString()):''}.${c.last_checked_at?` Last checked ${esc(new Date(c.last_checked_at).toLocaleString())}${c.last_check_message?': '+esc(c.last_check_message):''}.`:' Not checked yet.'}</div>`;
const lockText={locked:'locked to the platform address',not_locked:'not IP-locked',mismatch:'locked to a different address',unknown_platform_ip:'IP lock recorded'};
const showFields=()=>form.querySelectorAll('[data-broker]').forEach(node=>{node.hidden=node.dataset.broker!==broker.value});
broker.addEventListener('change',showFields);showFields();
const load=()=>fetch('/api/broker-connections').then(json).then(data=>{
  document.querySelector('#bc-list').innerHTML=data.connections.length?data.connections.map(c=>row(`${c.broker_label} · ${c.display_name}`,[c.environment==='mock'?'Mock environment':null,c.expires_on?`expires ${c.expires_on}`:null,lockText[c.ip_lock]].filter(Boolean).join(' · '),pill(c.health_label,healthClass[c.health])+(c.flex_source_id?`<button class="button" type="button" data-check="${esc(c.flex_source_id)}">Check now</button>`:'')+(c.can_check_now?`<button class="button" type="button" data-kiwoom-check="${esc(c.connection_id)}">Check now</button>`:''))+keyLine(c)+agentReport((data.agent_reports||{})[c.connection_id])).join(''):'<p class="muted">No connections yet.</p>';
  const address=document.querySelector('#bc-address-ip'),copy=document.querySelector('#bc-address-copy');if(data.platform_ip){address.textContent=data.platform_ip;copy.hidden=false;copy.onclick=()=>navigator.clipboard&&navigator.clipboard.writeText(data.platform_ip).then(()=>{copy.textContent='Copied'})}
  document.querySelectorAll('[data-kiwoom-check]').forEach(b=>b.addEventListener('click',async()=>{b.disabled=true;b.textContent='Checking…';try{const r=await post(`/api/broker-connections/${encodeURIComponent(b.dataset.kiwoomCheck)}/check`,{});b.textContent=r.started?'Check started':r.status==='already_waiting'?'Check already waiting':'Check queued';setTimeout(load,60000)}catch(error){b.textContent='Not checked';b.title=error.message;b.disabled=false}}));
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

PORTFOLIO_DESCRIPTION = ("Build a portfolio from live accounts, portfolios and market indices. "
                         "Starting it opens a private case; nothing reaches viewers until you approve.")

PORTFOLIO_CONTENT = """<style>
#pb-form .status-item input,#pb-form .status-item select{width:100%;min-width:0}
.pb-sleeve{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,1fr) minmax(0,1.3fr) auto;gap:12px;align-items:end;margin-top:10px;padding:12px 14px;background:var(--surface-soft);border:1px solid var(--line);border-radius:12px}
.pb-sleeve select,.pb-sleeve input{width:100%;min-width:0}
.pb-sleeve input[aria-invalid=true]{border-color:#d9485f}
.button.pb-quiet,.button.pb-quiet:hover{color:var(--ink-soft);background:transparent;border:1px solid var(--line-strong);box-shadow:none;transform:none}
.button.pb-chosen{outline:2px solid var(--brand);outline-offset:2px}
.pb-tree{max-width:760px;margin-top:14px;overflow-x:auto}.pb-tree svg{display:block;min-width:560px}
@media (max-width:650px){.pb-sleeve{grid-template-columns:1fr 1fr}.pb-sleeve label:first-child{grid-column:1/-1}}
</style>
<div class="page-view active" data-section="account-onboarding">
<div class="card wide"><div class="label">Start from</div><p class="muted">A blank composite, a filter on one account, or a copy of a live portfolio such as Alpha World MS or T. A copy keeps the sleeves and their weights; give it its own name, ID and description.</p><div id="pb-starts" class="actions"><div class="muted">Loading…</div></div><div id="pb-start-note" class="muted" style="margin-top:10px"></div></div>
<form id="pb-form" autocomplete="off" style="display:contents">
<div class="card wide"><div class="label">About the portfolio</div>
<div class="status-row">
<label class="status-item"><span class="label">Kind</span><select name="kind" id="pb-kind"><option value="composite_portfolio">Fixed-unit composite</option><option value="filtered_portfolio">Filtered account</option></select></label>
<label class="status-item"><span class="label">Name</span><input type="text" name="display_name" required minlength="3" maxlength="160" placeholder="e.g. Alpha World Asia"></label>
<label class="status-item"><span class="label">Portfolio ID</span><input type="text" name="subject_id" required pattern="[A-Za-z0-9][A-Za-z0-9\\-]{2,63}" maxlength="64" placeholder="Made from the name" title="Letters, digits and hyphens"></label>
<label class="status-item"><span class="label">Reporting currency</span><input type="text" name="reporting_currency" required pattern="[A-Za-z]{3}" maxlength="3" value="USD"></label>
<label class="status-item"><span class="label" id="pb-date-label">Starts at 100 on</span><input type="date" name="start_date" required></label>
</div>
<label style="display:block;margin-top:16px"><span class="label">Description</span><textarea name="description" rows="3" required minlength="10" maxlength="600" placeholder="What the portfolio holds and why. Its reports and factsheets show this text."></textarea></label>
</div>
<div class="card wide" data-kind="composite_portfolio"><div class="label">Sleeves</div><p class="muted">Give each sleeve a share of the starting value. Long and short sleeves can add up to more than 100%; cash makes up the rest, and negative cash means the portfolio borrows. Alpha World MS is 91% and 45% long, 49% and 45% short, with 58% cash.</p><div id="pb-sleeves"></div><div class="actions"><button class="button pb-quiet" type="button" id="pb-add">Add sleeve</button></div><div id="pb-totals" class="status-row"></div></div>
<div class="card wide" data-kind="composite_portfolio"><div class="label">Reports</div><p class="muted">An HKD composite reports in the Alpha World MS or T design: the same factsheet and presentation, under this portfolio's name, with its description and composition tree. A USD composite uses the standard design.</p><div class="status-row"><label class="status-item"><span class="label">Report design</span><select name="report_style"><option value="">Standard design (USD)</option><option value="alpha-world-ms">Alpha World MS design</option><option value="alpha-world-t">Alpha World T design</option></select></label></div></div>
<div class="card wide" data-kind="filtered_portfolio" hidden><div class="label">Filter</div><p class="muted">Keep one account's holdings except what you exclude, and rebuild its cash in the currencies you allow.</p>
<div class="status-row">
<label class="status-item"><span class="label">Account</span><select name="source_account_id" required></select></label>
<label class="status-item"><span class="label">Excluded countries</span><input type="text" name="excluded_countries" placeholder="Taiwan, Japan"></label>
<label class="status-item"><span class="label">Excluded exchanges</span><input type="text" name="excluded_exchanges" placeholder="TWSE, TPEX"></label>
<label class="status-item"><span class="label">Excluded currencies</span><input type="text" name="excluded_currencies" placeholder="TWD"></label>
<label class="status-item"><span class="label">Cash currencies</span><input type="text" name="allowed_cash_currencies" required placeholder="HKD, USD"></label>
<label class="status-item"><span class="label">Opening cash</span><input type="text" name="opening_cash" required placeholder="HKD=484300, USD=0"></label>
</div>
<label style="display:flex;gap:10px;align-items:flex-start;margin-top:14px"><input type="checkbox" name="include_actual_interest" checked> <span>Include interest the broker actually posted in an allowed cash currency. No assumed interest is added.</span></label></div>
<div class="card wide"><div class="label">Composition</div><p class="muted">This tree goes on the case page and, in the Alpha World designs, on the portfolio's factsheet and presentation.</p><div id="pb-tree" class="pb-tree"><div class="muted">Add a sleeve to see the tree.</div></div></div>
<div class="card wide"><div class="label">Check and start</div><p class="muted">Check runs the same rules as starting, without saving anything. Starting opens a private case where you approve the definition.</p><div id="pb-check" class="onboarding-checks"></div><div class="actions"><button class="button pb-quiet" type="button" id="pb-run-check">Check</button><button class="button" type="submit" id="pb-submit">Start onboarding</button></div><div id="pb-status" class="muted" role="status" aria-live="polite"></div></div>
</form>
</div>"""

PORTFOLIO_SCRIPT = "<script>\n(()=>{" + _COMMON_SCRIPT + r"""
const form=document.querySelector('#pb-form'),kind=form.elements.kind,rows=document.querySelector('#pb-sleeves'),tree=document.querySelector('#pb-tree'),checks=document.querySelector('#pb-check'),status=document.querySelector('#pb-status');
let catalogue={sleeves:[],indices:[],starting_points:[]},idTouched=false,timer=null,sequence=0;
const csvList=value=>String(value||'').split(',').map(item=>item.trim()).filter(Boolean);
const codes=value=>csvList(value).map(item=>item.toUpperCase());
const amount=value=>Number(String(value??'').replace(/[\s%]/g,'').replace(/,(?=\d{3}(\D|$))/g,''));
const parseCash=value=>{const result={};String(value||'').split(/[,;](?=\s*[A-Za-z])/).map(item=>item.trim()).filter(Boolean).forEach(item=>{const match=item.match(/^([A-Za-z]{3})\s*[=:\s]\s*(-?[\d,]*\.?\d+)$/);if(!match)throw new Error(`Write opening cash as HKD=484300, USD=0 (not "${item}")`);result[match[1].toUpperCase()]=amount(match[2])});return result};
const cashText=cash=>Object.entries(cash||{}).map(([code,value])=>`${code}=${value}`).join(', ');
const slug=text=>String(text).normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-+|-+$/g,'').slice(0,64);
const pct=value=>`${Number(value).toFixed(1)}%`;
const today=()=>new Date().toISOString().slice(0,10);
const option=(item,selected)=>`<option value="${esc(item.component_id)}"${item.component_id===selected?' selected':''}${item.disabled?' disabled':''}>${esc(item.name)} · ${esc(item.currency||'')}${item.note?' · '+esc(item.note):''}</option>`;
const sleeveOptions=selected=>{const indices=catalogue.indices.map(i=>({...i,disabled:!i.first_date,note:i.first_date?`prices from ${i.first_date}`:'no prices yet'}));const known=[...catalogue.sleeves,...indices].some(i=>i.component_id===selected);const group=(label,items)=>items.length?`<optgroup label="${esc(label)}">${items.map(i=>option(i,selected)).join('')}</optgroup>`:'';return '<option value="">Choose a sleeve</option>'+(selected&&!known?option({component_id:selected,name:selected,note:'not live'},selected):'')+group('Accounts',catalogue.sleeves.filter(s=>s.kind==='account'))+group('Portfolios',catalogue.sleeves.filter(s=>s.kind==='portfolio'))+group('Market indices',indices)};
const addRow=(sleeve={})=>{const div=document.createElement('div');div.className='pb-sleeve';div.innerHTML=`<label><span class="label">Sleeve</span><select name="sleeve_component" aria-label="Sleeve">${sleeveOptions(sleeve.component_id||'')}</select></label><label><span class="label">Side</span><select name="sleeve_direction" aria-label="Long or short"><option value="long">Long</option><option value="short"${sleeve.direction==='short'?' selected':''}>Short</option></select></label><label><span class="label">% of starting value</span><input type="text" name="sleeve_exposure" inputmode="decimal" aria-label="Percent of starting value" placeholder="e.g. 45" value="${esc(sleeve.exposure_percent??'')}"></label><button class="button pb-quiet" type="button" data-remove aria-label="Remove this sleeve">Remove</button>`;rows.append(div)};
const sleeves=()=>[...rows.querySelectorAll('.pb-sleeve')].map(row=>{const input=row.querySelector('[name=sleeve_exposure]'),value=amount(input.value),bad=input.value.trim()!==''&&!(value>0&&value<=1000);input.setAttribute('aria-invalid',bad?'true':'false');return {component_id:row.querySelector('[name=sleeve_component]').value,direction:row.querySelector('[name=sleeve_direction]').value,exposure_percent:value}}).filter(s=>s.component_id&&s.exposure_percent>0&&s.exposure_percent<=1000);
const showKind=()=>{const value=kind.value;form.querySelectorAll('[data-kind]').forEach(card=>{const active=card.dataset.kind===value;card.hidden=!active;card.querySelectorAll('input,select,textarea,button').forEach(field=>{field.disabled=!active})});document.querySelector('#pb-date-label').textContent=value==='composite_portfolio'?'Starts at 100 on':'History from'};
const body=()=>{const values=new FormData(form),b={kind:kind.value,subject_id:String(values.get('subject_id')||'').trim(),display_name:String(values.get('display_name')||'').trim(),description:String(values.get('description')||'').trim(),reporting_currency:String(values.get('reporting_currency')||'').trim().toUpperCase(),start_date:values.get('start_date')||''};
  if(b.kind==='composite_portfolio')Object.assign(b,{sleeves:sleeves(),report_style:values.get('report_style')||null});
  else Object.assign(b,{source_account_id:values.get('source_account_id')||null,excluded_countries:csvList(values.get('excluded_countries')),excluded_exchanges:codes(values.get('excluded_exchanges')),excluded_currencies:codes(values.get('excluded_currencies')),allowed_cash_currencies:codes(values.get('allowed_cash_currencies')),opening_cash:parseCash(values.get('opening_cash')),include_actual_interest:form.elements.include_actual_interest.checked});
  return b};
const showTotals=()=>{const box=document.querySelector('#pb-totals');if(kind.value!=='composite_portfolio'){box.innerHTML='';return}const chosen=sleeves(),long=chosen.filter(s=>s.direction==='long').reduce((t,s)=>t+s.exposure_percent,0),short=chosen.filter(s=>s.direction==='short').reduce((t,s)=>t+s.exposure_percent,0),cash=100-long+short;box.innerHTML=[['Long',pct(long)],['Short',pct(short)],['Cash',cash<0?`${pct(cash)} (borrowed)`:pct(cash)],['Gross exposure',pct(long+short)]].map(([k,v])=>`<div class="status-item"><div class="label">${esc(k)}</div><strong>${esc(v)}</strong></div>`).join('')};
const preview=()=>{clearTimeout(timer);timer=setTimeout(async()=>{let b;try{b=body()}catch(error){tree.innerHTML=`<div class="muted">${esc(error.message)}</div>`;return}if(b.kind==='composite_portfolio'&&!b.sleeves.length){tree.innerHTML='<div class="muted">Add a sleeve to see the tree.</div>';return}if(!/^[A-Z]{3}$/.test(b.reporting_currency))b.reporting_currency='USD';b.start_date=b.start_date||today();const mine=++sequence;try{const result=await post('/api/onboarding-v2/portfolios/preview',b);if(mine===sequence)tree.innerHTML=result.svg}catch(error){if(mine===sequence)tree.innerHTML=`<div class="muted">${esc(error.message)}</div>`}},300)};
const changed=()=>{checks.innerHTML='';status.textContent='';showTotals();preview()};
const fill=(form_,label)=>{kind.value=form_.kind;showKind();rows.innerHTML='';
  if(form_.kind==='composite_portfolio')(form_.sleeves||[]).forEach(addRow);
  const set=(name,value)=>{const field=form.elements[name];if(field&&value!==undefined&&value!==null)field.value=value};
  set('reporting_currency',form_.reporting_currency||'USD');set('start_date',form_.start_date||'');if(form_.kind==='composite_portfolio')set('report_style',form_.report_style||'');
  if(form_.kind==='filtered_portfolio'){set('source_account_id',form_.source_account_id||'');set('excluded_countries',(form_.excluded_countries||[]).join(', '));set('excluded_exchanges',(form_.excluded_exchanges||[]).join(', '));set('excluded_currencies',(form_.excluded_currencies||[]).join(', '));set('allowed_cash_currencies',(form_.allowed_cash_currencies||[]).join(', '));set('opening_cash',cashText(form_.opening_cash));form.elements.include_actual_interest.checked=form_.include_actual_interest!==false}
  ['display_name','subject_id','description'].forEach(name=>{form.elements[name].value=''});idTouched=false;
  document.querySelector('#pb-start-note').textContent=label?`Copied from ${label}. Give the new portfolio its own name, ID and description.`:'';
  changed()};
const blank={composite:{kind:'composite_portfolio',reporting_currency:'USD',sleeves:[{direction:'long'},{direction:'short'}]},filtered:{kind:'filtered_portfolio',reporting_currency:'USD',allowed_cash_currencies:[],opening_cash:{}}};
const choose=(button,form_,label)=>{document.querySelectorAll('#pb-starts .button').forEach(b=>b.classList.toggle('pb-chosen',b===button));fill(form_,label)};
const load=()=>fetch('/api/onboarding-v2/portfolio-builder').then(json).then(data=>{catalogue=data;
  form.elements.source_account_id.innerHTML='<option value="">Choose an account</option>'+data.sleeves.filter(s=>s.kind==='account').map(s=>`<option value="${esc(s.component_id)}">${esc(s.name)} · ${esc(s.currency)}</option>`).join('');
  const starts=document.querySelector('#pb-starts');starts.innerHTML=`<button class="button pb-quiet" type="button" data-start="composite">Blank composite</button><button class="button pb-quiet" type="button" data-start="filtered">Filter one account</button>`+data.starting_points.map((p,i)=>`<button class="button pb-quiet" type="button" data-copy="${i}">Copy ${esc(p.display_name)}</button>`).join('');
  starts.querySelectorAll('[data-start]').forEach(b=>b.addEventListener('click',()=>choose(b,blank[b.dataset.start],'')));
  starts.querySelectorAll('[data-copy]').forEach(b=>b.addEventListener('click',()=>{const p=data.starting_points[Number(b.dataset.copy)];choose(b,p.form,p.display_name)}));
  const wanted=new URLSearchParams(window.location.search).get('from'),index=data.starting_points.findIndex(p=>p.subject_id===wanted);
  if(index>=0)starts.querySelector(`[data-copy="${index}"]`).click();else starts.querySelector('[data-start=composite]').click();
}).catch(error=>{document.querySelector('#pb-starts').innerHTML=`<div class="warning">This page could not load: ${esc(error.message)}</div>`});
const report=result=>{const line=(cls,title,text)=>`<div class="onboarding-check ${cls}"><strong>${esc(title)}</strong><span>${esc(text)}</span></div>`;checks.innerHTML=(result.ok?line('pass','Ready to start','Every rule the case checks passes.'):'')+result.problems.map(p=>line('fail','Fix before starting',p)).join('')+result.notes.map(n=>line('warn','Note',n)).join('')+(result.summary||[]).map(r=>line('',r.label,r.value)).join('');if(result.svg)tree.innerHTML=result.svg};
document.querySelector('#pb-add').addEventListener('click',()=>{addRow({direction:'long'});rows.lastElementChild.querySelector('select').focus()});
rows.addEventListener('click',event=>{const button=event.target.closest('[data-remove]');if(button){button.closest('.pb-sleeve').remove();changed()}});
form.addEventListener('input',event=>{if(event.target.name==='display_name'&&!idTouched)form.elements.subject_id.value=slug(event.target.value);if(event.target.name==='subject_id')idTouched=event.target.value.trim()!=='';changed()});
kind.addEventListener('change',()=>{showKind();rows.innerHTML='';if(kind.value==='composite_portfolio')blank.composite.sleeves.forEach(addRow);document.querySelectorAll('#pb-starts .button').forEach(b=>b.classList.remove('pb-chosen'));changed()});
document.querySelector('#pb-run-check').addEventListener('click',async()=>{const button=document.querySelector('#pb-run-check');if(!form.reportValidity())return;button.disabled=true;status.textContent='Checking…';try{report(await post('/api/onboarding-v2/portfolios/check',body()));status.textContent=''}catch(error){checks.innerHTML='';status.textContent=`Not checked: ${error.message}`}finally{button.disabled=false}});
form.addEventListener('submit',async event=>{event.preventDefault();const button=document.querySelector('#pb-submit');if(!form.reportValidity())return;button.disabled=true;status.textContent='Opening the case…';try{const result=await post('/api/onboarding-v2/portfolios',body());window.location.href=result.next_page}catch(error){status.textContent=`Not started: ${error.message}`;button.disabled=false}});
showKind();load();
})();
</script>"""
