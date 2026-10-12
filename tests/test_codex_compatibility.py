from __future__ import annotations

import json
import base64
from datetime import datetime, timezone
import tomllib
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request

from ibkr_etl.codex_compatibility import (
    MAX_RESPONSE_BYTES, PROBE_REPLY, _NoRedirect, probe_azure_responses,
    credential_diagnostics, rejection_diagnostics,
)
from ibkr_etl.codex_workspace import AzureCodexProvider


class AzureCompatibilityTests(unittest.TestCase):
    def test_claim_diagnostics_never_assert_signature_or_retain_claims(self):
        now = datetime(2026, 10, 2, tzinfo=timezone.utc)
        claims = {"aud": "expected", "tid": "tenant", "exp": int(now.timestamp()) + 500,
                  "nbf": int(now.timestamp()) - 500, "oid": "private-identity"}
        encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        result = credential_diagnostics("header." + encoded + ".signature", expected_audience="expected",
                                         expected_tenant="tenant", now=now)
        self.assertEqual(result, {"signature_verified": False, "claims_readable": True,
                                 "audience_matches": True, "tenant_matches": True,
                                 "expired": False, "not_yet_valid": False})
        self.assertNotIn("private-identity", json.dumps(result))
        for token in ("opaque", "a.bad.c", "a." + "x" * 33000 + ".c"):
            self.assertFalse(credential_diagnostics(token, expected_audience="a",
                expected_tenant="b", now=now)["claims_readable"])

    def test_error_classification_is_fixed_and_sanitized(self):
        for message, expected in (("Invalid subscription key or wrong endpoint", "key_or_endpoint_rejected"),
                                   ("Wrong audience", "token_audience_rejected"),
                                   ("Token expired", "token_expired"),
                                   ("RBAC permission failure", "permission_rejected"),
                                   ("Firewall denied", "network_rule_rejected"),
                                   ("private secret token value", "unclassified")):
            value = rejection_diagnostics(json.dumps({"error": {"message": message}}).encode())
            self.assertEqual(value, {"error_hint": expected})
            self.assertNotIn(message, json.dumps(value))
        self.assertEqual(rejection_diagnostics(b"raw private error"), {"error_hint": "non_json_error"})
        self.assertEqual(rejection_diagnostics(b"x" * (MAX_RESPONSE_BYTES + 1)), {"error_hint": "oversized_error"})

    def setUp(self):
        self.provider = AzureCodexProvider("https://test.openai.azure.com/", "test",
                                          "2025-04-01-preview", ("test.openai.azure.com",))

    def response(self, **overrides):
        value = {"status": "completed", "output": [
            {"type": "reasoning", "content": "must-not-be-retained"},
            {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": PROBE_REPLY}]}]}
        value.update(overrides)
        return json.dumps(value).encode()

    def test_success_is_not_codex_or_execution_acceptance(self):
        requests = []
        def post(request):
            requests.append(request)
            return 200, self.response()
        result = probe_azure_responses(self.provider, "synthetic-token", transport=post)
        self.assertEqual(result["state"], "passed")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://test.openai.azure.com/openai/responses?api-version=2025-04-01-preview")
        body = json.loads(requests[0].data)
        self.assertFalse(body["store"])
        self.assertEqual(body["tools"], [])
        self.assertEqual(body["tool_choice"], "none")
        self.assertEqual(body["max_output_tokens"], 128)
        for key in ("execution_authorized", "codex_runtime_verified", "source_data_sent", "tools_enabled"):
            self.assertIs(result[key], False)
        for secret in ("synthetic-token", "must-not-be-retained", PROBE_REPLY):
            self.assertNotIn(secret, json.dumps(result))

    def test_invalid_tokens_do_not_start_request(self):
        for token in ("", None, "x\r\nOther: x", "two words"):
            with self.subTest(token=token), self.assertRaises(ValueError):
                probe_azure_responses(self.provider, token,
                                     transport=lambda request: self.fail("Request issued"))

    def test_v1_is_explicit_and_never_falls_back(self):
        provider = AzureCodexProvider("https://test.openai.azure.com/", "test", "v1",
                                      ("test.openai.azure.com",))
        self.assertNotEqual(provider.fingerprint, self.provider.fingerprint)
        config = tomllib.loads(provider.isolated_config())["model_providers"]["azure"]
        self.assertEqual(config["base_url"], "https://test.openai.azure.com/openai/v1")
        self.assertNotIn("query_params", config)
        requests = []
        def post(request):
            requests.append(request)
            return 401, b"private"
        result = probe_azure_responses(provider, "token", transport=post)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "https://test.openai.azure.com/openai/v1/responses")
        self.assertEqual(result["api_version"], "v1")
        self.assertEqual(result["state"], "failed")

    def test_errors_are_sanitized_and_never_retried(self):
        for error in (HTTPError("https://secret", 403, "private-error", {}, None),
                      URLError("private-error"), TimeoutError("private-error")):
            calls = []
            def post(request):
                calls.append(request)
                raise error
            result = probe_azure_responses(self.provider, "token", transport=post)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("private-error", json.dumps(result))
            self.assertNotIn("https://secret", json.dumps(result))

    def test_incomplete_or_failed_response_cannot_pass(self):
        for status in ("in_progress", "failed", "cancelled", "incomplete", None):
            result = probe_azure_responses(self.provider, "token", transport=lambda request: (
                200, self.response(status=status)))
            self.assertEqual(result["reason"], "response_not_completed")

    def test_rejects_wrong_output_and_tool_items(self):
        for output in (None, [], ["bad"], [{"type": "reasoning"}],
                       [{"type": "function_call", "name": "shell"}],
                       [{"type": "message", "role": "user", "content": []}],
                       [{"type": "message", "role": "assistant", "content": "bad"}],
                       [{"type": "message", "role": "assistant", "content": [
                           {"type": "output_text", "text": "Wrong response"}]}]):
            result = probe_azure_responses(self.provider, "token", transport=lambda request: (
                200, self.response(output=output)))
            self.assertEqual(result["state"], "failed")

    def test_rejects_oversized_and_invalid_json(self):
        for body, reason in ((b"x" * (MAX_RESPONSE_BYTES + 1), "response_size_limit"),
                             (b"private error", "invalid_response"),
                             (b"[]", "response_not_completed")):
            result = probe_azure_responses(self.provider, "token", transport=lambda request: (200, body))
            self.assertEqual(result["reason"], reason)
            self.assertNotIn("private error", json.dumps(result))

    def test_redirects_and_non_success_are_not_accepted(self):
        self.assertIsNone(_NoRedirect().redirect_request(
            Request("https://test.openai.azure.com"), None, 302, "", {}, "https://evil.invalid"))
        for status in (201, 302, 401, 403, 429, 500):
            result = probe_azure_responses(self.provider, "token", transport=lambda request: (status, b"secret"))
            self.assertEqual(result["reason"], "http_response_not_successful")
            self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
