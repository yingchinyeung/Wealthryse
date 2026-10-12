"""Legacy-only DNS rehearsal and exact-owned-link rollback; public paths stay on."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from urllib.parse import urlsplit
from deploy_viewer_private_admin_20261008 import request,rid
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings
from preview_upgrade_private_endpoints_20261009 import validate_changes
from deploy_upgrade_private_endpoints_20261009 import contains_requested,stable,target_boundary
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import AZ,RG,az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261010-stage28/legacy-dns-v1'
API='?api-version=2020-06-01';APP_API='?api-version=2026-07-01'
VNET=rid('Microsoft.Network/virtualNetworks','ibkrv11-vnet')
JOBS=('ibkr-v11-bootstrap','ibkr-v11-daily','ibkr-v11-history-backfill')
JOB=JOBS[1];JOBPATH=rid('Microsoft.App/jobs',JOB)
ZONES={
 'ibkrv11-5rcc62xxqblay.privatelink.vaultcore.azure.net':('ibkrv11-5rcc62xxqblay.vault.azure.net','10.40.10.6'),
 'ibkrv115rcc62xxqblay.privatelink.blob.core.windows.net':('ibkrv115rcc62xxqblay.blob.core.windows.net','10.40.10.7'),
 'ibkrv115rcc62xxqblay.privatelink.azurecr.io':('ibkrv115rcc62xxqblay.azurecr.io','10.40.10.5'),
 'ibkrv115rcc62xxqblay.eastasia.data.privatelink.azurecr.io':('ibkrv115rcc62xxqblay.eastasia.data.azurecr.io','10.40.10.4')}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def zone_id(zone):return rid('Microsoft.Network/privateDnsZones',zone)
def link_id(zone):return zone_id(zone)+'/virtualNetworkLinks/upgrade-legacy-20261010'
def body():return {'location':'global','properties':{'registrationEnabled':False,'virtualNetwork':{'id':VNET}}}


def source_hash():return {name:sha(ROOT/'scripts'/name) for name in (Path(__file__).name,'probe_upgrade_dns_route_20261010.py')}


def boundary():
    network=request('GET',VNET+'?api-version=2024-05-01')
    if network['location']!='eastasia' or network['properties'].get('virtualNetworkPeerings') or network['properties'].get('dhcpOptions',{}).get('dnsServers'):
        raise ValueError('Legacy isolated default-DNS network required')
    records={}
    for zone,(_,ip) in ZONES.items():
        response=request('GET',zone_id(zone)+'/A'+API)
        if response.get('nextLink'):raise ValueError('Unexpected DNS pagination')
        records[zone]={v['name']:sorted(x['ipv4Address'] for x in v['properties']['aRecords']) for v in response['value']}
        if records[zone]!={'@':[ip]}:raise ValueError('Legacy exact-name private record differs')
    resources=current_resources()
    legacy=[r for r in resources if (r['properties'].get('environmentId') or r['properties'].get('managedEnvironmentId','')).lower().endswith('/ibkrv11-env')]
    if {r['name'] for r in legacy}!=set(JOBS):raise ValueError('Unreviewed legacy caller')
    services={}
    for kind,name,version in (('Microsoft.KeyVault/vaults','ibkrv11-5rcc62xxqblay','2023-07-01'),
            ('Microsoft.Storage/storageAccounts','ibkrv115rcc62xxqblay','2023-05-01'),
            ('Microsoft.ContainerRegistry/registries','ibkrv115rcc62xxqblay','2023-07-01')):
        service=request('GET',rid(kind,name)+'?api-version='+version)
        if service['properties'].get('publicNetworkAccess','Enabled')!='Enabled':raise ValueError('Public fallback must remain enabled during DNS rehearsal')
        services[kind]=stable(target_boundary(service))
    return {'records':records,'workloads':bindings(resources),'vnet':network['id'],'service_boundaries':services}


def owned_links(expected_present):
    found=[]
    for zone in ZONES:
        response=request('GET',zone_id(zone)+'/virtualNetworkLinks'+API)
        if response.get('nextLink'):raise ValueError('Unbounded DNS links')
        links=response['value']
        if not expected_present:
            if links:raise ValueError('Unexpected existing DNS link')
            continue
        if len(links)!=1 or links[0]['id'].lower()!=link_id(zone).lower() or not contains_requested(links[0]['properties'],body()['properties']):raise ValueError('Only exact owned legacy links permitted')
        if links[0]['properties'].get('provisioningState')!='Succeeded' or links[0]['properties'].get('virtualNetworkLinkState')!='Completed':raise ValueError('Link not ready')
        found.append(links[0])
    return found


def idle_all():
    runtime.safe_window()
    for name in JOBS:runtime.idle(name)


def plan():
    if OUT.exists():raise ValueError('Preserve previous DNS rehearsal')
    current=boundary();owned_links(False);idle_all()
    transport=protected(ROOT/'.work/platform-upgrade-20261010-stage28/archive-transport-v1/result.json')
    if transport.get('passed') is not True:raise ValueError('Private audit recovery path required')
    save(OUT,'plan',{'boundary':current,'body':body(),'source_hashes':source_hash(),
        'links':[link_id(z) for z in ZONES],'private_archive_receipt':transport['protected_receipt'],
        'scope':'Four exact-name legacy DNS links only, three legacy jobs; no public restrictions, secret, role, source or report changes',
        'rollback':'Delete only the four links created by this plan after exact readback; retain zones, records and endpoint resources. Verify restored public DNS/TLS from the same legacy workload.'})
    print('Protected four-link legacy DNS rehearsal plan')


def main(stage):
    if stage=='plan':return plan()
    if stage not in ('preview','apply','verify','start-private','read-private','rollback','verify-rollback','start-public','read-public'):raise ValueError('Unknown stage')
    p=protected(OUT/'plan.json')
    if p['source_hashes']!=source_hash() or p['boundary']!=boundary():raise ValueError('Source, records or workload drift')
    if stage=='preview':
        owned_links(False)
        if (OUT/'template.json').exists():raise ValueError('Reconcile previous preview')
        template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0','resources':[
            {'type':'Microsoft.Network/privateDnsZones/virtualNetworkLinks','apiVersion':'2020-06-01','name':z+'/upgrade-legacy-20261010',**body()} for z in ZONES]}
        with (OUT/'template.json').open('x',encoding='utf-8') as f:json.dump(template,f,indent=2)
        r=subprocess.run([AZ,'-m','azure.cli','deployment','group','what-if','-g',RG,'--template-file',str(OUT/'template.json'),'--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if r.returncode:raise ValueError('DNS preview failed')
        value=json.loads(r.stdout);save(OUT,'preview',value)
        if value.get('status')!='Succeeded':raise ValueError('DNS preview incomplete')
        validate_changes(value['changes'],p['links']);save(OUT,'preview-qualified',{'passed':True,'plan_sha256':sha(OUT/'plan.json')})
        print('Exact four-link create-only preview passed');return
    if stage=='apply':
        q=protected(OUT/'preview-qualified.json')
        if q.get('passed') is not True or q['plan_sha256']!=sha(OUT/'plan.json'):raise ValueError('Qualified preview required')
        idle_all();owned_links(False)
        if (OUT/'apply-intent.json').exists():raise ValueError('Reconcile earlier mutation')
        save(OUT,'apply-intent',{'links':p['links'],'body':body()})
        for index,zone in enumerate(ZONES):
            if request('GET',link_id(zone)+API,missing=True) is not None:raise ValueError('Do not overwrite DNS link')
            ack=request('PUT',link_id(zone)+API,data=body());save(OUT,'apply-ack-'+str(index),{'id':ack['id'],'state':ack['properties'].get('provisioningState')})
        print('Submitted four legacy-only DNS links; public paths remain enabled');return
    if stage in ('verify','verify-rollback'):
        owned_links(stage=='verify')
        name='linked' if stage=='verify' else 'rollback-verified'
        if not (OUT/(name+'.json')).exists():save(OUT,name,{'passed':True,'links_present':stage=='verify','public_access_changed':False,'workloads_preserved':True})
        print(name+' settings verified');return
    if stage=='rollback':
        idle_all();links=owned_links(True)
        if (OUT/'rollback-intent.json').exists():raise ValueError('Reconcile prior rollback')
        # The rollback is available even if a private route test fails.
        save(OUT,'rollback-intent',{'links':links,'delete_scope':p['links'],'retained':'All DNS zones, A records, endpoints and workload definitions'})
        for index,zone in enumerate(ZONES):
            live=request('GET',link_id(zone)+API)
            if not contains_requested(live['properties'],body()['properties']):raise ValueError('Rollback target changed')
            request('DELETE',link_id(zone)+API);save(OUT,'rollback-ack-'+str(index),{'resource':link_id(zone),'delete_requested':True})
        print('Removed only the four rehearsal links; zones/records/endpoints retained');return
    mode=stage.split('-',1)[1];folder=OUT/(mode+'-probe-v1')
    owned_links(mode=='private')
    job=request('GET',JOBPATH+APP_API)
    if stage.startswith('start-'):
        idle_all()
        if folder.exists():raise ValueError('Reconcile previous probe')
        code='EXPECTED='+repr(dict(ZONES.values()))+'\nMODE='+repr(mode)+'\n'+(ROOT/'scripts/probe_upgrade_dns_route_20261010.py').read_text(encoding='utf-8')
        template=corrected_template(job,code);template['containers'][0]['env']=[]
        save(folder,'intent',{'template':template,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'mode':mode,'credentials_injected':False})
        ack=request('POST',JOBPATH+'/start'+APP_API,data=template);save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'mode':mode,'execution':ack['name']}));return
    intent=protected(folder/'intent.json');name=protected(folder/'dispatch.json')['execution']
    execution=request('GET',JOBPATH+'/executions/'+name+APP_API);actual=execution['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')) or actual.get('env'):raise ValueError('Executed probe differs')
    state=execution['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('DNS route probe failed; use exact owned-link rollback when applicable')
    prefix='UPGRADE_DNS_ROUTE '
    rows=az('monitor','log-analytics','query','-w','f594ac4f-31eb-4684-a5af-a8adc5020cfa','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting DNS proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if proof.get('passed') is not True or proof.get('mode')!=mode or {r['host'] for r in proof['results']}!=set(dict(ZONES.values())) or len(proof['results'])!=4 or any(r.get('verified_tls') is not True for r in proof['results']):raise ValueError('Incomplete DNS proof')
    if mode=='private' and any(r['addresses']!=[dict(ZONES.values())[r['host']]] or r['peer']!=dict(ZONES.values())[r['host']] for r in proof['results']):raise ValueError('Private route mismatch')
    if proof.get('dns_overridden') is not False or proof.get('credentials_read') is not False or proof.get('database_writes')!=0 or proof.get('blob_writes')!=0:raise ValueError('Read-only proof mismatch')
    if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'execution':name,'proof':proof,'normal_workflows_run':False,'public_access_changed':False})
    print(json.dumps({'mode':mode,'passed':True,'hosts':4}))


if __name__=='__main__':main(*sys.argv[1:])
