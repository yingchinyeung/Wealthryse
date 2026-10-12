"""Trusted synthetic SQL trial in one blank disposable database.

The existing bootstrap secret is used only by this reviewed administrator
wrapper. The trusted acceptance harness receives role-bound connections to the
synthetic database; their underlying session still authenticates as the
administrator. This is SQL behavior acceptance, NOT credential containment.
Neither the factory nor the connection may be exposed to a model. No login is created.
Keep the trial until protected evidence is reviewed; cleanup is a separate run.
"""
import json
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

HOST = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
DATABASE = 'owner_codex_acceptance_20261003'
ROLE = 'ibkr_owner_codex_trial_20261003'
SCHEMA = 'owner_codex_059_trial'
MARKER = 'wealthryse-owner-task-synthetic-059-20261003-v1'
PREFIX = 'OWNER_TASK_POSTGRES_TRIAL '
TABLES = ('tenants','owner_task_authority_fixture','owner_task_worker_fixture',
    'owner_codex_sessions','owner_codex_tasks','owner_codex_task_events')
FUNCTIONS = ('app_tenant_id','guard_owner_codex_append_only','guard_owner_codex_task',
    'guard_owner_codex_event','guard_owner_codex_event_presence')


def validate_admin_url(value):
    params = conninfo_to_dict(value)
    if (set(params) - {'host','dbname','port','sslmode','user','password'}
            or params.get('host') != HOST or params.get('dbname') != 'ibkr_staging'
            or params.get('port', '5432') != '5432' or params.get('sslmode') != 'require'
            or not params.get('user') or not params.get('password')):
        raise ValueError('Unexpected administrator binding')
    return params


def metadata(admin):
    database = admin.execute("SELECT datname,shobj_description(oid,'pg_database') AS marker,"
        "pg_get_userbyid(datdba) AS owner FROM pg_database WHERE datname=%s", (DATABASE,)).fetchone()
    role = admin.execute("SELECT rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,"
        "rolreplication,rolbypassrls,shobj_description(oid,'pg_authid') AS marker "
        "FROM pg_roles WHERE rolname=%s", (ROLE,)).fetchone()
    return database, role


def validate_trial_memberships(members):
    # PostgreSQL 16 records the implicit creator grant and explicit self-grant
    # under distinct grantors. Never merge their options or modify the creator
    # lifecycle grant. The explicit operational link must not inherit rights.
    expected = [dict(member_is_connector=True,bootstrap_grantor=True,self_grantor=False,
        admin_option=True,inherit_option=False,set_option=False),
        dict(member_is_connector=True,bootstrap_grantor=False,self_grantor=True,
        admin_option=False,inherit_option=False,set_option=True)]
    if (members != expected or any(type(v) is not bool for row in members for v in row.values())):
        raise ValueError('Trial role creator and explicit non-inherited SET grants differ')


def validate_trial_database_acl(grants, connector):
    expected = {(ROLE,k,ROLE,False) for k in ('CONNECT','CREATE','TEMPORARY')} | {
        (connector,'CONNECT',ROLE,False)}
    if (type(connector) is not str or not connector or connector == ROLE
            or any(set(row) != {'grantee','privilege_type','grantor','is_grantable'}
                or type(row['is_grantable']) is not bool for row in grants)
            or len(grants) != len(expected)
            or {(row['grantee'],row['privilege_type'],row['grantor'],row['is_grantable'])
                for row in grants} != expected):
        raise ValueError('Trial database ACL is not limited to non-grantable owner-issued privileges')


