"""Narrow stale August attribution repair; NAV, total return and cash unchanged."""
from collections import defaultdict
from copy import deepcopy
import hashlib,math
from ibkr_etl.util import canonical_json
from audit_monthly_nav_coverage_v2_20261011 import audit
def digest(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
def correct(payload,fresh):
    if payload['subject_id']!='U16088606' or payload['period_end']!='2026-08-31' or payload['reporting_currency']!='USD':raise ValueError('Exact August USD scope required')
    old=payload['monthly_reporting'];m=fresh['data']
    if old.get('available') is not True or fresh.get('quality_status')!='pass' or m.get('available') is not True or m.get('method_version')!='monthly-reporting-attribution-v2-full-nav-coverage' or m.get('fx_conversion',{}).get('missing_currencies')!=[]:raise ValueError('Qualified full-NAV attribution required')
    if not isinstance(old.get('daily_observations'),int) or old['daily_observations']>=m['daily_observations']:raise ValueError('Proven stale interval count required')
    allowed={'daily_observations','equity_contribution','method_version','residual_contribution','sector_contributions','security_contributions','position_coverage'}
    if set(m)-set(old)-{'position_coverage'} or any(m.get(k)!=v for k,v in old.items() if k not in allowed):raise ValueError('Unrelated monthly return, dates, currency, cash or FX changed')
    coverage=m.get('position_coverage') or {}
    if coverage.get('nav_intervals')!=m['daily_observations'] or coverage.get('matched_position_intervals')!=m['daily_observations'] or coverage.get('missing_position_dates')!=[] or coverage.get('residual_only_intervals')!=[]:raise ValueError('Complete dated positions required for this repair')
    candidate=deepcopy(payload);candidate['monthly_reporting']=deepcopy(m);audit(candidate)
    def number(value):
        if isinstance(value,bool):raise ValueError('Boolean contribution')
        n=float(value)
        if not math.isfinite(n):raise ValueError('Nonfinite contribution')
        return n
    securities=m['security_contributions'];keys=[r['conid'] for r in securities]
    if len(keys)!=len(set(keys)) or any(not str(k).strip() for k in keys):raise ValueError('Unique security identities required')
    sectors=defaultdict(float)
    for r in securities:sectors[r['sector']]+=number(r['contribution'])
    rows=m['sector_contributions']
    if len(rows)!=len(sectors) or {r['sector'] for r in rows}!=set(sectors) or any(abs(number(r['contribution'])-sectors[r['sector']])>1e-10 for r in rows) or abs(sum(sectors.values())-number(m['equity_contribution']))>1e-10:raise ValueError('Security and sector effects do not reconcile')
    required={'subject_nav_series','subject_position_series','subject_cash_activity_series','instrument_classifications'}
    if not required<={r.get('table') for r in fresh['citations']}:raise ValueError('Complete source citations required')
    candidate['source_citations']['monthly_reporting']=[*deepcopy(fresh['citations']),{'method':'stale-august-attribution-full-nav-reconciliation-v1',
        'source_payload_sha256':digest(payload),'previous_monthly_sha256':digest(old),'calculation_sha256':digest(fresh),'nav_total_return_and_cash_unchanged':True}]
    return candidate
