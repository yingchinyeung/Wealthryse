"""Bind an existing authenticated owner download to private registered byte evidence."""
import json
from datetime import datetime,timezone
from pathlib import Path
import run_ms_website_input_probe as p
from qualify_viewer_database_recovery_20261008 import ROOT,read,save

def main():
    qualification=read(ROOT/'source-v2','qualified')
    proof=ROOT/'source-v2/reports-proof.json'
    if p.sha(proof)!=qualification['report_proof_sha256']:raise ValueError('Registered report proof differs')
    rows=json.loads(proof.read_bytes())['owner_review_downloads']
    matching=[r for r in rows if r['generation_run_id']==920 and r['subject_id']=='alpha-world-ms' and r['period_end']=='2026-09-30' and r['scope']=='owner-review']
    path=Path('C:/Users/antho/Downloads/AlphaWorld_MS_Factsheet_2026-09-30_aca3c6dc71cd7be606af2dcbc16672b287be93ce84eaa20bd9bfce109c427fe3.pdf')
    if len(matching)!=1 or p.sha(path)!=matching[0]['sha256'] or path.stat().st_size!=matching[0]['bytes']:raise ValueError('Actual owner download differs')
    record={'status':'passed','checked_at':datetime.now(timezone.utc).isoformat(),'browser':'Chrome existing authenticated owner session','method':'documented link downloadMedia','route':'/api/accounts/alpha-world-ms/reporting/review-artifacts/920/download','artifact_sha256':p.sha(path),'bytes':path.stat().st_size,'registered_proof_sha256':p.sha(proof),'reports_released':False,'approval_changes':0,'fresh_sign_in_tested':False,'restored_database_tested':False,'cookies_or_tokens_read':False}
    if (ROOT/'owner-download-qualified.json').exists():raise ValueError('Preserve existing owner evidence')
    save(ROOT,'owner-download-qualified',record)
    print(json.dumps({'status':'authenticated_owner_download_passed','generation_run_id':920,'approval_changes':0}))

if __name__=='__main__':main()
