"""Read only non-secret job routing needed for a scoped T release."""
import hashlib,json
from pathlib import Path
from datetime import datetime,timezone
import run_alpha_ms_recovery_operation as c

NAMES=('onboard-analytics','derived-daily','benchmark-daily','risk-snapshots',
       'risk-free-daily','report-snapshot','onboard-render','month-end-factsheet')
SAFE_ENV={'PORTFOLIO_TENANT_ID','PILOT_ACCOUNT_ID','REPORTING_CURRENCY','REPORTING_PERIOD_END',
          'RISK_SNAPSHOT_SUBJECTS','HIBOR_LICENSE_SCOPE_CONFIRMED','DEPLOYMENT_ENVIRONMENT'}
if __name__=='__main__':
 rows=[]
 for suffix in NAMES:
  name='ibkrv2stg-'+suffix
  job=c.request('GET',c.BASE+'Microsoft.App/jobs/'+name+'?api-version=2024-03-01')
  cfg=job['properties']['configuration'];containers=job['properties']['template']['containers']
  if len(containers)!=1:raise ValueError('Unexpected multi-container job')
  x=containers[0]
  rows.append({'job':name,'image':x['image'],'trigger_type':cfg['triggerType'],
   'schedule':(cfg.get('scheduleTriggerConfig') or {}).get('cronExpression'),
   'script_arguments':[v for v in x.get('args',[]) if isinstance(v,str) and v.endswith('.py')],
   'non_secret_routing':{e['name']:e.get('value') for e in x.get('env',[]) if e['name'] in SAFE_ENV},
   'invariant_sha256':c.invariant(job)})
 result={'observed_at':datetime.now(timezone.utc).isoformat(),'jobs':rows,'azure_changes':False}
 folder=Path('.work/alpha-t-job-contracts-20261009-v1');folder.mkdir(exist_ok=True)
 with (folder/'inspection.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
 print(json.dumps(result))
