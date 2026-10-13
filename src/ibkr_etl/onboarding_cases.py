"""The four-stage case behind the new account onboarding.

One broker account, or one derived portfolio (a filtered account like China &
Hong Kong, or a fixed-unit composite like Alpha World MS and T), becomes one
case. The platform does the work in each stage and records checks; the owner
makes three decisions:

1. Request: the owner picks one discovered account and adds it, or describes
   a derived portfolio whose sleeves are all live.
2. Load and reconcile: holdings, cash and history load, every security is
   mapped, NAV reconciles. A derived portfolio instead gets a dry run of its
   calculation over the full history. Owner approval 1: the account terms
   (for a derived portfolio, its exact definition).
3. Build history: historical reports are built and reviewed. Owner approval 2:
   the historical reports. Owner approval 3: go live, after the website
   preview, by typing the confirmation phrase.
4. First month-end: the first scheduled month-end release completes, then
   the case closes.

Everything here is pure; the website and jobs persist what it returns.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping


STAGES = ("request", "load_and_reconcile", "build_history", "first_month_end", "closed")
STAGE_LABELS = {
    "request": "Request",
    "load_and_reconcile": "Load and reconcile",
    "build_history": "Build history",
    "first_month_end": "First month-end",
    "closed": "Closed",
}
# Only one account at a time is onboarded: a new case may start once every
# other case has gone live (or was cancelled).
PRE_LIVE_STAGES = ("request", "load_and_reconcile", "build_history")

SOURCE_KINDS = ("broker_account", "filtered_portfolio", "composite_portfolio")
DERIVED_SOURCE_KINDS = ("filtered_portfolio", "composite_portfolio")
SOURCE_LABELS = {
    "filtered_portfolio": "Filtered portfolio",
    "composite_portfolio": "Fixed-unit composite",
}

STAGE_CHECKS: Mapping[str, tuple[str, ...]] = {
    "request": ("connection_sees_account", "account_not_already_onboarded"),
    "load_and_reconcile": (
        "holdings_and_cash_loaded",
        "transactions_loaded",
        "securities_mapped",
        "nav_reconciled",
    ),
    "build_history": ("history_built", "reports_rendered", "agent_review_complete"),
    "first_month_end": ("first_month_end_released",),
    "closed": (),
}
# A derived portfolio has no broker to load from; its first two stages check
# its sleeves and dry-run the calculation instead.
DERIVED_STAGE_CHECKS: Mapping[str, tuple[str, ...]] = {
    **STAGE_CHECKS,
    "request": ("sleeves_are_live", "subject_id_is_free"),
    "load_and_reconcile": ("sleeves_cover_history", "calculation_dry_run"),
}
CHECK_LABELS = {
    "connection_sees_account": "Connection can see the account",
    "account_not_already_onboarded": "Account is not already on the platform",
    "holdings_and_cash_loaded": "Holdings and cash today",
    "transactions_loaded": "Transaction history",
    "securities_mapped": "Every security is identified",
    "nav_reconciled": "NAV reconciliation",
    "history_built": "Daily history built",
    "reports_rendered": "Monthly reports rendered",
    "agent_review_complete": "Agent team review",
    "first_month_end_released": "First month-end released",
    "sleeves_are_live": "Every sleeve is live",
    "subject_id_is_free": "Portfolio ID is not already used",
    "sleeves_cover_history": "Sleeves and prices cover the history",
    "calculation_dry_run": "Calculation dry run",
}
STAGE_EXIT_APPROVALS: Mapping[str, tuple[str, ...]] = {
    "request": (),
    "load_and_reconcile": ("account_terms",),
    "build_history": ("historical_reports", "go_live"),
    "first_month_end": (),
    "closed": (),
}
APPROVALS = ("account_terms", "historical_reports", "go_live")
APPROVAL_LABELS = {
    "account_terms": "Account terms",
    "historical_reports": "Historical reports",
    "go_live": "Go live",
}
CHECK_STATES = ("pending", "pass", "warn", "fail")


@dataclass(frozen=True)
class OnboardingCase:
    case_id: str
    broker: str | None
    broker_account_id: str | None
    stage: str = "request"
    cancelled: bool = False
    checks: Mapping[str, str] = field(default_factory=dict)
    approvals: frozenset[str] = frozenset()
    acknowledged_warnings: frozenset[str] = frozenset()
    source_kind: str = "broker_account"
    target_subject_id: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in STAGES:
            raise ValueError(f"Unknown onboarding stage: {self.stage}")
        if self.source_kind not in SOURCE_KINDS:
            raise ValueError(f"Unknown onboarding source: {self.source_kind}")
        if (self.source_kind == "broker_account") != bool(self.broker and self.broker_account_id):
            raise ValueError("A broker account case needs its broker and account, and only it has them")
        unknown = set(self.approvals) - set(APPROVALS)
        if unknown:
            raise ValueError(f"Unknown approvals: {sorted(unknown)}")
        for check_id, state in self.checks.items():
            if state not in CHECK_STATES:
                raise ValueError(f"Invalid state for {check_id}: {state}")


@dataclass(frozen=True)
class CaseEvent:
    event_type: str
    from_stage: str
    to_stage: str
    detail: Mapping[str, object] = field(default_factory=dict)


def checks_for(source_kind: str, stage: str) -> tuple[str, ...]:
    table = DERIVED_STAGE_CHECKS if source_kind in DERIVED_SOURCE_KINDS else STAGE_CHECKS
    return table[stage]


def stage_check_states(case: OnboardingCase) -> dict[str, str]:
    return {check_id: case.checks.get(check_id, "pending") for check_id in checks_for(case.source_kind, case.stage)}


def case_status(case: OnboardingCase) -> str:
    """platform_working, waiting_on_owner, blocked, live, closed or cancelled."""

    if case.cancelled:
        return "cancelled"
    if case.stage == "closed":
        return "closed"
    states = stage_check_states(case)
    if "fail" in states.values():
        return "blocked"
    if "pending" in states.values():
        return "live" if case.stage == "first_month_end" else "platform_working"
    if missing_approvals(case):
        return "waiting_on_owner"
    return "live" if case.stage == "first_month_end" else "platform_working"


def missing_approvals(case: OnboardingCase) -> tuple[str, ...]:
    return tuple(item for item in STAGE_EXIT_APPROVALS[case.stage] if item not in case.approvals)


def unacknowledged_warnings(case: OnboardingCase) -> tuple[str, ...]:
    return tuple(
        check_id
        for check_id, state in stage_check_states(case).items()
        if state == "warn" and check_id not in case.acknowledged_warnings
    )


def next_owner_action(case: OnboardingCase) -> str | None:
    """One plain sentence for the case page, or None when nothing needs the owner."""

    status = case_status(case)
    if status in {"cancelled", "closed", "platform_working", "live"}:
        return None
    if status == "blocked":
        failed = [CHECK_LABELS[c] for c, s in stage_check_states(case).items() if s == "fail"]
        return f"Look at the failed check: {failed[0]}."
    pending = missing_approvals(case)[0]
    if pending == "account_terms" and case.source_kind in DERIVED_SOURCE_KINDS:
        return "Approve the portfolio definition and terms."
    return {
        "account_terms": "Approve the account terms.",
        "historical_reports": "Approve the historical reports in the Monthly reports tab.",
        "go_live": "Preview the account on the website, then type the confirmation phrase to go live.",
    }[pending]


def go_live_phrase(broker_account_display: str) -> str:
    return f"GO LIVE {broker_account_display.strip().upper()}"


def terms_digest(terms: Mapping[str, object]) -> str:
    """Hash of exactly what the owner approved, stored with the approval."""

    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_check(case: OnboardingCase, check_id: str, state: str) -> OnboardingCase:
    if case.cancelled or case.stage == "closed":
        raise ValueError("The case is no longer open")
    if check_id not in checks_for(case.source_kind, case.stage):
        raise ValueError(f"{check_id} is not a check for the {STAGE_LABELS[case.stage]} stage")
    if state not in CHECK_STATES:
        raise ValueError(f"Invalid check state: {state}")
    checks = dict(case.checks)
    checks[check_id] = state
    return replace(case, checks=checks)


def approve(
    case: OnboardingCase,
    approval: str,
    *,
    acknowledge_warnings: Iterable[str] = (),
    confirmation: str | None = None,
    broker_account_display: str | None = None,
) -> tuple[OnboardingCase, CaseEvent]:
    """Record one owner approval once its stage's checks allow it."""

    if approval not in APPROVALS:
        raise ValueError(f"Unknown approval: {approval}")
    if case.cancelled or case.stage == "closed":
        raise ValueError("The case is no longer open")
    if approval not in STAGE_EXIT_APPROVALS[case.stage]:
        raise ValueError(f"{APPROVAL_LABELS[approval]} cannot be approved in the {STAGE_LABELS[case.stage]} stage")
    if approval in case.approvals:
        raise ValueError(f"{APPROVAL_LABELS[approval]} is already approved")
    states = stage_check_states(case)
    if any(state in {"pending", "fail"} for state in states.values()):
        raise ValueError("Every check for this stage must finish without failing first")
    acknowledged = frozenset(case.acknowledged_warnings) | frozenset(acknowledge_warnings)
    outstanding = [c for c, s in states.items() if s == "warn" and c not in acknowledged]
    if outstanding:
        raise ValueError("Acknowledge each warning before approving: " + ", ".join(outstanding))
    if approval == "go_live":
        if "historical_reports" not in case.approvals:
            raise ValueError("Approve the historical reports before going live")
        if not broker_account_display:
            raise ValueError("The account is required to check the confirmation phrase")
        if (confirmation or "").strip() != go_live_phrase(broker_account_display):
            raise ValueError("Type the exact confirmation phrase to go live")
    updated = replace(case, approvals=case.approvals | {approval}, acknowledged_warnings=acknowledged)
    return updated, CaseEvent("approved", case.stage, case.stage, {"approval": approval})


