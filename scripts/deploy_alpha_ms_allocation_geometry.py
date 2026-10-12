"""Layout-only release on the installed reporting image; no HKD cutover.

Never copy the current financial candidate into production. Reuse the existing
evidence, build, scan, private Azure qualification and image-only release gates.
"""
import ast
import base64
import copy
import json
from pathlib import Path
import re
import subprocess
import sys

import deploy_alpha_world_ms_report_runtime as previous

ROOT=previous.ROOT
FOLDER=ROOT/'.work/alpha-ms-azure-geometry-v14r9-20261007-r2'
BASE_CONTEXT=ROOT/'.work/alpha-ms-azure-v14r7-20261007/context'
BASE_IMAGE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:ad7058ad550c7fc34e33439ad9375ce36997d34606e98588d978e8c9b993eedf'
LAYOUT='subject-completed-factsheet-model-v14r9-alpha-world-ms-allocation-geometry'
PREFIX='review/alpha-world-ms/azure-geometry-v14r9-20261007-r2/'
OUTPUT_FOLDER=ROOT/'output/pdf/AlphaWorld_MS_Azure_Allocation_Geometry_v14r9'
QA_FOLDER=ROOT/'tmp/pdfs/alpha-ms-allocation-history-azure-v14r9'
RUN_MARKER='PASS: geometry-only Azure historical and future MS entrypoints v14r9'
MIN_LINUX_TESTS=51
PROBE_PREFIX='qualification/alpha-world-ms/geometry-v14r9-r2-20261007/'
BASE_LAYOUT='subject-completed-factsheet-model-v14r7-alpha-world-ms-validated-charts'
RELEASE_STATUS='geometry-only-azure-renderer-deployed-and-qualified'
previous.FOLDER=FOLDER;previous.LAYOUT=LAYOUT;previous.PREFIX=PREFIX
previous.TAG='ibkr-platform:alpha-ms-geometry-v14r9-20261007-r2'
previous.build.FOLDER=FOLDER;previous.build.TAG=previous.TAG


def node_source(text, name):
    for node in ast.parse(text).body:
        if getattr(node,'name',None)==name or (isinstance(node,ast.Assign)
                and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)):
            return node, '\n'.join(text.splitlines()[node.lineno-1:node.end_lineno])
    raise ValueError('Expected source symbol missing: '+name)


def replace_symbol(text,name,replacement):
    node,_=node_source(text,name);lines=text.splitlines()
    return '\n'.join(lines[:node.lineno-1]+replacement.splitlines()+lines[node.end_lineno:])+'\n'


def patched_renderer(base,current):
    old_tree=ast.parse(base)
    result=replace_symbol(base,'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION',node_source(current,'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION')[1])
    result=replace_symbol(result,'_draw_alpha_world_page_two',node_source(current,'_draw_alpha_world_page_two')[1])
    anchor='    if inputs.subject_id == "alpha-world-ms":\n        _require_run('
    if result.count(anchor)!=1:raise ValueError('Exact MS geometry validation anchor required')
    result=result.replace(anchor,'    if inputs.subject_id == "alpha-world-ms":\n        _validate_alpha_world_ms_allocation(document.pages[1])\n        _require_run(')
    for name in ('ALPHA_WORLD_MS_ALLOCATION_LAYOUT','_draw_alpha_world_ms_allocation','_validate_alpha_world_ms_allocation'):
        result+='\n'+node_source(current,name)[1]+'\n'
    allowed={'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION','_draw_alpha_world_page_two','validate_alpha_world_factsheet_layout',
        'ALPHA_WORLD_MS_ALLOCATION_LAYOUT','_draw_alpha_world_ms_allocation','_validate_alpha_world_ms_allocation'}
    def unchanged(tree):
        result=[]
        for n in tree.body:
            name=getattr(n,'name',None)
            if isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name):name=n.targets[0].id
            if name not in allowed:result.append(ast.dump(n,include_attributes=False))
        return result
    if unchanged(old_tree)!=unchanged(ast.parse(result)):raise ValueError('Non-layout renderer logic changed')
    return result


