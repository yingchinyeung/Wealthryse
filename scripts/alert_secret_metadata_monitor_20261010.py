"""One owner-routed metadata hygiene/watchdog rule; no credential contents."""
from datetime import datetime,timedelta,timezone
import hashlib,json,sys
from pathlib import Path
import control_secret_metadata_monitor_20261010 as c

RULE='ibkr-v2-secret-metadata-findings'
PATH=c.rid('Microsoft.Insights/scheduledQueryRules',RULE)+'?api-version=2023-12-01'
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'
OUT=c.OUT/'alert-v3'


def query(source='ContainerAppConsoleLogs_CL'):
    prefix=c.monitor.PREFIX
    return """let Latest = SOURCE
| where TimeGenerated > ago(2d)
| where ContainerGroupName_s startswith 'ibkrv2stg-secret-metadata-'
| where Log_s startswith 'PREFIX'
| extend E=parse_json(substring(Log_s, PREFIX_LENGTH))
| top 1 by TimeGenerated desc;
union
(Latest
| extend Stamp=todatetime(E.checked_at), MissingVaults=set_difference(dynamic(VAULTS), pack_array(tostring(E.vaults[0].vault),tostring(E.vaults[1].vault),tostring(E.vaults[2].vault)))
| extend Invalid=isempty(tostring(E.schema)) or tostring(E.schema) != 'wealthryse-secret-metadata-v1' or tostring(E.version) != '20261010-v1'
    or tostring(E.kind) != 'complete' or isnull(toint(E.vault_count)) or toint(E.vault_count) != 3 or array_length(E.vaults) != 3 or array_length(MissingVaults) != 0
    or isnull(Stamp) or Stamp > now()+5m or TimeGenerated > now()+5m
    or isnull(toint(E.secret_values_read)) or toint(E.secret_values_read) != 0
    or isnull(toint(E.secret_values_changed)) or toint(E.secret_values_changed) != 0
    or isnull(toint(E.expiry_changes)) or toint(E.expiry_changes) != 0
    or isnull(toint(E.permissions_changed)) or toint(E.permissions_changed) != 0
    or isnull(toint(E.finding_count)) or toint(E.finding_count) < 0 or isnull(array_length(E.errors)) or array_length(E.errors) != 0
| extend Issue=case(Invalid, 'Incomplete or invalid metadata monitor evidence', TimeGenerated < ago(26h) or Stamp < ago(26h), 'No fresh metadata check within 26 hours', toint(E.finding_count)>0, 'Secret ownership expiry or age metadata needs review', '')
| where isnotempty(Issue)
| project Scope='secret-metadata',Issue,FindingCount=tolong(E.finding_count),LastObserved=TimeGenerated),
(print Scope='secret-metadata',Issue='No metadata monitor evidence in two days',FindingCount=long(0),LastObserved=datetime(null)
| where toscalar(Latest | count)==0)
""".replace('SOURCE',source).replace('PREFIX_LENGTH',str(len(prefix))).replace('PREFIX',prefix).replace('VAULTS',json.dumps(list(c.monitor.VAULTS)))


