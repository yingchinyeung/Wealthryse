"""Archive a scoped deployment proposal only; this file cannot deploy or write SQL."""
from datetime import datetime, timezone
from pathlib import Path

import continue_ms_website_release_rollback as continuation
import qualify_ms_website_fresh_release_runtime as fresh
import qualify_ms_benchmark_entrypoint_v2 as entrypoint

r=continuation.original
p=r.p


def main():
    folder=r.FOLDER
    base=p.release.read(folder,'baseline');p.verify_protected(base);r.live_unchanged(base)
    _,image=r.qualified.image_gate()
    records={'fresh_inputs':p.release.read(folder/'fresh-inputs-v1','qualified'),
             'fresh_native':p.release.read(fresh.STAGE,'qualified'),
             'fresh_metrics':p.release.read(fresh.STAGE,'independent-metrics'),
             'metrics_binding':p.release.read(fresh.STAGE,'independent-metrics-binding'),
             'image_rollback':r.read('acceptance'),
             'rollback_projection':r.read('projection-continuation'),
             'entrypoint_projection':p.release.read(folder,'entrypoint-null-default-continuation'),
             'benchmark_entrypoint':p.release.read(entrypoint.STAGE,'qualified')}
    for record in records.values():p.verify_protected(record)
    require=r.require
    require(records['fresh_native']['image']==image
        and records['benchmark_entrypoint']['image']==image
        and records['benchmark_entrypoint']['status']=='native_benchmark_entrypoint_passed'
        and records['image_rollback']['image']==image
        and records['image_rollback']['scratch_resources_removed']
        and records['fresh_metrics']['runtime_proof_sha256']==records['fresh_native']['proof_sha256']
        and records['metrics_binding']['independent_metrics_sha256']==p.sha(fresh.STAGE/'independent-metrics.json')
        and records['rollback_projection']['controller_sha256']==p.sha(Path(continuation.__file__))
        and records['entrypoint_projection']['controller_sha256']==p.sha(Path(entrypoint.__file__)),
        'Qualification evidence does not match candidate')
    inputs=p.release.read(folder/'fresh-inputs-v1','inputs')
    require(p.sha(folder/'fresh-inputs-v1/inputs.json')==records['fresh_inputs']['inputs_sha256'],
        'Fresh inputs changed')
    proof=p.release.read(fresh.STAGE,'proof')
    require(p.sha(fresh.STAGE/'proof.json')==records['fresh_native']['proof_sha256']
        and proof['mapping_selection_rollback_verified']
        and proof['comparison_rows_preserved_on_mapping_rollback']
        and proof['public_fingerprints_before']==proof['public_fingerprints_after'],
        'Temporary database rollback proof differs')
    mappings=inputs['mappings']
    require([(x['benchmark_id'],x['mapping_type'],x['reporting_currency'],x['effective_to']) for x in mappings]
        ==[('GSPC','primary','USD',None),('HSI','secondary','USD',None),
           ('IXIC','secondary','USD',None),('SHCOMP','secondary','USD',None)],
        'Original MS mapping selection differs')
    require(not inputs['external_flows'],'Captured zero-flow comparison qualification differs')
    approved=[b for b in inputs['batch'] if b['status']=='approved']
    require(len(approved)==1 and approved[0]['batch_id']=='a46bea9b-ed50-4942-9dda-bc7e28d05b07'
        and approved[0]['manifest_sha256']=='b64c4a187ebf772da7ae81ed9a9b19b88a418b2c5e97453f8d58516dc2b22e24'
        and approved[0]['expected_artifact_count']==42,'Approved report batch changed')
    images={}
    for kind,key in (('website','website'),('benchmark','benchmark_job')):
        job=base[key]
        images[kind]={'resource_id':job['id'],'from_image':job['properties']['template']['containers'][0]['image'],
                      'to_image':image,'command_unchanged':True,'credentials_and_identity_unchanged':True,
                      'schedule_and_non_image_settings_unchanged':True}
    record={'status':'scoped_cutover_proposal_pending_owner_authorization',
        'created_at':datetime.now(timezone.utc).isoformat(),'controller_sha256':p.sha(Path(__file__)),
        'image_changes':images,'protected_baseline_sha256':p.sha(folder/'baseline.json'),
        'input_sha256':records['fresh_inputs']['inputs_sha256'],
        'native_proof_sha256':records['fresh_native']['proof_sha256'],
        'comparison_rows_qualified':sum(x['observations'] for x in proof['audit']),
        'comparison_version':'benchmark-comparison-v2-ms-hkd',
        'subject_id':'alpha-world-ms','tenant_id':'00000000-0000-0000-0000-000000000001',
        'website_benchmarks':[{'benchmark_id':'GSPC','mapping_type':'primary','reporting_currency':'HKD'},
                              {'benchmark_id':'HSI','mapping_type':'secondary','reporting_currency':'HKD'}],
        'original_mappings_to_preserve':mappings,
        'approved_report_batch_to_preserve':approved[0],
        'qualification_evidence_sha256':{
            'rollback_acceptance':p.sha(r.STAGE/'acceptance.json'),
            'rollback_projection':p.sha(r.STAGE/'projection-continuation.json'),
            'benchmark_entrypoint':p.sha(entrypoint.STAGE/'qualified.json'),
            'entrypoint_projection':p.sha(folder/'entrypoint-null-default-continuation.json'),
            'independent_metrics':p.sha(fresh.STAGE/'independent-metrics.json')},
        'allowed_database_changes':['close only original active MS mapping intervals',
            'insert two dated MS HKD mapping intervals','insert versioned MS HKD comparison history',
            'append truthful service/agent release and rollback audit events'],
        'transaction_requirements':['serializable MS-scoped atomic mapping and comparison transaction',
            'optimistic before-state and source hash checks','both indices mandatory',
            'exact writer must pass a temporary-table rehearsal before any production write'],
        'cutover_order':['refresh read-only drift, idle and security gates',
            'deploy website candidate first; MS comparison fails closed until HKD mappings exist',
            'deploy benchmark candidate without starting normal processing or changing registrations',
            'atomically install MS mapping and versioned comparison history',
            'verify authenticated website comparisons, health and approved report downloads'],
        'rollback_order':['restore exact original MS mapping selection first; preserve v2 rows as history',
            'restore protected benchmark image and then website image',
            'verify authentication, data selection and report downloads'],
        'owner_preview_approval_required':True,'final_activation_separate':True,
        'approved_factsheet_heading_unchanged':'HIBOR + Spread',
        'factsheets_changed':False,'reporting_jobs_changed':False,'ibkr_source_changed':False,
        'onboarding_activated':False,'approvals_written':False,'live_mutations':0,
        'full_live_rollback_exercised':False,'execution_authorized_by_this_plan':False,
        'impact':'Brief website MS-comparison unavailability is possible during coordinated cutover'}
    p.release.build.save(folder,'cutover-proposal',{**record,'protected_receipt':p.retain(record,'ms-website-scoped-cutover-proposal-v1')})
    print('SCOPED_CUTOVER_PROPOSAL_ARCHIVED_NO_LIVE_MUTATIONS',flush=True)


if __name__=='__main__':main()
