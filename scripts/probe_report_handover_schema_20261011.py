"""Restricted website-role, SELECT-only report handover preflight."""
import json,os
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row

def main():
    url=os.environ['DATABASE_URL'];tenant='00000000-0000-0000-0000-000000000001'
    if os.getuid()!=10001 or urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected runtime')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=dict_row,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=30000 -c app.tenant_id='+tenant) as db:
        identity=db.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkr_stg_web','ro':'on','ssl':True}:raise ValueError('Incorrect read-only role')
        columns=[r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='report_artifact_releases' ORDER BY ordinal_position")]
        duplicates=db.execute("""SELECT count(*) AS n FROM (
            SELECT s.subject_id,s.period_end,r.report_type FROM report_artifact_releases a
            JOIN report_generation_runs r ON r.tenant_id=a.tenant_id AND r.generation_run_id=a.generation_run_id
            JOIN reporting_snapshots s ON s.tenant_id=r.tenant_id AND s.snapshot_id=r.snapshot_id
            WHERE a.tenant_id=%s AND a.revoked_at IS NULL AND a.release_scope='internal_shareholder'
            GROUP BY s.subject_id,s.period_end,r.report_type HAVING count(*)>1) q""",(tenant,)).fetchone()['n']
        db.rollback()
    print('REPORT_HANDOVER_SCHEMA_FINAL '+json.dumps({'passed':True,'identity':identity,'columns':columns,'duplicate_active_release_groups':duplicates,'database_writes':0}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORT_HANDOVER_SCHEMA_ERROR '+json.dumps({'type':type(e).__name__}),flush=True)
        raise SystemExit(1)
