"""One-time test-copy creation on the isolated recovery server, not production."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as p
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=p.ROOT;OUT=ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1'


def hashes():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        (Path(__file__).name,'create_upgrade_g04_database_remote_20261010.py')}


def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    preflight=protected(p.OUT/'result.json');proof=preflight['proof']
    if preflight.get('passed') is not True or proof['host']!='ibkrv2stg-upgrade-recovery-1009.postgres.database.azure.com' or not proof['can_create_database'] or proof['test_database_exists'] or proof['database']!='ibkr_staging' or proof['addresses']!=['10.50.11.4']:
        raise ValueError('Exact isolated preflight required')
    job=p.request('GET',p.PATH+p.API)
    original=protected(p.OUT/'intent.json')
    if p.bindings([job])!=original['binding']:raise ValueError('Bootstrap changed')
    clone=p.request('GET',p.SERVER+'?api-version=2024-08-01')
    expected=protected(ROOT/'.work/platform-upgrade-20261009-stage8/fresh-recovery/plan.json')['restore_body']['properties']['network']
    if clone['location'].replace(' ','').lower()!='eastasia' or clone['properties']['state']!='Ready' or clone['properties']['network']!=expected:raise ValueError('Recovery boundary differs')
    if stage=='start':
        p.runtime.safe_window();p.runtime.idle(p.JOB)
        if OUT.exists():raise ValueError('Reconcile prior creation; never blind retry')
        code='EXPECTED_COUNTS='+repr(proof['table_counts'])+'\n'+(ROOT/'scripts/create_upgrade_g04_database_remote_20261010.py').read_text(encoding='utf-8')
        template=p.corrected_template(job,code)
        template['containers'][0]['env']=original['template']['containers'][0]['env']
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':p.bindings([job]),
            'preflight_receipt':preflight['protected_receipt'],'new_database':'upgrade_g04_20261010',
            'scope':'Create-only isolated QA database inside existing private East Asia recovery server; never connect to production',
            'recovery':'Preserve test copy if interrupted; source recovery database unchanged; no drop/overwrite permitted'})
        ack=p.request('POST',p.PATH+'/start'+p.API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=p.request('GET',p.PATH+'/executions/'+name+p.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(p.JOB,actual.get('env') or [],wanted['env'])
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Copy attempt failed; inspect logs and exact database before any retry')
    prefix='UPGRADE_G04_DATABASE '
    rows=p.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    result=json.loads(rows[0]['Result'])
    if result.get('passed') is not True or result.get('host')!=proof['host'] or result.get('database')!='upgrade_g04_20261010' or result.get('source_database')!='ibkr_staging' or result.get('source_unchanged') is not True or result.get('copy_identical') is not True or result.get('production_connection_used') is not False or result.get('private_data_uploaded_to_build_service') is not False or result.get('new_databases')!=1:raise ValueError('Copy acceptance incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':result,'saved_job_unchanged':True,'g04_complete':False})
    print(json.dumps({'passed':True,'identical_tables':len(result['tables']),'g04_complete':False}))


if __name__=='__main__':main(*sys.argv[1:])
