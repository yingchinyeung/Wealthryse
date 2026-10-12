"""Create one missing derived cache in private QA, with rollback and 97-table checks."""
import hashlib,json,os,socket
from pathlib import Path
import prepare_reporting_migration_clone_20261011 as q

def main():
    if os.getuid()!=10001 or {r[4][0] for r in socket.getaddrinfo(q.HOST,5432,type=socket.SOCK_STREAM)}!={'10.50.11.4'}:raise ValueError('Private boundary differs')
    for name,digest in SOURCE_HASHES.items():
        if hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()!=digest:raise ValueError('Source drift')
    from ibkr_etl.db import PostgresConnection,_hybrid_row_factory
    from ibkr_etl.risk_snapshots import compute_risk_snapshot,snapshot_read_status,RISK_ANALYTICS_VERSION,CALCULATION_VERSION
    from ibkr_etl.util import canonical_json,utc_now_iso
    from ibkr_etl.agent_tools import AgentToolContext
    from ibkr_etl.portfolio_assurance import collect_assurance_inputs,evaluate_portfolio_assurance
    scope=(q.TENANT,'alpha-world-t','2026-09-30',RISK_ANALYTICS_VERSION)
    predicate='tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s'
    before=q.fingerprint(q.TEST_DB)
    if before['reporting_snapshots']['sha256']!=EXPECTED:raise ValueError('Private snapshot drift')
    def execute(commit):
        with q.connect(q.TEST_DB,readonly=False) as raw:
            raw.row_factory=_hybrid_row_factory;db=PostgresConnection(raw)
            raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            if raw.execute('SELECT pg_try_advisory_xact_lock(3511102026) AS ok').fetchone()['ok'] is not True:raise ValueError('Private cache writer busy')
            if raw.execute('SELECT 1 FROM risk_analytics_snapshots WHERE '+predicate,scope).fetchone():raise ValueError('Preserve existing cache; reconcile first')
            payload,timings,source_sha=compute_risk_snapshot(db,tenant_id=q.TENANT,subject_id='alpha-world-t',as_of='2026-09-30')
            payload_sha=hashlib.sha256(canonical_json(payload).encode()).hexdigest()
            if source_sha!=PLAN['new_source_sha256'] or payload_sha!=PLAN['new_payload_sha256']:raise ValueError('Independent read-only calculation drift')
            row={'payload_json':payload,'payload_sha256':payload_sha,'quality_status':payload['quality_status'],'comparison_version':CALCULATION_VERSION}
            if snapshot_read_status(row,subject_id='alpha-world-t',as_of='2026-09-30')!='current':raise ValueError('Invalid recomputed cache')
            n=raw.execute('''INSERT INTO risk_analytics_snapshots(tenant_id,subject_id,as_of_date,analytics_version,comparison_version,source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,duration_ms,timings_json)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING''',
              (*scope,CALCULATION_VERSION,source_sha,payload_sha,canonical_json(payload),payload['quality_status'],utc_now_iso(),float(timings['total_ms']),canonical_json(timings))).rowcount
            if n!=1:raise ValueError('Expected one create-only cache')
            records=[]
            for subject in ('alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606'):
                ctx=AgentToolContext(q.TENANT,'private-assurance-cache',frozenset({subject}),'no-model')
                values,reports=collect_assurance_inputs(db,ctx,subject_id=subject,as_of='2026-09-30')
                result=evaluate_portfolio_assurance(values,reports,subject_id=subject)
                records.append({'subject':subject,'passed':result['checks_failed']==0 and result['status']!='fail','status':result['status'],'checks_failed':result['checks_failed'],'checks_passed':result['checks_passed']})
            if not all(r['passed'] for r in records):raise ValueError('Full assurance failed; rollback required')
            if not commit:raw.rollback()
        return records
    execute(False)
    if q.fingerprint(q.TEST_DB)!=before:raise ValueError('Transaction rollback failed')
    records=execute(True)
    after=q.fingerprint(q.TEST_DB)
    if {k:v for k,v in before.items() if k!='risk_analytics_snapshots'}!={k:v for k,v in after.items() if k!='risk_analytics_snapshots'}:raise ValueError('Unrelated QA table changed')
    with q.connect(q.TEST_DB) as db:
        rows=db.execute('SELECT md5(row_to_json(t)::text) AS h FROM risk_analytics_snapshots t WHERE NOT ('+predicate+') ORDER BY 1',scope).fetchall()
        remaining={'rows':len(rows),'sha256':hashlib.sha256('\n'.join(r['h'] for r in rows).encode()).hexdigest()}
    if remaining!=before['risk_analytics_snapshots'] or after['risk_analytics_snapshots']['rows']!=remaining['rows']+1:raise ValueError('Existing caches changed')
    print('REPORTING_ASSURANCE_CACHE_FINAL '+json.dumps({'passed':True,'consumer':CONSUMER,'image':IMAGE,'subjects':records,'source_hashes':SOURCE_HASHES,'database_writes':1,'private_cache_rows_created':1,'transaction_rollback_verified':True,'existing_caches_preserved':True,'other_tables_unchanged':96,'production_connection_used':False,'source_overlays':False,'model_calls':0,'payload_sha256':PLAN['new_payload_sha256']},default=str),flush=True)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_ASSURANCE_CACHE_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:400],'reconcile_before_retry':True}),flush=True)
        raise SystemExit(1)
