"""Read-only private-network caller map; omit inline environment and secret values."""
import json
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlsplit
from deploy_metadata_monitor import request,BASE
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage6'
def listing(kind,version):
    value=request('GET',BASE+kind+'?api-version='+version)
    items=list(value['value']);seen=set()
    while value.get('nextLink'):
        link=value['nextLink'];parsed=urlsplit(link)
        if parsed.scheme!='https' or parsed.netloc!='management.azure.com' or parsed.path.lower()!=(BASE+kind).lower() or link in seen or len(seen)>=20:raise ValueError('Unexpected pagination scope')
        seen.add(link);value=request('GET',parsed.path+'?'+parsed.query);items.extend(value['value'])
    return items
def main():
    result=[]
    for kind in ('Microsoft.App/containerApps','Microsoft.App/jobs'):
        for item in listing(kind,'2024-03-01'):
            props=item['properties'];config=props.get('configuration',{});template=props['template']
            result.append({'name':item['name'],'type':item['type'],'identity':item.get('identity'),
                'environment_id':props.get('environmentId') or props.get('managedEnvironmentId'),
                'registries':[{k:r.get(k) for k in ('server','identity','passwordSecretRef','username')} for r in (config.get('registries') or [])],
                'secret_references':[{'name':s['name'],'identity':s.get('identity'),'vault_host':urlsplit(s.get('keyVaultUrl') or '').hostname,'inline_value_omitted':bool(s.get('value'))} for s in (config.get('secrets') or [])],
                'containers':[{'name':c['name'],'image':c['image'],'env_names':[e['name'] for e in (c.get('env') or [])],'env_secret_refs':{e['name']:e['secretRef'] for e in (c.get('env') or []) if e.get('secretRef')}} for c in (template.get('containers') or [])],
                'schedule':config.get('scheduleTriggerConfig'),'state':props.get('provisioningState')})
    environments=[{'id':e['id'],'name':e['name'],'network':e['properties'].get('vnetConfiguration'),'workload_profiles':e['properties'].get('workloadProfiles')} for e in listing('Microsoft.App/managedEnvironments','2024-03-01')]
    ROOT.mkdir(exist_ok=True);path=ROOT/('caller-map-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json')
    with path.open('x',encoding='utf-8') as f:json.dump({'captured_at':datetime.now(timezone.utc).isoformat(),'read_only':True,'callers':result,'environments':environments,'public_access_changed':False,'secret_values_retained':False},f,indent=2)
    print(json.dumps({'file':str(path),'callers':len(result),'environments':len(environments),'vault_hosts':sorted({s['vault_host'] for r in result for s in r['secret_references'] if s['vault_host']})}))
if __name__=='__main__':main()
