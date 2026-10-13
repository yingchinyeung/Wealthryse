from __future__ import annotations

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
                         "/account-onboarding/cases/00000000-0000-0000-0000-000000000001"):
                self.assertEqual(self.client.get(path).status_code, 404, path)
            response = self.client.post("/api/broker-connections", json={"broker": "kiwoom_rest", "display_name": "x"})
            self.assertEqual(response.status_code, 404)
            page = web.account_onboarding_page(None, OWNER)
            self.assertIn("Guided onboarding wizard", page)
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
            self.assertNotIn("IBKR connections</a>", page)
            self.assertNotIn("Guided onboarding wizard", page)
            classic = self.client.get("/account-onboarding/classic")
            self.assertEqual(classic.status_code, 200)
            self.assertIn("Derived Portfolio Studio", classic.text)
            connections = self.client.get("/broker-connections").text
            self.assertIn('aria-current="page">Broker connections</a>', connections)
            self.assertIn("Show the Flex query recipe", connections)
            self.assertIn("Never paste a token or key into chat", connections)
            case_page = self.client.get("/account-onboarding/cases/00000000-0000-0000-0000-000000000001").text
            self.assertIn('data-case-id="00000000-0000-0000-0000-000000000001"', case_page)
            self.assertIn("Your three approvals", case_page)
            self.assertEqual(self.client.get("/account-onboarding/cases/not-a-uuid").status_code, 422)

    def test_overview_and_case_read_through_the_read_only_database(self):
        connection = _RecordingConnection()
        with patch.dict(os.environ, {**ON, "BROKER_EGRESS_IP": "20.6.1.10"}), \
                patch.object(web, "_database", return_value=_database(connection)), \
                patch.object(onboarding_v2_web, "onboarding_overview", return_value={"cases": []}) as overview:
            self.assertEqual(self.client.get("/api/onboarding-v2/overview").json(), {"cases": []})
        self.assertIs(overview.call_args.args[0], connection)
        self.assertEqual(overview.call_args.kwargs["platform_ip"], "20.6.1.10")

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


if __name__ == "__main__":
    unittest.main()
