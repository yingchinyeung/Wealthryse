import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch

from ibkr_etl import specialist_archive_reader as reader
from ibkr_etl.util import canonical_json


class ArchiveReaderTests(unittest.TestCase):
    def setUp(self):
        self.container=Mock(container_name=reader.CONTAINER,account_name=reader.ACCOUNT)
        self.blob=self.container.get_blob_client.return_value
        self.data=b'retained receipt'
        self.blob.get_blob_properties.return_value=SimpleNamespace(size=len(self.data),etag='exact',version_id=reader.CANARY_VERSION)
        self.blob.download_blob.return_value.readall.return_value=self.data
        self.props={'id':reader.SCOPE,'properties':{'publicAccess':'None','hasLegalHold':True}}
        self.arm=Mock(return_value=self.props)
        self.archive=reader.ReportingArchiveReader(self.container,self.arm)

    def test_readiness_checks_protection_and_pinned_exact_bytes_without_writes(self):
        with patch.object(reader,'CANARY_SHA256',hashlib.sha256(self.data).hexdigest()):
            self.assertTrue(self.archive.check_ready()['protected_archive_verified'])
        self.assertEqual(self.arm.call_count,2)
        self.assertEqual(self.container.get_blob_client.call_args.kwargs,{'version_id':reader.CANARY_VERSION})
        self.assertEqual(self.blob.download_blob.call_args.kwargs['etag'],'exact')
        self.blob.upload_blob.assert_not_called()
        self.assertFalse(hasattr(self.archive,'put'))

    def test_wrong_hash_size_version_or_scope_rejected(self):
        with self.assertRaises(ValueError): self.archive.check_ready()
        for props in (SimpleNamespace(size=65537),SimpleNamespace(size=0),
                      SimpleNamespace(size=len(self.data),version_id='other')):
            self.blob.get_blob_properties.return_value=props
            with self.assertRaises(ValueError): self.archive.check_ready()
        self.arm.return_value={'id':'other','properties':self.props['properties']}
        with self.assertRaises(ValueError): self.archive.check_ready()

    def test_archive_unavailable_or_unprotected_is_not_success(self):
        for changes in ({'hasLegalHold':False},{'publicAccess':'Blob'},{'deleted':True}):
            self.arm.return_value={'id':reader.SCOPE,'properties':{**self.props['properties'],**changes}}
            with self.assertRaises(ValueError): self.archive.check_ready()
        self.arm.side_effect=ConnectionError('offline')
        with self.assertRaises(ConnectionError): self.archive.check_ready()

    def test_lost_protection_after_read_rejected(self):
        self.arm.side_effect=[self.props,{'id':reader.SCOPE,'properties':{'publicAccess':'None','hasLegalHold':False}}]
        with patch.object(reader,'CANARY_SHA256',hashlib.sha256(self.data).hexdigest()):
            with self.assertRaises(ValueError): self.archive.check_ready()

    def test_event_is_exact_bounded_read_with_no_repair(self):
        tenant='00000000-0000-0000-0000-000000000001'; event={'sequence':1}
        data=canonical_json({'tenant_id':tenant,'event':event}).encode()
        self.blob.get_blob_properties.return_value=SimpleNamespace(size=len(data),etag='event')
        self.blob.download_blob.return_value.readall.return_value=data
        self.assertTrue(self.archive.verify(tenant,event)['protected_archive_verified'])
        self.blob.download_blob.return_value.readall.return_value=b'x'*len(data)
        with self.assertRaises(ValueError): self.archive.verify(tenant,event)
        self.blob.upload_blob.assert_not_called()

    def test_unregistered_destination_rejected(self):
        for kwargs in ({'account_name':'other'},{'container_name':'ibkr-raw'}):
            c=Mock(**{**{'account_name':reader.ACCOUNT,'container_name':reader.CONTAINER},**kwargs})
            with self.assertRaises(ValueError): reader.ReportingArchiveReader(c,self.arm)

    def test_managed_identity_is_explicit_without_credential_fallback(self):
        with patch.object(reader,'ManagedIdentityCredential') as credential,patch.object(reader,'ContainerClient',return_value=self.container):
            reader.managed_reporting_archive()
        credential.assert_called_once_with(client_id=reader.CLIENT_ID)


if __name__=='__main__': unittest.main()
