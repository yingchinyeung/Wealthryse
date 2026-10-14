"""Stage 3 reports for a composite in the Alpha World MS or T design.

Once approval 1 has registered the composite and the daily refresh has kept its
history (the case's history_built check), this run renders one factsheet for
every completed month since inception in the composite's design, stores each
privately where the month-end packages go, and records the case's
reports_rendered check with the list of files. Approval 2 is bound to that list.

A month is frozen only once its month-end calculation has run, by the same rule
the month-end package applies to derived portfolios, and the frozen snapshot is
the one the month-end package later reuses. A month already rendered from the
same snapshot and design is reused, so a daily run renders only a newly
completed month, and an unchanged list is not recorded again.
"""

from __future__ import annotations

import calendar
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from .historical_reporting import queue_report_generation_run
from .monthly_artifacts import render_factsheet
from .onboarding_v2_web import OnboardingError, _evidence, record_platform_check
from .profiled_factsheet import DESIGNS, LAYOUT_VERSION
from .report_profiles import ReportProfileError, load_profile
from .reporting_snapshot import active_month_end_snapshot_id, freeze_month_end_snapshot
from .util import utc_now_iso

HONG_KONG = ZoneInfo("Asia/Hong_Kong")
CHECK_ID = "reports_rendered"
PDF = "application/pdf"
# The month-end package's limit on how far before the month end its last NAV may be.
STALE_AFTER_DAYS = 7


class CompositeReportError(ValueError):
    """Why a case's reports could not be rendered, as the case page shows it."""


@dataclass(frozen=True)
class DesignAssets:
    """The design's own current approved factsheet files, as registered for MS or T."""

    style: str
    template: Mapping[str, Any]
    completed_example: Mapping[str, Any]

    @property
    def template_version(self) -> str:
        # The month-end package's version format, so its runs and these line up.
        template_sha, design_sha = str(self.template["sha256"]), str(self.completed_example["sha256"])
        if design_sha != template_sha:
            return f"sha256:{design_sha}+supporting-sha256:{template_sha}+layout:{LAYOUT_VERSION}"
        return f"sha256:{template_sha}+layout:{LAYOUT_VERSION}"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def report_months(inception: date, today: date) -> list[str]:
    """Each calendar month end after inception up to the last completed month, oldest first."""

    last = today.replace(day=1) - timedelta(days=1)
    months, year, month = [], inception.year, inception.month
    while True:
        end = date(year, month, calendar.monthrange(year, month)[1])
        if end > last:
            return months
        if end > inception:
            months.append(end.isoformat())
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def month_end_ready(connection, subject_id: str, period_end: str) -> bool:
    """The month-end package's rule for a derived portfolio's month: NAV and
    components on the same recent day, from a calculation run after the month ended."""

    nav = connection.execute(
        "SELECT MAX(snapshot_date) AS day FROM subject_nav_series WHERE subject_id = ? AND snapshot_date <= ?",
        (subject_id, period_end),
    ).fetchone()
    day = str(nav["day"])[:10] if nav and nav["day"] else ""
    if not day or (date.fromisoformat(period_end) - date.fromisoformat(day)).days > STALE_AFTER_DAYS:
        return False
    components = connection.execute(
        "SELECT MAX(snapshot_date) AS day FROM synthetic_portfolio_components "
        "WHERE portfolio_id = ? AND snapshot_date <= ?",
        (subject_id, period_end),
    ).fetchone()
    if not components or str(components["day"])[:10] != day:
        return False
    run = connection.execute(
        "SELECT completed_at FROM subject_calculation_runs WHERE subject_id = ? AND requested_to >= ? "
        "AND status IN ('validated', 'published') ORDER BY completed_at DESC LIMIT 1",
        (subject_id, day),
    ).fetchone()
    if not run or not run["completed_at"]:
        return False
    completed = run["completed_at"]
    if not isinstance(completed, datetime):
        completed = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
    if completed.tzinfo is None:
        completed = completed.replace(tzinfo=timezone.utc)
    return completed.astimezone(HONG_KONG).date() > date.fromisoformat(period_end)


def design_assets(connection, style: str) -> DesignAssets:
    """The design's own current approved factsheet files.

    A composite reports in the design of Alpha World MS or T, so its sources are
    the files approved for that subject, never a copy registered elsewhere.
    """

    rows = connection.execute(
        """
        SELECT asset_role, container_name, blob_name, sha256
        FROM subject_report_template_assets
        WHERE subject_id = ? AND report_type = 'factsheet'
          AND asset_role IN ('template', 'completed_example') AND is_current
          AND approval_status IN ('validated', 'active')
        ORDER BY updated_at DESC
        """,
        (style,),
    ).fetchall()
    assets: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        assets.setdefault(str(row["asset_role"]), dict(row))
    if set(assets) != {"template", "completed_example"}:
        raise CompositeReportError(f"The {style} design has no approved factsheet template and completed example")
    if str(assets["completed_example"]["sha256"]) != DESIGNS[style]["authority"]:
        raise CompositeReportError(f"The {style} design's completed factsheet is not its approved authority")
    if style == "alpha-world-t" and assets["template"]["sha256"] != assets["completed_example"]["sha256"]:
        raise CompositeReportError("The T design has one source, its owner template")
    return DesignAssets(style, assets["template"], assets["completed_example"])


