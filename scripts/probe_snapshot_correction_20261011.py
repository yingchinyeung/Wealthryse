"""SELECT-only full snapshot collection on the private QA database, no overlays."""
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import prepare_reporting_migration_clone_20261011 as q

def main():
    if os.getuid()!=10001 or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:
        raise ValueError('Private East Asia runtime required')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Installed source differs')
    from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
    from ibkr_etl.reporting_snapshot import _collect_tool_results,assemble_month_end_payload,active_month_end_snapshot_id
    from ibkr_etl.util import canonical_json
    class ReadOnly(PostgresConnection):
        def execute(self,sql,parameters=None):
            if not sql.lstrip().upper().startswith('SELECT '):raise ValueError('SELECT only')
            return super().execute(sql,parameters)
        def commit(self):raise ValueError('Commit prohibited')
    with q.connect(q.TEST_DB) as raw:
        if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only transaction required')
        def state():return hashlib.sha256('\n'.join(r['h'] for r in raw.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
        if state()!=EXPECTED:raise ValueError('Private snapshot baseline differs')
        raw.row_factory=_hybrid_row_factory;db=ReadOnly(raw);records=[]
        for subject in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):
            currency='HKD' if subject.startswith('alpha-world-') else 'USD'
            active=active_month_end_snapshot_id(db,tenant_id=q.TENANT,subject_id=subject,period_end='2026-09-30')
            if not active:raise ValueError('Expected frozen QA snapshot absent')
            results=[]
            for replay in range(2):
                tools=_collect_tool_results(db,tenant_id=q.TENANT,subject_id=subject,period_end='2026-09-30',reporting_currency=currency)
                payload,quality,allowed,blockers=assemble_month_end_payload(subject_id=subject,period_start='2025-01-20',period_end='2026-09-30',reporting_currency=currency,tool_results=tools)
                monthly=payload.get('monthly_reporting') or {}
                if quality=='fail' or not monthly.get('available') or not math.isfinite(monthly['portfolio_monthly_return']):raise ValueError('Snapshot quality failed: '+subject)
                if payload['reporting_currency']!=currency or payload['data_as_of']!='2026-09-30':raise ValueError('Reporting scope differs')
                results.append({'subject':subject,'passed':True,'currency':currency,'payload_sha256':hashlib.sha256(canonical_json(payload).encode()).hexdigest(),
                    'quality_status':quality,'publication_allowed':allowed,'publication_blockers':list(blockers),
                    'sections_sha256':{key:hashlib.sha256(canonical_json(payload.get(key)).encode()).hexdigest() for key in ('performance','risk','holdings','sector_allocation','monthly_reporting','performance_comparison')},
                    'active_snapshot_id':active})
            if results[0]!=results[1]:raise ValueError('Unchanged-input replay differs: '+subject)
            records.append(results[0])
        if state()!=EXPECTED:raise ValueError('Private snapshot history changed')
    print('SNAPSHOT_RUNTIME_FINAL '+json.dumps({'passed':True,'image':IMAGE,'subjects':records,'source_hashes':SOURCE_HASHES,
        'database_writes':0,'production_connection_used':False,'source_overlays':False,'unchanged_replays':5,
        'snapshot_history_preserved':True,'private_fixture_uploaded':False}),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('SNAPSHOT_RUNTIME_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True)
        raise SystemExit(1)
