"""Isolated regression reproductions. Synthetic data only. Never open a database."""
import ast, contextlib, hashlib, json, sys, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-review-20261009-v1'
RUNTIME=ROOT/'.work/alpha-t-download-names-20261009-v2/web/local-runtime'
sys.path[:0]=[str(RUNTIME/'src'),str(RUNTIME/'scripts'),str(ROOT/'tests')]
from ibkr_etl import web

def main():
    evidence={}
    payload=json.dumps({'subject_id':'U16088571','synthetic':True})
    connection=MagicMock()
    connection.execute.return_value.fetchone.return_value={'snapshot_payload_json':payload,'data_sha256':hashlib.sha256(payload.encode()).hexdigest(),'subject_id':'U16088571','period_end':'2026-09-30','status':'frozen'}
    @contextlib.contextmanager
    def database():yield connection
    with patch.object(web,'_database',database),patch.object(web,'_portfolio_access_context',side_effect=AssertionError('must authorize subject')) as guard:
        response=web.export_reporting_snapshot('latest',download=True,user={'object_id':'synthetic-viewer','role':'viewer','allowed_subject_ids':['hong-kong-china'],'authorization_source':'database'})
        evidence['unscoped_snapshot_export']={'status':response.status_code,'subject_access_guard_called':guard.called,'returned_synthetic_pilot_payload':json.loads(response.body)['subject_id']=='U16088571','selected_parameters':list(connection.execute.call_args.args[1])}
    path=ROOT/'.work/alpha-t-runtime-release-20261009-v7/worker/local-runtime/scripts/generate_month_end_package.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_existing_package')
    namespace={}
    exec(compile(ast.Module(body=[function],type_ignores=[]),str(path),'exec'),namespace)
    try:
        namespace['_existing_package'](None,tenant_id='synthetic',account_id='synthetic',period_end='2026-09-30',expected_versions={},allow_replacement=False,snapshot_id=None)
    except TypeError as error:
        evidence['month_end_signature']={'reproduced':True,'error':str(error),'file_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    modules=['test_viewer_report_fixes','test_ai_analyst','test_burn_in','test_portfolio_assurance','test_alpha_t_download_names','test_alpha_ms_download_names']
    suite=unittest.defaultTestLoader.loadTestsFromNames(modules)
    with (OUT/'targeted-tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    evidence['targeted_tests']={'run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'modules':modules,'source_runtime':str(RUNTIME)}
    (OUT/'local-reproductions.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    print(json.dumps(evidence,indent=2))
if __name__=='__main__':main()
