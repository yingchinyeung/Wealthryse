"""Compact status only; completion still requires the full qualification reader."""
import json,sys
import control_reporting_historical_outputs_20261011 as c
from control_upgrade_legacy_recovery_probe_20261009 import protected
def main(subject):
    if subject not in ('U16088571','U16088606','hong-kong-china','alpha-world-ms','alpha-world-t'):raise ValueError('Unknown subject')
    out=c.qa.OUT.parent/('historical-outputs-'+subject+'-v1')
    name=protected(out/'dispatch.json')['execution']
    rows=c.b.az('monitor','log-analytics','query','-w','5264a124-10cd-45f6-93a5-6d65c37e9bfb','--analytics-query',
        "ContainerAppConsoleLogs_CL | where ContainerGroupName_s startswith '"+name+"-' | where Log_s startswith 'REPORTING_HISTORICAL_MONTH ' | summarize completed_months=count()")
    print(json.dumps({'subject':subject,'execution':name,'progress_only':True,'rows':rows}))
if __name__=='__main__':main(*sys.argv[1:])
