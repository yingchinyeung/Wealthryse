"""Independent installed website-role confirmation; SELECT-only, not viewer sign-in."""
import json,os
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row
from ibkr_etl import web

def main():
    url=os.environ['DATABASE_URL'];tenant='00000000-0000-0000-0000-000000000001'
    if os.getuid()!=10001 or urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Wrong runtime')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=dict_row,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=30000 -c app.tenant_id='+tenant) as db:
        identity=db.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkr_stg_web','ro':'on','ssl':True}:raise ValueError('Wrong read-only identity')
        rows=db.execute('SELECT artifact_release_id,revoked_at,revoked_by,state_revision FROM report_artifact_releases WHERE tenant_id=%s AND artifact_release_id IN (55,57) ORDER BY artifact_release_id',(tenant,)).fetchall()
        if len(rows)!=2 or rows[0]['artifact_release_id']!=55 or rows[0]['revoked_at'] is None or rows[0]['revoked_by']!='ms-september-disclaimer-duplicate-20261011-v1' or rows[0]['state_revision']!=2 or rows[1]!={'artifact_release_id':57,'revoked_at':None,'revoked_by':None,'state_revision':1}:raise ValueError('Unexpected corrected registration state')
        months=web._monthly_report_catalog(db,'alpha-world-ms')
        selected=[m for m in months if m['period_end']=='2026-09-30']
        if len(selected)!=1:raise ValueError('Expected one monthly catalogue')
        ids=[a['artifact_release_id'] for a in selected[0]['released_artifacts'] if a['report_type']=='factsheet']
        if ids!=[57]:raise ValueError('Website catalogue did not retain exactly the approved replacement')
        duplicates=db.execute("""SELECT count(*) AS n FROM (SELECT s.subject_id,s.period_end,r.report_type
            FROM report_artifact_releases a JOIN report_generation_runs r ON r.tenant_id=a.tenant_id AND r.generation_run_id=a.generation_run_id
            JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
            WHERE a.tenant_id=%s AND a.revoked_at IS NULL AND a.release_scope='internal_shareholder'
            GROUP BY s.subject_id,s.period_end,r.report_type HAVING count(*)>1) q""",(tenant,)).fetchone()['n']
        if duplicates!=0:raise ValueError('Duplicate active registrations remain')
        db.rollback()
    print('REPORT_HANDOVER_SCHEMA_FINAL '+json.dumps({'passed':True,'identity':identity,'database_writes':0,
        'website_catalogue_factsheet_release':57,'obsolete_release_withdrawn':55,'duplicate_active_release_groups':0,
        'real_viewer_sign_in':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORT_HANDOVER_SCHEMA_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e) if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
