"""Exercise the proposed helper SQL on private PostgreSQL tables only.

The injected WORKER_SOURCE is hash-bound by the transient-job controller.
Tests never read or write broker records, report rows, or public sequences.
This is helper qualification, not full scheduled-render qualification.
"""
import ast
import hashlib
import json
import os
import sys
sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.reporting_snapshot import active_month_end_snapshot_id, ReportingSnapshotError

TENANT = '00000000-0000-0000-0000-000000000001'
OTHER = '11111111-1111-1111-1111-111111111111'
SID = '22222222-2222-2222-2222-222222222222'
OLD = '33333333-3333-3333-3333-333333333333'
CHECKS = []

def check(name, observed, expected):
    CHECKS.append({'name':name, 'passed':observed==expected})
    if observed!=expected:
        raise AssertionError(name)

def main():
    tree=ast.parse(WORKER_SOURCE)
    function=next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name=='_existing_package')
    namespace={}
    exec(compile(ast.Module(body=[function], type_ignores=[]), '<hash-bound-worker-helper>', 'exec'), namespace)
    helper=namespace['_existing_package']
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory,
                         connect_timeout=20, options='-c default_transaction_read_only=on -c statement_timeout=15000') as raw:
        role=raw.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone()
        if role['rolsuper'] or role['rolbypassrls']:
            raise ValueError('Application identity required')
        raw.rollback()
        raw.execute('SET TRANSACTION READ WRITE')
        raw.execute('CREATE TEMP TABLE reporting_snapshots(tenant_id uuid,snapshot_id uuid,subject_id text,period_end text,status text,created_at text)')
        raw.execute('CREATE TEMP TABLE report_generation_runs(generation_run_id bigint,tenant_id uuid,snapshot_id uuid,report_type text,status text,template_version text,output_reference text,output_sha256 text)')
        raw.commit()
        raw.execute('SET TRANSACTION READ ONLY')
        for table in ('reporting_snapshots','report_generation_runs'):
            row=raw.execute("SELECT n.nspname LIKE 'pg_temp_%%' AS temporary FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=to_regclass(%s)",(table,)).fetchone()
            if not row['temporary']:
                raise ValueError('Private table isolation failed')
        connection=PostgresConnection(raw)
        def snapshot(sid=SID, tenant=TENANT, status='frozen', subject='alpha-world-t'):
            raw.execute('INSERT INTO pg_temp.reporting_snapshots VALUES(%s,%s,%s,%s,%s,%s)',(tenant,sid,subject,'2026-09-30',status,'2026-10-01'))
        def pair(sid=SID, tenant=TENANT, status='awaiting_approval', version='v2', offset=0, missing=False):
            for i,kind in enumerate(('factsheet','presentation')):
                raw.execute('INSERT INTO pg_temp.report_generation_runs VALUES(%s,%s,%s,%s,%s,%s,%s,%s)',(offset+i+1,tenant,sid,kind,status,version,None if missing else 'synthetic-private-reference','a'*64))
        def call(**kwargs):
            return helper(connection,tenant_id=TENANT,account_id='alpha-world-t',period_end='2026-09-30',expected_versions={'factsheet':'v2','presentation':'v2'},**kwargs)
        snapshot();snapshot(OLD,status='superseded');pair(OLD,offset=100)
        check('active_snapshot_exact',active_month_end_snapshot_id(connection,tenant_id=TENANT,subject_id='alpha-world-t',period_end='2026-09-30'),SID)
        check('superseded_package_not_reused',call(snapshot_id=SID),None)
        pair()
        check('current_package_idempotent',call(snapshot_id=SID),'already_generated')
        check('repeat_package_idempotent',call(snapshot_id=SID),'already_generated')
        raw.execute('DELETE FROM pg_temp.report_generation_runs WHERE snapshot_id=%s',(SID,))
        pair(status='released_internal',version='v1')
        check('released_history_preserved',call(snapshot_id=SID),'already_released')
        check('explicit_replacement_regenerates',call(snapshot_id=SID,allow_replacement=True),None)
        raw.execute("DELETE FROM pg_temp.report_generation_runs WHERE snapshot_id=%s AND report_type='presentation'",(SID,))
        try: call(snapshot_id=SID)
        except RuntimeError: CHECKS.append({'name':'partial_release_fail_closed','passed':True})
        else: raise AssertionError('partial release accepted')
        raw.execute('DELETE FROM pg_temp.report_generation_runs WHERE snapshot_id=%s',(SID,))
        pair(missing=True)
        check('incomplete_artifact_regenerates',call(snapshot_id=SID),None)
        raw.execute('DELETE FROM pg_temp.report_generation_runs WHERE snapshot_id=%s',(SID,))
        pair(version='v1')
        check('old_template_regenerates',call(snapshot_id=SID),None)
        snapshot('44444444-4444-4444-4444-444444444444',tenant=OTHER)
        pair('44444444-4444-4444-4444-444444444444',tenant=OTHER,offset=500)
        check('other_tenant_not_reused',call(snapshot_id='44444444-4444-4444-4444-444444444444'),None)
        snapshot('55555555-5555-5555-5555-555555555555')
        try: active_month_end_snapshot_id(connection,tenant_id=TENANT,subject_id='alpha-world-t',period_end='2026-09-30')
        except ReportingSnapshotError: CHECKS.append({'name':'multiple_active_snapshots_rejected','passed':True})
        else: raise AssertionError('multiple active snapshots accepted')
        # This regression is deliberately recorded, not hidden by a mock.
        raw.execute('DELETE FROM pg_temp.report_generation_runs WHERE snapshot_id=%s',(SID,))
        raw.execute('DELETE FROM pg_temp.reporting_snapshots WHERE status=\'frozen\'')
        check('no_active_snapshot_is_none',active_month_end_snapshot_id(connection,tenant_id=TENANT,subject_id='alpha-world-t',period_end='2026-09-30'),None)
        observed=call(snapshot_id=None)
        CHECKS.append({'name':'no_active_snapshot_never_reuses_superseded','passed':observed is None,'observed':observed})
        check('application_transaction_read_only',raw.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value'],'on')
        raw.rollback()
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'checks':CHECKS,'passed':all(c['passed'] for c in CHECKS),
          'worker_sha256':hashlib.sha256(WORKER_SOURCE.encode()).hexdigest(),'persistent_database_writes':0,
          'temporary_synthetic_tables_only':True,'full_worker_qualified':False}),flush=True)

if __name__=='__main__':
    main()
