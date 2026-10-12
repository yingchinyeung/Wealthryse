"""Registered-account, calendar-aware source observations; never dispatch work.

Requires an explicit cutoff, uses a repeatable READ ONLY transaction and emits
hashes/counts/status only. No Flex/provider requests or approval/ledger writes.
"""
from __future__ import annotations
import argparse
from datetime import date,datetime,timezone
from decimal import DecimalException
import json,os,sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from ibkr_etl.comparison_source_evidence import load_source_projection,verify_sources
from ibkr_etl.comparison_cashflow_evidence import load_flow_evidence,verify_returns
from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
from ibkr_etl.market_calendar import benchmark_session_on_or_before,CALENDAR_VERSION


def observe(connection, *, tenant_id, as_of, targets):
    cutoff=date.fromisoformat(as_of)
    results=[]
    for target in targets:
        scope={'tenant_id':tenant_id,'subject_id':target['subject_id'],
            'benchmark_id':target['benchmark_id'],'as_of':as_of}
        try:
            expected=benchmark_session_on_or_before(target['benchmark_id'],cutoff)
            data=load_source_projection(connection,**scope)
            projection=verify_sources(data,expected_date=expected)
            flow=verify_returns(data,load_flow_evidence(connection,data))
        except (ValueError,DecimalException,TypeError,KeyError) as error:
            result={k:v for k,v in scope.items() if k!='tenant_id'}
            result.update(observation_complete=False,error_type=type(error).__name__,
                source_projection_verified=False,cashflow_projection_verified=False,
                execution_authorized=False)
        else:
            result={'observation_complete':True,'source_projection':projection,'cashflow_projection':flow,
                'execution_authorized':False,'calendar_version':CALENDAR_VERSION}
        results.append(result)
    return results


def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--as-of',required=True)
    args=p.parse_args(argv)
    cutoff=date.fromisoformat(args.as_of)
    if cutoff>datetime.now(timezone.utc).date():p.error('Future cycle cutoff is not allowed')
    import psycopg
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    with psycopg.connect(os.environ['DATABASE_URL'],connect_timeout=20,
            options='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000',
            row_factory=_hybrid_row_factory) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        targets=raw.execute('''SELECT m.subject_id,m.benchmark_id FROM platform_subjects s
            JOIN subject_operating_registrations r ON r.tenant_id=s.tenant_id AND r.subject_id=s.subject_id
            JOIN portfolio_benchmark_mapping m ON m.tenant_id=s.tenant_id AND m.subject_id=s.subject_id
            WHERE s.tenant_id=%s AND s.lifecycle_status='active' AND s.subject_type<>'master_account'
              AND r.capability='benchmark_refresh' AND r.operating_state='active'
              AND r.effective_from<=%s AND (r.effective_to IS NULL OR r.effective_to>=%s)
              AND m.effective_from<=%s AND (m.effective_to IS NULL OR m.effective_to>=%s)
            ORDER BY m.subject_id,m.benchmark_id''',(tenant,args.as_of,args.as_of,args.as_of,args.as_of)).fetchall()
        results=observe(PostgresConnection(raw),tenant_id=tenant,as_of=args.as_of,targets=targets)
        for result in results:print('WORKFLOW_SOURCE_OBSERVATION '+json.dumps(result,sort_keys=True,allow_nan=False),flush=True)
        verified=sum(r['observation_complete'] and r['source_projection']['source_projection_verified']
            and r['cashflow_projection']['cashflow_projection_verified'] for r in results)
        complete=bool(results) and verified==len(results)
        print('WORKFLOW_SOURCE_OBSERVATION '+json.dumps({'probe_complete':True,'mappings':len(results),
            'projections_verified':verified,'as_of':args.as_of,'database_read_only':raw.execute('SHOW transaction_read_only').fetchone()[0]=='on',
            'source_completeness_verified':False,'execution_authorized':False},sort_keys=True),flush=True)
        raw.rollback()
    return 0 if complete else 1


if __name__=='__main__':raise SystemExit(main())
