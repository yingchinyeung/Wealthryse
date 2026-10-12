"""One-shot scoped read-only crisis diagnostic; no normal worker dispatch."""
import hashlib
import json
import sys
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import build_ms_presentation_worker_candidate as build
import deploy_ms_presentation_worker_release as jobs
from deploy_metadata_monitor import request
from verify_installed_ms_worker_release import corrected_template, no_matching_execution
from run_specialist_gateway_read_verification import retain

FOLDER = build.ROOT / '.work/alpha-ms-crisis-release-20261008/preflight-v2'
API = '?api-version=2024-03-01'
JOB = build.JOBS[0]
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:bae3b6cb43d48af8c525e57895c61b74c25e4b9bc688dd90458a2995a954e372'


def save(name, record):
    build.save(FOLDER,name,{**record,'protected_receipt':retain(record,'ms-crisis-release-preflight-v2-'+name)})


def main():
    stage = sys.argv[1]
    job = request('GET',jobs.path(JOB)+API)
    if job['properties']['template']['containers'][0]['image'] != IMAGE:
        raise ValueError('Installed worker drift')
    if stage == 'prepare':
        if FOLDER.exists(): raise ValueError('Existing diagnostic must be inspected, not repeated')
        jobs.window(); jobs.idle(JOB)
        source = (build.ROOT/'scripts/inspect_ms_crisis_release_inputs.py').read_text(encoding='utf-8')
        template = corrected_template(job,source)
        no_matching_execution(JOB,[template['containers'][0]['args']])
        FOLDER.mkdir(parents=True)
        with (FOLDER/'frozen-source.py').open('x',encoding='utf-8') as stream:stream.write(source)
        save('intent',{'image':IMAGE,'template':template,'source_sha256':build.sha(FOLDER/'frozen-source.py'),
             'settings_invariant':jobs.settings_invariant(job),'database_writes':0,'market_data_requests':0})
        print(json.dumps({'prepared':True,'database_writes':0}),flush=True); return
    intent = json.loads((FOLDER/'intent.json').read_bytes())
    if jobs.settings_invariant(job) != intent['settings_invariant'] or build.sha(FOLDER/'frozen-source.py') != intent['source_sha256']:
        raise ValueError('Diagnostic source/settings drift')
    if stage == 'dispatch':
        jobs.window(); jobs.idle(JOB)
        no_matching_execution(JOB,[intent['template']['containers'][0]['args']])
        save('dispatch-intent',{'source_sha256':intent['source_sha256'],'image':IMAGE})
        ack = request('POST',jobs.path(JOB)+'/start'+API,data=intent['template'])
        save('dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name']}),flush=True); return
    if stage != 'inspect': raise ValueError('Unknown stage')
    ack=json.loads((FOLDER/'dispatch.json').read_bytes())
    run=request('GET',jobs.path(JOB)+'/executions/'+ack['execution']+API)
    status=run['properties']['status']; print(json.dumps({'status':status}),flush=True)
    if status != 'Succeeded': return
    wanted=intent['template']['containers'][0]; actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed diagnostic differs')
    env={e['name']:e for e in wanted['env']}
    service=BlobServiceClient(env['AZURE_STORAGE_ACCOUNT_URL']['value'],credential=AzureCliCredential())
    data=service.get_blob_client('internal-reports','review/alpha-world-ms/crisis-release-20261008/preflight-v2/inputs.json').download_blob().readall()
    value=json.loads(data)
    if value['database_writes']!=0 or not value['read_only_enforced'] or len(value['snapshots'])!=21:raise ValueError('Diagnostic scope differs')
    with (FOLDER/'inputs.json').open('xb') as stream:stream.write(data)
    save('qualified',{'status':'passed','execution':ack['execution'],'inputs_sha256':hashlib.sha256(data).hexdigest(),'database_writes':0})
    print(json.dumps({'status':'captured','months':21,'replay_rows':len(value['replays']),'historical_fx_rows':len(value['fx'])}),flush=True)


if __name__ == '__main__':main()
