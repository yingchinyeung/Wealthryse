"""Collect hash-bound historical QA files, never publish or inherit approvals."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from azure.identity import AzureCliCredential
from azure.storage.blob import BlobServiceClient
from control_upgrade_readers_20261010 import save
from control_upgrade_legacy_recovery_probe_20261009 import protected

ROOT=Path(__file__).resolve().parents[1]
STAGE=ROOT/'.work/platform-upgrade-20261011-stage34'
OUT=ROOT/'.work/platform-upgrade-20261011-stage40/historical-review-v1'
CONTAINER='upgrade-g04-reports-20261010'
ACCOUNT='https://ibkrv2stgauhecwc4itaoo.blob.core.windows.net'
VERSIONS={'U16088571':'v2','U16088606':'v3','hong-kong-china':'v1','alpha-world-ms':'v3','alpha-world-t':'v3'}
NAMES={'U16088571':'U16088571','U16088606':'U16088606','hong-kong-china':'HongKong_China','alpha-world-ms':'AlphaWorld_MS','alpha-world-t':'AlphaWorld_T'}
PERIODS=('2025-01-31','2025-02-28','2025-03-31','2025-04-30','2025-05-31','2025-06-30','2025-07-31','2025-08-31','2025-09-30','2025-10-31','2025-11-30','2025-12-31','2026-01-31','2026-02-28','2026-03-31','2026-04-30','2026-05-31','2026-06-30','2026-07-31','2026-08-31','2026-09-30')

def sha(raw):return hashlib.sha256(raw).hexdigest()

def get(service,name,digest,limit):
    blob=service.get_blob_client(CONTAINER,name)
    props=blob.get_blob_properties()
    if not 0<props.size<=limit:raise ValueError('Artifact size outside bound')
    content=blob.download_blob(max_concurrency=2).readall()
    if len(content)!=props.size or sha(content)!=digest:raise ValueError('Artifact integrity mismatch')
    return content

def artifact_row(subject,month,artifact,prefix):
    kind=artifact['report_type']
    if kind not in ('factsheet','presentation'):raise ValueError('Unknown artifact type')
    suffix='factsheet.pdf' if kind=='factsheet' else 'presentation.pptx'
    expected=CONTAINER+'/'+prefix+month['period_end']+'/'+suffix
    if artifact['output_reference']!=expected:raise ValueError('Artifact outside exact private prefix')
    return {'subject':subject,'period_end':month['period_end'],'snapshot_id':month['snapshot_id'],
            'snapshot_sha256':month['snapshot_sha256'],**artifact}

def main():
    if (OUT/'result.json').exists():raise ValueError('Preserve completed review batch')
    with AzureCliCredential() as credential,BlobServiceClient(ACCOUNT,credential=credential) as service:
        if service.get_container_client(CONTAINER).get_container_properties().get('public_access') is not None:
            raise ValueError('Private QA container required')
        artifacts=[];receipts={};manifests={}
        for subject,version in VERSIONS.items():
            trial=protected(STAGE/('historical-outputs-'+subject+'-'+version)/'result.json');proof=trial['proof']
            prefix='candidate-stage34-historical-'+version+'/'+subject+'/'
            if not trial['passed'] or proof['subject']!=subject or proof['months']!=21 or proof['artifacts']!=42 or proof['financial_charts_verified']!=126 or proof['receipt_blob']!=prefix+'receipt.json':
                raise ValueError('Complete qualified historical receipt required')
            raw=get(service,proof['receipt_blob'],proof['receipt_sha256'],4*1024*1024);manifest=json.loads(raw)
            if manifest['subject']!=subject or manifest['image']!=trial['image'] or len(manifest['months'])!=21:
                raise ValueError('Receipt identity or image differs')
            if tuple(m['period_end'] for m in manifest['months'])!=PERIODS:raise ValueError('Exact 21 month coverage required')
            for month in manifest['months']:
                if month['subject']!=subject or month['financial_status']!='passed' or month['charts_verified']!=6 or len(month['artifacts'])!=2 or {a['report_type'] for a in month['artifacts']}!={'factsheet','presentation'}:
                    raise ValueError('Incomplete monthly numerical acceptance')
                artifacts.extend(artifact_row(subject,month,a,prefix) for a in month['artifacts'])
            receipts[subject]=trial['protected_receipt'];manifests[subject]=manifest
        # The v5 generic factsheets supersede only PDFs. Their presentation
        # counterparts retain the exact same corrected snapshot inputs.
        pdftrial=protected(STAGE/'factsheet-history-outputs-v1/result.json');proof=pdftrial['proof']
        if not pdftrial['passed'] or proof['factsheets']!=63 or proof['history_rows_verified']!=756:raise ValueError('Qualified full-history PDF correction required')
        prefix='candidate-stage34-factsheet-history-v1/'
        if proof['receipt_blob']!=prefix+'receipt.json':raise ValueError('Exact PDF receipt required')
        pdfmanifest=json.loads(get(service,proof['receipt_blob'],proof['receipt_sha256'],4*1024*1024))
        pdfs={(row['subject'],row['period_end']):row for row in pdfmanifest['factsheets']}
        if len(pdfs)!=63 or set(pdfs)!={(s,p) for s in ('U16088571','U16088606','hong-kong-china') for p in PERIODS}:
            raise ValueError('Full generic PDF scope required')
        for item in artifacts:
            key=(item['subject'],item['period_end'])
            if item['report_type']=='factsheet' and key in pdfs:
                row=pdfs[key]
                if row['output_reference']!=CONTAINER+'/'+prefix+key[0]+'/'+key[1]+'/factsheet.pdf':raise ValueError('PDF prefix differs')
                if row.get('input_sha256')!=item['snapshot_sha256']:raise ValueError('PDF/PPT frozen input differs')
                item['output_reference']=row['output_reference'];item['sha256']=row['sha256']
        if len(artifacts)!=210:raise ValueError('Exactly 105 report pairs required')
        payload={'artifacts':artifacts,'source_receipts':receipts,'corrected_pdf_receipt':pdftrial['protected_receipt'],
            'scope':'Private local review of 105 exact monthly pairs; no generation, registration, approval or production writes.',
            'cleanup_required':True,
            'cleanup_authorization':'Owner approved this complete local copy on condition that downloaded files and review exports are deleted after use.',
            'cleanup_boundary':str((OUT/'files').resolve())}
        if (OUT/'intent.json').exists():
            old=protected(OUT/'intent.json')
            if {k:v for k,v in old.items() if k!='protected_receipt'}!=payload:raise ValueError('Interrupted review intent changed')
        else:save(OUT,'intent',payload)
        def download(item):
            subject=item['subject'];kind=item['report_type'];suffix='pdf' if kind=='factsheet' else 'pptx'
            path=OUT/'files'/subject/(NAMES[subject]+'_'+kind.title()+'_'+item['period_end']+'.'+suffix)
            if path.exists():
                if sha(path.read_bytes())!=item['sha256']:raise ValueError('Existing private review file changed; preserve it')
            else:
                name=item['output_reference'].split('/',1)[1]
                content=get(service,name,item['sha256'],64*1024*1024)
                path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('xb') as stream:stream.write(content)
            return {**item,'file':str(path.resolve()),'bytes':path.stat().st_size}
        with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(download,artifacts))
        save(OUT,'result',{'passed':True,'artifacts':results,'report_pairs':105,'files':210,'verified_hashes':210,
            'database_writes':0,'production_files_changed':False,'prior_approvals_transferred':False,
            'full_visual_review':'UNVERIFIED','native_powerpoint_review':'UNVERIFIED',
            'fourteen_day_observation':'UNVERIFIED','real_viewer_sign_in':'UNVERIFIED','full_upgrade_complete':False})
        print(json.dumps({'passed':True,'files':210,'pairs':105,'visual_review':'UNVERIFIED','published':False}))

if __name__=='__main__':main()
