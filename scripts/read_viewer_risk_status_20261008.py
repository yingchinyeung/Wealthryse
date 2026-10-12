"""Read only structured risk worker status fields, not arbitrary log messages."""
import json
from audit_azure_runtime import az
from audit_viewer_resource_group_20261008 import FOLDER

def main():
    q='''ContainerAppConsoleLogs_CL | where TimeGenerated > ago(2d) | where ContainerGroupName_s startswith "ibkrv2stg-risk-snapshots-29857" | where Log_s startswith "{" | extend p=parse_json(Log_s) | where isnotempty(p.status) or isnotempty(p.historical_audit) | project TimeGenerated,ContainerGroupName_s,status=p.status,blocked=p.blocked_subject_dates,historical=p.historical_audit,unavailable=p.unavailable_inception_dates'''
    result=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    (FOLDER/'risk-worker-status.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
