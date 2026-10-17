"""Stage 3 reports for an onboarding case that has no built-in design, from the owner's factsheet design.

A composite can report in the Alpha World MS or T design (``composite_reports``).
Every other case of the new onboarding (a broker account, a filtered portfolio,
or a composite without a design) reports in the design its owner makes in the
factsheet designer: they upload the template, review the agents' draft and
submit it. Until a design is submitted, the case's reports_rendered check stays
pending and says so, so the case cannot reach approval 2.

Once a design is submitted, this run renders a factsheet for every completed
month since the history start through the spec engine, from the design's own
template and completed example (kept by SHA-256 in factsheet_design_files),
stores each privately and records reports_rendered with the list of files, as
``composite_reports`` does. Until approval 2, a newer submitted design replaces
the older one and its months render again; once the owner has approved the
reports, the run leaves the case alone. A month already rendered from the same
snapshot and design is reused.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from .composite_reports import (
    CHECK_ID, KINDS, STALE_AFTER_DAYS, CompositeReportError, _existing_run, _frozen_payload, _store,
    _verify_reused, month_end_ready, report_months,
)
from .factsheet_spec import engine
from .historical_reporting import queue_report_generation_run
from .monthly_reports import REPORT_TYPES
from .onboarding_v2_web import OnboardingError, _evidence, record_platform_check
from .reporting_snapshot import ReportingSnapshotError, active_month_end_snapshot_id, freeze_month_end_snapshot
from .util import utc_now_iso

DESIGNER_PATH = "/accounts/{subject_id}/factsheet-designer"
WAITING_FOR_DESIGN = ("Waiting for the factsheet design. Open the factsheet designer, draft the design from "
                      "the template and submit it; the historical factsheets are then rendered from it.")


class DesignedReportError(CompositeReportError):
    """Why a case's reports could not be rendered from its design, as the case page shows it."""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def submitted_design(connection, tenant_id: str, subject_id: str) -> dict | None:
    """The subject's most recently submitted design: the draft, its current version's spec and its files."""

    row = connection.execute(
        """
        SELECT draft_id, title, current_version, template_sha256, example_sha256, updated_at
        FROM factsheet_design_drafts
        WHERE tenant_id = ? AND subject_id = ? AND status = 'submitted'
        ORDER BY updated_at DESC, created_at DESC LIMIT 1
        """,
        (tenant_id, subject_id),
    ).fetchone()
    if row is None:
        return None
    version = connection.execute(
        "SELECT spec_json, spec_sha256 FROM factsheet_design_versions WHERE draft_id = ? AND version = ?",
        (str(row["draft_id"]), int(row["current_version"])),
    ).fetchone()
    if version is None:
        raise DesignedReportError("The submitted factsheet design has lost its current version")
    spec = _json(version["spec_json"])
    if engine.spec_sha256(spec) != str(version["spec_sha256"]):
        raise DesignedReportError("The submitted factsheet design failed its integrity check")
    return {
        "draft_id": str(row["draft_id"]), "title": str(row["title"]), "version": int(row["current_version"]),
        "spec": spec, "spec_sha256": str(version["spec_sha256"]),
        "template_sha256": str(row["template_sha256"]),
        "example_sha256": str(row["example_sha256"]) if row["example_sha256"] else None,
    }


