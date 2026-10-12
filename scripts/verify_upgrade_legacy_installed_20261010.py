"""Credential-free native smoke; never execute live bootstrap or backfill."""
from datetime import datetime, timezone
import json
import sys
from control_upgrade_legacy_ingestion_20261010 import OUT, candidate, save
from release_upgrade_legacy_security_20261010 import JOBS, API
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
        folder.mkdir();save(folder,'intent',{'image':image,'template':template,'saved_settings_invariant':settings_invariant(live),'scope':'Installed native 36-test smoke; no injected secrets, broker, database or market connections'})
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
        prefix='LEGACY_NATIVE_PROOF '
        query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
        rows=az('monitor','log-analytics','query','-w','f594ac4f-31eb-4684-a5af-a8adc5020cfa','--analytics-query',query)
        if len(rows)!=1:print('Waiting for native proof ingestion');return
        proof=json.loads(rows[0]['Result'])
        build=json.loads((OUT/'native-build-qualified.json').read_bytes())
        expected_proof=next(p for p in build['proofs'] if p['stage']=='candidate')
        if proof!=expected_proof:raise ValueError('Installed native proof differs from build')
        if not (folder/'result.json').exists():save(folder,'result',{'passed':True,'job':job,'image':image,'execution':execution,'proof':proof,'settings_preserved':True,'database_connections':0,'normal_cycle_verified':False})
        print(json.dumps({'job':job,'installed_checks_passed':True}));return
    raise ValueError('Only start/read of credential-free smoke is authorized by this controller')


if __name__=='__main__':main(*sys.argv[1:])
