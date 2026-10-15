"""The platform side of the onboarding agents: switch, case page parts, documents and the scheduled recorders."""
from __future__ import annotations

import json
import os
import sys
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from ibkr_etl import onboarding_agents, web

SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

ON = {"ONBOARDING_V2_ENABLED": "true", "ONBOARDING_AGENTS_ENABLED": "true"}
AGENTS_OFF = {"ONBOARDING_V2_ENABLED": "true", "ONBOARDING_AGENTS_ENABLED": ""}
OWNER = {"email": "owner@example.com", "object_id": "owner-oid", "role": "owner"}
CASE_ID = "00000000-0000-0000-0000-0000000000c1"
PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Untouched:
    """A connection the switched-off paths must never use."""

    def execute(self, *args, **kwargs):
        raise AssertionError("queried while switched off")

    def rollback(self):
        raise AssertionError("rolled back while switched off")


class _Catalogue:
    """Answers the table checks; records rollbacks."""

    def __init__(self, exists=True, allowed=True):
        self.exists, self.allowed, self.rollbacks = exists, allowed, 0

    def execute(self, statement, parameters=()):
        if "pg_catalog.pg_class" in statement:
            return _Rows([{"allowed": self.allowed if self.exists else None}])
        raise AssertionError(statement)

    def rollback(self):
        self.rollbacks += 1


class _CaseReads:
    """The read-only website connection behind one onboarding case in stage 2."""

    def __init__(self, *, stage="load_and_reconcile", approvals=(), status="active"):
        self.row = {"case_id": CASE_ID, "stage": stage, "status": status, "broker": "ibkr_flex",
                    "source_kind": "broker_account", "broker_account_id": "U0000001", "target_subject_id": None}
        self.approvals = [{"approval_kind": kind, "terms_sha256": "a" * 64, "approved_at": "x"} for kind in approvals]

    def execute(self, statement, parameters=()):
        if statement.startswith("SELECT * FROM broker_onboarding_cases"):
            return _Rows([self.row])
        if "FROM broker_onboarding_approvals" in statement:
            return _Rows(self.approvals)
        return _Rows([])


@contextmanager
def _database(connection):
    yield connection


class SwitchTests(unittest.TestCase):
    def test_off_unless_exactly_true(self):
        self.assertFalse(onboarding_agents.agents_enabled({}))
        self.assertFalse(onboarding_agents.agents_enabled({"ONBOARDING_AGENTS_ENABLED": "1"}))
        self.assertTrue(onboarding_agents.agents_enabled({"ONBOARDING_AGENTS_ENABLED": " TRUE "}))

    def test_switched_off_nothing_reads_the_agent_schema_or_records(self):
        off = {}
        self.assertEqual(onboarding_agents.run_reconciliations(_Untouched(), "t", off), {"status": "switched_off"})
        self.assertEqual(onboarding_agents.run_reviews(_Untouched(), "t", off), {"status": "switched_off"})
        self.assertIsNone(onboarding_agents.case_agent_view(_Untouched(), "t", CASE_ID, [], environ=off))

    def test_missing_or_unreadable_agent_tables_mean_no_results_yet(self):
        on = {"ONBOARDING_AGENTS_ENABLED": "true"}
        for connection in (_Catalogue(exists=False), _Catalogue(allowed=False)):
            self.assertEqual(onboarding_agents.run_reconciliations(connection, "t", on), {"status": "no_agent_results"})
            self.assertEqual(onboarding_agents.run_reviews(connection, "t", on), {"status": "no_agent_results"})
            self.assertEqual(connection.rollbacks, 2)
            view = onboarding_agents.case_agent_view(connection, "t", CASE_ID, [], environ=on)
            self.assertEqual(view, {"checks": {}, "term_draft": None, "security_proposals": []})

    def test_switched_on_the_recorders_run(self):
        on = {"ONBOARDING_AGENTS_ENABLED": "true"}
        with patch("ibkr_etl.onboarding_agent_reconcile.record_agent_reconciliations",
                   return_value=[{"case_id": CASE_ID, "recorded": ["nav_reconciled"]}]) as reconcile, \
                patch("ibkr_etl.onboarding_agent_review.record_agent_reviews",
                      return_value=[{"case_id": CASE_ID, "check": "pending", "recorded": True}]) as review:
            connection = _Catalogue()
            self.assertEqual(onboarding_agents.run_reconciliations(connection, "t", on)["cases"][0]["recorded"],
                             ["nav_reconciled"])
            self.assertEqual(onboarding_agents.run_reviews(connection, "t", on)["cases"][0]["check"], "pending")
        reconcile.assert_called_once_with(connection, "t")
        review.assert_called_once_with(connection, "t")


