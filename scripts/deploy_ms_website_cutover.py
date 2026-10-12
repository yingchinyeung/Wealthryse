"""Owner-authorized two-image cutover; never changes auth, credentials or schedules."""
import argparse
import copy
from pathlib import Path
from urllib.parse import urlsplit

import control_ms_website_exact_cutover as control
from repair_alpha_ms_website_batch import web_invariant

p=control.p
r=control.r
FOLDER=control.FOLDER
REVISION_SUFFIX='ms-bench-hkd-20261008'


def web_settings(job):
    value=r.old.clean(copy.deepcopy(job))
    for c in value['properties']['template']['containers']:
        for e in c.get('env',[]):
            if e.get('secretRef') and e.get('value')=='':e.pop('value')
    return web_invariant(value)


def normalize_template(template):
    result=r.old.clean(copy.deepcopy(template))
    for c in result['containers']:
        for e in c.get('env',[]):
            if e.get('secretRef') and e.get('value')=='':e.pop('value')
    return result


def current_images(require_candidate=False):
    proposal,image=control.gate();base=p.release.read(r.FOLDER,'baseline');p.verify_protected(base)
    if (FOLDER/'images-plan.json').exists():
        plan=p.release.read(FOLDER,'images-plan');p.verify_protected(plan)
        if plan['controller_sha256']!=p.sha(Path(__file__)) or plan['binding_sha256']!=p.sha(FOLDER/'binding.json'):
            raise ValueError('Frozen image controller/plan changed')
    result={}
    for kind,key in (('website','website'),('benchmark','benchmark_job'),('manual','manual_job')):
        original=base[key];job=p.request('GET',original['id']+p.release.API)
        if job['id'].casefold()!=original['id'].casefold() or job['name']!=original['name']:
            raise ValueError('Live resource scope differs')
        invariant=web_settings if kind=='website' else p.release.settings_invariant
        if invariant(job)!=invariant(original):raise ValueError('Live non-image settings drift: '+kind)
        actual=job['properties']['template']['containers'][0]['image']
        baseline=original['properties']['template']['containers'][0]['image']
        allowed={baseline}
        if kind!='manual' and (FOLDER/(kind+'-apply-intent.json')).exists():allowed.add(image)
        if actual not in allowed or (require_candidate and kind!='manual' and actual!=image):
            raise ValueError('Live image differs from approved stage: '+kind)
        result[kind]=job
    auth=p.request('GET',base['website']['id']+'/authConfigs/current'+p.release.API)
    if auth['properties']!=base['website_auth']['properties']:raise ValueError('Website authentication drift')
    return result


