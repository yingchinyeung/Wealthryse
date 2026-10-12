"""Normal unchanged assurance worker, only after current consumer acceptance."""
import json,sys
from datetime import datetime,timezone
from release_upgrade_derived_tail_20261009 import OUT
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
from control_upgrade_live_catchup_20261009 import normal_template
import deploy_t_runtime_release_20261009 as runtime
from audit_azure_runtime import az
from run_specialist_gateway_read_verification import retain
runtime.request=request
FOLDER=OUT/'assurance-cycle-v1';JOB='ibkrv2stg-analyst-assurance';PATH=rid('Microsoft.App/jobs',JOB);API='?api-version=2024-03-01'
IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1226d2e0ef97739f2b9f380e8eb153848e98c7cf1b3a1ec81353c3e9fc25eb90'
def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-assurance-cycle-'+name)},f,indent=2)
def main(stage):
    live=request('GET',PATH+API);c=live['properties']['template']['containers'][0]
    if c['image']!=IMAGE or c.get('command')!=['python'] or c.get('args')!=['scripts/run_registry_portfolio_assurance.py']:raise ValueError('Qualified assurance runtime drift')
    if stage=='start':
        if FOLDER.exists():raise ValueError('Reconcile existing assurance run')
        archive=protected(OUT/'assurance-history-v1/result.json')
        if archive['status']!='Succeeded' or not json.loads(archive['records'][0]['Result'])['passed']:raise ValueError('Original history must be protected')
        runtime.safe_window()
        for name in (JOB,'ibkrv2stg-derived-daily','ibkrv2stg-risk-snapshots','ibkrv2stg-benchmark-daily'):runtime.idle(name)
        template=normal_template(live);FOLDER.mkdir();save('intent',{'image':IMAGE,'invariant':settings_invariant(live),'template':template,'prior_history':archive['protected_receipt'],'started_at':datetime.now(timezone.utc).isoformat(),'scope':'Unchanged authorized assurance process, existing AI configuration, normal validation records only; no report/source/approval/access change'})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown step')
    intent=protected(FOLDER/'intent.json');execution=protected(FOLDER/'dispatch.json')['execution']
    if settings_invariant(live)!=intent['invariant']:raise ValueError('Assurance settings drift')
    run=request('GET',PATH+'/executions/'+execution+API);status=run['properties']['status'];a=run['properties']['template']['containers'][0];w=intent['template']['containers'][0]
    if any(a.get(k)!=w.get(k) for k in ('image','command','args')):raise ValueError('Executed assurance differs')
    print(json.dumps({'execution':execution,'status':status}))
    if status not in ('Succeeded','Failed'):return
    q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | extend J=parse_json(Log_s) | where isnotempty(J.subjects) | project Result=tostring(J)"
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    if len(rows)!=1:print('Final five-account assurance evidence not yet available');return
    record=json.loads(rows[0]['Result']);subjects=record.get('subjects',[])
    passed=status=='Succeeded' and record.get('status')=='success' and len(subjects)==5 and {s['subject_id'] for s in subjects}=={'U16088571','U16088606','alpha-world-ms','alpha-world-t','hong-kong-china'} and all(s['status']=='pass' for s in subjects)
    if not (FOLDER/'result.json').exists():save('result',{'passed':passed,'execution':execution,'image':IMAGE,'proof':record,'saved_settings_preserved':True,'scheduled_cycle':False})
    print(json.dumps({'passed':passed,'subjects':subjects}))
if __name__=='__main__':
    if sys.argv[2:]:
        if sys.argv[2:]!=['qualified-v2']:raise ValueError('Unknown assurance cycle attempt')
        from release_upgrade_assurance_period_20261009 import OUT as QUALIFIED
        verification=protected(QUALIFIED/'deployment-v1/verified.json')
        if not verification['passed']:raise ValueError('Qualified deployment required')
        IMAGE=verification['image'];FOLDER=QUALIFIED/'installed-cycle-v2'
    main(sys.argv[1])
