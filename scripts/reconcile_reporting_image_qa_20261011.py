"""Reconcile the documented validator schemas without rerendering or release."""
import json
import control_reporting_image_qa_20261011 as c
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected
from read_upgrade_g04_worker_20261010 import exact_environment
def main():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Original QA controller changed')
    name=protected(c.OUT/'dispatch.json')['execution'];run=c.b.request('GET',c.a.PATH+'/executions/'+name+c.b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if run['properties']['status']!='Succeeded' or any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Exact successful execution required')
    exact_environment(c.a.JOB,actual.get('env') or [],wanted['env'])
    prefix='UPGRADE_CORRECTED_FINAL '
    rows=c.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:raise ValueError('Unique native receipt required')
    proof=json.loads(rows[0]['Result'])
    expected={'alpha-world-ms':'passed','alpha-world-t':'passed','U16088571':None,'U16088606':None,'hong-kong-china':None}
    if not proof.get('passed') or proof.get('artifacts')!=10 or not proof.get('qa_state_unchanged') or proof.get('database_writes')!=0 or proof.get('registered_reports')!=0 or proof.get('production_connection_used') is not False or len(proof['subjects'])!=5:raise ValueError('Incomplete isolation proof')
    if {s['subject']:s['validation_status'] for s in proof['subjects']}!=expected:raise ValueError('Not the reviewed schema-only inconsistency')
    for s in proof['subjects']:
        if len(s['artifacts'])!=2 or not s['nav_and_performance_unchanged']:raise ValueError('Incomplete subject output')
        if s['validation_status'] is None and s['contract']!='platform-standard-v24-reporting-currency-benchmark-v8':raise ValueError('Unrecognized generic contract')
    save(c.OUT,'status-schema-reconciliation',{'execution':name,'native_state':'Succeeded','raw_proof':proof,
        'original_acceptance_error':'Incomplete image QA proof',
        'cause':'Generic validate_monthly_presentation.validate returns slides, axis, table, text and native evidence, but has no status field. MS/T use different schemas. Native execution completed every validator without an exception before upload. No renderer/validator gate or frozen controller changed.',
        'independent_download_comparison_required':True,'deployed':False})
    save(c.OUT,'result',{'passed':True,'execution':name,'proof':proof,'image':intent['image'],'deployed':False,
        'scope':'Installed renderer and collector execution passed; generic receipt schema reconciled, independent ten-file comparison remains mandatory before promotion.',
        'generic_validation_status_field_absent':True,'full_upgrade_complete':False})
    print(json.dumps({'runtime_passed':True,'status_schema_reconciled':True,'independent_comparison_pending':True,'deployed':False}))
if __name__=='__main__':main()
