"""Install only reviewed migration 058 and a dormant SELECT-only capability.

No login, enrollment, enabled grant, source update, runtime or schedule is created.
An existing partial/changed installation is rejected, never repaired implicitly.
MIGRATIONS and INSTALL_APPLY are injected by the reviewed manual dispatcher.
"""
import hashlib
import json
import os
import psycopg
from psycopg.rows import dict_row

EXPECTED = ('058_specialist_observation_authority.sql',)
ROLE = 'ibkr_specialist_authority_reader'
TABLES = ['specialist_observation_controls', 'specialist_observation_grants']
TENANT = '00000000-0000-0000-0000-000000000001'
ROLE_FLAGS = ('rolcanlogin','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls','rolinherit')


def inspect(c):
    return dict(
        versions={r['version']:r['sha256'] for r in c.execute(
            'SELECT version,sha256 FROM public.schema_migrations WHERE version=ANY(%s)',(list(EXPECTED),)).fetchall()},
        tables=c.execute('''SELECT relname,relkind,relrowsecurity,relforcerowsecurity
            FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public' AND relname=ANY(%s) ORDER BY relname''',(TABLES,)).fetchall(),
        roles=c.execute('''SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,
            rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s''',(ROLE,)).fetchall())


def verify(c, state):
    hashes={name:hashlib.sha256(MIGRATIONS[name].encode()).hexdigest() for name in EXPECTED}
    if state['versions']!=hashes or state['tables']!=[
        dict(relname=name,relkind='r',relrowsecurity=True,relforcerowsecurity=True) for name in TABLES]:
        raise ValueError('Authority migration or forced isolation differs')
    if state['roles']!=[dict(rolname=ROLE,**{flag:False for flag in ROLE_FLAGS})]:
        raise ValueError('Dormant authority role attributes differ')
    memberships=c.execute('''SELECT m.admin_option,m.inherit_option,m.set_option,
        m.member=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS is_installing_administrator,
        m.roleid=(SELECT oid FROM pg_roles WHERE rolname=%s) AS is_capability_role
        FROM pg_auth_members m WHERE m.roleid=(SELECT oid FROM pg_roles WHERE rolname=%s)
        OR m.member=(SELECT oid FROM pg_roles WHERE rolname=%s)''',(ROLE,)*3).fetchall()
    if any(not (m['is_installing_administrator'] and m['is_capability_role'] and m['admin_option'])
           or m['inherit_option'] or m['set_option'] for m in memberships):
        raise ValueError('Authority reader has executable or inherited memberships')
    # Inspect effective privileges, including PUBLIC and column grants.
    exposed=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
        AND t.relkind IN ('r','p','v','m','f') AND NOT (n.nspname='public' AND t.relname=ANY(%s))
        AND (has_table_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          OR has_any_column_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'))''',(TABLES,ROLE,ROLE)).fetchone()['n']
    if exposed: raise ValueError('Authority reader can access unrelated tables')
    for name in TABLES:
        p=c.execute('''SELECT has_table_privilege(%s,%s,'SELECT') AS read,
            has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR has_any_column_privilege(%s,%s,'INSERT,UPDATE,REFERENCES') AS forbidden''',
            (ROLE,'public.'+name)*3).fetchone()
        if p!={'read':True,'forbidden':False}: raise ValueError('Authority reader is not SELECT-only')
    privileged=c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
        AND has_function_privilege(%s,p.oid,'EXECUTE')''',(ROLE,)).fetchone()['n']
    if privileged: raise ValueError('Authority reader can execute privileged functions')
    schemas=c.execute('''SELECT count(*) AS n FROM pg_namespace WHERE nspname NOT IN
        ('pg_catalog','information_schema') AND nspname NOT LIKE 'pg_toast%%'
        AND has_schema_privilege(%s,oid,'CREATE')''',(ROLE,)).fetchone()['n']
    if schemas or c.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS allowed",(ROLE,)).fetchone()['allowed']:
        raise ValueError('Authority reader can create schema objects')
    access=c.execute('''SELECT has_schema_privilege(%s,'public','USAGE') AS usage,
        has_function_privilege(%s,'public.app_tenant_id()','EXECUTE') AS tenant_function''',(ROLE,ROLE)).fetchone()
    if access!={'usage':True,'tenant_function':True}: raise ValueError('Authority reader prerequisites missing')
    policies=c.execute('''SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename''',(TABLES,)).fetchall()
    if policies!=[dict(tablename=name,policyname='tenant_isolation',permissive='PERMISSIVE',
        roles=['public'],cmd='ALL',qual='(tenant_id = app_tenant_id())',with_check='(tenant_id = app_tenant_id())') for name in TABLES]:
        raise ValueError('Authority tenant policies differ')
    # No synthetic permissions are inserted into live authority tables.
    # Counts are explicitly scoped by the existing forced tenant policy.
    visible=c.execute('''SELECT
        (SELECT count(*) FROM public.specialist_observation_controls) AS controls,
        (SELECT count(*) FROM public.specialist_observation_grants) AS grants''').fetchone()
    if visible!={'controls':0,'grants':0}: raise ValueError('Expected dormant tenant authority store')
    return dict(migration_hashes=hashes,forced_rls_tables=2,nonlogin_roles=1,
        executable_role_memberships=0,creator_admin_only_memberships=len(memberships),
        capability_select_only=True,unrelated_table_access=False,privileged_function_access=False,
        schema_creation_authority=False,tenant_visible_authority_rows=visible)


def main():
    if tuple(MIGRATIONS)!=EXPECTED or type(INSTALL_APPLY) is not bool:
        raise ValueError('Only the reviewed authority migration is allowed')
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
        options='-c statement_timeout=30000 -c lock_timeout=5000',row_factory=dict_row) as c:
        if c.info.dbname!='ibkr_staging' or c.info.host!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':
            raise ValueError('Unexpected authority database target')
        with c.transaction():
            c.execute('SET LOCAL search_path TO public')
            c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended('specialist-metadata-install',0))")
            before=inspect(c)
            if any(before.values()):
                result=dict(state='already_installed',**verify(c,before))
            elif not INSTALL_APPLY:
                result=dict(state='ready_for_additive_install',existing_objects=before)
            else:
                for name in EXPECTED:
                    c.execute(MIGRATIONS[name])
                    c.execute('INSERT INTO public.schema_migrations(version,sha256,applied_at) VALUES (%s,%s,clock_timestamp())',
                        (name,hashlib.sha256(MIGRATIONS[name].encode()).hexdigest()))
                c.execute('CREATE ROLE ibkr_specialist_authority_reader NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                c.execute('GRANT USAGE ON SCHEMA public TO ibkr_specialist_authority_reader')
                c.execute('GRANT EXECUTE ON FUNCTION public.app_tenant_id() TO ibkr_specialist_authority_reader')
                c.execute('GRANT SELECT ON public.specialist_observation_controls,public.specialist_observation_grants TO ibkr_specialist_authority_reader')
                result=dict(state='installed_dormant',**verify(c,inspect(c)))
        if result['state'] in ('installed_dormant','already_installed'):
            # A new transaction proves committed state; no repair/retry on failure.
            with c.transaction():
                c.execute('SET TRANSACTION READ ONLY')
                c.execute('SET LOCAL search_path TO public')
                c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
                result['committed_readback']=verify(c,inspect(c))
    print('SPECIALIST_AUTHORITY_INSTALL '+json.dumps(dict(result,source_rows_changed=0,
        service_logins_created=0,agents_enabled=0,schedules_enabled=0,messages_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_AUTHORITY_INSTALL '+json.dumps({'state':'error','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Authority installation or readback failed; inspect before retry'}),flush=True)
        raise SystemExit(1)
