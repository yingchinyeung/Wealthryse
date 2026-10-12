"""MS axis-only reporting release. Never touches the paused website cutover."""
import ast
import base64
import copy
import json
import sys
import zipfile

import deploy_alpha_ms_copy as driver

ROOT = driver.ROOT
FOLDER = ROOT/'.work/alpha-ms-distribution-v14r13-20261007'
BASE_FOLDER = ROOT/'.work/alpha-ms-copy-v14r12-20261007'
BASE_IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:f94f3a57b8170aeb294ec7dc69215738ab6c96a834d8019db78f1ebfaaaff827'
DATABASE_IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038'
DATABASE_PREFIX = 'review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
PREFIX = 'review/alpha-world-ms/distribution-v14r13-20261007/'
LAYOUT = 'subject-completed-factsheet-model-v14r13-alpha-world-ms-distribution-quarter-ticks'
OUTPUT = ROOT/'output/pdf/AlphaWorld_MS_Azure_Distribution_Ticks_v14r13'
gate = driver.gate
previous = driver.previous
driver.FOLDER = FOLDER
driver.BASE_FOLDER = BASE_FOLDER
driver.BASE_IMAGE = BASE_IMAGE
driver.LAYOUT = LAYOUT
driver.OUTPUT = OUTPUT
gate.FOLDER = FOLDER
gate.RUN_MARKER = 'PASS: MS axis-only historical/future renderer v14r13'
gate.MIN_LINUX_TESTS = 166
previous.FOLDER = FOLDER
previous.LAYOUT = LAYOUT
previous.TAG = 'ibkr-platform:alpha-ms-distribution-v14r13-20261007'
previous.build.FOLDER = FOLDER
previous.build.TAG = previous.TAG


def prepare():
    if FOLDER.exists(): raise ValueError('Frozen attempt exists; inspect, never replace')
    baseline = json.loads((BASE_FOLDER/'source-intent.json').read_bytes())['record']
    context_base = BASE_FOLDER/'context'
    for name,digest in baseline['files_sha256'].items():
        if previous.sha((context_base/name).read_bytes()) != digest: raise ValueError('Frozen baseline differs')
    before = {name:previous.get_job(name) for name in previous.JOBS}
    if any(j['properties']['template']['containers'][0]['image'] != BASE_IMAGE for j in before.values()):
        raise ValueError('Live reporting image drift')
    module = 'src/ibkr_etl/monthly_artifacts.py'
    patched = (context_base/module).read_text(encoding='utf-8')
    current = (ROOT/module).read_text(encoding='utf-8')
    for symbol in ('ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION','_draw_alpha_world_distribution'):
        replacement = gate.node_source(current,symbol)[1]
        if symbol == '_draw_alpha_world_distribution':
            replacement = gate.node_source(current,'_alpha_world_ms_distribution_ticks')[1]+'\n\n'+replacement
        patched = gate.replace_symbol(patched,symbol,replacement)
    if ast.dump(ast.parse(patched)) != ast.dump(ast.parse(current)):
        raise ValueError('Non-axis renderer changes found')
    validator = 'src/ibkr_etl/alpha_world_ms_pdf_validation.py'
    old = (context_base/validator).read_text(encoding='utf-8')
    new = (ROOT/validator).read_text(encoding='utf-8')
    check = gate.replace_symbol(old,'validate',gate.node_source(new,'validate')[1])
    if ast.dump(ast.parse(check)) != ast.dump(ast.parse(new)):
        raise ValueError('Non-axis validator changes found')
    context = FOLDER/'context';context.mkdir(parents=True)
    release = json.loads((context_base/'ALPHA_MS_HKD_RELEASE.json').read_bytes())
    for name in release['installed_files_sha256']:
        data = patched.encode() if name == module else check.encode() if name == validator else (context_base/name).read_bytes()
        target = context/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        release['installed_files_sha256'][name] = previous.sha(data)
    release.update(layout_version=LAYOUT,base_image=BASE_IMAGE,distribution_ticks_only=True,
                   financial_calculation_code_unchanged=True,source_data_changed=False)
    (context/'ALPHA_MS_HKD_RELEASE.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    for path in (context_base/'tests').glob('*.py'):
        target = context/'tests'/path.name;target.parent.mkdir(exist_ok=True);target.write_bytes(path.read_bytes())
    for name in ('test_alpha_world_ms_chart_style.py','test_alpha_world_ms_pdf_accuracy.py'):
        (context/'tests'/name).write_bytes((ROOT/'tests'/name).read_bytes())
    docker = f'FROM {BASE_IMAGE}\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker += 'COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker += 'COPY --chown=10001:10001 ALPHA_MS_HKD_RELEASE.json /app/ALPHA_MS_HKD_RELEASE.json\nCOPY --chown=10001:10001 tests/ /app/alpha-ms-tick-tests/\n'
    docker += 'RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/alpha-ms-tick-tests","-v"]\n'
    code = "import run_registry_month_end_packages,run_self_service_onboarding_reports;import ibkr_etl.monthly_artifacts as m;assert m.factsheet_layout_version('alpha-world-ms')=="+repr(LAYOUT)+';print('+repr(gate.RUN_MARKER)+')'
    docker += 'RUN '+json.dumps(['python','-c',code])+'\nCMD ["python","scripts/run_registry_month_end_packages.py"]\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    previous.build.save('source-intent',{'tag':previous.TAG,'layout_version':LAYOUT,
        'files_sha256':{p.relative_to(context).as_posix():previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs':before,'before_invariants':{n:previous.invariant(j) for n,j in before.items()},
        'base_image':BASE_IMAGE,'scope':'MS X-axis labels and guides only; historical/future reporting',
        'financial_calculation_code_unchanged':True,'source_data_changed':False,'production_promotion':False,
        'website_cutover':False,'paused_worker_drafts_included':False})
    print(json.dumps({'prepared':True,'targets':previous.JOBS,'website_cutover':False}))


