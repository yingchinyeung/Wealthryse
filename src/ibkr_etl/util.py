from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json_default(value: Any) -> str:
    """Normalize exact database scalar types without weakening JSON validation."""

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"Object of type {value.__class__.__name__} is not JSON serializable")


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_canonical_json_default,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def normalized_attributes(attributes: Mapping[str, str]) -> dict[str, str]:
    return {normalized_key(key): value.strip() for key, value in attributes.items()}


def pick(attributes: Mapping[str, str], *names: str, default: str = "") -> str:
    normalized = normalized_attributes(attributes)
    for name in names:
        value = normalized.get(normalized_key(name))
        if value not in (None, ""):
            return value
    return default


def parse_date_text(value: str) -> str | None:
    if not value:
        return None
    candidate = value.strip().split(";")[0].split(",")[0]
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(candidate, fmt).date().isoformat()
        except ValueError:
            pass
    if len(candidate) >= 10:
        try:
            return date.fromisoformat(candidate[:10]).isoformat()
        except ValueError:
            pass
    return None


def decimal_text(value: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    candidate = str(value).strip().replace(",", "")
    try:
        return format(Decimal(candidate), "f")
    except InvalidOperation:
        return None


def redact_secret(value: str, secret: str) -> str:
    return value.replace(secret, "***") if secret else value

