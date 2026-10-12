"""Frozen, reversible one-registration correction; never approve new reports."""
import hashlib,json,sys
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
import release_report_handover_20261011 as release
import control_upgrade_g04_preflight_20261010 as b
from control_upgrade_readers_20261010 import save,execution_env
from control_upgrade_legacy_recovery_probe_20261009 import protected
ROOT=Path(__file__).resolve().parents[1];STAGE=ROOT/'.work/platform-upgrade-20261011-stage43'

def sources():return {n:hashlib.sha256((ROOT/'scripts'/n).read_bytes()).hexdigest() for n in (Path(__file__).name,'reconcile_ms_duplicate_registration_20261011.py')}

def main(action,phase):
    if action not in ('start','read') or phase not in ('rehearse','apply'):raise ValueError('Unsupported operation')
    release.unchanged();out=STAGE/('ms-duplicate-'+phase+'-v1')
    checkpoint=protected(ROOT/'.work/platform-upgrade-20261011-stage42/closeout.json')
    if not checkpoint['checkpoint_evidence_verified']:raise ValueError('Verified baseline required')
    provenance=protected(ROOT/'.work/platform-upgrade-20261011-stage42/duplicates-v1/result.json')
    review=protected(ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-pdf-review-v1/review.json')
    blobs=protected(ROOT/'.work/platform-upgrade-20261011-stage42/duplicate-blobs-v1/result.json')
    if not review['passed'] or not review['page1_render_identical'] or not review['newer_disclaimer_matches_user_requested_sprout_wording'] or not blobs['passed']:raise ValueError('Reviewed user-requested replacement required')
    job=b.request('GET',b.PATH+b.API)
    if action=='start':
        if out.exists():raise ValueError('Preserve/reconcile earlier attempt')
        expected_rows=None
        if phase=='apply':
            rehearsal=protected(STAGE/'ms-duplicate-rehearse-v1/result.json')
            intent=protected(STAGE/'ms-duplicate-rehearse-v1/intent.json')
            if not rehearsal['passed'] or not rehearsal['proof']['rehearsal_rolled_back'] or intent['sources']!=sources():raise ValueError('Exact rolled-back rehearsal required')
            expected_rows=rehearsal['proof']['rows_before']
        storage=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
        for row,verified in zip(provenance['proof']['duplicate_registrations'],blobs['checks']):
            prop=storage.get_blob_client(row['container_name'],row['blob_name']).get_blob_properties()
            if row['artifact_release_id']!=verified['artifact_release_id'] or prop.etag!=verified['etag'] or prop.size!=verified['bytes'] or prop.version_id!=verified['version_id']:raise ValueError('Previously verified file changed')
        b.runtime.safe_window();b.runtime.idle(b.JOB)
        code='PHASE='+repr(phase)+'\nEXPECTED='+repr(provenance['proof']['duplicate_registrations'])+'\nEXPECTED_ROWS='+repr(expected_rows)+'\n'
        code+='REVIEW_RECEIPT='+repr(review['protected_receipt'])+'\nBLOB_RECEIPT='+repr(blobs['protected_receipt'])+'\n'+(ROOT/'scripts/reconcile_ms_duplicate_registration_20261011.py').read_text()
        template=b.corrected_template(job,code);c=template['containers'][0];c['image']=release.BASE
        c['env']=[e for e in c.get('env',[]) if e['name']=='DATABASE_ADMIN_URL']
        if len(c['env'])!=1 or not c['env'][0].get('secretRef') or c['env'][0].get('value'):raise ValueError('Existing admin reference only')
        save(out,'intent',{'phase':phase,'template':template,'sources':sources(),'review_receipt':review['protected_receipt'],'blob_receipt':blobs['protected_receipt'],
            'scope':'Only withdraw obsolete MS September factsheet registration 55 in favor of already-approved 57. Preserve both Azure files, approval decision 28, all financial inputs and other registrations. Rehearsal rolls back records; apply records one audit event with exact prior state. No schema change or new report approval.',
            'rollback':'Restore only registration 55 from its exact before-state recorded in the service audit event after checking for intervening changes; append a compensating audit event. Never delete files or approval history.'})
        ack=b.request('POST',b.PATH+'/start'+b.API,data=template);save(out,'dispatch',{'execution':ack['name']});print(ack['name']);return
    intent=protected(out/'intent.json')
    if intent['sources']!=sources():raise ValueError('Frozen source changed')
    name=protected(out/'dispatch.json')['execution'];run=b.request('GET',b.PATH+'/executions/'+name+b.API)
    wanted=intent['template']['containers'][0];actual=run['properties']['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Runtime differs')
    execution_env(b.JOB,actual.get('env',[]),wanted['env']);status=run['properties']['status']
    if status not in ('Succeeded','Failed'):print(status);return
    prefix='MS_DUPLICATE_FINAL ' if status=='Succeeded' else 'MS_DUPLICATE_ERROR '
    rows=b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith '"+prefix+"' | project Result=substring(Log_s,"+str(len(prefix))+')')
    if len(rows)!=1:print('Awaiting exact proof; do not redispatch');return
    proof=json.loads(rows[0]['Result']);passed=status=='Succeeded' and proof.get('passed') is True
    if passed and (proof['phase']!=phase or proof['old_release']!=55 or proof['current_release']!=57 or proof['source_files_deleted']!=0 or proof['approval_decisions_changed']!=0):raise ValueError('Unexpected mutation scope')
    label='result' if passed else 'failure'
    if not (out/(label+'.json')).exists():save(out,label,{'passed':passed,'execution':name,'proof':proof,'saved_jobs_changed':False})
    print(json.dumps({'passed':passed,'proof':proof}))

if __name__=='__main__':main(*sys.argv[1:])
