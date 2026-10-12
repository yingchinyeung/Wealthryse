"""Ten-minute read-only clone connection observer with full-table postcheck."""
import json,sys,hashlib
from pathlib import Path
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
import deploy_t_runtime_release_20261009 as runtime
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage10'
OUT=ROOT/'legacy-failover-probe-v1';PATH=rid('Microsoft.App/jobs','ibkr-v11-daily');API='?api-version=2024-03-01'

def execution_matches(actual,wanted):
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):return False
    a={e['name']:e for e in actual.get('env',[])};w={e['name']:e for e in wanted.get('env',[])}
    if set(a)!=set(w):return False
    for name,e in w.items():
        if e.get('secretRef'):
            # Azure exposes this job's execution-local reference, not the saved
            # Key Vault reference. Never compare or export resolved secrets.
            if name!='DATABASE_URL' or e['secretRef']!='database-url' or a[name].get('secretRef') not in ('database-url','cappjob-ibkr-v11-daily') or a[name].get('value'):return False
        elif a[name].get('value')!=e.get('value') or a[name].get('secretRef'):return False
    return True

def source_code(baseline):
    source=Path(__file__).with_name('probe_upgrade_legacy_recovery_20261009.py').read_text(encoding='utf-8')
    marker='before=fingerprint(source);restored=fingerprint(target);after=fingerprint(source)'
    if source.count(marker)!=1:raise ValueError('Qualified fingerprint helper changed')
    prefix=source.split(marker)[0]
    return 'BASELINE='+repr(baseline)+'\n'+prefix+'''
events=[];deadline=time.monotonic()+600;announced=False
while time.monotonic()<deadline:
    point=time.monotonic()
    try:
        with psycopg.connect(target,row_factory=dict_row,connect_timeout=5,options='-c default_transaction_read_only=on -c statement_timeout=3000') as db:
            row=db.execute("SELECT pg_postmaster_start_time()::text AS boot, pg_is_in_recovery() AS replica").fetchone()
            if db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']!='on':raise ValueError('Observer must remain read-only')
            event={'t':round(point-started,3),'ok':True,'boot':row['boot'],'replica':row['replica']}
    except (psycopg.OperationalError,psycopg.InterfaceError) as error:
        event={'t':round(point-started,3),'ok':False,'error_type':type(error).__name__}
    event['duration']=round(time.monotonic()-point,3);events.append(event)
    if event['ok'] and not announced:
        print('LEGACY_FAILOVER_READY '+json.dumps({'read_only':True,'elapsed_seconds':event['t'],'duration_seconds':600,'clone_host':'ibkrv11-upgrade-recovery-1009.postgres.database.azure.com'}),flush=True);announced=True
    time.sleep(min(2,max(0,deadline-time.monotonic())))
after=fingerprint(target)
good=[e for e in events if e['ok']];bad=[e for e in events if not e['ok']]
gaps=[round(b['t']-a['t'],3) for a,b in zip(good,good[1:])]
passed=announced and len(good)>=2 and events[-1]['ok'] and after==BASELINE
print('LEGACY_FAILOVER_PROOF '+json.dumps({'passed':passed,'database_writes':0,'clone_only':True,'source_connected':False,'table_fingerprints_unchanged':after==BASELINE,'successful_samples':len(good),'failed_samples':len(bad),'distinct_postmaster_times':len({e['boot'] for e in good}),'largest_success_sample_gap_seconds':max(gaps,default=0),'sample_events_sha256':hashlib.sha256(json.dumps(events,sort_keys=True).encode()).hexdigest(),'first_sample':events[0],'last_sample':events[-1],'failure_samples':bad[:12],'after':after,'measurement_note':'Sampled reconnect gap, not a guaranteed RTO; actual failover must separately be confirmed from Azure zone transition. Compact proof prevents the 16KB log-line split.'}),flush=True)
if not passed:raise SystemExit(1)
'''

def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-legacy-failover-'+name)},f,indent=2)

