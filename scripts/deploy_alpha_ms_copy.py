"""Owner-authorized, text-only MS rollout, with exact build/scan/live gates."""
import ast
import base64
import copy
import json
from pathlib import Path
import sys

import deploy_alpha_ms_allocation_geometry as gate

ROOT = gate.ROOT
BASE_FOLDER = ROOT/'.work/alpha-ms-hkd-cutover-v14r11-20261007-r3'
FOLDER = ROOT/'.work/alpha-ms-copy-v14r12-20261007'
BASE_IMAGE = 'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038'
BASE_PREFIX = 'review/alpha-world-ms/hkd-cutover-v14r11-20261007/'
PREFIX = 'review/alpha-world-ms/copy-v14r12-20261007/'
OUTPUT = ROOT/'output/pdf/AlphaWorld_MS_Azure_Copy_v14r12'
LAYOUT = 'subject-completed-factsheet-model-v14r12-alpha-world-ms-disclaimer-spacing'
RUN_MARKER = 'PASS: MS text-only historical/future renderer v14r12'
previous = gate.previous
gate.FOLDER = FOLDER; gate.RUN_MARKER = RUN_MARKER; gate.MIN_LINUX_TESTS = 163
previous.FOLDER = FOLDER; previous.LAYOUT = LAYOUT
previous.TAG = 'ibkr-platform:alpha-ms-copy-v14r12-20261007'
previous.build.FOLDER = FOLDER; previous.build.TAG = previous.TAG


def prepare():
    if FOLDER.exists(): raise ValueError('Frozen attempt exists; inspect, never replace')
    baseline = json.loads((BASE_FOLDER/'source-intent.json').read_bytes())['record']
    context_base = BASE_FOLDER/'context'
    for name, digest in baseline['files_sha256'].items():
        if previous.sha((context_base/name).read_bytes()) != digest: raise ValueError('Frozen baseline differs')
    before = {name: previous.get_job(name) for name in previous.JOBS}
    if any(j['properties']['template']['containers'][0]['image'] != BASE_IMAGE for j in before.values()):
        raise ValueError('Live reporting image drift')
    context = FOLDER/'context'; context.mkdir(parents=True)
    module = 'src/ibkr_etl/monthly_artifacts.py'
    original = (context_base/module).read_text(encoding='utf-8')
    current = (ROOT/module).read_text(encoding='utf-8')
    patched = original
    for symbol in ('ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION', 'render_factsheet'):
        patched = gate.replace_symbol(patched, symbol, gate.node_source(current, symbol)[1])
    if ast.dump(ast.parse(patched)) != ast.dump(ast.parse(current)):
        raise ValueError('Non-disclaimer renderer changes found')
    release = json.loads((context_base/'ALPHA_MS_HKD_RELEASE.json').read_bytes())
    for name in release['installed_files_sha256']:
        data = patched.encode() if name == module else (context_base/name).read_bytes()
        target = context/name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
        release['installed_files_sha256'][name] = previous.sha(data)
    name = 'src/ibkr_etl/alpha_world_ms_copy.py'
    data = (ROOT/name).read_bytes(); (context/name).write_bytes(data)
    release['installed_files_sha256'][name] = previous.sha(data)
    release.update(layout_version=LAYOUT, base_image=BASE_IMAGE, disclaimer_typography_only=True,
                   financial_calculation_code_unchanged=True, source_data_changed=False)
    (context/'ALPHA_MS_HKD_RELEASE.json').write_text(json.dumps(release, indent=2), encoding='utf-8')
    for path in (context_base/'tests').glob('*.py'):
        target = context/'tests'/path.name; target.parent.mkdir(exist_ok=True); target.write_bytes(path.read_bytes())
    fixture = context/'tests/test_monthly_artifacts.py'
    fixture.write_text(gate.replace_symbol(fixture.read_text(encoding='utf-8'), '_write_alpha_world_source',
                      gate.node_source((ROOT/'tests/test_monthly_artifacts.py').read_text(encoding='utf-8'), '_write_alpha_world_source')[1]), encoding='utf-8')
    (context/'tests/test_alpha_world_ms_copy.py').write_bytes((ROOT/'tests/test_alpha_world_ms_copy.py').read_bytes())
    docker = f'FROM {BASE_IMAGE}\nENV PYTHONPATH=/app/src:/app:/app/scripts\n'
    docker += 'COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 scripts/ /app/scripts/\n'
    docker += 'COPY --chown=10001:10001 ALPHA_MS_HKD_RELEASE.json /app/ALPHA_MS_HKD_RELEASE.json\nCOPY --chown=10001:10001 tests/ /app/alpha-ms-copy-tests/\n'
    docker += 'RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/alpha-ms-copy-tests","-v"]\n'
    code = "import run_registry_month_end_packages,run_self_service_onboarding_reports; import ibkr_etl.monthly_artifacts as m; assert m.factsheet_layout_version('alpha-world-ms') == " + repr(LAYOUT) + '; print('+repr(RUN_MARKER)+')'
    docker += 'RUN '+json.dumps(['python','-c',code])+'\nCMD ["python","scripts/run_registry_month_end_packages.py"]\n'
    (context/'Dockerfile').write_text(docker, encoding='utf-8')
    previous.build.save('source-intent', {'tag': previous.TAG, 'layout_version': LAYOUT,
        'files_sha256': {p.relative_to(context).as_posix():previous.sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()},
        'before_jobs': before, 'before_invariants': {n:previous.invariant(j) for n,j in before.items()},
        'base_image': BASE_IMAGE, 'scope':'MS disclaimer typography only; historical and future rendering',
        'financial_calculation_code_unchanged':True, 'source_data_changed':False, 'production_promotion':False})
    print(json.dumps({'prepared':True, 'financial_modules_unchanged':True, 'targets':previous.JOBS}))


