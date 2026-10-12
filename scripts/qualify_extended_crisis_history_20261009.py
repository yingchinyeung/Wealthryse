"""All-date read-only SPY identity migration rehearsal; archive full preimages."""
import os,sys,json,hashlib
import psycopg
from psycopg.rows import dict_row
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl import web
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload
from ibkr_etl.alpha_world_ms_runtime import VERSION
from ibkr_etl.util import canonical_json

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()

def main():
    if VERSION!='alpha-world-ms-hkd-risk-runtime-v2':raise ValueError('Exact proxy-qualified runtime required')
    packages=[];failed=[];excluded=[]
    with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,connect_timeout=10) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        raw.execute("SELECT set_config('app.tenant_id',%s,true)",(web.TENANT_ID,))
        if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
        rows=raw.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND analytics_version='risk-analytics-v2' ORDER BY as_of_date",(web.TENANT_ID,)).fetchall()
        connection=web._portable_database_connection(raw)
        for index,item in enumerate(rows):
            old=item['record'];day=old['as_of_date'];prior=decoded_payload(old['payload_json'])
            if day in ('2025-01-20','2025-01-21'):
                excluded.append({'as_of':day,'reason':'fewer than two validated returns; preserve original unavailable cache'});continue
            try:
                if prior.get('runtime_version')!='alpha-world-ms-hkd-risk-runtime-v1':raise ValueError('Original cache version changed')
                payload,timings,source=compute_risk_snapshot(connection,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
                if {k:v for k,v in payload['metrics'].items() if k!='crisis_maximum_drawdown'}!={k:v for k,v in prior['metrics'].items() if k!='crisis_maximum_drawdown'}:
                    raise ValueError('Non-crisis metrics changed')
                if payload['source_snapshot']!=prior['source_snapshot'] or payload['risk_free']!=prior['risk_free']:
                    raise ValueError('Source NAV/HIBOR snapshot differs')
                if prior['crisis_replay'] is not None and payload['crisis_replay'] is None:raise ValueError('Lost existing crisis coverage')
                packages.append({'subject':'alpha-world-ms','as_of':day,'old_row':old,'candidate_payload':payload,
                    'candidate_payload_sha256':sha(payload),'candidate_source_sha256':source,'timings':timings,
                    'non_crisis_metrics_identical':True,'core_metrics_identical':True})
            except (ValueError,KeyError,TypeError) as error:
                failed.append({'as_of':day,'error_class':type(error).__name__,'reason':str(error)[:180]})
            if (index+1)%25==0:print('CRISIS_HISTORY_PROGRESS '+json.dumps({'checked':index+1,'qualified':len(packages),'failed':len(failed)}),flush=True)
        reports={}
        for table in ('reporting_snapshots','report_generation_runs','report_release_decisions','report_artifact_releases'):
            values=raw.execute(f'SELECT to_jsonb(r) AS record FROM {table} r WHERE tenant_id=%s ORDER BY to_jsonb(r)::text',(web.TENANT_ID,)).fetchall()
            reports[table]={'rows':len(values),'sha256':sha(sorted((r['record'] for r in values),key=canonical_json))}
    package={'read_only':True,'database_writes':0,'subject':'alpha-world-ms','failures':failed,'excluded':excluded,
        'qualified':packages,'report_fingerprints':reports,'policy_version':'us-sleeve-ms-crisis-identity-proxy-v1'}
    data=canonical_json(package).encode();digest=hashlib.sha256(data).hexdigest()
    name='review/platform-upgrade-20261009/ms-crisis-identity-history-'+digest+'.json'
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    blob=service.get_blob_client('internal-reports',name);blob.upload_blob(data,overwrite=False)
    if hashlib.sha256(blob.download_blob().readall()).hexdigest()!=digest:raise ValueError('Full preimage archive readback differs')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':not failed and len(packages)==446 and len(excluded)==2,
        'read_only':True,'database_writes':0,'historical_rows':len(rows),'qualified':len(packages),'excluded':excluded,
        'failed':len(failed),'failure_examples':failed[:5],'private_blob':name,'blob_sha256':digest,'blob_bytes':len(data),
        'non_crisis_metrics_identical':not failed,'report_or_approval_writes':0,'report_fingerprints':reports}),flush=True)

if __name__=='__main__':main()
