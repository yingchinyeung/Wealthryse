"""Read current assurance execution metadata and sanitized results only."""
import json
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az, RG

query = """ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(3h)
| where ContainerGroupName_s startswith 'ibkrv2stg-analyst-assurance-'
| extend J=parse_json(Log_s)
| where isnotempty(J.subjects) or isnotempty(J.findings)
| project TimeGenerated,ContainerGroupName_s,Status=tostring(J.status),Subjects=J.subjects,Findings=J.findings
| order by TimeGenerated desc
| take 20"""
data = {
    'executions': az('containerapp','job','execution','list','-g',RG,'-n','ibkrv2stg-analyst-assurance','--query','[:8].{name:name,status:properties.status,start:properties.startTime,end:properties.endTime}'),
    'results': az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query),
    'captured_at': datetime.now(timezone.utc).isoformat(),
}
output=Path(__file__).resolve().parents[1]/'.work/platform-review-20261009-v1/assurance-refresh.json'
output.write_text(json.dumps(data,indent=2),encoding='utf-8')
print(json.dumps(data,indent=2))
