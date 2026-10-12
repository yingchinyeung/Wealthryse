"""Read only job status and bounded diagnostic log prefixes."""
import json
import control_upgrade_g04_preflight_20261010 as b
NAME='ibkrv2stg-inbox-acceptance-r2-pnce1ev'
p=b.rid('Microsoft.App/jobs','ibkrv2stg-inbox-acceptance-r2')
r=b.request('GET',p+'/executions/'+NAME+b.API)
print(json.dumps({k:r['properties'].get(k) for k in ('status','startTime','endTime')}))
query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+NAME+"-' | summarize Rows=count() by Prefix=substring(Log_s,0,160)"
print(json.dumps(b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)))
