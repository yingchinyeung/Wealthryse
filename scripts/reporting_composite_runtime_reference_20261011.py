"""Independent composite-sector reconciliation for private consumer qualification."""
import ast
import math
from collections import defaultdict
from decimal import Decimal


def classification_order_only(actual, expected):
    """Check every field exactly, allowing only presentation order to differ."""
    def index(rows):
        result = {str(row['conid']): row for row in rows}
        if len(result) != len(rows):
            raise ValueError('Duplicate classification identity')
        return result
    left, right = index(actual), index(expected)
    if left != right:
        fields = sorted({k for key in set(left) & set(right) for k in set(left[key]) | set(right[key]) if left[key].get(k) != right[key].get(k)})
        raise ValueError('Classification content differs: '+str(fields)+' identities='+str(sorted(set(left)^set(right))))
    return actual != expected


def composite_branch(source):
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_sector_allocation')
    branches = [n for n in function.body if isinstance(n, ast.If) and 'composite_portfolio' in ast.unparse(n.test)]
    if len(branches) != 1:
        raise ValueError('One composite branch required')
    return ast.dump(branches[0], include_attributes=False)


def validate(actual, records):
    if not records or actual.get('classification_basis') != 'composite sleeve type':
        raise ValueError('Composite records/basis unavailable')
    totals = defaultdict(Decimal)
    for row in records:
        value = Decimal(str(row['contribution']))
        if not value.is_finite():
            raise ValueError('Nonfinite contribution')
        totals[str(row['component_type']).replace('_', ' ').title()] += abs(value)
    gross = sum(totals.values())
    if gross <= 0:
        raise ValueError('Positive gross exposure required')
    def same(left, right):
        return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-10)
    allocation = actual['allocation']
    if len(allocation) != len(totals) or {r['sector'] for r in allocation} != set(totals):
        raise ValueError('Missing, duplicate or unexpected sleeves')
    for row in allocation:
        if not same(row['market_value'], totals[row['sector']]) or not same(row['weight'], totals[row['sector']] / gross):
            raise ValueError('Composite sleeve amount/weight differs')
    if not same(actual['gross_market_value'], gross) or not same(actual['classified_market_value'], gross):
        raise ValueError('Composite gross amount differs')
    if actual['holdings'] != len(totals) or actual['classified_holdings'] != len(totals) or actual['classification_coverage_pct'] != 100:
        raise ValueError('Composite coverage differs')
    return {'sleeves': len(totals), 'rows': len(records), 'independent_decimal_reconciliation': True}
