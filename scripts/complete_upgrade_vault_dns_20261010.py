"""Complete the missing unlinked private DNS record; no caller cutover."""
import json,os,subprocess,sys
from pathlib import Path
import add_upgrade_metadata_private_path_20261010 as p
from retire_upgrade_additional_diagnostics_20261010 import current_resources,bindings

OUT=p.OUT.parents[1]/'platform-upgrade-20261010-stage28/vault-dns-v1'
GROUP=p.ENDPOINT+'/privateDnsZoneGroups/upgrade'
RECORD='ibkrv11-5rcc62xxqblay'


def body():return {'properties':{'privateDnsZoneConfigs':[{'name':'service-zone','properties':{'privateDnsZoneId':p.ZONE}}]}}


def records():
    if p.old.request('GET',p.ZONE+'/virtualNetworkLinks'+p.DNS_API)['value']:
        raise ValueError('Zone already linked; reassess active callers')
    response=p.old.request('GET',p.ZONE+'/A'+p.DNS_API)
    if response.get('nextLink'):raise ValueError('Unexpected record pagination')
    return {v['name']:sorted(x['ipv4Address'] for x in v['properties']['aRecords']) for v in response['value']}


def endpoint():
    proof=p.protected(p.OUT/'verified.json')
    runtime=p.protected(p.OUT/'runtime-v1/result.json')
    live=p.old.request('GET',p.ENDPOINT+p.old.API)
    if proof.get('passed') is not True or runtime.get('passed') is not True or runtime['proof']['private_peer']!=proof['private_ip'] or runtime['proof']['secret_values_read']!=0:
        raise ValueError('Authenticated metadata-only path required')
    prop=live['properties']
    if prop.get('provisioningState')!='Succeeded' or not p.old.contains_requested(prop,p.body()['properties']) or prop['privateLinkServiceConnections'][0]['properties']['privateLinkServiceConnectionState']['status']!='Approved':
        raise ValueError('Endpoint differs')
    nic=p.old.request('GET',prop['networkInterfaces'][0]['id']+p.old.API)
    if [v['properties']['privateIPAddress'] for v in nic['properties']['ipConfigurations']]!=[proof['private_ip']]:raise ValueError('Private IP drift')
    return proof


def main(stage):
    if stage not in ('plan','preview','apply','verify'):raise ValueError('Unknown stage')
    proof=endpoint();actual=records();group=p.old.request('GET',GROUP+p.old.API,missing=True)
    if stage=='plan':
        if OUT.exists() or group is not None or RECORD in actual:raise ValueError('Do not overwrite existing DNS state')
        p.save(OUT,'plan',{'body':body(),'records':actual,'bindings':bindings(current_resources()),
            'ip':proof['private_ip'],'endpoint_receipt':proof['protected_receipt'],
            'controller_sha256':p.sha(Path(__file__)),'dns_links_created':False,
            'rollback':'No virtual network links exist. This adds only the missing endpoint zone group and its A record; preserve on failure without redirecting callers.'})
        print('Protected missing unlinked vault DNS record plan');return
    plan=p.protected(OUT/'plan.json')
    if plan['body']!=body() or plan['controller_sha256']!=p.sha(Path(__file__)) or plan['ip']!=proof['private_ip'] or bindings(current_resources())!=plan['bindings']:
        raise ValueError('Source, endpoint or workload drift')
    wanted={**plan['records'],RECORD:[plan['ip']]}
    if stage in ('preview','apply') and (actual!=plan['records'] or group is not None):raise ValueError('Never overwrite existing records or association')
    if stage=='preview':
        if (OUT/'template.json').exists():raise ValueError('Reconcile previous preview')
        template={'$schema':'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#','contentVersion':'1.0.0.0',
            'resources':[{'type':'Microsoft.Network/privateEndpoints/privateDnsZoneGroups','apiVersion':'2024-05-01',
            'name':p.ENDPOINT.rsplit('/',1)[1]+'/upgrade',**body()}]}
        with (OUT/'template.json').open('x',encoding='utf-8') as f:json.dump(template,f,indent=2)
        run=subprocess.run([p.old.AZ,'-m','azure.cli','deployment','group','what-if','-g',p.old.RG,'--template-file',str(OUT/'template.json'),
            '--mode','Incremental','--no-pretty-print','-o','json'],capture_output=True,text=True,encoding='utf-8',timeout=240,env={**os.environ,'PYTHONUTF8':'1'})
        if run.returncode:raise ValueError('DNS create-only preview failed')
        result=json.loads(run.stdout);p.save(OUT,'preview',result)
        if result.get('status')!='Succeeded':raise ValueError('Incomplete preview')
        p.validate_changes(result['changes'],[GROUP])
        p.save(OUT,'preview-qualified',{'passed':True,'plan_sha256':p.sha(OUT/'plan.json')})
        print('Exact one-zone-group create-only preview passed');return
    if stage=='apply':
        qualified=p.protected(OUT/'preview-qualified.json')
        if qualified.get('passed') is not True or qualified['plan_sha256']!=p.sha(OUT/'plan.json') or (OUT/'apply-intent.json').exists():raise ValueError('Qualified unique mutation required')
        p.save(OUT,'apply-intent',{'resource':GROUP,'body':body()})
        if records()!=actual or p.old.request('GET',GROUP+p.old.API,missing=True) is not None:raise ValueError('Immediate DNS drift')
        ack=p.old.request('PUT',GROUP+p.old.API,data=body())
        p.save(OUT,'apply-ack',{'id':ack['id'],'state':ack['properties'].get('provisioningState')})
        print('Added unlinked vault zone association; no caller DNS or public access changed');return
    if group is None or group['properties'].get('provisioningState')!='Succeeded':print('Association pending');return
    if not p.old.contains_requested(group['properties'],body()['properties']) or actual!=wanted:raise ValueError('Association or exact record coverage differs')
    if not (OUT/'verified.json').exists():p.save(OUT,'verified',{'passed':True,'records':actual,'private_ip':plan['ip'],
        'existing_records_preserved':True,'workloads_preserved':True,'dns_links_created':False,'public_access_changed':False})
    print('Three unlinked vault records verified; 37 workload bindings preserved')


if __name__=='__main__':main(*sys.argv[1:])
