"""Normal snapshot-role postflight, with no saved-command or secret changes."""
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=(root/'scripts/control_reporting_onboard_installed_20261011.py').read_text()
changes={
    'import deploy_reporting_onboard_20261011 as d':'import deploy_snapshot_correction_20261011 as d',
    "OUT=d.qa.STAGE/'onboard-installed-role-v1'":"OUT=d.qa.STAGE/'snapshot-installed-role-v1'",
    "d.release.r.read(d.release.OUT/'source-intent.json')":"d.release.h.read(d.release.OUT/'source-intent.json')",
    'REPORTING_ONBOARD_INSTALLED_':'SNAPSHOT_INSTALLED_',
    'normal_onboarding_workflow_executed':'normal_snapshot_workflow_executed',
}
for before,after in changes.items():
    if before not in source:raise ValueError('Installed-postflight anchor changed')
    source=source.replace(before,after)
source=source.replace('onboarding worker','snapshot worker').replace('onboarding workflow','snapshot workflow')
exec(compile(source,'snapshot-installed-postflight','exec'))
