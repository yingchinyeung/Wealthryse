"""Apply only hash-bound reporting patches and qualify inside the pinned image."""
import hashlib,json,os,shutil,subprocess,sys,unittest
from pathlib import Path
ROOT=Path('/app');Q=Path('/qualification/reporting-20261011')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory():return {p.relative_to(ROOT).as_posix():sha(p) for p in ROOT.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def main():
    if os.getuid()!=10001:raise ValueError('Non-root qualification required')
    manifest=json.loads((Q/'patch-manifest.json').read_bytes());before=inventory()
    for name,h in manifest['baseline'].items():
        if before.get(name)!=h:raise ValueError('Installed source differs: '+name)
    for name in manifest['new_files']:
        if name in before:raise ValueError('New helper already exists: '+name)
    for name,h in manifest['candidate'].items():
        source=Q/'payload'/name
        if sha(source)!=h:raise ValueError('Candidate source changed')
        shutil.copyfile(source,ROOT/name)
    release=ROOT/'ALPHA_T_RUNTIME_RELEASE.json'
    value=json.loads(release.read_bytes())
    value['installed_files_sha256'].update(manifest['candidate'])
    value['scope']='Qualified reporting currency, source-labelled benchmarks, complete cash notes, bounded conclusions, MS note spacing and T exact shared-renderer integration. No template, NAV, approval or source-data change.'
    release.write_text(json.dumps(value,sort_keys=True,indent=2),encoding='utf-8')
    after=inventory();changed={n for n in set(before)|set(after) if before.get(n)!=after.get(n)}
    if changed!=set(manifest['candidate'])|{'ALPHA_T_RUNTIME_RELEASE.json'}:raise ValueError('Unrelated installed files changed')
    for name,h in manifest['candidate'].items():
        if after[name]!=h:raise ValueError('Installed candidate mismatch')
    # Source-inspection tests resolve ../src. No copied production data needed.
    (Q/'src').symlink_to(ROOT/'src',target_is_directory=True)
    sys.path[:0]=[str(ROOT/'src'),str(ROOT/'scripts')]
    suite=unittest.defaultTestLoader.discover(str(Q/'tests'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.testsRun!=72 or result.skipped:raise ValueError('Native reporting regressions incomplete')
    for name in manifest['node_tests']:
        if (ROOT/'scripts'/name).exists():raise ValueError('Preserve existing test path: '+name)
    for name in manifest['node_tests']:shutil.copyfile(Q/'node-tests'/name,ROOT/'scripts'/name)
    run=subprocess.run(['node','--test','--test-reporter=tap',*[str(ROOT/'scripts'/n) for n in manifest['node_tests']]],capture_output=True,text=True,timeout=60)
    print(run.stdout)
    if run.returncode or '# pass 13\n' not in run.stdout or '# fail 0\n' not in run.stdout:raise ValueError('Native Node tests failed')
    for name in manifest['node_tests']:(ROOT/'scripts'/name).unlink()
    if inventory()!=after:raise ValueError('Native tests changed installed files')
    runtime=subprocess.run([sys.executable,'scripts/check_report_runtime.py'],check=True,capture_output=True,text=True,timeout=60)
    print(runtime.stdout)
    print('REPORTING_IMAGE_NATIVE '+json.dumps({'passed':True,'python_tests':72,'node_tests':13,'baseline_files':len(before),'changed_files':sorted(changed),'unrelated_files_preserved':True,'candidate_hashes':manifest['candidate'],'database_access':False,'portfolio_fixture_uploaded':False}),flush=True)
if __name__=='__main__':main()
