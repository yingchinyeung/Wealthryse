"""Append verified recovery/rerender checkpoint, never imply owner activation."""
import hashlib,html,json
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/audit/AlphaWorld_T_Onboarding_20261009'
def read(p):return json.loads(p.read_bytes())
recovery=ROOT/'.work/alpha-t-document-recovery-20261009-v1/completed.json'
repair=ROOT/'.work/alpha-t-axis-contract-20261009-v3'
batch=ROOT/'.work/alpha-t-registered-reports-20261009-v2'
receipts=[recovery,repair/'worker/scan-completed.json',repair/'worker/smoke-v1/runtime-result.json',
    repair/'cutover/ibkrv2stg-onboard-render-verified.json',repair/'cutover/ibkrv2stg-month-end-factsheet-verified.json',
    batch/'registry.json',batch/'local-validation.json',batch/'browser-download-validation.json']
r=read(recovery);validation=read(batch/'local-validation.json');registry=read(batch/'registry.json')
assert r['status']=='completed' and r['rollback_verified']
assert validation['status']=='passed' and validation['downloaded_hash_verified']==42
assert registry['batch']['status']=='awaiting_owner_approval' and registry['batch']['approved_at'] is None
browser=read(batch/'browser-download-validation.json')
assert browser['status']=='passed' and browser['browser_files_verified']==4
result={'generated_at':datetime.now(timezone.utc).isoformat(),'supersedes_status_in':'Checkpoint_v8.json; previous evidence retained',
 'status':'Document recovery and 42-file private rerender complete; report approval and onboarding activation remain pending',
 'corrected_document_version':'portal-upload-20261009T101539Z',
 'recovery':r,'worker_repair':'T validator now returns its verified performance-axis bounds; calculations and layouts unchanged',
 'deployed_workers':{name:read(repair/('cutover/'+name+'-verified.json')) for name in ('ibkrv2stg-onboard-render','ibkrv2stg-month-end-factsheet')},
 'batch_id':registry['batch']['batch_id'],'manifest_sha256':registry['batch']['manifest_sha256'],
 'automated_acceptance':{k:v for k,v in validation.items() if k!='results'},
 'website_download_acceptance':browser,
 'full_all_month_visual_review_complete':False,'reports_released':False,'viewer_access_activated':False,
 'next':['Complete visual review of all final monthly reports. Website delivery passed for the first and latest PDF/PPTX pairs; all 42 stored files passed hash verification.',
         'Obtain final report/preview approval and explicit viewer activation after all gates pass.'],
 'evidence':[{'file':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in receipts]}
with (OUT/'Checkpoint_v9.json').open('x',encoding='utf-8') as f:json.dump(result,f,indent=2)
body='<h1>Alpha World T: private report batch validated</h1><p>The document reference was corrected after an audited T-only stage recovery. A separate presentation-validation handoff defect was repaired in the two reporting workers. The website, calculations, approvals and viewer access were not changed by that deployment.</p><p>All 21 factsheets and 21 presentations are registered privately, downloaded with matching hashes and checked against frozen monthly inputs. This does not represent completed all-slide visual review, report approval or viewer activation.</p><pre>'+html.escape(json.dumps(result,indent=2))+'</pre>'
with (OUT/'Checkpoint_v9.html').open('x',encoding='utf-8') as f:f.write('<!doctype html><meta charset="utf-8"><title>Alpha World T checkpoint 9</title><style>body{max-width:1000px;margin:40px auto;font:16px/1.55 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>'+body)
print(json.dumps({'checkpoint':str(OUT/'Checkpoint_v9.html'),'onboarding_complete':False}))
