"""Rebuild current risk caches after the qualified benchmark/crisis refresh."""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
from control_upgrade_live_catchup_20261009 import protected,normal_template,TARGETS
from deploy_viewer_private_admin_20261008 import request,rid
from deploy_ms_presentation_worker_release import settings_invariant
import deploy_t_runtime_release_20261009 as runtime
from run_specialist_gateway_read_verification import retain
runtime.request=request
ROOT=Path(__file__).resolve().parents[1]/'.work/platform-upgrade-20261009-stage9'
OUT=ROOT/'risk-after-benchmark-v1';PATH=rid('Microsoft.App/jobs','ibkrv2stg-risk-snapshots');API='?api-version=2024-03-01'
def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-risk-tail-'+name)},f,indent=2)
def main(stage):
    live=request('GET',PATH+API);plan=protected(ROOT/'live-catchup-v2/plan.json')
    if settings_invariant(live)!=plan['job_invariants']['risk'] or live['properties']['template']['containers'][0]['image']!=plan['images']['risk']:raise ValueError('Qualified risk worker drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve existing execution')
        if not protected(ROOT/'benchmark-after-derived-v1/result.json')['passed']:raise ValueError('Benchmark dependency required')
        proof=protected(ROOT/'live-cycle-postflight-v5/result.json');result=json.loads(proof['records'][0]['Result'])
        if result['protected_table_differences'] or any(r['last_benchmark_observation']!=r['expected_benchmark_session'] for r in result['consumer_records']):raise ValueError('All current comparisons and source preservation required')
        bad=[r for r in result['consumer_records'] if r['quality']=='fail']
        if {r['subject'] for r in bad}!={'alpha-world-ms','alpha-world-t'} or any(r['tool']!='get_risk_metrics' for r in bad):raise ValueError('Expected invalidated composite caches only')
        runtime.safe_window()
        for name,_,_ in TARGETS.values():runtime.idle(name)
        template=normal_template(live)
        OUT.mkdir();save('intent',{'template':template,'job_invariant':settings_invariant(live),'started_at':datetime.now(timezone.utc).isoformat(),'scope':'Unchanged qualified normal risk worker after benchmark/crisis source refresh; preserve source, reporting and approval inputs.'})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown step')
    intent=protected(OUT/'intent.json');execution=protected(OUT/'dispatch.json')['execution'];run=request('GET',PATH+'/executions/'+execution+API);state=run['properties']['status']
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Actual normal worker differs')
    print(json.dumps({'execution':execution,'status':state}))
    if state in ('Succeeded','Failed') and not (OUT/'result.json').exists():save('result',{'passed':state=='Succeeded','execution':execution,'status':state,'saved_job_preserved':True})
if __name__=='__main__':main(sys.argv[1])
