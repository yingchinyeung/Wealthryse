"""Credential-free installed native smoke, or observed discovery cron acceptance."""
from datetime import datetime, timezone
import json
import sys
from control_upgrade_institutional_postgres_20261010 import candidate, save
from release_upgrade_institutional_security_20261010 import OUT, JOBS, API
from control_upgrade_legacy_recovery_probe_20261009 import protected
from deploy_ms_presentation_worker_release import settings_invariant
from deploy_viewer_private_admin_20261008 import request, rid
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request


def main(stage, job, execution=None):
    if job not in JOBS: raise ValueError('Outside release scope')
    image=candidate(); path=rid('Microsoft.App/jobs',job)
    plan=protected(OUT/'deployment-v1'/job/'plan.json')
    verified=protected(OUT/'deployment-v1'/job/'verified.json')
    live=request('GET',path+API)
    if not verified['passed'] or live['properties']['template']['containers'][0]['image']!=image or settings_invariant(live)!=plan['invariant']:
        raise ValueError('Installed qualification boundary changed')
    folder=OUT/('installed-'+job+'-v1')
    if stage=='start':
        if folder.exists(): raise ValueError('Reconcile existing execution')
        runtime.safe_window(); runtime.idle(job)
        template=corrected_template(live,"import runpy,sys;sys.argv=['/qualification/qualify.py','candidate'];runpy.run_path(sys.argv[0],run_name='__main__')")
        template['containers'][0]['env']=[]
        folder.mkdir();save(folder,'intent',{'image':image,'template':template,'saved_settings_invariant':settings_invariant(live),'scope':'Installed native 23-test smoke; no injected secrets, broker, database or market connections'})
        ack=request('POST',path+'/start'+API,data=template)
        save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'job':job,'execution':ack['name']}));return
    if stage=='read':
        intent=protected(folder/'intent.json');execution=protected(folder/'dispatch.json')['execution']
        run=request('GET',path+'/executions/'+execution+API)
        actual=run['properties']['template']['containers'][0];expected=intent['template']['containers'][0]
        if any(actual.get(k)!=expected.get(k) for k in ('image','command','args')) or actual.get('env'):
            raise ValueError('Credential-free runtime template differs')
        state=run['properties']['status'];print(json.dumps({'job':job,'state':state}))
        if state not in ('Succeeded','Failed'):return
        if state!='Succeeded':raise ValueError('Installed smoke failed')
        prefix='INSTITUTIONAL_NATIVE_PROOF '
        query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
        rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
        if len(rows)!=1:print('Waiting for native proof ingestion');return
        proof=json.loads(rows[0]['Result'])
        build=json.loads((OUT/'native-build-qualified.json').read_bytes())
        expected_proof=next(p for p in build['proofs'] if p['stage']=='candidate')
        if proof!=expected_proof:raise ValueError('Installed native proof differs from build')
        if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'job':job,'image':image,'execution':execution,'proof':proof,'settings_preserved':True,'database_connections':0,'normal_cycle_verified':False})
        print(json.dumps({'job':job,'installed_checks_passed':True}));return
    if stage!='scheduled' or job!='ibkrv2stg-flex-discovery' or not execution:raise ValueError('Observed discovery execution required')
    suffix=execution.removeprefix(job+'-')
    if not suffix.isdigit() or execution==suffix:raise ValueError('Cron-named execution required')
    run=request('GET',path+'/executions/'+execution+API);p=run['properties']
    start=datetime.fromisoformat(p['startTime'].replace('Z','+00:00'))
    if start.minute%5 or start.second or int(start.timestamp()//60)!=int(suffix):raise ValueError('Cron cadence differs')
    actual=p['template']['containers'][0];installed=live['properties']['template']['containers'][0]
    if p['status']!='Succeeded' or actual['image']!=image or any(actual.get(k)!=installed.get(k) for k in ('command','args')):
        raise ValueError('Normal scheduled command or image differs')
    q="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '{' | project Result=parse_json(Log_s)"
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',q)
    proofs=[json.loads(r['Result']) for r in rows]
    if proofs!=[{'status':'idle','message':'No pending validation request'}]:raise ValueError('Expected idle discovery proof unavailable; do not assume no broker work')
    folder=OUT/'discovery-scheduled-v1'
    if folder.exists():raise ValueError('Preserve prior scheduled acceptance')
    folder.mkdir();save(folder,'result',{'passed':True,'image':image,'execution':execution,'start':start.isoformat(),'proof':proofs[0],
        'scheduled_idle_path_verified':True,'active_broker_path_exercised':False,'whole_platform_cycle_verified':False,'burn_in_complete':False})
    print(json.dumps({'execution':execution,'scheduled_idle_path_verified':True}))


if __name__=='__main__':main(*sys.argv[1:])
