"""Reuse the proven private rollback harness for exactly one qualified month."""
from pathlib import Path
import ast
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/control_monthly_snapshot_enrichment_20261011.py').read_text()
tree=ast.parse(source);node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='MODULES' for t in n.targets))
old='\n'.join(source.splitlines()[node.lineno-1:node.end_lineno])
modules=['prepare_reporting_migration_clone_20261011','audit_monthly_nav_coverage_20261011','audit_monthly_nav_coverage_v2_20261011',
    'reconcile_legacy_monthly_reporting_20261011','test_legacy_monthly_reporting_20261011','reconcile_august_monthly_reporting_20261011',
    'prepare_august_snapshot_correction_20261011','test_august_monthly_reporting_20261011','test_august_snapshot_correction_20261011','test_reporting_migration_clone_20261011']
changes={old:'MODULES='+repr(modules),
    "OUT=base.OUT.parent/'monthly-snapshot-enrichment-v1'":"OUT=base.OUT.parent/'august-snapshot-correction-v1'",
    "FILES=[Path(__file__).name,":"FILES=[Path(__file__).name,'control_monthly_snapshot_enrichment_20261011.py',",
    'from prepare_monthly_snapshot_enrichment_20261011 import prepare as prepare_enrichment':'from prepare_august_snapshot_correction_20261011 import prepare as prepare_enrichment',
    "'tests.testsRun!=41'":"'tests.testsRun!=26'",
    "'len(expected)!=20'":"'len(expected)!=1'",
    "'len(pairs)!=20'":"'len(pairs)!=1'",
    'Expected exactly 20 enriched snapshots':'Expected exactly one corrected snapshot',
    '"\'corrected_snapshots\':20"':'"\'corrected_snapshots\':1"',
    "plan=protected(OUT.parent/'legacy-monthly-plan-v3/result.json');migration=protected(OUT.parent/'snapshot-migration-v2/result.json')":"plan=protected(OUT.parent/'august-monthly-plan-v1/result.json');migration=protected(OUT.parent/'monthly-snapshot-enrichment-v1/result.json')",
    "months=[{**m,'status':'frozen','needs_snapshot_correction':True} for m in plan['proof']['months_detail']]":"months=[{**plan['proof'],'needs_snapshot_correction':True}]",
    'if len(months)!=20:':'if len(months)!=1:',
    'repr(MODULES[3:])':"repr(['test_august_monthly_reporting_20261011','test_august_snapshot_correction_20261011','test_reporting_migration_clone_20261011'])",
    "proof['native_tests']!=41":"proof['native_tests']!=26",
    "proof['corrected_snapshots']!=20":"proof['corrected_snapshots']!=1",
    'Create twenty versioned monthly-analytics snapshots':'Create one versioned August attribution snapshot',
}
for before,after in changes.items():
    if source.count(before)!=1:raise ValueError('Exact one-month harness anchor required: '+before)
    source=source.replace(before,after)
if __name__=='__main__':exec(compile(source,'qualified-private-one-month-rollback','exec'))
