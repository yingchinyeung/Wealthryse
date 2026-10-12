"""Re-render the qualified QA inputs after the bounded conclusion correction."""
import ast, base64, gzip, hashlib, inspect, json, sys
from pathlib import Path
import control_upgrade_corrected_reports_20261011 as base
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment

b=base.b; a=base.a; ROOT=base.ROOT
OUT=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v4'
PREV=ROOT/'.work/platform-upgrade-20261011-stage33/corrected-reports-v3'
def hashes():
    names=[*base.FILES,'scripts/report_summary_label.mjs','src/ibkr_etl/monthly_artifacts.py','scripts/'+Path(__file__).name]
    return {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    job=b.request('GET',a.PATH+b.API)
    if b.bindings([job])[a.JOB]!=baseline['bindings'][a.JOB]:raise ValueError('Reporting binding drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior attempt')
        previous=protected(PREV/'result.json'); prior=protected(PREV/'intent.json')
        if previous.get('passed') is not True or previous['proof'].get('artifacts')!=10:raise ValueError('Previous candidate incomplete')
        for name,digest in prior['sources'].items():
            if name!='scripts/render_monthly_presentation.mjs' and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:
                raise ValueError('Unrelated source drift: '+name)
        template=prior['template']; container=template['containers'][0]
        # The prior hash-bound command embeds source only and secret references.
        args=container['args']
        if len(args)!=2 or args[0]!='-c' or container['command']!=['python']:raise ValueError('Unexpected command envelope')
        literals=[n.value for n in ast.walk(ast.parse(args[1])) if isinstance(n,ast.Constant) and isinstance(n.value,str) and len(n.value)>100]
        if len(literals)!=1:raise ValueError('Expected one compressed source')
        code=gzip.decompress(base64.b64decode(literals[0],validate=True)).decode('utf-8');lines=code.splitlines(keepends=True)
        assignments=[n for n in ast.parse(code).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='JS_CANDIDATES' for t in n.targets)]
        if len(assignments)!=1:raise ValueError('Expected candidate source assignment')
        node=assignments[0]; sources=ast.literal_eval(node.value)
        if set(sources)!=set(base.JS):raise ValueError('Unexpected candidate JS inventory')
        if hashlib.sha256(sources['render_monthly_presentation.mjs'].encode()).hexdigest()!=prior['sources']['scripts/render_monthly_presentation.mjs']:
            raise ValueError('Embedded renderer differs from prior source hash')
        sources['render_monthly_presentation.mjs']=(ROOT/'scripts/render_monthly_presentation.mjs').read_text(encoding='utf-8')
        sources['report_summary_label.mjs']=(ROOT/'scripts/report_summary_label.mjs').read_text(encoding='utf-8')
        lines[node.lineno-1:node.end_lineno]=['JS_CANDIDATES='+repr(sources)+'\n']
        code=''.join(lines)
        if code.count("PREFIX='candidate-stage33-v3/'")!=1:raise ValueError('Expected prior prefix')
        code=code.replace("PREFIX='candidate-stage33-v3/'","PREFIX='candidate-stage33-v4/'")
        from ibkr_etl.monthly_artifacts import _specific_region
        region=inspect.getsource(_specific_region).replace('\r\n','\n')
        anchor='    from ibkr_etl.monthly_artifacts import render_factsheet'
        if code.count(anchor)!=1:raise ValueError('Expected one renderer import')
        replacement="    import inspect\n    import ibkr_etl.monthly_artifacts as ma\n    if sha(inspect.getsource(ma._specific_region).replace('\\r\\n','\\n').encode())!='cad566d67db04e02e1dea897fbb6eb3787adb709c5d71df58e3233e388c28548':raise ValueError('Installed listing-region source drift')\n    exec("+repr(region)+",ma.__dict__)\n"+anchor
        code=code.replace(anchor,replacement)
        candidate=b.corrected_template(job,code)
        container['command']=candidate['containers'][0]['command'];container['args']=candidate['containers'][0]['args']
        b.runtime.safe_window();b.runtime.idle(a.JOB)
        if b.bindings(b.current_resources())!=baseline['bindings']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'previous':previous['protected_receipt'],
            'scope':'Same read-only QA inputs and validators; bounded generic conclusion and original listing-currency region labels; ten new private QA outputs. Exact installed region function checked before source-only overlay. No registration, publication, saved job update or production connection.'})
        ack=b.request('POST',a.PATH+'/start'+b.API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution']; run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution drift')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('artifacts')!=10 or proof.get('database_writes')!=0 or proof.get('registered_reports')!=0 or not proof.get('qa_state_unchanged') or proof.get('production_connection_used') is not False:raise ValueError('Incomplete isolation evidence')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'full_upgrade_complete':False})
    print(json.dumps({'passed':True,'artifacts':10,'deployed':False,'visual_acceptance':'UNVERIFIED'}))
if __name__=='__main__':main(*sys.argv[1:])
