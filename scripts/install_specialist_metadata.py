"""Add only migrations 056/057 and dormant capability roles, atomically.

No credentials, service memberships, runtime, schedule, source updates or email.
Injected MIGRATIONS contains exact source text and INSTALL_APPLY is explicit.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from ibkr_etl.specialist_service_access import ROLES, TABLES, grant_statements

EXPECTED = ('056_specialist_audit_ledger.sql','057_specialist_daily_delivery.sql')
FUNCTIONS = ('guard_specialist_audit','guard_specialist_daily_report')


def inspect(c):
    versions = {r['version']:r['sha256'] for r in c.execute(
        'SELECT version,sha256 FROM public.schema_migrations WHERE version=ANY(%s)',(list(EXPECTED),)).fetchall()}
    tables = [r['relname'] for r in c.execute('''SELECT relname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND relname=ANY(%s)''',(sorted(TABLES),)).fetchall()]
    roles = c.execute('SELECT rolname,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=ANY(%s)',
                      (list(ROLES.values()),)).fetchall()
    functions = [r['proname'] for r in c.execute('''SELECT proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND proname=ANY(%s)''',(list(FUNCTIONS),)).fetchall()]
    return dict(versions=versions,tables=sorted(tables),roles=roles,functions=sorted(functions))


def verify(c, state):
    expected_hashes = {name:hashlib.sha256(MIGRATIONS[name].encode()).hexdigest() for name in EXPECTED}
    if state['versions'] != expected_hashes or state['tables'] != sorted(TABLES) or state['functions'] != sorted(FUNCTIONS):
        raise ValueError('Unexpected installed metadata objects or migration hashes')
    if {r['rolname'] for r in state['roles']} != set(ROLES.values()) or any(
        any(r[field] for field in ('rolcanlogin','rolsuper','rolcreatedb','rolcreaterole','rolbypassrls','rolinherit')) for r in state['roles']):
        raise ValueError('Unexpected capability-role attributes')
    memberships = c.execute('''SELECT m.admin_option,m.inherit_option,m.set_option,
        m.member=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS is_installing_administrator,
        m.roleid IN (SELECT oid FROM pg_roles WHERE rolname=ANY(%s)) AS is_capability_role
        FROM pg_auth_members m WHERE
        m.roleid IN (SELECT oid FROM pg_roles WHERE rolname=ANY(%s)) OR
        m.member IN (SELECT oid FROM pg_roles WHERE rolname=ANY(%s))''',
        (list(ROLES.values()),list(ROLES.values()),list(ROLES.values()))).fetchall()
    # PostgreSQL grants a CREATEROLE creator administrative lifecycle rights.
    # Accept only that administrator's ADMIN-only link, never SET/INHERIT access.
    # Do not revoke the lifecycle grant and strand roles without their manager.
    if any(not (m['is_installing_administrator'] and m['is_capability_role'] and m['admin_option'])
           or m['inherit_option'] or m['set_option'] for m in memberships):
        raise ValueError('Dormant roles must have no executable or inherited memberships')
    if c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND relname=ANY(%s) AND relrowsecurity AND relforcerowsecurity''',
        (sorted(TABLES),)).fetchone()['n'] != 3:
        raise ValueError('Forced tenant isolation missing')
    return dict(migration_hashes=expected_hashes,forced_rls_tables=3,nonlogin_roles=3,
                executable_role_memberships=0,creator_admin_only_memberships=len(memberships))


def main():
    if tuple(MIGRATIONS) != EXPECTED: raise ValueError('Only reviewed additive migrations are allowed')
    with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True,connect_timeout=20,
            options='-c statement_timeout=30000 -c lock_timeout=5000',row_factory=dict_row) as c:
        if c.info.dbname != 'ibkr_staging' or c.info.host != 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':
            raise ValueError('Unexpected database target')
        with c.transaction():
            c.execute("SET LOCAL search_path TO public")
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended('specialist-metadata-install',0))")
            before = inspect(c)
            if any(before.values()):
                result = dict(state='already_installed',**verify(c,before))
            elif not INSTALL_APPLY:
                result = dict(state='ready_for_additive_install',existing_objects=before)
            else:
                for name in EXPECTED:
                    c.execute(MIGRATIONS[name])
                    c.execute('INSERT INTO public.schema_migrations(version,sha256,applied_at) VALUES (%s,%s,%s)',
                        (name,hashlib.sha256(MIGRATIONS[name].encode()).hexdigest(),datetime.now(timezone.utc).isoformat()))
                for capability,role in ROLES.items():
                    c.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS').format(sql.Identifier(role)))
                    for statement in grant_statements(capability): c.execute(statement)
                result = dict(state='installed_dormant',**verify(c,inspect(c)))
        # Separate readback after the actual commit, not merely in-transaction proof.
        if result['state'] in ('installed_dormant','already_installed'):
            result['committed_readback'] = verify(c,inspect(c))
    print('SPECIALIST_METADATA_INSTALL '+json.dumps(dict(result,source_rows_changed=0,service_logins_created=0,
        schedules_enabled=0,messages_sent=0)),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print('SPECIALIST_METADATA_INSTALL '+json.dumps({'state':'error','error_type':type(error).__name__,
            'reason':str(error) if isinstance(error,ValueError) else 'Database installation or readback failed; inspect before retry'}),flush=True)
        raise SystemExit(1)
