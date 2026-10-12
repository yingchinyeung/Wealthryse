"""Freeze a website/benchmark-only image; retain existing report renderer bytes."""
import hashlib
import json
from pathlib import Path
import unittest

import build_ms_presentation_worker_candidate as build
import run_ms_website_hkd_rehearsal_v6 as rehearsal

ROOT=build.ROOT
FOLDER=ROOT/'.work/alpha-ms-website-benchmark-candidate-20261008-v1'
TESTS=('test_ms_website_comparisons.py','test_provisional_benchmark.py',
       'test_onboarding_preview.py','test_market_calendar.py','test_subject_read_contract.py')


def main():
    stage=rehearsal.STAGE
    qualification=json.loads((stage/'qualified.json').read_bytes())
    rehearsal.probe.verify_protected(qualification)
    proof=json.loads((stage/'proof.json').read_bytes())
    if (build.sha(stage/'proof.json')!=qualification['proof_sha256']
            or proof['status']!='temporary_postgres_and_website_readers_passed'
            or not proof['secondary_failure_rollback_verified']
            or proof['database_persistent_writes']!=0):
        raise ValueError('Protected private PostgreSQL qualification required')
    modules=json.loads((stage/'frozen-modules.json').read_bytes())
    binding=json.loads((stage/'binding.json').read_bytes())
    for name,code in modules.items():
        current=(ROOT/'src/ibkr_etl'/(name+'.py')).read_text(encoding='utf-8') if name!='web' else rehearsal.scoped_web()
        if current!=code or hashlib.sha256(code.encode()).hexdigest()!=binding['module_sha256'][name]:
            raise ValueError('Qualified source changed: '+name)
    if FOLDER.exists(): raise ValueError('Preserve prior candidate')
    context=FOLDER/'context'; context.mkdir(parents=True)
    files={('src/ibkr_etl/'+name+'.py'):code for name,code in modules.items()}
    for name in ('read_ms_website_input_probe.py','run_ms_website_input_probe.py','probe_ms_website_comparison_inputs.py'):
        files['scripts/'+name]=(ROOT/'scripts'/name).read_text(encoding='utf-8')
    for name in TESTS:
        files['tests/'+name]=(ROOT/'tests'/name).read_text(encoding='utf-8')
    protected_renderer=json.loads((ROOT/'.work/alpha-ms-format-worker-candidate-20261008-r4/context/MS_PRESENTATION_WORKER_RELEASE.json').read_bytes())
    contract={'scope':'MS website S&P 500 / HSI HKD comparisons only',
        'base_image':rehearsal.probe.IMAGE,'approved_report_files_sha256':protected_renderer['installed_files_sha256'],
        'website_files_sha256':{'src/ibkr_etl/'+name+'.py':digest for name,digest in binding['module_sha256'].items()},
        'private_postgres_proof_sha256':qualification['proof_sha256'],
        'factsheets_changed':False,'ibkr_source_changed':False,'report_design_updates_included':False}
    files['MS_WEBSITE_BENCHMARK_RELEASE.json']=json.dumps(contract,indent=2)+'\n'
    files['scripts/check_ms_website_benchmark_candidate.py']='''import hashlib,json
from pathlib import Path
root=Path('/app')
r=json.loads((root/'MS_WEBSITE_BENCHMARK_RELEASE.json').read_bytes())
for section in ('approved_report_files_sha256','website_files_sha256'):
 for name,digest in r[section].items():
  if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest:
   raise ValueError('Scoped candidate bytes differ: '+name)
if 'report_design_updates' in (root/'src/ibkr_etl/web.py').read_text():
 raise ValueError('Unrelated report-editing feature must not enter this release')
from ibkr_etl import web,provisional_benchmark,onboarding_preview,website_benchmarks
assert website_benchmarks.MS_HKD_COMPARISON_VERSION=='benchmark-comparison-v2-ms-hkd'
print('PASS: MS website benchmark candidate and unchanged approved report files',flush=True)
'''
    files['Dockerfile']='\n'.join([
        'FROM '+rehearsal.probe.IMAGE,'ENV PYTHONPATH=/app/src:/app/scripts:/app',
        'COPY --chown=10001:10001 src/ibkr_etl/ /app/src/ibkr_etl/',
        'COPY --chown=10001:10001 scripts/ /app/scripts/',
        'COPY --chown=10001:10001 tests/ /app/ms-website-benchmark-tests/',
        'COPY --chown=10001:10001 MS_WEBSITE_BENCHMARK_RELEASE.json /app/',
        'RUN ["python", "scripts/check_ms_website_benchmark_candidate.py"]',
        'RUN ["python", "-m", "unittest", "discover", "-s", "/app/ms-website-benchmark-tests", "-v"]',''])
    for name,content in files.items():
        path=context/name; path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf-8',newline='\n') as f:f.write(content)
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_ms_website_comparisons.py')
    count=suite.countTestCases()
    for name in TESTS[1:]: count+=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern=name).countTestCases()
    record={'base_image':rehearsal.probe.IMAGE,'tag':'ibkr-platform:ms-website-hkd-20261008-v1',
        'files_sha256':{name:build.sha(context/name) for name in files},'expected_test_count':count,
        'private_postgres_qualification_sha256':build.sha(stage/'qualified.json'),
        'report_contract':contract,'normal_worker_entrypoints_changed':False}
    build.save(FOLDER,'source-intent',{**record,'protected_receipt':rehearsal.probe.retain(record,'ms-website-benchmark-source-v1')})
    print(json.dumps({'status':'website_only_candidate_frozen','tests_expected':count,
        'approved_report_files_preserved':len(contract['approved_report_files_sha256']),
        'running_services_changed':False}),flush=True)


if __name__=='__main__':main()
