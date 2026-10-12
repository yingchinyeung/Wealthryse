"""Synthetic access tests and bounded SELECT-only MS/T model-cash checks."""
import calendar,hashlib,io,json,math,os,socket,sys,types,unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
sys.path[:0]=['/app/src','/app/scripts','/app/identity-tests']
os.environ['PORTAL_TEST_RUNTIME']='/app'
from ibkr_etl import web
from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row


def deny(*args,**kwargs):raise RuntimeError('Network disabled for synthetic tests')


def main():
    if os.getuid()!=10001:raise ValueError('Nonroot required')
    for name,digest in EXPECTED_FILES.items():
        if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Candidate source changed')
    if MODE=='fallback' and Path('/app/src/ibkr_etl/model_cash_display.py').exists():raise ValueError('Unexpected fallback feature')
    suite=unittest.defaultTestLoader.discover('/app/identity-tests',pattern='test_snapshot_access_correction.py')
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny),patch.dict(os.environ,{'PORTAL_TEST_RUNTIME':'/app'},clear=True):
        if MODE!='fallback':
            for name,source in TEST_SOURCES.items():
                module=types.ModuleType(name);module.__file__='/qualification/'+name+'.py';exec(compile(source,module.__file__,'exec'),module.__dict__)
                suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        tests=unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    expected_count=14 if MODE=='fallback' else 42
    if not tests.wasSuccessful() or tests.testsRun!=expected_count or tests.skipped:raise ValueError('Access/cash regression failed')
    url=os.environ['DATABASE_URL'];tenant='00000000-0000-0000-0000-000000000001'
    if urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected database')
    checked=[]
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',row_factory=dict_row,connect_timeout=15,
            options='-c default_transaction_read_only=on -c statement_timeout=15000 -c app.tenant_id='+tenant) as connection:
        identity=connection.execute("SELECT current_user AS role,current_setting('transaction_read_only') AS ro,ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if identity!={'role':'ibkr_stg_web','ro':'on','ssl':True}:raise ValueError('Exact read-only web identity and verified TLS required')
        if MODE!='fallback':
            from ibkr_etl.model_cash_display import load_model_cash
            for subject in ('alpha-world-ms','alpha-world-t'):
                for year,months in ((2025,range(1,13)),(2026,range(1,10))):
                    for month in months:
                        date=f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}'
                        value=load_model_cash(connection,tenant_id=tenant,subject_id=subject,reporting_currency='HKD',as_of=date)
                        if not value or value['normalized_value']!=58 or value['is_broker_balance'] is not False or value['interest_bearing'] is not False or not math.isfinite(value['nav_weight']) or value['nav_weight']<=0 or value['as_of']>date:raise ValueError('Model cash history not qualified')
                        checked.append({'subject':subject,'requested_month':date,'as_of':value['as_of'],'passed':True})
    client=TestClient(web.app);statuses=[client.get('/readyz').status_code]
    with patch.dict(os.environ,{'DATABASE_URL':'postgresql://unavailable@127.0.0.1:1/unavailable'}):
        statuses.append(client.get('/readyz').status_code)
        if client.get('/healthz').json()!={'status':'ok'}:raise ValueError('Liveness differs')
    statuses.append(client.get('/readyz').status_code)
    if statuses!=[200,503,200]:raise ValueError('Readiness failure/recovery differs')
    print('MODEL_CASH_RUNTIME '+json.dumps({'passed':True,'mode':MODE,'tests':tests.testsRun,'checked_model_months':checked,'readiness_statuses':statuses,
        'verified_tls':True,'database_role':'ibkr_stg_web','database_readonly':True,'database_writes':0,'reports_changed':False,'real_viewer_sign_in_verified':False,
        'source_files':EXPECTED_FILES}),flush=True)


if __name__=='__main__':main()
