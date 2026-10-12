"""Create private QA output storage, then run one guarded reporting trial."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_worker_20261010 as b
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment

ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage29/fresh-report-v1'
CONTAINER=b.p.rid('Microsoft.Storage/storageAccounts/blobServices/containers','ibkrv2stgauhecwc4itaoo/default/upgrade-g04-reports-20261010')
STORAGE_API='?api-version=2023-05-01'


def hashes():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        (Path(__file__).name,'probe_upgrade_g04_fresh_20261010.py','probe_upgrade_g04_worker_20261010.py','read_upgrade_g04_worker_20261010.py')}


def main(stage):
    if stage not in ('prepare','start','read'):raise ValueError('Unknown operation')
    proof=protected(ROOT/'.work/platform-upgrade-20261010-stage29/worker-preflight-v2/result.json')
    if proof.get('passed') is not True:raise ValueError('QA app-role preflight required')
    job=b.p.request('GET',b.PATH+b.p.API)
    baseline=protected(ROOT/'.work/platform-upgrade-20261010-stage29/worker-preflight-v2/intent.json')
    if b.p.bindings([job])!=baseline['binding']:raise ValueError('Normal worker drift')
    if stage=='prepare':
        if OUT.exists():raise ValueError('Preserve prior operation')
        if b.p.request('GET',CONTAINER+STORAGE_API,missing=True) is not None:raise ValueError('Container already exists')
        body={'properties':{'publicAccess':'None','metadata':{'purpose':'isolated-upgrade-g04','created':'2026-10-10'}}}
        save(OUT,'storage-intent',{'target':CONTAINER,'body':body,'sources':hashes(),
            'scope':'One new private QA-only output container; no access grants or canonical report changes',
            'recovery':'Preserve test outputs and evidence; no deletion or publication'})
        b.p.request('PUT',CONTAINER+STORAGE_API,data=body)
        current=b.p.request('GET',CONTAINER+STORAGE_API)
        if current['properties']['publicAccess']!='None' or current['properties']['metadata']!=body['properties']['metadata']:raise ValueError('Private container readback failed')
        save(OUT,'storage-verified',{'passed':True,'target':current['id'],'public_access':'None','role_assignments_added':0})
        print('Private QA container created and verified');return
    intent=protected(OUT/'storage-intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    storage=b.p.request('GET',CONTAINER+STORAGE_API)
    if storage['properties']['publicAccess']!='None' or storage['properties']['metadata']!=intent['body']['properties']['metadata']:raise ValueError('Storage boundary changed')
    if stage=='start':
        if (OUT/'intent.json').exists():raise ValueError('Reconcile previous execution, never retry blindly')
        b.p.runtime.safe_window();b.p.runtime.idle(b.JOB)
        remote=(ROOT/'scripts/probe_upgrade_g04_worker_20261010.py').read_text(encoding='utf-8')
        code='import types,sys\nq=types.ModuleType("qa_boundary");sys.modules["qa_boundary"]=q\nexec('+repr(remote)+',q.__dict__)\n'
        code+='EXPECTED_WORKER_SHA='+repr(hashlib.sha256(b.WORKER.read_text(encoding='utf-8').encode()).hexdigest())+'\n'
        code+='EXPECTED_RECORDS='+repr(proof['proof']['records'])+'\nEXPECTED_SNAPSHOT='+repr(proof['proof']['snapshot_id'])+'\n'
        code+=(ROOT/'scripts/probe_upgrade_g04_fresh_20261010.py').read_text(encoding='utf-8')
        template=b.p.corrected_template(job,code)
        save(OUT,'intent',{'sources':hashes(),'template':template,'binding':b.p.bindings([job]),'preflight':proof['protected_receipt'],
            'scope':'Reset only two unreleased T September report statuses in isolated QA copy; actual normal-worker generation, second-upload failure, recovery and replay; private test container only',
            'production_writes_authorized':False,'report_publication_authorized':False})
        ack=b.p.request('POST',b.PATH+'/start'+b.p.API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    trial=protected(OUT/'intent.json')
    if trial['sources']!=hashes() or trial['binding']!=b.p.bindings([job]):raise ValueError('Trial drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.p.request('GET',b.PATH+'/executions/'+name+b.p.API)
    actual=run['properties']['template']['containers'][0];wanted=trial['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(b.JOB,actual.get('env') or [],wanted['env'])
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_G04_FRESH ' if state=='Succeeded' else 'UPGRADE_G04_FRESH_ERROR '
    rows=b.p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting bounded proof ingestion');return
    result=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':result,'reconcile_before_retry':True})
        print(json.dumps({'failed':True,'proof':result}));return
    for key in ('passed','fresh_artifact_generation','injected_upload_failure_rolled_back','idempotent_replay_passed','approvals_and_snapshots_unchanged'):
        if result.get(key) is not True:raise ValueError('Incomplete acceptance: '+key)
    if result.get('production_connection_used') is not False or result.get('canonical_report_container_writes')!=0 or result.get('database')!='upgrade_g04_20261010':raise ValueError('Isolation failed')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':result,'g04_complete':False})
    print(json.dumps({'passed':True,'fresh_artifacts':len(result['artifacts']),'g04_complete':False}))


if __name__=='__main__':main(*sys.argv[1:])
