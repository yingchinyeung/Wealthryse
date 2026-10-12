"""Database capacity and secret-expiry metadata only; no secret values."""
import concurrent.futures,json
from datetime import datetime,timedelta,timezone
from urllib.parse import urlencode
from audit_azure_runtime import az
from audit_viewer_resource_group_20261008 import FOLDER
from deploy_metadata_monitor import request

def main():
    inventory=json.loads((FOLDER/'resource-group-inventory.json').read_bytes())
    now=datetime.now(timezone.utc);period=(now-timedelta(days=1)).isoformat()+'/'+now.isoformat()
    targets=[r for r in inventory['resources'] if r['type'].lower() in ('microsoft.dbforpostgresql/flexibleservers','microsoft.keyvault/vaults')]
    def read(resource):
        try:
            if resource['type'].lower()=='microsoft.keyvault/vaults':
                values=az('keyvault','secret','list','--vault-name',resource['name'],'--query','[].{name:name,enabled:attributes.enabled,expires:attributes.expires,updated:attributes.updated}')
                return {'resource':resource['name'],'kind':'secret_metadata','values':values,'secret_values_read':0}
            metrics=[]
            for name in ('cpu_percent','memory_percent','storage_percent','active_connections'):
                value=az('monitor','metrics','list','--resource',resource['id'],'--metric',name,'--interval','PT1H','--aggregation','Maximum','--start-time',(now-timedelta(days=1)).strftime('%Y-%m-%dT%H:%M:%SZ'),'--end-time',now.strftime('%Y-%m-%dT%H:%M:%SZ'))
                for metric in value.get('value',[]):
                    points=[p for series in metric.get('timeseries',[]) for p in series.get('data',[])]
                    maxima=[p['maximum'] for p in points if p.get('maximum') is not None]
                    metrics.append({'name':metric['name']['value'],'maximum':max(maxima) if maxima else None,'points':len(points),'error_code':metric.get('errorCode')})
            return {'resource':resource['name'],'kind':'database_capacity','metrics':metrics}
        except Exception as error:return {'resource':resource['name'],'error_type':type(error).__name__}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(read,targets))
    path=FOLDER/'capacity-and-expiry-metadata-v2.json'
    if path.exists():raise ValueError('Preserve prior capacity evidence')
    record={'captured_at':now.isoformat(),'period':period,'results':results,'mutations':0,'secret_values_read':0}
    path.write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({'databases':[r for r in results if r.get('kind')=='database_capacity'],'vault_secret_counts':[{'vault':r['resource'],'metadata_rows':len(r['values']),'disabled':sum(not v.get('enabled',True) for v in r['values']),'expiry_count':sum(v.get('expires') is not None for v in r['values'])} for r in results if r.get('kind')=='secret_metadata'],'read_failures':[r for r in results if 'error_type' in r],'secret_values_read':0},indent=2))

if __name__=='__main__':main()
