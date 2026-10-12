"""Build a one-module assurance validation correction on the scanned app image."""
import json,os,re,shutil,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
import release_platform_access_correction_20261009 as r
from control_upgrade_assurance_candidate_20261009 import candidate,OLD,PATH,API,CURRENT,FILES
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261009-stage11/assurance-period-release-v1';MODULE='src/ibkr_etl/portfolio_assurance.py'
r.WEB=OUT;r.MARKER='PASS: composite assurance reconciles independently aligned validated NAV windows'
def prepare():
    if OUT.exists():raise ValueError('Preserve frozen release')
    image,expected=candidate();runtime=OUT/'runtime';context=OUT/'context'
    shutil.copytree(CURRENT,runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(ROOT/MODULE,runtime/MODULE)
    before=r.manifest(CURRENT);after=r.manifest(runtime)
    if {n for n in before.keys()|after.keys() if before.get(n)!=after.get(n)}!={MODULE}:raise ValueError('Only assurance validator may change')
    (context/MODULE).parent.mkdir(parents=True);shutil.copyfile(runtime/MODULE,context/MODULE)
    (context/'tests').mkdir()
    for name in ('test_assurance_return_windows.py','test_portfolio_assurance.py'):shutil.copyfile(ROOT/'tests'/name,context/'tests'/name)
    metadata=r.read(ROOT/'.work/platform-upgrade-20261009-stage7/app-release-v1/context/ALPHA_T_RUNTIME_RELEASE.json')
    metadata['base_image']=image;metadata['installed_files_sha256'][MODULE]=r.sha(context/MODULE);metadata['scope']='Assurance period reconciliation only; financial calculations, reports, approvals, and AI policy unchanged'
    r.save(context/'ALPHA_T_RUNTIME_RELEASE.json',metadata)
    expected[MODULE]=before[MODULE]
    check='from pathlib import Path;import hashlib;expected='+repr(expected)+';assert all(hashlib.sha256((Path("/app")/n).read_bytes()).hexdigest()==v for n,v in expected.items())'
    run='import os,subprocess,sys;os.environ["PYTHONPATH"]="/app/src:/app/scripts";sys.exit(subprocess.call([sys.executable,"-m","unittest","discover","-s","/app/assurance-period-tests","-v"]))'
    docker='FROM '+image+'\nRUN '+json.dumps(['python','-c',check])+'\nCOPY --chown=10001:10001 '+MODULE+' /app/'+MODULE+'\nCOPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/assurance-period-tests/\nRUN '+json.dumps(['python','-c',run])+'\nRUN '+json.dumps(['python','-c','print('+repr(r.MARKER)+')'])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:assurance-aligned-periods-20261009-v1','base_image':image,'installed_baseline':OLD,'changed_files':[MODULE],'files_sha256':r.manifest(context),'financial_formulas_changed':False,'report_templates_changed':False,'policy_version':'portfolio-analyst-assurance-v2-aligned-periods','cause':'Old assurance runtime read legacy benchmark versions. Current runtime revealed that MS risk starts 20 January but its primary comparison starts 21 January. Validate both against their own exact NAV windows without loosening numerical tolerances.'})
    print('Prepared one-module isolated assurance candidate')
def qualify():
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(OUT/'runtime/src',OUT/'runtime/scripts'))),'PYTHONDONTWRITEBYTECODE':'1','PYTHONUTF8':'1'}
    with (OUT/'local-tests.log').open('x',encoding='utf-8') as f:result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(OUT/'context/tests'),'-v'],env=env,cwd=OUT/'runtime',stdout=f,stderr=subprocess.STDOUT,timeout=300)
    log=(OUT/'local-tests.log').read_text(encoding='utf-8')
    if result.returncode:print(log[-3000:]);raise ValueError('Local assurance qualification failed')
    count=int(re.findall(r'Ran (\d+) tests',log)[-1]);r.save(OUT/'local-qualification.json',{'status':'passed','tests_run':count,'source_intent_sha256':r.sha(OUT/'source-intent.json'),'log_sha256':r.sha(OUT/'local-tests.log')});print(json.dumps({'tests_passed':count}))
def build():
    source=r.read(OUT/'source-intent.json');proof=r.read(OUT/'local-qualification.json')
    if source['files_sha256']!=r.manifest(OUT/'context') or proof['source_intent_sha256']!=r.sha(OUT/'source-intent.json'):raise ValueError('Source drift')
    job=request('GET',PATH+API)
    if job['properties']['template']['containers'][0]['image']!=OLD:raise ValueError('Assurance baseline drift')
    r.save(OUT/'build-intent.json',{'tag':source['tag'],'source_intent_sha256':r.sha(OUT/'source-intent.json'),'before_jobs':{'ibkrv2stg-analyst-assurance':{'image':OLD,'settings_invariant_sha256':settings_invariant(job)}},'prepared_at':datetime.now(timezone.utc).isoformat(),'database_writes':0,'running_service_update':False})
    ack=r.build.az('acr','build','-r',r.build.REGISTRY,'-t',source['tag'],'-f',str(OUT/'context/Dockerfile'),'--no-logs','--no-wait',str(OUT/'context'))
    if not ack or not ack.get('runId'):raise ValueError('Unknown build acknowledgement; reconcile existing run')
    r.save(OUT/'build-ack.json',{'run':ack['runId'],'tag':source['tag']});print(json.dumps({'run':ack['runId']}))
if __name__=='__main__':{'prepare':prepare,'qualify':qualify,'build':build,'inspect':r.inspect,'logs':r.logs,'reconcile':r.reconcile}[sys.argv[1]]()
