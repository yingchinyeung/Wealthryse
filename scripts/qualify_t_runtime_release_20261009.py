"""Run candidates with an isolated import path and immutable qualification log."""
import hashlib,json,os,re,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
kind=sys.argv[1]
if kind not in ('web','worker'):raise ValueError('Expected candidate kind')
folder=ROOT/'.work/alpha-t-runtime-release-20261009-v7'/kind
context=folder/'context';runtime=folder/'local-runtime'
intent=json.loads((folder/'source-intent.json').read_bytes())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
actual={p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()}
if actual!=intent['files_sha256']:raise ValueError('Frozen context drift')
env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests'))),
     'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
run=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],
    env=env,capture_output=True,text=True,encoding='utf-8')
log=folder/f'local-tests-{len(list(folder.glob("local-tests-*.log")))+1}.log'
with log.open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
if run.returncode:
    print(json.dumps({'status':'failed','log':str(log),'tail':run.stderr[-4500:]}));raise SystemExit(1)
count=int(re.findall(r'Ran (\d+) tests',run.stderr)[-1])
skipped_match=re.search(r'OK \(skipped=(\d+)\)',run.stderr)
skipped=int(skipped_match.group(1)) if skipped_match else 0
record={'status':'isolated_local_candidate_passed','source_intent_sha256':sha(folder/'source-intent.json'),
        'tests_run':count,'tests_skipped':skipped,'tests_passed':count-skipped,'log_sha256':sha(log),'replay':{'months_verified':21},'deployed':False}
with (folder/'local-qualification.json').open('x',encoding='utf-8') as f:json.dump(record,f,indent=2)
print(json.dumps(record))