def main(stage):
    job=request('GET',PATH+API)
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile existing observer')
        runtime.idle('ibkr-v11-daily')
        ha=protected(ROOT/'legacy-ha-rehearsal-v1/verify-ha-result.json')
        if not ha['passed']:raise ValueError('Healthy isolated HA required')
        clone=request('GET',ha['target']+'?api-version=2024-08-01')
        if clone['properties']['state']!='Ready' or clone['properties']['highAvailability']['state']!='Healthy':raise ValueError('Clone is not healthy')
        recovery=protected(ROOT/'legacy-recovery-probe-v1/result.json');image=protected(ROOT/'legacy-private-build-proof-v1/result.json')['pushed_image']
        code=source_code(recovery['proof']['restored']);compile(code,'bounded-clone-observer','exec')
        template=corrected_template(job,code);c=template['containers'][0];c['image']=image;c['env']=[e for e in c.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID')]
        if {e['name'] for e in c['env']}!={'DATABASE_URL','PORTFOLIO_TENANT_ID','AZURE_CLIENT_ID'}:raise ValueError('Existing credential references required')
        environment=request('GET',job['properties']['environmentId']+API)
        OUT.mkdir();save('intent',{'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'log_workspace_id':environment['properties']['appLogsConfiguration']['logAnalyticsConfiguration']['customerId'],'database_writes':0,'clone_only':True})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage not in ('ready','read'):raise ValueError('Unknown observer step')
    intent=protected(OUT/'intent.json');execution=protected(OUT/'dispatch.json')['execution']
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Saved legacy job changed')
    run=request('GET',PATH+'/executions/'+execution+API);status=run['properties']['status'];actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if not execution_matches(actual,wanted):raise ValueError('Actual observer differs')
    prefix='LEGACY_FAILOVER_READY ' if stage=='ready' else 'LEGACY_FAILOVER_PROOF '
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project TimeGenerated,Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
    rows=az('monitor','log-analytics','query','-w',intent['log_workspace_id'],'--analytics-query',query)
    print(json.dumps({'execution':execution,'status':status,'proof_rows':len(rows)}))
    if len(rows)!=1:return
    try:result=json.loads(rows[0]['Result'])
    except json.JSONDecodeError:
        # v1's single 34KB JSON line was split by Azure. Reassemble only a
        # uniquely valid JSON object from at most four exact retained chunks.
        if stage!='read' or OUT.name!='legacy-failover-probe-v1':raise
        import itertools
        q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s !startswith 'LEGACY_FAILOVER_READY ' | project Log_s | take 5"
        chunks=[r['Log_s'] for r in az('monitor','log-analytics','query','-w',intent['log_workspace_id'],'--analytics-query',q)]
        heads=[c for c in chunks if c.startswith(prefix)]
        if len(heads)!=1 or not 1<=len(chunks)<=4 or sum(map(len,chunks))>65536:raise ValueError('Observer log split outside bounded reconstruction')
        tail=[c for c in chunks if c is not heads[0]];valid=[]
        for order in itertools.permutations(tail):
            try:parsed=json.loads(heads[0][len(prefix):]+''.join(order))
            except json.JSONDecodeError:continue
            if isinstance(parsed,dict) and 'after' in parsed and 'events' in parsed:valid.append(parsed)
        if len(valid)!=1:raise ValueError('No unique exact observer log reconstruction')
        result=valid[0]
        if not (OUT/'log-reassembly.json').exists():save('log-reassembly',{'chunks':chunks,'unique_reassembly':True,'execution':execution})
    if stage=='ready':
        if status!='Running' or not result['read_only']:raise ValueError('Observer not actively ready')
        if not (OUT/'ready.json').exists():save('ready',{'execution':execution,'event_at':rows[0]['TimeGenerated'],'proof':result})
    elif status in ('Succeeded','Failed'):
        if not (OUT/'result.json').exists():save('result',{'execution':execution,'passed':status=='Succeeded' and result['passed'],'proof':result,'job_settings_preserved':True})
        print(json.dumps({k:v for k,v in result.items() if k not in ('events','after')}))

if __name__=='__main__':
    if sys.argv[2:]:
        if sys.argv[2:]!=['v2']:raise ValueError('Unknown observer attempt')
        prior=protected(ROOT/'legacy-failover-probe-v1/result.json')
        if not prior['passed'] or (ROOT/'legacy-ha-rehearsal-v1/failover-intent.json').exists() and sys.argv[1]=='start':raise ValueError('Prior observer must be reconciled; no repeated failover')
        OUT=ROOT/'legacy-failover-probe-v2'
    main(sys.argv[1])
