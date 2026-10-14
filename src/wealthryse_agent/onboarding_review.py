"""The history reviewer: the onboarding agent that reads a new composite's past reports before go-live.

Stage 3 of an onboarding case renders a factsheet and a deck for every month
since the composite's inception, in the Alpha World MS or T design under the
composite's own name (ibkr_etl.composite_reports). Before the owner approves
those historical reports, this agent reads every stored file, as the
month-end team reads each month's package:

  - every month from the first to the last has both files, with no gap;
  - each stored file is the one recorded when it was rendered (its hash);
  - each factsheet has the design's two pages, prints the composite's own
    name and the month's date, carries no commentary (commentary belongs in
    the deck only) and no text left over from the reference design;
  - each deck names the composite and carries no MS or T identity text;
  - visual QA looks at every factsheet page with the Alpha World layout's
    checklist, on the APAC report deployment.

The review is written to agent.onboarding_reviews, bound to the exact
reports_rendered evidence it read. The platform records it on the case as the
agent_review_complete check (ibkr_etl.onboarding_agent_review). The agent
never writes a case, a check or a report.

    python -m wealthryse_agent.jobs onboarding
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from ibkr_etl.onboarding_agent_review import reports_digest

from .config import _int
from .month_team import documents
from .month_team.agents import TeamModel, TeamModelError, VisualQA

LOGGER = logging.getLogger("wealthryse_agent.onboarding_review")
REVIEW_KINDS = ("factsheet", "presentation")
# Text from the reference designs that must not survive in a composite's files unless the composite's own
# name, description or tree uses it (the renderers' own read-back lists: profiled_factsheet.FOREIGN_TEXT and
# scripts/audit_composite_presentation.py).
FACTSHEET_FOREIGN_TEXT = ("Alpha World Enhanced Index", "Alpha World T Enhanced Index", "Greater China",
                          "China & Hong Kong", "US Technology", "S&P500", "130% LONG", "70% SHORT", "150% LONG",
                          "150% SHORT")
DECK_FOREIGN_TEXT = ("Alpha World MS", "Alpha World T", "UST 3-month", "FOCUS US Tech")
COMMENTARY_PREFIX = "Portfolio analyst:"
SUMMARY_LIMIT = 1000


@dataclass(frozen=True)
class ReviewSettings:
    storage_account_url: str
    container: str
    monthly_token_budget: int
    page_image_dpi: int = 110
    # Visual QA looks at this many of the newest months; any older ones make the review a warning, never a pass.
    visual_months: int = 36

    @classmethod
    def from_env(cls) -> "ReviewSettings":
        from .config import _env

        return cls(
            storage_account_url=_env("AZURE_STORAGE_ACCOUNT_URL").rstrip("/"),
            container=_env("INTERNAL_REPORT_CONTAINER", "internal-reports"),
            monthly_token_budget=_int("AGENT_ONBOARDING_TOKEN_BUDGET", 5_000_000, 100_000, 100_000_000),
            page_image_dpi=_int("AGENT_MONTH_TEAM_PAGE_DPI", 110, 50, 200),
            visual_months=_int("AGENT_ONBOARDING_VISUAL_MONTHS", 36, 1, 240),
        )

    def validate(self) -> None:
        if not self.storage_account_url.startswith("https://"):
            raise ValueError("AZURE_STORAGE_ACCOUNT_URL must be the private report storage account")


@dataclass
class Review:
    outcome: str  # pass, warn or fail, as the case check will show it
    summary: str
    findings: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, int] = field(default_factory=dict)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _month_index(period_end: str) -> int:
    day = date.fromisoformat(period_end)
    return day.year * 12 + day.month


def header_date(period_end: str) -> str:
    """The date the Alpha World designs print on each factsheet page (profiled_factsheet.validate)."""
    day = date.fromisoformat(period_end)
    return f"{day:%B} {day.day} {day.year}"


def month_label(period_end: str) -> str:
    return date.fromisoformat(period_end).strftime("%B %Y")


def _finding(severity: str, period_end: str | None, report_type: str | None, message: str,
             page: int | None = None) -> dict[str, Any]:
    return {"severity": severity, "period_end": period_end, "report_type": report_type, "page": page,
            "message": message[:500]}


def coverage_findings(reports: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Every month from the first to the last, each with exactly one factsheet and one deck."""
    findings = []
    months: dict[str, list[str]] = {}
    for item in reports:
        months.setdefault(str(item["period_end"]), []).append(str(item["report_type"]))
    for period_end, kinds in sorted(months.items()):
        for kind in REVIEW_KINDS:
            count = kinds.count(kind)
            if count != 1:
                what = "factsheet" if kind == "factsheet" else "deck"
                findings.append(_finding("high", period_end, kind, (
                    f"{month_label(period_end)} has no {what}." if count == 0 else
                    f"{month_label(period_end)} lists {count} {what}s; there should be one.")))
    ordered = sorted(months)
    for previous, current in zip(ordered, ordered[1:]):
        gap = _month_index(current) - _month_index(previous) - 1
        if gap > 0:
            findings.append(_finding("high", current, None, (
                f"{gap} month(s) between {month_label(previous)} and {month_label(current)} have no reports.")))
    return findings


