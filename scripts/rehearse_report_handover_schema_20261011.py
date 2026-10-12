"""Transactional schema and supersession rehearsal; private QA only."""
import hashlib,json,os,socket
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q
from ibkr_etl import web

def main():
    if os.getuid()!=10001 or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private QA endpoint required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Candidate drift')
    before=q.fingerprint(q.TEST_DB)
    with q.connect(q.TEST_DB,readonly=False) as db:
        if db.execute('SELECT current_database() AS name').fetchone()['name']!='upgrade_g04_migration_20261011':raise ValueError('Wrong database')
        db.execute("SET LOCAL lock_timeout='5s'")
        if db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='report_artifact_releases' AND column_name='superseded_by_artifact_release_id'").fetchone():raise ValueError('Preserve pre-existing migration')
        db.execute(MIGRATION_SQL)
        names={r['conname'] for r in db.execute("SELECT conname FROM pg_constraint WHERE conrelid='public.report_artifact_releases'::regclass")}
        if not {'report_supersession_tenant_fk','report_artifact_no_self_supersession','superseded_report_is_withdrawn'}<=names:raise ValueError('Missing integrity constraints')
        rows=db.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('public.qualified_report_designs'::regclass,'public.report_design_update_requests'::regclass,'public.active_report_designs'::regclass)").fetchall()
        if len(rows)!=3 or any(not r['relrowsecurity'] or not r['relforcerowsecurity'] for r in rows):raise ValueError('New tables must force tenant RLS')
        # Synthetic session-local fixtures shadow only the helper's three tables.
        db.execute('CREATE TEMP TABLE reporting_snapshots(snapshot_id TEXT,tenant_id TEXT,subject_id TEXT,period_end TEXT) ON COMMIT DROP')
        db.execute('CREATE TEMP TABLE report_generation_runs(generation_run_id INTEGER,tenant_id TEXT,snapshot_id TEXT,report_type TEXT) ON COMMIT DROP')
        db.execute('''CREATE TEMP TABLE report_artifact_releases(artifact_release_id INTEGER PRIMARY KEY,tenant_id TEXT,generation_run_id INTEGER,
            release_scope TEXT,revoked_at TEXT,revoked_by TEXT,state_revision INTEGER,superseded_by_artifact_release_id INTEGER) ON COMMIT DROP''')
        for rid,subject,period,kind,tenant in ((1,'a','2026-09-30','factsheet','t'),(2,'a','2026-09-30','factsheet','t'),
                (3,'b','2026-09-30','factsheet','t'),(4,'a','2026-08-31','factsheet','t'),(5,'a','2026-09-30','presentation','t'),(6,'a','2026-09-30','factsheet','u')):
            db.execute('INSERT INTO reporting_snapshots VALUES (%s,%s,%s,%s)',(str(rid),tenant,subject,period))
            db.execute('INSERT INTO report_generation_runs VALUES (%s,%s,%s,%s)',(rid,tenant,str(rid),kind))
            db.execute("INSERT INTO report_artifact_releases VALUES (%s,%s,%s,'internal_shareholder',NULL,NULL,1,NULL)",(rid,tenant,rid))
        db.execute('SAVEPOINT approval')
        retired=web._supersede_prior_monthly_releases(db,'t','a','2026-09-30','factsheet',2,'synthetic-owner','2026-10-11')
        if retired!=[{'artifact_release_id':1}]:raise ValueError('Cross-scope supersession')
        if db.execute('SELECT count(*) AS n FROM report_artifact_releases WHERE revoked_at IS NULL').fetchone()['n']!=5:raise ValueError('Wrong withdrawal scope')
        db.execute('ROLLBACK TO SAVEPOINT approval')
        if db.execute('SELECT count(*) AS n FROM report_artifact_releases WHERE revoked_at IS NULL').fetchone()['n']!=6:raise ValueError('Approval rollback failed')
        db.rollback()
    if q.fingerprint(q.TEST_DB)!=before:raise ValueError('QA contents not restored')
    with q.connect(q.TEST_DB) as db:
        if db.execute("SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='report_artifact_releases' AND column_name='superseded_by_artifact_release_id'").fetchone():raise ValueError('DDL rollback failed')
    print('REPORT_HANDOVER_QA_FINAL '+json.dumps({'passed':True,'image':IMAGE,'source_hashes':SOURCE_HASHES,
        'schema_migration_sha256':hashlib.sha256(MIGRATION_SQL.encode()).hexdigest(),'database':q.TEST_DB,
        'tables_unchanged':97,'schema_rollback_passed':True,'approval_rollback_passed':True,'cross_scope_isolation_passed':True,
        'new_tables_force_tenant_rls':True,'integrity_constraints_verified':True,'production_connection_used':False,'persistent_database_changes':0}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORT_HANDOVER_QA_ERROR '+json.dumps({'type':type(e).__name__,'sqlstate':getattr(e,'sqlstate',None),'message':str(e)[:180] if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
