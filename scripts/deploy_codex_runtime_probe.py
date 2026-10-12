"""Protected staged build and synthetic start; no application/job PUT or grants."""
import argparse
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path
from audit_azure_runtime import az, RG, AZ
from deploy_metadata_monitor import request, SUB, BASE
from deploy_specialist_daily_schedule import unaffected, fingerprint, accepted
from run_specialist_gateway_read_verification import retain
from run_codex_identity_trial import JOB, CLIENT, PRINCIPAL, trial_template
from prepare_codex_runtime_probe import FOLDER, manifest

REGISTRY='ibkrv2stgauhecwc4itaoo'
REPO='codex-synthetic-runtime'
PREFIX='CODEX_RUNTIME_PROBE '
WORKSPACE='5264a124-10cd-45f6-93a5-6d65c37e9bfb'


def saved(name, value, kind):
    value['protected_receipt']=retain(value,kind)
    (FOLDER/name).write_text(json.dumps(value,sort_keys=True,indent=2))
    print(json.dumps({k:v for k,v in value.items() if k not in {'baseline','template','source'}},sort_keys=True),flush=True)


def protected_record(path, expected_status):
    value=json.loads(path.read_text())
    receipt=value.get('protected_receipt',{})
    payload={k:v for k,v in value.items() if k!='protected_receipt'}
    if (value.get('status')!=expected_status or receipt.get('protected_readback_verified') is not True
            or fingerprint(payload)!=receipt.get('sha256')
            or not receipt.get('blob','').endswith('-'+receipt.get('sha256','')+'.json')):
        raise ValueError('Protected stage receipt binding differs')
    return value


def verified_build_image(run,digest,tag):
    images=run.get('outputImages',[])
    if (run.get('status')!='Succeeded' or len(images)!=1 or images[0].get('digest')!=digest
            or images[0].get('repository')!=REPO or images[0].get('tag')!=tag
            or not re.fullmatch('sha256:[0-9a-f]{64}',digest or '')):
        raise ValueError('Image tag and successful build output differ')
    return REGISTRY+'.azurecr.io/'+REPO+'@'+digest


def build_command(context,tag):
    # Azure CLI resolves --file relative to the invoking cwd, NOT the context.
    # Always name the frozen Dockerfile by absolute path.
    return [AZ,'-IBm','azure.cli','acr','build','-r',REGISTRY,'-t',REPO+':'+tag,
            '--file',str((context/'Dockerfile').resolve()),'--no-logs','--no-wait',
            '--timeout','600',str(context.resolve()),'-o','json']


def build_run_id(output):
    ids=set(re.findall(r'(?:Queued a build with ID:|Run ID:)\s*([a-z0-9]{4,20})',output))
    if len(ids)!=1:raise ValueError('Inspect protected build intent; dispatch ID unavailable')
    return ids.pop()


