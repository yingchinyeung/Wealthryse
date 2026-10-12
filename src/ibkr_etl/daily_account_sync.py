from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath
from zoneinfo import ZoneInfo

from .onboarding import AccountSyncTarget, parse_account_sync_targets


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HONG_KONG = ZoneInfo("Asia/Hong_Kong")


@dataclass(frozen=True)
class ProductionExportManifest:
    generated_at: datetime
    sqlite_file: str
    sqlite_sha256: str
    tenant_id: str
    row_counts: dict[str, int]


__all__ = [
    "AccountSyncTarget",
    "ProductionExportManifest",
    "parse_account_sync_targets",
    "validate_production_export_manifest",
]


def validate_production_export_manifest(
    payload: bytes,
    *,
    expected_tenant_id: str,
    now: datetime | None = None,
    maximum_age: timedelta = timedelta(hours=12),
    require_current_hkt_date: bool = True,
) -> ProductionExportManifest:
    """Fail closed unless the daily production export is current and well formed."""
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("Production export manifest is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or value.get("format_version") != 1:
        raise RuntimeError("Unsupported production export manifest format")
    if value.get("tenant_id") != expected_tenant_id:
        raise RuntimeError("Production export manifest tenant does not match")

    sqlite_file = str(value.get("sqlite_file", ""))
    if (
        not sqlite_file
        or PurePosixPath(sqlite_file).name != sqlite_file
        or not sqlite_file.lower().endswith(".sqlite")
    ):
        raise RuntimeError("Production export manifest contains an unsafe SQLite name")
    sqlite_sha256 = str(value.get("sqlite_sha256", "")).lower()
    if not SHA256_PATTERN.fullmatch(sqlite_sha256):
        raise RuntimeError("Production export manifest contains an invalid SHA-256")

    try:
        generated_at = datetime.fromisoformat(str(value["generated_at"]))
    except (KeyError, ValueError) as error:
        raise RuntimeError("Production export manifest has an invalid generated_at") from error
    if generated_at.tzinfo is None:
        raise RuntimeError("Production export generated_at must include a time zone")
    current = (now or datetime.now(HONG_KONG)).astimezone(HONG_KONG)
    generated_hkt = generated_at.astimezone(HONG_KONG)
    age = current - generated_hkt
    if age < timedelta(minutes=-10) or age > maximum_age:
        raise RuntimeError("Production export manifest is stale or future-dated")
    if require_current_hkt_date and generated_hkt.date() != current.date():
        raise RuntimeError("Production export was not generated on the current HKT date")

    raw_counts = value.get("row_counts")
    if not isinstance(raw_counts, dict):
        raise RuntimeError("Production export manifest row counts are missing")
    row_counts: dict[str, int] = {}
    for name, count in raw_counts.items():
        if not isinstance(name, str) or not isinstance(count, int) or count < 0:
            raise RuntimeError("Production export manifest row counts are invalid")
        row_counts[name] = count
    for required in ("accounts", "nav_snapshots", "position_snapshots"):
        if row_counts.get(required, 0) <= 0:
            raise RuntimeError(f"Production export manifest has no {required}")

    return ProductionExportManifest(
        generated_at=generated_at,
        sqlite_file=sqlite_file,
        sqlite_sha256=sqlite_sha256,
        tenant_id=expected_tenant_id,
        row_counts=row_counts,
    )