def approved_design(connection, tenant_id: str, case_id: str, subject_id: str) -> dict:
    """The design version the owner approved with the historical reports (approval 2).

    After go-live every month renders from this version, so a design submitted
    later never changes the monthly factsheets without its own review.
    """

    row = connection.execute(
        "SELECT terms_json FROM broker_onboarding_approvals "
        "WHERE tenant_id = ? AND case_id = ? AND approval_kind = 'historical_reports'",
        (tenant_id, case_id),
    ).fetchone()
    terms = _json(row["terms_json"]) if row is not None else None
    evidence = (((terms or {}).get("checks") or {}).get(CHECK_ID) or {}).get("evidence") or {}
    approved = evidence.get("design") if isinstance(evidence, Mapping) else None
    if not isinstance(approved, Mapping) or not approved.get("draft_id") or not approved.get("version"):
        raise DesignedReportError("The approved historical reports name no factsheet design")
    draft = connection.execute(
        "SELECT title, template_sha256, example_sha256 FROM factsheet_design_drafts "
        "WHERE tenant_id = ? AND subject_id = ? AND draft_id = ?",
        (tenant_id, subject_id, str(approved["draft_id"])),
    ).fetchone()
    version = connection.execute(
        "SELECT spec_json, spec_sha256 FROM factsheet_design_versions WHERE draft_id = ? AND version = ?",
        (str(approved["draft_id"]), int(approved["version"])),
    ).fetchone()
    if draft is None or version is None:
        raise DesignedReportError("The approved factsheet design is no longer stored")
    spec = _json(version["spec_json"])
    if str(version["spec_sha256"]) != str(approved.get("spec_sha256")) or engine.spec_sha256(spec) != str(
            version["spec_sha256"]):
        raise DesignedReportError("The approved factsheet design failed its integrity check")
    return {
        "draft_id": str(approved["draft_id"]), "title": str(draft["title"]), "version": int(approved["version"]),
        "spec": spec, "spec_sha256": str(version["spec_sha256"]),
        "template_sha256": str(draft["template_sha256"]),
        "example_sha256": str(draft["example_sha256"]) if draft["example_sha256"] else None,
    }


def design_file(connection, tenant_id: str, sha256: str) -> bytes:
    row = connection.execute(
        "SELECT content FROM factsheet_design_files WHERE tenant_id = ? AND sha256 = ?", (tenant_id, sha256),
    ).fetchone()
    if row is None:
        raise DesignedReportError("A file of the submitted factsheet design is missing")
    content = bytes(row["content"])
    if _sha256(content) != sha256:
        raise DesignedReportError("A file of the submitted factsheet design failed its integrity check")
    return content


def eligible_cases(connection, tenant_id: str) -> list[dict]:
    """Stage 3 cases with no built-in design whose history has been built and whose reports are not yet approved."""

    rows = connection.execute(
        """
        SELECT c.case_id, c.source_kind, c.target_subject_id, c.reporting_currency, c.history_start_date,
               k.status AS recorded, k.message AS recorded_message, k.evidence_json
        FROM broker_onboarding_cases c
        LEFT JOIN broker_onboarding_checks k
          ON k.tenant_id = c.tenant_id AND k.case_id = c.case_id AND k.check_id = 'reports_rendered'
        WHERE c.tenant_id = ? AND c.report_style IS NULL
          AND c.stage = 'build_history' AND c.status <> 'cancelled'
          AND EXISTS (SELECT 1 FROM broker_onboarding_checks k WHERE k.tenant_id = c.tenant_id
                        AND k.case_id = c.case_id AND k.check_id = 'history_built' AND k.status = 'pass')
          -- Approved reports are what goes live: a later design no longer changes them.
          AND NOT EXISTS (SELECT 1 FROM broker_onboarding_approvals a WHERE a.tenant_id = c.tenant_id
                            AND a.case_id = c.case_id AND a.approval_kind = 'historical_reports')
        ORDER BY c.created_at
        """,
        (tenant_id,),
    ).fetchall()
    return [{**dict(row), "case_id": str(row["case_id"])} for row in rows]


def _latest_common_day(connection, statement: str, subject_id: str, period_end: str) -> str:
    row = connection.execute(statement, (subject_id, period_end)).fetchone()
    return str(row["day"])[:10] if row and row["day"] else ""


def month_ready(connection, source_kind: str, subject_id: str, period_end: str) -> bool:
    """Whether the month's data are in: the same rule as the month-end package for each kind of subject."""

    if source_kind == "composite_portfolio":
        return month_end_ready(connection, subject_id, period_end)
    if source_kind == "filtered_portfolio":
        statement = ("SELECT MAX(p.snapshot_date) AS day FROM derived_portfolio_positions p "
                     "WHERE p.portfolio_id = ? AND p.snapshot_date <= ? AND EXISTS (SELECT 1 FROM derived_portfolio_nav n "
                     "WHERE n.portfolio_id = p.portfolio_id AND n.snapshot_date = p.snapshot_date)")
    else:
        statement = ("SELECT MAX(p.snapshot_date) AS day FROM position_snapshots p "
                     "WHERE p.account_id = ? AND p.snapshot_date <= ? AND EXISTS (SELECT 1 FROM nav_snapshots n "
                     "WHERE n.account_id = p.account_id AND n.snapshot_date = p.snapshot_date AND n.asset_class = 'TOTAL')")
    day = _latest_common_day(connection, statement, subject_id, period_end)
    return bool(day) and (date.fromisoformat(period_end) - date.fromisoformat(day)).days <= STALE_AFTER_DAYS


