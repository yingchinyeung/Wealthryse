"""Credential-broker qualification with synthetic values and no external I/O."""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import unittest


def deny_network(*args, **kwargs):
    raise RuntimeError('Offline broker qualification forbids network access')


def manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def main():
    stage = sys.argv[1]
    if stage not in ('baseline', 'candidate') or os.getuid() != 10001:
        raise ValueError('Non-root qualification required')
    if any(os.environ.get(n) for n in ('DATABASE_URL', 'IBKR_FLEX_TOKEN', 'BROKER_AUTH', 'AZURE_CLIENT_SECRET')):
        raise ValueError('No live credentials permitted')
    socket.socket.connect = deny_network
    socket.create_connection = deny_network
    import ssl, uvicorn
    from ibkr_etl import credential_broker
    if Path(credential_broker.__file__) != Path('/app/src/ibkr_etl/credential_broker.py'):
        raise ValueError('Installed broker application required')
    before = manifest()
    baseline = Path('/qualification/tests/baseline-source.json')
    if stage == 'baseline':
        baseline.write_text(json.dumps(before, sort_keys=True))
    elif before != json.loads(baseline.read_text()):
        raise ValueError('Application bytes changed')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests'))
    if not result.wasSuccessful() or result.testsRun < 10 or result.skipped or before != manifest():
        raise ValueError('Broker regression or preservation failed')
    if stage == 'candidate' and Path('/bin/sh').exists():
        raise ValueError('Minimal runtime contains a shell')
    print('BROKER_NATIVE_PROOF ' + json.dumps({'passed': True, 'stage': stage, 'tests': result.testsRun,
          'source_files': len(before), 'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(),
          'broker_source_sha256': before['src/ibkr_etl/credential_broker.py'],
          'python': sys.version.split()[0], 'openssl': ssl.OPENSSL_VERSION,
          'external_network_calls': 0, 'secret_writes': 0, 'database_connections': 0}), flush=True)


if __name__ == '__main__':
    main()
