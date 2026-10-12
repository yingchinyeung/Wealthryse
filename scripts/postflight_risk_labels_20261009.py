"""Read-only live display-release health and protected acceptance record."""
import json
from datetime import datetime,timezone
from urllib.parse import urlsplit
import requests
from audit_azure_runtime import az
from release_platform_risk_labels_20261009 import BUNDLE,release
from deploy_platform_risk_labels_20261009 import read_protected
from run_specialist_gateway_read_verification import retain

def main():
 installed=read_protected(BUNDLE/'web-deployment-v1/verified.json')
 revision=installed['revision']
 query="ContainerAppConsoleLogs_CL | where RevisionName_s == '"+revision+"' | where Log_s contains '/readyz' | summarize Requests=count(),Successful=countif(Log_s contains '200'),Failures=countif(Log_s contains '503'),Last=max(TimeGenerated)"
 logs=az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
 if not logs or int(logs[0]['Successful'])<1 or int(logs[0]['Failures'])!=0:raise ValueError('Readiness evidence not yet qualified')
 response=requests.get('https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io',timeout=20,allow_redirects=False)
 redirect=response.status_code==302 and '/.auth/login/' in urlsplit(response.headers.get('Location','')).path
 if response.status_code!=401 and not redirect:raise ValueError('Anonymous authentication gate failed')
 download_path=release.ROOT/'.work/platform-upgrade-20261009-stage3/download-manifest-v1/owner-downloads-verified.json'
 downloads=read_protected(download_path)
 if not downloads['passed'] or len(downloads['files'])!=10:raise ValueError('Owner download acceptance missing')
 record={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':True,'image':installed['image'],
  'revision':revision,'live_readiness':logs,'anonymous_http_status':response.status_code,
  'anonymous_login_enforced':True,'owner_download_hashes_verified':10,'actual_viewer_session':False,
  'report_bytes_changed':False,'report_approvals_changed':False,'financial_calculations_changed':False,
  'full_upgrade_complete':False,'browser_acceptance_record':'browser-acceptance.json'}
 receipt=retain(record,'platform-risk-labels-postflight')
 release.save(BUNDLE/'web-deployment-v1/postflight.json',{**record,'protected_receipt':receipt})
 print(json.dumps({'passed':True,'ready_requests':logs[0]['Successful'],'anonymous_status':response.status_code,'full_upgrade_complete':False}))
if __name__=='__main__':main()
