"""Independent frozen-NAV coverage audit, not a replay of attribution code."""
from datetime import date
import math
def audit(payload):
    end=payload['period_end'];start=date.fromisoformat(end).replace(day=1).isoformat()
    currency=payload['reporting_currency'];series=payload['performance']['series'];monthly=payload['monthly_reporting']
    def num(value):
        if isinstance(value,bool):raise ValueError('Boolean financial value')
        try:n=float(value)
        except (TypeError,ValueError) as e:raise ValueError('Missing financial value') from e
        if not math.isfinite(n):raise ValueError('Nonfinite financial value')
        return n
    dates=[r['date'] for r in series]
    if dates!=sorted(set(dates)) or any(d>end for d in dates) or any(r.get('currency')!=currency or num(r['nav'])<=0 for r in series):raise ValueError('Unique positive frozen reporting-currency NAV required')
    prior=[r for r in series if r['date']<start];inside=[r for r in series if start<=r['date']<=end];selected=prior[-1:]+inside
    if len(selected)<2 or not monthly or monthly.get('available') is not True or monthly.get('reporting_currency')!=currency:raise ValueError('Monthly analytics unavailable')
    if monthly.get('daily_observations')!=len(selected)-1 or monthly.get('period_start')!=selected[1]['date'] or monthly.get('period_end')!=selected[-1]['date']:raise ValueError('Monthly interval coverage differs from frozen NAV')
    expected=num(selected[-1]['nav'])/num(selected[0]['nav'])-1
    if abs(num(monthly['portfolio_monthly_return'])-expected)>1e-10:raise ValueError('Monthly return differs from frozen NAV')
    if abs(num(monthly['equity_contribution'])+num(monthly['residual_contribution'])-expected)>1e-10:raise ValueError('Contributions do not reconcile to frozen NAV')
    return {'passed':True,'nav_intervals':len(selected)-1,'start':selected[1]['date'],'end':selected[-1]['date']}
