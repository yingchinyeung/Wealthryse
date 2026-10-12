"""Transactional, append-only SHADOW evidence ledger. Never dispatches work.

Callers must own the transaction and set app.tenant_id. Per-subject/cycle locks
serialize cooperating writers; expected-head fencing rejects late completions.
Evidence is producer-attested, not proof of financial accuracy. Real adapters,
cross-subject lineage and approved per-source calendars precede any cutover.
"""
from __future__ import annotations
import hashlib
import json
import re
from datetime import date
from uuid import UUID, uuid4
from .workflow_readiness import PREREQUISITES, StageEvidence, readiness

POLICY_VERSION='shadow-workflow-evidence-v1'
DATED_POLICY_VERSION='shadow-workflow-evidence-v2'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def normalized_record(*,tenant_id,cycle_key,subject_id,stage,status,validated,through_date,
                      input_sha256,output_sha256,dependency_ids,producer,date_policy_id=None,date_policy_sha256=None):
    tenant_id=str(UUID(str(tenant_id)))
    for value,limit in ((cycle_key,80),(subject_id,80),(producer,100)):
        if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>limit:
            raise ValueError('Invalid workflow identity')
    if stage not in PREREQUISITES or status not in ('succeeded','unchanged','failed'):
        raise ValueError('Unknown workflow stage/status')
    if type(validated) is not bool or (status=='failed' and validated):
        raise ValueError('Invalid validation state')
    if type(through_date) is not date:
        raise ValueError('Explicit observation date required')
    for fingerprint in (input_sha256,output_sha256):
        if not isinstance(fingerprint,str) or not re.fullmatch('[0-9a-f]{64}',fingerprint):
            raise ValueError('Value fingerprint required')
    if set(dependency_ids)!=set(PREREQUISITES[stage]):
        raise ValueError('Exact typed dependencies required')
    dependencies={key:str(UUID(str(value))) for key,value in dependency_ids.items()}
    record=dict(tenant_id=tenant_id,cycle_key=cycle_key,subject_id=subject_id,stage=stage,
                status=status,validated=validated,through_date=through_date.isoformat(),
                input_sha256=input_sha256,output_sha256=output_sha256,
                dependency_ids=dependencies,producer=producer,policy_version=POLICY_VERSION)
    if date_policy_id is not None or date_policy_sha256 is not None:
        if date_policy_id is None or not isinstance(date_policy_sha256,str) or not re.fullmatch('[0-9a-f]{64}',date_policy_sha256):
            raise ValueError('Exact date-policy identity and hash required')
        record.update(date_policy_id=str(UUID(str(date_policy_id))),date_policy_sha256=date_policy_sha256,
                      policy_version=DATED_POLICY_VERSION)
    return record


def _scope(connection,tenant_id,cycle_key,subject_id):
    actual=connection.execute("SELECT app_tenant_id() AS tenant_id").fetchone()['tenant_id']
    if str(actual)!=str(tenant_id):
        raise ValueError('Transaction tenant mismatch')
    # Bound SQL arguments, not user-provided executable SQL. Signed bigint key.
    key=int.from_bytes(hashlib.sha256(canonical([str(tenant_id),cycle_key,subject_id]).encode()).digest()[:8],
                       byteorder='big',signed=True)
    connection.execute('SELECT pg_advisory_xact_lock(%s)',(key,))


def heads(connection,*,tenant_id,cycle_key,subject_id):
    rows=connection.execute("""SELECT DISTINCT ON (stage) * FROM workflow_stage_evidence
       WHERE tenant_id=%s AND cycle_key=%s AND subject_id=%s
       ORDER BY stage,sequence_no DESC""",(tenant_id,cycle_key,subject_id)).fetchall()
    return {r['stage']:dict(r) for r in rows}


