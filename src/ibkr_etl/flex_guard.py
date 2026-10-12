from __future__ import annotations

import hashlib
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator

from .config import Settings
from .db import connect, initialize, is_postgres
from .flex_client import FlexError, is_lockout_error
from .util import utc_now_iso


LOGGER = logging.getLogger(__name__)


class FlexCircuitOpen(RuntimeError):
    pass


def token_fingerprint(token: str) -> str:
    """Return a non-reversible identifier suitable for logs and database keys."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _row(connection, fingerprint: str, query_id: str):
    return connection.execute(
        """
        SELECT last_send_at, blocked_until, requires_manual_reset, blocked_reason
        FROM flex_request_state
        WHERE token_fingerprint = ? AND query_id = ?
        """,
        (fingerprint, query_id),
    ).fetchone()


def circuit_status(settings: Settings) -> dict[str, object]:
    settings.require_flex_credentials()
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    fingerprint = token_fingerprint(settings.flex_token)
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        row = connection.execute(
            """
            SELECT token_fingerprint, query_id, last_send_at, last_error_at,
                   last_error_phase, last_error_code, consecutive_failures, blocked_until,
                   blocked_reason, requires_manual_reset, updated_at
            FROM flex_request_state
            WHERE token_fingerprint = ? AND query_id = ?
            """,
            (fingerprint, settings.flex_query_id),
        ).fetchone()
        return dict(row) if row else {
            "token_fingerprint": fingerprint,
            "query_id": settings.flex_query_id,
            "status": "clear",
        }
    finally:
        connection.close()


def reset_circuit(settings: Settings) -> dict[str, object]:
    settings.require_flex_credentials()
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    fingerprint = token_fingerprint(settings.flex_token)
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    try:
        connection.execute(
            """
            DELETE FROM flex_request_state
            WHERE token_fingerprint = ? AND query_id = ?
            """,
            (fingerprint, settings.flex_query_id),
        )
        connection.commit()
    finally:
        connection.close()
    return {"status": "reset", "token_fingerprint": fingerprint, "query_id": settings.flex_query_id}


@contextmanager
def guarded_flex_request(settings: Settings) -> Iterator[dict[str, str]]:
    """Serialize Flex generation per token and persist a fail-closed circuit."""

    settings.require_flex_credentials()
    initialize(
        settings.database_path,
        settings.database_url,
        settings.tenant_id,
        settings.tenant_name,
    )
    fingerprint = token_fingerprint(settings.flex_token)
    connection = connect(settings.database_path, settings.database_url, settings.tenant_id)
    advisory_lock = False
    try:
        if is_postgres(connection):
            lock_key = f"ibkr-flex-token:{fingerprint}"
            advisory_lock = bool(
                connection.execute(
                    "SELECT pg_try_advisory_lock(hashtext(?))", (lock_key,)
                ).fetchone()[0]
            )
            if not advisory_lock:
                raise FlexCircuitOpen(
                    f"Another Flex operation is active for token {fingerprint}; no request was sent"
                )

        row = _row(connection, fingerprint, settings.flex_query_id)
        now = datetime.now(timezone.utc)
        if row:
            if bool(row[2]):
                raise FlexCircuitOpen(
                    f"Flex circuit requires manual reset for token {fingerprint}: {row[3] or 'lockout detected'}"
                )
            blocked_until = _parse_utc(row[1])
            if blocked_until and blocked_until > now:
                raise FlexCircuitOpen(
                    f"Flex circuit is cooling down for token {fingerprint} until {blocked_until.isoformat()}"
                )
            last_send_at = _parse_utc(row[0])
            earliest = (
                last_send_at + timedelta(seconds=settings.flex_min_send_interval_seconds)
                if last_send_at
                else None
            )
            if earliest and earliest > now:
                raise FlexCircuitOpen(
                    f"Flex token {fingerprint} was used too recently; no request was sent"
                )

        now_text = utc_now_iso()
        conflict_clause = (
            "ON CONFLICT (tenant_id, token_fingerprint, query_id) DO UPDATE SET"
            if is_postgres(connection)
            else "ON CONFLICT (token_fingerprint, query_id) DO UPDATE SET"
        )
        connection.execute(
            f"""
            INSERT INTO flex_request_state(
                token_fingerprint, query_id, last_send_at, consecutive_failures,
                requires_manual_reset, updated_at
            ) VALUES (?, ?, ?, 0, false, ?)
            {conflict_clause}
                last_send_at = excluded.last_send_at,
                updated_at = excluded.updated_at
            """,
            (fingerprint, settings.flex_query_id, now_text, now_text),
        )
        connection.commit()
        LOGGER.info(
            "IBKR Flex generation authorized phase=send_request code=none attempt=1 token=%s",
            fingerprint,
        )
        try:
            yield {"token_fingerprint": fingerprint, "attempt": "1"}
        except FlexError as exc:
            lockout = exc.lockout or is_lockout_error(exc.code, str(exc))
            blocked_until = None if lockout else (
                datetime.now(timezone.utc)
                + timedelta(seconds=settings.flex_transient_cooldown_seconds)
            ).isoformat(timespec="seconds")
            reason = "lockout-or-credentials" if lockout else "transient-failure"
            connection.execute(
                """
                UPDATE flex_request_state
                SET last_error_at = ?, last_error_phase = ?, last_error_code = ?,
                    consecutive_failures = consecutive_failures + 1,
                    blocked_until = ?, blocked_reason = ?,
                    requires_manual_reset = ?, updated_at = ?
                WHERE token_fingerprint = ? AND query_id = ?
                """,
                (
                    utc_now_iso(),
                    exc.phase,
                    exc.code,
                    blocked_until,
                    reason,
                    lockout,
                    utc_now_iso(),
                    fingerprint,
                    settings.flex_query_id,
                ),
            )
            connection.commit()
            LOGGER.error(
                "IBKR Flex failure phase=%s code=%s attempt=1 token=%s circuit=%s",
                exc.phase,
                exc.code or "none",
                fingerprint,
                "manual-reset" if lockout else "cooldown",
            )
            raise
        else:
            connection.execute(
                """
                UPDATE flex_request_state
                SET last_error_at = NULL, last_error_phase = NULL, last_error_code = NULL,
                    consecutive_failures = 0, blocked_until = NULL,
                    blocked_reason = NULL, requires_manual_reset = false,
                    updated_at = ?
                WHERE token_fingerprint = ? AND query_id = ?
                """,
                (utc_now_iso(), fingerprint, settings.flex_query_id),
            )
            connection.commit()
    finally:
        if advisory_lock:
            # A failed SQL statement leaves PostgreSQL in an aborted transaction.
            # Roll it back before releasing the session-level advisory lock.
            connection.rollback()
            connection.execute(
                "SELECT pg_advisory_unlock(hashtext(?))",
                (f"ibkr-flex-token:{fingerprint}",),
            )
            connection.commit()
        connection.close()
