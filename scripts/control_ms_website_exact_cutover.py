"""One-shot exact-writer execution; owner authorized the protected scoped proposal."""
import argparse
import ast
import copy
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import continue_ms_website_release_rollback as continuation
import qualify_ms_website_fresh_release_runtime as fresh
from read_ms_website_input_probe import verify_env

r=continuation.original
p=r.p
FOLDER=r.FOLDER/'live-cutover-v1'
PREFIX='review/alpha-world-ms/website-release-20261008-v1/live-cutover-v1/'


def literal_assignment(source,name):
    matches=[node.value for node in ast.walk(ast.parse(source)) if isinstance(node,ast.Assign)
             and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)]
    if len(matches)!=1:raise ValueError('Unambiguous frozen query set required: '+name)
    return ast.literal_eval(matches[0])


def gate():
    proposal=p.release.read(r.FOLDER,'cutover-proposal');p.verify_protected(proposal)
    _,image=r.qualified.image_gate()
    scan_receipt=p.release.read(r.qualified.candidate.FOLDER,'scan-protected-receipt')
    age=datetime.now(timezone.utc)-datetime.fromisoformat(scan_receipt['version_id'].replace('Z','+00:00'))
    if not timedelta(0)<=age<timedelta(hours=24):raise ValueError('Refresh candidate scan before release')
    if any(change['to_image']!=image for change in proposal['image_changes'].values()):raise ValueError('Approved image differs')
    return proposal,image


def bundle(binding):
    writer=(FOLDER/'frozen-writer.py').read_text(encoding='utf-8')
    runner=(FOLDER/'frozen-runner.py').read_text(encoding='utf-8')
    if hashlib.sha256(writer.encode()).hexdigest()!=binding['writer_sha256'] or hashlib.sha256(runner.encode()).hexdigest()!=binding['runner_sha256']:
        raise ValueError('Exact frozen writer/runner changed')
    source="import sys,types,hashlib,json\nfrom pathlib import Path\nsys.path[:0]=['/app/src','/app/scripts']\n"
    source+='BINDING='+repr(binding)+'\n'
    source+='''
for name,digest in BINDING['module_sha256'].items():
 if hashlib.sha256(Path('/app/src/ibkr_etl',name+'.py').read_bytes()).hexdigest()!=digest:raise ValueError('Candidate application bytes differ')
release=json.loads(Path('/app/MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())
for name,digest in release['approved_report_files_sha256'].items():
 if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest:raise ValueError('Approved report bytes differ')
'''
    source+="module=types.ModuleType('ms_website_cutover_writer');sys.modules[module.__name__]=module\n"
    source+='exec(compile('+repr(writer)+",'frozen-ms-website-cutover-writer','exec'),module.__dict__)\n"
    return source+runner


