"""One trusted manual producer: audit-service permission inspection only.

Not a general HTTP ingress or specialist executor. Fixed role/action/scope;
there is no caller-selected SQL, evidence body, timestamp or destination.
"""
import hashlib
import uuid

from azure.core.exceptions import ResourceExistsError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import ContainerClient
import requests

from .specialist_archive_reader import ReportingArchiveReader, ACCOUNT, CONTAINER, SCOPE, MAX_BYTES
from .specialist_audit_runtime import TENANT, LOGIN, bind_audit_session, verify_audit_access
from .specialist_audit_service import commit_and_archive
from .util import canonical_json

CLIENT_ID='7135fc5a-3714-41d8-a73a-71a22e56a29a'
PRINCIPAL_ID='60313d53-6387-46d4-9bb8-dc5f8ab925a5'
JOB_SCOPE=('/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11'
           '/providers/Microsoft.App/jobs/ibkrv2stg-agent-audit')


class CaptureArchive(ReportingArchiveReader):
    """Create-only bounded evidence, plus exact readback; no delete or overwrite."""
    def _retain(self,name,payload):
        if not 0<len(payload)<=MAX_BYTES:
            raise ValueError('Audit evidence exceeds bounds')
        self._protection()
        blob=self._container.get_blob_client(name)
        try: blob.upload_blob(payload,overwrite=False)
        except ResourceExistsError: pass
        if self._read(name,expected_size=len(payload))!=payload:
            raise ValueError('Audit evidence collision; original retained')
        self._protection()
        return {'blob_name':name,'sha256':hashlib.sha256(payload).hexdigest(),'protected_archive_verified':True}

    def put(self,tenant_id,event):
        if tenant_id!=TENANT or type(event['sequence']) is not int or event['sequence']<1:
            raise ValueError('Unregistered audit event tenant or sequence')
        return self._retain(f"{tenant_id}/events/{event['sequence']:020d}.json",
                            canonical_json({'tenant_id':tenant_id,'event':event}).encode())

    def retain_inspection_evidence(self,operation_id,phase,source_manifest_sha256,result=None):
        if str(uuid.UUID(operation_id))!=operation_id or phase not in ('started','succeeded'):
            raise ValueError('Registered inspection operation and phase required')
        if phase=='started' and result is not None:
            raise ValueError('Intent must not claim a result')
        if phase=='succeeded' and result!=EXPECTED_ACCESS:
            raise ValueError('Inspection result not verified')
        content={'schema':'audit-permission-inspection-v1','tenant_id':TENANT,'operation_id':operation_id,
                 'phase':phase,'role':'platform_operations','action':'inspect_and_analyze',
                 'producer_kind':'trusted_manual_rollout_worker','autonomous_specialist':False,
                 'producer_resource':JOB_SCOPE,'producer_principal':PRINCIPAL_ID,'database_login':LOGIN,
                 'scope':'specialist audit database privileges only','source_manifest_sha256':source_manifest_sha256,
                 'result':result,'financial_or_source_data_included':False,
                 'cost_measurement':'no model calls; infrastructure cost not measured'}
        payload=canonical_json(content).encode()
        digest=hashlib.sha256(payload).hexdigest()
        return self._retain(f'{TENANT}/evidence/{digest}.json',payload)


EXPECTED_ACCESS={'database_access':'verified','reachable_roles_verified':True,'source_table_access':False,
                 'source_mutation_authority':False,'ledger_append_only':True,'events_written':0}


def capture_permission_inspection(c,archive,*,operation_id,source_manifest_sha256):
    import re
    if str(uuid.UUID(operation_id))!=operation_id or not re.fullmatch('[0-9a-f]{64}',source_manifest_sha256):
        raise ValueError('Exact registered operation and source manifest required')
    bind_audit_session(c)
    if verify_audit_access(c)!=EXPECTED_ACCESS:
        raise ValueError('Audit service access preflight failed')
    if archive.check_ready().get('protected_archive_verified') is not True:
        raise ValueError('Protected audit archive unavailable')
    phases=[]
    for phase in ('started','succeeded'):
        # The inspected action is read-only and repeatable. Never generalize
        # this recovery loop to actions with external effects.
        result=verify_audit_access(c) if phase=='succeeded' else None
        evidence=archive.retain_inspection_evidence(operation_id,phase,source_manifest_sha256,result)
        fields=dict(tenant_id=TENANT,event_id=operation_id+'-'+phase,role='platform_operations',
                    action='inspect_and_analyze',operation_id=operation_id,phase=phase,
                    evidence_sha256=evidence['sha256'],cost_microusd=0)
        receipt=commit_and_archive(c,archive,**fields)
        repeated=commit_and_archive(c,archive,**fields)
        if repeated['event']!=receipt['event']:
            raise ValueError('Audit replay changed the original record')
        phases.append({'phase':phase,'sequence':receipt['event']['sequence'],
                       'event_sha256':receipt['event']['sha256'],'evidence_sha256':evidence['sha256']})
    n=c.execute("SELECT count(*) AS n FROM specialist_audit_events WHERE tenant_id=%s AND event_json->>'operation_id'=%s",
                (TENANT,operation_id)).fetchone()['n']
    if n!=2: raise ValueError('Inspection audit inventory differs')
    return {'status':'passed','operation_id':operation_id,'mode':'trusted_manual_inspection_capture',
            'phases':phases,'operation_event_count':n,'exact_replay_verified':True,
            'database_committed':True,'protected_archive_verified':True,'source_rows_changed':0,
            'messages_sent':0,'specialist_execution_enabled':False,'execution_authorized':False}


def managed_capture_archive():
    credential=ManagedIdentityCredential(client_id=CLIENT_ID)
    container=ContainerClient('https://'+ACCOUNT+'.blob.core.windows.net',CONTAINER,credential=credential,
                              connection_timeout=10,read_timeout=15,retry_total=0)
    def read_arm():
        token=credential.get_token('https://management.azure.com/.default').token
        response=requests.get('https://management.azure.com'+SCOPE+'?api-version=2023-05-01',
            headers={'Authorization':'Bearer '+token},timeout=15,allow_redirects=False)
        if response.status_code!=200: raise ValueError('Audit protection inspection unavailable')
        return response.json()
    return CaptureArchive(container,read_arm)
