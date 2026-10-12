"""Download hash-bound private reports and independently repeat frozen-source checks."""
import concurrent.futures,hashlib,json,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/alpha-t-registered-reports-20261009-v2'
RUNTIME=ROOT/'.work/alpha-t-axis-contract-20261009-v3/worker/local-runtime'
sys.path[:0]=[str(RUNTIME/'src'),str(RUNTIME/'scripts')]
from ibkr_etl.alpha_world_t_pdf_validation import validate as validate_pdf
from validate_subject_presentation import validate_subject_presentation

def main():
    registry=json.loads((FOLDER/'registry.json').read_bytes())
    batch=registry['batch']; items=registry['items']; snapshots=registry['snapshots']
    if batch['status']!='awaiting_owner_approval' or batch['validated_artifact_count']!=42 or len(items)!=42 or len(snapshots)!=21: raise ValueError('Completed, private 42-file batch required')
    if batch['approved_at'] or any(i['status']!='validated' for i in items): raise ValueError('Unexpected approval or item state')
    inputs=FOLDER/'inputs'; outputs=FOLDER/'download'
    inputs.mkdir(exist_ok=True); outputs.mkdir(exist_ok=True)
    by_id={str(s['snapshot_id']):s for s in snapshots}
    for row in snapshots:
        raw=row['snapshot_payload_json'].encode()
        if hashlib.sha256(raw).hexdigest()!=row['data_sha256']: raise ValueError('Snapshot hash mismatch')
        target=inputs/(row['period_end']+'.json')
        if target.exists() and target.read_bytes()!=raw: raise ValueError('Local snapshot conflict')
        if not target.exists(): target.write_bytes(raw)
    def download(item):
        kind='Factsheet' if item['report_type']=='factsheet' else 'Presentation'
        suffix='.pdf' if kind=='Factsheet' else '.pptx'
        target=outputs/f"AlphaWorld_T_{kind}_{item['period_end']}{suffix}"
        if not target.exists():
            run=subprocess.run([r'C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe','-m','azure.cli','storage','blob','download','--account-name','ibkrv2stgauhecwc4itaoo','--container-name',item['container_name'],'--name',item['blob_name'],'--file',str(target),'--auth-mode','login','--no-progress','-o','none','--only-show-errors'],capture_output=True,timeout=120)
            if run.returncode: raise ValueError('Private artifact download failed: '+item['period_end']+' '+kind)
        raw=target.read_bytes()
        if len(raw)!=item['byte_count'] or hashlib.sha256(raw).hexdigest()!=item['artifact_sha256']: raise ValueError('Registered artifact integrity failed')
        return item,target
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded=list(pool.map(download,items))
    results=[]
    for item,target in downloaded:
        source=by_id[str(item['snapshot_id'])]
        snapshot=inputs/(source['period_end']+'.json')
        if item['report_type']=='factsheet':
            validation=validate_pdf(target,json.loads(snapshot.read_bytes()),template_path=ROOT/'templates/alpha-world-t/owner_template_20261008.pdf')
        else:
            validation=validate_subject_presentation(target,snapshot,subject_id='alpha-world-t',period_end=item['period_end'],expected_strategy='iStrategy: Alpha World T')
        results.append({'period_end':item['period_end'],'type':item['report_type'],'sha256':item['artifact_sha256'],'validation':validation})
        print('Validated '+item['period_end']+' '+item['report_type'],flush=True)
    result={'status':'passed','batch_id':batch['batch_id'],'manifest_sha256':batch['manifest_sha256'],'downloaded_hash_verified':42,'factsheets':21,'presentations':21,'visual_review_complete':False,'report_approvals_changed':False,'results':results}
    with (FOLDER/'local-validation.json').open('x',encoding='utf-8') as f: json.dump(result,f,indent=2,default=str)
    print(json.dumps({k:v for k,v in result.items() if k!='results'}))

if __name__=='__main__': main()
