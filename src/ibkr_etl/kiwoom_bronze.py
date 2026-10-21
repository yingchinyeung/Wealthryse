"""Kiwoom's raw inquiry answers in bronze.kiwoom_responses (Medallion; held migration 082).

Off unless KIWOOM_BRONZE=true. When on, the collector opens one ingestion_runs row per Kiwoom account
it calls (run_type 'kiwoom_rest_bronze'), and every page of every inquiry for that account, refusals
included, is stored as one row: the body exactly as received, its sha256, the request's query fields
and account number (request_key), and the two paging headers. Token and revoke calls never reach this
module (KiwoomClient hands over inquiry pages only), and request_key refuses any key, secret or token.

The same request asked twice in one run keeps its first answer (counted as "repeated").

Best effort, like raw_capture: it writes through its own database connection, so the collector's
transactions never wait on it or roll it back. The first failed write logs one warning, counts it and
switches capture off for the rest of the process; the daily run carries on. Nothing here logs a body,
a request_key or an account number: only counts.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .kiwoom_client import INQUIRY_APIS, Answer

LOGGER = logging.getLogger(__name__)

ENABLED_ENV = "KIWOOM_BRONZE"
RUN_TYPE = "kiwoom_rest_bronze"
TABLE = "bronze.kiwoom_responses"
# Field names that may carry a credential. A request carrying one is never stored.
_SECRET_FIELD = re.compile(r"(key|secret|token|auth|password|credential)", re.I)
# Kiwoom's paging field in a request body is a query field, not a credential.
_ALLOWED_FIELDS = frozenset({"next_key"})
_API_ID = re.compile(r"^(k[at][0-9]{5}|ust[0-9]{5}|usa[0-9]{5})$")


def enabled(environ: Mapping[str, str] | None = None) -> bool:
    environ = os.environ if environ is None else environ
    return str(environ.get(ENABLED_ENV, "")).strip().lower() == "true"


def request_key(account: str, request: Mapping[str, object]) -> dict[str, str]:
    """The account number and the request's query fields; refuses anything that looks like a credential."""

    key: dict[str, str] = {"account": str(account)}
    for name, value in request.items():
        name = str(name)
        if name == "account" or (_SECRET_FIELD.search(name) and name.lower() not in _ALLOWED_FIELDS):
            raise ValueError("a Kiwoom request field cannot go into bronze")
        key[name] = "" if value is None else str(value)
    return key


class KiwoomBronze:
    """Writes Kiwoom answers to bronze for one collector process."""

    def __init__(self, connect: Callable[[], Any], tenant_id: str, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
        self._connect = connect
        self._connection: Any = None
        self.tenant_id = tenant_id
        self._clock = clock
        self.enabled = True
        self.stats = {"runs": 0, "written": 0, "repeated": 0, "failed": 0, "skipped": 0}

    @classmethod
    def from_environment(cls, connect: Callable[[], Any], tenant_id: str,
                         environ: Mapping[str, str] | None = None) -> "KiwoomBronze | None":
        return cls(connect, tenant_id) if enabled(environ) else None

    def for_target(self, target: Any) -> "TargetCapture | None":
        """Opens the account's ingestion_runs row; None (capture skipped) once bronze is off."""

        if not self.enabled:
            return None
        try:
            connection = self._open()
            now = self._clock().isoformat()
            run_id = connection.execute(
                "INSERT INTO ingestion_runs (run_type, requested_from, requested_to, started_at, status, row_count) "
                "VALUES (?, ?, ?, ?, 'running', 0) RETURNING run_id",
                (RUN_TYPE, now[:10], now[:10], now),
            ).fetchone()["run_id"]
            connection.commit()
        except Exception as error:  # noqa: BLE001 - bronze never fails the daily run
            self._failed(error)
            return None
        self.stats["runs"] += 1
        return TargetCapture(self, int(run_id), str(target.connection_id), str(target.broker_account_id))

    def summary(self) -> dict[str, int | bool]:
        return {"enabled": self.enabled, **self.stats}

    def close(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001
                pass
            self._connection = None

    def _open(self) -> Any:
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def _failed(self, error: BaseException) -> None:
        self.stats["failed"] += 1
        if self._connection is not None:
            try:
                self._connection.rollback()
            except Exception:  # noqa: BLE001
                pass
        if self.enabled:
            # The error's type only: its text could quote a row.
            LOGGER.warning("Kiwoom bronze capture switched off for this run after %s", type(error).__name__)
        self.enabled = False


class TargetCapture:
    """The capture callback for one account; ``finish`` closes its ingestion_runs row."""

    def __init__(self, bronze: KiwoomBronze, run_id: int, connection_id: str, account: str) -> None:
        self._bronze = bronze
        self.run_id = run_id
        self._connection_id = connection_id
        self._account = account
        self._digests: list[str] = []

    def __call__(self, answer: Answer) -> None:
        bronze = self._bronze
        if not bronze.enabled:
            bronze.stats["skipped"] += 1
            return
        try:
            if answer.api_id not in INQUIRY_APIS or not _API_ID.match(answer.api_id):
                raise ValueError("only Kiwoom inquiry answers go into bronze")
            import json

            key = json.dumps(request_key(self._account, answer.request), sort_keys=True, ensure_ascii=False)
            digest = hashlib.sha256(answer.body).hexdigest()
            connection = bronze._open()
            # The same request asked twice in one run keeps its first answer (the page key is unique per run).
            inserted = connection.execute(
                f"INSERT INTO {TABLE} (run_id, tenant_id, connection_id, api_id, request_key, page_no, cont_yn, "
                "next_key, requested_at, received_at, http_status, return_code, body, body_sha256) "
                "VALUES (?, ?, ?, ?, CAST(? AS JSONB), ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT ON CONSTRAINT kiwoom_responses_page DO NOTHING",
                (self.run_id, bronze.tenant_id, self._connection_id, answer.api_id, key, answer.page_no,
                 answer.cont_yn, (answer.next_key or None) and answer.next_key[:200], answer.requested_at,
                 answer.received_at, answer.http_status, answer.return_code, answer.body, digest),
            )
            connection.commit()
        except Exception as error:  # noqa: BLE001 - bronze never fails the daily run
            bronze._failed(error)
            return
        if getattr(inserted, "rowcount", 1) == 0:
            bronze.stats["repeated"] += 1
            return
        self._digests.append(digest)
        bronze.stats["written"] += 1

    def finish(self, ok: bool) -> None:
        """Marks the run success or failed, with the page count and the sha256 over the pages' sha256s."""

        bronze = self._bronze
        try:
            combined = hashlib.sha256("\n".join(self._digests).encode("ascii")).hexdigest()
            connection = bronze._open()
            connection.execute(
                "UPDATE ingestion_runs SET status = ?, completed_at = ?, row_count = ?, raw_sha256 = ? "
                "WHERE run_id = ? AND run_type = ?",
                ("success" if ok and bronze.enabled else "failed", bronze._clock().isoformat(),
                 len(self._digests), combined, self.run_id, RUN_TYPE),
            )
            connection.commit()
        except Exception as error:  # noqa: BLE001
            bronze._failed(error)
