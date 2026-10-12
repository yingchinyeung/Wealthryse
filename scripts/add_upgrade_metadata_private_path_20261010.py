"""Create-only legacy-vault endpoint for the main metadata monitor.

No DNS link, secret access, role, public-access setting or workload is changed.
"""
import hashlib,json,os,subprocess,sys
from pathlib import Path
import add_upgrade_legacy_private_paths_20261009 as old
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
import control_secret_metadata_monitor_20261010 as monitor
from preview_upgrade_private_endpoints_20261009 import validate_changes

OUT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261010-stage27/metadata-private-path-v1'
TARGET=old.rid('Microsoft.KeyVault/vaults','ibkrv11-5rcc62xxqblay')
ENDPOINT=old.rid('Microsoft.Network/privateEndpoints','ibkr-upgrade-main-legacy-vault-pe')
ZONE=old.rid('Microsoft.Network/privateDnsZones','privatelink.vaultcore.azure.net')
VAULT_API='?api-version=2023-07-01'
DNS_API='?api-version=2020-06-01'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def body():
    return {'location':'eastasia','properties':{'subnet':{'id':old.SUBNET},
        'privateLinkServiceConnections':[{'name':'ibkr-upgrade-main-legacy-vault-pe-connection',
            'properties':{'privateLinkServiceId':TARGET,'groupIds':['vault']}}]}}


def boundary():
    network=old.boundary()
    vault=old.request('GET',TARGET+VAULT_API)
    if vault['location']!='eastasia' or vault['properties'].get('enableRbacAuthorization') is not True:
        raise ValueError('Vault region or RBAC drift')
    identity=old.request('GET',monitor.IDENTITY+'?api-version=2023-01-31')
    job=old.request('GET',monitor.JOBPATH+monitor.API)
    monitor.verify_job(job,identity,scheduled=True)
    links=old.request('GET',ZONE+'/virtualNetworkLinks'+DNS_API)['value']
    if links:raise ValueError('DNS already linked; reassess before additive work')
    return {'network':network,'vault':old.stable(old.target_boundary(vault)),
        'monitor_invariant':monitor.fingerprint(job['properties']),
        'monitor_identity':identity['id'],'unlinked_zone':ZONE}


def main(stage):
    if stage not in ('plan','preview','apply','verify'):raise ValueError('Unknown stage')
    current=boundary()
    existing=old.request('GET',ENDPOINT+old.API,missing=True)
    if stage=='plan':
        if OUT.exists() or existing is not None:raise ValueError('Preserve existing evidence/resource')
        save(OUT,'plan',{'boundary':current,'body':body(),'resource':ENDPOINT,
            'controller_sha256':sha(Path(__file__)),
            'dependency':'Main-network metadata-only monitor lists three vaults, including legacy vault; existing main vault zone lacks its private path',
            'rollback':'Additive endpoint with no DNS association or caller redirection. Preserve on failure; no existing resource is deleted.',
            'dns_links_created':False,'public_access_changed':False,'permissions_changed':False})
        print('Protected one create-only metadata private-path plan');return
    plan=protected(OUT/'plan.json')
    if plan['boundary']!=current or plan['body']!=body() or plan['controller_sha256']!=sha(Path(__file__)):
        raise ValueError('Source or infrastructure drift')
    if stage=='preview':
        if existing is not None or (OUT/'template.json').exists():raise ValueError('Reconcile prior preview')
        template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#',
            'contentVersion':'1.0.0.0','resources':[{'type':'Microsoft.Network/privateEndpoints',
            'apiVersion':'2024-05-01','name':ENDPOINT.rsplit('/',1)[1],**body()}]}
        with (OUT/'template.json').open('x',encoding='utf-8') as f:json.dump(template,f,indent=2)
        run=subprocess.run([old.AZ,'-m','azure.cli','deployment','group','what-if','--resource-group',old.RG,
            '--template-file',str(OUT/'template.json'),'--mode','Incremental','--no-pretty-print','-o','json'],
            capture_output=True,text=True,encoding='utf-8',timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if run.returncode:raise ValueError('Create-only preview failed; no resource deployed')
        result=json.loads(run.stdout);save(OUT,'what-if',result)
        if result.get('status')!='Succeeded':raise ValueError('Incomplete preview')
        validate_changes(result['changes'],[ENDPOINT])
        save(OUT,'preview-qualified',{'passed':True,'plan_sha256':sha(OUT/'plan.json'),'exact_create_only_resources':[ENDPOINT]})
        print('Exact one-endpoint create-only preview passed');return
    if stage=='apply':
        qualified=protected(OUT/'preview-qualified.json')
        if qualified.get('passed') is not True or qualified['plan_sha256']!=sha(OUT/'plan.json'):
            raise ValueError('Qualified protected preview required')
        if existing is not None or (OUT/'apply-intent.json').exists():raise ValueError('Reconcile prior mutation')
        save(OUT,'apply-intent',{'resource':ENDPOINT,'body':body()})
        if boundary()!=current or old.request('GET',ENDPOINT+old.API,missing=True) is not None:
            raise ValueError('Immediate drift')
        result=old.request('PUT',ENDPOINT+old.API,data=body())
        save(OUT,'apply-ack',{'id':result['id'],'state':result['properties'].get('provisioningState')})
        print('Submitted additive vault endpoint; no callers redirected');return
    if existing is None or existing['properties'].get('provisioningState')!='Succeeded':
        print('Endpoint not ready');return
    p=existing['properties']
    if not old.contains_requested(p,body()['properties']) or p['privateLinkServiceConnections'][0]['properties']['privateLinkServiceConnectionState']['status']!='Approved':
        raise ValueError('Endpoint approval or configuration differs')
    if old.request('GET',ENDPOINT+'/privateDnsZoneGroups'+old.API)['value']:raise ValueError('Unexpected DNS association')
    nics=p.get('networkInterfaces',[])
    if len(nics)!=1:raise ValueError('Expected one endpoint NIC')
    nic=old.request('GET',nics[0]['id']+old.API)
    ips=[v['properties']['privateIPAddress'] for v in nic['properties']['ipConfigurations']]
    import ipaddress
    if len(ips)!=1 or ipaddress.ip_address(ips[0]) not in ipaddress.ip_network('10.50.10.0/27'):
        raise ValueError('Unexpected private endpoint address')
    if not (OUT/'verified.json').exists():save(OUT,'verified',{'passed':True,'endpoint':ENDPOINT,'private_ip':ips[0],
        'dns':p.get('customDnsConfigs'),'dns_changed':False,'public_access_changed':False,
        'permissions_changed':False,'metadata_private_request_verified':False,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'private_ip':ips[0],'dns_changed':False}))


if __name__=='__main__':main(*sys.argv[1:])
