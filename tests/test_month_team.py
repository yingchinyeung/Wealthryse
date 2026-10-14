"""The month-end agent team: planted figure errors, planted layout faults, and the coordinator's stages.

No database, network or model: tools, storage and the model are fakes, while
the factsheet is a real PDF rendered by the platform's own renderer, so the
reviewer's printed-figure checks read genuine output. The database side of
the team (tables, grants, tools) is covered in test_wealthryse_agent_db.py.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from pypdf import PdfWriter  # noqa: E402

from ibkr_etl.monthly_artifacts import ALPHA_WORLD_SUBJECT_IDS, render_factsheet  # noqa: E402
from test_monthly_artifacts import _snapshot  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.month_team import documents, figures as fig  # noqa: E402
from wealthryse_agent.month_team.agents import (  # noqa: E402
    BudgetExhausted, DataAgent, Reviewer, TeamModel, VisualQA, Writer)
from wealthryse_agent.month_team.coordinator import Coordinator  # noqa: E402
from wealthryse_agent.month_team.settings import TeamSettings  # noqa: E402
from wealthryse_agent.tool_client import ToolCallFailed  # noqa: E402

ACCOUNT = "U0000001"  # synthetic
PERIOD = "2026-01-31"
STORAGE = "https://reports.blob.core.windows.net"
SPECIAL = sorted(ALPHA_WORLD_SUBJECT_IDS)[0]  # a subject with its own report layout
NO_LINE = sorted(fig.DECKS_WITHOUT_COMMENTARY)[0]  # a subject whose deck has no commentary line
APAC = ModelSettings(endpoint="https://wr-agents-sea.services.ai.azure.com", deployment="wr-reports-apac",
                     router_deployment="", region="southeastasia", processing_scope="apac-data-zone",
                     managed_identity_client_id="x")
SETTINGS = TeamSettings(storage_account_url=STORAGE, container="internal-reports", monthly_token_budget=1_000_000,
                        report_time_limit_seconds=900)


def snapshot() -> dict:
    """The shared factsheet fixture, under this file's synthetic account."""
    return {**_snapshot(), "subject_id": ACCOUNT}


def render(payload: dict) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        template, output = Path(directory, "template.pdf"), Path(directory, "factsheet.pdf")
        writer = PdfWriter()
        writer.add_blank_page(width=595.5, height=842.25)
        writer.add_blank_page(width=595.5, height=842.25)
        with template.open("wb") as stream:
            writer.write(stream)
        render_factsheet(payload, template_path=template, output_path=output)
        return output.read_bytes()


