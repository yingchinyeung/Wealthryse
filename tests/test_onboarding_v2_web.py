from __future__ import annotations

import hashlib
import json
import os
import unittest
from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient

from ibkr_etl import onboarding_v2_web, web

OWNER = {"email": "owner@example.com", "object_id": "owner-oid", "role": "owner"}
OPERATOR = {"email": "operator@example.com", "object_id": "operator-oid", "role": "onboarding_operator"}
ON = {"ONBOARDING_V2_ENABLED": "true"}
OFF = {"ONBOARDING_V2_ENABLED": "false"}
MS_LIKE = {
    "kind": "composite_portfolio", "subject_id": "alpha-world-asia", "display_name": "Alpha World Asia",
    "description": "Alpha World MS sleeves, reported under its own name.", "reporting_currency": "HKD",
    "start_date": "2025-01-20", "report_style": "alpha-world-ms",
    "sleeves": [{"component_id": "hong-kong-china", "direction": "long", "exposure_percent": 91},
                {"component_id": "U16088571", "direction": "long", "exposure_percent": 45},
                {"component_id": "hang-seng-price-index", "direction": "short", "exposure_percent": 49},
                {"component_id": "spy-settlement", "direction": "short", "exposure_percent": 45}],
}


class _Rows:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row

    def fetchall(self):
        return []


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement, parameters=()):
        self.statements.append((statement, parameters))
        return _Rows()


class _SourceConnection:
    """Answers every query with one row: the Studio's lookup of its source account."""

    def __init__(self, row):
        self.row = row

    def execute(self, statement, parameters=()):
        return _Rows(self.row)


@contextmanager
def _database(connection):
    yield connection


