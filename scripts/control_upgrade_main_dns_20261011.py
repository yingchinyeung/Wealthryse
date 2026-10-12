"""Reversible main-VNet DNS rehearsal. Never close public access or alter jobs."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
import control_upgrade_legacy_dns_20261010 as old
from plan_upgrade_owner_retirement_20261010 import listing
from deploy_ms_headers_v14r15 import clean,web_hash

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage31/main-dns-v2'
VNET=old.rid('Microsoft.Network/virtualNetworks','ibkrv2stg-vnet')
JOB='ibkrv2stg-inbox-acceptance-r2';JOBPATH=old.rid('Microsoft.App/jobs',JOB)
WEB=old.rid('Microsoft.App/containerApps','ibkrv2stg-web')
API=old.API;APP_API=old.APP_API
RECORDS={
 'privatelink.azurecr.io':{'ibkrv2stgauhecwc4itaoo.eastasia.data':['10.50.10.4'],'ibkrv2stgauhecwc4itaoo':['10.50.10.5'],
                        'ibkrv115rcc62xxqblay.eastasia.data':['10.50.10.10'],'ibkrv115rcc62xxqblay':['10.50.10.11']},
 'privatelink.vaultcore.azure.net':{'ibkrv2stg-auhecwc4itaoo':['10.50.10.8'],'ibkrv2flexauhecwc4ita':['10.50.10.7'],'ibkrv11-5rcc62xxqblay':['10.50.10.12']},
 'privatelink.blob.core.windows.net':{'ibkrv2stgauhecwc4itaoo':['10.50.10.6'],'ibkrv115rcc62xxqblay':['10.50.10.9']}}
SUFFIX={'privatelink.azurecr.io':'.azurecr.io','privatelink.vaultcore.azure.net':'.vault.azure.net','privatelink.blob.core.windows.net':'.blob.core.windows.net'}
EXPECTED={label+SUFFIX[zone]:ips[0] for zone,records in RECORDS.items() for label,ips in records.items()}

def link_id(zone):
    if zone not in RECORDS:raise ValueError('Outside three owned links')
    return old.zone_id(zone)+'/virtualNetworkLinks/upgrade-main-20261011'
def body():return {'location':'global','properties':{'registrationEnabled':False,'virtualNetwork':{'id':VNET}}}
def hashes():return {n:old.sha(ROOT/'scripts'/n) for n in (Path(__file__).name,'probe_upgrade_dns_route_20261010.py','test_upgrade_main_dns_20261011.py')}

def links(present):
    found=[]
    for zone in RECORDS:
        values=listing(old.zone_id(zone)+'/virtualNetworkLinks','2020-06-01')
        if not present:
            if values:raise ValueError('Unexpected pre-existing main DNS link')
        elif len(values)!=1 or values[0]['id'].lower()!=link_id(zone).lower() or not old.contains_requested(values[0]['properties'],body()['properties']):
            raise ValueError('Only exact owned main link permitted')
        elif values[0]['properties'].get('provisioningState')!='Succeeded' or values[0]['properties'].get('virtualNetworkLinkState')!='Completed':
            raise ValueError('DNS link not ready')
        found.extend(values)
    return found

def rollback_links():
    """Permit exact-owned partial deployment recovery without touching other links."""
    found=[]
    for zone in RECORDS:
        values=listing(old.zone_id(zone)+'/virtualNetworkLinks','2020-06-01')
        for value in values:
            if value['id'].lower()!=link_id(zone).lower():continue
            if not old.contains_requested(value['properties'],body()['properties']):raise ValueError('Owned rollback target binding changed')
            found.append(value)
    return found

def boundary():
    network=old.request('GET',VNET+'?api-version=2024-05-01')
    if network['location']!='eastasia' or network['properties'].get('virtualNetworkPeerings') or network['properties'].get('dhcpOptions',{}).get('dnsServers'):
        raise ValueError('Isolated default-DNS VNet required')
    for zone,expected in RECORDS.items():
        records=listing(old.zone_id(zone)+'/A','2020-06-01')
        actual={r['name']:sorted(x['ipv4Address'] for x in r['properties']['aRecords']) for r in records}
        if actual!=expected:raise ValueError('Private record drift')
    resources=old.current_resources();workloads=old.bindings(resources);callers=[];hosts=set()
    for item in resources:
        p=item['properties']
        if not (p.get('environmentId') or p.get('managedEnvironmentId','')).lower().endswith('/ibkrv2stg-env'):continue
        callers.append(item['name']);config=p['configuration']
        hosts.update(urlsplit(s['keyVaultUrl']).hostname for s in config.get('secrets') or [] if s.get('keyVaultUrl'))
        hosts.update(r['server'] for r in config.get('registries') or [])
        for container in p['template']['containers']:
            for env in container.get('env') or []:
                value=env.get('value','')
                if value.startswith('https://'):
                    host=urlsplit(value).hostname
                    if host and host.endswith(tuple(SUFFIX.values())):hosts.add(host)
    if len(callers)!=34 or not hosts<=set(EXPECTED):raise ValueError('Unreviewed main caller or service host')
    services=[]
    targets=[('Microsoft.KeyVault/vaults',n,'2023-07-01') for n in ('ibkrv2stg-auhecwc4itaoo','ibkrv2flexauhecwc4ita','ibkrv11-5rcc62xxqblay')]
    targets += [(k,n,a) for k,a in (('Microsoft.Storage/storageAccounts','2023-05-01'),('Microsoft.ContainerRegistry/registries','2023-07-01')) for n in ('ibkrv2stgauhecwc4itaoo','ibkrv115rcc62xxqblay')]
    for kind,name,api in targets:
        service=old.request('GET',old.rid(kind,name)+'?api-version='+api)
        if service['properties'].get('publicNetworkAccess','Enabled')!='Enabled':raise ValueError('Public rollback path must remain enabled')
        services.append({'name':name,'type':kind,'boundary':old.stable(old.target_boundary(service))})
    web=next(r for r in resources if r['name']=='ibkrv2stg-web')
    auth=old.request('GET',WEB+'/authConfigs/current'+APP_API)
    return {'workloads':workloads,'services':services,'callers':sorted(callers),'referenced_hosts':sorted(hosts),
            'web_invariant':web_hash(clean(web)),'web_auth':auth['properties']}

def health():
    import requests
    web=old.request('GET',WEB+APP_API);p=web['properties']
    rev=old.request('GET',WEB+'/revisions/'+p['latestReadyRevisionName']+APP_API)['properties']
    if p['provisioningState']!='Succeeded' or rev['healthState']!='Healthy' or not rev['active']:raise ValueError('Website unhealthy')
    response=requests.get('https://'+p['configuration']['ingress']['fqdn']+'/',timeout=20,allow_redirects=False)
    if response.status_code!=401 and not(response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path):raise ValueError('Anonymous authentication boundary failed')
    return {'healthy':True,'revision':p['latestReadyRevisionName'],'anonymous_denied':True,'owner_or_viewer_acceptance':False}

def idle():
    old.runtime.safe_window();old.runtime.idle(JOB)

def main(stage):
    if stage=='plan':
        if OUT.exists():raise ValueError('Preserve previous rehearsal')
        resume=old.protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
        if resume['drift'] or resume['web_health']!='Healthy':raise ValueError('Clean resume required')
        current=boundary();links(False);idle()
        if current['workloads']!=resume['bindings']:raise ValueError('Workload drift since resume')
        tests=subprocess.run([sys.executable,'-m','unittest','scripts/test_upgrade_main_dns_20261011.py','-q'],cwd=ROOT,capture_output=True,text=True,timeout=60)
        if tests.returncode:raise ValueError('DNS guard tests failed')
        old.save(OUT,'plan',{'boundary':current,'sources':hashes(),'body':body(),'links':[link_id(z) for z in RECORDS],'before_health':health(),
            'scope':'Three main VNet DNS links; nine exact private service addresses. Public access, credentials, workload definitions and data remain unchanged.',
            'rollback':'Delete only these exact owned links after checking their VNet binding. Retain zones, records and endpoints. Verify public DNS/TLS again.',
            'all_caller_application_acceptance':False,'platform_secret_refresh_verified':False,'cold_registry_pull_verified':False})
        print('Protected main-network DNS rehearsal plan');return
    p=old.protected(OUT/'plan.json')
    if stage=='rollback':
        # Recovery must remain possible even if unrelated health or settings drift.
        if p['links']!=[link_id(z) for z in RECORDS] or p['body']!=body():raise ValueError('Rollback scope mismatch')
        owned=rollback_links()
        if (OUT/'rollback-intent.json').exists():raise ValueError('Reconcile prior rollback')
        old.save(OUT,'rollback-intent',{'links':p['links'],'retained':'Zones, records, endpoints, every workload and public paths'})
        for index,item in enumerate(owned):
            fresh=old.request('GET',item['id']+API)
            if not old.contains_requested(fresh['properties'],body()['properties']):raise ValueError('Rollback target changed')
            old.request('DELETE',item['id']+API);old.save(OUT,'rollback-ack-'+str(index),{'id':item['id'],'delete_requested':True})
        print('Removed only three owned main DNS rehearsal links');return
    if p['sources']!=hashes() or p['boundary']!=boundary():raise ValueError('Source or protected configuration drift')
    if stage=='preview':
        links(False)
        template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':[
            {'type':'Microsoft.Network/privateDnsZones/virtualNetworkLinks','apiVersion':'2020-06-01','name':z+'/upgrade-main-20261011',**body()} for z in RECORDS]}
        file=OUT/'template.json'
        with file.open('x',encoding='utf-8') as f:json.dump(template,f)
        result=subprocess.run([old.AZ,'-m','azure.cli','deployment','group','what-if','-g',old.RG,'--template-file',str(file),'--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if result.returncode:raise ValueError('Preview failed')
        value=json.loads(result.stdout);old.save(OUT,'preview',value)
        if value.get('status')!='Succeeded':raise ValueError('Preview incomplete')
        old.validate_changes(value['changes'],p['links']);old.save(OUT,'preview-qualified',{'passed':True,'plan_sha256':old.sha(OUT/'plan.json')})
        print('Exact three-link create-only preview passed');return
    if stage=='apply':
        qualified=old.protected(OUT/'preview-qualified.json')
        if not qualified['passed'] or qualified['plan_sha256']!=old.sha(OUT/'plan.json'):raise ValueError('Qualified preview required')
        links(False);idle();health()
        if (OUT/'apply-intent.json').exists():raise ValueError('Reconcile earlier apply')
        old.save(OUT,'apply-intent',{'links':p['links'],'body':body()})
        for i,zone in enumerate(RECORDS):
            if old.request('GET',link_id(zone)+API,missing=True) is not None:raise ValueError('Do not overwrite link')
            ack=old.request('PUT',link_id(zone)+API,data=body())
            old.save(OUT,'apply-ack-'+str(i),{'id':ack.get('id',link_id(zone)),'state':ack.get('properties',{}).get('provisioningState')})
        print('Submitted three main DNS links; public access unchanged');return
    if stage in ('verify','verify-rollback'):
        present=stage=='verify';links(present);value={'passed':True,'links_present':present,'health':health(),'workloads_preserved':True,'public_access_changed':False}
        name='linked' if present else 'rollback-verified'
        if not (OUT/(name+'.json')).exists():old.save(OUT,name,value)
        print(json.dumps(value));return
    if stage not in ('start-private','read-private','start-public','read-public'):raise ValueError('Unknown stage')
    mode=stage.split('-',1)[1];links(mode=='private');folder=OUT/(mode+'-probe-v1');job=old.request('GET',JOBPATH+APP_API)
    if stage.startswith('start-'):
        idle()
        if folder.exists():raise ValueError('Reconcile prior probe')
        code='EXPECTED='+repr(EXPECTED)+'\nMODE='+repr(mode)+'\n'+(ROOT/'scripts/probe_upgrade_dns_route_20261010.py').read_text(encoding='utf-8')
        template=old.corrected_template(job,code);template['containers'][0]['env']=[]
        old.save(folder,'intent',{'template':template,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'mode':mode,'credentials_injected':False})
        ack=old.request('POST',JOBPATH+'/start'+APP_API,data=template);old.save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'mode':mode}));return
    intent=old.protected(folder/'intent.json');name=old.protected(folder/'dispatch.json')['execution']
    run=old.request('GET',JOBPATH+'/executions/'+name+APP_API);actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')) or actual.get('env'):raise ValueError('Executed probe differs')
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('DNS probe failed; use exact owned-link rollback')
    prefix='UPGRADE_DNS_ROUTE '
    rows=old.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting DNS evidence');return
    proof=json.loads(rows[0]['Result'])
    if not proof.get('passed') or proof.get('mode')!=mode or len(proof['results'])!=9 or {r['host'] for r in proof['results']}!=set(EXPECTED) or any(r.get('verified_tls') is not True for r in proof['results']):raise ValueError('Incomplete nine-host proof')
    if mode=='private' and any(r['addresses']!=[EXPECTED[r['host']]] or r['peer']!=EXPECTED[r['host']] for r in proof['results']):raise ValueError('Private peer differs')
    if proof.get('dns_overridden') is not False or proof.get('credentials_read') is not False or proof.get('database_writes')!=0 or proof.get('blob_writes')!=0:raise ValueError('Read-only proof differs')
    if not (folder/'result.json').exists():old.save(folder,'result',{'passed':True,'execution':name,'proof':proof,'health':health(),'normal_workflows_run':False})
    print(json.dumps({'passed':True,'mode':mode,'hosts':9}))

if __name__=='__main__':main(*sys.argv[1:])
