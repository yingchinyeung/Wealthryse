"""Run T with the exact corrected shared renderer in private read-only QA."""
import ast,base64,gzip,hashlib,json,sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
a=base.a;b=base.b;ROOT=base.ROOT
OUT=ROOT/'.work/platform-upgrade-20261011-stage34/t-integration-v1'
PREV=ROOT/'.work/platform-upgrade-20261011-stage33/ms-note-v1'
FILES=['render_monthly_presentation.mjs','render_alpha_t_presentation.mjs','render_alpha_t_private_presentation.mjs',
       'alpha_t_presentation_runtime.mjs','report_content_labels.mjs','report_summary_label.mjs']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in [*FILES,Path(__file__).name]}
def code_for_trial(prior):
    container=prior['template']['containers'][0]
    literals=[n.value for n in ast.walk(ast.parse(container['args'][1])) if isinstance(n,ast.Constant) and isinstance(n.value,str) and len(n.value)>100]
    if len(literals)!=1:raise ValueError('Unexpected source envelope')
    code=gzip.decompress(base64.b64decode(literals[0],validate=True)).decode();lines=code.splitlines(keepends=True)
    nodes=[n for n in ast.parse(code).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='JS_CANDIDATES' for t in n.targets)]
    if len(nodes)!=1:raise ValueError('Candidate source assignment differs')
    node=nodes[0];sources=ast.literal_eval(node.value)
    for name,source in sources.items():
        if (ROOT/'scripts'/name).read_text(encoding='utf-8')!=source:raise ValueError('Reviewed shared source drift: '+name)
    sources.update({n:(ROOT/'scripts'/n).read_text(encoding='utf-8') for n in FILES})
    lines[node.lineno-1:node.end_lineno]=['JS_CANDIDATES='+repr(sources)+'\n'];code=''.join(lines)
    deltas={"PREFIX='candidate-stage33-ms-note-v1/'":"PREFIX='candidate-stage34-t-integration-v1/'",
        "if row['subject_id']!='alpha-world-ms':continue":"if row['subject_id']!='alpha-world-t':continue",
        "Path('/app/scripts/render_alpha_t_presentation.mjs')":"(root/'render_alpha_t_presentation.mjs')"}
    for old,new in deltas.items():
        if code.count(old)!=1:raise ValueError('Trial scope anchor differs')
        code=code.replace(old,new)
    return code
def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    job=b.request('GET',a.PATH+b.API)
    resume=protected(ROOT/'.work/platform-upgrade-20261011-stage34/resume-v1/result.json')
    if b.bindings([job])[a.JOB]!=resume['boundary']['workloads'][a.JOB]:raise ValueError('Saved worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve previous trial')
        prior=protected(PREV/'intent.json');result=protected(PREV/'result.json')
        if not result['passed']:raise ValueError('Reviewed source required')
        code=code_for_trial(prior)
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=resume['boundary']['workloads']:raise ValueError('Workload drift')
        template=b.corrected_template(job,code);template['containers'][0]['env']=prior['template']['containers'][0]['env']
        save(OUT,'intent',{'template':template,'sources':hashes(),'previous':result['protected_receipt'],
            'scope':'One T QA pair using corrected shared renderer and exact pinned adapter. QA SELECT-only, no production connection, registration, approvals or saved-job change.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting receipt ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (proof.get('passed') is not True or proof.get('artifacts')!=2 or [r['subject'] for r in proof['subjects']]!=['alpha-world-t'] or not proof.get('qa_state_unchanged') or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete T-only proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main(*sys.argv[1:])
