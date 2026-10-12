"""Read only the comparison mapping and cache contract metadata."""
import os,sys,json
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from ibkr_etl import web
from ibkr_etl.risk_snapshots import decoded_payload,snapshot_read_status
with web._database() as c:
    mappings=[dict(r) for r in c.execute("SELECT subject_id,benchmark_id,mapping_type,reporting_currency,effective_from,effective_to FROM portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id IN ('alpha-world-ms','alpha-world-t') ORDER BY subject_id,effective_from,benchmark_id",(web.TENANT_ID,)).fetchall()]
    comparisons=[dict(r) for r in c.execute("SELECT subject_id,reporting_currency,calculation_version,benchmark_id,COUNT(*) AS observations,MIN(observation_date) AS first,MAX(observation_date) AS last FROM performance_comparison_daily WHERE tenant_id=%s AND subject_id IN ('alpha-world-ms','alpha-world-t') GROUP BY subject_id,reporting_currency,calculation_version,benchmark_id",(web.TENANT_ID,)).fetchall()]
    caches=[]
    for sid in ('alpha-world-ms','alpha-world-t','U16088571','U16088606'):
        row=c.execute("SELECT * FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s ORDER BY as_of_date DESC LIMIT 1",(web.TENANT_ID,sid)).fetchone()
        if row:
            payload=decoded_payload(row['payload_json'])
            caches.append({'subject':sid,'as_of':str(row['as_of_date']),'status':snapshot_read_status(row,subject_id=sid,as_of=str(row['as_of_date'])),'metric_keys':list(payload.get('metrics') or {}),'unavailable':payload.get('unavailable_capabilities'),'message':payload.get('message')})
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'mappings':mappings,'comparisons':comparisons,'caches':caches},default=str),flush=True)