def probe(mode):
    if mode not in ('candidate','rollback','installed'): raise ValueError('Invalid probe mode')
    previous.require_candidate(); label = mode+'-probe'
    if (FOLDER/(label+'-intent.json')).exists(): raise ValueError('Probe intent exists; reconcile instead')
    job = previous.get_job(previous.JOBS[1]); installed = previous.image() if mode=='installed' else BASE_IMAGE
    if job['properties']['template']['containers'][0]['image'] != installed: raise ValueError('Job image drift')
    if previous.invariant(job) != previous.read('source-intent')['before_invariants'][previous.JOBS[1]]: raise ValueError('Job settings drift')
    runs = previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions?api-version=2024-03-01').get('value',[])
    if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Worker busy')
    source = (ROOT/'scripts/verify_alpha_ms_hkd_installed.py').read_text(encoding='utf-8')
    anchor = "PREFIX='"+BASE_PREFIX+"'"
    if source.count(anchor)!=1: raise ValueError('Exact source prefix required')
    prefix = PREFIX+mode+'/'
    source = source.replace(anchor,'PREFIX='+repr(prefix))
    old = "PREFIX+'database-cutover-'+image.split(':')[1]+'.json'"
    if source.count(old)!=1: raise ValueError('Exact immutable commit reference required')
    source = source.replace(old, repr(BASE_PREFIX+'database-cutover-'+BASE_IMAGE.split('@sha256:')[1]+'.json'))
    source = source.replace("commit['image_digest']!=image", "commit['image_digest']!="+repr(BASE_IMAGE.split('@')[1]))
    if mode != 'rollback':
        source = source.replace('                validate(target,payload)',
             '                validate(target,payload)\n                from ibkr_etl.alpha_world_ms_copy import validate_copy\n                validate_copy(target)')
    image = BASE_IMAGE if mode=='rollback' else previous.image()
    template = {k:v for k,v in copy.deepcopy(job['properties']['template']).items() if v is not None}
    c = template['containers'][0]; c['image']=image; c['command']=['python']
    c['args']=['-c',"import base64;exec(compile(base64.b64decode('"+base64.b64encode(source.encode()).decode()+"'),'ms-copy-read-only-probe','exec'))"]
    c['env']=[e for e in c['env'] if e['name']!='ALPHA_MS_IMAGE_DIGEST']+[{'name':'ALPHA_MS_IMAGE_DIGEST','value':image.split('@')[1]}]
    previous.build.save(label+'-intent', {'template':template,'source_sha256':previous.sha(source.encode()), 'prefix':prefix,
                                        'image':image,'persistent_settings_changed':False,'database_writes':0})
    result = previous.request('POST',previous.job_path(previous.JOBS[1])+'/start?api-version=2024-03-01',data=template)
    previous.build.save(label+'-dispatch', {'execution':result['name'],'image':image})
    print(json.dumps({'mode':mode,'execution':result['name']}))


