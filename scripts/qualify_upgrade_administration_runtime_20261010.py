"""Credential-free qualification of unchanged installed administration code."""
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import unittest
from unittest.mock import patch


def deny(*args, **kwargs):
    raise RuntimeError('Qualification forbids external connections')


def manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def main():
    stage = sys.argv[1]
    if stage not in ('baseline', 'candidate') or os.getuid() != 10001:
        raise ValueError('Non-root baseline/candidate required')
    if any(os.environ.get(k) for k in ('DATABASE_URL', 'DATABASE_ADMIN_URL', 'AZURE_CLIENT_SECRET')):
        raise ValueError('Credentials forbidden')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    import ssl, sqlite3, lzma, psycopg, azure.identity, azure.storage.blob
    before = manifest()
    baseline = Path('/qualification/tests/baseline-source.json')
    if stage == 'baseline': baseline.write_text(json.dumps(before, sort_keys=True))
    elif before != json.loads(baseline.read_text()): raise ValueError('Installed source changed')
    entries = ('run_autonomous_administrator.py', 'run_monthly_authority_review.py')
    for entry in entries:
        module = runpy.run_path('/app/scripts/' + entry, run_name='qualification_import')
        with patch.dict(os.environ, {}, clear=True), patch('psycopg.connect', side_effect=deny) as db:
            try: module['main']()
            except RuntimeError as error:
                if str(error) != 'DATABASE_URL is required': raise
            else: raise ValueError('Missing credential did not fail closed')
            db.assert_not_called()
    context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED or context.cert_store_stats()['x509_ca'] < 1:
        raise ValueError('TLS trust unavailable')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests'))
    if not result.wasSuccessful() or result.testsRun < 20 or result.skipped or before != manifest():
        raise ValueError('Native regression/source preservation failed')
    if stage == 'candidate' and Path('/bin/sh').exists(): raise ValueError('Unexpected shell')
    print('ADMINISTRATION_NATIVE_PROOF ' + json.dumps({'stage': stage, 'passed': True,
        'tests': result.testsRun, 'source_files': len(before),
        'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
        'entry_points_imported': list(entries), 'missing_credentials_rejected': True,
        'network_calls': 0, 'database_connections': 0, 'normal_administration_executed': False,
        'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION}), flush=True)


if __name__ == '__main__': main()
