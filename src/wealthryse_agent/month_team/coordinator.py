"""The coordinator: one stage machine per account and month.

Each hourly wake picks up every account where it left off:

    data -> (held) -> words -> package -> checking -> ready

A stage either moves on, or waits for something outside the team (the next
sync, Ying's decision, the month-end package job's files). Waiting writes nothing,
so the run record shows what happened, not how often the team looked.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Sequence

from ..tool_client import ToolCallFailed
from . import documents, figures, page_checks
from .agents import (ALPHA_WORLD_FACTSHEETS, Analyst, BudgetExhausted, DataAgent, Outcome, Reviewer, TeamModel,
                     VisualQA, Writer, commentary_items)
from .settings import MAX_REVIEW_ROUNDS, MAX_STEP_ATTEMPTS, TeamSettings
from .store import MonthStore

LOGGER = logging.getLogger("wealthryse_agent.month_team")
ToolCall = Callable[[str, dict[str, Any]], dict[str, Any]]
DATA_STEP = {"clean": "pass", "fixable": "waiting", "blocked": "blocked"}
RELEASED = ("released_internal", "published")


class StepFailed(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def month_label(period_end: str) -> str:
    return date.fromisoformat(period_end).strftime("%B %Y")


@dataclass
class Context:
    account_id: str
    period_end: str
    mode: str
    started: float
    live: bool = False
    decks: bool = False

    @property
    def month(self) -> bool:
        """The month being reported now, as opposed to a replay of a released one."""
        return self.mode == "month"

    @property
    def deck_line(self) -> bool:
        """Whether this account's deck has a commentary line. The factsheet never carries commentary."""
        return self.decks and self.account_id not in figures.DECKS_WITHOUT_COMMENTARY


