"""Reconcile interrupted requests; never resubmit an unknown job start."""
from datetime import datetime
import hashlib,json
import control_upgrade_readers_20261010 as r
import control_upgrade_authority_check_20261010 as a


def main():
    job='ibkrv2stg-observe-reader';folder=r.OUT/(job+'-installed')
    intent=r.protected(folder/'intent.json');time=datetime.fromisoformat(intent['protected_receipt']['version_id'].replace('Z','+00:00'))
    runs=r.request('GET',r.rid('Microsoft.App/jobs',job)+'/executions'+r.API)
    if runs.get('nextLink'):raise ValueError('Unbounded reconciliation page')
    matches=[]
    for item in runs['value']:
        start=datetime.fromisoformat(item['properties']['startTime'].replace('Z','+00:00'))
        if not 0<=(start-time).total_seconds()<=120:continue
        run=r.request('GET',r.rid('Microsoft.App/jobs',job)+'/executions/'+item['name']+r.API)
        actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
        if any(actual.get(k)!=expected.get(k) for k in ('image','command','args')):raise ValueError('Contemporaneous execution differs')
        r.execution_env(job,actual.get('env',[]),expected.get('env',[]));matches.append(item['name'])
    if len(matches)!=1:raise ValueError('Unique exact observation execution not proven')
    if not (folder/'dispatch.json').exists():r.save(folder,'dispatch',{'execution':matches[0],'reconciled_after_connection_reset':True,'duplicate_start_submitted':False})
    empty=a.OUT/'fallback/intent.json'
    if empty.read_bytes()!=b'' or (a.OUT/'fallback/dispatch.json').exists():raise ValueError('Expected pre-dispatch archive failure only')
    _,args,_=a.build_args();runs=r.request('GET',a.PATH+'/executions'+a.API)
    if runs.get('nextLink'):raise ValueError('Incomplete authority execution inventory')
    for item in runs['value']:
        run=r.request('GET',a.PATH+'/executions/'+item['name']+a.API)
        c=run['properties']['template']['containers'][0]
        if c['image']==r.SOURCE and c.get('args')==args:raise ValueError('Compatible fallback was started; reconcile it instead')
    if not (a.OUT/'fallback-pre-dispatch-interruption.json').exists():r.save(a.OUT,'fallback-pre-dispatch-interruption',{
        'empty_intent_sha256':hashlib.sha256(b'').hexdigest(),'old_artifact_preserved':True,'compatible_fallback_execution_absent':True,
        'failure_stage':'Protected intent archive before POST; no start reached for this request','checked_executions':len(runs['value']),
        'safe_next_attempt_folder':'fallback-v2','production_mutation_repeated':False})
    print(json.dumps({'observation_reconciled':matches[0],'authority_fallback_not_dispatched':True}))


if __name__=='__main__':main()
