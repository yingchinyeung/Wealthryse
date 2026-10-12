"""Fresh private-path dependency inventory; record hosts, never secret values."""
import json
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
import inventory_upgrade_callers_20261009 as inventory
from deploy_viewer_private_admin_20261008 import request
from deploy_metadata_monitor import BASE
from run_specialist_gateway_read_verification import retain
inventory.request=request
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10'
URL_FIELDS={'AZURE_STORAGE_ACCOUNT_URL','REFERENCE_STORAGE_ACCOUNT_URL','KEY_VAULT_URI','AZURE_KEY_VAULT_URL','AZURE_KEYVAULT_URL','KEY_VAULT_URL','CREDENTIAL_BROKER_URL'}

def main():
    result=[]
    for kind in ('Microsoft.App/containerApps','Microsoft.App/jobs'):
        for item in inventory.listing(kind,'2024-03-01'):
            p=item['properties'];config=p.get('configuration',{});references=[]
            for s in config.get('secrets') or []:
                if s.get('keyVaultUrl'):
                    references.append({'kind':'secret_reference','alias':s['name'],'host':urlsplit(s['keyVaultUrl']).hostname,'identity':s.get('identity')})
            for c in p['template'].get('containers') or []:
                for e in c.get('env') or []:
                    if e['name'] in URL_FIELDS and e.get('value'):
                        parsed=urlsplit(e['value'])
                        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('Unexpected non-public endpoint shape')
                        references.append({'kind':'endpoint','alias':e['name'],'host':parsed.hostname})
            result.append({'id':item['id'],'name':item['name'],'type':item['type'],'environment_id':p.get('environmentId') or p.get('managedEnvironmentId'),'identity':item.get('identity'),'registries':[{k:r.get(k) for k in ('server','identity')} for r in config.get('registries') or []],'references':references,'images':[c['image'] for c in p['template'].get('containers') or []],'schedule':config.get('scheduleTriggerConfig'),'state':p.get('provisioningState')})
    zones=inventory.listing('Microsoft.Network/privateDnsZones','2020-06-01')
    network=[]
    for name in ('ibkrv2stg-vnet','ibkrv11-vnet'):
        value=request('GET',BASE+'Microsoft.Network/virtualNetworks/'+name+'?api-version=2024-05-01')
        network.append({'id':value['id'],'dns':value['properties'].get('dhcpOptions'),'peerings':value['properties'].get('virtualNetworkPeerings',[])})
    payload={'captured_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'secret_values_retained':False,'callers':result,'private_dns_zones':[{'id':z['id'],'name':z['name']} for z in zones],'networks':network}
    ROOT.mkdir(exist_ok=True);path=ROOT/'dependency-inventory-v1.json'
    if path.exists():raise ValueError('Preserve existing inventory')
    with path.open('x',encoding='utf-8') as f:json.dump({**payload,'protected_receipt':retain(payload,'upgrade-network-dependency-inventory')},f,indent=2)
    print(json.dumps({'callers':len(result),'environments':dict(Counter(r['environment_id'].rsplit('/',1)[-1] for r in result)),'referenced_hosts':sorted({r['host'] for c in result for r in c['references']}),'registry_hosts':sorted({r['server'] for c in result for r in c['registries']}),'dns_zones':payload['private_dns_zones']}))

if __name__=='__main__':main()
