"""Deploy one independently fault-qualified metadata alert; no retries or data writes."""
import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from audit_azure_runtime import az
from deploy_metadata_monitor import request, BASE, SUB
from ibkr_etl.operational_alerts import metadata_findings_query
from run_specialist_gateway_read_verification import retain

RULE='ibkr-v2-operational-data-findings'
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'
PATH=BASE+'Microsoft.Insights/scheduledQueryRules/'+RULE+'?api-version=2023-12-01'
FOLDER=Path('.work/system-improvements-20261006/operational-findings-v4')


def definition():
    return {'location':'eastasia','kind':'LogAlert',
        'tags':{'application':'ibkr-portfolio-platform','purpose':'operational-data-findings','owner':'portfolio-platform'},
        'properties':{'displayName':'Portfolio data and workflow need attention',
            'description':'Persistent risk/queue/benchmark metadata issues or invalid/stale monitor evidence. '
                'Two complete captures at least 20 minutes apart for data findings. '
                'Not financial certification. No automatic retries or data changes.',
            'enabled':True,'severity':2,
            'scopes':[BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'],
            'evaluationFrequency':'PT15M','windowSize':'PT15M','overrideQueryTimeRange':'PT2H',
            'autoMitigate':True,'skipQueryValidation':False,
            'criteria':{'allOf':[{'query':metadata_findings_query(),'timeAggregation':'Count',
                'operator':'GreaterThan','threshold':0,
                'dimensions':[{'name':'Scope','operator':'Include','values':['*']}],
                'failingPeriods':{'numberOfEvaluationPeriods':1,'minFailingPeriodsToAlert':1}}]},
            'actions':{'actionGroups':[BASE+'Microsoft.Insights/actionGroups/ibkrv2stg-alerts']}}}


def save(name,value):
    target=FOLDER/(name+'.json')
    if target.exists():
        raise ValueError('Evidence already exists; reconcile before repeating a mutation')
    receipt=retain(value,'operational-data-alert-'+name+'-20261006')
    with target.open('x',encoding='utf-8') as handle:
        json.dump(dict(record=value,protected_receipt=receipt),handle,indent=2)


def qualify():
    value=json.loads((FOLDER/'qualification.json').read_text(encoding='utf-8'))
    record=value['record']
    observed=datetime.fromisoformat(record['checked_at'])
    now=datetime.now(timezone.utc)
    if (value['protected_receipt']['protected_readback_verified'] is not True or not record['passed']
        or len(record['cases'])<28 or any(not x['passed'] for x in record['cases'])
        or not now-timedelta(hours=1)<=observed<=now
        or record['query_sha256']!=hashlib.sha256(metadata_findings_query().encode()).hexdigest()):
        raise ValueError('Recent independently protected fault qualification required')
    return value


def verify(current):
    wanted=definition()
    if current.get('id','').lower()!=(BASE+'Microsoft.Insights/scheduledQueryRules/'+RULE).lower():
        raise ValueError('Unexpected alert resource identity')
    actual=current['properties']; expected=wanted['properties']
    for key in ('displayName','description','enabled','severity','evaluationFrequency','windowSize',
                'overrideQueryTimeRange','autoMitigate','skipQueryValidation','criteria'):
        if actual.get(key)!=expected[key]:
            raise ValueError('Alert readback mismatch: '+key)
    for key in ('scopes',):
        if [x.lower() for x in actual[key]]!=[x.lower() for x in expected[key]]:
            raise ValueError('Alert resource scope mismatch')
    if [x.lower() for x in actual['actions']['actionGroups']]!=[x.lower() for x in expected['actions']['actionGroups']]:
        raise ValueError('Owner notification routing mismatch')
    if current.get('tags')!=wanted['tags']:
        raise ValueError('Alert ownership tags mismatch')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('stage',choices=('prepare','deploy','verify'))
    args=parser.parse_args()
    if az('account','show','--query','id')!=SUB:
        raise ValueError('Unexpected Azure subscription')
    qualification=qualify()
    if args.stage=='prepare':
        if request('GET',PATH,missing=True) is not None:
            raise ValueError('Alert already exists; do not overwrite it')
        group=request('GET',BASE+'Microsoft.Insights/actionGroups/ibkrv2stg-alerts?api-version=2023-01-01')
        if (group['properties'].get('enabled') is not True or not any(
            x.get('emailAddress','').lower()=='anthony.ying@wealthryse.ai' and x.get('status')=='Enabled'
            for x in group['properties'].get('emailReceivers',[]))):
            raise ValueError('Previously approved owner email route is not enabled')
        live=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',metadata_findings_query())
        if live:
            raise ValueError('Live findings require reconciliation before enabling a new notice')
        save('deployment-plan',dict(checked_at=datetime.now(timezone.utc).isoformat(),
            definition=definition(),qualification_receipt=qualification['protected_receipt'],
            live_issues=live,existing_alert_absent=True,owner_route_verified=True,
            portfolio_data_changes=False,application_or_job_changes=False))
        print(json.dumps(dict(prepared=True,live_issues=0)),flush=True)
        return
    plan=json.loads((FOLDER/'deployment-plan.json').read_text(encoding='utf-8'))['record']
    if plan['definition']!=definition():
        raise ValueError('Reviewed alert definition changed')
    if args.stage=='deploy':
        if request('GET',PATH,missing=True) is not None:
            raise ValueError('Alert exists; use verification, never blind redispatch')
        save('dispatch-intent',dict(checked_at=datetime.now(timezone.utc).isoformat(),definition=definition()))
        request('PUT',PATH,data=definition())
    current=request('GET',PATH)
    verify(current)
    live=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',metadata_findings_query())
    save('deployment-verified',dict(checked_at=datetime.now(timezone.utc).isoformat(),
        alert=current['id'],properties=current['properties'],query_sha256=qualification['record']['query_sha256'],
        live_issues=live,notification_delivery_tested=False,programme_complete=False,
        application_or_job_changes=False,source_data_changed=False))
    print(json.dumps(dict(deployed=True,verified=True,live_issues=len(live),notification_delivery_tested=False)),flush=True)


if __name__=='__main__':
    main()
