"""Bounded per-subject diagnostic log transport; preserve the truncated v1 trial."""
import hashlib,json,sys
import control_upgrade_snapshot_difference_20261011 as prior
b=prior.b;ROOT=prior.ROOT;save=prior.save;protected=prior.protected;execution_env=prior.execution_env
OUT=ROOT/'.work/platform-upgrade-20261011-stage31/snapshot-difference-v2'
SUBJECTS={'U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'}

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in ('control_upgrade_snapshot_difference_v2_20261011.py','probe_upgrade_snapshot_difference_20261011.py','prepare_upgrade_fresh_reporting_remote_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Bootstrap drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile existing trial')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='import types,sys,io,contextlib,json,hashlib\nm=types.ModuleType("qa_read_only");sys.modules[m.__name__]=m\nexec('+repr((ROOT/'scripts/prepare_upgrade_fresh_reporting_remote_20261011.py').read_text(encoding='utf-8'))+',m.__dict__)\n'
        code+='d=types.ModuleType("difference_logic")\nexec('+repr((ROOT/'scripts/probe_upgrade_snapshot_difference_20261011.py').read_text(encoding='utf-8'))+',d.__dict__)\n'
        code+='''captured=io.StringIO()
with contextlib.redirect_stdout(captured):d.main()
value=json.loads(captured.getvalue().split(' ',1)[1])
subjects=value.pop('subjects');digests={}
for name,subject in sorted(subjects.items()):
    subject['material_paths_truncated']=subject['material_paths_truncated'] or len(subject['material_numeric_paths'])>20
    subject['material_numeric_paths']=subject['material_numeric_paths'][:20]
    item={'subject':name,'analysis':subject}
    raw=json.dumps(item,sort_keys=True,separators=(',',':'))
    if len(raw.encode())>14000:raise ValueError('Per-subject proof exceeds bounded log size')
    digests[name]=hashlib.sha256(raw.encode()).hexdigest()
    print('UPGRADE_SNAPSHOT_DIFF_PART '+raw,flush=True)
value['subject_proof_sha256']=digests
print('UPGRADE_SNAPSHOT_DIFF_FINAL '+json.dumps(value),flush=True)
'''
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'scope':'Repeat read-only classification with bounded per-subject integrity-bound logs; no data changes','prior_trial':protected(prior.OUT/'dispatch.json')['execution']})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Bounded classification trial failed')
    prefix='UPGRADE_SNAPSHOT_DIFF_'
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Log_s")
    if len(rows)!=6:print('Awaiting six complete proof sections');return
    parts={};final=None
    for row in rows:
        line=row['Log_s'];tag,raw=line.split(' ',1);value=json.loads(raw)
        if tag=='UPGRADE_SNAPSHOT_DIFF_FINAL':
            if final is not None:raise ValueError('Duplicate final proof')
            final=value
        elif tag=='UPGRADE_SNAPSHOT_DIFF_PART':
            if value['subject'] in parts:raise ValueError('Duplicate subject proof')
            parts[value['subject']]=(value,hashlib.sha256(raw.encode()).hexdigest())
        else:raise ValueError('Unexpected log proof')
    if not final or set(parts)!=SUBJECTS or final['subject_proof_sha256']!={s:r[1] for s,r in parts.items()}:raise ValueError('Section integrity failed')
    if final.get('passed') is not True or final.get('database_writes')!=0 or final.get('production_connection_used') is not False:raise ValueError('Read-only proof incomplete')
    final['subjects']={s:r[0]['analysis'] for s,r in parts.items()}
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':final,'all_six_sections_verified':True})
    print(json.dumps({'passed':True,'subjects':{s:{k:v for k,v in r['analysis'].items() if k not in ('families','material_numeric_paths')} for s,(r,_) in parts.items()}}))

if __name__=='__main__':main(*sys.argv[1:])
