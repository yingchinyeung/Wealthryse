"""Reuse the SELECT-only clone audit harness for 63 generic factsheet histories."""
from pathlib import Path
root = Path(__file__).resolve().parents[1]
source = (root / 'scripts/control_all_monthly_nav_audit_20261011.py').read_text()
changes = {
    "OUT=base.OUT.parent/'all-monthly-nav-audit-v1'": "OUT=base.OUT.parent/'factsheet-history-audit-v1'\nbase.OUT=base.OUT.parent/'august-snapshot-correction-v1'",
    "FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','audit_monthly_nav_coverage_20261011.py']":
        "FILES=[Path(__file__).name,'prepare_reporting_migration_clone_20261011.py','audit_factsheet_history_20261011.py']",
    'from audit_monthly_nav_coverage_20261011 import audit': 'from audit_factsheet_history_20261011 import audit',
    "AND status IN ('frozen','approved') ORDER BY subject_id,period_end":
        "AND status IN ('frozen','approved') AND subject_id IN ('U16088571','U16088606','hong-kong-china') ORDER BY subject_id,period_end",
    'if len(rows)!=105:': 'if len(rows)!=63:',
    "'months_checked':105": "'months_checked':63",
    "proof['months_checked']!=105": "proof['months_checked']!=63",
    'sum(proof[\'passed_by_subject\'].values())+len(issues)!=105': 'sum(proof[\'passed_by_subject\'].values())+len(issues)!=63',
}
for before, after in changes.items():
    if source.count(before) != 1:
        raise ValueError('Exact history-audit harness anchor required')
    source = source.replace(before, after)
source = source.replace('ALL_MONTHLY_NAV_', 'FACTSHEET_HISTORY_').replace('105 active', '63 generic active').replace('Exactly 105 active', 'Exactly 63 active')
if __name__ == '__main__':
    exec(compile(source, 'read-only-factsheet-history-audit', 'exec'))