def advance(case: OnboardingCase) -> tuple[OnboardingCase, CaseEvent | None]:
    """Move to the next stage when its checks pass and approvals are in.

    Returns the case unchanged and no event when it cannot move yet.
    """

    if case.cancelled or case.stage == "closed":
        return case, None
    states = stage_check_states(case)
    if any(state in {"pending", "fail"} for state in states.values()):
        return case, None
    if unacknowledged_warnings(case) or missing_approvals(case):
        return case, None
    following = STAGES[STAGES.index(case.stage) + 1]
    return replace(case, stage=following), CaseEvent("advanced", case.stage, following)


def cancel(case: OnboardingCase, *, reason: str) -> tuple[OnboardingCase, CaseEvent]:
    if case.cancelled or case.stage == "closed":
        raise ValueError("The case is no longer open")
    if case.stage not in PRE_LIVE_STAGES:
        raise ValueError("A live account is paused from its account page, not cancelled")
    text = " ".join(str(reason or "").split())
    if not 3 <= len(text) <= 500:
        raise ValueError("Give a short reason for cancelling")
    return replace(case, cancelled=True), CaseEvent("cancelled", case.stage, case.stage, {"reason": text})


def assert_can_start(
    *,
    open_cases: Iterable[OnboardingCase],
    broker: str | None = None,
    broker_account_id: str | None = None,
    target_subject_id: str | None = None,
) -> None:
    """One account at a time, and never the same account or portfolio twice."""

    for other in open_cases:
        if other.cancelled:
            continue
        if broker and other.broker == broker and other.broker_account_id == broker_account_id:
            raise ValueError("This account already has an onboarding case")
        if target_subject_id and other.target_subject_id == target_subject_id:
            raise ValueError("This portfolio ID already has an onboarding case")
        if other.stage in PRE_LIVE_STAGES:
            raise ValueError(
                "Another account is still being onboarded; it must go live or be cancelled first"
            )


def progress(case: OnboardingCase) -> list[dict[str, str]]:
    """The four progress tiles on the case page."""

    current = STAGES.index(case.stage)
    tiles = []
    for index, stage in enumerate(STAGES[:-1]):
        if case.cancelled and index == current:
            state = "cancelled"
        elif index < current or case.stage == "closed":
            state = "done"
        elif index == current:
            state = "current"
        else:
            state = "not_started"
        tiles.append({"stage": stage, "label": STAGE_LABELS[stage], "number": str(index + 1), "state": state})
    return tiles