def validate_trial_objects(namespaces, relations, functions):
    if (namespaces != [{'nspname': SCHEMA,'trial_owner':True}, {'nspname': 'public','trial_owner':False}]
            or any(type(row['trial_owner']) is not bool for row in namespaces)):
        raise ValueError('Disposable database contains an unregistered schema')
    # Inspect all user relation kinds: a sequence, foreign table, materialized
    # view or composite relation must not disappear behind a table-only filter.
    # Automatic constraint index names can be truncated by PostgreSQL; bind
    # those indexes to their exact table/column/primary/unique signature instead.
    relation_keys = {'nspname','relname','relkind','owner','index_table',
        'index_primary','index_unique','index_columns'}
    if any(set(row) != relation_keys or row['nspname'] != SCHEMA or row['owner'] != ROLE
            or row['relkind'] not in ('r','i') or type(row['relname']) is not str
            or not row['relname'] for row in relations):
        raise ValueError('Disposable database contains unregistered relations or sequences')
    tables = [row for row in relations if row['relkind']=='r']
    if (len(tables) != len(TABLES) or {row['relname'] for row in tables} != set(TABLES)
            or any(row['index_table'] is not None or row['index_primary'] is not None
                or row['index_unique'] is not None or row['index_columns'] != [] for row in tables)):
        raise ValueError('Disposable trial table inventory differs')
    indexes = [row for row in relations if row['relkind']=='i']
    expected = {
        ('tenants',True,True,('tenant_id',)),
        ('owner_task_authority_fixture',True,True,('tenant_id','owner_object_id')),
        ('owner_task_worker_fixture',True,True,('tenant_id','worker_id')),
        ('owner_codex_sessions',True,True,('tenant_id','owner_object_id','session_id')),
        ('owner_codex_sessions',False,True,('tenant_id','owner_object_id','idempotency_key')),
        ('owner_codex_tasks',True,True,('tenant_id','owner_object_id','task_id')),
        ('owner_codex_tasks',False,True,('tenant_id','owner_object_id','idempotency_key')),
        ('owner_codex_task_events',True,True,('tenant_id','owner_object_id','task_id','event_no')),
        ('owner_codex_tasks',False,False,('tenant_id','owner_object_id','session_id','created_at','task_id')),
        ('owner_codex_tasks',False,False,('tenant_id','owner_object_id','lease_expires_at')),
    }
    if (len(indexes) != len(expected)
            or any(type(row['index_primary']) is not bool or type(row['index_unique']) is not bool
                or type(row['index_columns']) is not list for row in indexes)
            or {(row['index_table'],row['index_primary'],row['index_unique'],tuple(row['index_columns']))
                for row in indexes} != expected):
        raise ValueError('Disposable trial index inventory differs')
    secondary = {row['relname'] for row in indexes if row['index_unique'] is False}
    if secondary != {'owner_codex_tasks_owner_history','owner_codex_tasks_leases'}:
        raise ValueError('Disposable trial secondary indexes differ')
    function_keys = {'nspname','proname','owner','pronargs','prokind','prosecdef','return_type'}
    if (len(functions) != len(FUNCTIONS) or {row['proname'] for row in functions} != set(FUNCTIONS)
            or any(set(row) != function_keys or row['nspname'] != SCHEMA or row['owner'] != ROLE
                or type(row['pronargs']) is not int or row['pronargs'] != 0 or row['prokind'] != 'f'
                or row['prosecdef'] is not False or row['return_type'] !=
                    ('uuid' if row['proname']=='app_tenant_id' else 'trigger') for row in functions)):
        raise ValueError('Disposable database contains unregistered functions')


def check_registered(admin):
    database, role = metadata(admin)
    if (not database or not role or database['marker'] != MARKER or role['marker'] != MARKER
            or database['owner'] != ROLE or any(role[k] for k in
                ('rolcanlogin','rolinherit','rolsuper','rolcreatedb','rolcreaterole','rolreplication','rolbypassrls'))):
        raise ValueError('Exact disposable database and NOLOGIN role required')
    members = admin.execute('SELECT member=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS member_is_connector,'
        'grantor=10 AS bootstrap_grantor,grantor=(SELECT oid FROM pg_roles WHERE rolname=current_user) AS self_grantor,'
        'admin_option,inherit_option,set_option FROM pg_auth_members '
        'WHERE roleid=(SELECT oid FROM pg_roles WHERE rolname=%s) ORDER BY grantor', (ROLE,)).fetchall()
    current = admin.execute('SELECT current_user AS name').fetchone()['name']
    validate_trial_memberships(members)
    if admin.execute('SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname=%s)', (ROLE,)).fetchone():
        raise ValueError('Trial role must not inherit or SET any other role')
    grants = admin.execute("SELECT pg_get_userbyid(a.grantee) AS grantee,a.privilege_type,"
        "pg_get_userbyid(a.grantor) AS grantor,a.is_grantable "
        "FROM pg_database d,LATERAL aclexplode(d.datacl) a WHERE d.datname=%s", (DATABASE,)).fetchall()
    validate_trial_database_acl(grants,current)


