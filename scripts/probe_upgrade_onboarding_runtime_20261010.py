"""Isolated original/candidate test: no normal onboarding, only SELECT-only TLS."""
import hashlib,json,os,runpy,socket,ssl,sys,types,unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch


def manifest():
    return {p.relative_to('/app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix!='.pyc'}


def deny(*args,**kwargs):raise RuntimeError('Connections forbidden during synthetic tests')


def main():
    before=manifest(); fingerprint=hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
    if len(before)!=EXPECTED['source_files'] or fingerprint!=EXPECTED['source_manifest_sha256']:
        raise ValueError('Exact application source changed')
    if os.getuid()!=10001 or sys.version.split()[0]!=EXPECTED['python'] or ssl.OPENSSL_VERSION!=EXPECTED['openssl']:
        raise ValueError('Runtime differs from native qualification')
    import psycopg
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny):
        entry=EXPECTED['entry_points_imported'][0]
        module=runpy.run_path('/app/scripts/'+entry,run_name='qualification_import')
        with patch.dict(os.environ,{},clear=True),patch.dict(module['main'].__globals__,connect=deny),patch('psycopg.connect',side_effect=deny) as db:
            try:module['main']()
            except RuntimeError as exc:
                if str(exc)!='DATABASE_URL must be PostgreSQL':raise
            else:raise ValueError('Missing credential accepted')
            db.assert_not_called()
        with patch.dict(os.environ,{},clear=True):
            suite=unittest.TestSuite()
            for name,source in TEST_SOURCES.items():
                m=types.ModuleType(name);exec(compile(source,name,'exec'),m.__dict__)
                suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(m))
            result=unittest.TextTestRunner(verbosity=1).run(suite)
            if not result.wasSuccessful() or result.testsRun!=EXPECTED['tests'] or result.skipped:raise ValueError('Runtime regression failed')
    url=os.environ['DATABASE_URL']
    if urlsplit(url).hostname!='ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com':raise ValueError('Unexpected database')
    with psycopg.connect(url,sslmode='verify-full',sslrootcert='/etc/ssl/certs/ca-certificates.crt',connect_timeout=20,
        options='-c default_transaction_read_only=on -c statement_timeout=10000') as c:
        row=c.execute("SELECT current_setting('transaction_read_only'), current_setting('default_transaction_read_only'), ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if row!=('on','on',True):raise ValueError('Read-only certificate-verified TLS required')
    if manifest()!=before:raise ValueError('Application files changed')
    print('UPGRADE_ONBOARDING_RUNTIME '+json.dumps({'passed':True,'source_files':len(before),'source_manifest_sha256':fingerprint,
        'python':sys.version.split()[0],'openssl':ssl.OPENSSL_VERSION,'tests':result.testsRun,
        'entry_points_imported':EXPECTED['entry_points_imported'],'missing_credentials_rejected':True,
        'verified_tls_readonly_connection':True,'normal_onboarding_executed':False,'financial_rows_read':0,
        'database_writes':0,'messages_sent':0,'arm_mutations':0}),flush=True)


if __name__=='__main__':main()