def validate_context():
    _,expected=manifest()
    record=json.loads((FOLDER/'source-manifest.json').read_text())
    if record!=expected:raise ValueError('Build manifest differs from reviewed source')
    context=FOLDER/'context'
    actual={str(p.relative_to(context)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in context.rglob('*') if p.is_file()}
    if actual!=record['files']:raise ValueError('Exact sanitized build context required')
    return record


def validate_azure_model():
    account=BASE+'Microsoft.CognitiveServices/accounts/ibkrv2stg-ai-auhecwc4itaoo'
    model=request('GET',account+'/deployments/gpt-5-6-sol-apac?api-version=2024-10-01')
    p=model['properties']
    if (p.get('provisioningState')!='Succeeded' or p['model'].get('name')!='gpt-5.6-sol'
            or p['model'].get('version')!='2026-07-09' or model['sku'].get('name')!='DataZoneStandard'
            or model['sku'].get('capacity')!=10):raise ValueError('Approved Azure model inventory changed')
    return dict(deployment='gpt-5-6-sol-apac',model='gpt-5.6-sol',version='2026-07-09',capacity=10)


def diagnostic_template(job,image):
    # Reuse the preaccepted diagnostic scope checks, but never its base image/files.
    trial_template(job,"print('scope-check-only')")
    if not re.fullmatch(re.escape(REGISTRY)+r'\.azurecr\.io/'+REPO+r'@sha256:[0-9a-f]{64}',image):
        raise ValueError('Exact isolated Codex image digest required')
    original=job['properties']['template']['containers'][0]
    return dict(containers=[dict(name=original['name'],image=image,
        resources=dict(cpu=1,memory='2Gi'),env=[dict(name='AZURE_CLIENT_ID',value=CLIENT)],
        command=['python'],args=['scripts/run_codex_runtime_probe.py','--mode','azure'])])


def validate_execution_template(actual, expected):
    """Accept only Azure's observed empty optional storage serialization."""
    if (actual.get('volumes') or actual.get('initContainers')
            or len(actual.get('containers',[]))!=1 or len(expected.get('containers',[]))!=1):
        raise ValueError('Executed trial differs')
    container=actual['containers'][0]; target=expected['containers'][0]
    if (any(container.get(k)!=target[k] for k in ('name','image','command','args','env'))
            or container.get('volumeMounts') or container.get('probes')):
        raise ValueError('Executed trial differs')
    resources=copy.deepcopy(container.get('resources',{}))
    # Do not ignore nonempty storage, GPU fields, or any other resource change.
    if resources.get('ephemeralStorage')=='':resources.pop('ephemeralStorage')
    if resources!=target['resources']:
        raise ValueError('Executed trial resources differ')


def main():
    global FOLDER
    p=argparse.ArgumentParser();p.add_argument('stage',choices=['build','verify-build','start-azure','verify-azure'])
    p.add_argument('--run-id');p.add_argument('--execution');args=p.parse_args()
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    FOLDER=FOLDER/manifest()[1]['source_manifest_sha256'][:12]
    record=validate_context();tag='probe-20261003-'+record['source_manifest_sha256'][:12]
    if args.stage=='build':
        if (FOLDER/'build-intent.json').exists():raise ValueError('Protected build intent exists; inspect before redispatch')
        intent=dict(status='intent',source=record,tag=tag,repository=REPO,
            baseline=unaffected(),production_mutations_authorized=False)
        saved('build-intent.json',intent,'codex-runtime-build-intent')
        result=subprocess.run(build_command(FOLDER/'context',tag),capture_output=True,text=True,timeout=120)
        if result.returncode!=0:raise ValueError('Build request failed; inspect protected intent before retry')
        # CLI returns no JSON object for --no-wait. Parse only the fixed run ID,
        # never retain its upload URL or other stderr text.
        run_id=build_run_id(result.stdout+'\n'+result.stderr)
        saved('build-dispatch.json',dict(status='dispatched',run_id=run_id,tag=tag,
              source_manifest_sha256=record['source_manifest_sha256']),'codex-runtime-build-dispatch')
    elif args.stage=='verify-build':
        intent=protected_record(FOLDER/'build-intent.json','intent');dispatch=protected_record(FOLDER/'build-dispatch.json','dispatched')
        run_id=dispatch['run_id']
        if args.run_id and args.run_id!=run_id:raise ValueError('Build run binding differs')
        run=az('acr','task','show-run','-r',REGISTRY,'--run-id',run_id)
        if run.get('status')!='Succeeded':
            print(json.dumps(dict(stage='build',run_id=run_id,status=run.get('status'))));return
        digest=az('acr','repository','show','-n',REGISTRY,'--image',REPO+':'+tag,'--query','digest')
        image=verified_build_image(run,digest,tag)
        if unaffected()!=intent['baseline']:raise ValueError('Existing runtime configuration changed during build')
        saved('build-acceptance.json',dict(status='passed',run_id=run_id,image=image,
            source_manifest_sha256=record['source_manifest_sha256'],base_and_cli_pinned=True,
            offline_protocol_gate_passed=True,existing_workloads_unchanged=True,
            azure_inference_verified=False,owner_runtime_active=False),'codex-runtime-build-completed')
    elif args.stage=='start-azure':
        build=accepted(FOLDER/'build-acceptance.json')
        if (FOLDER/'azure-intent.json').exists():raise ValueError('Azure trial intent exists; inspect before redispatch')
        if build['source_manifest_sha256']!=record['source_manifest_sha256']:raise ValueError('Source/image binding differs')
        job=request('GET',BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
        active=az('containerapp','job','execution','list','-g',RG,'-n',JOB,
                  '--query',"[?properties.status=='Running' || properties.status=='Processing'].name")
        if active:raise ValueError('Existing diagnostic worker busy')
        template=diagnostic_template(job,build['image'])
        intent=dict(status='intent',image=build['image'],template=template,baseline=unaffected(),
                    azure_model=validate_azure_model(),source=record,
                    diagnostic_identity_principal=PRINCIPAL,synthetic_only=True,
                    no_database_or_flex_credentials=True,source_writes_authorized=False)
        saved('azure-intent.json',intent,'codex-runtime-azure-intent')
        result=request('POST',job['id']+'/start?api-version=2024-03-01',data=template)
        saved('azure-dispatch.json',dict(status='dispatched',execution=result['name'],image=build['image']),
              'codex-runtime-azure-dispatch')
    else:
        intent=protected_record(FOLDER/'azure-intent.json','intent');dispatch=protected_record(FOLDER/'azure-dispatch.json','dispatched')
        execution=dispatch['execution']
        if args.execution and args.execution!=execution:raise ValueError('Execution binding differs')
        if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7,12}',execution):raise ValueError('Unregistered execution')
        run=request('GET',BASE+'Microsoft.App/jobs/'+JOB+'/executions/'+execution+'?api-version=2024-03-01')
        validate_execution_template(run['properties']['template'],intent['template'])
        logs=az('monitor','log-analytics','query','-w',WORKSPACE,'--analytics-query',
            "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+
            "' | where Log_s startswith '"+PREFIX+"' | project Log_s")
        rows=[json.loads(row['Log_s'].removeprefix(PREFIX)) for row in logs]
        if run['properties']['status']!='Succeeded' or len(rows)!=1:
            print(json.dumps(dict(status='not_accepted',execution=execution,job_status=run['properties']['status'],records=rows)));return
        value=rows[0]
        if (value.get('status')!='passed' or value.get('mode')!='azure' or value.get('request_count')!=1
                or value.get('accepted_count')!=1 or value.get('source_data_sent') is not False
                or value.get('upstream_tools_enabled') is not False or value.get('runtime_version')!='0.160.0'
                or value.get('owner_runtime_active') is not False or value.get('production_compatible') is not False
                or not all(value.get(k) is True for k in ('handshake_verified','turn_completed',
                      'streamed_delta_observed','runtime_output_verified'))):raise ValueError('Exact runtime acceptance failed')
        if unaffected()!=intent['baseline']:raise ValueError('Existing runtime configuration changed during trial')
        saved('azure-acceptance.json',dict(status='passed',execution=execution,image=intent['image'],
            runtime=value,existing_workloads_unchanged=True,owner_runtime_active=False,
            six_specialist_runtimes_active=False,source=record),'codex-runtime-azure-completed')


if __name__=='__main__':main()
