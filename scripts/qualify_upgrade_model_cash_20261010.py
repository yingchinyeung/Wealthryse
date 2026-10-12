"""Scoped website patch regression; all tests offline and synthetic."""
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
        if old.get('src/ibkr_etl/web.py')!='7b478e52d664486e097455956cde4a38e42775b9569b6ec519cb6c2551333f14' or 'src/ibkr_etl/model_cash_display.py' in old:raise ValueError('Installed source boundary changed')
        (root/'before.json').write_text(json.dumps(old,sort_keys=True));return
    if stage!='after':raise ValueError('Unknown qualification stage')
    old=json.loads((root/'before.json').read_bytes());before=manifest();changed={name for name in set(old)|set(before) if old.get(name)!=before.get(name)}
    allowed={'src/ibkr_etl/web.py','src/ibkr_etl/model_cash_display.py','ALPHA_T_RUNTIME_RELEASE.json'}
    if changed!=allowed:raise ValueError('Application change exceeded exact cash display scope')
    release=json.loads(Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
    for name,value in release['installed_files_sha256'].items():
        if before.get(name)!=value:raise ValueError('Runtime release source manifest differs: '+name)
    with patch.object(socket.socket,'connect',deny),patch.object(socket.socket,'connect_ex',deny),patch.object(socket,'create_connection',deny),patch.dict(os.environ,{'PORTAL_TEST_RUNTIME':'/app','RISK_DISPLAY_SOURCE':'/app/src/ibkr_etl/risk_display.py','PATH':'/usr/local/bin','PYTHONPATH':'/app/src:/app/scripts'},clear=True):
        suites=unittest.TestSuite([unittest.defaultTestLoader.discover('/app/identity-tests'),unittest.TestLoader().discover('/qualification/tests')])
        result=unittest.TextTestRunner(verbosity=2).run(suites)
    skips=[(test.id(),reason) for test,reason in result.skipped]
    expected=[('test_alpha_world_t_reporting.TReportingTests.test_month_end_snapshot_uses_t_profile_and_preserves_internal_limit','Private qualification fixture unavailable')]
    if not result.wasSuccessful() or result.testsRun!=301 or skips!=expected or before!=manifest():raise ValueError('Website/source regression failed')
    print('MODEL_CASH_NATIVE '+json.dumps({'passed':True,'tests':result.testsRun,'passed_tests':result.testsRun-len(skips),'explicit_private_fixture_skip':skips,'changed_files':sorted(changed),'other_application_files_preserved':True,
        'new_file_sha256':before['src/ibkr_etl/model_cash_display.py'],'web_sha256':before['src/ibkr_etl/web.py'],'database_connections':0,'live_reports_changed':False}),flush=True)


if __name__=='__main__':main()
