"""One offline CLI event-shape inspection in a previously accepted image.

Reports only allowlisted type names/key names; never text, IDs, reasoning,
environment, credentials or model outputs. No Azure inference is performed.
"""
import argparse
import base64
import hashlib
import json
import re
from pathlib import Path
from audit_azure_runtime import az, RG
from deploy_metadata_monitor import request, BASE
from deploy_specialist_daily_schedule import unaffected
from deploy_codex_runtime_probe import diagnostic_template, protected_record, WORKSPACE
from deploy_codex_exec_probe import validate_execution_template
from run_codex_identity_trial import JOB
from run_specialist_gateway_read_verification import retain

ROOT = Path(__file__).resolve().parents[1]
FOLDER = ROOT/'.work/codex-exec-runtime-20261003/shape-classification'
IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/codex-synthetic-runtime@sha256:00f3f12ca1dead98dae5587e436d6240471fc518edaf48b59468180991a1b666'
PREFIX = 'CODEX_EXEC_EVENT_SHAPE '


def source():
    adapter = (ROOT/'src/ibkr_etl/codex_exec_probe.py').read_text()
    prefix = "import sys,types\nm=types.ModuleType('ibkr_etl.codex_exec_probe');m.__package__='ibkr_etl';sys.modules[m.__name__]=m\n"
    prefix += 'exec(compile('+repr(adapter)+",'synthetic-exec-adapter','exec'),m.__dict__)\n"
    body = r'''
import asyncio, hashlib, json, re, tempfile
from pathlib import Path
from ibkr_etl.codex_exec_probe import exec_command, _stop_owned_process
from ibkr_etl.codex_runtime_probe import SyntheticRelay, child_environment, PROMPT, MAX_BYTES
ALLOWED_TYPES={'thread.started','turn.started','turn.completed','turn.failed','error','item.started','item.updated','item.completed'}
ALLOWED_ITEMS={'agent_message','reasoning','command_execution','file_change','mcp_tool_call','web_search','plan'}
ALLOWED_KEYS={'type','thread_id','item','usage','error','message'}
async def inspect(relay,d):
    p=await asyncio.create_subprocess_exec(*exec_command(relay),cwd=d/'workspace',env=child_environment(d,relay.capability),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,limit=MAX_BYTES)
    records=[];total=0;turn_started=False
    try:
        p.stdin.write(PROMPT.encode());await p.stdin.drain();p.stdin.close()
        for i in range(100):
            raw=await asyncio.wait_for(p.stdout.readline(),20)
            if not raw:break
            total+=len(raw)
            if total>2*MAX_BYTES:raise ValueError()
            v=json.loads(raw);kind=v.get('type');item=v.get('item') or {}
            safe_name=lambda name:name if isinstance(name,str) and re.fullmatch('[a-z_]{1,60}',name) else 'unrecognized'
            warning=(item.get('message','').lower() if not turn_started and item.get('type')=='error' and isinstance(item.get('message'),str) else '')
            records.append({'type':kind if kind in ALLOWED_TYPES else 'unrecognized','keys':sorted(set(v)&ALLOWED_KEYS),'item_type':safe_name(item.get('type')) if item else None,'item_keys':sorted(safe_name(k) for k in item),'message_sha256':hashlib.sha256(item['message'].encode()).hexdigest() if isinstance(item.get('message'),str) else None,'phase':item.get('phase') if item.get('phase') in {None,'final_answer','commentary'} else 'unrecognized','startup_categories':[w for w in ('model','metadata','deprecated','sandbox','config','permission','unknown','failed','experimental','feature') if w in warning],'mentioned_config_keys':[w for w in ('shell_tool','unified_exec','apps','tools.view_image','skill_mcp_dependency_install','model_catalog_json','personality','gpt-5-6-sol-apac') if w in warning]})
            if kind=='turn.started':turn_started=True
        code=await asyncio.wait_for(p.wait(),10)
        return {'events':records,'exit_code':code,'source_data_sent':False,'azure_model_called':False}
    finally:await _stop_owned_process(p)
with tempfile.TemporaryDirectory(prefix='codex-shape-only-') as name:
    d=Path(name)
    for part in ('home','codex-home','tmp','workspace'):(d/part).mkdir()
    with SyntheticRelay() as relay:
        result=asyncio.run(asyncio.wait_for(inspect(relay,d),80))
        result.update(request_count=relay.request_count,accepted_count=relay.accepted_count,relay_failure=relay.failure)
print('CODEX_EXEC_EVENT_SHAPE '+json.dumps(result,sort_keys=True),flush=True)
'''
    return prefix+body