def setup(admin, params):
    if any(metadata(admin)):
        raise ValueError('Trial objects already exist; inspect before any retry')
    connector = admin.execute('SELECT current_user AS name').fetchone()['name']
    # Commit the role metadata atomically. CREATE DATABASE cannot be transactional.
    with admin.transaction():
        admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB '
            'NOCREATEROLE NOREPLICATION NOBYPASSRLS').format(sql.Identifier(ROLE)))
        admin.execute(sql.SQL('COMMENT ON ROLE {} IS {}').format(sql.Identifier(ROLE), sql.Literal(MARKER)))
        # Preserve the bootstrap-granted ADMIN-only lifecycle link. This creates
        # a distinct self-granted link; every option must be explicit so the
        # connector's INHERIT attribute cannot leak into it.
        admin.execute(sql.SQL('GRANT {} TO CURRENT_USER WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
            .format(sql.Identifier(ROLE)))
    admin.execute(sql.SQL('CREATE DATABASE {} OWNER {} TEMPLATE template0')
        .format(sql.Identifier(DATABASE), sql.Identifier(ROLE)))
    with admin.transaction():
        admin.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(ROLE)))
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(DATABASE), sql.Literal(MARKER)))
        admin.execute(sql.SQL('REVOKE ALL ON DATABASE {} FROM PUBLIC').format(sql.Identifier(DATABASE)))
        admin.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}')
            .format(sql.Identifier(DATABASE),sql.Identifier(connector)))
    with psycopg.connect(**dict(params, dbname=DATABASE), autocommit=True,
                         connect_timeout=20, row_factory=dict_row) as raw:
        if raw.info.host != HOST or raw.info.dbname != DATABASE:
            raise ValueError('Disposable setup connection differs')
        with raw.transaction():
            # Azure owns public through azure_pg_admin, not the trial owner.
            # Apply the revoke as the trusted administrator in this blank DB,
            # then verify its catalog before switching to the trial role.
            raw.execute('REVOKE ALL ON SCHEMA public FROM PUBLIC')
            if raw.execute("SELECT count(*) AS n FROM pg_namespace n,LATERAL "
                    "aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a "
                    "WHERE n.nspname='public' AND a.grantee=0").fetchone()['n']:
                raise ValueError('Disposable public schema still has PUBLIC privileges')
            raw.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(ROLE)))
            raw.execute(sql.SQL('CREATE SCHEMA {} AUTHORIZATION {}')
                .format(sql.Identifier(SCHEMA), sql.Identifier(ROLE)))
            raw.execute(sql.SQL('REVOKE ALL ON SCHEMA {} FROM PUBLIC').format(sql.Identifier(SCHEMA)))
    check_registered(admin)


def connection_factory(params):
    def connect():
        raw = psycopg.connect(**dict(params, dbname=DATABASE), autocommit=True,
            connect_timeout=20, row_factory=dict_row,
            options='-c statement_timeout=45000 -c lock_timeout=3000 -c idle_in_transaction_session_timeout=45000')
        try:
            if raw.info.host != HOST or raw.info.dbname != DATABASE:
                raise ValueError('Disposable test connection differs')
            raw.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(ROLE)))
            raw.execute(sql.SQL('SET search_path TO {},pg_catalog').format(sql.Identifier(SCHEMA)))
            row = raw.execute('SELECT current_user AS name,session_user AS original,rolsuper,rolbypassrls '
                'FROM pg_roles WHERE rolname=current_user').fetchone()
            if (not row or row['name'] != ROLE or row['original'] == ROLE
                    or row['rolsuper'] or row['rolbypassrls']):
                raise ValueError('Restricted SQL role binding failed')
            return raw
        except Exception:
            raw.close()
            raise
    return connect


