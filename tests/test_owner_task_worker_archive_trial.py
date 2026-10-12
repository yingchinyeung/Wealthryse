"""Synthetic archive trial tests; no live Azure identity acceptance."""
from dataclasses import asdict, replace
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from azure.core.exceptions import HttpResponseError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import owner_task_worker_archive_trial as w
import qualify_owner_task_worker_archive_source as gate
from test_owner_codex_worker_audit import Container, Blob, arm


def uid(n):return f'00000000-0000-0000-0000-{n:012d}'
def context(mode='writer'):
    return w.Context(mode,uid(10 if mode=='writer' else 11),uid(20 if mode=='writer' else 21),
        uid(20),uid(21),'a'*64,'b'*64)


def api_error(status,code):
    error=HttpResponseError('synthetic API denial')
    error.status_code,error.error_code=status,code
    return error


class HeldBlob(Blob):
    def upload_blob(self,data,overwrite):
        self.container.calls.append(('upload',self.name,overwrite))
        if self.container.reader:raise api_error(403,'AuthorizationPermissionMismatch')
        if self.name in self.container.data:
            raise api_error(409,'BlobImmutableDueToLegalHold' if overwrite else 'BlobAlreadyExists')
        self.container.data[self.name]=data
        return dict(version_id=self.container.version,etag=self.container.etag)


class HeldContainer(Container):
    def __init__(self,data,reader=False):super().__init__(data);self.reader=reader
    def get_blob_client(self,name,version_id=None):return HeldBlob(self,name,version_id)


class FixtureBoundary:
    def __init__(self,mode,data=None):
        self.container=HeldContainer(data if data is not None else {},mode=='verifier')
        self.archive=w.VersionedWorkerArchive(self.container,self.protection,writer=mode=='writer')
        self.closed=False
    def protection(self):return arm()
    def close(self):self.closed=True


def jwt_token(ctx,**changes):
    claims=dict(tid=w.TENANT,oid=ctx.principal_id,appid=ctx.client_id,exp=2000,nbf=900)
    claims.update(changes)
    middle=base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return SimpleNamespace(token='header.'+middle+'.signature',expires_on=claims['exp'])


