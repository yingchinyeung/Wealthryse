"""The intake agent: a new case's account terms, drafted from the client's own documents.

No database, network or model: cases come from a fake tool, storage is a dict,
the model is a fake that returns citations. The documents are real PDF, Word
and text files with synthetic wording. The database side (the case view and
the drafts table) is in test_onboarding_agent_review_db.py.
"""
from __future__ import annotations

import io
import json
import os
import unittest
import zipfile
from types import SimpleNamespace

os.environ.setdefault("AGENT_TOOLS_AUTOSTART", "0")
os.environ.setdefault("AGENT_API_AUTOSTART", "0")

from reportlab.pdfgen import canvas  # noqa: E402

from wealthryse_agent.config import ModelSettings  # noqa: E402
from wealthryse_agent.month_team.agents import TeamModel, TeamModelError  # noqa: E402
from wealthryse_agent.onboarding_intake import (  # noqa: E402
    PREFIX, TERMS, IntakeDrafter, IntakeSettings, docx_text, documents_digest, draft_waiting_cases)

CASE_ID = "aaaaaaaa-0000-4000-8000-0000000000d1"
STORAGE = "https://reports.blob.core.windows.net"
SETTINGS = IntakeSettings(storage_account_url=STORAGE, container="onboarding-intake")
APAC = ModelSettings(endpoint="https://wr-agents-sea.services.ai.azure.com", deployment="wr-reports-apac",
                     router_deployment="", region="southeastasia", processing_scope="apac-data-zone",
                     managed_identity_client_id="x")
# Synthetic documents only.
MANDATE_PAGES = [
    ["Investment Management Agreement", "Account: Synthetic Growth Account",
     "The Account shall be valued and reported in Hong Kong Dollars (HKD).",
     "Effective date: this Agreement commences on 3 March 2025."],
    ["Benchmark: MSCI AC Asia Pacific Net Total Return Index.",
     "Restrictions: no single issuer above 10% of NAV."],
]
FEE_LINES = ["Fee Schedule", "Management fee: 1.25% per annum of NAV, accrued daily and paid quarterly.",
             "Performance fee: 15% of returns above the high-water mark."]


