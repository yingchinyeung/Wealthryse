"""One-shot isolated PostgreSQL rehearsals on the existing recovery clone."""
import hashlib
import json
from pathlib import Path
import sys
from release_upgrade_institutional_security_20261010 import OUT, SOURCE, JOBS, API
from deploy_viewer_private_admin_20261008 import request, rid
from deploy_ms_presentation_worker_release import settings_invariant
from verify_installed_ms_worker_release import corrected_template
from scan_ms_presentation_worker_candidate import verify
from rehearse_ms_worker_image_rollback import restore_protected
from control_upgrade_legacy_recovery_probe_20261009 import protected
from run_specialist_gateway_read_verification import retain
from audit_azure_runtime import az
import deploy_t_runtime_release_20261009 as runtime
runtime.request = request
JOB = 'ibkrv2stg-institutional-daily'
PATH = rid('Microsoft.App/jobs', JOB)
PROBE = Path(__file__).with_name('probe_upgrade_institutional_postgres_20261010.py')
CHECKS = {'queue_idempotency','dependency_and_lease_exclusion','wrong_lease_owner_rejected',
          'successful_dependency_advancement','broker_failure_no_retry_and_descendant_block',
          'discovery_empty_queue','discovery_ineligible_source_blocked','discovery_single_attempt_claim',
          'sector_upsert_and_unchanged_replay'}


def candidate():
    _, _, output = verify(OUT)
    image = output['registry'] + '/' + output['repository'] + '@' + output['digest']
    scan = json.loads((OUT / 'scan-completed.json').read_bytes())
    remote, _ = restore_protected(json.loads((OUT / 'scan-protected-receipt.json').read_bytes()))
    if scan != remote or scan['image'] != image or scan['assessment']['status'] != 'pass' or scan['assessment']['blocking_findings']:
        raise ValueError('Protected exact-image passing scan required')
    return image


def save(folder, name, data):
    with (folder / (name + '.json')).open('x', encoding='utf-8') as f:
        json.dump({**data,'protected_receipt':retain(data,'upgrade-institutional-'+folder.name+'-'+name)},f,indent=2)


def accept(proof):
    if not proof.get('passed') or set(proof.get('checks',[])) != CHECKS or len(proof.get('checks',[])) != len(CHECKS):
        raise ValueError('Complete SQL rehearsal required')
    if proof.get('public_tables_checked') != 92 or proof.get('public_table_differences') != [] or proof.get('temporary_relations') != 7:
        raise ValueError('Recovery preservation incomplete')
    if not proof.get('database_read_only_enforced') or not proof.get('temporary_objects_dropped_on_disconnect'):
        raise ValueError('Database safety guard missing')
    if any(proof.get(k) != 0 for k in ('production_connections','production_writes','persistent_clone_writes','broker_calls','market_calls')):
        raise ValueError('Disallowed external side effects')


def main(stage, mode):
    if mode not in ('candidate','fallback'): raise ValueError('Unknown mode')
    target = candidate(); image = SOURCE if mode == 'fallback' else target
    folder = OUT / ('postgres-' + mode + '-v1')
    job = request('GET', PATH + API)
    expected = json.loads((OUT / 'source-intent.json').read_bytes())['before_jobs'][JOB]
    if job['properties']['template']['containers'][0]['image'] != SOURCE or settings_invariant(job) != expected['settings_invariant_sha256']:
        raise ValueError('Installed job drift')
    if stage == 'start':
        if folder.exists(): raise ValueError('Existing execution: reconcile rather than repeat')
        runtime.safe_window(); runtime.idle(JOB)
        code = PROBE.read_text(encoding='utf-8'); compile(code,'isolated-institutional','exec')
        template = corrected_template(job,code); c = template['containers'][0]; c['image'] = image
        c['env'] = [e for e in c.get('env',[]) if e['name'] in ('DATABASE_URL','PORTFOLIO_TENANT_ID')]
        if {e['name'] for e in c['env']} != {'DATABASE_URL','PORTFOLIO_TENANT_ID'} or not next(e for e in c['env'] if e['name']=='DATABASE_URL').get('secretRef'):
            raise ValueError('Exact referenced database environment required')
        folder.mkdir()
        save(folder,'intent',{'image':image,'template':template,'source_sha256':hashlib.sha256(code.encode()).hexdigest(),
             'job_invariant':settings_invariant(job),'scope':'Recovery clone only, seven synthetic temporary relations, server-enforced persistent read-only transactions. No broker/market calls.'})
        ack = request('POST',PATH+'/start'+API,data=template)
        save(folder,'dispatch',{'execution':ack['name']}); print(json.dumps({'execution':ack['name']})); return
    if stage != 'read': raise ValueError('Unknown stage')
    intent = protected(folder/'intent.json'); execution = protected(folder/'dispatch.json')['execution']
    run = request('GET',PATH+'/executions/'+execution+API)
    actual = run['properties']['template']['containers'][0]; expected = intent['template']['containers'][0]
    if any(actual.get(k) != expected.get(k) for k in ('image','command','args')): raise ValueError('Executed probe differs')
    state = run['properties']['status']; print(json.dumps({'mode':mode,'state':state}))
    if state not in ('Succeeded','Failed'): return
    prefix = 'INSTITUTIONAL_POSTGRES_PROOF '
    query = "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"-' | where Log_s startswith '"+prefix+"' | project Result=parse_json(substring(Log_s,"+str(len(prefix))+'))'
    rows = az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if state != 'Succeeded': raise ValueError('Runtime failed; inspect bounded non-sensitive diagnostics')
    if len(rows) != 1: print('Waiting for final proof ingestion'); return
    proof = json.loads(rows[0]['Result']); accept(proof)
    if not (folder/'result.json').exists(): save(folder,'result',{'passed':True,'image':image,'execution':execution,'proof':proof,'saved_job_preserved':True})
    print(json.dumps({'mode':mode,'passed':True,'checks':len(proof['checks']),'public_tables_unchanged':92}))


if __name__ == '__main__': main(*sys.argv[1:])