def deck(*slides: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number, lines in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{line.replace('&', '&amp;')}</a:t></a:r></a:p>" for line in lines)
            archive.writestr(f"ppt/slides/slide{number}.xml", f"<p:sld><p:txBody>{body}</p:txBody></p:sld>")
    return buffer.getvalue()


FACTSHEET = render(snapshot())
DECK = deck([f"Monthly portfolio review | {ACCOUNT} | 31 January 2026"],
            ["Portfolio analyst: The portfolio rose strongly and finished ahead of the benchmark."])


def nav_points(jump_on: str | None = None) -> list[dict]:
    day, level, points = date(2025, 12, 31), 0.0, []
    while day <= date(2026, 1, 30):
        if day.weekday() < 5:
            level = (1 + level) * (1.25 if day.isoformat() == jump_on else 1.004) - 1
            points.append({"date": day.isoformat(), "cumulative_return": level})
        day += timedelta(days=1)
    return points


class FakeTools:
    """The tool server, answering as the month_team job would see it."""

    def __init__(self, *, factsheet: bytes = FACTSHEET, presentation: bytes | None = DECK, package: bool = True,
                 valuation: str = "2026-01-30", month_return: float | None = None,
                 status: str = "awaiting_approval", jump_on: str | None = None, generation: int = 11) -> None:
        self.files = {"factsheet": factsheet, "presentation": presentation}
        self.package = package
        self.valuation = valuation
        self.month_return = 110 / 99 - 1 if month_return is None else month_return
        self.status = status
        self.jump_on = jump_on
        self.generation = generation
        self.calls: list[str] = []
        self.fail: dict[str, ToolCallFailed] = {}

    def blob(self, kind: str) -> str:
        content = self.files[kind]
        return f"review/{ACCOUNT}/{PERIOD}/{hashlib.sha256(content).hexdigest()}-{kind}"

    def download(self, account_url: str, container: str, blob_name: str) -> bytes:
        self.downloaded = (account_url, container)
        return next(c for k, c in self.files.items() if c is not None and self.blob(k) == blob_name)

    def __call__(self, name: str, arguments: dict) -> dict:
        self.calls.append(name)
        if name in self.fail:
            raise self.fail[name]
        data = getattr(self, "_" + name)(arguments)
        return {"tool": name, "as_of": arguments.get("as_of") or arguments.get("to_date"), "data": data,
                "citations": [{"source": name}]}

    def _get_data_freshness(self, _):
        return {"latest_valuation_date": self.valuation, "latest_holdings_date": self.valuation,
                "benchmarks": [{"benchmark_id": "GSPC", "latest": "2026-01-30"}], "last_successful_import": "2026-02-01"}

    def _get_performance(self, _):
        return {"currency": "USD", "inception_date": "2025-01-20", "latest_nav": 110000.0, "periods": [
            {"key": "mtd", "label": "Month to date", "twr": self.month_return, "net_flows": 0.0},
            {"key": "ytd", "label": "Year to date", "twr": self.month_return, "net_flows": 0.0},
            {"key": "since_inception", "label": "Since inception", "twr": 0.10, "net_flows": 0.0}]}

    def _compare_to_benchmark(self, _):
        rows = [("Month to date", self.month_return, 0.02), ("Year to date", self.month_return, 0.02),
                ("Since inception", 0.10, 0.05)]
        return {"currency": "USD", "benchmark": {"id": "GSPC", "name": "S&P 500"}, "periods": [
            {"period": p, "portfolio_return": r, "benchmark_return": b, "excess_return": r - b} for p, r, b in rows]}

    def _get_risk_metrics(self, _):
        return {"annualized_volatility": 0.15, "max_drawdown": -0.12, "benchmark": {"beta": 0.9, "tracking_error": 0.05}}

    def _get_holdings_changes(self, _):
        return {"changes": [{"symbol": "ONE", "from_price": 5000.0}, {"symbol": "TWO", "from_price": -1000.0}],
                "from_price": 4000.0, "from_fx": 0.0}

    def _get_sector_allocation(self, _):
        return {"allocation": [{"sector": "Technology", "weight": 0.6}, {"sector": "Healthcare", "weight": 0.4}]}

    def _get_nav_history(self, _):
        return {"points": nav_points(self.jump_on)}

    def _get_month_end_package(self, _):
        if not self.package:
            raise ToolCallFailed("no_package", "No month-end package has been built for that month yet.")
        files = [{"report_type": kind, "generation_run_id": self.generation + n, "status": self.status,
                  "snapshot_id": "s1", "output_reference": "internal-reports/" + self.blob(kind),
                  "output_sha256": hashlib.sha256(content).hexdigest(), "completed_at": "2026-02-02"}
                 for n, (kind, content) in enumerate(sorted(self.files.items())) if content is not None]
        return {"period_end": PERIOD, "snapshot_id": "s1", "snapshot_sha256": "a" * 64, "quality_status": "pass",
                "publication_allowed": True, "files": files}

    def _get_month_end_snapshot(self, _):
        return {"snapshot_id": "s1", "payload": snapshot()}


class FakeModel:
    """The report deployment. Answers per schema name from scripted queues; records every request."""

    def __init__(self, *, drafts=None, reviews=None, visual=None, deck_review=None, reject_images=False) -> None:
        self.drafts = list(drafts or [GOOD_DRAFT])
        self.reviews = list(reviews or [[]])
        self.visual = visual or []
        self.deck_review = deck_review or []
        self.reject_images = reject_images
        self.requests: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **request):
        self.requests.append(request)
        name = request["text"]["format"]["name"]
        if name == "month_end_commentary":
            body = self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
        elif name == "commentary_review":
            body = {"objections": self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0]}
        elif name == "deck_review":
            body = {"objections": self.deck_review}
        else:
            if self.reject_images:
                error = Exception("bad request")
                error.status_code, error.body = 400, {"error": {"code": "invalid_value", "param": "input"}}
                raise error
            body = {"findings": self.visual}
        return SimpleNamespace(status="completed", output_text=json.dumps(body),
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))

    def named(self, name: str) -> list[dict]:
        return [r for r in self.requests if r["text"]["format"]["name"] == name]


def sentence(text: str, *ids: str) -> dict:
    return {"text": text, "figures": list(ids)}


GOOD_DRAFT = {
    "executive_summary": "The portfolio rose over the month and finished ahead of the benchmark.",
    "executive_summary_figures": ["F1", "F3"],
    "performance_observations": [sentence("The month return was well ahead of the benchmark.", "F1", "F2", "F3"),
                                 sentence("Since inception the portfolio remains ahead of the benchmark.", "F7", "F9")],
    "risk_observations": [sentence("Volatility over the last year was moderate.", "F12"),
                          sentence("The largest fall from a peak was contained.", "F13")],
}
BAD_DIRECTION = copy.deepcopy(GOOD_DRAFT)
BAD_DIRECTION["performance_observations"][0] = sentence("The portfolio lagged the benchmark this month.", "F3")


