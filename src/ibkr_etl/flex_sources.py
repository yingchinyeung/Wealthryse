from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from .parser import ParsedReport


QUERY_ID_PATTERN = re.compile(r"^[0-9]{1,20}$")
SECRET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,127}$")
SOURCE_ACCOUNT_PATTERN = re.compile(r"^[A-Z0-9-]{3,32}$")


@dataclass(frozen=True)
class FlexSourceDefinition:
    flex_source_id: str
    credential_id: str
    query_id: str
    display_name: str

    def __post_init__(self) -> None:
        if not QUERY_ID_PATTERN.fullmatch(self.query_id):
            raise ValueError("IBKR Flex Query ID must contain digits only")
        if not self.display_name.strip() or len(self.display_name.strip()) > 120:
            raise ValueError("Flex source display name must contain 1 to 120 characters")


@dataclass(frozen=True)
class SourceAccountBinding:
    subject_id: str
    flex_source_id: str
    source_account_id: str
    status: str = "active"

    def __post_init__(self) -> None:
        if not SOURCE_ACCOUNT_PATTERN.fullmatch(self.source_account_id):
            raise ValueError("Invalid IBKR source account ID")
        if self.status not in {"pending", "active", "paused", "retired"}:
            raise ValueError("Invalid Flex source binding status")


@dataclass(frozen=True)
class FlexSourceExecution:
    source: FlexSourceDefinition
    bindings: tuple[SourceAccountBinding, ...]


def token_fingerprint(token: str) -> str:
    """Return a non-secret identifier. The token must never be persisted or serialized."""

    if not token.strip():
        raise ValueError("Flex token is required")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def validate_secret_reference(secret_name: str) -> str:
    candidate = secret_name.strip()
    if not SECRET_NAME_PATTERN.fullmatch(candidate):
        raise ValueError("Key Vault secret name is invalid")
    return candidate


def credential_record(
    *, display_name: str, key_vault_secret_name: str, token: str
) -> dict[str, str]:
    """Create the persistable credential metadata; deliberately excludes the token."""

    return {
        "display_name": display_name.strip(),
        "key_vault_secret_name": validate_secret_reference(key_vault_secret_name),
        "token_fingerprint": token_fingerprint(token),
        "status": "pending_validation",
    }


def source_execution_plan(
    sources: Sequence[FlexSourceDefinition],
    bindings: Iterable[SourceAccountBinding],
) -> tuple[FlexSourceExecution, ...]:
    """Group account work so each Flex query source is requested at most once."""

    source_by_id = {source.flex_source_id: source for source in sources}
    if len(source_by_id) != len(sources):
        raise ValueError("Duplicate Flex source definitions are not allowed")
    grouped: dict[str, list[SourceAccountBinding]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for binding in bindings:
        if binding.status != "active":
            continue
        if binding.flex_source_id not in source_by_id:
            raise ValueError("Binding references an unknown Flex source")
        key = (binding.flex_source_id, binding.source_account_id)
        if key in seen:
            raise ValueError("Duplicate source-account binding")
        seen.add(key)
        grouped[binding.flex_source_id].append(binding)
    return tuple(
        FlexSourceExecution(
            source=source_by_id[source_id],
            bindings=tuple(sorted(grouped[source_id], key=lambda item: item.source_account_id)),
        )
        for source_id in sorted(grouped)
    )


def partition_report(
    report: ParsedReport,
) -> dict[str, tuple[object, ...]]:
    """Partition one downloaded multi-account statement without another IBKR request."""

    partitions: dict[str, list[object]] = defaultdict(list)
    for row in report.rows:
        account_id = row.account_id.strip().upper()
        if account_id == "UNKNOWN" or not SOURCE_ACCOUNT_PATTERN.fullmatch(account_id):
            continue
        partitions[account_id].append(row)
    return {account_id: tuple(rows) for account_id, rows in sorted(partitions.items())}


def discovered_account_summaries(report: ParsedReport) -> list[dict[str, object]]:
    """Return non-secret account coverage for the onboarding selection step."""

    sections: dict[str, Counter[str]] = defaultdict(Counter)
    currencies: dict[str, Counter[str]] = defaultdict(Counter)
    statement_metadata: dict[str, dict[str, str]] = {}
    for row in report.rows:
        account_id = row.account_id.strip().upper()
        if account_id == "UNKNOWN" or not SOURCE_ACCOUNT_PATTERN.fullmatch(account_id):
            continue
        sections[account_id][row.section] += 1
        if row.currency:
            currencies[account_id][row.currency.upper()] += 1
        if row.record_type == "FlexStatement":
            statement_metadata[account_id] = row.attributes
    return [
        {
            "source_account_id": account_id,
            "row_count": sum(section_counts.values()),
            "section_counts": dict(sorted(section_counts.items())),
            "observed_currencies": [item[0] for item in currencies[account_id].most_common()],
            "base_currency": (
                statement_metadata.get(account_id, {}).get("currency")
                or statement_metadata.get(account_id, {}).get("baseCurrency")
                or (currencies[account_id].most_common(1)[0][0] if currencies[account_id] else None)
            ),
            "account_type": (
                statement_metadata.get(account_id, {}).get("accountType")
                or statement_metadata.get(account_id, {}).get("type")
                or None
            ),
            "account_name": (
                statement_metadata.get(account_id, {}).get("accountName")
                or statement_metadata.get(account_id, {}).get("name")
                or None
            ),
        }
        for account_id, section_counts in sorted(sections.items())
    ]


def circuit_keys_by_credential(
    sources: Iterable[FlexSourceDefinition],
) -> Mapping[str, tuple[str, ...]]:
    """Expose shared circuit membership; every query using one token shares one key."""

    grouped: dict[str, list[str]] = defaultdict(list)
    for source in sources:
        grouped[source.credential_id].append(source.flex_source_id)
    return {
        credential_id: tuple(sorted(source_ids))
        for credential_id, source_ids in sorted(grouped.items())
    }
