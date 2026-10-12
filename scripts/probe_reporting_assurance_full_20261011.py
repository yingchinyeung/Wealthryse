"""Installed assurance evaluator, private database, no model calls or writes."""
import hashlib,json,os,socket
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q

def main():
    if os.getuid()!=10001 or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private boundary differs')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source hash differs')
    from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
    from ibkr_etl.agent_tools import AgentToolContext
    from ibkr_etl.portfolio_assurance import collect_assurance_inputs,evaluate_portfolio_assurance
    with q.connect(q.TEST_DB) as raw:
        if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
        def state():return hashlib.sha256('\n'.join(r['h'] for r in raw.execute('SELECT md5(row_to_json(t)::text) AS h FROM reporting_snapshots t ORDER BY 1').fetchall()).encode()).hexdigest()
        if state()!=EXPECTED:raise ValueError('Private snapshots differ')
        raw.row_factory=_hybrid_row_factory;db=PostgresConnection(raw);records=[]
        for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
            context=AgentToolContext(q.TENANT,'stage35-private-assurance',frozenset({subject}),'no-model-no-writes')
            values,reports=collect_assurance_inputs(db,context,subject_id=subject,as_of='2026-09-30')
            result=evaluate_portfolio_assurance(values,reports,subject_id=subject)
            records.append({'subject':subject,'passed':result['checks_failed']==0 and result['status']!='fail',
                'status':result['status'],'checks_failed':result['checks_failed'],'checks_passed':result['checks_passed'],
                'checks_warned':result['checks_warned'],'findings':result['findings']})
        if state()!=EXPECTED:raise ValueError('Private snapshot state changed')
    passed=all(r['passed'] for r in records)
    print('REPORTING_ASSURANCE_FULL_FINAL '+json.dumps({'passed':passed,'consumer':CONSUMER,'image':IMAGE,'subjects':records,'source_hashes':SOURCE_HASHES,'database_writes':0,'production_connection_used':False,'source_overlays':False,'model_calls':0},default=str),flush=True)
    if not passed:raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_ASSURANCE_FULL_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True)
        raise SystemExit(1)