def verify_probe(mode):
    label=mode+'-probe'; ack=previous.read(label+'-dispatch'); intent=previous.read(label+'-intent')
    run=previous.request('GET',previous.job_path(previous.JOBS[1])+'/executions/'+ack['execution']+'?api-version=2024-03-01')
    if run['properties']['status']!='Succeeded':
        print(json.dumps({'mode':mode,'status':run['properties']['status'],'execution':ack['execution']})); return
    wanted=intent['template']['containers'][0]; actual=run['properties']['template']['containers'][0]
    if any(actual[k]!=wanted[k] for k in ('image','command','args')): raise ValueError('Executed probe identity differs')
    service=previous.storage()
    raw=service.get_blob_client('internal-reports',intent['prefix']+'installed-outputs/verification.json').download_blob().readall()
    proof=json.loads(raw)
    expected_layout=json.loads((BASE_FOLDER/'completion.json').read_bytes())['record']['layout_version'] if mode=='rollback' else LAYOUT
    if (proof['status']!='passed' or proof['count']!=21 or proof['database_writes']!=0
        or proof['image_digest']!=intent['image'].split('@')[1] or proof['layout_version']!=expected_layout
        or proof['reports_released'] or proof['onboarding_activated'] or proof['ibkr_source_changed']): raise ValueError('Live probe scope/coverage mismatch')
    if previous.invariant(previous.get_job(previous.JOBS[1]))!=previous.read('source-intent')['before_invariants'][previous.JOBS[1]]: raise ValueError('Persistent settings drift')
    if mode != 'rollback':
        from ibkr_etl.alpha_world_ms_copy import validate_copy
        folder=OUTPUT if mode=='installed' else FOLDER/'candidate-pdfs'; folder.mkdir(exist_ok=False)
        for row in proof['artifacts']:
            data=service.get_blob_client('internal-reports',row['blob_name']).download_blob().readall()
            if previous.sha(data)!=row['sha256']: raise ValueError('Downloaded PDF integrity mismatch')
            path=folder/('AlphaWorld_MS_Factsheet_'+row['period_end']+'.pdf'); path.write_bytes(data); validate_copy(path)
        (folder/'manifest.json').write_bytes(raw)
    previous.build.save(label+'-qualified', {'image':intent['image'],'execution':ack['execution'], 'proof':proof,
                                          'proof_sha256':previous.sha(raw),'persistent_settings_unchanged':True})
    print(json.dumps({'mode':mode,'status':'pass','months':21}))


def accept():
    proof=json.loads((FOLDER/'pdf-page-checks.json').read_bytes())
    cloud=previous.read('candidate-probe-qualified')
    if proof['status']!='pass' or proof['count']!=21 or proof['image']!=previous.image(): raise ValueError('Exact PDF acceptance required')
    expected={r['period_end']:r['sha256'] for r in cloud['proof']['artifacts']}
    if {r['period_end']:r['pdf_sha256'] for r in proof['checks']}!=expected: raise ValueError('Accepted artifact hashes differ')
    previous.build.save('azure-render-qualified', {'image':previous.image(),'count':21,'source':'Actual live HKD frozen snapshots'})
    previous.build.save('pdf-verification', proof)


def promote():
    for mode in ('candidate','rollback'):
        if previous.read(mode+'-probe-qualified')['proof']['status']!='passed': raise ValueError('Runtime qualification missing')
    for name in previous.JOBS:
        runs=previous.request('GET',previous.job_path(name)+'/executions?api-version=2024-03-01').get('value',[])
        if any(r['properties']['status'] in ('Running','Processing') for r in runs): raise ValueError('Worker busy')
    previous.promote()


def finish():
    previous.require_candidate(); candidate=previous.read('candidate-probe-qualified'); installed=previous.read('installed-probe-qualified')
    if {r['period_end']:r['sha256'] for r in candidate['proof']['artifacts']}!={r['period_end']:r['sha256'] for r in installed['proof']['artifacts']}:
        raise ValueError('Installed artifact bytes differ from accepted candidate')
    for name in previous.JOBS:
        job=previous.get_job(name)
        if job['properties']['template']['containers'][0]['image']!=previous.image() or previous.invariant(job)!=previous.read('source-intent')['before_invariants'][name]: raise ValueError('Installed renderer settings differ')
    previous.build.save('completion', {'status':'azure_ms_copy_deployed_and_verified','image':previous.image(),'layout_version':LAYOUT,
        'jobs':previous.JOBS,'historical_factsheets':21,'first_month':'2025-01-31','last_month':'2026-09-30',
        'financial_calculation_code_unchanged':True,'ibkr_source_changed':False,'database_writes':0,
        'reports_released':False,'onboarding_activated':False,'old_outputs_preserved':True,
        'rollback_image':BASE_IMAGE,'rollback_live_probe_passed':True,'installed_probe':installed['execution'],
        'linux_tests':previous.read('native-build-qualified')['linux_tests'],'scan':previous.read('scan-completed')['assessment'],
        'future_rendering':'Both reporting entrypoints use v14r12; issuance retains validated-input, registration, activation and approval controls',
        'private_output_prefix':'internal-reports/'+PREFIX+'installed/installed-outputs/'})
    print(json.dumps({'status':'complete','cloud_factsheets':21,'reporting_workers':2}))


if __name__=='__main__':
    stage=sys.argv[1] if len(sys.argv)==2 else ''
    if stage=='prepare': prepare()
    elif stage in ('build','inspect'): previous.build.main()
    elif stage=='reconcile': previous.reconcile_build()
    elif stage=='log': gate.qualify_log()
    elif stage=='scan':
        import scan_report_runtime_candidate; scan_report_runtime_candidate.main()
    elif stage.startswith('probe-'): probe(stage.removeprefix('probe-'))
    elif stage.startswith('verify-'): verify_probe(stage.removeprefix('verify-'))
    elif stage=='accept': accept()
    elif stage=='promote': promote()
    elif stage=='finish': finish()
    else: raise ValueError('Unknown stage')
