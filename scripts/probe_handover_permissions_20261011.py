"""SELECT-only exact website grants and schema state."""
import json,os
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row

def main():
    uri=os.environ['DATABASE_ADMIN_URL'];p=urlsplit(uri)
    if p.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or p.path!='/ibkr_staging':raise ValueError('Wrong database')
    with psycopg.connect(uri,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=dict_row,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=30000') as db:
        columns=db.execute("SELECT column_name,has_column_privilege('ibkr_stg_web','report_artifact_releases',column_name,'SELECT') AS select_ok,has_column_privilege('ibkr_stg_web','report_artifact_releases',column_name,'INSERT') AS insert_ok,has_column_privilege('ibkr_stg_web','report_artifact_releases',column_name,'UPDATE') AS update_ok FROM information_schema.columns WHERE table_schema='public' AND table_name='report_artifact_releases' ORDER BY ordinal_position").fetchall()
        ledger=db.execute('SELECT version,sha256,applied_at FROM schema_migrations ORDER BY version').fetchall()
        db.rollback()
    print('HANDOVER_MIGRATION_FINAL '+json.dumps({'passed':True,'phase':'preflight','migration_sha256':MIGRATION_DIGEST,'report_data_rows_changed':0,'approval_changes':0,'columns':columns,'ledger':ledger}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('HANDOVER_MIGRATION_ERROR '+json.dumps({'type':type(e).__name__}),flush=True);raise SystemExit(1)
