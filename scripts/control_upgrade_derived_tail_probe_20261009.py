"""Exact scanned daily candidate, isolated PostgreSQL temporary-gap acceptance."""
import json,sys,hashlib
from pathlib import Path
from release_upgrade_derived_tail_20261009 import OUT,IMAGE,PATH,JOB,FILES
from deploy_viewer_private_admin_20261008 import request
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain
from control_upgrade_legacy_recovery_probe_20261009 import protected
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request
FOLDER=OUT/'postgres-v1';SOURCE=Path(__file__).with_name('probe_upgrade_derived_tail_20261009.py');API='?api-version=2024-03-01'
def save(name,value):
    with (FOLDER/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-derived-tail-probe-'+name)},f,indent=2)
def candidate():
    _,_,output=verify(OUT);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scan=json.loads((OUT/'scan-completed.json').read_bytes());receipt=json.loads((OUT/'scan-protected-receipt.json').read_bytes());remote,_=restore_protected(receipt)
    if scan!=remote or scan['image']!=image or scan['assessment']['status']!='pass':raise ValueError('Exact protected image scan required')
    return image
def main(stage):
    image=candidate();job=request('GET',PATH)
    if job['properties']['template']['containers'][0]['image']!=IMAGE:raise ValueError('Live daily baseline changed')
    if stage=='start':
        if FOLDER.exists():raise ValueError('Preserve existing qualification')
        runtime.idle(JOB)
        expected={name:hashlib.sha256((OUT/'context'/name).read_bytes()).hexdigest() for name in FILES}
        code='EXPECTED_FILES='+repr(expected)+'\n'+SOURCE.read_text(encoding='utf-8');compile(code,'clone-temporary-gap','exec')
        template=corrected_template(job,code);template['containers'][0]['image']=image
        FOLDER.mkdir();save('intent',{'image':image,'template':template,'job_invariant':settings_invariant(job),'source_sha256':hashlib.sha256(code.encode()).hexdigest(),'live_database_writes':0})
        ack=request('POST',PATH.split('?')[0]+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'private_clone_only':True}));return
    if stage!='read':raise ValueError('Unknown step')
    intent=protected(FOLDER/'intent.json');execution=protected(FOLDER/'dispatch.json')['execution']
    if settings_invariant(job)!=intent['job_invariant']:raise ValueError('Live daily settings changed')
    run=request('GET',PATH.split('?')[0]+'/executions/'+execution+API);state=run['properties']['status'];actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Executed diagnostic differs')
    print(json.dumps({'execution':execution,'state':state}))
    if state not in ('Succeeded','Failed'):return
    prefix='DERIVED_TAIL_PROOF '
    query="ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+"))"
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if len(rows)!=1:print('Final proof not yet available');return
    proof=json.loads(rows[0]['Result']);result={'passed':state=='Succeeded' and proof['passed'],'image':image,'execution':execution,'proof':proof,'job_settings_preserved':True}
    if not (FOLDER/'result.json').exists():save('result',result)
    print(json.dumps(result))
if __name__=='__main__':main(sys.argv[1])
