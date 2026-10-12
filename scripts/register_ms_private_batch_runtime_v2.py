"""Scoped metadata-only MS registration, injected and frozen by the controller.

Baseline and verification enforce READ ONLY. Registration is one serializable
transaction. No source, calculation, approval, lifecycle or old artifact writes.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from datetime import date
from uuid import UUID

sys.path[:0] = ['/app/src', '/app/scripts']
import psycopg
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContentSettings
from ibkr_etl.db import PostgresConnection, _hybrid_row_factory
from ibkr_etl.util import canonical_json, utc_now_iso
from ibkr_etl.onboarding_reports import (template_evidence, source_period_evidence,
    discover_completed_reporting_periods, build_historical_report_batch_validation)
from ibkr_etl.subject_read_contract import require_ms_hkd_contract
from ibkr_etl.alpha_world_ms_hkd import load_risk_snapshot
from generate_month_end_package import _load_frozen_snapshot
from render_onboarding_historical_reports import _upload_review, _immutable_review_blob, _expected_strategy

TENANT = '00000000-0000-0000-0000-000000000001'
SUBJECT = 'alpha-world-ms'


def require(ok, message):
    if not ok: raise ValueError(message)


def digest(value): return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def rows(c, sql, args=(TENANT, SUBJECT)):
    return [dict(r) for r in c.execute(sql, args)]


def normalize_uuids(value):
    if isinstance(value, UUID): return str(value)
    if isinstance(value, dict): return {k:normalize_uuids(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [normalize_uuids(v) for v in value]
    return value


def state(c):
    result = {
        'subjects': rows(c, 'SELECT * FROM platform_subjects WHERE tenant_id=? AND subject_id=?'),
        'cases': rows(c, 'SELECT * FROM account_onboarding_cases WHERE tenant_id=? AND target_subject_id=? ORDER BY case_id'),
        'approvals': rows(c, 'SELECT * FROM account_onboarding_approvals WHERE tenant_id=? AND subject_id=? ORDER BY approval_id'),
        'templates': rows(c, 'SELECT * FROM subject_report_template_assets WHERE tenant_id=? AND subject_id=? ORDER BY report_type,asset_role,version_label'),
        'batches': rows(c, 'SELECT * FROM onboarding_historical_report_batches WHERE tenant_id=? AND subject_id=? ORDER BY batch_id'),
        'items': rows(c, 'SELECT i.* FROM onboarding_historical_report_items i JOIN onboarding_historical_report_batches b USING(tenant_id,batch_id) WHERE b.tenant_id=? AND b.subject_id=? ORDER BY batch_id,period_end,report_type'),
        'runs': rows(c, 'SELECT g.* FROM report_generation_runs g JOIN reporting_snapshots s USING(tenant_id,snapshot_id) WHERE s.tenant_id=? AND s.subject_id=? ORDER BY generation_run_id'),
        'snapshots': rows(c, 'SELECT snapshot_id,data_sha256,status,quality_status,publication_allowed,reporting_currency,period_end FROM reporting_snapshots WHERE tenant_id=? AND subject_id=? ORDER BY snapshot_id'),
        'releases': rows(c, 'SELECT r.* FROM report_artifact_releases r JOIN report_generation_runs g USING(tenant_id,generation_run_id) JOIN reporting_snapshots s USING(tenant_id,snapshot_id) WHERE s.tenant_id=? AND s.subject_id=? ORDER BY artifact_release_id'),
    }
    # Keep a content fingerprint of large historical manifest/validation JSON.
    # Old DB rows and files remain untouched; this bounds protected evidence.
    for row in result['batches']:
        row['manifest_json'] = {'baseline_content_sha256': digest(row['manifest_json'])}
    for row in result['items']:
        row['validation_json'] = {'baseline_content_sha256': digest(row['validation_json'])}
    # Normalize UUID/date/time exactly as canonical persisted baseline JSON.
    return json.loads(canonical_json(normalize_uuids(result)))


def unchanged(before, after, old_id, batch_id):
    for key in ('subjects', 'cases', 'approvals', 'templates', 'snapshots', 'releases'):
        require(before[key] == after[key], 'Protected prior state changed: ' + key)
    require(before['items'] == [r for r in after['items'] if r['batch_id'] != batch_id], 'Prior batch items changed')
    old_runs = {r['generation_run_id'] for r in before['runs']}
    require(before['runs'] == [r for r in after['runs'] if r['generation_run_id'] in old_runs], 'Prior report generation records changed')
    expected = json.loads(canonical_json(before['batches']))
    for row in expected:
        if row['batch_id'] == old_id: row['status'] = 'superseded'
    require(expected == [r for r in after['batches'] if r['batch_id'] != batch_id], 'Unexpected prior batch mutation')


def eligibility(c, binding, service):
    require_ms_hkd_contract(c, tenant_id=TENANT)
    before = state(c)
    require(len(before['subjects']) == len(before['cases']) == 1, 'Exact MS subject/case required')
    subject = before['subjects'][0]
    require(subject['base_currency'] == subject['reporting_currency'] == 'HKD'
            and subject['lifecycle_status'] == 'onboarding', 'HKD onboarding boundary differs')
    require(before['cases'][0]['status'] not in ('active', 'cancelled'), 'Onboarding case no longer eligible')
    current = [r for r in before['batches'] if r['status'] not in ('failed', 'superseded')]
    require(len(current) <= 1 and all(r['status'] == 'awaiting_owner_approval' for r in current),
            'Only an idle unapproved private batch may be superseded')
    assets = [r for r in before['templates'] if r['is_current']]
    evidence = template_evidence(assets, subject_id=SUBJECT)
    _expected_strategy(c, SUBJECT, next(r['sha256'] for r in assets if r['report_type'] == 'presentation' and r['asset_role'] == 'template'))
    periods = discover_completed_reporting_periods(c, subject_id=SUBJECT,
        reporting_start=subject['history_start_date'], today=date(2026, 10, 8), subject_type='composite_portfolio')
    require(len(periods) == 21 and [r['period_end'] for r in periods] == binding['periods'], 'Completed period coverage differs')
    source = source_period_evidence(subject_id=SUBJECT, reporting_start=subject['history_start_date'], periods=periods)
    proof_data = service.get_blob_client('internal-reports', binding['proof_blob']).download_blob().readall()
    require(hashlib.sha256(proof_data).hexdigest() == binding['proof_sha256'], 'Accepted output proof changed')
    proof = json.loads(proof_data)
    require(proof['status'] == 'passed' and proof['image'] == binding['image']
            and proof['database_writes'] == 0 and proof['read_only_transaction_enforced']
            and not proof['reports_released'] and not proof['onboarding_activated'], 'Read-only renderer proof required')
    require([r['period_end'] for r in proof['artifacts']] == binding['periods'], 'Distinct accepted month list differs')
    for row in proof['artifacts']:
        saved = c.execute('SELECT data_sha256,publication_allowed,reporting_currency,status,quality_status FROM reporting_snapshots WHERE tenant_id=? AND subject_id=? AND snapshot_id=?', (TENANT,SUBJECT,row['snapshot_id'])).fetchone()
        require(saved and saved['data_sha256'] == row['data_sha256'] and not saved['publication_allowed']
                and saved['reporting_currency'] == 'HKD' and saved['status'] in ('frozen', 'approved')
                and saved['quality_status'] != 'fail', 'Accepted snapshot drifted')
        payload, snapshot_sha = _load_frozen_snapshot(c,tenant_id=TENANT,account_id=SUBJECT,snapshot_id=row['snapshot_id'])
        _, current_metrics, _ = load_risk_snapshot(c,row['period_end'],tenant_id=TENANT)
        require(snapshot_sha == row['data_sha256'] and payload['risk']['metrics'] == current_metrics,
                'Accepted calculation inputs no longer match current validated metrics')
    return before, evidence, periods, source, proof


def main():
    b = BATCH_BINDING
    mode = b['mode']; require(mode in ('baseline', 'register', 'verify'), 'Unknown registration phase')
    for name, expected in b['installed_files_sha256'].items():
        require(hashlib.sha256(Path('/app', name).read_bytes()).hexdigest() == expected, 'Installed release source drifted')
    service = BlobServiceClient(os.environ['AZURE_STORAGE_ACCOUNT_URL'],
        credential=DefaultAzureCredential(exclude_interactive_browser_credential=True))
    readonly = mode != 'register'
    options = '-c statement_timeout=120000 -c lock_timeout=5000' + (' -c default_transaction_read_only=on' if readonly else '')
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=_hybrid_row_factory, connect_timeout=25, options=options) as raw:
        raw.execute('SET TRANSACTION ISOLATION LEVEL ' + ('REPEATABLE READ READ ONLY' if readonly else 'SERIALIZABLE'))
        raw.execute("SELECT set_config('app.tenant_id',%s,true)", (TENANT,)); c = PostgresConnection(raw)
        if mode == 'baseline':
            before, evidence, periods, source, proof = eligibility(c, b, service)
            result = {'status': 'baseline_passed', 'state': before, 'state_sha256': digest(before),
                      'template_evidence': evidence, 'source_evidence': source, 'periods': periods,
                      'database_writes': 0, 'batch_id': b['batch_id']}
            raw.rollback()
        else:
            baseline_data = service.get_blob_client('internal-reports', b['baseline_blob']).download_blob().readall()
            require(hashlib.sha256(baseline_data).hexdigest() == b['baseline_sha256'], 'Frozen baseline blob changed')
            baseline = json.loads(baseline_data); before = baseline['state']; batch_id = b['batch_id']
            old = [r['batch_id'] for r in before['batches'] if r['status'] not in ('failed', 'superseded')]
            old_id = old[0] if old else None
            if mode == 'register':
                # Subject-level lock and SERIALIZABLE predicate reads prevent a
                # racing registration from silently selecting another batch.
                raw.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (TENANT + ':' + SUBJECT + ':private-reports',))
                actual, evidence, periods, source, proof = eligibility(c, b, service)
                require(actual == before and digest(actual) == baseline['state_sha256'], 'Registration baseline drifted')
                require(evidence == baseline['template_evidence'] and periods == baseline['periods'] and source == baseline['source_evidence'], 'Template/source evidence drifted')
                artifacts = []
                for row in proof['artifacts']:
                    for kind, ext, mime in [('factsheet', '.pdf', 'application/pdf'), ('presentation', '.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation')]:
                        content = service.get_blob_client('internal-reports', row[kind+'_blob']).download_blob().readall()
                        require(hashlib.sha256(content).hexdigest() == row[kind+'_sha256'], 'Accepted output bytes changed')
                        name = Path(row[kind+'_blob']).name
                        dest = _immutable_review_blob(SUBJECT, row['period_end'], Path(name), row[kind+'_sha256'])
                        _upload_review(service.get_blob_client('internal-reports',dest), content,
                            metadata={'sha256':row[kind+'_sha256'],'snapshot_id':row['snapshot_id']},content_type=mime)
                        if kind == 'factsheet':
                            validation = {'passed':True,'pages':2,'layout_version':evidence[kind]['layout_version'],
                                'design_source':'subject_completed_factsheet','design_model_sha256':evidence[kind]['sha256'],
                                'supporting_template_sha256':evidence[kind]['supporting_template']['sha256'],
                                'render_contract_sha256':evidence[kind]['render_contract_sha256'],
                                'render_contract_version':evidence[kind]['render_contract_version'],'snapshot_sha256':row['data_sha256']}
                        else:
                            v=row['presentation_validation']
                            require(v['output_sha256']==row['presentation_sha256'] and v['snapshot_sha256']==row['data_sha256']
                                and v['ms_chart_workbook_audit']['embedded_workbooks_verified']==6,'Output-bound editable evidence required')
                            validation={'passed':True,'slides':20,'layout_version':v['content_contract_version'],
                                'design_source':'approved_u16088571_platform_layout_with_subject_identity',
                                'design_model_sha256':evidence[kind]['sha256'],'identity_evidence_sha256':evidence[kind]['identity_evidence_sha256'],
                                'snapshot_sha256':row['data_sha256'],'performance_axis':v['performance_axis'],
                                'strategy_name':'iStrategy: Alpha World MS','content_contract_version':v['content_contract_version'],
                                'content_requirements':v['content_requirements'],'output_validation':v}
                        artifacts.append({'period_end':row['period_end'],'report_type':kind,'snapshot_id':row['snapshot_id'],
                            'blob_name':dest,'sha256':row[kind+'_sha256'],'bytes':len(content),'validation':validation})
                if old_id:
                    changed=c.execute("UPDATE onboarding_historical_report_batches SET status='superseded' WHERE tenant_id=? AND subject_id=? AND batch_id=? AND status='awaiting_owner_approval'", (TENANT,SUBJECT,old_id))
                    require(changed.rowcount==1,'Prior private batch changed concurrently')
                c.execute("INSERT INTO onboarding_historical_report_batches(batch_id,tenant_id,case_id,subject_id,reporting_start,reporting_end,expected_month_count,expected_artifact_count,template_evidence_sha256,source_evidence_sha256,status,requested_by_identity_sha256) VALUES (?,?,?,?,?,?,21,42,?,?,'validating',?)",
                    (batch_id,TENANT,before['cases'][0]['case_id'],SUBJECT,before['subjects'][0]['history_start_date'],b['periods'][-1],evidence['evidence_sha256'],source['evidence_sha256'],b['actor_sha256']))
                for a in artifacts:
                    kind=a['report_type']; template_id=SUBJECT+'-completed-factsheet-model' if kind=='factsheet' else 'approved-u16088571-platform-presentation-layout'
                    version='sha256:'+evidence[kind]['sha256']+'+layout:'+a['validation']['layout_version']+'+private-batch:'+batch_id
                    run=c.execute("INSERT INTO report_generation_runs(tenant_id,snapshot_id,report_type,template_id,template_version,status,output_reference,output_sha256,started_at,completed_at) VALUES (?,?,?,?,?,'awaiting_approval',?,?,?,?) RETURNING generation_run_id",
                        (TENANT,a['snapshot_id'],kind,template_id,version,'internal-reports/'+a['blob_name'],a['sha256'],utc_now_iso(),utc_now_iso())).fetchone()[0]
                    a['generation_run_id']=int(run)
                    holdings=next(r['holdings_as_of'] for r in periods if r['period_end']==a['period_end'])
                    template_sha=evidence[kind]['render_contract_sha256'] if kind=='factsheet' else evidence[kind]['sha256']
                    c.execute("INSERT INTO onboarding_historical_report_items(tenant_id,batch_id,period_end,holdings_as_of,report_type,snapshot_id,generation_run_id,status,container_name,blob_name,artifact_sha256,byte_count,template_sha256,validation_json) VALUES (?,?,?,?,?,?,?,'validated','internal-reports',?,?,?,?,?)",
                        (TENANT,batch_id,a['period_end'],holdings,kind,a['snapshot_id'],run,a['blob_name'],a['sha256'],a['bytes'],template_sha,canonical_json(a['validation'])))
                manifest={'schema_version':2,'subject_id':SUBJECT,'batch_id':batch_id,'period_count':21,'artifact_count':42,
                    'artifacts':artifacts,'installed_image':b['image'],'render_execution':b['render_execution'],
                    'template_evidence':evidence,'accepted_visual_sha256':b['visual_sha256'],'baseline_state_sha256':baseline['state_sha256']}
                manifest_json=canonical_json(manifest); manifest_sha=hashlib.sha256(manifest_json.encode()).hexdigest()
                c.execute("UPDATE onboarding_historical_report_batches SET status='awaiting_owner_approval',manifest_json=?,manifest_sha256=?,rendered_artifact_count=42,validated_artifact_count=42,started_at=now(),completed_at=now() WHERE tenant_id=? AND batch_id=?",
                    (manifest_json,manifest_sha,TENANT,batch_id))
                after=state(c); unchanged(before,after,old_id,batch_id)
                result={'status':'private_batch_registered','batch_id':batch_id,'superseded_private_batch_id':old_id,'manifest_sha256':manifest_sha,
                    'report_count':42,'source_data_changed':False,'approvals_changed':False,'onboarding_activated':False,
                    'reports_released':False,'prior_report_records_preserved':True}
                c.execute("INSERT INTO audit_events(tenant_id,occurred_at,actor_type,actor_id,event_type,subject_id,outcome,details_json) VALUES (?,?,'system','owner-authorized-ms-report-registration','onboarding_historical_reports_rendered',?,'awaiting_owner_approval',?)",
                    (TENANT,utc_now_iso(),SUBJECT,canonical_json(result)))
                raw.commit()
            else:
                after=state(c); unchanged(before,after,old_id,batch_id)
                batches=[r for r in after['batches'] if r['batch_id']==batch_id]
                require(len(batches)==1 and batches[0]['status']=='awaiting_owner_approval','Exact private registered batch required')
                items=rows(c,'SELECT * FROM onboarding_historical_report_items WHERE tenant_id=? AND batch_id=? ORDER BY period_end,report_type',(TENANT,batch_id))
                validation=build_historical_report_batch_validation(subject=after['subjects'][0],templates=[r for r in after['templates'] if r['is_current']],
                    periods=baseline['periods'],batch=batches[0],items=items,container='internal-reports')
                require(validation['ready_for_owner_approval'],'Registered batch acceptance gate failed')
                for item in items:
                    content=service.get_blob_client(item['container_name'],item['blob_name']).download_blob().readall()
                    require(hashlib.sha256(content).hexdigest()==item['artifact_sha256'] and len(content)==item['byte_count'],'Registered download readback differs')
                result={'status':'private_batch_verified','batch_id':batch_id,'report_count':len(items),'batch':batches[0],
                    'validation':validation,'items':items,'prior_report_records_preserved':True,'approvals_changed':False,
                    'reports_released':False,'onboarding_activated':False,'database_writes':0}
                raw.rollback()
    content=json.dumps(result,sort_keys=True,indent=2,default=str,allow_nan=False).encode()
    _upload_review(service.get_blob_client('internal-reports',b['result_blob']),content,metadata={'sha256':hashlib.sha256(content).hexdigest()},content_type='application/json')
    print(json.dumps({'status':result['status'],'batch_id':b['batch_id'],'proof_blob':b['result_blob'],'proof_sha256':hashlib.sha256(content).hexdigest()}),flush=True)


if __name__=='__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'status':'stopped','error_class':type(error).__name__,
            'error':str(error) if isinstance(error,ValueError) else 'Service details redacted'}),flush=True)
        raise SystemExit(1)
