"""Offline source-preserving runtime qualification; no owner task is launched."""
import hashlib, json, os, socket, sys, unittest
from pathlib import Path


def manifest():
    return {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}


def deny(*args, **kwargs): raise RuntimeError('External connections forbidden in qualification')


def main():
    stage = sys.argv[1]
    if stage not in ('baseline', 'candidate') or os.getuid() != 10001: raise ValueError('Invalid qualification stage')
    if any(os.environ.get(k) for k in ('DATABASE_URL', 'DATABASE_ADMIN_URL', 'OWNERTRIAL_ADMIN_URL', 'AZURE_CLIENT_SECRET')): raise ValueError('Credentials forbidden')
    socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
    sys.path.insert(0, '/qualification/scripts')
    import owner_task_provider_runtime as runtime
    if hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest() != '161db0ff2d9145f3fe72a2d96b0c6e8a2a71eec3fb2a3b0cff70e06aa85ec63d': raise ValueError('Runtime checker drift')
    before = manifest(); record = Path('/qualification/tests/baseline-source.json')
    if stage == 'baseline': record.write_text(json.dumps(before, sort_keys=True))
    elif before != json.loads(record.read_text()): raise ValueError('Installed application changed')
    descriptor = runtime.check_runtime()
    import ssl, sqlite3, lzma, psycopg, azure.identity, azure.storage.blob, requests
    context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED or context.cert_store_stats()['x509_ca'] < 1: raise ValueError('Default CA verification unavailable')
    if stage == 'candidate' and Path('/bin/sh').exists(): raise ValueError('Unexpected shell')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover('/qualification/tests', pattern='test_*.py'))
    if not result.wasSuccessful() or result.testsRun < 10 or result.skipped or manifest() != before: raise ValueError('Runtime qualification failed')
    print('OWNER_NATIVE_PROOF ' + json.dumps({'stage': stage, 'passed': True, 'tests': result.testsRun, 'source_files': len(before),
        'source_manifest_sha256': hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest(), 'runtime': descriptor,
        'default_tls_ca_verified': True, 'default_ca_count': context.cert_store_stats()['x509_ca'],
        'owner_payloads_executed': False, 'network_calls': 0, 'database_connections': 0}), flush=True)


if __name__ == '__main__': main()