def render_case_reports(connection, tenant_id: str, case: Mapping[str, Any], *, container: str,
                        today: date, fetch: Callable[[str, str], bytes],
                        store: Callable[[str, str, bytes, str], None],
                        render: Callable[..., Mapping[str, Any]] = engine.render,
                        design: Mapping[str, Any] | None = None) -> dict:
    """Render the case's completed months and return the reports_rendered evidence.

    The design is the latest submitted one unless given (after go-live, the approved one).
    """

    subject_id = str(case["target_subject_id"])
    currency = str(case["reporting_currency"])
    if design is None:
        design = submitted_design(connection, tenant_id, subject_id)
    if design is None:
        return {"design": None, "designer_page": DESIGNER_PATH.format(subject_id=subject_id),
                "reports": [], "waiting_for": None}
    if currency not in tuple(design["spec"].get("currencies") or ()):
        raise DesignedReportError(f"The submitted design reports in {', '.join(design['spec'].get('currencies') or [])}, "
                                  f"but this account reports in {currency}. Submit a design in {currency}.")
    versions = {"factsheet": f"spec:{design['spec_sha256']}"}
    reports: list[dict] = []
    waiting_for = None
    inception = date.fromisoformat(str(case["history_start_date"])[:10])
    with TemporaryDirectory(prefix=f"{subject_id}-stage-3-") as directory:
        root = Path(directory)
        sources: dict[str, Path | None] | None = None
        for period_end in report_months(inception, today):
            snapshot_id = active_month_end_snapshot_id(connection, tenant_id=tenant_id, subject_id=subject_id,
                                                       period_end=period_end)
            if snapshot_id is None:
                if not month_ready(connection, str(case["source_kind"]), subject_id, period_end):
                    waiting_for = period_end  # a later month cannot be ready before this one
                    break
                try:
                    snapshot_id = freeze_month_end_snapshot(
                        connection, tenant_id=tenant_id, subject_id=subject_id, period_start=inception.isoformat(),
                        period_end=period_end, reporting_currency=currency, reject_quality_fail=True).snapshot_id
                except ReportingSnapshotError:
                    connection.rollback()
                    waiting_for = period_end
                    break
            frozen = None
            for kind, label in KINDS.items():
                template_id = f"{subject_id}-monthly-{kind}"
                file_name = f"{subject_id}_{label}_{period_end}{REPORT_TYPES[kind]['extension']}"
                existing = _existing_run(connection, tenant_id, str(snapshot_id), kind, template_id, versions[kind])
                if existing is not None:
                    run_id, reference = int(existing["generation_run_id"]), str(existing["output_reference"])
                    digest = str(existing["output_sha256"])
                else:
                    if frozen is None:
                        frozen = _frozen_payload(connection, tenant_id, subject_id, str(snapshot_id))
                    if sources is None:
                        sources = {"template_path": root / "template.pdf", "example_path": None}
                        sources["template_path"].write_bytes(design_file(connection, tenant_id, design["template_sha256"]))
                        if design["example_sha256"]:
                            sources["example_path"] = root / "completed-example.pdf"
                            sources["example_path"].write_bytes(design_file(connection, tenant_id, design["example_sha256"]))
                    output = root / file_name
                    try:
                        render(design["spec"], frozen[0], output_path=output, **sources)
                    except (engine.RenderError, ValueError) as error:
                        raise DesignedReportError(f"The {period_end} factsheet failed its checks: {error}") from error
                    content = output.read_bytes()
                    output.unlink()
                    digest = _sha256(content)
                    blob = f"review/{subject_id}/{period_end}/{digest}-{file_name}"
                    _store(store, fetch, container, blob, content, kind)
                    run_id = queue_report_generation_run(connection, tenant_id=tenant_id, snapshot_id=str(snapshot_id),
                                                         report_type=kind, template_id=template_id,
                                                         template_version=versions[kind])
                    reference = f"{container}/{blob}"
                    connection.execute(
                        "UPDATE report_generation_runs SET status = 'awaiting_approval', output_reference = ?, "
                        "output_sha256 = ?, completed_at = ?, error_message = NULL "
                        "WHERE tenant_id = ? AND generation_run_id = ? AND status NOT IN ('released_internal', 'published')",
                        (reference, digest, utc_now_iso(), tenant_id, run_id),
                    )
                    connection.commit()
                reports.append({
                    "period_end": period_end, "report_type": kind, "file_name": file_name, "sha256": digest,
                    "generation_run_id": run_id, "snapshot_id": str(snapshot_id), "output_reference": reference,
                    "download_path": f"/api/onboarding-v2/cases/{case['case_id']}/reports/{run_id}",
                })
    return {"design": {"title": design["title"], "draft_id": design["draft_id"], "version": design["version"],
                       "spec_sha256": design["spec_sha256"]},
            "template_version": versions["factsheet"], "reports": reports, "waiting_for": waiting_for}


