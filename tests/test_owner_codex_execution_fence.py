"""Durable synthetic SQL/coordinator qualification, NOT live PostgreSQL acceptance."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ibkr_etl.owner_codex_execution_fence import DurableWorkerCallbacks, ExecutionFenceStore, Reservation
from ibkr_etl.owner_codex_worker import OwnerReviewCoordinator, WorkerError, WorkerEvidence, _digest
from test_owner_codex_worker import RecordingFixture, WORK, WORKER, uid


FIXTURE_SCHEMA = '''
CREATE TABLE owner_codex_execution_fences (
tenant_id TEXT,owner_object_id TEXT,task_id TEXT,attempt_id TEXT,worker_id TEXT,lease_token TEXT,
request_sha256 TEXT,intent_binding_sha256 TEXT,state TEXT,intent_receipt_sha256 TEXT,outcome_receipt_sha256 TEXT,
PRIMARY KEY(tenant_id,owner_object_id,task_id),UNIQUE(tenant_id,worker_id,attempt_id));
CREATE TABLE owner_codex_execution_runtimes (
tenant_id TEXT,worker_id TEXT,active_attempt_id TEXT,quarantined BOOLEAN NOT NULL,
PRIMARY KEY(tenant_id,worker_id));
CREATE TABLE owner_codex_execution_fence_events (
tenant_id TEXT,owner_object_id TEXT,task_id TEXT,worker_id TEXT,state TEXT,binding_sha256 TEXT,
PRIMARY KEY(tenant_id,owner_object_id,task_id,state));
'''


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def intent(work=WORK):
    fixture = RecordingFixture()
    return WorkerEvidence(work, 'intent', None, None, 'not_started', False,
        fixture.policy_sha256, 'f' * 64, uid(7))


def race_reserve(path, index, ready, go, results):
    db = connect(path)
    fixture = RecordingFixture()
    store = ExecutionFenceStore(db, authorize_worker=lambda work: fixture.worker)
    work = replace(WORK, attempt_id=uid(100 + index), lease_token=uid(200 + index))
    ready.put(index)
    go.wait(15)
    try:
        store.reserve(intent(work))
        results.put('won')
    except WorkerError as error:
        results.put(error.code)
    finally:
        db.close()


class FenceFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'synthetic-task-fence.sqlite')
        self.db = connect(self.path)
        self.addCleanup(self.db.close)
        self.db.executescript(FIXTURE_SCHEMA)
        self.fixture = RecordingFixture()
        self.enabled = True
        self.store = ExecutionFenceStore(self.db,
            authorize_worker=lambda work: self.fixture.worker if self.enabled else None)

    def reopen(self):
        db = connect(self.path)
        self.addCleanup(db.close)
        return ExecutionFenceStore(db, authorize_worker=lambda work: self.fixture.worker)

    def state(self):
        return self.db.execute('SELECT state FROM owner_codex_execution_fences').fetchone()[0]

    def outcome(self, work=WORK, *, launched=False, unknown=False, intent_receipt=None):
        return WorkerEvidence(work, 'outcome', 'needs_reconciliation' if unknown else 'cancelled',
            'stop_unverified' if unknown else 'cancel_requested',
            'unknown' if unknown else 'stopped' if launched else 'not_started', launched,
            intent_receipt_sha256=intent_receipt)


class DurableFenceTests(FenceFixture, unittest.TestCase):
    def test_new_attempt_lease_request_and_worker_cannot_rearm_same_logical_task(self):
        self.store.reserve(intent())
        for change in ({'attempt_id': uid(70)}, {'lease_token': uid(71)},
                       {'request_sha256': '9' * 64}, {'worker_id': uid(72)}):
            work = replace(WORK, **change)
            with self.subTest(change=change), self.assertRaises(WorkerError):
                self.reopen().reserve(intent(work))
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_codex_execution_fences').fetchone()[0], 1)

    def test_crash_after_reservation_blocks_another_task_on_same_runtime(self):
        self.store.reserve(intent())
        other = replace(WORK, task_id=uid(70), attempt_id=uid(71))
        with self.assertRaises(WorkerError):
            self.reopen().reserve(intent(other))
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_codex_execution_fences').fetchone()[0], 1)

    def test_crash_after_launch_cannot_be_resumed_from_fresh_coordinator(self):
        permit = self.store.reserve(intent())
        self.store.intent_verified(permit, self.fixture.receipt(intent()), intent())
        self.store.launch_reserved(permit)
        self.assertEqual(self.state(), 'launch_reserved')
        with self.assertRaises(WorkerError):
            self.reopen().reserve(intent(replace(WORK, attempt_id=uid(90))))

    def test_unknown_stop_is_sticky_even_after_store_reconstruction(self):
        permit = self.store.reserve(intent())
        self.store.quarantine(permit)
        self.reopen().quarantine(permit)
        self.assertEqual(self.state(), 'quarantined')
        with self.assertRaises(WorkerError):
            self.reopen().settle(permit, self.fixture.receipt(self.outcome()), self.outcome())
        self.assertEqual(self.db.execute('SELECT quarantined FROM owner_codex_execution_runtimes').fetchone()[0], 1)

    def test_verified_not_started_outcome_releases_runtime_but_never_task(self):
        permit = self.store.reserve(intent())
        evidence = self.outcome()
        self.store.settle(permit, self.fixture.receipt(evidence), evidence)
        self.assertEqual(self.state(), 'settled')
        with self.assertRaises(WorkerError):
            self.reopen().reserve(intent())
        self.reopen().reserve(intent(replace(WORK, task_id=uid(70), attempt_id=uid(71))))

    def test_launch_requires_verified_intent_and_commits_before_inference(self):
        permit = self.store.reserve(intent())
        with self.assertRaises(WorkerError):
            self.store.launch_reserved(permit)
        self.store.intent_verified(permit, self.fixture.receipt(intent()), intent())
        self.store.launch_reserved(permit)
        with self.assertRaises(WorkerError):
            self.store.launch_reserved(permit)

    def test_launch_cannot_be_closed_with_not_started_evidence(self):
        permit = self.store.reserve(intent())
        receipt = self.fixture.receipt(intent())
        self.store.intent_verified(permit, receipt, intent())
        self.store.launch_reserved(permit)
        evidence = self.outcome(intent_receipt=receipt.record_sha256)
        with self.assertRaises(WorkerError):
            self.store.settle(permit, self.fixture.receipt(evidence), evidence)
        self.assertEqual(self.state(), 'launch_reserved')

    def test_inference_cannot_be_closed_before_launch_reservation(self):
        permit = self.store.reserve(intent())
        evidence = self.outcome(launched=True)
        with self.assertRaises(WorkerError):
            self.store.settle(permit, self.fixture.receipt(evidence), evidence)

    def test_mismatched_outcome_cannot_release_runtime(self):
        permit = self.store.reserve(intent())
        receipt = self.fixture.receipt(intent())
        self.store.intent_verified(permit, receipt, intent())
        evidence = self.outcome(intent_receipt='0' * 64)
        with self.assertRaises(WorkerError):
            self.store.settle(permit, self.fixture.receipt(evidence), evidence)
        self.assertEqual(self.state(), 'intent_verified')

    def test_changed_policy_cannot_attach_an_intent_to_an_old_reservation(self):
        permit = self.store.reserve(intent())
        changed = replace(intent(), policy_sha256='8' * 64)
        with self.assertRaises(WorkerError):
            self.store.intent_verified(permit, self.fixture.receipt(changed), changed)
        self.assertEqual(self.state(), 'reserved')

    def test_receipt_flags_and_binding_are_checked_not_manufactured(self):
        permit = self.store.reserve(intent())
        receipt = self.fixture.receipt(intent())
        for field in ('database_committed', 'protected_archive_verified', 'independently_verified', 'newly_created'):
            with self.subTest(field=field), self.assertRaises(WorkerError):
                self.store.intent_verified(permit, replace(receipt, **{field: False}), intent())
        with self.assertRaises(WorkerError):
            self.store.intent_verified(permit, replace(receipt, binding_sha256='0' * 64), intent())
        self.assertEqual(self.state(), 'reserved')

    def test_worker_revocation_is_fresh_and_exception_text_is_redacted(self):
        permit = self.store.reserve(intent())
        self.enabled = False
        with self.assertRaises(WorkerError) as caught:
            self.store.verify_active(permit)
        self.assertEqual(caught.exception.code, 'access_denied')
        self.store.authorize_worker = lambda work: (_ for _ in ()).throw(RuntimeError('private secret'))
        with self.assertRaises(WorkerError) as caught:
            self.store.verify_active(permit)
        self.assertNotIn('private secret', str(caught.exception))

    def test_connection_transaction_is_never_committed_on_behalf_of_caller(self):
        self.db.execute('BEGIN')
        with self.assertRaises(WorkerError):
            self.store.reserve(intent())
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()

    def test_missing_schema_fails_closed_without_install_or_grants(self):
        db = connect(':memory:')
        self.addCleanup(db.close)
        store = ExecutionFenceStore(db, authorize_worker=lambda work: self.fixture.worker)
        with self.assertRaises(WorkerError):
            store.reserve(intent())
        self.assertEqual(db.execute('SELECT count(*) FROM sqlite_master').fetchone()[0], 0)

    def test_reservation_binding_is_validated_and_cross_scope_changes_rejected(self):
        permit = self.store.reserve(intent())
        with self.assertRaises(WorkerError):
            Reservation(WORK, 'bad')
        with self.assertRaises(WorkerError):
            self.store.verify_active(replace(permit, work=replace(WORK, request_sha256='7' * 64)))

    def test_history_records_atomic_fixed_transitions_only(self):
        permit = self.store.reserve(intent())
        self.store.intent_verified(permit, self.fixture.receipt(intent()), intent())
        self.store.launch_reserved(permit)
        self.store.quarantine(permit)
        self.assertEqual([r[0] for r in self.db.execute('SELECT state FROM owner_codex_execution_fence_events ORDER BY rowid')],
            ['reserved', 'intent_verified', 'launch_reserved', 'quarantined'])

    def test_four_independent_processes_compete_for_exactly_one_reservation(self):
        context = multiprocessing.get_context('spawn')
        ready, results, go = context.Queue(), context.Queue(), context.Event()
        workers = [context.Process(target=race_reserve, args=(self.path, i, ready, go, results)) for i in range(4)]
        try:
            for worker in workers:
                worker.start()
            for _ in workers:
                ready.get(timeout=20)
            go.set()
            observed = [results.get(timeout=20) for _ in workers]
            for worker in workers:
                worker.join(10)
                self.assertEqual(worker.exitcode, 0)
            self.assertEqual(observed.count('won'), 1)
            self.assertEqual(observed.count('attempt_already_consumed'), 3)
        finally:
            for worker in workers:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
            ready.close()
            results.close()


class DurableCoordinatorTests(FenceFixture, unittest.IsolatedAsyncioTestCase):
    def coordinator(self, fixture=None, store=None):
        return OwnerReviewCoordinator(DurableWorkerCallbacks(store or self.store,
            (fixture or self.fixture).callbacks()).callbacks(), call_timeout=.1,
            inference_timeout=.2, poll_interval=.005, stop_timeout=.1)

    async def test_success_remains_private_and_fresh_coordinator_cannot_replay(self):
        decision = await self.coordinator().run(WORK)
        self.assertEqual(decision.status, 'succeeded')
        self.assertFalse(decision.result_delivery_authorized)
        self.assertEqual(self.state(), 'settled')
        replacement = RecordingFixture()
        await self.coordinator(replacement, self.reopen()).run(WORK)
        self.assertNotIn('start', replacement.calls)
        self.assertNotIn('observe', replacement.calls)

    async def test_lost_archive_ack_does_not_relaunch_after_process_reconstruction(self):
        self.fixture.intent_error = True
        decision = await self.coordinator().run(WORK)
        self.assertNotEqual(decision.status, 'succeeded')
        replacement = RecordingFixture()
        await self.coordinator(replacement, self.reopen()).run(WORK)
        self.assertNotIn('start', replacement.calls)

    async def test_unknown_start_persists_quarantine_before_failed_audit(self):
        self.fixture.start_error = self.fixture.outcome_error = True
        decision = await self.coordinator().run(WORK)
        self.assertFalse(decision.protected_outcome_verified)
        self.assertEqual(self.state(), 'quarantined')
        other = replace(WORK, task_id=uid(70), attempt_id=uid(71))
        replacement = RecordingFixture()
        replacement.control = replace(replacement.control, task_id=other.task_id, attempt_id=other.attempt_id)
        await self.coordinator(replacement, self.reopen()).run(other)
        self.assertNotIn('start', replacement.calls)

    async def test_unverified_stop_persists_quarantine_across_restart(self):
        self.fixture.release.clear()
        self.fixture.stop_error = True
        await self.coordinator().run(WORK)
        self.assertEqual(self.state(), 'quarantined')
        replacement = RecordingFixture()
        await self.coordinator(replacement, self.reopen()).run(WORK)
        self.assertNotIn('start', replacement.calls)

    async def test_owner_cancellation_stops_and_does_not_release_success(self):
        self.fixture.release.clear()
        self.fixture.control_hook = lambda f: setattr(f, 'control', replace(f.control,
            cancellation_requested=f.start_called.is_set()))
        decision = await self.coordinator().run(WORK)
        self.assertEqual(decision.status, 'cancelled')
        self.assertIn('stop', self.fixture.calls)
        self.assertEqual(self.state(), 'settled')

    async def test_owner_revocation_stops_without_source_access_or_result_delivery(self):
        self.fixture.release.clear()
        self.fixture.authorization_hook = lambda f: setattr(f, 'authorized', () if f.start_called.is_set() else f.authorized)
        decision = await self.coordinator().run(WORK)
        self.assertEqual(decision.status, 'denied')
        self.assertIn('stop', self.fixture.calls)
        self.assertIsNone(decision.result_sha256)
        self.assertEqual(self.state(), 'settled')

    async def test_unverified_outcome_keeps_runtime_occupied(self):
        self.fixture.outcome_error = True
        decision = await self.coordinator().run(WORK)
        self.assertFalse(decision.protected_outcome_verified)
        self.assertEqual(self.state(), 'launch_reserved')
        with self.assertRaises(WorkerError):
            self.reopen().reserve(intent(replace(WORK, task_id=uid(90), attempt_id=uid(91))))

    async def test_cancel_before_intent_never_reserves_or_starts(self):
        self.fixture.control = replace(self.fixture.control, cancellation_requested=True)
        await self.coordinator().run(WORK)
        self.assertNotIn('start', self.fixture.calls)
        self.assertEqual(self.db.execute('SELECT count(*) FROM owner_codex_execution_fences').fetchone()[0], 0)

    async def test_two_coordinators_cannot_duplicate_inference(self):
        self.fixture.release.clear()
        first = asyncio.create_task(self.coordinator().run(WORK))
        await asyncio.wait_for(self.fixture.start_called.wait(), 1)
        replacement = RecordingFixture()
        decision = await self.coordinator(replacement, self.reopen()).run(WORK)
        self.assertNotEqual(decision.status, 'succeeded')
        self.assertNotIn('start', replacement.calls)
        self.fixture.release.set()
        self.assertEqual((await first).status, 'succeeded')


class FenceSchemaTests(unittest.TestCase):
    def test_postgres_schema_is_not_automatically_applied_to_production(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / 'scripts/owner_task_execution_fence_schema.sql').read_text()
        self.assertFalse((root / 'migrations/postgres/060_owner_task_execution_fence.sql').exists())
        self.assertEqual(source.count('ENABLE ROW LEVEL SECURITY'), 3)
        self.assertEqual(source.count('FORCE ROW LEVEL SECURITY'), 3)
        self.assertNotIn('GRANT ', source)
        self.assertIn('OLD.quarantined', source)
        self.assertIn("OLD.state='launch_reserved'", source)
        self.assertIn('Execution reservations cannot be deleted', source)
        self.assertIn('Execution events are append only', source)


if __name__ == '__main__':
    unittest.main()