def prepare():
    if FOLDER.exists():raise ValueError('Existing frozen attempt; inspect instead of replacing')
    prior=json.loads((ROOT/'.work/alpha-ms-azure-v14r7-20261007/source-intent.json').read_bytes())['record']
    for name,digest in prior['files_sha256'].items():
        if previous.sha((BASE_CONTEXT/name).read_bytes())!=digest:raise ValueError('Baseline frozen source differs')
    before={name:previous.get_job(name) for name in previous.JOBS}
    if any(job['properties']['template']['containers'][0]['image']!=BASE_IMAGE for job in before.values()):
        raise ValueError('Installed reporting baseline changed; do not overwrite')
    context=FOLDER/'context';context.mkdir(parents=True)
    renderer_name='src/ibkr_etl/monthly_artifacts.py'
    source=(BASE_CONTEXT/renderer_name).read_text(encoding='utf-8')
    patched=patched_renderer(source,(ROOT/renderer_name).read_text(encoding='utf-8'))
    validator_name='src/ibkr_etl/alpha_world_ms_pdf_validation.py'
    validator=(BASE_CONTEXT/validator_name).read_text(encoding='utf-8')
    replacements={
        '399.5 <= r[1] <= 490.5 and 618 <= r[2] <= 641 and r[3] == 8.':'406.5 <= r[1] <= 497.5 and 618 <= r[2] <= 641',
        'abs(spy[0][2]-634)<.001':'abs(spy[0][2]-627.5)<.001 and spy[0][3]==9.4',
        "445-stringWidth('S&P500','Helvetica',8.)/2":"452-stringWidth('S&P500','Helvetica',9.4)/2"}
    for old,new in replacements.items():
        if validator.count(old)!=1:raise ValueError('Expected baseline allocation caption check missing')
        validator=validator.replace(old,new)
    release=json.loads((BASE_CONTEXT/'ALPHA_MS_RENDER_RELEASE.json').read_bytes())
    names=list(release['installed_files_sha256'])
    for name in names:
        content=(BASE_CONTEXT/name).read_bytes()
        if name==renderer_name:content=patched.encode()
        elif name==validator_name:content=validator.encode()
        elif name=='scripts/render_alpha_world_ms_azure_batch.py':
            content=content.replace(b'review/alpha-world-ms/azure-v14r7-20261007/',PREFIX.encode())
        target=context/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
        release['installed_files_sha256'][name]=previous.sha(content)
    # Preserve and explicitly bind the installed numerical code and calendars.
    release.update(layout_version=LAYOUT,base_image=BASE_IMAGE,financial_calculation_code_unchanged=True,
        hkd_cutover=False,geometry_only=True,report_release_authorized=False)
    for name in ('src/ibkr_etl/alpha_world_ms_analytics.py','src/ibkr_etl/market_calendar.py','scripts/generate_month_end_package.py'):
        if (context/name).read_bytes()!=(BASE_CONTEXT/name).read_bytes():raise ValueError('Financial or scheduling module changed')
    (context/'ALPHA_MS_RENDER_RELEASE.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    for original in (BASE_CONTEXT/'tests').glob('*.py'):
        target=context/'tests'/original.name;target.parent.mkdir(exist_ok=True);target.write_bytes(original.read_bytes())
    # New geometry checks run against the baseline's numerical fixtures.
    target=context/'tests/test_alpha_world_ms_allocation_layout.py'
    target.write_bytes((ROOT/'tests/test_alpha_world_ms_allocation_layout.py').read_bytes())
    docker=f'FROM {BASE_IMAGE}\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker+='COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker+='COPY --chown=10001:10001 ALPHA_MS_RENDER_RELEASE.json /app/ALPHA_MS_RENDER_RELEASE.json\nCOPY --chown=10001:10001 tests/ /tmp/alpha-ms-tests/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    docker+='RUN ["python","-m","unittest","discover","-s","/tmp/alpha-ms-tests","-v"]\n'
    docker+='RUN ["python","-c","import run_registry_month_end_packages,run_self_service_onboarding_reports; import ibkr_etl.monthly_artifacts as m; assert m.factsheet_layout_version(\'alpha-world-ms\') == \''+LAYOUT+'\'; print(\'PASS: geometry-only Azure historical and future MS entrypoints v14r9\')"]\n'
    docker+='CMD ["python","scripts/run_registry_month_end_packages.py"]\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    previous.build.save('source-intent',{'tag':previous.TAG,'layout_version':LAYOUT,
        'files_sha256':{p.relative_to(context).as_posix():previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs':before,'before_invariants':{n:previous.invariant(j) for n,j in before.items()},
        'base_image':BASE_IMAGE,'scope':'MS allocation geometry only','financial_calculation_code_unchanged':True,
        'hkd_cutover':False,'source_data_changed':False,'production_promotion':False})
    print(json.dumps({'prepared':True,'scope':'geometry-only','financial_modules_unchanged':True}))


