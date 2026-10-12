"""Reporter-specific version of the image-only deployment safeguard tests."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/test_reporting_onboard_deploy_20261011.py').read_text(encoding='utf-8')
changes={
 'import deploy_reporting_onboard_20261011 as d':'import deploy_reporting_reporter_20261011 as d',
 'import test_reporting_onboard_qa_20261011 as qa_tests':'import test_reporting_reporter_qa_20261011 as qa_tests',
 'scripts/run_self_service_onboarding_reports.py':'scripts/run_registry_month_end_packages.py',
 "'configuration':{'triggerType':'Manual','secrets'":"'configuration':{'triggerType':'Schedule','scheduleTriggerConfig':{'cronExpression':'0 4 1 * *'},'secrets'",
 "j['properties']['configuration']['triggerType']='Schedule'":"j['properties']['configuration']['triggerType']='Manual'",
}
for before,after in changes.items():
    if source.count(before)!=1:raise ValueError('Exact reporter test anchor required')
    source=source.replace(before,after)
exec(compile(source,'reporter-image-deployment-tests','exec'))