def _record_from_row(row):
    dependencies=row['dependency_ids_json']
    if isinstance(dependencies,str):
        dependencies=json.loads(dependencies)
    return normalized_record(tenant_id=row['tenant_id'],cycle_key=row['cycle_key'],subject_id=row['subject_id'],
        stage=row['stage'],status=row['status'],validated=row['validated'],through_date=row['through_date'],
        input_sha256=row['input_sha256'],output_sha256=row['output_sha256'],dependency_ids=dependencies,
        producer=row['producer'],date_policy_id=row.get('date_policy_id'),date_policy_sha256=row.get('date_policy_sha256'))


def evaluate_heads(current,*,tenant_id,cycle_key,subject_id,stage,expected_date,expected_dates=None,date_policy=None):
    if stage not in PREREQUISITES or type(expected_date) is not date:
        raise ValueError('Stage and approved expected observation date required')
    evidence={}
    reasons=[]
    needed=set()
    if date_policy is not None:
        from .workflow_dates import evaluation_dates
        if expected_dates is not None:
            raise ValueError('Persisted date policy cannot be overridden')
        expected_dates=evaluation_dates(date_policy,tenant_id=tenant_id,cycle_key=cycle_key,
            subject_id=subject_id,stage=stage,expected_date=expected_date)
    def visit(name):
        for parent in PREREQUISITES[name]:
            if parent not in needed:
                needed.add(parent)
                visit(parent)
    visit(stage)
    for name in sorted(needed):
        row=current.get(name)
        if row is None:
            continue
        record=_record_from_row(row)
        if (record['tenant_id']!=str(tenant_id) or record['cycle_key']!=cycle_key
                or record['subject_id']!=subject_id or record['stage']!=name
                or row['policy_version']!=record['policy_version'] or digest(record)!=row['record_sha256']):
            reasons.append(name+': evidence identity/integrity mismatch')
        if date_policy is not None:
            if (record.get('date_policy_id')!=str(date_policy['policy_id'])
                    or record.get('date_policy_sha256')!=date_policy['record_sha256']):
                reasons.append(name+': date policy changed; recalculate')
        elif record.get('date_policy_id') is not None:
            reasons.append(name+': persisted date policy required')
        deps={}
        for parent,reference in record['dependency_ids'].items():
            upstream=current.get(parent)
            if upstream is None or str(upstream['evidence_id'])!=reference:
                reasons.append(name+': '+parent+' completion superseded or missing')
            else:
                deps[parent]=upstream['output_sha256']
        evidence[name]=StageEvidence(subject_id,name,record['status'],record['validated'],
                     date.fromisoformat(record['through_date']),record['output_sha256'],deps)
    ready,other=readiness(stage,subject_id=subject_id,expected_date=expected_date,evidence=evidence,
                         expected_dates=expected_dates)
    reasons.extend(other)
    return ready and not reasons,tuple(sorted(set(reasons)))


