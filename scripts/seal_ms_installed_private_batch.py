"""Bind protected installed render, independent QA and private registration."""
import argparse
import json
from pathlib import Path

import deploy_ms_presentation_worker_release as release
from run_ms_installed_private_batch import protected
from run_specialist_gateway_read_verification import retain


def require(ok,message):
    if not ok:raise ValueError(message)


def main():
    p=argparse.ArgumentParser();p.add_argument('--folder',type=Path,required=True)
    args=p.parse_args();folder=args.folder.resolve();root=folder/'registered-batch-v1'
    require(not (root/'completion.json').exists(),'Preserve completed batch evidence')
    render=release.read(root,'render-qualified');protected(render)
    records={mode:release.read(root/('registration-'+mode+'-v2'),'qualified') for mode in ('baseline','register','verify')}
    for record in records.values():protected(record)
    baseline,registered,verified=(records[m]['proof'] for m in ('baseline','register','verify'))
    require(registered['status']=='private_batch_registered' and verified['status']=='private_batch_verified'
        and registered['batch_id']==verified['batch_id']==baseline['batch_id']
        and registered['manifest_sha256']==verified['batch']['manifest_sha256']
        and verified['batch']['manifest_json']['baseline_content_sha256']==registered['manifest_sha256']
        and verified['validation']['ready_for_owner_approval'] and verified['report_count']==42,
        'Exact private registration and verification required')
    visual=release.read(root/'visual-v1','receipt');audit=release.read(root/'independent-audit-v1','receipt')
    require(visual['status']=='accepted_pixel_identity_passed' and visual['slides_rendered']==420
        and visual['pdf_pages_rendered']==42 and visual['differences']==0 and not visual['errors'], 'Full pixel identity required')
    require(audit['status']=='passed' and audit['months']==21 and audit['financial_charts']==126
        and audit['risk_metrics']==147 and audit['editable_workbooks']==126, 'Independent financial/workbook acceptance required')
    render_sha=release.build.sha(root/'render-qualified.json')
    require(visual['render_qualified_sha256']==audit['render_qualified_sha256']==render_sha, 'QA render identity differs')
    semantic=None
    if folder.name.startswith('alpha-ms-format-worker-candidate-'):
        semantic=release.read(root,'semantic-package-v2')
        require(semantic['status']=='passed' and semantic['months']==21
            and semantic['render_qualified_sha256']==render_sha
            and semantic['accepted_qualified_sha256']==release.build.sha(folder/'runtime-v1/candidate-qualified.json')
            and semantic['checker_sha256']==release.build.sha(release.build.ROOT/'scripts/compare_ms_package_semantics.py')
            and not any(i['semantic_differences'] for i in semantic['items']), 'Resolved package semantics differ')
    render_items={r['period_end']:r for r in render['proof']['artifacts']}
    require(len(render_items)==21 and len(verified['items'])==42
        and {(i['period_end'],i['report_type']) for i in verified['items']}=={(d,k) for d in render_items for k in ('factsheet','presentation')}, 'Exact registered inventory required')
    for item in verified['items']:
        accepted=render_items[item['period_end']];kind=item['report_type']
        require(item['artifact_sha256']==accepted[kind+'_sha256'] and item['snapshot_id']==accepted['snapshot_id'], 'Registered output/input identity differs')
    release_plan=release.read(folder/'deployment-v1','plan');jobs=release.all_jobs(folder/'deployment-v1',release_plan)
    require(all(j['properties']['template']['containers'][0]['image']==render['image'] for j in jobs.values()), 'Installed images drifted')
    for name in release.build.JOBS:release.idle(name)
    receipts={name:retain(value,'ms-installed-private-batch-v1-'+name+'-acceptance') for name,value in [('visual',visual),('independent-audit',audit)]}
    if semantic is not None:receipts['package-semantics']=retain(semantic,'ms-installed-private-batch-v1-package-semantics-acceptance')
    record={'status':'installed_private_historical_batch_registered_and_verified','image':render['image'],
        'batch_id':registered['batch_id'],'superseded_unapproved_batch_id':registered['superseded_private_batch_id'],
        'render_execution':render['execution'],'registration_execution':records['register']['execution'],
        'verification_execution':records['verify']['execution'],'months':21,'factsheets':21,'presentations':21,
        'financial_charts':126,'risk_metric_values':147,'editable_chart_workbooks':126,
        'visually_matched_slides':420,'visually_matched_pdf_pages':42,
        'presentations_semantically_matched':21 if semantic is not None else None,
        'manifest_sha256':registered['manifest_sha256'],'batch_status':'awaiting_owner_approval',
        'ready_for_owner_approval':True,'registered_items_blob_readback_verified':True,
        'prior_report_records_preserved':True,'ibkr_source_changed':False,'source_snapshots_changed':False,
        'approvals_changed':False,'reports_released':False,'onboarding_activated':False,
        'worker_settings_preserved':True,'website_authenticated_downloads_verified':False,
        'native_microsoft_powerpoint_tested':False,'failed_v1_baseline_preserved':True,
        'qa_protected_receipts':receipts,
        'qualification_files_sha256':{'render-qualified.json':render_sha,
            'visual-v1/receipt.json':release.build.sha(root/'visual-v1/receipt.json'),
            'independent-audit-v1/receipt.json':release.build.sha(root/'independent-audit-v1/receipt.json'),
            **{'registration-'+m+'-v2/qualified.json':release.build.sha(root/('registration-'+m+'-v2/qualified.json')) for m in records}},
        'phase_protected_receipts':{m:r['protected_receipt'] for m,r in records.items()},
        'next_gate':'Authenticated website download identity verification; separate owner report approval and onboarding'}
    release.build.save(root,'completion',{**record,'protected_receipt':retain(record,'ms-installed-private-batch-v1-completion')})
    print(json.dumps({k:record[k] for k in ('status','batch_id','factsheets','presentations','reports_released','onboarding_activated','website_authenticated_downloads_verified')}),flush=True)


if __name__=='__main__':main()
