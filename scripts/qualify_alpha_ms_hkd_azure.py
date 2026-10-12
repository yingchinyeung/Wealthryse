"""Private HKD runtime rehearsal only. Explicitly contains no promotion stage."""
import argparse
import base64
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import subprocess
import sys

import deploy_alpha_world_ms_report_runtime as previous

ROOT=previous.ROOT
FOLDER=ROOT/'.work/alpha-ms-hkd-azure-v14r8c-20261007'
TAG='ibkr-platform:alpha-ms-hkd-hibor-v14r8c-20261007'
LAYOUT='subject-completed-factsheet-model-v14r8-alpha-world-ms-hkd-hibor'
INPUT=ROOT/'.work/alpha-ms-hkd-hibor-20261007'
CAPTURE=ROOT/'.work/alpha-ms-factsheets-v13-20261007'
PREFIX='review/alpha-world-ms/hkd-hibor-v14r8-20261007/'
previous.FOLDER=FOLDER;previous.TAG=TAG;previous.LAYOUT=LAYOUT;previous.PREFIX=PREFIX+'outputs/'
previous.build.FOLDER=FOLDER;previous.build.TAG=TAG
build=previous.build


def prepare():
    if FOLDER.exists():raise ValueError('Qualification directory exists; never replace frozen context')
    manifest=json.loads((ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8/manifest.json').read_bytes())
    context=FOLDER/'context';context.mkdir(parents=True)
    modules=['monthly_artifacts','alpha_world_ms_analytics','alpha_world_ms_pdf_validation','market_calendar',
        'alpha_world_ms_hkd','hibor','subject_onboarding','agent_tools','risk_snapshots']
    scripts=['generate_month_end_package','render_alpha_world_ms_hkd_azure_batch','refresh_staging_risk_free']
    tests=['hibor','alpha_world_ms_hkd','monthly_artifacts','alpha_world_ms_chart_style','alpha_world_ms_analytics',
        'alpha_world_ms_pdf_accuracy','risk_snapshots','subject_onboarding','agent_tools','derived_portfolio',
        'risk_free','reporting_snapshot','month_end_package_readiness']
    names=['src/ibkr_etl/'+s+'.py' for s in modules]+['scripts/'+s+'.py' for s in scripts]+['tests/test_'+s+'.py' for s in tests]
    installed={}
    for name in names:
        target=context/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes((ROOT/name).read_bytes())
        if not name.startswith('tests/'):installed[name]=previous.sha(target.read_bytes())
    inputs={'source-inspection.json':INPUT/'source-inspection.json','official-hibor.json':INPUT/'official-hibor.json',
        'capture.json':CAPTURE/'capture.json'}
    base='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:ad7058ad550c7fc34e33439ad9375ce36997d34606e98588d978e8c9b993eedf'
    release={'layout_version':LAYOUT,'installed_files_sha256':installed,'inputs_sha256':{n:previous.sha(p.read_bytes()) for n,p in inputs.items()},
        'snapshot_sha256':{a['period_end']:a['data_sha256'] for a in manifest['artifacts']},
        'contract_sha256':manifest['contract_sha256'],'base_image':base,'currency':'HKD',
        'risk_free_basis':manifest['risk_free_basis'],'commercial_usage_rights_owner_confirmed':True,
        'additional_interest_charge':False,'database_writes_authorized':False,'production_promotion':False}
    (context/'ALPHA_MS_HKD_RELEASE.json').open('x',encoding='utf-8').write(json.dumps(release,indent=2))
    docker='FROM '+base+'\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker+='COPY --chown=10001:10001 ALPHA_MS_HKD_RELEASE.json /app/ALPHA_MS_HKD_RELEASE.json\nCOPY --chown=10001:10001 tests/ /app/alpha-ms-hkd-tests/\n'
    docker+='RUN ["python", "scripts/check_report_runtime.py"]\n'
    docker+='RUN ["python", "-m", "unittest", "discover", "-s", "/app/alpha-ms-hkd-tests", "-v"]\n'
    docker+='RUN ["python", "-c", "import render_alpha_world_ms_hkd_azure_batch; import ibkr_etl.monthly_artifacts as m; assert m.factsheet_layout_version(\'alpha-world-ms\') == \''+LAYOUT+'\'; print(\'PASS: private Azure HKD/HIBOR runtime v14r8\')"]\n'
    docker+='CMD ["python", "scripts/render_alpha_world_ms_hkd_azure_batch.py"]\n'
    (context/'Dockerfile').open('x',encoding='utf-8').write(docker)
    before={name:previous.get_job(name) for name in previous.JOBS}
    build.save('source-intent',{'tag':TAG,'prepared_at':datetime.now(timezone.utc).isoformat(),
        'files_sha256':{p.relative_to(context).as_posix():previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs':before,'before_invariants':{n:previous.invariant(j) for n,j in before.items()},
        'scope':'private HKD/HIBOR Azure rehearsal only','production_promotion':False})
    print(json.dumps({'prepared':True,'source_files':len(names),'persistent_jobs_changed':False}))


def upload_inputs():
    from azure.storage.blob import ContentSettings
    from azure.core.exceptions import ResourceExistsError
    release=json.loads((FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes())
    storage=previous.storage()
    inputs={'source-inspection.json':INPUT/'source-inspection.json','official-hibor.json':INPUT/'official-hibor.json',
        'capture.json':CAPTURE/'capture.json'}
    for name,path in inputs.items():
        data=path.read_bytes()
        if previous.sha(data)!=release['inputs_sha256'][name]:raise ValueError('Input changed after freeze')
        blob=storage.get_blob_client('internal-reports',PREFIX+'inputs/'+name)
        try:blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':previous.sha(data)})
        except ResourceExistsError:
            if blob.download_blob().readall()!=data:raise ValueError('Private input already differs')
        if blob.download_blob().readall()!=data:raise ValueError('Private upload readback differs')
    build.save('private-inputs-uploaded',{'inputs_sha256':release['inputs_sha256'],'source_data_changed':False})
    print(json.dumps({'private_inputs_uploaded':len(inputs)}))


def upload_via_worker():
    # The owner's login has read-only blob access. Use the already authorized
    # reporting identity, not an additional human write grant or storage key.
    if (FOLDER/'input-upload-dispatch-intent.json').exists():raise ValueError('Reconcile original upload, never redispatch')
    release=json.loads((FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes())
    job=previous.get_job(previous.JOBS[0])
    if previous.invariant(job)!=previous.read('source-intent')['before_invariants'][previous.JOBS[0]]:raise ValueError('Job drift')
    executions=previous.request('GET',previous.job_path(previous.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(e['properties']['status'] in ('Running','Processing') for e in executions):raise ValueError('Worker busy')
    expected=release['inputs_sha256']
    source_blob='review/alpha-world-ms/hkd-hibor-20261007/source-inspection-'+expected['source-inspection.json']+'.json'
    originals={'source-inspection.json':source_blob,'capture.json':'render-inputs/alpha-world-ms/v13-20261007/capture.json'}
    # Only public benchmark observations are embedded; no credentials or IBKR
    # payloads are placed in process arguments.
    public_fixings=base64.b64encode((INPUT/'official-hibor.json').read_bytes()).decode()
    code='''import os,json,hashlib,base64
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient,ContentSettings
from azure.core.exceptions import ResourceExistsError
service=BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
def sha(data):return hashlib.sha256(data).hexdigest()
expected=EXPECTED
originals=ORIGINALS
for name,digest in expected.items():
 data=base64.b64decode(PUBLIC_FIXINGS) if name=='official-hibor.json' else service.get_blob_client('internal-reports',originals[name]).download_blob().readall()
 if sha(data)!=digest:raise ValueError('Original private input digest differs')
 blob=service.get_blob_client('internal-reports',PREFIX+'inputs/'+name)
 try:blob.upload_blob(data,overwrite=False,content_settings=ContentSettings(content_type='application/json'),metadata={'sha256':digest})
 except ResourceExistsError:
  if blob.download_blob().readall()!=data:raise ValueError('Private target already differs')
 if blob.download_blob().readall()!=data:raise ValueError('Private readback differs')
 print(json.dumps({'input':name,'sha256':digest,'create_only':True}),flush=True)
'''.replace('EXPECTED',repr(expected)).replace('ORIGINALS',repr(originals)).replace('PUBLIC_FIXINGS',repr(public_fixings)).replace('PREFIX',repr(PREFIX))
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['command']=['python'];c['args']=['-c',code]
    build.save('input-upload-dispatch-intent',{'job':previous.JOBS[0],'image':c['image'],
        'inputs_sha256':expected,'command_sha256':previous.sha(code.encode()),'persistent_configuration_changed':False,'database_writes':0})
    result=previous.request('POST',previous.job_path(previous.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    build.save('input-upload-dispatch',{'execution':result['name']});print(json.dumps({'input_upload_execution':result['name']}))


def check_upload():
    ack=previous.read('input-upload-dispatch')
    run=previous.request('GET',previous.job_path(previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    status=run['properties']['status']
    if status!='Succeeded':print(json.dumps({'status':status}));return
    expected=previous.read('input-upload-dispatch-intent')['inputs_sha256'];storage=previous.storage()
    for name,digest in expected.items():
        data=storage.get_blob_client('internal-reports',PREFIX+'inputs/'+name).download_blob().readall()
        if previous.sha(data)!=digest:raise ValueError('Private worker upload readback differs')
    build.save('private-inputs-uploaded',{'inputs_sha256':expected,'source_data_changed':False,
        'execution':ack['execution'],'additional_permissions_granted':False})
    print(json.dumps({'private_inputs_verified':len(expected),'execution':ack['execution']}))


def verify_existing_inputs():
    # Recover the acknowledged previous candidate's successful input transfer.
    old=ROOT/'.work/alpha-ms-hkd-azure-v14r8-20261007/private-inputs-uploaded.json'
    receipt=json.loads(old.read_bytes())['record']
    expected=json.loads((FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes())['inputs_sha256']
    if receipt['inputs_sha256']!=expected:raise ValueError('Previous upload belongs to different inputs')
    storage=previous.storage()
    for name,digest in expected.items():
        data=storage.get_blob_client('internal-reports',PREFIX+'inputs/'+name).download_blob().readall()
        if previous.sha(data)!=digest:raise ValueError('Previous upload readback differs')
    build.save('private-inputs-uploaded',{'inputs_sha256':expected,'source_data_changed':False,
        'execution':receipt['execution'],'upload_repeated':False,'additional_permissions_granted':False})
    print(json.dumps({'existing_private_inputs_verified':len(expected),'upload_repeated':False}))


def qualify_log():
    native=previous.read('build-completed');run=native['run']
    result=subprocess.run(['C:/Program Files/Microsoft SDKs/Azure/CLI2/python.exe','-m','azure.cli',
        'acr','task','logs','-r',build.REGISTRY,'--run-id',run],capture_output=True,text=True,encoding='utf-8',timeout=120)
    if result.returncode:raise ValueError('Cannot read native build log')
    log=result.stdout+result.stderr
    counts=re.findall(r'Ran (\d+) tests? in [^\n]+\n\s*OK(?:\s|$)',log)
    count=sum(map(int,counts))
    passed=native['native_status']=='Succeeded' and count>=116 and 'PASS: private Azure HKD/HIBOR runtime v14r8' in log and 'PASS: report templates, PowerPoint runtime and native chart canvas' in log
    (FOLDER/'native-build.log').open('x',encoding='utf-8').write(log)
    build.save('native-build-qualified' if passed else 'native-build-rejected',{'qualified':passed,'linux_tests':count,'run':run,'log_sha256':previous.sha(log.encode()),'image':previous.image()})
    print(json.dumps({'qualified':passed,'linux_tests':count}))


def dispatch():
    previous.require_candidate();previous.read('private-inputs-uploaded')
    if (FOLDER/'azure-dispatch-intent.json').exists():raise ValueError('Original dispatch must be reconciled; never redispatch')
    job=previous.get_job(previous.JOBS[0])
    if previous.invariant(job)!=previous.read('source-intent')['before_invariants'][previous.JOBS[0]]:raise ValueError('Worker configuration changed')
    executions=previous.request('GET',previous.job_path(previous.JOBS[0])+'/executions?api-version=2024-03-01').get('value',[])
    if any(e['properties']['status'] in ('Running','Processing') for e in executions):raise ValueError('Worker busy')
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['image']=previous.image();c['command']=['python'];c['args']=['scripts/render_alpha_world_ms_hkd_azure_batch.py']
    c['env']=[e for e in c['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']+[{'name':'ALPHA_MS_IMAGE_DIGEST','value':previous.image().split('@')[1]}]
    build.save('azure-dispatch-intent',{'template':template,'image':previous.image(),'job':previous.JOBS[0],'persistent_configuration_changed':False,'database_writes':0})
    result=previous.request('POST',previous.job_path(previous.JOBS[0])+'/start?api-version=2024-03-01',data=template)
    build.save('azure-dispatch',{'execution':result['name'],'image':previous.image()});print(json.dumps({'execution':result['name']}))


def download():
    from ibkr_etl.alpha_world_ms_pdf_validation import validate
    ack=previous.read('azure-dispatch')
    run=previous.request('GET',previous.job_path(previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':raise ValueError('Successful private execution required')
    actual=run['properties']['template']['containers'][0]
    wanted=previous.read('azure-dispatch-intent')['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Execution identity differs')
    service=previous.storage()
    data=service.get_blob_client('internal-reports',PREFIX+'outputs/manifest.json').download_blob().readall()
    manifest=json.loads(data)
    if manifest['image_digest']!=previous.image().split('@')[1] or manifest['release_manifest_sha256']!=previous.sha((FOLDER/'context/ALPHA_MS_HKD_RELEASE.json').read_bytes()):
        raise ValueError('Private Azure release identity differs')
    if manifest['count']!=21 or manifest['database_writes']!=0 or manifest['production_cutover'] or manifest['reports_released'] or manifest['onboarding_activated']:
        raise ValueError('Unexpected write, release or historical scope')
    local=json.loads((ROOT/'output/pdf/AlphaWorld_MS_HKD_HIBOR_Validation_v14r8/manifest.json').read_bytes())
    expected={r['period_end']:r['data_sha256'] for r in local['artifacts']}
    if {r['period_end']:r['data_sha256'] for r in manifest['artifacts']}!=expected:raise ValueError('Azure financial input parity differs')
    output=ROOT/'output/pdf/AlphaWorld_MS_Azure_HKD_HIBOR_v14r8';output.mkdir(exist_ok=True)
    for item in manifest['artifacts']:
        period=item['period_end'];name='AlphaWorld_MS_Factsheet_'+period+'.pdf'
        if item['blob_name']!=PREFIX+'outputs/'+name:raise ValueError('Artifact path outside private scope')
        pdf=service.get_blob_client('internal-reports',item['blob_name']).download_blob().readall()
        if previous.sha(pdf)!=item['sha256']:raise ValueError('Actual Azure PDF hash differs')
        target=output/name
        if target.exists():
            if target.read_bytes()!=pdf:raise ValueError('Existing download differs')
        else:target.open('xb').write(pdf)
        validate(target,json.loads((INPUT/'hkd-inputs'/(period+'.json')).read_bytes()))
    target=output/'manifest.json'
    if target.exists():
        if target.read_bytes()!=data:raise ValueError('Existing Azure manifest differs')
    else:target.open('xb').write(data)
    before=previous.read('source-intent')['before_jobs']
    for name in previous.JOBS:
        live=previous.get_job(name)
        if previous.invariant(live)!=previous.invariant(before[name]) or live['properties']['template']['containers'][0]['image']!=before[name]['properties']['template']['containers'][0]['image']:
            raise ValueError('Persistent reporting worker changed')
    build.save('azure-private-validation',{'execution':ack['execution'],'image':previous.image(),'count':21,'pages':42,
        'actual_pdf_vector_checks':'pass','financial_input_parity':'pass','database_writes':0,
        'manifest_sha256':previous.sha(data),'persistent_jobs_changed':False,'production_cutover':False,
        'visual_review':'pending','reports_released':False})
    print(json.dumps({'azure_private_pdfs_downloaded':21,'actual_vector_checks':'pass','persistent_jobs_changed':False}))


def closeout():
    build.verify_context()
    visual=json.loads((FOLDER/'actual-azure-visual-qa.json').read_bytes())
    if visual['azure_pages_checked']!=42 or visual['pixel_parity_with_visually_reviewed_pages']!='pass':
        raise ValueError('Complete actual Azure visual acceptance required')
    local=json.loads((INPUT/'private-qa.json').read_bytes())
    simulations=json.loads((INPUT/'private-simulation-qa.json').read_bytes())
    if simulations['status']!='pass' or not local['visual_review'].startswith('pass:'):raise ValueError('Independent private acceptance required')
    context,intent=build.verify_context()
    changes=[name for name,digest in intent['files_sha256'].items() if name.startswith(('src/','scripts/','tests/'))
        and (ROOT/name).is_file() and previous.sha((ROOT/name).read_bytes())!=digest]
    build.save('private-closeout',{'scope':'21 historical Alpha World MS private HKD/HIBOR factsheets only',
        'azure':previous.read('azure-private-validation'),'visual':visual,'independent_source_qa':local,
        'independent_simulation_qa':simulations,'commercial_usage_rights_owner_confirmed':True,
        'native_tests':116,'current_local_tests':118,'local_changes_since_frozen_candidate':changes,
        'recurring_hibor_transport_qualified':False,'hkma_transport_observation':'HTTP 405',
        'production_cutover':False,'future_month_end_end_to_end_qualified':False,
        'remaining':['recurring official HIBOR ingestion/publication-time policy','versioned HKD database metadata/calculation/benchmark/cache rehearsal',
            'coordinated compatible derived/risk/reporting/website release and rollback','future month-end end-to-end verification'],
        'ibkr_source_data_changed':False,'published_history_changed':False,'alpha_world_t_changed':False})
    print(json.dumps({'private_azure_batch_qualified':True,'production_cutover':False,'future_month_end_qualified':False}))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=['prepare','upload','upload-worker','upload-check','verify-inputs','build','reconcile','inspect','log','scan','dispatch','execution','download','closeout'])
    stage=parser.parse_args().stage
    if stage=='prepare':prepare()
    elif stage=='upload':upload_inputs()
    elif stage=='upload-worker':upload_via_worker()
    elif stage=='upload-check':check_upload()
    elif stage=='verify-inputs':verify_existing_inputs()
    elif stage in ('build','inspect'):sys.argv=[sys.argv[0],stage];build.main()
    elif stage=='reconcile':previous.reconcile_build()
    elif stage=='log':qualify_log()
    elif stage=='scan':
        import scan_report_runtime_candidate as scanner
        scanner.main()
    elif stage=='dispatch':dispatch()
    elif stage=='download':download()
    elif stage=='closeout':closeout()
    else:previous.inspect_execution()


if __name__=='__main__':main()
