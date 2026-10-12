"""Read only the source-bound replay diagnostic, never weaken migration gates."""
import json
import control_reporting_correction_diagnostic_20261011 as wrapper
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import execution_env,save
c=wrapper.c;b=c.b;CASES=63
def main():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Diagnostic source drift')
    job=b.request('GET',b.PATH+b.API)
    if intent['binding']!=b.bindings([job]):raise ValueError('Saved job drift')
    name=protected(c.OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Diagnostic execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_MIGRATION_FINAL ' if state=='Succeeded' else 'REPORTING_MIGRATION_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting diagnostic receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (not proof.get('passed') or not proof.get('diagnostic_only') or proof.get('cases')!=CASES or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False):raise ValueError('Incomplete read-only diagnosis')
    label='result' if state=='Succeeded' else 'failure'
    if not (c.OUT/(label+'.json')).exists():save(c.OUT,label,{'passed':state=='Succeeded','proof':proof,'execution':name,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main()
