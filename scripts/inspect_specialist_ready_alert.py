"""Read one bounded informational alert; never claim mailbox delivery from it."""
import json
from urllib.parse import urlencode
from deploy_metadata_monitor import request,BASE
from deploy_specialist_report_worker import ROOT
from ibkr_etl.agent_report_notifications import RULE
from run_specialist_gateway_read_verification import retain


def main():
    folder=ROOT/'.work/specialist-notification-enable-20261003'
    accepted=json.loads((folder/'acceptance.json').read_text())
    if accepted.get('status')!='passed' or accepted.get('notification_rule_enabled') is not True:
        raise ValueError('Accepted notification activation required')
    scope=BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
    # Azure represents alertRule as either a name or an ARM ID depending on the
    # monitor. Use one bounded workspace inventory, then exact local matching,
    # rather than guessing filter variants or fetching financial context.
    query=urlencode(dict(**{'api-version':'2019-03-01'},timeRange='1h',
        pageCount=25,includeContext='false',includeEgressConfig='false'))
    response=request('GET',scope+'/providers/Microsoft.AlertsManagement/alerts?'+query)
    rows=[]
    for item in response.get('value',[]):
        value=item['properties']['essentials']
        if str(value.get('alertRule','')).lower() not in (RULE.lower(),(BASE+'Microsoft.Insights/scheduledQueryRules/'+RULE).lower()):
            continue
        rows.append(dict(alert_id=item['id'],rule=value['alertRule'],severity=value.get('severity'),
            condition=value.get('monitorCondition'),started_at=value.get('startDateTime'),
            action_status=value.get('actionStatus'),recipient_delivery_confirmed=False))
    record=dict(notification_rule_enabled=True,alerts=rows,recipient_delivery_confirmed=False,
        full_report_emailed=False,query_scope='bounded registered workspace, exact local rule match and last hour',
        additional_pages_not_loaded=bool(response.get('nextLink')))
    if rows:
        record['protected_receipt']=retain(record,'ready-alert-observed')
        (folder/'alert-observation.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record))


if __name__=='__main__':main()
