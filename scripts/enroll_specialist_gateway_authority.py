"""One fixed manual authority enrollment; no SQL or grant body from a client.

The deployment retains a protected intent before dispatch. Existing or revoked
authority is never overwritten. This worker changes only the two control tables.
"""
import hashlib
import json
import os
from datetime import datetime,timezone
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.agent_governance import POLICY_VERSION
from ibkr_etl.agent_authority import PostgresAuthoritySource
from ibkr_etl.specialist_authority_runtime import authority_database,TENANT,HOST,DATABASE
from ibkr_etl.agent_observation_evidence import AGENT_PRINCIPAL


def verify_intent(intent):
    expected={'schema','operation_id','owner_sha256','source_sha256','expires_at','tenant_id','principal_id','subject_ids','tool_names'}
    import re,uuid
    if (not isinstance(intent,dict) or set(intent)!=expected or intent['schema']!='specialist-gateway-authority-enrollment-v1'
        or intent['tenant_id']!=TENANT or intent['principal_id']!=AGENT_PRINCIPAL
        or intent['subject_ids']!=['U16088571'] or intent['tool_names']!=['get_performance_series']
        or str(uuid.UUID(intent['operation_id']))!=intent['operation_id']
        or any(not isinstance(intent[k],str) or not re.fullmatch('[0-9a-f]{64}',intent[k]) for k in ('owner_sha256','source_sha256'))):
        raise ValueError('Exact reviewed interactive-read intent required')
    expiry=datetime.fromisoformat(intent['expires_at'])
    if expiry.utcoffset() is None:raise ValueError('Aware authority expiry required')
    return expiry


def enroll(c,intent):
    expiry=verify_intent(intent)
    with c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
        now=c.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if not 0<(expiry-now).total_seconds()<=8*86400: raise ValueError('Bounded unexpired authority required')
        users=c.execute('''SELECT u.entra_object_id FROM app_users u JOIN app_user_roles r
            ON r.tenant_id=u.tenant_id AND r.entra_object_id=u.entra_object_id
            WHERE u.tenant_id=%s AND u.status='active' AND r.role_name='owner' AND r.role_status='active'
              AND r.effective_from<=clock_timestamp() AND (r.effective_to IS NULL OR r.effective_to>=clock_timestamp())''',(TENANT,)).fetchall()
        if sum(hashlib.sha256(r['entra_object_id'].lower().encode()).hexdigest()==intent['owner_sha256'] for r in users)!=1:
            raise ValueError('Authenticated current database owner required')
        subjects=c.execute('''SELECT s.subject_id FROM platform_subjects s JOIN subject_feature_states f
            ON f.tenant_id=s.tenant_id AND f.subject_id=s.subject_id WHERE s.tenant_id=%s
            AND s.subject_id=ANY(%s) AND s.lifecycle_status='active'
            AND f.feature_key='ai_analyst' AND f.feature_state='active' ORDER BY s.subject_id''',
            (TENANT,intent['subject_ids'])).fetchall()
        if [r['subject_id'] for r in subjects]!=intent['subject_ids']: raise ValueError('Active approved account feature required')
        controls=c.execute('SELECT kill_switch,policy_version,revision FROM specialist_observation_controls WHERE tenant_id=%s FOR UPDATE',(TENANT,)).fetchall()
        grants=c.execute('''SELECT principal_id::text,specialist_role,subject_ids,resource_ids,tool_names,enabled,
            background_allowed,expires_at,policy_version,revision FROM specialist_observation_grants WHERE tenant_id=%s FOR UPDATE''',(TENANT,)).fetchall()
        wanted=dict(principal_id=AGENT_PRINCIPAL,specialist_role='data_analyst',subject_ids=intent['subject_ids'],resource_ids=[],
            tool_names=intent['tool_names'],enabled=True,background_allowed=False,expires_at=expiry,policy_version=POLICY_VERSION,revision=1)
        control=dict(kill_switch=False,policy_version=POLICY_VERSION,revision=1)
        if controls or grants:
            if controls!=[control] or grants!=[wanted]: raise ValueError('Existing/revoked authority differs; no implicit reset')
            return False
        c.execute('INSERT INTO specialist_observation_controls(tenant_id,kill_switch,policy_version,revision) VALUES(%s,FALSE,%s,1)',(TENANT,POLICY_VERSION))
        c.execute('''INSERT INTO specialist_observation_grants(tenant_id,principal_id,specialist_role,subject_ids,
            resource_ids,tool_names,enabled,background_allowed,expires_at,policy_version,revision)
            VALUES(%s,%s,'data_analyst',%s,'{}',%s,TRUE,FALSE,%s,%s,1)''',
            (TENANT,AGENT_PRINCIPAL,intent['subject_ids'],intent['tool_names'],expiry,POLICY_VERSION))
        return True


def main():
    intent=json.loads(os.environ['GATEWAY_ENROLLMENT_INTENT']);verify_intent(intent)
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=15,row_factory=dict_row,
        options='-c statement_timeout=60000 -c lock_timeout=5000') as admin:
        if admin.info.host!=HOST or admin.info.dbname!=DATABASE:raise ValueError('Unexpected enrollment target')
        created=enroll(admin,intent)
    factory=lambda tenant:authority_database(os.environ['SPECIALIST_AUTHORITY_DATABASE_URL'],tenant)
    state=PostgresAuthoritySource(factory).load_current(tenant_id=TENANT,principal_id=AGENT_PRINCIPAL)
    if (state.kill_switch is not False or state.grant is None or state.grant.role!='data_analyst'
        or state.grant.subject_ids!=frozenset(intent['subject_ids']) or state.grant.resource_ids
        or state.grant.tool_names!=frozenset(intent['tool_names']) or state.grant.background_allowed
        or state.grant.expires_at!=datetime.fromisoformat(intent['expires_at'])):
        raise ValueError('Independent fresh-reader enrollment verification failed')
    print('SPECIALIST_GATEWAY_ENROLLMENT '+json.dumps(dict(status='passed',operation_id=intent['operation_id'],
        rows_inserted=2 if created else 0,exact_replay=not created,fresh_authority_verified=True,
        principal_id=AGENT_PRINCIPAL,subjects=intent['subject_ids'],tools=intent['tool_names'],
        expires_at=intent['expires_at'],background_allowed=False,source_rows_changed=0,
        existing_authority_overwritten=False,messages_sent=0)),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('SPECIALIST_GATEWAY_ENROLLMENT '+json.dumps(dict(status='failed',error_type=type(error).__name__,
            reason=str(error) if isinstance(error,ValueError) else 'Enrollment failed; inspect before retry')),flush=True)
        raise SystemExit(1)
