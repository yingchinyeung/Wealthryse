"""Broker connections for the new account onboarding.

A connection is one broker login. It never holds a secret: the token or key
pair goes to Key Vault under the connection's own secret name and is never
read back by the website. Each broker declares how its account numbers look,
how long its credentials may last, and whether the broker locks them to the
platform's fixed outbound address.

An IBKR connection shares its ID with its ``ibkr_flex_credentials`` row and
keeps the existing ``ibkr-flex-source-<id>`` secret name, so the current Flex
fetch, queue and circuit code reads it unchanged.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping


RENEW_WARNING_DAYS = 30
SWITCH_NAME = "ONBOARDING_V2_ENABLED"


@dataclass(frozen=True)
class BrokerSpec:
    key: str
    label: str
    account_pattern: re.Pattern[str]
    account_hint: str
    secret_prefix: str
    credential_fields: tuple[str, ...]
    credential_max_days: int | None
    supports_ip_lock: bool
    environments: tuple[str, ...]


BROKERS: Mapping[str, BrokerSpec] = {
    "ibkr_flex": BrokerSpec(
        key="ibkr_flex",
        label="IBKR (Flex)",
        account_pattern=re.compile(r"^U[0-9]{6,12}$"),
        account_hint="U followed by 6 to 12 digits",
        secret_prefix="ibkr-flex-source-",
        credential_fields=("flex_token",),
        # IBKR lets the owner pick 6 hours to 1 year when generating a token.
        credential_max_days=366,
        supports_ip_lock=True,
        environments=("live",),
    ),
    "kiwoom_rest": BrokerSpec(
        key="kiwoom_rest",
        label="Kiwoom (REST API)",
        account_pattern=re.compile(r"^[0-9]{8}([0-9]{2})?$"),
        account_hint="8 digits, or 10 with the product code",
        secret_prefix="broker-kiwoom-rest-",
        credential_fields=("app_key", "app_secret"),
        credential_max_days=None,
        # Kiwoom only answers calls from IP addresses registered to the app key.
        supports_ip_lock=True,
        environments=("live", "mock"),
    ),
}


def onboarding_v2_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """The new onboarding stays off unless the switch is set explicitly."""

    source = os.environ if environ is None else environ
    return source.get(SWITCH_NAME, "").strip().lower() == "true"


def broker_spec(broker: str) -> BrokerSpec:
    try:
        return BROKERS[broker]
    except KeyError as error:
        raise ValueError(f"Unsupported broker: {broker}") from error


def normalize_account_id(broker: str, value: str) -> str:
    """Return the stored form of a broker account number, or raise."""

    spec = broker_spec(broker)
    candidate = str(value or "").strip().upper()
    if broker == "kiwoom_rest":
        candidate = candidate.replace("-", "").replace(" ", "")
    if not spec.account_pattern.fullmatch(candidate):
        raise ValueError(f"{spec.label} account number must be {spec.account_hint}")
    return candidate


def display_account_id(broker: str, account_id: str) -> str:
    normalized = normalize_account_id(broker, account_id)
    if broker == "kiwoom_rest":
        groups = [normalized[:4], normalized[4:8]] + ([normalized[8:]] if len(normalized) > 8 else [])
        return "-".join(groups)
    return normalized


def secret_name_for(broker: str, connection_id: str | uuid.UUID) -> str:
    """Key Vault secret name for a connection; one secret per connection."""

    return f"{broker_spec(broker).secret_prefix}{uuid.UUID(str(connection_id)).hex}"


def credential_fingerprint(values: Mapping[str, str]) -> str:
    """A short non-secret identifier for the submitted credential values.

    It lets the website show that a key was replaced without ever storing it.
    """

    if not values or any(not str(item).strip() for item in values.values()):
        raise ValueError("Every credential field is required")
    joined = "\x1f".join(f"{key}={values[key]}" for key in sorted(values))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def validate_credential_fields(broker: str, values: Mapping[str, str]) -> None:
    spec = broker_spec(broker)
    if set(values) != set(spec.credential_fields):
        raise ValueError(f"{spec.label} needs exactly: {', '.join(spec.credential_fields)}")
    for name, value in values.items():
        text = str(value or "")
        if not text.strip() or len(text) > 1024 or any(ch.isspace() for ch in text.strip()):
            raise ValueError(f"{name} is missing or malformed")


def validate_expiry(broker: str, expires_on: date | None, *, today: date) -> date | None:
    """Check the credential expiry the owner copied from the broker."""

    spec = broker_spec(broker)
    if expires_on is None:
        if broker == "ibkr_flex":
            raise ValueError("Enter the token expiry date shown by IBKR")
        return None
    if expires_on <= today:
        raise ValueError("The credential has already expired")
    if spec.credential_max_days is not None and expires_on > today + timedelta(days=spec.credential_max_days):
        raise ValueError(f"{spec.label} credentials cannot last longer than one year")
    return expires_on


def validate_ip_lock(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    address = ipaddress.ip_address(str(value).strip())
    if address.is_private or address.is_loopback or address.is_unspecified:
        raise ValueError("The IP lock must be the platform's public outbound address")
    return str(address)


def ip_lock_state(locked_to: str | None, platform_egress_ip: str | None) -> str:
    """Compare the broker's IP lock with the platform's fixed outbound address."""

    if not locked_to:
        return "not_locked"
    if not platform_egress_ip:
        return "unknown_platform_ip"
    return "locked" if locked_to == platform_egress_ip.strip() else "mismatch"


def connection_health(
    *,
    stored_status: str,
    expires_on: date | None,
    today: date,
    ip_lock: str = "locked",
) -> str:
    """The single health word the Broker connections page shows.

    ``stored_status`` is what the last check recorded; expiry and IP lock are
    re-evaluated on every read so a connection turns amber without a job.
    """

    if stored_status in {"suspended", "retired"}:
        return stored_status
    if expires_on is not None and expires_on <= today:
        return "expired"
    if stored_status == "failing" or ip_lock == "mismatch":
        return "failing"
    if stored_status == "pending_check":
        return "not_checked"
    if expires_on is not None and expires_on <= today + timedelta(days=RENEW_WARNING_DAYS):
        return "renew_soon"
    return "healthy"


HEALTH_LABELS = {
    "healthy": "Healthy",
    "renew_soon": "Renew soon",
    "expired": "Expired",
    "failing": "Check failed",
    "not_checked": "Not checked yet",
    "suspended": "Paused",
    "retired": "Retired",
}


@dataclass(frozen=True)
class ConnectionRecord:
    """The persistable part of a new connection; deliberately excludes secrets."""

    connection_id: str
    broker: str
    display_name: str
    environment: str
    key_vault_secret_name: str
    credential_fingerprint: str
    credential_expires_on: date | None
    ip_locked_to: str | None
    status: str = "pending_check"


def new_connection_record(
    *,
    broker: str,
    display_name: str,
    environment: str,
    credentials: Mapping[str, str],
    expires_on: date | None,
    ip_locked_to: str | None,
    today: date,
    connection_id: str | uuid.UUID | None = None,
) -> ConnectionRecord:
    spec = broker_spec(broker)
    name = " ".join(str(display_name or "").split())
    if not 1 <= len(name) <= 120:
        raise ValueError("Connection name must contain 1 to 120 characters")
    if environment not in spec.environments:
        raise ValueError(f"{spec.label} has no {environment} environment")
    validate_credential_fields(broker, credentials)
    identifier = uuid.UUID(str(connection_id)) if connection_id else uuid.uuid4()
    return ConnectionRecord(
        connection_id=str(identifier),
        broker=broker,
        display_name=name,
        environment=environment,
        key_vault_secret_name=secret_name_for(broker, identifier),
        credential_fingerprint=credential_fingerprint(credentials),
        credential_expires_on=validate_expiry(broker, expires_on, today=today),
        ip_locked_to=validate_ip_lock(ip_locked_to),
    )