def qualify_log():
    previous.build.verify_context()
    ack=previous.read('build-ack')
    result=subprocess.run(['C:/Program Files/Microsoft SDKs/Azure/CLI2/python.exe','-m','azure.cli',
        'acr','task','logs','-r',previous.build.REGISTRY,'--run-id',ack['run']],
        capture_output=True,encoding='utf-8',timeout=120)
    if result.returncode:raise ValueError('Native log read failed')
    content=result.stdout+result.stderr
    tests=sum(map(int,re.findall(r'Ran (\d+) tests? in [^\n]+\n\s*OK(?:\s|$)',content)))
    passed=(tests>=MIN_LINUX_TESTS and 'PASS: report templates, PowerPoint runtime and native chart canvas' in content
        and RUN_MARKER in content)
    with (FOLDER/'native-build.log').open('x',encoding='utf-8') as f:f.write(content)
    previous.build.save('native-build-qualified' if passed else 'native-build-rejected',
        {'qualified':passed,'linux_tests':tests,'log_sha256':previous.sha(content.encode()),'image':previous.image()})
    print(json.dumps({'qualified':passed,'linux_tests':tests}))


def download():
    ack=previous.read('azure-dispatch')
    run=previous.request('GET',previous.job_path(previous.JOBS[0])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':raise ValueError('Cloud qualification is not successful')
    wanted=previous.read('azure-dispatch-intent')['template']['containers'][0]
    actual=run['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed candidate differs')
    storage=previous.storage();raw=storage.get_blob_client('internal-reports',PREFIX+'manifest.json').download_blob().readall()
    manifest=json.loads(raw)
    if manifest['count']!=21 or manifest['layout_version']!=LAYOUT or manifest['image_digest']!=previous.image().split('@')[1]:
        raise ValueError('Cloud manifest differs')
    folder=OUTPUT_FOLDER;folder.mkdir(exist_ok=False)
    for row in manifest['artifacts']:
        data=storage.get_blob_client('internal-reports',row['blob_name']).download_blob().readall()
        if previous.sha(data)!=row['sha256']:raise ValueError('Cloud output digest differs')
        (folder/Path(row['blob_name']).name).write_bytes(data)
    (folder/'manifest.json').write_bytes(raw)
    previous.build.save('azure-render-qualified',{'execution':ack['execution'],'image':previous.image(),
        'manifest':manifest,'manifest_sha256':previous.sha(raw),'downloaded_pdfs':21,'native_execution':run})
    print(json.dumps({'azure_rendered':21,'folder':str(folder)}))


def accept_pdfs():
    proof=json.loads((QA_FOLDER/'verification.json').read_bytes())
    cloud=previous.read('azure-render-qualified')
    if proof['status']!='pass' or proof['count']!=21 or cloud['image']!=previous.image():
        raise ValueError('Exact complete Azure geometry acceptance required')
    artifacts={Path(r['blob_name']).name:r['sha256'] for r in cloud['manifest']['artifacts']}
    for row in proof['checks']:
        name='AlphaWorld_MS_Factsheet_'+row['period_end']+'.pdf'
        if artifacts[name]!=row['pdf_sha256']:raise ValueError('Accepted PDF differs from cloud manifest')
    previous.build.save('pdf-verification',dict(proof,image=previous.image(),layout_version=LAYOUT,
        financial_calculation_code_unchanged=True,hkd_cutover=False,reports_released=False))
    print(json.dumps({'pdf_acceptance':'pass','count':21}))


def probe(mode):
    previous.require_candidate()
    if mode not in ('candidate','rollback','installed'):raise ValueError('Probe outside scope')
    name=mode+'-probe';job=previous.get_job(previous.JOBS[1])
    expected_live=previous.image() if mode=='installed' else BASE_IMAGE
    if job['properties']['template']['containers'][0]['image']!=expected_live:
        raise ValueError('Unexpected installed image; do not run probe')
    if previous.invariant(job)!=previous.read('source-intent')['before_invariants'][previous.JOBS[1]]:
        raise ValueError('Scheduled settings drift')
    runs=previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Scheduled worker busy')
    if (FOLDER/(name+'-intent.json')).exists():raise ValueError('Probe already dispatched; verify only')
    source=(ROOT/'scripts/verify_alpha_world_ms_monthend_runtime.py').read_bytes()
    old=b'qualification/alpha-world-ms/monthend-v14r7-20261007/'
    if source.count(old)!=1:raise ValueError('Exact qualification source anchor required')
    prefix=PROBE_PREFIX+mode+'/'
    source=source.replace(old,prefix.encode())
    image=BASE_IMAGE if mode=='rollback' else previous.image()
    template=copy.deepcopy(job['properties']['template'])
    template={k:v for k,v in template.items() if v is not None}
    c=template['containers'][0];c['image']=image;c['command']=['python']
    c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source).decode()+"'),'ms-geometry-qualification','exec'))"]
    c['env']=[e for e in c['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']
    c['env'].append({'name':'ALPHA_MS_IMAGE_DIGEST','value':image.split('@')[1]})
    previous.build.save(name+'-intent',{'template':template,'source_sha256':previous.sha(source),
        'prefix':prefix,'image':image,'persistent_settings_changed':False,'database_writes':0})
    result=previous.request('POST',previous.job_path(previous.JOBS[1])+'/start?api-version=2024-03-01',data=template)
    previous.build.save(name+'-dispatch',{'execution':result['name'],'image':image})
    print(json.dumps({'mode':mode,'execution':result['name'],'image':image}))


def verify_probe(mode):
    name=mode+'-probe';ack=previous.read(name+'-dispatch');intent=previous.read(name+'-intent')
    native=previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    status=native['properties']['status']
    if status!='Succeeded':
        print(json.dumps({'mode':mode,'execution':ack['execution'],'status':status}));return
    actual=native['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')):raise ValueError('Executed probe differs')
    if previous.invariant(previous.get_job(previous.JOBS[1]))!=previous.read('source-intent')['before_invariants'][previous.JOBS[1]]:
        raise ValueError('Persistent scheduled settings changed')
    storage=previous.storage()
    raw=storage.get_blob_client('internal-reports',intent['prefix']+'verification.json').download_blob().readall()
    proof=json.loads(raw)
    expected_layout=BASE_LAYOUT if mode=='rollback' else LAYOUT
    if (proof['status']!='passed' or proof['image_digest']!=intent['image'].split('@')[1]
            or proof['layout_version']!=expected_layout or proof['database_writes']!=0
            or proof['reports_released'] or proof['onboarding_activated']):raise ValueError('Scheduled proof mismatch')
    pdf=storage.get_blob_client('internal-reports',proof['private_pdf_blob']).download_blob().readall()
    if previous.sha(pdf)!=proof['pdf_sha256']:raise ValueError('Probe PDF integrity failed')
    previous.build.save(name+'-qualified',{'image':intent['image'],'proof':proof,'native_execution':native,
        'proof_sha256':previous.sha(raw),'persistent_settings_unchanged':True})
    print(json.dumps({'mode':mode,'status':'passed','execution':ack['execution']}))


