"""Local transactional foundation tests, NOT live PostgreSQL RLS acceptance."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
import uuid

from ibkr_etl.owner_codex_tasks import (
    AUDIT_NAMESPACE, OwnerAuthority, OwnerCodexTasks, OwnerRef, OwnerTaskError,
    ServerValidatedTaskRequest, WorkerAuthority, _hash,
)


TENANT = "00000000-0000-0000-0000-000000000001"
OWNER = OwnerRef(TENANT, "00000000-0000-0000-0000-000000000002")
OTHER = OwnerRef(TENANT, "00000000-0000-0000-0000-000000000003")
OTHER_TENANT = OwnerRef("00000000-0000-0000-0000-000000000004", OWNER.object_id)
WORKER = "00000000-0000-0000-0000-000000000005"
SERVICE_PRINCIPAL = "00000000-0000-0000-0000-000000000006"
NOW = datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)
H = "a" * 64
REQUEST = ServerValidatedTaskRequest(("U16088571",), H)

# Mirrors the columns/foreign keys used by the adapter and realistic transaction
# constraints. PostgreSQL-only forced RLS/deferred triggers require isolated live
# acceptance; these SQLite fixtures deliberately make no such claim.
SQLITE_SCHEMA = """
CREATE TABLE owner_codex_sessions (
 tenant_id TEXT NOT NULL,owner_object_id TEXT NOT NULL,session_id TEXT NOT NULL,
 idempotency_key TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,owner_object_id,session_id),
 UNIQUE(tenant_id,owner_object_id,idempotency_key));
CREATE TABLE owner_codex_tasks (
 tenant_id TEXT NOT NULL,owner_object_id TEXT NOT NULL,task_id TEXT NOT NULL,
 session_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,task_kind TEXT NOT NULL,
 subject_ids TEXT NOT NULL,input_sha256 TEXT NOT NULL,request_sha256 TEXT NOT NULL,
 authority_sha256 TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,
 worker_id TEXT,lease_token TEXT,lease_expires_at TEXT,cancel_requested_at TEXT,intent_receipt_sha256 TEXT,
 outcome_receipt_sha256 TEXT,result_sha256 TEXT,error_code TEXT,
 delivery_available INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,owner_object_id,task_id),
 UNIQUE(tenant_id,owner_object_id,idempotency_key),
 FOREIGN KEY(tenant_id,owner_object_id,session_id) REFERENCES owner_codex_sessions,
 CHECK(state IN ('queued','running','cancel_requested','needs_reconciliation','succeeded','failed','cancelled')),
 CHECK(revision>=1),
 CHECK((state IN ('running','cancel_requested') AND lease_token IS NOT NULL AND
   worker_id IS NOT NULL AND lease_expires_at IS NOT NULL AND intent_receipt_sha256 IS NOT NULL)
   OR (state NOT IN ('running','cancel_requested') AND lease_token IS NULL AND
   worker_id IS NULL AND lease_expires_at IS NULL)),
 CHECK(NOT delivery_available OR (state IN ('succeeded','failed','cancelled') AND
   intent_receipt_sha256 IS NOT NULL AND outcome_receipt_sha256 IS NOT NULL)),
 CHECK((state='succeeded' AND result_sha256 IS NOT NULL AND delivery_available)
   OR (state<>'succeeded' AND result_sha256 IS NULL)),
 CHECK((state='failed' AND error_code IS NOT NULL AND error_code='worker_failed') OR (state<>'failed' AND error_code IS NULL)),
 CHECK(state<>'cancel_requested' OR cancel_requested_at IS NOT NULL),
 CHECK(state<>'succeeded' OR cancel_requested_at IS NULL));
CREATE TABLE owner_codex_task_events (
 tenant_id TEXT NOT NULL,owner_object_id TEXT NOT NULL,task_id TEXT NOT NULL,
 event_no INTEGER NOT NULL,state TEXT NOT NULL,code TEXT NOT NULL,occurred_at TEXT NOT NULL,
 PRIMARY KEY(tenant_id,owner_object_id,task_id,event_no),
 FOREIGN KEY(tenant_id,owner_object_id,task_id) REFERENCES owner_codex_tasks);
CREATE TRIGGER immutable_sessions BEFORE UPDATE ON owner_codex_sessions
 BEGIN SELECT RAISE(ABORT,'immutable'); END;