class OnboardingV2WebTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web.app)
        self.user = OWNER
        web.app.dependency_overrides[web._onboarding_user] = lambda: self.user
        web.app.dependency_overrides[web._owner_user] = self._owner
        self.addCleanup(web.app.dependency_overrides.clear)

    def _owner(self):
        if self.user["role"] != "owner":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Owner access is required")
        return self.user

    def test_switched_off_everything_new_is_hidden(self):
        with patch.dict(os.environ, OFF):
            for path in ("/broker-connections", "/api/onboarding-v2/overview", "/api/broker-connections",
                         "/account-onboarding/cases/00000000-0000-0000-0000-000000000001",
                         "/account-onboarding/new-portfolio", "/api/onboarding-v2/portfolio-builder"):
                self.assertEqual(self.client.get(path).status_code, 404, path)
            response = self.client.post("/api/broker-connections", json={"broker": "kiwoom_rest", "display_name": "x"})
            self.assertEqual(response.status_code, 404)
            for path in ("/api/onboarding-v2/portfolios", "/api/onboarding-v2/portfolios/check",
                         "/api/onboarding-v2/portfolios/preview"):
                self.assertEqual(self.client.post(path, json=MS_LIKE).status_code, 404, path)
            page = web.account_onboarding_page(None, OWNER)
            self.assertIn("Guided onboarding wizard", page)
            self.assertIn("Creation starts only a private nine-page onboarding case", page)
            self.assertIn('href="/ibkr-connections">IBKR connections</a>', page)
            self.assertNotIn("Broker connections", page)
            classic = self.client.get("/account-onboarding/classic", follow_redirects=False)
            self.assertEqual((classic.status_code, classic.headers["location"]), (307, "/account-onboarding"))

    def test_switched_on_the_pages_replace_the_old_ones(self):
        with patch.dict(os.environ, ON):
            page = web.account_onboarding_page(None, OWNER)
            self.assertIn("Cases in progress", page)
            self.assertIn("Available to onboard", page)
            self.assertIn("/api/onboarding-v2/overview", page)
            self.assertIn('href="/broker-connections">Broker connections</a>', page)
            self.assertIn('href="/account-onboarding/new-portfolio">New portfolio</a>', page)
            self.assertNotIn("IBKR connections</a>", page)
            self.assertNotIn("Guided onboarding wizard", page)
            classic = self.client.get("/account-onboarding/classic")
            self.assertEqual(classic.status_code, 200)
            self.assertIn("Portfolios now start in the portfolio builder", classic.text)
            self.assertIn('id="derived-portfolio-studio" style="display:none" aria-hidden="true"', classic.text)
            self.assertEqual(classic.text.count('id="derived-portfolio-studio"'), 1)
            builder = self.client.get("/account-onboarding/new-portfolio").text
            self.assertIn("Start from", builder)
            self.assertIn("/api/onboarding-v2/portfolio-builder", builder)
            self.assertIn('aria-current="page">Account onboarding</a>', builder)
            connections = self.client.get("/broker-connections").text
            self.assertIn('aria-current="page">Broker connections</a>', connections)
            self.assertIn("Show the Flex query recipe", connections)
            self.assertIn("Never paste a token or key into chat", connections)
            case_page = self.client.get("/account-onboarding/cases/00000000-0000-0000-0000-000000000001").text
            self.assertIn('data-case-id="00000000-0000-0000-0000-000000000001"', case_page)
            self.assertIn("Your three approvals", case_page)
            self.assertIn('id="case-portfolio"', case_page)
            self.assertEqual(self.client.get("/account-onboarding/cases/not-a-uuid").status_code, 422)

    def test_overview_and_case_read_through_the_read_only_database(self):
        connection = _RecordingConnection()
        with patch.dict(os.environ, {**ON, "BROKER_EGRESS_IP": "20.6.1.10"}), \
                patch.object(web, "_database", return_value=_database(connection)), \
                patch.object(onboarding_v2_web, "onboarding_overview", return_value={"cases": []}) as overview:
            self.assertEqual(self.client.get("/api/onboarding-v2/overview").json(), {"cases": []})
        self.assertIs(overview.call_args.args[0], connection)
        self.assertEqual(overview.call_args.kwargs["platform_ip"], "20.6.1.10")

    def test_the_studio_is_replaced_by_the_builder_when_switched_on(self):
        source = {"subject_id": "U0000501", "subject_type": "physical_account", "lifecycle_status": "active",
                  "currency": "USD", "history_start_date": None}
        body = {"subject_id": "synthetic-ex-taiwan", "display_name": "Synthetic ex Taiwan",
                "source_account_id": "U0000501", "base_currency": "USD", "history_start_date": "2025-01-20",
                "excluded_countries": ["Taiwan"], "opening_cash": {"USD": 0}, "allowed_cash_currencies": ["USD"]}

        def studio(switch, url="/api/onboarding/derived-studio/filtered/start", json_body=body):
            with patch.dict(os.environ, switch), \
                    patch.object(web, "_approval_database", return_value=_database(_SourceConnection(source))), \
                    patch.object(web, "_create_derived_studio_case", return_value=("c2", "d" * 64)) as classic:
                return self.client.post(url, json=json_body), classic

        response, classic = studio(ON)
        self.assertEqual(response.status_code, 409)
        self.assertIn("replaced by the portfolio builder", response.json()["detail"])
        classic.assert_not_called()
        response, classic = studio(ON, "/api/onboarding/derived-studio/composite/start", {
            "subject_id": "synthetic-long-short", "display_name": "Synthetic long short", "base_currency": "HKD",
            "normalization_date": "2025-01-20", "constant_cash": 58,
            "components": [{"component_id": "U0000501", "coefficient": 0.42}]})
        self.assertEqual(response.status_code, 409)
        response, classic = studio(OFF)
        self.assertEqual(response.json()["next_page"], "/account-onboarding/synthetic-ex-taiwan/steps/financial-contract")
        self.assertEqual(classic.call_args.kwargs["definition"]["subject_type"], "filtered_portfolio")

    def test_the_builder_runs_the_studio_rules_then_opens_a_case(self):
        captured = {}

        def composite(connection, submission, subject):
            captured["submission"], captured["subject"] = submission, subject
            return {"subject_type": "composite_portfolio", "subject_id": subject}

        opened = {"case_id": "c1", "next_page": "/account-onboarding/cases/c1"}
        with patch.dict(os.environ, ON), \
                patch.object(web, "_approval_database", side_effect=lambda: _database(_RecordingConnection())), \
                patch.object(web, "_studio_composite_definition", side_effect=composite), \
                patch.object(onboarding_v2_web, "start_derived_case", return_value=opened) as start:
            response = self.client.post("/api/onboarding-v2/portfolios", json=MS_LIKE)
            self.assertEqual((response.status_code, response.json()), (200, opened), response.text)
            submission = captured["submission"]
            self.assertEqual((captured["subject"], submission.base_currency, submission.constant_cash),
                             ("alpha-world-asia", "HKD", 58.0))
            self.assertEqual([(c.component_id, c.coefficient) for c in submission.components],
                             [("hong-kong-china", 0.91), ("U16088571", 0.45), ("hang-seng-price-index", -0.49),
                              ("spy-settlement", -0.45)])
            self.assertEqual(start.call_args.kwargs["description"], MS_LIKE["description"])
            self.assertEqual(start.call_args.kwargs["definition"]["subject_id"], "alpha-world-asia")
            self.assertEqual(start.call_args.kwargs["report_style"], "alpha-world-ms")
            unknown = self.client.post("/api/onboarding-v2/portfolios", json={**MS_LIKE, "report_style": "x"})
            self.assertEqual(unknown.status_code, 422)
            for bad in ("ab", "alpha_world_asia"):
                refused = self.client.post("/api/onboarding-v2/portfolios", json={**MS_LIKE, "subject_id": bad})
                self.assertEqual(refused.status_code, 400)
                self.assertIn("letters, digits and hyphens", refused.json()["detail"])
            no_name = self.client.post("/api/onboarding-v2/portfolios", json={**MS_LIKE, "display_name": "A"})
            self.assertEqual(no_name.status_code, 400)
            self.assertTrue(no_name.json()["detail"].startswith("Check the name:"), no_name.text)
            no_sleeves = self.client.post("/api/onboarding-v2/portfolios", json={**MS_LIKE, "sleeves": []})
            self.assertEqual((no_sleeves.status_code, no_sleeves.json()["detail"]), (400, "Add at least one sleeve"))
        self.user = OPERATOR
        with patch.dict(os.environ, ON), \
                patch.object(web, "_approval_database", side_effect=lambda: _database(_RecordingConnection())), \
                patch.object(web, "_studio_composite_definition", side_effect=composite), \
                patch.object(onboarding_v2_web, "check_portfolio", return_value={"ok": True}) as check:
            self.assertEqual(self.client.post("/api/onboarding-v2/portfolios/check", json=MS_LIKE).json(),
                             {"ok": True})
        self.assertEqual(check.call_args.kwargs["description"], MS_LIKE["description"])
        self.assertEqual(check.call_args.kwargs["report_style"], "alpha-world-ms")

    def test_the_builder_previews_the_tree_from_the_read_only_database(self):
        connection = _RecordingConnection()
        with patch.dict(os.environ, ON), patch.object(web, "_database", side_effect=lambda: _database(connection)):
            preview = self.client.post("/api/onboarding-v2/portfolios/preview", json=MS_LIKE)
            catalogue = self.client.get("/api/onboarding-v2/portfolio-builder")
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertEqual(preview.json()["totals"]["cash"], 58.0)
        self.assertIn("Composition of Alpha World Asia", preview.json()["svg"])
        self.assertIn("Hang Seng", preview.json()["svg"])
        self.assertEqual(catalogue.status_code, 200, catalogue.text)
        self.assertIn("hang-seng-price-index", [item["component_id"] for item in catalogue.json()["indices"]])
        self.assertFalse(any("INSERT" in sql or "UPDATE" in sql for sql, _ in connection.statements))

    def test_operators_can_start_but_only_the_owner_approves_or_adds_keys(self):
        self.user = OPERATOR
        with patch.dict(os.environ, ON):
            approval = self.client.post(
                "/api/onboarding-v2/cases/00000000-0000-0000-0000-000000000001/approvals/account_terms",
                json={"terms_sha256": "a" * 64})
            self.assertEqual(approval.status_code, 403)
            added = self.client.post("/api/broker-connections", json={"broker": "kiwoom_rest", "display_name": "x"})
            self.assertEqual(added.status_code, 403)

    def test_refusals_come_back_as_plain_messages(self):
        connection = _RecordingConnection()
        refusal = onboarding_v2_web.OnboardingError("Another account is still being onboarded")
        with patch.dict(os.environ, ON), \
                patch.object(web, "_approval_database", return_value=_database(connection)), \
                patch.object(onboarding_v2_web, "start_case", side_effect=refusal):
            response = self.client.post("/api/onboarding-v2/cases", json={
                "connection_id": "00000000-0000-0000-0000-000000000001", "broker_account_id": "50123456",
                "display_name": "Kiwoom Korea", "reporting_currency": "KRW", "history_start_date": "2025-01-02"})
        self.assertEqual((response.status_code, response.json()["detail"]),
                         (409, "Another account is still being onboarded"))

    def test_kiwoom_keys_go_only_to_key_vault(self):
        writes, reads = _RecordingConnection(), _RecordingConnection()
        stored = {}

        def store(name, value):
            stored[name] = value
            return {"status": "stored", "secret_name": name, "token_fingerprint": "0123456789abcdef"}

        body = {"broker": "kiwoom_rest", "display_name": "Kiwoom Korea", "environment": "mock",
                "account_id": "5012-3456-10", "app_key": "synthetic-app-key", "app_secret": "synthetic-app-secret",
                "ip_locked_to": "20.6.1.10"}
        with patch.dict(os.environ, ON), \
                patch.object(web, "_database", return_value=_database(reads)), \
                patch.object(web, "_approval_database", return_value=_database(writes)), \
                patch.object(web, "_store_flex_token_via_broker", side_effect=store):
            response = self.client.post("/api/broker-connections", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        (secret_name, value), = stored.items()
        self.assertTrue(secret_name.startswith("broker-kiwoom-rest-"))
        self.assertEqual(json.loads(value), {"app_key": "synthetic-app-key", "app_secret": "synthetic-app-secret"})
        everything_written = repr(writes.statements) + response.text
        self.assertNotIn("synthetic-app", everything_written)
        self.assertIn("5012345610", everything_written)
        self.assertTrue(any("INSERT INTO broker_connection_accounts" in sql for sql, _ in writes.statements))

    def test_ibkr_needs_a_query_id_and_its_expiry(self):
        with patch.dict(os.environ, ON), \
                patch.object(web, "_store_flex_token_via_broker", side_effect=AssertionError("not stored")):
            base = {"broker": "ibkr_flex", "display_name": "Second login", "flex_token": "123456"}
            missing_query = self.client.post("/api/broker-connections", json=base)
            self.assertEqual(missing_query.status_code, 400)
            missing_expiry = self.client.post("/api/broker-connections", json={**base, "query_id": "123"})
            self.assertEqual(missing_expiry.status_code, 400)
            self.assertIn("expiry", missing_expiry.json()["detail"])
            too_long = (date.today() + timedelta(days=500)).isoformat()
            self.assertEqual(self.client.post("/api/broker-connections", json={
                **base, "query_id": "123", "expires_on": too_long}).status_code, 400)

    def test_a_failed_key_vault_write_registers_nothing(self):
        writes, reads = _RecordingConnection(), _RecordingConnection()
        body = {"broker": "ibkr_flex", "display_name": "Second login", "flex_token": "123456", "query_id": "123",
                "expires_on": (date.today() + timedelta(days=300)).isoformat()}
        with patch.dict(os.environ, ON), \
                patch.object(web, "_database", return_value=_database(reads)), \
                patch.object(web, "_approval_database", return_value=_database(writes)), \
                patch.object(web, "_store_flex_token_via_broker", side_effect=RuntimeError("vault down")):
            response = self.client.post("/api/broker-connections", json=body)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(writes.statements, [])
        self.assertNotIn("123456", response.text)

    def test_the_owner_opens_a_listed_stage_three_report_from_private_storage(self):
        content = b"%PDF synthetic composite factsheet"
        artifact = {"container_name": "internal-reports", "sha256": hashlib.sha256(content).hexdigest(),
                    "blob_name": "review/synthetic-design/2025-01-31/x-synthetic-design_Factsheet_2025-01-31.pdf",
                    "media_type": "application/pdf", "file_name": "synthetic-design_Factsheet_2025-01-31.pdf"}
        case_id = "00000000-0000-0000-0000-000000000001"
        path = f"/api/onboarding-v2/cases/{case_id}/reports/7"
        with patch.dict(os.environ, OFF):
            self.assertEqual(self.client.get(path).status_code, 404)
        with patch.dict(os.environ, ON), \
                patch.object(web, "_database", side_effect=lambda: _database(_RecordingConnection())), \
                patch.object(onboarding_v2_web, "case_report", return_value=artifact) as lookup, \
                patch.object(web, "download_blob_bytes", return_value=content) as download:
            response = self.client.get(path)
            self.assertEqual((response.status_code, response.content), (200, content))
            self.assertEqual(response.headers["content-disposition"],
                             'attachment; filename="synthetic-design_Factsheet_2025-01-31.pdf"')
            self.assertIn("no-store", response.headers["cache-control"])
            self.assertEqual(lookup.call_args.args[1:], (web.TENANT_ID, case_id, 7))
            self.assertEqual(lookup.call_args.kwargs, {"container": web.INTERNAL_REPORT_CONTAINER})
            self.assertEqual(download.call_args.args,
                             (web.AZURE_STORAGE_ACCOUNT_URL, "internal-reports", artifact["blob_name"]))
            download.return_value = b"changed in storage"
            self.assertEqual(self.client.get(path).status_code, 503)
            lookup.side_effect = onboarding_v2_web.OnboardingError("This report is not listed on the case", 404)
            self.assertEqual(self.client.get(path).status_code, 404)
            self.user = OPERATOR
            self.assertEqual(self.client.get(path).status_code, 403)


if __name__ == "__main__":
    unittest.main()
