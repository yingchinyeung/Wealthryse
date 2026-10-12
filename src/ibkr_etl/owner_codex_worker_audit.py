"""Separate Step 3 worker audit contract; inert until trusted service wiring.

This is NOT enrollment in the existing daily specialist ledger, a runtime
activation, or proof of live identity qualification. Production composition
must qualify separate restricted SQL and archive writer/reader identities.
No model text, source values, URLs, credentials or caller-selected destinations
are accepted. Failed/uncertain writes are never repaired or retried here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import uuid

from azure.core import MatchConditions

from .owner_codex_worker import (
    AUDIT_NAMESPACE, ProtectedWorkerReceipt, WorkerEvidence, _digest,
)

SCHEMA = 'owner-codex-worker-audit-envelope-v1'
ACCOUNT = 'ibkrv2stgauhecwc4itaoo'
CONTAINER = 'specialist-audit-checkpoints'
ARM_SCOPE = ('/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/'
    'resourceGroups/rg-ibkr-database-v11/providers/Microsoft.Storage/storageAccounts/'
    + ACCOUNT + '/blobServices/default/containers/' + CONTAINER)
PREFIX = 'owner-worker-v1/'
MAX_BYTES = 16384


class AuditError(ValueError):
    """Fixed error only: never propagate SQL/provider details or credentials."""
    def __init__(self):
        super().__init__('owner_worker_audit_unverified')


def require(value):
    if not value:
        raise AuditError()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=True, allow_nan=False).encode()


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def exact_uuid(value):
    require(type(value) is str and str(uuid.UUID(value)) == value)


@dataclass(frozen=True)
class ProducerRegistration:
    """Reviewed server composition, never a form or model-supplied manifest."""
    tenant_id: str
    worker_id: str
    service_principal_id: str
    source_manifest_sha256: str
    image_sha256: str
    writer_principal_id: str
    verifier_principal_id: str

    def __post_init__(self):
        for value in (self.tenant_id, self.worker_id, self.service_principal_id,
                      self.writer_principal_id, self.verifier_principal_id):
            exact_uuid(value)
        for value in (self.source_manifest_sha256, self.image_sha256):
            require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value))
        require(len({self.service_principal_id, self.writer_principal_id,
                     self.verifier_principal_id}) == 3)


@dataclass(frozen=True)
class VerificationChallenge:
    nonce: str
    scope_sha256: str

    def __post_init__(self):
        exact_uuid(self.nonce)
        require(type(self.scope_sha256) is str and re.fullmatch('[0-9a-f]{64}', self.scope_sha256))


def challenge_scope(evidence):
    require(type(evidence) is WorkerEvidence)
    return _digest(dict(evidence=evidence.payload(), lease_sha256=sha(evidence.work.lease_token.encode()),
        revision=evidence.work.revision))


def envelope(evidence, registration, challenge):
    require(type(evidence) is WorkerEvidence and type(registration) is ProducerRegistration)
    require(type(challenge) is VerificationChallenge and challenge.scope_sha256 == challenge_scope(evidence))
    work = evidence.work
    require(work.owner.tenant_id == registration.tenant_id
        and work.worker_id == registration.worker_id
        and (evidence.worker_service_principal_id == registration.service_principal_id
            or (evidence.worker_service_principal_id is None and evidence.phase == 'outcome'
                and evidence.inference_attempted is False and evidence.status != 'succeeded'))
        and work.owner.object_id not in {registration.writer_principal_id,
            registration.verifier_principal_id, registration.service_principal_id})
    result = dict(schema=SCHEMA, registration=registration.__dict__, challenge=challenge.__dict__,
        # Bind the lease/revision without retaining the lease token itself.
        execution=dict(lease_sha256=sha(work.lease_token.encode()), revision=work.revision),
        worker_evidence=evidence.payload(), binding_sha256=_digest(evidence.payload()))
    payload = canonical(result)
    require(0 < len(payload) <= MAX_BYTES)
    return payload


@dataclass(frozen=True)
class ArchiveVersion:
    name: str
    version_id: str
    etag: str
    size: int
    sha256: str

    def __post_init__(self):
        require(type(self.sha256) is str and re.fullmatch('[0-9a-f]{64}', self.sha256))
        require(self.name == PREFIX + self.sha256 + '.json')
        require(type(self.version_id) is str and re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', self.version_id))
        require(type(self.etag) is str and 0 < len(self.etag) <= 128
            and all(ord(c) >= 32 for c in self.etag))
        require(type(self.size) is int and 0 < self.size <= MAX_BYTES)


class VersionedWorkerArchive:
    """Fixed destination; use DIFFERENT instances/identities for write and read.

    Supplied clients must be independently authenticated in trusted composition.
    Client account strings are destination checks, not authentication evidence.
    Reader mode has no public upload/repair API usable by the verifier.
    """
    def __init__(self, container, read_arm, *, writer=False):
        require(container.account_name == ACCOUNT and container.container_name == CONTAINER
            and type(writer) is bool and callable(read_arm))
        self._container, self._arm, self._writer = container, read_arm, writer

    def _protection(self):
        value = self._arm()
        require(type(value) is dict and value.get('id', '').casefold() == ARM_SCOPE.casefold())
        p = value['properties']
        require(p.get('publicAccess') == 'None' and p.get('hasLegalHold') is True
            and p.get('deleted') in (None, False)
            and p.get('legalHold', {}).get('protectedAppendWritesHistory', {}).get(
                'allowProtectedAppendWritesAll') in (None, False))

    def create(self, payload):
        try:
            require(self._writer and type(payload) is bytes and 0 < len(payload) <= MAX_BYTES)
            self._protection()
            digest = sha(payload)
            name = PREFIX + digest + '.json'
            blob = self._container.get_blob_client(name)
            # No ResourceExists repair. A conflict or lost acknowledgement is
            # consumed evidence, NOT a newly-created launch receipt.
            reply = blob.upload_blob(payload, overwrite=False)
            reference = ArchiveVersion(name, reply['version_id'], reply['etag'], len(payload), digest)
            self.read(reference, payload)
            return reference
        except Exception:
            raise AuditError() from None

    def read(self, reference, expected):
        try:
            require(type(reference) is ArchiveVersion and type(expected) is bytes
                and len(expected) == reference.size and sha(expected) == reference.sha256)
            self._protection()
            # Never read "latest" and never accept a different version or ETag.
            blob = self._container.get_blob_client(reference.name, version_id=reference.version_id)
            p = blob.get_blob_properties()
            require(p.version_id == reference.version_id and p.etag == reference.etag
                and p.size == reference.size)
            data = blob.download_blob(offset=0, length=reference.size + 1,
                etag=reference.etag, match_condition=MatchConditions.IfNotModified).readall()
            require(data == expected)
            self._protection()
        except Exception:
            raise AuditError() from None


@dataclass(frozen=True)
class CommittedAudit:
    """Transport descriptor, not proof of commit. The reader rechecks SQL."""
    registration: ProducerRegistration
    payload: bytes
    archive: ArchiveVersion

    def __post_init__(self):
        require(type(self.registration) is ProducerRegistration and type(self.payload) is bytes
            and type(self.archive) is ArchiveVersion and sha(self.payload) == self.archive.sha256
            and len(self.payload) == self.archive.size)


class WorkerAuditWriter:
    """Fresh dedicated SQL connection; qualifier is trusted, never agent input.

    qualify_session(connection, registration, 'writer'/'reader') must establish
    the separately enrolled restricted principal, certificate/hostname TLS,
    exact database/schema, forced tenant/worker RLS and least-privilege ACLs.
    A writer acknowledgement alone never becomes a ProtectedWorkerReceipt.
    """
    def __init__(self, registration, connection_factory, qualify_session, archive):
        require(type(registration) is ProducerRegistration and callable(connection_factory)
            and callable(qualify_session) and type(archive) is VersionedWorkerArchive
            and archive._writer is True)
        self.registration, self._connections = registration, connection_factory
        self._qualify, self._archive = qualify_session, archive

    def record(self, evidence, challenge):
        try:
            payload = envelope(evidence, self.registration, challenge)
            w = evidence.work
            with self._connections() as c:
                require(c.autocommit is True and c.info.transaction_status == 0)
                self._qualify(c, self.registration, 'writer')
                require(c.autocommit is True and c.info.transaction_status == 0)
                with c.transaction():
                    c.execute("SELECT set_config('app.tenant_id', %s, true)", (w.owner.tenant_id,))
                    c.execute("SELECT set_config('app.execution_worker_id', %s, true)", (w.worker_id,))
                    created = c.execute('INSERT INTO owner_codex_worker_audit_events '
                        '(tenant_id,owner_object_id,task_id,phase,worker_id,attempt_id,'
                        'binding_sha256,record_sha256,envelope_json) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) '
                        'ON CONFLICT (tenant_id,owner_object_id,task_id,phase) DO NOTHING',
                        (w.owner.tenant_id,w.owner.object_id,w.task_id,evidence.phase,w.worker_id,
                         w.attempt_id,_digest(evidence.payload()),sha(payload),payload.decode())).rowcount
                    require(created == 1)
            # Exit committed SQL scope and close its connection BEFORE archival.
            reference = self._archive.create(payload)
            return CommittedAudit(self.registration, payload, reference)
        except Exception:
            raise AuditError() from None


class IndependentWorkerAuditReader:
    """Fresh read-only SQL and pinned protected bytes; no append or repair.

    The separately deployed verifier authenticates its own identity, not a
    writer-supplied Boolean. This adapter grants no execution or result delivery.
    The durable fence must still reject receipt replay before any model launch.
    """
    def __init__(self, registration, connection_factory, qualify_session, archive):
        require(type(registration) is ProducerRegistration and callable(connection_factory)
            and callable(qualify_session) and type(archive) is VersionedWorkerArchive
            and archive._writer is False)
        self.registration, self._connections = registration, connection_factory
        self._qualify, self._archive = qualify_session, archive
        self._pending = {}

    def _row(self, evidence):
        w = evidence.work
        with self._connections() as c:
            require(c.autocommit is True and c.info.transaction_status == 0)
            self._qualify(c, self.registration, 'reader')
            require(c.autocommit is True and c.info.transaction_status == 0)
            with c.transaction():
                c.execute('SET TRANSACTION READ ONLY')
                c.execute("SELECT set_config('app.tenant_id', %s, true)", (w.owner.tenant_id,))
                c.execute("SELECT set_config('app.execution_worker_id', %s, true)", (w.worker_id,))
                return c.execute('SELECT worker_id,attempt_id,binding_sha256,record_sha256,envelope_json '
                    'FROM owner_codex_worker_audit_events '
                    'WHERE tenant_id=%s AND owner_object_id=%s AND task_id=%s AND phase=%s',
                    (w.owner.tenant_id,w.owner.object_id,w.task_id,evidence.phase)).fetchone()

    def prepare(self, evidence):
        """Fresh independent absent-row check BEFORE the writer's single try.

        A restart loses the local challenge and cannot resume it. The SQL task
        fence still consumes the task durably. No historical receipt can be
        relabelled newly-created by equality/readback alone.
        """
        try:
            scope = challenge_scope(evidence)
            require(len(self._pending) < 1000 and scope not in self._pending)
            challenge = VerificationChallenge(str(uuid.uuid4()), scope)
            envelope(evidence, self.registration, challenge)
            require(self._row(evidence) is None)
            self._pending[scope] = challenge
            return challenge
        except Exception:
            raise AuditError() from None

    def verify(self, committed, evidence, challenge):
        try:
            require(type(challenge) is VerificationChallenge)
            # Consume BEFORE reading: a lost verification acknowledgement is
            # not a retry grant, even in this same process.
            pending = self._pending.pop(challenge.scope_sha256, None)
            require(pending == challenge)
            expected = envelope(evidence, self.registration, challenge)
            require(type(committed) is CommittedAudit and committed.registration == self.registration
                and committed.payload == expected)
            w = evidence.work
            row = self._row(evidence)
            require(row is not None and str(row['worker_id']) == w.worker_id
                and str(row['attempt_id']) == w.attempt_id
                and row['binding_sha256'] == _digest(evidence.payload())
                and row['record_sha256'] == sha(expected) and row['envelope_json'] == expected.decode())
            self._archive.read(committed.archive, expected)
            return ProtectedWorkerReceipt(sha(expected), _digest(evidence.payload()),
                AUDIT_NAMESPACE, True, True, True, True)
        except Exception:
            raise AuditError() from None
