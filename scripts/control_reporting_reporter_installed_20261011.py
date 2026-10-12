"""Use the installed monthly-report worker's normal restricted role, SELECT only."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/control_reporting_onboard_installed_20261011.py').read_text(encoding='utf-8')
changes={
 'import deploy_reporting_onboard_20261011 as d':'import deploy_reporting_reporter_20261011 as d',
 'onboard-installed-role-v1':'reporter-installed-role-v1',
 'REPORTING_ONBOARD_INSTALLED_':'REPORTING_REPORTER_INSTALLED_',
 "'read_reporting_web_installed_receipt_20261011.py')":"'read_reporting_web_installed_receipt_20261011.py','control_reporting_onboard_installed_20261011.py')",
 'normal_onboarding_workflow_executed':'normal_scheduled_workflow_executed',
}
for before,after in changes.items():
    if source.count(before)!=(3 if before=='REPORTING_ONBOARD_INSTALLED_' else 1):raise ValueError('Exact installed reporter anchor required: '+before)
    source=source.replace(before,after)
exec(compile(source,'installed-month-end-reporter-postflight','exec'))
