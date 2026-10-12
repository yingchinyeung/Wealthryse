"""Read-only runtime smoke check; never run the database bootstrap operation."""
import hashlib, json, os, runpy, socket, ssl
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch


def deny(*args, **kwargs): raise RuntimeError('Connection forbidden during import/guard qualification')


def main():
    # runtime_check is embedded verbatim by the trusted controller.
    descriptor = runtime_check.check_runtime()
    if descriptor != EXPECTED_RUNTIME: raise ValueError('Independent runtime descriptor differs')
    before = {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}
    if len(before) != 530 or hashlib.sha256(json.dumps(before, sort_keys=True).encode()).hexdigest() != EXPECTED_MANIFEST: raise ValueError('Installed application bytes differ')
    with patch.object(socket.socket, 'connect', deny), patch.object(socket, 'create_connection', deny):
        module = runpy.run_path('/app/scripts/bootstrap_postgres.py', run_name='qualification_import')
        with patch.dict(os.environ, {}, clear=True):
            try: module['main']()
            except RuntimeError as error:
                if str(error) != 'DATABASE_ADMIN_URL must be a PostgreSQL connection URL': raise ValueError('Unexpected missing-credential guard') from None
            else: raise ValueError('Bootstrap accepted missing credentials')
    context = ssl.create_default_context()
    if not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED or context.cert_store_stats()['x509_ca'] < 1: raise ValueError('Default TLS validation unavailable')
    import psycopg
    url = os.environ['DATABASE_ADMIN_URL']
    parsed = urlsplit(url)
    if parsed.hostname != 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com': raise ValueError('Unexpected bootstrap database host')
    # Explicit read-only options and verified hostname/certificate; no source rows
    # or credential values enter the output. No bootstrap/main call with secrets.
    with psycopg.connect(url, sslmode='verify-full', sslrootcert='/etc/ssl/certs/ca-certificates.crt',
        connect_timeout=20, options='-c default_transaction_read_only=on -c statement_timeout=10000') as connection:
        row = connection.execute("SELECT current_setting('transaction_read_only'), current_setting('default_transaction_read_only'), ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if row != ('on', 'on', True): raise ValueError('Read-only TLS connection not established')
    after = {p.relative_to('/app').as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix != '.pyc'}
    if after != before: raise ValueError('Application bytes changed')
    print('UPGRADE_BOOTSTRAP_RUNTIME ' + json.dumps({'passed': True, 'runtime': descriptor,
        'source_files': len(before), 'source_manifest_sha256': EXPECTED_MANIFEST, 'entrypoint_imported': True,
        'missing_credentials_rejected_before_network': True, 'verified_tls_readonly_connection': True,
        'financial_rows_read': 0, 'database_writes': 0, 'migrations_run': 0, 'roles_or_passwords_changed': False}), flush=True)


if __name__ == '__main__': main()
