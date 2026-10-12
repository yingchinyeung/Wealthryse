"""PostgreSQL rehearsal of the shadow ledger; ALL DDL/data/roles roll back.

Requires launcher-supplied reviewed policy, ledger and migration source. No
source rows, financial values, Flex calls, production work items or schedules
are touched. Refuses to run once these tables exist persistently.
"""
import hashlib
import json
import os
import sys
import types
from datetime import date
import psycopg
from psycopg.rows import dict_row


def main():
    for name,source in [('ibkr_etl.workflow_readiness',SOURCE_POLICY),('ibkr_etl.workflow_ledger',SOURCE_LEDGER)]:
        module=types.ModuleType(name)
        sys.modules[name]=module
        exec(compile(source,name,'exec'),module.__dict__)
    ledger=sys.modules['ibkr_etl.workflow_ledger']
    policy=sys.modules['ibkr_etl.workflow_readiness']
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    role='ibkr_workflow_ledger_trial'
    names=('workflow_stage_evidence','workflow_shadow_decisions')
    checks=[]
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=25,
                         options='-c statement_timeout=60000 -c lock_timeout=5000',row_factory=dict_row) as c:
        def absent():
            return (all(c.execute('SELECT to_regclass(%s) AS found',('public.'+name,)).fetchone()['found'] is None for name in names)
                    and not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(role,)).fetchone()
                    and c.execute("SELECT to_regprocedure('public.reject_shadow_workflow_mutation()') AS found").fetchone()['found'] is None)
        if not absent():
            raise ValueError('Trial objects already exist; no changes attempted')
        with c.transaction(force_rollback=True):
            c.execute(MIGRATION_SQL)
            c.execute('CREATE ROLE ibkr_workflow_ledger_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
            c.execute('GRANT ibkr_workflow_ledger_trial TO CURRENT_USER')
            c.execute('GRANT USAGE ON SCHEMA public TO ibkr_workflow_ledger_trial')
            c.execute('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON workflow_stage_evidence,workflow_shadow_decisions TO ibkr_workflow_ledger_trial')
            c.execute('GRANT EXECUTE ON FUNCTION app_tenant_id() TO ibkr_workflow_ledger_trial')
            c.execute('SET LOCAL ROLE ibkr_workflow_ledger_trial')
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            scope=dict(tenant_id=tenant,cycle_key='rollback-test-20261001',subject_id='rollback-fixture')
            day=date(2026,9,30)
            def decision(**overrides):
                return ledger.record_shadow_decision(c,**dict(scope,**overrides),stage='assurance',expected_date=day)
            def check(name,condition):
                if not condition:
                    raise ValueError('Rehearsal check failed: '+name)
                checks.append(name)
            def rejected(name,call,error_type=ValueError):
                try:
                    with c.transaction():
                        call()
                except error_type:
                    checks.append(name)
                else:
                    raise ValueError('Expected rejection missing: '+name)
            check('missing_evidence_blocks',not decision()['ready'])
            arguments={}
            ids={}
            for i,(stage,parents) in enumerate(policy.PREREQUISITES.items(),1):
                arguments[stage]=dict(**scope,stage=stage,status='succeeded',validated=True,through_date=day,
                    input_sha256=f'{i:064x}',output_sha256=f'{i:064x}',producer='rollback-only-test',
                    dependency_ids={p:ids[p] for p in parents})
                ids[stage]=ledger.append_evidence(c,**arguments[stage])['evidence_id']
            ready=decision()
            check('complete_chain_ready_without_authority',ready['ready'] and ready['execution_authorized'] is False)
            count=c.execute('SELECT COUNT(*) AS n FROM workflow_shadow_decisions').fetchone()['n']
            decision()
            check('decision_idempotent',c.execute('SELECT COUNT(*) AS n FROM workflow_shadow_decisions').fetchone()['n']==count)
            check('completion_idempotent',ledger.append_evidence(c,**arguments['risk'])['evidence_id']==ids['risk'])
            lock_key=int.from_bytes(hashlib.sha256(ledger.canonical([tenant,scope['cycle_key'],scope['subject_id']]).encode()).digest()[:8],byteorder='big',signed=True)
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
                    options='-c statement_timeout=5000',row_factory=dict_row) as other_connection:
                check('concurrent_subject_writer_lock_held',not other_connection.execute(
                    'SELECT pg_try_advisory_xact_lock(%s) AS acquired',(lock_key,)).fetchone()['acquired'])
            check('cross_cycle_blocked',not decision(cycle_key='different-cycle')['ready'])
            check('cross_subject_blocked',not decision(subject_id='different-subject')['ready'])
            changed=dict(arguments['ingestion'],output_sha256='f'*64)
            corrected=ledger.append_evidence(c,**changed,expected_previous_id=ids['ingestion'])['evidence_id']
            check('upstream_correction_blocks_descendants',not decision()['ready'])
            rejected('duplicate_transitive_completion_blocked',lambda:ledger.append_evidence(c,**arguments['assurance']))
            rejected('late_writer_fenced',lambda:ledger.append_evidence(c,**dict(changed,output_sha256='e'*64),expected_previous_id=ids['ingestion']))
            rejected('old_parent_completion_fenced',lambda:ledger.append_evidence(c,**arguments['reconciliation'],expected_previous_id=ids['reconciliation']))
            reverted=ledger.append_evidence(c,**arguments['ingestion'],expected_previous_id=corrected)['evidence_id']
            check('value_reversion_appends_new_evidence',reverted not in (corrected,ids['ingestion']))
            check('value_reversion_still_requires_reconciliation',not decision()['ready'])
            for table in names:
                for operation in ('UPDATE '+table+' SET subject_id=subject_id','DELETE FROM '+table,'TRUNCATE '+table):
                    rejected('append_only_'+operation.split()[0].lower()+'_'+table,
                             lambda sql=operation:c.execute(sql),psycopg.errors.RaiseException)
            rejected('shadow_cannot_authorize_execution',lambda:c.execute("""INSERT INTO workflow_shadow_decisions
                (tenant_id,decision_id,cycle_key,subject_id,stage,expected_date,ready,reasons_json,evidence_ids_json,
                decision_sha256,policy_version,execution_authorized)
                VALUES (%s,gen_random_uuid(),'trial','trial','risk','2026-09-30',true,'[]','{}',%s,'trial',true)""",
                (tenant,'0'*64)),psycopg.errors.CheckViolation)
            other='00000000-0000-0000-0000-000000000002'
            rejected('cross_tenant_api_denied',lambda:decision(tenant_id=other))
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(other,))
            check('rls_hides_other_tenant',c.execute('SELECT COUNT(*) AS n FROM workflow_stage_evidence').fetchone()['n']==0)
            rejected('rls_rejects_cross_tenant_insert',lambda:c.execute("""INSERT INTO workflow_shadow_decisions
                (tenant_id,decision_id,cycle_key,subject_id,stage,expected_date,ready,reasons_json,evidence_ids_json,
                decision_sha256,policy_version) VALUES (%s,gen_random_uuid(),'trial','trial','risk','2026-09-30',false,'[]','{}',%s,'trial')""",
                (tenant,'0'*64)),psycopg.errors.InsufficientPrivilege)
            authority=c.execute("""SELECT COUNT(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m')
                  AND t.relname NOT IN ('workflow_stage_evidence','workflow_shadow_decisions')
                  AND (has_table_privilege(current_user,t.oid,'INSERT')
                    OR has_table_privilege(current_user,t.oid,'UPDATE')
                    OR has_table_privilege(current_user,t.oid,'DELETE')
                    OR has_table_privilege(current_user,t.oid,'TRUNCATE'))""").fetchone()['n']
            check('trial_role_has_no_application_writes',authority==0)
        if not absent():
            raise ValueError('Rollback cleanup verification failed')
        print('WORKFLOW_LEDGER_TRIAL '+json.dumps({'status':'passed','checks':checks,'checks_passed':len(checks),
             'mode':'rollback_only','persistent_tables_created':False,'persistent_roles_created':False,
             'migration_sha256':hashlib.sha256(MIGRATION_SQL.encode()).hexdigest()}),flush=True)


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('WORKFLOW_LEDGER_TRIAL '+json.dumps({'status':'error','error_type':type(error).__name__,
              'reason':str(error) if isinstance(error,ValueError) else 'database/runtime check failed'}),flush=True)
        raise SystemExit(1)
