"""Read-only symbol-level comparison of installed operations and T candidate."""
import ast, difflib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CANDIDATE=ROOT/'.work/alpha-t-runtime-release-20261009-v6/worker/local-runtime'
TARGETS={
 'daily':ROOT/'.work/alpha-t-daily-baselines-20261009-v1/daily/local-runtime',
 'benchmark':ROOT/'.work/alpha-t-daily-baselines-20261009-v1/benchmark/local-runtime',
 'analytics':ROOT/'.work/alpha-t-daily-baselines-20261009-v4/analytics/local-runtime',
}
FILES={
 'src/ibkr_etl/risk_snapshots.py':['compute_risk_snapshot','load_risk_inputs','risk_input_fingerprint','snapshot_read_status','store_risk_snapshot','validate_risk_input_readiness'],
 'src/ibkr_etl/derived_portfolio.py':['refresh_fixed_unit_composite','refresh_alpha_world_t_portfolio'],
 'src/ibkr_etl/provisional_benchmark.py':['refresh_and_materialize','_refresh_and_materialize'],
 'src/ibkr_etl/subject_onboarding.py':['approved_reference_definitions'],
 'scripts/run_self_service_derived_refresh.py':['_refresh_analytics_if_approved'],
 'scripts/run_self_service_onboarding_analytics.py':['main'],
}
def symbol(path,name):
 text=path.read_text(encoding='utf-8-sig')
 node=next((n for n in ast.parse(text).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name),None)
 return '' if node is None else ast.get_source_segment(text,node)
if __name__=='__main__':
 import sys
 kind=sys.argv[1]
 for file,names in FILES.items():
  if not (TARGETS[kind]/file).exists():continue
  for name in names:
   old=symbol(TARGETS[kind]/file,name);new=symbol(CANDIDATE/file,name)
   if old!=new:
    print('\n'.join(difflib.unified_diff(old.splitlines(),new.splitlines(),fromfile=kind+'/'+file+':'+name,tofile='T-v6',n=2)))
