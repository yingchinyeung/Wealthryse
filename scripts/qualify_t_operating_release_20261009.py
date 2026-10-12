"""Immutable local qualification on each job's own import baseline."""
import hashlib,json,os,re,subprocess,sys
from prepare_t_operating_release_20261009 import ROOT,FOLDER,IMAGES

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main(kind):
 if kind not in IMAGES:raise ValueError('Unknown operating kind')
 folder=FOLDER/kind;context=folder/'context';runtime=folder/'local-runtime'
 intent=json.loads((folder/'source-intent.json').read_bytes())
 if {p.relative_to(context).as_posix():sha(p) for p in context.rglob('*') if p.is_file()}!=intent['files_sha256']:raise ValueError('Frozen context drift')
 env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(runtime/'src',runtime/'scripts',context/'tests'))),
  'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','TMP':str(ROOT/'.work/t-test-temp'),'TEMP':str(ROOT/'.work/t-test-temp')}
 if (folder/'local-qualification.json').exists():raise ValueError('Qualification exists; preserve evidence')
 outputs=[];results=[]
 for command in ([sys.executable,'-m','unittest','discover','-s',str(context/'tests'),'-v'],
                 [sys.executable,str(context/'tests/check_t_operating_candidate.py')]):
  run=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8')
  results.append(run);outputs.append(run.stdout+run.stderr)
  if run.returncode:break
 log=folder/f'local-tests-{len(list(folder.glob("local-tests-*.log")))+1}.log'
 with log.open('x',encoding='utf-8') as stream:stream.write('\n'.join(outputs))
 if any(r.returncode for r in results):raise ValueError('Local qualification failed; inspect '+str(log))
 count=int(re.findall(r'Ran (\d+) tests',outputs[0])[-1])
 if 'skipped=' in outputs[0]:raise ValueError('Operating acceptance must not skip tests')
 replay=json.loads(results[1].stdout)
 if replay.get('months_verified')!=21 or replay.get('status')!='passed':raise ValueError('All-month replay failed')
 result={'status':'isolated_local_candidate_passed','source_intent_sha256':sha(folder/'source-intent.json'),
  'tests_passed':count,'tests_skipped':0,'tests_log_sha256':sha(log),'replay':replay,'deployed':False}
 with (folder/'local-qualification.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
 print(json.dumps(result))
if __name__=='__main__':main(sys.argv[1])
