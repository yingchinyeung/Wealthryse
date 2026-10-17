"""Fixed restricted NAV reader connection; does not enroll or enable agents."""
from contextlib import contextmanager
from urllib.parse import urlsplit,parse_qs
from .specialist_observation_access import ROLE,SEARCH_PATH,verify_permissions

LOGIN='ibkr_specialist_observation_login'
HOST='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE='ibkr_staging'
TENANT='00000000-0000-0000-0000-000000000001'
NAV_VIEW_SHA256='cb08102f309992e2cd7dc097a9f0ad7a361480fbf67b1a27303ece79168c329d'


def validate_database_url(value):
    target=urlsplit(value)
    if (target.scheme!='postgresql' or target.username!=LOGIN or target.hostname!=HOST or target.port!=5432
        or target.path!='/'+DATABASE or target.fragment or not target.password
        or parse_qs(target.query,keep_blank_values=True)!={'sslmode':['require']}):
        raise ValueError('Unregistered observation database connection')
    return target


def bind_observation_session(c):
    c.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY')
    if c.execute('SELECT session_user::text AS login').fetchone()['login']!=LOGIN:
        raise ValueError('Restricted authenticated observation login required')
    c.execute('SET LOCAL ROLE ibkr_specialist_observation_reader')
    c.execute('SET LOCAL search_path TO '+SEARCH_PATH)
    c.execute("SELECT set_config('app.tenant_id',%s,true)",(TENANT,))
    c.execute("SET LOCAL statement_timeout='15s'")
    c.execute("SET LOCAL lock_timeout='2s'")
    c.execute("SET LOCAL idle_in_transaction_session_timeout='15s'")


def verify_observation_access(c):
    identity=c.execute('''SELECT current_user::text AS role,session_user::text AS login,
        app_tenant_id()::text AS tenant,current_setting('transaction_read_only') AS read_only,
        current_setting('transaction_isolation') AS isolation''').fetchone()
    if identity!=dict(role=ROLE,login=LOGIN,tenant=TENANT,read_only='on',isolation='read committed'):
        raise ValueError('Unexpected observation identity or transaction')
    attrs=c.execute('''SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,
        rolconnlimit FROM pg_roles WHERE rolname=%s''',(LOGIN,)).fetchone()
    if attrs!=dict(rolcanlogin=True,rolinherit=False,rolsuper=False,rolcreatedb=False,rolcreaterole=False,
        rolreplication=False,rolbypassrls=False,rolconnlimit=2):
        raise ValueError('Unexpected observation login attributes')
    reachable=c.execute("SELECT rolname FROM pg_roles WHERE pg_has_role(session_user,oid,'SET')").fetchall()
    if {r['rolname'] for r in reachable}!={LOGIN,ROLE}: raise ValueError('Unexpected reachable observation roles')
    memberships=c.execute('''SELECT granted.rolname AS role,m.admin_option,m.inherit_option,m.set_option
        FROM pg_auth_members m JOIN pg_roles granted ON granted.oid=m.roleid
        JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s''',(LOGIN,)).fetchall()
    if memberships!=[dict(role=ROLE,admin_option=False,inherit_option=False,set_option=True)]:
        raise ValueError('Observation login membership differs')
    exposed=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
        AND t.relkind IN ('r','p','v','m','f')
        AND (has_table_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
          OR has_any_column_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,REFERENCES'))''',(LOGIN,LOGIN)).fetchone()['n']
    if exposed: raise ValueError('Observation login has direct or inherited table access')
    if c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND has_function_privilege(%s,p.oid,'EXECUTE')''',(LOGIN,)).fetchone()['n']:
        raise ValueError('Observation login can execute privileged functions')
    if c.execute('''SELECT count(*) AS n FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema')
        AND nspname NOT LIKE 'pg_toast%%' AND has_schema_privilege(%s,oid,'CREATE')''',(LOGIN,)).fetchone()['n']:
        raise ValueError('Observation login can create schema objects')
    if c.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS allowed",(LOGIN,)).fetchone()['allowed']:
        raise ValueError('Observation login can create schemas')
    evidence=verify_permissions(c,ROLE)
    if evidence['nav_view_sha256']!=NAV_VIEW_SHA256: raise ValueError('Observation NAV view changed since rehearsal')
    return dict(evidence,database_access='verified',reachable_roles_verified=True,source_mutation_authority=False)


@contextmanager
def observation_database(value,tenant_id):
    import psycopg
    from psycopg.rows import dict_row
    from .db import PostgresConnection
    validate_database_url(value)
    if tenant_id!=TENANT: raise ValueError('Unregistered observation tenant')
    with psycopg.connect(value,autocommit=True,connect_timeout=10,row_factory=dict_row) as raw:
        with raw.transaction():
            bind_observation_session(raw)
            verify_observation_access(raw)
            yield PostgresConnection(raw)
