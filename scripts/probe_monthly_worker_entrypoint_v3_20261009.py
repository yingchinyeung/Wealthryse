"""Real registry/main call path; fail closed before any generation or write."""
import contextlib,hashlib,io,json,os,sys,subprocess
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
os.environ.update(WORKER_SETTINGS)
os.environ['PGOPTIONS']='-c default_transaction_read_only=on -c statement_timeout=30000'
sys.path[:0]=['/app/src','/app/scripts']
import generate_month_end_package as worker
import run_registry_month_end_packages as registry

class GenerationRequired(RuntimeError):pass
def prohibited(*args,**kwargs):
    raise GenerationRequired('Read-only qualification stopped before generation')

def main():
    source=Path('/app/scripts/generate_month_end_package.py').read_bytes()
    if hashlib.sha256(source).hexdigest()!=EXPECTED_WORKER_SHA:raise ValueError('Installed candidate differs')
    runs=[]; package_checks=[]
    original=worker._existing_package
    def inspect_package(connection,**kwargs):
        result=original(connection,**kwargs)
        rows=connection.execute('''SELECT report_type,status,template_version FROM report_generation_runs
            WHERE tenant_id=? AND snapshot_id=CAST(? AS uuid)
            ORDER BY generation_run_id DESC''',(kwargs['tenant_id'],kwargs['snapshot_id'])).fetchall()
        latest={}
        for row in rows:latest.setdefault(str(row['report_type']),dict(row))
        package_checks.append({'subject':kwargs['account_id'],'helper_result':result,
            'expected_versions':kwargs['expected_versions'],'latest_report_metadata':latest})
        return result
    def bounded_subprocess(args,*,cwd,env,check,timeout):
        if args!=[sys.executable,'/app/scripts/generate_month_end_package.py'] or timeout!=1200:
            raise ValueError('Unexpected subprocess in read-only acceptance')
        stream=io.StringIO()
        try:
            with patch.dict(os.environ,env,clear=True),contextlib.redirect_stdout(stream):code=worker.main()
        except GenerationRequired:
            runs.append({'subject':env['PILOT_ACCOUNT_ID'],'status':'generation_required_blocked_by_readonly_probe'})
            return subprocess.CompletedProcess(args,0)
        if code:raise subprocess.CalledProcessError(code,args)
        result=json.loads(stream.getvalue())
        if result.get('status') not in ('already_released','already_generated'):
            raise ValueError('Acceptance must reuse current artifacts only')
        runs.append({'subject':env['PILOT_ACCOUNT_ID'],'status':result['status']})
        return subprocess.CompletedProcess(args,0)
    with patch.object(worker,'freeze_month_end_snapshot',prohibited),patch.object(worker,'generate_month_end_commentary',prohibited),\
         patch.object(worker,'render_factsheet',prohibited),patch.object(worker,'upload_bytes',prohibited),\
         patch.object(worker,'upload_ms_review',prohibited),patch.object(worker,'_existing_package',inspect_package),patch.object(registry,'subprocess',SimpleNamespace(
             run=bounded_subprocess,CalledProcessError=subprocess.CalledProcessError,TimeoutExpired=subprocess.TimeoutExpired)):
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream):status=registry.main()
    if status or len(runs)!=5:raise ValueError('All five active subject paths must pass')
    print('PLATFORM_CORRECTION_PROBE '+json.dumps({'passed':all(r['status']!='generation_required_blocked_by_readonly_probe' for r in runs),'entrypoint':'run_registry_month_end_packages.main',
          'normal_subject_paths':runs,'package_checks':package_checks,'worker_sha256':EXPECTED_WORKER_SHA,'database_mode':'read_only',
          'database_writes':0,'blob_writes':0,'model_calls':0,'report_approvals_changed':False}),flush=True)

if __name__=='__main__':main()
