"""Isolated-schema PostgreSQL rehearsal; all DDL, roles and rows roll back.

No Flex requests, source writes, published reports, scheduler or approvals.
Launcher supplies the exact qualified sources and migrations.
"""
import hashlib,json,os,sys,types
from datetime import date
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

SCHEMA='workflow_dates_trial_20261006'
ROLE='ibkr_workflow_dates_trial_20261006'
CHECKS=[]


def main():
    for name,source in [('ibkr_etl.market_calendar',SOURCE_CALENDAR),('ibkr_etl.workflow_readiness',SOURCE_READINESS),
            ('ibkr_etl.workflow_ledger',SOURCE_LEDGER),('ibkr_etl.workflow_dates',SOURCE_DATES)]:
        module=types.ModuleType(name);sys.modules[name]=module
        exec(compile(source,name,'exec'),module.__dict__)
    ledger=sys.modules['ibkr_etl.workflow_ledger'];dates=sys.modules['ibkr_etl.workflow_dates']
    tenant=os.environ['PORTFOLIO_TENANT_ID'];checks=CHECKS
    def check(name,value):
        if not value:raise ValueError('Rehearsal check failed: '+name)
        checks.append(name)
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=25,
            options='-c statement_timeout=60000 -c lock_timeout=5000',row_factory=dict_row) as c:
        def absent():
            return (c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone() is None
                and c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone() is None)
        if not absent():raise ValueError('Trial scope already exists; no changes attempted')
        def rejected(name,call,error_type=ValueError):
            try:
                with c.transaction():call()
            except error_type:checks.append(name)
            else:raise ValueError('Expected rejection missing: '+name)
        with c.transaction(force_rollback=True):
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(SCHEMA)))
            c.execute(sql.SQL('SET LOCAL search_path TO {},public').format(sql.Identifier(SCHEMA)))
            c.execute(MIGRATION_054);c.execute(MIGRATION_060)
            c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT {} TO CURRENT_USER').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT USAGE ON SCHEMA {},public TO {}').format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON workflow_stage_evidence,workflow_shadow_decisions,workflow_cycle_date_policies TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(ROLE)))
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            scope=dict(tenant_id=tenant,cycle_key='rollback-dates-20261006',subject_id='rollback-fixture')
            cutoff=date(2026,10,5)
            expected={name:cutoff for name in ledger.PREREQUISITES};expected['benchmarks_fx']=date(2026,9,30)
            args=dict(**scope,cycle_cutoff=cutoff,calendar_version=dates.CALENDAR_VERSION,expected_dates=expected)
            policy=dates.append_policy(c,**args)
            check('policy_idempotent',dates.append_policy(c,**args)['policy_id']==policy['policy_id'])
            def decision():return ledger.record_shadow_decision(c,**scope,stage='assurance',expected_date=cutoff,date_policy_id=policy['policy_id'])
            check('missing_completions_block',not decision()['ready'])
            stage_args={};ids={}
            for index,(stage,parents) in enumerate(ledger.PREREQUISITES.items(),1):
                stage_args[stage]=dict(**scope,stage=stage,status='succeeded',validated=True,through_date=expected[stage],
                    input_sha256=f'{index:064x}',output_sha256=f'{index:064x}',producer='rollback-only-test',
                    dependency_ids={parent:ids[parent] for parent in parents},
                    date_policy_id=policy['policy_id'],date_policy_sha256=policy['policy_sha256'])
                ids[stage]=ledger.append_evidence(c,**stage_args[stage])['evidence_id']
            result=decision()
            check('mixed_holiday_dates_ready_without_authority',result['ready'] and not result['execution_authorized'])
            check('completion_restart_idempotent',ledger.append_evidence(c,**stage_args['risk'])['evidence_id']==ids['risk'])
            count=c.execute('SELECT COUNT(*) AS n FROM workflow_shadow_decisions').fetchone()['n'];decision()
            check('decision_restart_idempotent',c.execute('SELECT COUNT(*) AS n FROM workflow_shadow_decisions').fetchone()['n']==count)
            key=int.from_bytes(hashlib.sha256(ledger.canonical([tenant,scope['cycle_key'],scope['subject_id']]).encode()).digest()[:8],byteorder='big',signed=True)
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
                    options='-c statement_timeout=5000',row_factory=dict_row) as other:
                check('concurrent_cycle_writer_serialized',not other.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired',(key,)).fetchone()['acquired'])
            rejected('future_completion_rejected',lambda:ledger.append_evidence(c,**dict(stage_args['ingestion'],through_date=date(2026,10,6))))
            rejected('stale_completion_rejected',lambda:ledger.append_evidence(c,**dict(stage_args['ingestion'],through_date=date(2026,9,30))))
            rejected('legacy_downgrade_rejected',lambda:ledger.append_evidence(c,**{k:v for k,v in stage_args['ingestion'].items() if not k.startswith('date_policy_')}))
            rejected('wrong_policy_hash_rejected',lambda:ledger.append_evidence(c,**dict(stage_args['ingestion'],date_policy_sha256='f'*64)))
            rejected('wrong_cycle_policy_rejected',lambda:ledger.record_shadow_decision(c,**dict(scope,cycle_key='other'),stage='risk',expected_date=cutoff,date_policy_id=policy['policy_id']))
            rejected('wrong_account_policy_rejected',lambda:ledger.record_shadow_decision(c,**dict(scope,subject_id='other'),stage='risk',expected_date=cutoff,date_policy_id=policy['policy_id']))
            corrected=dict(args,expected_dates=dict(expected,benchmarks_fx=cutoff))
            rejected('late_policy_writer_rejected',lambda:dates.append_policy(c,**corrected))
            new_policy=dates.append_policy(c,**corrected,expected_previous_id=policy['policy_id'])
            rejected('late_completion_policy_rejected',lambda:ledger.append_evidence(c,**stage_args['ingestion']))
            rejected('old_policy_decision_rejected',decision)
            policy=new_policy
            check('policy_correction_blocks_old_completions',not decision()['ready'])
            reverted=dates.append_policy(c,**args,expected_previous_id=policy['policy_id'])
            check('policy_value_reversion_uses_new_identity',reverted['policy_id'] not in (policy['policy_id'],stage_args['ingestion']['date_policy_id']))
            policy=reverted
            check('value_reversion_still_requires_new_completions',not decision()['ready'])
            for stage,parents in ledger.PREREQUISITES.items():
                stage_args[stage].update(date_policy_id=policy['policy_id'],date_policy_sha256=policy['policy_sha256'],
                    dependency_ids={parent:ids[parent] for parent in parents})
                ids[stage]=ledger.append_evidence(c,**stage_args[stage],expected_previous_id=ids[stage])['evidence_id']
            check('rebound_chain_ready_without_authority',decision()['ready'] and not decision()['execution_authorized'])
            for table in ('workflow_cycle_date_policies','workflow_stage_evidence','workflow_shadow_decisions'):
                # CASCADE stays inside this isolated schema. Without it,
                # PostgreSQL's FK guard rejects the parent TRUNCATE before the
                # append-only trigger can be exercised.
                for operation in ('UPDATE '+table+' SET subject_id=subject_id','DELETE FROM '+table,'TRUNCATE '+table+' CASCADE'):
                    rejected('append_only_'+operation.split()[0].lower()+'_'+table,lambda query=operation:c.execute(query),psycopg.errors.RaiseException)
            other_tenant='00000000-0000-0000-0000-000000000002'
            rejected('cross_tenant_api_rejected',lambda:dates.append_policy(c,**dict(args,tenant_id=other_tenant)))
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(other_tenant,))
            check('rls_hides_other_tenant_policies',c.execute('SELECT COUNT(*) AS n FROM workflow_cycle_date_policies').fetchone()['n']==0)
            rejected('rls_cross_tenant_insert_rejected',lambda:c.execute('''INSERT INTO workflow_cycle_date_policies
                (tenant_id,policy_id,cycle_key,subject_id,sequence_no,cycle_cutoff,calendar_version,expected_dates_json,policy_version,record_sha256)
                VALUES (%s,gen_random_uuid(),'trial','trial',1,'2026-10-05','trial','{}','trial',%s)''',(tenant,'0'*64)),psycopg.errors.InsufficientPrivilege)
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            rejected('policy_cannot_authorize_execution',lambda:c.execute('''INSERT INTO workflow_cycle_date_policies
                (tenant_id,policy_id,cycle_key,subject_id,sequence_no,cycle_cutoff,calendar_version,expected_dates_json,policy_version,record_sha256,execution_authorized)
                VALUES (%s,gen_random_uuid(),'trial','trial',1,'2026-10-05','trial','{}','trial',%s,true)''',(tenant,'0'*64)),psycopg.errors.CheckViolation)
            authority=c.execute('''SELECT COUNT(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m') AND
                  (has_table_privilege(current_user,t.oid,'INSERT') OR has_table_privilege(current_user,t.oid,'UPDATE')
                   OR has_table_privilege(current_user,t.oid,'DELETE') OR has_table_privilege(current_user,t.oid,'TRUNCATE'))''').fetchone()['n']
            check('trial_role_has_no_application_writes',authority==0)
        check('schema_and_role_rollback_verified',absent())
        print('WORKFLOW_DATES_TRIAL '+json.dumps(dict(status='passed',checks=checks,checks_passed=len(checks),
            mode='rollback_only',persistent_schema_created=False,persistent_roles_created=False,
            execution_authorized=False,migration_sha256=hashlib.sha256(MIGRATION_060.encode()).hexdigest())),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('WORKFLOW_DATES_TRIAL '+json.dumps(dict(status='error',error_type=type(error).__name__,
            checks_completed=CHECKS,reason=str(error) if isinstance(error,ValueError) else 'database/runtime check failed')),flush=True)
        raise SystemExit(1)
