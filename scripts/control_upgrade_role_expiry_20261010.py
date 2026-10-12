"""Existing bootstrap identities, SELECT-only system catalog inspection."""
import hashlib, json, sys
from pathlib import Path
from control_upgrade_legacy_dns_20261010 import request, rid, bindings, current_resources
from control_upgrade_readers_20261010 import save, execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
from verify_installed_ms_worker_release import corrected_template
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'.work/platform-upgrade-20261010-stage28/role-expiry-v1'
API = '?api-version=2026-07-01'
SCOPES = {
 'main': ('ibkrv2stg-bootstrap','ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com','5264a124-10cd-45f6-93a5-6d65c37e9bfb'),
 'legacy': ('ibkr-v11-bootstrap','ibkrv11-pg-5rcc62xxqblay.postgres.database.azure.com','f594ac4f-31eb-4684-a5af-a8adc5020cfa')}


def hashes():
    return {name: hashlib.sha256((ROOT/'scripts'/name).read_bytes()).hexdigest()
        for name in (Path(__file__).name,'probe_upgrade_role_expiry_20261010.py')}


def accept(proof, host):
    if proof.get('passed') is not True or proof.get('host') != host or not proof.get('connection_role'):
        raise ValueError('Wrong result identity')
    if proof.get('database_readonly') is not True or proof.get('verified_tls') is not True or proof.get('database_writes') != 0:
        raise ValueError('Read-only boundary failed')
    if proof.get('credentials_changed') is not False or proof.get('password_hashes_read') is not False or proof.get('actual_last_rotation_verified') is not False:
        raise ValueError('Credential scope differs')
    if not 1 <= len(proof.get('roles',[])) <= 100 or any(r['expiry_state'] not in ('no_database_expiration','expired','future_expiration') for r in proof['roles']):
        raise ValueError('Role proof incomplete')


def main(stage, scope):
    if stage not in ('start','read') or scope not in SCOPES:
        raise ValueError('Unknown operation')
    jobname, host, workspace = SCOPES[scope]
    path = rid('Microsoft.App/jobs',jobname)
    job = request('GET',path+API)
    folder = OUT/scope
    if stage == 'start':
        runtime.safe_window(); runtime.idle(jobname)
        if folder.exists():
            raise ValueError('Preserve previous execution; inspect instead of retrying')
        baseline = protected(ROOT/'.work/platform-upgrade-20261010-stage28/legacy-dns-v1/plan.json')['boundary']['workloads']
        if bindings(current_resources()) != baseline:
            raise ValueError('Current workloads differ from reviewed images and settings')
        code = 'EXPECTED_HOST='+repr(host)+'\n'+(ROOT/'scripts/probe_upgrade_role_expiry_20261010.py').read_text(encoding='utf-8')
        template = corrected_template(job,code)
        template['containers'][0]['env'] = [e for e in template['containers'][0].get('env',[]) if e['name']=='DATABASE_ADMIN_URL']
        env = template['containers'][0]['env']
        if len(env)!=1 or not env[0].get('secretRef') or env[0].get('value'):
            raise ValueError('Exactly the existing database admin reference required')
        save(folder,'intent',{'template':template,'job_binding':bindings([job]),'source_hashes':hashes(),
            'scope':'SELECT-only role expiry metadata, no password hashes or normal bootstrap'})
        ack = request('POST',path+'/start'+API,data=template)
        save(folder,'dispatch',{'execution':ack['name']})
        print(json.dumps({'scope':scope,'execution':ack['name']}));return
    intent=protected(folder/'intent.json')
    if intent['source_hashes']!=hashes() or intent['job_binding']!=bindings([job]):
        raise ValueError('Source or saved job drift')
    name=protected(folder/'dispatch.json')['execution']
    execution=request('GET',path+'/executions/'+name+API)
    wanted=intent['template']['containers'][0];actual=execution['properties']['template']['containers'][0]
    if any(wanted.get(k)!=actual.get(k) for k in ('image','command','args')):
        raise ValueError('Execution differs')
    execution_env(jobname,actual.get('env') or [],wanted['env'])
    state=execution['properties']['status']
    if state not in ('Succeeded','Failed'):
        print(state);return
    if state=='Failed':
        raise ValueError('Read-only metadata query failed; preserve execution')
    prefix='UPGRADE_ROLE_EXPIRY '
    rows=az('monitor','log-analytics','query','-w',workspace,'--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))')
    if len(rows)!=1:
        print('Awaiting proof ingestion');return
    proof=json.loads(rows[0]['Result']);accept(proof,host)
    if not (folder/'result.json').exists():
        save(folder,'result',{'passed':True,'execution':name,'proof':proof,'saved_job_unchanged':True})
    print(json.dumps({'scope':scope,'passed':True,'roles':len(proof['roles']),
        'expired_roles':[r['rolname'] for r in proof['roles'] if r['expiry_state']=='expired']}))


if __name__=='__main__':
    main(*sys.argv[1:])
