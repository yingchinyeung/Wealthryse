"""Explicitly owner-authorized website image alignment, after isolated checks."""
import base64
import copy
import json
import sys

import repair_alpha_ms_website_batch as earlier
import deploy_alpha_ms_distribution_ticks as release

p=release.previous
ROOT=release.ROOT
FOLDER=ROOT/'.work/alpha-ms-web-v14r13-20261007'
PREFIX='review/alpha-world-ms/web-v14r13-20261007/'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f7aaa6935794c7a2ce1ef94ed177fa3f987fd31df8df7d6b56f644242922a590'
WEB=p.BASE+'Microsoft.App/containerApps/ibkrv2stg-web'
REVISION='ibkrv2stg-web--ms-layout-v14r13'
web_invariant=earlier.web_invariant


def save(name,record):
    FOLDER.mkdir(exist_ok=True)
    target=FOLDER/(name+'.json')
    if target.exists():raise ValueError('Original operation exists; reconcile it')
    receipt=p.retain(record,'ms-web-v14r13-'+name+'-20261007')
    with target.open('x',encoding='utf-8') as handle:
        json.dump({'record':record,'protected_receipt':receipt},handle,indent=2)


def read(name):return json.loads((FOLDER/(name+'.json')).read_bytes())['record']


def baseline():
    p.require_candidate()
    done=p.read('completion')
    if done['image']!=IMAGE or done['historical_factsheets']!=21:raise ValueError('Qualified current image required')
    account=p.build.az('account','show')
    if account['id']!='3956e821-cee4-403f-8708-e84e2ed490eb' or account['user']['name'].lower()!='anthony.ying@wealthryse.ai':
        raise ValueError('Expected owner subscription required')
    web=p.request('GET',WEB+'?api-version=2024-03-01')
    if web['properties']['template']['containers'][0]['image']!=release.DATABASE_IMAGE:
        raise ValueError('Website baseline image drift')
    auth=p.request('GET',WEB+'/authConfigs/current?api-version=2024-03-01')
    source=ROOT/'.work/alpha-ms-hkd-cutover-v14r11-20261007-r3/context/src/ibkr_etl/web.py'
    candidate=release.FOLDER/'context/src/ibkr_etl/web.py'
    if source.read_bytes()!=candidate.read_bytes():raise ValueError('Unreviewed website code delta')
    save('baseline',{'website':web,'auth':auth,'image':IMAGE,'web_source_sha256':p.sha(candidate.read_bytes()),
        'website_code_unchanged':True,'web_invariant':web_invariant(web),
        'authorization':'Owner explicitly agreed to continue the proposed v14r13-only website update after staging qualification; preserve auth/credentials/settings; no release or activation'})
    print(json.dumps({'baseline_saved':True,'website_code_unchanged':True,'website_image_updated':False}))


