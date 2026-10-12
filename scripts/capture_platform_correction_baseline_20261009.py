"""Read-only post-coordination baseline. No resource writes or job starts."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az, RG

OUT=Path(__file__).resolve().parents[1]/'.work/platform-corrections-20261009-v1'
JOBS=('ibkrv2stg-account-sync-daily','ibkrv2stg-month-end-factsheet','ibkrv2stg-analyst-assurance','ibkrv2stg-burn-in-monitor')
def inspect(name):
    return {'name':name,
      'configuration':az('containerapp','job','show','-g',RG,'-n',name,'--query','{image:properties.template.containers[0].image,command:properties.template.containers[0].command,args:properties.template.containers[0].args,state:properties.provisioningState}'),
      'executions':az('containerapp','job','execution','list','-g',RG,'-n',name,'--query','[:4].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}')}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=4) as pool: jobs=list(pool.map(inspect,JOBS))
    web=az('containerapp','show','-g',RG,'-n','ibkrv2stg-web','--query','{image:properties.template.containers[0].image,revision:properties.latestReadyRevisionName,state:properties.provisioningState,traffic:properties.configuration.ingress.traffic}')
    result={'captured_at':datetime.now(timezone.utc).isoformat(),'jobs':jobs,'web':web,'read_only':True}
    name='baseline-'+datetime.now(timezone.utc).strftime('%H%M%S')+'.json'
    (OUT/name).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
