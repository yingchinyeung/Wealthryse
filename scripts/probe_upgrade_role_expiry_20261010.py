"""Read authoritative PostgreSQL login-role expiry; never read password hashes."""
import json, os
from urllib.parse import urlsplit
import psycopg
from psycopg.rows import dict_row


def main():
    uri = os.environ['DATABASE_ADMIN_URL']
    if urlsplit(uri).hostname != EXPECTED_HOST:
        raise ValueError('Exact database host required')
    with psycopg.connect(uri, row_factory=dict_row, connect_timeout=15,
            sslmode='verify-full', sslrootcert='/etc/ssl/certs/ca-certificates.crt',
            options='-c default_transaction_read_only=on -c statement_timeout=15000') as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        identity = db.execute("SELECT current_user AS role, current_setting('transaction_read_only') AS ro, ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()
        if not identity or identity['ro'] != 'on' or identity['ssl'] is not True:
            raise ValueError('Read-only verified TLS required')
        rows = db.execute("""SELECT rolname, rolvaliduntil::text AS valid_until,
            CASE WHEN rolvaliduntil IS NULL OR rolvaliduntil='infinity'::timestamptz
              THEN 'no_database_expiration' WHEN rolvaliduntil <= now()
              THEN 'expired' ELSE 'future_expiration' END AS expiry_state,
            rolsuper, rolcreaterole, rolcreatedb
            FROM pg_roles WHERE rolcanlogin ORDER BY rolname LIMIT 101""").fetchall()
        if len(rows) > 100:
            raise ValueError('Role count exceeds review scope')
    print('UPGRADE_ROLE_EXPIRY '+json.dumps({'passed':True,'host':EXPECTED_HOST,
        'connection_role':identity['role'],'roles':rows,'verified_tls':True,
        'database_readonly':True,'database_writes':0,'password_hashes_read':False,
        'credentials_changed':False,'actual_last_rotation_verified':False}), flush=True)


if __name__ == '__main__':
    main()