def factsheet_findings(pages: Sequence[str], *, period_end: str, display_name: str, own_text: str) -> list[dict]:
    findings = []
    flat = [_flat(page) for page in pages]
    if len(flat) != 2:
        findings.append(_finding("high", period_end, "factsheet",
                                 f"The factsheet has {len(flat)} pages; the design has two."))
    if flat and _flat(display_name) not in flat[0]:
        findings.append(_finding("high", period_end, "factsheet", "Page 1 does not print the composite's name.", 1))
    wanted = header_date(period_end)
    for number, text in enumerate(flat[:2], 1):
        if wanted not in text:
            findings.append(_finding("high", period_end, "factsheet",
                                     f"Page {number} does not show the report date {wanted}.", number))
        if COMMENTARY_PREFIX in text:
            findings.append(_finding("high", period_end, "factsheet", (
                f"Page {number} prints commentary; commentary belongs in the deck only."), number))
    for foreign in FACTSHEET_FOREIGN_TEXT:
        if foreign not in own_text:
            for number, text in enumerate(flat, 1):
                if foreign in text:
                    findings.append(_finding("high", period_end, "factsheet", (
                        f"Page {number} still prints '{foreign}' from the reference design."), number))
    return findings


def deck_findings(slides: Sequence[str], *, period_end: str, display_name: str, own_text: str) -> list[dict]:
    if not slides:
        return [_finding("high", period_end, "presentation", "The deck has no slides.")]
    findings = []
    flat = [_flat(slide) for slide in slides]
    if not any(_flat(display_name) in text for text in flat):
        findings.append(_finding("high", period_end, "presentation", "No slide names the composite."))
    for foreign in DECK_FOREIGN_TEXT:
        if foreign not in own_text:
            hits = [number for number, text in enumerate(flat, 1) if foreign in text]
            if hits:
                findings.append(_finding("high", period_end, "presentation", (
                    f"Slide {hits[0]} still names '{foreign}' from the reference design"
                    + (f" (and {len(hits) - 1} more slide(s))." if len(hits) > 1 else ".")), hits[0]))
    return findings


PageRenderer = Callable[..., list[bytes]]


