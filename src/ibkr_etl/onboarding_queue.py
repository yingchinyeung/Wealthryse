from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


QUEUE_ITEM_STATES = (
    "current",
    "queued",
    "completed",
    "deferred",
    "cancelled",
)


@dataclass(frozen=True)
class QueueAccount:
    source_account_id: str
    position: int
    state: str
    onboarding_case_status: str | None = None


def normalize_account_order(
    account_ids: Sequence[str], *, discovered_account_ids: Iterable[str]
) -> tuple[str, ...]:
    """Validate an ordered, duplicate-free subset of discovered Flex accounts."""

    discovered = {str(value).strip().upper() for value in discovered_account_ids}
    if not account_ids:
        raise ValueError("Select at least one discovered account")
    if len(account_ids) > 100:
        raise ValueError("A connection queue cannot contain more than 100 accounts")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in account_ids:
        account_id = str(raw or "").strip().upper()
        if account_id not in discovered:
            raise ValueError(f"Account {account_id or '(blank)'} was not discovered by this connection")
        if account_id in seen:
            raise ValueError(f"Duplicate account in onboarding order: {account_id}")
        seen.add(account_id)
        normalized.append(account_id)
    return tuple(normalized)


def initial_queue_items(account_ids: Sequence[str]) -> tuple[QueueAccount, ...]:
    """Start exactly one account and keep every later selection locked."""

    if not account_ids:
        raise ValueError("An onboarding queue requires at least one account")
    return tuple(
        QueueAccount(
            source_account_id=account_id,
            position=index,
            state="current" if index == 1 else "queued",
        )
        for index, account_id in enumerate(account_ids, start=1)
    )


def next_queue_state(items: Sequence[QueueAccount]) -> tuple[QueueAccount, ...]:
    """Advance only when the one current account is fully active."""

    current = [item for item in items if item.state == "current"]
    if len(current) != 1:
        raise ValueError("An open onboarding queue must have exactly one current account")
    if current[0].onboarding_case_status != "active":
        raise ValueError("The current account must be completely activated before the next account starts")
    next_waiting = min(
        (item for item in items if item.state == "queued"),
        key=lambda item: item.position,
        default=None,
    )
    result: list[QueueAccount] = []
    for item in items:
        if item.source_account_id == current[0].source_account_id:
            result.append(
                QueueAccount(item.source_account_id, item.position, "completed", "active")
            )
        elif next_waiting and item.source_account_id == next_waiting.source_account_id:
            result.append(
                QueueAccount(item.source_account_id, item.position, "current", item.onboarding_case_status)
            )
        else:
            result.append(item)
    return tuple(result)


def classify_connection_accounts(
    discovered_accounts: Sequence[Mapping[str, Any]],
    queue_items: Sequence[Mapping[str, Any]],
    active_source_accounts: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Return stable UI groups while retaining every unselected account for later."""

    queue_by_account = {
        str(item["source_account_id"]).upper(): item for item in queue_items
    }
    active = {str(value).upper() for value in active_source_accounts}
    classified: list[dict[str, Any]] = []
    for source in discovered_accounts:
        account_id = str(source["source_account_id"]).upper()
        item = queue_by_account.get(account_id)
        if account_id in active or (item and item.get("item_status") == "completed"):
            group = "active"
        elif item and item.get("item_status") == "current":
            group = "onboarding_now"
        elif item and item.get("item_status") == "queued":
            group = "queued"
        elif item and item.get("item_status") == "deferred":
            group = "available_later"
        else:
            group = "available_later"
        classified.append({**dict(source), "onboarding_group": group})
    return classified
