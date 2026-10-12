"""One-shot native ACR qualification; never deploy a website or worker."""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess

import build_ms_presentation_worker_candidate as build
import prepare_ms_website_benchmark_candidate_v2 as candidate

JOB='ibkrv2stg-benchmark-daily'


def frozen():
    folder=candidate.FOLDER
    source=json.loads((folder/'source-intent.json').read_bytes())
    candidate.rehearsal.probe.verify_protected(source)
    actual={p.relative_to(folder/'context').as_posix():build.sha(p)
            for p in (folder/'context').rglob('*') if p.is_file()}
    if actual!=source['files_sha256']:raise ValueError('Frozen source changed')
    return folder,source


def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('build','inspect','log'))
    args=parser.parse_args();folder,source=frozen()
    if build.az('account','show','--query','id')!=build.SUB:raise ValueError('Wrong subscription')
    if args.stage=='build':
        if (folder/'build-intent.json').exists():raise ValueError('Inspect native run; do not redispatch')
        audit_path=candidate.rehearsal.STAGE/'independent-metrics.json'
        audit=json.loads(audit_path.read_bytes())
        candidate.rehearsal.probe.verify_protected(audit)
        qualification=json.loads((candidate.rehearsal.STAGE/'qualified.json').read_bytes())
        if (audit['status']!='independent_website_metrics_passed'
                or audit['runtime_proof_sha256']!=qualification['proof_sha256']
                or len(audit['audits'])!=2 or any(row['metrics_verified']!=20 for row in audit['audits'])):
            raise ValueError('Independent two-benchmark metric qualification required')
        job=build.az('containerapp','job','show','--subscription',build.SUB,'-g',build.GROUP,'-n',JOB)
        baseline=job['properties']['template']['containers']
        if len(baseline)!=1 or not re.fullmatch(r'.+@sha256:[0-9a-f]{64}',baseline[0]['image']):
            raise ValueError('Immutable benchmark worker baseline required')
        local={'status':'protected_private_postgres_source_qualified',
            'source_intent_sha256':build.sha(folder/'source-intent.json'),
            'private_postgres_qualification_sha256':source['private_postgres_qualification_sha256'],
            'independent_metrics_sha256':build.sha(audit_path),
            'expected_native_tests':source['expected_test_count'],
            'factsheets_changed':False,'production_promotion':False}
        build.save(folder,'local-qualification',local)
        intent={'tag':source['tag'],'source_intent_sha256':build.sha(folder/'source-intent.json'),
            'local_qualification_sha256':build.sha(folder/'local-qualification.json'),
            'before_jobs':{JOB:{'image':baseline[0]['image'],'settings_invariant_sha256':build.invariant(job)}},
            'prepared_at':datetime.now(timezone.utc).isoformat(),'running_service_update':False}
        intent['controller_sha256']=build.sha(Path(__file__))
        build.save(folder,'build-intent',{**intent,'protected_receipt':candidate.rehearsal.probe.retain(intent,'ms-website-benchmark-build-v2')})
        result=build.az('acr','build','--subscription',build.SUB,'-r',build.REGISTRY,'-t',source['tag'],
            '-f',str(folder/'context/Dockerfile'),'--no-logs','--no-wait',str(folder/'context'))
        if not result or not result.get('runId'):raise ValueError('Unknown native build outcome; do not redispatch')
        build.save(folder,'build-ack',{'run':result['runId'],'tag':source['tag']})
        print(json.dumps({'run':result['runId'],'production_promotion':False}),flush=True);return
    ack=json.loads((folder/'build-ack.json').read_bytes())
    run=build.az('acr','task','show-run','--subscription',build.SUB,'-r',build.REGISTRY,'--run-id',ack['run'])
    if args.stage=='inspect':
        print(json.dumps({'run':ack['run'],'status':run['status']}),flush=True)
        if run['status'] not in ('Succeeded','Failed','Canceled','Error','Timeout'):return
        outputs=run.get('outputImages') or []
        if run['status']=='Succeeded' and (len(outputs)!=1 or outputs[0]['registry']!=build.REGISTRY+'.azurecr.io'
                or outputs[0]['repository']!='ibkr-platform' or outputs[0]['tag']!=source['tag'].split(':',1)[1]
                or not re.fullmatch(r'sha256:[0-9a-f]{64}',outputs[0]['digest'])):
            raise ValueError('Native output identity differs')
        record={'run':ack['run'],'native_status':run['status'],'output_images':outputs,
            'source_intent_sha256':build.sha(folder/'source-intent.json'),'running_services_changed':False}
        name='build-completed' if run['status']=='Succeeded' else 'build-rejected'
        if (folder/(name+'.json')).exists():
            if json.loads((folder/(name+'.json')).read_bytes())!=record:raise ValueError('Native result drifted')
        else:build.save(folder,name,record)
        return
    if run['status']!='Succeeded':raise ValueError('Native build has not passed')
    result=subprocess.run([build.AZ,'-m','azure.cli','acr','task','logs','--subscription',build.SUB,
        '-r',build.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,
        env={**os.environ,'PYTHONUTF8':'1'})
    if result.returncode:raise ValueError('Native log read failed')
    log=result.stdout+result.stderr
    with (folder/'native-build.log').open('x',encoding='utf-8') as f:f.write(log)
    passed=bool(re.search(r'Ran '+str(source['expected_test_count'])+r' tests\b',log)
        and '\nOK' in log and 'PASS: MS website benchmark candidate and unchanged approved report files' in log)
    record={'run':ack['run'],'qualified':passed,'native_status':run['status'],
        'expected_test_count':source['expected_test_count'],'native_log_sha256':build.sha(folder/'native-build.log'),
        'approved_report_files_unchanged':True,'production_promotion':False}
    build.save(folder,'native-build-qualified',record)
    print(json.dumps(record),flush=True)


if __name__=='__main__':main()
