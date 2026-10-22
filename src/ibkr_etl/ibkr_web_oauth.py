"""IBKR Web API client signed with first-party OAuth 1.0a: no gateway, no browser login.

Each run starts from the long-lived credentials made once on IBKR's OAuth page
(docs/IBKR_OAUTH_MARKET_DATA.md):

- consumer key (9 letters chosen at registration), access token and access token secret;
- the private signature key and private encryption key whose public halves were uploaded;
- the Diffie-Hellman prime from the dhparam.pem that was uploaded.

The run then:

1. decrypts the access token secret with the private encryption key (the "prepend");
2. asks /oauth/live_session_token for a live session token, signing with RSA-SHA256 and
   sending a fresh Diffie-Hellman challenge; the token is derived from IBKR's answer and
   checked against the signature IBKR returns, and lasts about 24 hours;
3. signs every later call with HMAC-SHA256 under that token;
4. opens the brokerage session (/iserver/auth/ssodh/init) that market data calls need,
   and logs out at the end.

Read-only use: this module has no order or account-change calls. No credential, token,
signature or Authorization header is ever logged or written to a raw copy.

Credentials come from the environment (IBKR_OAUTH_*); each PEM value may be the PEM text
itself or a path to the file. See OAuthCredentials.from_environment.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.ibkr.com/v1/api"
LIVE_SESSION_TOKEN_PATH = "/oauth/live_session_token"
USER_AGENT = "wealthryse-ibkr-oauth/1"
DEFAULT_REALM = "limited_poa"
TEST_CONSUMER = "TESTCONS"
TEST_REALM = "test_realm"
DH_GENERATOR = 2
TIMEOUT_SECONDS = 30
_NONCE_ALPHABET = string.ascii_letters + string.digits

ENV = {
    "consumer_key": "IBKR_OAUTH_CONSUMER_KEY",
    "access_token": "IBKR_OAUTH_ACCESS_TOKEN",
    "access_token_secret": "IBKR_OAUTH_ACCESS_TOKEN_SECRET",
    "signature_key": "IBKR_OAUTH_SIGNATURE_KEY",
    "encryption_key": "IBKR_OAUTH_ENCRYPTION_KEY",
    "dh_prime": "IBKR_OAUTH_DH_PRIME",
}
REALM_ENV = "IBKR_OAUTH_REALM"
BASE_URL_ENV = "IBKR_WEB_API_BASE_URL"


class OAuthError(RuntimeError):
    """IBKR refused a call, or an answer failed a check. The message never holds a credential."""


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes
    content_type: str = ""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


Transport = Callable[[str, str, Mapping[str, str], bytes | None], Response]


def urllib_transport(method: str, url: str, headers: Mapping[str, str], body: bytes | None) -> Response:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as answer:
            return Response(answer.status, answer.read(), answer.headers.get("Content-Type", ""))
    except urllib.error.HTTPError as error:
        return Response(error.code, error.read() or b"", error.headers.get("Content-Type", "") if error.headers else "")


def _pem(value: str) -> bytes:
    text = value.strip()
    if text.startswith("-----BEGIN"):
        return text.encode("ascii")
    path = Path(text).expanduser()
    if path.is_file():
        return path.read_bytes()
    raise OAuthError("a PEM setting is neither PEM text nor a readable file")


@dataclass(frozen=True)
class OAuthCredentials:
    consumer_key: str
    access_token: str
    access_token_secret: str
    signature_key_pem: bytes = field(repr=False)
    encryption_key_pem: bytes = field(repr=False)
    dh_prime: int = field(repr=False)
    realm: str = DEFAULT_REALM

    def __repr__(self) -> str:  # never show a credential
        return f"OAuthCredentials(consumer_key={self.consumer_key[:2]}..., realm={self.realm})"

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> "OAuthCredentials":
        environ = os.environ if environ is None else environ
        missing = [name for name in ENV.values() if not str(environ.get(name, "")).strip()]
        if missing:
            raise OAuthError("missing settings: " + ", ".join(missing))
        value = {key: str(environ[name]).strip() for key, name in ENV.items()}
        consumer_key = value["consumer_key"].upper()
        realm = str(environ.get(REALM_ENV, "")).strip() or (
            TEST_REALM if consumer_key == TEST_CONSUMER else DEFAULT_REALM)
        try:
            prime = int(value["dh_prime"].replace(":", "").replace(" ", ""), 16)
        except ValueError as error:
            raise OAuthError(f"{ENV['dh_prime']} is not a hex number") from error
        return cls(
            consumer_key=consumer_key,
            access_token=value["access_token"],
            access_token_secret=value["access_token_secret"],
            signature_key_pem=_pem(value["signature_key"]),
            encryption_key_pem=_pem(value["encryption_key"]),
            dh_prime=prime,
            realm=realm,
        )


# Signing helpers ----------------------------------------------------------------------

def nonce(length: int = 16) -> str:
    return "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(length))


def base_string(method: str, url: str, params: Mapping[str, str], prepend: str = "") -> str:
    """OAuth base string: METHOD&quote(url)&quote(sorted k=v pairs), IBKR-style prepend first."""
    pairs = "&".join(f"{key}={params[key]}" for key in sorted(params))
    return (prepend + method.upper() + "&" + urllib.parse.quote_plus(url)
            + "&" + urllib.parse.quote(pairs, safe=""))


def authorization_header(params: Mapping[str, str], realm: str) -> str:
    items = ", ".join(f'{key}="{params[key]}"' for key in sorted(params))
    return f'OAuth realm="{realm}", {items}'


def java_byte_array(value: int) -> bytes:
    """Big-endian bytes of a positive integer as Java's BigInteger.toByteArray gives them
    (a leading zero byte when the top bit is set), which is what IBKR hashes."""
    hex_text = format(value, "x")
    if len(hex_text) % 2:
        hex_text = "0" + hex_text
    raw = bytes.fromhex(hex_text)
    if value.bit_length() % 8 == 0:
        raw = b"\x00" + raw
    return raw


def decrypt_prepend(access_token_secret: str, encryption_key_pem: bytes) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(encryption_key_pem, password=None)
    try:
        clear = key.decrypt(base64.b64decode(access_token_secret), padding.PKCS1v15())
    except Exception as error:  # noqa: BLE001 - never echo the secret
        raise OAuthError("the access token secret does not decrypt with the private encryption key") from error
    return clear.hex()


def rsa_sha256_signature(text: str, signature_key_pem: bytes) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = serialization.load_pem_private_key(signature_key_pem, password=None)
    signed = key.sign(text.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return urllib.parse.quote_plus(base64.b64encode(signed).decode("ascii"))


def hmac_sha256_signature(text: str, live_session_token: str) -> str:
    digest = hmac.new(base64.b64decode(live_session_token), text.encode("utf-8"), hashlib.sha256).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii"))


def derive_live_session_token(dh_response_hex: str, dh_random: int, prime: int, prepend: str) -> str:
    shared = pow(int(dh_response_hex, 16), dh_random, prime)
    digest = hmac.new(java_byte_array(shared), bytes.fromhex(prepend), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def token_matches(live_session_token: str, consumer_key: str, expected_hex: str) -> bool:
    check = hmac.new(base64.b64decode(live_session_token), consumer_key.encode("utf-8"), hashlib.sha1).hexdigest()
    return hmac.compare_digest(check, str(expected_hex).lower())


# Client -------------------------------------------------------------------------------

class IbkrWebClient:
    """Signed calls to the IBKR Web API. One instance holds one live session token."""

    def __init__(self, credentials: OAuthCredentials, *, base_url: str | None = None,
                 transport: Transport = urllib_transport, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.credentials = credentials
        self.base_url = (base_url or os.environ.get(BASE_URL_ENV) or BASE_URL).rstrip("/")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._token: str | None = None
        self._token_expires_ms = 0
        self.session_open = False

    def __repr__(self) -> str:
        return f"IbkrWebClient(base_url={self.base_url!r}, session_open={self.session_open})"

    def _headers(self, authorization: str, body: bytes | None) -> dict[str, str]:
        headers = {"Authorization": authorization, "User-Agent": USER_AGENT, "Accept": "application/json",
                   "Connection": "keep-alive", "Content-Length": str(len(body or b""))}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return headers

    def live_session_token(self) -> str:
        """A live session token, fetched once and reused until 10 minutes before it expires."""
        now_ms = int(self._clock() * 1000)
        if self._token and now_ms < self._token_expires_ms - 600_000:
            return self._token
        creds = self.credentials
        prepend = decrypt_prepend(creds.access_token_secret, creds.encryption_key_pem)
        dh_random = secrets.randbits(256)
        challenge = format(pow(DH_GENERATOR, dh_random, creds.dh_prime), "x")
        url = self.base_url + LIVE_SESSION_TOKEN_PATH
        params = {
            "diffie_hellman_challenge": challenge,
            "oauth_consumer_key": creds.consumer_key,
            "oauth_nonce": nonce(),
            "oauth_signature_method": "RSA-SHA256",
            "oauth_timestamp": str(int(self._clock())),
            "oauth_token": creds.access_token,
        }
        params["oauth_signature"] = rsa_sha256_signature(base_string("POST", url, params, prepend),
                                                         creds.signature_key_pem)
        answer = self._transport("POST", url, self._headers(authorization_header(params, creds.realm), None), None)
        if answer.status != 200:
            raise OAuthError(f"live session token refused: HTTP {answer.status} {_short(answer.body)}")
        payload = answer.json() or {}
        try:
            token = derive_live_session_token(payload["diffie_hellman_response"], dh_random,
                                              creds.dh_prime, prepend)
            expected = payload["live_session_token_signature"]
        except (KeyError, TypeError, ValueError) as error:
            raise OAuthError("live session token answer is missing its Diffie-Hellman fields") from error
        if not token_matches(token, creds.consumer_key, expected):
            raise OAuthError("live session token failed IBKR's signature check (check the DH prime and keys)")
        self._token = token
        self._token_expires_ms = int(payload.get("live_session_token_expiration") or now_ms + 86_400_000)
        return token

    def request(self, method: str, path: str, *, params: Mapping[str, Any] | None = None,
                body: Mapping[str, Any] | None = None) -> Response:
        """One signed call. Query parameters are signed; a JSON body is not (IBKR's rule)."""
        token = self.live_session_token()
        url = self.base_url + path
        query = {str(k): str(v) for k, v in (params or {}).items() if v is not None}
        oauth = {
            "oauth_consumer_key": self.credentials.consumer_key,
            "oauth_nonce": nonce(),
            "oauth_signature_method": "HMAC-SHA256",
            "oauth_timestamp": str(int(self._clock())),
            "oauth_token": self.credentials.access_token,
        }
        oauth["oauth_signature"] = hmac_sha256_signature(base_string(method, url, {**oauth, **query}), token)
        full_url = url + ("?" + urllib.parse.urlencode(query) if query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        return self._transport(method.upper(), full_url,
                               self._headers(authorization_header(oauth, self.credentials.realm), data), data)

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        answer = self.request("GET", path, params=params)
        if answer.status != 200:
            raise OAuthError(f"GET {path} refused: HTTP {answer.status} {_short(answer.body)}")
        return answer.json()

    def open_brokerage_session(self, *, attempts: int = 5, pause: float = 2.0) -> dict[str, Any]:
        """Opens the brokerage session market data needs, then waits until it reports authenticated."""
        answer = self.request("POST", "/iserver/auth/ssodh/init", body={"publish": True, "compete": True})
        if answer.status != 200:
            raise OAuthError(f"brokerage session refused: HTTP {answer.status} {_short(answer.body)}")
        status: dict[str, Any] = {}
        for attempt in range(attempts):
            status = self.request("POST", "/iserver/auth/status").json() or {}
            if status.get("authenticated") and status.get("connected", True):
                break
            self._sleep(pause * (attempt + 1))
        else:
            raise OAuthError("brokerage session did not become authenticated: "
                             + json.dumps({k: status.get(k) for k in ("authenticated", "competing", "connected", "message")}))
        # IBKR wants /iserver/accounts read once before market data calls.
        self.get_json("/iserver/accounts")
        self.session_open = True
        return {k: status.get(k) for k in ("authenticated", "competing", "connected")}

    def logout(self) -> None:
        if self._token is None:
            return
        try:
            self.request("POST", "/logout")
        except Exception as error:  # noqa: BLE001 - logging out is best effort
            LOGGER.warning("IBKR logout failed: %s", type(error).__name__)
        self.session_open = False

    def __enter__(self) -> "IbkrWebClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.logout()


def _short(body: bytes, limit: int = 200) -> str:
    text = body.decode("utf-8", "replace").strip().replace("\n", " ")
    return text[:limit]