def pdf(pages: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    document = canvas.Canvas(buffer)
    for lines in pages:
        for row, text in enumerate(lines):
            document.drawString(72, 760 - row * 20, text)
        document.showPage()
    document.save()
    return buffer.getvalue()


def docx(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{line}</w:t></w:r></w:p>" for line in lines)
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:body>{body}</w:body></w:document>")
    return buffer.getvalue()


class Storage:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def put(self, name: str, content: bytes, case_id: str = CASE_ID) -> None:
        self.blobs[PREFIX.format(case_id=case_id) + name] = content

    def lister(self, account_url, container, prefix):
        assert (account_url, container) == (STORAGE, "onboarding-intake")
        return [{"name": n, "size": len(c)} for n, c in self.blobs.items() if n.startswith(prefix)]

    def download(self, account_url, container, blob_name):
        return self.blobs[blob_name]


def cite(term, value, document, page, quote):
    return {"term": term, "value": value, "document": document, "page": page, "quote": quote}


GOOD = [
    cite("display_name", "Synthetic Growth Account", "mandate.pdf", 1, "Account: Synthetic Growth Account"),
    cite("reporting_currency", "HKD", "mandate.pdf", 1, "valued and reported in Hong Kong Dollars (HKD)"),
    cite("history_start_date", "2025-03-03", "mandate.pdf", 1, "this Agreement commences on 3 March 2025"),
    cite("benchmark", "MSCI AC Asia Pacific Net TR", "mandate.pdf", 2,
         "Benchmark: MSCI AC Asia Pacific Net Total Return Index."),
    cite("management_fee", "1.25% a year of NAV", "fees.docx", 1, "Management fee: 1.25% per annum of NAV"),
    cite("performance_fee", "15% above the high-water mark", "fees.docx", 1,
         "Performance fee: 15% of returns above the high-water mark."),
    cite("fee_frequency", "Accrued daily, paid quarterly", "fees.docx", 1, "accrued daily and paid quarterly"),
]


class FakeResponses:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def create(self, **request):
        self.calls.append(request)
        citations = self.answers.pop(0) if self.answers else []
        return SimpleNamespace(output_text=json.dumps({"citations": citations}), status="completed",
                               usage=SimpleNamespace(input_tokens=200, output_tokens=20))


def model(*answers):
    client = SimpleNamespace(responses=FakeResponses(answers))
    return TeamModel(APAC, tokens_left=1_000_000, client=client), client.responses


def case(**changes):
    return {"case_id": CASE_ID, "source_kind": "broker_account", "subject_id": "synthetic-growth",
            "display_name": "Synthetic Growth", "description": None, "reporting_currency": "HKD",
            "history_start_date": "2025-03-03", "stage": "load_and_reconcile", **changes}


def storage(**extra) -> Storage:
    files = Storage()
    files.put("mandate.pdf", pdf(MANDATE_PAGES))
    files.put("fees.docx", docx(FEE_LINES))
    for name, content in extra.items():
        files.put(name, content)
    return files


def drafter(files: Storage, team_model: TeamModel) -> IntakeDrafter:
    return IntakeDrafter(SETTINGS, lambda: team_model, download=files.download, lister=files.lister)


def run(files: Storage, answer, the_case=None):
    team_model, responses = model(answer)
    agent = drafter(files, team_model)
    docs, findings = agent.read(CASE_ID)
    return agent.draft(the_case or case(), docs, findings), responses


class DraftTests(unittest.TestCase):
    def test_a_clean_set_of_documents_drafts_every_term_with_its_clause(self):
        draft, responses = run(storage(), GOOD)
        self.assertEqual(draft.outcome, "pass", draft.summary)
        self.assertEqual(draft.terms["reporting_currency"]["value"], "HKD")
        self.assertEqual(draft.terms["history_start_date"]["value"], "2025-03-03")
        self.assertEqual(draft.terms["management_fee"]["sources"][0],
                         {"value": "1.25% a year of NAV", "document": "fees.docx", "page": 1,
                          "quote": "Management fee: 1.25% per annum of NAV"})
        self.assertEqual(draft.terms["hurdle_or_high_water_mark"]["status"], "not_found")
        self.assertEqual(set(draft.terms), set(TERMS))
        self.assertEqual([d["document"] for d in draft.documents], ["fees.docx", "mandate.pdf"])
        self.assertIn("traced 7 of 10 terms", draft.summary)
        # The model gets the documents' text on the APAC deployment, and nothing else from storage.
        request = responses.calls[0]
        self.assertEqual(request["model"], "wr-reports-apac")
        sent = json.loads(request["input"][0]["content"][0]["text"])
        self.assertEqual(set(sent), {"terms_wanted", "documents"})  # never the case's own terms
        self.assertEqual([d["document"] for d in sent["documents"]], ["fees.docx", "mandate.pdf"])
        self.assertIn("Hong Kong Dollars (HKD)", sent["documents"][1]["pages"][0]["text"])

    def test_a_quote_that_is_not_in_the_document_is_dropped(self):
        invented = GOOD[:1] + [cite("reporting_currency", "USD", "mandate.pdf", 1, "reported in US Dollars"),
                               cite("benchmark", "S&P 500", "side-letter.pdf", 1, "Benchmark: S&P 500")] + GOOD[2:3]
        draft, _ = run(storage(), invented)
        self.assertEqual(draft.terms["reporting_currency"]["status"], "not_found")
        self.assertEqual(draft.terms["benchmark"]["status"], "not_found")
        dropped = [f for f in draft.findings if f["severity"] == "info"]
        self.assertEqual(len(dropped), 2)
        self.assertEqual(draft.outcome, "warn")
        self.assertIn("No document states the reporting currency", draft.summary)

    def test_a_quote_on_another_page_is_kept_with_its_real_page(self):
        draft, _ = run(storage(), [cite("benchmark", "MSCI AC Asia Pacific", "mandate.pdf", 1,
                                        "Benchmark: MSCI AC Asia Pacific Net Total Return Index.")] + GOOD[1:3])
        self.assertEqual(draft.terms["benchmark"]["sources"][0]["page"], 2)

    def test_documents_that_disagree_fail_with_both_clauses(self):
        files = storage(**{"side-letter.txt": b"Side letter. Management fee: 1.00% per annum of NAV."})
        answer = GOOD + [cite("management_fee", "1.00% a year of NAV", "side-letter.txt", 1,
                              "Management fee: 1.00% per annum of NAV.")]
        draft, _ = run(files, answer)
        self.assertEqual(draft.outcome, "fail")
        self.assertEqual(draft.terms["management_fee"]["status"], "conflict")
        self.assertIsNone(draft.terms["management_fee"]["value"])
        conflict = next(f for f in draft.findings if f["term"] == "management_fee")
        self.assertIn("fees.docx page 1 says 1.25% a year of NAV, side-letter.txt page 1 says 1.00% a year of NAV",
                      conflict["message"])
        self.assertIn("Before approving the terms, resolve", draft.summary)

    def test_the_same_fee_worded_differently_is_not_a_conflict(self):
        files = storage(**{"summary.txt": b"Fees in brief: management fee 1.25% p.a. of NAV."})
        answer = GOOD + [cite("management_fee", "1.25% p.a.", "summary.txt", 1, "management fee 1.25% p.a. of NAV")]
        draft, _ = run(files, answer)
        self.assertEqual(draft.terms["management_fee"]["status"], "found")
        self.assertEqual(len(draft.terms["management_fee"]["sources"]), 2)
        self.assertEqual(draft.outcome, "pass")

    def test_a_currency_other_than_the_case_says_fails_and_names_the_clause(self):
        draft, _ = run(storage(), GOOD, case(reporting_currency="USD"))
        self.assertEqual(draft.outcome, "fail")
        finding = next(f for f in draft.findings if f["term"] == "reporting_currency")
        self.assertEqual((finding["document"], finding["page"]), ("mandate.pdf", 1))
        self.assertIn("The case's reporting currency is USD, but mandate.pdf page 1 says HKD", finding["message"])

    def test_a_different_start_date_is_a_warning_to_check(self):
        draft, _ = run(storage(), GOOD, case(history_start_date="2025-04-01"))
        self.assertEqual(draft.outcome, "warn")
        self.assertIn("gives 2025-03-03 as the start date", draft.summary)

    def test_an_unreadable_or_unsupported_document_is_named(self):
        files = storage(**{"scan.png": b"\x89PNG", "broken.pdf": b"not a pdf"})
        draft, _ = run(files, GOOD)
        self.assertEqual(draft.outcome, "warn")
        named = {f["document"]: f["message"] for f in draft.findings if f["severity"] == "medium"}
        self.assertIn("Only PDF, Word (.docx) and text documents can be read.", named["scan.png"])
        self.assertIn("The PDF could not be read.", named["broken.pdf"])
        self.assertIn("read 2 of 4 document(s)", draft.summary)

    def test_a_badly_formed_value_is_not_used(self):
        draft, _ = run(storage(), [cite("history_start_date", "3 March 2025", "mandate.pdf", 1,
                                        "this Agreement commences on 3 March 2025")] + GOOD[1:2])
        self.assertEqual(draft.terms["history_start_date"]["status"], "not_found")
        self.assertTrue(any("not in a form the platform can use" in f["message"] for f in draft.findings))

    def test_long_documents_are_cut_and_the_draft_says_where(self):
        from dataclasses import replace

        files = storage()
        team_model, _ = model(GOOD)
        agent = IntakeDrafter(replace(SETTINGS, max_characters=10_000), lambda: team_model,
                              download=files.download, lister=files.lister)
        files.put("annex.txt", b"x" * 20_000)
        docs, findings = agent.read(CASE_ID)
        draft = agent.draft(case(), docs, findings)
        self.assertTrue(any(f["document"] == "annex.txt" and f["message"].startswith("annex.txt was not read")
                            for f in draft.findings))

    def test_word_documents_are_read_in_parts(self):
        parts = docx_text(docx([f"Clause {i}" for i in range(90)]))
        self.assertEqual(len(parts), 3)
        self.assertTrue(parts[2].startswith("Clause 80"))


class FakeStore:
    def __init__(self):
        self.saved = {}

    def drafted(self, case_id, digest):
        return (case_id, digest) in self.saved

    def save(self, the_case, digest, draft, correlation_id):
        self.saved[(the_case["case_id"], digest)] = draft


class JobTests(unittest.TestCase):
    def call(self, cases):
        return lambda name, arguments: {"data": {"cases": cases}} if name == "list_onboarding_intake_cases" else None

    def test_each_set_of_documents_is_drafted_once_and_a_new_document_is_drafted_again(self):
        files, store, usage = storage(), FakeStore(), {}
        team_model, responses = model(GOOD, GOOD)
        agent = drafter(files, team_model)
        other = case(case_id="aaaaaaaa-0000-4000-8000-0000000000d2", subject_id="no-documents-yet")
        self.assertEqual(draft_waiting_cases(store, self.call([case(), other]), agent, usage), 1)
        self.assertEqual(draft_waiting_cases(store, self.call([case(), other]), agent, usage), 0)
        files.put("side-letter.txt", b"Nothing new.")
        self.assertEqual(draft_waiting_cases(store, self.call([case(), other]), agent, usage), 1)
        self.assertEqual(len(responses.calls), 2)
        self.assertEqual(usage, {"input_tokens": 400, "output_tokens": 40})

    def test_a_model_error_writes_nothing_so_the_next_run_tries_again(self):
        files, store = storage(), FakeStore()
        team_model, _ = model()
        team_model.ask = lambda **_: (_ for _ in ()).throw(TeamModelError("model_rejected", "no"))
        self.assertEqual(draft_waiting_cases(store, self.call([case()]), drafter(files, team_model), {}), 0)
        self.assertEqual(store.saved, {})

    def test_the_digest_follows_the_documents_not_their_order(self):
        files = storage()
        agent = drafter(files, model()[0])
        docs, _ = agent.read(CASE_ID)
        self.assertEqual(documents_digest(docs), documents_digest(list(reversed(docs))))
        files.put("fees.docx", docx(FEE_LINES + ["Amended."]))
        self.assertNotEqual(documents_digest(docs), documents_digest(agent.read(CASE_ID)[0]))


if __name__ == "__main__":
    unittest.main()
