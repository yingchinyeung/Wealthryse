"""The history reviewer: a composite's historical reports, read before approval 2.

No database, network or model: the case comes from a fake tool, storage is a
dict, the model is a fake that answers visual QA. The factsheets are real PDFs
and the decks real PPTX archives, so the reviewer reads genuine files. The
database side (the case view, the review table and the platform recording the
check) is in test_onboarding_agent_review_db.py.
"""
from __future__ import annotations

import hashlib
import io
import os
import unittest
import zipfile
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from reportlab.pdfgen import canvas  # noqa: E402

from ibkr_etl.onboarding_agent_review import check_for, reports_digest  # noqa: E402
from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.month_team.agents import TeamModel  # noqa: E402
from wealthryse_agent.onboarding_review import (  # noqa: E402
    HistoryReviewer, ReviewSettings, coverage_findings, header_date, review_waiting_cases)

NAME = "Synthetic Long Short"  # synthetic
STORAGE = "https://reports.blob.core.windows.net"
SETTINGS = ReviewSettings(storage_account_url=STORAGE, container="internal-reports", monthly_token_budget=1_000_000)
APAC = ModelSettings(endpoint="https://wr-agents-sea.services.ai.azure.com", deployment="wr-reports-apac",
                     router_deployment="", region="southeastasia", processing_scope="apac-data-zone",
                     managed_identity_client_id="x")