def append_evidence(connection,*,expected_previous_id=None,**kwargs):
    record=normalized_record(**kwargs)
    scope={key:record[key] for key in ('tenant_id','cycle_key','subject_id')}
    _scope(connection,**scope)
    current=heads(connection,**scope)
    stage=record['stage']
    previous=current.get(stage)
    record_hash=digest(record)
    date_policy=None
    if record.get('date_policy_id') is not None:
        from .workflow_dates import require_policy,policy_record
        date_policy=require_policy(connection,**scope,policy_id=record['date_policy_id'],
            policy_sha256=record['date_policy_sha256'])
        dates=policy_record(date_policy)
        if kwargs['through_date']>date_policy['cycle_cutoff']:
            raise ValueError('Completion is future-dated for its cycle')
        if record['validated'] and kwargs['through_date']<date.fromisoformat(dates['expected_dates'][stage]):
            raise ValueError('Completion is stale for its stage')
    elif any(row.get('date_policy_id') is not None for row in current.values()):
        raise ValueError('Persisted date policy required for this cycle')
    for parent,reference in record['dependency_ids'].items():
        upstream=current.get(parent)
        if upstream is None or str(upstream['evidence_id'])!=reference:
            raise ValueError('Completion uses a superseded dependency')
    if record['validated']:
        ready,_=evaluate_heads(current,**scope,stage=stage,
            expected_date=date_policy['cycle_cutoff'] if date_policy else kwargs['through_date'],date_policy=date_policy)
        if not ready:
            raise ValueError('Prerequisite evidence is not ready')
    # Only a CURRENT identical record is idempotent. Never return an obsolete
    # completion as current after an intervening correction/failure.
    if previous and previous['record_sha256']==record_hash:
        return {'evidence_id':str(previous['evidence_id']),'status':'unchanged'}
    if (str(previous['evidence_id']) if previous else None)!=(str(expected_previous_id) if expected_previous_id else None):
        raise ValueError('Stale completion: expected head changed')
    evidence_id=str(uuid4())
    sql="""INSERT INTO workflow_stage_evidence(tenant_id,evidence_id,cycle_key,subject_id,
      stage,sequence_no,status,validated,through_date,input_sha256,output_sha256,dependency_ids_json,
      producer,policy_version,record_sha256)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)"""
    parameters=(record['tenant_id'],evidence_id,record['cycle_key'],record['subject_id'],stage,
       previous['sequence_no']+1 if previous else 1,record['status'],record['validated'],kwargs['through_date'],
       record['input_sha256'],record['output_sha256'],canonical(record['dependency_ids']),record['producer'],
       record['policy_version'],record_hash)
    if date_policy is not None:
        sql=sql.replace('policy_version,record_sha256)','policy_version,record_sha256,date_policy_id,date_policy_sha256)')
        sql=sql.removesuffix(')')+',%s,%s)'
        parameters+=record['date_policy_id'],record['date_policy_sha256']
    connection.execute(sql,parameters)
    return {'evidence_id':evidence_id,'status':'recorded'}


def record_shadow_decision(connection,*,tenant_id,cycle_key,subject_id,stage,expected_date,date_policy_id=None):
    _scope(connection,tenant_id,cycle_key,subject_id)
    current=heads(connection,tenant_id=tenant_id,cycle_key=cycle_key,subject_id=subject_id)
    date_policy=None
    if date_policy_id is not None:
        from .workflow_dates import require_policy
        date_policy=require_policy(connection,tenant_id=tenant_id,cycle_key=cycle_key,subject_id=subject_id,policy_id=date_policy_id)
    ready,reasons=evaluate_heads(current,tenant_id=tenant_id,cycle_key=cycle_key,
                subject_id=subject_id,stage=stage,expected_date=expected_date,date_policy=date_policy)
    record=dict(tenant_id=str(tenant_id),cycle_key=cycle_key,subject_id=subject_id,stage=stage,
                expected_date=expected_date.isoformat(),ready=ready,reasons=list(reasons),
                evidence_ids={key:str(row['evidence_id']) for key,row in sorted(current.items())},
                policy_version=POLICY_VERSION,execution_authorized=False)
    if date_policy is not None:
        record.update(date_policy_id=str(date_policy['policy_id']),date_policy_sha256=date_policy['record_sha256'],
                      policy_version=DATED_POLICY_VERSION)
    fingerprint=digest(record)
    sql="""INSERT INTO workflow_shadow_decisions(tenant_id,decision_id,cycle_key,subject_id,
        stage,expected_date,ready,reasons_json,evidence_ids_json,decision_sha256,policy_version)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
        ON CONFLICT(tenant_id,decision_sha256) DO NOTHING"""
    parameters=(tenant_id,str(uuid4()),cycle_key,subject_id,stage,expected_date,ready,
         canonical(list(reasons)),canonical(record['evidence_ids']),fingerprint,record['policy_version'])
    if date_policy is not None:
        sql=sql.replace('decision_sha256,policy_version)','decision_sha256,policy_version,date_policy_id,date_policy_sha256)')
        sql=sql.replace('%s::jsonb,%s::jsonb,%s,%s)','%s::jsonb,%s::jsonb,%s,%s,%s,%s)')
        parameters+=record['date_policy_id'],record['date_policy_sha256']
    connection.execute(sql,parameters)
    return record
