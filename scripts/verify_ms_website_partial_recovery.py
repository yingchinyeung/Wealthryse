"""Read-only original selection check before image-only partial release recovery."""
import argparse
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import control_ms_website_exact_cutover as control
from read_ms_website_input_probe import verify_env

p=control.p
FOLDER=control.FOLDER/'partial-recovery-v1'
OUTPUT=control.PREFIX+'partial-recovery-original-selection-v1.json'
CODE='''import os,json,hashlib
import psycopg
from psycopg.rows import dict_row
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
data=service.get_blob_client('internal-reports',BINDING['input_blob']).download_blob().readall()
if hashlib.sha256(data).hexdigest()!=BINDING['inputs_sha256']:raise ValueError('Original input differs')
inputs=json.loads(data)
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row,options='-c default_transaction_read_only=on -c statement_timeout=120000',connect_timeout=25) as c:
 c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
 c.execute("SELECT set_config('app.tenant_id',%s,true)",('00000000-0000-0000-0000-000000000001',))
 rows=[dict(r) for r in c.execute("SELECT benchmark_id,mapping_type,effective_from,effective_to,reporting_currency,created_at FROM portfolio_benchmark_mapping WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY benchmark_id,effective_from",('00000000-0000-0000-0000-000000000001',))]
 if rows!=inputs['mappings']:raise ValueError('Original mapping selection drifted')
 n=c.execute("SELECT count(*) n FROM performance_comparison_daily WHERE tenant_id=%s AND subject_id='alpha-world-ms' AND calculation_version='benchmark-comparison-v2-ms-hkd'",('00000000-0000-0000-0000-000000000001',)).fetchone()['n']
 if n:raise ValueError('A database cutover may already exist; full recovery required')
 c.rollback()
proof={'status':'live_original_selection_verified','binding':BINDING,'persistent_database_writes':0}
content=json.dumps(proof,sort_keys=True,separators=(',',':')).encode();digest=hashlib.sha256(content).hexdigest()
blob=service.get_blob_client('internal-reports',BINDING['output_blob']);blob.upload_blob(content,overwrite=False,metadata={'sha256':digest})
if blob.download_blob().readall()!=content:raise ValueError('Read-only recovery proof differs')
print('MS_PARTIAL_RECOVERY_BASELINE_VERIFIED',flush=True)
'''


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));args=parser.parse_args()
    if (control.stage_folder('apply')/'dispatch-intent.json').exists():raise ValueError('Full database selection recovery required')
    binding=p.release.read(control.FOLDER,'binding');p.verify_protected(binding)
    bound={k:binding[k] for k in ('input_blob','inputs_sha256')};bound['output_blob']=OUTPUT
    job=p.request('GET',p.release.path(p.JOB)+p.release.API);p.release.scope(job,p.JOB)
    if job['properties']['template']['containers'][0]['image']!=p.IMAGE:raise ValueError('Manual worker drift')
    template=p.corrected_template(job,'BINDING='+repr(bound)+'\n'+CODE)
    if args.stage=='prepare':
        if FOLDER.exists():raise ValueError('Preserve original partial recovery preparation')
        FOLDER.mkdir();record={'template':template,'controller_sha256':p.sha(Path(__file__)),
            'binding_sha256':p.sha(control.FOLDER/'binding.json'),'job_settings_sha256':p.release.settings_invariant(job),
            'persistent_database_writes':0}
        p.release.build.save(FOLDER,'intent',{**record,'protected_receipt':p.retain(record,'ms-partial-recovery-baseline-intent-v1')})
        print('PARTIAL_RECOVERY_BASELINE_PREPARED',flush=True);return
    intent=p.release.read(FOLDER,'intent');p.verify_protected(intent)
    if intent['template']!=template or intent['controller_sha256']!=p.sha(Path(__file__)) or intent['job_settings_sha256']!=p.release.settings_invariant(job):
        raise ValueError('Frozen partial recovery check drift')
    if args.stage=='dispatch':
        if (FOLDER/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        p.release.build.save(FOLDER,'dispatch-intent',{'persistent_database_writes':0})
        ack=p.request('POST',p.release.path(p.JOB)+'/start'+p.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown outcome; inspect, never repeat')
        p.release.build.save(FOLDER,'dispatch',{'execution':ack['name']});print(ack['name'],flush=True);return
    name=p.release.read(FOLDER,'dispatch')['execution'];run=p.request('GET',p.release.path(p.JOB)+'/executions/'+name+p.release.API)
    print(run['properties']['status'],flush=True)
    if run['properties']['status']!='Succeeded':return
    c=run['properties']['template']['containers'][0];wanted=template['containers'][0]
    if any(c.get(k)!=wanted.get(k) for k in ('image','command','args','name')):raise ValueError('Executed recovery check differs')
    verify_env(wanted['env'],c['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',OUTPUT)
    data=blob.download_blob().readall();proof=json.loads(data)
    if proof['status']!='live_original_selection_verified' or proof['binding']!=bound or proof['persistent_database_writes']!=0 or blob.get_blob_properties().metadata.get('sha256')!=hashlib.sha256(data).hexdigest():
        raise ValueError('Read-only recovery baseline integrity differs')
    record={'status':proof['status'],'binding_sha256':intent['binding_sha256'],'proof_sha256':hashlib.sha256(data).hexdigest(),
            'execution':name,'controller_sha256':p.sha(Path(__file__)),'persistent_database_writes':0}
    p.release.build.save(control.FOLDER,'partial-recovery-baseline-qualified',
        {**record,'protected_receipt':p.retain(record,'ms-partial-recovery-original-selection-passed-v1')})


if __name__=='__main__':main()
