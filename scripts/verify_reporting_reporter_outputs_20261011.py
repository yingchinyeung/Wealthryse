"""Compare exact month-end image outputs with the accepted onboarding output set."""
import hashlib,json,zipfile
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from compare_ms_package_semantics import compare
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
import control_reporting_reporter_qa_20261011 as q
ROOT=q.ROOT;OUT=q.STAGE/'reporter-output-review-v1';BUCKET='upgrade-g04-reports-20261010'
def sha(data):return hashlib.sha256(data).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)
def get(bucket,name,digest,limit=64*1024*1024):
    require(name.startswith('candidate-stage37-reporter-v1/'),'Exact current private QA scope required')
    blob=bucket.get_blob_client(name);require(blob.get_blob_properties().size<limit,'Oversized QA object')
    data=blob.download_blob().readall();require(sha(data)==digest,'Hash-bound download differs');return data
def main():
    require(not OUT.exists(),'Preserve prior comparison')
    trial=protected(q.OUT/'result.json');require(trial['passed'],'Runtime qualification required');q.accept(trial['proofs'])
    intent=protected(q.OUT/'intent.json');prior=protected(ROOT/'.work/platform-upgrade-20261011-stage36/onboard-output-review-v1/result.json')
    require(prior['passed'] and len(prior['artifacts'])==20,'Complete accepted reference required')
    refs={(r['subject'],r['period_end'],r['report_type']):r for r in prior['artifacts']}
    for r in refs.values():require(sha(Path(r['file']).read_bytes())==r['sha256'],'Reference file changed')
    save(OUT,'intent',{'image':trial['image'],'runtime_receipt':trial['protected_receipt'],'reference_receipt':prior['protected_receipt'],'checker_sha256':sha(Path(__file__).read_bytes()),'comparator_sha256':sha((ROOT/'scripts/compare_ms_package_semantics.py').read_bytes()),'scope':'Twenty private report downloads; identical PDF bytes and complete semantic PowerPoint package comparison. No production change or approval.'})
    artifacts=[]
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        bucket=service.get_container_client(BUCKET);require(bucket.get_container_properties().get('public_access') is None,'Private output required')
        for proof in trial['proofs']:
            subject=proof['subject'];receipt=json.loads(get(bucket,proof['receipt_blob'],proof['receipt_sha256'],2*1024*1024))
            require(receipt['image']==trial['image'] and receipt['subject']==subject and receipt['database_read_only'] is True,'Runtime receipt scope differs')
            require(len(receipt['months'])==2 and {r['period_end'] for r in receipt['months']}=={'2025-01-31','2026-09-30'},'Exact periods required')
            for row in receipt['months']:
                period=row['period_end'];require(row['subject']==subject and row['snapshot_sha256']==intent['expected_periods'][subject][period],'Frozen input differs')
                require(row['financial_status']=='passed' and row['charts_verified']==6,'Financial checks missing')
                require(len(row['artifacts'])==2 and {r['report_type'] for r in row['artifacts']}=={'factsheet','presentation'},'Report pair required')
                for artifact in row['artifacts']:
                    kind=artifact['report_type'];ext='pdf' if kind=='factsheet' else 'pptx';reference=refs[(subject,period,kind)]
                    container,name=artifact['output_reference'].split('/',1)
                    require(container==BUCKET and name==f'candidate-stage37-reporter-v1/{subject}/{period}/'+('factsheet.pdf' if kind=='factsheet' else 'presentation.pptx'),'Unexpected output reference')
                    data=get(bucket,name,artifact['sha256']);path=OUT/f'{subject}-{period}.{ext}'
                    with path.open('xb') as f:f.write(data)
                    if kind=='factsheet':require(sha(data)==reference['sha256'],'Factsheet content changed')
                    else:
                        differences=compare(Path(reference['file']),path);require(not differences,'Presentation content differs: '+repr(differences[:12]))
                        with zipfile.ZipFile(path) as z:require(sum(n.startswith('ppt/slides/slide') and n.endswith('.xml') for n in z.namelist())==20,'Twenty slides required')
                    artifacts.append({'subject':subject,'period_end':period,'report_type':kind,'file':str(path),'sha256':sha(data),'reference_sha256':reference['sha256'],'content_identical':True})
    require(len(artifacts)==20,'Complete comparison required')
    save(OUT,'result',{'passed':True,'image':trial['image'],'artifacts':artifacts,'pdfs_byte_identical':10,'presentation_packages_compared':10,'slides_compared':200,'financial_charts_verified':60,'canonicalized_only':['core creation/modification timestamps','relationship identifiers with identical resolved properties'],'full_historical_native_visual_review':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','deployed':False})
    print(json.dumps({'passed':True,'pdfs_identical':10,'presentations_compared':10,'slides':200,'charts':60,'deployed':False}))
if __name__=='__main__':main()