class HistoryReviewer:
    def __init__(self, settings: ReviewSettings, model: Callable[[], TeamModel], *,
                 download: documents.Downloader = documents.default_downloader,
                 render_pages: PageRenderer = documents.pdf_page_images) -> None:
        self.settings = settings
        self._model_factory = model
        self._model: TeamModel | None = None
        self.download = download
        self.render_pages = render_pages

    def model(self) -> TeamModel:
        if self._model is None:
            self._model = self._model_factory()
        return self._model

    def review(self, case: Mapping[str, Any]) -> Review:
        evidence_in = case["reports_rendered"]
        reports = sorted(evidence_in.get("reports") or [], key=lambda r: (str(r["period_end"]), str(r["report_type"])))
        display_name = str(case["display_name"])
        own_text = " ".join([display_name, str(case.get("description") or "")])
        findings = coverage_findings(reports)
        usage: dict[str, int] = {}
        factsheets: dict[str, bytes] = {}
        files_checked = 0
        for item in reports:
            period_end, kind = str(item["period_end"]), str(item["report_type"])
            if kind not in REVIEW_KINDS:
                continue
            record = {"output_reference": item.get("output_reference"), "output_sha256": item.get("sha256"),
                      "report_type": kind, "generation_run_id": item.get("generation_run_id") or 0}
            try:
                file = documents.fetch(record, account_url=self.settings.storage_account_url,
                                       expected_container=self.settings.container, download=self.download)
                if kind == "factsheet":
                    pages = documents.pdf_text(file.content)
                    findings += factsheet_findings(pages, period_end=period_end, display_name=display_name,
                                                   own_text=own_text)
                    factsheets[period_end] = file.content
                else:
                    slides = documents.pptx_text(file.content)
                    findings += deck_findings(slides, period_end=period_end, display_name=display_name,
                                              own_text=own_text)
                files_checked += 1
            except documents.DocumentError as error:
                findings.append(_finding("high", period_end, kind, error.message))
        visual = self._visual(factsheets, findings, usage)
        months = sorted({str(item["period_end"]) for item in reports})
        evidence = {"files_checked": files_checked, "files_listed": len(reports),
                    "months": {"first": months[0] if months else None, "last": months[-1] if months else None,
                               "count": len(months)},
                    "design": evidence_in.get("design"), "visual_qa": visual,
                    "checks": ["every month has a factsheet and a deck, with no gap",
                               "each stored file matches the hash recorded when it was rendered",
                               "factsheet: two pages, the composite's name and each page's report date, "
                               "no commentary, no text left from the reference design",
                               "deck: names the composite, no MS or T identity text",
                               "visual QA of every factsheet page against the Alpha World layout"]}
        return Review(*_verdict(findings, visual, files_checked, months, evidence_in.get("design")), findings,
                      evidence, usage)

    def _visual(self, factsheets: Mapping[str, bytes], findings: list[dict], usage: dict[str, int]) -> dict[str, Any]:
        newest = sorted(factsheets)[-self.settings.visual_months:]
        looked: list[str] = []
        notes: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []
        skipped = None
        qa = VisualQA(self.model()) if newest else None
        for period_end in reversed(newest):
            try:
                images = self.render_pages(factsheets[period_end], dpi=self.settings.page_image_dpi)
            except documents.DocumentError as error:
                findings.append(_finding("high", period_end, "factsheet", error.message))
                continue
            try:
                outcome = qa.inspect_factsheet(images, display_date=header_date(period_end),
                                               period_label=month_label(period_end), alpha_world_layout=True)
            except TeamModelError as error:
                errors.append({"period_end": period_end, "error": error.code})
                continue
            for key, value in (outcome.usage or {}).items():
                usage[key] = usage.get(key, 0) + int(value)
            if outcome.outcome == "skipped":
                skipped = outcome.summary
                break
            looked.append(period_end)
            notes += [{"period_end": period_end, **n} for n in outcome.evidence.get("cosmetic_notes", [])]
            for objection in outcome.objections:
                severity = "high" if "(high)" in objection["message"] else "medium"
                findings.append({**_finding(severity, period_end, "factsheet", objection["message"],
                                            objection.get("page")), "region": objection.get("region")})
        return {"months_looked_at": len(looked), "months_with_factsheets": len(factsheets),
                "not_looked_at": sorted(set(factsheets) - set(looked)), "skipped": skipped,
                "model_errors": errors, "cosmetic_notes": notes[:20]}


