"""Installed worker metadata only; no network, database, rendering or model calls."""
import json, hashlib
from pathlib import Path

root = Path('/app')
names = ['scripts/generate_month_end_package.py','scripts/run_registry_month_end_packages.py',
         'scripts/render_onboarding_historical_reports.py','scripts/check_report_runtime.py',
         'src/ibkr_etl/reporting_snapshot.py','src/ibkr_etl/historical_reporting.py',
         'src/ibkr_etl/monthly_artifacts.py','src/ibkr_etl/monthly_release.py',
         'src/ibkr_etl/presentation_design.py','src/ibkr_etl/template_governance.py']
files = {name: hashlib.sha256((root/name).read_bytes()).hexdigest() if (root/name).exists() else None for name in names}
manifests = {}
for name in ('ALPHA_T_RUNTIME_RELEASE.json','MS_PRESENTATION_WORKER_RELEASE.json'):
    path=root/name
    if path.exists():
        value=json.loads(path.read_bytes())
        manifests[name] = {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                          'keys':list(value), 'installed_files_sha256':value.get('installed_files_sha256',{})}
print('PLATFORM_CORRECTION_PROBE '+json.dumps({'read_only':True,'database_writes':0,'files_sha256':files,'manifests':manifests}),flush=True)
