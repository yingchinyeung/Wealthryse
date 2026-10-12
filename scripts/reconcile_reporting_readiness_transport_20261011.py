"""Preserve the successful execution but reject its truncated proof transport."""
import json
import control_reporting_history_readiness_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from read_upgrade_g04_worker_20261010 import exact_environment
def main():
    intent=protected(c.OUT/'intent.json');name=protected(c.OUT/'dispatch.json')['execution']
    run=c.b.request('GET',c.a.PATH+'/executions/'+name+c.b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if intent['sources']!=c.hashes() or run['properties']['status']!='Succeeded' or any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Unexpected readiness execution')
    exact_environment(c.a.JOB,actual.get('env') or [],wanted['env'])
    prefix='REPORTING_READINESS_FINAL '
    rows=c.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:raise ValueError('One original receipt required')
    raw=rows[0]['Result']
    try:json.loads(raw)
    except json.JSONDecodeError:pass
    else:raise ValueError('Original transport is not truncated')
    if not (c.OUT/'transport-failure.json').exists():save(c.OUT,'transport-failure',{'execution':name,'native_status':'Succeeded','proof_accepted':False,'reason':'Native final line exceeded log transport capacity; incomplete JSON is not acceptance','truncated_receipt':raw,'rerun_scope':'read-only bounded per-month receipts'})
    print(json.dumps({'preserved':True,'proof_accepted':False,'characters':len(raw)}))
if __name__=='__main__':main()
