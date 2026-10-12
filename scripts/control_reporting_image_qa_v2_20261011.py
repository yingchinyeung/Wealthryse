"""Installed v4 candidate, fresh private output prefix, no live change."""
import json,sys
import release_reporting_full_nav_20261011
import control_reporting_image_qa_20261011 as c
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
c.OUT=c.OUT.parent/'image-qa-v2'
c.FILES.append('scripts/control_reporting_image_qa_v2_20261011.py')
original=c.probe_source
def probe_source():
    source=original();old="PREFIX='candidate-stage34-image-qa-v1/'"
    if source.count(old)!=1:raise ValueError('Exact private prefix required')
    return source.replace(old,"PREFIX='candidate-stage34-image-qa-v2/'")
c.probe_source=probe_source
def read():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Frozen QA source drift')
    name=protected(c.OUT/'dispatch.json')['execution'];run=c.b.request('GET',c.a.PATH+'/executions/'+name+c.b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(c.a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_CORRECTED_FINAL ' if state=='Succeeded' else 'UPGRADE_CORRECTED_ERROR '
    rows=c.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting native receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded':
        expected={'alpha-world-ms':'passed','alpha-world-t':'passed','U16088571':None,'U16088606':None,'hong-kong-china':None}
        if not proof['passed'] or proof['artifacts']!=10 or not proof['qa_state_unchanged'] or proof['database_writes']!=0 or proof['registered_reports']!=0 or proof['production_connection_used'] is not False or len(proof['subjects'])!=5:raise ValueError('Incomplete isolation proof')
        if {s['subject']:s['validation_status'] for s in proof['subjects']}!=expected:raise ValueError('Validator receipt schema differs')
        for s in proof['subjects']:
            if len(s['artifacts'])!=2 or not s['nav_and_performance_unchanged']:raise ValueError('Incomplete output')
            if s['validation_status'] is None and s['contract']!='platform-standard-v24-reporting-currency-full-nav-v9':raise ValueError('Wrong generic contract')
    label='result' if state=='Succeeded' else 'failure'
    if not (c.OUT/(label+'.json')).exists():save(c.OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'image':intent['image'],'deployed':False,'independent_download_comparison_required':True})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':
    if sys.argv[1:] == ['start']:c.main('start')
    elif sys.argv[1:] == ['read']:read()
    else:raise ValueError('Start or read only')
