"""Offline native regression for a one-line cash label correction."""
import hashlib,json,os,socket,sys,unittest
from pathlib import Path
from unittest.mock import patch

def manifest():return {p.relative_to('/app').as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path('/app').rglob('*')) if p.is_file() and p.suffix!='.pyc'}
def deny(*args,**kwargs):raise RuntimeError('Network forbidden during synthetic UI tests')

def main():
    root=Path('/qualification');stage=sys.argv[1]
    if os.getuid()!=10001:raise ValueError('Nonroot qualification required')
    if stage=='before':
        old=manifest()
        if old.get('src/ibkr_etl/web.py')!='c7ebd37906cea94b164c96fb818e76cee2e2398e2c2c751b8b4bc18123e75891' or old.get('src/ibkr_etl/model_cash_display.py')!='f43ea9a2ff37b6f967b5b851f1d05ad0d87bde28bd7f47e93245d7b3de6430c9':raise ValueError('Installed source boundary changed')
        (root/'before.json').write_text(json.dumps(old,sort_keys=True));return
    if stage!='after':raise ValueError('Unknown qualification stage')
    old=json.loads((root/'before.json').read_bytes());before=manifest()
    changed={name for name in set(old)|set(before) if old.get(name)!=before.get(name)}
    if changed!={'src/ibkr_etl/web.py','ALPHA_T_RUNTIME_RELEASE.json'}:raise ValueError('Application change exceeded exact display scope')
    release=json.loads(Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    if any(before.get(name)!=value for name,value in release['installed_files_sha256'].items()):raise ValueError('Release manifest differs')
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny),patch.dict(os.environ,{'PORTAL_TEST_RUNTIME':'/app','RISK_DISPLAY_SOURCE':'/app/src/ibkr_etl/risk_display.py','PATH':'/usr/local/bin','PYTHONPATH':'/app/src:/app/scripts'},clear=True):
        suites=unittest.TestSuite([unittest.defaultTestLoader.discover('/app/identity-tests'),unittest.TestLoader().discover('/qualification/tests')])
        result=unittest.TextTestRunner(verbosity=2).run(suites)
    skips=[(test.id(),reason) for test,reason in result.skipped]
    expected=[('test_alpha_world_t_reporting.TReportingTests.test_month_end_snapshot_uses_t_profile_and_preserves_internal_limit','Private qualification fixture unavailable')]
    if not result.wasSuccessful() or result.testsRun!=301 or skips!=expected or before!=manifest():raise ValueError('Website/source regression failed')
    print('CASH_SUMMARY_NATIVE '+json.dumps({'passed':True,'tests':result.testsRun,'passed_tests':300,'explicit_private_fixture_skip':skips,'changed_files':sorted(changed),'other_application_files_preserved':True,
        'web_sha256':before['src/ibkr_etl/web.py'],'database_connections':0,'live_reports_changed':False}),flush=True)

if __name__=='__main__':main()
