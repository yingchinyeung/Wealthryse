"""Offline qualification of installed institutional/discovery/sector code."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import unittest
from unittest.mock import patch


def deny_network(*args, **kwargs):
    raise RuntimeError('Build qualification forbids external connections')


def manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def main():
    stage = sys.argv[1]
    if stage not in ('baseline', 'candidate') or os.getuid() != 10001:
        raise ValueError('Non-root baseline/candidate required')
    if any(os.environ.get(n) for n in ('DATABASE_URL', 'IBKR_FLEX_TOKEN', 'AZURE_CLIENT_SECRET')):
        raise ValueError('Live credentials forbidden')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny_network
    import ssl, sqlite3, lzma
    import psycopg, requests, numpy, pandas, azure.identity, azure.storage.blob
    from ibkr_etl import flex_discovery, flex_sources, sector_classification
    before = manifest()
    baseline = Path('/qualification/tests/baseline-source.json')
    if stage == 'baseline':
        baseline.write_text(json.dumps(before, sort_keys=True))
    elif before != json.loads(baseline.read_text()):
        raise ValueError('Installed application bytes changed')
    assert lzma.decompress(lzma.compress(b'fixture')) == b'fixture'
    with sqlite3.connect(':memory:') as db:
        assert db.execute('select 1').fetchone() == (1,)
    # Import every actual scheduled entry point, without executing its main.
    for name in ('dispatch_institutional_daily_work.py', 'run_institutional_flex_worker.py',
                 'discover_flex_sources.py', 'refresh_staging_sector_classifications.py'):
        runpy.run_path('/app/scripts/' + name, run_name='qualified_import')
    pipeline = runpy.run_path('/app/scripts/run_institutional_daily_pipeline.py', run_name='qualified_import')
    with patch('subprocess.run') as child:
        assert pipeline['main']() == 0
        assert len(child.call_args_list) == 2
        for call, name in zip(child.call_args_list, ('dispatch_institutional_daily_work.py', 'run_institutional_flex_worker.py')):
            assert call.args == ([sys.executable, '/app/scripts/' + name],)
            assert call.kwargs == {'cwd': Path('/app'), 'check': True, 'timeout': 2700}
    sys.path.insert(0, '/qualification/tests')
    suite = unittest.defaultTestLoader.discover('/qualification/tests', pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun < 20 or result.skipped or before != manifest():
        raise ValueError('Native tests/source preservation failed')
    if stage == 'candidate' and Path('/bin/sh').exists():
        raise ValueError('Unexpected shell in minimal runtime')
    print('INSTITUTIONAL_NATIVE_PROOF ' + json.dumps({'stage': stage, 'passed': True,
          'tests': result.testsRun, 'skipped': len(result.skipped), 'entry_points_imported': 5,
          'pipeline_dispatch_checked': True, 'source_files': len(before),
          'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
          'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION,
          'network_calls': 0, 'production_writes': 0, 'postgres_rehearsal_qualified': False}), flush=True)


if __name__ == '__main__': main()
