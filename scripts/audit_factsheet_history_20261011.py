"""Independent comparison of generic factsheet table data with full frozen NAV."""
import math


def audit(payload):
    if payload['subject_id'] not in ('U16088571', 'U16088606', 'hong-kong-china'):
        raise ValueError('Generic history audit only')
    series = payload['performance']['series']
    dates = [r['date'] for r in series]
    navs = [float(r['nav']) for r in series]
    if not dates or dates != sorted(set(dates)) or any(not math.isfinite(v) or v <= 0 for v in navs):
        raise ValueError('Unique, finite, positive portfolio history required')
    expected = {}
    for row, value in zip(series, navs):
        expected[row['date'][:7]] = (row['date'], value / navs[0] * 100)
    table = payload['month_end_performance']
    if len(table) != len(expected):
        raise ValueError('Factsheet comparison-based history omits portfolio months')
    mismatches = []
    for row, (day, value) in zip(table, expected.values()):
        if row['date'] != day or not math.isclose(float(row['portfolio']), value, abs_tol=1e-8, rel_tol=0):
            mismatches.append(day)
    if mismatches:
        first_comparison = (payload.get('performance_comparison', {}).get('series') or [{}])[0].get('date')
        raise ValueError('Comparison-based factsheet history differs from full NAV on ' + str(len(mismatches)) +
                         ' month ends; first=' + mismatches[0] + '; portfolio_start=' + dates[0] +
                         '; comparison_start=' + str(first_comparison))
    return {'passed': True, 'nav_intervals': len(series) - 1}
