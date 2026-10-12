"""Native source-preserving qualification of the installed legacy ingestion app."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import unittest


def manifest():
    return {p.relative_to('/app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix!='.pyc'}


def deny(*args,**kwargs):raise RuntimeError('No external connections in native qualification')


def main():
    stage=sys.argv[1]
    if stage not in ('baseline','candidate') or os.getuid()!=10001:raise ValueError('Non-root native stage required')
    if any(os.environ.get(k) for k in ('DATABASE_URL','DATABASE_ADMIN_URL','IBKR_FLEX_TOKEN','AZURE_CLIENT_SECRET')):raise ValueError('Live credentials forbidden')
    socket.socket.connect=socket.socket.connect_ex=socket.create_connection=deny
    import ssl,sqlite3,lzma,psycopg,azure.identity,azure.storage.blob
    before=manifest();baseline=Path('/qualification/tests/baseline-source.json')
    if stage=='baseline':baseline.write_text(json.dumps(before,sort_keys=True))
    elif before!=json.loads(baseline.read_text()):raise ValueError('Installed legacy source changed')
    assert lzma.decompress(lzma.compress(b'fixture'))==b'fixture'
    with sqlite3.connect(':memory:') as db:assert db.execute('select 1').fetchone()==(1,)
    scripts=('run_cloud_job.py','run_cloud_historical_backfill.py','bootstrap_postgres.py','health_check.py',
             'refresh_reference_data.py','refresh_derived_portfolios.py','generate_daily_report.py','export_analyst_snapshot.py')
    for name in scripts:runpy.run_path('/app/scripts/'+name,run_name='qualification_import')
    sys.path.insert(0,'/qualification/tests')
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests',pattern='test_*.py'))
    if not result.wasSuccessful() or result.testsRun<30 or result.skipped or manifest()!=before:raise ValueError('Legacy native test or source preservation failure')
    if stage=='candidate' and Path('/bin/sh').exists():raise ValueError('Unexpected shell')
    print('LEGACY_NATIVE_PROOF '+json.dumps({'passed':True,'stage':stage,'tests':result.testsRun,'skipped':len(result.skipped),
          'entry_points_imported':len(scripts),'source_files':len(before),
          'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
          'python':sys.version.split()[0],'openssl':ssl.OPENSSL_VERSION,'external_connections':0,'production_writes':0}),flush=True)


if __name__=='__main__':main()