def cleanup(admin, params):
    check_registered(admin)
    with psycopg.connect(**dict(params, dbname=DATABASE), autocommit=True,
                         connect_timeout=20, row_factory=dict_row) as raw:
        namespaces = raw.execute("SELECT nspname,nspowner=(SELECT oid FROM pg_roles WHERE rolname=%s) "
            "AS trial_owner FROM pg_namespace WHERE nspname !~ '^pg_' "
            "AND nspname<>'information_schema' ORDER BY nspname", (ROLE,)).fetchall()
        relations = raw.execute("SELECT n.nspname,c.relname,c.relkind,pg_get_userbyid(c.relowner) AS owner,"
            "t.relname AS index_table,i.indisprimary AS index_primary,i.indisunique AS index_unique,"
            "ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ordinal) "
            "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
            "WHERE k.ordinal<=i.indnkeyatts ORDER BY k.ordinal) AS index_columns "
            "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "LEFT JOIN pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_class t ON t.oid=i.indrelid "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'").fetchall()
        functions = raw.execute("SELECT n.nspname,p.proname,pg_get_userbyid(p.proowner) AS owner,"
            "p.pronargs,p.prokind,p.prosecdef,p.prorettype::regtype::text AS return_type "
            "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'").fetchall()
        validate_trial_objects(namespaces,relations,functions)
    # Never FORCE or terminate a session: an unknown live connection blocks cleanup.
    if admin.execute('SELECT count(*) AS n FROM pg_stat_activity WHERE datname=%s', (DATABASE,)).fetchone()['n']:
        raise ValueError('Disposable database still has a connection; cleanup stopped')
    admin.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(ROLE)))
    try:
        admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(DATABASE)))
    finally:
        admin.execute('RESET ROLE')
    with admin.transaction():
        # DROP ROLE removes this role's lifecycle membership as well.
        admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(ROLE)))
    if any(metadata(admin)):
        raise ValueError('Disposable cleanup readback failed')
    return dict(status='passed',stage='cleanup',trial_database_removed=True,trial_role_removed=True,
        production_database_touched=False,ibkr_source_writes=False,existing_service_grants_changed=False)


def main():
    stage = os.environ.get('OWNER_TASK_TRIAL_STAGE')
    if stage not in ('run','cleanup'):
        raise ValueError('Explicit trial stage required')
    params = validate_admin_url(os.environ['DATABASE_ADMIN_URL'])
    # The management connection is postgres, not the application database.
    with psycopg.connect(**dict(params, dbname='postgres'), autocommit=True,
            connect_timeout=20, row_factory=dict_row,
            options='-c statement_timeout=45000 -c lock_timeout=3000') as admin:
        if admin.info.host != HOST or admin.info.dbname != 'postgres':
            raise ValueError('Unexpected trial management target')
        if stage == 'cleanup':
            result = cleanup(admin, params)
        else:
            setup(admin, params)
            # Verify only receives factory-created, pre-bound restricted connections.
            result = verify_trial(connection_factory(params))
            check_registered(admin)
            result.update(stage='run',trial_database=DATABASE,trial_role=ROLE,trial_retained_for_review=True,
                fresh_login_verified=False,existing_service_grants_changed=False,
                production_migration_installed=False,production_database_touched=False,
                ibkr_source_writes=False,model_calls=0,owner_runtime_active=False)
    print(PREFIX + json.dumps(result,sort_keys=True), flush=True)
    if result.get('status') != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Provider/database text may contain query parameters; emit only fixed metadata.
        print(PREFIX + json.dumps(dict(status='failed',stage=os.environ.get('OWNER_TASK_TRIAL_STAGE'),
            error_type=type(error).__name__,reason='Trial stopped; inspect exact disposable objects before retry',
            automatic_retry=False,secret_exposed=False)), flush=True)
        raise SystemExit(1)
