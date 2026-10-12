"""Execute only read-only source searches on an explicitly selected Azure DB."""
import json
import os
import psycopg
from psycopg.rows import dict_row

TARGETS = {'legacy': 'ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com',
           'current': 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com',
           'recovery': 'ibkrv2stg-ha-recovery-20261006.postgres.database.azure.com'}
kind = os.environ['MS_SOURCE_DATABASE_TARGET']
if kind not in TARGETS:
    raise ValueError('Unregistered read-only source-search target')
TENANT = '00000000-0000-0000-0000-000000000001'
result = {'target': kind, 'host': TARGETS[kind], 'database_writes': 0,
          'flex_requests': 0, 'queries': {}}
try:
    with psycopg.connect(os.environ['DATABASE_URL'], host=TARGETS[kind], row_factory=dict_row,
                        connect_timeout=25,
                        options='-c default_transaction_read_only=on -c statement_timeout=60000') as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,))
        result['read_only'] = c.execute("SELECT current_setting('transaction_read_only') AS mode").fetchone()['mode']
        tables = {r['table_name'] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        result['source_tables_available'] = sorted(tables & {'position_snapshots', 'flex_rows', 'raw_reports', 'ingestion_runs', 'nav_snapshots'})
        queries = {
            'normalized_positions': ('position_snapshots', "SELECT account_id,snapshot_date,count(*) AS rows FROM position_snapshots WHERE account_id IN ('U16088571','U16088606') AND snapshot_date BETWEEN '2026-08-26' AND '2026-08-31' GROUP BY account_id,snapshot_date ORDER BY account_id,snapshot_date"),
            'original_position_rows': ('flex_rows', "SELECT account_id,report_date,count(*) AS rows FROM flex_rows WHERE account_id IN ('U16088571','U16088606') AND report_date BETWEEN '2026-08-26' AND '2026-08-31' AND lower(record_type)='openposition' GROUP BY account_id,report_date ORDER BY account_id,report_date"),
            'nav_coverage': ('nav_snapshots', "SELECT account_id,snapshot_date,count(*) AS rows FROM nav_snapshots WHERE account_id IN ('U16088571','U16088606') AND snapshot_date BETWEEN '2026-08-26' AND '2026-08-31' GROUP BY account_id,snapshot_date ORDER BY account_id,snapshot_date"),
            'covering_ingestion_runs': ('ingestion_runs', "SELECT run_id,run_type,requested_from,requested_to,started_at,completed_at,status,raw_sha256,raw_path,row_count FROM ingestion_runs WHERE requested_from <= '2026-08-28' AND requested_to >= '2026-08-27' ORDER BY run_id"),
            'raw_archive_references': ('raw_reports', "SELECT content_sha256,file_path,received_at,byte_count FROM raw_reports ORDER BY raw_report_id"),
            'exact_original_positions': ('flex_rows', "SELECT account_id,report_date,row_sha256,data_json,first_seen_run_id,last_seen_run_id FROM flex_rows WHERE account_id IN ('U16088571','U16088606') AND report_date IN ('2026-08-27','2026-08-28') AND lower(record_type)='openposition' ORDER BY account_id,report_date,conid"),
        }
        for name, (table, query) in queries.items():
            if table not in tables:
                result['queries'][name] = {'status': 'table_unavailable'}
                continue
            c.execute('SAVEPOINT source_search')
            try:
                rows = c.execute(query).fetchall()
                result['queries'][name] = {'status': 'searched', 'rows': rows}
            except Exception as e:
                c.execute('ROLLBACK TO SAVEPOINT source_search')
                result['queries'][name] = {'status': 'error', 'error': type(e).__name__}
            c.execute('RELEASE SAVEPOINT source_search')
        c.rollback()
    result['status'] = 'searched'
except Exception as e:
    # Never emit connection URLs, passwords or exception messages.
    result['status'] = 'unavailable'
    result['error'] = type(e).__name__

# Bounded safe chunks, keyed to the exact execution by Azure's existing logs.
for query_name, query_data in result.pop('queries').items():
    rows = query_data.get('rows')
    if rows:
        for index in range(0, len(rows), 20):
            print('MS_SOURCE_DB_RESULT ' + json.dumps({'target': kind, 'query': query_name,
                  'status': query_data['status'], 'total_rows': len(rows), 'offset': index,
                  'rows': rows[index:index + 20]}, default=str), flush=True)
    else:
        print('MS_SOURCE_DB_RESULT ' + json.dumps({'target': kind, 'query': query_name, **query_data}, default=str), flush=True)
print('MS_SOURCE_DB_RESULT ' + json.dumps({'summary': result}, default=str), flush=True)
