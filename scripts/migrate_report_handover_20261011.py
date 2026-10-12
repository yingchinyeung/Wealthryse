"""Bounded additive migration 062; no report or approval data writes."""
import hashlib,json,os
from datetime import datetime,timezone
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row

VERSION='062_report_design_updates.sql'
DIGEST='63710f9e0d6d0e305dc8ec4fefd1389b781f864b35370c3005409fbdf5e00ae9'
TABLES=('qualified_report_designs','report_design_update_requests','active_report_designs')

def state(db):
    return {
        'ledger':[dict(r) for r in db.execute('SELECT version,sha256,applied_at FROM schema_migrations ORDER BY version')],
        'column':bool(db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='report_artifact_releases' AND column_name='superseded_by_artifact_release_id'").fetchone()),
        'tables':[r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name=ANY(%s) ORDER BY table_name",(list(TABLES),))],
        'web_privileges':dict(db.execute("SELECT has_table_privilege('ibkr_stg_web','report_artifact_releases','SELECT') AS select_ok,has_table_privilege('ibkr_stg_web','report_artifact_releases','UPDATE') AS update_ok,has_table_privilege('ibkr_stg_web','report_artifact_releases','INSERT') AS insert_ok").fetchone())}

def release_digest(db):
    rows=db.execute("SELECT to_jsonb(a)-'superseded_by_artifact_release_id' AS row FROM report_artifact_releases a ORDER BY tenant_id,artifact_release_id").fetchall()
    return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()

def main():
    if PHASE not in ('preflight','rehearse','apply') or os.getuid()!=10001:raise ValueError('Invalid execution')
    if hashlib.sha256(MIGRATION_SQL.encode()).hexdigest()!=DIGEST:raise ValueError('Unqualified migration')
    uri=os.environ['DATABASE_ADMIN_URL'];p=urlsplit(uri)
    if p.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or p.path!='/ibkr_staging':raise ValueError('Wrong database')
    with psycopg.connect(uri,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=15,row_factory=dict_row,
            options='-c statement_timeout=30000 -c lock_timeout=5000 -c app.tenant_id=00000000-0000-0000-0000-000000000001') as db:
        if PHASE=='preflight':db.execute('SET TRANSACTION READ ONLY')
        else:db.execute("SELECT pg_advisory_xact_lock(hashtextextended('report-handover-schema-062',0))")
        identity=db.execute("SELECT current_user AS role,current_database() AS database,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity['database']!='ibkr_staging' or not identity['ssl']:raise ValueError('Database/TLS differs')
        before=state(db)
        if before['column'] or before['tables'] or any(r['version']>=VERSION for r in before['ledger']):raise ValueError('Preserve pre-existing or newer schema')
        if not all(before['web_privileges'].values()):raise ValueError('Website report permissions incomplete')
        if PHASE!='preflight':
            if before!=EXPECTED:raise ValueError('Schema changed after preflight')
            db.execute('LOCK TABLE report_artifact_releases IN SHARE ROW EXCLUSIVE MODE')
            fingerprint=release_digest(db)
            db.execute(MIGRATION_SQL)
            constraints={r['conname'] for r in db.execute("SELECT conname FROM pg_constraint WHERE conrelid='public.report_artifact_releases'::regclass")}
            if not {'report_supersession_tenant_fk','report_artifact_no_self_supersession','superseded_report_is_withdrawn'}<=constraints:raise ValueError('Missing constraints')
            rls=db.execute('SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE relnamespace=\'public\'::regnamespace AND relname=ANY(%s)',(list(TABLES),)).fetchall()
            if len(rls)!=3 or any(not r['relrowsecurity'] or not r['relforcerowsecurity'] for r in rls):raise ValueError('Missing forced tenant RLS')
            if release_digest(db)!=fingerprint:raise ValueError('Report rows changed')
            db.execute('INSERT INTO schema_migrations(version,sha256,applied_at) VALUES (%s,%s,%s)',(VERSION,DIGEST,datetime.now(timezone.utc).isoformat()))
            after=state(db)
            if not after['column'] or len(after['tables'])!=3 or after['ledger'][:-1]!=before['ledger'] or after['web_privileges']!=before['web_privileges']:raise ValueError('Unexpected schema delta')
            if PHASE=='apply':db.commit()
            else:
                db.rollback()
                if state(db)!=before or release_digest(db)!=fingerprint:raise ValueError('Rollback failed')
                db.rollback()
        else:db.rollback()
    print('HANDOVER_MIGRATION_FINAL '+json.dumps({'passed':True,'phase':PHASE,'identity':identity,'before':before,'migration_sha256':DIGEST,
        'schema_committed':PHASE=='apply','rollback_verified':PHASE=='rehearse','report_data_rows_changed':0,'approval_changes':0}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('HANDOVER_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None),'message':str(e) if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
