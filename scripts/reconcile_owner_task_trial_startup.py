"""Read-only reconciliation of the one fixed pre-SQL bundle startup failure.

Preserves protected evidence before any reviewed packaging correction/retry.
No database connection, job dispatch, settings change or automatic retry occurs.
"""
import json

import deploy_owner_task_postgres_trial as deploy


EXECUTION = 'ibkrv2stg-bootstrap-8jrej7c'


def main():
    if deploy.az('account','show','--query','id') != deploy.SUB:
        raise ValueError('Unexpected subscription')
    intent = deploy.protected(json.loads((deploy.FOLDER/'run-intent.json').read_text()),'run-intent')
    dispatch = deploy.protected(json.loads((deploy.FOLDER/'run-dispatch.json').read_text()),'run-dispatch')
    job = deploy.request('GET',deploy.PATH+'?api-version=2024-03-01')
    wanted, source = deploy.template(job,'run')
    if (dispatch['execution'] != EXECUTION or intent['source'] != source or dispatch['source'] != source
            or intent['template'] != wanted or intent['bootstrap_settings_sha256'] != deploy.settings(job)):
        raise ValueError('Exact failed execution/source required before reconciliation')
    run = deploy.request('GET',deploy.PATH+'/executions/'+EXECUTION+'?api-version=2024-03-01')
    actual = run['properties']['template']
    if run['properties']['status'] != 'Failed' or actual.get('volumes') or actual.get('initContainers'):
        raise ValueError('Only the exact terminal startup failure may be reconciled')
    deploy.verify_containers(actual['containers'],wanted)
    query = ("ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+EXECUTION+
        "' | where Log_s startswith 'NameError:' | project "
        "MissingIdentifier=extract(@\"name '([A-Za-z_][A-Za-z0-9_]*)' is not defined\",1,Log_s)")
    rows = deploy.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',query)
    if len(rows) != 1 or rows[0].get('MissingIdentifier') != '__file__':
        raise ValueError('Exact sanitized startup error required')
    # The fixed db module evaluates Path(__file__) before the wrapper is loaded.
    # This is source-order evidence, not a live administrative role inventory.
    db_source = (deploy.ROOT/'src/ibkr_etl/db.py').read_text(encoding='utf-8')
    if deploy.MODULES != ('util','db','owner_codex_tasks') or 'Path(__file__).resolve().parents[2]' not in db_source:
        raise ValueError('Reviewed pre-wrapper failure ordering differs')
    records = deploy.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb',
        '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+EXECUTION+
        "' | where Log_s startswith '"+deploy.PREFIX+"' | project Log_s")
    if records:
        raise ValueError('Wrapper ran; this pre-SQL reconciliation is not applicable')
    databases = deploy.az('postgres','flexible-server','db','list','-g','rg-ibkr-database-v11',
        '-s','ibkrv2stg-pg-auhecwc4itaoo','--query','[].name')
    if 'owner_codex_acceptance_20261003' in databases:
        raise ValueError('Disposable database exists; manual SQL reconciliation required')
    if deploy.baseline() != intent['baseline'] or deploy.bundle('run')[1] != source:
        raise ValueError('Frozen configuration/source changed')
    result = deploy.save(dict(status='reconciled',execution=EXECUTION,source=source,
        error_class='NameError',missing_identifier='__file__',failure_before_sql_wrapper=True,
        trial_database_absent_verified=True,trial_role_absence_verified=False,
        role_absence_inferred_from_pre_wrapper_failure=True,baseline_unchanged=True,
        existing_jobs_unchanged=34,job_definition_changed=False,production_database_touched=False,
        ibkr_source_writes=False,model_calls=0,automatic_retry=False,
        intent_receipt=intent and json.loads((deploy.FOLDER/'run-intent.json').read_text())['protected_receipt'],
        dispatch_receipt=json.loads((deploy.FOLDER/'run-dispatch.json').read_text())['protected_receipt']),
        'startup-reconciliation.json','startup-reconciled')
    print(json.dumps(dict(status=result['status'],execution=EXECUTION,
        protected_receipt=result['protected_receipt'],automatic_retry=False),sort_keys=True),flush=True)


if __name__ == '__main__':
    main()
