from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any, Mapping, Sequence

from .market_calendar import (
    BENCHMARK_MARKETS, CALENDAR_VERSION, CalendarCoverageError,
    benchmark_session_on_or_before,
)


REQUIRED_TEMPLATE_ASSETS = {
    ("factsheet", "template"),
    ("presentation", "template"),
    ("factsheet", "completed_example"),
}
PREVIEW_GATE = "website_preview_validated"
ACTIVATION_GATE = "owner_activation"
MAXIMUM_BENCHMARK_STALENESS_DAYS = 4


def _date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _date_text(value: object) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def preview_evidence_sha256(evidence: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(evidence).encode("utf-8")).hexdigest()


def _benchmark_freshness(row: Mapping[str, Any], cutoff: date | None) -> dict[str, Any]:
    """Preserve bounded lag; extend it only for an evidenced exchange closure.

    This validates dates, never creates or carries a price observation. A long
    closure exception requires the exact last session, not any older price.
    """
    observed = _date(row.get("latest_comparison_date"))
    age = (cutoff - observed).days if cutoff and observed else None
    benchmark_id = str(row.get("benchmark_id") or "").upper()
    result = {"staleness_days": age, "freshness_passed": False,
              "freshness_policy": "bounded-calendar-lag",
              "expected_exchange_session": None, "calendar_version": None}
    if cutoff is None or observed is None or age < 0:
        return result
    if benchmark_id in BENCHMARK_MARKETS:
        result["calendar_version"] = CALENDAR_VERSION
        try:
            expected = benchmark_session_on_or_before(benchmark_id, cutoff)
        except CalendarCoverageError:
            result["freshness_policy"] = "calendar-unverified"
            return result
        result["expected_exchange_session"] = expected.isoformat()
        if age > MAXIMUM_BENCHMARK_STALENESS_DAYS:
            result["freshness_policy"] = "exact-last-session-during-closure"
            result["freshness_passed"] = observed == expected
            return result
    result["freshness_passed"] = age <= MAXIMUM_BENCHMARK_STALENESS_DAYS
    return result


