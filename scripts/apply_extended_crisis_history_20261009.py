"""Atomic, exact-preimage historical cache extension, or conditional full restore."""
import os,sys,json,hashlib
sys.path[:0]=['/app/src','/app/scripts']
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl.db import PostgresConnection
from ibkr_etl.risk_snapshots import load_risk_inputs,risk_input_fingerprint,decoded_payload
from ibkr_etl.util import canonical_json,utc_now_iso
def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def validate_scope(package,tenant):
    items=package['qualified'];days=[r['as_of'] for r in items]
    if tenant!='00000000-0000-0000-0000-000000000001' or len(items)!=446 or len(set(days))!=446 or package['failures'] or set(r['as_of'] for r in package['excluded'])!={'2025-01-20','2025-01-21'}:raise ValueError('Historical repair scope differs')
    for item in items:
        old=item['old_row']
        if not '2025-01-22'<=item['as_of']<='2026-10-07' or (old['tenant_id'],old['subject_id'],old['as_of_date'],old['analytics_version'])!=(tenant,'alpha-world-ms',item['as_of'],'risk-analytics-v2'):raise ValueError('Foreign repair identity')
        if sha(item['candidate_payload'])!=item['candidate_payload_sha256']:raise ValueError('Candidate hash differs')
        original=decoded_payload(old['payload_json']);candidate=item['candidate_payload'];metrics=original['metrics']
        if original.get('runtime_version')!='alpha-world-ms-hkd-risk-runtime-v1' or candidate.get('runtime_version')!='alpha-world-ms-hkd-risk-runtime-v2':raise ValueError('Unexpected policy transition')
        if not metrics or {k:v for k,v in candidate['metrics'].items() if k!='crisis_maximum_drawdown'}!={k:v for k,v in metrics.items() if k!='crisis_maximum_drawdown'}:raise ValueError('Non-crisis metric changed')
        if candidate['source_snapshot']!=original['source_snapshot'] or candidate['risk_free']!=original['risk_free']:raise ValueError('Actual NAV/HIBOR inputs changed')
    return items
def main():
    tenant=os.environ['PORTFOLIO_TENANT_ID']
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    blob=service.get_blob_client('internal-reports',PLAN['private_blob'])
    if blob.get_blob_properties().size>128*1024**2:raise ValueError('Archive bound exceeded')
    content=blob.download_blob().readall()
    if hashlib.sha256(content).hexdigest()!=PLAN['blob_sha256']:raise ValueError('Protected history differs')
    items=validate_scope(json.loads(content),tenant);records=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10,options='-c statement_timeout=120000 -c lock_timeout=5000') as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(tenant,))
        connection=PostgresConnection(raw)
        for item in items:
            day=item['as_of'];scope=(tenant,'alpha-world-ms',day,'risk-analytics-v2')
            key=int.from_bytes(hashlib.sha256(canonical_json(['risk-refresh-v1',*scope[:3]]).encode()).digest()[:8],byteorder='big',signed=True)
            if not raw.execute('SELECT pg_try_advisory_xact_lock(%s) AS acquired',(key,)).fetchone()['acquired']:raise ValueError('Historical date busy')
            raw.execute('SELECT 1 FROM risk_analytics_snapshots WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s FOR UPDATE',scope)
            current=raw.execute('SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',scope).fetchone()['record']
            if MODE=='apply':
                if canonical_json(current)!=canonical_json(item['old_row']):raise ValueError('Full preimage drift')
                inputs,_=load_risk_inputs(connection,tenant_id=tenant,subject_id='alpha-world-ms',as_of=day)
                if risk_input_fingerprint(inputs)!=item['candidate_source_sha256']:raise ValueError('Current source differs from qualified history')
            elif MODE=='restore':
                if current['payload_sha256']!=item['candidate_payload_sha256'] or current['source_data_sha256']!=item['candidate_source_sha256']:raise ValueError('Restore would overwrite a different cache')
            else:raise ValueError('Unknown transition mode')
            records.append(current)
        if MODE=='restore' and sha(records)!=PLAN['after_rows_sha256']:raise ValueError('Full current cache rows drifted; reconcile before rollback')
        after=[]
        for item in items:
            day=item['as_of'];scope=(tenant,'alpha-world-ms',day,'risk-analytics-v2');old=item['old_row'];payload=item['candidate_payload']
            if MODE=='apply':
                changed=raw.execute('''UPDATE risk_analytics_snapshots SET payload_json=%s::jsonb,payload_sha256=%s,source_data_sha256=%s,quality_status=%s,
                    generated_at=%s,duration_ms=%s,timings_json=%s::jsonb WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s AND payload_sha256=%s''',
                    (canonical_json(payload),item['candidate_payload_sha256'],item['candidate_source_sha256'],payload['quality_status'],utc_now_iso(),float(item['timings']['total_ms']),canonical_json({**item['timings'],'source_check_status':'current','source_checked_at':utc_now_iso(),'refresh_reason':'owner-approved-ms-crisis-identity-proxy'}),*scope,old['payload_sha256'])).rowcount
            else:
                changed=raw.execute('''UPDATE risk_analytics_snapshots SET (comparison_version,source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,duration_ms,timings_json)=
                    (SELECT comparison_version,source_data_sha256,payload_sha256,payload_json,quality_status,generated_at,duration_ms,timings_json FROM jsonb_populate_record(NULL::public.risk_analytics_snapshots,%s::jsonb))
                    WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s AND payload_sha256=%s''',(canonical_json(old),*scope,item['candidate_payload_sha256'])).rowcount
            row=raw.execute('SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id=%s AND as_of_date=%s AND analytics_version=%s',scope).fetchone()['record']
            if changed!=1:raise ValueError('Expected exact cache update')
            if MODE=='apply' and sha(row['payload_json'])!=item['candidate_payload_sha256']:raise ValueError('Update readback differs')
            if MODE=='restore' and canonical_json(row)!=canonical_json(old):raise ValueError('Full restore differs')
            after.append(row)
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':True,'mode':MODE,'cache_rows_updated':446,'after_rows_sha256':sha(after),'core_metrics_identical':True,'source_data_changed':False,'reports_changed':False,'approvals_changed':False,'excluded_insufficient_history':['2025-01-20','2025-01-21']}),flush=True)
if __name__=='__main__':main()
