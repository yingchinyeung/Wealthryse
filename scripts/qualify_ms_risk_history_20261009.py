"""Private read-only all-cache migration rehearsal; no historical values changed."""
import os,sys,json,hashlib
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=120000'
sys.path[:0]=['/app/src','/app/scripts']
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from ibkr_etl import web
from ibkr_etl.risk_snapshots import compute_risk_snapshot,decoded_payload
from ibkr_etl.util import canonical_json

def sha(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def main():
    packages=[];failed=[];existing=[]
    with web._database() as raw:
        if raw.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Read-only required')
        rows=raw.execute("SELECT to_jsonb(r) AS record FROM risk_analytics_snapshots r WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND analytics_version='risk-analytics-v2' ORDER BY as_of_date",(web.TENANT_ID,)).fetchall()
        connection=web._portable_database_connection(raw)
        for index,item in enumerate(rows):
            old=item['record'];day=old['as_of_date'];prior=decoded_payload(old['payload_json'])
            if prior.get('runtime_version')=='alpha-world-ms-hkd-risk-runtime-v1':existing.append(day);continue
            try:
                payload,timings,source=compute_risk_snapshot(connection,tenant_id=web.TENANT_ID,subject_id='alpha-world-ms',as_of=day)
                if not prior.get('metrics') or any(payload['metrics'].get(k)!=v for k,v in prior['metrics'].items()):raise ValueError('Original core metrics differ')
                packages.append({'subject':'alpha-world-ms','as_of':day,'old_row':old,'candidate_payload':payload,
                    'candidate_payload_sha256':sha(payload),'candidate_source_sha256':source,'timings':timings,'core_metrics_identical':True})
            except (ValueError,KeyError,TypeError) as error:
                failed.append({'as_of':day,'error_class':type(error).__name__,'reason':str(error)[:200]})
            if (index+1)%25==0:print('MS_HISTORY_PROGRESS '+json.dumps({'checked':index+1,'qualified':len(packages),'failed':len(failed)}),flush=True)
    package={'read_only':True,'database_writes':0,'subject':'alpha-world-ms','already_extended':existing,'failures':failed,'qualified':packages}
    data=canonical_json(package).encode();digest=hashlib.sha256(data).hexdigest()
    name='review/platform-upgrade-20261009/ms-history-rehearsal-'+digest+'.json'
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    blob=service.get_blob_client('internal-reports',name);blob.upload_blob(data,overwrite=False)
    if blob.download_blob().readall()!=data:raise ValueError('Historical backup readback differs')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'database_writes':0,'historical_rows':len(rows),'qualified':len(packages),
        'already_extended':len(existing),'failed':len(failed),'failure_examples':failed[:5],'private_blob':name,'blob_sha256':digest,
        'core_metrics_identical_for_qualified_rows':True,'report_or_approval_writes':0}),flush=True)
if __name__=='__main__':main()
