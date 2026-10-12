"""Read an actual scheduled execution and alert state; never trigger a test."""
from datetime import datetime,timezone
import json,sys
from urllib.parse import urlencode
import control_secret_metadata_monitor_20261010 as c
import alert_secret_metadata_monitor_20261010 as a


def main(name):
    suffix=name.removeprefix(c.JOB+'-')
    if not name.startswith(c.JOB+'-') or not suffix.isdigit():raise ValueError('Scheduled execution name required')
    job=c.request('GET',c.JOBPATH+c.API)
    c.verify_job(job,c.request('GET',c.IDENTITY+'?api-version=2023-01-31'),True)
    run=c.request('GET',c.JOBPATH+'/executions/'+name+c.API);p=run['properties']
    stamp=datetime.fromisoformat(p['startTime'].replace('Z','+00:00'))
    if stamp.hour!=6 or stamp.minute!=15 or stamp.second or int(stamp.timestamp()//60)!=int(suffix):raise ValueError('Not the accepted cron cadence')
    if p['status']!='Succeeded':raise ValueError('Scheduled run did not succeed')
    actual=p['template']['containers'][0];wanted=job['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args','env')):raise ValueError('Scheduled payload changed')
    prefix=c.monitor.PREFIX
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows=c.az('monitor','log-analytics','query','-w',a.WORKSPACE,'--analytics-query',query)
    if len(rows)!=1:raise ValueError('One complete scheduled record required')
    proof=json.loads(rows[0]['Result'])
    if proof.get('kind')!='complete' or proof.get('schema')!=c.monitor.SCHEMA or proof.get('version')!=c.monitor.VERSION or proof.get('vault_count')!=3 or len(proof['vaults'])!=3 or {v['vault'] for v in proof['vaults']}!=set(c.monitor.VAULTS) or proof.get('errors')!=[]:raise ValueError('Incomplete scheduled evidence')
    for k in ('secret_values_read','secret_values_changed','expiry_changes','permissions_changed'):
        if type(proof.get(k)) is not int or proof[k]!=0:raise ValueError('Metadata-only boundary exceeded')
    a.verify(c.request('GET',a.PATH))
    scope=c.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'
    response=c.request('GET',scope+'/providers/Microsoft.AlertsManagement/alerts?'+urlencode({'api-version':'2019-03-01','timeRange':'1d','pageCount':100,'includeContext':'false','includeEgressConfig':'false'}))
    observed=[]
    for item in response.get('value',[]):
        e=item['properties']['essentials']
        if str(e.get('alertRule','')).lower() in (a.RULE.lower(),a.PATH.split('?')[0].lower()):
            observed.append({'id':item['id'],'condition':e.get('monitorCondition'),'started_at':e.get('startDateTime'),
                'action_status':e.get('actionStatus'),'human_delivery_confirmed':False})
    record={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'execution':name,'started_at':stamp.isoformat(),
        'image':c.IMAGE,'scheduled_payload_verified':True,'proof':proof,'alerts':observed,
        'additional_alert_pages_not_loaded':bool(response.get('nextLink')),'human_delivery_confirmed':False,
        'new_manual_execution_started':False,'new_notification_requested':False,'whole_platform_cycle_complete':False,'burn_in_complete':False}
    c.save(c.OUT/'scheduled-observation-v1','result',record)
    print(json.dumps({'scheduled_execution':name,'passed':True,'metadata_findings':proof['finding_count'],'matching_alerts':len(observed),'human_delivery_confirmed':False}))


if __name__=='__main__':main(sys.argv[1])
