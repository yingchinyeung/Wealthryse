import copy
import hashlib
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from azure.core import MatchConditions
from ibkr_etl.protected_release_reader import EvidenceVersion, ProtectedReleaseReader, MAX_BYTES


class ProtectedReleaseReaderTests(unittest.TestCase):
    def setUp(self):
        self.data = b'{"test":"synthetic evidence only"}'
        sha = hashlib.sha256(self.data).hexdigest()
        self.receipt = EvidenceVersion('acceptance/test-' + sha + '.json',
            '2026-10-06T12:00:00.0000000Z', sha, len(self.data))
        self.scope = '/subscriptions/11111111-1111-1111-1111-111111111111/resourceGroups/test-rg/providers/Microsoft.Storage/storageAccounts/testarchive/blobServices/default/containers/release-evidence'
        self.arm = {'id': self.scope, 'properties': {'publicAccess': 'None', 'hasLegalHold': True}}
        self.read_arm = Mock(return_value=self.arm)
        self.blob = Mock()
        self.blob.get_blob_properties.return_value = SimpleNamespace(size=len(self.data),
            version_id=self.receipt.version_id, etag='synthetic-etag')
        self.blob.download_blob.return_value.readall.return_value = self.data
        self.container = Mock(account_name='testarchive', container_name='release-evidence')
        self.container.get_blob_client.return_value = self.blob
        self.reader = self.create()

    def create(self, **changes):
        args = dict(account='testarchive', container_name='release-evidence', resource_id=self.scope)
        args.update(changes)
        return ProtectedReleaseReader(self.container, self.read_arm, **args)

    def test_reads_exact_version_with_etag_and_protection_before_and_after(self):
        self.assertEqual(self.reader.read(self.receipt), self.data)
        self.assertEqual(self.read_arm.call_count, 2)
        self.container.get_blob_client.assert_called_once_with(self.receipt.blob, version_id=self.receipt.version_id)
        self.blob.download_blob.assert_called_once_with(offset=0, length=len(self.data)+1,
            etag='synthetic-etag', match_condition=MatchConditions.IfNotModified, timeout=60)
        self.blob.upload_blob.assert_not_called()
        self.blob.delete_blob.assert_not_called()

    def test_destination_mismatch_rejected(self):
        for changes in ({'account': 'otherarchive'}, {'container_name': 'other-evidence'},
                        {'resource_id': self.scope.replace('test-rg', 'other-rg') + '/extra'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.create(**changes)

    def test_no_latest_fallback_or_unbounded_receipt(self):
        for changes in ({'version_id': None}, {'version_id': ''}, {'version_id': '2026-02-30T12:00:00.0000000Z'},
                {'size': True}, {'size': 0}, {'size': MAX_BYTES+1}, {'sha256': 'a'*64},
                {'blob': '../' + self.receipt.blob}, {'blob': self.receipt.blob.replace('test-', '%2e%2e-')}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reader.read(replace(self.receipt, **changes))
        self.container.get_blob_client.assert_not_called()

    def test_incorrect_native_properties_rejected_before_content_read(self):
        for changes in ({'size': len(self.data)+1}, {'version_id': 'different'}, {'etag': None}):
            props = dict(size=len(self.data), version_id=self.receipt.version_id, etag='synthetic-etag')
            props.update(changes)
            self.blob.get_blob_properties.return_value = SimpleNamespace(**props)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reader.read(self.receipt)
        self.blob.download_blob.assert_not_called()

    def test_wrong_scope_public_or_unprotected_store_fails(self):
        for changes in ({'publicAccess': 'Blob'}, {'hasLegalHold': 1}, {'hasLegalHold': False},
                {'deleted': True}, {'legalHold': {'protectedAppendWritesHistory': {'allowProtectedAppendWritesAll': True}}}):
            record = copy.deepcopy(self.arm)
            record['properties'].update(changes)
            self.read_arm.return_value = record
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reader.read(self.receipt)
        self.read_arm.return_value = {'id': self.scope.replace('test-rg', 'wrong-rg'), 'properties': self.arm['properties']}
        with self.assertRaises(ValueError):
            self.reader.read(self.receipt)
        self.container.get_blob_client.assert_not_called()

    def test_tampered_or_truncated_bytes_rejected(self):
        for content in (b'bad', self.data+b'extra', b'x'*len(self.data)):
            self.blob.download_blob.return_value.readall.return_value = content
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.reader.read(self.receipt)

    def test_protection_revoked_during_read_fails(self):
        after = copy.deepcopy(self.arm)
        after['properties']['hasLegalHold'] = False
        self.read_arm.side_effect = [self.arm, after]
        with self.assertRaises(ValueError):
            self.reader.read(self.receipt)


if __name__ == '__main__':
    unittest.main()
