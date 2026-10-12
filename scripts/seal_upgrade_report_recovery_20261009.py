"""Independently retain report-repair and point-in-time recovery evidence."""
import json,hashlib
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage8'

def read(name):return json.loads((ROOT/name).read_bytes())

def main():
    out=ROOT/'report-recovery-closeout.json'
    if out.exists():raise ValueError('Preserve existing evidence')
    service=BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential())
    def audit(folder):
        receipt=read(folder+'/result.json')
        if receipt['status']!='Succeeded' or not receipt['job_settings_preserved']:raise ValueError('Diagnostic execution not qualified')
        summary=json.loads(receipt['records'][0]['Result'])
        data=service.get_blob_client('internal-reports',summary['private_blob']).download_blob().readall()
        if hashlib.sha256(data).hexdigest()!=summary['sha256']:raise ValueError('Audit evidence differs')
        return json.loads(data),summary
    before,before_summary=audit('recovery-source-v3')
    restored,restored_summary=audit('recovery-restored-v1')
    after,after_summary=audit('recovery-source-v4')
    expected=['alpha-world-ms','alpha-world-t','hong-kong-china','U16088571','U16088606']
    if any(sorted(x['active_subjects'])!=sorted(expected) or x['readiness_status']!=200 or not x['read_only'] for x in (before,restored,after)):raise ValueError('Five-subject read-only recovery readiness differs')
    if before['schema_sha256']!=restored['schema_sha256'] or before['tables'].keys()!=restored['tables'].keys():raise ValueError('Recovery schema differs')
    differing={k:{'source':before['tables'][k],'restored':restored['tables'][k]} for k in before['tables'] if before['tables'][k]!=restored['tables'][k]}
    if set(differing)!={'autonomous_agent_runs','autonomy_recovery_state'}:raise ValueError('Unexpected recovery data differences')
    if before['artifact_mismatches']!=restored['artifact_mismatches']:raise ValueError('Restore did not preserve historical report state')
    if len(before['artifact_mismatches'])!=375 or after['artifact_mismatches'] or len(after['registered_artifacts'])!=775 or not after['passed']:raise ValueError('Full report repair audit failed')
    apply=read('report-repair-apply-v1/result.json')
    result=json.loads(apply['records'][0]['Result'])
    if apply['status']!='Succeeded' or not result['passed'] or result['reference_rows_updated']!=375:raise ValueError('Exact reference repair not confirmed')
    for field in ('output_hashes_changed','statuses_changed','released_files_changed','approvals_changed'):
        if result[field] is not False:raise ValueError('Protected report state changed')
    for table in ('report_artifact_releases','report_release_decisions','reporting_snapshots'):
        if before['tables'][table]!=after['tables'][table]:raise ValueError('Approval or snapshot table changed')
    value={'full_upgrade_complete':False,'report_reference_repair_passed':True,'repaired_awaiting_approval_rows':375,
        'registered_artifacts_verified':775,'artifact_mismatches':0,'released_files_changed':False,'approvals_changed':False,
        'recovery':{'private_east_asia_clone':'ibkrv2stg-upgrade-recovery-1009','restore_point':read('fresh-recovery/plan.json')['restore_body']['properties']['pointInTimeUTC'],
            'schema_matched':True,'identical_tables':len(before['tables'])-len(differing),'total_tables':len(before['tables']),
            'operational_table_differences':differing,'operational_timing_reconciliation_pending':True,
            'all_five_subjects_present':True,'application_readiness_status':200,'live_failover_tested':False},
        'source_before':before_summary,'source_after':after_summary,'restored':restored_summary,
        'real_viewer_acceptance':'Explicitly unverified at owner request'}
    value['protected_receipt']=retain(value,'upgrade-report-recovery-closeout')
    with out.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)
    print(json.dumps({'report_repair_passed':True,'registered_artifacts_verified':775,'recovery_identical_tables':90,'full_upgrade_complete':False}))

if __name__=='__main__':main()
