"""Trusted audit service database boundary; no HTTP ingress or agent executor.

Checks the login's reachable SET roles as well as direct/column privileges.
Preflight never inserts synthetic activity into the production audit ledger.
"""
from urllib.parse import urlsplit, parse_qs

LOGIN='ibkr_specialist_audit_login'
ROLE='ibkr_specialist_audit_writer'
HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE='ibkr_staging'
TENANT='00000000-0000-0000-0000-000000000001'
TABLE='specialist_audit_events'


def validate_database_url(value):
    target=urlsplit(value)
    if (target.scheme!='postgresql' or target.username!=LOGIN or target.hostname!=HOST or target.port!=5432
            or target.path!='/'+DATABASE or target.fragment or not target.password
            or parse_qs(target.query,keep_blank_values=True)!={'sslmode':['require']}):
        raise ValueError('Unregistered audit database connection')
    return target


def bind_audit_session(c):
    if not c.autocommit or c.info.transaction_status!=0:
        raise ValueError('Dedicated idle audit connection required')
    if c.execute('SELECT session_user::text AS login').fetchone()['login']!=LOGIN:
        raise ValueError('Restricted authenticated audit login required')
    c.execute('SET ROLE ibkr_specialist_audit_writer')
    c.execute('SET search_path TO public')
    c.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
    c.execute("SET statement_timeout='60s'")
    c.execute("SET lock_timeout='5s'")
    c.execute("SET idle_in_transaction_session_timeout='60s'")


def verify_audit_access(c):
    identity=c.execute('SELECT current_user::text AS role,session_user::text AS login,app_tenant_id()::text AS tenant').fetchone()
    if identity!={'role':ROLE,'login':LOGIN,'tenant':TENANT}:
        raise ValueError('Unexpected audit identity or tenant')
    roles=c.execute('''SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=ANY(%s)''',([LOGIN,ROLE],)).fetchall()
    if ({r['rolname'] for r in roles}!={LOGIN,ROLE} or any(
            r['rolcanlogin']!=(r['rolname']==LOGIN) or any(r[k] for k in
            ('rolinherit','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')) for r in roles)):
        raise ValueError('Unexpected audit role attributes')
    reachable=c.execute("SELECT rolname FROM pg_roles WHERE pg_has_role(session_user,oid,'SET')").fetchall()
    if {r['rolname'] for r in reachable}!={LOGIN,ROLE}:
        raise ValueError('Audit login can assume unexpected roles')
    memberships=c.execute('''SELECT granted.rolname AS role,member.rolname AS member,
        m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m
        JOIN pg_roles granted ON granted.oid=m.roleid JOIN pg_roles member ON member.oid=m.member
        WHERE member.rolname=ANY(%s)''',([LOGIN,ROLE],)).fetchall()
    if memberships!=[{'role':ROLE,'member':LOGIN,'admin_option':False,'inherit_option':False,'set_option':True}]:
        raise ValueError('Audit role membership differs from registered capability')
    # Examine both session login and capability: NOINHERIT does not remove
    # privileges granted directly to the login or via PUBLIC.
    for principal in (LOGIN,ROLE):
        exposed=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
            AND t.relkind IN ('r','p','v','m','f') AND NOT (n.nspname='public' AND t.relname=%s)
            AND (has_table_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
              OR has_any_column_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'))''',
            (TABLE,principal,principal)).fetchone()['n']
        if exposed: raise ValueError('Audit identity can access unrelated tables')
        privileged=c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
              AND has_function_privilege(%s,p.oid,'EXECUTE')''',(principal,)).fetchone()['n']
        if privileged: raise ValueError('Audit identity can call privileged functions')
        schemas=c.execute('''SELECT count(*) AS n FROM pg_namespace WHERE nspname NOT IN
            ('pg_catalog','information_schema') AND nspname NOT LIKE 'pg_toast%%'
            AND has_schema_privilege(%s,oid,'CREATE')''',(principal,)).fetchone()['n']
        if schemas: raise ValueError('Audit identity can create schema objects')
        if c.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS allowed",(principal,)).fetchone()['allowed']:
            raise ValueError('Audit identity can create schemas')
        p=c.execute('''SELECT has_table_privilege(%s,'public.specialist_audit_events','SELECT') AS read,
            has_table_privilege(%s,'public.specialist_audit_events','INSERT') AS insert,
            has_table_privilege(%s,'public.specialist_audit_events','UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
            OR has_any_column_privilege(%s,'public.specialist_audit_events','UPDATE,REFERENCES') AS forbidden''',
            (principal,)*4).fetchone()
        if p!={'read':principal==ROLE,'insert':principal==ROLE,'forbidden':False}:
            raise ValueError('Unexpected audit table privileges')
    isolation=c.execute('''SELECT relrowsecurity,relforcerowsecurity FROM pg_class t
        JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=%s''',(TABLE,)).fetchone()
    if isolation!={'relrowsecurity':True,'relforcerowsecurity':True}:
        raise ValueError('Forced audit tenant isolation missing')
    triggers=c.execute('''SELECT tgname,tgenabled,p.proname FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid
        WHERE t.tgrelid='public.specialist_audit_events'::regclass AND NOT t.tgisinternal ORDER BY tgname''').fetchall()
    if triggers!=[{'tgname':name,'tgenabled':'O','proname':'guard_specialist_audit'} for name in
                  ('specialist_audit_append_only','specialist_audit_insert_guard')]:
        raise ValueError('Audit append-only guards missing or disabled')
    return {'database_access':'verified','reachable_roles_verified':True,'source_table_access':False,
            'source_mutation_authority':False,'ledger_append_only':True,'events_written':0}
