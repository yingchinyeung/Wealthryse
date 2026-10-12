import base64
import copy
import gzip
import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from azure.core.exceptions import ResourceExistsError
from ibkr_etl import specialist_capture as capture
from ibkr_etl.util import canonical_json
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import run_specialist_capture as deployment
import deploy_specialist_audit_worker as audit_deployment

OP='d8d57217-1d5e-4e34-8fe9-89b377135c72'


class Blob:
    def __init__(self): self.data=None;self.writes=0
    def upload_blob(self,payload,overwrite):
        assert overwrite is False
        if self.data is not None: raise ResourceExistsError('exists')
        self.data=payload;self.writes+=1
    def get_blob_properties(self): return SimpleNamespace(size=len(self.data),etag='exact')
    def download_blob(self,**kwargs): return SimpleNamespace(readall=lambda:self.data)


class Container:
    container_name=capture.CONTAINER
    account_name=capture.ACCOUNT
    def __init__(self): self.blobs={}
    def get_blob_client(self,name,**kwargs): return self.blobs.setdefault(name,Blob())


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.client=Container()
        self.properties={'id':capture.SCOPE,'properties':{'publicAccess':'None','hasLegalHold':True}}
        self.archive=capture.CaptureArchive(self.client,lambda:self.properties)

    def test_evidence_fixed_scope_create_only_and_exact_replay(self):
        a=self.archive.retain_inspection_evidence(OP,'started','a'*64)
        self.assertEqual(a,self.archive.retain_inspection_evidence(OP,'started','a'*64))
        blob=self.client.blobs[a['blob_name']]
        self.assertEqual(blob.writes,1)
        self.assertIn(b'trusted_manual_rollout_worker',blob.data)
        self.assertIn(capture.PRINCIPAL_ID.encode(),blob.data)
        self.assertEqual(hashlib.sha256(blob.data).hexdigest(),a['sha256'])

    def test_wrong_completion_and_phase_rejected(self):
        for phase,result in [('started',capture.EXPECTED_ACCESS),('succeeded',None),('failed',None),
                             ('succeeded',{**capture.EXPECTED_ACCESS,'source_table_access':True})]:
            with self.assertRaises(ValueError): self.archive.retain_inspection_evidence(OP,phase,'a'*64,result)
        self.assertEqual(self.client.blobs,{})

    def test_event_collision_preserves_original_and_wrong_tenant_denied(self):
        event={'sequence':1,'sha256':'a'*64}
        receipt=self.archive.put(capture.TENANT,event)
        original=self.client.blobs[receipt['blob_name']].data
        with self.assertRaises(ValueError): self.archive.put(capture.TENANT,{**event,'sha256':'b'*64})
        self.assertEqual(self.client.blobs[receipt['blob_name']].data,original)
        with self.assertRaises(ValueError): self.archive.put('other',event)

    def test_protection_loss_prevents_acknowledgement(self):
        arm=Mock(side_effect=[self.properties,{'id':capture.SCOPE,'properties':{'publicAccess':'None','hasLegalHold':False}}])
        archive=capture.CaptureArchive(self.client,arm)
        with self.assertRaises(ValueError): archive.retain_inspection_evidence(OP,'started','a'*64)

    def test_oversized_evidence_rejected_before_write(self):
        with self.assertRaises(ValueError): self.archive._retain('unused',b'x'*(capture.MAX_BYTES+1))
        self.assertEqual(self.client.blobs,{})

    def test_complete_capture_verifies_replay_and_exact_two_events(self):
        c=Mock();c.execute.return_value.fetchone.return_value={'n':2}
        archive=Mock();archive.check_ready.return_value={'protected_archive_verified':True}
        archive.retain_inspection_evidence.return_value={'sha256':'a'*64}
        def commit(conn,storage,**fields):
            phase=fields['phase'];return {'event':{'sequence':1 if phase=='started' else 2,'sha256':phase,'phase':phase}}
        with patch.object(capture,'bind_audit_session'),patch.object(capture,'verify_audit_access',return_value=capture.EXPECTED_ACCESS), \
             patch.object(capture,'commit_and_archive',side_effect=commit) as writer:
            result=capture.capture_permission_inspection(c,archive,operation_id=OP,source_manifest_sha256='b'*64)
        self.assertEqual(writer.call_count,4)
        self.assertEqual(result['operation_event_count'],2)
        self.assertFalse(result['specialist_execution_enabled'])
        self.assertTrue(all(call.kwargs['role']=='platform_operations' and call.kwargs['action']=='inspect_and_analyze' for call in writer.call_args_list))

    def test_missing_intent_archive_stops_before_action(self):
        archive=Mock();archive.check_ready.return_value={'protected_archive_verified':True}
        archive.retain_inspection_evidence.return_value={'sha256':'a'*64}
        with patch.object(capture,'bind_audit_session'),patch.object(capture,'verify_audit_access',return_value=capture.EXPECTED_ACCESS) as inspect, \
             patch.object(capture,'commit_and_archive',side_effect=ConnectionError('archive unavailable')):
            with self.assertRaises(ConnectionError):
                capture.capture_permission_inspection(Mock(),archive,operation_id=OP,source_manifest_sha256='b'*64)
        self.assertEqual(inspect.call_count,1)
        archive.retain_inspection_evidence.assert_called_once_with(OP,'started','b'*64,None)

    def test_invalid_registration_precedes_any_connection(self):
        with patch.object(capture,'bind_audit_session') as bind:
            with self.assertRaises(ValueError): capture.capture_permission_inspection(Mock(),Mock(),operation_id='not-uuid',source_manifest_sha256='b'*64)
            with self.assertRaises(ValueError): capture.capture_permission_inspection(Mock(),Mock(),operation_id=OP,source_manifest_sha256='wrong')
            bind.assert_not_called()

    def test_explicit_managed_identity_has_no_fallback(self):
        with patch.object(capture,'ManagedIdentityCredential') as credential,patch.object(capture,'ContainerClient',return_value=self.client):
            capture.managed_capture_archive()
        credential.assert_called_once_with(client_id=capture.CLIENT_ID)


class DeploymentTests(unittest.TestCase):
    def test_bounded_reviewed_override_keeps_installed_definition(self):
        job=audit_deployment.definition();before=copy.deepcopy(job)
        result=deployment.template(job,OP)
        self.assertEqual(job,before)
        self.assertEqual(result['containers'][0]['env'],[{'name':'SPECIALIST_AUDIT_DATABASE_URL','secretRef':'audit-url'}])
        args,digest,manifest=deployment.bundle(OP)
        source=gzip.decompress(base64.b64decode(args[1].split("b64decode('")[1].split("')")[0])).decode()
        compile(source,'capture','exec')
        self.assertIn(OP,source);self.assertEqual(len(digest),64);self.assertEqual(len(manifest),64)
        self.assertLess(len(args[1]),30000)
        self.assertNotIn('database-admin-url',source)

    def test_grants_cannot_expand(self):
        role=deployment.ROLES[0]
        grant={'scope':capture.SCOPE,'principalId':capture.PRINCIPAL_ID,
               'roleDefinitionId':f'/subscriptions/{deployment.SUB}/providers/Microsoft.Authorization/roleDefinitions/{role}'}
        deployment.verify_grant(grant,role)
        for changes in ({'scope':capture.SCOPE.rsplit('/',1)[0]},{'principalId':'other'},{'condition':'other'}):
            with self.assertRaises(ValueError): deployment.verify_grant({**grant,**changes},role)


if __name__=='__main__': unittest.main()
