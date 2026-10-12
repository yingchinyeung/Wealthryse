"""Bind all-month fallback and isolated recovery evidence; no live promotion."""
import argparse
import calendar
import json
from pathlib import Path

import build_ms_presentation_worker_candidate as build
from qualify_ms_presentation_worker_runtime import candidate_gate
from rehearse_ms_worker_image_rollback import restore_protected
from run_specialist_gateway_read_verification import retain


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate(folder, image):
    runtime = folder / 'runtime-v1'
    candidate = json.loads((runtime / 'candidate-qualified.json').read_bytes())
    accepted = json.loads((runtime / 'candidate-render-acceptance.json').read_bytes())
    fallback = json.loads((runtime / 'rollback-qualified.json').read_bytes())
    recovery = json.loads((folder / 'rollback-rehearsal-v1/acceptance.json').read_bytes())
    require(accepted['status'] == 'isolated_cloud_render_and_output_acceptance_passed'
            and accepted['image'] == image and accepted['total_slides_visually_covered'] == 420,
            'Accepted exact-image all-output coverage required')
    require(accepted['candidate_qualified_sha256']==build.sha(runtime/'candidate-qualified.json')
            and accepted['persistent_reporting_jobs_unchanged'] is True
            and accepted['database_writes']==0 and accepted['ibkr_source_data_changed'] is False
            and accepted['reports_released'] is False and accepted['onboarding_activated'] is False
            and accepted['website_batch_registered'] is False, 'Candidate evidence identity or non-release scope differs')
    require(fallback['status'] == 'passed' and fallback['mode'] == 'rollback'
            and fallback['proof']['count'] == 21 and fallback['artifact_readback_verified']
            and fallback['persistent_jobs_unchanged'] and fallback['proof']['database_writes'] == 0
            and fallback['proof']['read_only_transaction_enforced'], 'Read-only all-month fallback required')
    require(fallback['production_promotion'] is False
            and fallback['proof']['status']=='passed' and fallback['proof']['image']==fallback['image']
            and fallback['proof']['owner_identity_verified'] is True
            and fallback['proof']['source_data_changed'] is False
            and fallback['proof']['reports_released'] is False
            and fallback['proof']['onboarding_activated'] is False, 'Fallback non-release/source scope differs')
    rows = {r['period_end']:r for r in fallback['proof']['artifacts']}
    expected = {r['period_end']:r for r in candidate['proof']['artifacts']}
    periods={f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}'
        for year,month in [(2025,m) for m in range(1,13)]+[(2026,m) for m in range(1,10)]}
    require(len(fallback['proof']['artifacts'])==len(candidate['proof']['artifacts'])==21
            and len(rows) == len(expected) == 21 and set(rows) == set(expected) == periods,
            'Fallback period coverage differs')
    for date,row in rows.items():
        path = runtime / 'rollback-outputs' / f'AlphaWorld_MS_Factsheet_{date}.pdf'
        require(build.sha(path) == row['factsheet_sha256'] == expected[date]['factsheet_sha256'],
                'Restored fallback factsheet differs from accepted bytes')
    require(recovery['status'] == 'isolated_image_switch_and_exact_archive_restore_passed'
            and recovery['candidate_image'] == image and recovery['base_image'] == fallback['image']
            and recovery['live_reporting_jobs_unchanged'] and recovery['scratch_resource_removed']
            and recovery['protected_definition_restored']
            and recovery['both_live_worker_rollback_definitions_recovered']
            and recovery['database_connections'] == 0 and recovery['database_writes']==0
            and recovery['ibkr_source_changed'] is False and recovery['reports_released'] is False
            and recovery['full_live_image_switch_rollback_exercised'] is False
            and recovery['production_promotion'] is False,
            'Complete bounded image-switch/archive recovery required')
    for record in (accepted,fallback,recovery):
        require(record['protected_receipt']['protected_readback_verified'] is True,
                'Protected qualification receipts required')
    return {'candidate_output_acceptance':accepted,'installed_factsheet_fallback':fallback,
            'isolated_image_switch_recovery':recovery}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--folder', type=Path, required=True)
    folder=parser.parse_args().folder.resolve()
    _,image=candidate_gate(folder)
    evidence=validate(folder,image)
    # Re-read every receipt from its exact protected version, not just local flags.
    restored=[]
    for name,record in evidence.items():
        recovered,elapsed=restore_protected(record['protected_receipt'])
        require(recovered == {k:v for k,v in record.items() if k!='protected_receipt'}, 'Protected evidence identity differs')
        restored.append({'kind':name,'sha256':record['protected_receipt']['sha256'],
                         'version_id':record['protected_receipt']['version_id'],'readback_seconds':elapsed})
    runtime=folder/'runtime-v1'
    record={'status':'reporting_fallback_and_isolated_recovery_qualified','image':image,
        'fallback_image':evidence['installed_factsheet_fallback']['image'],
        'fallback_execution':evidence['installed_factsheet_fallback']['execution'],
        'fallback_factsheets_byte_identical':21,'candidate_months_accepted':21,
        'protected_receipts_recovered':restored,
        'candidate_acceptance_sha256':build.sha(runtime/'candidate-render-acceptance.json'),
        'fallback_qualification_sha256':build.sha(runtime/'rollback-qualified.json'),
        'isolated_recovery_sha256':build.sha(folder/'rollback-rehearsal-v1/acceptance.json'),
        'live_reporting_jobs_unchanged':True,'temporary_job_removed':True,
        'database_writes':0,'ibkr_source_data_changed':False,'reports_released':False,
        'onboarding_activated':False,'website_downloads_changed':False,'production_promotion':False,
        'full_live_image_switch_rollback_exercised':False,
        'scope':'Installed-image render fallback plus isolated ARM image switch and exact protected configuration recovery; not full database/application disaster recovery',
        'next_gate':'Owner-authorized scoped worker promotion with active-execution/drift checks and image-only rollback safeguards'}
    build.save(runtime,'recovery-acceptance',{**record,'protected_receipt':retain(record,folder.name+'-reporting-recovery-acceptance')})
    print(json.dumps({'status':record['status'],'fallback_factsheets_byte_identical':21,
                      'live_reporting_jobs_unchanged':True,'temporary_job_removed':True,'production_promotion':False}))


if __name__=='__main__':
    main()
