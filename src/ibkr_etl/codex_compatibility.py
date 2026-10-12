"""Single synthetic Azure Responses probe, not a Codex execution permit.

No portfolio inputs, tools, filesystem access, retries, redirects or fallback
providers. Authentication is supplied in memory by a trusted caller and never
included in the result. A successful response does not validate Codex runtime,
streaming, tools, managed identity, or the future execution service.
"""
from __future__ import annotations

from datetime import datetime, timezone
import base64
import hashlib
import json
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .codex_workspace import AzureCodexProvider

MAX_RESPONSE_BYTES = 65536
PROBE_REPLY = "CODEX_AZURE_PROBE_OK"


def credential_diagnostics(token: str, *, expected_audience: str,
                           expected_tenant: str, now: datetime) -> dict:
    """Inspect claims locally, without treating unsigned decoding as validation."""
    result = {"signature_verified": False, "claims_readable": False}
    try:
        if len(token) > 32768 or len(token.split(".")) != 3:
            return result
        encoded = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        if not isinstance(claims, dict):
            return result
        expiry = claims.get("exp")
        not_before = claims.get("nbf")
        result.update(claims_readable=True,
                      audience_matches=claims.get("aud") == expected_audience,
                      tenant_matches=claims.get("tid") == expected_tenant)
        if type(expiry) is int:
            result["expired"] = expiry <= now.timestamp()
        if type(not_before) is int:
            result["not_yet_valid"] = not_before > now.timestamp()
    except (ValueError, TypeError, UnicodeError):
        pass
    return result


def rejection_diagnostics(body: bytes) -> dict:
    """Return fixed hints only; remote messages cannot inject retained evidence."""
    if len(body) > MAX_RESPONSE_BYTES:
        return {"error_hint": "oversized_error"}
    try:
        value = json.loads(body)
        error = value.get("error", {}) if isinstance(value, dict) else {}
        message = error.get("message", "") if isinstance(error, dict) else ""
        if not isinstance(message, str):
            return {"error_hint": "unclassified"}
        message = message.lower()
        if "invalid subscription key" in message and "endpoint" in message:
            hint = "key_or_endpoint_rejected"
        elif "audience" in message:
            hint = "token_audience_rejected"
        elif "expired" in message and "token" in message:
            hint = "token_expired"
        elif "permission" in message or "rbac" in message:
            hint = "permission_rejected"
        elif "firewall" in message or "virtual network" in message:
            hint = "network_rule_rejected"
        else:
            hint = "unclassified"
        return {"error_hint": hint}
    except (ValueError, TypeError, UnicodeError):
        return {"error_hint": "non_json_error"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(request: Request) -> tuple[int, bytes]:
    # Explicit no-redirect handler prevents forwarding authorization elsewhere.
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        return response.status, response.read(MAX_RESPONSE_BYTES + 1)


def probe_azure_responses(provider: AzureCodexProvider, bearer_token: str, *,
                          transport: Callable = _post) -> dict:
    if (not isinstance(bearer_token, str) or not bearer_token
            or any(character.isspace() for character in bearer_token)):
        raise ValueError("A valid in-memory bearer credential is required")
    started = time.monotonic()
    result = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "provider_fingerprint": provider.fingerprint,
        "deployment": provider.deployment,
        "api_version": provider.api_version,
        "probe": "synthetic_azure_responses_without_tools",
        "authentication": "caller_supplied_bearer_not_worker_identity",
        "request_count": 1,
        "source_data_sent": False,
        "tools_enabled": False,
        "execution_authorized": False,
        "codex_runtime_verified": False,
        "state": "failed",
    }
    payload = {
        "model": provider.deployment,
        "input": "Reply with exactly " + PROBE_REPLY + ". Do not add any other text.",
        "max_output_tokens": 128,
        "store": False,
        "tools": [],
        "tool_choice": "none",
    }
    request = Request(
        provider.responses_url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + bearer_token,
                 "Content-Type": "application/json"}, method="POST")
    try:
        status, body = transport(request)
        result["http_status"] = status
        if status != 200:
            result["reason"] = "http_response_not_successful"
            result.update(rejection_diagnostics(body))
        elif len(body) > MAX_RESPONSE_BYTES:
            result["reason"] = "response_size_limit"
        else:
            # Keep a hash, not potentially sensitive or untrusted error/output text.
            result["response_sha256"] = hashlib.sha256(body).hexdigest()
            response = json.loads(body)
            if not isinstance(response, dict) or response.get("status") != "completed":
                result["reason"] = "response_not_completed"
            else:
                output = response.get("output")
                valid = isinstance(output, list) and bool(output)
                text_parts = []
                for item in output if isinstance(output, list) else []:
                    if not isinstance(item, dict):
                        valid = False
                        continue
                    # Reasoning may be present but is neither read nor retained.
                    if item.get("type") == "reasoning":
                        continue
                    if item.get("type") != "message" or item.get("role") != "assistant":
                        valid = False
                        continue
                    content = item.get("content")
                    if not isinstance(content, list):
                        valid = False
                        continue
                    for part in content:
                        if (not isinstance(part, dict) or part.get("type") != "output_text"
                                or not isinstance(part.get("text"), str)):
                            valid = False
                        else:
                            text_parts.append(part["text"])
                if valid and "".join(text_parts).strip() == PROBE_REPLY:
                    result.update(state="passed", reason="synthetic_response_verified")
                else:
                    result["reason"] = "unexpected_output_or_tool_item"
    except HTTPError as error:
        result.update(http_status=error.code, reason="http_request_rejected")
        try:
            result.update(rejection_diagnostics(error.read(MAX_RESPONSE_BYTES + 1)))
        except (OSError, TypeError, AttributeError):
            result["error_hint"] = "unavailable"
        finally:
            error.close()
    except (URLError, TimeoutError, OSError):
        result["reason"] = "connection_failed_or_timed_out"
    except (ValueError, TypeError):
        result["reason"] = "invalid_response"
    result["duration_seconds"] = round(time.monotonic() - started, 3)
    return result
