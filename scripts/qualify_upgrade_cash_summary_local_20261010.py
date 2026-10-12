"""Keep the private T integration fixture local; upload only application code."""
import os,re,subprocess,sys
from pathlib import Path
import build_upgrade_cash_summary_20261010 as b

def main():
    out=b.OUT;root=b.ROOT
    if (out/'local-qualification.json').exists():raise ValueError('Preserve previous evidence')
    frozen=root/'.work/platform-upgrade-20261009-stage7/app-release-v1/context/tests'
    test=frozen/'test_alpha_world_t_reporting.py'
    code="""import importlib.util,sys,unittest
from pathlib import Path
path=Path(sys.argv[1]);spec=importlib.util.spec_from_file_location('frozen_t_reporting',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.__file__=str(Path.cwd()/'tests/test_alpha_world_t_reporting.py')
suite=unittest.TestSuite([module.TReportingTests('test_month_end_snapshot_uses_t_profile_and_preserves_internal_limit'),unittest.TestLoader().discover(sys.argv[2])])
result=unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful() or result.testsRun!=29 or result.skipped:raise SystemExit(1)
"""
    if b.h.sha(out/'local-runtime/src/ibkr_etl/web.py')!=b.h.sha(out/'context/src/ibkr_etl/web.py'):raise ValueError('Local candidate differs')
    if b.h.sha(out/'local-runtime/src/ibkr_etl/model_cash_display.py')!='f43ea9a2ff37b6f967b5b851f1d05ad0d87bde28bd7f47e93245d7b3de6430c9':raise ValueError('Preserved model source differs')
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(out/'local-runtime/src',frozen))),'PYTHONDONTWRITEBYTECODE':'1'}
    done=subprocess.run([sys.executable,'-c',code,str(test),str(out/'context/tests')],cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=180)
    with (out/'local-qualification.log').open('x',encoding='utf-8') as f:f.write(done.stdout+done.stderr)
    if done.returncode or re.findall(r'Ran (\d+) tests',done.stderr)!=['29']:raise ValueError('Local integration failed')
    b.h.save(out/'local-qualification.json',{'passed':True,'tests':29,'private_fixture_tests':1,'synthetic_cash_tests':28,'private_records_uploaded':False,
        'frozen_test_sha256':b.h.sha(test),'log_sha256':b.h.sha(out/'local-qualification.log'),
        'responsive_cases':b.h.read(out/'browser-v3/result.json'),'source_files_sha256':b.h.manifest(out/'context')})
    print('29 local tests passed; private fixture not uploaded')

if __name__=='__main__':main()
