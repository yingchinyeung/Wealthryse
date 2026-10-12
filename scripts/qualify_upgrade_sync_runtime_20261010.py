"""Offline native sync smoke/fixture checks; no credentials or network calls."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import unittest


def deny_network(*args, **kwargs):
    raise RuntimeError('Native qualification forbids network access')


def application_manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def main():
    stage = sys.argv[1]
    if stage not in ('baseline', 'candidate') or os.getuid() != 10001:
        raise ValueError('Non-root isolated baseline or candidate required')
    if any(os.environ.get(n) for n in ('DATABASE_URL', 'IBKR_FLEX_TOKEN', 'AZURE_CLIENT_SECRET')):
        raise ValueError('No live credentials allowed during qualification')
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    import ssl, lzma, sqlite3, uuid
    import numpy, pandas, psycopg, requests, azure.identity, azure.storage.blob
    from zoneinfo import ZoneInfo
    from ibkr_etl import daily_account_sync, pilot_import, pilot_snapshot, cloud_storage, onboarding
    assert lzma.decompress(lzma.compress(b'qualification')) == b'qualification'
    assert ZoneInfo('Asia/Hong_Kong')
    assert uuid.UUID(str(uuid.uuid4()))
    with sqlite3.connect(':memory:') as db:
        assert db.execute('select 1').fetchone() == (1,)
    before = application_manifest()
    if before['src/ibkr_etl/pilot_import.py'] != '0d6e0cd84cf2c3b8b2780e1601dddb8a71951a9a98f225247a39dd7a29bb83a0':
        raise ValueError('Installed Grok reconciliation correction must remain exact')
    if stage == 'baseline':
        Path('/qualification/tests/baseline-source.json').write_text(json.dumps(before, sort_keys=True))
    elif before != json.loads(Path('/qualification/tests/baseline-source.json').read_text()):
        raise ValueError('Candidate application source differs from installed immutable image')
    suite = unittest.defaultTestLoader.discover('/qualification/tests')
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun < 10 or application_manifest() != before:
        raise ValueError('Offline native tests or application preservation failed')
    if stage == 'candidate' and Path('/bin/sh').exists():
        raise ValueError('Minimal sync runtime unexpectedly contains a shell')
    print('SYNC_NATIVE_PROOF ' + json.dumps({'stage': stage, 'passed': True, 'tests': result.testsRun, 'skipped': len(result.skipped), 'postgres_replay_qualified': False, 'source_files': len(before), 'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(), 'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION, 'network_calls': 0, 'database_writes': 0, 'fixture_sqlite_only': True}), flush=True)


if __name__ == '__main__': main()
