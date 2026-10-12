"""Qualify the exact two-file handover change on installed Linux dependencies."""
import hashlib,json,os,shutil,subprocess,sys,unittest
from pathlib import Path
APP=Path('/app');Q=Path('/qualification/report-handover')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def inventory():return {p.relative_to(APP).as_posix():sha(p) for p in APP.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}
def main():
    if os.getuid()!=10001:raise ValueError('Non-root qualification required')
    plan=json.loads((Q/'prepared.json').read_bytes());before=inventory()
    if any(before.get(n)!=v for n,v in plan['baseline'].items()):raise ValueError('Installed baseline differs')
    for name,digest in plan['candidate'].items():
        if sha(Q/'payload'/name)!=digest:raise ValueError('Source drift')
        shutil.copyfile(Q/'payload'/name,APP/name)
    release=APP/'ALPHA_T_RUNTIME_RELEASE.json';value=json.loads(release.read_bytes())
    value['installed_files_sha256'].update(plan['candidate']);value['scope']=plan['scope']
    release.write_text(json.dumps(value,sort_keys=True,indent=2),encoding='utf-8')
    after=inventory();changed={n for n in set(before)|set(after) if before.get(n)!=after.get(n)}
    if changed!=set(plan['candidate'])|{'ALPHA_T_RUNTIME_RELEASE.json'}:raise ValueError('Unrelated change')
    (Q/'src').symlink_to(APP/'src',target_is_directory=True)
    sys.path[:0]=[str(APP/'src'),str(APP/'scripts')]
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(str(Q/'tests')))
    if not result.wasSuccessful() or result.testsRun!=plan['test_count'] or result.skipped:raise ValueError('Native tests incomplete')
    runtime=subprocess.run([sys.executable,'scripts/check_report_runtime.py'],cwd=APP,capture_output=True,text=True,timeout=60)
    if runtime.returncode:raise ValueError('Runtime checks failed')
    if inventory()!=after:raise ValueError('Tests changed installed source')
    print('REPORT_HANDOVER_NATIVE '+json.dumps({'passed':True,'tests':result.testsRun,'candidate_hashes':plan['candidate'],
        'changed_files':sorted(changed),'unrelated_files_preserved':True,'runtime_passed':True,
        'production_database_access':False,'private_fixture_uploaded':False}),flush=True)
if __name__=='__main__':main()
