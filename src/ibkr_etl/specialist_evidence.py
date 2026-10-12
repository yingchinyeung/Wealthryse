"""Reviewed evidence registrations, not execution or producer-authentication grants.

Only independently accepted manual inspection builds are registered initially.
Enrollment is a reviewed code change; event payloads cannot add registrations.
Protected bytes and a matching claim prove retained evidence, not possession of
an identity token. The separate service login/dispatch boundary authenticates
the producer. No agent, browser or model can configure this registry.
"""
import hashlib
import json
import re
import uuid
from types import MappingProxyType
from datetime import datetime,timedelta

from .util import canonical_json

TENANT='00000000-0000-0000-0000-000000000001'
REGISTRY_VERSION='specialist-evidence-v2'
# Reviewed source manifests only. Payloads cannot extend this allowlist.
OBSERVATION_MANIFESTS=frozenset({'dbe5ee61a08a6b2f350949c03463c5d080fd4f1f4cf849aa2a966817e410f247'})
INSPECTION_MANIFESTS=frozenset({
    'ba01bb2995738985b2c96f936cec3dcfc3eed13ff742091cc5d71d0db6bcd1b6',
    '6ce56d1f26c472c03e39f82397c380e7d0e77b69fdc6f202e36e051a3956334b',
})
INSPECTION_SCOPE=MappingProxyType({
    'schema':'audit-permission-inspection-v1','tenant_id':TENANT,
    'role':'platform_operations','action':'inspect_and_analyze',
    'producer_kind':'trusted_manual_rollout_worker','autonomous_specialist':False,
    'producer_resource':'/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb/resourceGroups/rg-ibkr-database-v11/providers/Microsoft.App/jobs/ibkrv2stg-agent-audit',
    'producer_principal':'60313d53-6387-46d4-9bb8-dc5f8ab925a5',
    'database_login':'ibkr_specialist_audit_login',
    'scope':'specialist audit database privileges only','financial_or_source_data_included':False,
    'cost_measurement':'no model calls; infrastructure cost not measured',
})
INSPECTION_RESULT=MappingProxyType({
    'database_access':'verified','reachable_roles_verified':True,'source_table_access':False,
    'source_mutation_authority':False,'ledger_append_only':True,'events_written':0,
})


def validate_registered_detail(tenant,event,payload):
    """Validate exact protected bytes against a fixed reviewed schema and build."""
    digest=event.get('evidence_sha256')
    if (tenant!=TENANT or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest)
        or not isinstance(payload,bytes) or not 0<len(payload)<=65536
        or hashlib.sha256(payload).hexdigest()!=digest):
        raise ValueError('Registered evidence hash or tenant mismatch')
    try:
        detail=json.loads(payload)
        operation=event['operation_id'];phase=event['phase']
        if str(uuid.UUID(operation))!=operation: raise ValueError('Invalid operation')
    except (KeyError,TypeError,ValueError,UnicodeError) as error:
        raise ValueError('Malformed registered evidence') from error
    if not isinstance(detail,dict) or canonical_json(detail).encode()!=payload:
        raise ValueError('Canonical registered evidence required')
    if detail.get('schema')=='specialist-observation-envelope-v1':
        from .agent_observation_evidence import validate_envelope
        envelope=validate_envelope(detail);observation=envelope['observation']
        if (envelope['source_manifest_sha256'] not in OBSERVATION_MANIFESTS
            or observation['tenant_id']!=tenant or observation['operation_id']!=operation
            or observation['phase']!=phase or event.get('event_id')!=operation+'-'+phase
            or event.get('role')!=observation['role'] or event.get('action')!=observation['action']
            or type(event.get('cost_microusd')) is not int or event['cost_microusd']!=0):
            raise ValueError('Unregistered observation evidence producer, build or scope')
        return dict(detail_verified=True,evidence_sha256=digest,registry_version=REGISTRY_VERSION,
            producer_kind=envelope['producer']['producer_kind'],execution_authorized=False)
    # Compare serialized values too: JSON true is not the integer 1.
    if (set(detail)!=set(INSPECTION_SCOPE)|{'operation_id','phase','source_manifest_sha256','result'}
        or any(canonical_json(detail.get(k))!=canonical_json(v) for k,v in INSPECTION_SCOPE.items())
        or detail.get('source_manifest_sha256') not in INSPECTION_MANIFESTS
        or phase not in ('started','succeeded') or detail['phase']!=phase
        or detail['operation_id']!=operation or event.get('event_id')!=operation+'-'+phase
        or event.get('role')!=INSPECTION_SCOPE['role'] or event.get('action')!=INSPECTION_SCOPE['action']):
        raise ValueError('Unregistered evidence producer, build or scope')
    expected=None if phase=='started' else dict(INSPECTION_RESULT)
    if canonical_json(detail['result'])!=canonical_json(expected):
        raise ValueError('Registered evidence result mismatch')
    return {'detail_verified':True,'evidence_sha256':digest,'registry_version':REGISTRY_VERSION,
            'producer_kind':INSPECTION_SCOPE['producer_kind'],'execution_authorized':False}