def main():
    p = argparse.ArgumentParser(); p.add_argument('stage', choices=['start', 'verify']); args = p.parse_args()
    code = source(); digest = hashlib.sha256(code.encode()).hexdigest()
    if args.stage == 'start':
        FOLDER.mkdir(parents=True, exist_ok=True)
        if (FOLDER/'shape-intent.json').exists():
            raise ValueError('Shape intent exists; inspect without redispatch')
        job = request('GET', BASE+'Microsoft.App/jobs/'+JOB+'?api-version=2024-03-01')
        active = az('containerapp', 'job', 'execution', 'list', '-g', RG, '-n', JOB,
                    '--query', "[?properties.status=='Running' || properties.status=='Processing'].name")
        if active: raise ValueError('Diagnostic worker busy')
        template = diagnostic_template(job, IMAGE)
        encoded = base64.b64encode(code.encode()).decode()
        template['containers'][0]['args'] = ['-c', "import base64;exec(compile(base64.b64decode('"+encoded+"'),'offline-shape-inspection','exec'))"]
        value = dict(status='intent', source_sha256=digest, image=IMAGE, template=template,
                     baseline=unaffected(), azure_model_called=False, source_data_sent=False)
        value['protected_receipt'] = retain(value, 'codex-exec-shape-intent')
        (FOLDER/'shape-intent.json').write_text(json.dumps(value, sort_keys=True, indent=2))
        dispatch = request('POST', job['id']+'/start?api-version=2024-03-01', data=template)
        value = dict(status='dispatched', execution=dispatch['name'], source_sha256=digest)
        value['protected_receipt'] = retain(value, 'codex-exec-shape-dispatch')
        (FOLDER/'shape-dispatch.json').write_text(json.dumps(value, sort_keys=True, indent=2))
        print(json.dumps(value)); return
    intent = protected_record(FOLDER/'shape-intent.json', 'intent')
    dispatch = protected_record(FOLDER/'shape-dispatch.json', 'dispatched')
    # Verify the immutable, protected dispatch source, not subsequently edited
    # workspace modules. This permits inspection without redispatch after a fix.
    frozen_arg = intent['template']['containers'][0]['args'][1]
    match = re.fullmatch(r"import base64;exec\(compile\(base64.b64decode\('([A-Za-z0-9+/=]+)'\),'offline-shape-inspection','exec'\)\)", frozen_arg)
    if not match:
        raise ValueError('Frozen diagnostic source unavailable')
    digest = hashlib.sha256(base64.b64decode(match[1], validate=True)).hexdigest()
    if digest != intent['source_sha256'] or digest != dispatch['source_sha256']:
        raise ValueError('Exact shape source binding required')
    execution = dispatch['execution']
    if not re.fullmatch(re.escape(JOB)+r'-[a-z0-9]{7,12}', execution): raise ValueError('Execution invalid')
    run = request('GET', BASE+'Microsoft.App/jobs/'+JOB+'/executions/'+execution+'?api-version=2024-03-01')
    validate_execution_template(run['properties']['template'], intent['template'])
    logs = az('monitor', 'log-analytics', 'query', '-w', WORKSPACE, '--analytics-query',
              "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+execution+"' | where Log_s startswith '"+PREFIX+"' | project Log_s")
    rows = [json.loads(v['Log_s'].removeprefix(PREFIX)) for v in logs]
    if run['properties']['status'] != 'Succeeded' or len(rows) != 1:
        print(json.dumps(dict(status=run['properties']['status'], records=rows))); return
    if (rows[0].get('azure_model_called') is not False or rows[0].get('source_data_sent') is not False or
            unaffected() != intent['baseline']): raise ValueError('Shape boundaries differ')
    result = dict(status='passed', execution=execution, source_sha256=digest, record=rows[0], existing_workloads_unchanged=True)
    result['protected_receipt'] = retain(result, 'codex-exec-shape-completed')
    (FOLDER/'shape-acceptance.json').write_text(json.dumps(result, sort_keys=True, indent=2))
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__': main()
