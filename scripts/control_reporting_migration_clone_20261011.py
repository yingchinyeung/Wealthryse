"""One isolated clone for versioned reporting migration and rollback tests."""
import hashlib,json,sys
from pathlib import Path
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=b.ROOT;OUT=ROOT/'.work/platform-upgrade-20261011-stage34/migration-clone-v1'
FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','test_reporting_migration_clone_20261011.py']
def hashes():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in FILES}

def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    baseline=protected(OUT.parent/'resume-v1/result.json');job=b.request('GET',b.PATH+b.API)
    if b.bindings([job])[b.JOB]!=baseline['boundary']['workloads'][b.JOB]:raise ValueError('Bootstrap drift')
    server=b.request('GET',b.SERVER+'?api-version=2024-08-01')
    if server['location'].replace(' ','').lower()!='eastasia' or server['properties']['state']!='Ready' or server['properties']['network']['publicNetworkAccess']!='Disabled':raise ValueError('Private East Asia recovery required')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve prior attempt; reconcile without retry')
        prior=protected(OUT.parent/'historical-reconciled-read-v1/result.json')
        raw=(OUT.parent/'historical-reconciled-read-v1/inventory.json').read_bytes()
        if not prior['passed'] or prior['blockers'] or hashlib.sha256(raw).hexdigest()!=prior['inventory_sha256']:raise ValueError('Reconciled history proof required')
        inventory=json.loads(raw)
        code='EXPECTED_REPORT_HASHES='+repr(inventory['qa_fingerprints'])+'\n'+(ROOT/'scripts'/FILES[1]).read_text(encoding='utf-8')
        template=b.corrected_template(job,code)
        env=template['containers'][0]['env']=[e for e in template['containers'][0]['env'] if e['name']=='DATABASE_ADMIN_URL']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):raise ValueError('Existing admin reference only')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        if b.bindings(b.current_resources())!=baseline['boundary']['workloads']:raise ValueError('Workload drift')
        save(OUT,'intent',{'template':template,'sources':hashes(),'binding':b.bindings([job]),'inventory_sha256':prior['inventory_sha256'],
            'scope':'Create one separate private recovery-server QA database from existing QA. Preserve all 97 tables including reports and approvals. No production connection, truncation, deletion, build upload or saved-job change.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=hashes() or intent['binding']!=b.bindings([job]):raise ValueError('Source/job drift')
    name=protected(OUT/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(b.JOB,actual.get('env') or [],wanted['env']);state=run['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    prefix='REPORTING_MIGRATION_CLONE ' if state=='Succeeded' else 'REPORTING_MIGRATION_CLONE_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting clone receipt');return
    proof=json.loads(rows[0]['Result'])
    if state=='Succeeded' and (proof.get('passed') is not True or proof.get('database')!='upgrade_g04_migration_20261011' or proof.get('tables_checked')!=97 or not proof.get('source_unchanged') or not proof.get('clone_identical') or proof.get('production_connection_used') is not False or proof.get('report_records_changed')!=0):raise ValueError('Incomplete isolation proof')
    label='result' if state=='Succeeded' else 'failure'
    if not (OUT/(label+'.json')).exists():save(OUT,label,{'passed':state=='Succeeded','execution':name,'proof':proof,'deployed':False,'saved_job_unchanged':True})
    print(json.dumps({'state':state,'passed':proof.get('passed',False),'database':proof.get('database'),'type':proof.get('type')}))
if __name__=='__main__':main(*sys.argv[1:])
