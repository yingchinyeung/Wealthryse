"""Compare full SELECT-only collectors across the reference and candidate images."""
import hashlib
import json
from pathlib import Path
import sys
import control_upgrade_g04_preflight_20261010 as b
import release_snapshot_correction_20261011 as release
import scan_ms_presentation_worker_candidate as scan
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from read_upgrade_g04_worker_20261010 import exact_environment
from rehearse_ms_worker_image_rollback import restore_protected

ROOT=release.ROOT;STAGE=release.STAGE
FILES=(Path(__file__).name,'probe_snapshot_correction_20261011.py','prepare_reporting_migration_clone_20261011.py')
SUBJECTS={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}

def accept(proof,image,source):
    if proof.get('passed') is not True or proof.get('image')!=image or proof.get('source_hashes')!=source:raise ValueError('Runtime identity differs')
    rows=proof.get('subjects',[])
    if len(rows)!=5 or {r['subject'] for r in rows}!=SUBJECTS or any(r.get('passed') is not True or r['quality_status']=='fail' for r in rows):raise ValueError('Five complete subjects required')
    if type(proof.get('database_writes')) is not int or proof['database_writes']!=0 or proof.get('production_connection_used') is not False or proof.get('source_overlays') is not False or proof.get('private_fixture_uploaded') is not False or proof.get('unchanged_replays')!=5 or proof.get('snapshot_history_preserved') is not True:raise ValueError('Private read-only boundary failed')

def candidate(kind):
    folder=release.OUT if kind=='candidate' else ROOT/'.work/platform-upgrade-20261011-stage34/reporting-image-v5'
    source,_,output=scan.verify(folder);image=output['registry']+'/'+output['repository']+'@'+output['digest']
    scanned=json.loads((folder/'scan-completed.json').read_bytes())
    remote,_=restore_protected(json.loads((folder/'scan-protected-receipt.json').read_bytes()))
    if scanned!=remote or scanned['image']!=image or scanned['assessment']['status']!='pass':raise ValueError('Exact protected scan required')
    source_hashes=dict(source['candidate_hashes'])
    if kind=='reference':
        captured=json.loads((release.QUALIFIED/'result.json').read_bytes())
        source_hashes={n:captured['files_sha256'][n] for n in json.loads((release.OUT/'source-intent.json').read_bytes())['candidate_hashes'] if n!=release.ASSET}
        source_hashes[release.ASSET]=release.ASSET_SHA
    return image,source_hashes

def main(stage,kind):
    if stage not in ('start','read') or kind not in ('reference','candidate'):raise ValueError('Exact trial required')
    out=STAGE/('snapshot-'+kind+'-runtime-v1')
    image,source=candidate(kind)
    baseline=protected(STAGE/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Saved bootstrap drift')
    if stage=='start':
        if out.exists():raise ValueError('Preserve earlier trial; reconcile')
        migration=protected(ROOT/'.work/platform-upgrade-20261011-stage34/august-snapshot-correction-v1/result.json')
        code='import sys,types\nsys.path[:0]=["/app/src","/app/scripts"]\nq=types.ModuleType("prepare_reporting_migration_clone_20261011");sys.modules[q.__name__]=q\nexec('+repr((ROOT/'scripts'/FILES[2]).read_text())+',q.__dict__)\n'
        code+='SOURCE_HASHES='+repr(source)+'\nEXPECTED='+repr(migration['proof']['final_snapshot_fingerprint']['sha256'])+'\nIMAGE='+repr(image)+'\n'+(ROOT/'scripts'/FILES[1]).read_text()
        template=b.corrected_template(job,code);template['containers'][0]['image']=image
        template['containers'][0]['env']=protected(ROOT/'.work/platform-upgrade-20261011-stage34/august-snapshot-correction-v1/intent.json')['template']['containers'][0]['env']
        b.runtime.safe_window();b.runtime.idle(b.JOB);release.unchanged()
        save(out,'intent',{'template':template,'sources':hashes(),'source_hashes':source,'image':image,'scope':'Full collectors for five subjects and deterministic replay, SELECT-only private QA, no saved-job change, report generation, approval, production connection or build-service data upload.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(out/'intent.json')
    if intent['sources']!=hashes() or intent['image']!=image or intent['source_hashes']!=source:raise ValueError('Trial source drift')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution changed')
    exact_environment(b.JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='SNAPSHOT_RUNTIME_FINAL ' if status=='Succeeded' else 'SNAPSHOT_RUNTIME_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting receipt');return
    proof=json.loads(rows[0]['Result'])
    if status=='Succeeded':accept(proof,image,source)
    label='result' if status=='Succeeded' else 'failure'
    if not (out/(label+'.json')).exists():save(out,label,{'passed':status=='Succeeded','proof':proof,'image':image,'execution':name,'deployed':False})
    print(json.dumps({'status':status,'subjects':len(proof.get('subjects',[])),'error':proof.get('message')}))

if __name__=='__main__':main(*sys.argv[1:])