class ArchiveTrialTests(unittest.TestCase):
    def test_context_requires_two_distinct_registered_principals(self):
        c=context()
        for fields in (dict(principal_id=uid(99)),dict(verifier_principal_id=c.writer_principal_id),
                dict(client_id=uid(0)),dict(mode='other'),dict(image_sha256='x'),dict(source_manifest_sha256='')):
            with self.assertRaises(w.TrialError):replace(c,**fields)

    def test_writer_and_reader_derive_identical_content_without_lease_or_source_values(self):
        self.assertEqual(w.payload(context(),'normal'),w.payload(context('verifier'),'normal'))
        value=json.loads(w.payload(context(),'normal'))
        self.assertFalse(value['database_committed']);self.assertFalse(value['protected_worker_receipt'])
        self.assertFalse(value['worker_execution_authorized'])
        self.assertNotIn('lease_token',value);self.assertNotIn('prompt',value)
        with self.assertRaises(w.TrialError):w.payload(context(),'arbitrary')

    def test_normal_and_lost_ack_payloads_cannot_alias(self):
        self.assertNotEqual(w.sha(w.payload(context(),'normal')),w.sha(w.payload(context(),'lost_ack')))

    def test_write_checks_duplicate_hold_and_consumed_lost_ack(self):
        b=FixtureBoundary('writer');r=w.write(context(),b)
        self.assertEqual(len(b.container.data),2)
        self.assertEqual(r['duplicate_denial']['error_code'],'BlobAlreadyExists')
        self.assertEqual(r['overwrite_denial']['error_code'],'BlobImmutableDueToLegalHold')
        self.assertFalse(r['lost_ack_receipt_accepted']);self.assertFalse(r['lost_ack_newly_created'])
        self.assertFalse(r['lost_ack_automatic_retry'])
        self.assertNotIn(w.PROBE_NAME,b.container.data)

    def test_separate_read_only_instance_reads_pinned_versions_and_denies_creation(self):
        writer=FixtureBoundary('writer');r=w.write(context(),writer)
        reader=FixtureBoundary('verifier',writer.container.data)
        value=w.verify(context('verifier'),reader,{k:r[k] for k in ('normal','lost_ack_inspection')})
        self.assertTrue(value['exact_versions_independently_read'])
        self.assertEqual(value['reader_creation_denial'],dict(status_code=403,error_code='AuthorizationPermissionMismatch'))
        self.assertNotIn(w.PROBE_NAME,writer.container.data)
        self.assertTrue(all(call[2]==reader.container.version for call in reader.container.calls if call[0]=='read'))

    def test_writer_cannot_be_claimed_as_reader_boundary(self):
        writer=FixtureBoundary('writer');r=w.write(context(),writer)
        with self.assertRaisesRegex(w.TrialError,'worker_archive_mutation_allowed'):
            w.verify(context('verifier'),writer,{k:r[k] for k in ('normal','lost_ack_inspection')})

    def test_reference_tampering_never_reads_latest(self):
        writer=FixtureBoundary('writer');r=w.write(context(),writer)
        for fields in (dict(sha256='c'*64),dict(size=1),dict(version_id='latest'),dict(name='other'),dict(extra=True)):
            with self.assertRaises((w.TrialError,w.AuditError)):
                w.reference(dict(r['normal'],**fields),w.payload(context(),'normal'))

    def test_wrong_etag_or_bytes_are_unaccepted(self):
        b=FixtureBoundary('writer');r=w.write(context(),b)
        reader=FixtureBoundary('verifier',b.container.data);reader.container.corrupt=True
        with self.assertRaises(w.AuditError):
            w.verify(context('verifier'),reader,{k:r[k] for k in ('normal','lost_ack_inspection')})

    def test_generic_error_is_never_an_api_permission_denial(self):
        for error in (TimeoutError(),RuntimeError('sensitive'),api_error(403,'AuthenticationFailed'),
                      api_error(409,'BlobAlreadyExists'),api_error(404,'ResourceNotFound')):
            with self.assertRaises(Exception):
                w.denied(lambda:(_ for _ in ()).throw(error),{'AuthorizationPermissionMismatch'},403)

    def test_boundary_protection_is_checked_before_every_create_or_read(self):
        b=FixtureBoundary('writer');b.archive._arm=lambda:dict(id=w.ARM_SCOPE,properties=dict(publicAccess='None',hasLegalHold=False))
        with self.assertRaises(w.AuditError):w.write(context(),b)
        self.assertEqual(b.container.calls,[])

    def test_bound_credential_scopes_and_selected_claims_are_fixed(self):
        c=context();token=jwt_token(c)
        credential=SimpleNamespace(get_token=lambda *a,**kw:token)
        bound=w.BoundCredential(c,credential)
        with patch.object(w.time,'time',return_value=1000):
            self.assertIs(bound.get_token('https://storage.azure.com/.default'),token)
            with self.assertRaises(w.TrialError):bound.get_token('https://vault.azure.net/.default')

    def test_wrong_identity_expiry_or_tenant_cannot_pass_claim_binding(self):
        c=context()
        for changes in (dict(tid=uid(99)),dict(oid=uid(99)),dict(appid=uid(99)),dict(exp=1020),dict(nbf=1100)):
            credential=SimpleNamespace(get_token=lambda *a,_changes=changes,**kw:jwt_token(c,**_changes))
            with patch.object(w.time,'time',return_value=1000):
                with self.assertRaisesRegex(w.TrialError,'worker_archive_identity_unverified'):
                    w.BoundCredential(c,credential).get_token('https://storage.azure.com/.default')

    def test_no_claim_binding_is_itself_native_or_worker_receipt_acceptance(self):
        writer=FixtureBoundary('writer')
        with patch.object(w.os,'getuid',return_value=10001,create=True),patch.object(w.os,'getgid',return_value=10001,create=True),patch.dict(os.environ,{},clear=True):
            r=w.main(context(),make_boundary=lambda _:writer)
        self.assertEqual(r['status'],'synthetic_archive_boundary_verified');self.assertTrue(writer.closed)
        for key in ('database_committed','protected_worker_receipts_verified','production_evidence_registry_enrolled',
                'runtime_process_cessation_verified','website_owner_authenticated','step_3_complete','step_4_started'):
            self.assertIs(r[key],False)
        self.assertEqual(r['sql_connections'],0);self.assertEqual(r['model_calls'],0)

    def test_ambient_sql_credentials_stop_before_any_archive_effect(self):
        calls=[]
        with patch.dict(os.environ,{'DATABASE_URL':'private credential'},clear=True):
            r=w.main(context(),make_boundary=lambda _:calls.append(True))
        self.assertEqual(calls,[]);self.assertEqual(r['reason'],'worker_archive_ambient_credential_present')
        self.assertNotIn('private credential',json.dumps(r))

    def test_failed_boundary_never_leaks_provider_text_or_claims_success(self):
        b=FixtureBoundary('writer');b.archive.create=lambda _:(_ for _ in ()).throw(RuntimeError('private token'))
        with patch.object(w.os,'getuid',return_value=10001,create=True),patch.object(w.os,'getgid',return_value=10001,create=True),patch.dict(os.environ,{},clear=True):
            r=w.main(context(),make_boundary=lambda _:b)
        self.assertEqual(r['status'],'unaccepted');self.assertTrue(b.closed)
        self.assertEqual(r['reason'],'worker_archive_unverified');self.assertNotIn('private token',json.dumps(r))

    def test_failed_close_cannot_be_accepted(self):
        b=FixtureBoundary('writer');b.close=lambda:(_ for _ in ()).throw(RuntimeError())
        with patch.object(w.os,'getuid',return_value=10001,create=True),patch.object(w.os,'getgid',return_value=10001,create=True),patch.dict(os.environ,{},clear=True):
            r=w.main(context(),make_boundary=lambda _:b)
        self.assertEqual(r['status'],'unaccepted');self.assertEqual(r['reason'],'worker_archive_close_unverified')

    def test_fixed_new_scope_is_not_sql_job_or_ibkr_storage(self):
        self.assertNotIn('ownaud3',str(w.JOBS));self.assertNotIn('ibkr-raw',w.ARM_SCOPE)
        self.assertTrue(all('20261005' in x for x in w.IDENTITIES.values()))
        self.assertEqual(w.CONTAINER,'specialist-audit-checkpoints')

    def test_only_three_additions_preserve_all_841_predecessors(self):
        before={f'old-{i}.py':'a'*64 for i in range(841)}
        after=dict(before,**{p:'b'*64 for p in gate.ADDITIONS})
        self.assertTrue(gate.source_extension(before,after))
        for changed in (dict(after,**{'old-0.py':'c'*64}),dict(after,**{'extra.py':'d'*64}),
                {k:v for k,v in after.items() if k!=w.PATH}):
            with self.assertRaises(ValueError):gate.source_extension(before,changed)

    def test_read_context_does_not_mutate_frozen_functions_or_globals(self):
        previous=gate.prior
        original_control,original_baseline,original_chain=previous.control,previous.baseline,previous.chain
        n=gate.read_context()
        self.assertIs(previous.control,original_control);self.assertIs(previous.baseline,original_baseline)
        self.assertIs(previous.chain,original_chain)
        self.assertEqual(n['JOB'],previous.candidate.worker_module().JOB_ID)
        self.assertIsNot(n['guards'].__globals__,previous.control.__globals__)

    def test_owned_read_guard_changes_only_source_comparison(self):
        n=gate.read_context()
        self.assertIn('archive_source_extension',n['guards'].__code__.co_names)
        self.assertIn('native_predecessors',n['guards'].__code__.co_names)
        self.assertIn('baseline',n['guards'].__code__.co_names)

    def test_actual_archive_module_graph_imports_without_sql_or_cloud(self):
        root=Path(__file__).resolve().parents[1]
        modules=(('src/ibkr_etl/util.py','ibkr_etl.util'),('src/ibkr_etl/db.py','ibkr_etl.db'),
            ('src/ibkr_etl/owner_codex_tasks.py','ibkr_etl.owner_codex_tasks'),
            ('src/ibkr_etl/owner_codex_worker.py','ibkr_etl.owner_codex_worker'),
            ('src/ibkr_etl/owner_codex_worker_audit.py','ibkr_etl.owner_codex_worker_audit'),
            (w.PATH,'owner_task_worker_archive_trial'))
        source={path:(root/path).read_text() for path,_ in modules}
        code="import sys,types\np=types.ModuleType('ibkr_etl');p.__path__=[];sys.modules['ibkr_etl']=p\n"
        code+='_sources='+repr(source)+'\n_modules='+repr(modules)+'\n'
        code+="for path,name in _modules:\n m=types.ModuleType(name);m.__file__=path;m.__package__=name.rpartition('.')[0];sys.modules[name]=m;exec(compile(_sources[path],path,'exec'),m.__dict__)\nprint('archive_import_without_sql_or_cloud')"
        result=subprocess.run([sys.executable,'-I','-'],input=code,text=True,capture_output=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'archive_import_without_sql_or_cloud')


if __name__=='__main__':unittest.main()
