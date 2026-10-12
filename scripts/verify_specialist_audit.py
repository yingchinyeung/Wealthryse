"""Synthetic isolated schema/role trial; force-rollback ALL DDL/data/grants."""
import json
import os
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.specialist_audit import append_event


def main():
    schema='specialist_audit_trial'
    role='ibkr_specialist_audit_trial'
    tenant='00000000-0000-0000-0000-000000000001'
    other='00000000-0000-0000-0000-000000000002'
    checks=[]
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
            options='-c statement_timeout=30000 -c lock_timeout=5000',row_factory=dict_row) as c:
        def absent():
            return not c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(schema,)).fetchone() and not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
        if not absent():
            raise ValueError('Existing trial objects; refusing changes')
        def check(name,condition):
            if not condition:
                raise ValueError('Failed check: '+name)
            checks.append(name)
        def reject(name,call,error=Exception):
            try:
                with c.transaction():
                    call()
            except error:
                checks.append(name)
            else:
                raise ValueError('Missing rejection: '+name)
        with c.transaction(force_rollback=True):
            c.execute('CREATE SCHEMA specialist_audit_trial')
            c.execute('SET LOCAL search_path TO specialist_audit_trial,public')
            c.execute('CREATE TABLE tenants(tenant_id UUID PRIMARY KEY)')
            c.execute('INSERT INTO tenants VALUES (%s),(%s)',(tenant,other))
            c.execute("CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql AS $$ SELECT current_setting('app.tenant_id')::uuid $$")
            c.execute(MIGRATION_SQL)
            c.execute('CREATE ROLE ibkr_specialist_audit_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            c.execute('GRANT ibkr_specialist_audit_trial TO CURRENT_USER')
            # Grant mutation verbs in the isolated trial to prove triggers reject them.
            c.execute('GRANT USAGE ON SCHEMA specialist_audit_trial TO ibkr_specialist_audit_trial')
            c.execute('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON specialist_audit_events TO ibkr_specialist_audit_trial')
            c.execute('GRANT EXECUTE ON FUNCTION app_tenant_id(),guard_specialist_audit() TO ibkr_specialist_audit_trial')
            c.execute('SET LOCAL ROLE ibkr_specialist_audit_trial')
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            fields=dict(tenant_id=tenant,event_id='event-one',role='data_engineer',action='rebuild_derived_data',
                operation_id='operation-one',phase='started',evidence_sha256='a'*64)
            first=append_event(c,**fields)
            check('first_event_chained',first['sequence']==1 and first['previous_sha256']=='0'*64)
            check('idempotent_retry',append_event(c,**fields)==first)
            reject('idempotency_conflict',lambda:append_event(c,**dict(fields,phase='failed')),ValueError)
            second=append_event(c,**dict(fields,event_id='event-two',phase='succeeded'))
            check('second_event_chained',second['sequence']==2 and second['previous_sha256']==first['sha256'])
            def direct_insert(event,sequence=3,previous=second['sha256']):
                c.execute('''INSERT INTO specialist_audit_events
                    (tenant_id,sequence_no,event_id,event_json,previous_sha256,event_sha256)
                    VALUES (%s,%s,%s,%s::jsonb,%s,%s)''',
                    (tenant,sequence,'direct-event',json.dumps(event),previous,'f'*64))
            valid={**second,'sequence':3,'event_id':'direct-event','previous_sha256':second['sha256'],'sha256':'f'*64}
            reject('direct_sequence_gap_denied',lambda:direct_insert(dict(valid,sequence=4),sequence=4),psycopg.errors.RaiseException)
            reject('direct_wrong_hash_link_denied',lambda:direct_insert(dict(valid,previous_sha256='0'*64),previous='0'*64),psycopg.errors.RaiseException)
            reject('unstructured_payload_denied',lambda:direct_insert(dict(valid,prompt='not permitted')),psycopg.errors.CheckViolation)
            reject('unsupported_role_denied',lambda:direct_insert(dict(valid,role='owner')),psycopg.errors.CheckViolation)
            for verb in ('UPDATE specialist_audit_events SET event_id=event_id','DELETE FROM specialist_audit_events','TRUNCATE specialist_audit_events'):
                reject('reject_'+verb.split()[0].lower(),lambda sql=verb:c.execute(sql),psycopg.errors.RaiseException)
            reject('cross_tenant_writer_denied',lambda:append_event(c,**dict(fields,tenant_id=other)),ValueError)
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(other,))
            check('rls_hides_other_tenant',c.execute('SELECT count(*) AS n FROM specialist_audit_events').fetchone()['n']==0)
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            check('only_two_records',c.execute('SELECT count(*) AS n FROM specialist_audit_events').fetchone()['n']==2)
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=15,row_factory=dict_row) as second_connection:
                check('concurrent_writer_lock_held',not second_connection.execute(
                    "SELECT pg_try_advisory_xact_lock(hashtextextended('specialist-audit:' || %s,0)) AS acquired",(tenant,)).fetchone()['acquired'])
            check('no_production_table_writes',c.execute("""SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m') AND
                (has_table_privilege(current_user,t.oid,'INSERT') OR has_table_privilege(current_user,t.oid,'UPDATE')
                OR has_table_privilege(current_user,t.oid,'DELETE') OR has_table_privilege(current_user,t.oid,'TRUNCATE'))""").fetchone()['n']==0)
        check('all_trial_objects_rolled_back',absent())
    print('SPECIALIST_AUDIT_TRIAL '+json.dumps({'status':'passed','checks':checks,'checks_passed':len(checks),
        'persistent_changes':False,'mode':'isolated_schema_force_rollback'}),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('SPECIALIST_AUDIT_TRIAL '+json.dumps({'status':'error','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Database/runtime check failed'}),flush=True)
        raise SystemExit(1)
