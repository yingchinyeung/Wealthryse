"""Immutable SHADOW date policies; supplied expectations never dispatch work.

The producer/calendar approval adapter must supply these dates. A policy record
is not independent evidence of ingestion, financial accuracy or permission.
"""
from datetime import date
from uuid import UUID,uuid4
from .market_calendar import CALENDAR_VERSION
from .workflow_readiness import PREREQUISITES

VERSION='shadow-workflow-dates-v1'


def normalized_policy(*,tenant_id,cycle_key,subject_id,cycle_cutoff,calendar_version,expected_dates):
    tenant_id=str(UUID(str(tenant_id)))
    for value in (cycle_key,subject_id):
        if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>80:
            raise ValueError('Invalid date-policy scope')
    if type(cycle_cutoff) is not date or calendar_version!=CALENDAR_VERSION:
        raise ValueError('Explicit cutoff and reviewed calendar version required')
    if (set(expected_dates)!=set(PREREQUISITES)
            or any(type(day) is not date or day>cycle_cutoff for day in expected_dates.values())):
        raise ValueError('Complete typed non-future stage dates required')
    return dict(tenant_id=tenant_id,cycle_key=cycle_key,subject_id=subject_id,
        cycle_cutoff=cycle_cutoff.isoformat(),calendar_version=calendar_version,
        expected_dates={key:value.isoformat() for key,value in sorted(expected_dates.items())},
        policy_version=VERSION)


def policy_record(row):
    import json
    from .workflow_ledger import digest
    dates=row['expected_dates_json']
    if isinstance(dates,str):dates=json.loads(dates)
    record=normalized_policy(tenant_id=row['tenant_id'],cycle_key=row['cycle_key'],subject_id=row['subject_id'],
        cycle_cutoff=row['cycle_cutoff'],calendar_version=row['calendar_version'],
        expected_dates={key:date.fromisoformat(value) for key,value in dates.items()})
    if row['policy_version']!=VERSION or digest(record)!=row['record_sha256']:
        raise ValueError('Date-policy integrity mismatch')
    return record


def current_policy(connection,*,tenant_id,cycle_key,subject_id):
    return connection.execute('''SELECT * FROM workflow_cycle_date_policies
        WHERE tenant_id=%s AND cycle_key=%s AND subject_id=%s
        ORDER BY sequence_no DESC LIMIT 1''',(tenant_id,cycle_key,subject_id)).fetchone()


def require_policy(connection,*,tenant_id,cycle_key,subject_id,policy_id,policy_sha256=None):
    row=current_policy(connection,tenant_id=tenant_id,cycle_key=cycle_key,subject_id=subject_id)
    if row is None or str(row['policy_id'])!=str(UUID(str(policy_id))):
        raise ValueError('Date policy missing or superseded')
    record=policy_record(row)
    if (record['tenant_id']!=str(tenant_id) or record['cycle_key']!=cycle_key or record['subject_id']!=subject_id
            or (policy_sha256 is not None and row['record_sha256']!=policy_sha256)):
        raise ValueError('Date-policy scope/hash mismatch')
    return dict(row)


def append_policy(connection,*,expected_previous_id=None,**kwargs):
    from .workflow_ledger import _scope,canonical,digest
    record=normalized_policy(**kwargs)
    scope={key:record[key] for key in ('tenant_id','cycle_key','subject_id')}
    _scope(connection,**scope)
    previous=current_policy(connection,**scope)
    if previous is not None:policy_record(previous)
    value_hash=digest(record)
    if previous is not None and previous['record_sha256']==value_hash:
        return {'policy_id':str(previous['policy_id']),'policy_sha256':value_hash,'status':'unchanged'}
    if (str(previous['policy_id']) if previous else None)!=(str(expected_previous_id) if expected_previous_id else None):
        raise ValueError('Stale date-policy writer')
    identifier=str(uuid4())
    connection.execute('''INSERT INTO workflow_cycle_date_policies
        (tenant_id,policy_id,cycle_key,subject_id,sequence_no,cycle_cutoff,calendar_version,
        expected_dates_json,policy_version,record_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)''',
        (record['tenant_id'],identifier,record['cycle_key'],record['subject_id'],
         previous['sequence_no']+1 if previous else 1,kwargs['cycle_cutoff'],record['calendar_version'],
         canonical(record['expected_dates']),VERSION,value_hash))
    return {'policy_id':identifier,'policy_sha256':value_hash,'status':'recorded'}


def evaluation_dates(policy,*,tenant_id,cycle_key,subject_id,stage,expected_date):
    record=policy_record(policy)
    if (record['tenant_id']!=str(tenant_id) or record['cycle_key']!=cycle_key or record['subject_id']!=subject_id
            or record['cycle_cutoff']!=expected_date.isoformat()):
        raise ValueError('Date policy does not match evaluation scope/cutoff')
    needed=set()
    def visit(name):
        for parent in PREREQUISITES[name]:
            if parent not in needed:needed.add(parent);visit(parent)
    visit(stage)
    return {name:date.fromisoformat(record['expected_dates'][name]) for name in needed}
