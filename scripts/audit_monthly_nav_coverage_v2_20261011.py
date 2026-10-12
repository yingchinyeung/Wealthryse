"""Respect T's documented endpoint-delta contract; retain strict daily checks elsewhere."""
from copy import deepcopy
from datetime import date
from audit_monthly_nav_coverage_20261011 import audit as daily_audit
def audit(payload):
    if payload['subject_id']!='alpha-world-t':return daily_audit(payload)
    monthly=payload['monthly_reporting'];series=payload['performance']['series']
    if monthly.get('method_version')!='fixed-unit-component-delta-over-prior-month-nav-v1' or payload['reporting_currency']!='HKD':raise ValueError('Exact T endpoint methodology required')
    start=date.fromisoformat(payload['period_end']).replace(day=1).isoformat()
    prior=[r for r in series if r['date']<start];inside=[r for r in series if start<=r['date']<=payload['period_end']];selected=prior[-1:]+inside
    if len(selected)<2 or monthly['period_start']!=selected[0]['date'] or monthly['period_end']!=selected[-1]['date']:raise ValueError('T endpoint coverage differs from frozen NAV')
    if 'daily_observations' in monthly and monthly['daily_observations']!=len(selected)-1:raise ValueError('T interval metadata differs')
    # Audit-only adapter: T stores the opening valuation date, not the first
    # return date, and makes no daily-observation metadata claim. No saved data
    # changes. The shared independent NAV/return/contribution checks still run.
    check=deepcopy(payload);check['monthly_reporting']['period_start']=selected[1]['date'];check['monthly_reporting']['daily_observations']=len(selected)-1
    result=daily_audit(check);result['method']='T endpoint delta, verified against full frozen NAV'
    return result
