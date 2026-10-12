"""Build and scan-compatible evidence for a scoped immutable website candidate."""
import argparse,json,re,subprocess,os
from pathlib import Path
from datetime import datetime,timezone
import build_ms_presentation_worker_candidate as b
import run_ms_website_input_probe as p
from repair_alpha_ms_website_batch import web_invariant

FOLDER=b.ROOT/'.work/viewer-report-investigation-20261008/candidate-v2'
TAG='ibkr-platform:viewer-report-fixes-20261008-v2'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('stage',choices=('prepare','build','reconcile','inspect','log'));parser.add_argument('--run-id');args=parser.parse_args();stage=args.stage
    assert b.az('account','show','--query','id')==b.SUB
    context=FOLDER/'context'
    files={path.relative_to(context).as_posix():b.sha(path) for path in context.rglob('*') if path.is_file()}
    if stage=='prepare':
        if (FOLDER/'source-intent.json').exists():raise ValueError('Preserve frozen release intent')
        proof_path=FOLDER.parent/'qualification-v5/proof.json'
        proof=json.loads(proof_path.read_bytes())
        if proof['database_writes']!=0 or len(proof['checks'])!=208:raise ValueError('Complete read-only four-viewer qualification required')
        errors=[r for r in proof['checks'] if r.get('error_type') or r.get('status',200)!=200 and not (r.get('status')==409 and r['endpoint']=='export/trades.csv' and r['subject_id'] in ('alpha-world-ms','hong-kong-china'))]
        if errors:raise ValueError('Resolve isolated qualification errors before native build')
        if any(r.get('csv_rows')==0 for r in proof['checks'] if r['endpoint'] in ('export/performance.csv','export/holdings.csv')):raise ValueError('Empty portfolio export remains')
        qualification=p.release.read(FOLDER.parent/'qualification-v5','intent')
        p.verify_protected(qualification)
        import base64,gzip
        frozen=gzip.decompress(base64.b64decode(''.join(qualification['template']['containers'][0]['args'][2:]))).decode()
        for name in ('subject_exports','viewer_labels','metrics_guide','web_theme','agent_tools','ai_analyst','web'):
            source=(context/'src/ibkr_etl'/f'{name}.py').read_text(encoding='utf-8')
            if repr(source) not in frozen:raise ValueError('Candidate module differs from privately qualified source: '+name)
        baseline=p.request('GET',p.release.BASE+'Microsoft.App/containerApps/ibkrv2stg-web'+p.release.API)
        auth=p.request('GET',baseline['id']+'/authConfigs/current'+p.release.API)
        # Store privately, never print settings or credentials.
        baseline_record={'website':baseline,'auth':auth,'image':baseline['properties']['template']['containers'][0]['image']}
        b.save(FOLDER,'baseline',{**baseline_record,'protected_receipt':p.retain(baseline_record,'viewer-web-baseline')})
        source={'tag':TAG,'files_sha256':files,'expected_test_count':86,'private_postgres_proof_sha256':b.sha(proof_path),'database_writes':0,'factsheets_changed':False,'website_only':True}
        b.save(FOLDER,'source-intent',{**source,'protected_receipt':p.retain(source,'viewer-report-candidate-source')})
        print('Frozen scoped candidate and exact rollback baseline');return
    intent=p.release.read(FOLDER,'source-intent');p.verify_protected(intent)
    if files!=intent['files_sha256']:raise ValueError('Frozen candidate bytes differ')
    if stage=='build':
        if (FOLDER/'build-intent.json').exists():raise ValueError('Inspect, never repeat native dispatch')
        before={}
        for name in b.JOBS:
            job=b.az('containerapp','job','show','-g',b.GROUP,'-n',name)
            before[name]={'image':job['properties']['template']['containers'][0]['image'],'settings_invariant_sha256':b.invariant(job)}
        b.save(FOLDER,'build-intent',{'source_intent_sha256':b.sha(FOLDER/'source-intent.json'),'tag':TAG,'before_jobs':before,'prepared_at':datetime.now(timezone.utc).isoformat()})
        ack=b.az('acr','build','--subscription',b.SUB,'-r',b.REGISTRY,'-t',TAG,'-f',str(context/'Dockerfile'),'--no-logs','--no-wait',str(context))
        if not ack or not ack.get('runId'):raise ValueError('Unknown native build; inspect instead of repeating')
        b.save(FOLDER,'build-ack',{'run':ack['runId'],'tag':TAG});print(json.dumps({'run':ack['runId'],'production_changed':False}));return
    if stage=='reconcile':
        if not args.run_id or (FOLDER/'build-ack.json').exists():raise ValueError('Explicit existing run required; preserve prior acknowledgement')
        prepared=p.release.read(FOLDER,'build-intent')
        run=b.az('acr','task','show-run','-r',b.REGISTRY,'--run-id',args.run_id)
        elapsed=(datetime.fromisoformat(run['createTime'].replace('Z','+00:00'))-datetime.fromisoformat(prepared['prepared_at'])).total_seconds()
        outputs=run.get('outputImages') or []
        if not (0<=elapsed<=120 and prepared['source_intent_sha256']==b.sha(FOLDER/'source-intent.json') and run.get('runId')==args.run_id and run['status']=='Succeeded' and len(outputs)==1 and outputs[0]['registry']==b.REGISTRY+'.azurecr.io' and outputs[0]['repository']=='ibkr-platform' and outputs[0]['tag']==TAG.split(':')[1] and re.fullmatch('sha256:[0-9a-f]{64}',outputs[0]['digest'])):raise ValueError('Existing build does not uniquely match frozen intent')
        b.save(FOLDER,'build-ack',{'run':args.run_id,'tag':TAG,'reconciled_after_missing_cli_ack':True,'native_created_at':run['createTime']});print('Reconciled existing native build; not redispatched');return
    ack=p.release.read(FOLDER,'build-ack');run=b.az('acr','task','show-run','-r',b.REGISTRY,'--run-id',ack['run'])
    print(json.dumps({'run':ack['run'],'status':run['status']}))
    if stage=='inspect':
        if run['status']!='Succeeded':return
        outputs=run.get('outputImages') or []
        if len(outputs)!=1 or outputs[0]['tag']!=TAG.split(':')[1] or outputs[0]['repository']!='ibkr-platform' or not re.fullmatch('sha256:[0-9a-f]{64}',outputs[0]['digest']):raise ValueError('Output image identity differs')
        record={'run':ack['run'],'native_status':'Succeeded','output_images':outputs,'source_intent_sha256':b.sha(FOLDER/'source-intent.json')}
        if not (FOLDER/'build-completed.json').exists():b.save(FOLDER,'build-completed',record)
        return
    if run['status']!='Succeeded':raise ValueError('Native build not passed')
    r=subprocess.run([b.AZ,'-m','azure.cli','acr','task','logs','-r',b.REGISTRY,'--run-id',ack['run']],capture_output=True,text=True,encoding='utf-8',timeout=120,env={**os.environ,'PYTHONUTF8':'1'})
    if r.returncode:raise ValueError('Native log unavailable')
    log=r.stdout+r.stderr
    if not (FOLDER/'native-build.log').exists():
        with (FOLDER/'native-build.log').open('x',encoding='utf-8') as stream:stream.write(log)
    passed=bool(re.search(r'Ran 86 tests\b',log) and '\nOK' in log and 'PASS: MS website benchmark candidate and unchanged approved report files' in log)
    record={'run':ack['run'],'qualified':passed,'native_log_sha256':b.sha(FOLDER/'native-build.log'),'tests':86,'report_bytes_unchanged':True}
    if not (FOLDER/'native-build-qualified.json').exists():b.save(FOLDER,'native-build-qualified',record)
    print(json.dumps(record))
if __name__=='__main__':main()
