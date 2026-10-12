"""Run only metadata inspection on the existing isolated recovery database."""
import hashlib,json,sys
from pathlib import Path
from control_upgrade_legacy_dns_20261010 import request,rid,bindings,current_resources
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261010-stage29/g04-preflight-v1'
JOB='ibkrv2stg-bootstrap';PATH=rid('Microsoft.App/jobs',JOB);API='?api-version=2026-07-01'
SERVER=rid('Microsoft.DBforPostgreSQL/flexibleServers','ibkrv2stg-upgrade-recovery-1009')


def source_hashes():
    return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in
        (Path(__file__).name,'probe_upgrade_g04_preflight_20261010.py')}


def main(stage):
    if stage not in ('start','read'):raise ValueError('Unknown operation')
    job=request('GET',PATH+API)
    clone=request('GET',SERVER+'?api-version=2024-08-01')
    expected=protected(ROOT/'.work/platform-upgrade-20261009-stage8/fresh-recovery/plan.json')['restore_body']['properties']['network']
    if clone['location'].replace(' ','').lower()!='eastasia' or clone['properties']['state']!='Ready' or clone['properties']['network']!=expected:
        raise ValueError('Existing private East Asia recovery boundary differs')
    if stage=='start':
        runtime.safe_window();runtime.idle(JOB)
        if OUT.exists():raise ValueError('Reconcile previous trial instead of retrying')
        baseline=protected(ROOT/'.work/platform-upgrade-20261010-stage28/legacy-dns-v1/plan.json')['boundary']['workloads']
        if bindings(current_resources())!=baseline:raise ValueError('Workloads changed')
        template=corrected_template(job,(ROOT/'scripts/probe_upgrade_g04_preflight_20261010.py').read_text(encoding='utf-8'))
        env=template['containers'][0]['env']=[e for e in template['containers'][0].get('env',[]) if e['name']=='DATABASE_ADMIN_URL']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):raise ValueError('Existing admin reference only')
        save(OUT,'intent',{'template':template,'binding':bindings([job]),'sources':source_hashes(),
            'recovery_server':SERVER,'scope':'SELECT-only recovery metadata; no production connection, database creation or data upload'})
        ack=request('POST',PATH+'/start'+API,data=template)
        save(OUT,'dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    intent=protected(OUT/'intent.json')
    if intent['sources']!=source_hashes() or intent['binding']!=bindings([job]):raise ValueError('Source or job changed')
    name=protected(OUT/'dispatch.json')['execution'];execution=request('GET',PATH+'/executions/'+name+API)
    wanted=intent['template']['containers'][0];actual=execution['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution differs')
    execution_env(JOB,actual.get('env') or [],wanted['env'])
    state=execution['properties']['status']
    if state not in ('Succeeded','Failed'):print(state);return
    if state=='Failed':raise ValueError('Preflight failed; no write is permitted')
    prefix='UPGRADE_G04_PREFLIGHT '
    rows=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result'])
    if proof.get('passed') is not True or proof.get('database_writes')!=0 or proof.get('production_connection_used') is not False or proof.get('private_data_uploaded_to_build_service') is not False or proof.get('verified_tls') is not True or proof.get('database_readonly') is not True:raise ValueError('Isolation proof incomplete')
    if not (OUT/'result.json').exists():save(OUT,'result',{'passed':True,'proof':proof,'execution':name,'saved_job_unchanged':True,'g04_complete':False})
    print(json.dumps({'passed':True,'can_create_database':proof['can_create_database'],'test_database_exists':proof['test_database_exists'],'subjects':len(proof['subjects']),'g04_complete':False}))


if __name__=='__main__':main(*sys.argv[1:])
