"""Synthetic Step 3 audit tests. NOT live PostgreSQL/identity acceptance."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import unittest

from azure.core.exceptions import ResourceExistsError

from ibkr_etl.owner_codex_worker import WorkerEvidence, _digest
from ibkr_etl.owner_codex_worker_audit import (
    ACCOUNT, ARM_SCOPE, CONTAINER, PREFIX, ArchiveVersion, AuditError,
    CommittedAudit, IndependentWorkerAuditReader, ProducerRegistration,
    VerificationChallenge, VersionedWorkerArchive, WorkerAuditWriter,
    canonical, envelope, sha,
)
from test_owner_codex_worker import WORK, uid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import qualify_owner_task_worker_audit_source as checkpoint


REGISTRATION = ProducerRegistration(WORK.owner.tenant_id, WORK.worker_id,
    uid(7), '1'*64, '2'*64, uid(8), uid(9))
INTENT = WorkerEvidence(WORK, 'intent', None, None, 'not_started', False,
    'c'*64, 'f'*64, uid(7))
VERSION = '2026-10-05T18:00:00.1234567Z'


def arm():
    return dict(id=ARM_SCOPE, properties=dict(publicAccess='None', hasLegalHold=True,
        legalHold=dict(protectedAppendWritesHistory=dict(allowProtectedAppendWritesAll=False))))


class Blob:
    def __init__(self, container, name, version):
        self.container, self.name, self.version = container, name, version

    def upload_blob(self, payload, overwrite):
        self.container.calls.append(('upload', self.name, overwrite))
        if self.name in self.container.data:
            raise ResourceExistsError('synthetic collision')
        self.container.data[self.name] = payload
        if self.container.lost_ack:
            raise RuntimeError('private credential must not leak')
        return dict(version_id=VERSION, etag='"one"')

    def get_blob_properties(self):
        return SimpleNamespace(version_id=self.container.version, etag=self.container.etag,
            size=len(self.container.data[self.name]) + self.container.size_delta)

    def download_blob(self, **kwargs):
        self.container.calls.append(('read', self.name, self.version, kwargs))
        data = self.container.data[self.name]
        if self.container.corrupt:
            data += b'!'
        return SimpleNamespace(readall=lambda: data)


class Container:
    account_name, container_name = ACCOUNT, CONTAINER
    def __init__(self, data):
        self.data, self.calls = data, []
        self.version, self.etag = VERSION, '"one"'
        self.size_delta, self.corrupt, self.lost_ack = 0, False, False

    def get_blob_client(self, name, version_id=None):
        return Blob(self, name, version_id)


class SQLFixture:
    """One durable SQLite fixture; separate synthetic writer/reader connections."""
    def __init__(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.execute('CREATE TABLE owner_codex_worker_audit_events ('
            'tenant_id TEXT, owner_object_id TEXT, task_id TEXT, phase TEXT, worker_id TEXT,'
            'attempt_id TEXT,binding_sha256 TEXT,record_sha256 TEXT,envelope_json TEXT,'
            'PRIMARY KEY (tenant_id,owner_object_id,task_id,phase))')
        self.calls, self.closed, self.commits = [], [], 0
        self.fail_qualify = None
        self.busy = False
        self.commit_loss = False

    @contextmanager
    def connection(self, role):
        fixture = self
        class Connection:
            autocommit = True
            info = SimpleNamespace(transaction_status=2 if fixture.busy else 0)
            @contextmanager
            def transaction(self):
                fixture.calls.append((role, 'begin'))
                fixture.db.execute('BEGIN')
                try:
                    yield
                    fixture.db.commit()
                    fixture.commits += 1
                    fixture.calls.append((role, 'commit'))
                    if fixture.commit_loss and role == 'writer':
                        raise RuntimeError('synthetic lost commit reply')
                except Exception:
                    fixture.db.rollback()
                    raise
            def execute(self, sql, parameters=()):
                fixture.calls.append((role, sql))
                if sql.startswith(('SELECT set_config', 'SET TRANSACTION')):
                    return SimpleNamespace()
                if role == 'reader' and not sql.startswith('SELECT'):
                    raise RuntimeError('synthetic read-only denial')
                return fixture.db.execute(sql.replace('%s', '?'), parameters)
        c = Connection()
        c.role = role
        try:
            yield c
        finally:
            fixture.closed.append(role)

    def qualify(self, c, registration, role):
        if role != c.role or role == self.fail_qualify or registration != REGISTRATION:
            raise RuntimeError('synthetic unauthorized principal')
        self.calls.append((role, 'qualified'))


class WorkerAuditTests(unittest.TestCase):
    def setUp(self):
        self.sql = SQLFixture()
        self.data = {}
        self.writer_container = Container(self.data)
        self.reader_container = Container(self.data)
        self.writer_arm, self.reader_arm = arm(), arm()
        self.writer_archive = VersionedWorkerArchive(self.writer_container,
            lambda: self.writer_arm, writer=True)
        self.reader_archive = VersionedWorkerArchive(self.reader_container,
            lambda: self.reader_arm)
        self.writer = WorkerAuditWriter(REGISTRATION, lambda: self.sql.connection('writer'),
            self.sql.qualify, self.writer_archive)
        self.reader = self.new_reader()

    def tearDown(self):
        self.sql.db.close()

    def new_reader(self):
        return IndependentWorkerAuditReader(REGISTRATION, lambda: self.sql.connection('reader'),
            self.sql.qualify, self.reader_archive)

    def committed(self, evidence=INTENT):
        challenge = self.reader.prepare(evidence)
        return self.writer.record(evidence, challenge), challenge

    def assertDenied(self, call):
        with self.assertRaisesRegex(AuditError, '^owner_worker_audit_unverified$'):
            call()

    def test_fresh_challenge_commit_archive_independent_verification(self):
        committed, challenge = self.committed()
        receipt = self.reader.verify(committed, INTENT, challenge)
        self.assertEqual(receipt.binding_sha256, _digest(INTENT.payload()))
        self.assertEqual(receipt.record_sha256, sha(committed.payload))
        self.assertTrue(all((receipt.database_committed, receipt.protected_archive_verified,
            receipt.independently_verified, receipt.newly_created)))
        self.assertIn('writer', self.sql.closed)
        self.assertEqual(self.writer_container.calls[0][0], 'upload')
        self.assertFalse(self.writer_container.calls[0][2])
        reads = [c for c in self.reader_container.calls if c[0] == 'read']
        self.assertTrue(reads)
        self.assertTrue(all(c[2] == VERSION for c in reads))
        self.assertFalse(any(c[0] == 'upload' for c in self.reader_container.calls))

    def test_payload_contains_only_metadata_and_hashes(self):
        committed, _ = self.committed()
        result = json.loads(committed.payload)
        self.assertEqual(result['execution'], dict(lease_sha256=sha(WORK.lease_token.encode()), revision=1))
        self.assertNotIn(WORK.lease_token.encode(), committed.payload)
        self.assertNotIn(b'U16088571', committed.payload)
        self.assertNotIn(b'prompt_text', committed.payload)
        self.assertEqual(set(result), {'schema','registration','challenge','execution',
            'worker_evidence','binding_sha256'})

    def test_existing_row_cannot_be_prepared_as_new(self):
        self.committed()
        self.assertDenied(lambda: self.new_reader().prepare(INTENT))

    def test_readback_replay_is_not_newly_created(self):
        committed, challenge = self.committed()
        self.reader.verify(committed, INTENT, challenge)
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_reconstructed_reader_cannot_resume_pending_challenge(self):
        committed, challenge = self.committed()
        self.assertDenied(lambda: self.new_reader().verify(committed, INTENT, challenge))

    def test_writer_duplicate_has_no_archive_effect(self):
        committed, challenge = self.committed()
        before = list(self.writer_container.calls)
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertEqual(before, self.writer_container.calls)
        self.assertEqual(self.sql.db.execute('SELECT count(*) FROM owner_codex_worker_audit_events').fetchone()[0], 1)

    def test_lost_database_ack_consumes_row_without_archive(self):
        challenge = self.reader.prepare(INTENT)
        self.sql.commit_loss = True
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.sql.commit_loss = False
        self.assertFalse(self.writer_container.calls)
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertDenied(lambda: self.new_reader().prepare(INTENT))

    def test_lost_archive_ack_cannot_rewrite_or_rearm(self):
        challenge = self.reader.prepare(INTENT)
        self.writer_container.lost_ack = True
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertEqual(len(self.data), 1)
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertEqual(len([c for c in self.writer_container.calls if c[0]=='upload']), 1)

    def test_archive_conflict_fails_not_repairs(self):
        challenge = self.reader.prepare(INTENT)
        payload = envelope(INTENT, REGISTRATION, challenge)
        self.data[PREFIX+sha(payload)+'.json'] = payload
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertEqual(len(self.data), 1)

    def test_failed_readback_consumes_verification_challenge(self):
        committed, challenge = self.committed()
        self.reader_container.corrupt = True
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))
        self.reader_container.corrupt = False
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_wrong_version_rejected(self):
        committed, challenge = self.committed()
        self.reader_container.version = '2026-10-05T18:00:00.1234568Z'
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_wrong_etag_rejected(self):
        committed, challenge = self.committed()
        self.reader_container.etag = '"different"'
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_wrong_size_rejected(self):
        committed, challenge = self.committed()
        self.reader_container.size_delta = 1
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_reader_unprotected_scope_rejected(self):
        for properties in (dict(id=ARM_SCOPE+'/wrong', properties=arm()['properties']),
            dict(id=ARM_SCOPE, properties=dict(publicAccess='Blob', hasLegalHold=True)),
            dict(id=ARM_SCOPE, properties=dict(publicAccess='None', hasLegalHold=False)),
            dict(id=ARM_SCOPE, properties=dict(publicAccess='None', hasLegalHold=True, deleted=True)),
            dict(id=ARM_SCOPE, properties=dict(publicAccess='None',hasLegalHold=True,
                legalHold=dict(protectedAppendWritesHistory=dict(allowProtectedAppendWritesAll=True))))):
            with self.subTest(properties=properties):
                fresh = self.new_reader()
                # Row exists: test archive independently; no new receipt issued.
                self.reader_arm = properties
                reference = ArchiveVersion(PREFIX+sha(b'x')+'.json',VERSION,'"one"',1,sha(b'x'))
                self.assertDenied(lambda: self.reader_archive.read(reference, b'x'))

    def test_writer_unprotected_scope_rejected_after_commit(self):
        challenge = self.reader.prepare(INTENT)
        self.writer_arm['properties']['hasLegalHold'] = False
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertFalse(self.writer_container.calls)
        self.assertDenied(lambda: self.new_reader().prepare(INTENT))

    def test_writer_and_reader_identity_must_differ(self):
        for field, value in (('writer_principal_id',uid(9)),('verifier_principal_id',uid(7))):
            with self.subTest(field=field):
                self.assertDenied(lambda: replace(REGISTRATION, **{field:value}))

    def test_unregistered_worker_and_principal_rejected_before_sql(self):
        challenge = self.reader.prepare(INTENT)
        before = list(self.sql.calls)
        for evidence in (replace(INTENT, worker_service_principal_id=uid(10)),
            replace(INTENT, work=replace(WORK, worker_id=uid(11)))):
            with self.subTest(evidence=evidence):
                self.assertDenied(lambda: self.writer.record(evidence, challenge))
        self.assertEqual(before, self.sql.calls)

    def test_lease_and_revision_rebinding_rejected(self):
        committed, challenge = self.committed()
        for work in (replace(WORK, lease_token=uid(12)),replace(WORK, revision=2)):
            with self.subTest(work=work):
                fresh = self.new_reader()
                self.assertDenied(lambda: fresh.verify(committed,replace(INTENT,work=work),challenge))

    def test_wrong_nonce_cannot_attest(self):
        committed, challenge = self.committed()
        forged = replace(challenge, nonce=uid(15))
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, forged))

    def test_wrong_database_row_rejected(self):
        committed, challenge = self.committed()
        self.sql.db.execute("UPDATE owner_codex_worker_audit_events SET record_sha256=?",('a'*64,))
        self.sql.db.commit()
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_missing_database_row_rejected(self):
        committed, challenge = self.committed()
        self.sql.db.execute('DELETE FROM owner_codex_worker_audit_events')
        self.sql.db.commit()
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_reader_authority_revocation_rejected(self):
        committed, challenge = self.committed()
        self.sql.fail_qualify = 'reader'
        self.assertDenied(lambda: self.reader.verify(committed, INTENT, challenge))

    def test_writer_authority_revocation_rejected(self):
        challenge = self.reader.prepare(INTENT)
        self.sql.fail_qualify = 'writer'
        self.assertDenied(lambda: self.writer.record(INTENT, challenge))
        self.assertFalse(self.writer_container.calls)

    def test_open_transaction_rejected(self):
        self.sql.busy = True
        self.assertDenied(lambda: self.reader.prepare(INTENT))

    def test_reader_cannot_upload(self):
        self.assertDenied(lambda: self.reader_archive.create(b'x'))
        self.assertFalse(self.reader_container.calls)

    def test_wrong_archive_account_rejected(self):
        self.reader_container.account_name = 'wrong'
        self.assertDenied(lambda: VersionedWorkerArchive(self.reader_container, arm))

    def test_wrong_archive_container_rejected(self):
        self.reader_container.container_name = 'wrong'
        self.assertDenied(lambda: VersionedWorkerArchive(self.reader_container, arm))

    def test_archive_reference_exact_shape(self):
        for changes in (dict(name='arbitrary.json'),dict(version_id='latest'),dict(size=True),
            dict(size=16385),dict(etag='\n'),dict(sha256='A'*64)):
            with self.subTest(changes=changes):
                values=dict(name=PREFIX+sha(b'x')+'.json',version_id=VERSION,etag='"one"',size=1,sha256=sha(b'x'))
                values.update(changes)
                self.assertDenied(lambda: ArchiveVersion(**values))

    def test_outcome_has_separate_logical_phase_not_new_launch(self):
        committed, challenge = self.committed()
        receipt = self.reader.verify(committed, INTENT, challenge)
        outcome = WorkerEvidence(WORK,'outcome','failed','observation_failed','not_started',False,
            'c'*64,'f'*64,uid(7),receipt.record_sha256)
        recorded, challenge2 = self.committed(outcome)
        result = self.reader.verify(recorded,outcome,challenge2)
        self.assertNotEqual(result.record_sha256, receipt.record_sha256)
        self.assertEqual(self.sql.db.execute('SELECT count(*) FROM owner_codex_worker_audit_events').fetchone()[0],2)

    def test_pre_authorization_denial_can_be_audited_without_grant(self):
        denied = WorkerEvidence(WORK,'outcome','denied','access_denied','not_started',False)
        committed, challenge = self.committed(denied)
        receipt = self.reader.verify(committed,denied,challenge)
        self.assertTrue(receipt.independently_verified)
        self.assertIsNone(json.loads(committed.payload)['worker_evidence']['worker_service_principal_id'])
        self.assertFalse(json.loads(committed.payload)['worker_evidence']['tools_authorized'])

    def test_wrong_registered_build_rejected(self):
        committed, challenge = self.committed()
        forged = replace(committed, registration=replace(REGISTRATION,source_manifest_sha256='e'*64))
        self.assertDenied(lambda: self.reader.verify(forged,INTENT,challenge))

    def test_challenge_cannot_cross_task_or_attempt(self):
        challenge = self.reader.prepare(INTENT)
        for work in (replace(WORK,task_id=uid(22)),replace(WORK,attempt_id=uid(23))):
            with self.subTest(work=work):
                self.assertDenied(lambda: self.writer.record(replace(INTENT,work=work),challenge))
        self.assertFalse(self.writer_container.calls)

    def test_second_challenge_for_same_pending_scope_rejected(self):
        self.reader.prepare(INTENT)
        self.assertDenied(lambda: self.reader.prepare(INTENT))

    def test_reader_mode_required_not_writer_readback_substitute(self):
        self.assertDenied(lambda: IndependentWorkerAuditReader(REGISTRATION,
            lambda: self.sql.connection('writer'),self.sql.qualify,self.writer_archive))

    def test_schema_is_append_only_forced_rls_outside_migrations(self):
        root=Path(__file__).resolve().parents[1]
        ddl=(root/'scripts/owner_task_worker_audit_schema.sql').read_text()
        self.assertIn('FORCE ROW LEVEL SECURITY',ddl)
        self.assertIn('BEFORE UPDATE OR DELETE OR TRUNCATE',ddl)
        self.assertIn('PRIMARY KEY (tenant_id,owner_object_id,task_id,phase)',ddl)
        self.assertNotIn('CREATE ROLE',ddl)
        self.assertNotIn('GRANT ',ddl)
        self.assertFalse((root/'migrations/postgres/owner_task_worker_audit_schema.sql').exists())


class SourceCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.before={f'old-{i}.py':'a'*64 for i in range(817)}
        self.after=dict(self.before,**{p:'b'*64 for p in checkpoint.ADDITIONS})

    def test_exact_four_additions_only(self):
        checkpoint.source_extension(self.before,self.after)
        self.assertEqual(len(self.after),821)

    def test_predecessor_hash_cannot_change(self):
        self.after['old-0.py']='c'*64
        with self.assertRaises(ValueError):
            checkpoint.source_extension(self.before,self.after)

    def test_unreviewed_addition_rejected(self):
        self.after['extra.py']='d'*64
        with self.assertRaises(ValueError):
            checkpoint.source_extension(self.before,self.after)

    def test_missing_addition_rejected(self):
        self.after.pop(next(iter(checkpoint.ADDITIONS)))
        with self.assertRaises(ValueError):
            checkpoint.source_extension(self.before,self.after)

    def test_bad_manifest_hash_rejected(self):
        self.after[next(iter(checkpoint.ADDITIONS))]='not-a-sha'
        with self.assertRaises(ValueError):
            checkpoint.source_extension(self.before,self.after)

    def test_detached_guard_preserves_original_namespace(self):
        original=checkpoint.candidate.controller_namespace()
        function=original['guards']
        detached=checkpoint.detached_guard(original)
        self.assertIs(original['guards'],function)
        self.assertNotIn('audit_source_extension',original)
        self.assertIs(detached.__globals__['audit_source_extension'],checkpoint.source_extension)
        self.assertIsNot(detached.__globals__,original)

    def test_checkpoint_has_no_effect_dispatch_api(self):
        self.assertFalse(hasattr(checkpoint,'dispatch'))
        self.assertFalse(hasattr(checkpoint,'start'))
        self.assertFalse(hasattr(checkpoint,'install'))


if __name__ == '__main__':
    unittest.main()
