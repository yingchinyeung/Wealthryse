"""Unmodified sync importer against synthetic session-local PostgreSQL tables.

Only the exact private recovery clone is reachable. Public tables and public
sequences are not written. Temporary sequence defaults replace serial defaults.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from urllib.parse import urlsplit, urlunsplit
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl.pilot_import import import_snapshot, ALLOWED_DESTINATION_ONLY_ENV
from ibkr_etl.pilot_snapshot import ACCOUNT_TABLES, build_pilot_snapshot

SOURCE = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
TARGET = 'ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
TENANT = '00000000-0000-0000-0000-000000000001'
RECOVERY = 'archived-cash-only-recovery-20261009'
p = urlsplit(os.environ['DATABASE_URL'])
if p.hostname != SOURCE or os.environ['PORTFOLIO_TENANT_ID'] != TENANT:
    raise ValueError('Expected credential source and tenant required')
auth, sep, _ = p.netloc.rpartition('@')
if not sep: raise ValueError('Existing private credential authority required')
uri = urlunsplit((p.scheme, auth + '@' + TARGET + (':' + str(p.port) if p.port else ''), p.path, p.query, p.fragment))
os.environ['DATABASE_URL'] = uri
if hashlib.sha256(Path('/app/src/ibkr_etl/pilot_import.py').read_bytes()).hexdigest() != '0d6e0cd84cf2c3b8b2780e1601dddb8a71951a9a98f225247a39dd7a29bb83a0':
    raise ValueError('Installed importer source must remain exact')
connect = psycopg.connect
tables = ('accounts', 'instruments', 'ingestion_runs', *ACCOUNT_TABLES)
if len(set(tables)) != len(tables): raise ValueError('Duplicate temporary table scope')


def fingerprints():
    records = {}
    with connect(uri, row_factory=dict_row, connect_timeout=20) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        db.execute("SET LOCAL TIME ZONE 'UTC'")
        db.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        names = [r['table_name'] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
        if not 25 <= len(names) <= 150: raise ValueError('Unexpected clone schema')
        for name in names:
            h = hashlib.sha256(); count = 0
            with db.cursor(name='sync_readonly_preservation') as cursor:
                cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS h FROM {} t ORDER BY 1').format(sql.Identifier('public', name)))
                for row in cursor: h.update((row['h'] + '\n').encode()); count += 1
            records[name] = {'rows': count, 'sha256': h.hexdigest()}
    return records


before = fingerprints()
raw = connect(uri, row_factory=dict_row, connect_timeout=20)
checks = []; temporary_sequences = []


class BorrowedConnection:
    def __getattr__(self, name): return getattr(raw, name)
    def __enter__(self): return self
    def __exit__(self, typ, value, tb):
        if typ: raw.rollback()
        else: raw.commit()


def isolated_connect(conninfo='', **kwargs):
    info = conninfo_to_dict(conninfo, **kwargs)
    expected = conninfo_to_dict(uri)
    if any(info.get(key) != expected.get(key) for key in ('host', 'dbname', 'user')) or info.get('host') != TARGET:
        raise ValueError('No importer connection outside exact recovery clone')
    return BorrowedConnection()


def temporary_financial_hash():
    result = {}
    for name in tables:
        if name == 'ingestion_runs': continue
        rows = raw.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS h FROM {} t ORDER BY 1').format(sql.Identifier('pg_temp', name))).fetchall()
        result[name] = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    return result


try:
    raw.execute("SELECT set_config('app.tenant_id',%s,false)", (TENANT,))
    raw.execute("SET TIME ZONE 'UTC'")
    for name in tables:
        raw.execute(sql.SQL('CREATE TEMP TABLE {} (LIKE {} INCLUDING ALL) ON COMMIT PRESERVE ROWS').format(sql.Identifier(name), sql.Identifier('public', name)))
        columns = raw.execute("SELECT column_name,column_default FROM information_schema.columns WHERE table_schema='public' AND table_name=%s", (name,)).fetchall()
        for column in columns:
            if str(column['column_default'] or '').startswith('nextval('):
                seq = name + '_' + column['column_name'] + '_fixture'
                if len(seq) > 63: raise ValueError('Temporary sequence name too long')
                raw.execute(sql.SQL('CREATE TEMP SEQUENCE {}').format(sql.Identifier(seq)))
                raw.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} SET DEFAULT nextval({})').format(sql.Identifier('pg_temp', name), sql.Identifier(column['column_name']), sql.Literal('pg_temp.' + seq)))
                temporary_sequences.append(seq)
    raw.commit()
    # Freeze relation resolution and verify every mutation target is temporary.
    raw.execute("SET search_path = pg_temp, public")
    for name in tables:
        r = raw.execute('SELECT relpersistence FROM pg_class WHERE oid=to_regclass(%s)', (name,)).fetchone()
        if r['relpersistence'] != 't': raise ValueError('Mutation target is not session-local')
    raw.commit()
    psycopg.connect = isolated_connect
    with tempfile.TemporaryDirectory(prefix='sync-synthetic-') as directory:
        root = Path(directory); source = root / 'v11.sqlite'; snapshot = root / 'U1.sqlite'
        with sqlite3.connect(source) as db:
            db.executescript(Path('/app/migrations/001_init.sql').read_text())
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_metadata(key TEXT PRIMARY KEY,value TEXT)')
            db.execute("INSERT INTO snapshot_metadata VALUES ('generated_at','2026-10-09T16:00:00+08:00')")
            columns = [r[1] for r in db.execute('PRAGMA table_info(accounts)')]
            values = {c: 'x' for c in columns}; values.update(account_id='U1', base_currency='USD')
            db.execute('INSERT INTO accounts(' + ','.join(columns) + ') VALUES (' + ','.join('?' for _ in columns) + ')', [values[c] for c in columns])
            db.execute("INSERT INTO ingestion_runs(run_type,started_at,status) VALUES ('daily','2026-10-01','success')")
            for day in range(1, 6):
                db.execute("INSERT INTO cash_snapshots VALUES ('U1',?,'BASE_SUMMARY','100','100','1','h',1)", (f'2026-09-{day:02d}',))
        build_pilot_snapshot(source, snapshot, account_id='U1', start_date='2025-01-20')
        def run_import():
            return import_snapshot(database_url=uri, local_path=snapshot, account_id='U1', start_date='2025-01-20', tenant_id=TENANT, source_reference='synthetic-private-upgrade-test')
        def change_snapshot(minute):
            with sqlite3.connect(snapshot) as db:
                db.execute("UPDATE snapshot_metadata SET value=? WHERE key='generated_at'", (f'2026-10-09T16:{minute:02d}:00+08:00',))
        os.environ.pop(ALLOWED_DESTINATION_ONLY_ENV, None)
        first = run_import()
        if first['target_rows']['cash_snapshots'] != 5: raise ValueError('Synthetic first import failed')
        checks.append('first_import_five_cash_rows')
        if run_import()['result'] != 'already_imported': raise ValueError('Idempotency failed')
        checks.append('unchanged_snapshot_idempotency')
        recovery = raw.execute("INSERT INTO ingestion_runs(run_type,started_at,status,tenant_id) VALUES (%s,'2026-10-09','success',%s) RETURNING run_id", (RECOVERY, TENANT)).fetchone()['run_id']
        for date in ('2026-08-27', '2026-08-28'):
            raw.execute("INSERT INTO cash_snapshots(account_id,snapshot_date,currency,ending_cash_text,settled_cash_text,fx_rate_to_base_text,row_sha256,last_seen_run_id) VALUES ('U1',%s,'BASE_SUMMARY','1','1','1','r',%s)", (date, recovery))
        raw.commit(); change_snapshot(1)
        old = temporary_financial_hash(); raw.commit()
        try: run_import()
        except RuntimeError as error:
            if 'destination-only' not in str(error): raise
        else: raise ValueError('Unapproved destination-only rows accepted')
        if temporary_financial_hash() != old: raise ValueError('Failed import did not roll back financial rows')
        raw.commit(); checks.append('unapproved_extras_rejected_and_financial_rows_rolled_back')
        os.environ[ALLOWED_DESTINATION_ONLY_ENV] = RECOVERY
        allowed = run_import()
        if allowed['status'] != 'PASS' or allowed['target_rows']['cash_snapshots'] != 5 or allowed['destination_only_rows']['cash_snapshots']['rows'] != 2:
            raise ValueError('Approved recovery-row reconciliation failed')
        if raw.execute('SELECT count(*) AS n FROM pg_temp.cash_snapshots').fetchone()['n'] != 7:
            raise ValueError('Recovery rows were not retained')
        raw.commit(); checks.append('allowlisted_recovery_rows_retained_and_disclosed')
        if run_import()['result'] != 'already_imported': raise ValueError('Approved replay idempotency failed')
        checks.append('approved_replay_idempotency')
        unknown = raw.execute("INSERT INTO ingestion_runs(run_type,started_at,status,tenant_id) VALUES ('unapproved-fixture','2026-10-09','success',%s) RETURNING run_id", (TENANT,)).fetchone()['run_id']
        raw.execute("INSERT INTO cash_snapshots(account_id,snapshot_date,currency,ending_cash_text,settled_cash_text,fx_rate_to_base_text,row_sha256,last_seen_run_id) VALUES ('U1','2026-08-26','BASE_SUMMARY','1','1','1','r',%s)", (unknown,))
        raw.commit(); change_snapshot(2); old = temporary_financial_hash(); raw.commit()
        try: run_import()
        except RuntimeError as error:
            if 'unapproved-fixture' not in str(error): raise
        else: raise ValueError('Unknown recovery type accepted')
        if temporary_financial_hash() != old: raise ValueError('Unknown-run rejection changed financial rows')
        checks.append('unknown_recovery_rejected_and_rolled_back')
finally:
    psycopg.connect = connect
    os.environ.pop(ALLOWED_DESTINATION_ONLY_ENV, None)
    raw.rollback(); raw.close()
after = fingerprints()
differences = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
proof = {'passed': len(checks) == 6 and not differences, 'checks': checks, 'public_tables_checked': len(before), 'public_table_differences': differences, 'temporary_tables': len(tables), 'temporary_sequences': len(temporary_sequences), 'temporary_objects_dropped_on_disconnect': True, 'production_connections': 0, 'production_writes': 0, 'persistent_clone_writes': 0, 'broker_calls': 0, 'fixture': 'Synthetic U1 USD importer mechanics only, not portfolio valuation or HKD methodology', 'source_manifest_qualified_separately': True}
print('SYNC_POSTGRES_PROOF ' + json.dumps(proof), flush=True)
if not proof['passed']: raise SystemExit(1)
