"""Build a compatible recovery image: installed worker plus one version fix.

This is not the unmodified prior image and must not be reported as such.
No running workload is modified by this controller.
"""
import os,re,shutil,subprocess,sys
from pathlib import Path
import release_platform_worker_stage3_v3_20261009 as candidate

BUNDLE=candidate.BUNDLE
BASE_IMAGE=candidate.BASE_IMAGE
JOB=candidate.JOB
release=candidate.release
release.WEB=BUNDLE/'worker-recovery-v2'
release.MARKER='PASS: compatible monthly recovery baseline; financial renderers unchanged'

def prepare():
    folder=release.WEB
    if folder.exists():raise ValueError('Preserve recovery evidence')
    original=(candidate.ROOT/'.work/platform-corrections-20261009-v1/changed-sources/worker/generate_month_end_package.py').read_bytes()
    source=(BUNDLE/'recovery-worker/generate_month_end_package.py').read_bytes()
    before=b'        if factsheet_design_sha:\n            factsheet_version = ('
    after=b'        if factsheet_design_sha and factsheet_design_sha != factsheet_sha:\n            factsheet_version = ('
    # Normalize line endings only for exact one-line-diff verification.
    if source.replace(b'\r\n',b'\n')!=original.replace(b'\r\n',b'\n').replace(before,after,1):
        raise ValueError('Recovery exceeds the single approved template identity correction')
    context=folder/'context'
    shutil.copytree(BUNDLE/'recovery-worker',context/'worker',ignore=shutil.ignore_patterns('Dockerfile'))
    shutil.copytree(BUNDLE/'recovery-tests',context/'tests',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    shutil.copyfile(BUNDLE/'recovery-worker/Dockerfile',context/'Dockerfile')
    release.save(folder/'source-intent.json',{'tag':'ibkr-platform:platform-worker-recovery-20261009-v2',
        'base_image':BASE_IMAGE,'files_sha256':release.manifest(context),
        'scope':'Recovery-only compatible baseline; single duplicate-template-hash correction',
        'unmodified_previous_image':False,'financial_renderers_and_templates_unchanged':True})
    run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],
        env={**os.environ,'WORKER_CANDIDATE':str(context/'worker/generate_month_end_package.py'),
             'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1'},capture_output=True,text=True,encoding='utf-8',timeout=60)
    with (folder/'local-tests.log').open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
    if run.returncode:raise ValueError(run.stderr)
    if {p.relative_to(context).as_posix():release.sha(p) for p in context.rglob('*') if p.is_file()}!=release.read(folder/'source-intent.json')['files_sha256']:
        raise ValueError('Unexpected build context files')
    count=int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
    release.save(folder/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':count,
        'source_intent_sha256':release.sha(folder/'source-intent.json'),'log_sha256':release.sha(folder/'local-tests.log')})
    print({'prepared':True,'tests_passed':count,'live_changes':False})

if __name__=='__main__':
    {'prepare':prepare,'build':candidate.dispatch,'reconcile':release.reconcile,'logs':release.logs}[sys.argv[1]]()
