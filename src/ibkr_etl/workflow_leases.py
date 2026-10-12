"""Shadow-only worker fencing; never dispatches or publishes external effects.

Call in a caller-owned transaction. Database time and the ledger's cycle lock
serialize claims. Every transition is append-only. Eligible deterministic work
excludes Flex ingestion, provider fetching and report release. An external
side effect is NOT exactly-once merely because its worker holds this lease.
"""
from datetime import timedelta
from uuid import UUID, uuid4
import json
import re
from . import workflow_ledger as ledger
from .workflow_dates import require_policy

VERSION = 'shadow-workflow-leases-v1'
ELIGIBLE = ('reconciliation', 'derived', 'risk', 'assurance')
MAX_ATTEMPTS = 3


def work_record(*, method_sha256, expected_previous_id=None, **kwargs):
    record = ledger.normalized_record(**kwargs)
    if record['stage'] not in ELIGIBLE or not record.get('date_policy_id'):
        raise ValueError('Only dated deterministic shadow work is eligible')
    if not isinstance(method_sha256, str) or not re.fullmatch('[0-9a-f]{64}', method_sha256):
        raise ValueError('Exact methodology fingerprint required')
    # Output/status/date are verified at completion, not known when claiming.
    for key in ('output_sha256', 'status', 'validated', 'through_date'):
        record.pop(key)
    record.update(method_sha256=method_sha256,
                  expected_previous_id=str(UUID(str(expected_previous_id))) if expected_previous_id else None)
    return record


def _transaction(connection):
    from psycopg.pq import TransactionStatus
    if connection.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('Caller-owned transaction required')


def _ttl(seconds):
    if type(seconds) is not int or not 1 <= seconds <= 900:
        raise ValueError('Lease duration must be 1 to 900 seconds')
    return seconds


def _head(connection, scope, stage):
    row = connection.execute('''SELECT * FROM workflow_lease_events
        WHERE tenant_id=%s AND cycle_key=%s AND subject_id=%s AND stage=%s
        ORDER BY sequence_no DESC LIMIT 1''', (*scope.values(), stage)).fetchone()
    if row is None:
        return None
    payload = row['payload_json']
    if isinstance(payload, str):
        payload = json.loads(payload)
    if (ledger.digest(payload) != row['record_sha256'] or payload['version'] != VERSION
            or any(str(row[key]) != str(payload[key]) for key in
                   ('tenant_id', 'cycle_key', 'subject_id', 'stage', 'sequence_no'))):
        raise ValueError('Lease event integrity mismatch')
    return payload


def _now(connection):
    return connection.execute('SELECT clock_timestamp() AS observed_at').fetchone()['observed_at']


def _time(value):
    from datetime import datetime
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Database timestamp requires timezone')
    return result


def _context(connection, work):
    _transaction(connection)
    scope = {key: work[key] for key in ('tenant_id', 'cycle_key', 'subject_id')}
    ledger._scope(connection, **scope)
    policy = require_policy(connection, **scope, policy_id=work['date_policy_id'],
                            policy_sha256=work['date_policy_sha256'])
    current = ledger.heads(connection, **scope)
    previous = current.get(work['stage'])
    if (str(previous['evidence_id']) if previous else None) != work['expected_previous_id']:
        raise ValueError('Stage completion head changed')
    if any(parent not in current or str(current[parent]['evidence_id']) != reference
           for parent, reference in work['dependency_ids'].items()):
        raise ValueError('Dependency completion changed')
    ready, _ = ledger.evaluate_heads(current, **scope, stage=work['stage'],
                                    expected_date=policy['cycle_cutoff'], date_policy=policy)
    if not ready:
        raise ValueError('Validated prerequisites required')
    return scope


def _append(connection, payload):
    connection.execute('''INSERT INTO workflow_lease_events
        (tenant_id,event_id,cycle_key,subject_id,stage,sequence_no,payload_json,record_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)''',
        (payload['tenant_id'], str(uuid4()), payload['cycle_key'], payload['subject_id'],
         payload['stage'], payload['sequence_no'], ledger.canonical(payload), ledger.digest(payload)))
    return payload