def factsheet(period_end: str, *, name: str = NAME, page_two: list[str] | None = None, pages: int = 2) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    lines = [[name, f"Report date {header_date(period_end)}", f"{name} key figures"],
             page_two or [f"Report date {header_date(period_end)}", "Composition", "Disclaimer"]]
    for number in range(pages):
        for row, text in enumerate(lines[min(number, 1)]):
            pdf.drawString(72, 760 - row * 20, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def deck(*slides: list[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number, lines in enumerate(slides, 1):
            body = "".join(f"<a:p><a:r><a:t>{line.replace('&', '&amp;')}</a:t></a:r></a:p>" for line in lines)
            archive.writestr(f"ppt/slides/slide{number}.xml", f"<p:sld><p:txBody>{body}</p:txBody></p:sld>")
    return buffer.getvalue()


class Files:
    """Private storage: what composite_reports stored, and the case's reports_rendered evidence."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.reports: list[dict] = []

    def add(self, period_end: str, report_type: str, content: bytes) -> dict:
        digest = hashlib.sha256(content).hexdigest()
        name = f"synthetic-ls_{'Factsheet' if report_type == 'factsheet' else 'Presentation'}_{period_end}"
        blob = f"review/synthetic-ls/{period_end}/{digest}-{name}"
        self.blobs[blob] = content
        item = {"period_end": period_end, "report_type": report_type, "file_name": name, "sha256": digest,
                "generation_run_id": len(self.reports) + 1, "snapshot_id": "s", "output_reference":
                f"internal-reports/{blob}", "download_path": f"/api/onboarding-v2/cases/c/reports/{len(self.reports) + 1}"}
        self.reports.append(item)
        return item

    def month(self, period_end: str, **factsheet_options) -> None:
        self.add(period_end, "factsheet", factsheet(period_end, **factsheet_options))

    def download(self, account_url: str, container: str, blob_name: str) -> bytes:
        assert account_url == STORAGE and container == "internal-reports"
        return self.blobs[blob_name]

    def case(self) -> dict:
        return {"case_id": "aaaaaaaa-0000-4000-8000-0000000000c1", "subject_id": "synthetic-ls", "display_name": NAME,
                "description": "A synthetic long short composite for tests.", "report_style": "alpha-world-ms",
                "reporting_currency": "HKD", "history_start_date": "2025-01-20",
                "reports_rendered": {"design": "alpha-world-ms", "reports": list(self.reports), "waiting_for": None}}


class FakeResponses:
    def __init__(self, findings_by_call):
        self.findings = list(findings_by_call)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        findings = self.findings.pop(0) if self.findings else []
        import json

        return SimpleNamespace(output_text=json.dumps({"findings": findings}), status="completed",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=10))


def model(*findings_by_call):
    client = SimpleNamespace(responses=FakeResponses(findings_by_call))
    return TeamModel(APAC, tokens_left=1_000_000, client=client), client.responses


def reviewer(files: Files, team_model: TeamModel, **settings) -> HistoryReviewer:
    from dataclasses import replace

    return HistoryReviewer(replace(SETTINGS, **settings), lambda: team_model, download=files.download,
                           render_pages=lambda content, dpi: [b"page-1", b"page-2"])


def finding(page, severity, kind="missing_element", description="The key figures panel is missing."):
    return {"page": page, "severity": severity, "kind": kind, "description": description,
            "region": {"x": 0.1, "y": 0.1, "w": 0.3, "h": 0.2}}


class CoverageTests(unittest.TestCase):
    def test_every_month_needs_its_factsheet_and_no_month_may_be_skipped(self):
        # Decks are no longer built: a month without one is complete, and an old deck is not counted.
        reports = [{"period_end": "2025-01-31", "report_type": "factsheet"},
                   {"period_end": "2025-01-31", "report_type": "presentation"},
                   {"period_end": "2025-02-28", "report_type": "factsheet"},
                   {"period_end": "2025-03-31", "report_type": "presentation"},
                   {"period_end": "2025-06-30", "report_type": "factsheet"}]
        messages = [f["message"] for f in coverage_findings(reports)]
        self.assertEqual(messages, ["March 2025 has no factsheet.",
                                    "2 month(s) between March 2025 and June 2025 have no reports."])


class ReviewTests(unittest.TestCase):
    def test_clean_reports_pass_and_every_factsheet_is_looked_at_in_the_alpha_world_layout(self):
        files = Files()
        for month in ("2025-01-31", "2025-02-28", "2025-03-31"):
            files.month(month)
        team_model, responses = model()
        review = reviewer(files, team_model).review(files.case())
        self.assertEqual(review.findings, [])
        self.assertEqual(review.outcome, "pass", review.summary)
        self.assertIn("all 3 files for January 2025 to March 2025 in the Alpha World MS design", review.summary)
        self.assertEqual((review.evidence["files_checked"], review.evidence["visual_qa"]["months_looked_at"]), (3, 3))
        self.assertEqual(len(responses.calls), 3)
        # The composite's factsheet is the Alpha World design, so visual QA must not ask for a Top 5 table.
        self.assertIn("Alpha World owner template", responses.calls[0]["input"][0]["content"][0]["text"])
        self.assertNotIn("Top 5", responses.calls[0]["input"][0]["content"][0]["text"])
        self.assertEqual(review.usage, {"input_tokens": 300, "output_tokens": 30})

    def test_planted_problems_fail_with_their_month_and_page(self):
        files = Files()
        files.month("2025-01-31", page_two=[f"Report date {header_date('2025-01-31')}",
                                            "Portfolio analyst: the composite rose.", "Greater China"])
        files.month("2025-02-28", name="Alpha World MS")
        files.add("2025-03-31", "factsheet", factsheet("2025-02-28"))  # last month's date on March's file
        files.add("2025-03-31", "presentation", deck(["Alpha World MS monthly review"]))  # an old deck, not read
        files.blobs[files.reports[0]["output_reference"].partition("/")[2]] += b"tampered"
        team_model, _ = model()
        review = reviewer(files, team_model).review(files.case())
        self.assertEqual(review.outcome, "fail")
        found = {(f["period_end"], f["report_type"], f["page"], f["message"]) for f in review.findings}
        self.assertIn(("2025-01-31", "factsheet", None,
                       "The stored file does not match the hash recorded when it was built."), found)
        self.assertIn(("2025-02-28", "factsheet", 1, "Page 1 does not print the composite's name."), found)
        self.assertIn(("2025-03-31", "factsheet", 1, "Page 1 does not show the report date March 31 2025."), found)
        self.assertFalse(any(kind == "presentation" for _, kind, _, _ in found))
        self.assertLessEqual(len(review.summary), 1000)
        self.assertTrue(review.summary.startswith("The agent review found"), review.summary)

    def test_commentary_and_reference_text_on_a_factsheet_fail(self):
        files = Files()
        files.month("2025-01-31", page_two=[f"Report date {header_date('2025-01-31')}",
                                            "Portfolio analyst: the composite rose.", "Greater China"])
        team_model, _ = model()
        found = {f["message"] for f in reviewer(files, team_model).review(files.case()).findings}
        self.assertEqual(found, {"Page 2 prints commentary; the factsheet never carries commentary.",
                                 "Page 2 still prints 'Greater China' from the reference design."})

    def test_reference_text_the_composite_uses_itself_is_allowed(self):
        files = Files()
        files.month("2025-01-31", page_two=[f"Report date {header_date('2025-01-31')}", "Greater China"])
        case = files.case()
        case["description"] = "A Greater China long short composite for tests."
        team_model, _ = model()
        self.assertEqual(reviewer(files, team_model).review(case).outcome, "pass")

    def test_a_high_layout_fault_fails_and_a_medium_one_warns(self):
        files = Files()
        files.month("2025-01-31")
        files.month("2025-02-28")
        team_model, _ = model([finding(1, "medium", "clipped_text", "The KPI label is clipped.")],
                              [finding(2, "high")])
        review = reviewer(files, team_model).review(files.case())
        self.assertEqual(review.outcome, "fail")
        self.assertEqual([(f["period_end"], f["severity"], f["page"]) for f in review.findings],
                         [("2025-02-28", "medium", 1), ("2025-01-31", "high", 2)])
        files = Files()
        files.month("2025-01-31")
        team_model, _ = model([finding(1, "medium", "clipped_text", "The KPI label is clipped.")])
        review = reviewer(files, team_model).review(files.case())
        self.assertEqual(review.outcome, "warn")
        self.assertIn("1 layout point(s) to look at: January 2025 factsheet: Clipped text (medium)", review.summary)

    def test_months_visual_qa_did_not_look_at_make_a_warning_never_a_pass(self):
        files = Files()
        for month in ("2025-01-31", "2025-02-28", "2025-03-31"):
            files.month(month)
        team_model, responses = model()
        review = reviewer(files, team_model, visual_months=2).review(files.case())
        self.assertEqual(review.outcome, "warn")
        self.assertEqual(review.evidence["visual_qa"]["not_looked_at"], ["2025-01-31"])
        self.assertIn("visual QA could not look at 1 month(s): January 2025", review.summary)
        self.assertEqual(len(responses.calls), 2)

    def test_a_model_that_cannot_read_images_warns(self):
        from wealthryse_agent.month_team.agents import TeamModelError

        files = Files()
        files.month("2025-01-31")
        team_model, _ = model()
        team_model.ask = lambda **_: (_ for _ in ()).throw(TeamModelError("model_rejected", "no"))
        review = reviewer(files, team_model).review(files.case())
        self.assertEqual(review.outcome, "warn")
        self.assertIn("visual QA did not look at the pages", review.summary)


KIWOOM = "Kiwoom Korea Equity"  # synthetic


def designed_factsheet(lines: list[str], pages: int = 1) -> bytes:
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    for _ in range(pages):
        for row, text in enumerate(lines):
            pdf.drawString(72, 760 - row * 20, text)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def designed_case(files: Files) -> dict:
    """A broker account in its owner's own design: report_style is null and the design is the submitted one."""
    return {"case_id": "aaaaaaaa-0000-4000-8000-0000000000k1", "subject_id": "KIWOOM-0000000001",
            "display_name": KIWOOM, "description": None, "report_style": None, "reporting_currency": "KRW",
            "history_start_date": "2025-03-21",
            "reports_rendered": {"design": {"title": "Korea monthly", "draft_id": "d", "version": 1,
                                            "spec_sha256": "0" * 64},
                                 "template_version": "spec:" + "0" * 64, "reports": list(files.reports),
                                 "waiting_for": None}}


class DesignedReviewTests(unittest.TestCase):
    def test_an_owner_design_passes_on_any_page_count_and_date_form_with_the_standard_checklist(self):
        files = Files()
        files.add("2025-03-31", "factsheet", designed_factsheet([KIWOOM, "31 March 2025", "Holdings"], pages=3))
        files.add("2025-04-30", "factsheet", designed_factsheet([KIWOOM, "2025-04-30"]))
        team_model, responses = model()
        review = reviewer(files, team_model).review(designed_case(files))
        self.assertEqual(review.findings, [])
        self.assertEqual(review.outcome, "pass", review.summary)
        self.assertIn("in the owner's Korea monthly design", review.summary)
        self.assertNotIn("Alpha World owner template", responses.calls[0]["input"][0]["content"][0]["text"])

    def test_layout_points_in_an_owner_design_warn_and_never_block(self):
        files = Files()
        files.add("2025-03-31", "factsheet", designed_factsheet(["Another name", "No date here"]))
        team_model, _ = model([finding(1, "high")])
        review = reviewer(files, team_model).review(designed_case(files))
        self.assertEqual(review.outcome, "warn", review.summary)
        self.assertEqual({f["severity"] for f in review.findings}, {"medium"})
        messages = {f["message"] for f in review.findings}
        self.assertIn(f"Page 1 does not print the account's name ({KIWOOM}).", messages)
        self.assertIn("No page shows the report month March 2025.", messages)

    def test_a_point_quoting_a_date_the_page_does_not_print_is_dismissed_as_a_misreading(self):
        # 20 October 2026: visual QA read the December table's last date 2025-12-30 as 2025-12-28.
        files = Files()
        files.add("2025-12-31", "factsheet", designed_factsheet([KIWOOM, "December 2025", "2025-12-30 1.00"]))
        misread = finding(1, "high", "wrong_period",
                          'The heading runs to 31 Dec 2025, but the table\'s last printed date is "2025-12-28".')
        team_model, _ = model([misread])
        review = reviewer(files, team_model).review(designed_case(files))
        self.assertEqual(review.findings, [])
        self.assertEqual(review.outcome, "pass", review.summary)
        dismissed = review.evidence["visual_qa"]["dismissed_as_misread"]
        self.assertEqual([(d["period_end"], d["not_in_page_text"]) for d in dismissed], [("2025-12-31", ["2025-12-28"])])

    def test_a_point_quoting_what_the_page_does_print_is_kept(self):
        files = Files()
        files.add("2025-12-31", "factsheet", designed_factsheet([KIWOOM, "December 2025", "2025-12-30 1.00"]))
        team_model, _ = model([finding(1, "high", "wrong_period", 'The table ends on "2025-12-30", a day early.')])
        review = reviewer(files, team_model).review(designed_case(files))
        self.assertEqual(review.outcome, "warn", review.summary)
        self.assertEqual(len(review.findings), 1)
        self.assertEqual(review.evidence["visual_qa"]["dismissed_as_misread"], [])

    def test_a_missing_month_a_changed_file_or_commentary_still_fails_an_owner_design(self):
        files = Files()
        files.add("2025-03-31", "factsheet", designed_factsheet([KIWOOM, "March 2025"]))
        files.add("2025-05-31", "factsheet", designed_factsheet([KIWOOM, "May 2025", "Portfolio analyst: up."]))
        files.blobs[files.reports[0]["output_reference"].partition("/")[2]] += b"tampered"
        team_model, _ = model()
        review = reviewer(files, team_model).review(designed_case(files))
        self.assertEqual(review.outcome, "fail")
        messages = {f["message"] for f in review.findings if f["severity"] == "high"}
        self.assertIn("1 month(s) between March 2025 and May 2025 have no reports.", messages)
        self.assertIn("The stored file does not match the hash recorded when it was built.", messages)
        self.assertIn("Page 1 prints commentary; the factsheet never carries commentary.", messages)


class FakeStore:
    def __init__(self):
        self.saved = {}

    def reviewed(self, case_id, digest):
        return (case_id, digest) in self.saved

    def save(self, case, digest, review, correlation_id):
        self.saved[(case["case_id"], digest)] = review


class JobTests(unittest.TestCase):
    def test_each_set_of_reports_is_reviewed_once_and_a_re_render_is_reviewed_again(self):
        files = Files()
        files.month("2025-01-31")
        case = files.case()
        calls = []

        def call(name, arguments):
            calls.append(name)
            return {"data": {"cases": [case]}}

        store, usage = FakeStore(), {}
        team_model, _ = model()
        self.assertEqual(review_waiting_cases(store, call, reviewer(files, team_model), usage), 1)
        self.assertEqual(review_waiting_cases(store, call, reviewer(files, team_model), usage), 0)
        files.month("2025-02-28")
        case = files.case()
        self.assertEqual(review_waiting_cases(store, call, reviewer(files, team_model), usage), 1)
        self.assertEqual(calls, ["list_onboarding_report_cases"] * 3)
        self.assertEqual(len(store.saved), 2)
        self.assertEqual(usage, {"input_tokens": 300, "output_tokens": 30})


class CheckTests(unittest.TestCase):
    def test_the_check_is_pending_until_these_exact_reports_are_reviewed(self):
        evidence = {"design": "alpha-world-ms", "reports": [{"period_end": "2025-01-31", "sha256": "1" * 64}]}
        digest = reports_digest(evidence)
        self.assertEqual(digest, reports_digest({"reports": [{"sha256": "1" * 64, "period_end": "2025-01-31"}],
                                                 "design": "alpha-world-ms"}))
        self.assertNotEqual(digest, reports_digest({**evidence, "design": "alpha-world-t"}))
        status, message, recorded = check_for(None, digest)
        self.assertEqual((status, recorded["reviewed_reports_sha256"]), ("pending", digest))
        review = {"review_id": 7, "outcome": "warn", "summary": "One point.", "created_at": "2026-10-14 10:00:00+00",
                  "evidence": '{"files_checked": 2}', "findings": [{"message": f"point {i}"} for i in range(60)]}
        status, message, recorded = check_for(review, digest)
        self.assertEqual((status, message, recorded["review_id"]), ("warn", "One point.", 7))
        self.assertEqual((len(recorded["findings"]), recorded["more_findings"]), (50, 10))
        self.assertEqual(recorded["checked"], {"files_checked": 2})


if __name__ == "__main__":
    unittest.main()
