"""Rebase qualified reporting corrections on the exact onboarding-render image."""
import json,shutil,sys
from pathlib import Path
import release_reporting_history_20261011 as h
import scan_ms_presentation_worker_candidate as scan

ROOT=h.ROOT;r=h.r;c=h.c
PRIOR=ROOT/'.work/platform-upgrade-20261011-stage34/reporting-image-v5'
CAPTURE=PRIOR.parent/'consumer-source-onboard-render-v1'
OUT=ROOT/'.work/platform-upgrade-20261011-stage36/onboard-render-image-v1'
BASE='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:3c0e67ca1c41746387ea0bafabafdf39d4bf48295e6b736bca41536dd147c729'
c.OUT=OUT;c.BASE_IMAGE=BASE;c.JOB='ibkrv2stg-onboard-render';h.OUT=OUT

def prepare():
    if OUT.exists():raise ValueError('Preserve existing candidate')
    source,_,_=scan.verify(PRIOR)
    capture=r.read(CAPTURE/'result.json');manifest=r.read(PRIOR/'context/patch-manifest.json')
    if not capture['passed'] or capture['image']!=BASE or capture['reporter_baseline_differences']:raise ValueError('Reviewed exact consumer baseline required')
    for name,digest in manifest['baseline'].items():
        if capture['files_sha256'].get(name)!=digest or r.sha(CAPTURE/name)!=digest:raise ValueError('Consumer baseline drift: '+name)
    if any(n not in capture['requested_but_absent'] for n in manifest['new_files']):raise ValueError('New helper presence unreviewed')
    if source['candidate_hashes']!=manifest['candidate']:raise ValueError('Qualified candidate mismatch')
    native=r.read(PRIOR/'native-build-qualified.json')
    if not native['qualified'] or native['linux_tests']!=104 or native['native_log_sha256']!=r.sha(PRIOR/'native-build.log'):raise ValueError('Prior native qualification required')
    context=OUT/'context';shutil.copytree(PRIOR/'context',context)
    old=(context/'Dockerfile').read_text()
    if old.count('FROM '+source['base_image'])!=1:raise ValueError('Exact Docker baseline required')
    (context/'Dockerfile').write_text(old.replace('FROM '+source['base_image'],'FROM '+BASE),encoding='utf-8',newline='\n')
    r.save(OUT/'source-intent.json',{'tag':'ibkr-platform:reporting-onboard-corrections-20261011-v1','base_image':BASE,
      'files_sha256':r.manifest(context),'candidate_hashes':manifest['candidate'],
      'scope':'Exact v5 reporting patch on existing onboarding-render base. All unrelated files, entrypoint, templates, broker data and approvals preserved.',
      'source_only':True,'private_records_uploaded':False,'database_fixture_uploaded':False,
      'consumer_capture_sha256':r.sha(CAPTURE/'result.json'),'prior_source_intent_sha256':r.sha(PRIOR/'source-intent.json')})
    r.save(OUT/'local-qualification.json',{'status':'isolated_local_candidate_passed','tests_run':104,
      'source_intent_sha256':r.sha(OUT/'source-intent.json'),'candidate_source_matches_qualified_v5':True,
      'consumer_native_tests_required':104,'previous_local_proof_sha256':r.sha(PRIOR/'local-qualification.json')})
    print(json.dumps({'prepared':True,'consumer':c.JOB,'source_only':True,'deployed':False,'native_qualification_pending':True}))

if __name__=='__main__':
    {'prepare':prepare,'build':c.dispatch,'inspect':c.inspect,'logs':h.logs,'reconcile':c.reconcile}[sys.argv[1]]()