class Coordinator:
    def __init__(self, store: MonthStore, call: ToolCall, model: TeamModel | Callable[[], TeamModel],
                 settings: TeamSettings, *, today: date | None = None,
                 download: documents.Downloader = documents.default_downloader,
                 clock: Callable[[], float] = time.monotonic, correlation_id: str | None = None,
                 render_pages: Callable[..., list[bytes]] = documents.pdf_page_images) -> None:
        self.store = store
        self.call = call
        self._model = model
        self.settings = settings
        self.today = today or date.today()
        self.download = download
        self.clock = clock
        self.correlation_id = correlation_id
        self.render_pages = render_pages
        self.data = DataAgent()
        self.analyst = Analyst()

    @property
    def model(self) -> TeamModel:
        if not isinstance(self._model, TeamModel):
            self._model = self._model()
        return self._model

    # Driving ---------------------------------------------------------------

    def run_month(self, accounts: Sequence[str], period_end: str, mode: str = "month") -> dict[str, int]:
        """Advance every account once. Stops at once, for every account, when the budget runs out."""
        counts: dict[str, int] = {}
        for account_id in accounts:
            stage = self.advance(account_id, period_end, mode)
            counts[stage] = counts.get(stage, 0) + 1
        return counts

    def advance(self, account_id: str, period_end: str, mode: str, replay_round: int | None = None) -> str:
        # A replay round other than the first checks the month afresh in its own run.
        run = (self.store.run(account_id, period_end, mode) if replay_round is None
               else self.store.run(account_id, period_end, mode, replay_round))
        if run["attempts"] >= MAX_STEP_ATTEMPTS:
            return "stopped"
        ctx = Context(account_id, period_end, mode, self.clock(), live=mode == "month" and self.settings.live,
                      decks=self.settings.decks)
        if run.get("live", False) != ctx.live:
            self.store.update_run(run["month_run_id"], live=ctx.live)
            run["live"] = ctx.live
        handlers = {"data": self._data, "held": self._held, "words": self._words, "package": self._package,
                    "checking": self._checking, "ready": self._ready}
        while True:
            if self.clock() - ctx.started > self.settings.report_time_limit_seconds:
                self._step(run, "coordinator", "waiting", "Reached the time limit for one report; continuing on the next run.")
                return run["stage"]
            stage = run["stage"]
            try:
                self._settle_decisions(run, ctx)
                following = handlers[stage](run, ctx)
            except BudgetExhausted:
                self._step(run, "coordinator", "waiting", "Paused: the team's monthly token budget is used up. "
                                                          "No check was skipped; the run resumes when budget is added.")
                raise
            except Exception as error:  # one account's failure never stops the others
                code = str(getattr(error, "code", "") or "internal_error")
                if code == "internal_error":
                    LOGGER.exception("month team step failed", extra={"stage": stage})
                attempts = int(run["attempts"]) + 1
                self._step(run, "coordinator", "failed", f"The {stage} stage failed ({code}); attempt {attempts} of "
                                                         f"{MAX_STEP_ATTEMPTS}.")
                self.store.update_run(run["month_run_id"], attempts=attempts, last_error=code[:200])
                LOGGER.warning("month team %s %s failed at %s: %s", account_id, period_end, stage, code)
                return "stopped" if attempts >= MAX_STEP_ATTEMPTS else stage
            if following is None or following == stage:
                return stage
            run["stage"] = following
            changes: dict[str, Any] = {"stage": following}
            if run["attempts"]:
                changes.update(attempts=0, last_error=None)
                run["attempts"] = 0
            self.store.update_run(run["month_run_id"], **changes)

    # Records ---------------------------------------------------------------

    def _step(self, run: Mapping[str, Any], agent: str, outcome: str, summary: str, *, round: int = 1,
              evidence: dict[str, Any] | None = None, usage: dict[str, int] | None = None) -> int:
        return self.store.add_step(run["month_run_id"], agent, outcome, summary, round=round, evidence=evidence,
                                   usage=usage, correlation_id=self.correlation_id)

    def _record(self, run: Mapping[str, Any], agent: str, result: Outcome, *, round: int = 1,
                outcome: str | None = None) -> int:
        step_id = self._step(run, agent, outcome or result.outcome, result.summary, round=round,
                             evidence=result.evidence, usage=result.usage)
        for objection in result.objections:
            self.store.add_objection(run["month_run_id"], step_id, raised_by=agent, target=objection["target"],
                                     message=objection["message"], page=objection.get("page"),
                                     region=objection.get("region"))
        return step_id

    # Stages ----------------------------------------------------------------

    def _data(self, run: dict[str, Any], ctx: Context) -> str | None:
        result = self.data.check(self.call, ctx.account_id, ctx.period_end, today=self.today, replay=not ctx.month)
        self._record(run, "data", result, outcome=DATA_STEP[result.outcome])
        self.store.update_run(run["month_run_id"], data_verdict=result.outcome)
        run["data_verdict"] = result.outcome
        if result.outcome == "clean" or (result.outcome == "blocked" and not ctx.month):
            return "words"
        if result.outcome == "fixable":
            return None
        findings = "\n".join(f"- {f['message']}" for f in result.evidence.get("findings", []))
        self.store.put_card(
            kind="month_end_hold", account_id=ctx.account_id, period_key=ctx.period_end,
            title=f"Hold or proceed with {ctx.account_id} for {month_label(ctx.period_end)}?",
            body=(f"The data agent found a problem it cannot wait out:\n\n{findings}\n\n" + (
                "Proceed lets the team write this account's deck commentary from the data as it is. Hold leaves "
                "this account's deck on the standard commentary. Either way, the problem above is still there when "
                "you release." if ctx.live and ctx.deck_line else
                "Proceed lets the team carry on with the data as it is. Hold stops the team for this account this "
                "month. Today's report process is unchanged either way.")),
            citations=[{"link": f"/accounts/{ctx.account_id}/reports", "period_end": ctx.period_end}])
        return "held"

    def _held(self, run: dict[str, Any], ctx: Context) -> str | None:
        decision = self.store.card_status("month_end_hold", ctx.account_id, ctx.period_end)
        if decision == "approved":
            self._step(run, "coordinator", "pass", "Ying chose to proceed with the data as it is.")
            return "words"
        if decision == "dismissed":
            return None
        result = self.data.check(self.call, ctx.account_id, ctx.period_end, today=self.today)
        if result.outcome != "clean":
            return None
        self._record(run, "data", result, outcome="pass")
        self.store.update_run(run["month_run_id"], data_verdict="clean")
        return "words"

    def _words(self, run: dict[str, Any], ctx: Context) -> str | None:
        analysis = self.analyst.figures(self.call, ctx.account_id, ctx.period_end)
        self._record(run, "analyst", analysis)
        if analysis.outcome != "pass":
            raise StepFailed("no_figures")
        figure_set = analysis.evidence["figure_set"]
        self.store.update_run(run["month_run_id"], figures=figure_set)
        run["figures"] = figure_set
        if not ctx.deck_line:
            self._step(run, "writer", "skipped", "No commentary written: " + (
                "this account's deck has no commentary line" if ctx.decks else "decks are no longer built")
                + ", and the factsheet never carries commentary.")
            return "package"
        previous = self.store.previous_commentary(ctx.account_id, ctx.period_end) if ctx.month else None
        writer, reviewer = Writer(self.model), Reviewer(self.model)
        objections: list[dict[str, Any]] = []
        draft = None
        status = "disputed"
        for round in range(1, MAX_REVIEW_ROUNDS + 1):
            written = writer.draft(figure_set, account_id=ctx.account_id, previous=(previous or {}).get("text"),
                                   objections=objections, last_draft=draft)
            self._record(run, "writer", written, round=round)
            if written.outcome != "pass":
                raise StepFailed("writer_rules")
            draft = written.evidence["draft"]
            review = reviewer.review_commentary(draft, figure_set, round=round)
            if review.outcome == "pass":
                self._record(run, "reviewer", review, round=round)
                self.store.settle_objections(run["month_run_id"], ["commentary"], "resolved",
                                             f"Fixed by the writer in round {round}.")
                status = "agreed"
                break
            self.store.settle_objections(run["month_run_id"], ["commentary"], "resolved",
                                         f"Replaced by the round {round} review.")
            self._record(run, "reviewer", review, round=round)
            objections = [{"field": o["field"], "index": o["index"], "message": o["message"]} for o in review.objections]
        commentary = {"text": written.evidence["commentary"], "sentences": commentary_items(draft)}
        self.store.update_run(run["month_run_id"], commentary=commentary, commentary_rounds=round,
                              commentary_status=status)
        run.update(commentary=commentary, commentary_status=status)
        if status == "disputed" and ctx.month:
            points = "\n".join(f"- {o['message']}" for o in review.objections)
            text = written.evidence["commentary"]
            self.store.put_card(
                kind="month_end_dispute", account_id=ctx.account_id, period_key=ctx.period_end,
                title=f"Commentary for {ctx.account_id}, {month_label(ctx.period_end)}: the writer and reviewer disagree",
                body=(f"After {round} rounds the reviewer still objects:\n\n{points}\n\nThe writer's text:\n\n"
                      f"{text['executive_summary']}\n\n" + "\n".join(f"- {s}" for s in
                      text["performance_observations"] + text["risk_observations"])
                      + ("\n\nKeep the writer's text and its summary goes into the deck. Agree with the reviewer and "
                         "the deck uses the standard commentary instead. The factsheet carries no commentary either way."
                         if ctx.live else
                         "\n\nThis is a shadow run, so today's report is unchanged whichever you choose.")),
                citations=[{"link": f"/accounts/{ctx.account_id}/reports", "period_end": ctx.period_end}])
        return "package"

    def _package(self, run: dict[str, Any], ctx: Context) -> str | None:
        package = self._current_package(ctx)
        if package is None:
            return None
        self._step(run, "coordinator", "pass", f"The month-end package is built ({len(package['files'])} file(s)); "
                                               "checking the files.", evidence={"package": package})
        self.store.update_run(run["month_run_id"], package=package)
        run["package"] = package
        return "checking"

    def _checking(self, run: dict[str, Any], ctx: Context) -> str | None:
        package = run["package"]
        files = {f["report_type"]: f for f in package["files"] if f.get("output_reference")}
        package_round = int((run.get("verdicts") or {}).get("package_round", 0)) + 1
        fetched: dict[str, documents.ReviewFile] = {}
        integrity: list[dict[str, Any]] = []
        for kind, file in files.items():
            try:
                fetched[kind] = documents.fetch(file, account_url=self.settings.storage_account_url,
                                                expected_container=self.settings.container, download=self.download)
            except documents.DocumentError as error:
                if error.code not in ("hash_mismatch", "unexpected_location"):
                    raise
                integrity.append({"target": kind, "page": None, "message": error.message})
        snapshot = None
        try:
            snapshot = self.call("get_month_end_snapshot", {"account_id": ctx.account_id,
                                                            "snapshot_id": package["snapshot_id"]})["data"]["payload"]
        except ToolCallFailed as error:
            if error.code not in ("snapshot_integrity_failed", "no_snapshot"):
                raise
            integrity.append({"target": "data", "page": None, "message": error.message})

        pages = documents.pdf_text(fetched["factsheet"].content) if "factsheet" in fetched else None
        slides = documents.pptx_text(fetched["presentation"].content) if "presentation" in fetched else None
        in_report = _carries_commentary(run, slides) if ctx.month and ctx.deck_line else None
        if ctx.live and in_report is False:
            integrity.append({"target": "presentation", "page": None,
                              "message": "The deck does not carry the team's agreed commentary; it was probably built "
                                         "with the standard commentary."})
        sentences = [item["text"] for item in (run.get("commentary") or {}).get("sentences") or []]
        for page in figures.factsheet_commentary(pages or [], sentences):
            integrity.append({"target": "factsheet", "page": page,
                              "message": "The factsheet prints commentary; the factsheet never carries commentary."})
        printed = page_checks.check_text(pages or [], date.fromisoformat(ctx.period_end))
        integrity += [{"target": "factsheet", **o} for o in printed.objections]
        review = Reviewer(self.model).check_files(
            account_id=ctx.account_id, period_end=ctx.period_end, snapshot=snapshot, factsheet_pages=pages,
            deck_slides=slides, figure_set=run.get("figures") or {}, inception_date=(run.get("figures") or {}).get("inception_date"))
        if printed.notes:
            # Template wording, the same every month: shown once with the evidence, not raised as objections.
            review.evidence["template_notes"] = printed.notes
        if integrity:
            review = Outcome("objection", f"{len(integrity) + len(review.objections)} objection(s) to the package files.",
                             review.evidence, integrity + review.objections, review.usage)
        self._record(run, "reviewer", review, round=package_round)

        visual: Outcome
        if "factsheet" in fetched:
            images = self.render_pages(fetched["factsheet"].content, dpi=self.settings.page_image_dpi)
            visual = VisualQA(self.model).inspect_factsheet(images, display_date=_display_date(ctx.period_end),
                                                            period_label=f"ending {_display_date(ctx.period_end)}",
                                                            account_id=ctx.account_id, page_text=pages,
                                                            period_end=ctx.period_end)
        else:
            visual = Outcome("skipped", "There is no factsheet in the package to look at.")
        self._record(run, "visual_qa", visual, round=package_round)
        if "presentation" in fetched:
            self._step(run, "visual_qa", "skipped", "Slides were not looked at: the package job does not save slide "
                                                    "images yet. The deck's text was checked by the reviewer.",
                       round=package_round)
        verdicts = {"package_round": package_round, "reviewer": review.outcome, "visual_qa": visual.outcome,
                    "reviewer_summary": review.summary, "visual_qa_summary": visual.summary,
                    "checked_generation_ids": sorted(int(f["generation_run_id"]) for f in files.values()),
                    "commentary_in_report": in_report, "deck_has_commentary": ctx.deck_line,
                    "owner_layout_checked": ctx.account_id in ALPHA_WORLD_FACTSHEETS}
        self.store.update_run(run["month_run_id"], verdicts=verdicts)
        run["verdicts"] = verdicts
        if ctx.month:
            self._report_card(run, ctx, reopen=package_round > 1)
        return "ready"

    def _ready(self, run: dict[str, Any], ctx: Context) -> str | None:
        if (ctx.account_id in ALPHA_WORLD_FACTSHEETS and not (run.get("verdicts") or {}).get("owner_layout_checked")
                and self.store.objections(run["month_run_id"], "open")):
            # Checked before visual QA knew the Alpha World layout, so its "missing table" objections were false alarms.
            self._step(run, "coordinator", "pass", "Checking the files again against the Alpha World factsheet layout.")
            self.store.settle_objections(run["month_run_id"], ["factsheet", "presentation", "data"], "resolved",
                                         "Replaced by a check against the Alpha World factsheet layout.")
            return "checking"
        package = self._current_package(ctx)
        if package is None or package == run.get("package"):
            return None
        if _identity(package) == _identity(run.get("package")):
            # Released or revoked since the check: the same files, so nothing to re-check.
            self.store.update_run(run["month_run_id"], package=package)
            run["package"] = package
            return None
        self._step(run, "coordinator", "pass", "The package was rebuilt; checking the new files.",
                   evidence={"package": package})
        self.store.settle_objections(run["month_run_id"], ["factsheet", "presentation", "data"], "resolved",
                                     "Replaced by the check of the rebuilt package.")
        self.store.update_run(run["month_run_id"], package=package)
        run["package"] = package
        return "checking"

    # Helpers ---------------------------------------------------------------

    def _current_package(self, ctx: Context) -> dict[str, Any] | None:
        try:
            package = self.call("get_month_end_package", {"account_id": ctx.account_id, "period_end": ctx.period_end})["data"]
        except ToolCallFailed as error:
            if error.code == "no_package":
                return None
            raise
        files = [f for f in package["files"] if f.get("output_reference")
                 and (ctx.decks or f["report_type"] != "presentation")]
        if not any(f["report_type"] == "factsheet" for f in files):
            return None
        if not ctx.month and any(f["status"] not in RELEASED for f in files):
            return None
        return {"snapshot_id": package["snapshot_id"], "snapshot_sha256": package["snapshot_sha256"],
                "quality_status": package["quality_status"], "files": files}

    def _settle_decisions(self, run: dict[str, Any], ctx: Context) -> None:
        """Apply Ying's answer on a disputed commentary, whatever stage the run has reached."""
        if run.get("commentary_status") != "disputed" or not ctx.month:
            return
        decision = self.store.card_status("month_end_dispute", ctx.account_id, ctx.period_end)
        if decision not in ("approved", "dismissed"):
            return
        upheld = "writer_upheld" if decision == "approved" else "reviewer_upheld"
        self.store.settle_objections(
            run["month_run_id"], ["commentary"], "overruled" if decision == "approved" else "resolved",
            "Ying kept the writer's text." if decision == "approved" else "Ying agreed with the reviewer.")
        self._step(run, "coordinator", "pass", "Ying settled the commentary: " + (
            "kept the writer's text." if decision == "approved" else "agreed with the reviewer."))
        self.store.update_run(run["month_run_id"], commentary_status=upheld)
        run["commentary_status"] = upheld

    def _report_card(self, run: Mapping[str, Any], ctx: Context, *, reopen: bool) -> None:
        open_items = self.store.objections(run["month_run_id"], "open")
        verdicts = run["verdicts"]
        commentary = {"agreed": f"agreed after {run.get('commentary_rounds') or 1} round(s)",
                      "disputed": "the writer and reviewer disagree (see the separate card)",
                      "writer_upheld": "you kept the writer's text", "reviewer_upheld": "you agreed with the reviewer",
                      }.get(str(run.get("commentary_status")),
                            "not drafted" if ctx.deck_line else
                            "none, as this account's deck has no commentary line" if ctx.decks else
                            "none, as decks are no longer built and the factsheet carries no commentary")
        carried = {True: "; the deck carries it", False: "; the deck does not carry it"}.get(
            verdicts.get("commentary_in_report"), "") if ctx.live else ""
        lines = [f"- Data: {run.get('data_verdict') or 'not checked'}",
                 f"- Commentary: {commentary}{carried}",
                 f"- Printed figures{' and deck' if ctx.decks else ''}: {verdicts['reviewer_summary']}",
                 f"- Pages: {verdicts['visual_qa_summary']}"]
        if open_items:
            lines.append("\nObjections:")
            lines += [f"- {o['target'].capitalize()}{', page ' + str(o['page']) if o.get('page') else ''}: {o['message']}"
                      for o in open_items[:8]]
            if len(open_items) > 8:
                lines.append(f"- and {len(open_items) - 8} more on the Reports tab.")
        lines.append("\nReview the evidence on the account's Reports tab and release there as usual. Release warns "
                     "while an objection is open." if ctx.live else
                     "\nThis is a shadow run: today's files and release process are unchanged. Review the evidence "
                     "on the account's Reports tab and release there as usual.")
        verdict = f"{len(open_items)} objection(s) to review" if open_items else "checks passed"
        self.store.put_card(kind="month_end_report", account_id=ctx.account_id, period_key=ctx.period_end,
                            title=f"{ctx.account_id}, {month_label(ctx.period_end)}: {verdict}", body="\n".join(lines),
                            citations=[{"link": f"/accounts/{ctx.account_id}/reports", "period_end": ctx.period_end}],
                            reopen=reopen)


def _carries_commentary(run: Mapping[str, Any], slides: Sequence[str] | None) -> bool | None:
    """Whether the deck prints the team's settled executive summary; None when there is nothing to compare."""
    text = ((run.get("commentary") or {}).get("text") or {}).get("executive_summary")
    if slides is None or not text or run.get("commentary_status") not in ("agreed", "writer_upheld"):
        return None
    printed = " ".join(c["text"] for c in figures.deck_commentary(slides))
    return " ".join(text.split()) in " ".join(printed.split())


def _identity(package: Mapping[str, Any] | None) -> list[tuple[str, int, str]]:
    return sorted((f["report_type"], int(f["generation_run_id"]), str(f.get("output_sha256")))
                  for f in (package or {}).get("files", []))


def _display_date(period_end: str) -> str:
    parsed = date.fromisoformat(period_end)
    return f"{parsed.day} {parsed.strftime('%b')} {parsed.year}"
