from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from azure.identity import ManagedIdentityCredential
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr


SECRET_PATTERN = re.compile(r"^ibkr-flex-source-[0-9a-f]{32}$")
VAULT_SCOPE = "https://vault.azure.net/.default"


class CredentialSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret_name: str = Field(pattern=r"^ibkr-flex-source-[0-9a-f]{32}$")
    token: SecretStr


app = FastAPI(title="IBKR credential broker", docs_url=None, redoc_url=None, openapi_url=None)


def _required_environment() -> tuple[str, str, str]:
    vault_uri = os.environ.get("KEY_VAULT_URI", "").strip().rstrip("/")
    broker_auth = os.environ.get("BROKER_AUTH", "").strip()
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    parsed = urllib.parse.urlparse(vault_uri)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(".vault.azure.net")
        or parsed.path not in {"", "/"}
    ):
        raise RuntimeError("Credential broker Key Vault URI is not configured safely")
    if len(broker_auth) < 32 or not client_id:
        raise RuntimeError("Credential broker authentication is not configured")
    return vault_uri, broker_auth, client_id


def _fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def store_flex_secret(
    *, vault_uri: str, client_id: str, secret_name: str, token: str
) -> dict[str, str]:
    if not SECRET_PATTERN.fullmatch(secret_name):
        raise ValueError("Secret name is outside the dedicated IBKR prefix")
    if not token.strip() or len(token) > 1024:
        raise ValueError("Flex token is invalid")
    access_token = ManagedIdentityCredential(client_id=client_id).get_token(VAULT_SCOPE).token
    request = urllib.request.Request(
        f"{vault_uri}/secrets/{secret_name}?api-version=7.4",
        data=json.dumps(
            {
                "value": token,
                "attributes": {"enabled": True},
                "tags": {
                    "application": "ibkr-portfolio-platform",
                    "purpose": "flex-web-service",
                    "managedBy": "credential-broker",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        method="PUT",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)
    secret_id = str(payload.get("id") or "")
    if f"/secrets/{secret_name}/" not in secret_id:
        raise RuntimeError("Key Vault did not confirm the requested secret")
    return {
        "secret_name": secret_name,
        "token_fingerprint": _fingerprint(token),
        "status": "stored",
    }


@app.get("/health")
def health() -> dict[str, str]:
    _required_environment()
    return {"status": "ready", "capability": "set-only-flex-secrets"}


@app.post("/v1/credentials")
def create_credential(
    submission: CredentialSubmission,
    authorization: str = Header(default=""),
) -> dict[str, str]:
    try:
        vault_uri, broker_auth, client_id = _required_environment()
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail="Credential broker is unavailable") from error
    expected = f"Bearer {broker_auth}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(status_code=403, detail="Credential broker request denied")
    try:
        return store_flex_secret(
            vault_uri=vault_uri,
            client_id=client_id,
            secret_name=submission.secret_name,
            token=submission.token.get_secret_value(),
        )
    except (ValueError, urllib.error.URLError, RuntimeError) as error:
        raise HTTPException(status_code=503, detail="Credential could not be stored") from error
