import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
from azure.core.exceptions import HttpResponseError

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from protect_specialist_sources import (path, validate_container, verify_preserved, verify_probe,
    verify_settled_probe, ProtectionNotEffective, jobs_digest, prepare_archive_reader)


class SourceProtectionTests(unittest.TestCase):
    def test_archive_reader_grant_is_exact_and_read_back(self):
        context={'id':'3956e821-cee4-403f-8708-e84e2ed490eb',
                 'tenantId':'0578a50b-a7a1-40a4-905b-8b20dba84124',
                 'user':{'name':'anthony.ying@wealthryse.ai'}}
        observed={}
        def request(method, target, **kwargs):
            if 'roleDefinitions/' in target:
                return {'properties':{'roleName':'Storage Blob Data Reader',
                    'permissions':[{'dataActions':['Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read']}]}}
            if method=='PUT':
                observed.update(kwargs['data']['properties'])
                observed['scope']=target.split('/providers/Microsoft.Authorization/')[0]
                return {}
            if 'roleAssignments/' in target: return {'properties':observed}
            return {'properties':{'publicAccess':'None'}}
        with patch('protect_specialist_sources.az',return_value=context), \
             patch('protect_specialist_sources.Journal'), \
             patch('protect_specialist_sources.request',side_effect=request):
            prepare_archive_reader()
        self.assertEqual(observed['scope'],path('ibkrv2stgauhecwc4itaoo','ibkr-raw'))
        self.assertEqual(observed['principalId'],'8617fd1c-2280-40f2-92dd-45686e9d686c')
        self.assertTrue(observed['roleDefinitionId'].endswith('/2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'))

    def test_archive_reader_wrong_operator_cannot_write(self):
        with patch('protect_specialist_sources.az',return_value={'id':'other'}), \
             patch('protect_specialist_sources.Journal'), \
             patch('protect_specialist_sources.request') as request:
            with self.assertRaises(ValueError): prepare_archive_reader()
            request.assert_not_called()

    def test_unknown_account_and_container_rejected(self):
        for a, c in [("other", "raw-statements"), ("ibkrv115rcc62xxqblay", "analytics-reports")]:
            with self.assertRaises(ValueError): path(a, c)

    def test_private_required(self):
        for p in [{"publicAccess": "Blob"}, {"publicAccess": "None", "deleted": True},
                  {"publicAccess": "None", "immutableStorageWithVersioning": {"enabled": True}}]:
            with self.assertRaises(ValueError): validate_container({"properties": p})

    def test_old_versions_must_remain_unchanged(self):
        before = [{"name": "one", "version": "1", "etag": "original", "bytes": 4}]
        for after in [[], [{**before[0], "etag": "changed"}], [{**before[0], "version": "2"}]]:
            with self.assertRaises(ValueError): verify_preserved(before, after)

    def test_new_arrivals_allowed(self):
        before = [{"name": "one", "version": "1", "etag": "original", "bytes": 4}]
        verify_preserved(before, before + [{"name": "two", "version": "2", "etag": "new", "bytes": 5}])

    def test_permission_failure_is_not_proof_of_immutability(self):
        client=Mock(); error=HttpResponseError('forbidden'); error.status_code=403; error.error_code='AuthorizationPermissionMismatch'
        client.get_blob_client.return_value.upload_blob.side_effect=error
        with self.assertRaises(RuntimeError): verify_probe(client,'synthetic',b'test',Mock())

    def test_successful_overwrite_stops_rollout(self):
        with self.assertRaises(RuntimeError): verify_probe(Mock(),'synthetic',b'test',Mock())

    def test_propagation_retry_uses_fresh_probe_and_preserves_fail_closed(self):
        client=Mock(account_name='ibkrv115rcc62xxqblay', container_name='source-protection-probes')
        with patch('protect_specialist_sources.verify_probe', side_effect=[ProtectionNotEffective(), None]) as check:
            verify_settled_probe(client,'synthetic',b'test',Mock(),delay=0)
            self.assertEqual(check.call_args_list[1].args[1], 'synthetic.settle-1')
            self.assertFalse(client.get_blob_client.return_value.upload_blob.call_args.kwargs['overwrite'])
        with patch('protect_specialist_sources.verify_probe', side_effect=ProtectionNotEffective()) as check:
            with self.assertRaises(ProtectionNotEffective):
                verify_settled_probe(client,'synthetic',b'test',Mock(),delay=0)
            self.assertEqual(check.call_count,4)

    def test_propagation_never_retries_other_failures_or_source_containers(self):
        client=Mock(account_name='ibkrv115rcc62xxqblay', container_name='source-protection-probes')
        with patch('protect_specialist_sources.verify_probe', side_effect=ValueError()) as check:
            with self.assertRaises(ValueError): verify_settled_probe(client,'synthetic',b'test',Mock(),delay=0)
            self.assertEqual(check.call_count,1)
        client.container_name='raw-statements'
        with self.assertRaises(ValueError): verify_settled_probe(client,'synthetic',b'test',Mock(),delay=0)
        client.get_blob_client.assert_not_called()

    def test_protection_requires_both_denials_and_successful_new_write(self):
        client=Mock(); blob=Mock(); second=Mock(); client.get_blob_client.side_effect=[blob,second]
        error=HttpResponseError('protected'); error.status_code=409; error.error_code='BlobImmutableDueToLegalHold'
        blob.upload_blob.side_effect=error; blob.delete_blob.side_effect=error
        blob.download_blob.return_value.readall.return_value=b'test'
        second.download_blob.return_value.readall.return_value=b'test'
        verify_probe(client,'synthetic',b'test',Mock())
        self.assertFalse(second.upload_blob.call_args.kwargs['overwrite'])

    def test_job_fingerprint_ignores_runtime_status_not_configuration(self):
        job={'name':'one','properties':{'configuration':{'retry':0},'template':{},'status':'Running'}}
        same={**job,'properties':{**job['properties'],'status':'Succeeded'}}
        self.assertEqual(jobs_digest([job]),jobs_digest([same]))
        changed={**job,'properties':{**job['properties'],'configuration':{'retry':1}}}
        self.assertNotEqual(jobs_digest([job]),jobs_digest([changed]))
