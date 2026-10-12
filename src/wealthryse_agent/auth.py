"""Service-to-service sign-in with Entra ID managed identities.

Callers send an access token for the callee's Entra app (its audience). The
callee checks the signature, issuer, audience and expiry, then checks the
caller's object ID against an explicit allow-list. No shared secrets.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from functools import lru_cache

import jwt

from .config import ServiceAuthSettings


class Unauthorized(Exception):
    pass


@dataclass(frozen=True)
class ServiceCaller:
    object_id: str


@lru_cache(maxsize=4)
def _jwks(tenant_id: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(
        f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys", cache_keys=True, lifespan=3600
    )


def verify_service_token(authorization: str | None, settings: ServiceAuthSettings) -> ServiceCaller:
    if settings.disabled_for_local_tests:
        return ServiceCaller(object_id="local-test")
    if not settings.entra_tenant_id or not settings.audience or not settings.allowed_callers:
        raise Unauthorized("service authentication is not configured")
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        key = _jwks(settings.entra_tenant_id).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=settings.audience,
            options={"require": ["exp", "iat", "aud", "iss", "oid", "tid"]},
        )
    except jwt.PyJWTError as error:
        raise Unauthorized("invalid token") from error
    tenant = settings.entra_tenant_id
    if claims["tid"] != tenant or claims["iss"] not in (
        f"https://login.microsoftonline.com/{tenant}/v2.0", f"https://sts.windows.net/{tenant}/"
    ):
        raise Unauthorized("token from another tenant")
    object_id = str(claims["oid"]).lower()
    if object_id not in settings.allowed_callers:
        raise Unauthorized("caller is not allowed")
    return ServiceCaller(object_id=object_id)


class TokenSource:
    """Cached managed-identity tokens for calling another agent service."""

    def __init__(self, client_id: str, audience: str) -> None:
        self._scope = audience.rstrip("/") + "/.default"
        self._client_id = client_id
        self._lock = threading.Lock()
        self._credential = None

    def header(self) -> dict[str, str]:
        if not self._client_id:
            return {}
        with self._lock:
            if self._credential is None:
                from azure.identity import ManagedIdentityCredential

                self._credential = ManagedIdentityCredential(client_id=self._client_id)
        return {"Authorization": "Bearer " + self._credential.get_token(self._scope).token}
