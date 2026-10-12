"""Candidate collector through the existing snapshot role, strictly read-only."""
import hashlib,json,os
from pathlib import Path
from urllib.parse import urlsplit,unquote
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload

def main():
    if os.getuid()!=10001:raise ValueError('Non-root required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed source differs')
    url=os.environ['DATABASE_URL'];parsed=urlsplit(url)
    if parsed.hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com' or parsed.path!='/ibkr_staging':raise ValueError('Unexpected endpoint')
    tenant='00000000-0000-0000-0000-000000000001';results=[]
    class ReadOnly(PostgresConnection):
        def execute(self,sql,parameters=None):
            if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
            return super().execute(sql,parameters)
        def commit(self):raise ValueError('Commit prohibited')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=_hybrid_row_factory,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c app.tenant_id='+tenant) as raw:
        identity=dict(raw.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone())
        if identity!={'role':unquote(parsed.username),'ro':'on','ssl':True}:raise ValueError('Configured role, read-only and verified TLS required')
        p=raw.execute('SELECT rolsuper,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if any(p[k] for k in ('rolsuper','rolcreaterole','rolbypassrls')):raise ValueError('Restricted role required')
        db=ReadOnly(raw)
        for subject in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):
            currency='HKD' if subject.startswith('alpha-world-') else 'USD'
            tools=_collect_tool_results(db,tenant_id=tenant,subject_id=subject,period_end='2026-09-30',reporting_currency=currency)
            payload,quality,allowed,blockers=assemble_month_end_payload(subject_id=subject,period_start='2025-01-20',period_end='2026-09-30',reporting_currency=currency,tool_results=tools)
            if quality=='fail' or not (payload.get('monthly_reporting') or {}).get('available'):raise ValueError('Current collector quality failed: '+subject)
            results.append({'subject':subject,'passed':True,'quality_status':quality,'publication_allowed':allowed,'blockers':list(blockers)})
        raw.rollback()
    print('SNAPSHOT_ROLE_FINAL '+json.dumps({'passed':True,'image':IMAGE,'source_hashes':SOURCE_HASHES,'subjects':results,
        'database_writes':0,'database_role':identity['role'],'database_readonly':True,'verified_tls':True,'privileged_role':False,
        'reports_changed':False,'real_viewer_sign_in':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('SNAPSHOT_ROLE_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True)
        raise SystemExit(1)
