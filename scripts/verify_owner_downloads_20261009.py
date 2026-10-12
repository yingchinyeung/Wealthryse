"""Compare explicitly observed owner-browser downloads with read-only registry evidence."""
import hashlib,json
from datetime import datetime,timezone
from pathlib import Path
from run_specialist_gateway_read_verification import retain

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'.work/platform-upgrade-20261009-stage3/download-manifest-v1'
FILES={
 'alpha-world-ms':('AlphaWorld_MS_Factsheet_2026-09-30 (1).pdf','AlphaWorld_MS_Presentation_2026-09-30.pptx'),
 'alpha-world-t':('AlphaWorld_T_Factsheet_2026-09-30 (2).pdf','AlphaWorld_T_Presentation_2026-09-30.pptx'),
 'hong-kong-china':('hong-kong-china_Factsheet_2026-09-30 (1).pdf','hong-kong-china_Monthly_Presentation_2026-09-30 (1).pptx'),
 'U16088571':('U16088571_Factsheet_2026-09-30 (1).pdf','U16088571_Monthly_Presentation_2026-09-30 (1).pptx'),
 'U16088606':('U16088606_Factsheet_2026-09-30 (1).pdf','U16088606_Monthly_Presentation_2026-09-30 (1).pptx'),
}
def main():
 result=json.loads((FOLDER/'result.json').read_bytes())
 if result['status']!='Succeeded' or len(result['records'])!=1:raise ValueError('Read-only registry query required')
 manifest=json.loads(result['records'][0]['Result'])
 if not manifest['read_only'] or manifest['database_writes']!=0:raise ValueError('Unexpected registry mutation')
 rows=[]
 for entry in manifest['records']:
  for kind,name in zip(('factsheet','presentation'),FILES[entry['subject']],strict=True):
   path=Path('C:/Users/antho/Downloads')/name
   actual=hashlib.sha256(path.read_bytes()).hexdigest()
   registered=entry['artifacts'][kind]
   if actual!=registered['output_sha256']:raise ValueError('Download mismatch: '+name)
   rows.append({'subject':entry['subject'],'report_type':kind,'generation_run_id':registered['generation_run_id'],
       'sha256':actual,'local_file':str(path),'download_route':'owner-review','matches_registry':True})
 if len(rows)!=10:raise ValueError('All ten downloads required')
 evidence={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'files':rows,
    'registry_evidence_sha256':hashlib.sha256((FOLDER/'result.json').read_bytes()).hexdigest(),
    'actual_browser_role':'owner','actual_viewer_session':False,'approvals_changed':False,
    'viewer_acceptance':'unverified: owner has no viewer account','full_upgrade_complete':False}
 receipt=retain(evidence,'platform-owner-download-verification')
 with (FOLDER/'owner-downloads-verified.json').open('x',encoding='utf-8') as f:
  json.dump({**evidence,'protected_receipt':receipt},f,indent=2)
 print(json.dumps({'download_hashes_matched':len(rows),'viewer_session_verified':False}))
if __name__=='__main__':main()
