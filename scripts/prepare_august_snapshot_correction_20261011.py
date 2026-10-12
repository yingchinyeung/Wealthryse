"""Version the qualified August attribution repair, retaining old approvals."""
from copy import deepcopy
import json,uuid
from ibkr_etl.util import canonical_json
from reconcile_august_monthly_reporting_20261011 import correct,digest
NAMESPACE=uuid.UUID('4b865e0b-f1c7-48aa-a9a4-b2da09ec4f68')
def prepare(row,plan,fresh,created_at):
    if row['subject_id']!='U16088606' or row['period_end']!='2026-08-31' or row['reporting_currency']!='USD' or row['status'] not in ('frozen','approved'):raise ValueError('Exact active August scope required')
    p=json.loads(row['snapshot_payload_json'])
    import hashlib
    if hashlib.sha256(row['snapshot_payload_json'].encode()).hexdigest()!=row['data_sha256'] or row['data_sha256']!=plan['source_sha256'] or str(row['snapshot_id'])!=plan['source_snapshot_id'] or row['status']!=plan['status']:raise ValueError('Frozen source changed')
    if not plan.get('candidate_verified') or not plan.get('monthly_return_nav_cash_unchanged') or digest(fresh)!=plan['calculation_sha256']:raise ValueError('Exact qualified plan required')
    if any(p[k]!=row[k] for k in ('subject_id','period_end','reporting_currency')):raise ValueError('Payload identity differs')
    candidate=correct(p,fresh);new_hash=digest(candidate)
    if new_hash!=plan['candidate_sha256'] or new_hash==row['data_sha256']:raise ValueError('Candidate differs')
    new=deepcopy(row);new.update(snapshot_id=uuid.uuid5(NAMESPACE,f"{row['tenant_id']}:{row['subject_id']}:{row['period_end']}:{new_hash}"),
        snapshot_payload_json=canonical_json(candidate),data_sha256=new_hash,status='draft',created_at=created_at,approved_at=None,approved_by=None)
    manifest=json.loads(row['source_manifest_json']);manifest['sources']=deepcopy(candidate['source_citations'])
    manifest['august_attribution_correction']={'source_snapshot_id':str(row['snapshot_id']),'source_sha256':row['data_sha256'],
        'candidate_sha256':new_hash,'calculation_sha256':plan['calculation_sha256'],'nav_return_cash_unchanged':True,'prior_approvals_transferred':False}
    new['source_manifest_json']=canonical_json(manifest);return new
