"""Source-only owner task metadata, not a Codex/model execution service.

Only a trusted server may construct requests, supply freshly database-resolved
authority, and independently verify protected audit receipts. No prompts,
outputs, reasoning, source values, credentials or executable instructions are
stored here. This module launches nothing and is not connected to the website.
The PostgreSQL migration grants no access. SQLite tests exercise transactional
behaviour, not PostgreSQL RLS or live service acceptance.

Owner revocation blocks all subsequent metadata transitions as well as delivery.
A future independent audit service must still record verified stopped outcomes
and quarantine such tasks for administrator reconciliation; this foundation
does not grant an operator access to the revoked owner's account or results.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import uuid

from .db import is_postgres, transaction


TASK_KIND = "performance_series_review"
AUDIT_NAMESPACE = "owner_codex_task_v1"
TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
STATES = frozenset({"queued", "running", "cancel_requested", "needs_reconciliation"}) | TERMINAL
SAFE_ERRORS = {
    "access_denied": "Current owner or account access is unavailable.",
    "invalid_request": "This task request is not supported.",
    "not_found": "This task or session is unavailable.",
    "idempotency_conflict": "This request identifier was already used for a different task.",
    "revision_conflict": "The task changed. Retrieve its current status before continuing.",
    "transition_denied": "This task cannot make the requested status change.",
    "lease_unavailable": "The task lease is unavailable. Independent reconciliation is required.",
    "audit_unverified": "Protected activity evidence has not been independently verified.",
    "worker_failed": "The task could not complete. No result was released.",
    "storage_unavailable": "Task status is temporarily unavailable. No change was confirmed.",
}
MESSAGES = {
    "queued": "The task is queued. No runtime has been started by this service.",
    "running": "The trusted worker has claimed the task.",
    "cancel_requested": "Cancellation was requested. The worker has not confirmed that it stopped.",
    "needs_reconciliation": "The worker lease expired. The task will not be automatically restarted.",
    "succeeded": "The task completed and its protected activity evidence was verified.",
    "failed": SAFE_ERRORS["worker_failed"],
    "cancelled": "The task is cancelled. No result will be released.",
}
EVENT_CODES = frozenset({"queued", "audit_intent_verified", "running", "heartbeat",
                        "cancel_requested", "needs_reconciliation", "succeeded",
                        "failed", "cancelled", "delivery_ready"})


class OwnerTaskError(ValueError):
    """Fixed public errors: never reflect a database/provider/model exception."""
    def __init__(self, code: str):
        if code not in SAFE_ERRORS:
            raise ValueError("Unsupported task error code")
        self.code = code
        super().__init__(SAFE_ERRORS[code])


def _uuid(value: str) -> str:
    if type(value) is not str:
        raise OwnerTaskError("invalid_request")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError):
        raise OwnerTaskError("invalid_request") from None
    return value


def _sha(value: str) -> str:
    if type(value) is not str or not re.fullmatch("[0-9a-f]{64}", value):
        raise OwnerTaskError("invalid_request")
    return value


def _ids(values: tuple[str, ...], maximum: int) -> tuple[str, ...]:
    if (type(values) is not tuple or not 0 < len(values) <= maximum
            or any(type(v) is not str or not re.fullmatch("[A-Za-z0-9][A-Za-z0-9_-]{0,79}", v) for v in values)
            or values != tuple(sorted(set(values)))):
        raise OwnerTaskError("invalid_request")
    return values


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode()).hexdigest()


@dataclass(frozen=True)
class OwnerRef:
    tenant_id: str
    object_id: str

    def __post_init__(self):
        _uuid(self.tenant_id)
        _uuid(self.object_id)

    @classmethod
    def from_database_user(cls, tenant_id: str, user: Mapping):
        # A fresh authority callback is still mandatory at every operation.
        if (not isinstance(user, Mapping) or user.get("role") != "owner"
                or user.get("authorization_source") != "database"):
            raise OwnerTaskError("access_denied")
        return cls(tenant_id, user.get("object_id"))


@dataclass(frozen=True)
class OwnerAuthority:
    owner: OwnerRef
    registered_subject_ids: tuple[str, ...]
    authorized_subject_ids: tuple[str, ...]
    authority_sha256: str
    role: str = "owner"
    authorization_source: str = "database"

    def __post_init__(self):
        if type(self.owner) is not OwnerRef or self.role != "owner" or self.authorization_source != "database":
            raise OwnerTaskError("access_denied")
        _ids(self.registered_subject_ids, 500)
        # Empty current account entitlement is a valid revocation, never an
        # implicit grant of the complete registered universe.
        if self.authorized_subject_ids:
            _ids(self.authorized_subject_ids, 500)
        elif type(self.authorized_subject_ids) is not tuple:
            raise OwnerTaskError("invalid_request")
        if not set(self.authorized_subject_ids).issubset(self.registered_subject_ids):
            raise OwnerTaskError("access_denied")
        _sha(self.authority_sha256)


@dataclass(frozen=True)
class ServerValidatedTaskRequest:
    """Typed server envelope; input digest is not a prompt or execution permit."""
    subject_ids: tuple[str, ...]
    input_sha256: str
    task_kind: str = TASK_KIND
    schema_version: int = 1

    def __post_init__(self):
        if self.task_kind != TASK_KIND or type(self.schema_version) is not int or self.schema_version != 1:
            raise OwnerTaskError("invalid_request")
        _ids(self.subject_ids, 50)
        _sha(self.input_sha256)

    def payload(self) -> dict:
        return {"schema_version": 1, "task_kind": self.task_kind,
                "subject_ids": list(self.subject_ids), "input_sha256": self.input_sha256}


@dataclass(frozen=True)
class WorkerAuthority:
    """Current service capability supplied by a separately authenticated server.

    This is not an owner identity or a browser-selected worker UUID. No worker
    login/grant or concrete authority resolver is installed by this foundation.
    """
    tenant_id: str
    worker_id: str
    service_principal_id: str
    enabled: bool = True
    capability: str = "owner_task_metadata"
    authorization_source: str = "database_service"

    def __post_init__(self):
        _uuid(self.tenant_id)
        _uuid(self.worker_id)
        _uuid(self.service_principal_id)
        if (self.enabled is not True or self.capability != "owner_task_metadata"
                or self.authorization_source != "database_service"):
            raise OwnerTaskError("access_denied")


class OwnerCodexTasks:
    """Trusted metadata service using the project's DB adapter conventions.

    ``authorize(owner)`` must resolve the CURRENT active database owner and exact
    registered/authorized subject set, not reuse request/session claims. A
    verifier must independently read protected audit bytes, prove the bound
    execution status (including process cessation for cancellation), and return
    the exact receipt contract below. These are server dependencies, never forms.
    Worker methods must not be exposed as owner HTTP actions. Future integration
    must use a separately restricted identity and verify its live permissions.
    """
    def __init__(self, connection, *, authorize: Callable, verify_receipt: Callable,
                 authorize_worker: Callable | None = None,
                 clock: Callable = lambda: datetime.now(timezone.utc)):
        if not callable(authorize) or not callable(verify_receipt) or not callable(clock):
            raise OwnerTaskError("invalid_request")
        if authorize_worker is not None and not callable(authorize_worker):
            raise OwnerTaskError("invalid_request")
        self.connection = connection
        self.authorize = authorize
        self.verify_receipt = verify_receipt
        self.authorize_worker = authorize_worker
        self.clock = clock

    def _now(self):
        value = self.clock()
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise OwnerTaskError("invalid_request")
        return value

    @contextmanager
    def _scope(self, owner: OwnerRef):
        if type(owner) is not OwnerRef:
            raise OwnerTaskError("access_denied")
        # Own an idle transaction; never commit a caller's unrelated work.
        if not is_postgres(self.connection) and self.connection.in_transaction:
            raise OwnerTaskError("transition_denied")
        raw = getattr(self.connection, "_connection", None)
        # PostgreSQL's project transaction adapter assumes implicit BEGIN.
        # Autocommit would split the task and its required event into separate
        # commits, so reject it before any SQL or authority callback is invoked.
        if raw is not None and (raw.autocommit is not False or raw.info.transaction_status != 0):
            raise OwnerTaskError("transition_denied")
        try:
            with transaction(self.connection):
                try:
                    authority = self.authorize(owner)
                except Exception:
                    raise OwnerTaskError("access_denied") from None
                if type(authority) is not OwnerAuthority or authority.owner != owner:
                    raise OwnerTaskError("access_denied")
                if is_postgres(self.connection):
                    scope = self.connection.execute("""SELECT app_tenant_id()::text AS tenant,
                        current_setting('app.tenant_id',true) AS configured_tenant""").fetchone()
                    if str(scope["tenant"]) != owner.tenant_id or scope["configured_tenant"] != owner.tenant_id:
                        raise OwnerTaskError("access_denied")
                    self.connection.execute("SELECT set_config('app.owner_object_id', ?, true)", (owner.object_id,))
                yield authority
        except OwnerTaskError:
            raise
        except Exception:
            raise OwnerTaskError("storage_unavailable") from None

    @staticmethod
    def _permitted(authority, subjects):
        if (not set(subjects).issubset(authority.registered_subject_ids)
                or not set(subjects).issubset(authority.authorized_subject_ids)):
            raise OwnerTaskError("access_denied")

    def _task(self, owner, task_id, authority, revision=None):
        _uuid(task_id)
        sql = "SELECT * FROM owner_codex_tasks WHERE tenant_id=? AND owner_object_id=? AND task_id=?"
        if is_postgres(self.connection):
            sql += " FOR UPDATE"
        row = self.connection.execute(sql, (owner.tenant_id, owner.object_id, task_id)).fetchone()
        if row is None:
            raise OwnerTaskError("not_found")
        row = dict(row)
        subjects = row["subject_ids"]
        row["subject_ids"] = json.loads(subjects) if isinstance(subjects, str) else subjects
        self._permitted(authority, row["subject_ids"])
        if revision is not None and (type(revision) is not int or revision < 1 or revision != row["revision"]):
            raise OwnerTaskError("revision_conflict")
        return row

    def _worker(self, owner, worker_id):
        _uuid(worker_id)
        try:
            authority = self.authorize_worker(owner.tenant_id, worker_id) if self.authorize_worker else None
        except Exception:
            raise OwnerTaskError("access_denied") from None
        if (type(authority) is not WorkerAuthority or authority.tenant_id != owner.tenant_id
                or authority.worker_id != worker_id or authority.service_principal_id == owner.object_id):
            raise OwnerTaskError("access_denied")

    def _event(self, row, code, now):
        if code not in EVENT_CODES:
            raise OwnerTaskError("invalid_request")
        self.connection.execute("""INSERT INTO owner_codex_task_events
            (tenant_id,owner_object_id,task_id,event_no,state,code,occurred_at)
            VALUES (?,?,?,?,?,?,?)""", (row["tenant_id"], row["owner_object_id"], row["task_id"],
            row["revision"], row["state"], code, now.isoformat()))

    def _change(self, row, *, code, now, **fields):
        allowed = {"state", "lease_token", "worker_id", "lease_expires_at", "intent_receipt_sha256",
                   "outcome_receipt_sha256", "result_sha256", "delivery_available", "error_code",
                   "cancel_requested_at"}
        if not fields or not fields.keys() <= allowed:
            raise OwnerTaskError("invalid_request")
        assignments = ",".join(name + "=?" for name in fields)
        values = tuple(fields.values()) + (now.isoformat(), row["tenant_id"], row["owner_object_id"],
                                          row["task_id"], row["revision"])
        cursor = self.connection.execute(f"""UPDATE owner_codex_tasks SET {assignments},
            updated_at=?,revision=revision+1 WHERE tenant_id=? AND owner_object_id=? AND task_id=? AND revision=?""", values)
        if cursor.rowcount != 1:
            raise OwnerTaskError("revision_conflict")
        row.update(fields)
        row["revision"] += 1
        row["updated_at"] = now.isoformat()
        self._event(row, code, now)
        return self._public(row)

    @staticmethod
    def _public(row):
        def stamp(value):
            return value.isoformat() if isinstance(value, datetime) else value
        delivered = bool(row["delivery_available"])
        state = row["state"]
        return {"task_id": str(row["task_id"]), "session_id": str(row["session_id"]),
                "state": state, "revision": row["revision"], "created_at": stamp(row["created_at"]),
                "updated_at": stamp(row["updated_at"]), "delivery_available": delivered,
                "cancellation_requested": row["cancel_requested_at"] is not None,
                "summary": MESSAGES[state] if state != "succeeded" or delivered else "The result is withheld pending protected evidence.",
                "error_code": row["error_code"], "result_sha256": row["result_sha256"] if delivered else None}

    def create_session(self, owner: OwnerRef, *, idempotency_key: str):
        _uuid(idempotency_key)
        with self._scope(owner):
            session_id = str(uuid.uuid4())
            now = self._now().isoformat()
            self.connection.execute("""INSERT INTO owner_codex_sessions
                (tenant_id,owner_object_id,session_id,idempotency_key,created_at)
                VALUES (?,?,?,?,?) ON CONFLICT(tenant_id,owner_object_id,idempotency_key) DO NOTHING""",
                (owner.tenant_id, owner.object_id, session_id, idempotency_key, now))
            row = self.connection.execute("""SELECT session_id,created_at FROM owner_codex_sessions
                WHERE tenant_id=? AND owner_object_id=? AND idempotency_key=?""",
                (owner.tenant_id, owner.object_id, idempotency_key)).fetchone()
            return {"session_id": str(row["session_id"]), "created_at": str(row["created_at"])}

    def submit(self, owner: OwnerRef, *, session_id: str, idempotency_key: str,
               request: ServerValidatedTaskRequest):
        _uuid(session_id)
        _uuid(idempotency_key)
        if type(request) is not ServerValidatedTaskRequest:
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            self._permitted(authority, request.subject_ids)
            if self.connection.execute("""SELECT 1 FROM owner_codex_sessions
                    WHERE tenant_id=? AND owner_object_id=? AND session_id=?""",
                    (owner.tenant_id, owner.object_id, session_id)).fetchone() is None:
                raise OwnerTaskError("not_found")
            digest = _hash({"session_id": session_id, "request": request.payload()})
            existing = self.connection.execute("""SELECT task_id,request_sha256 FROM owner_codex_tasks
                WHERE tenant_id=? AND owner_object_id=? AND idempotency_key=?""",
                (owner.tenant_id, owner.object_id, idempotency_key)).fetchone()
            if existing is not None:
                if existing["request_sha256"] != digest:
                    raise OwnerTaskError("idempotency_conflict")
                return self._public(self._task(owner, str(existing["task_id"]), authority))
            task_id, now = str(uuid.uuid4()), self._now()
            cursor = self.connection.execute("""INSERT INTO owner_codex_tasks
                (tenant_id,owner_object_id,task_id,session_id,idempotency_key,task_kind,
                 subject_ids,input_sha256,request_sha256,authority_sha256,state,revision,
                 delivery_available,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,'queued',1,?,?,?)
                ON CONFLICT(tenant_id,owner_object_id,idempotency_key) DO NOTHING""",
                (owner.tenant_id, owner.object_id, task_id, session_id, idempotency_key, TASK_KIND,
                 _json(list(request.subject_ids)), request.input_sha256, digest,
                 authority.authority_sha256, False, now.isoformat(), now.isoformat()))
            if cursor.rowcount != 1:
                # PostgreSQL concurrent submit may have won while we waited.
                existing = self.connection.execute("""SELECT task_id,request_sha256 FROM owner_codex_tasks
                    WHERE tenant_id=? AND owner_object_id=? AND idempotency_key=?""",
                    (owner.tenant_id, owner.object_id, idempotency_key)).fetchone()
                if existing is None or existing["request_sha256"] != digest:
                    raise OwnerTaskError("idempotency_conflict")
                return self._public(self._task(owner, str(existing["task_id"]), authority))
            row = self._task(owner, task_id, authority)
            self._event(row, "queued", now)
            return self._public(row)

    def get(self, owner: OwnerRef, *, task_id: str):
        with self._scope(owner) as authority:
            return self._public(self._task(owner, task_id, authority))

    def list_tasks(self, owner: OwnerRef, *, session_id: str, limit=30):
        _uuid(session_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            if self.connection.execute("""SELECT 1 FROM owner_codex_sessions
                    WHERE tenant_id=? AND owner_object_id=? AND session_id=?""",
                    (owner.tenant_id, owner.object_id, session_id)).fetchone() is None:
                raise OwnerTaskError("not_found")
            rows = self.connection.execute("""SELECT task_id FROM owner_codex_tasks
                WHERE tenant_id=? AND owner_object_id=? AND session_id=?
                ORDER BY created_at DESC,task_id DESC LIMIT ?""",
                (owner.tenant_id, owner.object_id, session_id, limit)).fetchall()
            result = []
            for row in rows:
                try:
                    result.append(self._public(self._task(owner, str(row["task_id"]), authority)))
                except OwnerTaskError as error:
                    if error.code != "access_denied":
                        raise
            return result

    def events(self, owner: OwnerRef, *, task_id: str, after_revision=0, limit=100):
        if (type(after_revision) is not int or after_revision < 0
                or type(limit) is not int or not 1 <= limit <= 100):
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            self._task(owner, task_id, authority)
            rows = self.connection.execute("""SELECT event_no,state,code,occurred_at
                FROM owner_codex_task_events WHERE tenant_id=? AND owner_object_id=? AND task_id=?
                AND event_no>? ORDER BY event_no LIMIT ?""",
                (owner.tenant_id, owner.object_id, task_id, after_revision, limit)).fetchall()
            return [{"revision": r["event_no"], "state": r["state"], "code": r["code"],
                     "occurred_at": str(r["occurred_at"])} for r in rows]

    @staticmethod
    def receipt_binding(row, phase, *, outcome=None, result_sha256=None):
        if phase not in {"intent", "outcome"} or (phase == "intent" and (outcome is not None or result_sha256 is not None)):
            raise OwnerTaskError("invalid_request")
        if phase == "outcome" and outcome not in TERMINAL:
            raise OwnerTaskError("invalid_request")
        if result_sha256 is not None:
            _sha(result_sha256)
        if (outcome == "succeeded") != (result_sha256 is not None):
            if phase == "outcome":
                raise OwnerTaskError("invalid_request")
        return {"schema_version": 1, "namespace": AUDIT_NAMESPACE, "phase": phase,
                "tenant_id": str(row["tenant_id"]), "owner_object_id": str(row["owner_object_id"]),
                "task_id": str(row["task_id"]), "request_sha256": row["request_sha256"],
                "subject_scope_sha256": _hash(row["subject_ids"]),
                "outcome": outcome, "result_sha256": result_sha256,
                "execution_status": (None if phase == "intent" else
                    "not_started" if outcome == "cancelled" and row["state"] == "cancelled"
                        and not row["delivery_available"] else
                    "stopped" if outcome == "cancelled" else "completed")}

    def _receipt(self, reference, binding):
        _sha(reference)
        expected = {"record_sha256": reference, "binding_sha256": _hash(binding),
                    "namespace": AUDIT_NAMESPACE, "protected_archive_verified": True,
                    "database_committed": True}
        try:
            receipt = self.verify_receipt(reference, binding.copy())
        except Exception:
            raise OwnerTaskError("audit_unverified") from None
        if (type(receipt) is not dict or receipt != expected
                or receipt.get("protected_archive_verified") is not True
                or receipt.get("database_committed") is not True):
            raise OwnerTaskError("audit_unverified")

    def attach_intent(self, owner, *, task_id, expected_revision, receipt_sha256, worker_id):
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row = self._task(owner, task_id, authority, expected_revision)
            if row["state"] != "queued" or row["intent_receipt_sha256"] is not None:
                raise OwnerTaskError("transition_denied")
            self._receipt(receipt_sha256, self.receipt_binding(row, "intent"))
            return self._change(row, code="audit_intent_verified", now=self._now(), intent_receipt_sha256=receipt_sha256)

    def claim(self, owner, *, task_id, expected_revision, worker_id, lease_seconds=120):
        _uuid(worker_id)
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 300:
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row = self._task(owner, task_id, authority, expected_revision)
            if row["state"] != "queued" or row["intent_receipt_sha256"] is None:
                raise OwnerTaskError("transition_denied")
            # Recheck the retained intent, not merely its database hash.
            self._receipt(row["intent_receipt_sha256"], self.receipt_binding(row, "intent"))
            now, token = self._now(), str(uuid.uuid4())
            task = self._change(row, code="running", now=now, state="running", worker_id=worker_id,
                                lease_token=token, lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat())
            # Internal worker return only. Never expose this lease in owner status.
            return {"task": task, "lease_token": token, "worker_id": worker_id}

    def _lease(self, row, token, worker_id, now):
        _uuid(token)
        _uuid(worker_id)
        expiry = row["lease_expires_at"]
        if isinstance(expiry, str):
            expiry = datetime.fromisoformat(expiry)
        if (row["lease_token"] is None or str(row["lease_token"]) != token
                or str(row["worker_id"]) != worker_id or expiry is None or expiry <= now):
            raise OwnerTaskError("lease_unavailable")

    def heartbeat(self, owner, *, task_id, expected_revision, lease_token, worker_id, lease_seconds=120):
        if type(lease_seconds) is not int or not 15 <= lease_seconds <= 300:
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row, now = self._task(owner, task_id, authority, expected_revision), self._now()
            if row["state"] != "running":
                raise OwnerTaskError("transition_denied")
            self._lease(row, lease_token, worker_id, now)
            return self._change(row, code="heartbeat", now=now,
                                lease_expires_at=(now + timedelta(seconds=lease_seconds)).isoformat())

    def cancel(self, owner, *, task_id, expected_revision):
        with self._scope(owner) as authority:
            row, now = self._task(owner, task_id, authority, expected_revision), self._now()
            if row["state"] == "queued":
                return self._change(row, code="cancelled", now=now, state="cancelled", cancel_requested_at=now.isoformat())
            if row["state"] == "running":
                return self._change(row, code="cancel_requested", now=now, state="cancel_requested", cancel_requested_at=now.isoformat())
            if row["state"] == "needs_reconciliation" and row["cancel_requested_at"] is None:
                # A lost lease does not discard a subsequent owner request.
                # Keep uncertainty until stopped-outcome evidence is verified.
                return self._change(row, code="cancel_requested", now=now, cancel_requested_at=now.isoformat())
            if row["state"] in TERMINAL | {"cancel_requested", "needs_reconciliation"}:
                return self._public(row)
            raise OwnerTaskError("transition_denied")

    def recover_expired(self, owner, *, task_id, expected_revision, worker_id):
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row, now = self._task(owner, task_id, authority, expected_revision), self._now()
            expiry = row["lease_expires_at"]
            if isinstance(expiry, str):
                expiry = datetime.fromisoformat(expiry)
            if row["state"] not in {"running", "cancel_requested"} or expiry is None or expiry > now:
                raise OwnerTaskError("transition_denied")
            return self._change(row, code="needs_reconciliation", now=now, state="needs_reconciliation",
                                lease_token=None, worker_id=None, lease_expires_at=None, delivery_available=False)

    def finish(self, owner, *, task_id, expected_revision, lease_token, worker_id,
               outcome, outcome_receipt_sha256, result_sha256=None):
        if outcome not in TERMINAL:
            raise OwnerTaskError("invalid_request")
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row, now = self._task(owner, task_id, authority, expected_revision), self._now()
            if row["state"] not in {"running", "cancel_requested"} or (outcome == "cancelled" and row["state"] != "cancel_requested"):
                raise OwnerTaskError("transition_denied")
            self._lease(row, lease_token, worker_id, now)
            return self._complete(row, now, outcome, row["intent_receipt_sha256"], outcome_receipt_sha256, result_sha256)

    def reconcile(self, owner, *, task_id, expected_revision, outcome,
                  intent_receipt_sha256, outcome_receipt_sha256, worker_id, result_sha256=None):
        """Evidence-only resolution. Never requeue/relaunch uncertain inference."""
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row = self._task(owner, task_id, authority, expected_revision)
            if row["state"] != "needs_reconciliation":
                raise OwnerTaskError("transition_denied")
            return self._complete(row, self._now(), outcome, intent_receipt_sha256, outcome_receipt_sha256, result_sha256)

    def seal_cancelled(self, owner, *, task_id, expected_revision,
                       intent_receipt_sha256, outcome_receipt_sha256, worker_id):
        """Anchor an atomically cancelled queued task before terminal delivery."""
        with self._scope(owner) as authority:
            self._worker(owner, worker_id)
            row = self._task(owner, task_id, authority, expected_revision)
            if row["state"] != "cancelled" or row["delivery_available"]:
                raise OwnerTaskError("transition_denied")
            return self._complete(row, self._now(), "cancelled", intent_receipt_sha256, outcome_receipt_sha256, None)

    def _complete(self, row, now, outcome, intent, receipt, result):
        if outcome not in TERMINAL or (row["intent_receipt_sha256"] is not None and row["intent_receipt_sha256"] != intent):
            raise OwnerTaskError("transition_denied")
        # Once owner cancellation wins the revision CAS, a later worker outcome
        # must not publish success, even after lease expiry/reconciliation. An
        # independently verified stopped/failed outcome can still close it.
        if outcome == "succeeded" and row["cancel_requested_at"] is not None:
            raise OwnerTaskError("transition_denied")
        self._receipt(intent, self.receipt_binding(row, "intent"))
        self._receipt(receipt, self.receipt_binding(row, "outcome", outcome=outcome, result_sha256=result))
        return self._change(row, code="delivery_ready" if row["state"] == "cancelled" else outcome,
                            now=now, state=outcome, intent_receipt_sha256=intent,
                            outcome_receipt_sha256=receipt, result_sha256=result, delivery_available=True,
                            error_code="worker_failed" if outcome == "failed" else None,
                            lease_token=None, worker_id=None, lease_expires_at=None)
