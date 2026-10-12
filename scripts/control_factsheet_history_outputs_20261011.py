"""Qualified v5, SELECT-only migration clone, 63 private generic PDFs."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_historical_outputs_20261011 as old
import release_reporting_history_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=old.ROOT;a=old.a;b=old.b;STAGE=release.OUT.parent;OUT=STAGE/'factsheet-history-outputs-v1'
FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','probe_factsheet_history_outputs_20261011.py']
def hashes(): return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}

def main(stage):
    if stage not in ('start','read'): raise ValueError('Start or read only')
    source,_,output=scan.verify(release.OUT);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scanned=release.r.read(release.OUT/'scan-completed.json');remote,_=restore_protected(release.r.read(release.OUT/'scan-protected-receipt.json'))
    if scanned!=remote or scanned['image']!=image or scanned['assessment']['status']!='pass': raise ValueError('Exact protected scan required')
    baseline=protected(STAGE/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]: raise ValueError('Saved reporting worker drift')
    if stage=='start':
        if OUT.exists(): raise ValueError('Preserve previous output attempt')
        migration=protected(STAGE/'august-snapshot-correction-v1/result.json');clone=protected(STAGE/'migration-clone-v1/result.json')
        expected={t:clone['proof']['all_tables'][t]['sha256'] for t in ('onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots')}
        expected['reporting_snapshots']=migration['proof']['final_snapshot_fingerprint']['sha256']
        code='import types,sys,os\nsys.path[:0]=["/app/src","/app/scripts"]\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("prepare_reporting_migration_clone_20261011");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts'/FILES[1]).read_text())+',q.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source['candidate_hashes'])+'\nEXPECTED_REPORT_HASHES='+repr(expected)+'\nIMAGE='+repr(image)+'\n'+(ROOT/'scripts'/FILES[2]).read_text()
        template=b.corrected_template(job,code);template['containers'][0]['image']=image
        template['containers'][0]['env']=protected(STAGE/'image-qa-v2/intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']: raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'image':image,'scope':'63 private generic factsheets; independently check every rendered history row against full frozen NAV. No database writes, source overlays, report registrations, approvals or saved-job changes.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes(): raise ValueError('Frozen QA source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')): raise ValueError('Executed template differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'): print(state);return
    prefix='FACTSHEET_HISTORY_OUTPUT_FINAL ' if state=='Succeeded' else 'FACTSHEET_HISTORY_OUTPUT_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1: print('Awaiting native receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof['passed'] or proof['factsheets']!=63 or proof['months']!=63 or proof['history_rows_verified']!=756 or proof['database_writes']!=0 or proof['production_connection_used'] is not False): raise ValueError('Incomplete corrected-history proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists(): save(OUT,label,{'passed':state=='Succeeded','proof':proof,'execution':name,'image':image,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__': main(*sys.argv[1:])
