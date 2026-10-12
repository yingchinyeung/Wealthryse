"""Create one scoped read-only workbook; no existing workbook is overwritten.

Unknown creation outcomes require exact readback, never a second PUT. The
snapshot is explicitly dated and the live billing link uses Azure's own UI.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import uuid

from deploy_metadata_monitor import request
from run_specialist_gateway_read_verification import retain
from ibkr_etl import resource_cost_workbook as policy

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/system-improvements-20261006/cost-workbook-v1'
SOURCES=('src/ibkr_etl/resource_cost_workbook.py','tests/test_resource_cost_workbook.py',
         'scripts/deploy_resource_cost_workbook.py')


def read(name):
    value=json.loads((FOLDER/(name+'.json')).read_text(encoding='utf-8'))
    if value['protected_receipt']['protected_readback_verified'] is not True:
        raise ValueError('Protected evidence required')
    return value['record']


def save(name,record):
    path=FOLDER/(name+'.json')
    if path.exists():raise ValueError('Recorded outcome exists; reconcile without repeating')
    receipt=retain(record,'resource-cost-workbook-'+name+'-20261006')
    with path.open('x',encoding='utf-8') as handle:
        json.dump({'record':record,'protected_receipt':receipt},handle,indent=2)


def inventory():
    path=policy.SCOPE+'/resources?api-version=2021-04-01';seen=set();rows=[];ids=set()
    while path:
        if path in seen or len(seen)>=50:raise ValueError('Inventory continuation incomplete')
        seen.add(path);page=request('GET',path)
        for item in page.get('value',[]):
            rid=item['id'].lower()
            if not rid.startswith(policy.SCOPE.lower()+'/providers/') or rid in ids:
                raise ValueError('Inventory scope or duplicate conflict')
            ids.add(rid)
            rows.append({k:item.get(k) for k in ('id','name','type','location')} | {
                'tags':{k:item.get('tags',{}).get(k) for k in ('owner','application','purpose')}})
        next_link=page.get('nextLink')
        if next_link:
            link=urlparse(next_link)
            if link.scheme!='https' or link.netloc!='management.azure.com' or link.path.lower()!=(policy.SCOPE+'/resources').lower():
                raise ValueError('Inventory continuation outside scope')
            path=link.path+'?'+link.query
        else:path=None
    return {'captured_at':datetime.now(timezone.utc).isoformat(),'resources':rows,
        'complete_pagination':True,'pages':len(seen),'tag_keys':['owner','application','purpose']}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','deploy','verify'))
    args=parser.parse_args()
    if args.stage=='prepare':
        FOLDER.mkdir()
        value=json.loads((ROOT/'.work/system-improvements-20261006/cost-observation-v1/result.json').read_text(encoding='utf-8'))
        if value['protected_receipt']['protected_readback_verified'] is not True:
            raise ValueError('Protected original billing evidence required')
        summary=policy.qualify_cost(value['record'],now=datetime.now(timezone.utc))
        group=request('GET',policy.SCOPE+'?api-version=2021-04-01')
        name=str(uuid.uuid4());rid=policy.SCOPE+'/providers/Microsoft.Insights/workbooks/'+name
        if request('GET',rid+'?api-version=2021-08-01',missing=True):
            raise ValueError('Fresh new workbook name required')
        save('plan',{'prepared_at':datetime.now(timezone.utc).isoformat(),'resource_id':rid,
            'definition':policy.workbook(summary,location=group['location']),
            'qualified_snapshot':summary,'billing_receipt':value['protected_receipt'],
            'inventory':inventory(),'source_files_sha256':{
                name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES},
            'grants_changed':False,'identity_created':False,'existing_resources_changed':False,
            'portfolio_data_read':False,'automatic_billing_refresh':False})
        print(json.dumps({'prepared':True,'resource_id':rid,'cost_snapshot':summary}));return
    plan=read('plan');rid=plan['resource_id']
    for name,wanted in plan['source_files_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=wanted:
            raise ValueError('Qualified workbook source changed')
    if args.stage=='verify':
        current=request('GET',rid+'?api-version=2021-08-01&canFetchContent=true')
        policy.verify_workbook(current,plan['definition'],rid)
        checked=inventory()
        save('verified',{'checked_at':datetime.now(timezone.utc).isoformat(),'resource_id':rid,
            'definition_readback_verified':True,'inventory':checked,'native_resource':current,
            'portfolio_data_read':False,'grants_changed':False,'render_acceptance':'pending'})
        print(json.dumps({'verified':True,'resource_id':rid,'resources':len(checked['resources'])}));return
    if (FOLDER/'creation-intent.json').exists():raise ValueError('Creation attempted; verify only, never redispatch')
    regression_path=ROOT/'.work/system-improvements-20261006/monitor-summary-regression-v2/full-regression.json'
    regression=json.loads(regression_path.read_text(encoding='utf-8'))
    if regression['passed'] is not True or regression['failures'] or regression['errors'] or regression['source_changes_during_run']:
        raise ValueError('Passing no-drift full regression required')
    for name,wanted in regression['source_files_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=wanted:
            raise ValueError('Source changed since full regression')
    if any(regression['source_files_sha256'].get(name)!=wanted for name,wanted in plan['source_files_sha256'].items()):
        raise ValueError('Workbook source differs from the full regression')
    if (datetime.now(timezone.utc)-datetime.fromisoformat(plan['qualified_snapshot']['captured_at'])).total_seconds()>7200:
        raise ValueError('Initial snapshot stale; do not create workbook')
    if request('GET',rid+'?api-version=2021-08-01',missing=True):
        raise ValueError('Target exists; do not overwrite')
    save('creation-intent',{'resource_id':rid,'definition':plan['definition'],
        'regression':regression,'regression_log':regression_path.with_name('full-regression.log').read_text(encoding='utf-8'),
        'existing_resources_changed':False,'grants_changed':False,'portfolio_data_changed':False})
    request('PUT',rid+'?api-version=2021-08-01',data=plan['definition'])
    print(json.dumps({'submitted':True,'next':'verify','resource_id':rid}))


if __name__=='__main__':main()