def stage_folder(mode):return FOLDER/(mode+'-v1')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'))
    parser.add_argument('--mode',choices=('qualify','apply','verify','rollback'),default='qualify');args=parser.parse_args()
    proposal,image=gate();base=p.release.read(r.FOLDER,'baseline');p.verify_protected(base)
    job=p.request('GET',base['manual_job']['id']+p.release.API);p.release.scope(job,p.JOB)
    if p.release.settings_invariant(job)!=p.release.settings_invariant(base['manual_job']) or job['properties']['template']['containers'][0]['image']!=p.IMAGE:
        raise ValueError('Manual reporting worker changed')
    if args.stage=='prepare' and args.mode=='qualify':
        r.live_unchanged(base)
        if FOLDER.exists():raise ValueError('Preserve original release intent')
        qualification=p.release.read(fresh.STAGE,'qualified');p.verify_protected(qualification)
        proof=p.release.read(fresh.STAGE,'proof')
        if p.sha(fresh.STAGE/'proof.json')!=qualification['proof_sha256']:raise ValueError('Native proof differs')
        input_qualification=p.release.read(r.FOLDER/'fresh-inputs-v1','qualified');p.verify_protected(input_qualification)
        writer=(p.ROOT/'scripts/ms_website_cutover_writer.py').read_text(encoding='utf-8')
        runner=(p.ROOT/'scripts/run_ms_website_exact_cutover.py').read_text(encoding='utf-8')
        queries=literal_assignment((r.FOLDER/'fresh-inputs-v1/frozen-source.py').read_text(encoding='utf-8'),'queries')
        fingerprints=literal_assignment((fresh.SOURCE/'frozen-runner.py').read_text(encoding='utf-8'),'fingerprint_queries')
        fingerprints['audit']="SELECT * FROM public.audit_events WHERE tenant_id=%s AND subject_id='alpha-world-ms' ORDER BY audit_event_id"
        now=datetime.now(timezone.utc)
        binding={'image':image,'writer_sha256':hashlib.sha256(writer.encode()).hexdigest(),
            'runner_sha256':hashlib.sha256(runner.encode()).hexdigest(),'controller_sha256':p.sha(Path(__file__)),
            'input_blob':input_qualification['input_blob'],'inputs_sha256':input_qualification['inputs_sha256'],
            'native_proof_blob':proof['source_binding']['output_blob'],'native_proof_sha256':qualification['proof_sha256'],
            'module_sha256':proof['source_binding']['module_sha256'],'source_queries':queries,
            'fingerprint_queries':fingerprints,'release_day':now.date().isoformat(),'release_stamp':now.isoformat(),
            'release_reference':{'proposal_sha256':p.sha(r.FOLDER/'cutover-proposal.json'),
                'input_sha256':input_qualification['inputs_sha256'],'release_id':'ms-website-hkd-20261008-v1'},
            'authorization':'Owner replied ok, please continue your work to the explicit website, daily benchmark and MS comparison cutover request; brief interruption disclosed; final activation excluded'}
        FOLDER.mkdir()
        for name,code in (('frozen-writer.py',writer),('frozen-runner.py',runner)):
            with (FOLDER/name).open('x',encoding='utf-8',newline='\n') as stream:stream.write(code)
        p.release.build.save(FOLDER,'binding',{**binding,'protected_receipt':p.retain(binding,'ms-exact-cutover-authorized-binding-v1')})
    binding=p.release.read(FOLDER,'binding');p.verify_protected(binding)
    if binding['controller_sha256']!=p.sha(Path(__file__)) or binding['image']!=image or binding['release_reference']['proposal_sha256']!=p.sha(r.FOLDER/'cutover-proposal.json'):
        raise ValueError('Frozen authorized release differs')
    mode=args.mode;stage=stage_folder(mode)
    if mode!='qualify':
        qualified=p.release.read(stage_folder('qualify'),'qualified');p.verify_protected(qualified)
        if qualified['status']!='exact_writer_and_rollback_qualified' or qualified['binding_sha256']!=p.sha(FOLDER/'binding.json'):
            raise ValueError('Exact writer must pass isolated qualification')
        if mode in ('apply','verify'):
            for kind in ('website','benchmark'):
                record=p.release.read(FOLDER,kind+'-qualified');p.verify_protected(record)
                if record['image']!=image:raise ValueError('Coordinated images not qualified')
    mode_binding={k:v for k,v in binding.items() if k!='protected_receipt'}
    mode_binding.update(mode=mode,output_blob=PREFIX+mode+'-proof-v1.json')
    template=p.corrected_template(job,bundle(mode_binding));template['containers'][0]['image']=image
    if args.stage=='prepare':
        if stage.exists():raise ValueError('Preserve original mode preparation')
        p.release.window();p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        stage.mkdir()
        record={'mode':mode,'template':template,'binding_sha256':p.sha(FOLDER/'binding.json'),
            'job_settings_sha256':p.release.settings_invariant(job),'persistent_database_writes':mode in ('apply','rollback')}
        p.release.build.save(stage,'intent',{**record,'protected_receipt':p.retain(record,'ms-exact-cutover-'+mode+'-v1-intent')})
        print(json.dumps({'prepared':mode,'persistent_database_writes':record['persistent_database_writes']}),flush=True);return
    intent=p.release.read(stage,'intent');p.verify_protected(intent)
    if intent['template']!=template or intent['binding_sha256']!=p.sha(FOLDER/'binding.json'):
        raise ValueError('Frozen transient command differs')
    if args.stage=='dispatch':
        if (stage/'dispatch-intent.json').exists():raise ValueError('Inspect rather than redispatch')
        if mode=='qualify':r.live_unchanged(base)
        else:
            import deploy_ms_website_cutover as deployment
            deployment.current_images(require_candidate=mode!='rollback')
        p.release.window();p.release.idle(p.JOB);p.no_matching_execution(p.JOB,[template['containers'][0]['args']])
        p.release.build.save(stage,'dispatch-intent',{'binding_sha256':p.sha(FOLDER/'binding.json'),'mode':mode})
        ack=p.request('POST',base['manual_job']['id']+'/start'+p.release.API,data=template)
        if not ack.get('name'):raise ValueError('Unknown dispatch; inspect, never repeat')
        p.release.build.save(stage,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'mode':mode}),flush=True);return
    name=p.release.read(stage,'dispatch')['execution']
    run=p.request('GET',base['manual_job']['id']+'/executions/'+name+p.release.API)
    print(json.dumps({'execution':name,'status':run['properties']['status']}),flush=True)
    if run['properties']['status']!='Succeeded':return
    actual=run['properties']['template']['containers'];wanted=template['containers']
    if len(actual)!=1 or any(actual[0].get(k)!=wanted[0].get(k) for k in ('name','image','command','args')):
        raise ValueError('Executed source/image differs')
    verify_env(wanted[0]['env'],actual[0]['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',mode_binding['output_blob'])
    content=blob.download_blob().readall();proof=json.loads(content);digest=hashlib.sha256(content).hexdigest()
    expected='exact_writer_and_rollback_qualified' if mode=='qualify' else 'live_'+mode+'_verified'
    if (proof['binding']!=mode_binding or proof['status']!=expected or blob.get_blob_properties().metadata.get('sha256')!=digest
        or any(proof[k] is not False for k in ('factsheets_changed','ibkr_source_changed','reports_released','approvals_written','onboarding_activated'))):
        raise ValueError('Runtime cutover proof differs')
    if mode=='qualify' and (proof['persistent_database_writes']!=0 or proof['public_before']!=proof['public_after']
        or not all(proof[k] for k in ('failure_after_100_rows_rolled_back','caller_commit_safe','selection_rollback_passed','comparison_history_retained'))):
        raise ValueError('Exact isolated rollback failed')
    if not (stage/'proof.json').exists():
        with (stage/'proof.json').open('xb') as stream:stream.write(content)
    elif (stage/'proof.json').read_bytes()!=content:raise ValueError('Prior proof bytes differ')
    record={'status':expected,'mode':mode,'image':image,'execution':name,'proof_sha256':digest,
        'binding_sha256':p.sha(FOLDER/'binding.json'),'comparison_rows':proof['comparison_rows'],
        'persistent_database_writes':proof['persistent_database_writes']}
    if not (stage/'qualified.json').exists():
        p.release.build.save(stage,'qualified',{**record,'protected_receipt':p.retain(record,'ms-exact-cutover-'+mode+'-v1-passed')})
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