def fetch_sources(assets: DesignAssets, folder: Path, *,
                  fetch: Callable[[str, str], bytes]) -> dict[str, Path | None]:
    """Download and check the design's files; returns render_factsheet's source arguments."""

    paths = {}
    for role, row in (("template", assets.template), ("completed_example", assets.completed_example)):
        content = fetch(str(row["container_name"]), str(row["blob_name"]))
        if _sha256(content) != str(row["sha256"]):
            raise CompositeReportError(f"The {assets.style} design's approved {role.replace('_', ' ')} "
                                       "failed its check")
        paths[role] = folder / f"{assets.style}-{role}.pdf"
        paths[role].write_bytes(content)
    # MS renders from its completed factsheet with its template as support; T from its owner template.
    return {"template_path": paths["template"],
            "design_model_path": paths["completed_example"] if assets.style == "alpha-world-ms" else None}


def eligible_cases(connection, tenant_id: str) -> list[dict]:
    """Composite cases in stage 3 with a report design whose history has been built."""

    rows = connection.execute(
        """
        SELECT c.case_id, c.target_subject_id, c.definition_sha256, c.report_style,
               k.status AS recorded, k.message AS recorded_message, k.evidence_json
        FROM broker_onboarding_cases c
        LEFT JOIN broker_onboarding_checks k
          ON k.tenant_id = c.tenant_id AND k.case_id = c.case_id AND k.check_id = 'reports_rendered'
        WHERE c.tenant_id = ? AND c.source_kind = 'composite_portfolio' AND c.report_style IS NOT NULL
          AND c.stage = 'build_history' AND c.status <> 'cancelled'
          AND EXISTS (SELECT 1 FROM broker_onboarding_checks k WHERE k.tenant_id = c.tenant_id
                        AND k.case_id = c.case_id AND k.check_id = 'history_built' AND k.status = 'pass')
        ORDER BY c.created_at
        """,
        (tenant_id,),
    ).fetchall()
    return [{**dict(row), "case_id": str(row["case_id"])} for row in rows]


def _frozen_payload(connection, tenant_id: str, subject_id: str, snapshot_id: str) -> tuple[dict, str]:
    row = connection.execute(
        "SELECT snapshot_payload_json, data_sha256 FROM reporting_snapshots "
        "WHERE tenant_id = ? AND snapshot_id = ? AND subject_id = ? AND status IN ('frozen', 'approved')",
        (tenant_id, snapshot_id, subject_id),
    ).fetchone()
    if row is None:
        raise CompositeReportError("The frozen month-end snapshot is unavailable")
    text = str(row["snapshot_payload_json"])
    if _sha256(text.encode("utf-8")) != str(row["data_sha256"]):
        raise CompositeReportError("The frozen month-end snapshot failed its integrity check")
    return json.loads(text), str(row["data_sha256"])


def _existing_run(connection, tenant_id: str, snapshot_id: str, template_id: str,
                  template_version: str) -> Mapping[str, Any] | None:
    return connection.execute(
        """
        SELECT generation_run_id, output_reference, output_sha256 FROM report_generation_runs
        WHERE tenant_id = ? AND snapshot_id = CAST(? AS uuid) AND report_type = 'factsheet'
          AND template_id = ? AND template_version = ?
          AND status IN ('awaiting_approval', 'released_internal')
          AND output_reference IS NOT NULL AND output_sha256 IS NOT NULL
        ORDER BY generation_run_id DESC LIMIT 1
        """,
        (tenant_id, snapshot_id, template_id, template_version),
    ).fetchone()


def _store(store: Callable[[str, str, bytes, str], None], fetch: Callable[[str, str], bytes],
           container: str, blob: str, content: bytes) -> None:
    store(container, blob, content, PDF)
    if fetch(container, blob) != content:
        raise CompositeReportError("A rendered factsheet did not read back from private storage")


