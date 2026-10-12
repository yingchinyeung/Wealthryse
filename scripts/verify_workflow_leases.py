"""Synthetic lease qualification in a private schema; all changes roll back.

Sources/migrations are supplied by the frozen-image launcher. Never calls Flex,
publishes files, alters schedules, installs production roles or changes data.
"""
import hashlib, json, os, sys, types
from datetime import date
from uuid import UUID
from unittest.mock import patch
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

SCHEMA='workflow_leases_trial_20261006'
ROLE='ibkr_workflow_leases_trial_20261006'
CHECKS=[]


def main():
    import ibkr_etl
    for name, source in [('market_calendar',SOURCE_CALENDAR),('workflow_readiness',SOURCE_READINESS),
            ('workflow_ledger',SOURCE_LEDGER),('workflow_dates',SOURCE_DATES),('workflow_leases',SOURCE_LEASES)]:
        module=types.ModuleType('ibkr_etl.'+name);sys.modules[module.__name__]=module
        setattr(ibkr_etl,name,module);exec(compile(source,module.__name__,'exec'),module.__dict__)
    ledger=ibkr_etl.workflow_ledger;dates=ibkr_etl.workflow_dates;leases=ibkr_etl.workflow_leases
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    def check(name,value):
        if not value:raise ValueError('Qualification failed: '+name)
        CHECKS.append(name)
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=25,
            options='-c statement_timeout=60000 -c lock_timeout=5000',row_factory=dict_row) as c:
        def absent():
            return (c.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone() is None
                and c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone() is None)
        if not absent():raise ValueError('Trial scope already exists; no changes attempted')
        def rejected(name,call,error_type=ValueError):
            try:
                with c.transaction():call()
            except error_type:CHECKS.append(name)
            else:raise ValueError('Expected rejection missing: '+name)
        with c.transaction(force_rollback=True):
            c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(SCHEMA)))
            c.execute(sql.SQL('SET LOCAL search_path TO {},public').format(sql.Identifier(SCHEMA)))
            for migration in (MIGRATION_054,MIGRATION_060,MIGRATION_061):c.execute(migration)
            c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT {} TO CURRENT_USER').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT USAGE ON SCHEMA {},public TO {}').format(sql.Identifier(SCHEMA),sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT SELECT,INSERT,UPDATE,DELETE,TRUNCATE ON workflow_stage_evidence,workflow_shadow_decisions,workflow_cycle_date_policies,workflow_lease_events TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO {}').format(sql.Identifier(ROLE)))
            c.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(ROLE)))
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
            def fixture(subject):
                scope=dict(tenant_id=tenant,cycle_key='rollback-leases-20261006',subject_id=subject)
                cutoff=date(2026,10,5);expected={s:cutoff for s in ledger.PREREQUISITES}
                expected['benchmarks_fx']=date(2026,9,30)
                args=dict(**scope,cycle_cutoff=cutoff,calendar_version=dates.CALENDAR_VERSION,expected_dates=expected)
                policy=dates.append_policy(c,**args)
                common=dict(**scope,status='succeeded',validated=True,through_date=cutoff,
                    input_sha256='a'*64,output_sha256='b'*64,producer='synthetic-rollback-only',
                    date_policy_id=policy['policy_id'],date_policy_sha256=policy['policy_sha256'])
                source=ledger.append_evidence(c,**common,stage='ingestion',dependency_ids={})
                return dict(**common,stage='reconciliation',dependency_ids={'ingestion':source['evidence_id']},method_sha256='c'*64),args,policy
            owner1=str(UUID(int=700));owner2=str(UUID(int=701))
            args,policy_args,policy=fixture('synthetic-account-a')
            first=leases.claim(c,owner_id=owner1,ttl_seconds=30,**args)
            check('shadow_claim_never_authorizes_execution',not first['execution_authorized'])
            check('restart_claim_does_not_extend_expiry',leases.claim(c,owner_id=owner1,**args)==first)
            rejected('competing_worker_rejected',lambda:leases.claim(c,owner_id=owner2,**args))
            scope={k:args[k] for k in ('tenant_id','cycle_key','subject_id')}
            key=int.from_bytes(hashlib.sha256(ledger.canonical(list(scope.values())).encode()).digest()[:8],byteorder='big',signed=True)
            with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
                    options='-c statement_timeout=5000',row_factory=dict_row) as other:
                check('concurrent_cycle_lock_held',not other.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired',(key,)).fetchone()['acquired'])
            beat=leases.heartbeat(c,first,ttl_seconds=1)
            check('heartbeat_retains_generation',beat['generation']==first['generation'] and beat['sequence_no']==2)
            # Synthetic expiry uses DATABASE wall clock even in this long transaction.
            c.execute('SELECT pg_sleep(1.1)')
            rejected('expired_worker_cannot_heartbeat',lambda:leases.heartbeat(c,first))
            rejected('expired_worker_cannot_complete',lambda:leases.complete(c,first,**args))
            second=leases.claim(c,owner_id=owner2,**args)
            check('recovery_has_new_generation_and_token',second['generation']==2 and second['token']!=first['token'])
            rejected('recovered_worker_fences_late_completion',lambda:leases.complete(c,first,**args))
            result=leases.complete(c,second,**args)
            check('completion_evidence_and_event_recorded',result['status']=='recorded' and c.execute("SELECT COUNT(*) AS n FROM workflow_lease_events WHERE payload_json->>'state'='completed'").fetchone()['n']==1)
            again=leases.complete(c,second,**args)
            check('lost_response_replay_is_idempotent',again['status']=='unchanged' and again['evidence_id']==result['evidence_id'])
            rejected('changed_completed_payload_rejected',lambda:leases.complete(c,second,**dict(args,output_sha256='d'*64)))
            rejected('closed_worker_cannot_heartbeat',lambda:leases.heartbeat(c,second))
            rejected('wrong_account_completion_rejected',lambda:leases.complete(c,second,**dict(args,subject_id='other')))
            initial=ledger.heads(c,**scope)['ingestion']
            correction={k:v for k,v in args.items() if k!='method_sha256'}
            correction.update(stage='ingestion',dependency_ids={},output_sha256='e'*64)
            ledger.append_evidence(c,**correction,expected_previous_id=initial['evidence_id'])
            rejected('corrected_parent_fences_completed_replay',lambda:leases.complete(c,second,**args))
            changed=dict(policy_args,expected_dates=dict(policy_args['expected_dates'],benchmarks_fx=date(2026,10,5)))
            dates.append_policy(c,**changed,expected_previous_id=policy['policy_id'])
            rejected('policy_correction_fences_completion_replay',lambda:leases.complete(c,second,**args))
            args2,_,_=fixture('synthetic-account-b')
            lease2=leases.claim(c,owner_id=owner1,**args2)
            # A journal failure must roll back its evidence, even if the caller catches it.
            before=c.execute('SELECT COUNT(*) AS n FROM workflow_stage_evidence').fetchone()['n']
            def unavailable(*unused):raise RuntimeError('synthetic journal failure')
            with patch.object(leases,'_append',side_effect=unavailable):
                rejected('journal_failure_raises',lambda:leases.complete(c,lease2,**args2),RuntimeError)
            check('journal_failure_cannot_leave_completion_evidence',c.execute('SELECT COUNT(*) AS n FROM workflow_stage_evidence').fetchone()['n']==before)
            leases.cancel(c,lease2)
            rejected('cancelled_worker_cannot_complete',lambda:leases.complete(c,lease2,**args2))
            for _ in range(2):leases.cancel(c,leases.claim(c,owner_id=owner2,**args2))
            rejected('bounded_three_attempt_recovery',lambda:leases.claim(c,owner_id=owner2,**args2))
            args3,_,_=fixture('synthetic-account-c')
            lease3=leases.claim(c,owner_id=owner1,**args3)
            ingestion=ledger.heads(c,**{k:args3[k] for k in scope})['ingestion']
            raw={k:v for k,v in args3.items() if k!='method_sha256'}
            raw.update(stage='ingestion',dependency_ids={},output_sha256='e'*64)
            ledger.append_evidence(c,**raw,expected_previous_id=ingestion['evidence_id'])
            rejected('upstream_correction_fences_worker',lambda:leases.complete(c,lease3,**args3))
            for query in ('UPDATE workflow_lease_events SET subject_id=subject_id','DELETE FROM workflow_lease_events','TRUNCATE workflow_lease_events'):
                rejected('append_only_'+query.split()[0].lower(),lambda q=query:c.execute(q),psycopg.errors.RaiseException)
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(str(UUID(int=2)),))
            check('tenant_rls_hides_lease_events',c.execute('SELECT COUNT(*) AS n FROM workflow_lease_events').fetchone()['n']==0)
            rejected('cross_tenant_claim_rejected',lambda:leases.claim(c,owner_id=owner1,**args3))
            authority=c.execute('''SELECT COUNT(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND t.relkind IN ('r','p','v','m') AND
                  (has_table_privilege(current_user,t.oid,'INSERT') OR has_table_privilege(current_user,t.oid,'UPDATE')
                   OR has_table_privilege(current_user,t.oid,'DELETE') OR has_table_privilege(current_user,t.oid,'TRUNCATE'))''').fetchone()['n']
            check('trial_role_has_no_application_writes',authority==0)
        check('schema_and_role_rollback_verified',absent())
    print('WORKFLOW_LEASES_TRIAL '+json.dumps(dict(status='passed',checks=CHECKS,checks_passed=len(CHECKS),
        mode='rollback_only',persistent_schema_created=False,persistent_roles_created=False,
        execution_authorized=False,migration_sha256=hashlib.sha256(MIGRATION_061.encode()).hexdigest())),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as error:
        print('WORKFLOW_LEASES_TRIAL '+json.dumps(dict(status='error',error_type=type(error).__name__,
            checks_completed=CHECKS,reason=str(error) if isinstance(error,ValueError) else 'database/runtime check failed')),flush=True)
        raise SystemExit(1)