def definition():
    return {'location':'eastasia','kind':'LogAlert','tags':c.TAGS,
        'properties':{'displayName':'Credential metadata needs review','description':'Metadata-only expiry ownership age review and monitor-liveness alert. No secret values, no automatic expiry or rotation. Age threshold requests review only.',
            'enabled':True,'severity':2,'scopes':[c.BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs'],
            'evaluationFrequency':'PT15M','windowSize':'PT15M','overrideQueryTimeRange':'P2D','autoMitigate':True,'skipQueryValidation':False,
            'criteria':{'allOf':[{'query':query(),'timeAggregation':'Count','operator':'GreaterThan','threshold':0,
                'dimensions':[{'name':'Scope','operator':'Include','values':['*']}],
                'failingPeriods':{'numberOfEvaluationPeriods':1,'minFailingPeriodsToAlert':1}}]},
            'actions':{'actionGroups':[c.BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts']}}}


def verify(live):
    expected=definition()
    if live['id'].lower()!=PATH.split('?')[0].lower() or live.get('tags')!=expected['tags']:raise ValueError('Unexpected alert identity')
    for key,value in expected['properties'].items():
        actual=live['properties'].get(key)
        if key=='scopes':
            if [x.lower() for x in actual]!=[x.lower() for x in value]:raise ValueError('Alert workspace scope changed')
        elif key=='actions':
            if [x.lower() for x in actual['actionGroups']]!=[x.lower() for x in value['actionGroups']]:raise ValueError('Alert destination changed')
        elif actual!=value:raise ValueError('Alert property differs: '+key)


def cases():
    now=datetime.now(timezone.utc);base={'schema':c.monitor.SCHEMA,'version':c.monitor.VERSION,'checked_at':now.isoformat(),'kind':'complete','vault_count':3,
        'vaults':[{'vault':v} for v in c.monitor.VAULTS],'errors':[],'finding_count':0,'secret_values_read':0,'secret_values_changed':0,'expiry_changes':0,'permissions_changed':0}
    yield 'healthy',base,now,0
    yield 'empty',None,now,1
    yield 'stale',dict(base,checked_at=(now-timedelta(hours=27)).isoformat()),now-timedelta(hours=27),1
    yield 'stale_payload_recent_ingestion',dict(base,checked_at=(now-timedelta(hours=27)).isoformat()),now,1
    yield 'future',dict(base,checked_at=(now+timedelta(hours=1)).isoformat()),now,1
    yield 'findings',dict(base,finding_count=2),now,1
    yield 'partial',dict(base,vault_count=2,vaults=base['vaults'][:2]),now,1
    yield 'wrong_vault',dict(base,vaults=[{'vault':'other'}]*3),now,1
    yield 'failed',dict(base,kind='failed',errors=[{'error_type':'RuntimeError'}]),now,1
    yield 'malformed',{'schema':'invalid'},now,1
    for key in ('schema','version','checked_at','vault_count','vaults','errors','finding_count','secret_values_read','secret_values_changed','expiry_changes','permissions_changed'):
        changed=dict(base);del changed[key];yield 'missing_'+key,changed,now,1
    for key in ('secret_values_read','secret_values_changed','expiry_changes','permissions_changed'):
        yield 'nonzero_'+key,dict(base,**{key:1}),now,1
    yield 'negative_count',dict(base,finding_count=-1),now,1


def main(stage):
    if stage=='qualify':
        if OUT.exists():raise ValueError('Preserve prior alert qualification')
        results=[]
        for name,evidence,stamp,count in cases():
            source='datatable(TimeGenerated:datetime,ContainerGroupName_s:string,Log_s:string)['
            if evidence is not None:source+='datetime('+stamp.isoformat()+'),"ibkrv2stg-secret-metadata-synthetic",'+json.dumps(c.monitor.PREFIX+json.dumps(evidence))
            source+=']'
            rows=c.az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',query(source))
            if len(rows)!=count:raise ValueError('Fault qualification failed: '+name)
            if any(set(row)-{'Scope','Issue','FindingCount','LastObserved','TableName'} or not {'Scope','Issue','FindingCount','LastObserved'}<=set(row) for row in rows):raise ValueError('Alert output schema differs: '+name)
            if name=='findings' and int(rows[0]['FindingCount'])!=2:raise ValueError('Finding count projection changed')
            results.append({'case':name,'expected':count,'actual':len(rows),'passed':True})
        c.save(OUT,'qualification',{'passed':True,'checked_at':datetime.now(timezone.utc).isoformat(),'cases':results,'query_sha256':hashlib.sha256(query().encode()).hexdigest()})
        print(json.dumps({'query_cases_passed':len(results)}));return
    qualification=c.protected(OUT/'qualification.json')
    if qualification.get('passed') is not True or len(qualification['cases'])!=26 or qualification['query_sha256']!=hashlib.sha256(query().encode()).hexdigest():raise ValueError('Exact fault-qualified query required')
    runtime=c.protected(c.OUT/'runtime-verified.json');schedule=c.protected(c.OUT/'schedule-verified.json')
    if not runtime['passed'] or not schedule['passed']:raise ValueError('Monitor not accepted')
    if stage=='plan':
        if c.request('GET',PATH,missing=True) is not None:raise ValueError('Existing alert must not be overwritten')
        group=c.request('GET',c.BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts?api-version=2023-01-01')['properties']
        emails=group.get('emailReceivers',[])
        if group.get('enabled') is not True or len(emails)!=1 or emails[0].get('emailAddress')!='anthony.ying@wealthryse.ai' or emails[0].get('status')!='Enabled' or any(v for k,v in group.items() if k.endswith('Receivers') and k!='emailReceivers'):raise ValueError('Previously approved owner-only route required')
        live=c.az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',query())
        if len(live)!=1 or live[0]['Issue']!='Secret ownership expiry or age metadata needs review' or int(live[0]['FindingCount'])!=runtime['result']['finding_count']:raise ValueError('Current metadata findings do not match retained inventory')
        c.save(OUT,'plan',{'definition':definition(),'qualification_receipt':qualification['protected_receipt'],'runtime_receipt':runtime['protected_receipt'],
            'known_findings':live,'owner_route_sha256':c.fingerprint(group),'notification_note':'Real metadata findings may notify the existing owner route. No new recipients or repeated synthetic test.'})
        print('Alert plan retained with current metadata findings; no secret values in notification');return
    plan=c.protected(OUT/'plan.json')
    if plan['definition']!=definition():raise ValueError('Alert definition drift')
    if stage=='apply':
        if c.request('GET',PATH,missing=True) is not None or (OUT/'apply-intent.json').exists():raise ValueError('Reconcile existing alert')
        group=c.request('GET',c.BASE+'Microsoft.Insights/actionGroups/ibkr-v11-daily-alerts?api-version=2023-01-01')['properties']
        if c.fingerprint(group)!=plan['owner_route_sha256']:raise ValueError('Alert recipient drift')
        c.save(OUT,'apply-intent',{'definition':definition(),'plan_receipt':plan['protected_receipt']});ack=c.request('PUT',PATH,data=definition())
        c.save(OUT,'apply-ack',{'id':ack['id']});print('Owner-routed metadata alert created');return
    if stage=='verify':
        live=c.request('GET',PATH);verify(live)
        if not (OUT/'verified.json').exists():c.save(OUT,'verified',{'passed':True,'id':live['id'],'definition':live['properties'],
            'actual_alert_delivery_observed':False,'human_inbox_receipt_confirmed':False,'source_data_changed':False})
        print('Metadata hygiene and liveness alert definition verified; actual evaluation/delivery still pending');return
    raise ValueError('Unknown stage')


if __name__=='__main__':main(sys.argv[1])
