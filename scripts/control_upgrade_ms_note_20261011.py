"""One MS-only QA pair to qualify the native table-footnote spacing correction."""
import ast,base64,gzip,hashlib,json,sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
a=base.a;b=base.b;ROOT=base.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage33/ms-note-v1'
PREV=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v4'
def hashes():return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in ['scripts/'+Path(__file__).name,'scripts/render_monthly_presentation.mjs']}
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    job=b.request('GET',a.PATH+b.API);baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior trial')
        prior=protected(PREV/'intent.json');result=protected(PREV/'result.json')
        if not result['passed']:raise ValueError('Previous render required')
        for name,digest in prior['sources'].items():
            if name!='scripts/render_monthly_presentation.mjs' and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise ValueError('Unrelated source drift')
        container=prior['template']['containers'][0]
        literals=[n.value for n in ast.walk(ast.parse(container['args'][1])) if isinstance(n,ast.Constant) and isinstance(n.value,str) and len(n.value)>100]
        if len(literals)!=1:raise ValueError('Unexpected envelope')
        code=gzip.decompress(base64.b64decode(literals[0],validate=True)).decode();lines=code.splitlines(keepends=True)
        nodes=[n for n in ast.parse(code).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='JS_CANDIDATES' for t in n.targets)]
        if len(nodes)!=1:raise ValueError('Unexpected candidate assignment')
        n=nodes[0];sources=ast.literal_eval(n.value);old=sources['render_monthly_presentation.mjs']
        new=(ROOT/'scripts/render_monthly_presentation.mjs').read_text(encoding='utf-8')
        anchor='setTable("tb/gjutwb61", holdingTable);\nif (isMsHkd) {\n'
        addition="  // Native PowerPoint expands the retained blank rows beyond the declared frame.\n  // Keep all rows and typography, but leave clear space below the actual table.\n  presentation.resolve('sh/4ji5kbid').position.top += 24;\n"
        if old.count(anchor)!=1 or new!=old.replace(anchor,anchor+addition):raise ValueError('Only the MS footnote spacing delta is permitted')
        sources['render_monthly_presentation.mjs']=new;lines[n.lineno-1:n.end_lineno]=['JS_CANDIDATES='+repr(sources)+'\n'];code=''.join(lines)
        changes={"PREFIX='candidate-stage33-v4/'":"PREFIX='candidate-stage33-ms-note-v1/'",
                 '        for row in rows:\n':"        for row in rows:\n            if row['subject_id']!='alpha-world-ms':continue\n",
                 "'artifacts':10":"'artifacts':len(uploads)"}
        for old,new in changes.items():
            if code.count(old)!=1:raise ValueError('Expected exact scope anchor')
            code=code.replace(old,new)
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['bindings']:raise ValueError('Other workload drift')
        template=b.corrected_template(job,code);template['containers'][0]['env']=container['env']
        save(OUT,'intent',{'template':template,'sources':hashes(),'previous':result['protected_receipt'],'scope':'One private MS QA pair, read-only QA DB, exact 24px footnote move only, no production changes'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting receipt ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (proof.get('passed') is not True or proof.get('artifacts')!=2 or [r['subject'] for r in proof['subjects']]!=['alpha-world-ms'] or not proof.get('qa_state_unchanged') or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete MS-only proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