def render_case_reports(connection, tenant_id: str, case: Mapping[str, Any], *, container: str,
                        today: date, fetch: Callable[[str, str], bytes],
                        store: Callable[[str, str, bytes, str], None],
                        render: Callable[..., Mapping[str, Any]] = render_factsheet) -> dict:
    """Render the case's completed months and return the reports_rendered evidence."""

    subject_id, style = str(case["target_subject_id"]), str(case["report_style"])
    try:
        profile = load_profile(connection, subject_id, tenant_id=tenant_id)
    except ReportProfileError as error:
        raise CompositeReportError(str(error)) from error
    if profile is None or profile.built_in or profile.style != style:
        raise CompositeReportError(f"{subject_id} has no {style} report profile")
    if profile.definition_sha256 != str(case["definition_sha256"]):
        raise CompositeReportError("The report profile is not bound to this case's approved definition")
    template_id = f"{subject_id}-monthly-factsheet"
    assets = design_assets(connection, style)
    reports: list[dict] = []
    waiting_for = None
    with TemporaryDirectory(prefix=f"{subject_id}-stage-3-") as directory:
        root = Path(directory)
        sources: dict[str, Path | None] | None = None
        for period_end in report_months(date.fromisoformat(profile.inception), today):
            snapshot_id = active_month_end_snapshot_id(connection, tenant_id=tenant_id, subject_id=subject_id,
                                                       period_end=period_end)
            if snapshot_id is None:
                if not month_end_ready(connection, subject_id, period_end):
                    waiting_for = period_end  # a later month cannot be ready before this one
                    break
                snapshot_id = freeze_month_end_snapshot(
                    connection, tenant_id=tenant_id, subject_id=subject_id, period_start=profile.inception,
                    period_end=period_end, reporting_currency=profile.reporting_currency,
                    reject_quality_fail=True).snapshot_id
            file_name = f"{subject_id}_Factsheet_{period_end}.pdf"
            existing = _existing_run(connection, tenant_id, str(snapshot_id), template_id, assets.template_version)
            if existing is not None:
                run_id, reference = int(existing["generation_run_id"]), str(existing["output_reference"])
                digest = str(existing["output_sha256"])
            else:
                if sources is None:
                    sources = fetch_sources(assets, root, fetch=fetch)
                payload, _ = _frozen_payload(connection, tenant_id, subject_id, str(snapshot_id))
                output = root / file_name
                result = render(payload, output_path=output, **sources)
                if (result.get("pdf_accuracy_validation") or {}).get("status") != "pass":
                    raise CompositeReportError(f"The {period_end} factsheet failed its read-back check")
                content = output.read_bytes()
                output.unlink()
                digest = _sha256(content)
                blob = f"review/{subject_id}/{period_end}/{digest}-{file_name}"
                _store(store, fetch, container, blob, content)
                run_id = queue_report_generation_run(connection, tenant_id=tenant_id, snapshot_id=str(snapshot_id),
                                                     report_type="factsheet", template_id=template_id,
                                                     template_version=assets.template_version)
                reference = f"{container}/{blob}"
                connection.execute(
                    "UPDATE report_generation_runs SET status = 'awaiting_approval', output_reference = ?, "
                    "output_sha256 = ?, completed_at = ?, error_message = NULL "
                    "WHERE tenant_id = ? AND generation_run_id = ? AND status NOT IN ('released_internal', 'published')",
                    (reference, digest, utc_now_iso(), tenant_id, run_id),
                )
                connection.commit()
            reports.append({
                "period_end": period_end, "report_type": "factsheet", "file_name": file_name, "sha256": digest,
                "generation_run_id": run_id, "snapshot_id": str(snapshot_id), "output_reference": reference,
                "download_path": f"/api/onboarding-v2/cases/{case['case_id']}/reports/{run_id}",
            })
    return {"design": style, "layout_version": LAYOUT_VERSION, "template_version": assets.template_version,
            "reports": reports, "waiting_for": waiting_for}


def _message(evidence: Mapping[str, Any]) -> tuple[str, str]:
    reports, waiting_for = evidence["reports"], evidence["waiting_for"]
    design = "Alpha World MS" if evidence["design"] == "alpha-world-ms" else "Alpha World T"
    if not reports:
        if waiting_for:
            return "pending", f"Waiting for the {waiting_for} month-end calculation before rendering."
        return "warn", "No month has ended since inception, so there is no factsheet to review yet."
    first, last = reports[0]["period_end"], reports[-1]["period_end"]
    span = f"for {first}" if first == last else f"from {first} to {last}"
    count = "1 monthly factsheet" if len(reports) == 1 else f"{len(reports)} monthly factsheets"
    later = f" {waiting_for} follows once its month-end calculation has run." if waiting_for else ""
    return "pass", f"{count} {span} in the {design} design, each read back and checked.{later}"


def _verify_reused(evidence: Mapping[str, Any], fetch: Callable[[str, str], bytes]) -> None:
    for item in evidence["reports"]:
        container, _, blob = str(item["output_reference"]).partition("/")
        if _sha256(fetch(container, blob)) != item["sha256"]:
            raise CompositeReportError(f"The stored {item['period_end']} factsheet failed its integrity check")


def run_composite_report_cases(connection, tenant_id: str, *, container: str, today: date,
                               fetch: Callable[[str, str], bytes], store: Callable[[str, str, bytes, str], None],
                               render: Callable[..., Mapping[str, Any]] = render_factsheet) -> list[dict]:
    """Render and record every eligible case. One case's failure never stops the others."""

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