class FakeStore:
    """In-memory MonthStore with the same methods."""

    def __init__(self) -> None:
        self.runs_by_key: dict[tuple, dict] = {}
        self.steps: list[dict] = []
        self.objection_rows: list[dict] = []
        self.cards: dict[tuple, dict] = {}
        self.earlier: dict | None = None

    def run(self, account_id, period_end, mode):
        key = (account_id, period_end, mode)
        if key not in self.runs_by_key:
            self.runs_by_key[key] = {"month_run_id": len(self.runs_by_key) + 1, "subject_id": account_id,
                                     "period_end": period_end, "mode": mode, "live": False, "stage": "data",
                                     "data_verdict": None,
                                     "figures": {}, "commentary": None, "commentary_rounds": 0,
                                     "commentary_status": None, "package": None, "verdicts": {}, "usage": {},
                                     "attempts": 0, "last_error": None}
        return copy.deepcopy(self.runs_by_key[key])

    def _by_id(self, month_run_id):
        return next(r for r in self.runs_by_key.values() if r["month_run_id"] == month_run_id)

    def update_run(self, month_run_id, **fields):
        self._by_id(month_run_id).update(copy.deepcopy(fields))

    def previous_commentary(self, account_id, period_end):
        return self.earlier

    def add_step(self, month_run_id, agent_name, outcome, summary, *, round=1, evidence=None, usage=None,
                 correlation_id=None):
        assert len(summary) <= 600
        self.steps.append({"step_id": len(self.steps) + 1, "run": month_run_id, "agent": agent_name, "outcome": outcome,
                           "summary": summary, "round": round, "evidence": evidence or {}})
        return len(self.steps)

    def add_objection(self, month_run_id, step_id, *, raised_by, target, message, page=None, region=None):
        assert target in ("data", "commentary", "factsheet", "presentation") and len(message) <= 600
        self.objection_rows.append({"objection_id": len(self.objection_rows) + 1, "run": month_run_id,
                                    "raised_by": raised_by, "target": target, "message": message, "page": page,
                                    "region": region, "status": "open", "resolution": None})
        return len(self.objection_rows)

    def objections(self, month_run_id, status=None):
        return [o for o in self.objection_rows if o["run"] == month_run_id and (status is None or o["status"] == status)]

    def settle_objections(self, month_run_id, targets, status, resolution):
        settled = [o for o in self.objections(month_run_id, "open") if o["target"] in targets]
        for o in settled:
            o.update(status=status, resolution=resolution)
        return len(settled)

    def card_status(self, kind, account_id, period_key):
        card = self.cards.get((kind, account_id, period_key))
        return card["status"] if card else None

    def put_card(self, *, kind, account_id, period_key, title, body, citations=(), reopen=False):
        card = self.cards.setdefault((kind, account_id, period_key), {"status": "new"})
        card.update(title=title, body=body, citations=list(citations))
        if reopen:
            card["status"] = "new"

    def stage(self, mode="month"):
        return self.runs_by_key[(ACCOUNT, PERIOD, mode)]["stage"]


def fake_pages(content: bytes, dpi: int) -> list[bytes]:
    """Stands in for pdftoppm, which CI runners may not have: one placeholder image per page."""
    return [b"\x89PNG page"] * len(documents.pdf_text(content))


def team(tools: FakeTools, model: FakeModel, store: FakeStore | None = None, *, today=date(2026, 2, 2),
         budget: int = 1_000_000, live: bool = False) -> tuple[Coordinator, FakeStore]:
    store = store or FakeStore()
    settings = dataclasses.replace(SETTINGS, live=live)
    coordinator = Coordinator(store, tools, lambda: TeamModel(APAC, tokens_left=budget, client=model),
                              settings, today=today, download=tools.download, render_pages=fake_pages)
    return coordinator, store


# Printed-figure checks --------------------------------------------------------


class PrintedFigureTests(unittest.TestCase):
    def check(self, rendered: bytes, against: dict | None = None) -> list[dict]:
        expected = fig.factsheet_expectations(against or snapshot())
        return fig.check_factsheet_text(expected, documents.pdf_text(rendered))

    def test_a_correct_factsheet_passes_every_printed_figure(self):
        self.assertEqual(self.check(FACTSHEET), [])

    def test_planted_holding_and_sector_errors_are_caught(self):
        planted = snapshot()
        planted["holdings"]["holdings"][0]["market_value_text"] = "70000"
        planted["sector_allocation"]["gross_market_value"] = 110000
        problems = self.check(render(planted))
        labels = " ".join(p["message"] for p in problems)
        self.assertIn("Top holding weights", labels)
        self.assertIn("expected 60.00%", labels)
        self.assertIn("Sector weights", labels)
        self.assertTrue(all(p["page"] == 1 for p in problems))

    def test_planted_return_error_is_caught(self):
        planted = snapshot()
        planted["performance"]["series"][-1]["nav"] = 111000
        planted["portfolio_summary"]["latest_nav"]["total_text"] = "111000"
        labels = " ".join(p["message"] for p in self.check(render(planted)))
        self.assertIn("Trailing twelve-month return", labels)
        self.assertIn("Historical performance rows", labels)

    def test_a_factsheet_for_the_wrong_month_is_caught(self):
        later = snapshot()
        later["period_end"] = "2026-02-28"
        problems = self.check(FACTSHEET, against=later)
        self.assertTrue(any("Report date" in p["message"] and p["page"] == 2 for p in problems))

    def test_snapshot_drift_from_todays_database(self):
        expected = fig.factsheet_expectations(snapshot())
        figure_set = {"figures": [{"key": "month.portfolio_return", "value": 0.1200, "display": "12.00%"},
                                  {"key": "inception.portfolio_return", "value": 0.10, "display": "10.00%"}]}
        problems = fig.check_against_live(expected, figure_set, "2025-01-20")
        self.assertEqual(len(problems), 1)
        self.assertIn("month return (11.11%)", problems[0])
        figure_set["figures"][0]["value"] = 0.1115
        self.assertEqual(fig.check_against_live(expected, figure_set, "2025-01-20"), [])

    def test_special_layouts_are_skipped_not_failed(self):
        special = snapshot()
        special["subject_id"] = SPECIAL
        self.assertIn("skipped", fig.factsheet_expectations(special))
        self.assertEqual(fig.check_deck_text(SPECIAL, PERIOD, []), [])

    def test_commentary_printed_on_a_factsheet_is_found(self):
        summary = GOOD_DRAFT["executive_summary"]
        self.assertEqual(fig.factsheet_commentary(documents.pdf_text(FACTSHEET), [summary]), [])
        wrapped = ["Top 5 Holdings", "Historical Performance\nThe portfolio rose over the month and finished\nahead of the benchmark."]
        self.assertEqual(fig.factsheet_commentary(wrapped, [summary]), [2])
        self.assertEqual(fig.factsheet_commentary(["Portfolio analyst: Steady month.", "Page two"], []), [1])

    def test_deck_date_and_narrative_lines(self):
        slides = documents.pptx_text(DECK)
        self.assertEqual(fig.check_deck_text(ACCOUNT, PERIOD, slides), [])
        self.assertEqual(fig.check_deck_text(ACCOUNT, "2026-02-28", slides)[0]["page"], 1)
        self.assertEqual(fig.deck_commentary(slides), [
            {"slide": 2, "text": "The portfolio rose strongly and finished ahead of the benchmark."}])

    def test_direction_check_catches_wording_against_the_figures(self):
        figure_set = {"figures": [{"id": "F1", "key": "month.portfolio_return", "value": -0.02, "display": "-2.00%"},
                                  {"id": "F3", "key": "month.excess_return", "value": 0.01, "display": "1.00%"}]}
        items = [{"field": "performance_observations", "index": 0, "text": "The portfolio rose this month.", "figures": ["F1"]},
                 {"field": "performance_observations", "index": 1, "text": "It lagged the benchmark.", "figures": ["F3"]},
                 {"field": "risk_observations", "index": 0, "text": "It fell but beat the benchmark.", "figures": ["F1", "F3"]}]
        conflicts = fig.direction_conflicts(items, figure_set)
        self.assertEqual([(c["field"], c["index"]) for c in conflicts],
                         [("performance_observations", 0), ("performance_observations", 1)])


