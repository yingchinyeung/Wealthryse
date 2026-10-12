"""Source-preserving native tests for two manual operational workflows."""
import hashlib,json,os,runpy,socket,ssl,sys,unittest
from pathlib import Path
from unittest.mock import patch


def manifest():
    return {p.relative_to('/app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix!='.pyc'}


def deny(*args,**kwargs):raise RuntimeError('Network is forbidden during offline qualification')


def main():
    stage=sys.argv[1];config=json.loads(Path('/qualification/config.json').read_bytes())
    if stage not in ('baseline','candidate') or os.getuid()!=10001:raise ValueError('Nonroot native qualification required')
    if any(os.environ.get(k) for k in ('DATABASE_URL','DATABASE_ADMIN_URL','AZURE_CLIENT_SECRET')):raise ValueError('No production credentials permitted')
    before=manifest();baseline=Path('/qualification/tests/baseline-source.json')
    if stage=='baseline':baseline.write_text(json.dumps(before,sort_keys=True))
    elif before!=json.loads(baseline.read_bytes()):raise ValueError('Installed application changed')
    import psycopg,sqlite3,lzma,azure.identity,azure.storage.blob
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny),patch.dict(os.environ,{},clear=True):
        module=runpy.run_path('/app/scripts/'+config['entry'],run_name='qualification_import')
        with patch.dict(module['main'].__globals__,connect=deny),patch('psycopg.connect',side_effect=deny) as db:
            try:module['main']()
            except RuntimeError as exc:
                if str(exc)!=config['missing_environment_error']:raise
            else:raise ValueError('Unconfigured operation accepted')
            db.assert_not_called()
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests'))
    if not result.wasSuccessful() or result.testsRun!=config['tests'] or result.skipped or before!=manifest():raise ValueError('Native behavioral or preservation check failed')
    tls=ssl.create_default_context()
    if not tls.check_hostname or tls.verify_mode!=ssl.CERT_REQUIRED or tls.cert_store_stats()['x509_ca']<1:raise ValueError('Verified TLS unavailable')
    if stage=='candidate' and Path('/bin/sh').exists():raise ValueError('Unexpected shell')
    print('WORKFLOW_NATIVE_PROOF '+json.dumps({'stage':stage,'passed':True,'tests':result.testsRun,
        'source_files':len(before),'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        'entry_points_imported':[config['entry']],'missing_credentials_rejected':True,'python':sys.version.split()[0],
        'openssl':ssl.OPENSSL_VERSION,'network_calls':0,'database_connections':0,'normal_workflow_executed':False}),flush=True)


if __name__=='__main__':main()
