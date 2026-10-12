"""Additive durable execution fencing, not a production runtime activation.

The SQL store contains identifiers, hashes and fixed states only. It never reads
or changes received IBKR data. Schema installation and restricted PostgreSQL/RLS
acceptance are separate deployment gates; SQLite fixtures prove persistence and
transactions, NOT live authentication, protected archival or process cessation.

A logical task is consumed before the audit/model boundary. A runtime stays
occupied across crashes until an independently verified stopped/not-started
outcome is committed. Unknown cessation is permanently quarantined here. There
is deliberately no expiry, reset, delete, automatic retry or resume API.
Protected receipts are supplied by the separate audit adapter, never fabricated
from a local SQL commit. Owner authority/cancellation/leases remain freshly
checked by the existing coordinator's trusted callbacks at every boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from .db import is_postgres, transaction
from .owner_codex_tasks import WorkerAuthority
from .owner_codex_worker import (
    OwnerReviewCoordinator, ProtectedWorkerReceipt, ReviewWork, WorkerCallbacks,
    WorkerError, WorkerEvidence, _digest,
    MAX_ATTEMPTS, _sha,
)


@dataclass(frozen=True)
class Reservation:
    work: ReviewWork
    intent_binding_sha256: str

    def __post_init__(self):
        if type(self.work) is not ReviewWork:
            raise WorkerError('invalid_request')
        _sha(self.intent_binding_sha256)


class ExecutionFenceStore:
    """Trusted worker-only metadata connection; no schema/grant creation.

    The authorization callback MUST freshly resolve enabled worker authority
    from the database. An owner/browser must never receive this connection or
    callback. Production composition must additionally qualify the restricted
    login, tenant/worker RLS and audit identity; passing this constructor does
    not establish that qualification. Transactions must own an idle connection.
    """
    def __init__(self, connection, *, authorize_worker):
        if not callable(authorize_worker):
            raise WorkerError('invalid_request')
        self.connection = connection
        self.authorize_worker = authorize_worker

    @contextmanager
    def _scope(self, work):
        if type(work) is not ReviewWork:
            raise WorkerError('invalid_request')
        raw = getattr(self.connection, '_connection', None)
        if ((raw is not None and (raw.autocommit is not False or raw.info.transaction_status != 0))
                or (not is_postgres(self.connection) and self.connection.in_transaction)):
            raise WorkerError('control_unavailable')
        try:
            with transaction(self.connection):
                authority = self.authorize_worker(work)
                if (type(authority) is not WorkerAuthority or authority.enabled is not True
                        or authority.tenant_id != work.owner.tenant_id
                        or authority.worker_id != work.worker_id
                        or authority.service_principal_id == work.owner.object_id
                        or authority.authorization_source != 'database_service'
                        or authority.capability != 'owner_task_metadata'):
                    raise WorkerError('access_denied')
                if is_postgres(self.connection):
                    configured = self.connection.execute('SELECT app_tenant_id()').fetchone()
                    if configured is None or str(configured[0]) != work.owner.tenant_id:
                        raise WorkerError('access_denied')
                    self.connection.execute("SELECT set_config('app.tenant_id', ?, true)", (work.owner.tenant_id,))
                    self.connection.execute("SELECT set_config('app.execution_worker_id', ?, true)", (work.worker_id,))
                yield
        except WorkerError:
            raise
        except Exception:
            raise WorkerError('control_unavailable') from None

    @staticmethod
    def _key(work):
        # NEVER include attempt, lease, request hash or runtime in the task key.
        return (work.owner.tenant_id, work.owner.object_id, work.task_id)

    def _row(self, work):
        return self.connection.execute(
            'SELECT * FROM owner_codex_execution_fences WHERE tenant_id=? AND owner_object_id=? AND task_id=?'
            + (' FOR UPDATE' if is_postgres(self.connection) else ''),
            self._key(work)).fetchone()

    def _event(self, work, state, binding):
        self.connection.execute('INSERT INTO owner_codex_execution_fence_events '
            '(tenant_id,owner_object_id,task_id,worker_id,state,binding_sha256) VALUES (?,?,?,?,?,?)',
            (*self._key(work), work.worker_id, state, binding))

    def reserve(self, evidence):
        if type(evidence) is not WorkerEvidence or evidence.phase != 'intent':
            raise WorkerError('invalid_request')
        work, binding = evidence.work, _digest(evidence.payload())
        with self._scope(work):
            created = self.connection.execute('INSERT INTO owner_codex_execution_fences '
                '(tenant_id,owner_object_id,task_id,attempt_id,worker_id,lease_token,request_sha256,'
                'intent_binding_sha256,state) VALUES (?,?,?,?,?,?,?,?,?) '
                'ON CONFLICT (tenant_id,owner_object_id,task_id) DO NOTHING',
                (*self._key(work), work.attempt_id, work.worker_id, work.lease_token,
                 work.request_sha256, binding, 'reserved')).rowcount
            if created != 1:
                raise WorkerError('attempt_already_consumed')
            self.connection.execute('INSERT INTO owner_codex_execution_runtimes '
                '(tenant_id,worker_id,quarantined) VALUES (?,?,?) '
                'ON CONFLICT (tenant_id,worker_id) DO NOTHING',
                (work.owner.tenant_id, work.worker_id, False))
            claimed = self.connection.execute('UPDATE owner_codex_execution_runtimes '
                'SET active_attempt_id=? WHERE tenant_id=? AND worker_id=? '
                'AND active_attempt_id IS NULL AND quarantined=?',
                (work.attempt_id, work.owner.tenant_id, work.worker_id, False)).rowcount
            if claimed != 1:
                # Transaction rollback removes only THIS losing reservation.
                raise WorkerError('worker_quarantined')
            self._event(work, 'reserved', binding)
        return Reservation(work, binding)

    def _owned(self, reservation, *, completed_allowed=False):
        if type(reservation) is not Reservation or type(reservation.work) is not ReviewWork:
            raise WorkerError('invalid_request')
        work = reservation.work
        row = self._row(work)
        if (row is None or str(row['attempt_id']) != work.attempt_id
                or str(row['worker_id']) != work.worker_id
                or str(row['lease_token']) != work.lease_token
                or row['request_sha256'] != work.request_sha256
                or row['intent_binding_sha256'] != reservation.intent_binding_sha256):
            raise WorkerError('attempt_already_consumed')
        if completed_allowed and row['state'] == 'settled' and row['outcome_receipt_sha256'] is not None:
            return row  # Fresh owner/lease checks still run; this cannot relaunch.
        runtime = self.connection.execute('SELECT * FROM owner_codex_execution_runtimes '
            'WHERE tenant_id=? AND worker_id=?', (work.owner.tenant_id, work.worker_id)).fetchone()
        if (runtime is None or str(runtime['active_attempt_id']) != work.attempt_id
                or runtime['quarantined'] or row['state'] in {'settled', 'quarantined'}):
            raise WorkerError('worker_quarantined')
        return row

    def verify_active(self, reservation, *, completed_allowed=False):
        work = reservation.work
        with self._scope(work):
            self._owned(reservation, completed_allowed=completed_allowed)

    def intent_verified(self, reservation, receipt, evidence):
        self._receipt(receipt, evidence)
        if (evidence.phase != 'intent' or evidence.work != reservation.work
                or _digest(evidence.payload()) != reservation.intent_binding_sha256):
            raise WorkerError('intent_unverified')
        self._transition(reservation, 'reserved', 'intent_verified', receipt.record_sha256)

    def launch_reserved(self, reservation):
        # Commit BEFORE invoking inference; unknown call acknowledgement remains
        # occupied even if the Python process dies before an outcome is written.
        self._transition(reservation, 'intent_verified', 'launch_reserved', None)

    @staticmethod
    def _receipt(receipt, evidence):
        if type(evidence) is not WorkerEvidence:
            raise WorkerError('invalid_request')
        if (type(receipt) is not ProtectedWorkerReceipt
                or not OwnerReviewCoordinator._verified(receipt, evidence)):
            raise WorkerError('outcome_unverified' if evidence.phase == 'outcome' else 'intent_unverified')

    def _transition(self, reservation, previous, state, receipt):
        work = reservation.work
        with self._scope(work):
            row = self._owned(reservation)
            if row['state'] != previous:
                raise WorkerError('attempt_already_consumed')
            count = self.connection.execute('UPDATE owner_codex_execution_fences '
                'SET state=?,intent_receipt_sha256=? WHERE tenant_id=? AND owner_object_id=? '
                'AND task_id=? AND state=?', (state, receipt if receipt is not None else row['intent_receipt_sha256'],
                *self._key(work), previous)).rowcount
            if count != 1:
                raise WorkerError('control_unavailable')
            self._event(work, state, reservation.intent_binding_sha256)

    def quarantine(self, reservation):
        work = reservation.work
        with self._scope(work):
            row = self._row(work)
            if (row is not None and row['state'] == 'quarantined'
                    and str(row['attempt_id']) == work.attempt_id):
                return  # Already sticky; cannot clear or rearm it.
            self._owned(reservation)
            self.connection.execute('UPDATE owner_codex_execution_fences SET state=? '
                'WHERE tenant_id=? AND owner_object_id=? AND task_id=?', ('quarantined', *self._key(work)))
            self.connection.execute('UPDATE owner_codex_execution_runtimes SET quarantined=? '
                'WHERE tenant_id=? AND worker_id=? AND active_attempt_id=?',
                (True, work.owner.tenant_id, work.worker_id, work.attempt_id))
            self._event(work, 'quarantined', reservation.intent_binding_sha256)

    def settle(self, reservation, receipt, evidence):
        self._receipt(receipt, evidence)
        work = reservation.work
        if (evidence.phase != 'outcome' or evidence.work != work
                or evidence.process_state not in {'not_started', 'stopped'}):
            raise WorkerError('stop_unverified')
        with self._scope(work):
            row = self._owned(reservation)
            if (evidence.inference_attempted and row['state'] != 'launch_reserved'):
                raise WorkerError('stop_unverified')
            if (not evidence.inference_attempted and row['state'] == 'launch_reserved'):
                raise WorkerError('stop_unverified')
            if (row['intent_receipt_sha256'] is not None
                    and evidence.intent_receipt_sha256 != row['intent_receipt_sha256']):
                raise WorkerError('outcome_unverified')
            self.connection.execute('UPDATE owner_codex_execution_fences '
                'SET state=?,outcome_receipt_sha256=? WHERE tenant_id=? AND owner_object_id=? AND task_id=?',
                ('settled', receipt.record_sha256, *self._key(work)))
            freed = self.connection.execute('UPDATE owner_codex_execution_runtimes SET active_attempt_id=NULL '
                'WHERE tenant_id=? AND worker_id=? AND active_attempt_id=? AND quarantined=?',
                (work.owner.tenant_id, work.worker_id, work.attempt_id, False)).rowcount
            if freed != 1:
                raise WorkerError('control_unavailable')
            self._event(work, 'settled', _digest(evidence.payload()))


class DurableWorkerCallbacks:
    """Connect durable SQL fencing to the existing qualified callback contract.

    Separate instances/processes share the SQL fences, not in-memory sets. The
    local permit is issued only by a winning reservation; no recovery method can
    reconstruct it. Receipt callbacks still require independently protected
    archive/DB verification. This adapter changes no owner-task status and grants
    no result delivery; concrete audit, authentication and website wiring remain
    separate acceptance work.
    """
    def __init__(self, store, callbacks):
        if type(store) is not ExecutionFenceStore or type(callbacks) is not WorkerCallbacks:
            raise WorkerError('invalid_request')
        self.store, self.inner, self._permits = store, callbacks, {}

    def _permit(self, work):
        permit = self._permits.get(work.attempt_id)
        if permit is None or permit.work != work:
            raise WorkerError('attempt_already_consumed')
        return permit

    async def check_control(self, work):
        if work.attempt_id in self._permits:
            self.store.verify_active(self._permit(work), completed_allowed=True)
        return await self.inner.check_control(work)

    async def protect_intent(self, evidence):
        if type(evidence) is not WorkerEvidence:
            raise WorkerError('invalid_request')
        if len(self._permits) >= MAX_ATTEMPTS:
            raise WorkerError('coordinator_capacity')
        permit = self.store.reserve(evidence)
        self._permits[evidence.work.attempt_id] = permit
        receipt = await self.inner.protect_intent(evidence)
        self.store.intent_verified(permit, receipt, evidence)
        return receipt

    async def start_inference(self, work, observation):
        permit = self._permit(work)
        self.store.launch_reserved(permit)
        return await self.inner.start_inference(work, observation)

    async def protect_outcome(self, evidence):
        permit = self._permit(evidence.work)
        # Persist BEFORE the archive callback: failure there must not lose the
        # stop uncertainty. Pending runtime occupancy is also sticky on DB loss.
        if evidence.process_state == 'unknown':
            self.store.quarantine(permit)
        receipt = await self.inner.protect_outcome(evidence)
        self.store._receipt(receipt, evidence)
        if evidence.process_state != 'unknown':
            self.store.settle(permit, receipt, evidence)
        return receipt

    def callbacks(self):
        return WorkerCallbacks(self.inner.authorize, self.check_control, self.protect_intent,
            self.inner.observe, self.start_inference, self.protect_outcome)
