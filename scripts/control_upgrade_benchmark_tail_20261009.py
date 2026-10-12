"""Qualified normal benchmark refresh after derived NAV is materialized."""
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
OUT=ROOT/'benchmark-after-derived-v1';API='?api-version=2024-03-01';JOB='ibkrv2stg-benchmark-daily';PATH=rid('Microsoft.App/jobs',JOB)

def save(name,value):
    with (OUT/(name+'.json')).open('x',encoding='utf-8') as f:json.dump({**value,'protected_receipt':retain(value,'upgrade-benchmark-tail-'+name)},f,indent=2)

def main(stage):
    live=request('GET',PATH+API);plan=protected(ROOT/'live-catchup-v2/plan.json')
    if settings_invariant(live)!=plan['job_invariants']['benchmark'] or live['properties']['template']['containers'][0]['image']!=plan['images']['benchmark']:raise ValueError('Qualified benchmark runtime drift')
    if stage=='start':
        if OUT.exists():raise ValueError('Preserve existing benchmark-tail execution')
        for phase in ('benchmark','derived','risk'):
            if not protected(ROOT/('live-catchup-v2/'+phase+'-result.json'))['passed']:raise ValueError('Completed dependency cycle required')
        post=protected(ROOT/'live-cycle-postflight-v4/result.json')
        result=json.loads(post['records'][0]['Result'])
        if result['protected_table_differences']:raise ValueError('Source preservation failed')
        bad=[r for r in result['consumer_records'] if r['last_benchmark_observation']!=r['expected_benchmark_session']]
        if {r['subject'] for r in bad}!={'alpha-world-ms','alpha-world-t'} or any(r['tool']!='compare_with_benchmarks' for r in bad):raise ValueError('Observed benchmark-only tail gap required')
        runtime.safe_window()
        for name,_,_ in TARGETS.values():runtime.idle(name)
        template=normal_template(live)
        if template!=plan['templates']['benchmark']:raise ValueError('Original normal benchmark template differs')
        OUT.mkdir();save('intent',{'template':template,'job_invariant':settings_invariant(live),'started_at':datetime.now(timezone.utc).isoformat(),'scope':'Normal qualified benchmark worker after current derived NAV; no saved-job, financial-methodology, source-ingestion, report or approval change. Recurrence prevention still needs separate qualification.','observed_gap':bad})
        ack=request('POST',PATH+'/start'+API,data=template);save('dispatch',{'execution':ack['name']});print(json.dumps({'execution':ack['name']}));return
    if stage!='read':raise ValueError('Unknown step')
    intent=protected(OUT/'intent.json');execution=protected(OUT/'dispatch.json')['execution']
    run=request('GET',PATH+'/executions/'+execution+API);status=run['properties']['status']
    actual=run['properties']['template']['containers'][0];wanted=intent['template']['containers'][0]
    if any(actual.get(k)!=wanted.get(k) for k in ('image','command','args')):raise ValueError('Unexpected execution')
    print(json.dumps({'execution':execution,'status':status}))
    if status in ('Succeeded','Failed') and not (OUT/'result.json').exists():save('result',{'passed':status=='Succeeded','execution':execution,'status':status,'saved_job_unchanged':True})

if __name__=='__main__':main(sys.argv[1])
