"""Preserve the local release-policy regression result, separate from live QA."""
import json,os,re,subprocess,sys
from pathlib import Path
from control_upgrade_readers_20261010 import save
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'.work/platform-upgrade-20261011-stage34/release-policy-v1'
def main():
    if OUT.exists():raise ValueError('Preserve earlier qualification')
    OUT.mkdir()
    env={**os.environ,'PYTHONPATH':'src'+os.pathsep+'scripts','TEMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp'),'TMP':str(ROOT/'.work/platform-upgrade-20261011-stage33/test-temp')}
    modules=['tests.test_month_end_package_readiness','tests.test_monthly_reports','tests.test_report_design_updates','tests.test_monthly_release','tests.test_monthly_release_api']
    run=subprocess.run([sys.executable,'-m','unittest',*modules,'-v'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
    (OUT/'tests.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    if run.returncode or re.findall(r'Ran (\d+) tests',run.stderr)!=['47']:raise ValueError('Policy regressions failed')
    import hashlib
    save(OUT,'result',{'passed':True,'tests':47,'log_sha256':hashlib.sha256((OUT/'tests.log').read_bytes()).hexdigest(),'scope':'Local release, immutable history, guarded regeneration and account-scoped approval policy tests; not a live viewer test'})
    print(json.dumps({'passed':True,'tests':47}))
if __name__=='__main__':main()
