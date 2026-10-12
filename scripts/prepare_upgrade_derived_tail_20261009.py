"""Prepare a private four-file daily-worker candidate from frozen runtimes."""
import json,shutil,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PRIOR=ROOT/'.work/alpha-t-operating-release-20261009-v4/daily'
BENCH=ROOT/'.work/platform-upgrade-20261009-stage7/benchmark/runtime'
OUT=ROOT/'.work/platform-upgrade-20261009-stage11/derived-tail-v1'
FILES=('scripts/run_self_service_derived_refresh.py','src/ibkr_etl/provisional_benchmark.py','src/ibkr_etl/website_benchmarks.py','src/ibkr_etl/derived_benchmark_refresh.py')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    if OUT.exists():raise ValueError('Preserve existing candidate')
    runtime=OUT/'runtime';shutil.copytree(PRIOR/'local-runtime',runtime,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    original=runtime/FILES[0]
    expected=json.loads((PRIOR/'context/ALPHA_T_OPERATING_RELEASE.json').read_bytes())['installed_files_sha256'][FILES[0]]
    if sha(original)!=expected:raise ValueError('Frozen derived baseline differs')
    for name in FILES[1:3]:shutil.copyfile(BENCH/name,runtime/name)
    shutil.copyfile(ROOT/FILES[3],runtime/FILES[3])
    tests=OUT/'tests';shutil.copytree(PRIOR/'context/tests',tests,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('test_provisional_benchmark.py','test_t_benchmark_atomicity.py'):
        shutil.copyfile(ROOT/'.work/platform-upgrade-20261009-stage7/benchmark-release-v1/context/tests'/name,tests/name)
    print('Prepared isolated source tree; entrypoint change, tests and qualification still required')
if __name__=='__main__':main()
