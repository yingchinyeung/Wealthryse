"""Unmodified worker functions on synthetic temporary PostgreSQL relations.

Never connect to production. After creating fixtures, PostgreSQL read-only
transactions prohibit persistent writes, while allowing temporary-table DML.
No broker or external market calls. No permissions or saved job changes.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
from datetime import date
from urllib.parse import urlsplit, urlunsplit
import psycopg
from psycopg.rows import dict_row
from psycopg import sql

sys.path[:0] = ['/app/src', '/app/scripts']
from ibkr_etl.db import PostgresConnection
from ibkr_etl.operating_registry import (FlexSubjectBinding, plan_flex_work, enqueue_work_plan,
    claim_next_work, complete_work, fail_work, dead_letter_exhausted_and_blocked_work)
from ibkr_etl.flex_discovery import _claim_request
from ibkr_etl.sector_classification import refresh_sector_classifications_range

SOURCE = 'ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com'
TARGET = 'ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com'
TENANT = '00000000-0000-0000-0000-000000000001'
p = urlsplit(os.environ['DATABASE_URL'])
if p.hostname != SOURCE or os.environ['PORTFOLIO_TENANT_ID'] != TENANT:
    raise ValueError('Exact credential source/tenant required')
auth, sep, _ = p.netloc.rpartition('@')
if not sep: raise ValueError('Expected private credential authority')
uri = urlunsplit((p.scheme, auth + '@' + TARGET + (':' + str(p.port) if p.port else ''), p.path, p.query, p.fragment))
os.environ['DATABASE_URL'] = uri
raw = psycopg.connect(uri, row_factory=dict_row, connect_timeout=20)
db = PostgresConnection(raw)


def deny(*args, **kwargs):
    raise RuntimeError('No additional database or external connections permitted')


psycopg.connect = deny
socket.socket.connect = socket.socket.connect_ex = socket.create_connection = deny
checks = []


def fingerprints():
    raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    names = [r['table_name'] for r in raw.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")]
    if len(names) != 92: raise ValueError('Expected exact recovery clone schema')
    result = {}
    for name in names:
        h = hashlib.sha256(); count = 0
        with raw.cursor(name='institutional_preservation') as cursor:
            cursor.execute(sql.SQL('SELECT md5(row_to_json(t)::text) AS h FROM {} t ORDER BY 1').format(sql.Identifier('public', name)))
            for row in cursor: h.update((row['h'] + '\n').encode()); count += 1
        result[name] = {'rows': count, 'sha256': h.hexdigest()}
    raw.commit()
    return result


try:
    raw.execute("SELECT set_config('app.tenant_id',%s,false)", (TENANT,))
    raw.execute("SET TIME ZONE 'UTC'"); raw.commit()
    before = fingerprints()
    raw.execute('CREATE TEMP TABLE institutional_work_items (LIKE public.institutional_work_items INCLUDING ALL) ON COMMIT PRESERVE ROWS')
    # Fixtures contain only columns consumed by the actual discovery/sector SQL.
    raw.execute('CREATE TEMP TABLE ibkr_flex_source_validation_requests(validation_request_id uuid,tenant_id uuid,flex_source_id uuid,requested_from date,requested_to date,status text,created_at timestamptz DEFAULT now(),error_phase text,error_code text,completed_at timestamptz,attempt_count int,started_at timestamptz)')
    raw.execute('CREATE TEMP TABLE ibkr_flex_queries(tenant_id uuid,flex_source_id uuid,query_id text,status text,credential_id uuid)')
    raw.execute('CREATE TEMP TABLE ibkr_flex_credentials(tenant_id uuid,credential_id uuid,key_vault_secret_name text,status text)')
    raw.execute('CREATE TEMP TABLE instruments(conid text PRIMARY KEY,symbol text,listing_exchange text,currency text)')
    raw.execute('CREATE TEMP TABLE subject_position_series(subject_id text,snapshot_date date,conid text,currency text,quantity_text text)')
    raw.execute("CREATE TEMP TABLE instrument_classifications(tenant_id uuid NOT NULL DEFAULT app_tenant_id(),conid text,provider text,symbol text,lookup_symbol text,sector text,industry text,exchange text,status text,quality_status text,license_status text,source_url text,response_sha256 text,error_code text,checked_at timestamptz,UNIQUE(tenant_id,conid,provider))")
    tables = ('institutional_work_items','ibkr_flex_source_validation_requests','ibkr_flex_queries','ibkr_flex_credentials','instruments','subject_position_series','instrument_classifications')
    raw.execute('SET search_path=pg_temp,public')
    for name in tables:
        if raw.execute('SELECT relpersistence FROM pg_class WHERE oid=to_regclass(%s)', (name,)).fetchone()['relpersistence'] != 't':
            raise ValueError('Non-temporary fixture relation')
    raw.execute('SET default_transaction_read_only=on'); raw.commit()
    if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] != 'on':
        raise ValueError('Persistent mutation guard not active')
    raw.commit()
    bindings = [FlexSubjectBinding('11111111-1111-1111-1111-111111111111','22222222-2222-2222-2222-222222222222','FIXTURE','FIXTURE',date(2025,1,20))]
    plan = plan_flex_work(bindings, run_date=date(2026,10,1))
    ids = enqueue_work_plan(db, tenant_id=TENANT, work_plan=plan); db.commit()
    if enqueue_work_plan(db, tenant_id=TENANT, work_plan=plan) != ids: raise ValueError('Queue replay identity changed')
    if raw.execute('SELECT count(*) n FROM institutional_work_items').fetchone()['n'] != len(plan): raise ValueError('Duplicate queued rows')
    db.commit(); checks.append('queue_idempotency')
    claim = claim_next_work(db, tenant_id=TENANT, worker_id='fixture-worker', allowed_kinds=('flex_statement_request',)); db.commit()
    if claim is None or claim.maximum_attempts != 1: raise ValueError('One-shot broker work required')
    if claim_next_work(db, tenant_id=TENANT, worker_id='other-worker', allowed_kinds=('source_partition','account_import')) is not None: raise ValueError('Unfinished dependencies claimed')
    db.commit(); checks.append('dependency_and_lease_exclusion')
    try:
        complete_work(db, tenant_id=TENANT, work_item_id=claim.work_item_id, worker_id='wrong-owner')
    except RuntimeError:
        db.rollback()
    else: raise ValueError('Wrong lease owner accepted')
    checks.append('wrong_lease_owner_rejected')
    complete_work(db, tenant_id=TENANT, work_item_id=claim.work_item_id, worker_id='fixture-worker'); db.commit()
    child = claim_next_work(db, tenant_id=TENANT, worker_id='fixture-worker', allowed_kinds=('source_partition',)); db.commit()
    if child is None: raise ValueError('Completed dependency did not unlock child')
    complete_work(db, tenant_id=TENANT, work_item_id=child.work_item_id, worker_id='fixture-worker'); db.commit()
    checks.append('successful_dependency_advancement')
    plan2 = plan_flex_work(bindings, run_date=date(2026,10,2)); enqueue_work_plan(db, tenant_id=TENANT, work_plan=plan2); db.commit()
    claim2 = claim_next_work(db, tenant_id=TENANT, worker_id='fixture-worker', allowed_kinds=('flex_statement_request',)); db.commit()
    if fail_work(db, tenant_id=TENANT, claim=claim2, worker_id='fixture-worker', error_code='synthetic', error_message='offline fixture') != 'dead_letter': raise ValueError('Broker failure retried')
    db.commit()
    if dead_letter_exhausted_and_blocked_work(db, tenant_id=TENANT) < 1: raise ValueError('Failed dependency not blocked')
    db.commit(); checks.append('broker_failure_no_retry_and_descendant_block')
    if _claim_request(raw, TENANT) is not None: raise ValueError('Empty discovery queue not idle')
    raw.commit(); checks.append('discovery_empty_queue')
    raw.execute("INSERT INTO ibkr_flex_credentials VALUES (%s,'11111111-1111-1111-1111-111111111111','ibkr-flex-source-fixture','active')",(TENANT,))
    raw.execute("INSERT INTO ibkr_flex_queries VALUES (%s,'22222222-2222-2222-2222-222222222222','fixture','active','11111111-1111-1111-1111-111111111111')",(TENANT,))
    raw.execute("INSERT INTO ibkr_flex_source_validation_requests(validation_request_id,tenant_id,flex_source_id,requested_from,requested_to,status) VALUES ('33333333-3333-3333-3333-333333333333',%s,'22222222-2222-2222-2222-222222222222','2026-10-01','2026-10-02','pending')",(TENANT,)); raw.commit()
    if _claim_request(raw,TENANT) is not None: raise ValueError('Ineligible discovery source claimed')
    raw.commit()
    if raw.execute('SELECT status,error_code FROM ibkr_flex_source_validation_requests').fetchone() != {'status':'blocked','error_code':'source_not_validatable'}: raise ValueError('Discovery preflight did not block')
    raw.commit(); checks.append('discovery_ineligible_source_blocked')
    raw.execute("UPDATE ibkr_flex_queries SET status='pending_validation'")
    raw.execute("UPDATE ibkr_flex_source_validation_requests SET status='pending'"); raw.commit()
    if _claim_request(raw,TENANT) is None: raise ValueError('Valid synthetic discovery not claimed')
    raw.commit()
    if raw.execute('SELECT status,attempt_count FROM ibkr_flex_source_validation_requests').fetchone() != {'status':'running','attempt_count':1}: raise ValueError('Discovery one-shot claim invalid')
    raw.commit(); checks.append('discovery_single_attempt_claim')
    raw.execute("INSERT INTO instruments VALUES ('fixture-conid','AAPL','NASDAQ','USD')")
    raw.execute("INSERT INTO subject_position_series VALUES ('FIXTURE','2026-10-01','fixture-conid','USD','1')"); raw.commit()
    transmitted = []
    def fixture_fetcher(symbol,timeout):
        transmitted.append(symbol)
        return {'quotes':[{'symbol':symbol,'sector':'Technology','industry':'fixture','exchange':'NMS'}]}
    first = refresh_sector_classifications_range(db,subject_id='FIXTURE',start_date='2026-10-01',end_date='2026-10-02',workers=1,fetcher=fixture_fetcher)
    second = refresh_sector_classifications_range(db,subject_id='FIXTURE',start_date='2026-10-01',end_date='2026-10-02',workers=1,fetcher=fixture_fetcher)
    if first['classified'] != 1 or second['tickers_transmitted'] != 0 or transmitted != ['AAPL']: raise ValueError('Sector fixture/replay failed')
    db.commit(); checks.append('sector_upsert_and_unchanged_replay')
    after = fingerprints()
    differences = [name for name in before if before[name] != after[name]]
    if differences: raise ValueError('Persistent clone table changed')
    print('INSTITUTIONAL_POSTGRES_PROOF ' + json.dumps({'passed':True,'checks':checks,'public_tables_checked':len(before),
          'public_table_differences':differences,'temporary_relations':len(tables),'database_read_only_enforced':True,
          'production_connections':0,'production_writes':0,'persistent_clone_writes':0,'broker_calls':0,'market_calls':0,
          'temporary_objects_dropped_on_disconnect':True}),flush=True)
finally:
    raw.close()
