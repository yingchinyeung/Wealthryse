"""Website-only image replacement; protected baseline, exact gates and one-shot recovery."""
import argparse,copy,json
from pathlib import Path
import run_ms_website_input_probe as p
from qualify_viewer_native_runtime_20261008 import gate,ROOT,CANDIDATE
from repair_alpha_ms_website_batch import web_invariant

FOLDER=ROOT/'website-release'
SUFFIX='viewer-fixes-20261008'

def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if v is not None}
    if isinstance(value,list):return [clean(v) for v in value]
    return value

def normalized(web):
    value=clean(copy.deepcopy(web))
    for c in value['properties']['template']['containers']:
        for e in c.get('env',[]):
            if e.get('secretRef') and e.get('value')=='':e.pop('value')
    return value

def baseline_and_current(image):
    baseline=p.release.read(CANDIDATE,'baseline');p.verify_protected(baseline)
    original=baseline['website'];current=p.request('GET',original['id']+p.release.API)
    if current['id'].casefold()!=original['id'].casefold() or current['name']!='ibkrv2stg-web' or len(current['properties']['template']['containers'])!=1:raise ValueError('Website scope differs')
    if web_invariant(normalized(current))!=web_invariant(normalized(original)):raise ValueError('Website non-image settings drifted')
    auth=p.request('GET',original['id']+'/authConfigs/current'+p.release.API)
    if auth['properties']!=baseline['auth']['properties']:raise ValueError('Website authentication drifted')
    allowed={baseline['image']}
    if (FOLDER/'apply-intent.json').exists():allowed.add(image)
    if current['properties']['template']['containers'][0]['image'] not in allowed:raise ValueError('Installed website image not in controlled release')
    return baseline,current

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','apply','verify','restore'));stage=parser.parse_args().stage
    image=gate()
    for kind in ('candidate','rollback'):
        proof=p.release.read(ROOT/('native-'+kind+'-v2'),'qualified');p.verify_protected(proof)
        if proof['status']!='passed' or proof['database_writes']!=0 or not proof['approved_report_assets_unchanged']:raise ValueError('Exact runtime and rollback assets must pass')
        if kind=='candidate' and proof['image']!=image:raise ValueError('Qualified candidate image differs')
    baseline,current=baseline_and_current(image)
    rollback=p.release.read(ROOT/'native-rollback-v2','qualified')
    if rollback['image']!=baseline['image']:raise ValueError('Qualified rollback differs from protected baseline')
    if stage=='prepare':
        if FOLDER.exists():raise ValueError('Preserve prior release plan')
        if current['properties']['template']['containers'][0]['image']!=baseline['image']:raise ValueError('Release baseline changed')
        if current['properties']['configuration'].get('activeRevisionsMode')!='Single':raise ValueError('Unqualified traffic mode')
        template=normalized(current)['properties']['template'];template['containers'][0]['image']=image;template['revisionSuffix']=SUFFIX
        FOLDER.mkdir()
        record={'image':image,'baseline_sha256':p.sha(CANDIDATE/'baseline.json'),'template':template,'controller_sha256':p.sha(Path(__file__)),'authorization':'Owner requested investigate viewer report and fix identified issues; scoped website image only','authentication_credentials_identity_scale_preserved':True,'factsheets_changed':False,'source_data_changed':False}
        p.release.build.save(FOLDER,'plan',{**record,'protected_receipt':p.retain(record,'viewer-website-release-plan')});print('Prepared website-only release and protected recovery plan');return
    plan=p.release.read(FOLDER,'plan');p.verify_protected(plan)
    if plan['controller_sha256']!=p.sha(Path(__file__)) or plan['image']!=image or plan['baseline_sha256']!=p.sha(CANDIDATE/'baseline.json'):raise ValueError('Frozen release plan differs')
    if stage in ('apply','restore'):
        name=stage+'-intent'
        if (FOLDER/(name+'.json')).exists():raise ValueError('Inspect, never repeat live update')
        actual=current['properties']['template']['containers'][0]['image']
        if stage=='apply':
            if actual!=baseline['image']:raise ValueError('Current image differs from baseline')
            template=plan['template']
        else:
            if actual!=image or not (FOLDER/'apply-intent.json').exists():raise ValueError('No candidate deployment to restore')
            template=normalized(baseline['website'])['properties']['template'];template['revisionSuffix']=SUFFIX+'-rb'
        record={'target':current['id'],'from_image':actual,'to_image':template['containers'][0]['image'],'template':template,'plan_sha256':p.sha(FOLDER/'plan.json')}
        p.release.build.save(FOLDER,name,{**record,'protected_receipt':p.retain(record,'viewer-website-'+name)})
        p.request('PATCH',current['id']+p.release.API,data={'properties':{'template':template}})
        print('Submitted website-only '+stage+'; no authentication or report changes');return
    restoring=(FOLDER/'restore-intent.json').exists();wanted=baseline['image'] if restoring else image
    revision='ibkrv2stg-web--'+SUFFIX+('-rb' if restoring else '')
    if current['properties']['template']['containers'][0]['image']!=wanted:raise ValueError('Installed image differs')
    if current['properties']['provisioningState']!='Succeeded' or current['properties']['latestReadyRevisionName']!=revision:print('Waiting for website revision readiness');return
    properties=p.request('GET',current['id']+'/revisions/'+revision+p.release.API)['properties']
    if not properties['active'] or properties['healthState']!='Healthy' or properties['runningState'] not in ('Running','RunningAtMaxScale'):print('Waiting for website health');return
    traffic=current['properties']['configuration']['ingress']['traffic']
    if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=revision:raise ValueError('Website traffic differs')
    record={'status':'passed','image':wanted,'revision':revision,'healthy':True,'traffic_percent':100,'authentication_credentials_identity_scale_preserved':True,'source_data_changed':False,'factsheets_changed':False,'plan_sha256':p.sha(FOLDER/'plan.json')}
    name='restore-qualified' if restoring else 'qualified'
    if not (FOLDER/(name+'.json')).exists():p.release.build.save(FOLDER,name,{**record,'protected_receipt':p.retain(record,'viewer-website-'+name)})
    print(json.dumps(record))

if __name__=='__main__':main()
