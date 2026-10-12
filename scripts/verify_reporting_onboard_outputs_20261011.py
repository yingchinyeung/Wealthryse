"""Hash-bound private outputs and complete presentation-content comparison."""
import hashlib,json,zipfile
from pathlib import Path
from compare_ms_package_semantics import compare
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_legacy_recovery_probe_20261009 import protected
from control_upgrade_readers_20261010 import save
import control_reporting_onboard_qa_20261011 as q
ROOT=q.ROOT;OUT=q.STAGE/'onboard-output-review-v1';BUCKET='upgrade-g04-reports-20261010'
REFERENCES={'U16088571':'historical-outputs-U16088571-v2','U16088606':'historical-outputs-U16088606-v3','hong-kong-china':'historical-outputs-hong-kong-china-v1','alpha-world-ms':'historical-outputs-alpha-world-ms-v3','alpha-world-t':'historical-outputs-alpha-world-t-v3'}

def sha(data):return hashlib.sha256(data).hexdigest()
def require(ok,message):
    if not ok:raise ValueError(message)

def compare_decks(left,right):
    """Compare every package part with tested semantic relationship resolution."""
    differences=compare(left,right)
    require(not differences,'Presentation content differs: '+repr(differences[:12]))
    with zipfile.ZipFile(left) as a,zipfile.ZipFile(right) as b:
        require(set(a.namelist())==set(b.namelist()),'Presentation package inventory differs')
        require(sum(n.startswith('ppt/slides/slide') and n.endswith('.xml') for n in a.namelist())==20,'Twenty slides required')

def download(bucket,name,digest,limit=64*1024*1024):
    require(name.startswith('candidate-stage34-') or name.startswith('candidate-stage36-onboard-v1/'),'Private QA prefix required')
    blob=bucket.get_blob_client(name);require(blob.get_blob_properties().size<limit,'QA object too large')
    data=blob.download_blob().readall();require(sha(data)==digest,'Hash-bound download differs')
    return data

def main():
    require(not OUT.exists(),'Preserve earlier comparison')
    trial=protected(q.OUT/'result.json');require(trial['passed'],'Passing candidate runtime required');q.accept(trial['proofs'])
    intent=protected(q.OUT/'intent.json')
    prior={s:protected(q.PRIOR/f/'result.json') for s,f in REFERENCES.items()}
    generic=protected(q.PRIOR/'factsheet-history-outputs-v1/result.json')
    require(all(r['passed'] for r in [*prior.values(),generic]),'Passing historical references required')
    save(OUT,'intent',{'image':trial['image'],'runtime_receipt':trial['protected_receipt'],'comparator_sha256':sha((ROOT/'scripts/compare_ms_package_semantics.py').read_bytes()),'scope':'Download exactly 20 candidate QA reports and hash-bound references. Compare PDF bytes and every PPTX package part, canonicalizing core timestamps and relationship IDs with identical resolved properties only. No production reads/writes or report approvals.'})
    artifacts=[]
    with BlobServiceClient('https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net',credential=AzureCliCredential()) as service:
        bucket=service.get_container_client(BUCKET)
        require(bucket.get_container_properties().get('public_access') is None,'Private QA container required')
        proof=generic['proof'];generic_receipt=json.loads(download(bucket,proof['receipt_blob'],proof['receipt_sha256'],2*1024*1024))
        generic_refs={(r['subject'],r['period_end']):r for r in generic_receipt['factsheets']}
        for proof in trial['proofs']:
            subject=proof['subject'];current=json.loads(download(bucket,proof['receipt_blob'],proof['receipt_sha256'],2*1024*1024))
            refproof=prior[subject]['proof'];reference=json.loads(download(bucket,refproof['receipt_blob'],refproof['receipt_sha256'],2*1024*1024))
            require(current['image']==trial['image'] and current['subject']==subject and current['database_read_only'] is True,'Runtime receipt boundary differs')
            refs={r['period_end']:r for r in reference['months']}
            require(len(current['months'])==2 and {r['period_end'] for r in current['months']}=={'2025-01-31','2026-09-30'},'Exact endpoint coverage required')
            for row in current['months']:
                period=row['period_end'];ref=refs[period]
                require(row['subject']==subject and row['snapshot_sha256']==ref['snapshot_sha256']==intent['expected_periods'][subject][period],'Compared inputs differ')
                require(row['financial_status']=='passed' and row['charts_verified']==6,'Financial audit required')
                require(len(row['artifacts'])==2 and {x['report_type'] for x in row['artifacts']}=={'factsheet','presentation'},'Report pair required')
                for artifact in row['artifacts']:
                    kind=artifact['report_type'];ext='pdf' if kind=='factsheet' else 'pptx'
                    container,name=artifact['output_reference'].split('/',1)
                    require(container==BUCKET and name==f'candidate-stage36-onboard-v1/{subject}/{period}/'+('factsheet.pdf' if kind=='factsheet' else 'presentation.pptx'),'Candidate output scope differs')
                    selected=next(r for r in ref['artifacts'] if r['report_type']==kind)
                    if kind=='factsheet' and subject in ('U16088571','U16088606','hong-kong-china'):
                        selected=generic_refs[(subject,period)]
                        require(selected['input_sha256']==row['snapshot_sha256'],'Corrected PDF input differs')
                    refcontainer,refname=selected['output_reference'].split('/',1);require(refcontainer==BUCKET,'Reference outside QA')
                    data=download(bucket,name,artifact['sha256']);refdata=download(bucket,refname,selected['sha256'])
                    path=OUT/f'{subject}-{period}.{ext}';refpath=OUT/f'{subject}-{period}.reference.{ext}'
                    with path.open('xb') as f:f.write(data)
                    with refpath.open('xb') as f:f.write(refdata)
                    if kind=='factsheet':require(data==refdata,'PDF content differs: '+subject+':'+period)
                    else:compare_decks(refpath,path)
                    artifacts.append({'subject':subject,'period_end':period,'report_type':kind,'file':str(path),'sha256':sha(data),'reference_sha256':sha(refdata),'reference_receipt':prior[subject]['protected_receipt'],'content_identical':True})
    require(len(artifacts)==20,'All twenty outputs required')
    save(OUT,'result',{'passed':True,'image':trial['image'],'artifacts':artifacts,'pdfs_byte_identical':10,'presentation_packages_compared':10,'slides_compared':200,'financial_charts_verified':60,'canonicalized_only':['core creation/modification timestamps','relationship identifiers with identical resolved properties'],'new_native_visual_review':'UNVERIFIED','full_historical_native_visual_review':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','fourteen_day_observation':'UNVERIFIED','deployed':False})
    print(json.dumps({'passed':True,'pdfs_identical':10,'complete_presentations_compared':10,'slides':200,'charts':60,'deployed':False}))

if __name__=='__main__':main()
