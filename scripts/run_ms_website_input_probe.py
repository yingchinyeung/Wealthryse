"""One-shot private read-only MS inputs; preserve installed job/configuration."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import deploy_ms_presentation_worker_release as release
from deploy_metadata_monitor import request
from verify_installed_ms_worker_release import corrected_template, no_matching_execution
from run_specialist_gateway_read_verification import retain
from rehearse_ms_worker_image_rollback import restore_protected

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT / '.work/alpha-ms-website-comparisons-hkd-20261008-v1'
JOB = 'ibkrv2stg-onboard-render'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:af5d255d4f71f762db763fa7bb8ab542e9c6f52a211c48b51cb1f44deb488fe9'
PREFIX = 'review/alpha-world-ms/website-comparisons-hkd-20261008-v1/'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_protected(record):
    restored, _ = restore_protected(record['protected_receipt'])
    if restored != {k:v for k,v in record.items() if k != 'protected_receipt'}:
        raise ValueError('Protected intent differs')


def main():
    p=argparse.ArgumentParser(); p.add_argument('stage', choices=('prepare','dispatch','inspect'))
    args=p.parse_args(); source_path=ROOT/'scripts/probe_ms_website_comparison_inputs.py'
    source=source_path.read_text(encoding='utf-8')
    job=request('GET', release.path(JOB)+release.API)
    release.scope(job, JOB)
    if job['properties']['template']['containers'][0]['image'] != IMAGE:
        raise ValueError('Installed baseline changed')
    template=corrected_template(job, source)
    if args.stage == 'prepare':
        if FOLDER.exists(): raise ValueError('Preserve prior attempt')
        release.window(); release.idle(JOB)
        no_matching_execution(JOB, [template['containers'][0]['args']])
        record={'job':JOB,'image':IMAGE,'template':template,'source_sha256':sha(source_path),
                'controller_sha256':sha(Path(__file__)),
                'job_settings_sha256':release.settings_invariant(job),
                'normal_processing_invoked':False,'database_writes':0,'factsheets_changed':False,
                'authorization':'Owner requested website-only S&P 500 / HSI comparisons in HKD; private read-only input inspection'}
        FOLDER.mkdir()
        release.build.save(FOLDER, 'intent', {**record,'protected_receipt':retain(record,'ms-website-hkd-inputs-v1')})
        print(json.dumps({'status':'prepared','database_writes':0}),flush=True); return
    intent=release.read(FOLDER,'intent'); verify_protected(intent)
    if (intent['source_sha256'] != sha(source_path) or intent['controller_sha256'] != sha(Path(__file__))
            or intent['template'] != template or intent['job_settings_sha256'] != release.settings_invariant(job)):
        raise ValueError('Frozen inputs or installed job drifted')
    if args.stage == 'dispatch':
        if (FOLDER/'dispatch-intent.json').exists(): raise ValueError('Inspect rather than redispatch')
        release.window(); release.idle(JOB)
        no_matching_execution(JOB, [template['containers'][0]['args']])
        release.build.save(FOLDER,'dispatch-intent',{'source_sha256':intent['source_sha256'],'job':JOB})
        ack=request('POST',release.path(JOB)+'/start'+release.API,data=template)
        if not ack.get('name'): raise ValueError('Unknown dispatch; do not repeat')
        release.build.save(FOLDER,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name']}),flush=True); return
    ack=release.read(FOLDER,'dispatch'); execution=ack['execution']
    if not re.fullmatch(re.escape(JOB)+'-[a-z0-9]{7}',execution): raise ValueError('Unexpected execution')
    run=request('GET',release.path(JOB)+'/executions/'+execution+release.API)
    status=run['properties']['status']
    print(json.dumps({'execution':execution,'status':status}),flush=True)
    if status != 'Succeeded': return
    actual=run['properties']['template']['containers'][0]; expected=template['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image','command','args','name','env')):
        raise ValueError('Executed probe differs')
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net', credential=AzureCliCredential())
    blob=service.get_blob_client('internal-reports',PREFIX+'inputs.json')
    content=blob.download_blob().readall(); digest=hashlib.sha256(content).hexdigest()
    if blob.get_blob_properties().metadata.get('sha256') != digest: raise ValueError('Private inputs hash differs')
    value=json.loads(content)
    if value['status'] != 'read_only_inputs_captured' or value['database_writes'] != 0:
        raise ValueError('Unexpected probe scope')
    proof={'status':'read_only_inputs_verified','execution':execution,'inputs_sha256':digest,
           'source_sha256':intent['source_sha256'],'persistent_settings_preserved':True,
           'database_writes':0,'factsheets_changed':False,'onboarding_activated':False}
    if (FOLDER/'qualified.json').exists():
        if release.read(FOLDER,'qualified') != proof: raise ValueError('Existing proof differs')
    else:
        with (FOLDER/'inputs.json').open('xb') as f: f.write(content)
        release.build.save(FOLDER,'qualified',proof)
    print(json.dumps({'status':proof['status'],'inputs_sha256':digest,
                      'nav_rows':len(value['nav']),'price_rows':len(value['prices']),'fx_rows':len(value['fx'])}),flush=True)


if __name__ == '__main__': main()
