"""Freeze a website-only delta and dispatch one temporary PostgreSQL rehearsal."""
import argparse
import base64
import zlib
import hashlib
import json
from pathlib import Path
import re

from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import run_ms_website_input_probe as probe
from read_ms_website_input_probe import verify_env

ROOT=probe.ROOT
STAGE=probe.FOLDER/'postgres-rehearsal-v4'
BASE_WEB=ROOT/'.work/alpha-ms-distribution-v14r13-20261007/context/src/ibkr_etl/web.py'


def replace_block(base,current,start,end):
    if base.count(start)!=1 or current.count(start)!=1: raise ValueError('Unambiguous section required')
    left,tail=base.split(start,1); _,replacement=current.split(start,1)
    _,right=tail.split(end,1); body,_=replacement.split(end,1)
    return left+start+body+end+right


def scoped_web():
    base=BASE_WEB.read_text(encoding='utf-8')
    current=(ROOT/'src/ibkr_etl/web.py').read_text(encoding='utf-8')
    for start,end in (('@app.get("/api/portfolio/{account_id}/benchmark-comparison")','\nACCOUNT_SECTIONS ='),
            ('def _onboarding_preview_payload(', '@app.get("/api/onboarding/{subject_id}/preview")')):
        base=replace_block(base,current,start,end)
    needle='from .onboarding_preview import build_physical_preview_validation\n'
    base=base.replace(needle,needle+'from .website_benchmarks import website_comparison_version, MS_HKD_COMPARISON_VERSION\n',1)
    for marker,new in ((" const s=data.subject,n=data.nav,p=data.positions;",
            " document.querySelector('#preview-benchmarks').previousElementSibling.textContent=`${{s.reporting_currency}} benchmark reconciliation`;"),
            ("const m=bc.metrics,items=", "document.querySelector('#benchmark-note').textContent+=` Aligned comparison: ${{m.period_start}} through ${{m.period_end}} · Reporting currency: ${{bc.reporting_currency}} · ${{m.observations}} common-date observations. Rebased to 100 at the first common observation; unmatched market dates are not filled.`;")):
        lines=base.splitlines(); indices=[i for i,line in enumerate(lines) if marker in line]
        if len(indices)!=1 or new not in current: raise ValueError('Scoped UI delta differs')
        lines.insert(indices[0]+1,new); base='\n'.join(lines)+'\n'
    if 'report_design_updates' in base: raise ValueError('Unrelated report-editing feature must not enter website-only release')
    compile(base,'scoped-website','exec')
    return base


def bundle():
    modules=json.loads((STAGE/'frozen-modules.json').read_bytes())
    binding=json.loads((STAGE/'binding.json').read_bytes())
    if any(hashlib.sha256(code.encode()).hexdigest()!=binding['module_sha256'][name] for name,code in modules.items()):
        raise ValueError('Frozen modules changed')
    runner=(STAGE/'frozen-runner.py').read_text(encoding='utf-8')
    if hashlib.sha256(runner.encode()).hexdigest()!=binding['runner_sha256']: raise ValueError('Frozen rehearsal changed')
    source="import sys,types\nsys.path[:0]=['/app/src','/app/scripts']\nimport ibkr_etl\n"
    for name in ('website_benchmarks','provisional_benchmark','onboarding_preview','web'):
        source+="m=types.ModuleType('ibkr_etl."+name+"');m.__package__='ibkr_etl';sys.modules[m.__name__]=m\n"
        source+='exec(compile('+repr(modules[name])+",'ms-website-frozen-"+name+"','exec'),m.__dict__)\n"
        source+='setattr(ibkr_etl,'+repr(name)+',m)\n'
    source+='BINDING='+repr(binding)+'\n'+runner
    compressed=base64.b64encode(zlib.compress(source.encode(),9)).decode()
    return compressed,binding


def runtime_template(job,encoded):
    # Linux bounds each individual argument. Preserve exact frozen bytes while
    # spreading the non-secret source across bounded, hash-bound arguments.
    loader="import base64,zlib,sys;exec(compile(zlib.decompress(base64.b64decode(''.join(sys.argv[1:]))).decode(),'ms-website-rehearsal-v4','exec'))"
    template=probe.corrected_template(job,loader)
    template['containers'][0]['args'].extend(encoded[i:i+60000] for i in range(0,len(encoded),60000))
    if len(encoded)>1000000: raise ValueError('Rehearsal payload exceeds bounded total size')
    return template


