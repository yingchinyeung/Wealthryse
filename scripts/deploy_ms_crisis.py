"""Qualified image-only MS crisis-evidence cutover; never approves reports."""
import json,sys
from types import SimpleNamespace
import release_ms_crisis as candidate
import deploy_ms_headers_v14r15 as base
import build_ms_presentation_worker_candidate as build
from run_specialist_gateway_read_verification import retain
FOLDER=candidate.FOLDER.parent/'candidate-v6'
LAYOUT='subject-completed-factsheet-model-v14r15-alpha-world-ms-right-aligned-dates'
def read(folder,name):return json.loads((folder/(name+'.json')).read_bytes())
def gates():
    binding=FOLDER/'release-gate-binding.json'
    if binding.exists():
        saved=read(FOLDER,'release-gate-binding');base.verify_protected(saved)
        for name,digest in saved['evidence_files_sha256'].items():
            if build.sha(candidate.ROOT/name)!=digest:raise ValueError('Qualification evidence changed: '+name)
        return
    acceptance=read(FOLDER,'cloud-acceptance');base.verify_protected(acceptance)
    if acceptance['status']!='passed' or acceptance['months']!=21 or not acceptance['financial_metrics_unchanged']:raise ValueError('Cloud acceptance required')
    paths=[FOLDER/'cloud-acceptance.json',candidate.FOLDER.parent/'local-visual-acceptance.json']
    visual=read(candidate.FOLDER.parent,'local-visual-acceptance')
    if visual['status']!='passed' or visual['changed_slides_visually_inspected']!=63:raise ValueError('All changed slides must be inspected')
    for kind in candidate.IMAGES:
        folder=FOLDER/kind;context=folder/'context';intent=read(folder,'source-intent')
        actual={p.relative_to(context).as_posix():build.sha(p) for p in context.rglob('*') if p.is_file()}
        if actual!=intent['files_sha256']:raise ValueError('Frozen candidate source changed')
        native=read(folder,'native-build-qualified')
        if not native['qualified'] or native['linux_tests']!=92 or native['native_log_sha256']!=build.sha(folder/'native-build.log'):raise ValueError('92 native tests required')
        scan=read(folder,'scan-completed');protected,_=base.restore_protected(read(folder,'scan-protected-receipt'))
        if protected!=scan or scan['image']!=base.image(kind) or scan['assessment']['status']!='pass' or scan['assessment']['blocking_findings']:raise ValueError('Exact image scan required')
        proof=read(folder/'candidate-runtime-v1','qualified');base.verify_protected(proof)
        if proof['image']!=base.image(kind) or proof['status']!='passed' or proof['count']!=21:raise ValueError('Normal Azure producer and all months must pass')
        # Exact currently installed v15 digest has a protected, read-only,
        # all-month native qualification from this same maintenance session.
        rollback=candidate.ROOT/'.work/alpha-ms-headers-v14r15-20261008-r2'/kind/'candidate-runtime-v1'
        old=read(rollback,'qualified');base.verify_protected(old)
        if old['image']!=candidate.IMAGES[kind] or old['status']!='passed' or old['count']!=21 or old['proof_sha256']!=build.sha(rollback/'verification.json'):raise ValueError('Qualified rollback digest required')
        paths += [folder/n for n in ('source-intent.json','local-qualification.json','build-completed.json','native-build-qualified.json','native-build.log','scan-completed.json','scan-protected-receipt.json')]
        paths += [folder/'candidate-runtime-v1'/n for n in ('qualified.json','verification.json')]
        paths += [rollback/n for n in ('qualified.json','verification.json')]
        paths += list(context.rglob('*'))
    record={'status':'passed','evidence_files_sha256':{p.relative_to(candidate.ROOT).as_posix():build.sha(p) for p in paths if p.is_file()},
        'authorization':'Owner asked to solve missing MS crisis replay; no report release or approval mutation',
        'rollback':'Exact currently installed v15 image digests and preserved configuration; protected all-month native qualifications verified'}
    build.save(FOLDER,'release-gate-binding',{**record,'protected_receipt':retain(record,'ms-crisis-v6-exact-release-gate-binding')})
def save(name,record):
    build.save(base.FOLDER,name,{**record,'protected_receipt':retain(record,'ms-crisis-v6-deploy-'+name)})
def configure():
    base.c=SimpleNamespace(FOLDER=FOLDER,IMAGES=candidate.IMAGES,LAYOUT=LAYOUT)
    base.FOLDER=FOLDER/'deployment-v1';base.REVISION_SUFFIX='ms-crisis-v6';base.COMPLETION_STATUS='scoped_crisis_deployed'
    base.AUTHORIZATION='Owner asked to solve the MS missing crisis replay approval blocker; scoped image-only update and new private report versions, no approval or source writes'
    base.gates=gates;base.save=save
def main():configure();base.main()
if __name__=='__main__':main()
