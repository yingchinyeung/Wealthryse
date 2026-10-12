"""Verify every bounded monthly receipt and preserve raw date strings."""
import json
import control_reporting_history_readiness_v2_20261011 as wrapper
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
from read_upgrade_g04_worker_20261010 import exact_environment
c=wrapper.c;b=c.b;a=c.a
def main():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Readiness source drift')
    name=protected(c.OUT/'dispatch.json')['execution'];run=b.request('GET',a.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    exact_environment(a.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    def read(prefix):
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
        return [json.loads(row['Result']) for row in rows]
    if state=='Failed':
        values=read('REPORTING_READINESS_ERROR ')
        if len(values)!=1:print('Awaiting failure receipt');return
        if not (c.OUT/'failure.json').exists():save(c.OUT,'failure',{'passed':False,'proof':values[0],'execution':name})
        print(json.dumps(values[0]));return
    values=read('REPORTING_READINESS_FINAL ')
    if len(values)!=1:print('Awaiting final receipt');return
    final=values[0];items=read('REPORTING_READINESS_ITEM ')
    if len(items)!=final['unready_count']:print('Awaiting all bounded items');return
    if not final['passed'] or final['snapshots']!=105 or final['database_writes']!=0 or final['production_connection_used'] is not False or len({(r['subject'],r['period_end']) for r in items})!=len(items) or sum(final['ready_by_subject'].values())+len(items)!=105:raise ValueError('Incomplete readiness proof')
    final['unready']=sorted(items,key=lambda r:(r['subject'],r['period_end']))
    if not (c.OUT/'result.json').exists():save(c.OUT,'result',{'passed':True,'proof':final,'execution':name,'deployed':False})
    print(json.dumps({'ready_by_subject':final['ready_by_subject'],'unready':[{k:r.get(k) for k in ('subject','period_end','monthly_present','rebuilt_available','rebuilt_quality')} for r in final['unready']]}))
if __name__=='__main__':main()