def main():
    p=argparse.ArgumentParser();p.add_argument('stage',choices=('prepare','dispatch','inspect')); args=p.parse_args()
    job=probe.request('GET',probe.release.path(probe.JOB)+probe.release.API)
    probe.release.scope(job,probe.JOB)
    if job['properties']['template']['containers'][0]['image']!=probe.IMAGE: raise ValueError('Installed baseline changed')
    if args.stage=='prepare':
        if STAGE.exists(): raise ValueError('Preserve prior rehearsal')
        capture=probe.release.read(probe.FOLDER,'qualified-v3');probe.verify_protected(capture)
        if probe.sha(probe.FOLDER/'inputs.json')!=capture['inputs_sha256']: raise ValueError('Protected source input changed')
        modules={name:(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8')
                 for name in ('website_benchmarks','provisional_benchmark','onboarding_preview')}
        modules['web']=scoped_web()
        runner=(ROOT/'scripts/qualify_ms_website_hkd_postgres.py').read_text(encoding='utf-8')
        binding={'inputs_sha256':capture['inputs_sha256'],'input_blob':probe.PREFIX+'inputs.json',
            'output_blob':probe.PREFIX+'postgres-rehearsal-v4.json',
            'module_sha256':{name:hashlib.sha256(code.encode()).hexdigest() for name,code in modules.items()},
            'runner_sha256':hashlib.sha256(runner.encode()).hexdigest(),
            'website_baseline_sha256':probe.sha(BASE_WEB),
            'scope':'Website-only HKD S&P 500 primary / HSI secondary; unchanged factsheets; temporary PostgreSQL tables'}
        probe.release.window();probe.release.idle(probe.JOB)
        STAGE.mkdir()
        with (STAGE/'frozen-modules.json').open('x',encoding='utf-8') as f: json.dump(modules,f)
        with (STAGE/'frozen-runner.py').open('x',encoding='utf-8',newline='\n') as f: f.write(runner)
        probe.release.build.save(STAGE,'binding',binding)
        source,_=bundle(); template=runtime_template(job,source)
        probe.no_matching_execution(probe.JOB,[template['containers'][0]['args']])
        record={'binding_sha256':probe.sha(STAGE/'binding.json'),'template':template,
            'controller_sha256':probe.sha(Path(__file__)),'job_settings_sha256':probe.release.settings_invariant(job),
            'persistent_database_writes':0,'website_changed':False,'factsheets_changed':False}
        probe.release.build.save(STAGE,'intent',{**record,'protected_receipt':probe.retain(record,'ms-website-hkd-postgres-v4')})
        print(json.dumps({'status':'prepared','scope':binding['scope']}),flush=True); return
    intent=probe.release.read(STAGE,'intent');probe.verify_protected(intent)
    source,binding=bundle();template=runtime_template(job,source)
    if (intent['template']!=template or intent['binding_sha256']!=probe.sha(STAGE/'binding.json')
            or intent['controller_sha256']!=probe.sha(Path(__file__))
            or intent['job_settings_sha256']!=probe.release.settings_invariant(job)):
        raise ValueError('Frozen rehearsal/job changed')
    if args.stage=='dispatch':
        if (STAGE/'dispatch-intent.json').exists(): raise ValueError('Inspect rather than redispatch')
        probe.release.window();probe.release.idle(probe.JOB)
        probe.no_matching_execution(probe.JOB,[template['containers'][0]['args']])
        probe.release.build.save(STAGE,'dispatch-intent',{'binding_sha256':intent['binding_sha256']})
        ack=probe.request('POST',probe.release.path(probe.JOB)+'/start'+probe.release.API,data=template)
        if not ack.get('name'): raise ValueError('Unknown dispatch; do not repeat')
        probe.release.build.save(STAGE,'dispatch',{'execution':ack['name']})
        print(json.dumps({'execution':ack['name'],'persistent_database_writes':0}),flush=True);return
    execution=probe.release.read(STAGE,'dispatch')['execution']
    if not re.fullmatch(re.escape(probe.JOB)+'-[a-z0-9]{7}',execution): raise ValueError('Unexpected execution')
    run=probe.request('GET',probe.release.path(probe.JOB)+'/executions/'+execution+probe.release.API)
    status=run['properties']['status']; print(json.dumps({'execution':execution,'status':status}),flush=True)
    if status!='Succeeded': return
    actual=run['properties']['template']['containers'];expected=template['containers']
    if len(actual)!=len(expected) or len(actual)!=1 or any(actual[0].get(k)!=expected[0].get(k) for k in ('image','command','args','name')):
        raise ValueError('Actual runtime source/image differs')
    verify_env(expected[0]['env'],actual[0]['env'],job)
    blob=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()).get_blob_client('internal-reports',binding['output_blob'])
    content=blob.download_blob().readall(); digest=hashlib.sha256(content).hexdigest()
    if blob.get_blob_properties().metadata.get('sha256')!=digest: raise ValueError('Runtime proof integrity differs')
    proof=json.loads(content)
    if (proof['status']!='temporary_postgres_and_website_readers_passed' or proof['source_binding']!=binding
            or proof['public_fingerprints_before']!=proof['public_fingerprints_after']
            or proof['database_persistent_writes']!=0 or not proof['legacy_usd_rows_unchanged']
            or proof['historical_batch_status']!='approved' or not proof['preview_validation']['ready_for_owner_preview_approval']
            or any(proof[k] is not False for k in ('factsheets_changed','ibkr_source_changed','reports_released','onboarding_activated'))):
        raise ValueError('Runtime qualification scope differs')
    path=STAGE/'proof.json'
    if path.exists():
        if path.read_bytes()!=content: raise ValueError('Existing proof differs')
    else:
        with path.open('xb') as f:f.write(content)
    record={'status':'private_postgres_rehearsal_verified','execution':execution,'proof_sha256':digest,
            'binding_sha256':intent['binding_sha256'],'database_persistent_writes':0,'factsheets_changed':False}
    if not (STAGE/'qualified.json').exists():
        probe.release.build.save(STAGE,'qualified',{**record,'protected_receipt':probe.retain(record,'ms-website-hkd-postgres-v4-passed')})
    print(json.dumps({'status':record['status'],'audit':proof['audit']}),flush=True)


if __name__=='__main__':main()
