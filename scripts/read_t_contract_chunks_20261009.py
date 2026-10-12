"""Recover the read-only probe's split log record and verify its table digest."""
import hashlib
import json
from pathlib import Path
from run_alpha_ms_recovery_operation import request, BASE, az, invariant

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-contract-probe-20261009-v1'
ack=json.loads((FOLDER/'contract-dispatch.json').read_bytes())
intent=json.loads((FOLDER/'contract-intent.json').read_bytes())
path=BASE+'Microsoft.App/jobs/'+ack['job']
run=request('GET',path+'/executions/'+ack['execution']+'?api-version=2024-03-01')
if run['properties']['status']!='Succeeded':raise ValueError('Probe not successfully completed')
w=request('GET',BASE+'Microsoft.OperationalInsights/workspaces/ibkrv2stg-logs?api-version=2023-09-01')
rows=az('monitor','log-analytics','query','-w',w['properties']['customerId'],
    '--analytics-query',"ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+ack['execution']+"' | project Log_s")
prefix='ALPHA_MS_RECOVERY_RESULT '
starts=[r['Log_s'] for r in rows if r['Log_s'].startswith(prefix)]
tails=[r['Log_s'] for r in rows if not r['Log_s'].startswith(prefix)]
if len(starts)!=1 or len(tails)!=1:raise ValueError('Expected the two observed log chunks; do not guess ordering')
record=json.loads(starts[0][len(prefix):]+tails[0])
digest=hashlib.sha256(json.dumps(record['tables'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
if digest!=record['tables_sha256']:raise ValueError('Reassembled evidence digest differs')
unchanged=invariant(request('GET',path+'?api-version=2024-03-01'))==intent['job_invariant']
if not unchanged:raise ValueError('Persistent job settings changed')
result={'status':'Succeeded','job_settings_unchanged':True,'records':[record],**ack,
        'log_reassembly':'two chunks, canonical table SHA-256 verified'}
with (FOLDER/'contract-result.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
print(json.dumps({'status':'verified','tables_sha256':digest,'artifact_counts':record['artifact_counts']}))
