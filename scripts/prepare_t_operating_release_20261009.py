"""Prepare isolated daily/benchmark candidates; no Azure or database writes.

Use each job's own installed baseline. No reporting image replaces a daily
image. Preserve all non-allowlisted AST and bind every copied byte.
"""
import ast, hashlib, json, shutil
from pathlib import Path
from prepare_t_runtime_release_20261009 import patch_symbols

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-operating-release-20261009-v4'
QUALIFIED=ROOT/'.work/alpha-t-runtime-release-20261009-v6/worker/local-runtime'
BASE=ROOT/'.work/alpha-t-daily-baselines-20261009-v1'
IMAGES={
 'daily':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:06c44ff756bbc7da0f41f14c9bfe08fff2ecb16e06a94c8576654bf176a00038',
 'benchmark':'ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:5e4edb964065bd2e1d802610117b9ce9c2e13f666d87c969ae3e969731bebfef',
}
COMMON={
 'src/ibkr_etl/subject_onboarding.py':('approved_reference_definitions',),
 'src/ibkr_etl/alpha_world_ms_analytics.py':('analyze','simulate','_analyze','_simulate'),
 'src/ibkr_etl/risk_snapshots.py':('compute_risk_snapshot','load_risk_inputs','risk_input_fingerprint','snapshot_read_status','store_risk_snapshot','validate_risk_input_readiness'),
 'src/ibkr_etl/derived_portfolio.py':('refresh_fixed_unit_composite','refresh_alpha_world_t_portfolio'),
 'scripts/run_self_service_derived_refresh.py':('_refresh_analytics_if_approved',),
}
NEW=('alpha_world_t_analytics','alpha_world_t_cache','alpha_world_t_crisis',
     'alpha_world_t_crisis_policy','alpha_world_t_presentation','alpha_world_t_runtime','alpha_world_ms_crisis')
TESTS=('test_alpha_world_t_analytics','test_alpha_world_t_cache','test_alpha_world_t_crisis','test_alpha_world_t_crisis_policy')

def sha(raw):return hashlib.sha256(raw).hexdigest()
def save(path,value):
 with path.open('x',encoding='utf-8') as f:json.dump(value,f,indent=2)

def prepare():
 if FOLDER.exists():raise ValueError('Preserve frozen candidate; use a new revision')
 FOLDER.mkdir()
 for kind,image in IMAGES.items():
  folder=FOLDER/kind;runtime=folder/'local-runtime';context=folder/'context'
  shutil.copytree(BASE/kind/'local-runtime',runtime)
  patch={};original={}
  symbols=dict(COMMON)
  if kind=='benchmark':
   symbols['src/ibkr_etl/provisional_benchmark.py']=('refresh_and_materialize','_refresh_and_materialize')
  for name,allow in symbols.items():
   raw=(runtime/name).read_bytes();original[name]=sha(raw)
   patch[name]=patch_symbols(raw.decode('utf-8'),(QUALIFIED/name).read_text(encoding='utf-8'),allow)
  for module in NEW:
   name=f'src/ibkr_etl/{module}.py'
   if (runtime/name).exists():raise ValueError('Unexpected existing T dependency: '+name)
   patch[name]=(QUALIFIED/name).read_bytes()
  if kind=='benchmark':
   name='src/ibkr_etl/website_benchmarks.py';original[name]=sha((runtime/name).read_bytes())
   patch[name]=(QUALIFIED/name).read_bytes()
  name='src/ibkr_etl/assets/alpha_world_ms_crisis_market_v1.json'
  raw=(ROOT/name).read_bytes()
  if sha(raw)!='422df9135669fe1478f0d18544d1a55926e612859659ea72b151c73a496d81e2':raise ValueError('Crisis source changed')
  patch[name]=raw
  for name,raw in patch.items():
   for parent in (runtime,context):
    target=parent/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
  tests=context/'tests';tests.mkdir()
  for name in TESTS:shutil.copyfile(ROOT/'tests'/f'{name}.py',tests/f'{name}.py')
  shutil.copyfile(ROOT/'tests/test_t_operating_release.py',tests/'test_t_operating_release.py')
  if kind=='benchmark':
   for name in ('test_provisional_benchmark','test_t_benchmark_atomicity'):
    shutil.copyfile(ROOT/'tests'/f'{name}.py',tests/f'{name}.py')
  # Copy the exact two regression fixtures without importing unrelated PDF
  # rendering tests into a calculation-only operating image.
  fixture=(ROOT/'tests/test_monthly_artifacts.py').read_text(encoding='utf-8')
  nodes={n.name:n for n in ast.parse(fixture).body if isinstance(n,ast.FunctionDef)}
  (tests/'test_monthly_artifacts.py').write_text('import copy, math\n\n'+
    '\n\n'.join(ast.get_source_segment(fixture,nodes[n]) for n in ('_snapshot','_ms_snapshot'))+'\n',encoding='utf-8')
  shutil.copytree(ROOT/'.work/alpha-t-reporting-batch-20261009-v2/inputs',tests/'inputs')
  shutil.copyfile(ROOT/'scripts/check_t_operating_candidate.py',tests/'check_t_operating_candidate.py')
  manifest={'kind':kind,'base_image':image,'baseline_files_sha256':original,
    'installed_files_sha256':{n:sha(v) for n,v in patch.items()},'allowed_symbols':symbols,
    'source_data_changed':False,'deployment_authorized':False,
    'scope':'T-only HKD calculation and risk branches on each installed operating baseline'}
  save(context/'ALPHA_T_OPERATING_RELEASE.json',manifest)
  check=lambda hashes:'import hashlib,pathlib; e='+repr(hashes)+"; assert all(hashlib.sha256((pathlib.Path('/app')/n).read_bytes()).hexdigest()==h for n,h in e.items())"
  docker=f'FROM {image}\nENV PYTHONPATH=/app/src:/app/scripts:/app/t-operating-tests PYTHONDONTWRITEBYTECODE=1\n'
  docker+='RUN '+json.dumps(['python','-c',check(original)])+'\n'
  for section in ('src','scripts'):docker+=f'COPY --chown=10001:10001 {section}/ /app/{section}/\n'
  docker+='COPY --chown=10001:10001 tests/ /app/t-operating-tests/\nCOPY --chown=10001:10001 ALPHA_T_OPERATING_RELEASE.json /app/\n'
  docker+='RUN ["python","-m","unittest","discover","-s","/app/t-operating-tests","-v"]\n'
  docker+='RUN ["python","/app/t-operating-tests/check_t_operating_candidate.py"]\n'
  docker+='RUN '+json.dumps(['python','-c',check(manifest['installed_files_sha256'])+"; print('PASS: scoped T operating candidate')"])+ '\n'
  (context/'Dockerfile').write_text(docker,encoding='utf-8')
  save(folder/'source-intent.json',{'tag':f'ibkr-platform:alpha-t-{kind}-20261009-v4','base_image':image,
    'files_sha256':{p.relative_to(context).as_posix():sha(p.read_bytes()) for p in context.rglob('*') if p.is_file()}})
 print(json.dumps({'prepared':['daily','benchmark'],'deployed':False}))

if __name__=='__main__':prepare()
