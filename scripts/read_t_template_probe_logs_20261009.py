import json
from run_alpha_ms_recovery_operation import request,BASE,az
w=request('GET',BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s contains '0nco22h' | project prefix=substring(Log_s,0,500), characters=strlen(Log_s)"
print(json.dumps(az('monitor','log-analytics','query','-w',w['properties']['customerId'],'--analytics-query',q)))
