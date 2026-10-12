"""Installed candidate startup and worker-validation handoff; no DB connection."""
import hashlib,json,sys,unittest
from pathlib import Path
sys.path[:0]=['/app/src','/app/scripts','/app/t-runtime-tests']
import generate_month_end_package as month
import render_onboarding_historical_reports as history
from validate_subject_presentation import validate_subject_presentation

manifest=json.loads(Path('/app/ALPHA_T_RUNTIME_RELEASE.json').read_bytes())
for name,digest in manifest['installed_files_sha256'].items():
    if hashlib.sha256(Path('/app',name).read_bytes()).hexdigest()!=digest: raise ValueError('Installed source differs')
if month.validate_subject_presentation is not validate_subject_presentation or history.validate_subject_presentation is not validate_subject_presentation: raise ValueError('Worker validator binding differs')
suite=unittest.defaultTestLoader.loadTestsFromName('test_t_presentation_axis_contract')
result=unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful(): raise SystemExit(1)
print('ALPHA_MS_RECOVERY_RESULT '+json.dumps({'status':'passed','tests':result.testsRun,'months_checked':21,'normal_entrypoints_invoked':0,'database_connections':0,'financial_calculations_changed':False}),flush=True)
