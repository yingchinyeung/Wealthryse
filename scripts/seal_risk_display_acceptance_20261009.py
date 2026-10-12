"""Retain browser/test evidence without claiming completion of the whole upgrade."""
import json,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from release_platform_risk_labels_20261009 import BUNDLE,release
from run_specialist_gateway_read_verification import retain

def main():
 run=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_risk_labels_deployment_20261009.py','-v'],
  cwd=release.ROOT,capture_output=True,text=True,timeout=60)
 if run.returncode or 'Ran 9 tests' not in run.stderr:raise ValueError('Deployment control tests did not pass')
 with (BUNDLE/'deployment-control-tests.log').open('x',encoding='utf-8') as f:f.write(run.stdout+run.stderr)
 browser=release.read(BUNDLE/'browser-acceptance.json')
 if not browser['t_risk_metric_cards_identical_before_and_after'] or not browser['viewport_reset_confirmed']:
  raise ValueError('Browser acceptance incomplete')
 names=['browser-acceptance.json','deployment-control-tests.log','web-deployment-v1/verified.json','web-deployment-v1/postflight.json']+browser['screenshots']
 record={'checked_at':datetime.now(timezone.utc).isoformat(),'scope':'display-only website stage',
  'browser_acceptance':browser,'evidence_sha256':{n:release.sha(BUNDLE/n) for n in names},
  'tests_passed':9,'actual_viewer_session':False,'full_upgrade_complete':False}
 receipt=retain(record,'platform-display-browser-and-control-acceptance')
 release.save(BUNDLE/'acceptance-seal.json',{**record,'protected_receipt':receipt})
 print(json.dumps({'sealed':True,'tests_passed':9,'full_upgrade_complete':False}))
if __name__=='__main__':main()