def stage():
    before=read('baseline')
    job=p.get_job(p.JOBS[0])
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Reporting image drift')
    runs=p.request('GET',p.job_path(p.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    source=(ROOT/'scripts/probe_alpha_ms_web_v14r13.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['command']=['python']
    c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'isolated-ms-web-qualification','exec'))"]
    names={'AUTHORIZATION_MODE','ALLOWED_EMAILS','ALLOWED_ENTRA_OBJECT_IDS',
        'ONBOARDING_OPERATOR_EMAILS','ONBOARDING_OPERATOR_ENTRA_OBJECT_IDS',
        'VIEWER_EMAILS','VIEWER_ENTRA_OBJECT_IDS','PORTFOLIO_TENANT_ID','RELEASE_CHANNEL'}
    env={e['name']:e for e in c['env']}
    for e in before['website']['properties']['template']['containers'][0]['env']:
        if e['name'] in names:
            if e.get('secretRef'):raise ValueError('Auth policy secret cannot be copied into this stage')
            env[e['name']]=e
    env['MS_WEB_IMAGE']={'name':'MS_WEB_IMAGE','value':IMAGE}
    env['MS_WEB_STAGE_BLOB']={'name':'MS_WEB_STAGE_BLOB','value':PREFIX+'stage-r1.json'}
    c['env']=list(env.values())
    save('stage-r1-intent',{'template':template,'source_sha256':p.sha(source),
        'before_job_invariant':p.invariant(job),'database_writes':0,'website_changed':False,
        'credential_source':'Existing reporting execution only; no new secrets or grants'})
    result=p.request('POST',p.job_path(p.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    save('stage-r1-dispatch',{'execution':result['name']})
    print(json.dumps({'execution':result['name'],'website_changed':False}))


def verify_stage():
    name=read('stage-r1-dispatch')['execution']
    run=p.request('GET',p.job_path(p.JOBS[0])+'/executions/'+name+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':
        print(json.dumps({'status':run['properties']['status'],'execution':name}));return
    actual=run['properties']['template']['containers'][0];wanted=read('stage-r1-intent')['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed stage identity differs')
    if p.invariant(p.get_job(p.JOBS[0]))!=read('stage-r1-intent')['before_job_invariant']:raise ValueError('Reporting settings changed')
    raw=p.storage().get_blob_client('internal-reports',PREFIX+'stage-r1.json').download_blob().readall()
    proof=json.loads(raw)
    if proof['status']!='passed' or proof['image']!=IMAGE or proof['layout_version']!=release.LAYOUT or proof['database_writes']!=0:
        raise ValueError('Stage proof failed')
    if proof['web_source_sha256']!=read('baseline')['web_source_sha256']:raise ValueError('Website source differs')
    save('stage-qualified',{'proof':proof,'execution':name,'sha256':p.sha(raw)})
    print(json.dumps({'status':'passed','checks':len(proof['checks']),'website_updated':False}))


def align():
    p.require_candidate()
    if read('stage-qualified')['proof']['status']!='passed':raise ValueError('Isolated website qualification required')
    before=p.request('GET',WEB+'?api-version=2024-03-01')
    expected=read('baseline')
    if web_invariant(before)!=expected['web_invariant'] or before['properties']['template']['containers'][0]['image']!=release.DATABASE_IMAGE:
        raise ValueError('Website drift; do not overwrite')
    auth=p.request('GET',WEB+'/authConfigs/current?api-version=2024-03-01')
    if auth['properties']!=expected['auth']['properties']:raise ValueError('Authentication drift')
    save('alignment-intent',{'before':before,'image':IMAGE,'revision':REVISION,
        'scope':'Only ibkrv2stg-web image alignment to current approved MS renderer',
        'authentication_preserved':True,'source_data_changed':False,'reports_released':False,'onboarding_activated':False})
    p.build.az('containerapp','update','-g','rg-ibkr-database-v11','-n','ibkrv2stg-web',
        '--image',IMAGE,'--revision-suffix','ms-layout-v14r13')
    verify_web()


def verify_web():
    read('alignment-intent')
    web=p.request('GET',WEB+'?api-version=2024-03-01')
    if web_invariant(web)!=read('baseline')['web_invariant'] or web['properties']['template']['containers'][0]['image']!=IMAGE:
        raise ValueError('Website image or non-image settings differ')
    if web['properties']['latestReadyRevisionName']!=REVISION or web['properties']['provisioningState']!='Succeeded':
        print(json.dumps({'status':'waiting','ready_revision':web['properties']['latestReadyRevisionName']}));return
    revision=p.request('GET',WEB+'/revisions/'+REVISION+'?api-version=2024-03-01')['properties']
    if revision['healthState']!='Healthy' or not revision['active'] or revision['runningState'] not in ('Running','RunningAtMaxScale'):
        print(json.dumps({'status':'waiting','health':revision['healthState']}));return
    traffic=web['properties']['configuration']['ingress']['traffic']
    if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=REVISION:raise ValueError('Traffic policy differs')
    auth=p.request('GET',WEB+'/authConfigs/current?api-version=2024-03-01')
    if auth['properties']!=read('baseline')['auth']['properties']:raise ValueError('Authentication changed')
    proof={'status':'passed','image':IMAGE,'revision':REVISION,'healthy':True,'traffic_percent':100,
        'settings_preserved':True,'authentication_preserved':True,'ibkr_source_changed':False,
        'reports_released':False,'onboarding_activated':False,'historical_batch_replaced':False}
    if not (FOLDER/'website-aligned.json').exists():save('website-aligned',proof)
    print(json.dumps(proof))


def logs():
    name=read('stage-r1-dispatch')['execution']
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"' | where Log_s startswith 'MS_WEB_STAGE' | project Log_s"
    print(json.dumps(p.build.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)))


def diagnose_web():
    """Read-only reconciliation after a command, never repeats the update."""
    web=p.request('GET',WEB+'?api-version=2024-03-01')
    before=read('baseline')['website']
    def governed(value):
        template=copy.deepcopy(value['properties']['template']);template.pop('revisionSuffix',None)
        for container in template['containers']:container.pop('image',None)
        config=copy.deepcopy(value['properties']['configuration'])
        for rule in config['ingress'].get('traffic',[]):rule.pop('revisionName',None)
        return {'template':template,'identity':value.get('identity'),'configuration':config,
            'environmentId':value['properties']['environmentId']}
    differences=[]
    def compare(a,b,path=''):
        if isinstance(a,dict) and isinstance(b,dict):
            for key in sorted(set(a)|set(b)):
                compare(a.get(key),b.get(key),path+'/'+key)
        elif isinstance(a,list) and isinstance(b,list) and len(a)==len(b):
            for i,(x,y) in enumerate(zip(a,b)):compare(x,y,path+'/'+str(i))
        elif a!=b:
            differences.append({'path':path,'before_sha256':p.sha(json.dumps(a,sort_keys=True).encode()),
                'after_sha256':p.sha(json.dumps(b,sort_keys=True).encode()),
                'before_type':type(a).__name__,'after_type':type(b).__name__})
    compare(governed(before),governed(web))
    save('post-update-diagnostic',{'website':web,'differences':differences})
    print(json.dumps({'image_matches':web['properties']['template']['containers'][0]['image']==IMAGE,
        'provisioning_state':web['properties']['provisioningState'],
        'latest_ready_revision':web['properties']['latestReadyRevisionName'],'differences':differences}))


if __name__=='__main__':
    stage_name=sys.argv[1]
    {'baseline':baseline,'stage':stage,'verify-stage':verify_stage,'align':align,'verify-web':verify_web,
        'diagnose-web':diagnose_web,'logs':logs}[stage_name]()
