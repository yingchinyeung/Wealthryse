from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
import json
import tomllib
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from ibkr_etl import web
from ibkr_etl.codex_workspace import (
    AzureCodexProvider, WORKSPACE_SCRIPT, provider_from_environment, workspace_status,
)


HOST = "test-resource.openai.azure.com"
OWNER = {"role": "owner", "authorization_source": "database",
         "object_id": "test-owner", "email": "owner@example.com"}
ENVIRONMENT = {"CODEX_AZURE_ENDPOINT": f"https://{HOST}/",
               "CODEX_AZURE_DEPLOYMENT": "test-deployment",
               "CODEX_AZURE_API_VERSION": "2025-04-01-preview",
               "CODEX_AZURE_ALLOWED_HOSTS": HOST}


class AzureCodexProviderTests(unittest.TestCase):
    def test_isolated_config_is_toml_without_secret_or_fallback(self):
        provider = provider_from_environment(ENVIRONMENT)
        document = tomllib.loads(provider.isolated_config())
        self.assertEqual(document["model"], "test-deployment")
        self.assertEqual(document["sandbox_mode"], "read-only")
        self.assertEqual(document["approval_policy"], "never")
        self.assertEqual(set(document["model_providers"]), {"azure"})
        azure = document["model_providers"]["azure"]
        self.assertEqual(azure["base_url"], f"https://{HOST}/openai")
        self.assertEqual(azure["env_key"], "CODEX_AZURE_BROKER_TOKEN")
        self.assertEqual(azure["wire_api"], "responses")
        self.assertNotIn("api_key", azure)
        with self.assertRaises(FrozenInstanceError):
            provider.deployment = "other"

    def test_missing_and_partial_configuration(self):
        self.assertIsNone(provider_from_environment({}))
        for key in ENVIRONMENT:
            with self.subTest(key=key):
                values = dict(ENVIRONMENT)
                del values[key]
                with self.assertRaises(ValueError):
                    provider_from_environment(values)

    def test_rejects_destination_injection_and_unapproved_hosts(self):
        for endpoint in (
            f"http://{HOST}", f"https://{HOST}:443", f"https://user:secret@{HOST}",
            f"https://{HOST}/openai", f"https://{HOST}?api-key=secret",
            f"https://{HOST}#secret", f"https://{HOST}.evil.example",
            "https://other.openai.azure.com", "https://127.0.0.1",
            f"\nhttps://{HOST}", f"https://{HOST}\n", f"https://{HOST}/../",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                provider_from_environment(dict(ENVIRONMENT, CODEX_AZURE_ENDPOINT=endpoint))

    def test_rejects_invalid_allowlist_deployment_and_api_version(self):
        for key, values in {
            "CODEX_AZURE_ALLOWED_HOSTS": ["*", ".openai.azure.com", "test.evil.com", HOST + ",*"],
            "CODEX_AZURE_DEPLOYMENT": ['x"\nmodel="evil', "../other", "a b", "x" * 129],
            "CODEX_AZURE_API_VERSION": ["latest", "2025-99-01", "2025-02-30", "2025-01-01&api-key=x"],
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    provider_from_environment(dict(ENVIRONMENT, **{key: value}))

    def test_config_hash_tracks_exact_provider_not_credentials(self):
        original = provider_from_environment(ENVIRONMENT).fingerprint
        self.assertEqual(len(original), 64)
        self.assertEqual(original, provider_from_environment(dict(
            ENVIRONMENT, CODEX_AZURE_BROKER_TOKEN="do-not-disclose")).fingerprint)
        for key, value in (("CODEX_AZURE_DEPLOYMENT", "changed"),
                           ("CODEX_AZURE_API_VERSION", "2025-05-01")):
            self.assertNotEqual(original, provider_from_environment(dict(
                ENVIRONMENT, **{key: value})).fingerprint)
        self.assertEqual(original, provider_from_environment(dict(
            ENVIRONMENT, CODEX_AZURE_ENDPOINT=f"https://{HOST}")).fingerprint)

    def test_configuration_never_implies_execution_or_compatibility(self):
        for environment, expected in (({}, "not_configured"),
                                      (ENVIRONMENT, "configured_unverified"),
                                      ({"CODEX_AZURE_ENDPOINT": "secret"}, "invalid")):
            data = workspace_status(dict(environment, CODEX_ENABLED="true",
                                         CODEX_EXECUTION_AVAILABLE="true",
                                         CODEX_AZURE_BROKER_TOKEN="do-not-disclose"))
            self.assertIs(data["execution_available"], False)
            self.assertEqual(data["checks"][0]["state"], expected)
            self.assertEqual(data["state"], "setup_required")
            self.assertNotIn("do-not-disclose", json.dumps(data))
            self.assertNotIn("secret", json.dumps(data))


class CodexWorkspaceWebTests(unittest.TestCase):
    def test_bookmarked_page_is_retired_without_deployment_controls(self):
        page = web.codex_workspace_page(Request({"type": "http"}), OWNER)
        html = page.body.decode()
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertEqual(page.status_code, 410)
        self.assertIn("This feature has been retired", html)
        self.assertNotIn("Your Codex workspace", html)
        self.assertNotIn("Daily specialist activity reports", html)
        self.assertNotIn("admin-chat-input", html)
        self.assertNotIn("<textarea", html)
        self.assertIn('href="/administration"', html)
        self.assertNotIn("<script", html)

    def test_direct_handlers_and_renderer_deny_fallback_and_other_roles(self):
        users = [{}, dict(OWNER, authorization_source="legacy"),
                 dict(OWNER, authorization_source="emergency_owner_fallback"),
                 dict(OWNER, object_id="")]
        users.extend(dict(OWNER, role=role) for role in (
            "viewer", "analyst", "platform_admin", "onboarding_operator"))
        for user in users:
            for handler in (web.codex_workspace_page, web.codex_workspace_status):
                with self.subTest(user=user, handler=handler.__name__), self.assertRaises(HTTPException) as error:
                    handler(Request({"type": "http"}), user)
                self.assertEqual(error.exception.status_code, 403)
            with self.assertRaises(HTTPException):
                web._render_platform_page(user, section="ai-operations")

    def test_navigation_never_exposes_cancelled_deployment(self):
        for user in (OWNER, {}, dict(OWNER, role="viewer"), dict(OWNER, authorization_source="legacy")):
            self.assertNotIn('href="/ai-operations"', web._platform_navigation(user))
        self.assertNotIn('ai-operations', web.PLATFORM_SECTIONS)
        self.assertIn('href="/administration"', web._platform_navigation(OWNER))

    def test_json_status_is_no_store_and_excludes_server_configuration(self):
        with patch.dict(web.os.environ, dict(ENVIRONMENT, CODEX_AZURE_BROKER_TOKEN="hidden"), clear=True):
            response = web.codex_workspace_status(Request({"type": "http"}), OWNER)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.status_code, 410)
        self.assertEqual(json.loads(response.body)["code"], "feature_retired")
        self.assertNotIn(b"hidden", response.body)
        self.assertNotIn(HOST.encode(), response.body)

    def test_script_refreshes_safely_and_does_not_submit_tasks(self):
        for text in ("response.status === 403", "document.hidden", "AbortController",
                     "textContent", "checks.replaceChildren()", "credentials: 'same-origin'",
                     "cache: 'no-store'", "120000", "pagehide"):
            self.assertIn(text, WORKSPACE_SCRIPT)
        for text in ("innerHTML", "localStorage", "method: 'POST'", "api-key", "eval("):
            self.assertNotIn(text, WORKSPACE_SCRIPT)

    def test_http_requests_reauthorize_and_revocation_removes_access(self):
        async def get(path):
            from httpx import ASGITransport, AsyncClient
            async with AsyncClient(transport=ASGITransport(app=web.app), base_url='https://test') as client:
                response = await asyncio.wait_for(client.get(path),10)
                return response.status_code

        with patch.object(web, "AUTHORIZATION_MODE", "database"), patch.object(
                web, "_database_authorized_user", side_effect=[OWNER, dict(OWNER, role="viewer"), None]) as auth:
            self.assertEqual(asyncio.run(get("/ai-operations")), 410)
            self.assertEqual(asyncio.run(get("/api/codex/status")), 403)
            self.assertEqual(asyncio.run(get("/ai-operations")), 403)
            self.assertEqual(auth.call_count, 3)


if __name__ == "__main__":
    unittest.main()