class CaseViewTests(unittest.TestCase):
    def test_agent_checks_and_proposals_come_from_the_recorded_evidence(self):
        proposal = {"security_key": "sec-1", "display_name": "Synthetic Co", "identifiers": [
            {"scheme": "isin", "identifier": "US0000000001"}], "evidence": {"symbol": "SYN"}}
        checks = [
            {"check_id": "securities_mapped", "evidence_json": json.dumps(
                {"recorded_by": "agent", "reconciliation_id": 7, "proposed": 1, "proposals": [proposal]})},
            {"check_id": "nav_reconciled", "evidence_json": {"recorded_by": "agent", "breaks": []}},
            {"check_id": "holdings_and_cash_loaded", "evidence_json": {"recorded_by": "flex_worker"}},
            {"check_id": "transactions_loaded", "evidence_json": None},
        ]
        view = onboarding_agents.case_agent_view(_Catalogue(exists=False), "t", CASE_ID, checks,
                                                 environ={"ONBOARDING_AGENTS_ENABLED": "true"})
        self.assertEqual(sorted(view["checks"]), ["nav_reconciled", "securities_mapped"])
        self.assertNotIn("proposals", view["checks"]["securities_mapped"])  # shown once, as their own list
        self.assertEqual(view["security_proposals"], [proposal])


class _Savepoints(_Catalogue):
    """Counts savepoints and answers the connection reports query."""

    def __init__(self, reports=(), **kwargs):
        super().__init__(**kwargs)
        self.reports, self.savepoints = list(reports), 0

    @contextmanager
    def transaction(self):
        self.savepoints += 1
        yield

    def execute(self, statement, parameters=()):
        if "FROM agent.connection_reports" in statement:
            return _Rows(self.reports)
        return super().execute(statement, parameters)


class ReadinessAndConnectionReportTests(unittest.TestCase):
    def test_the_readiness_pack_shows_only_while_the_case_waits_for_go_live(self):
        pack = {"ready": True, "pack_sha256": "f" * 64}
        with patch("ibkr_etl.onboarding_readiness.readiness_pack", return_value=pack) as built:
            self.assertIsNone(onboarding_agents.case_readiness(_Untouched(), "t", CASE_ID, "go_live", environ={}))
            self.assertIsNone(onboarding_agents.case_readiness(_Untouched(), "t", CASE_ID, "historical_reports",
                                                               environ=ON))
            connection = _Savepoints()
            self.assertEqual(onboarding_agents.case_readiness(connection, "t", CASE_ID, "go_live", environ=ON), pack)
        built.assert_called_once_with(connection, "t", CASE_ID)
        self.assertEqual(connection.savepoints, 1)

    def test_a_pack_that_cannot_be_built_says_so_instead_of_breaking_the_case_page(self):
        with patch("ibkr_etl.onboarding_readiness.readiness_pack", side_effect=RuntimeError("boom")), \
                self.assertLogs("ibkr_etl.onboarding_agents", "ERROR"):
            self.assertEqual(onboarding_agents.case_readiness(_Savepoints(), "t", CASE_ID, "go_live", environ=ON),
                             {"unavailable": True})

    def test_connection_reports_are_the_latest_per_connection_and_only_while_switched_on(self):
        self.assertIsNone(onboarding_agents.connection_reports(_Untouched(), "t", environ={}))
        self.assertEqual(onboarding_agents.connection_reports(_Savepoints(allowed=False), "t", environ=ON), {})
        report = {"connection_id": uuid.UUID(int=7), "state": "failed", "title": "The token was refused",
                  "body": "IBKR said the token is not valid.", "owner_steps": json.dumps(["Make a new token.", " "]),
                  "created_at": "2026-10-14 21:00:00+00"}
        self.assertEqual(onboarding_agents.connection_reports(_Savepoints([report]), "t", environ=ON),
                         {str(uuid.UUID(int=7)): {"state": "failed", "title": "The token was refused",
                                                  "body": "IBKR said the token is not valid.",
                                                  "owner_steps": ["Make a new token."],
                                                  "reported_at": "2026-10-14 21:00:00+00"}})


