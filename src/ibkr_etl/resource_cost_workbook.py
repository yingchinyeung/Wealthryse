"""Read-only resource ownership workbook with a qualified billing snapshot.

Current costs remain in Azure's native Cost Analysis view, which understands
its response headers, pagination and billing periods. A frozen snapshot is
never silently described as live and currencies are never added together.
"""
from datetime import datetime, timezone
from decimal import Decimal
import html
import json
import re

from . import medallion_tags

TAGS_KEY='resource-cost-visibility'

SUB='3956e821-cee4-403f-8708-e84e2ed490eb'
SCOPE=f'/subscriptions/{SUB}/resourceGroups/rg-ibkr-database-v11'
DISPLAY='Portfolio platform — resource ownership and costs'
COST_URL='https://portal.azure.com/#@wealthryse.ai/resource'+SCOPE+'/costanalysis'
SCHEMA={'PreTaxCost':'Number','UsageDate':'Number','ResourceId':'String',
        'ServiceName':'String','Currency':'String'}


def qualify_cost(record, *, now):
    if now.tzinfo is None or record.get('scope','').lower()!=SCOPE.lower() or record.get('complete_pagination') is not True:
        raise ValueError('Complete billing evidence for the exact resource group required')
    captured=datetime.fromisoformat(record['captured_at'])
    if captured.tzinfo is None or not 0<=(now-captured).total_seconds()<=7200:
        raise ValueError('Recent aware billing snapshot required')
    query=record.get('query',{})
    expected={'type':'ActualCost','timeframe':'MonthToDate','dataset':{
        'granularity':'Daily','aggregation':{'totalCost':{'name':'PreTaxCost','function':'Sum'}},
        'grouping':[{'name':'ResourceId','type':'Dimension'},{'name':'ServiceName','type':'Dimension'}]}}
    if query!=expected:raise ValueError('Exact reviewed billing period and aggregation required')
    pages=record.get('pages',[])
    if not pages:raise ValueError('No dataset is not evidence of zero cost')
    totals={}; by_service={}; keys=set(); count=0; schema=None
    for index,page in enumerate(pages):
        props=page.get('properties',{})
        columns=props.get('columns',[])
        names=[c.get('name') for c in columns]
        if len(names)!=len(SCHEMA) or len(set(names))!=len(names) or {c.get('name'):c.get('type') for c in columns}!=SCHEMA:
            raise ValueError('Billing response schema differs')
        if schema is not None and columns!=schema:raise ValueError('Billing schemas differ across pages')
        schema=columns
        if bool(props.get('nextLink'))!=(index<len(pages)-1):
            raise ValueError('Billing continuation is not complete')
        if not isinstance(props.get('rows'),list):raise ValueError('Billing rows required')
        for values in props['rows']:
            if not isinstance(values,list) or len(values)!=len(names):raise ValueError('Invalid billing row width')
            row=dict(zip(names,values)); cost=row['PreTaxCost']; currency=row['Currency']
            if type(cost) not in (int,float) or not Decimal(str(cost)).is_finite():
                raise ValueError('Finite numeric billing amount required')
            if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency):
                raise ValueError('Explicit billing currency required')
            day=row['UsageDate']
            if type(day) is not int:raise ValueError('Billing date must be an actual calendar date')
            try:date=datetime.strptime(str(day),'%Y%m%d').date()
            except ValueError:raise ValueError('Invalid billing date') from None
            if date.strftime('%Y%m')!=captured.astimezone(timezone.utc).strftime('%Y%m') or date>captured.astimezone(timezone.utc).date():
                raise ValueError('Billing row falls outside the qualified month-to-date period')
            resource=row['ResourceId']; service=row['ServiceName']
            if not isinstance(resource,str) or (resource and not resource.lower().startswith(SCOPE.lower()+'/providers/')):
                raise ValueError('Billing resource outside approved scope')
            if not isinstance(service,str) or not service.strip() or len(service)>200:
                raise ValueError('Bounded billing service required')
            key=(day,resource.lower(),service,currency)
            if key in keys:raise ValueError('Duplicate grouped billing row')
            keys.add(key);count+=1;amount=Decimal(str(cost))
            totals[currency]=totals.get(currency,Decimal(0))+amount
            group=(currency,service);by_service[group]=by_service.get(group,Decimal(0))+amount
    if count!=record.get('rows') or type(record.get('rows')) is not int:
        raise ValueError('Billing count differs from retained evidence')
    if sorted(totals)!=record.get('currencies'):raise ValueError('Billing currencies differ from retained evidence')
    return {'captured_at':captured.isoformat(),'rows':count,'scope':SCOPE,'period':'MonthToDate',
        'totals':{k:str(v) for k,v in sorted(totals.items())},
        'by_service':[{'currency':k[0],'service':k[1],'amount':str(v)} for k,v in sorted(by_service.items())],
        'zero_spend_certified':False,'live':False,'financial_reconciliation':False}