def _message(evidence: Mapping[str, Any]) -> tuple[str, str]:
    if evidence["design"] is None:
        return "pending", WAITING_FOR_DESIGN
    reports, waiting_for = evidence["reports"], evidence["waiting_for"]
    title = evidence["design"]["title"]
    if not reports:
        if waiting_for:
            return "pending", f"Waiting for the {waiting_for} month-end data before rendering in the {title} design."
        return "warn", "No month has ended since the history start, so there are no reports to review yet."
    first, last = reports[0]["period_end"], reports[-1]["period_end"]
    months = len({item["period_end"] for item in reports})
    span = f"for {first}" if first == last else f"from {first} to {last}"
    count = "A factsheet" if months == 1 else f"Factsheets for {months} months"
    later = f" {waiting_for} follows once its month-end data are in." if waiting_for else ""
    return "pass", f"{count} {span} in your {title} design, each read back and checked.{later}"


def run_designed_report_cases(connection, tenant_id: str, *, container: str, today: date,
                              fetch: Callable[[str, str], bytes], store: Callable[[str, str, bytes, str], None],
                              render: Callable[..., Mapping[str, Any]] = engine.render) -> list[dict]:
    """Render and record every eligible case. One case's failure never stops the others."""

    # The designer's tables come with migration 067; until then there is no design to render from.
    if connection.execute("SELECT to_regclass('public.factsheet_design_drafts') AS present").fetchone()["present"] is None:
        return []
    results = []
    for case in eligible_cases(connection, tenant_id):
        case_id = str(case["case_id"])
        recorded = (case["recorded"], case["recorded_message"], _evidence(case))
        try:
            evidence = render_case_reports(connection, tenant_id, case, container=container, today=today,
                                           fetch=fetch, store=store, render=render)
            status, message = _message(evidence)
            if (status, message, evidence) != recorded:
                _verify_reused(evidence, fetch)
        except Exception as error:  # recorded on the case, never raised to the month-end run
            connection.rollback()
            status, message = "fail", f"The reports could not be rendered: {' '.join(str(error).split())[:400]}"
            evidence = {"error_type": type(error).__name__}
        # An unchanged result stays as recorded, so approval 2 is not reset every day.
        if (status, message, evidence) == recorded:
            results.append({"case_id": case_id, "check": status, "recorded": False})
            continue
        try:
            record_platform_check(connection, tenant_id, case_id, CHECK_ID, status, message, evidence)
            connection.commit()
            results.append({"case_id": case_id, "check": status, "recorded": True,
                            "reports": len(evidence.get("reports") or [])})
        except (OnboardingError, ValueError) as error:
            connection.rollback()
            results.append({"case_id": case_id, "error": str(error)[:400]})
    return results
