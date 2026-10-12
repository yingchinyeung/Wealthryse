import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from azure.core.exceptions import ResourceExistsError
from ibkr_etl.specialist_audit_service import (
    ProtectedAuditArchive, archive_key, commit_and_archive, validate_archive_properties)

TENANT = '00000000-0000-0000-0000-000000000001'


class Blob:
    def __init__(self): self.data = None; self.overwrite = None
    def upload_blob(self, data, overwrite):
        self.overwrite = overwrite
        if self.data is not None: raise ResourceExistsError('exists')
        self.data = data
    def get_blob_properties(self): return SimpleNamespace(size=len(self.data), etag='retained')
    def download_blob(self, **kwargs): return SimpleNamespace(readall=lambda: self.data)


class Container:
    container_name = 'specialist-audit-checkpoints'
    def __init__(self): self.blobs = {}
    def get_blob_client(self, name): return self.blobs.setdefault(name, Blob())


class Connection:
    autocommit = True
    info = SimpleNamespace(transaction_status=0)
    def __init__(self): self.committed = False
    @contextmanager
    def transaction(self):
        yield
        self.committed = True


class AuditServiceTests(unittest.TestCase):
    def setUp(self):
        self.client = Container()
        self.props = {'properties': {'publicAccess': 'None', 'hasLegalHold': True}}
        self.archive = ProtectedAuditArchive(self.client, lambda: self.props)
        self.event = {'sequence': 1, 'event_id': 'one', 'sha256': 'a'*64}

    def test_create_only_and_exact_retry(self):
        first = self.archive.put(TENANT, self.event)
        self.assertEqual(first, self.archive.put(TENANT, self.event))
        self.assertFalse(next(iter(self.client.blobs.values())).overwrite)

    def test_report_verifier_reads_without_upload_or_repair(self):
        self.archive.put(TENANT,self.event)
        blob=next(iter(self.client.blobs.values()))
        with patch.object(blob,'upload_blob',side_effect=AssertionError('must not write')):
            self.assertEqual(self.archive.verify(TENANT,self.event)['etag'],'retained')

    def test_report_verifier_rejects_modified_retained_bytes(self):
        self.archive.put(TENANT,self.event)
        blob=next(iter(self.client.blobs.values()))
        blob.data=b'x'*len(blob.data)
        with self.assertRaises(ValueError): self.archive.verify(TENANT,self.event)

    def test_collision_cannot_replace_history(self):
        self.archive.put(TENANT, self.event)
        before = next(iter(self.client.blobs.values())).data
        with self.assertRaises(ValueError): self.archive.put(TENANT, {**self.event, 'sha256': 'b'*64})
        self.assertEqual(before, next(iter(self.client.blobs.values())).data)

    def test_wrong_size_fails_closed(self):
        self.client.get_blob_client(archive_key(TENANT, self.event)).data = b'bad'
        with self.assertRaises(ValueError): self.archive.put(TENANT, self.event)

    def test_unprotected_or_public_archive_denied(self):
        for changes in [{'hasLegalHold': False}, {'publicAccess': 'Blob'}, {'deleted': True},
                        {'legalHold': {'protectedAppendWritesHistory': {'allowProtectedAppendWritesAll': True}}}]:
            with self.assertRaises(ValueError): validate_archive_properties({'properties': {**self.props['properties'], **changes}})

    def test_db_commit_precedes_archive_and_does_not_authorize_execution(self):
        connection = Connection()
        class Archive:
            def put(_, tenant, event):
                self.assertTrue(connection.committed)
                return {'verified': True}
        with patch('ibkr_etl.specialist_audit_service.append_event', return_value=self.event):
            result = commit_and_archive(connection, Archive(), tenant_id=TENANT)
        self.assertFalse(result['execution_authorized'])

    def test_archive_failure_is_not_success(self):
        connection = Connection()
        class Archive:
            def put(*args): raise ConnectionError('unavailable')
        with patch('ibkr_etl.specialist_audit_service.append_event', return_value=self.event):
            with self.assertRaises(ConnectionError): commit_and_archive(connection, Archive(), tenant_id=TENANT)
        self.assertTrue(connection.committed)

    def test_nested_or_uncommitted_connection_rejected(self):
        for auto, status in [(False, 0), (True, 2)]:
            c = Connection(); c.autocommit = auto; c.info = SimpleNamespace(transaction_status=status)
            with self.assertRaises(ValueError): commit_and_archive(c, self.archive, tenant_id=TENANT)

    def test_archive_protection_rechecked_after_upload(self):
        responses = iter([self.props, {'properties': {'publicAccess': 'None', 'hasLegalHold': False}}])
        archive = ProtectedAuditArchive(self.client, lambda: next(responses))
        with self.assertRaises(ValueError): archive.put(TENANT, self.event)

    def test_invalid_tenant_or_sequence_rejected(self):
        for tenant, sequence in [('bad', 1), (TENANT, True), (TENANT, 0)]:
            with self.assertRaises(ValueError): archive_key(tenant, {'sequence': sequence})
