"""Read-only exact-image normal monthly workflow using existing nonsecret settings."""
import sys,hashlib
from pathlib import Path
import control_platform_upgrade_probe_20261009 as probe
import release_platform_worker_stage3_v2_20261009 as worker_release
from deploy_metadata_monitor import request,BASE

ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path(__file__).with_name('probe_monthly_worker_entrypoint_20261009.py')
ROLLBACK=False
ALLOWED={'DEPLOYMENT_ENVIRONMENT','AZURE_STORAGE_ACCOUNT_URL','INTERNAL_REPORT_CONTAINER',
         'FACTSHEET_TEMPLATE_BLOB','FACTSHEET_TEMPLATE_SHA256','PRESENTATION_TEMPLATE_PATH',
         'PRESENTATION_TEMPLATE_SHA256','AZURE_OPENAI_ENDPOINT','AZURE_OPENAI_DEPLOYMENT',
         'AI_RESOURCE_REGION','AI_PROCESSING_SCOPE'}
class BoundSource:
    def read_text(self,encoding):
        job=request('GET',BASE+'Microsoft.App/jobs/ibkrv2stg-month-end-factsheet?api-version=2024-03-01')
        env=job['properties']['template']['containers'][0]['env']
        settings={item['name']:item['value'] for item in env if item['name'] in ALLOWED and 'value' in item and not item.get('secretRef')}
        settings['REPORT_PERIOD_END']='2026-09-30'
        sha=hashlib.sha256((worker_release.BUNDLE/'worker/generate_month_end_package.py').read_bytes()).hexdigest()
        if ROLLBACK:sha='3e806b23f609a7be64a3ca9ddfe5f8706bc6f439011f9431130dfd6ff857ca7f'
        return 'WORKER_SETTINGS = '+repr(settings)+'\nEXPECTED_WORKER_SHA = '+repr(sha)+'\n'+SOURCE.read_text(encoding=encoding)
probe.SOURCE=BoundSource()
probe.release.WEB=worker_release.release.WEB
probe.OUT=worker_release.BUNDLE/'entrypoint-probe-v1'
if __name__=='__main__':
    if sys.argv[2:]==['v2']:
        SOURCE=SOURCE.with_name('probe_monthly_worker_entrypoint_v2_20261009.py')
        probe.OUT=worker_release.BUNDLE/'entrypoint-probe-v2'
    if sys.argv[2:]==['rollback']:
        ROLLBACK=True
        SOURCE=SOURCE.with_name('probe_monthly_worker_entrypoint_v2_20261009.py')
        probe.OUT=worker_release.BUNDLE/'rollback-entrypoint-v1'
        probe.ROLLBACK=True
        probe.release.IMAGE=worker_release.BASE_IMAGE
    if sys.argv[2:]==['diagnostic']:
        SOURCE=SOURCE.with_name('probe_monthly_worker_entrypoint_v3_20261009.py')
        probe.OUT=worker_release.BUNDLE/'entrypoint-diagnostic-v1'
    probe.main(sys.argv[1])
