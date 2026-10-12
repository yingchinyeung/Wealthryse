"""Versioned image-only deployment for the separately qualified scheduled reporter."""
from pathlib import Path
root=Path(__file__).resolve().parents[1]
source=(root/'scripts/deploy_reporting_onboard_20261011.py').read_text(encoding='utf-8')
changes={
 'import control_reporting_onboard_qa_20261011 as qa':'import control_reporting_reporter_qa_20261011 as qa',
 'import release_reporting_onboard_20261011 as release':'import release_reporting_history_20261011 as release',
 "JOB='ibkrv2stg-onboard-render'":"JOB='ibkrv2stg-month-end-factsheet'",
 "OUT=qa.STAGE/'onboard-deployment-v1'":"OUT=qa.STAGE/'reporter-deployment-v1'",
 'scripts/run_self_service_onboarding_reports.py':'scripts/run_registry_month_end_packages.py',
 "require(cfg.get('triggerType')=='Manual' and not cfg.get('scheduleTriggerConfig') and not cfg.get('eventTriggerConfig'),'Manual trigger required')":"require(cfg.get('triggerType')=='Schedule' and bool((cfg.get('scheduleTriggerConfig') or {}).get('cronExpression')) and not cfg.get('eventTriggerConfig'),'Existing scheduled trigger required')",
 'onboard-output-review-v1':'reporter-output-review-v1',
 'test_reporting_onboard_deploy_20261011':'test_reporting_reporter_deploy_20261011',
 'test_reporting_onboard_qa_20261011':'test_reporting_reporter_qa_20261011',
 "'test_reporting_reporter_deploy_20261011.py')":"'test_reporting_reporter_deploy_20261011.py','deploy_reporting_onboard_20261011.py')",
 'normal_onboarding_workflow_executed':'normal_scheduled_workflow_executed',
}
for before,after in changes.items():
    count=2 if before=='test_reporting_onboard_deploy_20261011' else 1
    if source.count(before)!=count:raise ValueError('Exact reporter deployment anchor required: '+before)
    source=source.replace(before,after)
source=source.replace('Onboarding renderer saved image only','Monthly-report worker saved image only').replace('original command, manual trigger','original command, schedule').replace('onboarding','monthly-report').replace('Onboarding','Monthly-report')
exec(compile(source,'versioned-reporting-worker-deployment','exec'))
