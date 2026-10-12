"""Trusted reporting worker: no agent SQL, arbitrary destination or raw-data access."""
from datetime import date
from urllib.parse import urlsplit, parse_qs

LOGIN = 'ibkr_specialist_report_login'
ROLE = 'ibkr_specialist_report_worker'
HOST = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE = 'ibkr_staging'
TENANT = '00000000-0000-0000-0000-000000000001'
TABLES = ['specialist_audit_events','specialist_daily_reports','specialist_report_deliveries']


def validate_database_url(value):
    target=urlsplit(value)
    if (target.scheme!='postgresql' or target.username!=LOGIN or target.hostname!=HOST or target.port!=5432
            or target.path!='/'+DATABASE or target.fragment or not target.password
            or parse_qs(target.query)!= {'sslmode':['require']}):
        raise ValueError('Unregistered reporting database connection')
    return target


def bind_report_session(c):
    if not c.autocommit or c.info.transaction_status!=0:
        raise ValueError('Dedicated idle reporting connection required')
    row=c.execute('SELECT session_user::text AS login').fetchone()
    if row['login']!=LOGIN: raise ValueError('Restricted reporting login required')
    c.execute('SET ROLE ibkr_specialist_report_worker')
    c.execute('SET search_path TO public')
    c.execute("SELECT set_config('app.tenant_id',%s,false)",(TENANT,))
    c.execute("SET statement_timeout='60s'")
    c.execute("SET lock_timeout='5s'")
    c.execute("SET idle_in_transaction_session_timeout='60s'")


def verify_report_access(c):
    """Read-only catalog checks, including PUBLIC and column-level grants."""
    identity=c.execute('SELECT current_user::text AS role,session_user::text AS login,app_tenant_id()::text AS tenant').fetchone()
    if identity!={'role':ROLE,'login':LOGIN,'tenant':TENANT}:
        raise ValueError('Unexpected reporting identity or tenant')
    rows=c.execute('''SELECT rolname,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=ANY(%s)''',([LOGIN,ROLE],)).fetchall()
    if len(rows)!=2 or any(any(r[k] for k in ('rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls')) for r in rows):
        raise ValueError('Privileged reporting identity rejected')
    exposed=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
        AND t.relkind IN ('r','p','v','m','f') AND NOT (n.nspname='public' AND t.relname=ANY(%s))
        AND (has_table_privilege(current_user,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          OR has_any_column_privilege(current_user,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'))''',(TABLES,)).fetchone()['n']
    if exposed: raise ValueError('Reporting identity can access unrelated application tables')
    elevated=c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
        AND has_function_privilege(current_user,p.oid,'EXECUTE')''').fetchone()['n']
    if elevated: raise ValueError('Callable privileged function requires separate review')
    if c.execute("SELECT has_schema_privilege(current_user,'public','CREATE') AS allowed").fetchone()['allowed']:
        raise ValueError('Reporting identity can create schema objects')
    if c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND relname=ANY(%s) AND relrowsecurity AND relforcerowsecurity''',(TABLES,)).fetchone()['n']!=3:
        raise ValueError('Forced tenant isolation missing')
    for table in TABLES:
        privileges=c.execute('''SELECT has_table_privilege(current_user,%s,'SELECT') AS read,
            has_table_privilege(current_user,%s,'INSERT') AS insert,
            has_table_privilege(current_user,%s,'UPDATE,DELETE,TRUNCATE') AS broad_write''',
            ('public.'+table,)*3).fetchone()
        if not privileges['read'] or privileges['insert']!=(table!='specialist_audit_events') or privileges['broad_write']:
            raise ValueError('Unexpected reporting table permissions')
    return {'database_access':'verified','source_table_access':False,'source_mutation_authority':False}


def run_report(c, archive, *, report_date: date, mode='verify'):
    bind_report_session(c)
    evidence=verify_report_access(c)
    if mode=='verify': return dict(evidence,mode=mode,messages_sent=0,reports_created=0)
    if mode!='build': raise ValueError('Unregistered reporting mode')
    from .agent_daily_report import prepare_daily_report
    from .agent_daily_inbox import publish_website_report
    result=prepare_daily_report(c,archive,tenant_id=TENANT,report_date=report_date)
    publication=publish_website_report(c,tenant_id=TENANT)
    return dict(evidence,mode=mode,report=result,website=publication,messages_sent=0)
