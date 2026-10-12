"""One create-only recovery-server QA preparation; never write production."""
import hashlib,json,sys
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage31/fresh-reporting-db-v1'

def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in ('control_upgrade_fresh_reporting_db_20261011.py','prepare_upgrade_fresh_reporting_remote_20261011.py','test_upgrade_fresh_reporting_boundary_20261011.py')}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown stage')
    baseline=protected(ROOT/'.work/platform-upgrade-20261011-stage31/resume-v1/result.json')
    scope=protected(ROOT/'.work/platform-upgrade-20261011-stage31/reporting-scope-v1/result.json')
    tables=['onboarding_historical_report_items','report_artifact_releases','report_generation_runs','report_release_decisions','reporting_snapshots']
    if scope['proof']['report_metadata_dependency_closure']!=tables:raise ValueError('Unexpected metadata dependencies')
    job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['bindings'][b.JOB]:raise ValueError('Bootstrap drift')
    server=b.request('GET',b.SERVER+'?api-version=2024-08-01')
    if server['location'].replace(' ','').lower()!='eastasia' or server['properties']['state']!='Ready' or server['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Private East Asia recovery required')
    if stage=='start':
        if OUT.exists():raise ValueError('Reconcile prior creation, never overwrite')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        original=protected(ROOT/'.work/platform-upgrade-20261010-stage29/g04-database-v1/result.json')
        code='EXPECTED_TABLES='+repr(original['proof']['tables'])+'\n'+(ROOT/'scripts/prepare_upgrade_fresh_reporting_remote_20261011.py').read_text(encoding='utf-8')
        template=b.corrected_template(job,code);template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(template['containers'][0]['env'])!=1 or not template['containers'][0]['env'][0].get('secretRef'):raise ValueError('Existing admin secret reference required')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'new_database':'upgrade_g04_fresh_20261011',
            'scope':'Create isolated copy on existing recovery server. Clear exactly five copied report-metadata tables with RESTRICT; preserve all 92 other tables and original recovery source.',
            'production_connection_authorized':False,'recovery':'Retain isolated copy if interrupted; no drop, overwrite, publication or source mutations'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes():raise ValueError('Source drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env'])
    state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='UPGRADE_FRESH_REPORTING_DB ' if state=='Succeeded' else 'UPGRADE_FRESH_REPORTING_DB_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting QA preparation proof');return
    proof=json.loads(rows[0]['Result'])
    if state=='Failed':
        if not (OUT/'failure.json').exists():save(OUT,'failure',{'execution':name,'proof':proof})
        print(json.dumps({'failed':True,'proof':proof}));return
    if proof.get('passed') is not True or proof.get('database')!='upgrade_g04_fresh_20261011' or proof.get('preserved_nonreport_tables')!=92 or proof.get('source_unchanged') is not True or proof.get('production_connection_used') is not False:raise ValueError('Isolation proof incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps({'passed':True,'tables_checked':97,'preserved_nonreport_tables':92}))

if __name__=='__main__':main(*sys.argv[1:])