def probe(mode):
    if mode not in ('candidate','rollback','installed'):raise ValueError('Invalid probe mode')
    previous.require_candidate();label=mode+'-probe'
    if (FOLDER/(label+'-intent.json')).exists():raise ValueError('Probe intent exists; reconcile instead')
    job=previous.get_job(previous.JOBS[1])
    installed=previous.image() if mode=='installed' else BASE_IMAGE
    if job['properties']['template']['containers'][0]['image']!=installed:raise ValueError('Job image drift')
    if previous.invariant(job)!=previous.read('source-intent')['before_invariants'][previous.JOBS[1]]:raise ValueError('Job settings drift')
    runs=previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs):raise ValueError('Worker busy')
    source=(ROOT/'scripts/verify_alpha_ms_hkd_installed.py').read_text(encoding='utf-8')
    anchor="PREFIX='"+DATABASE_PREFIX+"'"
    if source.count(anchor)!=1:raise ValueError('Exact source prefix required')
    prefix=PREFIX+mode+'/'
    source=source.replace(anchor,'PREFIX='+repr(prefix))
    anchor="PREFIX+'database-cutover-'+image.split(':')[1]+'.json'"
    if source.count(anchor)!=1:raise ValueError('Exact immutable database commit required')
    source=source.replace(anchor,repr(DATABASE_PREFIX+'database-cutover-'+DATABASE_IMAGE.split('@sha256:')[1]+'.json'))
    source=source.replace("commit['image_digest']!=image","commit['image_digest']!="+repr(DATABASE_IMAGE.split('@')[1]))
    source=source.replace('                validate(target,payload)',
        '                validate(target,payload)\n                from ibkr_etl.alpha_world_ms_copy import validate_copy\n                validate_copy(target)')
    image=BASE_IMAGE if mode=='rollback' else previous.image()
    template={k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c=template['containers'][0];c['image']=image;c['command']=['python']
    c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source.encode()).decode()+"'),'ms-axis-read-only-probe','exec'))"]
    c['env']=[e for e in c['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']+[{'name':'ALPHA_MS_IMAGE_DIGEST','value':image.split('@')[1]}]
    previous.build.save(label+'-intent',{'template':template,'source_sha256':previous.sha(source.encode()),
        'prefix':prefix,'image':image,'persistent_settings_changed':False,'database_writes':0})
    result=previous.request('POST',previous.job_path(previous.JOBS[1])+'/start?api-version=2024-03-01',data=template)
    previous.build.save(label+'-dispatch',{'execution':result['name'],'image':image})
    print(json.dumps({'mode':mode,'execution':result['name']}))


def finish():
    previous.require_candidate()
    candidate=previous.read('candidate-probe-qualified');installed=previous.read('installed-probe-qualified')
    if {r['period_end']:r['sha256'] for r in candidate['proof']['artifacts']}!={r['period_end']:r['sha256'] for r in installed['proof']['artifacts']}:
        raise ValueError('Installed bytes differ from accepted candidate')
    for name in previous.JOBS:
        job=previous.get_job(name)
        if job['properties']['template']['containers'][0]['image']!=previous.image() or previous.invariant(job)!=previous.read('source-intent')['before_invariants'][name]:raise ValueError('Installed job settings differ')
    previous.build.save('completion',{'status':'azure_ms_distribution_ticks_deployed_and_verified','image':previous.image(),
        'layout_version':LAYOUT,'jobs':previous.JOBS,'historical_factsheets':21,'first_month':'2025-01-31','last_month':'2026-09-30',
        'financial_calculation_code_unchanged':True,'ibkr_source_changed':False,'database_writes':0,
        'reports_released':False,'onboarding_activated':False,'website_cutover':False,'old_outputs_preserved':True,
        'rollback_image':BASE_IMAGE,'rollback_live_probe_passed':True,'installed_probe':installed['execution'],
        'linux_tests':previous.read('native-build-qualified')['linux_tests'],'scan':previous.read('scan-completed')['assessment'],
        'future_rendering':'Both reporting entrypoints use v14r13; approval and publication gates retained',
        'private_output_prefix':'internal-reports/'+PREFIX+'installed/installed-outputs/'})
    archive=OUTPUT.parent/'AlphaWorld_MS_Azure_Distribution_Ticks_v14r13.zip'
    with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(OUTPUT.glob('*.pdf')):
            bundle.write(path,path.name)
        bundle.write(OUTPUT/'manifest.json','manifest.json')
    print(json.dumps({'status':'complete','cloud_factsheets':21,'reporting_workers':2,'website_cutover':False}))


if __name__=='__main__':
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    if stage=='prepare':prepare()
    elif stage in ('build','inspect'):previous.build.main()
    elif stage=='reconcile':previous.reconcile_build()
    elif stage=='log':gate.qualify_log()
    elif stage=='scan':
        import scan_report_runtime_candidate
        scan_report_runtime_candidate.main()
    elif stage.startswith('probe-'):probe(stage.removeprefix('probe-'))
    elif stage.startswith('verify-'):driver.verify_probe(stage.removeprefix('verify-'))
    elif stage=='accept':driver.accept()
    elif stage=='promote':driver.promote()
    elif stage=='finish':finish()
    else:raise SystemExit('Unknown stage')
