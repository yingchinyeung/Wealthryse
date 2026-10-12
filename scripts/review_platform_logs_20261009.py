"""Read-only, sanitized operational log evidence; no jobs or model calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from audit_azure_runtime import az

OUT=Path(__file__).resolve().parents[1]/'.work/platform-review-20261009-v1'
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'
QUERIES={
 'failed_job_messages': '''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d)
| where ContainerGroupName_s has_any ('account-sync-daily-29858890','month-end-factsheet-29858940','analyst-assurance-29858950','burn-in-monitor-29858955')
| extend J=parse_json(Log_s)
| extend Keys=bag_keys(J), ErrorType=extract(@'([A-Za-z]+(?:Error|Exception)):',1,Log_s), Location=extract(@'File .*?, line [0-9]+',0,Log_s)
| project TimeGenerated,ContainerGroupName_s,Keys,ErrorType,Location,
  Event=tostring(J.event),Status=tostring(J.status),Reason=tostring(J.reason),Code=tostring(J.code)
| take 500''',
 'job_system_failures': '''ContainerAppSystemLogs_CL
| where TimeGenerated > ago(1d)
| where Reason_s in ('BackoffLimitExceeded','DeadlineExceeded','ErrImagePull','ImagePullBackOff','ContainerCrashing','Failed')
| summarize Occurrences=count(),First=min(TimeGenerated),Last=max(TimeGenerated) by JobName_s,ExecutionName_s,Reason_s
| order by Last desc''',
 'web_security_summary': r'''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d)
| where ContainerGroupName_s startswith 'ibkrv2stg-web-'
| where Log_s contains 'SECURITY_AUDIT '
| extend J=parse_json(extract(@'SECURITY_AUDIT (\{.*\})',1,Log_s))
| summarize Requests=count(),P95ms=percentile(todouble(J.duration_ms),95),Last=max(TimeGenerated) by Status=tostring(J.status_code),Outcome=tostring(J.outcome),Path=tostring(J.path),Revision=RevisionName_s
| order by Requests desc''',
 'monitor_findings': '''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(2h)
| where ContainerGroupName_s startswith 'ibkrv2stg-metadata-monitor-'
| where Log_s startswith 'METADATA_MONITOR '
| extend J=parse_json(substring(Log_s,17))
| where J.kind in ('risk','benchmark','finding','complete')
| project TimeGenerated,Kind=tostring(J.kind),Captured=tostring(J.captured_at),State=tostring(J.value.state),Subject=tostring(J.value.subject_id),Benchmark=tostring(J.value.benchmark_id),Quality=tostring(J.value.quality),Records=tostring(J.records),Complete=tostring(J.inventory_complete),Access=tostring(J.access_check)
| take 500''',
}
QUERIES['failure_details']='''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d)
| where ContainerGroupName_s has_any ('account-sync-daily-29858890','month-end-factsheet-29858940','analyst-assurance-29858950','burn-in-monitor-29858955')
| extend J=parse_json(Log_s)
| where Log_s matches regex @'^(TypeError|RuntimeError):' or isnotempty(J.findings) or isnotempty(J.checks)
| project TimeGenerated,ContainerGroupName_s,
  SafeError=extract(@'^(?:TypeError|RuntimeError): ([A-Za-z0-9_ :.,=()<>!\\x22\\x27-]{1,240})',1,Log_s),
  Findings=J.findings,Failures=J.failures,Checks=J.checks,Status=tostring(J.status),Subjects=J.subjects
| take 50'''
QUERIES['burn_in_details']='''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d) and ContainerGroupName_s has 'burn-in-monitor-29858955'
| extend J=parse_json(Log_s)
| where isnotempty(J.status)
| project TimeGenerated,Status=tostring(J.status),Keys=bag_keys(J),Failures=J.failures,ChecksKeys=bag_keys(J.checks),DatabaseFailures=J.database_failures'''
QUERIES['assurance_subjects']='''ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(1d) and ContainerGroupName_s has 'analyst-assurance-29858950'
| extend J=parse_json(Log_s)
| where isnotempty(J.subjects)
| project TimeGenerated,Subjects=J.subjects'''
def run(item):
    name,query=item
    try:return name,az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',query)
    except Exception as e:return name,{'error_type':type(e).__name__}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:result=dict(pool.map(run,QUERIES.items()))
    result['captured_at']=datetime.now(timezone.utc).isoformat()
    path=OUT/'operational-logs.json'
    path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