class RegisteredEvidenceReader:
    """Read-only reporting wrapper; no archive repair or caller-supplied registry."""
    def __init__(self,archive): self._archive=archive
    def check_ready(self): return self._archive.check_ready()
    def verify(self,tenant,event): return self._archive.verify(tenant,event)
    def verify_detail(self,tenant,event):
        digest=event.get('evidence_sha256')
        if tenant!=TENANT or not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Exact registered detail scope required')
        self._archive._protection()
        payload=self._archive._read(f'{tenant}/evidence/{digest}.json')
        receipt=validate_registered_detail(tenant,event,payload)
        self._archive._protection()
        return receipt


def inspect_evidence_inventory(connection,archive):
    """Bounded current ledger readiness, never a prematurely closed daily report."""
    from .specialist_autonomy import build_daily_activity_report
    if not connection.autocommit or connection.info.transaction_status!=0:
        raise ValueError('Idle reporting connection required')
    if archive.check_ready().get('protected_archive_verified') is not True:
        raise ValueError('Protected archive unavailable')
    with connection.transaction():
        connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        if connection.execute('SELECT app_tenant_id()::text AS tenant_id').fetchone()['tenant_id']!=TENANT:
            raise ValueError('Registered evidence tenant mismatch')
        rows=connection.execute('''SELECT sequence_no,event_sha256,event_json
            FROM specialist_audit_events WHERE tenant_id=%s ORDER BY sequence_no LIMIT 1001''',(TENANT,)).fetchall()
        if len(rows)>1000: raise ValueError('Evidence inventory exceeds rehearsal limit; paginated verification required')
        events=[]
        for row in rows:
            e=row['event_json']
            if row['sequence_no']!=e.get('sequence') or row['event_sha256']!=e.get('sha256'):
                raise ValueError('Evidence inventory row mismatch')
            archive.verify(TENANT,e)
            receipt=archive.verify_detail(TENANT,e)
            if not isinstance(receipt,dict) or receipt.get('detail_verified') is not True or receipt.get('evidence_sha256')!=e['evidence_sha256']:
                raise ValueError('Evidence inventory detail not verified')
            events.append(e)
        if events:
            build_daily_activity_report(events,period_start=datetime.fromisoformat(events[0]['occurred_at']),
                period_end=datetime.fromisoformat(events[-1]['occurred_at'])+timedelta(microseconds=1),
                first_sequence=1,previous_sha256='0'*64,last_sequence=events[-1]['sequence'],last_sha256=events[-1]['sha256'])
        if archive.check_ready().get('protected_archive_verified') is not True:
            raise ValueError('Protected archive unavailable after verification')
    return {'registered_detail_count':len(events),'evidence_registry_version':REGISTRY_VERSION,
            'inventory_scope':'bounded_current_ledger_not_a_closed_daily_report','inventory_database_writes':0}