def escape(value):
    return html.escape(str(value)).replace('|','&#124;').replace('\n',' ').replace('\r',' ')


def workbook(summary, *, location):
    if summary.get('scope')!=SCOPE or summary.get('live') is not False:
        raise ValueError('Qualified scoped snapshot required')
    if location not in {'eastasia','East Asia'}:raise ValueError('Exact resource-group region required')
    total='; '.join(escape(currency)+' '+format(Decimal(amount),',.2f') for currency,amount in summary['totals'].items()) or 'No billing rows returned — spend unknown'
    text=('# Portfolio platform: ownership and costs\n\n'
        '[Open live Azure Cost Analysis]('+COST_URL+')\n\n'
        '## Month-to-date billing snapshot — not live\n\n'
        'Captured '+escape(summary['captured_at'])+'; '+str(summary['rows'])+' grouped daily rows.\n\n'
        'Actual pre-tax cost: **'+total+'**. Billing can be delayed; this is not a final invoice, forecast or enforced spend cap. '
        'Currencies are kept separate. Retired resources can still have historical charges.\n\n'
        '| Service | Currency | Snapshot cost |\n| --- | --- | ---: |\n')
    for row in summary['by_service']:
        text+='| '+escape(row['service'])+' | '+escape(row['currency'])+' | '+format(Decimal(row['amount']),',.2f')+' |\n'
    endpoint={'version':'ARMEndpoint/1.0','data':None,'headers':[],
        'method':'GETARRAY','path':SCOPE+'/resources','urlParams':[{'key':'api-version','value':'2021-04-01'}],
        'batchDisabled':False,'transformers':[{'type':'jsonpath','settings':{'columns':[
            {'path':path,'columnid':name,'columnType':'string'} for name,path in (
                ('Resource','$.name'),('Type','$.type'),('Region','$.location'),
                ('Owner','$.tags.owner'),('Application','$.tags.application'),
                ('Purpose','$.tags.purpose'),('ResourceId','$.id'))]}}]}
    notebook={'version':'Notebook/1.0','isLocked':False,'fallbackResourceIds':[SCOPE],'items':[
        {'type':1,'content':{'json':text},'name':'billing-snapshot'},
        {'type':1,'content':{'json':'## Live resource ownership inventory\n\nRefresh this table to read current Azure resource metadata. Blank owner/application/purpose fields indicate missing tags, not unused resources. No tags or permissions are changed. Only these three tag keys are displayed.'},'name':'inventory-guide'},
        {'type':3,'content':{'version':'KqlItem/1.0','query':json.dumps(endpoint,separators=(',',':')),
            'queryType':12,'size':0,'title':'Resource ownership (read-only)',
            'showRefreshButton':True,'gridSettings':{'filter':True},
            'noDataMessage':'Inventory unavailable or empty — inspect query status; do not assume all resources were retired.'},'name':'ownership-inventory'}]}
    return {'location':location,'kind':'shared',
        'tags':medallion_tags.with_medallion(TAGS_KEY,{'application':'ibkr-portfolio-platform',
            'purpose':'resource-cost-visibility','owner':'portfolio-platform'}),
        'properties':{'displayName':DISPLAY,'serializedData':json.dumps(notebook,ensure_ascii=False,allow_nan=False),
            'version':'1.0','sourceId':SCOPE,'category':'workbook',
            'description':'Read-only ownership metadata and qualified cost snapshot. Live costs use native Cost Analysis.'}}


def verify_workbook(actual,expected,resource_id):
    if actual.get('id','').lower()!=resource_id.lower() or actual.get('identity'):
        raise ValueError('Unexpected workbook identity or target')
    for key in ('location','kind'):
        if actual.get(key)!=expected[key]:raise ValueError('Workbook metadata differs')
    # Azure adds this title tag to the stored workbook. Accept only its exact
    # existing display name, not arbitrary additional tags or a changed title.
    tags=actual.get('tags')
    if not isinstance(tags,dict):raise ValueError('Workbook tags required')
    tags=dict(tags)
    if 'hidden-title' in tags:
        if tags.pop('hidden-title')!=expected['properties']['displayName']:
            raise ValueError('Native workbook title tag differs')
    # The Medallion tags (layer, component) may be missing still, or there with exactly their values.
    if not medallion_tags.same_tags(TAGS_KEY,tags,expected['tags']):raise ValueError('Workbook tags differ')
    for key,value in expected['properties'].items():
        observed=actual.get('properties',{}).get(key)
        if key=='sourceId':matches=isinstance(observed,str) and observed.lower()==value.lower()
        elif key=='serializedData':matches=isinstance(observed,str) and json.loads(observed)==json.loads(value)
        else:matches=observed==value
        if not matches:raise ValueError('Workbook content or scope differs')
