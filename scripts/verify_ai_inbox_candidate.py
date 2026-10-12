"""Retain candidate build evidence, never approve or promote a website image."""
import argparse
import hashlib
import json
import re
import subprocess

from audit_azure_runtime import az, AZ
from deploy_metadata_monitor import SUB
from prepare_ai_inbox_candidate import FOLDER, TAG
from inspect_agent_inbox_release import live_web
from run_specialist_gateway_read_verification import retain


def test_summary(log):
    summaries=re.findall(r'Ran (\d+) tests in [\d.]+s\s+\r?\n\s*(OK(?: \(skipped=\d+\))?|FAILED[^\r\n]*)',log)
    if len(summaries)!=1:raise ValueError('Exact single regression-test outcome required')
    count,outcome=summaries[0]
    skipped=re.search(r'skipped=(\d+)',outcome)
    return int(count),outcome,int(skipped.group(1)) if skipped else 0


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True)
    p.add_argument('--attempt',type=int,choices=range(1,6),default=1);args=p.parse_args()
    if not re.fullmatch('[a-z0-9]{4,12}',args.run):raise ValueError('Exact ACR execution required')
    if az('account','show','--query','id')!=SUB:raise ValueError('Unexpected subscription')
    folder=FOLDER if args.attempt==1 else FOLDER.with_name(FOLDER.name+'-r'+str(args.attempt))
    packet=json.loads((folder/'intent.json').read_text());intent=packet['intent'];context=folder/'context'
    expected_tag=TAG if args.attempt==1 else TAG+'-r'+str(args.attempt)
    if intent['tag']!=expected_tag or packet.get('protected_receipt',{}).get('protected_readback_verified') is not True:
        raise ValueError('Protected exact candidate intent required')
    names={path.relative_to(context).as_posix() for path in context.rglob('*') if path.is_file()}
    if names!=set(intent['files']):raise ValueError('Build context inventory changed')
    for name,digest in intent['files'].items():
        if hashlib.sha256((context/name).read_bytes()).hexdigest()!=digest:raise ValueError('Build context bytes changed')
    if live_web()!=intent['base']:raise ValueError('Live website changed during candidate build')
    run=az('acr','task','show-run','--registry','ibkrv2stgauhecwc4itaoo','--run-id',args.run)
    if run['runId']!=args.run or run['status'] not in ('Succeeded','Failed'):raise ValueError('Build still running; inspect later')
    result=subprocess.run([AZ,'-m','azure.cli','acr','task','logs','--registry','ibkrv2stgauhecwc4itaoo',
        '--run-id',args.run,'--only-show-errors'],capture_output=True,text=True,timeout=120)
    if result.returncode:raise ValueError('Build log unavailable')
    count,outcome,skipped=test_summary(result.stdout)
    record=dict(status='passed' if run['status']=='Succeeded' else 'failed',run_id=args.run,
        tag=intent['tag'],base=intent['base'],source_manifest_sha256=intent['source_manifest_sha256'],
        local_build_context_integrity_verified=True,test_count=count,test_outcome=outcome,skipped_tests=skipped,
        protected_intent=packet['protected_receipt'],production_deployed=False,notifications_enabled=False,
        source_rows_changed=0,emails_sent=0)
    if run['status']=='Succeeded':
        if not outcome.startswith('OK'):raise ValueError('Successful tests required')
        images=run.get('outputImages',[])
        if len(images)!=1 or images[0].get('registry')!='ibkrv2stgauhecwc4itaoo.azurecr.io' or images[0].get('repository')!='ibkr-platform' or images[0].get('tag')!=intent['tag'].split(':')[1] or not re.fullmatch('sha256:[0-9a-f]{64}',images[0].get('digest','')):
            raise ValueError('Unexpected candidate image')
        record['image']='ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@'+images[0]['digest']
    elif not outcome.startswith('FAILED'):
        raise ValueError('Build failure needs separate investigation')
    acceptance_path=folder/'acceptance.json'
    if acceptance_path.exists():
        previous=json.loads(acceptance_path.read_text())
        comparable={k:v for k,v in previous.items() if k not in ('protected_receipt','supersedes_receipt')}
        if comparable==record:
            print(json.dumps(previous),flush=True);return
        # Preserve previous protected bytes; a corrected receipt supersedes it.
        record['supersedes_receipt']=previous['protected_receipt']
    record['protected_receipt']=retain(record,'inbox-candidate-build-'+record['status'])
    (folder/'acceptance.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
