from __future__ import annotations

import calendar
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from .presentation_design import (
    PLATFORM_PRESENTATION_LAYOUT_VERSION,
    PLATFORM_PRESENTATION_REFERENCE_SUBJECT,
    PLATFORM_PRESENTATION_SHA256,
    PLATFORM_PRESENTATION_SLIDE_COUNT,
)
from .monthly_artifacts import (
    FACTSHEET_RENDER_CONTRACT_VERSION,
    factsheet_layout_version,
    factsheet_render_contract_sha256,
)
from .template_upload import normalize_strategy_identity, validate_strategy_name


class OnboardingReportError(RuntimeError):
    pass


REPORT_TYPES = ("factsheet", "presentation")
REPORT_EXTENSIONS = {"factsheet": ".pdf", "presentation": ".pptx"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# U16088571 remains the approved evidence-layout model for onboarded accounts.
# Account-specific uploaded decks provide locked identity and branding evidence;
# they do not replace the approved native table and chart system.
APPROVED_PRESENTATION_MODEL = {
    "reference_subject_id": PLATFORM_PRESENTATION_REFERENCE_SUBJECT,
    "layout_version": PLATFORM_PRESENTATION_LAYOUT_VERSION,
    "sha256": PLATFORM_PRESENTATION_SHA256,
    "slide_count": PLATFORM_PRESENTATION_SLIDE_COUNT,
}

SUBJECT_PRESENTATION_MODELS = {
    "U16088606": {
        "layout_version": "u16088606-greater-china-platform-v24-evidence-layout-v1",
        "identity_evidence_sha256": "2cfa0f334585627f695d8c60fb7fada7307a9d9769d8850402699a354073fea6",
        "slide_count": 20,
        "design_source": "approved_u16088571_platform_layout_with_subject_identity",
        "strategy_name": "iStrategy: Greater China",
    }
}


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def historical_report_evidence_sha256(evidence: Mapping[str, Any]) -> str:
    return _sha256(dict(evidence))


def previous_calendar_month_end(today: date) -> date:
    return today.replace(day=1) - timedelta(days=1)


def _calendar_month_end(value: date) -> date:
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


def _next_month(value: date) -> date:
    return date(
        value.year + (1 if value.month == 12 else 0),
        1 if value.month == 12 else value.month + 1,
        1,
    )


def discover_completed_reporting_periods(
    connection: Any,
    *,
    subject_id: str,
    reporting_start: str | date,
    today: date,
    subject_type: str = "physical_account",
) -> list[dict[str, str]]:
    """Return every completed month with a same-day NAV and holdings snapshot."""

    start = (
        reporting_start
        if isinstance(reporting_start, date)
        else date.fromisoformat(reporting_start)
    )
    completed_through = previous_calendar_month_end(today)
    if start > completed_through:
        raise OnboardingReportError("No completed reporting month exists yet")
    if subject_type == "filtered_portfolio":
        statement = """
            SELECT DISTINCT p.snapshot_date
            FROM derived_portfolio_positions p
            WHERE p.portfolio_id = ? AND p.snapshot_date BETWEEN ? AND ?
              AND EXISTS (
                  SELECT 1 FROM derived_portfolio_nav n
                  WHERE n.portfolio_id = p.portfolio_id
                    AND n.snapshot_date = p.snapshot_date
              )
            ORDER BY p.snapshot_date
        """
    elif subject_type == "composite_portfolio":
        statement = """
            SELECT DISTINCT snapshot_date
            FROM derived_portfolio_nav
            WHERE portfolio_id = ? AND snapshot_date BETWEEN ? AND ?
            ORDER BY snapshot_date
        """
    else:
        statement = """
            SELECT DISTINCT p.snapshot_date
            FROM position_snapshots p
            WHERE p.account_id = ? AND p.snapshot_date BETWEEN ? AND ?
              AND EXISTS (
                  SELECT 1 FROM nav_snapshots n
                  WHERE n.account_id = p.account_id
                    AND n.snapshot_date = p.snapshot_date
                    AND n.asset_class = 'TOTAL'
              )
            ORDER BY p.snapshot_date
        """
    rows = connection.execute(
        statement,
        (subject_id, start.isoformat(), completed_through.isoformat()),
    ).fetchall()
    latest_by_month: dict[str, date] = {}
    for row in rows:
        observed = date.fromisoformat(str(row["snapshot_date"]))
        latest_by_month[f"{observed.year:04d}-{observed.month:02d}"] = observed

    expected: list[str] = []
    cursor = start.replace(day=1)
    final = completed_through.replace(day=1)
    while cursor <= final:
        expected.append(f"{cursor.year:04d}-{cursor.month:02d}")
        cursor = _next_month(cursor)
    missing = [month for month in expected if month not in latest_by_month]
    if missing:
        raise OnboardingReportError(
            "No common NAV and holdings observation exists for completed month(s): "
            + ", ".join(missing)
        )
    return [
        {
            "reporting_month": month,
            "period_end": _calendar_month_end(latest_by_month[month]).isoformat(),
            "holdings_as_of": latest_by_month[month].isoformat(),
        }
        for month in expected
    ]


def template_evidence(
    templates: Sequence[Mapping[str, Any]], *, subject_id: str | None = None
) -> dict[str, Any]:
    current = {
        (str(row.get("report_type")), str(row.get("asset_role"))): row
        for row in templates
        if bool(row.get("is_current", True))
    }
    required = (
        ("factsheet", "template"),
        ("factsheet", "completed_example"),
        ("presentation", "template"),
    )
    missing = [f"{kind}:{role}" for kind, role in required if (kind, role) not in current]
    if missing:
        raise OnboardingReportError(
            "Current report templates are missing: " + ", ".join(missing)
        )
    validated: dict[tuple[str, str], dict[str, Any]] = {}
    validated_metadata: dict[tuple[str, str], dict[str, Any]] = {}
    for kind, role in required:
        row = current[(kind, role)]
        digest = str(row.get("sha256") or "").lower()
        if not SHA256_PATTERN.fullmatch(digest):
            raise OnboardingReportError(f"{kind} template SHA-256 is invalid")
        if str(row.get("approval_status")) not in {"validated", "active"}:
            raise OnboardingReportError(f"{kind} template has not been validated")
        metadata = row.get("metadata_json") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError as error:
                raise OnboardingReportError(
                    f"{kind}:{role} template metadata is invalid"
                ) from error
        if not isinstance(metadata, Mapping):
            raise OnboardingReportError(f"{kind}:{role} template metadata is invalid")
        validated_metadata[(kind, role)] = dict(metadata)
        validated[(kind, role)] = {
            "version_label": str(row.get("version_label") or ""),
            "sha256": digest,
            "byte_count": int(row.get("byte_count") or 0),
        }
        if validated[(kind, role)]["byte_count"] <= 0:
            raise OnboardingReportError(f"{kind}:{role} document is empty")

    factsheet_example = validated[("factsheet", "completed_example")]
    factsheet_template = validated[("factsheet", "template")]
    presentation_reference = validated[("presentation", "template")]
    subject_model = SUBJECT_PRESENTATION_MODELS.get(str(subject_id or "").upper())
    if subject_model:
        if (
            presentation_reference["sha256"]
            != subject_model["identity_evidence_sha256"]
        ):
            raise OnboardingReportError(
                "The subject presentation template does not match its approved SHA-256"
            )
        presentation_state = {
            "version_label": str(subject_model["layout_version"]),
            "sha256": APPROVED_PRESENTATION_MODEL["sha256"],
            "byte_count": presentation_reference["byte_count"],
            "design_source": str(subject_model["design_source"]),
            "reference_subject_id": APPROVED_PRESENTATION_MODEL[
                "reference_subject_id"
            ],
            "slide_count": int(subject_model["slide_count"]),
            "strategy_name": str(subject_model["strategy_name"]),
            "identity_evidence_sha256": presentation_reference["sha256"],
            "account_supporting_reference": presentation_reference,
        }
    else:
        contract = validated_metadata[("presentation", "template")].get(
            "presentation_contract"
        )
        if subject_id and not isinstance(contract, Mapping):
            raise OnboardingReportError(
                "The presentation template has no validated strategy identity contract"
            )
        contract = dict(contract or {})
        strategy_name = validate_strategy_name(
            str(contract.get("strategy_name") or "iStrategy: Platform Standard")
        )
        strategy_identity_sha256 = hashlib.sha256(
            normalize_strategy_identity(strategy_name).encode("utf-8")
        ).hexdigest()
        if contract and contract.get("strategy_identity_sha256") != strategy_identity_sha256:
            raise OnboardingReportError(
                "The presentation strategy identity digest does not match its contract"
            )
        if contract and contract.get("source_template_sha256") != presentation_reference["sha256"]:
            raise OnboardingReportError(
                "The presentation strategy contract is bound to another template"
            )
        if subject_id:
            if contract.get("identity_source") != "owner_approved_subject_display_name":
                raise OnboardingReportError(
                    "The presentation strategy was not derived from an owner-approved subject identity"
                )
            if not SHA256_PATTERN.fullmatch(
                str(contract.get("owner_approval_scope_sha256") or "")
            ):
                raise OnboardingReportError(
                    "The presentation strategy has no valid owner-approval scope"
                )
        layout_policy = str(contract.get("layout_policy") or "platform_standard")
        if layout_policy != "platform_standard":
            raise OnboardingReportError(
                "New account presentations must use the approved U16088571 platform layout"
            )
        presentation_state = {
            "version_label": APPROVED_PRESENTATION_MODEL["layout_version"],
            "sha256": APPROVED_PRESENTATION_MODEL["sha256"],
            "byte_count": presentation_reference["byte_count"],
            "design_source": "platform_approved_u16088571_presentation",
            "reference_subject_id": APPROVED_PRESENTATION_MODEL[
                "reference_subject_id"
            ],
            "slide_count": APPROVED_PRESENTATION_MODEL["slide_count"],
            "strategy_name": strategy_name,
            "strategy_identity_sha256": strategy_identity_sha256,
            "layout_policy": "platform_standard",
            "identity_evidence_sha256": presentation_reference["sha256"],
            "account_supporting_reference": presentation_reference,
        }
    result: dict[str, Any] = {
        "factsheet": {
            **factsheet_example,
            "design_source": "subject_completed_factsheet",
            "supporting_template": factsheet_template,
            "layout_version": factsheet_layout_version(str(subject_id or "")),
            "render_contract_version": FACTSHEET_RENDER_CONTRACT_VERSION,
            "render_contract_sha256": factsheet_render_contract_sha256(
                subject_id=str(subject_id or ""),
                supporting_template_sha256=factsheet_template["sha256"],
                completed_example_sha256=factsheet_example["sha256"],
            ),
        },
        "presentation": presentation_state,
    }
    result["evidence_sha256"] = _sha256(result)
    return result


def source_period_evidence(
    *, subject_id: str, reporting_start: str, periods: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    evidence = {
        "subject_id": subject_id,
        "reporting_start": reporting_start,
        "periods": [
            {
                "period_end": str(row["period_end"]),
                "holdings_as_of": str(row["holdings_as_of"]),
            }
            for row in periods
        ],
    }
    evidence["evidence_sha256"] = _sha256(evidence)
    return evidence


def _artifact_reference_valid(
    item: Mapping[str, Any], *, subject_id: str, container: str
) -> bool:
    report_type = str(item.get("report_type") or "")
    extension = REPORT_EXTENSIONS.get(report_type)
    if extension is None or str(item.get("container_name") or "") != container:
        return False
    period_end = str(item.get("period_end") or "")
    parts = PurePosixPath(str(item.get("blob_name") or "")).parts
    return (
        len(parts) == 4
        and tuple(parts[:3]) == ("review", subject_id, period_end)
        and parts[-1].lower().endswith(extension)
        and ".." not in parts
    )


def build_historical_report_batch_validation(
    *,
    subject: Mapping[str, Any],
    templates: Sequence[Mapping[str, Any]],
    periods: Sequence[Mapping[str, Any]],
    batch: Mapping[str, Any] | None,
    items: Sequence[Mapping[str, Any]],
    container: str,
) -> dict[str, Any]:
    """Validate a complete private historical report batch without publishing it."""

    subject_id = str(subject.get("subject_id") or "")
    template_state = template_evidence(templates, subject_id=subject_id)
    reporting_start = str(subject.get("history_start_date") or "")
    expected_periods = [str(row["period_end"]) for row in periods]
    expected_keys = {
        (period_end, report_type)
        for period_end in expected_periods
        for report_type in REPORT_TYPES
    }
    item_by_key = {
        (str(row.get("period_end")), str(row.get("report_type"))): row for row in items
    }

    results: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, message: str, evidence: object) -> None:
        results.append(
            {
                "check_id": check_id,
                "status": "pass" if passed else "fail",
                "message": message,
                "evidence": evidence,
            }
        )

    batch_exists = batch is not None
    add(
        "historical_batch_exists",
        batch_exists,
        "A private historical report batch exists."
        if batch_exists
        else "No private historical report batch has been created.",
        {"batch_id": str(batch.get("batch_id")) if batch else None},
    )
    counts_match = bool(
        batch
        and int(batch.get("expected_month_count") or 0) == len(expected_periods)
        and int(batch.get("expected_artifact_count") or 0) == len(expected_keys)
    )
    add(
        "completed_month_coverage",
        counts_match,
        "Every completed reporting month is included in the batch."
        if counts_match
        else "The batch does not cover every completed reporting month.",
        {
            "expected_periods": expected_periods,
            "expected_month_count": len(expected_periods),
            "batch_month_count": int(batch.get("expected_month_count") or 0)
            if batch
            else 0,
        },
    )
    template_match = bool(
        batch
        and str(batch.get("template_evidence_sha256") or "")
        == template_state["evidence_sha256"]
    )
    add(
        "template_versions_locked",
        template_match,
        "The batch is bound to the current validated templates."
        if template_match
        else "The batch template evidence is missing or stale.",
        template_state,
    )
    exact_items = set(item_by_key) == expected_keys
    add(
        "report_pair_inventory_complete",
        exact_items,
        "Every completed month has exactly one factsheet and one presentation."
        if exact_items
        else "One or more completed months lack an exact factsheet and presentation pair.",
        {
            "expected_artifact_count": len(expected_keys),
            "observed_artifact_count": len(item_by_key),
        },
    )

    invalid_artifacts: list[dict[str, str]] = []
    for key in sorted(expected_keys):
        item = item_by_key.get(key)
        if item is None:
            invalid_artifacts.append(
                {"period_end": key[0], "report_type": key[1], "reason": "missing"}
            )
            continue
        report_type = key[1]
        validation = item.get("validation_json") or {}
        if isinstance(validation, str):
            try:
                validation = json.loads(validation)
            except json.JSONDecodeError:
                validation = {}
        expected_template_sha = (
            template_state[report_type]["render_contract_sha256"]
            if report_type == "factsheet"
            else template_state[report_type]["sha256"]
        )
        valid = (
            str(item.get("status")) == "validated"
            and item.get("snapshot_id") is not None
            and item.get("generation_run_id") is not None
            and _artifact_reference_valid(item, subject_id=subject_id, container=container)
            and SHA256_PATTERN.fullmatch(str(item.get("artifact_sha256") or ""))
            is not None
            and int(item.get("byte_count") or 0) > 0
            and str(item.get("template_sha256") or "") == expected_template_sha
            and bool(validation.get("passed"))
        )
        if not valid:
            invalid_artifacts.append(
                {
                    "period_end": key[0],
                    "report_type": report_type,
                    "reason": "validation_failed",
                }
            )
    artifacts_valid = exact_items and not invalid_artifacts
    add(
        "all_artifacts_validated",
        artifacts_valid,
        "Every private PDF and PPTX passed integrity and layout validation."
        if artifacts_valid
        else "One or more private PDF or PPTX artifacts failed validation.",
        {"invalid_artifacts": invalid_artifacts},
    )

    batch_ready = bool(
        batch
        and str(batch.get("status")) in {"awaiting_owner_approval", "approved"}
        and int(batch.get("validated_artifact_count") or 0) == len(expected_keys)
        and str(batch.get("manifest_sha256") or "")
        and SHA256_PATTERN.fullmatch(str(batch.get("manifest_sha256") or ""))
    )
    add(
        "batch_manifest_finalized",
        batch_ready,
        "The immutable batch manifest is complete and ready for owner review."
        if batch_ready
        else "The batch manifest is not ready for owner review.",
        {
            "status": str(batch.get("status")) if batch else None,
            "manifest_sha256": str(batch.get("manifest_sha256")) if batch else None,
        },
    )

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "subject_id": subject_id,
        "reporting_start": reporting_start,
        "completed_reporting_periods": expected_periods,
        "expected_month_count": len(expected_periods),
        "expected_artifact_count": len(expected_keys),
        "batch_id": str(batch.get("batch_id")) if batch else None,
        "batch_manifest_sha256": str(batch.get("manifest_sha256")) if batch else None,
        "template_evidence_sha256": template_state["evidence_sha256"],
        "results": results,
        "ready_for_owner_approval": all(row["status"] == "pass" for row in results),
    }
    evidence["evidence_sha256"] = historical_report_evidence_sha256(evidence)
    return evidence
