"""Build the full PostgreSQL schema in a disposable local database for QA.

Migrations 001-062 cannot replay on an empty server: early seed rows need the
tenant row and migration 027 needs existing subjects. This harness inserts the
tenant and synthetic subject rows as soon as their tables exist. It refuses any
non-local host, so it can never touch Azure. Not a production bootstrap.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECTS = (('U16088571', 'physical_account', 'USD'), ('U16088606', 'physical_account', 'USD'),
            ('hong-kong-china', 'filtered_portfolio', 'USD'), ('alpha-world-ms', 'composite_portfolio', 'HKD'),
            ('alpha-world-t', 'composite_portfolio', 'HKD'))


def local_only(url: str) -> None:
    parsed = urlparse(url)
    host = parsed.hostname or parse_qs(parsed.query).get('host', [''])[0]
    database = parsed.path.lstrip('/')
    if not (host in ('localhost', '127.0.0.1') or host.startswith('/')) or 'qa' not in database:
        raise SystemExit('Refusing: QA schema builds need a local database whose name contains "qa"')


def main(url: str) -> int:
    local_only(url)
    with psycopg.connect(url) as connection:
        connection.execute('''CREATE TABLE IF NOT EXISTS schema_migrations(
            version TEXT PRIMARY KEY, sha256 TEXT NOT NULL, applied_at TEXT NOT NULL)''')
        for path in sorted((PROJECT_ROOT / 'migrations/postgres').glob('*.sql')):
            if connection.execute('SELECT 1 FROM schema_migrations WHERE version=%s', (path.name,)).fetchone():
                continue
            content = path.read_text(encoding='utf-8')
            connection.execute(content)
            connection.execute('INSERT INTO schema_migrations VALUES (%s,%s,now()::text)',
                               (path.name, hashlib.sha256(content.encode('utf-8')).hexdigest()))
            if connection.execute("SELECT to_regclass('public.tenants')").fetchone()[0]:
                connection.execute('''INSERT INTO tenants(tenant_id,name,status,created_at)
                    VALUES (%s,'QA','active',now()::text) ON CONFLICT DO NOTHING''', (TENANT,))
            if connection.execute("SELECT to_regclass('public.platform_subjects')").fetchone()[0]:
                for subject, kind, currency in SUBJECTS:
                    connection.execute('''INSERT INTO platform_subjects(tenant_id,subject_id,subject_type,
                        display_name,base_currency,reporting_currency,lifecycle_status)
                        VALUES (%s,%s,%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING''',
                        (TENANT, subject, kind, 'QA ' + subject, currency, currency))
            connection.commit()
        count = connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0]
    print(f'{count} migrations applied to disposable QA database')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