def build_physical_preview_validation(
    *,
    subject: Mapping[str, Any],
    nav: Mapping[str, Any],
    positions: Mapping[str, Any],
    benchmarks: Sequence[Mapping[str, Any]],
    templates: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build deterministic, integrity-bindable evidence for owner preview sign-off."""

    results: list[dict[str, Any]] = []

    def record(check_id: str, passed: bool, message: str, evidence: Any) -> None:
        results.append(
            {
                "check_id": check_id,
                "status": "pass" if passed else "fail",
                "message": message,
                "evidence": evidence,
            }
        )

    subject_type = str(subject.get("subject_type") or "")
    metadata = subject.get("metadata_json") or subject.get("configuration_json") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    administrative_master = bool(
        subject_type == "master_account"
        and isinstance(metadata, Mapping)
        and metadata.get("reporting_mode") == "administrative_only"
    )
    contract_fields = {
        "subject_id": subject.get("subject_id"),
        "subject_type": subject_type,
        "display_name": subject.get("display_name"),
        "reporting_currency": subject.get("reporting_currency"),
        "history_start_date": _date_text(subject.get("history_start_date")),
    }
    contract_complete = subject_type in {
        "physical_account", "master_account", "filtered_portfolio",
        "composite_portfolio",
    } and all(contract_fields.values())
    record(
        "subject_contract_complete",
        contract_complete,
        "The subject reporting contract is complete."
        if contract_complete
        else "The subject reporting contract is incomplete or has an unsupported type.",
        contract_fields,
    )

    start = _date(subject.get("history_start_date"))
    first_nav = _date(nav.get("first_date"))
    latest_nav = _date(nav.get("latest_date"))
    observations = int(nav.get("observations") or 0)
    nav_complete = bool(administrative_master or (
        start
        and first_nav
        and latest_nav
        and first_nav <= start <= latest_nav
        and observations > 0
    ))
    record(
        "nav_history_covered",
        nav_complete,
        "Investment NAV is not applicable to this administrative-only master."
        if administrative_master
        else "NAV history covers the reporting start and has validated observations."
        if nav_complete
        else "NAV history does not cover the approved reporting period.",
        {
            "reporting_start": _date_text(start),
            "first_nav": _date_text(first_nav),
            "latest_nav": _date_text(latest_nav),
            "observations": observations,
        },
    )

    latest_position = _date(positions.get("latest_date"))
    position_rows = int(positions.get("rows") or 0)
    position_required = subject_type != "composite_portfolio" and not administrative_master
    positions_aligned = bool(
        not position_required
        or (
            latest_nav
            and latest_position
            and latest_position == latest_nav
            and position_rows > 0
        )
    )
    record(
        "latest_positions_aligned",
        positions_aligned,
        (
            "Positions are not applicable to this administrative-only master."
            if administrative_master
            else "Component exposure replaces a position snapshot for this fixed-unit composite."
            if not position_required
            else "The latest NAV and position snapshot use the same date."
        )
        if positions_aligned
        else "The latest NAV does not have a same-date position snapshot.",
        {
            "latest_nav": _date_text(latest_nav),
            "latest_positions": _date_text(latest_position),
            "position_rows": position_rows,
            "position_required": position_required,
        },
    )

    benchmark_rows = [
        {
            "benchmark_id": row.get("benchmark_id"),
            "mapping_type": row.get("mapping_type"),
            "latest_comparison_date": _date_text(
                row.get("latest_comparison_date")
            ),
            "comparison_rows": int(row.get("comparison_rows") or 0),
            "reporting_currency": row.get("reporting_currency"),
            **_benchmark_freshness(row, latest_nav),
        }
        for row in benchmarks
    ]
    primary_count = sum(
        1 for row in benchmark_rows if row["mapping_type"] == "primary"
    )
    benchmarks_aligned = bool(administrative_master or (
        benchmark_rows
        and primary_count == 1
        and latest_nav
        and all(
            row["freshness_passed"]
            and row["comparison_rows"] > 0
            and row["reporting_currency"] == subject.get("reporting_currency")
            for row in benchmark_rows
        )
    ))
    record(
        "benchmarks_reconciled",
        benchmarks_aligned,
        (
            "Benchmarks are not applicable to this administrative-only master."
            if administrative_master
            else "Every configured benchmark is reconciled through its latest available "
            "market close within the bounded lag policy or an evidenced exchange closure."
        )
        if benchmarks_aligned
        else (
            "One or more benchmark comparisons are missing, stale under the exchange "
            "calendar/lag policy, dated after the portfolio, or use the wrong reporting currency."
        ),
        {
            "primary_count": primary_count,
            "maximum_staleness_days": MAXIMUM_BENCHMARK_STALENESS_DAYS,
            "benchmarks": benchmark_rows,
        },
    )

    current_templates = {
        (str(row.get("report_type")), str(row.get("asset_role"))): {
            "version_label": row.get("version_label"),
            "approval_status": row.get("approval_status"),
            "sha256": row.get("sha256"),
            "byte_count": int(row.get("byte_count") or 0),
        }
        for row in templates
    }
    templates_ready = bool(administrative_master or all(
        key in current_templates
        and current_templates[key]["approval_status"] in {"validated", "active"}
        and current_templates[key]["byte_count"] > 0
        and len(str(current_templates[key]["sha256"] or "")) == 64
        for key in REQUIRED_TEMPLATE_ASSETS
    ))
    record(
        "report_documents_validated",
        templates_ready,
        "Report templates are not applicable to this administrative-only master."
        if administrative_master
        else "The mandatory factsheet, presentation and completed example are validated."
        if templates_ready
        else "The mandatory three-document reporting set is incomplete or unvalidated.",
        {
            f"{report_type}:{asset_role}": current_templates.get(
                (report_type, asset_role)
            )
            for report_type, asset_role in sorted(REQUIRED_TEMPLATE_ASSETS)
        },
    )

    prior_gates = [
        {
            "check_id": str(row.get("check_id")),
            "status": str(row.get("status")),
        }
        for row in checks
        if row.get("check_id") not in {PREVIEW_GATE, ACTIVATION_GATE}
    ]
    prior_gates_ready = bool(prior_gates) and all(
        row["status"] == "pass" for row in prior_gates
    )
    record(
        "prior_onboarding_gates_passed",
        prior_gates_ready,
        "All preceding onboarding gates passed."
        if prior_gates_ready
        else "At least one preceding onboarding gate has not passed.",
        prior_gates,
    )

    evidence = {
        "validation_version": "portfolio-subject-preview-v5-evidenced-exchange-closures",
        "subject_id": str(subject.get("subject_id") or ""),
        "latest_data_date": _date_text(latest_nav),
        "results": results,
        "ready_for_owner_preview_approval": all(
            row["status"] == "pass" for row in results
        ),
    }
    evidence["evidence_sha256"] = preview_evidence_sha256(evidence)
    return evidence