def reconcile_probe(mode):
    if mode not in ('candidate','rollback','installed'):raise ValueError('Probe outside scope')
    name=mode+'-probe'
    if (FOLDER/(name+'-dispatch.json')).exists():raise ValueError('Acknowledgement already exists; verify instead')
    intent=previous.read(name+'-intent');wanted=intent['template']['containers'][0]
    runs=previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions?api-version=2024-03-01').get('value',[])
    matches=[]
    for run in runs:
        containers=run['properties'].get('template',{}).get('containers',[])
        if len(containers)==1 and all(containers[0].get(k)==wanted[k] for k in ('image','command','args')):
            matches.append(run)
    if len(matches)!=1:raise ValueError('Unique exact original probe required; never redispatch')
    previous.build.save(name+'-dispatch',{'execution':matches[0]['name'],'image':intent['image'],
        'reconciled_from_exact_native_template':True,'redispatched':False})
    print(json.dumps({'mode':mode,'execution':matches[0]['name'],'status':matches[0]['properties']['status'],'redispatched':False}))


def promote():
    for mode in ('candidate','rollback'):
        proof=previous.read(mode+'-probe-qualified')
        expected=BASE_IMAGE if mode=='rollback' else previous.image()
        if proof['image']!=expected or proof['proof']['status']!='passed':raise ValueError('Live qualification required')
    for job in previous.JOBS:
        runs=previous.request('GET',previous.job_path(job)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Reporting worker busy; defer promotion')
    previous.promote()


def finish():
    previous.require_candidate()
    if previous.read('installed-probe-qualified')['proof']['status']!='passed':raise ValueError('Installed live qualification required')
    jobs={n:previous.get_job(n) for n in previous.JOBS}
    for n,j in jobs.items():
        if (j['properties']['template']['containers'][0]['image']!=previous.image()
                or previous.invariant(j)!=previous.read('source-intent')['before_invariants'][n]):raise ValueError('Final live settings differ')
    previous.build.save('completion',{'status':RELEASE_STATUS,'scope':previous.read('source-intent')['scope'],
        'image':previous.image(),'layout_version':LAYOUT,'jobs':previous.JOBS,'historical_factsheets':21,
        'first_month':'2025-01-31','last_month':'2026-09-30','financial_calculation_code_unchanged':True,
        'hkd_cutover':False,'source_data_changed':False,'database_records_changed':False,
        'reports_released':False,'website_downloads_replaced':False,'onboarding_activated':False,
        'rollback_image':BASE_IMAGE,'rollback_runtime_qualified':True,
        'scan_assessment':previous.read('scan-completed')['assessment'],
        'linux_tests':previous.read('native-build-qualified')['linux_tests'],
        'private_output_prefix':'internal-reports/'+PREFIX,
        'installed_probe_execution':previous.read('installed-probe-dispatch')['execution'],
        'schedules_identities_credentials_retry_limits_unchanged':True,
        'future_months':f'Both installed reporting entrypoints use {LAYOUT}; rendering remains conditional on validated inputs and existing activation/registration controls'})
    print(json.dumps({'status':'complete','cloud_factsheets':21,'installed_workers':2,'geometry_only':True}))


def main():
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    if stage=='prepare':prepare()
    elif stage=='log':qualify_log()
    elif stage=='download':download()
    elif stage=='accept-pdfs':accept_pdfs()
    elif stage=='scan':
        import scan_report_runtime_candidate;scan_report_runtime_candidate.main()
    elif stage in ('build','inspect'):previous.build.main()
    elif stage=='execution':previous.inspect_execution()
    elif stage=='reconcile':previous.reconcile_build()
    elif stage=='dispatch':previous.dispatch()
    elif stage=='promote':promote()
    elif stage=='finish':finish()
    elif stage.startswith('reconcile-'):reconcile_probe(stage.removeprefix('reconcile-'))
    elif stage.startswith('probe-'):probe(stage.removeprefix('probe-'))
    elif stage.startswith('verify-'):verify_probe(stage.removeprefix('verify-'))
    else:raise ValueError('Unknown or unqualified release stage')


if __name__=='__main__':main()
