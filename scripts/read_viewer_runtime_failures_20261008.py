"""Sanitized exception locations, not raw log contents."""
import json
from audit_azure_runtime import az
from audit_viewer_resource_group_20261008 import FOLDER

QUERY = '''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(2d)
| where ContainerGroupName_s startswith "ibkrv2stg-risk-snapshots-" or ContainerGroupName_s startswith "ibkrv2stg-web-"
| where Log_s has_any ("Error", "Exception", "failed", "Traceback")
| extend error_type=extract(@"([A-Za-z]+Error):",1,Log_s), file=extract(@"File .*?, line [0-9]+",0,Log_s)
| summarize occurrences=count(),first_seen=min(TimeGenerated),last_seen=max(TimeGenerated) by ContainerGroupName_s,error_type,file'''
def main():
    result=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',QUERY)
    FOLDER.mkdir(parents=True,exist_ok=True)
    (FOLDER/'runtime-exception-locations.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