class DocumentTests(unittest.TestCase):
    def test_files_must_match_their_recorded_hash_and_place(self):
        tools = FakeTools()
        file = tools._get_month_end_package({})["files"][0]
        fetched = documents.fetch(file, account_url=STORAGE, expected_container="internal-reports", download=tools.download)
        self.assertEqual(fetched.content, FACTSHEET if file["report_type"] == "factsheet" else DECK)
        with self.assertRaises(documents.DocumentError) as raised:
            documents.fetch({**file, "output_sha256": "0" * 64}, account_url=STORAGE,
                            expected_container="internal-reports", download=tools.download)
        self.assertEqual(raised.exception.code, "hash_mismatch")
        with self.assertRaises(documents.DocumentError) as raised:
            documents.fetch({**file, "output_reference": "public/" + file["output_reference"].split("/", 1)[1]},
                            account_url=STORAGE, expected_container="internal-reports", download=tools.download)
        self.assertEqual(raised.exception.code, "unexpected_location")

    @unittest.skipUnless(shutil.which("pdftoppm"), "poppler-utils is not installed")
    def test_pages_render_to_images(self):
        images = documents.pdf_page_images(FACTSHEET, dpi=40)
        self.assertEqual(len(images), 2)
        self.assertTrue(all(i.startswith(b"\x89PNG") for i in images))


# Agents -----------------------------------------------------------------------


class DataAgentTests(unittest.TestCase):
    def test_verdicts(self):
        agent = DataAgent()
        self.assertEqual(agent.check(FakeTools(), ACCOUNT, PERIOD, today=date(2026, 2, 2)).outcome, "clean")
        late = FakeTools(valuation="2026-01-29")
        fixable = agent.check(late, ACCOUNT, PERIOD, today=date(2026, 2, 2))
        self.assertEqual(fixable.outcome, "fixable")
        self.assertIn("2026-01-30", fixable.summary)
        self.assertEqual(agent.check(late, ACCOUNT, PERIOD, today=date(2026, 2, 5)).outcome, "blocked")
        jump = agent.check(FakeTools(jump_on="2026-01-15"), ACCOUNT, PERIOD, today=date(2026, 2, 2))
        self.assertEqual(jump.outcome, "blocked")
        self.assertIn("2026-01-15", jump.summary)
        fx = FakeTools()
        fx.fail["get_holdings_changes"] = ToolCallFailed("reporting_fx_unavailable", "No HKD/USD exchange rate.")
        self.assertEqual(agent.check(fx, ACCOUNT, PERIOD, today=date(2026, 2, 2)).outcome, "fixable")

    def test_replay_judges_the_month_not_todays_freshness(self):
        tools = FakeTools(valuation="2026-01-29")
        self.assertEqual(DataAgent().check(tools, ACCOUNT, PERIOD, today=date(2026, 10, 14), replay=True).outcome, "clean")
        self.assertNotIn("get_data_freshness", tools.calls)