def claim(connection, *, owner_id, ttl_seconds=120, **kwargs):
    owner_id = str(UUID(str(owner_id)))
    duration = _ttl(ttl_seconds)
    work = work_record(**kwargs)
    scope = _context(connection, work)
    head = _head(connection, scope, work['stage'])
    now = _now(connection)
    fingerprint = ledger.digest(work)
    if head and head['state'] in ('claimed', 'heartbeat') and _time(head['expires_at']) > now:
        if head['owner_id'] == owner_id and head['work_sha256'] == fingerprint:
            return head  # Restart-safe; does not renew expiry or consume an attempt.
        raise ValueError('Worker lease is busy')
    if head and head['state'] == 'completed' and head['work_sha256'] == fingerprint:
        raise ValueError('Work already completed')
    attempts = connection.execute('''SELECT COUNT(*) AS n FROM workflow_lease_events
        WHERE tenant_id=%s AND cycle_key=%s AND subject_id=%s AND stage=%s
        AND payload_json->>'state'='claimed' AND payload_json->>'work_sha256'=%s''',
        (*scope.values(), work['stage'], fingerprint)).fetchone()['n']
    if attempts >= MAX_ATTEMPTS:
        raise ValueError('Recovery attempt limit reached; review required')
    return _append(connection, dict(**scope, stage=work['stage'], version=VERSION,
        sequence_no=head['sequence_no']+1 if head else 1,
        generation=head['generation']+1 if head else 1, token=str(uuid4()), owner_id=owner_id,
        work=work, work_sha256=fingerprint, state='claimed', attempt=attempts+1,
        observed_at=now.isoformat(), expires_at=(now+timedelta(seconds=duration)).isoformat(),
        execution_authorized=False))


def _fence(connection, lease):
    work = lease['work']
    if ledger.digest(work) != lease['work_sha256']:
        raise ValueError('Work fingerprint mismatch')
    scope = _context(connection, work)
    head = _head(connection, scope, work['stage'])
    if head is None or any(head[key] != lease[key] for key in
                           ('token', 'owner_id', 'generation', 'work_sha256')):
        raise ValueError('Worker lease superseded')
    now = _now(connection)
    if head['state'] not in ('claimed', 'heartbeat') or _time(head['expires_at']) <= now:
        raise ValueError('Worker lease expired or closed')
    return head, now


def heartbeat(connection, lease, *, ttl_seconds=120):
    duration = _ttl(ttl_seconds)
    head, now = _fence(connection, lease)
    return _append(connection, dict(head, sequence_no=head['sequence_no']+1, state='heartbeat',
        observed_at=now.isoformat(), expires_at=(now+timedelta(seconds=duration)).isoformat()))


def cancel(connection, lease):
    head, now = _fence(connection, lease)
    return _append(connection, dict(head, sequence_no=head['sequence_no']+1, state='cancelled',
                                  observed_at=now.isoformat()))


def complete(connection, lease, *, expected_previous_id=None, method_sha256, **kwargs):
    # Savepoint prevents a caller catching a failed journal insertion from
    # committing evidence without its corresponding completion transition.
    _transaction(connection)
    with connection.transaction():
        work = work_record(method_sha256=method_sha256, expected_previous_id=expected_previous_id, **kwargs)
        if work != lease['work'] or ledger.digest(work) != lease['work_sha256']:
            raise ValueError('Completion does not match claimed work')
        scope = {key: work[key] for key in ('tenant_id', 'cycle_key', 'subject_id')}
        ledger._scope(connection, **scope)
        previous = _head(connection, scope, work['stage'])
        if previous and previous['state'] == 'completed' and all(previous[key] == lease[key]
                for key in ('token', 'owner_id', 'generation', 'work_sha256')):
            policy = require_policy(connection, **scope, policy_id=work['date_policy_id'],
                                    policy_sha256=work['date_policy_sha256'])
            current = ledger.heads(connection, **scope)
            evidence = current.get(work['stage'])
            fingerprint = ledger.digest(ledger.normalized_record(**kwargs))
            ready, _ = ledger.evaluate_heads(current, **scope, stage=work['stage'],
                expected_date=policy['cycle_cutoff'], date_policy=policy)
            if (not ready or evidence is None or str(evidence['evidence_id']) != previous['evidence_id']
                    or any(parent not in current or str(current[parent]['evidence_id']) != reference
                           for parent, reference in work['dependency_ids'].items())
                    or evidence['record_sha256'] != fingerprint or previous['completion_sha256'] != fingerprint
                    or ledger.digest(ledger._record_from_row(evidence)) != fingerprint):
                raise ValueError('Completed work changed; review required')
            return {'evidence_id': previous['evidence_id'], 'status': 'unchanged'}
        head, now = _fence(connection, lease)
        result = ledger.append_evidence(connection, expected_previous_id=expected_previous_id, **kwargs)
        _append(connection, dict(head, sequence_no=head['sequence_no']+1, state='completed',
            observed_at=now.isoformat(), evidence_id=result['evidence_id'],
            completion_sha256=ledger.digest(ledger.normalized_record(**kwargs))))
        return result