def _verdict(findings: Sequence[Mapping[str, Any]], visual: Mapping[str, Any], files_checked: int,
             months: Sequence[str], design: str | None) -> tuple[str, str]:
    design_name = {"alpha-world-ms": "Alpha World MS", "alpha-world-t": "Alpha World T"}.get(str(design), "its")
    span = (f"{month_label(months[0])}" if len(months) == 1 else
            f"{month_label(months[0])} to {month_label(months[-1])}") if months else "no months"
    high = [f for f in findings if f["severity"] == "high"]
    medium = [f for f in findings if f["severity"] == "medium"]
    if not months:
        return "warn", "There are no rendered reports to review yet."
    if high:
        points = "; ".join(_point(f) for f in high[:3])
        more = f" and {len(high) - 3} more" if len(high) > 3 else ""
        return "fail", _fit(f"The agent review found {len(high)} problem(s) in the historical reports for {span}: "
                            f"{points}{more}. Each is listed with its month and page.")
    gaps = []
    if visual.get("skipped"):
        gaps.append(f"visual QA did not look at the pages ({visual['skipped']})")
    elif visual.get("not_looked_at"):
        missed = len(visual["not_looked_at"])
        gaps.append(f"visual QA could not look at {missed} month(s): " + ", ".join(
            month_label(m) for m in visual["not_looked_at"][:6]) + (" and more" if missed > 6 else ""))
    if medium or gaps:
        parts = [f"{len(medium)} layout point(s) to look at: " + "; ".join(_point(f) for f in medium[:3])] if medium else []
        return "warn", _fit(f"The agent review read {files_checked} files for {span} in the {design_name} design with "
                            f"no blocking problem, but " + " and ".join(parts + gaps) + ".")
    return "pass", _fit(f"The agent review read all {files_checked} files for {span} in the {design_name} design: "
                        "every month is there, every file matches its recorded hash, each factsheet prints the "
                        "composite's name and date with no commentary or reference-design text, each deck names "
                        "the composite, and visual QA found no layout faults.")


def _point(finding: Mapping[str, Any]) -> str:
    where = month_label(finding["period_end"]) if finding.get("period_end") else ""
    kind = {"factsheet": "factsheet", "presentation": "deck"}.get(str(finding.get("report_type")), "")
    prefix = " ".join(p for p in (where, kind) if p)
    return f"{prefix}: {finding['message']}" if prefix else str(finding["message"])


def _fit(text: str) -> str:
    return text if len(text) <= SUMMARY_LIMIT else text[:SUMMARY_LIMIT - 1].rstrip() + "…"


# Storage and the job -------------------------------------------------------------


class ReviewStore:
    """agent.onboarding_reviews, written by the agent_writer role."""

    def __init__(self, connection: Any, tenant_id: str) -> None:
        self.db = connection
        self.tenant_id = tenant_id

    def reviewed(self, case_id: str, digest: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM agent.onboarding_reviews WHERE tenant_id = ?::uuid AND case_id = ?::uuid AND reports_sha256 = ?",
            (self.tenant_id, case_id, digest),
        ).fetchone()
        self.db.rollback()
        return row is not None

    def save(self, case: Mapping[str, Any], digest: str, review: Review, correlation_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO agent.onboarding_reviews(tenant_id, case_id, subject_id, reports_sha256, outcome, summary, "
            "findings, evidence, usage, correlation_id) VALUES (?::uuid, ?::uuid, ?, ?, ?, ?, ?::jsonb, ?::jsonb, "
            "?::jsonb, ?) ON CONFLICT (tenant_id, case_id, reports_sha256) DO NOTHING",
            (self.tenant_id, case["case_id"], case["subject_id"], digest, review.outcome, review.summary,
             json.dumps(review.findings, default=str), json.dumps(review.evidence, default=str),
             json.dumps(review.usage), correlation_id),
        )
        self.db.commit()

    def tokens_used_this_month(self) -> int:
        row = self.db.execute(
            "SELECT COALESCE(SUM((usage->>'input_tokens')::bigint + (usage->>'output_tokens')::bigint), 0) AS used "
            "FROM agent.runs WHERE tenant_id = ?::uuid AND job_name = 'onboarding' "
            "AND started_at >= date_trunc('month', now()) AND (usage->>'input_tokens') IS NOT NULL",
            (self.tenant_id,),
        ).fetchone()
        self.db.rollback()
        return int(row["used"] or 0)


def review_waiting_cases(store: ReviewStore, call: Callable[[str, dict], dict], reviewer: HistoryReviewer,
                         usage: dict[str, int], correlation_id: str | None = None) -> int:
    """Review every case whose current reports have no review yet. Returns how many were reviewed."""
    reviewed = 0
    for case in call("list_onboarding_report_cases", {})["data"]["cases"]:
        digest = reports_digest(case["reports_rendered"])
        if store.reviewed(case["case_id"], digest):
            continue
        review = reviewer.review(case)
        for key, value in review.usage.items():
            usage[key] = usage.get(key, 0) + int(value)
        store.save(case, digest, review, correlation_id)
        LOGGER.info("onboarding review of %s: %s (%s finding(s))", case["subject_id"], review.outcome,
                    len(review.findings))
        reviewed += 1
    return reviewed
