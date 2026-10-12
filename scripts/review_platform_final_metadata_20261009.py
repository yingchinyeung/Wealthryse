"""Final read-only coverage checks, excluding credential values."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from audit_azure_runtime import az,RG
OUT=Path(__file__).resolve().parents[1]/'.work/platform-review-20261009-v1'
INV=json.loads((OUT/'resource-group-inventory.json').read_text())
Q={}
for r in INV['resources']:
    if r['type']=='Microsoft.Storage/storageAccounts':
        Q['blob_diagnostics_'+r['name']]=('monitor','diagnostic-settings','list','--resource',r['id']+'/blobServices/default')
Q['auth_mode']=('containerapp','show','-g',RG,'-n','ibkrv2stg-web','--query','properties.template.containers[0].env[?name==`PORTFOLIO_AUTHORIZATION_MODE` || name==`AUTHORIZATION_MODE`].{name:name,value:value}')
Q['latest_jobs']=('containerapp','job','list','-g',RG,'--query','[?name==`ibkrv2stg-account-sync-daily` || name==`ibkrv2stg-analyst-assurance` || name==`ibkrv2stg-month-end-factsheet`].{name:name,image:properties.template.containers[0].image}')
def run(item):
    k,args=item
    try:return k,az(*args)
    except Exception as e:return k,{'error_type':type(e).__name__}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:data=dict(pool.map(run,Q.items()))
    data['captured_at']=datetime.now(timezone.utc).isoformat()
    (OUT/'final-metadata.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps(data,indent=2))
