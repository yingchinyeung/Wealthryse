"""Prepare, but never apply, a versioned T-only USD-to-HKD correction.

New HKD definition is validated, not silently approved. The old USD approval
and definition remain in history. No calculation, publication or access gate
is promoted by this plan.
"""
import copy
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ibkr_etl.subject_onboarding import approved_reference_definitions, configuration_sha256

ROOT=Path(__file__).resolve().parents[1]
TENANT='00000000-0000-0000-0000-000000000001'
SUBJECT='alpha-world-t'
CASE='e71119a4-882f-4f87-87ed-024f7ea9fe73'
OWNER='a38491c3f0c09db172e0d34c83f0ab3de67011b1e082d2ea6a01e7804c0ffb54'
OLD_HASH='2f584e7e30863e5a1b7dda56560c07bd364e68d73ef7f3e2dae0084343d77037'
NEW_HASH='99b9d84e8118632b59dfd73fa64c08c05af409bb05a3daa34dd2fe78173f0209'
AUTHORIZATION='No—continue with the approved HKD setup'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def prepare(record, *, now):
    before=copy.deepcopy(record['tables'])
    if sha(before)!=record['tables_sha256']:
        raise ValueError('Inspected configuration digest differs')
    if record['artifact_counts']!={'derived_portfolio_nav':0,'risk_analytics_snapshots':0,'reporting_snapshots':0}:
        raise ValueError('Calculated or released T data requires a different migration')
    if before['derived_portfolios']:
        raise ValueError('Unexpected materialized T portfolio')
    for rows in before.values():
        for row in rows:
            if row['tenant_id']!=TENANT:
                raise ValueError('Tenant scope differs')
    subjects=before['platform_subjects'];cases=before['account_onboarding_cases']
    definitions=before['subject_calculation_definitions']
    if len(subjects)!=1 or len(cases)!=1 or len(definitions)!=1:
        raise ValueError('Exact pending case and definition required')
    subject,case,old=subjects[0],cases[0],definitions[0]
    if (subject['subject_id']!=SUBJECT or subject['lifecycle_status']!='onboarding'
        or subject['base_currency']!='USD' or subject['reporting_currency']!='USD'
        or subject['history_start_date']!='2025-01-20' or subject['inception_date']!='2025-01-20'
        or case['case_id']!=CASE or case['target_subject_id']!=SUBJECT
        or case['status']!='in_progress' or case['stage']!='analytics_configuration'
        or case['reporting_currency']!='USD' or case['activated_at'] is not None):
        raise ValueError('Pending USD case changed')
    definition=approved_reference_definitions()[SUBJECT]
    if configuration_sha256(definition)!=NEW_HASH:
        raise ValueError('Qualified HKD definition changed')
    old_expected={**definition,'base_currency':'USD'}
    if (old['configuration_json']!=old_expected or old['configuration_sha256']!=OLD_HASH
        or configuration_sha256(old_expected)!=OLD_HASH or old['approval_status']!='approved'
        or old['effective_to'] is not None or old['approved_by_identity_sha256']!=OWNER):
        raise ValueError('Exact original USD contract required')
    approvals=before['account_onboarding_approvals']
    calc=[a for a in approvals if a['approval_type']=='calculation_contract' and a['approval_status']=='approved']
    ext=[a for a in approvals if a['approval_type']=='external_market_data' and a['approval_status']=='approved']
    if len(approvals)!=2 or len(calc)!=1 or len(ext)!=1:
        raise ValueError('Unexpected approvals require review')
    if calc[0]['approved_by_identity_sha256']!=OWNER or calc[0]['scope_json']['definition_sha256']!=OLD_HASH:
        raise ValueError('Calculation approval binding differs')
    if any(a['subject_id']!=SUBJECT or a['case_id']!=CASE for a in approvals):
        raise ValueError('Approval scope differs')
    mappings=before['portfolio_benchmark_mapping']
    if sorted((m['benchmark_id'],m['mapping_type'],m['reporting_currency'],m['effective_to']) for m in mappings)!=[
        ('GSPC','secondary','USD',None),('HSI','primary','USD',None)]:
        raise ValueError('Recorded benchmark roles differ')
    allowed_pass={'source_account_discovered','connection_validated','external_market_data_approved',
                  'calculation_definition_approved','financial_contract_approved'}
    checks=before['account_onboarding_checks']
    if any(r['case_id']!=CASE or (r['status']!='pending' and r['check_id'] not in allowed_pass) for r in checks):
        raise ValueError('Downstream qualification changed')
    if any(r['feature_state']=='active' for r in before['subject_feature_states']):
        raise ValueError('Active T feature requires a different migration')
    after=copy.deepcopy(before)
    journal={'reason':'Owner-confirmed HKD correction; preserve USD history and require fresh HKD contract approval',
             'authorization':AUTHORIZATION,'original_configuration_sha256':OLD_HASH,
             'new_configuration_sha256':NEW_HASH,'corrected_at':now,
             'prior_inspected_tables_sha256':record['tables_sha256']}
    s=after['platform_subjects'][0]
    s.update(base_currency='HKD',reporting_currency='HKD',updated_at=now)
    s['metadata_json'].update(definition_sha256=NEW_HASH,hkd_correction=journal)
    c=after['account_onboarding_cases'][0]
    c.update(reporting_currency='HKD',stage='financial_contract',updated_at=now)
    c['configuration_json'].pop('calculation_contract',None)
    c['configuration_json'].update(definition_sha256=NEW_HASH,hkd_correction=journal)
    d=after['subject_calculation_definitions'][0]
    d.update(approval_status='superseded',effective_to=d['effective_from'])
    new=copy.deepcopy(old)
    new.update(definition_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'wealthryse:alpha-t-hkd:'+NEW_HASH)),
               definition_version=old['definition_version']+1,configuration_json=definition,
               configuration_sha256=NEW_HASH,approval_status='validated',effective_to=None,
               created_by_identity_sha256=OWNER,created_at=now,
               approved_by_identity_sha256=None,approved_at=None)
    after['subject_calculation_definitions'].append(new)
    for a in after['account_onboarding_approvals']:
        if a['approval_id']==calc[0]['approval_id']:
            a.update(approval_status='revoked',revoked_at=now)
            a['metadata_json'].update(hkd_correction=journal)
    for r in after['account_onboarding_checks']:
        if r['check_id'] in ('calculation_definition_approved','financial_contract_approved'):
            r.update(status='pending',checked_at=now,
                     message='The superseded USD approval is retained; the new HKD contract requires approval.',
                     evidence_json={'hkd_correction':journal,'prior_check_evidence':r['evidence_json']})
    for m in after['portfolio_benchmark_mapping']:
        m['reporting_currency']='HKD'
    return {'schema_version':1,'subject_id':SUBJECT,'tenant_id':TENANT,'case_id':CASE,
            'authorization':AUTHORIZATION,'before':before,'after':after,
            'before_sha256':sha(before),'after_sha256':sha(after),
            'new_definition_id':new['definition_id'],'new_definition_sha256':NEW_HASH,
            'reports_released':False,'viewer_access_changed':False,'new_contract_approved':False,
            'benchmark_roles_changed':False,'factsheet_wording_changed':False}


def main():
    source=ROOT/'.work/alpha-t-contract-probe-20261009-v1/contract-result.json'
    receipt=json.loads(source.read_bytes())
    if receipt['status']!='Succeeded' or not receipt['job_settings_unchanged']:
        raise ValueError('Successful read-only inspection required')
    plan=prepare(receipt['records'][0],now=datetime.now(timezone.utc).isoformat())
    folder=ROOT/'.work/alpha-t-hkd-correction-20261009-v1'
    folder.mkdir(exist_ok=True)
    with (folder/'plan.json').open('x',encoding='utf-8') as f:json.dump(plan,f,indent=2)
    print(json.dumps({'status':'prepared_not_applied','new_definition_sha256':NEW_HASH,
                      'plan_sha256':sha(plan),'new_contract_approved':False}))


if __name__=='__main__':main()
