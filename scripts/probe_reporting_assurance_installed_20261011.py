"""Installed assurance image through its normal database role; SELECT-only checks."""
import hashlib,json,math,os
from pathlib import Path
from urllib.parse import urlsplit,unquote
import psycopg
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.agent_tools import _sector_allocation

def main():
    if os.getuid()!=10001:raise ValueError('Non-root required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed source drift')
    url=os.environ['DATABASE_URL']
    if urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected production endpoint')
    tenant='00000000-0000-0000-0000-000000000001';results=[]
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=_hybrid_row_factory,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=30000 -c app.tenant_id='+tenant) as raw:
        identity=dict(raw.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone())
        if identity!={'role':unquote(urlsplit(url).username),'ro':'on','ssl':True}:raise ValueError('Configured read-only worker role and verified TLS required')
        privileges=raw.execute('SELECT rolsuper,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if any(privileges[k] for k in ('rolsuper','rolcreaterole','rolbypassrls')):raise ValueError('Non-privileged worker required')
        db=PostgresConnection(raw)
        for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
            sector=_sector_allocation(db,subject,'2026-09-30',{}).as_dict()['data']
            if not sector.get('available') or sector['classification_coverage_pct']<99.999 or not math.isclose(sum(r['weight'] for r in sector['allocation']),1,abs_tol=1e-10):raise ValueError('Incomplete sector coverage/weights: '+subject)
            if not math.isfinite(sector['gross_market_value']) or sector['gross_market_value']<=0:raise ValueError('Invalid gross exposure')
            if subject not in ('alpha-world-ms','alpha-world-t'):
                currency=db.execute('SELECT COALESCE(reporting_currency,base_currency) AS currency FROM platform_subjects WHERE subject_id=?',(subject,)).fetchone()['currency']
                if sector.get('currency')!=currency or sector.get('valuation_method')!='dated-reporting-fx-v1':raise ValueError('Reporting currency/method differs')
            results.append({'subject':subject,'passed':True,'position_date':sector['position_date'],'allocation_rows':len(sector['allocation'])})
        raw.rollback()
    print('REPORTING_ASSURANCE_INSTALLED_FINAL '+json.dumps({'passed':True,'image':IMAGE,'subjects':results,'source_hashes':SOURCE_HASHES,'database_writes':0,'database_role':identity['role'],'database_readonly':True,'verified_tls':True,'reports_changed':False,'privileged_role':False,'real_viewer_sign_in':'UNVERIFIED'}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_ASSURANCE_INSTALLED_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True)
        raise SystemExit(1)
