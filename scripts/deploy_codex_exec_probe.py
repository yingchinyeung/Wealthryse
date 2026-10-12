"""Protected isolated CLI build/trial. Never updates production or installed jobs."""
import argparse
import copy
import hashlib
import json
import re
import subprocess
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from azure.core import MatchConditions
from ibkr_etl.specialist_archive_reader import ACCOUNT, CONTAINER, SCOPE
from ibkr_etl.specialist_audit_service import validate_archive_properties
from ibkr_etl.codex_exec_probe import DECLARATION_TOOLS_SHA256
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, SUB, BASE
from deploy_specialist_daily_schedule import unaffected, fingerprint
from run_specialist_gateway_read_verification import retain
from run_codex_identity_trial import JOB, PRINCIPAL
from deploy_codex_runtime_probe import (
    REGISTRY, REPO, WORKSPACE, protected_record as local_protected_record, verified_build_image, build_command,
    build_run_id, validate_azure_model, diagnostic_template as app_template,
)
from prepare_codex_exec_probe import FOLDER, manifest

PREFIX = 'CODEX_EXEC_PROBE '
CHECKS = ('thread_started_verified', 'turn_started_verified', 'turn_completed',
          'event_stream_observed', 'runtime_output_verified', 'process_exit_verified', 'cli_flags_verified',
          'native_catalog_matches_linux', 'custom_alias_verified', 'upstream_tool_contract_verified')


def archive_protection():
    value = request('GET', SCOPE+'?api-version=2023-05-01')
    if (value.get('id', '').casefold() != SCOPE.casefold()
        or value.get('properties', {}).get('hasLegalHold') is not True):
        raise ValueError('Protected CLI archive identity or hold differs')
    validate_archive_properties(value)