CREATE TRIGGER immutable_events_update BEFORE UPDATE ON owner_codex_task_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER immutable_events_delete BEFORE DELETE ON owner_codex_task_events
 BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""


class LocalAuthority:
    def __init__(self):
        self.active = True
        self.authorized = ("U16088571", "U16088606")
        self.calls = 0

    def __call__(self, owner):
        self.calls += 1
        if not self.active:
            return None
        return OwnerAuthority(owner, ("U16088571", "U16088606"), self.authorized, H)


class LocalProtectedReceiptVerifier:
    """Synthetic exact readback: no Azure storage or existing audit reuse."""
    def __init__(self):
        self.records = {}
        self.available = True
        self.calls = []

    def add(self, binding):
        encoded = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
        reference = hashlib.sha256(b"synthetic-owner-task-receipt:" + encoded).hexdigest()
        self.records[reference] = json.loads(encoded)
        return reference

    def __call__(self, reference, binding):
        self.calls.append((reference, binding.copy()))
        if not self.available or self.records.get(reference) != binding:
            raise RuntimeError("secret provider error must not reach a user")
        return {"record_sha256": reference, "binding_sha256": _hash(binding),
                "namespace": AUDIT_NAMESPACE, "protected_archive_verified": True,
                "database_committed": True}


def local_worker_authority(tenant_id, worker_id):
    return WorkerAuthority(tenant_id, worker_id, SERVICE_PRINCIPAL)


class OwnerCodexTaskTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SQLITE_SCHEMA)
        self.authority = LocalAuthority()
        self.archive = LocalProtectedReceiptVerifier()
        self.now = NOW
        self.service = OwnerCodexTasks(self.connection, authorize=self.authority,
            verify_receipt=self.archive, authorize_worker=local_worker_authority, clock=lambda: self.now)
        self.session = self.service.create_session(OWNER, idempotency_key=str(uuid.uuid4()))["session_id"]

    def tearDown(self):
        self.connection.close()

    def submit(self, request=REQUEST, key=None, session=None, owner=OWNER):
        return self.service.submit(owner, session_id=session or self.session,
            idempotency_key=key or str(uuid.uuid4()), request=request)

    def row(self, task):
        row = dict(self.connection.execute("SELECT * FROM owner_codex_tasks WHERE task_id=?", (task["task_id"],)).fetchone())
        row["subject_ids"] = json.loads(row["subject_ids"])
        return row

    def intent(self, task):
        receipt = self.archive.add(self.service.receipt_binding(self.row(task), "intent"))
        task = self.service.attach_intent(OWNER, task_id=task["task_id"], expected_revision=task["revision"], receipt_sha256=receipt, worker_id=WORKER)
        return task, receipt

    def claimed(self):
        task, receipt = self.intent(self.submit())
        lease = self.service.claim(OWNER, task_id=task["task_id"], expected_revision=task["revision"], worker_id=WORKER)
        return lease["task"], receipt, lease

    def error(self, code, function, **arguments):
        with self.assertRaises(OwnerTaskError) as context:
            function(**arguments)
        self.assertEqual(context.exception.code, code)
        return str(context.exception)

    def test_known_database_owner_identity_only(self):
        user = {"role": "owner", "authorization_source": "database", "object_id": OWNER.object_id}
        self.assertEqual(OwnerRef.from_database_user(TENANT, user), OWNER)
        for changes in ({"role": "viewer"}, {"authorization_source": "legacy"},
                        {"authorization_source": "emergency_owner_fallback"}):
            self.error("access_denied", OwnerRef.from_database_user, tenant_id=TENANT, user=dict(user, **changes))
        for object_id in ("", "owner@example.com", "not-a-uuid", OWNER.object_id.upper() + " "):
            self.error("invalid_request", OwnerRef.from_database_user, tenant_id=TENANT,
                       user=dict(user, object_id=object_id))

    def test_authority_must_be_exact_typed_current_database_binding(self):
        for resolver in (lambda _: {"role": "owner"}, lambda _: None,
                         lambda _: OwnerAuthority(OTHER, ("U16088571",), ("U16088571",), H)):
            service = OwnerCodexTasks(self.connection, authorize=resolver, verify_receipt=self.archive)
            self.error("access_denied", service.get, owner=OWNER, task_id=str(uuid.uuid4()))
        for fields in ({"role": "platform_admin"}, {"authorization_source": "cache"},
                       {"authorized_subject_ids": ("UNREGISTERED",)}):
            arguments = dict(registered_subject_ids=("U16088571",), authorized_subject_ids=("U16088571",), authority_sha256=H)
            arguments.update(fields)
            self.error("access_denied", OwnerAuthority, owner=OWNER, **arguments)

    def test_typed_request_rejects_arbitrary_content_scope_and_hash(self):
        for arguments in ({"task_kind": "execute_shell"}, {"schema_version": True},
                          {"subject_ids": ("U16088606", "U16088571")}, {"subject_ids": ("U16088571", "U16088571")},
                          {"subject_ids": ["U16088571"]}, {"subject_ids": ()},
                          {"subject_ids": ("https://evil.example",)}, {"input_sha256": "prompt text"}):
            fields = dict(subject_ids=("U16088571",), input_sha256=H)
            fields.update(arguments)
            self.error("invalid_request", ServerValidatedTaskRequest, **fields)
        self.error("invalid_request", self.submit, request={"prompt": "execute something"})

    def test_session_idempotency_and_owner_scoped_submission(self):
        key = str(uuid.uuid4())
        first = self.service.create_session(OWNER, idempotency_key=key)
        self.assertEqual(first, self.service.create_session(OWNER, idempotency_key=key))
        self.assertNotEqual(first["session_id"], self.service.create_session(OTHER, idempotency_key=key)["session_id"])
        for owner in (OTHER, OTHER_TENANT):
            self.error("not_found", self.submit, owner=owner)

    def test_request_idempotency_hash_binds_session_scope_and_input(self):
        key = str(uuid.uuid4())
        task = self.submit(key=key)
        self.assertEqual(task, self.submit(key=key))
        self.assertEqual(len(self.service.events(OWNER, task_id=task["task_id"])), 1)
        for request in (ServerValidatedTaskRequest(("U16088606",), H),
                        ServerValidatedTaskRequest(("U16088571",), "b" * 64)):
            self.error("idempotency_conflict", self.submit, key=key, request=request)
        other_session = self.service.create_session(OWNER, idempotency_key=str(uuid.uuid4()))["session_id"]
        self.error("idempotency_conflict", self.submit, key=key, session=other_session)

    def test_metadata_never_stores_prompt_output_reasoning_or_source_values(self):
        task = self.submit()
        stored = self.row(task)
        self.assertEqual(stored["input_sha256"], H)
        self.assertNotIn("input_sha256", task)
        self.assertNotIn("request_sha256", task)
        for name in ("prompt", "output", "reasoning", "provider", "credentials", "source_values"):
            self.assertNotIn(name, stored)
        self.assertFalse(task["delivery_available"])
        self.assertEqual(task["state"], "queued")

    def test_all_owner_reads_and_cancel_deny_other_owner_and_tenant(self):
        task = self.submit()
        for owner in (OTHER, OTHER_TENANT):
            self.error("not_found", self.service.get, owner=owner, task_id=task["task_id"])
            self.error("not_found", self.service.events, owner=owner, task_id=task["task_id"])
            self.error("not_found", self.service.cancel, owner=owner, task_id=task["task_id"], expected_revision=1)
            self.error("not_found", self.service.list_tasks, owner=owner, session_id=self.session)

    def test_authority_rechecked_and_revocation_denies_future_actions(self):
        task = self.submit()
        before = self.authority.calls
        self.service.get(OWNER, task_id=task["task_id"])
        self.service.events(OWNER, task_id=task["task_id"])
        self.service.list_tasks(OWNER, session_id=self.session)
        self.assertEqual(self.authority.calls, before + 3)
        self.authority.active = False
        for function, arguments in ((self.service.get, {}), (self.service.cancel, {"expected_revision": 1})):
            self.error("access_denied", function, owner=OWNER, task_id=task["task_id"], **arguments)
        self.assertEqual(self.row(task)["state"], "queued")

    def test_account_revocation_filters_history_without_allow_all_fallback(self):
        first = self.submit()
        second = self.submit(request=ServerValidatedTaskRequest(("U16088606",), H))
        self.authority.authorized = ("U16088606",)
        self.error("access_denied", self.service.get, owner=OWNER, task_id=first["task_id"])
        self.assertEqual(self.service.list_tasks(OWNER, session_id=self.session), [second])
        self.authority.authorized = ()
        self.assertEqual(self.service.list_tasks(OWNER, session_id=self.session), [])
        self.error("access_denied", self.submit)

    def test_queued_cancel_atomic_prevents_claim_and_withholds_delivery(self):
        task = self.submit()
        cancelled = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=1)
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertFalse(cancelled["delivery_available"])
        self.error("transition_denied", self.service.claim, owner=OWNER, task_id=task["task_id"],
                   expected_revision=cancelled["revision"], worker_id=WORKER)
        self.assertEqual([e["state"] for e in self.service.events(OWNER, task_id=task["task_id"])], ["queued", "cancelled"])

    def test_claim_requires_protected_intent_and_public_status_hides_lease(self):
        task = self.submit()
        self.error("transition_denied", self.service.claim, owner=OWNER, task_id=task["task_id"], expected_revision=1, worker_id=WORKER)
        task, _ = self.intent(task)
        self.archive.available = False
        self.error("audit_unverified", self.service.claim, owner=OWNER, task_id=task["task_id"], expected_revision=2, worker_id=WORKER)
        self.assertEqual(self.row(task)["state"], "queued")
        self.archive.available = True
        lease = self.service.claim(OWNER, task_id=task["task_id"], expected_revision=2, worker_id=WORKER)
        self.assertEqual(lease["task"]["state"], "running")
        for secret in ("lease_token", "worker_id", "lease_expires_at", "intent_receipt_sha256"):
            self.assertNotIn(secret, self.service.get(OWNER, task_id=task["task_id"]))

    def test_running_cancel_is_request_not_confirmation(self):
        task, _, lease = self.claimed()
        task = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=task["revision"])
        self.assertEqual(task["state"], "cancel_requested")
        self.assertIn("not confirmed", task["summary"])
        self.assertFalse(task["delivery_available"])
        row = self.row(task)
        receipt = self.archive.add(self.service.receipt_binding(row, "outcome", outcome="cancelled"))
        done = self.service.finish(OWNER, task_id=task["task_id"], expected_revision=task["revision"],
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="cancelled", outcome_receipt_sha256=receipt)
        self.assertEqual(done["state"], "cancelled")
        self.assertTrue(done["delivery_available"])

    def test_cancelled_without_request_rejected_for_running_task(self):
        task, _, lease = self.claimed()
        self.error("transition_denied", self.service.finish, owner=OWNER, task_id=task["task_id"], expected_revision=3,
                   lease_token=lease["lease_token"], worker_id=WORKER, outcome="cancelled", outcome_receipt_sha256=H)

    def test_terminal_success_requires_exact_intent_outcome_and_result_binding(self):
        task, _, lease = self.claimed()
        row = self.row(task)
        valid = self.archive.add(self.service.receipt_binding(row, "outcome", outcome="succeeded", result_sha256=H))
        for receipt, result in (("b" * 64, H), (valid, "c" * 64)):
            self.error("audit_unverified", self.service.finish, owner=OWNER, task_id=task["task_id"], expected_revision=3,
                lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded", outcome_receipt_sha256=receipt, result_sha256=result)
        done = self.service.finish(OWNER, task_id=task["task_id"], expected_revision=3,
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded", outcome_receipt_sha256=valid, result_sha256=H)
        self.assertTrue(done["delivery_available"])
        self.assertEqual(done["result_sha256"], H)
        self.assertIsNone(self.row(task)["lease_token"])
        self.error("transition_denied", self.service.claim, owner=OWNER, task_id=task["task_id"], expected_revision=4, worker_id=WORKER)

    def test_old_observation_evidence_boolean_claim_and_extra_metadata_rejected(self):
        task = self.submit()
        binding = self.service.receipt_binding(self.row(task), "intent")
        reference = self.archive.add(binding)
        good = self.archive(reference, binding)
        for changes in ({"namespace": "specialist_observation"}, {"protected_archive_verified": 1},
                        {"database_committed": 1}, {"untrusted_reasoning": "secret"}, {"binding_sha256": "b" * 64}):
            bad = dict(good, **changes)
            self.service.verify_receipt = lambda *_, value=bad: value
            self.error("audit_unverified", self.service.attach_intent, owner=OWNER, task_id=task["task_id"],
                       expected_revision=1, receipt_sha256=reference, worker_id=WORKER)
        self.assertEqual(self.row(task)["revision"], 1)

    def test_failed_status_has_fixed_error_no_private_payload(self):
        task, _, lease = self.claimed()
        receipt = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="failed"))
        done = self.service.finish(OWNER, task_id=task["task_id"], expected_revision=3,
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="failed", outcome_receipt_sha256=receipt)
        self.assertEqual(done["error_code"], "worker_failed")
        self.assertNotIn("secret", json.dumps(done))
        self.assertTrue(done["delivery_available"])
        self.assertIsNone(done["result_sha256"])

    def test_expired_lease_is_uncertain_and_never_automatically_requeued(self):
        task, intent, lease = self.claimed()
        self.now += timedelta(seconds=121)
        self.error("lease_unavailable", self.service.heartbeat, owner=OWNER, task_id=task["task_id"], expected_revision=3,
                   lease_token=lease["lease_token"], worker_id=WORKER)
        uncertain = self.service.recover_expired(OWNER, task_id=task["task_id"], expected_revision=3, worker_id=WORKER)
        self.assertEqual(uncertain["state"], "needs_reconciliation")
        self.assertIsNone(self.row(task)["lease_token"])
        self.error("transition_denied", self.service.claim, owner=OWNER, task_id=task["task_id"], expected_revision=4, worker_id=WORKER)
        outcome = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="succeeded", result_sha256=H))
        done = self.service.reconcile(OWNER, task_id=task["task_id"], expected_revision=4, outcome="succeeded",
            intent_receipt_sha256=intent, outcome_receipt_sha256=outcome, result_sha256=H, worker_id=WORKER)
        self.assertEqual(done["state"], "succeeded")
        self.assertTrue(done["delivery_available"])

    def test_unexpired_or_queued_recovery_rejected(self):
        task = self.submit()
        self.error("transition_denied", self.service.recover_expired, owner=OWNER, task_id=task["task_id"], expected_revision=1, worker_id=WORKER)
        task, _, _ = self.claimed()
        self.error("transition_denied", self.service.recover_expired, owner=OWNER, task_id=task["task_id"], expected_revision=3, worker_id=WORKER)

    def test_lease_tokens_worker_bindings_and_revision_cas(self):
        task, _, lease = self.claimed()
        for token, worker in ((str(uuid.uuid4()), WORKER), (lease["lease_token"], str(uuid.uuid4()))):
            self.error("lease_unavailable", self.service.heartbeat, owner=OWNER, task_id=task["task_id"], expected_revision=3,
                       lease_token=token, worker_id=worker)
        heartbeat = self.service.heartbeat(OWNER, task_id=task["task_id"], expected_revision=3,
            lease_token=lease["lease_token"], worker_id=WORKER)
        self.assertEqual(heartbeat["revision"], 4)
        self.error("revision_conflict", self.service.cancel, owner=OWNER, task_id=task["task_id"], expected_revision=3)
        self.assertEqual(self.row(task)["state"], "running")

    def test_queued_cancellation_can_be_anchored_without_runtime(self):
        task = self.submit()
        task = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=1)
        row = self.row(task)
        intent = self.archive.add(self.service.receipt_binding(row, "intent"))
        outcome = self.archive.add(self.service.receipt_binding(row, "outcome", outcome="cancelled"))
        done = self.service.seal_cancelled(OWNER, task_id=task["task_id"], expected_revision=2,
            intent_receipt_sha256=intent, outcome_receipt_sha256=outcome, worker_id=WORKER)
        self.assertTrue(done["delivery_available"])
        self.assertEqual(self.service.events(OWNER, task_id=task["task_id"])[-1]["code"], "delivery_ready")
        self.assertIsNone(self.row(task)["worker_id"])

    def test_transition_and_event_commit_are_atomic_on_storage_failure(self):
        task = self.submit()
        self.connection.execute("""CREATE TRIGGER reject_event BEFORE INSERT ON owner_codex_task_events
            WHEN NEW.event_no=2 BEGIN SELECT RAISE(ABORT,'secret password from infrastructure'); END;""")
        error = self.error("storage_unavailable", self.service.cancel, owner=OWNER, task_id=task["task_id"], expected_revision=1)
        self.assertNotIn("secret", error)
        self.assertEqual(self.row(task)["revision"], 1)
        self.assertEqual(self.row(task)["state"], "queued")
        self.assertEqual(len(self.service.events(OWNER, task_id=task["task_id"])), 1)

    def test_events_append_only_fixed_schema_bounded_and_reconnectable(self):
        task, _, lease = self.claimed()
        self.service.heartbeat(OWNER, task_id=task["task_id"], expected_revision=3,
            lease_token=lease["lease_token"], worker_id=WORKER)
        events = self.service.events(OWNER, task_id=task["task_id"], after_revision=2, limit=1)
        self.assertEqual([e["revision"] for e in events], [3])
        self.assertEqual(set(events[0]), {"revision", "state", "code", "occurred_at"})
        for sql in ("UPDATE owner_codex_task_events SET code='changed'", "DELETE FROM owner_codex_task_events"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.connection.execute(sql)
            self.connection.rollback()
        self.assertEqual(len(self.service.events(OWNER, task_id=task["task_id"])), 4)

    def test_rejects_nested_transaction_without_committing_callers_work(self):
        self.connection.execute("INSERT INTO owner_codex_sessions VALUES (?,?,?,?,?)", (TENANT, OWNER.object_id, str(uuid.uuid4()), str(uuid.uuid4()), NOW.isoformat()))
        self.error("transition_denied", self.service.get, owner=OWNER, task_id=str(uuid.uuid4()))
        self.assertTrue(self.connection.in_transaction)
        self.connection.rollback()

    def test_bounded_lease_history_and_utc_clock(self):
        task, _ = self.intent(self.submit())
        for seconds in (True, 0, 14, 301, 100000):
            self.error("invalid_request", self.service.claim, owner=OWNER, task_id=task["task_id"], expected_revision=2,
                       worker_id=WORKER, lease_seconds=seconds)
        for limit in (True, 0, 101):
            self.error("invalid_request", self.service.list_tasks, owner=OWNER, session_id=self.session, limit=limit)
        self.now = NOW.replace(tzinfo=None)
        self.error("invalid_request", self.service.cancel, owner=OWNER, task_id=task["task_id"], expected_revision=2)

    def test_authority_failure_sanitized_and_receipt_outage_no_lease_or_delivery(self):
        task = self.submit()
        self.service.authorize = lambda _: (_ for _ in ()).throw(RuntimeError("secret token"))
        self.assertNotIn("secret", self.error("access_denied", self.service.get, owner=OWNER, task_id=task["task_id"]))
        self.service.authorize = self.authority
        self.archive.available = False
        self.assertNotIn("secret", self.error("audit_unverified", self.service.attach_intent, owner=OWNER,
                       task_id=task["task_id"], expected_revision=1, receipt_sha256=H, worker_id=WORKER))
        self.assertEqual(self.row(task)["revision"], 1)

    def test_owner_access_alone_never_authorizes_worker_operations(self):
        task = self.submit()
        binding = self.service.receipt_binding(self.row(task), "intent")
        receipt = self.archive.add(binding)
        for resolver in (None, lambda *_: {"role": "owner"}, lambda tenant, worker: WorkerAuthority(
                tenant, worker, OWNER.object_id), lambda tenant, worker: WorkerAuthority(
                OTHER_TENANT.tenant_id, worker, SERVICE_PRINCIPAL)):
            self.service.authorize_worker = resolver
            self.error("access_denied", self.service.attach_intent, owner=OWNER,
                task_id=task["task_id"], expected_revision=1, receipt_sha256=receipt, worker_id=WORKER)
        self.assertEqual(self.row(task)["revision"], 1)

    def test_worker_authority_rechecked_before_terminal_transition(self):
        task, _, lease = self.claimed()
        outcome = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="failed"))
        self.service.authorize_worker = lambda *_: None
        self.error("access_denied", self.service.finish, owner=OWNER, task_id=task["task_id"],
            expected_revision=3, lease_token=lease["lease_token"], worker_id=WORKER,
            outcome="failed", outcome_receipt_sha256=outcome)
        self.assertFalse(self.service.get(OWNER, task_id=task["task_id"])["delivery_available"])

    def test_cancel_receipt_requires_stopped_or_never_started_binding(self):
        task, _, lease = self.claimed()
        task = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=3)
        stopped = self.service.receipt_binding(self.row(task), "outcome", outcome="cancelled")
        self.assertEqual(stopped["execution_status"], "stopped")
        requested = dict(stopped, execution_status="cancel_requested")
        wrong_receipt = self.archive.add(requested)
        self.error("audit_unverified", self.service.finish, owner=OWNER, task_id=task["task_id"],
            expected_revision=4, lease_token=lease["lease_token"], worker_id=WORKER,
            outcome="cancelled", outcome_receipt_sha256=wrong_receipt)
        queued = self.submit()
        queued = self.service.cancel(OWNER, task_id=queued["task_id"], expected_revision=1)
        self.assertEqual(self.service.receipt_binding(self.row(queued), "outcome", outcome="cancelled")["execution_status"], "not_started")

    def test_cancellation_wins_cas_success_cannot_be_delivered(self):
        task, _, lease = self.claimed()
        success = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="succeeded", result_sha256=H))
        task = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=3)
        self.error("revision_conflict", self.service.finish, owner=OWNER, task_id=task["task_id"], expected_revision=3,
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded", outcome_receipt_sha256=success, result_sha256=H)
        self.error("transition_denied", self.service.finish, owner=OWNER, task_id=task["task_id"], expected_revision=4,
            lease_token=lease["lease_token"], worker_id=WORKER, outcome="succeeded", outcome_receipt_sha256=success, result_sha256=H)
        self.assertFalse(task["delivery_available"])
        self.assertTrue(task["cancellation_requested"])
        self.assertEqual(self.row(task)["state"], "cancel_requested")

    def test_cancellation_intent_survives_expired_recovery_and_blocks_success_reconcile(self):
        task, intent, _ = self.claimed()
        task = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=3)
        cancelled_at = self.row(task)["cancel_requested_at"]
        self.now += timedelta(seconds=121)
        uncertain = self.service.recover_expired(OWNER, task_id=task["task_id"], expected_revision=4, worker_id=WORKER)
        self.assertTrue(uncertain["cancellation_requested"])
        self.assertEqual(self.row(task)["cancel_requested_at"], cancelled_at)
        success = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="succeeded", result_sha256=H))
        self.error("transition_denied", self.service.reconcile, owner=OWNER, task_id=task["task_id"], expected_revision=5,
            outcome="succeeded", intent_receipt_sha256=intent, outcome_receipt_sha256=success, result_sha256=H, worker_id=WORKER)
        stopped = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="cancelled"))
        closed = self.service.reconcile(OWNER, task_id=task["task_id"], expected_revision=5, outcome="cancelled",
            intent_receipt_sha256=intent, outcome_receipt_sha256=stopped, worker_id=WORKER)
        self.assertEqual(closed["state"], "cancelled")
        self.assertTrue(closed["delivery_available"])
        self.assertTrue(closed["cancellation_requested"])
        self.assertIsNone(closed["result_sha256"])

    def test_owner_cancel_after_expired_recovery_records_sticky_intent(self):
        task, intent, _ = self.claimed()
        self.now += timedelta(seconds=121)
        task = self.service.recover_expired(OWNER, task_id=task["task_id"], expected_revision=3, worker_id=WORKER)
        self.assertFalse(task["cancellation_requested"])
        requested = self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=4)
        self.assertEqual(requested["state"], "needs_reconciliation")
        self.assertEqual(requested["revision"], 5)
        self.assertTrue(requested["cancellation_requested"])
        self.assertFalse(requested["delivery_available"])
        self.assertEqual(self.service.events(OWNER, task_id=task["task_id"])[-1]["code"], "cancel_requested")
        success = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="succeeded", result_sha256=H))
        self.error("transition_denied", self.service.reconcile, owner=OWNER, task_id=task["task_id"], expected_revision=5,
            outcome="succeeded", intent_receipt_sha256=intent, outcome_receipt_sha256=success, result_sha256=H, worker_id=WORKER)
        self.assertEqual(requested, self.service.cancel(OWNER, task_id=task["task_id"], expected_revision=5))
        stopped = self.archive.add(self.service.receipt_binding(self.row(task), "outcome", outcome="cancelled"))
        done = self.service.reconcile(OWNER, task_id=task["task_id"], expected_revision=5, outcome="cancelled",
            intent_receipt_sha256=intent, outcome_receipt_sha256=stopped, worker_id=WORKER)
        self.assertEqual(done["state"], "cancelled")
        self.assertIsNone(done["result_sha256"])

    def test_failed_state_requires_non_null_fixed_error_in_transactional_store(self):
        task = self.submit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("""UPDATE owner_codex_tasks SET state='failed',error_code=NULL,
                delivery_available=1,intent_receipt_sha256=?,outcome_receipt_sha256=? WHERE task_id=?""",
                (H, H, task["task_id"]))
        self.connection.rollback()
        self.assertEqual(self.row(task)["state"], "queued")

    def test_verifier_cannot_change_the_expected_receipt_binding(self):
        task = self.submit()
        reference = H

        def changed(reference, binding):
            binding["tenant_id"] = OTHER_TENANT.tenant_id
            return {"record_sha256": reference, "binding_sha256": _hash(binding),
                    "namespace": AUDIT_NAMESPACE, "protected_archive_verified": True,
                    "database_committed": True}

        self.service.verify_receipt = changed
        self.error("audit_unverified", self.service.attach_intent, owner=OWNER, task_id=task["task_id"],
                   expected_revision=1, receipt_sha256=reference, worker_id=WORKER)
        self.assertEqual(self.row(task)["revision"], 1)


