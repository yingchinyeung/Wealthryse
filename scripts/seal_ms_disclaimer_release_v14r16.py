"""Readback proof and create-only export for the owner-approved MS deployment."""
import json
from pathlib import Path
import shutil
import sys
from zipfile import ZipFile,ZIP_DEFLATED
import release_alpha_ms_disclaimer_v14r16 as c
import build_ms_presentation_worker_candidate as build
from ibkr_etl.alpha_world_ms_copy import validate_copy
from run_specialist_gateway_read_verification import retain

OUTPUT=build.ROOT/'output/pdf/AlphaWorld_MS_Azure_Disclaimer_v14r16'


def downloads():
    observation=c.read(c.FOLDER,'browser-observation')
    registered=c.read(c.FOLDER/'registration-v1/register','result')
    cloud=c.read(c.FOLDER,'cloud-acceptance')
    wanted={r['period_end']:r for r in registered['factsheets']}
    accepted={r['period_end']:r['pdf_sha256'] for r in cloud['checks'] if r['kind']=='worker'}
    assert len(observation['downloads'])==len(wanted)==len(accepted)==21
    assert {r['period_end'] for r in observation['downloads']}==set(wanted)
    assert observation['authentication']=='existing owner session' and observation['approval_changes_submitted'] is False
    assert not OUTPUT.exists() and not OUTPUT.with_suffix('.zip').exists()
    rows=[]
    for row in observation['downloads']:
        period=row['period_end'];path=Path(row['path']);expected=wanted[period]
        assert row['generation_run_id']==expected['generation_run_id']
        assert build.sha(path)==expected['sha256']==accepted[period]
        validate_copy(path)
        rows.append({'period_end':period,'generation_run_id':row['generation_run_id'],'sha256':accepted[period],'source':str(path)})
    OUTPUT.mkdir()
    for row in rows:shutil.copyfile(row['source'],OUTPUT/('AlphaWorld_MS_Factsheet_'+row['period_end']+'.pdf'))
    result={'status':'passed','count':21,'authenticated_website_downloads':True,
        'layout_version':c.LAYOUT,'rows':rows,'approval_changes_submitted':False}
    build.save(OUTPUT,'verification',result)
    with ZipFile(OUTPUT.with_suffix('.zip'),'x',compression=ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):archive.write(path,path.name)
    with ZipFile(OUTPUT.with_suffix('.zip')) as archive:assert archive.testzip() is None and len(archive.namelist())==22
    record={**result,'export_directory':str(OUTPUT),'archive':str(OUTPUT.with_suffix('.zip')),'archive_sha256':build.sha(OUTPUT.with_suffix('.zip'))}
    build.save(c.FOLDER,'downloads-qualified',{**record,'protected_receipt':retain(record,'ms-disclaimer-v14r16-downloads')})
    print(json.dumps({'website_downloads_verified':21,'export':str(OUTPUT)}),flush=True)


def seal():
    c.configure();d=c.configure_deploy();d.gates()
    from deploy_metadata_monitor import request
    import deploy_ms_presentation_worker_release as jobs
    plan=c.read(d.FOLDER,'plan');d.verify_protected(plan)
    completion=c.read(d.FOLDER,'completion');d.verify_protected(completion)
    for name,target in plan['targets'].items():
        actual=request('GET',target['path']+d.API)
        assert actual['properties']['template']['containers'][0]['image']==target['image']
        assert actual['properties']['provisioningState']=='Succeeded'
        assert (d.web_hash(actual) if target['kind']=='web' else jobs.settings_invariant(actual))==target['invariant']
        if target['kind']=='web':
            revision=actual['properties']['latestReadyRevisionName'];assert revision=='ibkrv2stg-web--'+d.REVISION_SUFFIX
            health=request('GET',target['path']+'/revisions/'+revision+d.API)['properties']
            assert health['active'] and health['healthState']=='Healthy'
    assert request('GET',d.WEB+'/authConfigs/current'+d.API)['properties']==plan['auth_before']['properties']
    evidence=['cloud-acceptance.json','deployment-v1/completion.json','browser-observation.json','downloads-qualified.json']
    for mode in ('baseline','register','verify'):
        folder=c.FOLDER/'registration-v1'/mode;proof=c.read(folder,'qualified');d.verify_protected(proof)
        assert proof['status']=='passed' and proof['result_sha256']==build.sha(folder/'result.json')
        evidence += ['registration-v1/'+mode+'/'+n for n in ('qualified.json','result.json')]
    registration=c.read(c.FOLDER/'registration-v1/verify','result')
    assert registration['count']==21 and not any(registration[k] for k in ('approvals_changed','source_data_changed','reports_released','onboarding_changed'))
    future=registration['future_reporting']
    assert future['status']=='passed' and future['layout_version']==c.LAYOUT and future['active_month_end_registrations']==1
    assert future['shared_historical_and_month_end_renderer'] and future['source_quality_and_approval_gates_preserved']
    checked=c.read(c.FOLDER,'downloads-qualified');d.verify_protected(checked)
    assert checked['count']==21 and checked['archive_sha256']==build.sha(Path(checked['archive']))
    record={'status':'ms_disclaimer_v14r16_deployed_and_download_verified','layout_version':c.LAYOUT,
        'historical_factsheets_registered':21,'authenticated_website_downloads_verified':21,'future_reporting':future,
        'targets':completion['targets'],'rollback_runtime_qualified':True,'calculations_changed':False,
        'source_data_changed':False,'alpha_world_t_changed':False,'presentations_changed':False,
        'authentication_and_other_settings_preserved':True,'report_approvals_changed':False,
        'new_factsheets_require_normal_release_approval':True,'old_report_files_and_approval_history_preserved':True,
        'evidence_files_sha256':{n:build.sha(c.FOLDER/n) for n in evidence},'export_archive_sha256':checked['archive_sha256']}
    build.save(c.FOLDER,'final-completion',{**record,'protected_receipt':retain(record,'ms-disclaimer-v14r16-completion')})
    print(json.dumps({'status':record['status'],'months':21,'new_report_approvals_written':0}),flush=True)


if __name__=='__main__':{'downloads':downloads,'seal':seal}[sys.argv[1]]()
