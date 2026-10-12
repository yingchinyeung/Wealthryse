"""Exact website-only cash display rollout, authentication and jobs unchanged."""
import copy,json,os,re,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests
import control_upgrade_model_cash_runtime_20261010 as r
from deploy_ms_headers_v14r15 import clean,web_hash
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings

b=r.b;c=r.c;OUT=b.OUT/'deployment-v1';PATH=c.rid('Microsoft.App/containerApps','ibkrv2stg-web');API=r.API
REVISION='model-cash-20261010-v1'


def body(live,image,suffix):
    template=clean(copy.deepcopy(live['properties']['template']))
    if len(template['containers'])!=1:raise ValueError('Single website container required')
    for e in template['containers'][0].get('env',[]):
        if e.get('secretRef') and e.get('value')=='':e.pop('value')
    template['containers'][0]['image']=image;template['revisionSuffix']=suffix
    projected=copy.deepcopy(live);projected['properties']['template']=template
    if web_hash(clean(projected))!=web_hash(clean(live)):raise ValueError('Image-only website boundary failed')
    return {'properties':{'template':template}}


def gates():
    image,_=r.candidate()
    for mode,wanted in (('candidate',image),('fallback',b.IMAGE)):
        value=c.protected(r.OUT/mode/'result.json')
        if value.get('passed') is not True or value.get('image')!=wanted or value.get('saved_job_unchanged') is not True:raise ValueError('Exact runtime acceptance required')
        r.accept(value['proof'],mode)
    prior=c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage25/closeout.json')
    if prior.get('remaining_image_families')!=0:raise ValueError('Legacy runtime checkpoint incomplete')
    return image


def unrelated(expected):
    actual=bindings(current_resources())
    if set(actual)!=set(expected):raise ValueError('Workload inventory drift')
    if {k:v for k,v in actual.items() if k!='ibkrv2stg-web'}!={k:v for k,v in expected.items() if k!='ibkrv2stg-web'}:raise ValueError('Another workload changed')


def main(stage):
    if stage not in ('plan','apply','verify'):raise ValueError('Unknown stage')
    image=gates();live=c.request('GET',PATH+API);auth=c.request('GET',PATH+'/authConfigs/current'+API)
    if stage=='plan':
        b.unchanged()
        guards=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_model_cash_guards_20261010.py','-q'],cwd=b.ROOT,env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(b.ROOT/'src',b.ROOT/'scripts')))},capture_output=True,text=True,encoding='utf-8',timeout=60)
        if guards.returncode or re.findall(r'Ran (\d+) tests',guards.stderr)!=['18']:raise ValueError('Website deployment guards failed')
        if OUT.exists() or live['properties']['configuration']['activeRevisionsMode']!='Single':raise ValueError('Existing deployment or revision-policy drift')
        if any(s.get('value') for s in live['properties']['configuration'].get('secrets') or []):raise ValueError('Inline secret capture forbidden')
        prior=c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage25/closeout.json');baseline=c.protected(b.ROOT/'.work/platform-upgrade-20261010-stage25/baseline.json');expected=copy.deepcopy(baseline['bindings'])
        for item in prior['upgrades']:expected[item['job']]['images']=[item['image']]
        if bindings(current_resources())!=expected:raise ValueError('Sealed workload checkpoint drift')
        c.save(OUT,'plan',{'image':image,'before_image':b.IMAGE,'before':live,'auth':auth,'invariant':web_hash(clean(live)),'bindings':expected,
            'apply_body':body(live,image,REVISION),'rollback_body':body(live,b.IMAGE,'model-cash-rollback-20261010-v1'),
            'controller_sha256':b.h.sha(Path(__file__)),'runtime_controller_sha256':b.h.sha(Path(r.__file__)),
            'guard_tests':18,'guard_test_sha256':b.h.sha(b.ROOT/'scripts/test_upgrade_model_cash_guards_20261010.py'),
            'scope':'Read-only MS/T model-cash website display only; no cash balances, NAV, reports, approvals, credentials, roles, schedules or network settings change',
            'real_viewer_sign_in_verified':False,'live_rollback_interruption_performed':False});print('Protected website-only deployment and qualified fallback plan');return
    plan=c.protected(OUT/'plan.json')
    if plan['image']!=image or plan['controller_sha256']!=b.h.sha(Path(__file__)) or plan['runtime_controller_sha256']!=b.h.sha(Path(r.__file__)) or plan['guard_test_sha256']!=b.h.sha(b.ROOT/'scripts/test_upgrade_model_cash_guards_20261010.py') or web_hash(clean(live))!=plan['invariant'] or auth['properties']!=plan['auth']['properties']:raise ValueError('Website source, settings or authentication drift')
    unrelated(plan['bindings']);actual=live['properties']['template']['containers'][0]['image']
    if stage=='apply':
        c.runtime.safe_window()
        if actual!=b.IMAGE or (OUT/'apply-intent.json').exists():raise ValueError('Reconcile previous mutation')
        c.save(OUT,'apply-intent',{'started_at':datetime.now(timezone.utc).isoformat(),'plan_receipt':plan['protected_receipt']})
        latest=c.request('GET',PATH+API);latest_auth=c.request('GET',PATH+'/authConfigs/current'+API)
        if latest['properties']['template']['containers'][0]['image']!=b.IMAGE or web_hash(clean(latest))!=plan['invariant'] or latest_auth['properties']!=plan['auth']['properties']:raise ValueError('Immediate website drift')
        ack=c.request('PATCH',PATH+API,data=plan['apply_body']);c.save(OUT,'apply-ack',{'state':ack.get('properties',{}).get('provisioningState')});print('Website-only model-cash update submitted');return
    p=live['properties'];revision='ibkrv2stg-web--'+REVISION
    if actual!=image or p['provisioningState']!='Succeeded' or p.get('latestReadyRevisionName')!=revision:print('Website revision not ready');return
    running=c.request('GET',PATH+'/revisions/'+revision+API)['properties'];traffic=p['configuration']['ingress']['traffic']
    if running['healthState']!='Healthy' or not running['active'] or len(traffic)!=1 or traffic[0].get('weight')!=100 or not(traffic[0].get('revisionName')==revision or traffic[0].get('latestRevision') is True):raise ValueError('New revision health/traffic failed')
    origin='https://'+p['configuration']['ingress']['fqdn'];checks=[]
    for path in ('/','/api/portfolio/alpha-world-ms/cash','/api/portfolio/alpha-world-t/cash'):
        response=requests.get(origin+path,allow_redirects=False,timeout=20)
        redirect=response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path
        if response.status_code!=401 and not redirect:raise ValueError('Anonymous access boundary failed')
        checks.append({'path':path,'status':response.status_code,'denied_or_login_required':True})
    if not (OUT/'verified.json').exists():c.save(OUT,'verified',{'passed':True,'image':image,'revision':revision,'healthy':True,'traffic_percent':100,'authentication_preserved':True,'nonimage_settings_preserved':True,
        'unrelated_workloads_preserved':True,'anonymous_checks':checks,'financial_calculations_changed':False,'reports_and_approvals_changed':False,'real_viewer_sign_in_verified':False,'full_upgrade_complete':False})
    print('Healthy website revision verified; authentication and unrelated workloads preserved')


if __name__=='__main__':main(*sys.argv[1:])
