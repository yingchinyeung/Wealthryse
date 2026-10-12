"""Narrow first observation capability: authorization and NAV series only.

This is not a general analytics or raw-statement reader. No deployment or login
is performed here. The broker remains responsible for user/account entitlement;
database RLS isolates tenants, not individual users within the same tenant.
"""
import hashlib

ROLE='ibkr_specialist_observation_reader'
TRIAL_ROLE='ibkr_specialist_observation_trial'
SUPPORTED_TOOLS=frozenset({'get_performance_series'})
COLUMNS={
    'app_users':'tenant_id entra_object_id email display_name status',
    'app_user_roles':'tenant_id entra_object_id role_name role_status effective_from effective_to',
    'user_account_access':'tenant_id entra_object_id subject_id access_level',
    'platform_subjects':'tenant_id subject_id history_start_date lifecycle_status',
    'subject_feature_states':'tenant_id subject_id feature_key feature_state',
    'accounts':'tenant_id account_id',
    'nav_snapshots':'account_id snapshot_date currency total_text asset_class',
    'derived_portfolio_nav':'tenant_id portfolio_id snapshot_date currency total_nav',
    'subject_nav_series':'tenant_id subject_id source_kind snapshot_date currency total_text',
}
DENIED_COLUMNS=(('raw_reports','file_path'),('ibkr_flex_credentials','key_vault_secret_name'),
    ('nav_snapshots','cash_text'),('specialist_observation_grants','enabled'))


def grant_statements(role):
    if role not in (ROLE,TRIAL_ROLE): raise ValueError('Unregistered observation role')
    return ['GRANT USAGE ON SCHEMA public TO '+role]+[
        f"GRANT SELECT ({', '.join(columns.split())}) ON public.{table} TO {role}"
        for table,columns in sorted(COLUMNS.items())]


def verify_permissions(c,role):
    if role not in (ROLE,TRIAL_ROLE): raise ValueError('Unregistered observation role')
    attrs=c.execute('''SELECT rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls
        FROM pg_roles WHERE rolname=%s''',(role,)).fetchone()
    if not attrs or any(attrs.values()): raise ValueError('Observation capability has elevated attributes')
    if c.execute('''SELECT count(*) AS n FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member
        WHERE r.rolname=%s''',(role,)).fetchone()['n']:
        raise ValueError('Observation capability has memberships')
    rows=c.execute('''SELECT n.nspname AS schema,t.relname AS table,a.attname AS column,
          has_column_privilege(%s,t.oid,a.attnum,'SELECT') AS readable,
          has_column_privilege(%s,t.oid,a.attnum,'INSERT,UPDATE,REFERENCES') AS writable
        FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE t.relkind IN ('r','p','v','m','f') AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%' ''',(role,role)).fetchall()
    actual={(r['schema'],r['table'],r['column']) for r in rows if r['readable']}
    expected={('public',table,col) for table,cols in COLUMNS.items() for col in cols.split()}
    if actual!=expected or any(r['writable'] for r in rows):
        raise ValueError('Observation column permissions differ from exact registry')
    broad=c.execute('''SELECT count(*) AS n FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE t.relkind IN ('r','p','v','m','f') AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_toast%%'
          AND has_table_privilege(%s,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')''',(role,)).fetchone()['n']
    if broad: raise ValueError('Observation table-wide privileges present')
    if c.execute('''SELECT count(*) AS n FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND has_function_privilege(%s,p.oid,'EXECUTE')''',(role,)).fetchone()['n']:
        raise ValueError('Observation capability can execute privileged functions')
    if c.execute('''SELECT count(*) AS n FROM pg_namespace WHERE nspname NOT IN ('pg_catalog','information_schema')
        AND nspname NOT LIKE 'pg_toast%%' AND has_schema_privilege(%s,oid,'CREATE')''',(role,)).fetchone()['n']:
        raise ValueError('Observation capability can create schema objects')
    if c.execute("SELECT has_database_privilege(%s,current_database(),'CREATE') AS allowed",(role,)).fetchone()['allowed']:
        raise ValueError('Observation capability can create schemas')
    tables=sorted(set(COLUMNS)-{'subject_nav_series'})
    isolation=c.execute('''SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class t
        JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relname=ANY(%s)
        ORDER BY relname''',(tables,)).fetchall()
    if isolation!=[dict(relname=n,relrowsecurity=True,relforcerowsecurity=True) for n in tables]:
        raise ValueError('Observation forced tenant isolation missing')
    policies=c.execute('''SELECT tablename,policyname,permissive,roles,cmd,qual,with_check
        FROM pg_policies WHERE schemaname='public' AND tablename=ANY(%s) ORDER BY tablename''',(tables,)).fetchall()
    expected=[]
    for table in tables:
        expression='account_belongs_to_current_tenant(account_id)' if table=='nav_snapshots' else '(tenant_id = app_tenant_id())'
        policy=(table+'_tenant_policy' if table in ('platform_subjects','subject_feature_states','app_user_roles')
            else 'account_isolation' if table=='nav_snapshots' else 'tenant_isolation')
        expected.append(dict(tablename=table,policyname=policy,
            permissive='PERMISSIVE',roles=['public'],cmd='ALL',qual=expression,with_check=expression))
    if policies!=expected: raise ValueError('Observation tenant policies differ')
    view=c.execute("SELECT reloptions,pg_get_viewdef(oid,true) AS definition FROM pg_class WHERE oid='public.subject_nav_series'::regclass").fetchone()
    if not view or 'security_invoker=true' not in (view['reloptions'] or []):
        raise ValueError('Observation NAV view must use caller privileges')
    return dict(column_permissions_verified=len(expected_columns()),write_privileges=0,
        raw_statement_access=False,credential_access=False,forced_rls_tables=len(tables),
        nav_view_sha256=hashlib.sha256(view['definition'].encode()).hexdigest())


def expected_columns():
    return {(table,col) for table,cols in COLUMNS.items() for col in cols.split()}
