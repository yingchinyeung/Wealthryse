"""Read-only postflight and retained evidence; never changes services or permissions."""
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit
import requests
from audit_azure_runtime import az
from release_platform_upgrade_20261009 import BUNDLE, release
from run_specialist_gateway_read_verification import retain


def main():
    before = release.read(release.ROOT / '.work/platform-corrections-20261009-v1/baseline-131539.json')
    after = release.read(release.ROOT / '.work/platform-corrections-20261009-v1/baseline-134005.json')
    jobs_before = {j['name']:j['configuration'] for j in before['jobs']}
    jobs_after = {j['name']:j['configuration'] for j in after['jobs']}
    if jobs_before != jobs_after:
        raise ValueError('Tracked job configuration changed')
    installed = release.read(BUNDLE / 'web-deployment-v1/verified.json')
    if after['web']['image'] != installed['image'] or after['web']['revision'] != installed['revision']:
        raise ValueError('Postflight website differs')
    query = "ContainerAppConsoleLogs_CL | where RevisionName_s == 'ibkrv2stg-web--platform-upgrade-1009-v1' | where Log_s contains '/readyz' | summarize Requests=count(),Successful=countif(Log_s contains '200'),Failures=countif(Log_s contains '503'),Last=max(TimeGenerated)"
    probe_logs = az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',query)
    if not probe_logs or int(probe_logs[0]['Successful']) < 1 or int(probe_logs[0]['Failures']) != 0:
        raise ValueError('Live application readiness evidence is incomplete')
    url = 'https://ibkrv2stg-web.purplerock-8cff24a2.eastasia.azurecontainerapps.io'
    response = requests.get(url, allow_redirects=False, timeout=20)
    destination = urlsplit(response.headers.get('Location',''))
    redirected = response.status_code == 302 and '/.auth/login/' in destination.path
    if response.status_code != 401 and not redirected:
        raise ValueError('Anonymous request did not reach expected authentication gate')
    record = {'captured_at':datetime.now(timezone.utc).isoformat(),'status':'application_stage_passed',
      'image':installed['image'],'revision':installed['revision'],'traffic_percent':100,
      'tracked_jobs_unchanged':sorted(jobs_before),'live_readiness':probe_logs,
      'anonymous_http_status':response.status_code,'anonymous_login_enforced':True,
      'anonymous_redirects_to_login':redirected,
      'owner_browser_home_and_t_trades_and_risk_checked':True,
      'actual_separate_viewer_session_checked':False,'report_downloads_reverified':False,
      'complete_upgrade':False,'source_data_changed':False,'report_approvals_changed':False,
      'additional_open_ui_observations':['T risk page still uses physical/USD contributor explanation for unavailable composite attribution',
         'T risk explanation displays 0 aligned valuations and undefined provider; numerical cache unchanged'],
      'remaining':['Reporting-worker PostgreSQL/regeneration acceptance','MS extended risk qualification',
         'Physical portfolios current-date crisis refresh','All-image-family scan and secret/audit improvements',
         'Viewer/download and responsive acceptance','Private-network costs and path qualification',
         'Recovery destination/RPO/RTO/budget','Real dependency-ordered cycle and burn-in']}
    receipt = retain(record, 'platform-upgrade-web-postflight')
    release.save(BUNDLE / 'web-deployment-v1/postflight.json', {**record,'protected_receipt':receipt})
    print(json.dumps({'status':record['status'],'traffic_percent':100,'live_readiness_requests':probe_logs[0]['Successful'],
                      'tracked_jobs_unchanged':len(jobs_before),'complete_upgrade':False}))


if __name__ == '__main__':
    main()