class WriterReviewerTests(unittest.TestCase):
    def setUp(self):
        self.figures = fig.build_figure_set(FakeTools(), ACCOUNT, PERIOD)

    def test_figure_set_cites_every_figure(self):
        keys = [f["key"] for f in self.figures["figures"]]
        self.assertEqual(keys[:3], ["month.portfolio_return", "month.benchmark_return", "month.excess_return"])
        self.assertEqual([f["id"] for f in self.figures["figures"]][:2], ["F1", "F2"])
        self.assertTrue(all(0 <= f["source"] < len(self.figures["sources"]) for f in self.figures["figures"]))
        self.assertEqual(self.figures["benchmark"], "S&P 500")
        tools = FakeTools()
        tools.fail["get_risk_metrics"] = ToolCallFailed("not_enough_history", "x")
        self.assertIn("Risk measures", fig.build_figure_set(tools, ACCOUNT, PERIOD)["unavailable"])

    def test_writer_sees_only_the_figure_set_and_must_cite_it(self):
        digits = copy.deepcopy(GOOD_DRAFT)
        digits["executive_summary"] = "The portfolio rose 11% in the month."
        model = FakeModel(drafts=[digits, GOOD_DRAFT])
        result = Writer(TeamModel(APAC, tokens_left=10**6, client=model)).draft(self.figures, account_id=ACCOUNT)
        self.assertEqual(result.outcome, "pass")
        self.assertEqual(len(model.requests), 2)
        self.assertIn("numerical claim", json.loads(model.requests[1]["input"][0]["content"][0]["text"])["format_problem"])
        sent = json.loads(model.requests[0]["input"][0]["content"][0]["text"])
        self.assertEqual(set(sent["figures"][0]), {"id", "label", "display"})
        self.assertNotIn("payload", json.dumps(sent))
        self.assertEqual(model.requests[0]["model"], "wr-reports-apac")
        self.assertFalse(model.requests[0]["store"])
        unknown = copy.deepcopy(GOOD_DRAFT)
        unknown["risk_observations"][0]["figures"] = ["F99"]
        failed = Writer(TeamModel(APAC, tokens_left=10**6, client=FakeModel(drafts=[unknown]))).draft(
            self.figures, account_id=ACCOUNT)
        self.assertEqual(failed.outcome, "failed")
        self.assertIn("F99", failed.summary)

    def test_the_summary_fits_the_decks_line(self):
        long = copy.deepcopy(GOOD_DRAFT)
        long["executive_summary"] = "The portfolio rose over the month and finished well ahead of the benchmark. " * 3
        model = FakeModel(drafts=[long, GOOD_DRAFT])
        result = Writer(TeamModel(APAC, tokens_left=10**6, client=model)).draft(self.figures, account_id=ACCOUNT)
        self.assertEqual(result.outcome, "pass")
        problem = json.loads(model.requests[1]["input"][0]["content"][0]["text"])["format_problem"]
        self.assertIn("the deck prints at most 221", problem)

    def test_reviewer_catches_a_planted_direction_error_even_when_the_model_does_not(self):
        reviewer = Reviewer(TeamModel(APAC, tokens_left=10**6, client=FakeModel(reviews=[[]])))
        result = reviewer.review_commentary(BAD_DIRECTION, self.figures, round=1)
        self.assertEqual(result.outcome, "objection")
        self.assertIn("Performance observations 1", result.objections[0]["message"])
        self.assertEqual(result.objections[0]["target"], "commentary")

    def test_reviewer_keeps_only_objections_to_real_sentences(self):
        model = FakeModel(reviews=[[{"field": "risk_observations", "index": 1, "message": "Not supported."},
                                    {"field": "risk_observations", "index": 9, "message": "Out of range."}]])
        result = Reviewer(TeamModel(APAC, tokens_left=10**6, client=model)).review_commentary(GOOD_DRAFT, self.figures, round=2)
        self.assertEqual([o["index"] for o in result.objections], [1])
        sent = json.loads(model.requests[0]["input"][0]["content"][0]["text"])
        self.assertNotIn("draft", sent)  # it sees the output only, cold
        self.assertIn("cited", sent["commentary"][0])

    def test_visual_qa_findings_become_marked_objections(self):
        findings = [{"page": 1, "severity": "high", "kind": "blank_area", "description": "The sector table is empty.",
                     "region": {"x": 0.05, "y": 0.4, "w": 2.0, "h": 0.1}},
                    {"page": 2, "severity": "low", "kind": "other", "description": "Slightly tight spacing.",
                     "region": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}},
                    {"page": 7, "severity": "high", "kind": "other", "description": "No such page.",
                     "region": {"x": 0, "y": 0, "w": 0.1, "h": 0.1}}]
        model = FakeModel(visual=findings)
        result = VisualQA(TeamModel(APAC, tokens_left=10**6, client=model)).inspect_factsheet(
            [b"png1", b"png2"], display_date="31 Jan 2026", period_label="ending 31 Jan 2026")
        self.assertEqual(result.outcome, "objection")
        self.assertEqual(len(result.objections), 1)
        self.assertEqual(result.objections[0]["region"], {"x": 0.05, "y": 0.4, "w": 0.95, "h": 0.1})
        self.assertEqual(result.evidence["cosmetic_notes"][0]["page"], 2)
        images = [c for c in model.requests[0]["input"][0]["content"] if c["type"] == "input_image"]
        self.assertEqual(len(images), 2)

    def test_visual_qa_says_so_when_the_model_cannot_read_images(self):
        result = VisualQA(TeamModel(APAC, tokens_left=10**6, client=FakeModel(reject_images=True))).inspect_factsheet(
            [b"png"], display_date="31 Jan 2026", period_label="x")
        self.assertEqual(result.outcome, "skipped")
        self.assertIn("did not accept page images", result.summary)

    def test_report_content_stays_in_the_apac_data_zone(self):
        with self.assertRaises(ValueError):
            TeamModel(ModelSettings(**{**APAC.__dict__, "processing_scope": "global"}), tokens_left=1, client=FakeModel())

    def test_budget_is_checked_before_each_call(self):
        model = TeamModel(APAC, tokens_left=100, client=FakeModel())
        Writer(model).draft(self.figures, account_id=ACCOUNT)
        with self.assertRaises(BudgetExhausted):
            Writer(model).draft(self.figures, account_id=ACCOUNT)


