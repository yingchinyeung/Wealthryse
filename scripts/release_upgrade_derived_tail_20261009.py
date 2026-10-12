"""Four-file daily benchmark-tail correction; build only, never deploy."""
import ast,json,os,re,shutil,subprocess,sys
from datetime import datetime,timezone
import release_platform_access_correction_20261009 as r
from prepare_upgrade_derived_tail_20261009 import PRIOR,OUT,FILES
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:3e7caaed1be1d5caf64cd027547c0f79f983f29c0d9b7850f6ad472344b4725a'
JOB='ibkrv2stg-derived-daily';PATH=rid('Microsoft.App/jobs',JOB)+'?api-version=2024-03-01'
r.WEB=OUT;r.IMAGE=IMAGE;r.MARKER='PASS: approved HKD benchmark pair refreshed after derived NAV; reports unchanged'

def prepare():
    runtime=OUT/'runtime';context=OUT/'context'
    if context.exists():raise ValueError('Preserve frozen build')
    before=r.manifest(PRIOR/'local-runtime');after=r.manifest(runtime)
    if {p for p in set(before)|set(after) if before.get(p)!=after.get(p)}!=set(FILES):raise ValueError('Exactly four qualified files required')
    def syntax(path):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        return {getattr(n,'name','statement-'+str(i)):ast.dump(n,include_attributes=False) for i,n in enumerate(tree.body)}
    a,b=syntax(PRIOR/'local-runtime'/FILES[0]),syntax(runtime/FILES[0])
    if {k for k in a.keys()|b.keys() if a.get(k)!=b.get(k)}!={'_refresh_analytics_if_approved'}:raise ValueError('Daily entrypoint scope changed')
    for name in FILES:
        (context/name).parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(runtime/name,context/name)
    shutil.copytree(OUT/'tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    record=r.read(PRIOR/'context/ALPHA_T_OPERATING_RELEASE.json')
    record['base_image']=IMAGE;record['scope']='Post-derived MS/T website benchmark materialization only; no report, formula, approval, identity or schedule changes'
    for name in FILES:record['installed_files_sha256'][name]=r.sha(context/name)
    r.save(context/'ALPHA_T_OPERATING_RELEASE.json',record)
    expected={name:before.get(name) for name in FILES}
    assertion="from pathlib import Path;import hashlib;expected="+repr(expected)+";assert all((hashlib.sha256((Path('/app')/n).read_bytes()).hexdigest()==v) if v else not (Path('/app')/n).exists() for n,v in expected.items())"
    docker='FROM '+IMAGE+'\nRUN '+json.dumps(['python','-c',assertion])+'\n'
    for name in FILES:docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    docker+='COPY --chown=10001:10001 ALPHA_T_OPERATING_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/derived-tail-tests/\n'
    run="import os,sys,subprocess;os.environ['PYTHONPATH']='/app/src:/app/scripts';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/derived-tail-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',run])+'\nRUN '+json.dumps(['python','-c','print('+repr(r.MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:derived-benchmark-tail-20261009-v1','base_image':IMAGE,'changed_files':list(FILES),'files_sha256':r.manifest(context),'financial_formulas_changed':False,'report_templates_changed':False,'cause':'MS/T branches returned before website comparison refresh; the prior benchmark job used yesterday derived NAV.'})
    print('Frozen four-file daily candidate; no live changes')

def qualify():
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(OUT/'runtime/src',OUT/'runtime/scripts'))),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    with (OUT/'local-tests.log').open('x',encoding='utf-8') as f:
        result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(OUT/'context/tests'),'-v'],env=env,cwd=OUT/'runtime',stdout=f,stderr=subprocess.STDOUT,timeout=300)
    output=(OUT/'local-tests.log').read_text(encoding='utf-8')
    if result.returncode:print(output[-3000:]);raise ValueError('Daily candidate regression failed')
    count=int(re.findall(r'Ran (\d+) tests',output)[-1])
    r.save(OUT/'local-qualification.json',{'status':'passed','tests_run':count,'source_intent_sha256':r.sha(OUT/'source-intent.json'),'log_sha256':r.sha(OUT/'local-tests.log')});print(json.dumps({'tests_passed':count}))

def build():
    source=r.read(OUT/'source-intent.json');proof=r.read(OUT/'local-qualification.json')
    if source['files_sha256']!=r.manifest(OUT/'context') or proof['source_intent_sha256']!=r.sha(OUT/'source-intent.json'):raise ValueError('Frozen candidate drift')
    job=request('GET',PATH)
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Live derived image drift')
    r.save(OUT/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(OUT/'source-intent.json'),'before_jobs':{JOB:{'image':IMAGE,'settings_invariant_sha256':settings_invariant(job)}},'prepared_at':datetime.now(timezone.utc).isoformat(),'database_writes':0,'running_service_update':False})
    ack=r.build.az('acr','build','-r',r.build.REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context'))
    if not ack or not ack.get('runId'):raise ValueError('Unknown build acknowledgement; reconcile, do not retry')
    r.save(OUT/'build-ack.json',{'run':ack['runId'],'tag':source['tag']});print(json.dumps({'run':ack['runId'],'live_changes':False}))

if __name__=='__main__':{'prepare':prepare,'qualify':qualify,'build':build,'inspect':r.inspect,'logs':r.logs,'reconcile':r.reconcile}[sys.argv[1]]()
