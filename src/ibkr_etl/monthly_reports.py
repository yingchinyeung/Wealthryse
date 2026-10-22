from __future__ import annotations

import calendar
from datetime import date
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import quote


class MonthlyReportError(ValueError):
    pass


REPORT_TYPES = {
    "factsheet": {
        "extension": ".pdf",
        "media_type": "application/pdf",
        "label": "Monthly factsheet",
    },
    "presentation": {
        "extension": ".pptx",
        "media_type": (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
        "label": "20-slide presentation",
    },
}

# What a month's package must hold. Per-account decks are no longer built:
# past decks stay supported above so they can still be downloaded, but a
# month is complete with its factsheet alone.
REQUIRED_REPORT_TYPES = frozenset({"factsheet"})


# Kiwoom subjects are stored under their account number; readers download them by market instead.
KIWOOM_DOWNLOAD_PREFIXES = {"KRW": "Korea", "USD": "US"}


def download_prefix(connection: Any, tenant_id: str, subject_id: str) -> str | None:
    """The reader-facing name a subject's downloads start with, or None to keep its own id.

    A Kiwoom account downloads as "Korea_Factsheet_<date>.pdf" (KRW) or "US_Factsheet_<date>.pdf" (USD),
    so the account number stays out of the file name. The subject is found by broker prefix and reporting
    currency, so no account number is in this code either. Reporting currency first: a Kiwoom U.S.
    account is a Korean account whose broker record may say KRW, as kiwoom_us_account.is_usd_account knows.
    """
    if not subject_id.startswith("KIWOOM-"):
        return None
    row = connection.execute(
        "SELECT COALESCE(reporting_currency, base_currency) AS currency FROM platform_subjects "
        "WHERE tenant_id = %s AND subject_id = %s",
        (tenant_id, subject_id),
    ).fetchone()
    return KIWOOM_DOWNLOAD_PREFIXES.get(str(row["currency"] or "").upper()) if row is not None else None


def validate_period_end(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise MonthlyReportError("Report month must be an ISO calendar month end") from error
    if parsed.day != calendar.monthrange(parsed.year, parsed.month)[1]:
        raise MonthlyReportError("Report month must be a calendar month end")
    return parsed.isoformat()


def validate_artifact(
    row: dict[str, Any],
    *,
    account_id: str,
    period_end: str,
    container: str,
) -> dict[str, Any]:
    row_subject_id = row.get("subject_id")
    if row_subject_id is not None and str(row_subject_id) != account_id:
        raise MonthlyReportError("Monthly report belongs to another portfolio")
    report_type = str(row.get("report_type") or "")
    definition = REPORT_TYPES.get(report_type)
    if definition is None:
        raise MonthlyReportError("Unsupported monthly report type")
    if str(row.get("container_name") or "") != container:
        raise MonthlyReportError("Monthly report is outside the approved container")
    blob_name = str(row.get("blob_name") or "")
    parts = PurePosixPath(blob_name).parts
    expected_prefix = ("released", account_id, period_end)
    if (
        len(parts) != 4
        or tuple(parts[:3]) != expected_prefix
        or ".." in parts
        or not parts[-1].lower().endswith(str(definition["extension"]))
    ):
        raise MonthlyReportError("Monthly report reference failed scope validation")
    digest = str(row.get("artifact_sha256") or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MonthlyReportError("Monthly report digest is invalid")
    return {
        "artifact_release_id": int(row["artifact_release_id"]),
        "report_type": report_type,
        "label": definition["label"],
        "filename": parts[-1],
        "container_name": container,
        "blob_name": blob_name,
        "sha256": digest,
        "media_type": definition["media_type"],
    }


def validate_review_artifact(
    row: dict[str, Any],
    *,
    account_id: str,
    period_end: str,
    container: str,
) -> dict[str, Any]:
    row_subject_id = row.get("subject_id")
    if row_subject_id is not None and str(row_subject_id) != account_id:
        raise MonthlyReportError("Monthly review report belongs to another portfolio")
    report_type = str(row.get("report_type") or "")
    definition = REPORT_TYPES.get(report_type)
    if definition is None:
        raise MonthlyReportError("Unsupported monthly report type")
    reference = str(row.get("output_reference") or "")
    parts = PurePosixPath(reference).parts
    expected_prefix = (container, "review", account_id, period_end)
    if (
        len(parts) != 5
        or tuple(parts[:4]) != expected_prefix
        or ".." in parts
        or not parts[-1].lower().endswith(str(definition["extension"]))
    ):
        raise MonthlyReportError("Monthly review report reference failed scope validation")
    digest = str(row.get("output_sha256") or "").lower()
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise MonthlyReportError("Monthly review report digest is invalid")
    return {
        "generation_run_id": int(row["generation_run_id"]),
        "report_type": report_type,
        "label": definition["label"],
        "filename": parts[-1],
        "container_name": container,
        "blob_name": "/".join(parts[1:]),
        "sha256": digest,
        "media_type": definition["media_type"],
        "review_only": True,
    }


def build_monthly_report_catalog(
    snapshots: Iterable[dict[str, Any]],
    generation_runs: Iterable[dict[str, Any]],
    released_artifacts: Iterable[dict[str, Any]],
    *,
    account_id: str,
    container: str,
) -> list[dict[str, Any]]:
    encoded_account_id = quote(account_id, safe="")
    runs_by_snapshot: dict[str, dict[str, str]] = {}
    review_artifacts_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    for run in generation_runs:
        if run.get("subject_id") is not None and str(run["subject_id"]) != account_id:
            raise MonthlyReportError("Monthly report run belongs to another portfolio")
        snapshot_id = str(run["snapshot_id"])
        runs_by_snapshot.setdefault(snapshot_id, {})[str(run["report_type"])] = str(
            run["status"]
        )
        if str(run.get("status")) in {"awaiting_approval", "released_internal"} and run.get("output_reference"):
            period_end = validate_period_end(str(run["period_end"]))
            if str(run["status"]) == "awaiting_approval":
                artifact = validate_review_artifact(
                    run, account_id=account_id,
                    period_end=period_end, container=container,
                )
            else:
                report_type = str(run["report_type"])
                if report_type not in REPORT_TYPES:
                    raise MonthlyReportError("Unsupported monthly report type")
                artifact = {
                    "generation_run_id": int(run["generation_run_id"]),
                    "report_type": report_type,
                    "label": REPORT_TYPES[report_type]["label"],
                    "review_only": True,
                }
            artifact["download_url"] = (
                f"/api/accounts/{encoded_account_id}/reporting/review-artifacts/"
                f"{artifact['generation_run_id']}/download"
            )
            review_artifacts_by_snapshot.setdefault(snapshot_id, []).append(artifact)
    # Approval belongs to an immutable artifact, not to whichever calculation
    # snapshot is currently active. Explicit replacement/revocation withdraws it.
    artifacts_by_period: dict[str, dict[str, dict[str, Any]]] = {}
    for row in released_artifacts:
        if row.get("subject_id") is not None and str(row["subject_id"]) != account_id:
            raise MonthlyReportError("Released monthly report belongs to another portfolio")
        snapshot_id = str(row["snapshot_id"])
        period_end = validate_period_end(str(row["period_end"]))
        artifact = validate_artifact(
            row,
            account_id=account_id,
            period_end=period_end,
            container=container,
        )
        artifact["download_url"] = (
            f"/api/accounts/{encoded_account_id}/reporting/artifacts/"
            f"{artifact['artifact_release_id']}/download"
        )
        artifact['snapshot_id'] = snapshot_id
        period_artifacts = artifacts_by_period.setdefault(period_end, {})
        if artifact['report_type'] in period_artifacts:
            raise MonthlyReportError('More than one current release exists for a report type')
        period_artifacts[artifact['report_type']] = artifact

    catalog = []
    seen_periods: set[str] = set()
    for snapshot in snapshots:
        if snapshot.get("subject_id") is not None and str(snapshot["subject_id"]) != account_id:
            raise MonthlyReportError("Monthly report snapshot belongs to another portfolio")
        snapshot_id = str(snapshot["snapshot_id"])
        period_end = validate_period_end(str(snapshot["period_end"]))
        if period_end in seen_periods:
            raise MonthlyReportError('More than one active reporting snapshot exists for a month')
        seen_periods.add(period_end)
        artifacts = sorted(
            artifacts_by_period.get(period_end, {}).values(),
            key=lambda item: item["report_type"],
        )
        available_types = {item["report_type"] for item in artifacts}
        ready = REQUIRED_REPORT_TYPES <= available_types
        review_artifacts = sorted(
            review_artifacts_by_snapshot.get(snapshot_id, []),
            key=lambda item: item["report_type"],
        )
        replacement_pending = any(item['snapshot_id'] != snapshot_id for item in artifacts)
        review_ready = (not ready or replacement_pending) and REQUIRED_REPORT_TYPES <= {
            item["report_type"] for item in review_artifacts
        }
        runs = runs_by_snapshot.get(snapshot_id, {})
        partially_released = bool(artifacts) and not ready
        catalog.append(
            {
                "snapshot_id": snapshot_id,
                "period_end": period_end,
                "data_as_of": str(snapshot["data_as_of"]),
                "quality_status": str(snapshot["quality_status"]),
                "ready": ready,
                "review_ready": review_ready,
                "downloadable": bool(artifacts) or review_ready,
                "partially_released": partially_released,
                "replacement_pending": replacement_pending,
                "status": (
                    "replacement_pending"
                    if replacement_pending
                    else "ready"
                    if ready
                    else "partially_released"
                    if partially_released
                    else "review_ready"
                    if review_ready
                    else "generation_pending"
                ),
                "generation_runs": runs,
                "artifacts": review_artifacts if review_ready else artifacts,
                "released_artifacts": artifacts,
                "review_artifacts": review_artifacts,
            }
        )
    return catalog
