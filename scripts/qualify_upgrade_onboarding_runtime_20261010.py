"""Offline original/candidate comparison; no real onboarding or data writes."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import ssl
import sys
import unittest
from unittest.mock import patch


def deny(*args, **kwargs): raise RuntimeError('External connection forbidden')


def manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def main():
    stage = sys.argv[1]
    if stage not in ('baseline','candidate') or os.getuid() != 10001: raise ValueError('Nonroot qualified stage required')
    if any(os.environ.get(k) for k in ('DATABASE_URL','DATABASE_ADMIN_URL','AZURE_CLIENT_SECRET')):
        raise ValueError('No build credentials permitted')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    import psycopg, sqlite3, lzma, azure.identity, azure.storage.blob
    before = manifest(); path = Path('/qualification/tests/baseline-source.json')
    if stage == 'baseline': path.write_text(json.dumps(before,sort_keys=True))
    elif before != json.loads(path.read_bytes()): raise ValueError('Application files changed')
    entry = 'run_self_service_onboarding_analytics.py'
    module = runpy.run_path('/app/scripts/'+entry,run_name='qualification_import')
    with patch.dict(os.environ, {}, clear=True), patch.dict(module['main'].__globals__, connect=deny), patch('psycopg.connect',side_effect=deny) as db:
        try: module['main']()
        except RuntimeError as exc:
            if str(exc) != 'DATABASE_URL must be PostgreSQL': raise
        else: raise ValueError('Missing credential did not fail closed')
        db.assert_not_called()
    tls = ssl.create_default_context()
    if not tls.check_hostname or tls.verify_mode != ssl.CERT_REQUIRED or tls.cert_store_stats()['x509_ca'] < 1:
        raise ValueError('Certificate-verifying TLS unavailable')
    with patch.dict(os.environ, {}, clear=True):
        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests'))
    if not result.wasSuccessful() or result.testsRun < 20 or result.skipped or before != manifest():
        raise ValueError('Native T regression or source preservation failed')
    if stage == 'candidate' and Path('/bin/sh').exists(): raise ValueError('Unexpected shell')
    print('ONBOARDING_NATIVE_PROOF '+json.dumps({'stage':stage,'passed':True,'tests':result.testsRun,
        'source_files':len(before),'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest(),
        'entry_points_imported':[entry],'missing_credentials_rejected':True,'python':sys.version.split()[0],
        'openssl':ssl.OPENSSL_VERSION,'normal_onboarding_executed':False,'network_calls':0,'database_connections':0}),flush=True)


if __name__ == '__main__': main()
