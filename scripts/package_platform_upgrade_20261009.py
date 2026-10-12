"""Assemble source-bound build contexts and patch diffs; never build or deploy."""
import difflib
import hashlib
import json
from pathlib import Path
import shutil
from qualify_platform_upgrade_20261009 import ROOT, OUT, BASE, ALLOWED, manifest, sha


def main():
    qualified=json.loads((OUT/'evidence/local-qualification.json').read_bytes())
    if qualified['runtime_sha256']!=manifest(OUT/'runtime') or qualified['tests_sha256']!=manifest(OUT/'tests'):
        raise ValueError('Candidate changed after qualification; rerun all tests')
    context=OUT/'build/application'
    if context.exists():raise ValueError('Build context exists; preserve frozen bundle')
    context.mkdir(parents=True)
    baseline=json.loads((OUT/'baseline-manifest.json').read_bytes())
    expected={name:sha(BASE/name) for name in ALLOWED if (BASE/name).exists()}
    image=baseline['base_images']['web']
    docker='FROM '+image+'\n'
    check="from pathlib import Path;import hashlib;expected="+repr(expected)+";assert all(hashlib.sha256((Path('/app')/p).read_bytes()).hexdigest()==s for p,s in expected.items())"
    docker+='RUN '+json.dumps(['python','-c',check])+'\n'
    release_source=ROOT/'.work/platform-corrections-20261009-v1/web-v2/context/ALPHA_T_RUNTIME_RELEASE.json'
    release=json.loads(release_source.read_bytes())
    for name in sorted(ALLOWED):
        source=OUT/'runtime'/name;target=context/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,target)
        release['installed_files_sha256'][name]=sha(source)
        docker+='COPY --chown=10001:10001 '+name+' /app/'+name+'\n'
    release['base_image']=image
    release['scope']='Consolidated application candidate; financial renderers and templates unchanged; not promoted'
    (context/'ALPHA_T_RUNTIME_RELEASE.json').write_text(json.dumps(release,indent=2),encoding='utf-8')
    shutil.copytree(OUT/'tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copytree(OUT/'worker',context/'worker')
    docker+='COPY --chown=10001:10001 ALPHA_T_RUNTIME_RELEASE.json /app/\nCOPY --chown=10001:10001 tests/ /app/upgrade-tests/\nCOPY --chown=10001:10001 worker/ /app/worker/\n'
    docker+='RUN ["python","scripts/check_report_runtime.py"]\n'
    run="import os,subprocess,sys;os.environ['PORTAL_TEST_RUNTIME']='/app';os.environ['PYTHONPATH']='/app/src:/app/scripts';sys.exit(subprocess.call([sys.executable,'-m','unittest','discover','-s','/app/upgrade-tests','-v']))"
    docker+='RUN '+json.dumps(['python','-c',run])+'\n'
    (context/'Dockerfile').write_text(docker,encoding='utf-8')
    patch=[]
    for name in sorted(ALLOWED):
        old=(BASE/name).read_text(encoding='utf-8') if (BASE/name).exists() else ''
        new=(OUT/'runtime'/name).read_text(encoding='utf-8')
        patch.extend(difflib.unified_diff(old.splitlines(keepends=True),new.splitlines(keepends=True),fromfile='a/'+name,tofile='b/'+name))
    old=(ROOT/'.work/platform-corrections-20261009-v1/changed-sources/worker/generate_month_end_package.py').read_text(encoding='utf-8')
    new=(OUT/'worker/generate_month_end_package.py').read_text(encoding='utf-8')
    patch.extend(difflib.unified_diff(old.splitlines(keepends=True),new.splitlines(keepends=True),fromfile='a/scripts/generate_month_end_package.py',tofile='b/scripts/generate_month_end_package.py'))
    (OUT/'candidate.patch').write_text(''.join(patch),encoding='utf-8')
    bundle={'status':'prepared_not_deployable','base_images':baseline['base_images'],
            'build_context_sha256':manifest(context),'source_patch_sha256':sha(OUT/'candidate.patch'),
            'local_qualification_sha256':sha(OUT/'evidence/local-qualification.json'),
            'release_gates_sha256':sha(OUT/'release-gates.json'),
            'infrastructure_sha256':manifest(OUT/'infra'),
            'worker_candidate_sha256':sha(OUT/'worker/generate_month_end_package.py'),
            'worker_base_source_sha256':baseline['baseline_sha256']['worker/generate_month_end_package.py'],
            'live_changes_made_by_preparation':False,
            'worker_linux_context_pending':'Capture exact installed worker release manifest before preparing overlay; do not deploy whole dirty repository'}
    (OUT/'bundle-manifest.json').write_text(json.dumps(bundle,indent=2),encoding='utf-8')
    print('Prepared source-bound application build context, worker patch, infrastructure modules and blocked release gates.')

if __name__=='__main__':main()
