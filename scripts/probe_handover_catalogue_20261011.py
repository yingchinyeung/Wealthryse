"""Native catalogue acceptance through website role; no sign-in claim."""
import hashlib,json,os
from pathlib import Path
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row
from ibkr_etl import web

def main():
    url=os.environ['DATABASE_URL'];tenant='00000000-0000-0000-0000-000000000001'
    if os.getuid()!=10001 or urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or urlsplit(url).path!='/ibkr_staging':raise ValueError('Wrong runtime')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Native source mismatch')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=dict_row,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=30000 -c app.tenant_id='+tenant) as db:
        identity=db.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkr_stg_web','ro':'on','ssl':True}:raise ValueError('Wrong website role')
        subjects=[]
        for subject in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):
            months=web._monthly_report_catalog(db,subject)
            if not months:raise ValueError('Missing subject catalogue')
            ids=sorted(a['artifact_release_id'] for m in months for a in m['released_artifacts'])
            if len(set(ids))!=len(ids):raise ValueError('Duplicate approved downloads')
            subjects.append({'subject':subject,'months':len(months),'released_ids':ids,'passed':True})
        db.rollback()
    print('REPORT_HANDOVER_SCHEMA_FINAL '+json.dumps({'passed':True,'identity':identity,'database_writes':0,
        'image':IMAGE,'source_hashes':SOURCE_HASHES,'subjects':subjects,'real_viewer_sign_in':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORT_HANDOVER_SCHEMA_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e) if isinstance(e,ValueError) else None}),flush=True)
        raise SystemExit(1)
