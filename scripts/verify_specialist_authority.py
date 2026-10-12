"""Committed isolated authority fixture, restricted reader, then exact cleanup.

No production authority tables/grants or runtime enrollment are installed.
"""
import json
import os
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from ibkr_etl.db import PostgresConnection
from ibkr_etl.agent_authority import PostgresAuthoritySource
from ibkr_etl.agent_governance import POLICY_VERSION

SCHEMA='specialist_authority_trial'
ROLE='ibkr_specialist_authority_trial'
TENANT='00000000-0000-0000-0000-000000000001'
OTHER='00000000-0000-0000-0000-000000000002'
PRINCIPAL='00000000-0000-0000-0000-000000000003'


def main():
    checks=[]
    def check(name,condition):
        if not condition: raise ValueError('Authority trial check failed: '+name)
        checks.append(name)
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,row_factory=dict_row) as admin:
        if admin.info.dbname!='ibkr_staging' or admin.info.host!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':
            raise ValueError('Unexpected authority trial database')
        def absent():
            return not admin.execute('SELECT 1 FROM pg_namespace WHERE nspname=%s',(SCHEMA,)).fetchone() and not admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(ROLE,)).fetchone()
        if not absent(): raise ValueError('Existing authority trial objects; refusing changes')
        created=False
        try:
            with admin.transaction():
                admin.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
                admin.execute('CREATE SCHEMA specialist_authority_trial')
                admin.execute('REVOKE ALL ON SCHEMA specialist_authority_trial FROM PUBLIC')
                admin.execute('SET LOCAL search_path TO specialist_authority_trial,public')
                admin.execute('CREATE TABLE tenants(tenant_id UUID PRIMARY KEY)')
                admin.execute('INSERT INTO tenants VALUES (%s),(%s)',(TENANT,OTHER))
                admin.execute("CREATE FUNCTION app_tenant_id() RETURNS uuid LANGUAGE sql AS $$ SELECT current_setting('app.tenant_id')::uuid $$")
                admin.execute(MIGRATION_SQL)
                admin.execute('CREATE ROLE ibkr_specialist_authority_trial NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS')
                admin.execute('GRANT ibkr_specialist_authority_trial TO CURRENT_USER')
                admin.execute('GRANT USAGE ON SCHEMA specialist_authority_trial TO ibkr_specialist_authority_trial')
                admin.execute('GRANT SELECT ON specialist_observation_controls,specialist_observation_grants TO ibkr_specialist_authority_trial')
                admin.execute('GRANT EXECUTE ON FUNCTION app_tenant_id() TO ibkr_specialist_authority_trial')
                admin.execute("INSERT INTO specialist_observation_controls (tenant_id,policy_version) VALUES (%s,%s)",(TENANT,POLICY_VERSION))
                admin.execute("""INSERT INTO specialist_observation_grants
                    (tenant_id,principal_id,specialist_role,subject_ids,tool_names,expires_at,policy_version)
                    VALUES (%s,%s,'data_analyst',ARRAY['synthetic-account'],ARRAY['get_portfolio_summary'],clock_timestamp()+interval '1 hour',%s)""",
                    (TENANT,PRINCIPAL,POLICY_VERSION))
            created=True
            @contextmanager
            def reader(tenant):
                with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=15,row_factory=dict_row) as raw:
                    raw.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY')
                    raw.execute('SET LOCAL ROLE ibkr_specialist_authority_trial')
                    raw.execute('SET LOCAL search_path TO specialist_authority_trial,public')
                    raw.execute("SET LOCAL statement_timeout='15s'")
                    raw.execute("SET LOCAL lock_timeout='2s'")
                    raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
                    yield PostgresConnection(raw)
            source=PostgresAuthoritySource(reader)
            def load(tenant=TENANT): return source.load_current(tenant_id=tenant,principal_id=PRINCIPAL)
            check('default_controls_disabled',load().grant is None)
            admin.execute("UPDATE specialist_authority_trial.specialist_observation_grants SET enabled=true,revision=revision+1 WHERE tenant_id=%s",(TENANT,))
            check('kill_switch_blocks_enabled_grant',load().grant is None)
            admin.execute("UPDATE specialist_authority_trial.specialist_observation_controls SET kill_switch=false,revision=revision+1 WHERE tenant_id=%s",(TENANT,))
            state=load();check('exact_registered_grant_loaded',state.grant is not None and state.grant.subject_ids==frozenset({'synthetic-account'}) and not state.grant.background_allowed)
            check('other_tenant_cannot_load_grant',load(OTHER).grant is None)
            with reader(TENANT) as c:
                check('restricted_reader_identity',c.execute('SELECT current_user AS name').fetchone()['name']==ROLE)
                check('reader_cannot_modify_authority',not c.execute("SELECT has_table_privilege(current_user,'specialist_authority_trial.specialist_observation_grants','INSERT,UPDATE,DELETE,TRUNCATE') AS allowed").fetchone()['allowed'])
                check('reader_cannot_create_objects',not c.execute("SELECT has_schema_privilege(current_user,'specialist_authority_trial','CREATE') AS allowed").fetchone()['allowed'])
                check('both_authority_tables_force_rls',c.execute("SELECT count(*) AS n FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='specialist_authority_trial' AND c.relname IN ('specialist_observation_controls','specialist_observation_grants') AND c.relrowsecurity AND c.relforcerowsecurity").fetchone()['n']==2)
            admin.execute("UPDATE specialist_authority_trial.specialist_observation_grants SET enabled=false,revision=revision+1 WHERE tenant_id=%s",(TENANT,))
            check('revocation_seen_without_cache',load().grant is None)
            admin.execute("UPDATE specialist_authority_trial.specialist_observation_grants SET enabled=true,expires_at=clock_timestamp()-interval '1 second' WHERE tenant_id=%s",(TENANT,))
            check('server_expiry_enforced',load().grant is None)
            admin.execute("UPDATE specialist_authority_trial.specialist_observation_grants SET expires_at=clock_timestamp()+interval '1 hour',tool_names=ARRAY['execute_sql'] WHERE tenant_id=%s",(TENANT,))
            try: load()
            except ValueError: checks.append('unknown_tool_rejected')
            else: raise ValueError('Unregistered tool was accepted')
        finally:
            if created:
                # Exact new trial schema only; never public or a computed broad target.
                with admin.transaction():
                    admin.execute('DROP SCHEMA specialist_authority_trial CASCADE')
                    admin.execute('DROP ROLE ibkr_specialist_authority_trial')
        check('trial_objects_removed',absent())
    print('SPECIALIST_AUTHORITY_TRIAL '+json.dumps({'status':'passed','checks':checks,'checks_passed':len(checks),
        'mode':'isolated_committed_schema_then_cleanup','production_authority_installed':False,
        'source_rows_changed':0,'agents_enabled':0,'messages_sent':0}),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_AUTHORITY_TRIAL '+json.dumps({'status':'failed','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Isolated authority acceptance failed; inspect cleanup before retry'}),flush=True)
        raise SystemExit(1)
