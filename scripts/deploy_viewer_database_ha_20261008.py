"""Gated v2 compute/HA maintenance; exact recovery, pause/resume and one-shot intents.

Never restores over a production database, changes passwords, or redirects an
application. Unknown outcomes are read back, not redispatched. Recovery remains
an operator-selected action, never an automatic data replacement.
"""
import argparse,copy,hashlib,json
from datetime import datetime,timedelta,timezone
from pathlib import Path
from audit_azure_runtime import az,RG
from deploy_metadata_monitor import BASE,SUB
import run_ms_website_input_probe as p
from qualify_viewer_database_recovery_20261008 import ROOT as RECOVERY,SOURCE,scope,save,read,API
from preflight_viewer_maintenance_20261008 import FOLDER

ROOT=FOLDER/'production-v2-ha'
PAUSE=('ibkrv2stg-autonomous-admin','ibkrv2stg-flex-discovery','ibkrv2stg-metadata-monitor')
SKU={'name':'Standard_D2ds_v5','tier':'GeneralPurpose'}
JOB_API='?api-version=2024-03-01'
ACTIVE={'Running','Processing','Pending'}

def invariant(server):
    keys=('network','storage','version','backup','authConfig','administratorLogin','dataEncryption','maintenanceWindow','fullyQualifiedDomainName')
    return {k:server.get(k) for k in ('id','location')}|{k:server['properties'].get(k) for k in keys}

def job_invariant(job):
    value=copy.deepcopy(job)
    config=value['properties']['configuration']
    for key in ('triggerType','scheduleTriggerConfig','manualTriggerConfig'):config.pop(key,None)
    return p.release.settings_invariant(value),[c['image'] for c in value['properties']['template']['containers']]

def paused_configuration(job):
    config=copy.deepcopy(job['properties']['configuration'])
    if config['triggerType']!='Schedule':raise ValueError('Only registered scheduled workers may be paused')
    if any(s.get('value') for s in config.get('secrets',[])):raise ValueError('Inline secret material is not eligible for archival')
    config['manualTriggerConfig']={k:v for k,v in config.pop('scheduleTriggerConfig').items() if k!='cronExpression'}
    config['triggerType']='Manual'
    return config

def window(plan):
    if datetime.now(timezone.utc)>datetime.fromisoformat(plan['window_end']):raise ValueError('Maintenance authorization window expired; resume workers and stop')

def job_path(name):
    if name not in PAUSE:raise ValueError('Only the three exact high-frequency maintenance workers are in scope')
    return BASE+'Microsoft.App/jobs/'+name

