"""One bounded cache repair with the installed risk image; no sources or reports changed."""
import argparse,copy,json
import run_ms_website_input_probe as p
from read_ms_website_input_probe import verify_env
FOLDER=p.ROOT/'.work/viewer-report-investigation-20261008/risk-repair-v1'
NAME='ibkrv2stg-risk-snapshots'
PATH=p.release.BASE+'Microsoft.App/jobs/'+NAME
def idle():
    data=p.request('GET',PATH+'/executions'+p.release.API)
    if data.get('nextLink'):raise ValueError('Paginated execution inventory requires review')
    if any(r['properties']['status'] in ('Running','Processing','Pending') for r in data['value']):raise ValueError('Risk worker currently busy')
def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','dispatch','inspect'));stage=parser.parse_args().stage
    job=p.request('GET',PATH+p.release.API)
    if job['name']!=NAME or len(job['properties']['template']['containers'])!=1 or job['properties']['configuration']['replicaRetryLimit']!=0:raise ValueError('Risk job scope differs')
    if stage=='prepare':
        if FOLDER.exists():raise ValueError('Preserve prior intent')
        idle();template=copy.deepcopy(job['properties']['template'])
        container=template['containers'][0]
        if container['command']!=['python'] or container['args']!=['scripts/build_risk_snapshots.py']:raise ValueError('Installed risk command differs')
        container['args']+=['--history-audit-limit','100','--history-rebuild-limit','100']
        def clean(value):
            if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if v is not None}
            if isinstance(value,list):return [clean(v) for v in value]
            return value
        template=clean(template)
        if not template.get('volumes'):template.pop('volumes',None)
        for entry in template['containers'][0].get('env',[]):
            if entry.get('secretRef') and entry.get('value')=='':entry.pop('value')
        record={'template':template,'job_settings_sha256':p.release.settings_invariant(job),'authorization':'Owner requested fix all confirmed resource group and viewer issues; repair up to 100 disposable historical caches with current installed risk image','source_data_writes':0,'report_changes':False,'historical_cache_rebuild_limit':100}
        FOLDER.mkdir();p.release.build.save(FOLDER,'intent',{**record,'protected_receipt':p.retain(record,'viewer-risk-backlog-repair')});print('Prepared bounded cache repair');return
    intent=p.release.read(FOLDER,'intent');p.verify_protected(intent)
    if p.release.settings_invariant(job)!=intent['job_settings_sha256']:raise ValueError('Persisted risk job changed')
    if job['properties']['template']['containers'][0]['image']!=intent['template']['containers'][0]['image']:raise ValueError('Persisted risk job image changed')
    if stage=='dispatch':
        if (FOLDER/'dispatch-intent.json').exists():raise ValueError('Inspect, never repeat dispatch')
        idle();p.release.build.save(FOLDER,'dispatch-intent',{'intent_sha256':p.sha(FOLDER/'intent.json')})
        ack=p.request('POST',PATH+'/start'+p.release.API,data=intent['template'])
        if not ack.get('name'):raise ValueError('Unknown dispatch; inspect')
        p.release.build.save(FOLDER,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    name=p.release.read(FOLDER,'dispatch')['execution'];run=p.request('GET',PATH+'/executions/'+name+p.release.API)
    print(json.dumps({'execution':name,'status':run['properties']['status']}))
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','name','command','args')):raise ValueError('Executed repair differs')
    verify_env(wanted['env'],actual['env'],job)
    if run['properties']['status']=='Succeeded' and (FOLDER/'summary.json').exists() and not (FOLDER/'qualified.json').exists():
        rows=json.loads((FOLDER/'summary.json').read_bytes())
        audits=[json.loads(r['historical']) for r in rows if r.get('historical') not in (None,'None')]
        if len(audits)!=1 or audits[0]['checked']!=100 or audits[0]['pending'] or audits[0]['blocked'] or not any(r.get('status')=='success' and r.get('blocked')=='[]' for r in rows):raise ValueError('Repair completion summary differs')
        record={'status':'passed','execution':name,'image':actual['image'],'historical_checked':100,'historical_rebuilt':audits[0]['rebuilt'],'historical_pending_in_checked_set':0,'persisted_settings_and_image_preserved':True,'source_data_changed':False,'report_changes':False,'summary_sha256':p.sha(FOLDER/'summary.json')}
        p.release.build.save(FOLDER,'qualified',{**record,'protected_receipt':p.retain(record,'viewer-risk-backlog-repair-qualified')});print(json.dumps(record))
if __name__=='__main__':main()
