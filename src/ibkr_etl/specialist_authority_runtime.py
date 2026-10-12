"""Dedicated authority reader boundary, never source data or grant administration."""
from contextlib import contextmanager
from urllib.parse import urlsplit,parse_qs

LOGIN='ibkr_specialist_authority_login'
ROLE='ibkr_specialist_authority_reader'
HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE='ibkr_staging'
TENANT='00000000-0000-0000-0000-000000000001'
TABLES=['specialist_observation_controls','specialist_observation_grants']


def validate_database_url(value):
    target=urlsplit(value)
    if (target.scheme!='postgresql' or target.username!=LOGIN or target.hostname!=HOST or target.port!=5432
        or target.path!='/'+DATABASE or target.fragment or not target.password
        or parse_qs(target.query,keep_blank_values=True)!={'sslmode':['require']}):
        raise ValueError('Unregistered authority database connection')
    return target


def bind_authority_session(c):
    # Called only within a new transaction; never accepts a caller-selected tenant.
    c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY')
    if c.execute('SELECT session_user::text AS login').fetchone()['login']!=LOGIN:
        raise ValueError('Restricted authenticated authority login required')
    c.execute('SET LOCAL ROLE ibkr_specialist_authority_reader')
    c.execute('SET LOCAL search_path TO public')
    c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
    c.execute("SET LOCAL statement_timeout='15s'")
    c.execute("SET LOCAL lock_timeout='2s'")
    c.execute("SET LOCAL idle_in_transaction_session_timeout='15s'")


@contextmanager
def authority_database(value,tenant_id):
    import psycopg
    from psycopg.rows import dict_row
    from .db import PostgresConnection
    validate_database_url(value)
    if tenant_id!=TENANT: raise ValueError('Unregistered authority tenant')
    with psycopg.connect(value,autocommit=True,connect_timeout=10,row_factory=dict_row) as raw:
        with raw.transaction():
            bind_authority_session(raw)
            verify_authority_access(raw)
            yield PostgresConnection(raw)


def verify_authority_access(c):
    identity=c.execute('''SELECT current_user::text AS role,session_user::text AS login,
        app_tenant_id()::text AS tenant,current_setting('transaction_read_only') AS read_only,
        current_setting('transaction_isolation') AS isolation''').fetchone()
    if identity!=dict(role=ROLE,login=LOGIN,tenant=TENANT,read_only='on',isolation='read committed'):
        raise ValueError('Unexpected authority identity or transaction')
    roles=c.execute('''SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=ANY(%s)''',([LOGIN,ROLE],)).fetchall()
    if ({r['rolname'] for r in roles}!={LOGIN,ROLE} or any(
        r['rolcanlogin']!=(r['rolname']==LOGIN) or any(r[k] for k in
        ('rolinherit','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')) for r in roles)):
        raise ValueError('Unexpected authority role attributes')
    reachable=c.execute("SELECT rolname FROM pg_roles WHERE pg_has_role(session_user,oid,'SET')").fetchall()
    if {r['rolname'] for r in reachable}!={LOGIN,ROLE}:
        raise ValueError('Authority login can assume unexpected roles')
    memberships=c.execute('''SELECT granted.rolname AS role,member.rolname AS member,
        m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m
        JOIN pg_roles granted ON granted.oid=m.roleid JOIN pg_roles member ON member.oid=m.member
        WHERE member.rolname=ANY(%s)''',([LOGIN,ROLE],)).fetchall()
    if memberships!=[dict(role=ROLE,member=LOGIN,admin_option=False,inherit_option=False,set_option=True)]:
        raise ValueError('Authority membership differs from registered capability')
    for principal in (LOGIN,ROLE):
        exposed=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
            AND t.relkind IN ('r','p','v','m','f') AND NOT (n.nspname='public' AND t.relname=ANY(%s))
            AND (has_table_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
              OR has_any_column_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'))''',(TABLES,principal,principal)).fetchone()['n']
        if exposed: raise ValueError('Authority identity can access unrelated tables')
        privileged=c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
            AND has_function_privilege(%s,p.oid,'EXECUTE')''',(principal,)).fetchone()['n']
        if privileged: raise ValueError('Authority identity can execute privileged functions')
        schemas=c.execute('''SELECT count(*) AS n FROM pg_namespace WHERE nspname NOT IN
            ('pg_catalog','information_schema') AND nspname NOT LIKE 'pg_toast%%'
            AND has_schema_privilege(%s,oid,'CREATE')''',(principal,)).fetchone()['n']
        if schemas: raise ValueError('Authority identity can create schema objects')
        if c.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS allowed",(principal,)).fetchone()['allowed']:
            raise ValueError('Authority identity can create schemas')
        for name in TABLES:
            p=c.execute('''SELECT has_table_privilege(%s,%s,'SELECT') AS read,
                has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(%s,%s,'INSERT,UPDATE,REFERENCES') AS forbidden''',(principal,'public.'+name)*3).fetchone()
            if p!=dict(read=principal==ROLE,forbidden=False):
                raise ValueError('Authority table privileges differ')
    isolation=c.execute('''SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class t
        JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=ANY(%s)
        ORDER BY relname''',(TABLES,)).fetchall()
    if isolation!=[dict(relname=n,relrowsecurity=True,relforcerowsecurity=True) for n in TABLES]:
        raise ValueError('Forced authority isolation missing')
    policies=c.execute('''SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename''',(TABLES,)).fetchall()
    if policies!=[dict(tablename=n,policyname='tenant_isolation',permissive='PERMISSIVE',roles=['public'],cmd='ALL',
        qual='(tenant_id = app_tenant_id())',with_check='(tenant_id = app_tenant_id())') for n in TABLES]:
        raise ValueError('Authority tenant policies differ')
    return dict(database_access='verified',reachable_roles_verified=True,source_table_access=False,
        source_mutation_authority=False,authority_mutation_allowed=False,authority_rows_written=0)
