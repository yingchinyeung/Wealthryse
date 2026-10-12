"""Prepare a new version, never overwrite or approve a legacy snapshot."""
from copy import deepcopy
import hashlib,json,uuid
from ibkr_etl.util import canonical_json
from reconcile_legacy_monthly_reporting_20261011 import enrich
NAMESPACE=uuid.UUID('4b865e0b-f1c7-48aa-a9a4-b2da09ec4f68')
def sha(raw):return hashlib.sha256(raw.encode()).hexdigest()

def prepare(row,plan,fresh,created_at):
    original=deepcopy(row);raw=row['snapshot_payload_json']
    if row['subject_id']!='U16088571' or row['reporting_currency']!='USD' or row['status'] not in ('frozen','approved'):raise ValueError('Exact active legacy USD scope required')
    if str(row['snapshot_id'])!=plan['source_snapshot_id'] or row['period_end']!=plan['period_end'] or plan['subject']!=row['subject_id']:raise ValueError('Source identity changed')
    if sha(raw)!=row['data_sha256'] or row['data_sha256']!=plan['source_sha256']:raise ValueError('Source payload changed')
    if not plan.get('monthly_reconciliation_passed') or not plan.get('frozen_nav_risk_returns_unchanged') or sha(canonical_json(fresh))!=plan['calculation_sha256']:raise ValueError('Exact reconciled calculation required')
    p=json.loads(raw)
    if p['subject_id']!=row['subject_id'] or p['period_end']!=row['period_end'] or p['reporting_currency']!=row['reporting_currency']:raise ValueError('Payload identity differs')
    candidate=enrich(p,fresh);payload=canonical_json(candidate);digest=sha(payload)
    if digest!=plan['candidate_sha256'] or digest==row['data_sha256']:raise ValueError('Qualified candidate differs')
    result=deepcopy(row)
    result.update(snapshot_id=uuid.uuid5(NAMESPACE,f"{row['tenant_id']}:{row['subject_id']}:{row['period_end']}:{digest}"),
        snapshot_payload_json=payload,data_sha256=digest,status='draft',created_at=created_at,approved_at=None,approved_by=None)
    manifest=json.loads(row['source_manifest_json']);manifest['sources']=deepcopy(candidate['source_citations'])
    manifest['monthly_reporting_enrichment']={'method':'frozen-nav-reconciled-full-coverage-v1','source_snapshot_id':str(row['snapshot_id']),
        'source_sha256':row['data_sha256'],'candidate_sha256':digest,'calculation_sha256':plan['calculation_sha256'],'prior_approvals_transferred':False}
    result['source_manifest_json']=canonical_json(manifest)
    if row!=original:raise ValueError('Original snapshot changed')
    return result
