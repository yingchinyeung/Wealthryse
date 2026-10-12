"""Frozen scoped qualification; does not build, deploy or update live data."""
import hashlib,json,os,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.work/platform-upgrade-20261009-stage7'
BASE=ROOT/'.work/platform-upgrade-20261009-stage5b'
FILES=('alpha_world_ms_crisis.py','alpha_world_ms_runtime.py','risk_snapshots.py','agent_tools.py')

def manifest(root):
    return {str(p.relative_to(root)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc'}

def main():
    run = sys.argv[1] if len(sys.argv)>1 else 'v1'
    if run not in ('v1','v2'):raise ValueError('Unknown qualification run')
    before,after=manifest(BASE/'runtime'),manifest(OUT/'runtime')
    changed=sorted(k for k in set(before)|set(after) if before.get(k)!=after.get(k))
    allowed=sorted('src/ibkr_etl/'+name for name in (*FILES,'crisis_identity_policy.py'))
    if changed!=allowed:raise ValueError('Unexpected application source changes')
    benchmark_base=ROOT/'.work/alpha-t-operating-release-20261009-v4/benchmark/local-runtime'
    bb,ba=manifest(benchmark_base),manifest(OUT/'benchmark/runtime')
    bc=sorted(k for k in set(bb)|set(ba) if bb.get(k)!=ba.get(k))
    if bc!=['src/ibkr_etl/crisis_identity_policy.py','src/ibkr_etl/crisis_replay.py']:
        raise ValueError('Unexpected benchmark source changes')
    evidence={'application_changes':changed,'benchmark_changes':bc,'application_sha256':after,
              'benchmark_sha256':ba,'live_changes':False,'report_templates_and_renderers_unchanged':True,
              't_policy_and_calculations_unchanged':True,'tests':{}}
    for name,runtime,tests in (('application',OUT/'runtime',OUT/'tests'),('benchmark',OUT/'benchmark/runtime',OUT/'benchmark')):
        env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts'))),
             'PORTAL_TEST_RUNTIME':str(runtime),'RISK_DISPLAY_SOURCE':str(runtime/'src/ibkr_etl/risk_display.py'),
             'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
        log_path=OUT/(name+'-tests.log' if run=='v1' else name+'-tests-'+run+'.log')
        with log_path.open('x',encoding='utf-8') as log:
            result=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(tests),'-v'],
                cwd=runtime,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=300)
        text=log_path.read_text(encoding='utf-8')
        print(name+': '+text[-400:])
        if result.returncode:raise ValueError(name+' regression failed; preserve failed log')
        evidence['tests'][name]={'passed':True,'log_sha256':hashlib.sha256(text.encode()).hexdigest()}
    with (OUT/'local-qualification.json').open('x',encoding='utf-8') as stream:json.dump(evidence,stream,indent=2)

if __name__=='__main__':main()