# Coordinator --------------------------------------------------------------------


class CoordinatorTests(unittest.TestCase):
    def test_a_month_end_to_end_in_shadow(self):
        tools = FakeTools(package=False)
        model = FakeModel(drafts=[BAD_DIRECTION, GOOD_DRAFT], reviews=[[]])
        coordinator, store = team(tools, model)
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "package")
        run = store.runs_by_key[(ACCOUNT, PERIOD, "month")]
        self.assertEqual((run["data_verdict"], run["commentary_status"], run["commentary_rounds"]), ("clean", "agreed", 2))
        self.assertEqual([o["status"] for o in store.objection_rows], ["resolved"])
        self.assertEqual(store.cards, {})
        steps_before = len(store.steps)
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "package")  # waiting writes nothing
        self.assertEqual(len(store.steps), steps_before)

        tools.package = True
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertEqual(tools.downloaded, (STORAGE, "internal-reports"))
        card = store.cards[("month_end_report", ACCOUNT, PERIOD)]
        self.assertIn("checks passed", card["title"])
        self.assertIn("shadow run", card["body"])
        self.assertEqual(card["citations"][0]["link"], f"/accounts/{ACCOUNT}/reports")
        agents = [s["agent"] for s in store.steps]
        self.assertEqual(agents[:7], ["data", "analyst", "writer", "reviewer", "writer", "reviewer", "coordinator"])
        self.assertEqual(agents[-3:], ["reviewer", "visual_qa", "visual_qa"])
        self.assertIn("printed values checked", json.dumps(store.steps[-3]["evidence"]))
        self.assertEqual(len(model.named("visual_qa")), 1)
        # No model request ever carries the frozen snapshot or a file.
        self.assertTrue(all("snapshot_payload" not in json.dumps(r["input"][0]["content"][0]) for r in model.requests))

    def test_planted_faults_reach_the_report_card_with_page_and_region(self):
        planted = snapshot()
        planted["holdings"]["holdings"][0]["market_value_text"] = "70000"
        planted["sector_allocation"]["gross_market_value"] = 110000
        model = FakeModel(visual=[{"page": 1, "severity": "medium", "kind": "clipped_text",
                                   "description": "A company name is cut off.", "region": {"x": 0.04, "y": 0.27, "w": 0.3, "h": 0.03}}])
        coordinator, store = team(FakeTools(factsheet=render(planted)), model)
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        open_items = store.objections(1, "open")
        self.assertEqual({o["raised_by"] for o in open_items}, {"reviewer", "visual_qa"})
        clipped = next(o for o in open_items if o["raised_by"] == "visual_qa")
        self.assertEqual((clipped["page"], clipped["region"]["y"]), (1, 0.27))
        card = store.cards[("month_end_report", ACCOUNT, PERIOD)]
        self.assertIn(f"{len(open_items)} objection(s) to review", card["title"])
        self.assertIn("Top holding weights", card["body"])

    def test_a_tampered_file_is_an_objection_not_a_crash(self):
        tools = FakeTools()
        original = tools.download
        tools.download = lambda url, container, name: original(url, container, name) + (b"x" if "factsheet" in name else b"")
        coordinator, store = team(tools, FakeModel())
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertIn("does not match the hash", store.objections(1, "open")[0]["message"])
        self.assertEqual(store.objections(1, "open")[0]["target"], "factsheet")

    def test_a_dispute_goes_to_ying_and_her_answer_is_applied(self):
        model = FakeModel(drafts=[GOOD_DRAFT], reviews=[[{"field": "executive_summary", "index": 0,
                                                          "message": "The benchmark is named wrongly."}]])
        coordinator, store = team(FakeTools(package=False), model)
        coordinator.advance(ACCOUNT, PERIOD, "month")
        run = store.runs_by_key[(ACCOUNT, PERIOD, "month")]
        self.assertEqual((run["commentary_status"], run["commentary_rounds"]), ("disputed", 2))
        card = store.cards[("month_end_dispute", ACCOUNT, PERIOD)]
        self.assertIn("named wrongly", card["body"])
        self.assertEqual([o["status"] for o in store.objection_rows], ["resolved", "open"])
        card["status"] = "approved"
        coordinator.advance(ACCOUNT, PERIOD, "month")
        self.assertEqual(run["commentary_status"], "writer_upheld")
        self.assertEqual(store.objection_rows[-1]["status"], "overruled")

    def test_a_blocked_account_waits_for_ying(self):
        tools = FakeTools(jump_on="2026-01-15")
        coordinator, store = team(tools, FakeModel())
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "held")
        card = store.cards[("month_end_hold", ACCOUNT, PERIOD)]
        self.assertIn("Hold or proceed", card["title"])
        card["status"] = "dismissed"
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "held")
        card["status"] = "approved"
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertIn("Ying chose to proceed", " ".join(s["summary"] for s in store.steps))

    def test_a_late_price_waits_for_the_next_sync(self):
        tools = FakeTools(valuation="2026-01-29")
        coordinator, store = team(tools, FakeModel())
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "data")
        self.assertEqual(store.steps[-1]["outcome"], "waiting")
        tools.valuation = "2026-01-30"
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")

    def test_a_rebuild_is_checked_again_but_a_release_is_not(self):
        tools = FakeTools()
        coordinator, store = team(tools, FakeModel())
        coordinator.advance(ACCOUNT, PERIOD, "month")
        store.cards[("month_end_report", ACCOUNT, PERIOD)]["status"] = "read"
        checks = len([s for s in store.steps if s["agent"] == "visual_qa"])
        tools.status = "released_internal"
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertEqual(len([s for s in store.steps if s["agent"] == "visual_qa"]), checks)
        self.assertEqual(store.runs_by_key[(ACCOUNT, PERIOD, "month")]["package"]["files"][0]["status"], "released_internal")
        tools.generation = 21
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertEqual(len([s for s in store.steps if s["agent"] == "visual_qa"]), checks * 2)
        self.assertEqual(store.runs_by_key[(ACCOUNT, PERIOD, "month")]["verdicts"]["package_round"], 2)
        self.assertEqual(store.cards[("month_end_report", ACCOUNT, PERIOD)]["status"], "new")

    def test_repeated_failures_stop_the_account_not_the_month(self):
        tools = FakeTools()
        tools.fail["get_performance"] = ToolCallFailed("performance_unavailable", "x")
        tools.fail["compare_to_benchmark"] = ToolCallFailed("benchmark_unavailable", "x")
        coordinator, store = team(tools, FakeModel())
        store.runs_by_key[(ACCOUNT, PERIOD, "month")] = {**store.run(ACCOUNT, PERIOD, "month"), "stage": "words"}
        results = [coordinator.advance(ACCOUNT, PERIOD, "month") for _ in range(4)]
        self.assertEqual(results, ["words", "words", "stopped", "stopped"])
        self.assertEqual(store.runs_by_key[(ACCOUNT, PERIOD, "month")]["last_error"], "performance_unavailable")
        self.assertEqual(coordinator.run_month([ACCOUNT, "U2"], PERIOD)["stopped"], 1)

    def test_running_out_of_budget_pauses_without_skipping(self):
        coordinator, store = team(FakeTools(), FakeModel(), budget=100)
        with self.assertRaises(BudgetExhausted):
            coordinator.advance(ACCOUNT, PERIOD, "month")
        self.assertIn("budget", store.steps[-1]["summary"])
        self.assertEqual(store.stage(), "words")

    def test_live_reports_must_carry_the_teams_commentary(self):
        carried = deck(["Monthly portfolio review | 31 January 2026"],
                       ["Portfolio analyst: " + GOOD_DRAFT["executive_summary"]])
        coordinator, store = team(FakeTools(presentation=carried), FakeModel(), live=True)
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        run = store.runs_by_key[(ACCOUNT, PERIOD, "month")]
        self.assertTrue(run["live"])
        self.assertTrue(run["verdicts"]["commentary_in_report"])
        self.assertEqual(store.objections(1, "open"), [])
        card = store.cards[("month_end_report", ACCOUNT, PERIOD)]
        self.assertIn("the deck carries it", card["body"])
        self.assertIn("Release warns", card["body"])
        self.assertNotIn("shadow", card["body"])

        coordinator, store = team(FakeTools(), FakeModel(), live=True)  # built with the standard commentary
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        self.assertFalse(store.runs_by_key[(ACCOUNT, PERIOD, "month")]["verdicts"]["commentary_in_report"])
        self.assertIn("does not carry the team's agreed commentary", store.objections(1, "open")[0]["message"])

    def test_commentary_on_the_factsheet_is_an_objection(self):
        real = documents.pdf_text

        def with_commentary(content):
            pages = real(content)
            return pages[:-1] + [pages[-1] + "\nPortfolio analyst: " + GOOD_DRAFT["executive_summary"]]

        carried = deck(["Monthly portfolio review | 31 January 2026"],
                       ["Portfolio analyst: " + GOOD_DRAFT["executive_summary"]])
        coordinator, store = team(FakeTools(presentation=carried), FakeModel(), live=True)
        with mock.patch.object(documents, "pdf_text", with_commentary):
            self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "month"), "ready")
        [objection] = store.objections(1, "open")
        self.assertEqual((objection["target"], objection["page"]), ("factsheet", 2))
        self.assertIn("belongs in the deck only", objection["message"])

    def test_a_deck_without_a_commentary_line_gets_no_commentary(self):
        model = FakeModel()
        plain = deck(["Monthly portfolio review | 31 January 2026"], ["What is working"])
        coordinator, store = team(FakeTools(presentation=plain), model, live=True)
        self.assertEqual(coordinator.advance(NO_LINE, PERIOD, "month"), "ready")
        run = store.runs_by_key[(NO_LINE, PERIOD, "month")]
        self.assertIsNone(run["commentary_status"])
        self.assertEqual(model.named("month_end_commentary"), [])
        self.assertEqual((run["verdicts"]["commentary_in_report"], run["verdicts"]["deck_has_commentary"]), (None, False))
        self.assertEqual(store.objections(1, "open"), [])
        self.assertIn("deck has no commentary line", store.cards[("month_end_report", NO_LINE, PERIOD)]["body"])

    def test_live_cards_say_what_each_choice_does_to_the_report(self):
        coordinator, store = team(FakeTools(jump_on="2026-01-15"), FakeModel(), live=True)
        coordinator.advance(ACCOUNT, PERIOD, "month")
        self.assertIn("standard commentary", store.cards[("month_end_hold", ACCOUNT, PERIOD)]["body"])
        model = FakeModel(reviews=[[{"field": "executive_summary", "index": 0, "message": "Wrong benchmark."}]])
        coordinator, store = team(FakeTools(package=False), model, live=True)
        coordinator.advance(ACCOUNT, PERIOD, "month")
        body = store.cards[("month_end_dispute", ACCOUNT, PERIOD)]["body"]
        self.assertIn("goes into the deck", body)
        self.assertIn("factsheet carries no commentary", body)
        self.assertNotIn("shadow", body)

    def test_replay_checks_released_months_and_writes_no_cards(self):
        tools = FakeTools(status="awaiting_approval", jump_on="2026-01-15")
        coordinator, store = team(tools, FakeModel(), today=date(2026, 10, 14))
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "replay"), "package")  # not released: not replayed
        tools.status = "released_internal"
        self.assertEqual(coordinator.advance(ACCOUNT, PERIOD, "replay"), "ready")
        self.assertEqual(store.runs_by_key[(ACCOUNT, PERIOD, "replay")]["data_verdict"], "blocked")
        self.assertEqual(store.cards, {})


