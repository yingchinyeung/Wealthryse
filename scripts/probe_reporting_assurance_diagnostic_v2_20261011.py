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
            diagnostic=None
            if subject=='alpha-world-t':
                from ibkr_etl.alpha_world_t_analytics import load_risk_snapshot
                from ibkr_etl.alpha_world_t_crisis import load
                from ibkr_etl.qualified_agent_risk import t_agent_payload
                try:
                    snapshot,metrics,evidence=load_risk_snapshot(db,'2026-09-30',tenant_id=q.TENANT)
                    crisis=load(db,snapshot,tenant_id=q.TENANT)
                    t_agent_payload(db,tenant_id=q.TENANT,as_of='2026-09-30',source_snapshot=snapshot,core_metrics=metrics,crisis=crisis)
                    diagnostic={'status':'qualified'}
                except ValueError as error:
                    diagnostic={'type':type(error).__name__,'reason':str(error)[:600]}
                from ibkr_etl.risk_snapshots import RISK_ANALYTICS_VERSION,decoded_payload,snapshot_read_status,compute_risk_snapshot
                from ibkr_etl.util import canonical_json
                row=db.execute('SELECT payload_json,payload_sha256,quality_status,comparison_version FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',(q.TENANT,subject,'2026-09-30',RISK_ANALYTICS_VERSION)).fetchone()
                diagnostic['cached_status']=snapshot_read_status(row,subject_id=subject,as_of='2026-09-30')
                diagnostic['cache_present']=row is not None
                try:
                    fresh,timings,source_sha=compute_risk_snapshot(db,tenant_id=q.TENANT,subject_id=subject,as_of='2026-09-30')
                    fresh_sha=hashlib.sha256(canonical_json(fresh).encode()).hexdigest()
                    fresh_row={'payload_json':fresh,'payload_sha256':fresh_sha,'quality_status':fresh['quality_status'],'comparison_version':row['comparison_version'] if row else __import__('ibkr_etl.risk_snapshots',fromlist=['CALCULATION_VERSION']).CALCULATION_VERSION}
                    diagnostic['recomputed_status']=snapshot_read_status(fresh_row,subject_id=subject,as_of='2026-09-30')
                    diagnostic['new_payload_sha256']=fresh_sha
                    diagnostic['new_source_sha256']=source_sha
                    if row:
                        old=decoded_payload(row['payload_json'])
                        diagnostic['old_payload_sha256']=row['payload_sha256']
                        diagnostic['metric_keys_changed']=[k for k in sorted(set(old.get('metrics',{}))|set(fresh.get('metrics',{}))) if old.get('metrics',{}).get(k)!=fresh.get('metrics',{}).get(k)]
                        diagnostic['top_level_keys_changed']=[k for k in sorted(set(old)|set(fresh)) if canonical_json(old.get(k))!=canonical_json(fresh.get(k))]
                except ValueError as error:
                    diagnostic['recompute_error']=str(error)[:500]
                diagnostic['module_sha256']=hashlib.sha256(Path('/app/src/ibkr_etl/qualified_agent_risk.py').read_bytes()).hexdigest()
            records.append({'subject':subject,'passed':result['checks_failed']==0 and result['status']!='fail',
                'status':result['status'],'checks_failed':result['checks_failed'],'checks_passed':result['checks_passed'],
                'checks_warned':result['checks_warned'],'findings':result['findings'],'diagnostic':diagnostic})
        if state()!=EXPECTED:raise ValueError('Private snapshot state changed')
    passed=all(r['passed'] for r in records)
    print('REPORTING_ASSURANCE_DIAGNOSTIC_FINAL '+json.dumps({'passed':passed,'consumer':CONSUMER,'image':IMAGE,'subjects':records,'source_hashes':SOURCE_HASHES,'database_writes':0,'production_connection_used':False,'source_overlays':False,'model_calls':0},default=str),flush=True)
    if not passed:raise SystemExit(1)

if __name__=='__main__':
    try:main()
    except Exception as e:
        print('REPORTING_ASSURANCE_DIAGNOSTIC_ERROR '+json.dumps({'type':type(e).__name__,'message':str(e)[:350]}),flush=True)
        raise SystemExit(1)
