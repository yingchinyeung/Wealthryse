"""Scoped control: preserve legacy batches, qualify exact existing Azure runtime."""
import base64
import copy
import json
from pathlib import Path
import sys

import deploy_alpha_ms_copy as runtime

p=runtime.previous
ROOT=p.ROOT
FOLDER=ROOT/'.work/alpha-ms-website-batch-20261007'
PREFIX='review/alpha-world-ms/website-batch-20261007/'
PREFLIGHT='preflight-r2'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f94f3a57b8170aeb294ec7dc69215738ab6c96a834d8019db78f1ebfaaaff827'
WEB=p.BASE+'Microsoft.App/containerApps/ibkrv2stg-web'


def save(name,record):
    FOLDER.mkdir(exist_ok=True)
    target=FOLDER/(name+'.json')
    if target.exists():raise ValueError('Frozen intent exists: reconcile original operation')
    receipt=p.retain(record,'ms-website-batch-'+name+'-20261007')
    with target.open('x',encoding='utf-8') as handle:json.dump({'record':record,'protected_receipt':receipt},handle,indent=2)


def read(name):return json.loads((FOLDER/(name+'.json')).read_bytes())['record']


def preflight():
    account=p.build.az('account','show')
    if account['id']!='3956e821-cee4-403f-8708-e84e2ed490eb' or account['user']['name'].lower()!='anthony.ying@wealthryse.ai':
        raise ValueError('Expected authorized owner subscription required')
    job=p.get_job(p.JOBS[0]);web=p.request('GET',WEB+'?api-version=2024-03-01')
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Reporting image drift')
    runs=p.request('GET',p.job_path(p.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    source=(ROOT/'scripts/probe_alpha_ms_registered_batch.py').read_bytes()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['command']=['python'];c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'ms-batch-read-only-preflight','exec'))"]
    c['env']=[e for e in c['env'] if e['name']!='MS_BATCH_PREFLIGHT_BLOB']+[{'name':'MS_BATCH_PREFLIGHT_BLOB','value':PREFIX+PREFLIGHT+'.json'}]
    save(PREFLIGHT+'-intent',{'template':template,'source_sha256':p.sha(source),'before_job':job,
        'before_website':web,'database_writes':0,'source_data_changed':False,
        'authorization':'Owner requested private MS historical batch regeneration and explicitly authorized Azure account use; no release or activation'})
    result=p.request('POST',p.job_path(p.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    save(PREFLIGHT+'-dispatch',{'execution':result['name']})
    print(json.dumps({'execution':result['name']}))


def verify():
    name=read(PREFLIGHT+'-dispatch')['execution']
    execution=p.request('GET',p.job_path(p.JOBS[0])+'/executions/'+name+'?api-version=2024-03-01')
    status=execution['properties']['status']
    if status!='Succeeded':print(json.dumps({'status':status,'execution':name}));return
    if execution['properties']['template']!=read(PREFLIGHT+'-intent')['template']:
        # Azure may normalize null/default fields, but exact reviewed container
        # command, args, image and env must be identical.
        actual=execution['properties']['template']['containers'][0]
        planned=read(PREFLIGHT+'-intent')['template']['containers'][0]
        if any(actual.get(k)!=planned.get(k) for k in ('image','command','args')):raise ValueError('Executed payload differs')
        a={item['name']:item for item in actual['env']};b={item['name']:item for item in planned['env']}
        if set(a)!=set(b):raise ValueError('Executed environment names differ')
        for key in a:
            if b[key].get('secretRef'):
                if a[key].get('secretRef') not in (b[key]['secretRef'],'cappjob-'+p.JOBS[0]):
                    raise ValueError('Unexpected synthesized Azure execution secret reference')
            elif a[key].get('value')!=b[key].get('value'):raise ValueError('Executed environment value differs')
    if p.invariant(p.get_job(p.JOBS[0]))!=p.invariant(read(PREFLIGHT+'-intent')['before_job']):
        raise ValueError('Persistent job credential/configuration changed')
    content=p.storage().get_blob_client('internal-reports',PREFIX+PREFLIGHT+'.json').download_blob().readall()
    proof=json.loads(content)
    if proof['status']!='passed' or proof['database_writes']!=0 or len(proof['months'])!=21:raise ValueError('Invalid preflight proof')
    save(PREFLIGHT+'-qualified',{'execution':name,'proof':proof,'sha256':p.sha(content)})
    print(json.dumps({'status':'passed','months':21,'deck_checks':[r['presentation_preflight']['status'] for r in proof['months']],'batch_count':len(proof['batches'])}))


def logs():
    name=read(PREFLIGHT+'-dispatch')['execution']
    query=("ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+
        "' | project Log_s | take 30")
    print(json.dumps(p.build.az('monitor','log-analytics','query','-w',
        '5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)))


def web_invariant(web):
    template=copy.deepcopy(web['properties']['template']);template.pop('revisionSuffix',None)
    for container in template['containers']:container.pop('image',None)
    configuration=copy.deepcopy(web['properties']['configuration'])
    for rule in configuration['ingress'].get('traffic',[]):rule.pop('revisionName',None)
    return p.sha(json.dumps({'template':template,'identity':web.get('identity'),
        'configuration':configuration,'environmentId':web['properties']['environmentId']},sort_keys=True).encode())


def align_web():
    p.require_candidate()
    proof=read(PREFLIGHT+'-qualified')['proof']
    if len(proof['months'])!=21 or proof['subject']['lifecycle_status']!='onboarding':
        raise ValueError('Qualified private HKD data required')
    before=p.request('GET',WEB+'?api-version=2024-03-01')
    baseline=read(PREFLIGHT+'-intent')['before_website']
    if before['properties']['template']['containers'][0]['image']!=runtime.BASE_IMAGE or web_invariant(before)!=web_invariant(baseline):
        raise ValueError('Website baseline drift')
    source=Path(runtime.BASE_FOLDER/'context/src/ibkr_etl/web.py').read_bytes()
    newer=Path(runtime.FOLDER/'context/src/ibkr_etl/web.py').read_bytes()
    if source!=newer:raise ValueError('Website code itself changed in the existing copy image')
    save('website-align-intent',{'before':before,'image':IMAGE,'scope':'align website validation with installed v14r12 reporting layout',
        'website_code_unchanged':True,'financial_calculation_code_unchanged':True,'source_data_changed':False,
        'reports_released':False,'onboarding_activated':False})
    p.build.az('containerapp','update','-g','rg-ibkr-database-v11','-n','ibkrv2stg-web',
        '--image',IMAGE,'--revision-suffix','ms-copy-v14r12')
    verify_web()


def verify_web():
    intent=read('website-align-intent');web=p.request('GET',WEB+'?api-version=2024-03-01')
    if web_invariant(web)!=web_invariant(intent['before']) or web['properties']['template']['containers'][0]['image']!=IMAGE:
        raise ValueError('Website credentials, settings, traffic policy or image differ')
    revision=web['properties']['latestReadyRevisionName']
    if revision!='ibkrv2stg-web--ms-copy-v14r12' or web['properties']['provisioningState']!='Succeeded':
        print(json.dumps({'status':'waiting','ready_revision':revision}));return
    active=p.request('GET',WEB+'/revisions/'+revision+'?api-version=2024-03-01')['properties']
    traffic=web['properties']['configuration']['ingress']['traffic']
    if active['healthState']!='Healthy' or not active['active'] or active['runningState'] not in ('Running','RunningAtMaxScale'):
        print(json.dumps({'status':'waiting','health':active['healthState']}));return
    if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=revision:
        raise ValueError('Expected preserved single-revision traffic policy')
    auth=p.request('GET',WEB+'/authConfigs/current?api-version=2024-03-01')
    old_auth=read('website-auth-baseline')
    if auth['properties']!=old_auth['properties']:raise ValueError('Authentication configuration changed')
    record={'status':'passed','image':IMAGE,'revision':revision,'healthy':True,'settings_preserved':True,
        'authentication_preserved':True,'source_data_changed':False,'reports_released':False,'onboarding_activated':False}
    if not (FOLDER/'website-aligned.json').exists():save('website-aligned',record)
    print(json.dumps(record))


if __name__=='__main__':
    stage=sys.argv[1]
    if stage=='preflight':preflight()
    elif stage=='verify':verify()
    elif stage=='logs':logs()
    elif stage=='align-web':
        if not (FOLDER/'website-auth-baseline.json').exists():
            save('website-auth-baseline',p.request('GET',WEB+'/authConfigs/current?api-version=2024-03-01'))
        align_web()
    elif stage=='verify-web':verify_web()
    else:raise ValueError('Unknown stage')
