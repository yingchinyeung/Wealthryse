"""Trusted observation audit adapter; not an HTTP endpoint or an agent tool.

The gateway supplies authenticated callers and reviewed authority. This adapter
uses a separate, restricted audit connection; it never borrows the analytics
reader or returns authority to change source data. No runtime enrollment is
enabled by importing this module.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime,timedelta

from .agent_governance import POLICY_VERSION,REGISTRY
from .specialist_audit_runtime import TENANT,bind_audit_session,verify_audit_access
from .specialist_audit_service import commit_and_archive
from .specialist_autonomy import build_daily_activity_report
from .util import canonical_json
from .agent_observation_evidence import validate_envelope

SCHEMA='specialist-subject-observation-v1'
FIELDS=frozenset({'schema','tenant_id','operation_id','role','action','phase','tool_name','subject_id',
                 'agent_sha256','user_sha256','request_sha256','response_sha256','code','policy_version',
                 'tool_attempted','result_delivery_confirmed'})


def validate_detail(detail):
    if not isinstance(detail,dict) or set(detail)!=FIELDS:
        raise ValueError('Exact observation evidence schema required')
    if (detail['schema']!=SCHEMA or detail['tenant_id']!=TENANT
        or detail['policy_version']!=POLICY_VERSION or detail['action']!='inspect_and_analyze'
        or str(uuid.UUID(detail['operation_id']))!=detail['operation_id']
        or detail['role'] not in REGISTRY or detail['tool_name'] not in REGISTRY[detail['role']].subject_tools
        or detail['phase'] not in ('started','succeeded','failed','denied')
        or type(detail['tool_attempted']) is not bool or detail['result_delivery_confirmed'] is not False):
        raise ValueError('Unregistered observation evidence scope')
    subject=detail['subject_id']
    if not isinstance(subject,str) or not subject or subject!=subject.strip() or len(subject)>120 or any(ord(c)<32 for c in subject):
        raise ValueError('Invalid observation subject')
    for key in ('agent_sha256','user_sha256','request_sha256'):
        if not isinstance(detail[key],str) or not re.fullmatch('[0-9a-f]{64}',detail[key]):
            raise ValueError('Observation identity/request fingerprint required')
    response=detail['response_sha256'];code=detail['code'];phase=detail['phase']
    if response is not None and (not isinstance(response,str) or not re.fullmatch('[0-9a-f]{64}',response)):
        raise ValueError('Invalid observation response fingerprint')
    if phase=='started' and (detail['tool_attempted'] or response is not None or code is not None):
        raise ValueError('Observation intent cannot claim an outcome')
    if phase=='succeeded' and (not detail['tool_attempted'] or response is None or code is not None):
        raise ValueError('Observation completion requires a result fingerprint')
    if phase in ('failed','denied') and (not isinstance(code,str) or not re.fullmatch('[a-z][a-z0-9_]{0,79}',code)):
        raise ValueError('Safe observation failure code required')
    payload=canonical_json(detail).encode()
    if len(payload)>4096: raise ValueError('Observation evidence exceeds bounds')
    return payload


class ProtectedObservationAudit:
    """Factory/archive are trusted server composition, never request parameters.

The supplied archive must expose create-only retention plus protected readback.
An append failure is surfaced: no automatic retries or substitute event IDs.
"""
    def __init__(self,connection_factory,archive,*,producer):
        self._connections=connection_factory
        self._archive=archive
        self._producer=producer

    def record(self,detail):
        detail=json.loads(validate_detail(detail))
        evidence=validate_envelope(self._producer.envelope(detail))
        # Validate enrollment before retaining bytes or touching the ledger.
        from .specialist_evidence import OBSERVATION_MANIFESTS
        if evidence['source_manifest_sha256'] not in OBSERVATION_MANIFESTS:
            raise ValueError('Observation producer build is not enrolled')
        payload=canonical_json(evidence).encode()
        digest=hashlib.sha256(payload).hexdigest()
        with self._connections() as c:
            bind_audit_session(c)
            access=verify_audit_access(c)
            if (access.get('database_access')!='verified' or access.get('source_table_access') is not False
                or access.get('source_mutation_authority') is not False or access.get('ledger_append_only') is not True):
                raise ValueError('Restricted audit writer unverified')
            retained=self._archive._retain(f'{TENANT}/evidence/{digest}.json',payload)
            if retained.get('sha256')!=digest or retained.get('protected_archive_verified') is not True:
                raise ValueError('Observation detail retention unverified')
            operation=detail['operation_id'];phase=detail['phase']
            receipt=commit_and_archive(c,self._archive,tenant_id=TENANT,event_id=operation+'-'+phase,
                operation_id=operation,role=detail['role'],action=detail['action'],phase=phase,
                evidence_sha256=digest,cost_microusd=0)
            return {**receipt,'evidence':evidence}


def verify_receipt(receipt,detail):
    validate_detail(detail)
    if (not isinstance(receipt,dict) or receipt.get('database_committed') is not True
        or receipt.get('protected_archive_verified') is not True):
        raise ValueError('Committed protected observation receipt required')
    event=receipt.get('event',{})
    # Older source-only synthetic receipt fixtures use bare details. A deployed
    # adapter always returns an enrolled envelope, verified independently below.
    if 'evidence' in receipt:
        evidence=validate_envelope(receipt['evidence'])
        if canonical_json(evidence['observation'])!=canonical_json(detail):
            raise ValueError('Observation receipt detail mismatch')
        from .specialist_evidence import validate_registered_detail
        payload=canonical_json(evidence).encode()
        validate_registered_detail(TENANT,event,payload)
    else:
        payload=validate_detail(detail)
    expected={key:detail[key] for key in ('operation_id','role','action','phase')}
    expected.update(event_id=detail['operation_id']+'-'+detail['phase'],
                    evidence_sha256=hashlib.sha256(payload).hexdigest(),cost_microusd=0)
    if any(canonical_json(event.get(k))!=canonical_json(v) for k,v in expected.items()):
        raise ValueError('Observation receipt does not match requested evidence')
    if (type(event.get('sequence')) is not int or event['sequence']<1
        or event.get('sha256')!=hashlib.sha256(canonical_json({k:v for k,v in event.items() if k!='sha256'}).encode()).hexdigest()):
        raise ValueError('Observation receipt event hash invalid')
    at=datetime.fromisoformat(event['occurred_at'])
    build_daily_activity_report([event],period_start=at,period_end=at+timedelta(microseconds=1),
        first_sequence=event['sequence'],previous_sha256=event['previous_sha256'],
        last_sequence=event['sequence'],last_sha256=event['sha256'])
