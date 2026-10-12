"""Image-only cash-summary rollout; preserve auth, every other workload and data."""
import copy,json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
import control_upgrade_cash_summary_runtime_20261010 as scoped
from deploy_upgrade_model_cash_20261010 import body
from deploy_ms_headers_v14r15 import clean,web_hash
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings
from prepare_upgrade_cash_summary_20261010 import BASE

r=scoped.runtime;b=scoped.build;c=r.c
OUT=b.OUT/'deployment-v1';PATH=c.rid('Microsoft.App/containerApps','ibkrv2stg-web');API=r.API
REVISION='cash-summary-20261010-v1'

def gates():
    image,_=r.candidate()
    tested=c.protected(r.OUT/'candidate/result.json')
    if tested.get('passed') is not True or tested.get('image')!=image or tested.get('saved_job_unchanged') is not True:raise ValueError('Candidate runtime proof required')
    r.accept(tested['proof'],'candidate')
    # The fallback is the unchanged exact image qualified and installed in stage 26.
    # Do not mislabel this prior evidence as a newly performed rollback trial.
    fallback=c.protected(BASE/'runtime-v1/installed/result.json')
    prior_files={name:b.h.sha(BASE/'context'/name) for name in ('src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py','ALPHA_T_RUNTIME_RELEASE.json')}
    if fallback.get('passed') is not True or fallback['image']!=b.IMAGE or fallback['proof']['source_files']!=prior_files:raise ValueError('Exact installed-image fallback proof required')
    previous_files=r.files
    try:
        r.files=lambda mode:prior_files
        r.accept(fallback['proof'],'installed')
    finally:r.files=previous_files
    if c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage29/closeout.json').get('passed') is not True:raise ValueError('Isolated reporting checkpoint missing')
    return image,fallback

def sources():
    names=[Path(__file__),Path(scoped.__file__),Path(r.__file__),b.ROOT/'scripts/test_upgrade_cash_summary_guards_20261010.py']
    return {p.name:b.h.sha(p) for p in names}

def unrelated(expected):
    actual=bindings(current_resources())
    if set(actual)!=set(expected) or {k:v for k,v in actual.items() if k!='ibkrv2stg-web'}!={k:v for k,v in expected.items() if k!='ibkrv2stg-web'}:raise ValueError('Another workload changed')

def main(stage):
    if stage not in ('plan','apply','verify'):raise ValueError('Unknown stage')
    image,fallback=gates();live=c.request('GET',PATH+API);auth=c.request('GET',PATH+'/authConfigs/current'+API)
    if stage=='plan':
        b.unchanged()
        guards=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_cash_summary_guards_20261010.py','-q'],cwd=b.ROOT,env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(b.ROOT/'src',b.ROOT/'scripts')))},capture_output=True,text=True,timeout=60)
        if guards.returncode or re.findall(r'Ran (\d+) tests',guards.stderr)!=['7']:raise ValueError('Deployment guards failed')
        if OUT.exists() or live['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Existing deployment or policy drift')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets') or []):raise ValueError('Inline secret capture forbidden')
        expected=copy.deepcopy(c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage28/legacy-dns-v1/plan.json')['boundary']['workloads'])
        if bindings(current_resources())!=expected:raise ValueError('Sealed workload checkpoint drift')
        c.save(OUT,'plan',{'image':image,'before_image':b.IMAGE,'before':live,'auth':auth,'invariant':web_hash(clean(live)),'bindings':expected,
            'apply_body':body(live,image,REVISION),'rollback_body':body(live,b.IMAGE,'cash-summary-rollback-20261010-v1'),
            'controller_sources':sources(),'guard_tests':7,'fallback_proof':fallback['protected_receipt'],
            'fallback_scope':'Previously qualified exact installed image; retains the BASE_SUMMARY display finding',
            'scope':'Physical cash-summary label only; no amounts, formulas, report files, approvals, roles, credentials or network changes',
            'real_viewer_sign_in_verified':False,'live_rollback_interruption_performed':False})
        print('Protected image-only deployment plan; exact previously qualified fallback retained');return
    plan=c.protected(OUT/'plan.json')
    if plan['image']!=image or sources()!=plan['controller_sources'] or web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties']:raise ValueError('Source, website settings or authentication drift')
    unrelated(plan['bindings']);actual=live['properties']['template']['containers'][0]['image']
    if stage=='apply':
        c.runtime.safe_window()
        if actual!=b.IMAGE or (OUT/'apply-intent.json').exists():raise ValueError('Reconcile previous mutation')
        c.save(OUT,'apply-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=c.request('GET',PATH+API);latest_auth=c.request('GET',PATH+'/authConfigs/current'+API)
        if latest['properties']['template']['containers'][0]['image']!=b.IMAGE or web_hash(clean(latest))!=plan['invariant'] or latest_auth['properties']!=plan['auth']['properties']:raise ValueError('Immediate website drift')
        ack=c.request('PATCH',PATH+API,data=plan['apply_body'])
        c.save(OUT,'apply-ack',{'state':ack.get('properties',{}).get('provisioningState')});print('Website cash-summary image submitted');return
    p=live['properties'];revision='ibkrv2stg-web--'+REVISION
    if actual!=image or p['provisioningState']!='Succeeded' or p.get('latestReadyRevisionName')!=revision:print('Website revision not ready');return
    running=c.request('GET',PATH+'/revisions/'+revision+API)['properties'];traffic=p['configuration']['ingress']['traffic']
    if running['healthState']!='Healthy' or not running['active'] or len(traffic)!=1 or traffic[0].get('weight')!=100 or not(traffic[0].get('revisionName')==revision or traffic[0].get('latestRevision') is True):raise ValueError('Revision health/traffic failed')
    origin='https://'+p['configuration']['ingress']['fqdn'];checks=[]
    for path in ('/','/api/portfolio/U16088571/cash','/api/portfolio/alpha-world-t/cash'):
        response=requests.get(origin+path,allow_redirects=False,timeout=20)
        redirect=response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path
        if response.status_code!=401 and not redirect:raise ValueError('Anonymous boundary failed')
        checks.append({'path':path,'status':response.status_code,'denied_or_login_required':True})
    if not (OUT/'verified.json').exists():c.save(OUT,'verified',{'passed':True,'image':image,'revision':revision,'healthy':True,'traffic_percent':100,'authentication_preserved':True,'nonimage_settings_preserved':True,
        'unrelated_workloads_preserved':True,'anonymous_checks':checks,'financial_calculations_changed':False,'reports_and_approvals_changed':False,'real_viewer_sign_in_verified':False,'full_upgrade_complete':False})
    print('Healthy image-only website change verified; all other workload bindings preserved')

if __name__=='__main__':main(*sys.argv[1:])
