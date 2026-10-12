"""Private archive qualification only; never a committed worker-task receipt.

Run on separately registered writer/verifier compute. Trusted deployment must
bind context to native identities, roles, image, sources and one-shot starts.
No SQL, model, owner authentication, ledger enrollment or agent activation.
Only the new operation's synthetic content-addressed archive/probe objects may
be created. Failed/uncertain effects have no retry or repair path.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import base64
import hashlib
import json
import os
import re
import time
import uuid

from azure.core.exceptions import HttpResponseError, ResourceExistsError
from azure.core.pipeline.transport import RequestsTransport
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient
import requests
from requests.adapters import HTTPAdapter

from ibkr_etl.owner_codex_worker_audit import (
    ACCOUNT, ARM_SCOPE, ArchiveVersion, AuditError, CONTAINER,
    VersionedWorkerArchive, canonical, sha,
)

OPERATION = '7e9f3535-9caf-4f6f-8dd7-14b5ddb10efc'
TENANT = '0578a50b-a7a1-40a4-905b-8b20dba84124'
BASE = '/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/'
IDENTITIES = {
    'writer': BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ownaw-20261005',
    'verifier': BASE+'Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ownar-20261005',
}
JOBS = {
    'writer': BASE+'Microsoft.App/jobs/ibkrv2stg-ownaw-20261005',
    'verifier': BASE+'Microsoft.App/jobs/ibkrv2stg-ownar-20261005',
}
PREFIX = 'OWNER_TASK_WORKER_ARCHIVE_TRIAL_V1 '
PATH = 'scripts/owner_task_worker_archive_trial.py'
PROBE_NAME = 'owner-worker-archive-probes-v1/'+OPERATION+'-reader-denial.json'
FORBIDDEN_ENV = frozenset(('OWNERTRIAL_ADMIN_URL','DATABASE_URL','DATABASE_ADMIN_URL',
    'PGPASSWORD','PGPASSFILE','PGSERVICEFILE','AZURE_CLIENT_SECRET','OPENAI_API_KEY'))


class TrialError(ValueError):
    def __init__(self, code='worker_archive_unverified'):
        super().__init__(code if type(code) is str and re.fullmatch('worker_archive_[a-z_]+',code)
            else 'worker_archive_unverified')


def require(value, code='worker_archive_unverified'):
    if not value:
        raise TrialError(code)


def guid(value):
    try:
        require(type(value) is str and str(uuid.UUID(value))==value
            and value!='00000000-0000-0000-0000-000000000000')
    except Exception:
        raise TrialError() from None


@dataclass(frozen=True)
class Context:
    mode: str
    client_id: str
    principal_id: str
    writer_principal_id: str
    verifier_principal_id: str
    source_manifest_sha256: str
    image_sha256: str

    def __post_init__(self):
        require(type(self.mode) is str and self.mode in IDENTITIES)
        for value in (self.client_id,self.principal_id,self.writer_principal_id,self.verifier_principal_id):
            guid(value)
        require(self.writer_principal_id!=self.verifier_principal_id
            and self.principal_id==(self.writer_principal_id if self.mode=='writer' else self.verifier_principal_id))
        for value in (self.source_manifest_sha256,self.image_sha256):
            require(type(value) is str and re.fullmatch('[0-9a-f]{64}',value))


def payload(context, kind):
    require(type(context) is Context and type(kind) is str and kind in ('normal','lost_ack'))
    return canonical(dict(schema='owner-worker-archive-only-trial-v1',operation_id=OPERATION,
        kind=kind,writer_principal_id=context.writer_principal_id,
        verifier_principal_id=context.verifier_principal_id,
        source_manifest_sha256=context.source_manifest_sha256,image_sha256=context.image_sha256,
        synthetic_only=True,database_committed=False,worker_execution_authorized=False,
        protected_worker_receipt=False,source_rows_read=0,source_rows_written=0,model_calls=0))


def reference(value, expected):
    require(type(value) is dict and set(value)==set(ArchiveVersion.__dataclass_fields__))
    item=ArchiveVersion(**value)
    require(item.sha256==sha(expected) and item.size==len(expected))
    return item


class BoundCredential:
    """Select a registered MI. Claims alone are NOT authentication acceptance.

    Azure ARM/Blob API calls must actually validate the token and role scope;
    the separate controller must independently validate native assignments.
    Tokens and unselected claims are never emitted or stored.
    """
    def __init__(self, context, credential):
        require(type(context) is Context)
        self.context,self.credential=context,credential

    def get_token(self,*scopes,**kwargs):
        require(scopes in (('https://management.azure.com/.default',),('https://storage.azure.com/.default',)))
        token=self.credential.get_token(*scopes,**kwargs)
        try:
            parts=token.token.split('.')
            require(len(parts)==3 and all(parts) and len(token.token)<=32768)
            value=json.loads(base64.urlsafe_b64decode(parts[1]+'='*((-len(parts[1]))%4)))
            now=time.time()
            require(value['tid']==TENANT and value['oid']==self.context.principal_id
                and value.get('appid',value.get('azp'))==self.context.client_id
                and type(value['exp']) is int and value['exp']==token.expires_on
                and now+30<value['exp']<=now+86400
                and type(value.get('nbf')) is int and value['nbf']<=now+30)
            return token
        except Exception:
            raise TrialError('worker_archive_identity_unverified') from None


class Boundary:
    def __init__(self, context, credential):
        self.credential=BoundCredential(context,credential)
        self.session=requests.Session();self.session.trust_env=False
        self.session.mount('https://',HTTPAdapter(max_retries=0))
        self.service=BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net',
            credential=self.credential,retry_total=0,connection_timeout=5,read_timeout=30,
            transport=RequestsTransport(session=self.session,session_owner=False))
        self.container=self.service.get_container_client(CONTAINER)
        self.archive=VersionedWorkerArchive(self.container,self.protection,writer=context.mode=='writer')

    def protection(self):
        token=self.credential.get_token('https://management.azure.com/.default')
        with self.session.get('https://management.azure.com'+ARM_SCOPE,
            params={'api-version':'2023-05-01'},headers={'Authorization':'Bearer '+token.token},
            allow_redirects=False,stream=True,timeout=(5,30)) as response:
            require(response.status_code==200,'worker_archive_protection_unverified')
            raw=bytearray()
            for chunk in response.iter_content(8192):
                raw.extend(chunk);require(len(raw)<=65536)
            value=json.loads(raw)
        return value

    def close(self):
        self.service.close();self.session.close();self.credential.credential.close()


def denied(call, codes, status):
    """Exact API denial, never generic exception/network failure as proof."""
    try:
        call()
    except HttpResponseError as error:
        require(error.status_code==status and error.error_code in codes,'worker_archive_denial_wrong_cause')
        return dict(status_code=status,error_code=error.error_code)
    raise TrialError('worker_archive_mutation_allowed')


class LostAckContainer:
    def __init__(self, container):
        self.container=container
        self.account_name,self.container_name=container.account_name,container.container_name
    def get_blob_client(self,name,**kwargs):
        blob=self.container.get_blob_client(name,**kwargs)
        class LostAckBlob:
            def upload_blob(_self,data,*,overwrite):
                blob.upload_blob(data,overwrite=overwrite)
                raise TimeoutError()
        return LostAckBlob()


def write(context,boundary):
    require(context.mode=='writer')
    normal=payload(context,'normal');normal_ref=boundary.archive.create(normal)
    duplicate=denied(lambda:boundary.container.get_blob_client(normal_ref.name).upload_blob(normal,overwrite=False),
        {'BlobAlreadyExists','BlobImmutableDueToLegalHold'},409)
    overwrite=denied(lambda:boundary.container.get_blob_client(normal_ref.name).upload_blob(b'changed synthetic probe',overwrite=True),
        {'BlobImmutableDueToLegalHold'},409)
    boundary.archive.read(normal_ref,normal)
    lost=payload(context,'lost_ack')
    uncertain=VersionedWorkerArchive(LostAckContainer(boundary.container),boundary.protection,writer=True)
    try:
        uncertain.create(lost)
        raise TrialError('worker_archive_lost_ack_was_accepted')
    except AuditError:
        pass
    # Read-only inspection is not repair, retry or a newly-created receipt.
    name='owner-worker-v1/'+sha(lost)+'.json'
    properties=boundary.container.get_blob_client(name).get_blob_properties()
    lost_ref=ArchiveVersion(name,properties.version_id,properties.etag,properties.size,sha(lost))
    boundary.archive.read(lost_ref,lost)
    lost_duplicate=denied(lambda:boundary.container.get_blob_client(name).upload_blob(lost,overwrite=False),
        {'BlobAlreadyExists','BlobImmutableDueToLegalHold'},409)
    return dict(normal=asdict(normal_ref),lost_ack_inspection=asdict(lost_ref),
        duplicate_denial=duplicate,overwrite_denial=overwrite,lost_ack_duplicate_denial=lost_duplicate,
        lost_ack_receipt_accepted=False,lost_ack_automatic_retry=False,lost_ack_newly_created=False)


def verify(context,boundary,references):
    require(context.mode=='verifier' and type(references) is dict
        and set(references)=={'normal','lost_ack_inspection'})
    for kind,key in (('normal','normal'),('lost_ack','lost_ack_inspection')):
        data=payload(context,kind);boundary.archive.read(reference(references[key],data),data)
    creation=denied(lambda:boundary.container.get_blob_client(PROBE_NAME).upload_blob(b'synthetic denied probe',overwrite=False),
        {'AuthorizationPermissionMismatch'},403)
    for kind,key in (('normal','normal'),('lost_ack','lost_ack_inspection')):
        data=payload(context,kind);boundary.archive.read(reference(references[key],data),data)
    return dict(exact_versions_independently_read=True,reader_creation_denial=creation,
        lost_ack_receipt_accepted=False,lost_ack_newly_created=False)


def main(context, references=None, *, make_boundary=None):
    require(type(context) is Context)
    result=dict(status='unaccepted',operation_id=OPERATION,mode=context.mode,
        job_resource_id=JOBS[context.mode],identity_resource_id=IDENTITIES[context.mode],
        principal_id=context.principal_id,source_manifest_sha256=context.source_manifest_sha256,
        image_sha256=context.image_sha256,source_rows_read=0,source_rows_written=0,
        sql_connections=0,model_calls=0,production_changed=False,automatic_retry=False,
        database_committed=False,protected_worker_receipts_verified=False,
        production_evidence_registry_enrolled=False,runtime_process_cessation_verified=False,
        website_owner_authenticated=False,owner_runtime_active=False,six_specialist_runtimes_active=False,
        step_3_complete=False,step_4_started=False)
    boundary=None
    try:
        require(not FORBIDDEN_ENV.intersection(os.environ),'worker_archive_ambient_credential_present')
        require(os.getuid()==os.getgid()==10001,'worker_archive_execution_identity_invalid')
        boundary=(make_boundary(context) if make_boundary is not None else
            Boundary(context,ManagedIdentityCredential(client_id=context.client_id,retry_total=0)))
        value=write(context,boundary) if context.mode=='writer' else verify(context,boundary,references)
        result.update(value,status='synthetic_archive_boundary_verified')
    except Exception as error:
        result['reason']=str(error) if type(error) is TrialError else 'worker_archive_unverified'
    finally:
        if boundary is not None:
            try:boundary.close()
            except Exception:result.update(status='unaccepted',reason='worker_archive_close_unverified')
    require(len(canonical(result))<=16384)
    return result