class OwnerCodexConcurrencyTests(unittest.TestCase):
    def test_real_sqlite_two_connections_duplicate_request_creates_one_task_event(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "owner_tasks.sqlite"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            connection.executescript(SQLITE_SCHEMA)
            service = OwnerCodexTasks(connection, authorize=LocalAuthority(), verify_receipt=LocalProtectedReceiptVerifier())
            session = service.create_session(OWNER, idempotency_key=str(uuid.uuid4()))["session_id"]
            connection.close()
            key = str(uuid.uuid4())

            def submit():
                c = sqlite3.connect(path, timeout=10)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA foreign_keys=ON")
                try:
                    return OwnerCodexTasks(c, authorize=LocalAuthority(), verify_receipt=LocalProtectedReceiptVerifier()).submit(
                        OWNER, session_id=session, idempotency_key=key, request=REQUEST)
                finally:
                    c.close()

            with ThreadPoolExecutor(max_workers=2) as executor:
                first, second = list(executor.map(lambda _: submit(), range(2)))
            self.assertEqual(first, second)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute("SELECT count(*) FROM owner_codex_tasks").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM owner_codex_task_events").fetchone()[0], 1)
            finally:
                connection.close()


class OwnerCodexSourceOnlyMigrationTests(unittest.TestCase):
    def test_postgres_migration_forces_owner_tenant_rls_and_grants_nothing(self):
        root = Path(__file__).resolve().parents[1]
        sql = (root / "migrations/postgres/059_owner_codex_tasks.sql").read_text(encoding="utf-8")
        statements = re.sub(r"--[^\n]*", "", sql)
        self.assertNotRegex(statements, r"\bGRANT\b|\bCREATE\s+ROLE\b|\bCREATE\s+USER\b|SECURITY\s+DEFINER")
        for table in ("owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events"):
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", sql)
            self.assertIn(f"CREATE POLICY owner_isolation ON {table}", sql)
        self.assertIn("app_tenant_id() AND owner_object_id::text=current_setting('app.owner_object_id',true)", sql)
        self.assertIn("Every owner task revision requires an atomic status event", sql)
        self.assertIn("DEFERRABLE INITIALLY DEFERRED", sql)
        self.assertIn("Owner task history cannot be rewritten", sql)
        self.assertIn("Protected intent binding cannot be replaced", sql)
        self.assertIn("Owner cancellation intent cannot be cleared or replaced", sql)
        self.assertIn("state='failed' AND error_code IS NOT NULL AND error_code='worker_failed'", sql)
        self.assertNotIn("OLD.state='cancel_requested' AND NEW.state IN ('needs_reconciliation','succeeded'", sql)
        self.assertIn("OLD.state='needs_reconciliation' AND NEW.state='needs_reconciliation'", sql)
        self.assertIn("NEW.state='needs_reconciliation' AND NEW.code='cancel_requested'", sql)
        self.assertIn("FROM %I.owner_codex_tasks", sql)
        self.assertIn("FROM %I.owner_codex_task_events", sql)
        self.assertIn("TG_TABLE_SCHEMA", sql)
        for table in ("nav_history", "position_snapshots", "raw_statements", "flex_sources", "reporting_snapshots"):
            self.assertNotIn(table, statements)

    def test_module_has_no_runtime_network_source_or_provider_access(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/ibkr_etl/owner_codex_tasks.py").read_text(encoding="utf-8")
        for operation in ("import subprocess", "import requests", "import socket", "import urllib",
                          "os.environ", "AzureCliCredential", "ChatOpenAI", "exec(", "eval("):
            self.assertNotIn(operation, source)
        self.assertIn("Owner revocation blocks all subsequent metadata transitions", source)
        self.assertIn("AS tenant", source)


if __name__ == "__main__":
    unittest.main()