def protected_record(path, expected_status):
    """Consume an exact protected remote version, not a local receipt assertion."""
    value = local_protected_record(path, expected_status)
    receipt = value['protected_receipt']
    if (not re.fullmatch(r'acceptance/gateway-read-codex-exec-[a-z-]+-[0-9a-f]{64}\.json', receipt['blob'])
        or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z', receipt.get('version_id', ''))):
        raise ValueError('Unregistered protected CLI receipt')
    payload = json.dumps({k:v for k,v in value.items() if k != 'protected_receipt'},
                         sort_keys=True, separators=(',', ':')).encode()
    if not 0 < len(payload) <= 2*1024*1024:
        raise ValueError('Protected CLI receipt exceeds limit')
    archive_protection()
    service = BlobServiceClient('https://'+ACCOUNT+'.blob.core.windows.net', credential=AzureCliCredential())
    blob = service.get_blob_client(CONTAINER, receipt['blob'], version_id=receipt['version_id'])
    properties = blob.get_blob_properties()
    if properties.version_id != receipt['version_id'] or properties.size != len(payload):
        raise ValueError('Protected CLI archive version differs')
    actual = blob.download_blob(offset=0, length=len(payload)+1, etag=properties.etag,
                                match_condition=MatchConditions.IfNotModified).readall()
    if actual != payload:
        raise ValueError('Protected CLI remote bytes differ')
    archive_protection()
    return value


def saved(name, value, kind):
    value['protected_receipt'] = retain(value, kind)
    (FOLDER/name).write_text(json.dumps(value, sort_keys=True, indent=2))
    print(json.dumps({k: v for k, v in value.items() if k not in {'baseline', 'template', 'source'}}, sort_keys=True), flush=True)


def validate_context():
    _, expected = manifest()
    record = json.loads((FOLDER/'source-manifest.json').read_text())
    if record != expected:
        raise ValueError('Source or invoked helpers differ from reviewed manifest')
    context = FOLDER/'context'
    actual = {str(p.relative_to(context)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in context.rglob('*') if p.is_file()}
    if actual != record['files']:
        raise ValueError('Exact sanitized context required')
    return record


def diagnostic_template(job, image):
    value = app_template(job, image)
    value['containers'][0]['args'] = ['scripts/run_codex_exec_probe.py', '--mode', 'azure']
    return value


def validate_execution_template(actual, expected):
    if (actual.get('volumes') or actual.get('initContainers') or
            len(actual.get('containers', [])) != 1 or len(expected.get('containers', [])) != 1):
        raise ValueError('Executed CLI trial differs')
    container, target = actual['containers'][0], expected['containers'][0]
    if (any(container.get(k) != target[k] for k in ('name', 'image', 'command', 'args', 'env')) or
            container.get('volumeMounts') or container.get('probes')):
        raise ValueError('Executed CLI trial differs')
    resources = copy.deepcopy(container.get('resources', {}))
    if resources.get('ephemeralStorage') == '':
        resources.pop('ephemeralStorage')
    cpu = resources.get('cpu')
    if (type(cpu) not in {int, float} or resources != target['resources']):
        raise ValueError('Executed CLI resources differ')


def validate_result(value):
    if (value.get('schema') != 'codex-synthetic-exec-acceptance-v1' or
            value.get('status') != 'passed' or value.get('mode') != 'azure' or
            type(value.get('request_count')) is not int or value['request_count'] != 1 or
            type(value.get('accepted_count')) is not int or value['accepted_count'] != 1 or
            value.get('source_data_sent') is not False or value.get('upstream_tools_enabled') is not False or
            value.get('runtime_version') != '0.160.0' or value.get('interface') != 'cli-exec' or
            value.get('interface_maturity') != 'stable' or value.get('relay_failure') is not None or
            type(value.get('discarded_declaration_count')) is not int or value['discarded_declaration_count'] != 1 or
            value.get('discarded_declaration_sha256') != DECLARATION_TOOLS_SHA256 or
            value.get('declaration_tools_forwarded') is not False or
            any(value.get(k) is not False for k in ('owner_runtime_active', 'six_specialist_runtimes_active', 'production_accepted')) or
            not all(value.get(k) is True for k in CHECKS)):
        raise ValueError('Exact CLI acceptance failed')


def main():
    global FOLDER
    p = argparse.ArgumentParser()
    p.add_argument('stage', choices=['build', 'verify-build', 'start-azure', 'verify-azure'])
    p.add_argument('--run-id'); p.add_argument('--execution')
    args = p.parse_args()
    if az('account', 'show', '--query', 'id') != SUB:
        raise ValueError('Unexpected subscription')
    FOLDER = FOLDER/manifest()[1]['source_manifest_sha256'][:12]
    record = validate_context()
    tag = 'exec-probe-20261003-'+record['source_manifest_sha256'][:12]
    if args.stage == 'build':
        if (FOLDER/'build-intent.json').exists():
            raise ValueError('Protected build intent exists; inspect before redispatch')
        saved('build-intent.json', dict(status='intent', source=record, tag=tag, repository=REPO,
              baseline=unaffected(), production_mutations_authorized=False), 'codex-exec-build-intent')
        result = subprocess.run(build_command(FOLDER/'context', tag), capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise ValueError('Build dispatch failed; inspect protected intent before retry')
        run_id = build_run_id(result.stdout+'\n'+result.stderr)
        saved('build-dispatch.json', dict(status='dispatched', run_id=run_id, tag=tag,
              source_manifest_sha256=record['source_manifest_sha256']), 'codex-exec-build-dispatch')
    elif args.stage == 'verify-build':
        intent = protected_record(FOLDER/'build-intent.json', 'intent')
        dispatch = protected_record(FOLDER/'build-dispatch.json', 'dispatched')
        run_id = dispatch['run_id']
        if args.run_id and args.run_id != run_id:
            raise ValueError('Build binding differs')
        run = az('acr', 'task', 'show-run', '-r', REGISTRY, '--run-id', run_id)
        if run.get('status') != 'Succeeded':
            print(json.dumps(dict(stage='build', run_id=run_id, status=run.get('status')))); return
        digest = az('acr', 'repository', 'show', '-n', REGISTRY, '--image', REPO+':'+tag, '--query', 'digest')
        image = verified_build_image(run, digest, tag)
        if unaffected() != intent['baseline']:
            raise ValueError('Existing workload configuration changed during build')
        saved('build-acceptance.json', dict(status='passed', run_id=run_id, image=image,
              source_manifest_sha256=record['source_manifest_sha256'], base_and_cli_pinned=True,
              offline_cli_gate_passed=True, existing_workloads_unchanged=True,
              azure_inference_verified=False, owner_runtime_active=False, production_accepted=False), 'codex-exec-build-completed')
    elif args.stage == 'start-azure':
        build = protected_record(FOLDER/'build-acceptance.json', 'passed')
        if (FOLDER/'azure-intent.json').exists():
            raise ValueError('Protected Azure intent exists; inspect before redispatch')
        if build['source_manifest_sha256'] != record['source_manifest_sha256']:
            raise ValueError('Source/image binding differs')
        job = request('GET', BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
        active = az('containerapp', 'job', 'execution', 'list', '-g', RG, '-n', JOB,
                    '--query', "[?properties.status=='Running' || properties.status=='Processing'].name")
        if active:
            raise ValueError('Diagnostic worker busy')
        template = diagnostic_template(job, build['image'])
        saved('azure-intent.json', dict(status='intent', image=build['image'], template=template,
              baseline=unaffected(), azure_model=validate_azure_model(), source=record,
              diagnostic_identity_principal=PRINCIPAL, synthetic_only=True,
              no_database_or_flex_credentials=True, source_writes_authorized=False), 'codex-exec-azure-intent')
        result = request('POST', job['id']+'/start?api-version=2024-03-01', data=template)
        saved('azure-dispatch.json', dict(status='dispatched', execution=result['name'], image=build['image']),
              'codex-exec-azure-dispatch')
    else:
        intent = protected_record(FOLDER/'azure-intent.json', 'intent')
        dispatch = protected_record(FOLDER/'azure-dispatch.json', 'dispatched')
        execution = dispatch['execution']
        if args.execution and args.execution != execution:
            raise ValueError('Execution binding differs')
        if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7,12}', execution):
            raise ValueError('Unregistered execution')
        run = request('GET', BASE+'Microsoft.App/jobs/'+JOB+'/executions/'+execution+'?api-version=2024-03-01')
        validate_execution_template(run['properties']['template'], intent['template'])
        logs = az('monitor', 'log-analytics', 'query', '-w', WORKSPACE, '--analytics-query',
                  "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+
                  "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
        rows = [json.loads(row['Log_s'].removeprefix(PREFIX)) for row in logs]
        status = run['properties']['status']
        if status != 'Succeeded' or len(rows) != 1:
            value = dict(status='not_accepted', execution=execution, job_status=status, records=rows)
            if status in {'Failed', 'Stopped', 'Degraded'} and not (FOLDER/'azure-failure.json').exists():
                saved('azure-failure.json', value, 'codex-exec-azure-failure')
            else:
                print(json.dumps(value, sort_keys=True))
            return
        validate_result(rows[0])
        if unaffected() != intent['baseline']:
            raise ValueError('Existing workload configuration changed during trial')
        saved('azure-acceptance.json', dict(status='passed', execution=execution, image=intent['image'],
              runtime=rows[0], existing_workloads_unchanged=True, owner_runtime_active=False,
              six_specialist_runtimes_active=False, production_accepted=False, source=record), 'codex-exec-azure-completed')


if __name__ == '__main__':
    main()
