"""Collect only structured cache-repair summary fields for the exact execution."""
import json
from audit_azure_runtime import az
from repair_viewer_risk_backlog_20261008 import FOLDER

def main():
    execution=json.loads((FOLDER/'dispatch.json').read_bytes())['execution']
    if execution!='ibkrv2stg-risk-snapshots-0clx0xj':raise ValueError('Unexpected repair execution')
    query='''ContainerAppConsoleLogs_CL | where TimeGenerated > ago(1d) | where ContainerGroupName_s startswith "'''+execution+'''" | where Log_s startswith "{" | extend p=parse_json(Log_s) | where isnotempty(p.status) or isnotempty(p.historical_audit) | project TimeGenerated,status=p.status,blocked=p.blocked_subject_dates,historical=p.historical_audit,unavailable=p.unavailable_inception_dates'''
    result=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    target=FOLDER/'summary.json'
    if target.exists():raise ValueError('Preserve previous summary')
    target.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
