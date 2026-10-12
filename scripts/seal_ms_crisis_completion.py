"""Seal the end-to-end report blocker repair, without releasing any files."""
import json
import deploy_ms_crisis as deploy
import build_ms_presentation_worker_candidate as build
from run_specialist_gateway_read_verification import retain

def main():
    deploy.configure();deploy.gates();folder=deploy.FOLDER
    names=['deployment-v1/completion.json','registration-v1/baseline/qualified.json',
        'registration-v1/rehearse/qualified.json','registration-v1/register/qualified.json',
        'registration-v1/verify/qualified.json','browser-downloads-qualified.json']
    records=[]
    for name in names:
        record=json.loads((folder/name).read_bytes());deploy.base.verify_protected(record);records.append(record)
    assert records[0]['status']=='scoped_crisis_deployed'
    assert all(r['status']=='passed' for r in records[1:])
    assert records[2]['transaction_rolled_back']
    ui=json.loads((folder/'owner-ui-verification.json').read_bytes())
    assert ui['status']=='passed' and ui['button_enabled_when_files_selected'] and ui['selections_cleared'] and ui['approvals_submitted']==0
    registered=json.loads((folder/'registration-v1/register/result.json').read_bytes())
    verified=json.loads((folder/'registration-v1/verify/result.json').read_bytes())
    assert registered['database_writes']==85 and verified['report_count']==42
    assert all(p['release_eligible'] and not p['approved_types'] for p in verified['packages'])
    assert all(not verified[k] for k in ('source_data_changed','approvals_changed','reports_released','onboarding_changed'))
    assert records[-1]['website_downloads_verified']==42
    evidence=names+['owner-ui-verification.json','owner-approval-ready.jpg',
        'registration-v1/rehearse/result.json','registration-v1/register/result.json','registration-v1/verify/result.json']
    record={'status':'ms_report_approval_blocker_resolved','months':21,'report_files':42,
        'historical_registration_complete':True,'website_download_verification_complete':True,
        'approval_button_enabled_when_files_selected':True,'test_selections_cleared':True,
        'future_reporting_qualified_producer_installed':True,'financial_metrics_unchanged':True,
        'factsheet_layout_unchanged':True,'crisis_method':'Qualified hypothetical HKD proxy stress replay, not actual MS crisis-period performance',
        'source_data_changed':False,'approvals_changed':False,'reports_released':False,
        'onboarding_changed':False,'old_files_and_records_retained':True,
        'client_distribution':'prohibited','rollback_transaction_verified':True,
        'targets':records[0]['targets'],'evidence_sha256':{n:build.sha(folder/n) for n in evidence}}
    build.save(folder,'end-to-end-completion',{**record,'protected_receipt':retain(record,'ms-crisis-v6-end-to-end-completion')})
    print(json.dumps({k:record[k] for k in ('status','months','report_files','reports_released','approvals_changed')}))

if __name__=='__main__':main()
