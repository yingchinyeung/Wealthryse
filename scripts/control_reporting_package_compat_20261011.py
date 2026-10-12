"""Image-only execution overrides for candidate/fallback package preservation."""
import hashlib,json,sys
from pathlib import Path
import control_reporting_image_qa_20261011 as qa
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
ROOT=qa.ROOT;a=qa.a;b=qa.b
FILES=['scripts/'+Path(__file__).name,'scripts/probe_reporting_package_compat_20261011.py','scripts/prepare_upgrade_fresh_reporting_remote_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
def main(mode,stage):
    if mode not in ('candidate','fallback') or stage not in ('start','read'):raise ValueError('Unknown operation')
    folder=qa.OUT.parent/('package-'+mode+'-v1')
    review=protected(qa.OUT.parent/'image-review-v1/result.json')
    if not review['passed']:raise ValueError('Built-image report acceptance required')
    baseline=protected(qa.OUT.parent/'resume-v1/result.json');job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if folder.exists():raise ValueError('Preserve earlier trial')
        sources=qa.release.r.read(qa.release.OUT/'source-intent.json')['candidate_hashes'] if mode=='candidate' else protected(qa.OUT.parent/'installed-reporting-source-v1/result.json')['files']
        image=review['image'] if mode=='candidate' else qa.release.BASE_IMAGE
        helper=(ROOT/FILES[2]).read_text(encoding='utf-8')
        if helper.count("TEST_DB='upgrade_g04_fresh_20261011'")!=1:raise ValueError('QA helper changed')
        helper=helper.replace("TEST_DB='upgrade_g04_fresh_20261011'","TEST_DB='upgrade_g04_20261010'")
        code='import types,sys,os\nos.environ["DATABASE_ADMIN_URL"]=os.environ["DATABASE_URL"]\nq=types.ModuleType("qa_read_only");sys.modules[q.__name__]=q\nexec('+repr(helper)+',q.__dict__)\nMODE='+repr(mode)+'\nSOURCE_HASHES='+repr(sources)+'\n'+(ROOT/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['image']=image
        template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name'] in {'DATABASE_URL','AZURE_CLIENT_ID','PYTHONPATH','PATH'}]
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(folder,'intent',{'template':template,'sources':hashes(),'image':image,'scope':'SELECT-only existing private QA package compatibility. No source overlays, generation, saved job changes, production connections or release changes.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(folder,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name'],'mode':mode}));return
    intent=protected(folder/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Probe changed')
    name=protected(folder/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_COMPAT_FINAL ' if state=='Succeeded' else 'REPORTING_COMPAT_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting receipt ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or len(proof['subjects'])!=5 or not proof.get('qa_report_state_unchanged') or proof.get('database_writes')!=0 or proof.get('blob_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (folder/(label+'.json')).exists():save(folder,label,{'passed':state=='Succeeded','proof':proof,'image':intent['image'],'execution':name,'saved_job_changed':False,'live_rollback_rehearsed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