class JobTests(unittest.TestCase):
    def test_replay_targets_only_released_months_in_range(self):
        from wealthryse_agent.jobs import replay_targets

        months = [{"period_end": "2026-07-31", "files": {"factsheet": {"status": "released_internal"}}},
                  {"period_end": "2026-08-31", "files": {"factsheet": {"status": "awaiting_approval"}}},
                  {"period_end": "2026-09-30", "files": {"factsheet": {"status": "published"}}}]
        tools = SimpleNamespace(call=lambda name, args: {"data": {"months": months}})
        self.assertEqual(replay_targets(tools, ["A"], "2026-01-31", "2026-08-31"), [("A", "2026-07-31")])
        self.assertEqual(len(replay_targets(tools, ["A", "B"], "1900-01-01", "2999-12-31")), 4)

    def test_month_team_flags_are_refused_for_other_jobs(self):
        from wealthryse_agent.jobs import main

        with self.assertRaises(SystemExit):
            main(["holdings_explainer", "--replay"])


class AgentServerMonthEndTests(unittest.TestCase):
    def client(self, accounts):
        from fastapi.testclient import TestClient

        from wealthryse_agent.agent_server import Services, create_app
        from wealthryse_agent.config import RuntimeSettings, ServiceAuthSettings

        class Tools:
            def call(self, name, arguments):
                return {"data": {"accounts": [{"account_id": a} for a in accounts]}}

            def close(self):
                pass

        class MonthStoreStub:
            def runs(self, accounts, period_end, mode):
                self.asked = (list(accounts), period_end, mode)
                return [{"subject_id": a} for a in accounts]

            def team_status(self):
                return {"latest_job": None, "tokens_this_month": 0}

            def run_detail(self, account_id, period_end, mode):
                return {"subject_id": account_id, "period_end": period_end} if period_end == PERIOD else None

        stub = MonthStoreStub()
        services = Services(auth=ServiceAuthSettings("", "", frozenset(), disabled_for_local_tests=True),
                            runtime=RuntimeSettings("", "", "", 1000, 90), tenant_id="t", store_factory=lambda: None,
                            agent_factory=lambda: None, tools_factory=lambda u, c: Tools(),
                            month_store_factory=lambda: stub)
        services._month_store = stub
        return TestClient(create_app(services)), stub

    def test_runs_are_limited_to_the_persons_accounts(self):
        client, stub = self.client(["U1"])
        user = "11111111-1111-1111-1111-111111111111"
        response = client.get("/v1/month-end/runs", params={"user_object_id": user, "period_end": PERIOD})
        self.assertEqual(response.json()["runs"], [{"subject_id": "U1"}])
        self.assertEqual(stub.asked, (["U1"], PERIOD, "month"))
        self.assertEqual(client.get(f"/v1/month-end/runs/U2/{PERIOD}", params={"user_object_id": user}).status_code, 403)
        self.assertEqual(client.get(f"/v1/month-end/runs/U1/{PERIOD}", params={"user_object_id": user}).status_code, 200)
        self.assertEqual(client.get("/v1/month-end/runs/U1/2026-02-28", params={"user_object_id": user}).status_code, 404)
        self.assertEqual(client.get("/v1/month-end/runs", params={"user_object_id": user, "mode": "live"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
