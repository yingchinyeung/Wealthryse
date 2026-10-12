"""Temporary PostgreSQL rehearsal on exact scanned candidate, without overlays."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import prepare_ms_website_benchmark_candidate_v2 as candidate
import scan_ms_presentation_worker_candidate as scanner
from read_ms_website_input_probe import verify_env

probe=candidate.rehearsal.probe
STAGE=candidate.FOLDER/'native-runtime-v1'


def image_gate():
    source,native,output=scanner.verify(candidate.FOLDER)
    scan=json.loads((candidate.FOLDER/'scan-completed.json').read_bytes())
    receipt=json.loads((candidate.FOLDER/'scan-protected-receipt.json').read_bytes())
    protected,_=probe.restore_protected(receipt)
    image=output['registry']+'/'+output['repository']+'@'+output['digest']
    if protected!=scan or scan['image']!=image or scan['assessment']['status']!='pass':
        raise ValueError('Exact candidate scan must pass')
    return source,image


def bundle(binding,runner):
    source="import sys,hashlib,json\nfrom pathlib import Path\nsys.path[:0]=['/app/src','/app/scripts']\n"
    source+='BINDING='+repr(binding)+'\n'
    source+='''
for name,digest in BINDING['module_sha256'].items():
 if hashlib.sha256(Path('/app/src/ibkr_etl',name+'.py').read_bytes()).hexdigest()!=digest:
  raise ValueError('Installed candidate module differs: '+name)
r=json.loads(Path('/app/MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())
for name,digest in r['approved_report_files_sha256'].items():
 if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:
  raise ValueError('Approved reporting file changed')
'''
    return source+runner


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('prepare','dispatch','inspect'));args=p.parse_args()
    source,image=image_gate()
    job=probe.request('GET',probe.release.path(probe.JOB)+probe.release.API)
    probe.release.scope(job,probe.JOB)
    if job['properties']['template']['containers'][0]['image']!=probe.IMAGE:
        raise ValueError('Installed reporting baseline changed')
    if args.stage=='prepare':
        if STAGE.exists():raise ValueError('Preserve prior native rehearsal')
        old=candidate.rehearsal.STAGE
        binding=json.loads((old/'binding.json').read_bytes())
        runner=(old/'frozen-runner.py').read_text(encoding='utf-8')
        if hashlib.sha256(runner.encode()).hexdigest()!=binding['runner_sha256']:
            raise ValueError('Qualified PostgreSQL rehearsal source differs')
        binding['output_blob']=probe.PREFIX+'native-candidate-v2-runtime-v1.json'
        binding['candidate_image']=image;binding['source_intent_sha256']=probe.sha(candidate.FOLDER/'source-intent.json')
        code=bundle(binding,runner);template=probe.corrected_template(job,code)
        template['containers'][0]['image']=image
        probe.release.window();probe.release.idle(probe.JOB)
        probe.no_matching_execution(probe.JOB,[template['containers'][0]['args']])
        STAGE.mkdir();probe.release.build.save(STAGE,'binding',binding)
        with (STAGE/'runner.py').open('x',encoding='utf-8',newline='\n') as f:f.write(runner)
        record={'template':template,'binding_sha256':probe.sha(STAGE/'binding.json'),
            'controller_sha256':probe.sha(Path(__file__)),'job_settings_sha256':probe.release.settings_invariant(job),
            'source_intent_sha256':probe.sha(candidate.FOLDER/'source-intent.json'),
            'scan_sha256':probe.sha(candidate.FOLDER/'scan-completed.json'),'persistent_database_writes':0}
        probe.release.build.save(STAGE,'intent',{**record,'protected_receipt':probe.retain(record,'ms-website-native-runtime-v1-intent')})
        print(json.dumps({'status':'prepared','candidate_image':image,'source_overlays':False}),flush=True);return
    intent=probe.release.read(STAGE,'intent');probe.verify_protected(intent)
    binding=probe.release.read(STAGE,'binding');runner=(STAGE/'runner.py').read_text(encoding='utf-8')
    template=probe.corrected_template(job,bundle(binding,runner));template['containers'][0]['image']=image
    if (intent['template']!=template or intent['controller_sha256']!=probe.sha(Path(__file__))
            or intent['binding_sha256']!=probe.sha(STAGE/'binding.json')
            or intent['job_settings_sha256']!=probe.release.settings_invariant(job)
            or intent['scan_sha256']!=probe.sha(candidate.FOLDER/'scan-completed.json')):
        raise ValueError('Frozen native rehearsal changed')
    if args.stage=='dispatch':
        if (STAGE/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        probe.release.window();probe.release.idle(probe.JOB)
        probe.no_matching_execution(probe.JOB,[template['containers'][0]['args']])
        probe.release.build.save(STAGE,'dispatch-intent',{'binding_sha256':intent['binding_sha256']})
        ack=probe.request('POST',probe.release.path(probe.JOB)+'/start'+probe.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown dispatch; do not repeat')
        probe.release.build.save(STAGE,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'source_overlays':False}),flush=True);return
    name=probe.release.read(STAGE,'dispatch')['execution']
    run=probe.request('GET',probe.release.path(probe.JOB)+'/executions/'+name+probe.release.API)
    status=run['properties']['status'];print(json.dumps({'execution':name,'status':status}),flush=True)
    if status!='Succeeded':return
    actual=run['properties']['template']['containers']
    if len(actual)!=1 or any(actual[0].get(k)!=template['containers'][0].get(k) for k in ('name','command','args','image')):
        raise ValueError('Actual candidate execution differs')
    verify_env(template['containers'][0]['env'],actual[0]['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',binding['output_blob'])
    content=blob.download_blob().readall();proof=json.loads(content)
    digest=hashlib.sha256(content).hexdigest()
    if (blob.get_blob_properties().metadata.get('sha256')!=digest or proof['source_binding']!=binding
            or proof['status']!='temporary_postgres_and_website_readers_passed'
            or proof['public_fingerprints_before']!=proof['public_fingerprints_after']
            or proof['database_persistent_writes']!=0 or not proof['secondary_failure_rollback_verified']
            or not proof['legacy_usd_rows_unchanged'] or proof['historical_batch_status']!='approved'
            or not proof['preview_validation']['ready_for_owner_preview_approval']
            or any(proof[k] is not False for k in ('factsheets_changed','ibkr_source_changed','reports_released','onboarding_activated'))):
        raise ValueError('Native proof failed scope/integrity checks')
    if not (STAGE/'proof.json').exists():
        with (STAGE/'proof.json').open('xb') as f:f.write(content)
    elif (STAGE/'proof.json').read_bytes()!=content:raise ValueError('Local proof drifted')
    record={'status':'native_candidate_postgres_and_readers_passed','image':image,'execution':name,
        'proof_sha256':digest,'source_overlays':False,'persistent_database_writes':0,'factsheets_changed':False}
    if not (STAGE/'qualified.json').exists():
        probe.release.build.save(STAGE,'qualified',{**record,'protected_receipt':probe.retain(record,'ms-website-native-runtime-v1-passed')})
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