class DocumentTests(unittest.TestCase):
    def test_names_are_kept_readable_and_safe(self):
        self.assertEqual(onboarding_agents.safe_document_name("C:\\Users\\x\\Mandate (signed).PDF"),
                         "Mandate -signed.pdf")
        self.assertEqual(onboarding_agents.safe_document_name("../../fee terms.docx"), "fee terms.docx")
        for bad in ("notes.exe", "archive.zip", ".pdf", "mandate"):
            with self.assertRaises(ValueError):
                onboarding_agents.safe_document_name(bad)

    def test_content_must_match_its_type_and_size(self):
        self.assertEqual(onboarding_agents.validate_document("m.pdf", PDF), ("m.pdf", "application/pdf"))
        self.assertEqual(onboarding_agents.validate_document("terms.md", "Fee: 1%".encode())[1],
                         "text/markdown; charset=utf-8")
        for name, content in (("m.pdf", b"not a pdf"), ("m.docx", PDF), ("m.txt", b"\xff\xfe"), ("m.pdf", b""),
                              ("m.pdf", b"%PDF-" + b"0" * onboarding_agents.MAX_DOCUMENT_BYTES)):
            with self.assertRaises(ValueError, msg=name):
                onboarding_agents.validate_document(name, content)

    def test_documents_are_taken_until_approval_one(self):
        row = {"stage": "load_and_reconcile", "status": "active"}
        self.assertTrue(onboarding_agents.documents_open(row, []))
        self.assertFalse(onboarding_agents.documents_open(row, [{"approval_kind": "account_terms"}]))
        self.assertFalse(onboarding_agents.documents_open({**row, "status": "cancelled"}, []))
        self.assertFalse(onboarding_agents.documents_open({**row, "stage": "build_history"}, []))
        self.assertEqual(onboarding_agents.document_blob_name(CASE_ID, "m.pdf"),
                         f"cases/{CASE_ID}/documents/m.pdf")


class DocumentRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app)
        web.app.dependency_overrides[web._onboarding_user] = lambda: OWNER
        web.app.dependency_overrides[web._owner_user] = lambda: OWNER
        self.addCleanup(web.app.dependency_overrides.clear)

    def _upload(self, name="Mandate.pdf", content=PDF):
        return self.client.post(f"/api/onboarding-v2/cases/{CASE_ID}/documents",
                                files={"document": (name, content, "application/pdf")})

    def test_switched_off_the_document_routes_are_hidden(self):
        with patch.dict(os.environ, AGENTS_OFF), patch.object(web, "upload_bytes") as upload:
            self.assertEqual(self.client.get(f"/api/onboarding-v2/cases/{CASE_ID}/documents").status_code, 404)
            self.assertEqual(self._upload().status_code, 404)
        upload.assert_not_called()

    def test_a_document_goes_to_the_case_folder_in_the_private_intake_container(self):
        stored = {}

        def upload(account, container, blob, content, **options):
            stored.update(account=account, container=container, blob=blob, content=content, **options)

        with patch.dict(os.environ, ON), patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://private.example"), \
                patch.object(web, "_database", side_effect=lambda: _database(_CaseReads())), \
                patch.object(web, "list_blobs", return_value=[]), patch.object(web, "upload_bytes", side_effect=upload):
            response = self._upload("Mandate (signed).pdf")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "Mandate -signed.pdf")
        self.assertEqual(stored, {"account": "https://private.example", "container": "onboarding-intake",
                                  "blob": f"cases/{CASE_ID}/documents/Mandate -signed.pdf", "content": PDF,
                                  "overwrite": False, "content_type": "application/pdf"})

    def test_documents_are_listed_by_name(self):
        listed = [{"name": f"cases/{CASE_ID}/documents/b.pdf", "size": 2048},
                  {"name": f"cases/{CASE_ID}/documents/a.docx", "size": 10}]
        with patch.dict(os.environ, ON), patch.object(web, "_database", side_effect=lambda: _database(_CaseReads())), \
                patch.object(web, "list_blobs", return_value=listed) as list_blobs:
            response = self.client.get(f"/api/onboarding-v2/cases/{CASE_ID}/documents")
        self.assertEqual(response.json()["documents"], [{"name": "a.docx", "byte_count": 10},
                                                         {"name": "b.pdf", "byte_count": 2048}])
        self.assertEqual(list_blobs.call_args.args[1:], ("onboarding-intake", f"cases/{CASE_ID}/documents/"))

    def test_refused_after_approval_one_on_a_duplicate_a_bad_file_or_a_full_case(self):
        with patch.dict(os.environ, ON), patch.object(web, "AZURE_STORAGE_ACCOUNT_URL", "https://private.example"), \
                patch.object(web, "upload_bytes") as upload:
            with patch.object(web, "_database", side_effect=lambda: _database(_CaseReads(approvals=("account_terms",)))):
                self.assertEqual(self._upload().status_code, 409)
            with patch.object(web, "_database", side_effect=lambda: _database(_CaseReads())):
                with patch.object(web, "list_blobs", return_value=[{"name": f"cases/{CASE_ID}/documents/Mandate.pdf"}]):
                    self.assertEqual(self._upload().status_code, 409)
                with patch.object(web, "list_blobs", return_value=[]):
                    self.assertEqual(self._upload("run.exe").status_code, 400)
                    self.assertEqual(self._upload("m.pdf", b"plain text").status_code, 400)
                full = [{"name": f"cases/{CASE_ID}/documents/{n}.pdf"} for n in range(onboarding_agents.MAX_DOCUMENTS)]
                with patch.object(web, "list_blobs", return_value=full):
                    self.assertEqual(self._upload().status_code, 409)
        upload.assert_not_called()

    def test_the_case_page_has_the_agent_parts(self):
        with patch.dict(os.environ, ON):
            page = self.client.get(f"/account-onboarding/cases/{uuid.UUID(CASE_ID)}").text
        for part in ("Checked by agent", "Proposed security master entries", "Client documents",
                     "Terms drafted by the agent from the client's documents", 'id="case-term-draft"',
                     f"/api/onboarding-v2/cases/${{caseId}}/documents", 'id="case-readiness"',
                     "Typing GO LIVE switches on", "Approved terms against the client's documents"):
            self.assertIn(part, page)

    def test_the_connections_page_shows_the_agents_reports(self):
        with patch.dict(os.environ, ON):
            page = self.client.get("/broker-connections").text
        self.assertIn("data.agent_reports", page)
        self.assertIn("Checked by agent", page)


class AskAboutCaseTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app)
        web.app.dependency_overrides[web._onboarding_user] = lambda: OWNER
        web.app.dependency_overrides[web._owner_user] = lambda: OWNER
        self.addCleanup(web.app.dependency_overrides.clear)

    def _ask(self, question="What is left before go-live?"):
        return self.client.post(f"/api/onboarding-v2/cases/{CASE_ID}/ask", json={"question": question})

    def test_hidden_while_switched_off_or_without_the_agent_service(self):
        asked = []
        with patch.dict(os.environ, AGENTS_OFF), patch.object(web, "_case_question_stream",
                                                              return_value=lambda payload: asked.append(payload)):
            self.assertEqual(self._ask().status_code, 404)
        with patch.dict(os.environ, ON), patch.object(web, "_case_question_stream", return_value=None):
            self.assertEqual(self._ask().status_code, 404)
        self.assertEqual(asked, [])

    def test_the_question_goes_to_the_agent_service_with_the_case_facts(self):
        sent = []

        def stream(payload):
            sent.append(payload)
            return iter([b'data: {"type": "answer", "answer": "Nothing [T1]."}\n\n'])

        facts = {"case": {"ready": True}, "drafted_terms": None, "proposed_securities": []}
        with patch.dict(os.environ, ON), patch.object(web, "_case_question_stream", return_value=stream), \
                patch.object(web, "_database", side_effect=lambda: _database(_CaseReads())), \
                patch.object(onboarding_agents, "case_facts", return_value=facts) as built:
            response = self._ask()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('"answer"', response.text)
        self.assertEqual(built.call_args.args[1:], (web.TENANT_ID, CASE_ID))
        self.assertEqual(sent, [{"user_object_id": "owner-oid", "case_id": CASE_ID, "question": "What is left before go-live?",
                                 "conversation_id": None, "case_facts": facts}])

    def test_only_the_owner_may_ask(self):
        viewer = {**OWNER, "role": "operator"}
        web.app.dependency_overrides[web._owner_user] = lambda: (_ for _ in ()).throw(
            web.HTTPException(status_code=403, detail="Owner only"))
        web.app.dependency_overrides[web._onboarding_user] = lambda: viewer
        with patch.dict(os.environ, ON), patch.object(web, "_case_question_stream", return_value=lambda p: iter([])):
            self.assertEqual(self._ask().status_code, 403)
            self.assertIn('id="case-ask"', self.client.get(f"/account-onboarding/cases/{CASE_ID}").text)


class ScheduledCallTests(unittest.TestCase):
    def test_the_daily_refresh_records_reconciliations_and_never_fails_on_them(self):
        import run_self_service_derived_refresh as refresh

        class Failing:
            rolled_back = False

            def rollback(self):
                self.rolled_back = True

        connection = Failing()
        with patch.object(refresh, "run_reconciliations", side_effect=RuntimeError("boom")):
            result = refresh._record_agent_reconciliations(connection, "t")
        self.assertEqual((result["status"], result["error_type"], connection.rolled_back),
                         ("failed", "RuntimeError", True))
        with patch.dict(os.environ, {"ONBOARDING_AGENTS_ENABLED": ""}):
            self.assertEqual(refresh._record_agent_reconciliations(_Untouched(), "t"), {"status": "switched_off"})

    def test_the_month_end_job_records_reviews_after_the_composite_reports(self):
        import run_registry_month_end_packages as month_end

        with patch.dict(os.environ, {"ONBOARDING_AGENTS_ENABLED": ""}), patch.object(month_end, "connect") as connect:
            self.assertEqual(month_end.record_agent_reviews("postgresql://x", "t"), {"status": "switched_off"})
        connect.assert_not_called()
        source = (month_end.PROJECT_ROOT / "scripts" / "run_registry_month_end_packages.py").read_text()
        self.assertLess(source.index("render_onboarding_composite_reports.py"),
                        source.index("record_agent_reviews(database_url, tenant_id)"))


if __name__ == "__main__":
    unittest.main()
