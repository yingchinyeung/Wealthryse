import json
import control_legacy_monthly_windows_20261011 as wrapper
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save,execution_env
c=wrapper.c;b=c.b
def main():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Diagnostic source drift')
    name=protected(c.OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    def read(prefix):
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
        return [json.loads(r['Result']) for r in rows]
    values=read('LEGACY_WINDOW_FINAL ' if state=='Succeeded' else 'LEGACY_WINDOW_ERROR ')
    if len(values)!=1:print('Awaiting diagnostic receipt');return
    proof=values[0]
    if state=='Succeeded':
        items=sorted(read('LEGACY_WINDOW_ITEM '),key=lambda r:r['period_end'])
        if len(items)!=20:print('Awaiting all month receipts');return
        if not proof['passed'] or proof['database_writes']!=0 or proof['production_connection_used'] is not False or proof['enrichment_accepted'] is not False or len({r['period_end'] for r in items})!=20:raise ValueError('Incomplete diagnosis')
        proof['months_detail']=items
    label='result' if state=='Succeeded' else 'failure'
    if not (c.OUT/(label+'.json')).exists():save(c.OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False})
    print(json.dumps({'state':state,'proof':proof}))
if __name__=='__main__':main()
