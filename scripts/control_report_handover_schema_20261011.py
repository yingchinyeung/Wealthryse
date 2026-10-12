"""One-off read-only schema trial on the unchanged installed website image."""
import hashlib,json,sys
from pathlib import Path
import release_report_handover_20261011 as release
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import execution_env,save
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261011-stage42/schema-v1'
JOB='ibkrv2stg-inbox-acceptance-r2';PATH=b.rid('Microsoft.App/jobs',JOB)

def sources():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'probe_report_handover_schema_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown action')
    release.unchanged();job=b.request('GET',PATH+b.API)
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior trial')
        b.runtime.safe_window();b.runtime.idle(JOB)
        code=(ROOT/'scripts/probe_report_handover_schema_20261011.py').read_text()
        template=b.corrected_template(job,code);container=template['containers'][0];container['image']=release.BASE
        container['env']=[e for e in container.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        refs=[e for e in container['env'] if e['name']=='DATABASE_URL']
        if len(refs)!=1 or not refs[0].get('secretRef') or refs[0].get('value'):raise ValueError('Existing secret reference required')
        save(OUT,'intent',{'template':template,'sources':sources(),'image':release.BASE,'scope':'SELECT-only schema and duplicate-release count; no portfolio records exported; no saved-job changes.'})
        ack=b.request('POST',PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=sources():raise ValueError('Source drift')
    execution=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',PATH+'/executions/'+execution+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Runtime mismatch')
    execution_env(JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='REPORT_HANDOVER_SCHEMA_FINAL ' if status=='Succeeded' else 'REPORT_HANDOVER_SCHEMA_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting proof');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded' and proof.get('passed') is True
    if passed and (proof['identity']!={'role':'ibkr_stg_web','ro':'on','ssl':True} or proof['database_writes']!=0):raise ValueError('Unsafe proof')
    name='result' if passed else 'failure'
    if not (OUT/(name+'.json')).exists():save(OUT,name,{'passed':passed,'execution':execution,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps({'passed':passed,'proof':proof}))

if __name__=='__main__':main(sys.argv[1])
