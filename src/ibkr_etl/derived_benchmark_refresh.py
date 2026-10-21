"""Refresh governed website comparisons after validated composite NAV exists.

This does not change factsheet benchmarks, currencies, formulae or approvals.
Inactive/onboarding subjects retain their separate review workflow.
"""
from datetime import date
from .provisional_benchmark import refresh_and_materialize,last_completed_us_close


def refresh_governed_derived_benchmarks(connection, *, tenant_id, subject_id, as_of):
    if subject_id not in ('alpha-world-ms','alpha-world-t') or not tenant_id:
        raise ValueError('Only explicitly scoped HKD composite subjects are eligible')
    end=date.fromisoformat(as_of)
    start=date(2025,1,20)
    if end<start or end>last_completed_us_close():
        raise ValueError('Validated completed-session NAV date required')
    subject=connection.execute(
        'SELECT lifecycle_status FROM platform_subjects WHERE tenant_id=? AND subject_id=?',
        (tenant_id,subject_id),
    ).fetchone()
    if subject is None or subject.get('lifecycle_status')!='active':
        return {'available':False,'reason':'subject remains in its separate onboarding/review workflow'}
    registration=connection.execute(
        "SELECT 1 FROM subject_operating_registrations WHERE tenant_id=? AND subject_id=? AND capability='benchmark_refresh' AND operating_state='active' AND effective_to IS NULL",
        (tenant_id,subject_id),
    ).fetchone()
    approval=connection.execute(
        "SELECT 1 FROM account_onboarding_approvals WHERE tenant_id=? AND subject_id=? AND approval_type='external_market_data' AND approval_status='approved' AND revoked_at IS NULL AND disclosure_version='yahoo-ticker-disclosure-v1' LIMIT 1",
        (tenant_id,subject_id),
    ).fetchone()
    if registration is None or approval is None:
        raise ValueError('Active benchmark registration and existing exact disclosure approval required')
    # The qualified benchmark module independently checks each contract,
    # currency and primary/secondary mapping and keeps the pair atomic.
    result=refresh_and_materialize(connection,account_id=subject_id,start_date=start,end_date=end)
    # Website-only catalog comparisons (WEBSITE_ALL_BENCHMARKS) are best effort, not the pair.
    comparisons=[item for item in result.get('benchmarks') or [] if item.get('mapping_type')!='comparison']
    if result.get('reporting_currency')!='HKD' or len(comparisons)!=2 or any(not item.get('available') or item.get('quality_status')=='fail' for item in comparisons):
        raise ValueError('Both existing approved HKD comparisons must pass')
    return {'available':True,'refreshed_after_validated_nav':True,'as_of':as_of,'result':result}
