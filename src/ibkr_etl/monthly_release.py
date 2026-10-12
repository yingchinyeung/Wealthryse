"""Owner-controlled, integrity-bound internal month-end package release."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from .internal_release import approve_internal_release
from .monthly_reports import (
    MonthlyReportError,
    validate_artifact,
    validate_period_end,
    validate_review_artifact,
)
from .util import canonical_json


def review_package(
    snapshot: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    *,
    subject_id: str,
    period_end: str,
    container: str,
    releases: Sequence[Mapping[str, Any]] = (),
    enforce_eligibility: bool = True,
) -> dict[str, Any]:
    """Bind a file checklist to the latest exact pair and current release state."""

    normalized_period = validate_period_end(period_end)
    if str(snapshot.get("subject_id")) != subject_id or str(snapshot.get("period_end")) != normalized_period:
        raise MonthlyReportError("The review snapshot belongs to another account or month")
    latest: dict[str, Mapping[str, Any]] = {}
    for row in sorted(runs, key=lambda item: int(item["generation_run_id"]), reverse=True):
        kind = str(row.get("report_type"))
        if kind in {"factsheet", "presentation"} and kind not in latest:
            latest[kind] = row
    if set(latest) != {"factsheet", "presentation"}:
        raise MonthlyReportError("A complete factsheet and presentation pair is required")
    selected = [latest["factsheet"], latest["presentation"]]
    if any(str(row.get("status")) not in {"awaiting_approval", "released_internal"} for row in selected):
        raise MonthlyReportError("The selected report pair is not ready for owner review")
    releases_by_run: dict[int, Mapping[str, Any]] = {}
    for row in releases:
        run_id = int(row["generation_run_id"])
        if run_id in releases_by_run:
            raise MonthlyReportError("Duplicate release record for a report file")
        releases_by_run[run_id] = row
    artifacts = []
    for row in selected:
        run_id = int(row["generation_run_id"])
        release = releases_by_run.get(run_id)
        if release is None:
            artifact = validate_review_artifact(
                row, account_id=subject_id,
                period_end=normalized_period, container=container,
            )
            artifact["source_kind"] = "review"
            artifact["approved"] = False
            artifact["state_revision"] = 0
        else:
            if str(release.get("report_type")) != str(row.get("report_type")):
                raise MonthlyReportError("Release record has the wrong report type")
            artifact = validate_artifact(
                release, account_id=subject_id,
                period_end=normalized_period, container=container,
            )
            if artifact["sha256"] != str(row.get("output_sha256") or "").lower():
                if enforce_eligibility:
                    raise MonthlyReportError("Released file does not match its generation record")
                artifact["integrity_warning"] = "Released file differs from its generation record"
            artifact["generation_run_id"] = run_id
            artifact["source_kind"] = "released"
            artifact["approved"] = release.get("revoked_at") is None
            artifact["state_revision"] = int(release.get("state_revision") or 1)
            artifact['superseded']=release.get('superseded_by_artifact_release_id') is not None
        artifacts.append(artifact)
    if enforce_eligibility:
        approval = approve_internal_release(snapshot, selected, user_acceptance_recorded=True)
        blockers = list(approval.blockers)
    else:
        raw = snapshot.get("publication_blockers_json") or "[]"
        try:
            blockers = list(json.loads(raw) if isinstance(raw, str) else raw)
        except (TypeError, ValueError):
            blockers = ["publication_blockers_unreadable"]
    evidence = {
        "snapshot_id": str(snapshot["snapshot_id"]),
        "subject_id": subject_id,
        "period_end": normalized_period,
        "snapshot_sha256": str(snapshot.get("data_sha256") or ""),
        "artifacts": [
            {
                "generation_run_id": item["generation_run_id"],
                "report_type": item["report_type"],
                "sha256": item["sha256"],
            }
            for item in artifacts
        ],
    }
    package_sha256 = hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()
    state_evidence = {
        "package_sha256": package_sha256,
        "files": [
            {
                "report_type": item["report_type"],
                "approved": item["approved"],
                "artifact_release_id": item.get("artifact_release_id"),
                "state_revision": item["state_revision"],
            }
            for item in artifacts
        ],
    }
    return {
        "snapshot_id": str(snapshot["snapshot_id"]),
        "subject_id": subject_id,
        "period_end": normalized_period,
        "package_sha256": package_sha256,
        "selection_version": hashlib.sha256(canonical_json(state_evidence).encode("utf-8")).hexdigest(),
        "approved_types": [item["report_type"] for item in artifacts if item["approved"]],
        "artifacts": artifacts,
        "publication_blockers": blockers,
        "release_scope": "internal_shareholder",
        "client_distribution": "prohibited",
    }