def benchmark_idle(job):
    target=job['id']+'/executions';path=target+p.release.API
    for _ in range(20):
        data=p.request('GET',path)
        if any(x['properties']['status'] not in p.release.TERMINAL for x in data.get('value',[])):
            raise ValueError('Benchmark still active or status unknown; do not interrupt it')
        link=data.get('nextLink')
        if not link:return
        url=urlsplit(link)
        if url.scheme!='https' or url.netloc!='management.azure.com' or url.path.casefold()!=target.casefold():
            raise ValueError('Execution pagination scope differs')
        path=url.path+'?'+url.query
    raise ValueError('Execution inventory exceeds bounded pagination')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','apply','verify','restore'))
    parser.add_argument('--kind',choices=('website','benchmark'));args=parser.parse_args()
    proposal,image=control.gate()
    qualification=p.release.read(control.stage_folder('qualify'),'qualified');p.verify_protected(qualification)
    if qualification['status']!='exact_writer_and_rollback_qualified' or qualification['binding_sha256']!=p.sha(FOLDER/'binding.json'):
        raise ValueError('Exact writer and rollback must pass before deploying any image')
    base=p.release.read(r.FOLDER,'baseline');p.verify_protected(base)
    if args.stage=='prepare':
        if (FOLDER/'images-plan.json').exists():raise ValueError('Preserve original image plan')
        r.live_unchanged(base);p.release.window();p.release.idle(p.JOB);benchmark_idle(base['benchmark_job'])
        templates={}
        for kind,key in (('website','website'),('benchmark','benchmark_job')):
            template=normalize_template(base[key]['properties']['template'])
            template['containers'][0]['image']=image
            if kind=='website':template['revisionSuffix']=REVISION_SUFFIX
            templates[kind]=template
        record={'controller_sha256':p.sha(Path(__file__)),'image':image,'templates':templates,
            'partial_recovery_controller_sha256':p.sha(p.ROOT/'scripts/verify_ms_website_partial_recovery.py'),
            'binding_sha256':p.sha(FOLDER/'binding.json'),'baseline_sha256':p.sha(r.FOLDER/'baseline.json'),
            'authorization':proposal['image_changes'],'approved_scoped_owner_request':True,
            'authentication_credentials_schedules_unchanged':True}
        p.release.build.save(FOLDER,'images-plan',{**record,'protected_receipt':p.retain(record,'ms-website-owner-approved-images-plan-v1')})
        print('COORDINATED_IMAGE_PLAN_PREPARED',flush=True);return
    plan=p.release.read(FOLDER,'images-plan');p.verify_protected(plan)
    if plan['controller_sha256']!=p.sha(Path(__file__)) or plan['image']!=image or plan['binding_sha256']!=p.sha(FOLDER/'binding.json'):
        raise ValueError('Frozen image controller/plan differs')
    kind=args.kind
    if kind not in ('website','benchmark'):raise ValueError('Exact image target required')
    current=current_images();job=current[kind]
    original=base['website' if kind=='website' else 'benchmark_job']
    original_image=original['properties']['template']['containers'][0]['image']
    if args.stage=='apply':
        if (FOLDER/(kind+'-apply-intent.json')).exists():raise ValueError('Inspect rather than repeat live image update')
        if job['properties']['template']['containers'][0]['image']!=original_image:raise ValueError('Image baseline changed')
        if kind=='benchmark':
            record=p.release.read(FOLDER,'website-qualified');p.verify_protected(record)
            if record['image']!=image:raise ValueError('Deploy fail-closed website reader before benchmark producer')
        p.release.window();p.release.idle(p.JOB);benchmark_idle(current['benchmark'])
        intent={'target':job['id'],'from_image':original_image,'to_image':image,'template':plan['templates'][kind],
            'image_plan_sha256':p.sha(FOLDER/'images-plan.json')}
        p.release.build.save(FOLDER,kind+'-apply-intent',
            {**intent,'protected_receipt':p.retain(intent,'ms-website-'+kind+'-live-apply-intent-v1')})
        p.request('PATCH',job['id']+p.release.API,data={'properties':{'template':plan['templates'][kind]}})
        print('LIVE_IMAGE_UPDATE_SUBMITTED '+kind,flush=True);return
    if args.stage=='restore':
        if (FOLDER/(kind+'-restore-intent.json')).exists():raise ValueError('Inspect rather than repeat image recovery')
        # Original selection must be restored before a legacy reader can serve it.
        if (control.stage_folder('apply')/'dispatch-intent.json').exists():
            restored=p.release.read(control.stage_folder('rollback'),'qualified');p.verify_protected(restored)
            if restored['status']!='live_rollback_verified':raise ValueError('Restore MS mapping selection before legacy images')
        else:
            restored=p.release.read(FOLDER,'partial-recovery-baseline-qualified');p.verify_protected(restored)
            if restored['status']!='live_original_selection_verified' or restored['binding_sha256']!=p.sha(FOLDER/'binding.json'):
                raise ValueError('Read-only original-selection verification required for partial image recovery')
            if restored['controller_sha256']!=plan['partial_recovery_controller_sha256']:
                raise ValueError('Partial recovery verifier source differs')
        if job['properties']['template']['containers'][0]['image']!=image:raise ValueError('Candidate image not installed')
        p.release.window();p.release.idle(p.JOB);benchmark_idle(current['benchmark'])
        recovered,_=p.restore_protected(base['protected_receipt'])
        template=normalize_template(recovered['website' if kind=='website' else 'benchmark_job']['properties']['template'])
        if kind=='website':template['revisionSuffix']='ms-bench-hkd-rb-20261008'
        intent={'target':job['id'],'from_image':image,'to_image':original_image,'template':template}
        p.release.build.save(FOLDER,kind+'-restore-intent',
            {**intent,'protected_receipt':p.retain(intent,'ms-website-'+kind+'-live-restore-intent-v1')})
        p.request('PATCH',job['id']+p.release.API,data={'properties':{'template':template}})
        print('LIVE_IMAGE_RECOVERY_SUBMITTED '+kind,flush=True);return
    if job['properties']['provisioningState']!='Succeeded':print('WAITING_PROVISION '+kind,flush=True);return
    wanted=image
    recovering=(FOLDER/(kind+'-restore-intent.json')).exists()
    if recovering:wanted=original_image
    if job['properties']['template']['containers'][0]['image']!=wanted:raise ValueError('Installed image differs')
    revision=None
    if kind=='website':
        revision=job['name']+'--'+('ms-bench-hkd-rb-20261008' if recovering else REVISION_SUFFIX)
        if job['properties']['latestReadyRevisionName']!=revision:print('WAITING_READY_REVISION',flush=True);return
        rev=p.request('GET',job['id']+'/revisions/'+revision+p.release.API)['properties']
        if not rev['active'] or rev['healthState']!='Healthy' or rev['runningState'] not in ('Running','RunningAtMaxScale'):
            print('WAITING_WEBSITE_HEALTH',flush=True);return
        traffic=job['properties']['configuration']['ingress']['traffic']
        if len(traffic)!=1 or traffic[0]['weight']!=100 or traffic[0].get('revisionName')!=revision:
            raise ValueError('Website traffic differs from approved single-revision policy')
    record={'status':'passed','image':wanted,'revision':revision,'settings_preserved':True,'authentication_preserved':True,
            'image_plan_sha256':p.sha(FOLDER/'images-plan.json'),'onboarding_activated':False}
    name=kind+('-restore-qualified' if recovering else '-qualified')
    if not (FOLDER/(name+'.json')).exists():
        p.release.build.save(FOLDER,name,{**record,'protected_receipt':p.retain(record,'ms-website-'+name+'-v1')})
    print('LIVE_IMAGE_VERIFIED '+kind,flush=True)


if __name__=='__main__':main()
