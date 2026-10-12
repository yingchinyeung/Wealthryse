"""Owner-authorized disclaimer-only release, bound to exact live parent images.

Each stage leaves immutable evidence. No financial or T source is shipped.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile

import build_ms_presentation_worker_candidate as build
import release_alpha_ms_headers_v14r15 as native

ROOT=build.ROOT
FOLDER=ROOT/'.work/alpha-ms-disclaimer-v14r16-20261009-r2'
LAYOUT='subject-completed-factsheet-model-v14r16-alpha-world-ms-owner-disclaimer'
OLD_LAYOUT='subject-completed-factsheet-model-v14r15-alpha-world-ms-right-aligned-dates'
MARKER='PASS: owner disclaimer v14r16; financial code, T, presentations and website controls unchanged'
IMAGES={
 'web':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:fc1da2c74385c9a288291bf8ca511822a45d1408823e94d6faee76496efbedd1',
 'worker':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:1b1583be5be5296e9362220193152426aa341d3db78c26b8ec1eaf69d079eebe'}
PARENTS={'web':ROOT/'.work/ms-release-validation-20261008-r2/web',
         'worker':ROOT/'.work/alpha-ms-crisis-release-20261008/candidate-v6/worker'}
FILES=('src/ibkr_etl/monthly_artifacts.py','src/ibkr_etl/alpha_world_ms_copy.py',
       'src/ibkr_etl/assets/alpha_world_ms_disclaimer_font.pdf')
PERIODS=sorted(p.stem for p in (ROOT/'.work/alpha-ms-hkd-hibor-20261007/hkd-inputs').glob('*.json'))
PREFIX='review/alpha-world-ms/disclaimer-v14r16-20261009-r2/'


def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())
def digest(raw):return hashlib.sha256(raw).hexdigest()


def extract_parent(kind, runtime):
    """Verify all uncompressed layer digests; extract only regular app sources."""
    scan=PARENTS[kind]/'scan-v1';image_sha=IMAGES[kind].split(':')[-1]
    manifest_raw=(scan/image_sha).read_bytes();assert digest(manifest_raw)==image_sha
    config_sha=json.loads(manifest_raw)['config']['digest'].split(':')[-1]
    config_raw=(scan/config_sha).read_bytes();assert digest(config_raw)==config_sha
    config=json.loads(config_raw)
    found={}
    with tarfile.open(scan/'image.docker.tar') as outer:
        manifest=json.load(outer.extractfile('manifest.json'))[0]
        assert digest(outer.extractfile(manifest['Config']).read())==config_sha
        assert len(manifest['Layers'])==len(config['rootfs']['diff_ids'])
        for name,wanted in zip(manifest['Layers'],config['rootfs']['diff_ids']):
            h=hashlib.sha256()
            with outer.extractfile(name) as stream:
                while chunk:=stream.read(1024*1024):h.update(chunk)
            assert 'sha256:'+h.hexdigest()==wanted
            with tarfile.open(fileobj=outer.extractfile(name),mode='r|') as layer:
                for entry in layer:
                    key=entry.name.removeprefix('./').removeprefix('/')
                    if not key.startswith(('app/src/','app/scripts/')):continue
                    relative=key.removeprefix('app/')
                    assert '..' not in Path(relative).parts
                    if '.wh.' in relative:raise ValueError('Review source layer deletion: '+relative)
                    if entry.isdir():continue
                    assert entry.isfile() and entry.size<20*1024*1024
                    if '__pycache__' in relative or relative.endswith('.pyc'):continue
                    found[relative]=layer.extractfile(entry).read()
    for name,raw in found.items():
        target=runtime/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    assert all(n in found for n in FILES[:2])
    return found


def prepare():
    if FOLDER.exists():raise ValueError('Preserve frozen release attempt')
    assert len(PERIODS)==21
    acceptance=read(ROOT/'output/pdf/AlphaWorld_MS_Disclaimer_v14r16','manifest')
    assert acceptance['count']==21 and acceptance['layout_version']==LAYOUT
    FOLDER.mkdir()
    for kind in IMAGES:
        folder=FOLDER/kind;runtime=folder/'local-runtime';context=folder/'context'
        original=extract_parent(kind,runtime)
        monthly=original[FILES[0]].decode('utf-8')
        assert OLD_LAYOUT in native.node_source(monthly,'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION')[1]
        updated=native.replace_symbol(monthly,'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION',
            'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION = '+repr(LAYOUT))
        assert native.unchanged(monthly,{'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION'})==native.unchanged(updated,{'ALPHA_WORLD_MS_FACTSHEET_LAYOUT_VERSION'})
        patch={FILES[0]:updated.encode(),FILES[1]:(ROOT/FILES[1]).read_bytes(),FILES[2]:(ROOT/FILES[2]).read_bytes()}
        # The frozen whole-file copy is the reviewed copy/font change, not a root renderer overwrite.
        assert digest(patch[FILES[2]])==acceptance['font_sha256']
        for name,raw in patch.items():
            for parent in (runtime,context):
                target=parent/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        shutil.copytree(PARENTS[kind]/'context/tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        # A source-inspection regression test resolves this relative to its test
        # directory. Supply the exact parent bytes locally; do not deploy them.
        supporting=context/'scripts/release_internal_reports.py'
        supporting.parent.mkdir(parents=True,exist_ok=True)
        supporting.write_bytes(original['scripts/release_internal_reports.py'])
        if (context/'tests/ms-release-fixtures/pdf').exists():
            for row in acceptance['checks']:
                name='AlphaWorld_MS_Factsheet_'+row['period_end']+'.pdf'
                source=ROOT/'output/pdf/AlphaWorld_MS_Disclaimer_v14r16'/name
                assert build.sha(source)==row['pdf_sha256']
                shutil.copyfile(source,context/'tests/ms-release-fixtures/pdf'/name)
        shutil.copyfile(ROOT/'tests/test_alpha_ms_disclaimer_release_v14r16.py',context/'tests/test_alpha_ms_disclaimer_release_v14r16.py')
        release={'scope':'MS page-two owner-supplied disclaimer copy only; existing MS Georgia typography',
            'base_image':IMAGES[kind],'layout_version':LAYOUT,'copy_sha256':acceptance['copy_sha256'],
            'baseline_files_sha256':{n:digest(original[n]) for n in FILES[:2]},
            'installed_files_sha256':{n:digest(raw) for n,raw in patch.items()},
            'financial_calculation_code_unchanged':True,'alpha_world_t_unchanged':True,
            'presentations_unchanged':True,'source_data_changed':False,'report_approvals_changed':False}
        build.save(context,'MS_LAYOUT_RELEASE',release)
        before="import hashlib,pathlib; e="+repr(release['baseline_files_sha256'])+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in e.items())"
        after="import hashlib,pathlib;from ibkr_etl.monthly_artifacts import factsheet_layout_version; e="+repr(release['installed_files_sha256'])+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in e.items()); assert factsheet_layout_version('alpha-world-ms')=="+repr(LAYOUT)+"; print("+repr(MARKER)+")"
        docker=f'FROM {IMAGES[kind]}\nENV PYTHONPATH=/app/src:/app/scripts:/app PYTHONDONTWRITEBYTECODE=1\n'
        docker+='RUN '+json.dumps(['python','-c',before])+'\nCOPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/\nCOPY --chown=10001:10001 tests/ /app/ms-disclaimer-v14r16-tests/\nCOPY --chown=10001:10001 MS_LAYOUT_RELEASE.json /app/\n'
        docker+='RUN ["python","scripts/check_report_runtime.py"]\nRUN ["python","-m","unittest","discover","-s","/app/ms-disclaimer-v14r16-tests","-v"]\n'
        docker+='RUN '+json.dumps(['python','-c',after])+'\n'
        (context/'Dockerfile').write_text(docker,encoding='utf-8')
        build.save(folder,'source-intent',{'tag':f'ibkr-platform:alpha-ms-disclaimer-v14r16-{kind}-20261009',
            'base_image':IMAGES[kind],'layout_version':LAYOUT,'scope':release['scope'],
            'files_sha256':{p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()}})
    build.save(FOLDER,'prepared',{'complete':True,'candidates':2,'accepted_local_manifest_sha256':build.sha(ROOT/'output/pdf/AlphaWorld_MS_Disclaimer_v14r16/manifest.json')})
    print(json.dumps({'prepared':True,'candidates':2,'exact_parent_layers_verified':True}),flush=True)


def qualify(kind):
    folder=FOLDER/kind;runtime=folder/'local-runtime';context=folder/'context'
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests',ROOT/'scripts'))),
         'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TEMP':str(ROOT/'.work'),'TMP':str(ROOT/'.work')}
    result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],env=env,capture_output=True,text=True,encoding='utf-8')
    log=folder/f'local-tests-{len(list(folder.glob("local-tests-*.log")))+1}.log'
    with log.open('x',encoding='utf-8') as stream:stream.write(result.stdout+result.stderr)
    if result.returncode:raise ValueError('Isolated tests failed: '+str(log))
    count=int(re.findall(r'Ran (\d+) tests',result.stderr)[-1])
    args=[sys.executable,str(ROOT/'scripts/replay_ms_disclaimer_v14r16.py'),'--output',str(folder/'replay')]
    subprocess.run(args,env=env,check=True)
    proof=read(folder/'replay','verification');assert proof['count']==21 and proof['status']=='passed'
    build.save(folder,'local-qualification',{'status':'isolated_local_candidate_passed','source_intent_sha256':build.sha(folder/'source-intent.json'),
        'tests_passed':count,'tests_log_sha256':build.sha(log),'replay':{'months_verified':21,'manifest_sha256':build.sha(folder/'replay/verification.json')}})
    print(json.dumps({'kind':kind,'tests_passed':count,'all_months':21}),flush=True)


def configure():
    native.FOLDER=FOLDER;native.IMAGES=IMAGES;native.LAYOUT=LAYOUT;native.MARKER=MARKER


def gates():
    import deploy_ms_headers_v14r15 as d
    from rehearse_ms_worker_image_rollback import restore_protected
    from run_specialist_gateway_read_verification import retain
    if (FOLDER/'release-gate-binding.json').exists():
        record=read(FOLDER,'release-gate-binding');d.verify_protected(record)
        assert all(build.sha(FOLDER/n)==h for n,h in record['evidence_files_sha256'].items())
    else:
        accepted=read(FOLDER,'cloud-acceptance');d.verify_protected(accepted)
        assert accepted['status']=='passed' and accepted['cloud_pdf_checks']==42 and accepted['website_worker_pdf_bytes_identical']
        paths=['cloud-acceptance.json']
        for kind in IMAGES:
            folder=FOLDER/kind;local=read(folder,'local-qualification');tested=read(folder,'native-build-qualified')
            assert tested['qualified'] and tested['linux_tests']==local['tests_passed'] and local['tests_passed']=={'web':122,'worker':95}[kind]
            assert tested['native_log_sha256']==build.sha(folder/'native-build.log')
            assert local['source_intent_sha256']==build.sha(folder/'source-intent.json')
            scan=read(folder,'scan-completed');protected,_=restore_protected(read(folder,'scan-protected-receipt'))
            assert protected==scan and scan['image']==d.image(kind) and scan['assessment']['status']=='pass' and scan['assessment']['blocking_findings']==0
            for mode in ('candidate','rollback'):
                runtime=folder/(mode+'-runtime-v1');proof=read(runtime,'qualified');d.verify_protected(proof)
                assert proof['status']=='passed' and proof['count']==21 and proof['image']==(d.image(kind) if mode=='candidate' else IMAGES[kind])
                assert proof['proof_sha256']==build.sha(runtime/'verification.json')
            paths += [kind+'/'+n for n in ('source-intent.json','local-qualification.json','build-completed.json','native-build-qualified.json','native-build.log','scan-completed.json','scan-protected-receipt.json')]
            paths += [kind+'/'+mode+'-runtime-v1/'+n for mode in ('candidate','rollback') for n in ('qualified.json','verification.json')]
        record={'status':'passed','evidence_files_sha256':{n:build.sha(FOLDER/n) for n in paths}}
        build.save(FOLDER,'release-gate-binding',{**record,'protected_receipt':retain(record,'ms-disclaimer-v14r16-exact-gates')})
    for kind in IMAGES:
        folder=FOLDER/kind;context=folder/'context'
        assert {p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()}==read(folder,'source-intent')['files_sha256']


def configure_deploy():
    import deploy_ms_headers_v14r15 as d
    d.FOLDER=FOLDER/'deployment-v1';d.REVISION_SUFFIX='ms-disclaimer-v14r16'
    d.AUTHORIZATION='Owner explicitly approved deployment of accepted MS disclaimer, replacement of 21 historical factsheets and future rendering; preserve financials, T, presentations, source data, auth and settings'
    d.gates=gates
    return d


def registration(stage,mode):
    """Reuse the proven metadata-only registration transaction, with new binding."""
    import uuid
    from run_specialist_gateway_read_verification import retain
    d=configure_deploy()
    import control_ms_headers_registration_v14r15 as r
    r.FOLDER=FOLDER/'registration-v1';r.PREFIX=PREFIX+'registration/'
    if not r.FOLDER.exists():
        assert stage=='prepare' and mode=='baseline'
        gates();completion=read(d.FOLDER,'completion');d.verify_protected(completion)
        assert completion['status']=='scoped_headers_deployed'
        proof=read(FOLDER/'worker/candidate-runtime-v1','verification')
        record={'release_id':str(uuid.uuid4()),'image':d.image('worker'),'layout_version':LAYOUT,
            'installed_files_sha256':read(FOLDER/'worker/context','MS_LAYOUT_RELEASE')['installed_files_sha256'],
            'periods':PERIODS,'proof_blob':PREFIX+'worker/candidate/verification.json',
            'proof_sha256':build.sha(FOLDER/'worker/candidate-runtime-v1/verification.json'),
            'scope':'21 qualified disclaimer-only factsheet replacements for owner review; source, financials, presentations, original files and approval history preserved'}
        assert proof['count']==21 and proof['image']==record['image']
        r.FOLDER.mkdir()
        build.save(r.FOLDER,'plan',{**record,'protected_receipt':retain(record,'ms-disclaimer-v14r16-registration-plan')})
    sys.argv=[sys.argv[0],stage,mode];r.main()


def probe_binding(kind,mode):
    folder=FOLDER/kind;release=read(folder/'context','MS_LAYOUT_RELEASE')
    if mode=='rollback':image=IMAGES[kind];files=release['baseline_files_sha256'];layout=OLD_LAYOUT
    else:
        scan=read(folder,'scan-completed');assert scan['assessment']['status']=='pass'
        output=read(folder,'build-completed')['output_images'][0]
        image=output['registry']+'/'+output['repository']+'@'+output['digest'];assert image==scan['image']
        files=release['installed_files_sha256'];layout=LAYOUT
    return {'mode':mode,'image':image,'installed_files_sha256':files,'layout_version':layout,
            'prefix':PREFIX+kind+'/'+mode+'/','periods':PERIODS}


if __name__=='__main__':
    configure();stage=sys.argv[1]
    if stage=='prepare':prepare()
    elif stage=='qualify':qualify(sys.argv[2])
    elif stage in ('build','inspect','log'):native.native(stage,sys.argv[2])
    elif stage=='probe':
        import control_ms_headers_probe_v14r15 as p
        p.binding=probe_binding
        sys.argv=[sys.argv[0],*sys.argv[2:]];p.main()
    elif stage=='deploy':
        d=configure_deploy();sys.argv=[sys.argv[0],*sys.argv[2:]];d.main()
    elif stage=='registration':registration(*sys.argv[2:4])
    else:raise ValueError('Unknown stage')
