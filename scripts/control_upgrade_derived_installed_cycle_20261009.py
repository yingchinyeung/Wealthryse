"""Installed normal derived then risk jobs; no saved configuration changes."""
import json,sys
from datetime import datetime,timezone
from deploy_upgrade_derived_tail_20261009 import FOLDER as DEPLOY
from release_upgrade_derived_tail_20261009 import OUT
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_live_catchup_20261009 import normal_template
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
import deploy_t_runtime_release_20261009 as runtime
from run_specialist_gateway_read_verification import retain
runtime.request=request
API='?api-version=2024-03-01'

def save(folder,name,value):
    with (folder/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-installed-derived-'+folder.name+'-'+name)},f,indent=2)

def main(stage,kind):
    if kind not in ('derived','risk'):raise ValueError('Unknown normal job')
    plan=protected(DEPLOY/'plan.json');verified=protected(DEPLOY/'verified.json')
    if not verified['passed'] or verified['image']!=plan['image']:raise ValueError('Verified installed candidate required')
    jobname='ibkrv2stg-derived-daily' if kind=='derived' else 'ibkrv2stg-risk-snapshots';path=rid('Microsoft.App/jobs',jobname);folder=OUT/('installed-'+kind+'-v1')
    live=request('GET',path+API)
    image=plan['image'] if kind=='derived' else 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:bd669adb8265ae75d11644cd98a1dec25fd9ba615281a2abc1536e4625d49429'
    entry='scripts/run_self_service_derived_refresh.py' if kind=='derived' else 'scripts/build_risk_snapshots.py'
    c=live['properties']['template']['containers'][0]
    if c['image']!=image or c.get('command')!=['python'] or c.get('args')!=[entry]:raise ValueError('Qualified normal runtime drift')
    if kind=='derived' and settings_invariant(live)!=plan['invariant']:raise ValueError('Non-image settings drift')
    if stage=='start':
        if folder.exists():raise ValueError('Reconcile prior execution')
        if kind=='risk' and not protected(OUT/'installed-derived-v1/result.json')['passed']:raise ValueError('Derived dependency must pass')
        runtime.safe_window()
        for name in ('ibkrv2stg-derived-daily','ibkrv2stg-benchmark-daily','ibkrv2stg-risk-snapshots'):runtime.idle(name)
        template=normal_template(live);folder.mkdir();save(folder,'intent',{'image':image,'template':template,'invariant':settings_invariant(live),'started_at':datetime.now(timezone.utc).isoformat(),'scope':'Normal approved analytics only; no source ingestion, reports or approvals'})
        ack=request('POST',path+'/start'+API,data=template);save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown stage')
    intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
    if settings_invariant(live)!=intent['invariant']:raise ValueError('Saved job drift')
    run=request('GET',path+'/executions/'+execution+API);status=run['properties']['status'];actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
    if any(actual.get(k)!=expected.get(k) for k in ('image','command','args')):raise ValueError('Executed normal job differs')
    if status in ('Succeeded','Failed') and not (folder/'result.json').exists():save(folder,'result',{'passed':status=='Succeeded','execution':execution,'image':image,'saved_settings_preserved':True})
    print(json.dumps({'execution':execution,'status':status}))

if __name__=='__main__':main(*sys.argv[1:])
