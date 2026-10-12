"""Pure versioned snapshot correction, bound to the prior immutable inventory."""
from copy import deepcopy
import hashlib,json,uuid
from ibkr_etl.util import canonical_json
from legacy_reporting_reconciliation_20261011 import reconcile_legacy_sector

SUBJECTS=('U16088571','U16088606','hong-kong-china')
NAMESPACE=uuid.UUID('4b865e0b-f1c7-48aa-a9a4-b2da09ec4f68')
def sha(raw):return hashlib.sha256(raw.encode()).hexdigest()

def prepare(row,expected,adapter,normalize,detail_loader,created_at):
    original=deepcopy(row);sid=row['subject_id'];raw=row['snapshot_payload_json']
    if sid not in SUBJECTS or row['status'] not in ('frozen','approved'):raise ValueError('Only approved correction scope may be versioned')
    if str(row['snapshot_id'])!=expected['snapshot_id'] or sid!=expected['subject'] or row['period_end']!=expected['period_end'] or row['status']!=expected['status']:raise ValueError('Historical identity/status changed')
    if not isinstance(raw,str) or sha(raw)!=row['data_sha256'] or row['data_sha256']!=expected['source_sha256']:raise ValueError('Frozen source hash changed')
    if not expected.get('needs_snapshot_correction') or expected.get('blocker') or not expected.get('nav_returns_risk_unchanged'):raise ValueError('Unqualified correction')
    p=json.loads(raw);old=deepcopy(p)
    if p['subject_id']!=sid or p['period_end']!=row['period_end'] or p['reporting_currency']!=row['reporting_currency']:raise ValueError('Payload identity differs')
    if any(p.get(k,{}).get('valuation_method') for k in ('holdings','sector_allocation')):raise ValueError('Do not renormalize already corrected data')
    if sid=='U16088571' and 'classifications' not in p['sector_allocation'] and 'allocation_detail' not in p['sector_allocation']:
        p['sector_allocation']=reconcile_legacy_sector(p,detail_loader(sid,p['data_as_of']))
        provenance=p['sector_allocation']['classification_detail_provenance']
        p['source_citations']['sector_allocation'].append({'table':'instrument_classifications','method':provenance['method'],'reference_sha256':provenance['reference_sha256'],'checked_at':provenance['reference_checked_at']})
    data=normalize(adapter,sid,p['reporting_currency'],{'holdings':{'as_of':p['data_as_of'],'data':p['holdings']},'sector_allocation':{'data':p['sector_allocation']}})
    for key in ('holdings','sector_allocation'):
        p[key]=data[key]['data'];p['source_citations'][key]=[*p['source_citations'][key],*data[key].get('citations',[])]
    if set(p)!=set(old) or any(p[k]!=old[k] for k in old if k not in ('holdings','sector_allocation','source_citations')):raise ValueError('NAV/returns/risk or other unrelated payload changed')
    payload=canonical_json(p);digest=sha(payload)
    if digest!=expected['candidate_sha256'] or digest==row['data_sha256']:raise ValueError('Correction differs from reconciled plan')
    new=deepcopy(row)
    new.update(snapshot_id=uuid.uuid5(NAMESPACE,f"{row['tenant_id']}:{sid}:{row['period_end']}:{digest}"),snapshot_payload_json=payload,data_sha256=digest,
        status='draft',created_at=created_at,approved_at=None,approved_by=None)
    manifest=json.loads(row['source_manifest_json'])
    manifest['sources']=deepcopy(p['source_citations'])
    manifest['reporting_correction']={'method':'dated-reporting-currency-versioned-correction-v1','source_snapshot_id':str(row['snapshot_id']),
        'source_sha256':row['data_sha256'],'candidate_sha256':digest,'nav_returns_risk_unchanged':True,'prior_approvals_transferred':False}
    new['source_manifest_json']=canonical_json(manifest)
    allowed={'snapshot_id','snapshot_payload_json','data_sha256','status','created_at','approved_at','approved_by','source_manifest_json'}
    if any(new[k]!=original[k] for k in original if k not in allowed) or row!=original:raise ValueError('Immutable source or unrelated row field changed')
    return new
