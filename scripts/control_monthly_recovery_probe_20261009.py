"""Read-only qualification of the compatible recovery image, not old baseline."""
import sys,hashlib
import control_monthly_entrypoint_probe_20261009 as controller
import release_platform_worker_recovery_20261009 as release
from deploy_metadata_monitor import request,BASE

class RecoverySource:
    def read_text(self,encoding):
        job=request('GET',BASE+'Microsoft.App/jobs/ibkrv2stg-month-end-factsheet?api-version=2024-03-01')
        env=job['properties']['template']['containers'][0]['env']
        settings={i['name']:i['value'] for i in env if i['name'] in controller.ALLOWED and 'value' in i and not i.get('secretRef')}
        settings['REPORT_PERIOD_END']='2026-09-30'
        source=release.release.WEB/'context/worker/generate_month_end_package.py'
        sha=hashlib.sha256(source.read_bytes()).hexdigest()
        return 'WORKER_SETTINGS = '+repr(settings)+'\nEXPECTED_WORKER_SHA = '+repr(sha)+'\n'+controller.SOURCE.with_name('probe_monthly_worker_entrypoint_v2_20261009.py').read_text(encoding=encoding)

controller.probe.SOURCE=RecoverySource()
controller.probe.release.WEB=release.release.WEB
controller.probe.OUT=release.BUNDLE/'recovery-entrypoint-v2'

if __name__=='__main__':controller.probe.main(sys.argv[1])
