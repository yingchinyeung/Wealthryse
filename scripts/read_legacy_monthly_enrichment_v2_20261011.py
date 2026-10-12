"""Bind private fix evidence and retain only the installed application source."""
import base64,hashlib,json,zlib
import control_legacy_monthly_enrichment_v2_20261011 as wrapper
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save,execution_env
from ibkr_etl.util import canonical_json
c=wrapper.c;b=c.b
def main():
    intent=protected(c.OUT/'intent.json')
    if intent['sources']!=c.hashes():raise ValueError('Candidate source drift')
    name=protected(c.OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    def read(prefix):
        rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
        return [json.loads(r['Result']) for r in rows]
    values=read('LEGACY_MONTHLY_FINAL ' if state=='Succeeded' else 'LEGACY_MONTHLY_ERROR ')
    if len(values)!=1:print('Awaiting native receipt');return
    proof=values[0]
    if state=='Succeeded':
        items=sorted(read('LEGACY_MONTHLY_ITEM '),key=lambda r:r['period_end']);captures=read('MONTHLY_INSTALLED_SOURCE ')
        if len(items)!=20 or len(captures)!=1:print('Awaiting all source/month receipts');return
        if not proof['passed'] or proof['native_tests']!=32 or proof['database_writes']!=0 or proof['production_connection_used'] is not False or hashlib.sha256(canonical_json(items).encode()).hexdigest()!=proof['plan_sha256']:raise ValueError('Incomplete source-only reconstruction proof')
        capture=captures[0];raw=zlib.decompress(base64.b64decode(capture['zlib_base64'],validate=True))
        if len(raw)>128*1024 or hashlib.sha256(raw).hexdigest()!=capture['sha256'] or capture['sha256']!=proof['installed_monthly_sha256']:raise ValueError('Installed source integrity failed')
        path=c.OUT/'installed_monthly_reporting_analytics.py'
        if path.exists():
            if path.read_bytes()!=raw:raise ValueError('Preserve installed source capture')
        else:
            with path.open('xb') as f:f.write(raw)
        proof['months_detail']=items
    label='result' if state=='Succeeded' else 'failure'
    if not (c.OUT/(label+'.json')).exists():save(c.OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False})
    print(json.dumps({'state':state,'proof':{k:v for k,v in proof.items() if k!='months_detail'}}))
if __name__=='__main__':main()
