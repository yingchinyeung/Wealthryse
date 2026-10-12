"""Narrow, pure legacy-sector enrichment. Never infer a currency or sector."""
from copy import deepcopy
from decimal import Decimal,InvalidOperation
import hashlib,json

def reconcile_legacy_sector(payload,detailed):
    def number(v):
        try:n=Decimal(str(v))
        except (InvalidOperation,TypeError) as e:raise ValueError('Invalid legacy numeric evidence') from e
        if not n.is_finite():raise ValueError('Nonfinite legacy numeric evidence')
        return n
    def same(a,b):return abs(number(a)-number(b))<=max(Decimal('0.0000001'),abs(number(a))*Decimal('0.000000000001'))
    if payload.get('subject_id')!='U16088571' or payload.get('reporting_currency')!='USD':raise ValueError('Only the reviewed USD legacy account is in scope')
    old=payload['sector_allocation'];holdings=payload['holdings']['holdings']
    if 'classifications' in old or 'allocation_detail' in old or not old.get('available') or not holdings:raise ValueError('Not the reviewed summary-only legacy schema')
    if detailed.get('currency')!='USD' or detailed.get('valuation_method')!='dated-reporting-fx-v1' or not detailed.get('available'):raise ValueError('Validated detailed USD allocation required')
    if any(r.get('currency')!='USD' for r in holdings):raise ValueError('Non-identity FX is prohibited in legacy enrichment')
    dates={r['snapshot_date'] for r in holdings}
    if dates!={detailed.get('position_date')}:raise ValueError('Classification position date differs from frozen holdings')
    values={str(r['conid']):abs(number(r['market_value_text'])) for r in holdings}
    if len(values)!=len(holdings):raise ValueError('Duplicate legacy holdings')
    classified={str(r['conid']):r for r in detailed['classifications']}
    if len(classified)!=len(detailed['classifications']) or set(classified)!=set(values):raise ValueError('Detailed security identities differ')
    if any(not same(values[k],classified[k]['market_value']) for k in values):raise ValueError('Detailed holding values differ')
    gross=sum(values.values(),Decimal(0))
    if gross<=0:raise ValueError('Positive reconciled gross value required')
    from collections import defaultdict
    totals=defaultdict(Decimal);counts=defaultdict(int)
    for conid,r in classified.items():
        key=(r.get('region'),r.get('sector'),r.get('sub_sector'))
        if any(not isinstance(k,str) or not k.strip() for k in key) or not same(r.get('weight'),values[conid]/gross):raise ValueError('Detailed classification evidence incomplete')
        totals[key]+=values[conid];counts[key]+=1
    details=detailed['allocation_detail'];keys=[(r['region'],r['sector'],r['sub_sector']) for r in details]
    if len(set(keys))!=len(keys) or set(keys)!=set(totals):raise ValueError('Detailed allocation identities differ')
    for r,key in zip(details,keys):
        if r['holdings']!=counts[key] or not same(r['market_value'],totals[key]) or not same(r['weight'],totals[key]/gross):raise ValueError('Detailed allocation totals differ')
    for r in detailed['allocation']:
        members=[k for k in totals if k[1]==r['sector']]
        if r['holdings']!=sum(counts[k] for k in members) or not same(r['market_value'],sum(totals[k] for k in members)):raise ValueError('Classifications differ from sector totals')
    for source in (old,detailed):
        if source.get('holdings')!=len(holdings) or source.get('classified_holdings')!=len(holdings):raise ValueError('Legacy coverage counts differ')
        if not same(source.get('gross_market_value'),gross) or not same(source.get('classified_market_value'),gross) or not same(source.get('classification_coverage_pct'),100):raise ValueError('Legacy coverage values differ')
        rows=source['allocation'];sectors=[r['sector'] for r in rows]
        if len(set(sectors))!=len(rows) or not same(sum(number(r['market_value']) for r in rows),gross) or sum(r['holdings'] for r in rows)!=len(holdings):raise ValueError('Sector summary does not reconcile')
    left={r['sector']:r for r in old['allocation']};right={r['sector']:r for r in detailed['allocation']}
    if set(left)!=set(right):raise ValueError('Sector names differ')
    for k in left:
        if left[k]['holdings']!=right[k]['holdings'] or any(not same(left[k][n],right[k][n]) for n in ('market_value','weight')):raise ValueError('Frozen sector summary differs')
    if old.get('provider')!=detailed.get('provider') or old.get('license_status')!=detailed.get('license_status') or old.get('publication_allowed')!=detailed.get('publication_allowed'):raise ValueError('Classification authority or distribution boundary differs')
    result=deepcopy(old)
    result.update(classifications=deepcopy(detailed['classifications']),allocation_detail=deepcopy(detailed['allocation_detail']))
    result['classification_detail_provenance']={'method':'legacy-usd-detail-reconciled-to-frozen-sector-totals-v1',
        'reference_checked_at':detailed.get('classification_checked_at'),'position_date':detailed['position_date'],
        'reference_sha256':hashlib.sha256(json.dumps(detailed,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
        'historical_sector_totals_unchanged':True,'reference_is_independent_historical_provider':False}
    return result
