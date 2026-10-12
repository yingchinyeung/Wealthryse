"""Scanned consumer images, five-subject SELECT-only private runtime trials."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as b
import scan_ms_presentation_worker_candidate as scan
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save,execution_env
ROOT=Path(__file__).resolve().parents[1];STAGE=ROOT/'.work/platform-upgrade-20261011-stage34'
FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','probe_reporting_assurance_diagnostic_20261011.py','reporting_composite_runtime_reference_v5_20261011.py','reporting_symbol_reconciliation_20261011.py']
def hashes(): return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}

def main(stage,kind):
    if kind!='assurance' or stage not in ('start','read'): raise ValueError('Exact read-only consumer trial only')
    build=STAGE/('consumer-agent-'+kind+'-v3');out=ROOT/'.work/platform-upgrade-20261011-stage35'/('consumer-'+kind+'-diagnostic-v1')
    source,_,output=scan.verify(build);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scanned=json.loads((build/'scan-completed.json').read_bytes());remote,_=restore_protected(json.loads((build/'scan-protected-receipt.json').read_bytes()))
    if scanned!=remote or scanned['image']!=image or scanned['assessment']['status']!='pass': raise ValueError('Protected exact-image scan required')
    prepared=json.loads((build/'prepared.json').read_bytes());baseline=protected(STAGE/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]: raise ValueError('Bootstrap job drift')
    if stage=='start':
        if out.exists(): raise ValueError('Preserve previous trial')
        from reporting_composite_runtime_reference_20261011 import composite_branch
        capture=STAGE/('consumer-source-'+kind+'-v1')
        captured=json.loads((capture/'result.json').read_bytes())
        old=(capture/'src/ibkr_etl/agent_tools.py').read_bytes()
        new=(build/'context/payload/src/ibkr_etl/agent_tools.py').read_bytes()
        if hashlib.sha256(old).hexdigest()!=captured['files_sha256']['src/ibkr_etl/agent_tools.py'] or hashlib.sha256(new).hexdigest()!=source['candidate_hashes']['src/ibkr_etl/agent_tools.py']: raise ValueError('Composite baseline/candidate hash differs')
        if composite_branch(old.decode())!=composite_branch(new.decode()): raise ValueError('Composite branch was changed')
        audit=protected(ROOT/'.work/platform-upgrade-20261011-stage35/symbol-audit-v1/result.json')
        if not audit['passed']:raise ValueError('Identity provenance audit required')
        migration=protected(STAGE/'august-snapshot-correction-v1/result.json')
        code='import types,sys\nsys.path[:0]=["/app/src","/app/scripts"]\nq=types.ModuleType("prepare_reporting_migration_clone_20261011");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts'/FILES[1]).read_text())+',q.__dict__)\n'
        code+='import types\nr=types.ModuleType("reporting_composite_runtime_reference_20261011");sys.modules[r.__name__]=r\nexec('+repr((ROOT/'scripts'/FILES[3]).read_text())+',r.__dict__)\n' 
        code+='s=types.ModuleType("reporting_symbol_reconciliation_20261011");sys.modules[s.__name__]=s\nexec('+repr((ROOT/'scripts'/FILES[4]).read_text())+',s.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source['candidate_hashes'])+'\nEXPECTED='+repr(migration['proof']['final_snapshot_fingerprint']['sha256'])+'\nCONSUMER='+repr(prepared['consumer'])+'\nIMAGE='+repr(image)+'\n'+(ROOT/'scripts'/FILES[2]).read_text()
        template=b.corrected_template(job,code);template['containers'][0]['image']=image
        template['containers'][0]['env']=protected(STAGE/'august-snapshot-correction-v1/intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        web=protected(ROOT/'.work/platform-upgrade-20261011-stage35/web-boundary-v1/result.json')
        if not web['passed']:raise ValueError('Verified website rollout boundary required')
        expected_bindings=web['boundary']['workloads']
        if b.bindings(b.current_resources())!=expected_bindings:raise ValueError('Workload drift')
        save(out,'intent',{'template':template,'sources':hashes(),'image':image,'scope':'Installed consumer source; SELECT-only private QA, no production connection or source overlays. Five-subject sector validation; no saved-job, report or approval change.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'consumer':prepared['consumer']}));return
    intent=protected(out/'intent.json')
    if intent['sources']!=hashes(): raise ValueError('Trial source drift')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')): raise ValueError('Execution template differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'): print(state);return
    prefix='REPORTING_ASSURANCE_DIAGNOSTIC_'
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=Log_s")
    if len(rows)!=1: print('Awaiting receipt');return
    line=rows[0]['Result']; proof=json.loads(line.split(' ',1)[1])
    if state=='Succeeded' and (not proof['passed'] or proof['image']!=image or len(proof['subjects'])!=5 or proof['source_hashes']!=source['candidate_hashes'] or proof['database_writes']!=0 or proof['production_connection_used'] is not False): raise ValueError('Incomplete runtime proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (out/(label+'.json')).exists(): save(out,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':image,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__': main(*sys.argv[1:])