def jobs_idle():
    jobs=az('containerapp','job','list','-g',RG,'--query',"[?starts_with(name,'ibkrv2stg-')].name")
    import concurrent.futures
    def busy(name):
        return name,az('containerapp','job','execution','list','-g',RG,'-n',name,'--query',"[?properties.status=='Running' || properties.status=='Processing' || properties.status=='Pending'].name")
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:active=[name for name,runs in pool.map(busy,jobs) if runs]
    if active:raise ValueError('Wait for existing v2 executions to finish: '+','.join(active))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','pause','pause-verify','resize','enable-ha','state','resume','resume-verify','rollback-compute'));parser.add_argument('--job',choices=PAUSE);args=parser.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Wrong subscription')
    server=p.request('GET',SOURCE+API)
    if args.stage=='prepare':
        if ROOT.exists():raise ValueError('Preserve prior maintenance plan')
        restored=read(RECOVERY/'restored-v3','qualified')
        source=read(RECOVERY/'source-v2','qualified')
        recovery_plan=read(RECOVERY,'plan')
        if restored['source_parity'] is not True or restored['viewer_checks']!=208 or restored['ms_owner_review_downloads']!=42 or not restored['registered_report_bytes_verified']:raise ValueError('Complete private application/report recovery required')
        if scope(server)!=recovery_plan['source_baseline'] or server['properties']['state']!='Ready':raise ValueError('Production baseline drift')
        web=read(p.ROOT/'.work/viewer-report-investigation-20261008/website-release','qualified')
        if web['image']!=restored['image'] or not web['healthy'] or web['traffic_percent']!=100:raise ValueError('Qualified website required')
        live=p.request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+JOB_API)
        if live['properties']['template']['containers'][0]['image']!=web['image'] or live['properties']['latestReadyRevisionName']!=web['revision']:raise ValueError('Website image drift')
        auth=p.request('GET',live['id']+'/authConfigs/current'+JOB_API)
        cap=az('postgres','flexible-server','list-skus','-l','eastasia')[0]
        skus=[s for e in cap['supportedServerEditions'] if e['name']=='GeneralPurpose' for s in e['supportedServerSkus'] if s['name']==SKU['name']]
        if cap.get('zoneRedundantHaSupported')!='Enabled' or len(skus)!=1 or 'ZoneRedundant' not in skus[0]['supportedHaMode']:raise ValueError('Exact region/SKU HA capability unavailable')
        jobs={name:p.request('GET',job_path(name)+JOB_API) for name in PAUSE}
        for job in jobs.values():paused_configuration(job)
        jobs_idle();ROOT.mkdir()
        now=datetime.now(timezone.utc)
        record={'baseline':server,'target_sku':SKU,'target_ha':'ZoneRedundant','same_zone_fallback':False,'paused_jobs':jobs,'website_image':web['image'],'website_auth_sha256':hashlib.sha256(json.dumps(auth['properties'],sort_keys=True).encode()).hexdigest(),'restored_qualification_sha256':p.sha(RECOVERY/'restored-v3/qualified.json'),'source_qualification_sha256':p.sha(RECOVERY/'source-v2/qualified.json'),'regional_capability':skus[0],'prepared_at':now.isoformat(),'window_end':(now+timedelta(hours=2)).isoformat(),'controller_sha256':p.sha(Path(__file__)),'authorization':'Owner requested outstanding availability improvements, approved unlimited budget and selected maintenance now on 2026-10-08','data_restore_or_connection_change':False,'recovery':'Retain hostname and credentials. Inspect async changes. If a Ready server on new compute fails acceptance and HA remains Disabled, exact original compute may be restored; another restart is expected. Never restore data over production. Resume all paused schedules even if qualification fails.'}
        save(ROOT,'plan',record);print('Prepared production v2-only HA maintenance; no live changes');return
    plan=read(ROOT,'plan')
    if plan['controller_sha256']!=p.sha(Path(__file__)):raise ValueError('Frozen controller drift')
    if args.stage!='state' and not args.stage.startswith('resume') and invariant(server)!=invariant(plan['baseline']):raise ValueError('Database non-compute settings drift')
    if args.stage=='state':
        print(json.dumps({'state':server['properties']['state'],'sku':server['sku'],'ha':server['properties']['highAvailability'],'hostname_credential_network_backup_preserved':invariant(server)==invariant(plan['baseline'])}));return
    if args.stage in ('pause','pause-verify','resume','resume-verify'):
        if not args.job:raise ValueError('Select one exact worker')
        name=args.job;original=plan['paused_jobs'][name];job=p.request('GET',job_path(name)+JOB_API)
        if job_invariant(job)!=job_invariant(original):raise ValueError('Worker non-schedule settings/image drift')
        if job['properties']['provisioningState']!='Succeeded':print('Wait for worker configuration readiness');return
        paused=paused_configuration(original);config=job['properties']['configuration']
        operation='resume' if args.stage.startswith('resume') else 'pause'
        if args.stage.endswith('verify'):
            wanted='Schedule' if operation=='resume' else 'Manual'
            if config['triggerType']!=wanted:raise ValueError('Worker trigger differs')
            if wanted=='Schedule' and config['scheduleTriggerConfig']!=original['properties']['configuration']['scheduleTriggerConfig']:raise ValueError('Original worker schedule not recovered')
            if not (ROOT/(name+'-'+operation+'-qualified.json')).exists():save(ROOT,name+'-'+operation+'-qualified',{'trigger':wanted,'non_schedule_settings_preserved':True})
            print(json.dumps({'worker':name,'verified_trigger':wanted}));return
        if (ROOT/(name+'-'+operation+'-intent.json')).exists():raise ValueError('Inspect rather than repeat worker update')
        if operation=='pause':window(plan)
        elif not (ROOT/(name+'-pause-intent.json')).exists():raise ValueError('Worker was not paused by this maintenance')
        if config['triggerType']!=('Manual' if operation=='resume' else 'Schedule'):raise ValueError('Worker trigger drift; inspect')
        body={'properties':{'configuration':copy.deepcopy(original['properties']['configuration']) if operation=='resume' else paused}}
        save(ROOT,name+'-'+operation+'-intent',{'target':job['id'],'body':body,'at':datetime.now(timezone.utc).isoformat()})
        p.request('PATCH',job['id']+JOB_API,data=body);print('Submitted '+operation+' for '+name);return
    window(plan)
    live=p.request('GET',BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+JOB_API)
    auth=p.request('GET',live['id']+'/authConfigs/current'+JOB_API)
    if live['properties']['template']['containers'][0]['image']!=plan['website_image'] or hashlib.sha256(json.dumps(auth['properties'],sort_keys=True).encode()).hexdigest()!=plan['website_auth_sha256']:raise ValueError('Website authentication or image drift')
    for name in PAUSE:
        read(ROOT,name+'-pause-qualified')
        job=p.request('GET',job_path(name)+JOB_API)
        if job['properties']['configuration']['triggerType']!='Manual' or job_invariant(job)!=job_invariant(plan['paused_jobs'][name]):raise ValueError('Exact worker pause no longer holds')
    jobs_idle()
    if server['properties']['state']!='Ready':raise ValueError('Server operation already in progress; inspect')
    if args.stage=='resize':
        if scope(server)!=scope(plan['baseline']):raise ValueError('Production baseline changed')
        body={'sku':SKU}
    elif args.stage=='enable-ha':
        if server['sku']!=SKU or server['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Qualified compute required and HA must still be disabled')
        read(ROOT,'post-resize-application-qualified')
        body={'properties':{'highAvailability':{'mode':'ZoneRedundant'}}}
    else:
        if server['sku']!=SKU or server['properties']['highAvailability']['mode']!='Disabled':raise ValueError('Compute recovery allowed only on Ready new compute without HA')
        if not (ROOT/'resize-intent.json').exists():raise ValueError('No scoped resize to recover')
        body={'sku':plan['baseline']['sku']}
    if (ROOT/(args.stage+'-intent.json')).exists():raise ValueError('Inspect rather than repeat database update')
    save(ROOT,args.stage+'-intent',{'target':SOURCE,'from_sku':server['sku'],'from_ha':server['properties']['highAvailability'],'body':body,'at':datetime.now(timezone.utc).isoformat()})
    p.request('PATCH',SOURCE+API,data=body)
    print('Submitted scoped '+args.stage+'; hostname, credentials and data preserved; read-back required')

if __name__=='__main__':main()
