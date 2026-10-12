"""Preserve the exact failed build and log before the source-only retry."""
import json,os,subprocess
from datetime import datetime
import release_reporting_corrections_20261011 as c
from control_upgrade_readers_20261010 import save

def main():
    folder=c.OUT.parent/'reporting-image-v2';intent=c.r.read(folder/'build-intent.json')
    run=c.r.build.az('acr','task','show-run','-r',c.r.build.REGISTRY,'--run-id','ckcr')
    elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(intent['prepared_at'])).total_seconds()
    if run['status']!='Failed' or not 0<=elapsed<=120:raise ValueError('Unexpected prior run')
    response=subprocess.run([c.r.build.AZ,'-m','azure.cli','acr','task','logs','-r',c.r.build.REGISTRY,'--run-id','ckcr'],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    log=response.stdout+response.stderr
    if 'Ran 72 tests' not in log or '\nOK\n' not in log or 'result.testsRun!=82' not in log or 'ValueError: Native reporting regressions incomplete' not in log:raise ValueError('Unexpected failure cause')
    with (folder/'failed-native.log').open('x',encoding='utf-8') as f:f.write(log)
    save(folder,'failure',{'run':'ckcr','status':'Failed','log_sha256':c.r.sha(folder/'failed-native.log'),'source_intent_sha256':c.r.sha(folder/'source-intent.json'),
        'cause':'Test count assertion expected 82; actual full selected suite is 72, all passed. Fix harness count only; source candidates unchanged.','live_changed':False})
    print(json.dumps({'failure_preserved':True,'retry_safe':True}))
if __name__=='__main__':main()
